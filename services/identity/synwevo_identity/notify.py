"""The e-mail side of the platform inbox: turns pending notification deliveries into outbox mail.

Only the account service has a mailer, so only it runs this. Every role writes inbox rows and
`pending` delivery rows (synwevo_core.notifications); this worker, alone, renders them in the
reader's language and queues them through the same `Mailer` (and so the same `mail_outbox`,
retries and admin view) that invitations use. It is idempotent by construction: a delivery is
claimed by flipping its status inside a transaction before any mail is queued, so two account
processes cannot both mail the same row, and a crash between the flip and the enqueue leaves a
`queued` row without a mail id — visible, and re-driven by the admin outbox, never silently lost.

Reader preferences are applied here, at send time, not at emit time: quiet hours defer the mail
to the end of the window, `digest` groups whatever is due for one reader into one message.
Security notifications ignore both — an attacker who changed a password must not have set the
quiet hours that hide the fact.
"""
from __future__ import annotations

import threading

from synwevo_core.identity import Identity
from synwevo_core.notifications import Notifier
from synwevo_core.push import PushSubscriptions, WebPush, payload_for
from synwevo_core.store import Store, now

from .mail import Mailer, MailNotConfigured, public_base

INTERVAL_S = 30
BATCH = 200

SUBJECT = {"zh-Hans": "Synwevo 通知：{title}", "zh-Hant": "Synwevo 通知：{title}", "en": "Synwevo: {title}"}
DIGEST_SUBJECT = {"zh-Hans": "Synwevo：{n} 条新通知", "zh-Hant": "Synwevo：{n} 則新通知", "en": "Synwevo: {n} new notifications"}
FOOTER = {
    "zh-Hans": "在「账户 → 通知」里可以调整每类通知的邮件方式与免打扰时段；账户安全通知始终发送。本邮件无需回复。",
    "zh-Hant": "在「帳戶 → 通知」可調整各類通知的郵件方式與免打擾時段；帳戶安全通知一律發送。本郵件無需回覆。",
    "en": "Adjust e-mail per category and quiet hours under Account → Notifications; security notices are always sent. Replies are not monitored.",
}
OPEN = {"zh-Hans": "查看", "zh-Hant": "查看", "en": "Open"}


