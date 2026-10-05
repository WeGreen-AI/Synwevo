"""Wallet usage reads and per-person low-balance reminders; additive, nothing seeded.

The index serves the account origin's usage page: one payer's calls in a time range, across
every status (the payer-budget index covers settled calls only). The reminder table holds a
person's own threshold; it is a preference, never a money fact, so it is mutable with an
optimistic revision. Rollback: older code ignores both.
"""

SCHEMA = """
CREATE INDEX IF NOT EXISTS billing_calls_account_created ON billing_calls(account_id,created_at);
CREATE TABLE IF NOT EXISTS billing_wallet_reminders (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    user_id TEXT NOT NULL REFERENCES users(id),
    unit TEXT NOT NULL REFERENCES billing_units(code),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    threshold INTEGER NOT NULL CHECK(typeof(threshold)='integer' AND threshold>=0),
    updated_at INTEGER NOT NULL,
    notified_at INTEGER,
    PRIMARY KEY(environment,user_id,unit)
);
"""
