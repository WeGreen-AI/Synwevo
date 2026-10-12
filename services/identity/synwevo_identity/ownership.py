"""Governance continuity shared by account deletion and ordinary suspension.

Call inside the account change's Store.tx so competing departures serialize. The returned
state is for an opaque preview digest only; it must not expose memberships in responses.
Frozen/recoverable organizations and frozen spaces still need stewards for their recovery.
This is the existing deletion policy, not an emergency suspension exception.

The second half (A37) is the same rule read the other way round: the workspaces and
organizations that already have no owner who can act — the platform's orphan list — and, for
each, the members the platform could name owner and why it could not (admin_ownership.py).
"""
from __future__ import annotations

from synwevo_core.errors import ServiceError
from synwevo_core.identity import USABLE_OWNER_CONDITION as USABLE_WORKSPACE_OWNER
from synwevo_core.organizations import USABLE_OWNER_CONDITION as USABLE_ORGANIZATION_OWNER
from synwevo_core.platform_access import LIVE_GRANT_EXISTS, SECOND_FACTOR_EXISTS, clock, current_rp_id
from synwevo_core.store import now

#: Why a member cannot be named owner by the platform (A37); the empty list means eligible.
BLOCKER_ACCOUNT = "account_unavailable"
BLOCKER_SECOND_FACTOR = "second_factor_required"
BLOCKER_ORGANIZATION = "not_organization_member"
BLOCKER_PLATFORM_ADMIN = "platform_admin"
ORPHAN_LIMIT_DEFAULT, ORPHAN_LIMIT_MAX = 50, 200
#: Candidates listed per orphan (eligible first, then by address); `candidates_total` says how
#: many members there are in all, so the page can say the list is cut.
CANDIDATE_LIMIT = 100

#: The workspaces the platform may hand to an existing member (A37): undeleted, not the
#: historical data still waiting for its claim (the claim flow's), not attached to an organization
#: deleted past its recovery window, with no owner who can act (the D11 rule, core
#: USABLE_OWNER_CONDITION), and — for a personal workspace — shared with somebody whose account
#: still exists; a solo personal workspace becomes inaccessible with its account (existing
#: policy). Frozen workspaces and frozen or recoverable organizations still need stewards.
#: One positional parameter: the current time, for `recover_until`. The scope (`_WORKSPACE_IN_SCOPE`)
#: is also the recovery route's `not_recoverable` rule (`workspace_recoverable`); whether an owner
#: can act is its own refusal there (`owner_present`), so the two conditions are kept apart.
_WORKSPACE_IN_SCOPE = (
    "w.deleted_at IS NULL AND w.status NOT IN ('pending_claim','deleted') "
    "AND (w.organization_id IS NULL OR EXISTS (SELECT 1 FROM organizations o WHERE o.id=w.organization_id "
    "AND (o.status<>'deleted' OR o.recover_until>?))) "
    "AND (w.kind<>'personal' OR EXISTS (SELECT 1 FROM memberships m2 JOIN users u2 ON u2.id=m2.user_id "
    "WHERE m2.workspace_id=w.id AND m2.role<>'owner' AND u2.deleted_at IS NULL))")
_ORPHAN_WORKSPACES = (
    f"FROM workspaces w WHERE {_WORKSPACE_IN_SCOPE} "
    "AND NOT EXISTS (SELECT 1 FROM memberships m JOIN users u ON u.id=m.user_id "
    f"WHERE m.workspace_id=w.id AND {USABLE_WORKSPACE_OWNER})")
#: The organizations likewise: not deleted past the recovery window (the scope, also the route's
#: `not_recoverable` rule through `organization_recoverable`), no owner who can act.
_ORGANIZATION_IN_SCOPE = "(o.status<>'deleted' OR o.recover_until>?)"
_ORPHAN_ORGANIZATIONS = (
    f"FROM organizations o WHERE {_ORGANIZATION_IN_SCOPE} "
    "AND NOT EXISTS (SELECT 1 FROM organization_memberships m JOIN users u ON u.id=m.user_id "
    f"WHERE m.organization_id=o.id AND {USABLE_ORGANIZATION_OWNER})")


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


