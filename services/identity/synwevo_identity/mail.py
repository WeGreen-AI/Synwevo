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
import os
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
from synwevo_core import workers

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
            workers.register(self, self._thread)

    # ------------------------------------------------------------------ enqueue
    def alive(self) -> bool:
        """Is the delivery thread running? Reported by /readyz on the account service.

        A mailer that has died takes password resets and invitations with it, and the failure is
        silent — the row is queued and nothing sends it. Started with ``start=False`` (tests,
        one-shot CLI) it reports alive, because there is no worker that was supposed to be there.
        """
        return self._thread is None or self._thread.is_alive()

    def enqueue(self, to_addr: str, subject: str, body: str, *, kind: str, ref_id: str | None = None, html: str | None = None) -> dict:
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
        row = {"id": new_id("ml"), "to_addr": to_addr, "subject": subject, "body": body, "html": html,
               "kind": kind, "ref_id": ref_id, "status": "queued", "attempts": 0,
               "next_attempt_at": now(), "created_at": now()}
        self.store.execute(
            "INSERT INTO mail_outbox (id,to_addr,subject,body,html,kind,ref_id,status,attempts,next_attempt_at,created_at) "
            "VALUES (:id,:to_addr,:subject,:body,:html,:kind,:ref_id,:status,:attempts,:next_attempt_at,:created_at)", row)
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
            "SELECT id,body,html FROM mail_outbox WHERE status='queued' AND last_error=? "
            "AND next_attempt_at<=?", (IN_PROGRESS, now()))
        for row in expired:
            self.store.execute(
                "UPDATE mail_outbox SET status='failed', last_error='delivery_unknown', "
                "body=?, html=?, next_attempt_at=NULL WHERE id=? AND status='queued' AND last_error=?",
                (redact(row["body"]), redact(row["html"]) if row.get("html") else None, row["id"], IN_PROGRESS))
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
                        "UPDATE mail_outbox SET status='failed', last_error=?, body=?, html=?, next_attempt_at=NULL "
                        "WHERE id=? AND status='queued' AND last_error=?",
                        (failure.code, redact(row["body"]), redact(row["html"]) if row.get("html") else None, row["id"], IN_PROGRESS))
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
                "UPDATE mail_outbox SET status='sent', sent_at=?, last_error=NULL, body=?, html=?, next_attempt_at=NULL "
                "WHERE id=? AND status='queued' AND last_error=?",
                (now(), redact(row["body"]), redact(row["html"]) if row.get("html") else None, row["id"], IN_PROGRESS))
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
        # Quoted-printable, not base64: our bodies are mostly text with some CJK, and several
        # institutional filters (rspamd's MIME_BASE64_TEXT among them) score base64-encoded text
        # as a spam signal. Quoted-printable stays 7-bit safe on every relay.
        msg.set_content(row["body"], cte="quoted-printable")
        if row.get("html"):
            # The same content as the text part, in the shared layout (mail_layout.render): a
            # client that renders HTML gets the card and the button, every other reads the text.
            msg.add_alternative(row["html"], subtype="html", cte="quoted-printable")

        if m == "file":
            # Under the data directory by default, never the process's working directory: these
            # files hold live links, and a cwd-relative default scattered them wherever the
            # service happened to be started from.
            out = Path(env.get("MAIL_DIR") or Path(self.store.root) / "mail")
            out.mkdir(parents=True, mode=0o700, exist_ok=True)
            out.chmod(0o700)
            # The banner exists so nobody mistakes a development sink for delivery.
            banner = ("X-Synwevo-Delivery: LOCAL FILE SINK — 这封邮件没有真正寄出，"
                      "仅写入本地文件用于开发验证\n")
            target = out / f"{int(time.time() * 1000)}-{row['id']}.eml"
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as saved:
                saved.write(banner + msg.as_string())
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
_TOKEN_IN_LINK = re.compile(r"([?&#]token=)[A-Za-z0-9_\-]+")
# The sign-in code line in every locale's template below: "<label>: 123456".
_CODE_LINE = re.compile(r"((?:登录验证码|登入驗證碼|Sign-in code)[:：]\s*)\d{6}\b")
REDACTED = "[已寄出，链接不在库内保留]"


