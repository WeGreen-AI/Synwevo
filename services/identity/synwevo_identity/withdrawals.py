"""Recording a withdrawal of access in the security journal, from the account service.

`synwevo_core.journal` is the file a restore re-applies, so a suspended account or a removed
member does not come back to life when an older database is put back. This wrapper exists for
one decision: **when the journal cannot be written, the withdrawal still stands** (it already
happened in the database, and undoing a security action because a log line failed would be the
wrong way round), but the failure is reported — to the log, and to the caller as `False`, which
each route passes back as `"journaled": false` so nobody is told a withdrawal is durable when it
is not.
"""
from __future__ import annotations

from synwevo_core import journal
from synwevo_core.store import Store


def record(store: Store, kind: str, **fields) -> bool:
    try:
        journal.record(store, kind, **fields)
        return True
    except (OSError, ValueError) as e:
        print(f"[synwevo] security journal write failed for {kind}: {type(e).__name__}: {e}", flush=True)
        return False
