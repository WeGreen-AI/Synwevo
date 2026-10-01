"""Immutable platform cost reservations, installed only by Store.

The payment ledger remains the financial fact source. These rows bind a physical attempt
to the provider account whose budgets admitted it; rotating a key or gateway namespace
cannot reset that account's exposure. Actual cost is read from billing_usage.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS model_cost_admissions (
    attempt_key TEXT PRIMARY KEY REFERENCES billing_attempts(id),
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    provider TEXT NOT NULL, account_ref TEXT NOT NULL,
    unit TEXT NOT NULL REFERENCES billing_units(code),
    reserved_units INTEGER NOT NULL CHECK(typeof(reserved_units)='integer' AND reserved_units>=0),
    policy_revision TEXT NOT NULL, limits_json TEXT NOT NULL,
    admitted_at INTEGER NOT NULL, expires_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_cost_admissions_scope ON model_cost_admissions(environment,provider,account_ref,admitted_at);
CREATE TRIGGER IF NOT EXISTS model_cost_admissions_no_update BEFORE UPDATE ON model_cost_admissions
BEGIN SELECT RAISE(ABORT,'immutable_cost_admission'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_admissions_no_delete BEFORE DELETE ON model_cost_admissions
BEGIN SELECT RAISE(ABORT,'immutable_cost_admission'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_admissions_no_replace BEFORE INSERT ON model_cost_admissions
WHEN EXISTS(SELECT 1 FROM model_cost_admissions WHERE attempt_key=NEW.attempt_key)
BEGIN SELECT RAISE(ABORT,'immutable_cost_admission'); END;

-- Searchable projection of missing trusted usage. User hold expiry never deletes it.
CREATE TABLE IF NOT EXISTS model_cost_pending (
    attempt_key TEXT PRIMARY KEY REFERENCES model_cost_admissions(attempt_key),
    environment TEXT NOT NULL, provider TEXT NOT NULL, account_ref TEXT NOT NULL,
    admitted_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_cost_pending_scope ON model_cost_pending(environment,provider,account_ref,admitted_at);
CREATE TRIGGER IF NOT EXISTS model_cost_pending_valid_insert BEFORE INSERT ON model_cost_pending
WHEN EXISTS(SELECT 1 FROM model_cost_pending WHERE attempt_key=NEW.attempt_key)
 OR EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=NEW.attempt_key)
 OR NOT EXISTS(SELECT 1 FROM model_cost_admissions a WHERE a.attempt_key=NEW.attempt_key
    AND a.environment=NEW.environment AND a.provider=NEW.provider
    AND a.account_ref=NEW.account_ref AND a.admitted_at=NEW.admitted_at)
BEGIN SELECT RAISE(ABORT,'invalid_cost_pending'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_pending_no_update BEFORE UPDATE ON model_cost_pending
BEGIN SELECT RAISE(ABORT,'immutable_cost_pending'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_pending_known_delete BEFORE DELETE ON model_cost_pending
WHEN NOT EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=OLD.attempt_key)
BEGIN SELECT RAISE(ABORT,'unresolved_cost_pending'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_admissions_pending AFTER INSERT ON model_cost_admissions
WHEN NOT EXISTS(SELECT 1 FROM billing_usage WHERE attempt_key=NEW.attempt_key)
BEGIN
 INSERT INTO model_cost_pending VALUES (NEW.attempt_key,NEW.environment,NEW.provider,NEW.account_ref,NEW.admitted_at);
END;
CREATE TRIGGER IF NOT EXISTS model_cost_usage_resolved AFTER INSERT ON billing_usage
BEGIN DELETE FROM model_cost_pending WHERE attempt_key=NEW.attempt_key; END;
"""


def migrate(c):
    """One atomic projection backfill; routine startup does not rescan financial history."""
    key = "model_cost_pending_projection_v1"
    if c.execute("SELECT 1 FROM meta WHERE key=?", (key,)).fetchone():
        return
    c.execute("INSERT INTO model_cost_pending "
              "SELECT a.attempt_key,a.environment,a.provider,a.account_ref,a.admitted_at "
              "FROM model_cost_admissions a WHERE NOT EXISTS "
              "(SELECT 1 FROM billing_usage u WHERE u.attempt_key=a.attempt_key) "
              "AND NOT EXISTS (SELECT 1 FROM model_cost_pending p WHERE p.attempt_key=a.attempt_key)")
    c.execute("INSERT INTO meta(key,value) VALUES (?,?)", (key, "1"))
