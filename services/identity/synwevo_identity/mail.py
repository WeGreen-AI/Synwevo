"""Outgoing mail: verification, invitations, password resets and security notices.

Three things this is careful about.

**Never report a send that did not happen.** Mail goes into `mail_outbox` and a worker drains
it, so an endpoint answers "queued" and the real outcome is visible afterwards. A handler that
sends inline must either block on a slow SMTP server or claim success before there is any, and
a screen that says "we sent you a link" when nothing left the building is the worst outcome
here — the person waits instead of asking for help.

**Links are built from configured identity, never from the request.** `SYNWEVO_PUBLIC_URL` is
the base. Taking the Host header would let anyone who can reach the service mint an invitation
mail pointing at a host they control, carrying a token that is valid on the real one.

**Not configured means not configured.** With no SMTP settings the mode is `disabled` and
enqueue refuses loudly, so a deployment cannot quietly drop account mail while the UI shows a
tick. `file` mode writes .eml files for local development and says so in every message it
writes, so a developer cannot mistake it for delivery.
"""
from __future__ import annotations

import re
import smtplib
import ssl
import math
from dataclasses import dataclass, field
from email.headerregistry import Address
from email.policy import SMTP as SMTP_POLICY
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, new_id, now

# Backoff between attempts; the last value repeats until MAX_ATTEMPTS.
RETRY_SCHEDULE = (30, 120, 600, 1800, 3600)
MAX_ATTEMPTS = 8
# A claimed row stays queued for schema/backward compatibility, but is never claimed twice.
# After a crash we cannot know if SMTP accepted DATA: expire to a visible failure, never resend.
IN_PROGRESS = "delivery_in_progress"
CLAIM_TIMEOUT_S = 900


