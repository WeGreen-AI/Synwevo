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
from ..mfa import TICKET_MAX_ATTEMPTS
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


def build_router(ident: Identity, sessions: Sessions, audit, mfa=None, tickets=None) -> APIRouter:
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
        if mfa and mfa.enabled_for(user["id"]):
            # The password is not enough on its own. A ticket carries the fact that it was
            # correct; it is not a session and cannot become one without the second factor.
            ticket = tickets.issue(user["id"], user_agent=request.headers.get("user-agent"),
                                   ip=client_ip(request))
            f = mfa.factors(user["id"])
            audit(request, "auth.login", actor_id=user["id"], object_kind="user",
                  object_id=user["id"], result="ok", meta={"stage": "password", "mfa": True})
            return {"mfa_required": True, "ticket": ticket,
                    "methods": {"totp": f["totp"], "recovery": f["recovery_remaining"] > 0}}
        audit(request, "auth.login", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok")
        return _issue(response, user, amr="password", request=request)

    @r.post("/login/mfa")
    def login_mfa(request: Request, response: Response, body: dict = Body(...)):
        """Second half of a sign-in. Requires the ticket from /login plus one factor."""
        raw = (body.get("ticket") or "").strip()
        code = (body.get("code") or "").strip()
        recovery = (body.get("recovery_code") or "").strip()
        t = tickets.load(raw)
        if not t:
            # Expired, spent, or out of attempts — all indistinguishable, all mean start again.
            raise ServiceError(401, "ticket_invalid", "验证已超时或失败次数过多，请重新登录")
        user = ident.get_user(t["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "ticket_invalid", "验证已超时或失败次数过多，请重新登录")

        used_recovery = False
        if code and mfa.verify_totp(user["id"], code):
            amr = "password+totp"
        elif recovery and mfa.use_recovery_code(user["id"], recovery):
            amr, used_recovery = "password+recovery", True
        else:
            left = TICKET_MAX_ATTEMPTS - tickets.count_failure(raw)
            audit(request, "auth.login.mfa", actor_id=user["id"], object_kind="user",
                  object_id=user["id"], result="denied", meta={"remaining": max(0, left)})
            raise ServiceError(401, "bad_code", "验证码不正确" + (f"，还可尝试 {left} 次" if left > 0 else "，请重新登录"))

        if not tickets.redeem(raw):
            raise ServiceError(401, "ticket_invalid", "验证已超时，请重新登录")
        audit(request, "auth.login.mfa", actor_id=user["id"], object_kind="user",
              object_id=user["id"], result="ok", meta={"amr": amr})
        out = _issue(response, user, amr=amr, request=request)
        if used_recovery:
            # Says it plainly: a spent recovery code is one fewer way back in, and the moment
            # someone notices is when they are already in the account and can act on it.
            out["recovery_used"] = True
            out["recovery_remaining"] = mfa.recovery_remaining(user["id"])
        return out

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


def build_invite_router(ident: Identity, sessions: Sessions, invites, mailer, audit) -> APIRouter:
    """Invitation and email-token endpoints.

    Split from the auth router only because it needs the invite and mail services; it is mounted
    on the same `/api/auth` prefix and the anonymous set in app.py lists exactly which of these
    may be reached without a session.
    """
    from ..invites import INVITE_TTL_S, ROLE_LABEL
    from .. import mail as mail_mod

    r = APIRouter(prefix="/api/auth", tags=["auth"])

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    # ------------------------------------------------------------------ issuing
    @r.post("/workspaces/{wid}/invitations")
    def invite(request: Request, wid: str, body: dict = Body(...)):
        s = _session(request)
        email, role = (body.get("email") or "").strip(), (body.get("role") or "viewer").strip()
        row, raw = invites.create(workspace_id=wid, email=email, role=role, actor_id=s["user_id"],
                                  can_export=body.get("can_export"))
        ws = ident.get_workspace(wid)
        me = ident.get_user(s["user_id"])
        subject, text = mail_mod.invitation_mail(
            workspace=ws["name"], inviter=me.get("display_name") or me["email"],
            role_label=ROLE_LABEL.get(role, role), token=raw, expires_h=INVITE_TTL_S // 3600)
        try:
            mailer.enqueue(email, subject, text, kind="invite", ref_id=row["id"])
        except ServiceError:
            # Mail is not configured or not usable. The invitation row would be unreachable, so
            # it is withdrawn rather than left as a link nobody will ever receive.
            invites.revoke(row["id"], s["user_id"])
            raise
        audit(request, "invite.create", actor_id=s["user_id"], workspace_id=wid,
              object_kind="invitation", object_id=row["id"], result="ok", meta={"role": role})
        return {"id": row["id"], "email": row["email_key"], "role": role,
                "expires_at": row["expires_at"], "mail": "queued"}

    @r.get("/workspaces/{wid}/invitations")
    def list_invites(request: Request, wid: str):
        s = _session(request)
        items = invites.list_for_workspace(wid, s["user_id"])
        for it in items:
            st = mailer.status_for(it["id"])
            it["mail"] = (st or {}).get("status") or "unknown"
            it["mail_error"] = (st or {}).get("last_error")
        return {"items": items}

    @r.delete("/invitations/{iid}")
    def revoke_invite(request: Request, iid: str):
        s = _session(request)
        invites.revoke(iid, s["user_id"])
        audit(request, "invite.revoke", actor_id=s["user_id"], object_kind="invitation",
              object_id=iid, result="ok")
        return {"ok": True}

    # ------------------------------------------------------------------ accepting
    @r.get("/invitations/preview")
    def preview_invite(request: Request, token: str):
        """Read-only. Opening the emailed link lands here and changes nothing, so a mail scanner
        following the URL cannot accept on the recipient's behalf."""
        out = invites.preview(token)
        s = getattr(request.state, "session", None)
        if s:
            u = ident.get_user(s["user_id"])
            out["signed_in_as"] = (u or {}).get("email")
        return out

    @r.post("/invitations/accept")
    def accept_invite(request: Request, response: Response, body: dict = Body(...)):
        token = (body.get("token") or "").strip()
        s = getattr(request.state, "session", None)
        out = invites.accept(token, password=body.get("password") or None,
                             display_name=body.get("display_name"),
                             as_user_id=s["user_id"] if s else None)
        audit(request, "invite.accept", actor_id=out["user"]["id"], workspace_id=out["workspace_id"],
              object_kind="membership", object_id=out["user"]["id"], result="ok",
              meta={"role": out["role"]})
        if s:
            sessions.set_workspace(s["id"], out["workspace_id"])
            return {"user": _public_user(ident, out["user"]), "workspace_id": out["workspace_id"]}
        return _issue_session(ident, sessions, response, out["user"], amr="password", request=request)

    return r


def _public_user(ident: Identity, user: dict) -> dict:
    spaces = ident.workspaces_for(user["id"])
    return {"id": user["id"], "email": user["email"], "display_name": user.get("display_name"),
            "status": user["status"], "platform_role": user["platform_role"],
            "workspaces": [{"id": w["id"], "name": w["name"], "kind": w["kind"],
                            "role": w["role"], "can_export": bool(w["can_export"])} for w in spaces]}


def _issue_session(ident: Identity, sessions: Sessions, response: Response, user: dict, *,
                   amr: str, request: Request) -> dict:
    spaces = ident.workspaces_for(user["id"])
    raw, row = sessions.create(user["id"], workspace_id=spaces[0]["id"] if spaces else None, amr=amr,
                               user_agent=request.headers.get("user-agent"), ip=client_ip(request),
                               admin=user["platform_role"] == "admin")
    secure = cookies_secure()
    response.set_cookie(COOKIE_NAME, raw, max_age=None, **cookie_kwargs(secure))
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), httponly=False, secure=secure,
                        samesite="lax", path="/")
    return {"user": _public_user(ident, user), "session": {"id": row["id"], "amr": amr}}


def build_mfa_router(ident: Identity, sessions: Sessions, mfa, audit) -> APIRouter:
    """Second-factor management for one's own account.

    Every change here is gated on a *recent* re-authentication recorded server-side, scoped to
    this purpose. Proving yourself to add a factor must not also unlock removing one, and a
    front-end flag would unlock nothing at all.
    """
    from ..passwords import verify as verify_password
    from ..sessions import REAUTH_SECONDS

    r = APIRouter(prefix="/api/auth/mfa", tags=["auth"])
    SCOPE = "mfa"

    def _session(request: Request) -> dict:
        s = getattr(request.state, "session", None)
        if not s:
            raise ServiceError(401, "unauthorized", "请先登录")
        return s

    def _fresh(request: Request) -> dict:
        s = _session(request)
        if not Sessions.reauth_fresh(s, SCOPE):
            raise ServiceError(403, "reauth_required",
                               "这是敏感操作，请重新输入口令确认", {"scope": SCOPE})
        return s

    @r.post("/reauth")
    def reauth(request: Request, body: dict = Body(...)):
        """Re-prove the password, for this purpose only and for a few minutes."""
        s = _session(request)
        user = ident.get_user(s["user_id"])
        row = ident.store.one("SELECT phc FROM user_passwords WHERE user_id=?", (s["user_id"],))
        if not user or not row or not verify_password(row["phc"], body.get("password") or ""):
            audit(request, "auth.reauth", actor_id=s["user_id"], result="denied")
            raise ServiceError(401, "bad_credentials", "口令不正确")
        sessions.mark_reauth(s["id"], SCOPE)
        audit(request, "auth.reauth", actor_id=s["user_id"], result="ok", meta={"scope": SCOPE})
        return {"ok": True, "valid_for_s": REAUTH_SECONDS}

    @r.get("")
    def state(request: Request):
        s = _session(request)
        f = mfa.factors(s["user_id"])
        return {**f, "reauth_fresh": Sessions.reauth_fresh(s, SCOPE)}

    # ------------------------------------------------------------------ TOTP
    @r.post("/totp/begin")
    def totp_begin(request: Request):
        s = _fresh(request)
        user = ident.get_user(s["user_id"])
        out = mfa.begin_totp(s["user_id"], account_label=user["email"])
        audit(request, "mfa.totp.begin", actor_id=s["user_id"], result="ok")
        return out

    @r.post("/totp/confirm")
    def totp_confirm(request: Request, body: dict = Body(...)):
        s = _fresh(request)
        mfa.confirm_totp(s["user_id"], body.get("code") or "")
        audit(request, "mfa.totp.enable", actor_id=s["user_id"], result="ok")
        # Recovery codes are issued with the first factor, not left for later: the moment MFA
        # starts mattering is the moment losing the phone starts mattering.
        codes = mfa.generate_recovery_codes(s["user_id"])
        return {"ok": True, "recovery_codes": codes}

    @r.post("/totp/disable")
    def totp_disable(request: Request, body: dict = Body(...)):
        s = _fresh(request)
        # Removing a factor is guarded at least as strongly as adding one, and it also takes a
        # current code: someone who has your unlocked laptop should not be able to strip MFA
        # without the thing MFA is made of.
        if not mfa.verify_totp(s["user_id"], body.get("code") or ""):
            raise ServiceError(400, "bad_code", "请输入当前的动态验证码以确认关闭", {"field": "code"})
        mfa.disable_totp(s["user_id"])
        audit(request, "mfa.totp.disable", actor_id=s["user_id"], result="ok")
        return {"ok": True, **mfa.factors(s["user_id"])}

    # ------------------------------------------------------------------ recovery codes
    @r.post("/recovery/regenerate")
    def recovery_regen(request: Request):
        s = _fresh(request)
        if not mfa.enabled_for(s["user_id"]):
            raise ServiceError(409, "no_mfa", "还没有启用任何第二因素，恢复码暂时没有用处")
        codes = mfa.generate_recovery_codes(s["user_id"])
        audit(request, "mfa.recovery.regenerate", actor_id=s["user_id"], result="ok",
              meta={"count": len(codes)})
        return {"recovery_codes": codes}

    return r