# ---------------------------------------------------------------------------- A37: the orphans
def _limit(limit) -> int:
    return max(1, min(int(limit or ORPHAN_LIMIT_DEFAULT), ORPHAN_LIMIT_MAX))


def _candidates_sql(member: str, scope: str, organization_member: str) -> str:
    """One query per orphan: every member who is not an owner row, judged by the same predicates
    the recovery applies inside its transaction — the core write's account and organization
    checks, the product's second-factor rule (platform_access.SECOND_FACTOR_EXISTS, which is
    `step_up()["second_factor"]`) and the route's platform-grant check (LIVE_GRANT_EXISTS, which
    is `live_grants()`). Eligible first, then by address, capped. Positional parameters, in
    order: the current RP ID, the organization id twice (workspace form only), the time, the
    workspace or organization id, the cap."""
    return ("SELECT * FROM (SELECT m.user_id, m.role, u.email, u.email_key, u.display_name, u.status, u.deleted_at, "
            + SECOND_FACTOR_EXISTS.format(u="u.id") + " AS second_factor, "
            + organization_member + " AS organization_member, "
            + LIVE_GRANT_EXISTS.format(u="u.id") + " AS platform_admin "
            f"FROM {member} m JOIN users u ON u.id=m.user_id WHERE m.{scope}=? AND m.role<>'owner') "
            "ORDER BY (status='active' AND deleted_at IS NULL AND second_factor AND organization_member "
            "AND NOT platform_admin) DESC, email_key, user_id LIMIT ?")


_WORKSPACE_CANDIDATES = _candidates_sql(
    "memberships", "workspace_id",
    "(? IS NULL OR EXISTS (SELECT 1 FROM organization_memberships om WHERE om.organization_id=? AND om.user_id=m.user_id))")
_ORGANIZATION_CANDIDATES = _candidates_sql("organization_memberships", "organization_id", "1")


def _candidate(row) -> dict:
    """A member as the platform may or may not name them owner, with every reason it may not, so
    the page explains exactly the refusals the write would give."""
    account = row["status"] == "active" and row["deleted_at"] is None
    second_factor, organization_member = bool(row["second_factor"]), bool(row["organization_member"])
    platform_admin = bool(row["platform_admin"])
    blockers = [code for ok, code in ((account, BLOCKER_ACCOUNT), (second_factor, BLOCKER_SECOND_FACTOR),
                                      (organization_member, BLOCKER_ORGANIZATION), (not platform_admin, BLOCKER_PLATFORM_ADMIN))
                if not ok]
    return {"user_id": row["user_id"], "email": row["email"], "display_name": row["display_name"],
            "role": row["role"], "status": row["status"], "second_factor": second_factor,
            "organization_member": organization_member, "platform_admin": platform_admin,
            "eligible": not blockers, "blockers": blockers}


def candidates(store, *, workspace: dict | None = None, organization_id: str | None = None) -> dict:
    """`{"items", "total"}`: the members of the workspace (or organization) who are not one of
    its owner rows — in an orphan, every owner row is an unusable one — each with their blockers;
    `items` is the first CANDIDATE_LIMIT of them, eligible first, `total` counts them all.
    Metadata only: addresses, names, roles."""
    rp_id, ts = current_rp_id(), clock()
    if workspace is not None:
        oid, wid = workspace.get("organization_id"), workspace["id"]
        rows = store.all(_WORKSPACE_CANDIDATES, (rp_id, oid, oid, ts, wid, CANDIDATE_LIMIT))
        total = store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=? AND role<>'owner'", (wid,))["n"]
    else:
        rows = store.all(_ORGANIZATION_CANDIDATES, (rp_id, ts, organization_id, CANDIDATE_LIMIT))
        total = store.one("SELECT COUNT(*) AS n FROM organization_memberships WHERE organization_id=? AND role<>'owner'",
                          (organization_id,))["n"]
    return {"items": [_candidate(r) for r in rows], "total": total}


