"""Signed webhooks of the business applications' machine API (docs/integration/suite-api.md).

A subscription belongs to one grant — a client acting for one person in one workspace — and is
that grant's alone to see and manage. Deliveries are the log of what was sent to it; they hold
where an event went and how the attempt ended, never the event's data or a receiver's response.

Separate from Synwevo Docs' ``webhook_subscriptions`` / ``webhook_deliveries``: those rows carry
no application, and each application's dispatcher reads its own event log.

`stopping` is here, beside the tables, because two things stop a subscription and must agree on
where it stopped: its own dispatcher, and the security journal replayed over a restored database
(`synwevo_core.journal`), which cannot import an application.
"""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS suite_webhook_subscriptions (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    client_id TEXT NOT NULL, user_id TEXT NOT NULL,
    -- The delegated descriptor (synwevo_core.authority) of the grant that answers for this: the
    -- one that created it, resumed it or last changed where it goes, what it asks for or its
    -- secret. Deliveries are made only while that grant can still be renewed and only for what it
    -- may read at that moment.
    created_by TEXT NOT NULL, authorized_at REAL NOT NULL,
    url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    events TEXT NOT NULL,                   -- JSON array: event types, or "<app>.*"
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','disabled')),
    disabled_reason TEXT, status_changed_at REAL NOT NULL,
    -- Read back to sign each delivery, so sealed (synwevo_core.sealed), not hashed. During a
    -- rotation the previous secret keeps signing alongside the new one until it expires.
    secret_enc TEXT NOT NULL, secret_prev_enc TEXT, secret_prev_expires_at REAL,
    version INTEGER NOT NULL DEFAULT 1,     -- optimistic concurrency for changes
    cursor INTEGER NOT NULL,                -- its place in app_events; never shown
    cursor_at REAL NOT NULL,                -- no event after `cursor` is older than this
    scanned_at REAL NOT NULL DEFAULT 0,     -- when the dispatcher last looked: whose turn is next
    -- Where pushing last stopped (a pause, a disable): the place and time a feed cursor needs, so
    -- what was not pushed can be pulled. And since when an event could not be decided at all.
    gap_cursor INTEGER, gap_at REAL, stalled_since REAL,
    failing_since REAL, consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at REAL, last_failure_at REAL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, deleted_at REAL
);
CREATE INDEX IF NOT EXISTS suite_whsub_workspace ON suite_webhook_subscriptions(workspace_id, deleted_at);
-- A grant has one subscription per address, so creating one twice (a lost answer, a retry) is
-- refused by name instead of leaving a second one whose first secret nobody received.
CREATE UNIQUE INDEX IF NOT EXISTS suite_whsub_address ON suite_webhook_subscriptions(workspace_id, client_id, user_id, url)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS suite_whsub_turn ON suite_webhook_subscriptions(status, scanned_at);

CREATE TABLE IF NOT EXISTS suite_webhook_deliveries (
    id TEXT PRIMARY KEY,
    subscription_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
    event_id TEXT NOT NULL, event_seq INTEGER, event_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','delivered','failed','dropped')),
    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL,
    last_code INTEGER, last_error TEXT, duration_ms INTEGER,
    redelivery_of TEXT,                     -- the delivery this one repeats
    claimed_by TEXT, claimed_until REAL,
    created_at REAL NOT NULL, delivered_at REAL
);
-- An event reaches a subscription once; a redelivery is a row of its own.
CREATE UNIQUE INDEX IF NOT EXISTS suite_whdel_once ON suite_webhook_deliveries(subscription_id, event_id)
    WHERE redelivery_of IS NULL;
CREATE INDEX IF NOT EXISTS suite_whdel_due ON suite_webhook_deliveries(status, next_attempt_at);
-- A subscription's deliveries go out in the order they were queued: its oldest pending row first.
CREATE INDEX IF NOT EXISTS suite_whdel_head ON suite_webhook_deliveries(subscription_id, status);
CREATE INDEX IF NOT EXISTS suite_whdel_log ON suite_webhook_deliveries(subscription_id, created_at);
"""


def stopping(c, sub, unsent: int | None = None) -> tuple[int | None, float | None]:
    """Where an active subscription stands as it stops (``gap_cursor``, ``gap_at``): before its
    place in the log, before the first event still waiting to be sent, and before `unsent` — the
    event whose refusal stopped it. Kept, so that what is not pushed can be pulled. One that had
    already stopped keeps the place it stopped at."""
    if sub["status"] != "active":
        return sub["gap_cursor"], sub["gap_at"]
    position, at = sub["cursor"], sub["cursor_at"]
    first = c.execute("SELECT MIN(event_seq) AS s FROM suite_webhook_deliveries WHERE subscription_id=? AND status='pending' "
                      "AND event_seq IS NOT NULL AND redelivery_of IS NULL", (sub["id"],)).fetchone()["s"]
    if unsent is not None and (first is None or unsent < first):
        first = unsent
    if first is not None and first - 1 < position:
        event = c.execute("SELECT created_at FROM app_events WHERE seq=?", (first,)).fetchone()
        position, at = first - 1, min(at, event["created_at"]) if event else at
    return position, at
