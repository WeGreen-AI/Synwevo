"""Platform-controlled ownership recovery (A37): the workspaces and organizations nobody can
administer any more, and the handover that names one of their existing members owner.

D11 (`ownership.check_ownership_continuity`) keeps the last usable owner from being suspended or
deleted. Orphans still arise — data from before that check, an owner who lost organization
membership along a path older than the strand check, operator states in the database — and
until now the only way out was an operator editing rows. The platform's answer is held to the
claim route's rules (admin_routes.claim), deliberately narrow:

* it names an **existing member** of that workspace or organization — it creates no account,
  no membership and no entitlement, and grants nothing anywhere else;
* the successor must be able to act as an owner: a live account, a current organization
  membership for an attached workspace, and a second factor for the owner-only step-up;
* not the administrator (`self_assignment`), and not anyone holding a platform grant
  (`target_is_platform_admin`): platform administration and business data stay in different
  hands, as for the historical data;
* a written reason, the administrator step-up, one transaction, one audit record — and the
  invariant that nothing is left to recover (`owner_present`) is checked inside that
  transaction, under the writer lock, not against the list the administrator looked at.

The list is metadata: names, addresses, roles and counts. Never a project name or a document.
"""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.audit_change import change
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.platform_access import PlatformAccess, audit_denied, require_platform
from synwevo_core.store import Store, now

from .ownership import (CANDIDATE_LIMIT, ORPHAN_LIMIT_DEFAULT, ORPHAN_LIMIT_MAX, organization_recoverable,
                        orphaned_organizations, orphaned_workspaces, workspace_recoverable)

REASON_MIN, REASON_MAX = 8, 500


def workspace_row(store: Store, w: dict) -> dict:
    """A workspace as administration sees it (admin_routes `_workspace_row`): counts, never
    contents. Listing project *names* here would hand every document title to anyone who reaches
    the page — the leak the whole administration surface exists to prevent."""
    wid = w["id"]
    return {
        "id": wid, "name": w["name"], "kind": w["kind"], "status": w["status"], "created_at": w["created_at"],
        "members": store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id=?", (wid,))["n"],
        "projects": store.one("SELECT COUNT(*) AS n FROM projects WHERE tenant=? AND deleted=0", (wid,))["n"],
        "apps": [row["app_id"] for row in store.all("SELECT app_id FROM workspace_apps WHERE workspace_id=?", (wid,))],
    }


