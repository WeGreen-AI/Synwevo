"""Additive WP7 tables. Applied only by Store's existing suite schema loader."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS flow_definitions (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
 latest_version INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS flow_definitions_owner ON flow_definitions(user_id,id);
CREATE TABLE IF NOT EXISTS flow_versions (
 flow_id TEXT NOT NULL, version INTEGER NOT NULL, spec_json TEXT NOT NULL,
 created_at REAL NOT NULL, PRIMARY KEY(flow_id,version)
);
CREATE TABLE IF NOT EXISTS flow_runs (
 id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, version INTEGER NOT NULL, user_id TEXT NOT NULL,
 params_json TEXT NOT NULL, input_digest TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL, step_id TEXT, error_code TEXT, correlation_id TEXT NOT NULL,
 parent_run_id TEXT, depth INTEGER NOT NULL DEFAULT 0, trigger_id TEXT, event_json TEXT,
 calls_used INTEGER NOT NULL DEFAULT 0, deadline REAL NOT NULL,
 lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0, fence INTEGER NOT NULL DEFAULT 0,
 due_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL,
 UNIQUE(user_id,flow_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS flow_runs_due ON flow_runs(status,due_at,lease_until);
CREATE INDEX IF NOT EXISTS flow_runs_owner ON flow_runs(user_id,id);
CREATE INDEX IF NOT EXISTS flow_runs_chain ON flow_runs(correlation_id,user_id);
CREATE TABLE IF NOT EXISTS flow_steps (
 run_id TEXT NOT NULL, step_id TEXT NOT NULL, status TEXT NOT NULL,
 attempt INTEGER NOT NULL DEFAULT 0, checks INTEGER NOT NULL DEFAULT 0,
 op_run_id TEXT, key TEXT, receipt_json TEXT, value_json TEXT, reader_json TEXT,
 error_code TEXT, started_at REAL NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(run_id,step_id)
);
CREATE TABLE IF NOT EXISTS flow_history (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, step_id TEXT,
 type TEXT NOT NULL, status TEXT NOT NULL, attempt INTEGER, op_run_id TEXT,
 error_code TEXT, fence INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS flow_history_run ON flow_history(run_id,seq);
CREATE TABLE IF NOT EXISTS flow_triggers (
 id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, version INTEGER NOT NULL, user_id TEXT NOT NULL,
 spec_json TEXT NOT NULL, policy_json TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 runs_used INTEGER NOT NULL DEFAULT 0, next_at REAL, cursor INTEGER NOT NULL,
 error_code TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS flow_triggers_due ON flow_triggers(enabled,next_at);
CREATE INDEX IF NOT EXISTS flow_triggers_owner ON flow_triggers(user_id,id);
CREATE TABLE IF NOT EXISTS flow_trigger_deliveries (
 trigger_id TEXT NOT NULL, occurrence TEXT NOT NULL, run_id TEXT,
 status TEXT NOT NULL, error_code TEXT, created_at REAL NOT NULL,
 PRIMARY KEY(trigger_id,occurrence)
);
"""

#: For the platform's run overview, which reads flow runs across owners. Counting the runs that
#: finished inside a time window reads (status, updated_at): `flow_runs_due` starts with the
#: state but orders by due time. One person's runs, newest first, are read through
#: (user_id, created_at): `flow_runs_owner` orders by id, which is random. The runs of one chain
#: are listed by correlation id newest first: `flow_runs_chain` (correlation_id, user_id) finds
#: them, but only this single-column index (which ends in the rowid) gives their order without a
#: sort, as `op_runs_correlation` does for an operation's.
INDEXES = (("flow_runs_status_updated", "flow_runs", ("status", "updated_at")),
           ("flow_runs_user_created", "flow_runs", ("user_id", "created_at")),
           ("flow_runs_correlation", "flow_runs", ("correlation_id",)))
