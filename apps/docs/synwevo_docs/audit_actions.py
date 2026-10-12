"""Every audit action Synwevo Docs writes (admin coverage G26, decision D7 §9).

The account service labels these codes on its audit page from a copy of this list kept as data
(`services/identity/synwevo_identity/app_actions.py`); the repository boundary forbids either side
importing the other, so two tests keep the copies honest: `apps/docs/tests/test_audit_catalogue.py`
scans this application's source for the action literals and refuses one that is not listed here,
and `tests/integration/test_audit_catalogue.py` compares this list with the account service's.
"""
from __future__ import annotations

ACTIONS: tuple[str, ...] = (
    # projects and sources (services.py, views.py, definitions.py)
    "docs.project.create", "docs.project.update", "docs.project.delete", "docs.project.import",
    "docs.source.add", "docs.source.remove", "docs.source.reorder", "docs.source.representative",
    "docs.source.options", "docs.source.desensitize", "docs.source.ocr", "docs.source.correction",
    "docs.source.scan.retry",
    # templates, fields and mappings (services.py, builtin_service.py, definitions.py)
    "docs.template.upload", "docs.template.create", "docs.template.version", "docs.template.restore",
    "docs.template.undo", "docs.template.redo", "docs.template.rename", "docs.template.delete",
    "docs.template.duplicate", "docs.template.fields.import",
    "docs.mappings.save", "docs.mappings.undo", "docs.mappings.redo",
    # sharing (org_templates.py, comments.py, api/app.py, api/v1_copies.py)
    "docs.shared_template.publish", "docs.shared_template.release", "docs.shared_template.deprecate",
    "docs.shared_template.restore", "docs.shared_template.use",
    "docs.comment.create", "docs.comment.edit", "docs.comment.delete", "docs.comment.resolve", "docs.comment.reopen",
    "docs.copy.request", "docs.copy.decide", "docs.copy.withdraw",
    # jobs and download links (jobs.py, api/v1.py)
    "docs.job.create", "docs.job.cancel", "docs.job.retry", "docs.job.archive_link", "docs.artifact.link",
    # resumable uploads and regression cases (api/v1_uploads.py, regression.py)
    "docs.upload.create", "docs.upload.complete", "docs.upload.abort", "docs.upload.consume",
    "docs.regression_case.create", "docs.regression_case.delete",
    # webhook subscriptions (webhooks.py)
    "docs.webhook.create", "docs.webhook.update", "docs.webhook.delete", "docs.webhook.pause",
    "docs.webhook.resume", "docs.webhook.rotate_secret", "docs.webhook.ping", "docs.webhook.redeliver",
    "docs.webhook.disable",
)
