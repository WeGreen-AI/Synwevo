"""Browser authentication endpoints (`/api/auth/*`).

These are UI endpoints, not part of the `/api/v1` automation contract — nothing here appears in
the published OpenAPI subset, and the standing rule that automation surfaces get no product UI
is untouched: this is the ordinary account surface a person needs to sign in and manage their
own sessions.

Responses are written so that an unauthenticated caller learns nothing about which addresses
have accounts. Login answers with one shape for "no such account", "wrong password" and
"account suspended"; only the audit record distinguishes them.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Body, Request, Response

from ..auth import ui_auth_mode
from ..identity import Identity, registration_mode
from ..passwords import PasswordError
from ..services import ServiceError
from ..sessions import COOKIE_NAME, CSRF_COOKIE, Sessions, cookie_kwargs, new_csrf_token

# Same shape for every failed sign-in attempt, whatever actually went wrong.
_LOGIN_FAILED = ("bad_credentials", "邮箱或口令不正确")


def cookies_secure() -> bool:
    """Whether to mark cookies Secure.

    Not guessed from the request: behind a TLS-terminating proxy the origin only ever sees plain
    HTTP, so inferring it would disable the flag exactly where it matters most. Defaults to on,
    and is turned off deliberately for local development over http://127.0.0.1.
    """
    raw = (os.environ.get("DOCWEAVE_COOKIE_SECURE") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def client_ip(request: Request) -> str:
    """The address used for rate limiting and audit.

    X-Forwarded-For is honoured only when a trusted proxy is configured, because otherwise any
    caller could rotate a header to escape their own rate-limit bucket.
    """
    if (os.environ.get("DOCWEAVE_TRUST_PROXY") or "").strip().lower() in ("1", "true", "yes", "on"):
        fwd = request.headers.get("x-forwarded-for") or ""
        if fwd:
            return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "") or "unknown"


def build_router(ident: Identity, sessions: Sessions, audit) -> APIRouter:
    r = APIRouter(prefix="/api/auth", tags=["auth"])

    def _issue(response: Response, user: dict, *, amr: str, request: Request) -> dict:
        """Start a session and set both cookies. Always called after authentication succeeds,
        never reusing anything the client supplied, so a pre-set session id cannot be promoted."""
        spaces = ident.workspaces_for(user["id"])
        raw, row = sessions.create(
            user["id"], workspace_id=spaces[0]["id"] if spaces else None, amr=amr,
            user_agent=request.headers.get("user-agent"), ip=client_ip(request),
            admin=user["platform_role"] == "admin")
        secure = cookies_secure()
        response.set_cookie(COOKIE_NAME, raw, max_age=None, **cookie_kwargs(secure))
        csrf = new_csrf_token()
        # Readable by script on purpose: the page has to echo it in a header. It is not a
        # credential on its own — it only proves the request came from our own page.
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=secure, samesite="lax", path="/")
        return {"user": _me(user, spaces), "session": {"id": row["id"], "amr": amr}}

    def _me(user: dict, spaces: list[dict]) -> dict:
        return {"id": user["id"], "email": user["email"], "display_name": user.get("display_name"),
                "status": user["status"], "platform_role": user["platform_role"],
                "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"],
                                "role": w["role"], "can_export": bool(w["can_export"])} for w in spaces]}

    # ------------------------------------------------------------------ state
    @r.get("/state")
    def state(request: Request):
        """What the login screen needs before anyone has identified themselves.

        Deliberately says nothing about specific accounts — only how this deployment is
        configured and whether it still needs its first administrator.

        `mode` is the load-bearing field. These routes are mounted whatever DOCWEAVE_UI_AUTH
        says, so on a `basic` deployment this endpoint answers 200 like any other — and a client
        that inferred "accounts are in use" from a successful response sent people who had
        already signed in with the shared password to a first-run bootstrap screen. Whether the
        account system is active is the server's answer, not something the client can deduce.
        """
        return {"mode": ui_auth_mode(),
                "registration": registration_mode(),
                "bootstrap_required": ident.bootstrap_needed(),
                "password_login": True}

    @r.post("/bootstrap")
    def bootstrap(request: Request, response: Response, body: dict = Body(...)):
        """Create the first platform administrator with a token issued by `manage.py bootstrap`.

        The token is the authority here; the request is otherwise unauthenticated. It is
        single-use and expires, so this cannot be left open as a way in.
        """
        token = (body.get("token") or "").strip()
        email, password = (body.get("email") or "").strip(), body.get("password") or ""
        if not token or not email or not password:
            raise ServiceError(400, "missing_fields", "请填写引导令牌、邮箱与口令")
        if not ident.bootstrap_needed():
            raise ServiceError(409, "already_bootstrapped", "本服务已经有平台管理员了")
        try:
            user = ident.create_user(email, password, display_name=body.get("display_name"),
                                     status="active", platform_role="admin")
        except PasswordError as e:
            raise ServiceError(400, "weak_password", str(e), {"field": "password"}) from None
        # Consumed after the account exists so a failed creation does not burn the token.
        if not ident.consume_bootstrap_token(token, user_id=user["id"]):
            ident.store.execute("DELETE FROM users WHERE id=?", (user["id"],))
            ident.store.execute("DELETE FROM memberships WHERE user_id=?", (user["id"],))
            raise ServiceError(403, "bad_token", "引导令牌无效或已过期，请重新生成")
        audit(request, "auth.bootstrap", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok")
        return _issue(response, user, amr="password", request=request)

    # ------------------------------------------------------------------ login / logout
    @r.post("/login")
    def login(request: Request, response: Response, body: dict = Body(...)):
        email, password = (body.get("email") or "").strip(), body.get("password") or ""
        if not email or not password:
            raise ServiceError(400, *_LOGIN_FAILED)
        res = ident.authenticate(email, password, ip=client_ip(request))
        if not res["ok"]:
            # One shape for every failure. The reason is recorded, never returned.
            audit(request, "auth.login", actor_id=(res.get("user") or {}).get("id"),
                  object_kind="user", object_id=(res.get("user") or {}).get("id"),
                  result="denied", meta={"reason": res["reason"]})
            if res["reason"] in ("backoff", "address_blocked"):
                raise ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试",
                                   {"retry_after": int(res.get("retry_after") or 60)})
            raise ServiceError(401, *_LOGIN_FAILED)
        user = res["user"]
        audit(request, "auth.login", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok")
        return _issue(response, user, amr="password", request=request)

    @r.post("/logout")
    def logout(request: Request, response: Response):
        s = getattr(request.state, "session", None)
        if s:
            sessions.revoke(s["id"], "logout")
            audit(request, "auth.logout", actor_id=s["user_id"], result="ok")
        response.delete_cookie(COOKIE_NAME, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True}

    @r.get("/me")
    def me(request: Request):
        s = _require_session(request)
        user = ident.get_user(s["user_id"])
        if not user:
            raise ServiceError(401, "unauthorized", "请先登录")
        return {**_me(user, ident.workspaces_for(user["id"])),
                "session": {"id": s["id"], "amr": s["amr"]}}

    # ------------------------------------------------------------------ sessions
    @r.get("/sessions")
    def list_sessions(request: Request):
        s = _require_session(request)
        return {"items": sessions.list_for_user(s["user_id"], current_id=s["id"])}

    @r.delete("/sessions/{sid}")
    def revoke_session(request: Request, sid: str):
        s = _require_session(request)
        # Scoped to the caller's own sessions: passing someone else's id must not end their day.
        owned = ident.store.one("SELECT user_id FROM sessions WHERE id=?", (sid,))
        if not owned or owned["user_id"] != s["user_id"]:
            raise ServiceError(404, "no_session", "找不到该会话")
        sessions.revoke(sid, "user_revoked")
        audit(request, "auth.session.revoke", actor_id=s["user_id"], object_kind="session",
              object_id=sid, result="ok")
        return {"ok": True, "was_current": sid == s["id"]}

    @r.post("/sessions/revoke-others")
    def revoke_others(request: Request):
        s = _require_session(request)
        n = sessions.revoke_all_for_user(s["user_id"], "user_revoked_others", except_session=s["id"])
        audit(request, "auth.session.revoke_others", actor_id=s["user_id"], result="ok",
              meta={"count": n})
        return {"ok": True, "revoked": n}

    @r.post("/workspace")
    def switch_workspace(request: Request, body: dict = Body(...)):
        """Change the workspace this session is working in.

        Membership is re-checked here rather than trusted from the request, so a stale id or a
        guessed one cannot move the session into a workspace the person has since left.
        """
        s = _require_session(request)
        wid = (body.get("workspace_id") or "").strip()
        if not ident.membership(wid, s["user_id"]):
            raise ServiceError(404, "no_workspace", "找不到该工作空间")
        sessions.set_workspace(s["id"], wid)
        return {"ok": True, "workspace_id": wid}

    def _require_session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    return r
