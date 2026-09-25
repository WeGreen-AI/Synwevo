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
        # Quoted-printable, not base64: our bodies are mostly text with some CJK, and several
        # institutional filters (rspamd's MIME_BASE64_TEXT among them) score base64-encoded text
        # as a spam signal. Quoted-printable stays 7-bit safe on every relay.
        msg.set_content(row["body"], cte="quoted-printable")

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


def login_code_mail(*, code: str, expires_min: int, locale: str = "zh-Hans") -> tuple[str, str]:
    """A one-time sign-in code. Deliberately a code rather than a link: nothing a mail scanner
    can follow, and the person types it into the page they already have open."""
    if locale == "en":
        return ("Your Synwevo sign-in code",
                f"Sign-in code: {code}\n\n"
                f"Enter this code on the Synwevo sign-in page within {expires_min} minutes. It works once. "
                "Synwevo will never ask you for it by phone or chat — anyone asking for it is not us.\n\n"
                "If you did not try to sign in, ignore this message; nobody can use the code without "
                "also having this mailbox. Replies are not monitored.")
    if locale == "zh-Hant":
        return ("你的 Synwevo 登入驗證碼",
                f"登入驗證碼：{code}\n\n"
                f"請在 {expires_min} 分鐘內於 Synwevo 登入頁面輸入此驗證碼，僅可使用一次。"
                "Synwevo 不會透過電話或聊天向你索取驗證碼，任何索取者都不是我們。\n\n"
                "若非你本人嘗試登入，請忽略這封郵件；沒有這個信箱，任何人都無法使用此驗證碼。請勿直接回覆。")
    return ("你的 Synwevo 登录验证码",
            f"""登录验证码：{code}

请在 {expires_min} 分钟内，在 Synwevo 登录页面输入这个验证码。它只能使用一次。
Synwevo 不会通过电话或聊天向你索要验证码——任何索要验证码的人都不是我们。

如果不是你本人在尝试登录，忽略这封邮件即可：没有这个邮箱，谁也用不了这个验证码。本邮件无需回复。
""")


def _link(path: str, token: str, locale: str = "zh-Hans") -> str:
    base = public_base() or "http://127.0.0.1:8765"
    locale = locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
    return f"{base}{path}?lang={locale}#token={token}"


def invitation_mail(*, workspace: str, inviter: str, role_label: str, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str]:
    if locale == "en":
        role = {"只读成员": "viewer", "编辑成员": "editor", "空间管理员": "workspace administrator", "空间所有者": "workspace owner"}.get(role_label, role_label)
        return (f"Join {workspace} on Synwevo",
                f"{inviter} invited you to {workspace} as {role}.\n\n{_link('/invite', token, locale)}\n\n"
                f"This single-use link expires in {expires_h} hours. Opening it does not accept the invitation; confirm on the page. "
                "Ignore this message if you do not recognize the inviter. Replies are not monitored.")
    if locale == "zh-Hant":
        role = {"只读成员": "檢視成員", "编辑成员": "編輯成員", "空间管理员": "工作空間管理員", "空间所有者": "工作空間擁有者"}.get(role_label, role_label)
        return (f"邀請你加入 Synwevo 工作空間「{workspace}」",
                f"{inviter} 邀請你以「{role}」身分加入「{workspace}」。\n\n{_link('/invite', token, locale)}\n\n"
                f"連結有效期限為 {expires_h} 小時，僅可使用一次。開啟後仍須按下確認，才會接受邀請。"
                "若你不認識邀請者，請忽略這封郵件。請勿直接回覆。")
    return (f"邀请你加入 Synwevo 工作空间「{workspace}」",
            f"""{inviter} 邀请你以「{role_label}」身份加入 Synwevo 工作空间「{workspace}」。

打开下面的链接接受邀请：

  {_link('/invite', token, locale)}

链接 {expires_h} 小时内有效，只能使用一次。打开链接后还需要你点击确认，
所以邮件安全扫描器预读这个链接不会替你接受邀请。

如果你不认识邀请人，忽略这封邮件即可，邀请会自动过期。本邮件无需回复。
""")


def verify_mail(*, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str]:
    if locale == "en":
        return ("Verify your Synwevo email", f"Confirm your email address:\n\n{_link('/verify-email', token, locale)}\n\n"
                f"This single-use link expires in {expires_h} hours. Confirm on the page to complete verification. "
                "If you did not request this, ignore this message. Replies are not monitored.")
    if locale == "zh-Hant":
        return ("驗證你的 Synwevo 電子郵件地址", f"請開啟以下連結驗證電子郵件地址：\n\n{_link('/verify-email', token, locale)}\n\n"
                f"連結有效期限為 {expires_h} 小時，僅可使用一次。請在頁面按下確認以完成驗證。"
                "若非你本人操作，請忽略這封郵件。請勿直接回覆。")
    return ("验证你的 Synwevo 邮箱",
            f"""请打开下面的链接验证这个邮箱地址：

  {_link('/verify-email', token, locale)}

链接 {expires_h} 小时内有效，只能使用一次。打开后需要你点击确认才会生效。

如果这不是你本人的操作，忽略这封邮件即可。本邮件无需回复。
""")


