"""Organization-shared Docs templates: the schema only.

A workspace's editors can publish one of their templates to the organization the workspace
belongs to. What is published is a frozen release — the working file, the fields and the
metadata of the template's head version at that moment — so a later edit or deletion of the
source changes nothing for the organization, and republishing is an explicit new release.
Using a shared template makes an ordinary template in the reader's own workspace (a copy of
the release, with its origin recorded), so projects, mappings and jobs keep working exactly as
they do with any other template and a job stays reproducible whatever happens to the release.

The behaviour lives in Synwevo Docs (`synwevo_docs.org_templates`); the tables live here because
`synwevo_core.store.Store` is the only place that creates tables.
"""
from __future__ import annotations

SCHEMA = """
CREATE TABLE IF NOT EXISTS organization_templates (
  id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, source_tenant TEXT NOT NULL, template_id TEXT NOT NULL,
  name TEXT NOT NULL, description TEXT, format TEXT NOT NULL, kind TEXT,
  release_no INTEGER NOT NULL DEFAULT 1, version_no INTEGER NOT NULL, working_blob TEXT NOT NULL, slots TEXT NOT NULL, info TEXT,
  published_by TEXT NOT NULL, published_at REAL NOT NULL, updated_by TEXT, updated_at REAL NOT NULL,
  deprecated_at REAL, deprecated_by TEXT, deprecation_note TEXT, use_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE (organization_id, template_id)
);
CREATE INDEX IF NOT EXISTS ix_org_templates_org ON organization_templates(organization_id, deprecated_at, updated_at);
CREATE TABLE IF NOT EXISTS organization_template_releases (
  publication_id TEXT NOT NULL, release_no INTEGER NOT NULL, version_no INTEGER NOT NULL, working_blob TEXT NOT NULL, slots TEXT NOT NULL,
  info TEXT, note TEXT, released_by TEXT NOT NULL, released_at REAL NOT NULL,
  PRIMARY KEY (publication_id, release_no)
);
"""
