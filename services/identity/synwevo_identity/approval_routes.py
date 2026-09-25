"""Approvals on the account origin: deciding whether a personal Docs project may be copied into an
organization workspace. The request and the copy itself belong to Synwevo Docs; the decision is
organization governance (`org.approvals.decide` plus admin/owner of the target workspace),
re-authenticated like every other sensitive organization write, audited, and answered in the
requester's inbox. The approver sees the frozen manifest — names, sizes, hashes, counts — and
never the personal workspace.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.notifications import Notifier
from synwevo_core.organizations import Organizations
from synwevo_core.project_copies import ProjectCopies
from synwevo_core.sessions import Sessions
from synwevo_core.store import now

REAUTH_SCOPE = "admin"


def build_router(store, ident, sessions, audit, *, notifier: Notifier) -> APIRouter:
    r = APIRouter(prefix="/api/auth/organizations", tags=["organization-governance"])
    orgs = Organizations(store, ident)
    copies = ProjectCopies(store, ident)
    r.copies = copies

    def session(request: Request) -> dict:
        state = getattr(request.state, "session", None)
        if not state:
            raise ServiceError(401, "unauthorized", "请先登录")
        current = store.one("SELECT * FROM sessions WHERE id=?", (state["id"],))
        ts = now()
        if (not current or current["revoked_at"] is not None or current["idle_expires_at"] <= ts or current["absolute_expires_at"] <= ts):
            raise ServiceError(401, "unauthorized", "会话已失效，请重新登录")
        return current

    def reviewer(request: Request, oid: str, *, sensitive: bool = False) -> str:
        s = session(request)
        if sensitive and not Sessions.reauth_fresh(s, REAUTH_SCOPE, strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": REAUTH_SCOPE})
        orgs.require(oid, s["user_id"], permission="org.approvals.decide", active=True)
        return s["user_id"]

    def _view(r: dict, uid: str) -> dict:
        out = copies.public(r)
        out["can_decide"] = r["status"] == "pending" and copies.can_decide(r["target_organization_id"], r["target_workspace_id"], uid)
        ws = store.one("SELECT name FROM workspaces WHERE id=?", (r["target_workspace_id"],)) or {}
        out["target_workspace_name"] = ws.get("name")
        return out

    @r.get("/{oid}/approvals")
    def list_approvals(request: Request, oid: str, status: str = Query("", pattern="^(|pending|approved|rejected|withdrawn|expired|running|done|failed|stale)$"),
                       limit: int = Query(50, ge=1, le=200)):
        uid = reviewer(request, oid)
        copies.sweep()
        return {"items": [_view(x, uid) for x in copies.list_for_organization(oid, status=status or None, limit=limit)]}

    @r.get("/{oid}/approvals/{aid}")
    def get_approval(request: Request, oid: str, aid: str):
        uid = reviewer(request, oid)
        x = copies.get(aid)
        if not x or x["target_organization_id"] != oid:
            raise ServiceError(404, "no_copy_request", "找不到该复制申请")
        return _view(x, uid)

    @r.post("/{oid}/approvals/{aid}/decide")
    def decide(request: Request, oid: str, aid: str, body: dict = Body(...)):
        """`{"action": "approve"|"reject", "reason", "version"?}` — recent `admin` re-authentication."""
        uid = reviewer(request, oid, sensitive=True)
        x = copies.get(aid)
        if not x or x["target_organization_id"] != oid:
            raise ServiceError(404, "no_copy_request", "找不到该复制申请")
        out = copies.decide(aid, uid, str(body.get("action") or ""), reason=str(body.get("reason") or ""), version=body.get("version"))
        audit(request, "organization.copy.decide", actor_id=uid, object_kind="project_copy", object_id=aid, result="ok",
              meta={"organization_id": oid, "action": body.get("action"), "self_approved": out["self_approved"], "target_workspace_id": out["target_workspace_id"]})
        me = ident.get_user(uid) or {}
        m = out.get("manifest") or {}
        try:
            notifier.emit(out["requester_id"], "copy.approved" if out["status"] == "approved" else "copy.rejected",
                          params={"project": (m.get("project") or {}).get("name") or out["name"], "decider": me.get("display_name") or me.get("email") or "—",
                                  "reason": out.get("decision_reason") or ""},
                          organization_id=oid, workspace_id=out["target_workspace_id"], object_kind="project_copy", object_id=aid,
                          dedupe_key=f"copy:{aid}:{out['status']}", target={"origin": "docs", "path": f"/p/{out['source_project_id']}"})
            notifier.set_action_state("project_copy", aid, "done")
        except Exception:  # noqa: BLE001 - the decision stands even if the inbox item does not
            pass
        return _view(out, uid)

    return r