class DeliveryFailure(Exception):
    def __init__(self, code: str, *, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


def _config_error():
    return ServiceError(503, "mail_config_invalid",
                        "邮件配置无效：请检查发件地址、SMTP 主机、端口、TLS、凭据与超时设置。")


def _flag(key: str, default: str) -> bool:
    value = (env.get(key) or default).strip().lower()
    if value not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
        raise _config_error()
    return value in ("1", "true", "yes", "on")


def _address(value: str) -> str:
    try:
        if any(c.isspace() for c in value) or any(c in value for c in "<>,;\r\n"):
            raise ValueError()
        value.encode("ascii")
        parsed = Address(addr_spec=value)
        if not parsed.username or not parsed.domain or "@" not in value:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise ServiceError(400, "mail_address_invalid", "邮件地址必须是单个有效邮箱地址。") from None
    return value


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    implicit_tls: bool
    timeout: float


def smtp_config() -> SmtpConfig:
    try:
        host = (env.get("SMTP_HOST") or "").strip()
        port = int(env.get("SMTP_PORT") or "587")
        user, password = env.get("SMTP_USER") or "", env.get("SMTP_PASSWORD") or ""
        implicit_tls = _flag("SMTP_SSL", "0")
        starttls = _flag("SMTP_STARTTLS", "1")
        timeout = float(env.get("SMTP_TIMEOUT_S") or "20")
        _, sender = from_address()
        _address(sender)
        if (not host or any(c.isspace() for c in host) or not 1 <= port <= 65535
                or implicit_tls == starttls or not math.isfinite(timeout) or not 1 <= timeout <= 60
                or bool(user) != bool(password) or not env.get("MAIL_FROM")):
            raise ValueError()
    except (ValueError, ServiceError):
        raise _config_error() from None
    return SmtpConfig(host, port, user, password, implicit_tls, timeout)


def _failure(exc: Exception) -> DeliveryFailure:
    # Never persist server-controlled response text: it can echo credentials, recipients or DATA.
    if isinstance(exc, DeliveryFailure):
        return exc
    if isinstance(exc, ssl.SSLCertVerificationError):
        return DeliveryFailure("tls_certificate_invalid")
    if isinstance(exc, ssl.SSLError):
        return DeliveryFailure("tls_failed")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return DeliveryFailure("smtp_authentication_failed")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return DeliveryFailure("smtp_security_not_supported")
    if isinstance(exc, smtplib.SMTPResponseException):
        return DeliveryFailure(f"smtp_rejected_{int(exc.smtp_code)}", retryable=400 <= exc.smtp_code < 500)
    if isinstance(exc, (OSError, smtplib.SMTPServerDisconnected)):
        return DeliveryFailure("transport_unavailable", retryable=True)
    if isinstance(exc, ServiceError):
        return DeliveryFailure(exc.code)
    return DeliveryFailure("mail_delivery_failed")


def mode() -> str:
    """smtp | file | disabled — decided by configuration, never guessed."""
    explicit = (env.get("MAIL_MODE") or "").strip().lower()
    if explicit:
        if explicit not in ("smtp", "file", "disabled"):
            raise _config_error()
        return explicit
    return "smtp" if env.get("SMTP_HOST") else "disabled"


def public_base() -> str:
    """The origin that goes into emailed links.

    Required in any deployment that sends mail. Deriving it from the request would make the
    destination attacker-controlled while the token stays valid on the real host.
    """
    return (env.get("PUBLIC_URL") or "").strip().rstrip("/")


def from_address() -> tuple[str, str]:
    addr = (env.get("MAIL_FROM") or "synwevo@localhost").strip()
    name = (env.get("MAIL_FROM_NAME") or "Synwevo").strip()
    if any(c in name for c in "\r\n"):
        raise _config_error()
    return name, addr


class MailNotConfigured(ServiceError):
    def __init__(self) -> None:
        super().__init__(503, "mail_not_configured",
                         "本服务尚未配置邮件发送，无法寄出邀请或验证邮件。"
                         "请先设置 SYNWEVO_SMTP_HOST 等参数（见 docs/DEPLOY.md）。")


class Mailer:
    def __init__(self, store: Store, start: bool = True):
        self.store = store
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if mode() == "smtp":
            smtp_config()  # Fail before starting a misconfigured production worker.
        if start:
            self._thread = threading.Thread(target=self._run, name="mail", daemon=True)
            self._thread.start()

    # ------------------------------------------------------------------ enqueue
    def alive(self) -> bool:
        """Is the delivery thread running? Reported by /readyz on the account service.

        A mailer that has died takes password resets and invitations with it, and the failure is
        silent — the row is queued and nothing sends it. Started with ``start=False`` (tests,
        one-shot CLI) it reports alive, because there is no worker that was supposed to be there.
        """
        return self._thread is None or self._thread.is_alive()

    def enqueue(self, to_addr: str, subject: str, body: str, *, kind: str, ref_id: str | None = None) -> dict:
        """Queue one message. Raises when mail is not configured, rather than silently dropping.

        The caller is expected to let that refusal reach the person who pressed the button: an
        invitation that cannot be delivered is not an invitation.
        """
        m = mode()
        if m == "disabled":
            raise MailNotConfigured()
        if m == "smtp" and not public_base():
            raise ServiceError(503, "public_url_missing",
                               "发送邮件前必须设置 SYNWEVO_PUBLIC_URL，否则邮件里的链接会指向错误的地址。")
        if m == "smtp":
            smtp_config()
        _address(to_addr)
        if any(c in subject for c in "\r\n"):
            raise ServiceError(400, "mail_subject_invalid", "邮件主题不能包含换行。")
        row = {"id": new_id("ml"), "to_addr": to_addr, "subject": subject, "body": body,
               "kind": kind, "ref_id": ref_id, "status": "queued", "attempts": 0,
               "next_attempt_at": now(), "created_at": now()}
        self.store.execute(
            "INSERT INTO mail_outbox (id,to_addr,subject,body,kind,ref_id,status,attempts,next_attempt_at,created_at) "
            "VALUES (:id,:to_addr,:subject,:body,:kind,:ref_id,:status,:attempts,:next_attempt_at,:created_at)", row)
        self._wake.set()
        return row

    def status_for(self, ref_id: str) -> dict | None:
        return self.store.one("SELECT id,status,attempts,last_error,created_at,sent_at "
                              "FROM mail_outbox WHERE ref_id=? ORDER BY created_at DESC LIMIT 1", (ref_id,))

    # ------------------------------------------------------------------ worker
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sent = self.drain_once()
            except Exception as e:  # noqa: BLE001 - a worker thread must not die
                print(f"[synwevo] mail worker error: {type(e).__name__}", flush=True)
                sent = 0
            self._wake.wait(1.0 if sent else 15.0)
            self._wake.clear()

    def drain_once(self, limit: int = 20) -> int:
        """Atomically claim each due message; SMTP uncertainty never causes automatic resends."""
        expired = self.store.all(
            "SELECT id,body FROM mail_outbox WHERE status='queued' AND last_error=? "
            "AND next_attempt_at<=?", (IN_PROGRESS, now()))
        for row in expired:
            self.store.execute(
                "UPDATE mail_outbox SET status='failed', last_error='delivery_unknown', "
                "body=?, next_attempt_at=NULL WHERE id=? AND status='queued' AND last_error=?",
                (redact(row["body"]), row["id"], IN_PROGRESS))
        due = self.store.all(
            "SELECT * FROM mail_outbox WHERE status='queued' AND COALESCE(last_error,'')!=? "
            "AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at LIMIT ?",
            (IN_PROGRESS, now(), max(1, min(limit, 100))))
        sent = 0
        for row in due:
            if self._stop.is_set():
                break
            claimed = self.store.execute(
                "UPDATE mail_outbox SET last_error=?, next_attempt_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='queued' AND COALESCE(last_error,'')!=? "
                "AND attempts=? AND (next_attempt_at IS NULL OR next_attempt_at<=?)",
                (IN_PROGRESS, now() + CLAIM_TIMEOUT_S, row["id"], IN_PROGRESS, row["attempts"], now()))
            if not claimed:
                continue
            try:
                receipt = self._deliver(row)
            except Exception as e:
                failure = _failure(e)
                attempts = int(row["attempts"]) + 1
                if not failure.retryable or attempts >= MAX_ATTEMPTS:
                    self.store.execute(
                        "UPDATE mail_outbox SET status='failed', last_error=?, body=?, next_attempt_at=NULL "
                        "WHERE id=? AND status='queued' AND last_error=?",
                        (failure.code, redact(row["body"]), row["id"], IN_PROGRESS))
                else:
                    delay = RETRY_SCHEDULE[min(attempts - 1, len(RETRY_SCHEDULE) - 1)]
                    self.store.execute(
                        "UPDATE mail_outbox SET next_attempt_at=?, last_error=? "
                        "WHERE id=? AND status='queued' AND last_error=?",
                        (now() + delay, failure.code, row["id"], IN_PROGRESS))
                continue
            # Keep this outside the delivery try block. A database error AFTER SMTP acceptance
            # leaves the claim intact; it must not be interpreted as a safe SMTP retry.
            self.store.execute(
                "UPDATE mail_outbox SET status='sent', sent_at=?, last_error=NULL, body=?, next_attempt_at=NULL "
                "WHERE id=? AND status='queued' AND last_error=?",
                (now(), redact(row["body"]), row["id"], IN_PROGRESS))
            if receipt:
                print(f"[synwevo] mail accepted id={row['id']} smtp_code=250 queue_id={receipt}", flush=True)
            sent += 1
        return sent

    def _deliver(self, row: dict) -> str | None:
        m = mode()
        if m == "disabled":
            raise MailNotConfigured()
        msg = EmailMessage()
        name, addr = from_address()
        msg["From"] = formataddr((name, addr))
        msg["To"] = row["to_addr"]
        msg["Subject"] = row["subject"]
        msg["Date"] = formatdate(row["created_at"], localtime=True)
        msg["Message-ID"] = f"<{row['id']}@{addr.rsplit('@', 1)[-1]}>"
        # Tells well-behaved scanners and clients not to auto-fetch or auto-reply.
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
        msg.set_content(row["body"])

        if m == "file":
            # Under the data directory by default, never the process's working directory: these
            # files hold live links, and a cwd-relative default scattered them wherever the
            # service happened to be started from.
            out = Path(env.get("MAIL_DIR") or Path(self.store.root) / "mail")
            out.mkdir(parents=True, exist_ok=True)
            # The banner exists so nobody mistakes a development sink for delivery.
            banner = ("X-Synwevo-Delivery: LOCAL FILE SINK — 这封邮件没有真正寄出，"
                      "仅写入本地文件用于开发验证\n")
            (out / f"{int(time.time() * 1000)}-{row['id']}.eml").write_text(banner + msg.as_string(), encoding="utf-8")
            return

        config = smtp_config()
        context = ssl.create_default_context()  # CERT_REQUIRED + hostname verification; no opt-out.
        smtp = None
        try:
            if config.implicit_tls:
                smtp = smtplib.SMTP_SSL(config.host, config.port, timeout=config.timeout, context=context)
            else:
                smtp = smtplib.SMTP(config.host, config.port, timeout=config.timeout)
                smtp.ehlo()
                smtp.starttls(context=context)  # A missing/rejected STARTTLS is terminal, never plaintext AUTH.
            smtp.ehlo()
            if config.user:
                smtp.login(config.user, config.password)
            code, _ = smtp.mail(addr)
            if code != 250:
                raise DeliveryFailure(f"smtp_sender_rejected_{code}", retryable=400 <= code < 500)
            code, _ = smtp.rcpt(row["to_addr"])
            if code not in (250, 251):
                raise DeliveryFailure(f"smtp_recipient_rejected_{code}", retryable=400 <= code < 500)
            try:
                code, response = smtp.data(msg.as_bytes(policy=SMTP_POLICY))
            except smtplib.SMTPResponseException:
                raise  # An explicit refusal is distinguishable from a lost acknowledgement.
            except (OSError, smtplib.SMTPServerDisconnected):
                raise DeliveryFailure("delivery_unknown") from None
            if code != 250:
                raise DeliveryFailure(f"smtp_data_rejected_{code}", retryable=400 <= code < 500)
            # Only keep a bounded queue identifier, never the arbitrary SMTP response text.
            match = re.search(rb"queued as ([A-Za-z0-9]{5,64})(?:\b|$)", response)
            return match.group(1).decode("ascii") if match else "accepted"
        finally:
            # A failing QUIT after DATA 250 must not turn a delivered message into a retry.
            if smtp is not None:
                try:
                    smtp.close()
                except OSError:
                    pass

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


# ---------------------------------------------------------------------- message bodies
_TOKEN_IN_LINK = re.compile(r"([?&]token=)[A-Za-z0-9_\-]+")
REDACTED = "[已寄出，链接不在库内保留]"


def redact(body: str) -> str:
    """The message with every link token replaced — what the outbox keeps once a mail is done."""
    return _TOKEN_IN_LINK.sub(lambda m: m.group(1) + REDACTED, body or "")


def _link(path: str, token: str) -> str:
    base = public_base() or "http://127.0.0.1:8765"
    return f"{base}{path}?token={token}"


def invitation_mail(*, workspace: str, inviter: str, role_label: str, token: str, expires_h: int) -> tuple[str, str]:
    return (f"邀请你加入 Synwevo 工作空间「{workspace}」",
            f"""{inviter} 邀请你以「{role_label}」身份加入 Synwevo 工作空间「{workspace}」。

打开下面的链接接受邀请：

  {_link('/invite', token)}

链接 {expires_h} 小时内有效，只能使用一次。打开链接后还需要你点击确认，
所以邮件安全扫描器预读这个链接不会替你接受邀请。

如果你不认识邀请人，忽略这封邮件即可，邀请会自动过期。本邮件无需回复。
""")


def verify_mail(*, token: str, expires_h: int) -> tuple[str, str]:
    return ("验证你的 Synwevo 邮箱",
            f"""请打开下面的链接验证这个邮箱地址：

  {_link('/verify-email', token)}

链接 {expires_h} 小时内有效，只能使用一次。打开后需要你点击确认才会生效。

如果这不是你本人的操作，忽略这封邮件即可。本邮件无需回复。
""")


def reset_mail(*, token: str, expires_h: int) -> tuple[str, str]:
    return ("重置你的 Synwevo 口令",
            f"""我们收到了重置这个账户口令的请求。打开下面的链接设置新口令：

  {_link('/reset-password', token)}

链接 {expires_h} 小时内有效，只能使用一次。

如果这不是你本人的操作，你不需要做任何事——没有点击链接，口令不会改变。
若你担心账户安全，请登录后在「账户与安全」里结束其他设备的登录。本邮件无需回复。
""")


def security_notice(*, what: str, when_text: str) -> tuple[str, str]:
    return (f"Synwevo 安全提醒：{what}",
            f"""你的 Synwevo 账户刚刚发生了一项安全相关的变更：

  {what}
  时间：{when_text}

如果这是你本人的操作，不需要做任何事。
如果不是，请立即登录、修改口令，并在「账户与安全」里结束其他设备的登录。本邮件无需回复。
""")
