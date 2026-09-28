"""Public registration and resumable onboarding, using the existing identity and mail outbox."""
from __future__ import annotations

import hashlib

from fastapi import APIRouter, Body, Request

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity, normalise_email, registration_mode, valid_email
from synwevo_core.passwords import PasswordError
from synwevo_core.sessions import Sessions
from synwevo_core.store import now
from synwevo_auth_client.session_routes import describe_user

from .auth_routes import _challenge, client_ip, _too_many
from .mail import account_exists_mail, mode, verify_mail

LOCALES = frozenset(("zh-Hans", "zh-Hant", "en"))


def build_router(ident: Identity, sessions: Sessions, invites, mailer, audit) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["account"])

    def _delivery_state():
        # Same response for known and unknown addresses. This acknowledges the request, not delivery.
        return {"status": "verification_pending", "delivery": "unavailable" if mode() == "disabled" else "requested"}

    def _queue_verification(user: dict, request: Request) -> None:
        with ident.store.tx():
            recent = ident.store.one("SELECT COUNT(*) AS n,MAX(created_at) AS last FROM email_tokens "
                                     "WHERE user_id=? AND kind='verify' AND created_at>?", (user["id"], now() - 3600))
            if recent["n"] >= 3 or (recent["last"] and now() - recent["last"] < 60):
                return
            token = invites.issue_email_token(user["id"], "verify", ttl_s=24 * 3600,
                                               new_email=user["email"])
            subject, body, html = verify_mail(token=token, expires_h=24, locale=user.get("locale") or "zh-Hans")
            mailer.enqueue(user["email"], subject, body, kind="verify", ref_id=user["id"], html=html)
            audit(request, "auth.email.verify_requested", actor_id=user["id"], result="ok")

    def _notify_existing(user: dict, request: Request) -> None:
        """The address already has an active account: tell the mailbox, once an hour at most.

        The HTTP answer stays the same as for a fresh registration, so this mail is the only
        thing that distinguishes the two cases — and it goes to the mailbox owner alone.
        """
        with ident.store.tx():
            recent = ident.store.one("SELECT COUNT(*) AS n FROM mail_outbox WHERE kind='account_exists' AND ref_id=? "
                                     "AND created_at>?", (user["id"], now() - 3600))
            if recent["n"]:
                return
            subject, body, html = account_exists_mail(locale=user.get("locale") or "zh-Hans")
            mailer.enqueue(user["email"], subject, body, kind="account_exists", ref_id=user["id"], html=html)
            audit(request, "auth.registration.existing_account", actor_id=user["id"], result="ok")

    def _limited(request: Request, email: str):
        allowed, wait = ident.window_allows(f"registration:{client_ip(request)}", limit=10, window_s=900)
        if not allowed:
            raise _too_many(wait)
        # Counts even nonexistent addresses, without storing their raw address in a limiter name.
        key = hashlib.sha256(normalise_email(email).encode()).hexdigest()
        allowed, _ = ident.window_allows(f"registration-email:{key}", limit=3, window_s=3600)
        return allowed

    @router.post("/register", status_code=202)
    def register(request: Request, body: dict = Body(...)):
        if registration_mode(ident.store) != "open":
            raise ServiceError(403, "registration_closed", "当前不开放公开注册，请使用有效邀请")
        allowed_fields = {"email", "password", "display_name", "locale", "intent", "invite_token", "challenge_token"}
        if set(body) - allowed_fields:
            raise ServiceError(400, "invalid_field", "注册请求包含不支持的字段")
        email, password = body.get("email"), body.get("password")
        if not isinstance(email, str) or not valid_email(email):
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        if not isinstance(password, str) or len(password) > 1024:
            raise ServiceError(400, "weak_password", "请输入有效口令", {"field": "password"})
        locale = body.get("locale", "zh-Hans")
        intent = body.get("intent", "personal")
        name = body.get("display_name", "")
        if not isinstance(locale, str) or locale not in LOCALES or intent not in ("personal", "organization", "invitation"):
            raise ServiceError(400, "invalid_field", "语言或首次使用方式无效")
        if not isinstance(name, str) or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ServiceError(400, "invalid_field", "显示名称不得超过 100 个字符", {"field": "display_name"})
        # Human verification first (when the deployment has it): a registration is an account
        # row plus a mail, and the address windows alone do not stop a bot with many addresses.
        # Checked after the cheap field validation so malformed requests cost no siteverify call,
        # and before the windows and the Argon2 hash so a tokenless bot burns nothing.
        _challenge(request).require(request, body, "register", audit=audit)
        if not _limited(request, email):
            return _delivery_state()
        # create_user always checks policy and hashes before checking uniqueness. No timing fast
        # path for an existing email, and neither caller role nor first-registration grants admin.
        try:
            with ident.store.tx():
                user = ident.create_user(email, password, display_name=name.strip(), status="pending",
                                         platform_role="none", with_personal_workspace=False)
                ident.store.execute("UPDATE users SET locale=?,onboarding_intent=? WHERE id=?",
                                    (locale, intent, user["id"]))
                user = ident.get_user(user["id"])
                audit(request, "auth.registration", actor_id=user["id"], result="ok")
        except PasswordError as exc:
            raise ServiceError(400, "weak_password", str(exc), {"field": "password", **exc.detail}) from None
        except ServiceError as exc:
            if exc.code != "email_taken":
                raise
            # Never replace an existing password/name/locale. A pending account can be resumed
            # only through the mailbox and the ordinary recovery flow.
            user = ident.by_email(email)
        if user and user["status"] == "pending":
            try:
                _queue_verification(user, request)
            except ServiceError as exc:
                audit(request, "auth.email.verify_requested", actor_id=user["id"], result="error",
                      meta={"mail_error": exc.code})
        elif user and user["status"] == "active":
            try:
                _notify_existing(user, request)
            except ServiceError as exc:
                audit(request, "auth.registration.existing_account", actor_id=user["id"], result="error",
                      meta={"mail_error": exc.code})
        return _delivery_state()

    @router.post("/email/resend", status_code=202)
    def resend(request: Request, body: dict = Body(...)):
        email = body.get("email", "")
        if not isinstance(email, str) or len(email) > 254:
            raise ServiceError(400, "bad_email", "邮箱格式不正确", {"field": "email"})
        _challenge(request).require(request, body, "resend", audit=audit)
        if not _limited(request, email):
            return _delivery_state()
        user = ident.by_email(email)
        if user and user["status"] == "pending":
            try:
                _queue_verification(user, request)
            except ServiceError as exc:
                audit(request, "auth.email.verify_requested", actor_id=user["id"], result="error",
                      meta={"mail_error": exc.code})
        return _delivery_state()

    def _active(request: Request):
        session = getattr(request.state, "session", None)
        user = ident.get_user(session["user_id"]) if session else None
        if not user or user["status"] != "active":
            raise ServiceError(403, "email_verification_required", "请先登录并验证邮箱")
        return session, user

    @router.get("/onboarding")
    def onboarding(request: Request):
        _, user = _active(request)
        rows = ident.store.all("SELECT i.id,i.workspace_id,i.role,i.expires_at,w.name AS workspace_name "
                               "FROM invitations i JOIN workspaces w ON w.id=i.workspace_id "
                               "WHERE i.email_key=? AND i.accepted_at IS NULL AND i.revoked_at IS NULL "
                               "AND i.expires_at>? AND w.status='active' AND w.deleted_at IS NULL "
                               "ORDER BY i.created_at DESC LIMIT 100", (user["email_key"], now()))
        return {"completed": bool(user.get("onboarding_completed_at") or ident.workspaces_for(user["id"])),
                "intent": user.get("onboarding_intent"), "workspaces": ident.workspaces_for(user["id"]),
                "pending_invitations": rows}

    @router.post("/onboarding/personal")
    def personal(request: Request):
        session, user = _active(request)
        workspace = ident.ensure_personal_workspace(user["id"])
        sessions.set_workspace(session["id"], workspace["id"])
        audit(request, "auth.onboarding.personal", actor_id=user["id"], workspace_id=workspace["id"], result="ok")
        return {"workspace": workspace, "user": describe_user(ident.get_user(user["id"]), ident.workspaces_for(user["id"]), session)}

    return router