def account_exists_mail(*, locale: str = "zh-Hans") -> tuple[str, str]:
    """Sent to a mailbox that just tried to register while it already has an active account.

    The HTTP answer to that registration is the same "check your inbox" as for a new address (no
    enumeration), so without this mail the person waits for a verification link that will never
    come. Telling the mailbox owner — and only them — is the OWASP-recommended way out.
    """
    base = public_base() or "http://127.0.0.1:8765"
    lang = locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
    login, forgot = f"{base}/login?lang={lang}", f"{base}/forgot?lang={lang}"
    if lang == "en":
        return ("Your Synwevo account already exists",
                "Someone — probably you — just tried to register a Synwevo account with this address, but an account "
                f"for it already exists.\n\nSign in here:\n\n{login}\n\nForgot the password? Reset it here:\n\n{forgot}\n\n"
                "If this was not you, nothing has changed: no new account was created and your password was not touched. "
                "Replies are not monitored.")
    if lang == "zh-Hant":
        return ("你的 Synwevo 帳戶已經存在",
                "剛才有人（很可能是你本人）用這個電子郵件地址註冊 Synwevo，但這個地址已經有帳戶了。\n\n"
                f"直接登入：\n\n{login}\n\n忘記密碼？在這裡重設：\n\n{forgot}\n\n"
                "若不是你本人操作，也不需要做任何事：沒有建立新帳戶，你的密碼也沒有變動。請勿直接回覆。")
    return ("你的 Synwevo 账户已经存在",
            f"""刚才有人（很可能是你本人）用这个邮箱注册 Synwevo，但这个邮箱已经有账户了。

直接登录：

  {login}

忘记口令？在这里重置：

  {forgot}

如果这不是你本人的操作，你不需要做任何事：没有创建新账户，你的口令也没有改变。本邮件无需回复。
""")


def reset_mail(*, token: str, expires_h: int, locale: str = "zh-Hans") -> tuple[str, str]:
    if locale == "en":
        return ("Reset your Synwevo password", f"Set a new password:\n\n{_link('/reset-password', token, locale)}\n\n"
                f"This single-use link expires in {expires_h} hours. Opening it does not change your password. "
                "If you did not request this, ignore this message. Sign in to review other sessions if you are concerned. Replies are not monitored.")
    if locale == "zh-Hant":
        return ("重設你的 Synwevo 密碼", f"請開啟以下連結設定新密碼：\n\n{_link('/reset-password', token, locale)}\n\n"
                f"連結有效期限為 {expires_h} 小時，僅可使用一次。開啟連結不會變更密碼。"
                "若非你本人操作，請忽略這封郵件；如有安全疑慮，請登入並撤銷其他裝置的工作階段。請勿直接回覆。")
    return ("重置你的 Synwevo 口令",
            f"""我们收到了重置这个账户口令的请求。打开下面的链接设置新口令：

  {_link('/reset-password', token, locale)}

链接 {expires_h} 小时内有效，只能使用一次。

如果这不是你本人的操作，你不需要做任何事——没有点击链接，口令不会改变。
若你担心账户安全，请登录后在「账户与安全」里结束其他设备的登录。本邮件无需回复。
""")


def security_notice(*, what: str, when_text: str, locale: str = "zh-Hans") -> tuple[str, str]:
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
    if locale == "en":
        return ("Synwevo security notice: " + translated[0], translated[0] + ".\n\n"
                "If this was not you, sign in, change your password and revoke other sessions. "
                "Security notices cannot be disabled. Replies are not monitored.")
    if locale == "zh-Hant":
        return ("Synwevo 帳戶安全通知：" + translated[1], translated[1] + "。\n\n"
                "若非你本人操作，請立即登入、變更密碼並撤銷其他裝置的工作階段。安全通知無法停用。請勿直接回覆。")
    return (f"Synwevo 安全提醒：{what}",
            f"""你的 Synwevo 账户刚刚发生了一项安全相关的变更：

  {what}
  时间：{when_text}

如果这是你本人的操作，不需要做任何事。
如果不是，请立即登录、修改口令，并在「账户与安全」里结束其他设备的登录。本邮件无需回复。
""")


def email_change_mail(*, token: str, expires_h: int = 24, locale: str = "zh-Hans") -> tuple[str, str]:
    link = _link("/account", token, locale) + "&action=email-change"
    if locale == "en":
        return ("Confirm your new Synwevo email", f"Confirm this address on the account page:\n\n{link}\n\nThe link expires in {expires_h} hours. Opening it does not change your address. Ignore it if you did not request the change.")
    if locale == "zh-Hant":
        return ("確認你的新 Synwevo 電子郵件地址", f"請在帳戶頁面確認新地址：\n\n{link}\n\n連結有效期限為 {expires_h} 小時。開啟連結不會變更地址。若非本人操作，請忽略。")
    return ("确认新的 Synwevo 邮箱", f"请在账户页面确认新地址：\n\n{link}\n\n链接 {expires_h} 小时内有效。打开链接不会变更邮箱。若非本人操作，请忽略。")
