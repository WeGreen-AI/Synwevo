"""Single-use personal model budgets. No default prices, balances or permission grants."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_model_quotes (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL,
    binding_digest TEXT NOT NULL, scope_json TEXT, terms_json TEXT NOT NULL,
    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
    accepted_turn_id TEXT UNIQUE, revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS agent_model_quotes_owner ON agent_model_quotes(user_id,conversation_id,created_at);
CREATE TRIGGER IF NOT EXISTS agent_model_quotes_fixed BEFORE UPDATE ON agent_model_quotes
WHEN NEW.id<>OLD.id OR NEW.environment<>OLD.environment OR NEW.namespace<>OLD.namespace
 OR NEW.user_id<>OLD.user_id OR NEW.conversation_id<>OLD.conversation_id
 OR NEW.binding_digest<>OLD.binding_digest OR NEW.scope_json IS NOT OLD.scope_json
 OR NEW.terms_json<>OLD.terms_json OR NEW.created_at<>OLD.created_at OR NEW.expires_at<>OLD.expires_at
 OR (OLD.accepted_turn_id IS NOT NULL AND NEW.accepted_turn_id IS NOT OLD.accepted_turn_id)
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at)
BEGIN SELECT RAISE(ABORT,'immutable_model_budget'); END;
CREATE TRIGGER IF NOT EXISTS agent_model_quotes_no_replace BEFORE INSERT ON agent_model_quotes
WHEN EXISTS(SELECT 1 FROM agent_model_quotes WHERE id=NEW.id OR accepted_turn_id=NEW.accepted_turn_id)
BEGIN SELECT RAISE(ABORT,'immutable_model_budget'); END;
CREATE TRIGGER IF NOT EXISTS agent_model_quotes_no_delete BEFORE DELETE ON agent_model_quotes
BEGIN SELECT RAISE(ABORT,'immutable_model_budget'); END;
"""
