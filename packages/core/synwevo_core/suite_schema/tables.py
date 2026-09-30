"""Synwevo Tables: data tables, their typed fields, records, uniqueness keys, relations, saved
views, the change history and the staging rows of imports and exports. Owner: `synwevo_suite.tables`.

* A field has a stable id (``fld_…``); a record's values are one JSON document keyed by those ids
  (``tb_records.cells``), so renaming a field never touches a record.
* Every record carries a revision (``rev``): an update names the revision it was based on and a
  stale one is refused, never merged silently.
* Uniqueness is a row per (table, field, normalised value) in ``tb_unique`` whose primary key is the
  constraint itself — two writers racing for the same value cannot both commit.
* ``tb_links`` mirrors the relation cells (record → linked record) so "who points at this record"
  is an index lookup; a relation to a record in the trash stays and is shown as unavailable.
* ``tb_changes`` is the history: record edits (old → new per field), schema changes, views.
* ``tb_leftovers`` keeps every value a type change could not convert (or converted with loss) —
  nothing a conversion touches is ever dropped.
* Imports keep the rows read from the file (not the file) for a limited time; exports are a
  short-lived ticket for the person who asked, the file is built when it is downloaded.

Column names avoid the ones `Store` decodes as JSON by name (`store.JSON_COLS`), and the words the
blob inventory treats as digests.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tb_tables (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    primary_field_id TEXT,
    schema_rev INTEGER NOT NULL DEFAULT 1,
    next_seq INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX IF NOT EXISTS tb_tables_workspace ON tb_tables(workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS tb_fields (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    config TEXT NOT NULL DEFAULT '{}',
    required INTEGER NOT NULL DEFAULT 0,
    is_unique INTEGER NOT NULL DEFAULT 0,
    default_json TEXT,
    read_role TEXT NOT NULL DEFAULT 'viewer' CHECK (read_role IN ('viewer','editor','admin')),
    position REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','deleted')),
    created_by TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    deleted_by TEXT
);
CREATE INDEX IF NOT EXISTS tb_fields_table ON tb_fields(table_id, status, position);

CREATE TABLE IF NOT EXISTS tb_records (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    rev INTEGER NOT NULL DEFAULT 1,
    cells TEXT NOT NULL DEFAULT '{}',
    formula_errors TEXT,
    created_by TEXT,
    created_at REAL NOT NULL,
    updated_by TEXT,
    updated_at REAL NOT NULL,
    deleted_at REAL,
    deleted_by TEXT,
    UNIQUE (table_id, seq)
);
CREATE INDEX IF NOT EXISTS tb_records_live ON tb_records(table_id, deleted_at, seq);

CREATE TABLE IF NOT EXISTS tb_unique (
    table_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    value_key TEXT NOT NULL,
    record_id TEXT NOT NULL,
    PRIMARY KEY (table_id, field_id, value_key)
);
CREATE INDEX IF NOT EXISTS tb_unique_record ON tb_unique(record_id);

CREATE TABLE IF NOT EXISTS tb_links (
    field_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (field_id, record_id, target_id)
);
CREATE INDEX IF NOT EXISTS tb_links_target ON tb_links(target_id);
CREATE INDEX IF NOT EXISTS tb_links_record ON tb_links(record_id);

CREATE TABLE IF NOT EXISTS tb_changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    record_id TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('record','schema','view','table')),
    action TEXT NOT NULL,
    field_id TEXT,
    rev INTEGER,
    diff TEXT,
    actor_id TEXT,
    via TEXT,
    run_id TEXT,
    at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tb_changes_table ON tb_changes(table_id, seq);
CREATE INDEX IF NOT EXISTS tb_changes_record ON tb_changes(record_id, seq);

CREATE TABLE IF NOT EXISTS tb_leftovers (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    field_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    from_type TEXT NOT NULL,
    from_config TEXT NOT NULL,
    original_json TEXT NOT NULL,
    converted_json TEXT,
    outcome TEXT NOT NULL CHECK (outcome IN ('failed','lossy')),
    schema_rev INTEGER NOT NULL,
    created_by TEXT,
    created_at REAL NOT NULL,
    restored_at REAL
);
CREATE INDEX IF NOT EXISTS tb_leftovers_field ON tb_leftovers(field_id, restored_at);
CREATE INDEX IF NOT EXISTS tb_leftovers_record ON tb_leftovers(record_id);

CREATE TABLE IF NOT EXISTS tb_views (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    config TEXT NOT NULL,
    position REAL NOT NULL,
    rev INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tb_views_table ON tb_views(table_id, position);

CREATE TABLE IF NOT EXISTS tb_imports (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    format TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    notes_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('ready','running','done','partial','failed')),
    mapping_json TEXT,
    outcome_json TEXT,
    run_id TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS tb_imports_table ON tb_imports(table_id, created_at);
CREATE INDEX IF NOT EXISTS tb_imports_expiry ON tb_imports(expires_at);

CREATE TABLE IF NOT EXISTS tb_exports (
    id TEXT PRIMARY KEY,
    table_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('csv','xlsx')),
    query_json TEXT NOT NULL,
    run_id TEXT,
    downloads INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS tb_exports_expiry ON tb_exports(expires_at);
"""

# Additive for worktrees that already opened a Store with the initial Tables schema.
COLUMNS = (
    ('tb_tables', 'read_role', "TEXT NOT NULL DEFAULT 'viewer'"),
    ('tb_tables', 'write_role', "TEXT NOT NULL DEFAULT 'editor'"),
    ('tb_fields', 'write_role', "TEXT NOT NULL DEFAULT 'editor'"),
    ('tb_records', 'read_role', "TEXT NOT NULL DEFAULT 'viewer'"),
    ('tb_records', 'write_role', "TEXT NOT NULL DEFAULT 'editor'"),
)
