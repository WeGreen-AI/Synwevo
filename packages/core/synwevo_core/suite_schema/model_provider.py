"""Immutable route metadata and one sealed active key per fixed provider account/environment.

Credential ciphertext is replaceable by rotation/revocation and by the existing root rekey;
old plaintext/ciphertext is not kept in configuration history. Prior attempts keep their
non-secret route/account attribution in the existing billing evidence.
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS model_provider_credentials (
    environment TEXT PRIMARY KEY CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    secret_enc TEXT
);
CREATE TABLE IF NOT EXISTS model_provider_versions (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    spec_json TEXT NOT NULL,
    credential_revision INTEGER NOT NULL CHECK(credential_revision>=0),
    credential_configured INTEGER NOT NULL CHECK(credential_configured IN (0,1)),
    actor_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL,
    operation_id TEXT NOT NULL UNIQUE, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,revision)
);
CREATE TRIGGER IF NOT EXISTS model_provider_no_update BEFORE UPDATE ON model_provider_versions
BEGIN SELECT RAISE(ABORT,'immutable_model_provider'); END;
CREATE TRIGGER IF NOT EXISTS model_provider_no_delete BEFORE DELETE ON model_provider_versions
BEGIN SELECT RAISE(ABORT,'immutable_model_provider'); END;
CREATE TRIGGER IF NOT EXISTS model_provider_no_replace BEFORE INSERT ON model_provider_versions
WHEN EXISTS(SELECT 1 FROM model_provider_versions
 WHERE (environment=NEW.environment AND revision=NEW.revision) OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_model_provider'); END;
-- Append-only health observations of one published provider revision (billing/provider_settings.py).
-- model_missing (GET /models no longer lists the route's model) and upstream_balance (HTTP 402)
-- close paid dispatch for that revision; a later revision starts open. No secret or content here.
CREATE TABLE IF NOT EXISTS model_provider_health (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    seq INTEGER NOT NULL CHECK(typeof(seq)='integer' AND seq>0),
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    state TEXT NOT NULL CHECK(state IN ('model_listed','model_missing','models_check_failed','upstream_balance')),
    requested_model TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,seq)
);
CREATE INDEX IF NOT EXISTS model_provider_health_revision ON model_provider_health(environment,revision,seq);
CREATE TRIGGER IF NOT EXISTS model_provider_health_no_update BEFORE UPDATE ON model_provider_health
BEGIN SELECT RAISE(ABORT,'immutable_model_provider_health'); END;
CREATE TRIGGER IF NOT EXISTS model_provider_health_no_delete BEFORE DELETE ON model_provider_health
BEGIN SELECT RAISE(ABORT,'immutable_model_provider_health'); END;
"""
