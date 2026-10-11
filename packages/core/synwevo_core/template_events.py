"""Synwevo Docs template events, as outbox rows (API-5 event catalogue).

An integration that mirrors templates wants to hear that a template appeared, took a new head
version, was deleted, or was published to its organization — without polling. Templates are
written from several places (upload, the field editor, restore, copy into another workspace, the
built-in editor, publication), so a trigger writes each event **in the same transaction as the row
change** (the pattern of synwevo_core.docs_file_events). The rows use the job event log's shape with
``job_id = ''`` and go to the template's workspace tenant (a publication's events to the publishing
workspace), which is whose subscriptions and feed may see them.

The data is identifiers and version numbers only — never the template's name, its file or its
fields: a receiver fetches those with its own credential (``GET /templates/{tid}``).

Types:

* ``template.created`` — a template row appeared (upload, built-in editor, restore-as-new, a copy
  into this workspace, a template taken from the organization library);
* ``template.updated`` — the head version moved (a field edit, a saved built-in template, a
  restore); ``previous_head`` says from where;
* ``template.deleted`` — the template was deleted (soft: its versions stay readable to nobody);
* ``template.published`` — the template was published to its organization's library
  (``publication_id``, ``organization_id``, ``release_no`` 1);
* ``template.released`` — a later release of a publication (``release_no`` > 1);
* ``template.deprecated`` — a publication was deprecated.

Store alone executes ``SCHEMA_SQL``, after the template and organization-template tables and the
``templates.tenant`` column exist. Rollback: older code ignores the types it does not know.
"""
from __future__ import annotations

TEMPLATE_EVENT_TYPES = ("template.created", "template.updated", "template.deleted",
                        "template.published", "template.released", "template.deprecated")

_NOW = "(julianday('now') - 2440587.5) * 86400.0"
_ID = "'ev_' || lower(hex(randomblob(10)))"


def _event(trigger: str, when: str, tenant: str, type_: str, data: str) -> str:
    return f"""
CREATE TRIGGER IF NOT EXISTS {trigger}
{when}
BEGIN
  INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data)
  VALUES ({_ID}, '', {tenant}, '{type_}', 1, {_NOW}, {data});
END;
"""


_PUBLICATION_TENANT = "(SELECT source_tenant FROM organization_templates WHERE id = NEW.publication_id)"

SCHEMA_SQL = "".join([
    _event("templates_created_event", "AFTER INSERT ON templates\nWHEN COALESCE(NEW.deleted, 0) = 0",
           "COALESCE(NEW.tenant, 'default')", "template.created",
           "json_object('template_id', NEW.id, 'kind', NEW.kind, 'format', NEW.format, 'head', NEW.head)"),
    _event("templates_updated_event",
           "AFTER UPDATE OF head ON templates\nWHEN NEW.head IS NOT OLD.head AND COALESCE(NEW.deleted, 0) = 0",
           "COALESCE(NEW.tenant, 'default')", "template.updated",
           "json_object('template_id', NEW.id, 'head', NEW.head, 'previous_head', OLD.head)"),
    _event("templates_deleted_event",
           "AFTER UPDATE OF deleted ON templates\nWHEN COALESCE(NEW.deleted, 0) = 1 AND COALESCE(OLD.deleted, 0) = 0",
           "COALESCE(NEW.tenant, 'default')", "template.deleted",
           "json_object('template_id', NEW.id, 'head', NEW.head)"),
    _event("organization_templates_published_event", "AFTER INSERT ON organization_templates",
           "NEW.source_tenant", "template.published",
           "json_object('template_id', NEW.template_id, 'publication_id', NEW.id, 'organization_id', NEW.organization_id, "
           "'release_no', NEW.release_no, 'version_no', NEW.version_no)"),
    _event("organization_template_releases_event",
           "AFTER INSERT ON organization_template_releases\nWHEN NEW.release_no > 1",
           _PUBLICATION_TENANT, "template.released",
           "json_object('template_id', (SELECT template_id FROM organization_templates WHERE id = NEW.publication_id), "
           "'publication_id', NEW.publication_id, "
           "'organization_id', (SELECT organization_id FROM organization_templates WHERE id = NEW.publication_id), "
           "'release_no', NEW.release_no, 'version_no', NEW.version_no)"),
    _event("organization_templates_deprecated_event",
           "AFTER UPDATE OF deprecated_at ON organization_templates\nWHEN NEW.deprecated_at IS NOT NULL AND OLD.deprecated_at IS NULL",
           "NEW.source_tenant", "template.deprecated",
           "json_object('template_id', NEW.template_id, 'publication_id', NEW.id, 'organization_id', NEW.organization_id, "
           "'release_no', NEW.release_no)"),
])
