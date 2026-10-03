"""Bounded, revocable personal model-spending consent. No implicit authority or funds."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_model_consents (
    id TEXT PRIMARY KEY, environment TEXT NOT NULL, namespace TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id), conversation_id TEXT NOT NULL,
    basis_quote_id TEXT NOT NULL REFERENCES agent_model_quotes(id),
    scope_json TEXT NOT NULL, terms_json TEXT NOT NULL,
    max_runs INTEGER NOT NULL CHECK(max_runs BETWEEN 1 AND 1000),
    created_at INTEGER NOT NULL, expires_at INTEGER, revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS agent_model_consents_owner
    ON agent_model_consents(user_id,conversation_id,created_at);
CREATE TABLE IF NOT EXISTS agent_quote_consents (
    quote_id TEXT PRIMARY KEY REFERENCES agent_model_quotes(id),
    consent_id TEXT NOT NULL REFERENCES agent_model_consents(id),
    ordinal INTEGER NOT NULL CHECK(ordinal>0),
    UNIQUE(consent_id,ordinal)
);
CREATE TRIGGER IF NOT EXISTS agent_model_consents_fixed BEFORE UPDATE ON agent_model_consents
WHEN NEW.id<>OLD.id OR NEW.environment<>OLD.environment OR NEW.namespace<>OLD.namespace
 OR NEW.user_id<>OLD.user_id OR NEW.conversation_id<>OLD.conversation_id
 OR NEW.basis_quote_id<>OLD.basis_quote_id OR NEW.scope_json<>OLD.scope_json
 OR NEW.terms_json<>OLD.terms_json OR NEW.max_runs<>OLD.max_runs
 OR NEW.created_at<>OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at
 OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at)
BEGIN SELECT RAISE(ABORT,'immutable_model_consent'); END;
CREATE TRIGGER IF NOT EXISTS agent_model_consents_no_replace BEFORE INSERT ON agent_model_consents
WHEN EXISTS(SELECT 1 FROM agent_model_consents WHERE id=NEW.id)
BEGIN SELECT RAISE(ABORT,'immutable_model_consent'); END;
CREATE TRIGGER IF NOT EXISTS agent_model_consents_no_delete BEFORE DELETE ON agent_model_consents
BEGIN SELECT RAISE(ABORT,'immutable_model_consent'); END;
"""
for _op in ('UPDATE', 'DELETE'):
    SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS agent_quote_consents_no_{_op.lower()} BEFORE {_op} ON agent_quote_consents
BEGIN SELECT RAISE(ABORT,'immutable_model_consent'); END;
"""
SCHEMA += """
CREATE TRIGGER IF NOT EXISTS agent_quote_consents_no_replace BEFORE INSERT ON agent_quote_consents
WHEN EXISTS(SELECT 1 FROM agent_quote_consents WHERE quote_id=NEW.quote_id
 OR (consent_id=NEW.consent_id AND ordinal=NEW.ordinal))
BEGIN SELECT RAISE(ABORT,'immutable_model_consent'); END;
"""
