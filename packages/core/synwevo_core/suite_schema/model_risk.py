"""Paid-dispatch risk state (docs/platform/model-risk.md); Store is the only schema owner.

State, not money: suspensions, platform breaker events, the staged-rollout allowlist, upstream
balance readings, recorded upstream top-ups, reconciliation results and the monitor's lease.
Every table is new and additive; older code never reads them, so a rollback leaves them idle.
History is append-only: a suspension or allowlist entry is ended by filling its end columns
once, never by deleting or rewriting it; breaker events, readings, top-ups and reconciliation
results are immutable.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS model_risk_suspensions (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    scope_kind TEXT NOT NULL CHECK(scope_kind IN ('user','organization','route')),
    scope_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK(source IN ('brake','journal')),
    reason_code TEXT NOT NULL,
    unit TEXT, window_s INTEGER, threshold_units INTEGER, observed_units INTEGER,
    policy_revision TEXT, actor_user_id TEXT,
    created_at INTEGER NOT NULL,
    lifted_at INTEGER, lifted_by TEXT REFERENCES users(id), lift_reason TEXT,
    CHECK((lifted_at IS NULL) = (lifted_by IS NULL) AND (lifted_at IS NULL) = (lift_reason IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS model_risk_suspensions_active
    ON model_risk_suspensions(environment,scope_kind,scope_id) WHERE lifted_at IS NULL;
CREATE INDEX IF NOT EXISTS model_risk_suspensions_recent ON model_risk_suspensions(environment,created_at);
CREATE TRIGGER IF NOT EXISTS model_risk_suspensions_lift_once BEFORE UPDATE ON model_risk_suspensions
WHEN OLD.lifted_at IS NOT NULL OR NEW.lifted_at IS NULL
  OR NEW.id IS NOT OLD.id OR NEW.environment IS NOT OLD.environment OR NEW.scope_kind IS NOT OLD.scope_kind
  OR NEW.scope_id IS NOT OLD.scope_id OR NEW.source IS NOT OLD.source OR NEW.reason_code IS NOT OLD.reason_code
  OR NEW.unit IS NOT OLD.unit OR NEW.window_s IS NOT OLD.window_s OR NEW.threshold_units IS NOT OLD.threshold_units
  OR NEW.observed_units IS NOT OLD.observed_units OR NEW.policy_revision IS NOT OLD.policy_revision
  OR NEW.actor_user_id IS NOT OLD.actor_user_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'immutable_risk_suspension'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_suspensions_no_delete BEFORE DELETE ON model_risk_suspensions
BEGIN SELECT RAISE(ABORT,'immutable_risk_suspension'); END;

CREATE TABLE IF NOT EXISTS model_risk_breaker_events (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    seq INTEGER NOT NULL CHECK(typeof(seq)='integer' AND seq>0),
    state TEXT NOT NULL CHECK(state IN ('open','closed')),
    source TEXT NOT NULL CHECK(source IN ('balance','upstream_402','admin','journal')),
    reason_code TEXT NOT NULL, reason TEXT, actor_id TEXT REFERENCES users(id),
    created_at INTEGER NOT NULL,
    UNIQUE(environment,seq)
);
CREATE TRIGGER IF NOT EXISTS model_risk_breaker_no_update BEFORE UPDATE ON model_risk_breaker_events
BEGIN SELECT RAISE(ABORT,'immutable_risk_breaker'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_breaker_no_delete BEFORE DELETE ON model_risk_breaker_events
BEGIN SELECT RAISE(ABORT,'immutable_risk_breaker'); END;

CREATE TABLE IF NOT EXISTS model_risk_allowlist (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    payer_kind TEXT NOT NULL CHECK(payer_kind IN ('user','organization')),
    payer_id TEXT NOT NULL,
    added_by TEXT NOT NULL REFERENCES users(id), add_reason TEXT NOT NULL, added_at INTEGER NOT NULL,
    removed_at INTEGER, removed_by TEXT REFERENCES users(id), remove_reason TEXT,
    CHECK((removed_at IS NULL) = (removed_by IS NULL) AND (removed_at IS NULL) = (remove_reason IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS model_risk_allowlist_active
    ON model_risk_allowlist(environment,payer_kind,payer_id) WHERE removed_at IS NULL;
CREATE TRIGGER IF NOT EXISTS model_risk_allowlist_remove_once BEFORE UPDATE ON model_risk_allowlist
WHEN OLD.removed_at IS NOT NULL OR NEW.removed_at IS NULL
  OR NEW.id IS NOT OLD.id OR NEW.environment IS NOT OLD.environment OR NEW.payer_kind IS NOT OLD.payer_kind
  OR NEW.payer_id IS NOT OLD.payer_id OR NEW.added_by IS NOT OLD.added_by OR NEW.add_reason IS NOT OLD.add_reason
  OR NEW.added_at IS NOT OLD.added_at
BEGIN SELECT RAISE(ABORT,'immutable_risk_allowlist'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_allowlist_no_delete BEFORE DELETE ON model_risk_allowlist
BEGIN SELECT RAISE(ABORT,'immutable_risk_allowlist'); END;

CREATE TABLE IF NOT EXISTS model_risk_balance_readings (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    observed_at INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok','not_configured','error')),
    code TEXT NOT NULL,
    is_available INTEGER CHECK(is_available IS NULL OR is_available IN (0,1)),
    unit TEXT, scale INTEGER,
    total_units INTEGER CHECK(total_units IS NULL OR typeof(total_units)='integer')
);
CREATE INDEX IF NOT EXISTS model_risk_balance_readings_time ON model_risk_balance_readings(environment,observed_at);
CREATE TRIGGER IF NOT EXISTS model_risk_balance_no_update BEFORE UPDATE ON model_risk_balance_readings
BEGIN SELECT RAISE(ABORT,'immutable_risk_reading'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_balance_no_delete BEFORE DELETE ON model_risk_balance_readings
BEGIN SELECT RAISE(ABORT,'immutable_risk_reading'); END;

CREATE TABLE IF NOT EXISTS model_risk_upstream_topups (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    unit TEXT NOT NULL,
    amount_units INTEGER NOT NULL CHECK(typeof(amount_units)='integer' AND amount_units>0),
    topped_up_at INTEGER NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_risk_upstream_topups_time ON model_risk_upstream_topups(environment,topped_up_at);
CREATE TRIGGER IF NOT EXISTS model_risk_topups_no_update BEFORE UPDATE ON model_risk_upstream_topups
BEGIN SELECT RAISE(ABORT,'immutable_risk_topup'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_topups_no_delete BEFORE DELETE ON model_risk_upstream_topups
BEGIN SELECT RAISE(ABORT,'immutable_risk_topup'); END;

CREATE TABLE IF NOT EXISTS model_risk_reconciliations (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    kind TEXT NOT NULL CHECK(kind IN ('daily','ledger_audit')),
    window_start INTEGER NOT NULL, window_end INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok','drift','incomplete','error')),
    code TEXT NOT NULL,
    unit TEXT, scale INTEGER,
    upstream_units INTEGER, topup_units INTEGER,
    ledger_cost_n TEXT, ledger_cost_d TEXT, pending_units INTEGER,
    drift_n TEXT, drift_d TEXT, tolerance_units INTEGER,
    differences INTEGER,
    reading_start_at INTEGER, reading_end_at INTEGER,
    trigger TEXT NOT NULL CHECK(trigger IN ('schedule','admin')), actor_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_risk_reconciliations_window
    ON model_risk_reconciliations(environment,kind,window_start,created_at);
CREATE TRIGGER IF NOT EXISTS model_risk_reconciliations_no_update BEFORE UPDATE ON model_risk_reconciliations
BEGIN SELECT RAISE(ABORT,'immutable_risk_reconciliation'); END;
CREATE TRIGGER IF NOT EXISTS model_risk_reconciliations_no_delete BEFORE DELETE ON model_risk_reconciliations
BEGIN SELECT RAISE(ABORT,'immutable_risk_reconciliation'); END;

-- One monitor across processes: whoever holds the unexpired lease runs a round.
CREATE TABLE IF NOT EXISTS model_risk_jobs (
    name TEXT PRIMARY KEY, owner TEXT NOT NULL, lease_until REAL NOT NULL
);
"""
