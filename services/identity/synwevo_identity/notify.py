"""The e-mail side of the platform inbox: turns pending notification deliveries into outbox mail.

Only the account service has a mailer, so only it runs this. Every role writes inbox rows and
`pending` delivery rows (synwevo_core.notifications); this worker, alone, renders them in the
reader's language and queues them through the same `Mailer` (and so the same `mail_outbox`,
retries and admin view) that invitations use. Current eligibility, the reader's digest budget,
claiming a delivery, inserting its outbox mail and linking the two share one write transaction.
Two account processes therefore share the same budget, and a failed enqueue or process exit
cannot leave a committed `queued` delivery without its mail. SMTP runs later in the mail worker.

Reader preferences are applied here, at send time, not at emit time: quiet hours defer the mail
to the end of the window, `digest` groups whatever is due for one reader into one message.
Security notifications ignore both — an attacker who changed a password must not have set the
quiet hours that hide the fact.

Before anything non-critical leaves, the row is checked again (`Notifier.stale_reason`): read in
the app since, channel turned off since, a conversation that was read, muted, left or whose
unread messages were all recalled — those are skipped with the reason on the row, not mailed.
Conversation digests additionally obey the current per-reader interval and daily budget, and
render from the live unread state, without message text unless the reader asked for it.
"""
from __future__ import annotations

import threading

