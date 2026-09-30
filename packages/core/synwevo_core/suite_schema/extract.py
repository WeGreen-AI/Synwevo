"""Extract-owned metadata. Only Store applies this additive schema.

Blob reference integration: extract_files.blob (workspace_id); definition_digest and
content_digest are checksums, never alternate blob access paths.
"""

COLUMNS = (("extract_files", "source_json", "TEXT NOT NULL DEFAULT '{}'"),)
SCHEMA = """
CREATE TABLE IF NOT EXISTS extract_templates (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
 draft_json TEXT NOT NULL, draft_rev INTEGER NOT NULL DEFAULT 1,
 published_version INTEGER, published_rev INTEGER, archived INTEGER NOT NULL DEFAULT 0,
 created_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS extract_templates_workspace ON extract_templates(workspace_id, updated_at, id);
CREATE TABLE IF NOT EXISTS extract_versions (
 template_id TEXT NOT NULL, workspace_id TEXT NOT NULL, version INTEGER NOT NULL,
 title TEXT NOT NULL, definition_json TEXT NOT NULL, definition_digest TEXT NOT NULL,
 retired INTEGER NOT NULL DEFAULT 0, published_by TEXT NOT NULL, published_at REAL NOT NULL,
 PRIMARY KEY(template_id, version)
);
CREATE TABLE IF NOT EXISTS extract_files (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, filename TEXT NOT NULL, content_type TEXT NOT NULL,
 blob TEXT NOT NULL, content_digest TEXT NOT NULL, size INTEGER NOT NULL, scan_json TEXT NOT NULL,
 created_by TEXT NOT NULL, created_at REAL NOT NULL, idempotency_key TEXT NOT NULL,
 UNIQUE(workspace_id, created_by, idempotency_key)
);
CREATE INDEX IF NOT EXISTS extract_files_blob ON extract_files(blob);
CREATE TABLE IF NOT EXISTS extract_tasks (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, template_id TEXT NOT NULL, template_version INTEGER NOT NULL,
 file_id TEXT NOT NULL, created_by TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_input','succeeded','failed','cancelled')),
 ocr INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0, confirmed_revision INTEGER,
 attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until REAL, error_code TEXT,
 parse_json TEXT, machine_json TEXT, current_json TEXT,
 operation_run_id TEXT NOT NULL, correlation_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS extract_tasks_queue ON extract_tasks(status, lease_until, created_at);
CREATE INDEX IF NOT EXISTS extract_tasks_workspace ON extract_tasks(workspace_id, created_at, id);
CREATE TABLE IF NOT EXISTS extract_reviews (
 task_id TEXT NOT NULL, workspace_id TEXT NOT NULL, revision INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('correction','confirmation')), values_json TEXT NOT NULL,
 note TEXT NOT NULL, actor_id TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(task_id, revision)
);
"""
