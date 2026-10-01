"""Append-only cost policy revisions; Store is the only schema owner."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS model_cost_policy_versions (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    spec_json TEXT NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
    reason TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,revision)
);
CREATE TRIGGER IF NOT EXISTS model_cost_policy_no_update BEFORE UPDATE ON model_cost_policy_versions
BEGIN SELECT RAISE(ABORT,'immutable_cost_policy'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_policy_no_delete BEFORE DELETE ON model_cost_policy_versions
BEGIN SELECT RAISE(ABORT,'immutable_cost_policy'); END;
CREATE TRIGGER IF NOT EXISTS model_cost_policy_no_replace BEFORE INSERT ON model_cost_policy_versions
WHEN EXISTS(SELECT 1 FROM model_cost_policy_versions
 WHERE (environment=NEW.environment AND revision=NEW.revision) OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_cost_policy'); END;
"""