def _workspace_orphan(store, w: dict) -> dict:
    """A workspace as the platform's orphan list shows it: counts, the owner rows that cannot act
    (why it is listed), the members who could be named. Never a project name or a document."""
    wid, oid = w["id"], w["organization_id"]
    owners = store.all(
        "SELECT m.user_id, u.email, u.status, u.deleted_at, "
        "(? IS NULL OR EXISTS (SELECT 1 FROM organization_memberships om WHERE om.organization_id=? "
        "AND om.user_id=m.user_id)) AS organization_member "
        "FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? AND m.role='owner' "
        "ORDER BY m.created_at, m.user_id", (oid, oid, wid))
    named = candidates(store, workspace=w)
    return {
        "id": wid, "name": w["name"], "kind": w["kind"], "status": w["status"], "organization_id": oid,
        "created_at": w["created_at"],
        "members": store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (wid,))["n"],
        "projects": store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0", (wid,))["n"],
        "owners": [{"user_id": o["user_id"], "email": o["email"], "status": o["status"],
                    "deleted": o["deleted_at"] is not None, "organization_member": bool(o["organization_member"])}
                   for o in owners],
        "candidates": named["items"], "candidates_total": named["total"],
    }


def _organization_orphan(store, o: dict) -> dict:
    oid = o["id"]
    owners = store.all(
        "SELECT m.user_id, u.email, u.status, u.deleted_at FROM organization_memberships m "
        "JOIN users u ON u.id=m.user_id WHERE m.organization_id=? AND m.role='owner' ORDER BY m.created_at, m.user_id",
        (oid,))
    named = candidates(store, organization_id=oid)
    return {
        "id": oid, "name": o["name"], "status": o["status"], "created_at": o["created_at"],
        "recover_until": o["recover_until"],
        "members": store.one("SELECT COUNT(*) AS n FROM organization_memberships WHERE organization_id=?", (oid,))["n"],
        "workspaces": store.one("SELECT COUNT(*) AS n FROM workspaces WHERE organization_id=? AND deleted_at IS NULL",
                                (oid,))["n"],
        "owners": [{"user_id": w["user_id"], "email": w["email"], "status": w["status"],
                    "deleted": w["deleted_at"] is not None} for w in owners],
        "candidates": named["items"], "candidates_total": named["total"],
    }


def workspace_recoverable(store, wid: str) -> bool:
    """Whether the platform may hand this workspace over at all (`_WORKSPACE_IN_SCOPE`), whatever
    its owners' state: the recovery route's `not_recoverable` rule, read under its write lock."""
    return bool(store.one(f"SELECT 1 FROM workspaces w WHERE w.id=? AND {_WORKSPACE_IN_SCOPE}", (wid, now())))


def organization_recoverable(store, oid: str) -> bool:
    """The organization counterpart (`_ORGANIZATION_IN_SCOPE`): not deleted past its recovery
    window. The route refuses before the write; core `recover_owner` refuses again inside it."""
    return bool(store.one(f"SELECT 1 FROM organizations o WHERE o.id=? AND {_ORGANIZATION_IN_SCOPE}", (oid, now())))


def orphaned_workspaces(store, limit=ORPHAN_LIMIT_DEFAULT) -> dict:
    """`{"total", "items"}`: the workspaces with no usable owner (`_ORPHAN_WORKSPACES`), oldest
    first (created_at, id); `total` counts them all, `items` the first `limit` (1..200)."""
    ts = now()
    total = store.one(f"SELECT COUNT(*) AS n {_ORPHAN_WORKSPACES}", (ts,))["n"]
    rows = store.all(f"SELECT w.* {_ORPHAN_WORKSPACES} ORDER BY w.created_at, w.id LIMIT ?", (ts, _limit(limit)))
    return {"total": total, "items": [_workspace_orphan(store, w) for w in rows]}


def orphaned_organizations(store, limit=ORPHAN_LIMIT_DEFAULT) -> dict:
    """`{"total", "items"}`: the organizations with no usable owner (`_ORPHAN_ORGANIZATIONS`),
    oldest first; `total` counts them all, `items` the first `limit` (1..200)."""
    ts = now()
    total = store.one(f"SELECT COUNT(*) AS n {_ORPHAN_ORGANIZATIONS}", (ts,))["n"]
    rows = store.all(f"SELECT o.id, o.name, o.status, o.created_at, o.recover_until {_ORPHAN_ORGANIZATIONS} "
                     "ORDER BY o.created_at, o.id LIMIT ?", (ts, _limit(limit)))
    return {"total": total, "items": [_organization_orphan(store, o) for o in rows]}