from synwevo_core.identity import Identity
from synwevo_core.notifications import Notifier
from synwevo_core.push import PushSubscriptions, WebPush, payload_for
from synwevo_core.store import Store, now
from synwevo_core import workers

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
            workers.register(self, self._thread)

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
        # This is only a bounded candidate list. A second dispatcher, an account change or
        # a reader's preference/read state may win before we acquire the writer lock.
        due = self.notifier.due_deliveries(BATCH)
        by_user: dict[str, list[str]] = {}
        for d in due:
            by_user.setdefault(d["user_id"], []).append(d["notification_id"])
        out = {"queued": 0, "deferred": 0, "skipped": 0, "digested": 0, "failed": 0}
        for uid, ids in by_user.items():
            result = self._email_user(uid, ids)
            for key, value in result.items():
                out[key] += value
        out.update(self._push_pass())
        return out

    def _email_user(self, uid: str, ids: list[str]) -> dict:
        out = {"queued": 0, "deferred": 0, "skipped": 0, "digested": 0, "failed": 0}
        with self.store.tx():
            prefs = self.notifier.preferences(uid)
            policy = prefs["conversation_digest_policy"]
            rows = self.notifier.pending_email_deliveries(uid, ids, policy_revision=policy["revision"])
            if not rows:
                return out
            user = self.ident.get_user(uid)
            if not user or user.get("deleted_at") or user["status"] not in ("active", "pending"):
                for d in rows:
                    self.notifier.finish_delivery(d["notification_id"], "skipped", error="reader inactive")
                out["skipped"] += len(rows)
                return out
            locale = user.get("locale") or "zh-Hans"
            quiet_until = self.notifier.quiet_until(prefs)
            send_now = []
            hold = None
            for d in rows:
                reason = self._stale(d, prefs)
                if reason:
                    self.notifier.finish_delivery(d["notification_id"], "skipped", error=reason)
                    out["skipped"] += 1
                    continue
                if quiet_until and d["severity"] != "critical":
                    self.notifier.defer_delivery(d["notification_id"], quiet_until, reason="quiet")
                    out["deferred"] += 1
                elif d["category"] == "conversation" and d["severity"] != "critical":
                    if hold is None:
                        hold = self.notifier.conversation_digest_hold(uid, policy=policy) or 0.0
                    if hold:
                        self.notifier.defer_delivery(d["notification_id"], hold, reason="budget", policy_revision=policy["revision"])
                        out["deferred"] += 1
                    else:
                        send_now.append(d)
                else:
                    send_now.append(d)
            critical = [[d] for d in send_now if d["severity"] == "critical"]
            others = [d for d in send_now if d["severity"] != "critical"]
            for batch in critical + ([others] if others else []):
                state = self._queue_email(user, batch, locale, prefs)
                out[state] += len(batch)
                if state == "queued" and len(batch) > 1:
                    out["digested"] += 1
        return out

    def _queue_email(self, user: dict, rows: list[dict], locale: str, prefs: dict) -> str:
        # Store.tx() is re-entrant, not a nested rollback boundary. A savepoint keeps one
        # malformed/render-failing message from undoing other independent security notices,
        # while an outbox insert followed by a failed delivery link is rolled back together.
        # The enclosing _email_user transaction retains the lock across eligibility and budget
        # checks, enqueue and these links. Mailer.enqueue does no network I/O.
        with self.store.tx() as tx:
            tx.execute("SAVEPOINT notification_email")
            try:
                for d in rows:
                    revision = (prefs["conversation_digest_policy"]["revision"]
                                if d["category"] == "conversation" and d["severity"] != "critical" else None)
                    tx.execute("UPDATE notification_deliveries SET status='queued', updated_at=?, "
                               "defer_reason=NULL, policy_revision=? "
                               "WHERE notification_id=? AND channel='email' AND status='pending'",
                               (now(), revision, d["notification_id"]))
                if len(rows) == 1:
                    self._mail_one(user, rows[0], locale, prefs)
                else:
                    self._mail_digest(user, rows, locale, prefs)
            except Exception as exc:
                tx.execute("ROLLBACK TO notification_email")
                tx.execute("RELEASE notification_email")
                if isinstance(exc, MailNotConfigured):
                    for d in rows:
                        self.notifier.finish_delivery(d["notification_id"], "skipped", error="mail not configured")
                    return "skipped"
                # Never persist arbitrary exception text: a renderer or transport validation
                # error may contain the recipient, notification body or a live link.
                for d in rows:
                    self.notifier.retry_delivery(d["notification_id"], "notification_enqueue_failed")
                return "failed"
            tx.execute("RELEASE notification_email")
        return "queued"

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
                reason = self._stale(d, prefs)
                if reason:
                    self.notifier.finish_push(d["notification_id"], "skipped", error=reason)
                    out["push_skipped"] += 1
                    continue
                if not self.notifier.claim_push(d["notification_id"]):
                    continue
                item = self._render(d, locale, prefs)
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
    def _stale(self, d: dict, prefs: dict) -> str | None:
        """The send-time re-check, plus the channel switch as it is *now*: an e-mail mode turned
        off after the row was queued means no mail (security always mails)."""
        reason = self.notifier.stale_reason(d, prefs)
        if reason or d["severity"] == "critical":
            return reason
        # Push-queue rows carry no `channel`; the e-mail switch is only for e-mail deliveries.
        if d.get("channel") == "email" and prefs["categories"].get(d["category"], {}).get("email", "immediate") == "off":
            return "channel off"
        return None

    def _render(self, d: dict, locale: str, prefs: dict | None = None) -> dict:
        row = self.store.one("SELECT * FROM notifications WHERE id=?", (d["notification_id"],))
        if not row:
            return {"title": "", "body": "", "href": None}
        return self.notifier.render_external(row, locale, prefs) if prefs is not None else self.notifier.render(row, locale)

    def _mail_one(self, user: dict, d: dict, locale: str, prefs: dict | None = None) -> None:
        from .mail_layout import Mail, render
        item = self._render(d, locale, prefs)
        subject = SUBJECT.get(locale, SUBJECT["zh-Hans"]).format(title=item["title"])
        m = Mail(locale, item["title"], [item["body"]] if item.get("body") else [],
                 action=(OPEN.get(locale, OPEN["zh-Hans"]), item["href"]) if item.get("href") else None)
        text, html = render(m)
        self._enqueue(user, [d], subject, text, html)

    def _mail_digest(self, user: dict, rows: list[dict], locale: str, prefs: dict | None = None) -> None:
        from .mail_layout import Mail, render
        subject = DIGEST_SUBJECT.get(locale, DIGEST_SUBJECT["zh-Hans"]).format(n=len(rows))
        items = []
        for d in rows:
            item = self._render(d, locale, prefs)
            items.append({"title": item["title"], "body": item.get("body") or "", "href": item.get("href") or ""})
        text, html = render(Mail(locale, subject, items=items))
        self._enqueue(user, rows, subject, text, html)

    def _enqueue(self, user: dict, rows: list[dict], subject: str, body: str, html: str | None = None) -> None:
        mail = self.mailer.enqueue(user["email"], subject, body, kind="notification",
                                   ref_id=rows[0]["notification_id"], html=html)
        for d in rows:
            self.notifier.finish_delivery(d["notification_id"], "queued", mail_id=mail["id"])


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
