"""Every audit action the workplace applications write outside the capability centre (admin
coverage G26, decision D7 §9).

Registered operations are audited under their own operation id (`forms.form.create`, …) by
`synwevo_core.capabilities`; this list is the rest — the byte-moving routes, public form fills,
the engines' own transitions, automation triggers and webhook operations. The account service
labels these codes from a copy kept as data (`services/identity/synwevo_identity/app_actions.py`);
`apps/suite/tests/test_audit_catalogue.py` scans the source for the literals and
`tests/integration/test_audit_catalogue.py` compares the two lists.
"""
from __future__ import annotations

ACTIONS: tuple[str, ...] = (
    "suite.forms.download", "suite.forms.file.upload", "suite.forms.file.discard",
    "suite.forms.submission.create", "suite.forms.submission.resubmit", "suite.forms.request.expire",
    "suite.tables.upload", "suite.tables.download",
    "suite.dataprep.upload", "suite.dataprep.export",
    "suite.extract.upload", "suite.extract.download", "suite.extract.export",
    "suite.approvals.stage.reminder", "suite.approvals.stage.escalated",
    "suite.agent.file.upload", "suite.agent.file.download", "suite.agent.flow.trigger",
    "suite.webhook.create", "suite.webhook.update", "suite.webhook.delete", "suite.webhook.pause",
    "suite.webhook.resume", "suite.webhook.rotate_secret", "suite.webhook.ping", "suite.webhook.redeliver",
    "suite.webhook.disable",
)
