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
"""