def redact(body: str) -> str:
    """The message with every link token and sign-in code replaced — what the outbox keeps once
    a mail is done, and what an operator sees in the queue."""
    body = _TOKEN_IN_LINK.sub(lambda m: m.group(1) + REDACTED, body or "")
    return _CODE_LINE.sub(lambda m: m.group(1) + REDACTED, body)


def _lang(locale: str | None) -> str:
    return locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"


def _render(m) -> tuple[str, str, str]:
    from .mail_layout import render
    text, html = render(m)
    return m.title, text, html


def login_code_mail(*, code: str, expires_min: int, locale: str = "zh-Hans") -> tuple[str, str, str]:
    """A one-time sign-in code. Deliberately a code rather than a link: nothing a mail scanner
    can follow, and the person types it into the page they already have open."""
    from .mail_layout import Mail
    lang = _lang(locale)
    if lang == "en":
        m = Mail(lang, "Your Synwevo sign-in code", ["Enter this code on the Synwevo sign-in page you already have open."], code=code, code_label="Sign-in code",
                 after=[f"It works once and expires in {expires_min} minutes.",
                        "If you did not try to sign in, ignore this message: nobody can use the code without this mailbox."])
    elif lang == "zh-Hant":
        m = Mail(lang, "你的 Synwevo 登入驗證碼", ["請在你已開啟的 Synwevo 登入頁面輸入這個驗證碼。"], code=code, code_label="登入驗證碼",
                 after=[f"驗證碼僅可使用一次，{expires_min} 分鐘內有效。", "若非你本人嘗試登入，請忽略這封郵件：沒有這個信箱，任何人都無法使用此驗證碼。"])
    else:
        m = Mail(lang, "你的 Synwevo 登录验证码", ["请在你已经打开的 Synwevo 登录页面输入这个验证码。"], code=code, code_label="登录验证码",
                 after=[f"验证码只能使用一次，{expires_min} 分钟内有效。", "如果不是你本人在尝试登录，忽略这封邮件即可：没有这个邮箱，谁也用不了这个验证码。"])
    return _render(m)


def _link(path: str, token: str, locale: str = "zh-Hans") -> str:
    base = public_base() or "http://127.0.0.1:8765"
    locale = locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
    return f"{base}{path}?lang={locale}#token={token}"


def invitation_mail(*, workspace: str, inviter: str, role_label: str, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str, str]:
    from .mail_layout import Mail
    lang = _lang(locale)
    url = _link("/invite", token, lang)
    if lang == "en":
        role = {"只读成员": "viewer", "编辑成员": "editor", "空间管理员": "workspace administrator", "空间所有者": "workspace owner"}.get(role_label, role_label)
        m = Mail(lang, f"Join {workspace} on Synwevo", [f"{inviter} invited you to the workspace {workspace} as {role}."],
                 facts=[("Workspace", workspace), ("Invited by", inviter), ("Role", role)], action=("Accept the invitation", url),
                 after=[f"The link works once and expires in {expires_h} hours. Opening it does not accept the invitation; you confirm on the page.",
                        "Ignore this message if you do not recognize the inviter; the invitation expires by itself."])
    elif lang == "zh-Hant":
        role = {"只读成员": "檢視成員", "编辑成员": "編輯成員", "空间管理员": "工作空間管理員", "空间所有者": "工作空間擁有者"}.get(role_label, role_label)
        m = Mail(lang, f"邀請你加入 Synwevo 工作空間「{workspace}」", [f"{inviter} 邀請你以「{role}」身分加入工作空間「{workspace}」。"],
                 facts=[("工作空間", workspace), ("邀請人", inviter), ("身分", role)], action=("接受邀請", url),
                 after=[f"連結僅可使用一次，{expires_h} 小時內有效。開啟後仍須在頁面按下確認，才會接受邀請。", "若你不認識邀請者，請忽略這封郵件，邀請會自動過期。"])
    else:
        m = Mail(lang, f"邀请你加入 Synwevo 工作空间「{workspace}」", [f"{inviter} 邀请你以「{role_label}」身份加入工作空间「{workspace}」。"],
                 facts=[("工作空间", workspace), ("邀请人", inviter), ("身份", role_label)], action=("接受邀请", url),
                 after=[f"链接只能使用一次，{expires_h} 小时内有效。打开链接后还需要你在页面上点击确认，邮件安全扫描器预读链接不会替你接受邀请。",
                        "如果你不认识邀请人，忽略这封邮件即可，邀请会自动过期。"])
    return _render(m)


