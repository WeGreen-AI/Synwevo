"""The platform portal's own API.

Deliberately small. The portal is where someone finds out which applications they have and
reaches their account settings — it is **not** a proxy with access to everything, and it does not
aggregate business data. It never returns a project name, a job, or a file: those belong to the
applications, behind their own object-level authorization, and a home page that listed them
would be a way to read titles you have no right to read.
"""
from __future__ import annotations

import os
from fastapi import APIRouter, Request

from synwevo_core.apps import Apps
from synwevo_core.config import platform_name
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store


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
                            "role": w["role"]} for w in spaces],
            "current_workspace_id": s.get("workspace_id"),
            # Only applications this person can actually open. An entry here is a door that
            # opens; there are no placeholder cards for things that do not exist yet.
            "apps": apps.apps_for(user["id"]),
            "account_url": _accounts_base(),
        }

    @r.post("/workspace")
    def switch_workspace(request: Request, body: dict):
        """Switch the workspace this portal session is acting in.

        Membership is re-checked here rather than trusted from the request: the id arrives from
        the browser, and a workspace someone was removed from must stop working immediately.
        """
        s = _session(request)
        wid = (body or {}).get("workspace_id")
        if wid is not None and not ident.membership(wid, s["user_id"]):
            raise ServiceError(403, "not_a_member", "你不是该工作空间的成员")
        sessions.set_workspace(s["id"], wid)
        audit(request, "portal.workspace.switch", actor_id=s["user_id"], workspace_id=wid,
              result="ok")
        return {"ok": True, "workspace_id": wid}

    return r


def _accounts_base() -> str:
    from synwevo_auth_client.rp import issuer
    return issuer()
