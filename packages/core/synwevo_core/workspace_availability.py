"""Whether the work that depends on a workspace may go on, must wait, or has ended (A44).

Every live gate refuses a workspace that is not `active` — that stays the rule for people and
machines asking now. This module answers a different question, for the things that outlive a
request and *write* when they find the workspace closed: a sweep that removes people from a
project's discussion, an approvals sweep that revokes votes, a wait that fails. Those must not turn
a recoverable stop into a permanent loss:

* **available** — the workspace is `active` (and so is its organization, when it has one).
* **paused** — the workspace or its organization is `frozen`, or `deleted` with `recover_until`
  still ahead: an act a platform administrator or an owner can undo. Dependents leave their state
  exactly as it is and stay refused live; when the workspace is available again everything goes on
  as before.
* **lost** — anything else: no such workspace, a deletion past (or without) its recovery window, a
  state no act brings back (the historical workspace before it is claimed). Dependents end as they
  always have.

A person's *own* standing — their account, their workspace membership, their organization
membership — is not asked here; a person who lost it loses what depends on it whether or not the
workspace is paused, and each caller asks that separately.
"""
from __future__ import annotations

from .store import Store, now

AVAILABLE, PAUSED, LOST = "available", "paused", "lost"
_ORDER = (AVAILABLE, PAUSED, LOST)


def _state(row: dict | None, ts: float) -> str:
    """One lifecycle row (a workspace's or an organization's: both carry `status`, `deleted_at`
    and `recover_until`)."""
    if not row:
        return LOST
    if row["status"] == "deleted" or row.get("deleted_at") is not None:
        until = row.get("recover_until")
        return PAUSED if row["status"] == "deleted" and until and ts < until else LOST
    if row["status"] == "active":
        return AVAILABLE
    return PAUSED if row["status"] == "frozen" else LOST


def availability(store: Store, workspace_id, ts: float | None = None) -> str:
    """AVAILABLE, PAUSED or LOST for the workspace now (`ts`, default the current time): the worse
    of the workspace's own state and its organization's."""
    if not isinstance(workspace_id, str) or not workspace_id:
        return LOST
    ts = now() if ts is None else ts
    ws = store.one("SELECT status, deleted_at, recover_until, organization_id FROM workspaces WHERE id=?",
                   (workspace_id,))
    state = _state(ws, ts)
    if state == LOST or not ws["organization_id"]:
        return state
    org = store.one("SELECT status, deleted_at, recover_until FROM organizations WHERE id=?", (ws["organization_id"],))
    return max(state, _state(org, ts), key=_ORDER.index)


def organization_availability(store: Store, organization_id, ts: float | None = None) -> str:
    """AVAILABLE, PAUSED or LOST for the organization itself (no workspace asked): the same rule
    `availability` applies to a workspace's organization. What closes every conversation of an
    organization (`synwevo_core.messaging`, B3/G2): they belong to the organization, not to a
    workspace."""
    if not isinstance(organization_id, str) or not organization_id:
        return LOST
    ts = now() if ts is None else ts
    org = store.one("SELECT status, deleted_at, recover_until FROM organizations WHERE id=?", (organization_id,))
    return _state(org, ts)


def lifecycle_state(row: dict | None, ts: float | None = None) -> str:
    """`_state` for a lifecycle row the caller already read (`status`, `deleted_at`,
    `recover_until`; None = no such row, LOST) — for a listing that reads many at once."""
    return _state(row, now() if ts is None else ts)


def paused(store: Store, workspace_id, ts: float | None = None) -> bool:
    return availability(store, workspace_id, ts) == PAUSED