def verify_mail(*, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str, str]:
    from .mail_layout import Mail
    lang = _lang(locale)
    url = _link("/verify-email", token, lang)
    if lang == "en":
        m = Mail(lang, "Verify your Synwevo email", ["Confirm that this address is yours to finish setting up your Synwevo account."], action=("Verify this address", url),
                 after=[f"The link works once and expires in {expires_h} hours. Opening it does not verify anything by itself; you confirm on the page.",
                        "If you did not request this, ignore this message."])
    elif lang == "zh-Hant":
        m = Mail(lang, "驗證你的 Synwevo 電子郵件地址", ["請確認這個地址屬於你，以完成 Synwevo 帳戶設定。"], action=("驗證這個地址", url),
                 after=[f"連結僅可使用一次，{expires_h} 小時內有效。開啟連結本身不會完成驗證，請在頁面按下確認。", "若非你本人操作，請忽略這封郵件。"])
    else:
        m = Mail(lang, "验证你的 Synwevo 邮箱", ["请确认这个邮箱地址是你的，以完成 Synwevo 账户设置。"], action=("验证这个邮箱", url),
                 after=[f"链接只能使用一次，{expires_h} 小时内有效。打开链接本身不会完成验证，需要你在页面上点击确认。", "如果这不是你本人的操作，忽略这封邮件即可。"])
    return _render(m)


def account_exists_mail(*, locale: str = "zh-Hans") -> tuple[str, str, str]:
    """Sent to a mailbox that just tried to register while it already has an active account.

    The HTTP answer to that registration is the same "check your inbox" as for a new address (no
    enumeration), so without this mail the person waits for a verification link that will never
    come. Telling the mailbox owner — and only them — is the OWASP-recommended way out.
    """
    from .mail_layout import Mail
    base = public_base() or "http://127.0.0.1:8765"
    lang = _lang(locale)
    login, forgot = f"{base}/login?lang={lang}", f"{base}/forgot?lang={lang}"
    if lang == "en":
        m = Mail(lang, "Your Synwevo account already exists", ["Someone — probably you — just tried to register a Synwevo account with this address, but an account for it already exists."],
                 action=("Sign in", login), after=[f"Forgot the password? Reset it here: {forgot}",
                                                   "If this was not you, nothing has changed: no new account was created and your password was not touched."])
    elif lang == "zh-Hant":
        m = Mail(lang, "你的 Synwevo 帳戶已經存在", ["剛才有人（很可能是你本人）用這個電子郵件地址註冊 Synwevo，但這個地址已經有帳戶了。"],
                 action=("直接登入", login), after=[f"忘記密碼？在這裡重設：{forgot}", "若不是你本人操作，也不需要做任何事：沒有建立新帳戶，你的密碼也沒有變動。"])
    else:
        m = Mail(lang, "你的 Synwevo 账户已经存在", ["刚才有人（很可能是你本人）用这个邮箱注册 Synwevo，但这个邮箱已经有账户了。"],
                 action=("直接登录", login), after=[f"忘记口令？在这里重置：{forgot}", "如果这不是你本人的操作，你不需要做任何事：没有创建新账户，你的口令也没有改变。"])
    return _render(m)


def reset_mail(*, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str, str]:
    from .mail_layout import Mail
    lang = _lang(locale)
    url = _link("/reset-password", token, lang)
    if lang == "en":
        m = Mail(lang, "Reset your Synwevo password", ["We received a request to reset the password of this account."], action=("Set a new password", url),
                 after=[f"The link works once and expires in {expires_h} hours. Opening it does not change your password.",
                        "If you did not request this, ignore this message; the password stays as it is. Sign in and end other sessions if you are concerned."])
    elif lang == "zh-Hant":
        m = Mail(lang, "重設你的 Synwevo 密碼", ["我們收到了重設這個帳戶密碼的請求。"], action=("設定新密碼", url),
                 after=[f"連結僅可使用一次，{expires_h} 小時內有效。開啟連結不會變更密碼。", "若非你本人操作，請忽略這封郵件，密碼不會改變；如有安全疑慮，請登入並結束其他裝置的工作階段。"])
    else:
        m = Mail(lang, "重置你的 Synwevo 口令", ["我们收到了重置这个账户口令的请求。"], action=("设置新口令", url),
                 after=[f"链接只能使用一次，{expires_h} 小时内有效。打开链接不会改变口令。", "如果这不是你本人的操作，忽略这封邮件即可，口令不会改变；若担心账户安全，请登录后在「账户与安全」里结束其他设备的登录。"])
    return _render(m)


