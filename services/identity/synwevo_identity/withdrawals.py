"""Recording a withdrawal of access in the security journal, from the account service.

`synwevo_core.journal` is the file a restore re-applies, so a suspended account or a removed
member does not come back to life when an older database is put back. This wrapper exists for
one decision: **when the journal cannot be written, the withdrawal still stands** (undoing a
security action because a log line failed would be the wrong way round), but the failure is
reported — to the log, and to the caller as `False`, which each route passes back as
`"journaled": false` so nobody is told a withdrawal is durable when it is not.

The line is written ahead, **inside the write transaction of the change it describes**
(`journal._intent(..., strict=False)`): a fresh `attempt_id` and a receipt in `meta` that commits
with the change, or not at all. A backup taken between the line and its COMMIT holds neither and
is given the withdrawal on restore; a process that stops after the COMMIT has written its line
already; a transaction that fails after the line voids it once its rollback is proven, so no
restore applies a withdrawal that never happened. The receipt goes in before the append, so a
failed append leaves a correct receipt and the change commits without its line (`False`).

Called outside a write transaction it raises RuntimeError: the caller opens `store.tx()` around
its change and records inside it, after every refusal.
"""
from __future__ import annotations

from synwevo_core import journal
from synwevo_core.store import Store


def record(store: Store, kind: str, **fields) -> bool:
    return journal._intent(store, kind, strict=False, **fields)
