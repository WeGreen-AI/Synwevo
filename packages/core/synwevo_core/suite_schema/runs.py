"""The capability layer's run records and event outbox (capabilities.py in this package).

Declared here rather than in that module so the store — which every role opens, the account
service included — applies them without importing the capability layer itself.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS op_runs (
    id TEXT PRIMARY KEY,
    op TEXT NOT NULL,
    app TEXT NOT NULL,
    op_version INTEGER NOT NULL,
    workspace_id TEXT,
    user_id TEXT NOT NULL,
    principal TEXT NOT NULL,
    via TEXT NOT NULL,
    delegation TEXT,
    correlation_id TEXT NOT NULL,
    parent_run_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    input_hash TEXT NOT NULL,
    input TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued','running','waiting_input','waiting_approval',
                                           'succeeded','partial','failed','cancelled','unknown')),
    output TEXT,
    error_code TEXT,
    error_message TEXT,
    error_detail TEXT,
    object_kind TEXT,
    object_id TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS op_runs_user ON op_runs(user_id, created_at);
CREATE INDEX IF NOT EXISTS op_runs_workspace ON op_runs(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS op_runs_correlation ON op_runs(correlation_id);
-- the platform's run search walks (correlation_id, user_id) pairs in that order, batch by batch:
-- without this index every batch sorted the whole remaining range (quadratic, review R4)
CREATE INDEX IF NOT EXISTS op_runs_correlation_user ON op_runs(correlation_id, user_id);
CREATE INDEX IF NOT EXISTS op_runs_parent ON op_runs(parent_run_id);
CREATE INDEX IF NOT EXISTS op_runs_status ON op_runs(status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS op_runs_idempotency
    ON op_runs(user_id, op, COALESCE(workspace_id, ''), idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS app_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    app TEXT NOT NULL,
    workspace_id TEXT,
    user_id TEXT,
    object_kind TEXT,
    object_id TEXT,
    run_id TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    depth INTEGER NOT NULL DEFAULT 0,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS app_events_type ON app_events(type, seq);
CREATE INDEX IF NOT EXISTS app_events_object ON app_events(object_kind, object_id, seq);
CREATE INDEX IF NOT EXISTS app_events_workspace ON app_events(workspace_id, seq);

CREATE TABLE IF NOT EXISTS app_event_cursors (
    consumer TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
"""