def security_notice(*, what: str, when_text: str, locale: str = "zh-Hans") -> tuple[str, str, str]:
    from .mail_layout import Mail
    events = {
        "登录邮箱已更改": ("Sign-in email changed", "登入電子郵件地址已變更"),
        "口令已重置": ("Password reset", "密碼已重設"),
        "口令已更改": ("Password changed", "密碼已變更"),
        "新增了一个安全密钥（Passkey）": ("A passkey was added", "已新增一組通行密鑰（Passkey）"),
        "删除了一个安全密钥（Passkey）": ("A passkey was removed", "已移除一組通行密鑰（Passkey）"),
        "启用了动态验证码（两步验证）": ("Authenticator verification enabled", "已啟用驗證器動態驗證碼（兩步驟驗證）"),
        "关闭了动态验证码（两步验证）": ("Authenticator verification disabled", "已停用驗證器動態驗證碼（兩步驟驗證）"),
        "重新生成了恢复码，旧的恢复码已全部失效": ("Recovery codes regenerated; all previous codes are invalid", "已重新產生復原碼；所有舊復原碼均已失效"),
    }
    translated = events.get(what, ("An account security setting changed", "帳戶安全設定已變更"))
    lang = _lang(locale)
    base = public_base() or "http://127.0.0.1:8765"
    if lang == "en":
        m = Mail(lang, "Synwevo security notice: " + translated[0], ["A security-related setting of your Synwevo account just changed."],
                 facts=[("What changed", translated[0]), ("When", when_text)], action=("Review account security", f"{base}/account?lang=en"),
                 after=["If this was you, nothing else is needed.", "If it was not, sign in now, change your password and end the sessions on other devices. Security notices cannot be disabled."], security=True)
    elif lang == "zh-Hant":
        m = Mail(lang, "Synwevo 帳戶安全通知：" + translated[1], ["你的 Synwevo 帳戶剛剛發生了一項與安全相關的變更。"],
                 facts=[("變更內容", translated[1]), ("時間", when_text)], action=("檢視帳戶安全", f"{base}/account?lang=zh-Hant"),
                 after=["若這是你本人的操作，不需要再做任何事。", "若不是，請立即登入、變更密碼並結束其他裝置的工作階段。安全通知無法停用。"], security=True)
    else:
        m = Mail(lang, f"Synwevo 安全提醒：{what}", ["你的 Synwevo 账户刚刚发生了一项安全相关的变更。"],
                 facts=[("变更内容", what), ("时间", when_text)], action=("查看账户与安全", f"{base}/account?lang=zh-Hans"),
                 after=["如果这是你本人的操作，不需要做任何事。", "如果不是，请立即登录、修改口令，并在「账户与安全」里结束其他设备的登录。安全提醒无法关闭。"], security=True)
    return _render(m)


def email_change_mail(*, token: str, expires_h: int = 24, locale: str = "zh-Hans") -> tuple[str, str, str]:
    from .mail_layout import Mail
    lang = _lang(locale)
    link = _link("/account", token, lang) + "&action=email-change"
    if lang == "en":
        m = Mail(lang, "Confirm your new Synwevo email", ["This address was entered as the new sign-in email of a Synwevo account. Confirm it on the account page."],
                 action=("Confirm this address", link), after=[f"The link expires in {expires_h} hours. Opening it does not change the address; you confirm on the page.", "Ignore this message if this was not you."])
    elif lang == "zh-Hant":
        m = Mail(lang, "確認你的新 Synwevo 電子郵件地址", ["有人把這個地址設為 Synwevo 帳戶的新登入信箱，請在帳戶頁面確認。"],
                 action=("確認這個地址", link), after=[f"連結 {expires_h} 小時內有效。開啟連結不會變更地址，請在頁面按下確認。", "若非本人操作，請忽略這封郵件。"])
    else:
        m = Mail(lang, "确认新的 Synwevo 邮箱", ["有人把这个地址设为 Synwevo 账户的新登录邮箱，请在账户页面确认。"],
                 action=("确认这个邮箱", link), after=[f"链接 {expires_h} 小时内有效。打开链接不会变更邮箱，需要你在页面上点击确认。", "如果这不是你本人的操作，忽略这封邮件即可。"])
    return _render(m)


