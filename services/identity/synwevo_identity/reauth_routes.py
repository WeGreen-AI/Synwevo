"""Scoped confirmation on the existing central session, by password or Passkey UV."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.http import client_ip
from synwevo_core.sessions import REAUTH_SCOPES, REAUTH_SECONDS

from .passkeys import Passkeys, allowed_origins


def _scope(value) -> str:
    if not isinstance(value, str) or value not in REAUTH_SCOPES:
        raise ServiceError(400, "bad_scope", "请选择有效的身份确认用途")
    return value


def _current(request, sessions):
    row = Passkeys(sessions.store).current_reauth_session(getattr(request.state, "session", None))
    request.state.session = row
    return row


def _result(row, scope):
    return {"ok": True, "scope": scope, "valid_for_s": REAUTH_SECONDS, "amr": row["amr"]}


def confirm_password(request, password, scope, *, ident, sessions, audit):
    """Preserve password policy and failure counting while making success transactional."""
    from .auth_routes import check_reauth_password
    scope = _scope(scope)
    failure = None
    failure_audit_unavailable = False

    def failure_audit(*args, **kwargs):
        nonlocal failure_audit_unavailable
        # A failed audit must not erase a password failure or keep a terminated session alive.
        # Isolate its writes too: an audit implementation may fail after its first INSERT.
        with ident.store.conn() as connection:
            connection.execute("SAVEPOINT reauth_failure_audit")
            try:
                audit(*args, **kwargs)
            except Exception:
                connection.execute("ROLLBACK TO reauth_failure_audit")
                failure_audit_unavailable = True
            finally:
                connection.execute("RELEASE reauth_failure_audit")

    with ident.store.tx():
        session = _current(request, sessions)
        try:
            check_reauth_password(ident, sessions, request, session,
                                  password if isinstance(password, str) else "", failure_audit, scope)
        except ServiceError as exc:
            # Failed attempts, backoff and terminal revocation are security writes too. Commit
            # them before raising the refusal, rather than rolling their protection back.
            failure = exc
        if failure is None:
            updated = sessions.confirm_reauth(session, scope, "password")
            audit(request, "auth.reauth", actor_id=session["user_id"], workspace_given=True,
                  result="ok", meta={"scope": scope, "amr": updated["amr"], "method": "password"})
    if failure is not None:
        if failure_audit_unavailable:
            logging.getLogger(__name__).error("reauth_failure_audit_unavailable")
        raise failure
    request.state.session = updated
    return _result(updated, scope)


def build_router(ident, sessions, passkeys, audit):
    r = APIRouter(prefix="/api/auth/reauth", tags=["auth"])
    store = ident.store

    def origin(request):
        value = (request.headers.get("origin") or str(request.base_url)).rstrip("/")
        if value not in allowed_origins():
            raise ServiceError(403, "bad_origin", "当前页面不能完成身份确认，请从账户服务重新开始")
        return value

    def limit(request, session, phase):
        for key, maximum in ((f"pkreauth:{phase}:session:{session['id']}", 10),
                             (f"pkreauth:{phase}:address:{client_ip(request)}", 30)):
            allowed, wait = ident.window_allows(key, limit=maximum, window_s=60)
            if not allowed:
                raise ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试", {"retry_after": wait})

    def failed(request, actor, scope, phase, exc):
        audit(request, "auth.reauth", actor_id=actor["user_id"], workspace_given=True,
              result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
              meta={"method": "passkey_uv", "phase": phase,
                    "scope": scope if isinstance(scope, str) and scope in REAUTH_SCOPES else None,
                    "code": exc.code if isinstance(exc, ServiceError) else "reauth_failed"})

    @r.get("/options")
    def options(request: Request, scope: str = Query(...)):
        scope = _scope(scope)
        with store.tx():
            session = _current(request, sessions)
            methods = []
            if store.one("SELECT 1 FROM user_passwords WHERE user_id=?", (session["user_id"],)):
                methods.append("password")
            if passkeys.reauth_keys(session["user_id"]):
                methods.append("passkey_uv")
            return {"scope": scope, "methods": methods}

    @r.post("/passkey/begin")
    def begin(request: Request, body: dict = Body(...)):
        session = _current(request, sessions)
        scope = body.get("scope")
        try:
            scope = _scope(scope)
            limit(request, session, "begin")
            return passkeys.begin_reauthentication(session, scope, origin(request))
        except Exception as exc:
            failed(request, session, scope, "begin", exc)
            raise

    @r.post("/passkey/finish")
    def finish(request: Request, body: dict = Body(...)):
        session = _current(request, sessions)
        scope = body.get("scope")
        try:
            scope = _scope(scope)
            limit(request, session, "finish")
            credential = body.get("credential") or {}
            if not isinstance(credential, dict):
                raise ServiceError(400, "bad_response", "安全密钥验证失败，请重新开始身份确认")
            challenge = passkeys.spend_reauthentication(session, scope, credential, origin(request))
            with store.tx():
                verified = passkeys.verify_reauthentication(session, scope, challenge, credential)
                updated = sessions.confirm_reauth(verified["session"], scope, "passkey_uv")
                audit(request, "auth.reauth", actor_id=session["user_id"], object_kind="passkey",
                      object_id=verified["passkey_id"], workspace_given=True, result="ok",
                      meta={"scope": scope, "amr": updated["amr"], "method": "passkey_uv"})
            request.state.session = updated
            return _result(updated, scope)
        except Exception as exc:
            failed(request, session, scope, "finish", exc)
            raise

    return r
