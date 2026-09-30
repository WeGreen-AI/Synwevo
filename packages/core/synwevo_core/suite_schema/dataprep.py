"""Synwevo DataPrep: uploaded files, datasets, recipes (draft and immutable published versions),
runs and the complete issue list of each run. Owner: `synwevo_suite.dataprep`.

* ``dataprep_files`` — an uploaded CSV / XLSX that passed the platform's upload check; the bytes
  are a content-addressed blob (``blob``). A file is read into a dataset with declared options.
* ``dataprep_datasets`` — an immutable table: every value kept as the text that was read, stored
  as a compressed blob (``rows_blob``); ``content_hash`` is the fingerprint of the canonical table
  (not a blob id). A run's output is a dataset too (``origin='run'``).
* ``dataprep_recipes`` / ``dataprep_recipe_versions`` — the draft is edited with a revision; a
  published version is frozen and never rewritten; runs pin one.
* ``dataprep_runs`` — one run: the pinned version, inputs (dataset id + content hash) and
  parameters, its persisted state (queued → running → succeeded / partial / failed / cancelled),
  progress, counts, step reports and the output dataset. ``heartbeat_at`` is how an interrupted
  run is recognised after a restart.
* ``dataprep_run_issues`` — every row-level issue of a run, with the row number in its source.

Column names avoid the ones `Store` decodes as JSON by name (`store.JSON_COLS`) except ``scan``.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS dataprep_files (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    blob TEXT NOT NULL,
    filename TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('csv','xlsx')),
    size INTEGER NOT NULL,
    scan TEXT,
    uploaded_by TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS dataprep_files_workspace ON dataprep_files(workspace_id, created_at);

CREATE TABLE IF NOT EXISTS dataprep_datasets (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT NOT NULL CHECK (origin IN ('file','records','form','run')),
    file_id TEXT,
    run_id TEXT,
    source_json TEXT NOT NULL,
    columns_json TEXT NOT NULL,
    read_notes_json TEXT NOT NULL DEFAULT '[]',
    read_problems_json TEXT NOT NULL DEFAULT '[]',
    row_count INTEGER NOT NULL,
    column_count INTEGER NOT NULL,
    rows_blob TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX IF NOT EXISTS dataprep_datasets_workspace ON dataprep_datasets(workspace_id, status, created_at);

CREATE TABLE IF NOT EXISTS dataprep_recipes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('active','archived')),
    draft_json TEXT NOT NULL,
    draft_rev INTEGER NOT NULL DEFAULT 1,
    sample_json TEXT NOT NULL DEFAULT '{}',
    published_version INTEGER,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived_at REAL
);
CREATE INDEX IF NOT EXISTS dataprep_recipes_workspace ON dataprep_recipes(workspace_id, status, updated_at);

CREATE TABLE IF NOT EXISTS dataprep_recipe_versions (
    recipe_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    published_by TEXT NOT NULL,
    published_at REAL NOT NULL,
    PRIMARY KEY (recipe_id, version)
);

CREATE TABLE IF NOT EXISTS dataprep_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    recipe_id TEXT NOT NULL,
    recipe_version INTEGER NOT NULL,
    definition_hash TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    params_json TEXT NOT NULL,
    output_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','partial','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    progress_json TEXT NOT NULL DEFAULT '{}',
    counts_json TEXT,
    reports_json TEXT,
    output_dataset_id TEXT,
    output_hash TEXT,
    issue_total INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    error_detail_json TEXT,
    op_run_id TEXT,
    correlation_id TEXT,
    via TEXT NOT NULL DEFAULT 'ui',
    worker TEXT,
    heartbeat_at REAL,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS dataprep_runs_workspace ON dataprep_runs(workspace_id, created_at);
CREATE INDEX IF NOT EXISTS dataprep_runs_status ON dataprep_runs(status, created_at);

CREATE TABLE IF NOT EXISTS dataprep_run_issues (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    workspace_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    code TEXT NOT NULL,
    step_index INTEGER,
    step_id TEXT,
    input_name TEXT,
    source_row INTEGER,
    column_name TEXT,
    value TEXT,
    params_json TEXT,
    message TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS dataprep_run_issues_filter ON dataprep_run_issues(run_id, severity, code);
"""

COLUMNS = (
    ('dataprep_files', 'upload_key', 'TEXT'),
    ('dataprep_datasets', 'stored_bytes', 'INTEGER NOT NULL DEFAULT 0'),
    ('dataprep_recipe_versions', 'status', "TEXT NOT NULL DEFAULT 'published'"),
    ('dataprep_runs', 'attempts', 'INTEGER NOT NULL DEFAULT 0'),
)
INDEXES = (('dataprep_files_upload', 'dataprep_files', ('workspace_id', 'uploaded_by', 'upload_key')),)