def deletion_mail(locale: str | None, url: str, *, resend: bool = False) -> tuple[str, str, str]:
    """Account deletion requested: the account is disabled now and the person has seven days to
    cancel through the link (which opens a page; it neither restores the account nor signs in).
    `resend`: a new cancellation link the platform sent at the person's request (admin coverage
    A20) — the deletion date is the one already set, and the earlier link no longer works."""
    from .mail_layout import Mail
    lang = _lang(locale)
    if resend and lang == "en":
        m = Mail(lang, "Synwevo account deletion: a new cancellation link",
                 ["Your Synwevo account is disabled and will be deleted on the date you were told. This is a new link to cancel the deletion; the earlier link no longer works."],
                 action=("Cancel the deletion", url),
                 after=["Opening the link does not restore the account or sign you in by itself; you confirm on the page.",
                        "If you did not ask for this link, you can ignore it: the deletion date is unchanged."], security=True)
    elif resend and lang == "zh-Hant":
        m = Mail(lang, "Synwevo 帳戶註銷：新的取消連結", ["你的 Synwevo 帳戶已停用，將在原定日期刪除。這是新的取消註銷連結；先前的連結已失效。"],
                 action=("取消註銷", url),
                 after=["開啟連結本身不會復原帳戶或登入，請在頁面上確認。", "若你沒有要求這個連結，可以忽略：刪除日期不變。"], security=True)
    elif resend:
        m = Mail(lang, "Synwevo 账户注销：新的取消链接", ["你的 Synwevo 账户已停用，将在原定日期删除。这是新的取消注销链接；之前的链接已失效。"],
                 action=("取消注销", url),
                 after=["打开链接本身不会恢复账户或登录，需要你在页面上确认。", "如果你没有要求这个链接，可以忽略：删除日期不变。"], security=True)
    elif lang == "en":
        m = Mail(lang, "Synwevo account deletion requested", ["Your Synwevo account is now disabled and will be deleted in seven days."],
                 action=("Cancel the deletion", url),
                 after=["Your contributions to organizations are retained.", "Opening the link does not restore the account or sign you in by itself; you confirm on the page.",
                        "If you did not request this, cancel the deletion now and change your password."], security=True)
    elif lang == "zh-Hant":
        m = Mail(lang, "Synwevo 帳戶註銷申請", ["你的 Synwevo 帳戶已停用，七天後將被刪除。"], action=("取消註銷", url),
                 after=["你在組織中的貢獻將保留。", "開啟連結本身不會復原帳戶或登入，請在頁面上確認。", "若這不是你本人的操作，請立即取消註銷並變更密碼。"], security=True)
    else:
        m = Mail(lang, "Synwevo 账户注销申请", ["你的 Synwevo 账户已停用，七天后将被删除。"], action=("取消注销", url),
                 after=["你在组织中的贡献将保留。", "打开链接本身不会恢复账户或登录，需要你在页面上确认。", "如果这不是你本人的操作，请立即取消注销并修改口令。"], security=True)
    return _render(m)


