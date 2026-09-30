"""Governed drafts and publication provenance; Store is the only migration runner.

No commercial choices or prices are seeded. Existing billing_prices remains the sole
immutable price source consumed by the ledger and model gateway.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_price_drafts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    revision INTEGER NOT NULL CHECK(revision>0), status TEXT NOT NULL CHECK(status IN ('draft','published')),
    spec_json TEXT NOT NULL, creation_json TEXT NOT NULL, create_key TEXT NOT NULL,
    creator_id TEXT NOT NULL REFERENCES users(id), actor_id TEXT NOT NULL REFERENCES users(id),
    price_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE(environment,creator_id,create_key), UNIQUE(environment,price_id),
    FOREIGN KEY(environment,price_id) REFERENCES billing_prices(environment,price_id)
);
CREATE INDEX IF NOT EXISTS model_price_drafts_page ON model_price_drafts(environment,seq);
CREATE TABLE IF NOT EXISTS model_price_publications (
    environment TEXT NOT NULL, price_id TEXT NOT NULL, draft_id TEXT NOT NULL UNIQUE,
    draft_revision INTEGER NOT NULL, actor_id TEXT NOT NULL REFERENCES users(id),
    operation_id TEXT NOT NULL UNIQUE, reason TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,price_id),
    FOREIGN KEY(environment,price_id) REFERENCES billing_prices(environment,price_id),
    FOREIGN KEY(draft_id) REFERENCES model_price_drafts(id)
);
CREATE TRIGGER IF NOT EXISTS model_price_drafts_published_locked BEFORE UPDATE ON model_price_drafts
WHEN OLD.status='published' OR NEW.id<>OLD.id OR NEW.environment<>OLD.environment
 OR NEW.creator_id<>OLD.creator_id OR NEW.create_key<>OLD.create_key
 OR NEW.creation_json<>OLD.creation_json OR NEW.created_at<>OLD.created_at OR NEW.seq<>OLD.seq
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
CREATE TRIGGER IF NOT EXISTS model_price_drafts_no_delete BEFORE DELETE ON model_price_drafts
BEGIN SELECT RAISE(ABORT,'immutable_price_provenance'); END;
CREATE TRIGGER IF NOT EXISTS model_price_drafts_no_replace BEFORE INSERT ON model_price_drafts
WHEN EXISTS(SELECT 1 FROM model_price_drafts WHERE id=NEW.id OR seq=NEW.seq
 OR (environment=NEW.environment AND creator_id=NEW.creator_id AND create_key=NEW.create_key)
 OR (environment=NEW.environment AND price_id=NEW.price_id))
BEGIN SELECT RAISE(ABORT,'immutable_price_provenance'); END;
CREATE TRIGGER IF NOT EXISTS model_price_publications_no_update BEFORE UPDATE ON model_price_publications
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
CREATE TRIGGER IF NOT EXISTS model_price_publications_no_delete BEFORE DELETE ON model_price_publications
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
CREATE TRIGGER IF NOT EXISTS model_price_publications_no_replace BEFORE INSERT ON model_price_publications
WHEN EXISTS(SELECT 1 FROM model_price_publications WHERE (environment=NEW.environment AND price_id=NEW.price_id)
 OR draft_id=NEW.draft_id OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
"""
