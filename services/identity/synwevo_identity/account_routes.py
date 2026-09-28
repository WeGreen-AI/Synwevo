"""Self-service account surface; install with build_router(ident, sessions, mailer, audit).

Store additions are documented in profile.py. The gateway must allow anonymous access ONLY
to /api/auth/deletion/recovery and /api/auth/deletion/recovery/preview. Both authenticate a
single-use recovery capability, never a suspended/deleted user's ordinary session. Mount
AccountRecovery at /account/recovery outside RequireSession. All other routes require an
active user (GET profile also permits pending), and only operate on the session's user ID.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Request, Response
from fastapi.responses import JSONResponse

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import normalise_email, valid_email
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE
from synwevo_core.store import new_id, now

from .auth_routes import check_reauth_password
from .mail import deletion_mail, email_change_mail, security_notice
from .notify import inbox_security
from .profile import (GRACE_SECONDS, canonical_avatar, check_deletion_constraints, digest,
                      invalidate_access, public_profile, validate_preferences)
from .withdrawals import record as journal


def fields(body: dict, allowed: set[str]) -> None:
    if set(body) - allowed or any(not isinstance(value, str) for value in body.values()):
        raise ServiceError(400, "bad_fields", "请求字段无效")
    for key, value in body.items():
        maximum = 349528 if key == "data" else 1024 if key == "password" else 254 if key == "email" else 128
        if len(value) > maximum:
            raise ServiceError(400, "bad_fields", "请求字段过长")


def build_router(ident, sessions, mailer, audit) -> APIRouter:
    r = APIRouter(prefix="/api/auth", tags=["account"])
    store = ident.store

    def current(request, *, allow_pending=False):
        session = getattr(request.state, "session", None)
        user = ident.get_user(session["user_id"]) if session else None
        if not user or user["status"] not in (("active", "pending") if allow_pending else ("active",)):
            raise ServiceError(401, "unauthorized", "请先登录可用账户")
        # Reload under the surrounding transaction to close revocation/check-then-write races.
        live = store.one("SELECT * FROM sessions WHERE id=? AND revoked_at IS NULL "
                         "AND idle_expires_at>? AND absolute_expires_at>?", (session["id"], now(), now()))
        if not live:
            raise ServiceError(401, "unauthorized", "请重新登录")
        return live, user

    def proof(request, body, purpose):
        session, user = current(request)
        check_reauth_password(ident, sessions, request, session, body.get("password", ""), audit, purpose)
        return session, user

    def limit(request, uid, purpose, maximum=4):
        allowed, retry = ident.window_allows(f"account:{purpose}:{uid}", limit=maximum, window_s=3600)
        if not allowed:
            raise ServiceError(429, "too_many_attempts", "操作过于频繁，请稍后再试", {"retry_after": retry})

    def link(path, token, locale):
        base = (env.get("PUBLIC_URL") or "").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.query or parsed.fragment:
            raise ServiceError(503, "public_url_missing", "账户邮件地址尚未配置，请联系管理员")
        language = locale if locale in ("zh-Hans", "zh-Hant", "en") else "zh-Hans"
        return f"{base}{path}?lang={language}#token={token}"

    def taken(key, uid):
        # Include pending/suspended/deleted accounts, whose identities must never be stolen.
        return store.one("SELECT 1 FROM users WHERE email_key=? AND id<>?", (key, uid))

    @r.get("/profile")
    def get_profile(request: Request, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return public_profile(current(request, allow_pending=True)[1])

    @r.patch("/profile")
    def patch_profile(request: Request, body: dict = Body(...)):
        values = validate_preferences(body)
        with store.tx():
            _, user = current(request)
            store.update("users", {"id": user["id"]}, {**values, "updated_at": now()})
            audit(request, "auth.profile.update", actor_id=user["id"], result="ok", meta={"fields": list(values)})
            return public_profile(ident.get_user(user["id"]))

    @r.put("/profile/avatar")
    def put_avatar(request: Request, body: dict = Body(...)):
        fields(body, {"data"})
        current(request)
        png = canonical_avatar(body.get("data", ""))
        with store.tx():
            _, user = current(request)
            store.execute("UPDATE users SET avatar_blob=?,updated_at=? WHERE id=?", (png, now(), user["id"]))
            audit(request, "auth.avatar.update", actor_id=user["id"], result="ok")
        return {"ok": True}

    @r.get("/profile/avatar")
    def get_avatar(request: Request):
        user = current(request)[1]
        if not user.get("avatar_blob"):
            raise ServiceError(404, "no_avatar", "尚未上传头像")
        return Response(bytes(user["avatar_blob"]), media_type="image/png", headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox"})

    @r.delete("/profile/avatar")
    def delete_avatar(request: Request):
        with store.tx():
            _, user = current(request)
            store.execute("UPDATE users SET avatar_blob=NULL,updated_at=? WHERE id=?", (now(), user["id"]))
            audit(request, "auth.avatar.remove", actor_id=user["id"], result="ok")
        return {"ok": True}

    @r.post("/password")
    def change_password(request: Request, body: dict = Body(...)):
        """Change the password from inside the account: current password, new password.

        Distinct from the mailed reset (§3.6): the person is signed in and proves the current
        password here, under the same throttling as any re-authentication. The session doing
        the change survives; every other session and every issued token ends, so a password
        changed because someone else knew it also ends that someone else's access. An account
        without a password (external provider only) sets one through the mailed reset link,
        which proves the mailbox instead.
        """
        from synwevo_core import passwords
        from synwevo_core.oidc import Grants
        fields(body, {"current_password", "new_password"})
        session, user = current(request)
        if not store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (user["id"],)):
            raise ServiceError(409, "no_password", "这个账户还没有口令。请通过邮箱中的重置链接设置口令")
        check_reauth_password(ident, sessions, request, session, body.get("current_password", ""), audit, "account_password")
        replacement = body.get("new_password")
        if not isinstance(replacement, str) or not replacement:
            raise ServiceError(400, "weak_password", "请输入新口令", {"field": "new_password"})
        if replacement == body.get("current_password"):
            raise ServiceError(400, "weak_password", "新口令不能与当前口令相同", {"field": "new_password"})
        ts = now()
        with store.tx():
            session, user = current(request)
            try:
                ident.set_password(user["id"], replacement)
            except passwords.PasswordError as e:
                raise ServiceError(400, "weak_password", str(e), {"field": "new_password", **e.detail}) from None
            ended = sessions.revoke_all_for_user(user["id"], "password_changed", except_session=session["id"])
            tokens = Grants(store).revoke_for_user(user["id"], "password_changed")
            store.execute("UPDATE email_tokens SET used_at=COALESCE(used_at,?) WHERE user_id=? AND kind='reset'", (ts, user["id"]))
            store.execute("DELETE FROM mfa_tickets WHERE user_id=?", (user["id"],))
            inbox_security(request, user, "password_changed")
            subject, text, html = security_notice(what="口令已更改", when_text="刚刚", locale=user["locale"])
            try:
                mailer.enqueue(user["email"], subject, text, html=html, kind="security", ref_id=user["id"])
            except ServiceError:
                # The change stands; the notice is best effort and the audit row is the record.
                pass
            audit(request, "auth.password.changed", actor_id=user["id"], object_kind="user", object_id=user["id"],
                  result="ok", meta={"sessions": ended, "tokens": tokens})
        journaled = journal(store, "password_changed", user_id=user["id"])
        return {"ok": True, "sessions_revoked": ended, "journaled": journaled}

    @r.post("/email-change")
    def email_change(request: Request, body: dict = Body(...)):
        fields(body, {"email", "password"})
        _, user = proof(request, body, "account_email")
        limit(request, user["id"], "email")
        address, key = body.get("email", "").strip(), normalise_email(body.get("email", ""))
        if not valid_email(address) or key == user["email_key"]:
            raise ServiceError(400, "bad_email", "请输入不同的有效邮箱")
        raw, ts = secrets.token_urlsafe(32), now()
        link("/account", raw, user["locale"])  # Refuse an unconfigured origin before minting a mail capability.
        subject, text, html = email_change_mail(token=raw, expires_h=1, locale=user["locale"])
        with store.tx():
            _, user = current(request)
            if taken(key, user["id"]):
                raise ServiceError(409, "email_unavailable", "此邮箱无法使用")
            store.execute("UPDATE email_tokens SET used_at=? WHERE user_id=? AND kind='email_change' AND used_at IS NULL", (ts, user["id"]))
            store.execute("INSERT INTO email_tokens (id,user_id,kind,token_hash,new_email,new_email_key,created_at,expires_at) "
                          "VALUES (?,?,'email_change',?,?,?,?,?)",
                          (new_id("et"), user["id"], digest(raw), address, key, ts, ts + 3600))
            mailer.enqueue(address, subject, text,
                           kind="email_change", ref_id=user["id"])
            audit(request, "auth.email.change_requested", actor_id=user["id"], result="ok")
        return {"ok": True, "mail_status": "queued", "expires_at": ts + 3600}

    @r.post("/email-change/confirm")
    def email_confirm(request: Request, body: dict = Body(...)):
        fields(body, {"token", "password"})
        _, user = proof(request, body, "account_email")
        ts = now()
        with store.tx():
            _, user = current(request)
            token = store.one("SELECT * FROM email_tokens WHERE token_hash=? AND kind='email_change' "
                              "AND user_id=? AND used_at IS NULL AND expires_at>?", (digest(body.get("token", "")), user["id"], ts))
            if not token:
                raise ServiceError(410, "bad_token", "链接无效或已过期，请重新申请")
            if taken(token["new_email_key"], user["id"]):
                raise ServiceError(409, "email_unavailable", "此邮箱无法使用")
            # Queue the security notification BEFORE committing the address change. A disabled
            # transport cannot leave the old inbox silently disconnected from its account.
            inbox_security(request, user, "email_changed")
            subject, text, html = security_notice(what="登录邮箱已更改", when_text="刚刚", locale=user["locale"])
            mailer.enqueue(user["email"], subject, text, html=html,
                           kind="security", ref_id=user["id"])
            store.execute("UPDATE users SET email=?,email_key=?,email_verified_at=?,updated_at=? WHERE id=?",
                          (token["new_email"], token["new_email_key"], ts, ts, user["id"]))
            # No existing address-bound invitation may silently follow an email identity switch.
            store.execute("UPDATE invitations SET revoked_at=? WHERE email_key IN (?,?) "
                          "AND accepted_at IS NULL AND revoked_at IS NULL", (ts, user["email_key"], token["new_email_key"]))
            invalidate_access(store, user["id"], "email_changed")
            audit(request, "auth.email.changed", actor_id=user["id"], result="ok")
        journaled = journal(store, "sessions_revoked", user_id=user["id"])
        return {"ok": True, "sign_in_required": True, "journaled": journaled}

    @r.get("/security-activity")
    def activity(request: Request, response: Response, before: int | None = None):
        _, user = current(request)
        response.headers["Cache-Control"] = "no-store"
        # No audit meta or other actors: governance events may contain other people's data.
        rows = store.all("SELECT seq,at,action,result,ip FROM audit_events WHERE actor_kind='user' "
                         "AND actor_id=? AND action LIKE 'auth.%' AND (? IS NULL OR seq<?) ORDER BY seq DESC LIMIT 51",
                         (user["id"], before, before))
        return {"items": rows[:50], "next_before": rows[49]["seq"] if len(rows) > 50 else None}

    @r.post("/export")
    def export(request: Request, body: dict = Body(...)):
        fields(body, {"password"})
        _, user = proof(request, body, "account_export")
        limit(request, user["id"], "export")
        uid = user["id"]
        result = {"format": "synwevo-account-2", "exported_at": now(), "profile": public_profile(user),
                  "memberships": store.all("SELECT workspace_id,role,can_export,created_at FROM memberships WHERE user_id=?", (uid,)),
                  "passkeys": store.all("SELECT name,rp_id,created_at,last_used_at FROM passkeys WHERE user_id=? AND deleted_at IS NULL", (uid,)),
                  "security_activity": store.all("SELECT at,action,result,ip FROM audit_events WHERE actor_kind='user' "
                                                 "AND actor_id=? AND action LIKE 'auth.%' ORDER BY seq", (uid,)),
                  "avatar_png_base64": base64_avatar(user)}
        if store.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='organization_memberships'"):
            result["organization_memberships"] = store.all(
                "SELECT organization_id,role,created_at FROM organization_memberships WHERE user_id=?", (uid,))
        # Synwevo Message blocks (CI-78): the person's own list only — never who blocked them.
        from synwevo_core.messaging import blocks_for_export, messaging_export
        result["message_blocks"] = blocks_for_export(store, uid)
        # Synwevo Message (CI-81): what this person wrote and where — never another person's words
        # or file bytes — plus their messaging settings and notification preferences.
        result["messaging"] = messaging_export(store, uid)
        try:
            from synwevo_core.notifications import Notifier
            result["notification_preferences"] = Notifier(store).preferences(uid)
        except Exception:  # noqa: BLE001 - a deployment without the inbox tables exports without it
            pass
        audit(request, "auth.data.export", actor_id=uid, result="ok")
        return JSONResponse(result, headers={"Cache-Control": "no-store",
                            "Content-Disposition": 'attachment; filename="synwevo-account.json"'})

    @r.post("/deletion")
    def deletion(request: Request, response: Response, body: dict = Body(...)):
        fields(body, {"password"})
        _, user = proof(request, body, "account_delete")
        raw, ts = secrets.token_urlsafe(32), now()
        url = link("/account/recovery", raw, user["locale"])
        with store.tx():
            _, user = current(request)
            check_deletion_constraints(store, user)
            due = ts + GRACE_SECONDS
            subject, text, html = deletion_mail(user["locale"], url)
            mailer.enqueue(user["email"], subject, text,
                           kind="account_recovery", ref_id=user["id"], html=html)
            store.execute("UPDATE account_recoveries SET used_at=? WHERE user_id=? AND used_at IS NULL", (ts, user["id"]))
            store.execute("INSERT INTO account_recoveries (id,user_id,token_hash,created_at,expires_at) VALUES (?,?,?,?,?)",
                          (new_id("ar"), user["id"], digest(raw), ts, due))
            store.execute("UPDATE users SET status='pending_delete',deletion_requested_at=?,deletion_due_at=?,updated_at=? WHERE id=?",
                          (ts, due, ts, user["id"]))
            store.execute("UPDATE invitations SET revoked_at=? WHERE email_key=? AND accepted_at IS NULL AND revoked_at IS NULL", (ts, user["email_key"]))
            invalidate_access(store, user["id"], "account_pending_delete")
            audit(request, "auth.deletion.requested", actor_id=user["id"], result="ok")
        # A backup predating this request must not make the account usable again. The existing
        # conservative suspension journal entry enforces this without teaching Store about UI.
        journaled = journal(store, "user_suspended", user_id=user["id"])
        response.delete_cookie(COOKIE_NAME, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"ok": True, "status": "pending_delete", "deletion_due_at": due, "journaled": journaled}

    def recoverable(token):
        row = store.one("SELECT r.id,r.user_id,r.expires_at FROM account_recoveries r JOIN users u ON u.id=r.user_id "
                        "WHERE r.token_hash=? AND r.used_at IS NULL AND r.expires_at>? "
                        "AND u.status='pending_delete' AND u.deleted_at IS NULL AND u.deletion_due_at>?", (digest(token), now(), now()))
        if not row:
            raise ServiceError(410, "bad_token", "链接无效或已过期，请联系管理员")
        return row

    @r.get("/deletion/recovery/preview")
    def recovery_preview(response: Response, token: str = ""):
        response.headers["Cache-Control"] = "no-store"
        if not 20 <= len(token) <= 128:
            raise ServiceError(410, "bad_token", "链接无效或已过期，请联系管理员")
        return {"status": "pending_delete", "deletion_due_at": recoverable(token)["expires_at"]}

    @r.post("/deletion/recovery/preview")
    def recovery_preview_body(response: Response, body: dict = Body(...)):
        fields(body, {"token"})
        return recovery_preview(response, body.get("token", ""))

    @r.post("/deletion/recovery")
    def recover(request: Request, body: dict = Body(...)):
        fields(body, {"token"})
        with store.tx():
            row = recoverable(body.get("token", ""))
            store.execute("UPDATE account_recoveries SET used_at=? WHERE id=?", (now(), row["id"]))
            store.execute("UPDATE users SET status='active',deletion_requested_at=NULL,deletion_due_at=NULL,updated_at=? WHERE id=?", (now(), row["user_id"]))
            audit(request, "auth.deletion.cancelled", actor_id=row["user_id"], result="ok")
        return {"ok": True, "sign_in_required": True}

    return r


def base64_avatar(user):
    import base64
    return base64.b64encode(user["avatar_blob"]).decode() if user.get("avatar_blob") else None