def build_router(store: Store, ident: Identity, audit) -> APIRouter:
    """Registered under the parent admin router, which supplies /api/admin, and included before
    the parent declares its own routes: `/organizations/{oid}/recover-owner` is therefore matched
    ahead of the parent's generic `/organizations/{oid}/{action}` (test_admin_ownership_recovery
    proves it)."""
    r = APIRouter(tags=["admin"])

    def _admin(request: Request, permission: str, *, dangerous: bool = False) -> dict:
        """admin_routes `_admin`: the platform permission, the session step-up and, for the
        dangerous operations, the strict re-authentication (platform_access.require_platform)."""
        return require_platform(request, permission, store=store, audit=audit, dangerous=dangerous)

    @contextmanager
    def _write(request: Request, permission: str, *, object_kind: str, object_id: str):
        """admin_routes `_write`: revalidate under the writer lock; retain denials after rollback."""
        admitted = _admin(request, permission, dangerous=True)
        try:
            with store.tx():
                yield _admin(request, permission, dangerous=True)
        except ServiceError as exc:
            if exc.status < 500:
                audit_denied(store, audit, request, admitted["id"], object_id or permission,
                             object_kind=object_kind, code=exc.code)
            raise

    def _handover(body: dict) -> tuple[str, str]:
        """The member named and the reason — required, because in a year the audit line is the
        only thing that will explain why these files became someone's."""
        target_id = body.get("user_id")
        if not isinstance(target_id, str) or not target_id.strip():
            raise ServiceError(400, "missing_user", "请选择要成为所有者的成员")
        reason = body.get("reason")
        if not isinstance(reason, str) or not REASON_MIN <= len(reason.strip()) <= REASON_MAX:
            raise ServiceError(400, "missing_reason", f"请写明理由（{REASON_MIN} 至 {REASON_MAX} 个字），它会写入审计记录")
        return target_id.strip(), reason.strip()

    def _not_self_or_platform(actor: dict, target_id: str) -> None:
        """A platform role is never the way to a document: not for the administrator acting, and
        not for anyone else holding a platform grant. Checked with the write (as the claim does)."""
        if target_id == actor["id"]:
            raise ServiceError(409, "self_assignment",
                               "不能把所有权指派给自己：平台管理权限不带来任何文档的访问。请指派给该空间的普通成员")
        if PlatformAccess(store).live_grants(target_id):
            raise ServiceError(409, "target_is_platform_admin",
                               "该账户持有平台权限，不能成为所有者：平台管理与业务数据须由不同账户持有。请指派给没有平台权限的成员")

    def _recoverable(ws: dict) -> None:
        """The workspaces the platform may hand over at all: not the historical data still
        waiting for its claim (the claim flow's), not deleted, not in an organization deleted
        past its recovery window, not a solo personal workspace (ownership.workspace_recoverable)."""
        if not workspace_recoverable(store, ws["id"]):
            raise ServiceError(409, "not_recoverable",
                               "该工作空间不在平台恢复的范围内：待认领的历史数据走认领流程，已删除的空间和无人共享的个人空间不恢复")

    # ------------------------------------------------------------------ the list
    @r.get("/ownership/orphans")
    def orphans(request: Request, limit: int = Query(ORPHAN_LIMIT_DEFAULT, ge=1, le=ORPHAN_LIMIT_MAX)):
        """The workspaces and organizations with no owner who can act, each with the owner rows
        that cannot (why it is listed) and the members who could be named, with their blockers.
        `candidates` holds at most 100 members (ownership.CANDIDATE_LIMIT), the eligible ones
        first and then by address; `candidates_total` is how many members there are in all, so
        the page can say the list is cut ("共 N 位成员，这里列出前 100 位"). `limit` bounds each
        kind's `items`, never its `total`. Both listing permissions, because the page shows both
        kinds (every built-in role that holds one holds the other)."""
        assert CANDIDATE_LIMIT == 100, "the docstring above states the cap"
        _admin(request, "platform.workspaces.view")
        _admin(request, "platform.orgs.view")
        return {"as_of": now(), "limit": limit,
                "workspaces": orphaned_workspaces(store, limit),
                "organizations": orphaned_organizations(store, limit)}

    # ------------------------------------------------------------------ workspaces
    @r.post("/workspaces/{wid}/recover-owner")
    def recover_workspace_owner(request: Request, wid: str, body: dict = Body(...)):
        """`{"user_id", "reason"}`: name an existing member owner of a workspace that has none.

        The write is core `Identity.recover_ownership` — the same UPDATE as an owner's own
        transfer (role owner, can_export 1) — which holds the invariants inside the transaction:
        `owner_present`, `target_unavailable`, `not_organization_member`,
        `successor_second_factor_required`. Refused here, with the write: `self_assignment` and
        `target_is_platform_admin`. The audit record is part of the transaction.
        """
        _admin(request, "platform.workspaces.manage", dangerous=True)
        target_id, reason = _handover(body)
        ws = ident.get_workspace(wid)
        if not ws:
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        _recoverable(ws)
        with _write(request, "platform.workspaces.manage", object_kind="workspace", object_id=wid) as actor:
            # Re-read under the writer lock: a deletion or a claim that committed while this
            # request waited is judged by the rows as they are now.
            ws = ident.get_workspace(wid)
            if not ws:
                raise ServiceError(404, "no_workspace", "找不到该工作空间")
            _recoverable(ws)
            _not_self_or_platform(actor, target_id)
            out = ident.recover_ownership(wid, target_id)
            target = ident.get_user(target_id)
            audit(request, "admin.workspace.recover_owner", actor_id=actor["id"], workspace_id=wid,
                  object_kind="workspace", object_id=wid, result="ok",
                  meta={**change({"role": out["previous_role"]}, {"role": "owner"}),
                        "target_user": target_id, "target_email": target["email"],
                        "previous_role": out["previous_role"], "unusable_owners": out["unusable_owners"],
                        "reason": reason[:REASON_MAX]})
        return {"ok": True, "workspace": workspace_row(store, ident.get_workspace(wid)),
                "member": {"user_id": target_id, "email": target["email"], "role": "owner",
                           "previous_role": out["previous_role"]}}

    # ------------------------------------------------------------------ organizations
    @r.post("/organizations/{oid}/recover-owner")
    def recover_organization_owner(request: Request, oid: str, body: dict = Body(...)):
        """`{"user_id", "reason"}`: name an existing member owner of an organization that has none.

        Core `Organizations.recover_owner` holds the invariants and writes the organization's own
        audit record (`organization.owner.recover`, as an owner's transfer records itself) inside
        the transaction; this route adds the platform's record. Platform governance does not add
        an organization membership, impersonate its owner, or bypass its checks.
        """
        _admin(request, "platform.orgs.manage", dangerous=True)
        target_id, reason = _handover(body)
        if not store.one("SELECT 1 FROM organizations WHERE id=?", (oid,)):
            raise ServiceError(404, "no_organization", "找不到该组织")
        # Out of scope before the write, as the workspace route refuses; core refuses again inside.
        if not organization_recoverable(store, oid):
            raise ServiceError(409, "not_recoverable", "该组织已删除且恢复期限已过，不在平台恢复的范围内")
        with _write(request, "platform.orgs.manage", object_kind="organization", object_id=oid) as actor:
            _not_self_or_platform(actor, target_id)
            # workspace_given: a platform action on an organization belongs to no workspace — not
            # to the administrator's own session workspace.
            out = Organizations(store, ident, lambda event, **kw: audit(request, event, workspace_given=True, **kw)
                                ).recover_owner(oid, target_id, actor_id=actor["id"], reason=reason)
            target = ident.get_user(target_id)
            audit(request, "admin.organization.recover_owner", actor_id=actor["id"], workspace_given=True,
                  object_kind="organization", object_id=oid, result="ok",
                  meta={**change({"role": out["previous_role"]}, {"role": "owner"}),
                        "target_user": target_id, "target_email": target["email"],
                        "previous_role": out["previous_role"], "unusable_owners": out["unusable_owners"],
                        "reason": reason[:REASON_MAX]})
        org = store.one("SELECT id, name, status FROM organizations WHERE id=?", (oid,))
        return {"ok": True, "organization": {"id": org["id"], "name": org["name"], "status": org["status"]},
                "member": {"user_id": target_id, "email": target["email"], "role": "owner",
                           "previous_role": out["previous_role"]}}

    return r
