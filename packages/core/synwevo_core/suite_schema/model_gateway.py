"""Persistent request fences and restricted metering observations; Store applies this DDL.

No prompts, completions, private reasoning or credentials are stored here. Route snapshots
are approved metadata supplied by governance. These tables do not enable model dispatch.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_metered_runs (
    run_key TEXT PRIMARY KEY REFERENCES billing_runs(id), route TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_metered_calls (
    id TEXT PRIMARY KEY, run_key TEXT NOT NULL REFERENCES agent_metered_runs(run_key),
    logical_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    call_key TEXT UNIQUE REFERENCES billing_calls(id),
    attempt_key TEXT UNIQUE REFERENCES billing_attempts(id),
    metering_state TEXT NOT NULL DEFAULT 'pending' CHECK(metering_state IN ('pending','settled','released')),
    created_at INTEGER NOT NULL, UNIQUE(run_key,logical_id)
);
CREATE TABLE IF NOT EXISTS agent_model_observations (
    call_id TEXT PRIMARY KEY REFERENCES agent_metered_calls(id),
    attempt_key TEXT NOT NULL UNIQUE REFERENCES billing_attempts(id),
    evidence TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_metered_pending ON agent_metered_calls(metering_state,created_at);
CREATE TRIGGER IF NOT EXISTS agent_metered_calls_preserve BEFORE UPDATE ON agent_metered_calls
WHEN NEW.id<>OLD.id OR NEW.run_key<>OLD.run_key OR NEW.logical_id<>OLD.logical_id
 OR NEW.request_digest<>OLD.request_digest OR NEW.created_at<>OLD.created_at
 OR (OLD.call_key IS NOT NULL AND NEW.call_key IS NOT OLD.call_key)
 OR (OLD.attempt_key IS NOT NULL AND NEW.attempt_key IS NOT OLD.attempt_key)
 OR (OLD.metering_state='settled' AND NEW.metering_state<>'settled')
BEGIN SELECT RAISE(ABORT,'immutable_metering_contract'); END;
CREATE TRIGGER IF NOT EXISTS agent_metered_calls_no_delete BEFORE DELETE ON agent_metered_calls
BEGIN SELECT RAISE(ABORT,'immutable_metering_contract'); END;
CREATE TRIGGER IF NOT EXISTS agent_metered_calls_no_replace BEFORE INSERT ON agent_metered_calls
WHEN EXISTS(SELECT 1 FROM agent_metered_calls WHERE id=NEW.id OR (run_key=NEW.run_key AND logical_id=NEW.logical_id))
BEGIN SELECT RAISE(ABORT,'immutable_metering_contract'); END;
"""
for _table, _key in (("agent_metered_runs", "run_key"), ("agent_model_observations", "call_id")):
    for _action in ("UPDATE", "DELETE"):
        SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_table}_no_{_action.lower()} BEFORE {_action} ON {_table}
BEGIN SELECT RAISE(ABORT,'immutable_metering_fact'); END;
"""
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_table}_no_replace BEFORE INSERT ON {_table}
WHEN EXISTS(SELECT 1 FROM {_table} WHERE {_key}=NEW.{_key})
BEGIN SELECT RAISE(ABORT,'immutable_metering_fact'); END;
"""