def _moment(ts: float, timezone: str | None) -> str:
    """A point in time as the account holder reads it: their own time zone, named (UTC when the
    account's zone is missing or unknown)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = timezone or "UTC"
    try:
        zone = ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        name, zone = "UTC", ZoneInfo("UTC")
    return f"{datetime.fromtimestamp(ts, zone).strftime('%Y-%m-%d %H:%M')} ({name})"


def second_factor_reset_requested_mail(locale: str | None, url: str, *, due_at: float, timezone: str | None,
                                       include_passkeys: bool) -> tuple[str, str, str]:
    """A26: a platform administrator, after confirming the person's identity outside the platform,
    asked for the account's second factor to be removed. Nothing changes until `due_at`; the link
    (to a page, single use) cancels it. The administrator is named by role, never by address."""
    from .mail_layout import Mail
    lang = _lang(locale)
    when = _moment(due_at, timezone)
    if lang == "en":
        removed = "your authenticator app, every recovery code" + (" and every passkey" if include_passkeys else "")
        m = Mail(lang, "Synwevo: a reset of your second factor was requested",
                 ["A Synwevo platform administrator confirmed your identity and asked for the second factor of your account to be reset."],
                 facts=[("Takes effect", when), ("Will be removed", removed)], action=("Cancel the reset", url),
                 after=["Until then nothing changes: you sign in as before.",
                        "When it takes effect, the factors above are removed and every device is signed out; you then sign in with your password and set up authenticator verification again.",
                        "If you did not ask for this, cancel it now with the link above and nothing changes. Opening the link does not cancel by itself; you confirm on the page."],
                 security=True)
    elif lang == "zh-Hant":
        removed = "驗證器動態驗證碼、全部復原碼" + ("與全部通行密鑰（Passkey）" if include_passkeys else "")
        m = Mail(lang, "Synwevo：有人申請重設你的第二因素", ["一位 Synwevo 平台管理員在核實你的身分後，申請重設你帳戶的第二因素。"],
                 facts=[("生效時間", when), ("將移除", removed)], action=("取消重設", url),
                 after=["生效前不會有任何變更，你可以照常登入。",
                        "生效時會移除上列第二因素，並結束你在所有裝置上的登入；之後請以密碼登入，再重新設定驗證器動態驗證碼。",
                        "若不是你本人要求的，請立即用上方連結取消，取消後不會有任何變更。開啟連結本身不會取消，請在頁面上確認。"],
                 security=True)
    else:
        removed = "动态验证码、全部恢复码" + ("和全部安全密钥（Passkey）" if include_passkeys else "")
        m = Mail(lang, "Synwevo：有人申请重置你的第二因素", ["一名 Synwevo 平台管理员在核实你的身份后，申请重置你账户的第二因素。"],
                 facts=[("生效时间", when), ("将移除", removed)], action=("取消重置", url),
                 after=["生效前什么都不会改变，你可以照常登录。",
                        "生效时会移除上面列出的第二因素，并结束你在所有设备上的登录；之后请用口令登录，再重新设置动态验证码。",
                        "如果这不是你本人要求的，请立即用上面的链接取消，取消后什么都不会改变。打开链接本身不会取消，需要你在页面上确认。"],
                 security=True)
    return _render(m)


def second_factor_reset_cancelled_mail(locale: str | None, *, by_holder: bool) -> tuple[str, str, str]:
    """A26: the requested reset will not happen — cancelled through the mailed link, or by a
    platform administrator. Nothing about the account changed."""
    from .mail_layout import Mail
    lang = _lang(locale)
    base = public_base() or "http://127.0.0.1:8765"
    if lang == "en":
        m = Mail(lang, "Synwevo: the second-factor reset was cancelled",
                 ["You cancelled the reset of your second factor." if by_holder
                  else "A Synwevo platform administrator cancelled the reset of your second factor."],
                 action=("Review account security", f"{base}/account?lang=en"),
                 after=["Your authenticator app, recovery codes and passkeys are unchanged.",
                        "If this was not you, sign in and review your account security."], security=True)
    elif lang == "zh-Hant":
        m = Mail(lang, "Synwevo：第二因素重設已取消", ["你已取消第二因素重設。" if by_holder else "一位 Synwevo 平台管理員取消了第二因素重設。"],
                 action=("檢視帳戶安全", f"{base}/account?lang=zh-Hant"),
                 after=["你的驗證器動態驗證碼、復原碼與通行密鑰都沒有變更。", "若不是你本人操作，請登入檢視帳戶安全。"], security=True)
    else:
        m = Mail(lang, "Synwevo：第二因素重置已取消", ["你已取消第二因素重置。" if by_holder else "一名 Synwevo 平台管理员取消了第二因素重置。"],
                 action=("查看账户与安全", f"{base}/account?lang=zh-Hans"),
                 after=["你的动态验证码、恢复码和安全密钥都没有改变。", "如果这不是你本人的操作，请登录后在「账户与安全」里检查。"], security=True)
    return _render(m)


def second_factor_reset_completed_mail(locale: str | None, *, passkeys_removed: bool) -> tuple[str, str, str]:
    """A26: the announced reset took effect. The second factor is gone and every device signed
    out; the person signs in with the password and enrols again."""
    from .mail_layout import Mail
    lang = _lang(locale)
    base = public_base() or "http://127.0.0.1:8765"
    if lang == "en":
        m = Mail(lang, "Synwevo: your second factor was reset",
                 ["As announced, the authenticator app and every recovery code of your account" + (" and every passkey" if passkeys_removed else "")
                  + " were removed, and every device was signed out."],
                 action=("Sign in with your password", f"{base}/login?lang=en"),
                 after=["After signing in, set up authenticator verification again under Account → Security and keep the new recovery codes.",
                        "If you never asked for this reset, sign in now, change your password and contact the platform administrators."], security=True)
    elif lang == "zh-Hant":
        m = Mail(lang, "Synwevo：你的第二因素已重設",
                 ["依先前通知的申請，你帳戶的驗證器動態驗證碼與全部復原碼" + ("及全部通行密鑰" if passkeys_removed else "") + "已移除，所有裝置上的登入也已結束。"],
                 action=("以密碼登入", f"{base}/login?lang=zh-Hant"),
                 after=["登入後請立即在「帳戶與安全」重新設定驗證器動態驗證碼，並保存新的復原碼。", "若你從未要求重設，請立即登入、變更密碼並聯絡平台管理員。"], security=True)
    else:
        m = Mail(lang, "Synwevo：你的第二因素已重置",
                 ["按之前通知的申请，你账户的动态验证码和全部恢复码" + ("以及全部安全密钥" if passkeys_removed else "") + "已移除，所有设备上的登录也已结束。"],
                 action=("用口令登录", f"{base}/login?lang=zh-Hans"),
                 after=["登录后请立即在「账户与安全」里重新设置动态验证码，并保存新的恢复码。", "如果你从未要求过重置，请立即登录、修改口令并联系平台管理员。"], security=True)
    return _render(m)


def second_factor_reset_void_mail(locale: str | None, code: str) -> tuple[str, str, str]:
    """A26: the announced reset fell due and was not carried out (`code`, a `VOID_WORDS` key, worded
    in the reader's language). Sent only when the reason is not about this mailbox itself."""
    from synwevo_core.notifications import VOID_WORDS
    from .mail_layout import Mail
    lang = _lang(locale)
    base = public_base() or "http://127.0.0.1:8765"
    why = VOID_WORDS.get(code, {}).get(lang)
    if lang == "en":
        m = Mail(lang, "Synwevo: the scheduled second-factor reset did not take place",
                 ["The reset of your second factor that a Synwevo platform administrator asked for was not carried out"
                  + (f": when it fell due, {why}." if why else ".")],
                 action=("Review account security", f"{base}/account?lang=en"),
                 after=["Your authenticator app, recovery codes and passkeys are unchanged.",
                        "If you still cannot sign in, contact the platform administrators again."], security=True)
    elif lang == "zh-Hant":
        m = Mail(lang, "Synwevo：預定的第二因素重設沒有執行",
                 ["Synwevo 平台管理員為你申請的第二因素重設沒有執行" + (f"：到期時{why}。" if why else "。")],
                 action=("檢視帳戶安全", f"{base}/account?lang=zh-Hant"),
                 after=["你的驗證器動態驗證碼、復原碼與通行密鑰都沒有變更。", "若你仍無法登入，請再次聯絡平台管理員。"], security=True)
    else:
        m = Mail(lang, "Synwevo：预定的第二因素重置没有执行",
                 ["Synwevo 平台管理员为你申请的第二因素重置没有执行" + (f"：到期时{why}。" if why else "。")],
                 action=("查看账户与安全", f"{base}/account?lang=zh-Hans"),
                 after=["你的动态验证码、恢复码和安全密钥都没有改变。", "如果你仍然无法登录，请再次联系平台管理员。"], security=True)
    return _render(m)
