"""Synwevo Docs source-file events: the outcome of accepting an uploaded file, as outbox rows.

A script that uploads a file wants to hear the verdict — usable, needs OCR, refused, failed —
without polling. The verdict is a change of ``source_files.status``, and it is written from several
places (the upload's scan and parse, a quarantine of shared bytes, an operator's rescan, an OCR run
that turns ``needs_ocr`` into ``ready``, a CSV re-parse). Rather than trust every one of them to also
write an event, a trigger writes it: **in the same transaction as the status change**, so there is
no committed verdict without its event and no event for a change that rolled back. That is the
transactional outbox the webhook dispatcher already reads (``job_events``, ordered by ``seq``).

The rows use the job event log's shape with ``job_id = ''`` (they belong to no job) and a minimal
``data``: the file and project ids, the new status, the scan conclusion as a reason, and the
version. No file name, no content, no link — a receiver fetches anything else with its own
credential, so a revoked credential learns nothing more than the ids it was already sent.

Types:

* ``file.ready`` — parsed and usable (also after OCR turns a ``needs_ocr`` source ready);
* ``file.needs_ocr`` — parsed, but its pages have no text layer: run OCR before mapping;
* ``file.rejected`` — refused by the safety check: malware (``rejected``), or a password-protected
  or unreadable file (``error`` with ``scan.status`` ``encrypted`` / ``scan.checked`` false);
* ``file.failed`` — accepted but could not be parsed (``error`` for any other reason).

A file held in ``scan_failed`` (scanner unavailable, retried automatically; or larger than the
scanner accepts) emits nothing until it reaches one of the above.

Store alone executes ``SCHEMA_SQL``. Rollback: older code ignores the event types it does not know
(its dispatcher still delivers them to subscriptions filtering on ``*``, with ``job: null``).
"""
from __future__ import annotations

FILE_EVENT_TYPES = ("file.ready", "file.needs_ocr", "file.rejected", "file.failed")

SCHEMA_SQL = """
CREATE TRIGGER IF NOT EXISTS source_files_status_event
AFTER UPDATE OF status ON source_files
WHEN NEW.status IS NOT OLD.status AND NEW.status IN ('ready', 'needs_ocr', 'rejected', 'error') AND NEW.removed = 0
BEGIN
  INSERT INTO job_events (id, job_id, tenant, type, version, occurred_at, data)
  VALUES (
    'ev_' || lower(hex(randomblob(10))),
    '',
    COALESCE((SELECT tenant FROM projects WHERE id = NEW.project_id), 'default'),
    CASE
      WHEN NEW.status = 'ready' THEN 'file.ready'
      WHEN NEW.status = 'needs_ocr' THEN 'file.needs_ocr'
      WHEN NEW.status = 'rejected' THEN 'file.rejected'
      WHEN json_valid(NEW.scan) AND (json_extract(NEW.scan, '$.status') = 'encrypted'
                                     OR json_extract(NEW.scan, '$.checked') = 0) THEN 'file.rejected'
      ELSE 'file.failed'
    END,
    1,
    (julianday('now') - 2440587.5) * 86400.0,
    json_object('file_id', NEW.id, 'project_id', NEW.project_id, 'status', NEW.status,
                'reason', CASE WHEN json_valid(NEW.scan) THEN json_extract(NEW.scan, '$.status') END,
                'version', NEW.version)
  );
END;
"""