class NotificationDispatcher:
    def __init__(self, store: Store, ident: Identity, mailer: Mailer, *, start: bool = True):
        self.store = store
        self.ident = ident
        self.mailer = mailer
        self.notifier = Notifier(store)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run, name="notify", daemon=True)
            self._thread.start()

    def alive(self) -> bool:
        return self._thread is None or self._thread.is_alive()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(INTERVAL_S):
            try:
                self.run_once()
            except Exception as e:  # noqa: BLE001 - a bad row must not stop the worker
                print(f"[synwevo] notification dispatch error: {type(e).__name__}: {str(e)[:120]}", flush=True)

    # ------------------------------------------------------------------ one pass
    def run_once(self) -> dict:
        due = self.notifier.due_deliveries(BATCH)
        by_user: dict[str, list[dict]] = {}
        for d in due:
            by_user.setdefault(d["user_id"], []).append(d)
        out = {"queued": 0, "deferred": 0, "skipped": 0, "digested": 0}
        for uid, rows in by_user.items():
            user = self.ident.get_user(uid)
            if not user or user.get("deleted_at") or user["status"] not in ("active", "pending"):
                for d in rows:
                    self.notifier.finish_delivery(d["notification_id"], "skipped", error="reader inactive")
                out["skipped"] += len(rows)
                continue
            prefs = self.notifier.preferences(uid)
            locale = user.get("locale") or "zh-Hans"
            quiet_until = self.notifier.quiet_until(prefs)
            send_now, deferred = [], []
            for d in rows:
                if quiet_until and d["severity"] != "critical":
                    deferred.append(d)
                else:
                    send_now.append(d)
            for d in deferred:
                self.notifier.defer_delivery(d["notification_id"], quiet_until)
            out["deferred"] += len(deferred)
            if not send_now:
                continue
            # Claim first: nobody else mails these rows even if this process dies mid-way.
            claimed = []
            with self.store.tx() as t:
                for d in send_now:
                    n = t.execute("UPDATE notification_deliveries SET status='queued', updated_at=? WHERE notification_id=? AND channel='email' AND status='pending'",
                                  (now(), d["notification_id"])).rowcount
                    if n:
                        claimed.append(d)
            if not claimed:
                continue
            critical = [d for d in claimed if d["severity"] == "critical"]
            others = [d for d in claimed if d["severity"] != "critical"]
            for d in critical:
                self._mail_one(user, d, locale)
                out["queued"] += 1
            if len(others) == 1:
                self._mail_one(user, others[0], locale)
                out["queued"] += 1
            elif others:
                self._mail_digest(user, others, locale)
                out["queued"] += len(others)
                out["digested"] += 1
        out.update(self._push_pass())
        return out

    # ------------------------------------------------------------------ Web Push
    def _push_pass(self) -> dict:
        """One item at a time to every live device of the reader; quiet hours defer everything
        but critical, a device that answers gone/keeps failing is revoked, a push service that
        cannot be reached puts the item back with backoff. The inbox row is the record; a push is
        a hint that it exists."""
        out = {"pushed": 0, "push_deferred": 0, "push_skipped": 0, "push_failed": 0}
        due = self.notifier.due_push(BATCH)
        if not due:
            return out
        subs, sender = PushSubscriptions(self.store), WebPush(self.store)
        by_user: dict[str, list[dict]] = {}
        for d in due:
            by_user.setdefault(d["user_id"], []).append(d)
        for uid, rows in by_user.items():
            user = self.ident.get_user(uid)
            if not user or user.get("deleted_at") or user["status"] not in ("active", "pending"):
                for d in rows:
                    self.notifier.finish_push(d["notification_id"], "skipped", error="reader inactive")
                out["push_skipped"] += len(rows)
                continue
            prefs = self.notifier.preferences(uid)
            locale = user.get("locale") or "zh-Hans"
            quiet_until = self.notifier.quiet_until(prefs)
            devices = subs.active_for(uid)
            for d in rows:
                if quiet_until and d["severity"] != "critical":
                    self.notifier.defer_push(d["notification_id"], quiet_until)
                    out["push_deferred"] += 1
                    continue
                if not devices:
                    self.notifier.finish_push(d["notification_id"], "skipped", error="no device")
                    out["push_skipped"] += 1
                    continue
                if not self.notifier.claim_push(d["notification_id"]):
                    continue
                item = self._render(d, locale)
                data = payload_for({**item, "id": d["notification_id"], "kind": d["kind"], "severity": d["severity"], "created_at": d["notified_at"]})
                urgency = "high" if d["severity"] in ("critical", "action") else "normal"
                sent = retry_error = None
                for sub in devices:
                    outcome, status, error = sender.send(sub, data, urgency=urgency)
                    subs.record(sub["id"], outcome, error)
                    if outcome == "sent":
                        sent = True
                    elif outcome == "retry":
                        retry_error = error
                if sent:
                    self.notifier.finish_push(d["notification_id"], "sent")
                    out["pushed"] += 1
                elif retry_error:
                    self.notifier.retry_push(d["notification_id"], retry_error)
                    out["push_failed"] += 1
                else:
                    self.notifier.finish_push(d["notification_id"], "failed", error="no device accepted the push")
                    out["push_failed"] += 1
            devices = subs.active_for(uid)     # a device revoked above must not be pushed again for this reader
        return out

    # ------------------------------------------------------------------ rendering
    def _render(self, d: dict, locale: str) -> dict:
        row = self.store.one("SELECT * FROM notifications WHERE id=?", (d["notification_id"],))
        return self.notifier.render(row, locale) if row else {"title": "", "body": "", "href": None}

    def _mail_one(self, user: dict, d: dict, locale: str) -> None:
        item = self._render(d, locale)
        subject = SUBJECT.get(locale, SUBJECT["zh-Hans"]).format(title=item["title"])
        lines = [item["title"], ""]
        if item.get("body"):
            lines += [item["body"], ""]
        if item.get("href"):
            lines += [f"{OPEN.get(locale, OPEN['zh-Hans'])}: {item['href']}", ""]
        lines.append(FOOTER.get(locale, FOOTER["zh-Hans"]))
        self._enqueue(user, [d], subject, "\n".join(lines))

    def _mail_digest(self, user: dict, rows: list[dict], locale: str) -> None:
        subject = DIGEST_SUBJECT.get(locale, DIGEST_SUBJECT["zh-Hans"]).format(n=len(rows))
        lines = []
        for d in rows:
            item = self._render(d, locale)
            lines.append(f"• {item['title']}")
            if item.get("body"):
                lines.append(f"  {item['body']}")
            if item.get("href"):
                lines.append(f"  {OPEN.get(locale, OPEN['zh-Hans'])}: {item['href']}")
            lines.append("")
        lines.append(FOOTER.get(locale, FOOTER["zh-Hans"]))
        self._enqueue(user, rows, subject, "\n".join(lines))

    def _enqueue(self, user: dict, rows: list[dict], subject: str, body: str) -> None:
        try:
            mail = self.mailer.enqueue(user["email"], subject, body, kind="notification", ref_id=rows[0]["notification_id"])
        except MailNotConfigured:
            for d in rows:
                self.notifier.finish_delivery(d["notification_id"], "skipped", error="mail not configured")
            return
        except Exception as e:  # noqa: BLE001 - public URL missing, address refused, …
            for d in rows:
                self.store.execute("UPDATE notification_deliveries SET status='pending', updated_at=? WHERE notification_id=? AND channel='email'",
                                   (now(), d["notification_id"]))
                self.notifier.retry_delivery(d["notification_id"], f"{type(e).__name__}: {str(e)[:120]}")
            return
        for d in rows:
            self.notifier.finish_delivery(d["notification_id"], "queued", mail_id=(mail or {}).get("id"))


def inbox_security(request, user: dict | None, what: str) -> None:
    """Record an account-security change in the holder's inbox. Best effort: the change stands
    whether or not the row lands, and the direct security mail is queued by the caller."""
    notifier = getattr(getattr(request, "app", None), "state", None)
    notifier = getattr(notifier, "notifier", None)
    if notifier is None or not user:
        return
    try:
        notifier.security_change(user["id"], what)
    except Exception as e:  # noqa: BLE001
        print(f"[synwevo] security notification not recorded ({what}): {type(e).__name__}", flush=True)


__all__ = ["NotificationDispatcher", "inbox_security", "public_base"]
