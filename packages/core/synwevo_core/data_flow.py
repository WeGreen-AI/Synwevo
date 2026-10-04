"""Whether content of one workspace may become part of something another workspace owns.

One contract for every place that moves content across workspaces — the personal Agent writing
after it read another workspace, a Forms definition duplicated elsewhere, a flow step using an
earlier step's result from another workspace (docs/platform/data-flow.md; decision of
2026-10-03 in docs/agent-work/DECISIONS.md). Being able to read both workspaces is not enough:

1. The person is a current member of the source with the right to take content out of it
   (owner, admin or editor, or a membership with ``can_export``; the same meaning as the Docs
   "reading is not exporting" rule).
2. The source workspace's outbound policy allows the target: ``none`` (content stays), ``audience``
   (every current member of the target is already a member of the source, so nobody gains a view
   they did not have), ``organization`` (the target belongs to the same organization) or ``any``.
   Defaults: a personal workspace that only its owner belongs to ``any``; a workspace of an
   organization ``organization``; any other shared workspace, including a personal workspace
   that has been shared, ``audience``. Owners and admins of the workspace change it. A write
   whose recipients are not the target's members (a message, a form request) cannot be shown to
   reach only the source's audience, so ``audience`` refuses it.
3. The source's organization caps it: ``organization`` (the default: content of an organization's
   workspaces stays in that organization) or ``any``. The cap can only narrow a workspace policy.
   Only the organization's owners change it.

The target must be a workspace the person currently belongs to: the answer is the same for a
workspace that does not exist and one the person is not in, so the check tells nothing about
other workspaces. A value this code does not know is treated as the strictest one.

The caller still authorizes the write in the target as for any other write; this module answers
only the transfer question, reads current state on every call and keeps no cache. It imports
nothing from the capability layer, so the account service can set the organization cap without
loading the business applications.
"""
from __future__ import annotations

from .errors import ServiceError
from .organizations import workspace_access_failure
from .store import Store, now

OUTBOUND = ("none", "audience", "organization", "any")
ORGANIZATION_OUTBOUND = ("organization", "any")
EXPORT_ROLES = ("owner", "admin", "editor")

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_transfer_policies (
  workspace_id TEXT PRIMARY KEY,
  outbound TEXT NOT NULL CHECK (outbound IN ('none','audience','organization','any')),
  revision INTEGER NOT NULL CHECK (revision > 0),
  updated_by TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS organization_transfer_policies (
  organization_id TEXT PRIMARY KEY,
  outbound TEXT NOT NULL CHECK (outbound IN ('organization','any')),
  revision INTEGER NOT NULL CHECK (revision > 0),
  updated_by TEXT NOT NULL, updated_at REAL NOT NULL
);
"""

#: Refusal codes, each with the sentence the person reads; the detail never names another
#: workspace's members.
REFUSALS = {
    "transfer_source_unavailable": "来源工作空间已不可用，或你已不是其成员，不能转移其中的内容",
    "transfer_export_denied": "你在来源工作空间没有导出权限，不能把其中的内容用到其他工作空间",
    "transfer_target_unavailable": "目标工作空间不可用，或你不是其成员，不能把内容转移到那里",
    "transfer_source_closed": "来源工作空间的数据流政策不允许其内容转移到其他工作空间",
    "transfer_audience_denied": "目标工作空间有 {outside} 位成员不在来源工作空间，来源的数据流政策只允许转移给已能看到这些内容的成员",
    "transfer_audience_external": "来源工作空间的数据流政策只允许转移给已能看到这些内容的成员；对外发送的接收方无法逐一核对，这些内容不能随之发出",
    "transfer_organization_denied": "来源工作空间的数据流政策只允许转移到同一组织的工作空间",
    "transfer_organization_policy_denied": "来源所属组织不允许其内容转移到组织以外的工作空间",
    "transfer_policy_changed": "数据流政策已被其他人修改，请刷新后重试",
}


def refusal(code: str, **detail) -> ServiceError:
    return ServiceError(403, code, REFUSALS[code].format(**detail), detail or None)


def workspace_outbound(store: Store, workspace_id: str) -> dict:
    """The workspace's outbound policy as it applies now, and whether someone chose it."""
    row = store.one("SELECT w.kind, w.organization_id, p.outbound, p.revision, p.updated_at FROM workspaces w "
                    "LEFT JOIN workspace_transfer_policies p ON p.workspace_id=w.id WHERE w.id=?", (workspace_id,))
    if not row:
        return {"outbound": "none", "explicit": False, "revision": 0, "updated_at": None}
    if row["outbound"] is None:
        if row["kind"] == "personal":
            # Taking content out of a workspace only its owner sees exposes nothing. Once it has
            # been shared, the other members' view counts as in any other shared workspace.
            shared = store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (workspace_id,))["n"] > 1
            default = "audience" if shared else "any"
        else:
            default = "organization" if row["organization_id"] else "audience"
        return {"outbound": default, "explicit": False, "revision": 0, "updated_at": None}
    return {"outbound": row["outbound"], "explicit": True, "revision": row["revision"], "updated_at": row["updated_at"]}


