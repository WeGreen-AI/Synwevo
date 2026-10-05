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

-- Governed peak windows and public-holiday calendar (billing/bands.py). Append-only versions.
CREATE TABLE IF NOT EXISTS billing_band_schedules (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')), schedule_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0), spec_json TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id), reason TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL, PRIMARY KEY(environment,schedule_id,revision)
);
CREATE TRIGGER IF NOT EXISTS billing_band_schedules_no_update BEFORE UPDATE ON billing_band_schedules
BEGIN SELECT RAISE(ABORT,'immutable_band_schedule'); END;
CREATE TRIGGER IF NOT EXISTS billing_band_schedules_no_delete BEFORE DELETE ON billing_band_schedules
BEGIN SELECT RAISE(ABORT,'immutable_band_schedule'); END;
CREATE TRIGGER IF NOT EXISTS billing_band_schedules_no_replace BEFORE INSERT ON billing_band_schedules
WHEN EXISTS(SELECT 1 FROM billing_band_schedules WHERE (environment=NEW.environment AND schedule_id=NEW.schedule_id
 AND revision=NEW.revision) OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_band_schedule'); END;
-- Revisions are consecutive: a concurrent publisher cannot skip or fork the history.
CREATE TRIGGER IF NOT EXISTS billing_band_schedules_next_revision BEFORE INSERT ON billing_band_schedules
WHEN NEW.revision <> 1 + COALESCE((SELECT MAX(revision) FROM billing_band_schedules
 WHERE environment=NEW.environment AND schedule_id=NEW.schedule_id), 0)
BEGIN SELECT RAISE(ABORT,'band_schedule_revision_gap'); END;

-- Sale prices published as cost price x markup: provenance of each derived sale snapshot.
CREATE TABLE IF NOT EXISTS model_price_markups (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')), price_id TEXT NOT NULL,
    model TEXT NOT NULL, cost_price_id TEXT NOT NULL, spec_json TEXT NOT NULL,
    actor_id TEXT NOT NULL REFERENCES users(id), operation_id TEXT NOT NULL UNIQUE, reason TEXT NOT NULL,
    created_at INTEGER NOT NULL, PRIMARY KEY(environment,price_id),
    FOREIGN KEY(environment,price_id) REFERENCES billing_prices(environment,price_id),
    FOREIGN KEY(environment,cost_price_id) REFERENCES billing_prices(environment,price_id)
);
CREATE INDEX IF NOT EXISTS model_price_markups_page ON model_price_markups(environment,created_at);
CREATE TRIGGER IF NOT EXISTS model_price_markups_no_update BEFORE UPDATE ON model_price_markups
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
CREATE TRIGGER IF NOT EXISTS model_price_markups_no_delete BEFORE DELETE ON model_price_markups
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
CREATE TRIGGER IF NOT EXISTS model_price_markups_no_replace BEFORE INSERT ON model_price_markups
WHEN EXISTS(SELECT 1 FROM model_price_markups WHERE (environment=NEW.environment AND price_id=NEW.price_id)
 OR operation_id=NEW.operation_id)
BEGIN SELECT RAISE(ABORT,'immutable_price_publication'); END;
"""

# The band a call is charged in, pinned by the ledger's dispatch transaction, and when the
# provider exchange ended. Rows dispatched before this migration keep NULL (flat tariffs only).
COLUMNS = (
    ("billing_attempts", "band", "TEXT CHECK(band IN ('flat','peak','off_peak'))"),
    ("billing_attempts", "band_schedule_revision", "INTEGER"),
    ("billing_attempts", "completed_at", "INTEGER"),
)


def migrate(c) -> None:
    # Created after COLUMNS so the trigger's column references exist on upgraded databases.
    c.execute("""
CREATE TRIGGER IF NOT EXISTS billing_attempts_pinned_band BEFORE UPDATE ON billing_attempts
WHEN (OLD.band IS NOT NULL AND NEW.band IS NOT OLD.band)
 OR (OLD.band_schedule_revision IS NOT NULL AND NEW.band_schedule_revision IS NOT OLD.band_schedule_revision)
 OR (OLD.completed_at IS NOT NULL AND NEW.completed_at IS NOT OLD.completed_at)
 OR (NEW.band IS NOT NULL AND NEW.dispatched_at IS NULL)
 OR (NEW.band IN ('peak','off_peak') AND NEW.band_schedule_revision IS NULL)
BEGIN SELECT RAISE(ABORT,'immutable_billing_contract'); END;""")
