"""The platform portal's own API.

Deliberately small. The portal is where someone finds out which applications they have and
reaches their account settings — it is **not** a proxy with access to everything, and it does not
aggregate business data. It never returns a project name, a job, or a file: those belong to the
applications, behind their own object-level authorization, and a home page that listed them
would be a way to read titles you have no right to read.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from synwevo_core.apps import Apps
from synwevo_core.config import platform_name
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations, workspace_access_failure
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, now


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, apps=None) -> APIRouter:
    r = APIRouter(tags=["portal"], prefix="/api/portal")
    if apps is None:
        apps = Apps(store, ident)

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    @r.get("/home")
    def home(request: Request):
        """Everything the portal home page needs, in one request.

        Someone who belongs to no workspace gets a real empty state here — an account, a name,
        an empty application list and a usable account link. Not an error, and emphatically not
        the legacy workspace.
        """
        s = _session(request)
        user = ident.get_user(s["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        spaces = ident.workspaces_for(user["id"])
        return {
            "platform": {"name": platform_name()},
            "user": {"id": user["id"], "email": user["email"],
                     "display_name": user.get("display_name"),
                     "platform_role": user["platform_role"]},
            "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"],
                            "role": w["role"], "status": w["status"],
                            **{key: w[key] for key in ("org_id", "org_name", "organization_id", "organization_name") if key in w}}
                           for w in spaces],
            "organizations": [{key: org[key] for key in ("id", "name", "status", "role")}
                              for org in Organizations(store, ident).list_for_user(user["id"])],
            "current_workspace_id": s.get("workspace_id"),
            # Only applications this person can actually open — in the workspace this session
            # is in, when one is selected. Computed without it, switching workspace changed
            # nothing on the page and a card could name a different workspace than the
            # highlighted one. An entry here is a door that opens; there are no placeholder
            # cards for things that do not exist yet.
            "apps": apps.apps_for(user["id"], s.get("workspace_id")),
            "account_url": _accounts_base(),
            # Read-side only: the portal does not mint/recover invitation tokens or accept
            # invitations. Email verification prevents an unverified address claiming someone
            # else's invitation metadata. Account owns the subsequent acceptance ceremony.
            "pending_invitations": _pending_invitations(store, user),
        }

    @r.post("/workspace")
    def switch_workspace(request: Request, body: dict):
        """Switch the workspace this portal session is acting in.

        Membership is re-checked here rather than trusted from the request: the id arrives from
        the browser, and a workspace someone was removed from must stop working immediately.
        """
        s = _session(request)
        wid = (body or {}).get("workspace_id")
        if wid is not None and (not isinstance(wid, str) or not wid.strip()):
            raise ServiceError(400, "bad_workspace", "请选择有效的工作空间")
        if wid is not None and (not ident.membership(wid, s["user_id"]) or not ident.get_workspace(wid)):
            # A deleted workspace keeps its membership rows until they are cleaned up, so the
            # membership alone would let a session select a workspace that no longer exists.
            raise ServiceError(403, "not_a_member", "你不是该工作空间的成员")
        if wid is not None and ident.get_workspace(wid)["status"] != "active":
            raise ServiceError(403, "workspace_unavailable", "该工作空间当前不可用")
        if wid is not None and (failure := workspace_access_failure(store, wid, s["user_id"])):
            raise ServiceError(403, failure, "该组织或工作空间当前不可用")
        sessions.set_workspace(s["id"], wid)
        audit(request, "portal.workspace.switch", actor_id=s["user_id"], workspace_id=wid,
              result="ok")
        return {"ok": True, "workspace_id": wid}

    return r


def _pending_invitations(store: Store, user: dict) -> list[dict]:
    if not user.get("email_verified_at"):
        return []
    return store.all(
        "SELECT i.id, i.workspace_id, w.name AS workspace_name, i.role, i.expires_at "
        "FROM invitations i JOIN workspaces w ON w.id=i.workspace_id "
        "LEFT JOIN organizations o ON o.id=w.organization_id "
        "WHERE i.email_key=? AND i.accepted_at IS NULL AND i.revoked_at IS NULL "
        "AND i.expires_at>? AND w.deleted_at IS NULL AND w.status='active' "
        "AND (w.organization_id IS NULL OR o.status='active') "
        "ORDER BY i.expires_at, i.id", (user["email_key"], now()))


def _accounts_base() -> str:
    from synwevo_auth_client.rp import issuer
    return issuer()