def organization_outbound(store: Store, organization_id: str | None) -> dict:
    if not organization_id:
        return {"outbound": "any", "explicit": False, "revision": 0, "updated_at": None}
    row = store.one("SELECT outbound, revision, updated_at FROM organization_transfer_policies WHERE organization_id=?",
                    (organization_id,))
    if not row:
        return {"outbound": "organization", "explicit": False, "revision": 0, "updated_at": None}
    return {"outbound": row["outbound"], "explicit": True, "revision": row["revision"], "updated_at": row["updated_at"]}


def _members(store: Store, workspace_id: str) -> set[str]:
    return {r["user_id"] for r in store.all("SELECT user_id FROM memberships WHERE workspace_id=?", (workspace_id,))}


def check(store: Store, *, user_id: str, source: str, target: str | None, external: bool = False) -> dict:
    """Raise the refusal that applies, or return the facts the decision rested on.

    ``external`` says the write reaches people who are not necessarily members of the target
    (the operation's side effect is ``external``). The returned basis (policy revisions,
    organization cap, how many target members) is what a confirmation binds to: when any of it
    changes, the person must look again.
    """
    if source == target:
        return {"source": source, "target": target, "same_workspace": True}
    src = store.one("SELECT id, kind, status, organization_id FROM workspaces WHERE id=? AND deleted_at IS NULL", (source,))
    member = store.one("SELECT role, can_export FROM memberships WHERE workspace_id=? AND user_id=?",
                       (source, user_id)) if src else None
    if not src or src["status"] != "active" or not member or workspace_access_failure(store, source, user_id):
        raise refusal("transfer_source_unavailable")
    if member["role"] not in EXPORT_ROLES and not member["can_export"]:
        raise refusal("transfer_export_denied")
    dst = store.one("SELECT id, status, organization_id FROM workspaces WHERE id=? AND deleted_at IS NULL",
                    (target,)) if target else None
    joined = store.one("SELECT 1 FROM memberships WHERE workspace_id=? AND user_id=?", (target, user_id)) if dst else None
    if not dst or dst["status"] != "active" or not joined or workspace_access_failure(store, target, user_id):
        # Decided before any policy fact is read, and identical for a workspace that does not
        # exist: a refusal must not describe a workspace the person is not in.
        raise refusal("transfer_target_unavailable")
    policy = workspace_outbound(store, source)
    cap = organization_outbound(store, src["organization_id"])
    same_org = bool(src["organization_id"]) and src["organization_id"] == dst["organization_id"]
    if policy["outbound"] not in OUTBOUND[1:]:  # ``none``, or a value this code does not know
        raise refusal("transfer_source_closed")
    if cap["outbound"] != "any" and src["organization_id"] and not same_org:
        raise refusal("transfer_organization_policy_denied")
    if policy["outbound"] == "organization" and not same_org:
        raise refusal("transfer_organization_denied")
    audience = _members(store, target)
    if policy["outbound"] == "audience":
        if external:
            raise refusal("transfer_audience_external")
        outside = len(audience - _members(store, source))
        if outside:
            raise refusal("transfer_audience_denied", outside=outside)
    return {"source": source, "target": target, "outbound": policy["outbound"], "revision": policy["revision"],
            "organization_outbound": cap["outbound"], "organization_revision": cap["revision"],
            "same_organization": same_org, "audience": len(audience)}


def set_workspace_outbound(store: Store, workspace_id: str, outbound: str, *, actor_id: str, revision: int) -> dict:
    """Record a workspace's outbound policy. The caller has already checked that the actor
    manages this workspace.

    ``revision`` is the one the actor was looking at (0 while nobody has chosen a policy). The
    write happens only if that is still the current one, so a decision made on a stale page
    cannot silently undo a tightening made in between.
    """
    if outbound not in OUTBOUND:
        raise ServiceError(400, "invalid_field", "请选择有效的数据流政策", {"field": "outbound"})
    if revision == 0:
        changed = store.execute("INSERT INTO workspace_transfer_policies (workspace_id,outbound,revision,updated_by,updated_at) "
                                "VALUES (?,?,1,?,?) ON CONFLICT(workspace_id) DO NOTHING",
                                (workspace_id, outbound, actor_id, now()))
    else:
        changed = store.execute("UPDATE workspace_transfer_policies SET outbound=?, revision=revision+1, updated_by=?, "
                                "updated_at=? WHERE workspace_id=? AND revision=?",
                                (outbound, actor_id, now(), workspace_id, revision))
    if changed != 1:
        raise ServiceError(409, "transfer_policy_changed", REFUSALS["transfer_policy_changed"])
    return workspace_outbound(store, workspace_id)


def set_organization_outbound(store: Store, organization_id: str, outbound: str, *, actor_id: str) -> dict:
    """Record an organization's cap. The caller has already checked that the actor owns it."""
    if outbound not in ORGANIZATION_OUTBOUND:
        raise ServiceError(400, "invalid_field", "请选择有效的组织数据流上限", {"field": "outbound"})
    store.execute("INSERT INTO organization_transfer_policies (organization_id,outbound,revision,updated_by,updated_at) "
                  "VALUES (?,?,1,?,?) ON CONFLICT(organization_id) DO UPDATE SET outbound=excluded.outbound, "
                  "revision=organization_transfer_policies.revision+1, updated_by=excluded.updated_by, "
                  "updated_at=excluded.updated_at", (organization_id, outbound, actor_id, now()))
    return organization_outbound(store, organization_id)
