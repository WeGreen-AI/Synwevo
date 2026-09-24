"""应用自己的会话：每个角色都提供的同一份实现。

这四条路由对账户服务、门户和业务应用是**同一件事**：我是谁、我在哪个工作空间、这个进程上的会话
怎么结束、这个部署是怎么配置的。它们原本长在账户服务的登录路由里，于是一个业务应用要回答
"谁登录了"，就得把整套登录仪式（口令、MFA、Passkey、邀请、找回）一起挂上——或者自己再写一遍。
写两遍的那一遍，就是会忘记把工作空间成员资格重新核对一次的那一遍。

所以它们在依赖方适配包里，由三个角色各自挂载**这一份**。登录、注册、改口令、MFA 与 Passkey
仍然只在账户服务上，一处都不多。
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Request, Response

from synwevo_core import env
from synwevo_core.auth import ui_auth_mode
from synwevo_core.http import app_access_refusal, app_role
from synwevo_core.config import platform_name
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, registration_mode
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions


def account_url(role: str) -> str:
    """Where the account and security pages live for this role.

    On a relying party that is the account service; on a single-process install it is this
    app's own page. Given by the server so no client has to assemble it from an issuer string.
    """
    if role in ("accounts", "all", ""):
        return "/account"
    from .rp import issuer
    return issuer().rstrip("/") + "/account"


def public_state(ident: Identity, role: str) -> dict:
    """What a sign-in screen needs before anyone has identified themselves."""
    r = role
    return {"mode": ui_auth_mode(),
            "registration": registration_mode(ident.store),
            "bootstrap_required": ident.bootstrap_needed(),
            # False on a relying party, where `/api/auth/login` is not served at all: the
            # password belongs to the account service and there is exactly one place to type
            # it. A client that showed its own password form here would be collecting a
            # credential it cannot check — and teaching people to type it anywhere.
            "password_login": r in ("accounts", "all", ""),
            # The sign-in screen belongs to the *platform*, not to Synwevo Docs. Hard-coding
            # Synwevo Docs as the name of the platform it is only one application on would have to
            # be undone later in every string, and would tell someone arriving from the portal
            # that they are signing in to the wrong product.
            "platform_name": platform_name(),
            "role": r,
            # Where "back" goes from the account pages. Server-controlled: a return target the
            # page took from a query string would be an open redirect on the one origin that
            # must never have one.
            "portal_url": env.get("PORTAL_URL") or "",
            "account_url": account_url(r)}


def describe_user(user: dict, spaces: list[dict]) -> dict:
    return {"id": user["id"], "email": user["email"], "display_name": user.get("display_name"),
            "status": user["status"], "platform_role": user["platform_role"],
            "locale": user.get("locale") or "zh-Hans", "timezone": user.get("timezone") or "UTC",
            "email_verified": bool(user.get("email_verified_at")),
            "onboarding_completed": bool(user.get("onboarding_completed_at") or spaces),
            "permissions": ["api_platform.manage"] if user["status"] == "active" and user["platform_role"] == "admin" else [],
            "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"],
                            "status": w.get("status", "active"),
                            "organization_id": w.get("organization_id"), "organization_name": w.get("organization_name"),
                            "role": w["role"], "can_export": bool(w["can_export"])} for w in spaces]}


def build_router(ident: Identity, sessions: Sessions, audit) -> APIRouter:
    """The session routes every role serves: /state, /me, /logout, /workspace."""
    r = APIRouter(prefix="/api/auth", tags=["auth"])

    def _me(user: dict, spaces: list[dict]) -> dict:
        return describe_user(user, spaces)

    def _require_session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def _serves_app(request: Request) -> bool:
        """Does this process serve a business application (and so need its access checked)?
        The account service and the portal switch workspaces for a live account alone."""
        return (app_role(request) in ("app", "all")
                and getattr(request.app.state, "route_policy", None) is not None)

    @r.get("/state")
    def state(request: Request):
        """Deliberately says nothing about specific accounts — only how this deployment is
        configured and whether it still needs its first administrator.

        `mode` is the load-bearing field. These routes are mounted whatever SYNWEVO_UI_AUTH
        says, so on a `basic` deployment this endpoint answers 200 like any other — and a client
        that inferred "accounts are in use" from a successful response sent people who had
        already signed in with the shared password to a first-run bootstrap screen. Whether the
        account system is active is the server's answer, not something the client can deduce.
        """
        return public_state(ident, app_role(request))

    @r.post("/logout")
    def logout(request: Request, response: Response):
        s = getattr(request.state, "session", None)
        if s:
            outcome = sessions.revoke_device(s["id"], "logout")
            audit(request, "auth.logout", actor_id=s["user_id"], result="ok", meta=outcome)
        response.delete_cookie(COOKIE_NAME, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @r.patch("/locale")
    def locale(request: Request, body: dict = Body(...)):
        s = _require_session(request)
        value = body.get("locale")
        if value not in ("zh-Hans", "zh-Hant", "en") or set(body) != {"locale"}:
            raise ServiceError(400, "invalid_field", "请选择支持的界面语言", {"field": "locale"})
        ident.store.execute("UPDATE users SET locale=? WHERE id=?", (value, s["user_id"]))
        return {"locale": value}

    @r.get("/me")
    def me(request: Request):
        s = _require_session(request)
        user = ident.get_user(s["user_id"])
        if not user:
            raise ServiceError(401, "unauthorized", "请先登录")
        return {**_me(user, ident.workspaces_for(user["id"])),
                # Which workspace this session is acting in. The page needs it to show the
                # switcher's current value; without it the switcher would guess, and guessing
                # wrong tells someone they are in a tenant they are not.
                "workspace_id": s.get("workspace_id"),
                "session": {"id": s["id"], "amr": s["amr"]}}

    @r.post("/workspace")
    def switch_workspace(request: Request, body: dict = Body(...)):
        """Change the workspace this session is working in.

        Membership is re-checked here rather than trusted from the request, so a stale id or a
        guessed one cannot move the session into a workspace the person has since left.
        """
        s = _require_session(request)
        value = body.get("workspace_id")
        if not isinstance(value, str) or set(body) != {"workspace_id"}:
            raise ServiceError(400, "invalid_field", "请选择有效的工作空间", {"field": "workspace_id"})
        wid = value.strip()
        if not wid or not ident.membership(wid, s["user_id"]) or not ident.get_workspace(wid):
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        workspace = ident.get_workspace(wid)
        if workspace["status"] != "active":
            raise ServiceError(403, "workspace_unavailable", "该工作空间当前不可用")
        from synwevo_core.organizations import workspace_access_failure
        if failure := workspace_access_failure(ident.store, wid, s["user_id"]):
            raise ServiceError(403, failure, "该组织或工作空间当前不可用")
        if _serves_app(request):
            # The same question the gate asks on every Synwevo Docs request. Asking it here too
            # means the switch fails with the reason, instead of succeeding into a workspace
            # where every following request is refused — and a member can no longer move a
            # session into a workspace that never enabled the application, or is frozen.
            err = app_access_refusal(ident.store, ident, s["user_id"], wid,
                                     getattr(request.app.state, "app_id", None))
            if err:
                audit(request, "auth.workspace.switch", actor_id=s["user_id"], workspace_id=wid,
                      result="denied", meta={"reason": err.code})
                raise err
        sessions.set_workspace(s["id"], wid)
        audit(request, "auth.workspace.switch", actor_id=s["user_id"], workspace_id=wid, result="ok")
        return {"ok": True, "workspace_id": wid}

    return r
