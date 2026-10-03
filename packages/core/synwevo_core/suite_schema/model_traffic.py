"""Fair, bounded waiting for platform-account model calls (docs/platform/model-traffic.md).

One row per model call that asked for capacity. Rows are state, not money facts: they move
waiting -> granted -> admitted -> finished/unknown, or end expired/abandoned.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS model_traffic_claims (
    id TEXT PRIMARY KEY,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    account_ref TEXT NOT NULL, payer_user_id TEXT NOT NULL,
    est_tokens INTEGER NOT NULL CHECK(typeof(est_tokens)='integer' AND est_tokens>0),
    state TEXT NOT NULL CHECK(state IN ('waiting','granted','admitted','finished','unknown','expired','abandoned')),
    enqueued_at INTEGER NOT NULL, wait_until INTEGER NOT NULL,
    granted_at INTEGER, grant_expires_at INTEGER,
    admitted_at INTEGER, inflight_until INTEGER, attempt_key TEXT UNIQUE,
    finished_at INTEGER,
    actual_tokens INTEGER CHECK(actual_tokens IS NULL OR (typeof(actual_tokens)='integer' AND actual_tokens>=0))
);
CREATE INDEX IF NOT EXISTS model_traffic_claims_state ON model_traffic_claims(environment,account_ref,state,enqueued_at);
CREATE INDEX IF NOT EXISTS model_traffic_claims_admitted ON model_traffic_claims(environment,account_ref,admitted_at);
CREATE INDEX IF NOT EXISTS model_traffic_claims_payer ON model_traffic_claims(environment,account_ref,payer_user_id,admitted_at);
CREATE INDEX IF NOT EXISTS model_traffic_claims_granted ON model_traffic_claims(environment,account_ref,granted_at);
"""
