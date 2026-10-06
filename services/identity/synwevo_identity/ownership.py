"""Governance continuity shared by account deletion and ordinary suspension.

Call inside the account change's Store.tx so competing departures serialize. The returned
state is for an opaque preview digest only; it must not expose memberships in responses.
Frozen/recoverable organizations and frozen spaces still need stewards for their recovery.
This is the existing deletion policy, not an emergency suspension exception.
"""
from __future__ import annotations

from synwevo_core.errors import ServiceError
from synwevo_core.store import now


def _require_successor(role, eligible, *, organization=False):
    if (role == "owner" and "owner" not in eligible) or not eligible.intersection({"owner", "admin"}):
        if organization:
            raise ServiceError(409, "organization_ownership_required", "请先移交组织的所有权和管理权限")
        raise ServiceError(409, "ownership_required", "请先移交共享工作空间的所有权和管理权限")


def check_ownership_continuity(store, uid: str) -> dict:
    """Require another active, undeleted owner (or manager for a departing admin).

Memberships have no suspended state: the row's presence, role and live account determine
eligibility. An attached workspace also requires current organization membership, just as
its authorization does. A solo personal space may become inaccessible with its account.
"""
    ts = now()
    state = {"organizations": [], "workspaces": []}
    has_organizations = bool(store.one(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='organizations'"))
    if has_organizations:
        for member in store.all(
                "SELECT o.id,o.status,o.deleted_at,o.recover_until,m.role,m.updated_at AS membership_updated_at "
                "FROM organization_memberships m JOIN organizations o ON o.id=m.organization_id "
                "WHERE m.user_id=? AND m.role IN ('owner','admin') "
                "AND (o.status<>'deleted' OR o.recover_until>?) ORDER BY o.id", (uid, ts)):
            others = store.all(
                "SELECT m.user_id,m.role,m.updated_at AS membership_updated_at,u.status,u.deleted_at "
                "FROM organization_memberships m JOIN users u ON u.id=m.user_id "
                "WHERE m.organization_id=? AND m.user_id<>? AND m.role IN ('owner','admin') ORDER BY m.user_id",
                (member["id"], uid))
            eligible = {row["role"] for row in others if row["status"] == "active" and row["deleted_at"] is None}
            _require_successor(member["role"], eligible, organization=True)
            state["organizations"].append({**member, "successors": others})

    for member in store.all(
            "SELECT w.id,w.kind,w.status,w.organization_id,m.role,m.updated_at AS membership_updated_at "
            "FROM memberships m JOIN workspaces w ON w.id=m.workspace_id "
            "WHERE m.user_id=? AND m.role IN ('owner','admin') AND w.deleted_at IS NULL ORDER BY w.id", (uid,)):
        oid = member["organization_id"]
        org = (store.one("SELECT id,status,deleted_at,recover_until FROM organizations WHERE id=?", (oid,))
               if has_organizations and oid else None)
        if org and org["status"] == "deleted" and (org["recover_until"] is None or org["recover_until"] <= ts):
            continue
        # Only the membership's presence matters here, not its organization role. An org
        # owner is not automatically a workspace owner (and vice versa).
        org_member = ("EXISTS (SELECT 1 FROM organization_memberships om "
                      "WHERE om.organization_id=? AND om.user_id=m.user_id)") if oid and has_organizations else "1"
        args = (oid, member["id"], uid) if oid and has_organizations else (member["id"], uid)
        others = store.all(
            "SELECT m.user_id,m.role,m.updated_at AS membership_updated_at,u.status,u.deleted_at,"
            f"{org_member} AS organization_member FROM memberships m JOIN users u ON u.id=m.user_id "
            "WHERE m.workspace_id=? AND m.user_id<>? ORDER BY m.user_id", args)
        if member["kind"] == "personal" and not any(row["deleted_at"] is None for row in others):
            continue
        eligible = {row["role"] for row in others if row["status"] == "active" and row["deleted_at"] is None
                    and row["organization_member"]}
        _require_successor(member["role"], eligible)
        state["workspaces"].append({**member, "organization": org, "successors": others})
    return state
