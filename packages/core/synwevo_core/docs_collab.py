"""Schema of Synwevo Docs' live co-editing (v20, v21): the persisted state of a live document and
its attributed change log. The rooms themselves live in the Docs process (``synwevo_docs.collab``);
this module only lets the single store own the tables, like every other schema."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS collab_docs (
  room TEXT PRIMARY KEY,               -- template:<tid>
  tenant TEXT NOT NULL,
  template_id TEXT NOT NULL,
  state BLOB NOT NULL,                 -- the whole live document as one CRDT update
  head INTEGER,                        -- the template version the live document was last saved to / seeded from
  dirty INTEGER NOT NULL DEFAULT 0,    -- live edits not yet saved as a version
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS collab_changes (
  id TEXT PRIMARY KEY,
  room TEXT NOT NULL,
  tenant TEXT NOT NULL,
  user_id TEXT,
  part TEXT NOT NULL,                  -- body / header / footer
  kind TEXT NOT NULL,                  -- edit / insert / delete / restore
  block_no INTEGER,
  node_type TEXT,
  excerpt TEXT,
  count INTEGER NOT NULL DEFAULT 1,
  started_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_collab_changes_room ON collab_changes (room, updated_at);
-- v21: what happened to a project, attributed (mappings saved/merged/undone, sources, fields,
-- template versions, settings); the project page's activity list and its live change frames.
CREATE TABLE IF NOT EXISTS project_activity (
  id TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  project_id TEXT NOT NULL,
  user_id TEXT,
  kind TEXT NOT NULL,
  detail TEXT,                          -- JSON: what changed, in the service's own words
  at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_project_activity_project ON project_activity (project_id, at);
"""
