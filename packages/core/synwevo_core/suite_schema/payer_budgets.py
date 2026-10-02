"""Personal spending limits; additive, no implicit limits, grants or balances."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_payer_budget_versions (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    user_id TEXT NOT NULL REFERENCES users(id), unit TEXT NOT NULL REFERENCES billing_units(code),
    revision INTEGER NOT NULL CHECK(revision>0), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    daily_budget INTEGER NOT NULL CHECK(typeof(daily_budget)='integer' AND daily_budget>=0),
    weekly_budget INTEGER NOT NULL CHECK(typeof(weekly_budget)='integer' AND weekly_budget>=0),
    monthly_budget INTEGER NOT NULL CHECK(typeof(monthly_budget)='integer' AND monthly_budget>=0),
    created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,user_id,unit,revision)
);
CREATE TABLE IF NOT EXISTS billing_payer_budget_admissions (
    call_key TEXT PRIMARY KEY REFERENCES billing_calls(id),
    environment TEXT NOT NULL, user_id TEXT NOT NULL, unit TEXT NOT NULL, revision INTEGER NOT NULL,
    FOREIGN KEY(environment,user_id,unit,revision)
        REFERENCES billing_payer_budget_versions(environment,user_id,unit,revision)
);
CREATE INDEX IF NOT EXISTS billing_calls_payer_period ON billing_calls(account_id,created_at)
    WHERE status='settled';
"""

for _table, _keys in (("versions", ("environment", "user_id", "unit", "revision")),
                      ("admissions", ("call_key",))):
    _name = "billing_payer_budget_" + _table
    for _op in ("UPDATE", "DELETE"):
        SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_name}_no_{_op.lower()} BEFORE {_op} ON {_name}
BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
"""
    _match = " AND ".join(f"{key}=NEW.{key}" for key in _keys)
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_name}_no_replace BEFORE INSERT ON {_name}
WHEN EXISTS(SELECT 1 FROM {_name} WHERE {_match})
BEGIN SELECT RAISE(ABORT,'immutable_billing_fact'); END;
"""
