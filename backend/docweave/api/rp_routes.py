"""DocWeave's single sign-on endpoints — the relying-party side, as HTTP.

Three browser routes and one machine route:

* `GET  /auth/start`               begin a sign-in, remembering where to land afterwards
* `GET  /auth/callback`            finish it and mint a DocWeave session cookie
* `POST /auth/sign-out`            end this DocWeave session (optionally the platform one too)
* `POST /auth/backchannel-logout`  the account service telling us a sign-in has ended

The callback is the only place a platform credential is ever handled, and it is handled
server-side. What reaches the browser is DocWeave's own session cookie and nothing else.
"""
from __future__ import annotations

import urllib.parse

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..identity import Identity
from ..rp import Rp, issuer, public_url, safe_next
from ..services import ServiceError
from ..sessions import COOKIE_NAME, CSRF_COOKIE, Sessions, cookie_kwargs, new_csrf_token
from ..store import Store

REQ_COOKIE = "dw_authreq"


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, rp: Rp | None = None,
                 apps=None) -> APIRouter:
    r = APIRouter(tags=["sso"])
    rp = rp if rp is not None else Rp(store)
    if apps is None:
        from ..platform.apps import Apps
        apps = Apps(store, ident)

    def _secure() -> bool:
        import os
        return (os.environ.get("DOCWEAVE_COOKIE_SECURE") or "1") not in ("0", "false", "no")

    @r.get("/auth/start")
    def start(request: Request):
        """Send the browser to the account service, remembering the page it wanted."""
        url, rid = rp.begin(next_path=request.query_params.get("next"),
                            prompt=request.query_params.get("prompt"))
        resp = RedirectResponse(url, status_code=303)
        # Host-only, short-lived, and holding nothing but an opaque id. It is a pointer to the
        # server-side request row, not the request itself.
        resp.set_cookie(REQ_COOKIE, rid, max_age=600, **cookie_kwargs(_secure()))
        return resp

    @r.get("/auth/callback")
    def callback(request: Request):
        q = request.query_params
        if q.get("error"):
            # The provider refused. Show why on our own page rather than following anything
            # the query string suggests.
            reason = urllib.parse.quote((q.get("error_description") or q.get("error"))[:200])
            resp = RedirectResponse(f"/sign-in-failed?reason={reason}", status_code=303)
            resp.delete_cookie(REQ_COOKIE, path="/")
            return resp

        out = rp.complete(request_id=request.cookies.get(REQ_COOKIE),
                          state=q.get("state"), code=q.get("code"))
        user = ident.get_user(out["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(403, "account_unavailable", "账户不可用，请联系管理员")

        # The id_token proves who signed in. It does not decide which workspace they land in —
        # that is re-resolved here from live membership and entitlement.
        allowed, workspace_id, reason = apps.check_access(user["id"], "docweave")
        if not allowed:
            audit(request, "sso.callback", actor_id=user["id"], result="denied",
                  meta={"reason": reason})
            resp = RedirectResponse(f"/no-access?reason={urllib.parse.quote(reason[:200])}",
                                    status_code=303)
            resp.delete_cookie(REQ_COOKIE, path="/")
            return resp

        raw, row = sessions.create(
            user["id"], workspace_id=workspace_id, amr=out["amr"],
            user_agent=request.headers.get("User-Agent"),
            ip=request.client.host if request.client else None,
            oidc_sid=out["sid"], auth_time=out.get("auth_time"))
        audit(request, "sso.callback", actor_id=user["id"], workspace_id=workspace_id,
              result="ok", meta={"amr": out["amr"], "sid": out["sid"]})

        resp = RedirectResponse(safe_next(out["next_path"]), status_code=303)
        resp.set_cookie(COOKIE_NAME, raw, max_age=None, **cookie_kwargs(_secure()))
        resp.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=None,
                        **{**cookie_kwargs(_secure()), "httponly": False})
        resp.delete_cookie(REQ_COOKIE, path="/")
        return resp

    @r.post("/api/auth/sign-out")
    def sign_out(request: Request, everywhere: bool = False):
        """End this DocWeave session. `everywhere` continues to the platform's own logout.

        The two are kept separate on purpose, because they mean different things to the person
        doing it: leaving DocWeave is not the same as signing out of the platform and every
        application on it, and a button that quietly did the second would be lying.
        """
        session = getattr(request.state, "session", None)
        out = {"ok": True, "scope": "app"}
        if session:
            sessions.revoke(session["id"], "user_signed_out")
            audit(request, "sso.sign_out", actor_id=session["user_id"], result="ok",
                  meta={"everywhere": bool(everywhere)})
        if everywhere:
            out["scope"] = "platform"
            out["redirect"] = (f"{issuer()}/oidc/logout?"
                               + urllib.parse.urlencode({"client_id": _client_id(),
                                                         "post_logout_redirect_uri": public_url() + "/signed-out"}))
        resp = JSONResponse(out)
        resp.delete_cookie(COOKIE_NAME, path="/")
        resp.delete_cookie(CSRF_COOKIE, path="/")
        return resp

    @r.post("/auth/backchannel-logout")
    def backchannel_logout(request: Request, logout_token: str = Form(...)):
        """The account service telling us a sign-in has ended.

        No cookies, no CSRF token — the signed logout token *is* the authentication, and it is
        verified before anything is revoked. A non-2xx here makes the provider record a delivery
        failure, so a malformed token must fail loudly rather than be swallowed.
        """
        try:
            out = rp.accept_logout(logout_token)
        except ServiceError as e:
            audit(request, "sso.backchannel_logout", result="denied", meta={"error": e.code})
            return JSONResponse({"error": e.code, "message": e.message}, status_code=400,
                                headers={"Cache-Control": "no-store"})
        audit(request, "sso.backchannel_logout", result="ok", meta=out)
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    def _client_id() -> str:
        from ..rp import client_id
        return client_id()

    return r
