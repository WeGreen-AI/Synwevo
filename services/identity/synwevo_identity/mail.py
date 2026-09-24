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

import os
import smtplib
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, new_id, now

# Backoff between attempts; the last value repeats until MAX_ATTEMPTS.
RETRY_SCHEDULE = (30, 120, 600, 1800, 3600)
MAX_ATTEMPTS = 8
SEND_TIMEOUT_S = float(env.get("SMTP_TIMEOUT_S", "20"))


def mode() -> str:
    """smtp | file | disabled — decided by configuration, never guessed."""
    explicit = (env.get("MAIL_MODE") or "").strip().lower()
    if explicit in ("smtp", "file", "disabled"):
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
                print(f"[synwevo] mail worker error: {e}", flush=True)
                sent = 0
            self._wake.wait(1.0 if sent else 15.0)
            self._wake.clear()

    def drain_once(self, limit: int = 20) -> int:
        """Attempt every message that is due. Returns how many were sent."""
        due = self.store.all(
            "SELECT * FROM mail_outbox WHERE status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
            "ORDER BY created_at LIMIT ?", (now(), limit))
        sent = 0
        for row in due:
            try:
                self._deliver(row)
                self.store.execute("UPDATE mail_outbox SET status='sent', sent_at=?, last_error=NULL WHERE id=?",
                                   (now(), row["id"]))
                sent += 1
            except Exception as e:  # noqa: BLE001 - every failure is recorded, never swallowed
                attempts = int(row["attempts"]) + 1
                if attempts >= MAX_ATTEMPTS:
                    # Terminal. The row stays so the failure remains visible in the UI and the
                    # audit trail; it is not deleted to make a dashboard look clean.
                    self.store.execute("UPDATE mail_outbox SET status='failed', attempts=?, last_error=? WHERE id=?",
                                       (attempts, str(e)[:500], row["id"]))
                else:
                    delay = RETRY_SCHEDULE[min(attempts - 1, len(RETRY_SCHEDULE) - 1)]
                    self.store.execute(
                        "UPDATE mail_outbox SET attempts=?, next_attempt_at=?, last_error=? WHERE id=?",
                        (attempts, now() + delay, str(e)[:500], row["id"]))
        return sent

    def _deliver(self, row: dict) -> None:
        m = mode()
        if m == "disabled":
            raise MailNotConfigured()
        msg = EmailMessage()
        name, addr = from_address()
        msg["From"] = formataddr((name, addr))
        msg["To"] = row["to_addr"]
        msg["Subject"] = row["subject"]
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid()
        # Tells well-behaved scanners and clients not to auto-fetch or auto-reply.
        msg["Auto-Submitted"] = "auto-generated"
        msg["X-Auto-Response-Suppress"] = "All"
        msg.set_content(row["body"])

        if m == "file":
            out = Path(env.get("MAIL_DIR") or "./data/mail")
            out.mkdir(parents=True, exist_ok=True)
            # The banner exists so nobody mistakes a development sink for delivery.
            banner = ("X-Synwevo-Delivery: LOCAL FILE SINK — 这封邮件没有真正寄出，"
                      "仅写入本地文件用于开发验证\n")
            (out / f"{int(time.time() * 1000)}-{row['id']}.eml").write_text(banner + msg.as_string(), encoding="utf-8")
            return

        host = env.get("SMTP_HOST") or ""
        port = int(env.get("SMTP_PORT") or 587)
        user = env.get("SMTP_USER") or ""
        pw = env.get("SMTP_PASSWORD") or ""
        use_ssl = (env.get("SMTP_SSL") or "").strip().lower() in ("1", "true", "yes", "on")
        starttls = (env.get("SMTP_STARTTLS") or "1").strip().lower() not in ("0", "false", "no", "off")
        cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with cls(host, port, timeout=SEND_TIMEOUT_S) as s:
            if starttls and not use_ssl:
                s.starttls()
            if user:
                s.login(user, pw)
            s.send_message(msg)

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


# ---------------------------------------------------------------------- message bodies
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
