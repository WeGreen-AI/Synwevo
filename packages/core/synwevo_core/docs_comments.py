"""Schema for comments on Synwevo Docs projects. The rules live in Synwevo Docs
(`synwevo_docs.comments`); the table is declared here because `synwevo_core.store.Store` is the
one place that creates and migrates the database."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS project_comments (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, tenant TEXT NOT NULL, author_id TEXT NOT NULL,
  body TEXT NOT NULL, anchor TEXT, mentions TEXT, parent_id TEXT,
  resolved_at REAL, resolved_by TEXT, created_at REAL NOT NULL, edited_at REAL, deleted_at REAL
);
CREATE INDEX IF NOT EXISTS ix_project_comments_project ON project_comments(project_id, created_at);
"""
