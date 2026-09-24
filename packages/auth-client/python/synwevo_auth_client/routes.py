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

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions, cookie_kwargs, new_csrf_token
from synwevo_core.store import Store

from .rp import Rp, app_id, issuer, public_url, safe_next

REQ_COOKIE = "dw_authreq"


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, rp: Rp | None = None,
                 apps=None) -> APIRouter:
    r = APIRouter(tags=["sso"])
    rp = rp if rp is not None else Rp(store)
    if apps is None:
        from synwevo_core.apps import Apps
        apps = Apps(store, ident)

    def _secure() -> bool:
        import os
        return (env.get("COOKIE_SECURE") or "1") not in ("0", "false", "no")

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
        # that is re-resolved here from live membership and entitlement. The application asked
        # about is this service's own, not one named in the token.
        allowed, workspace_id, reason = apps.check_access(user["id"], app_id())
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
        from .rp import client_id
        return client_id()

    return r


# Account paths that exist only on the account service. On a relying party they are not pages,
# they are redirects.
ACCOUNT_PATHS = ("/login", "/account", "/invite", "/forgot-password", "/reset-password",
                 "/logged-out")


def build_account_redirect_router() -> APIRouter:
    """Send the account paths to the account service instead of rendering a dead page.

    Before the split these paths were routes inside the application's own bundle, and on a
    relying party they rendered a sign-in form that could never succeed — `/api/auth/login` is
    not even served there. The client-side code already knew to bounce; doing it on the server
    means a bookmark, a link written before the platform existed, or a redirect after signing
    out all land somewhere that works, rather than on the application's "page not found".

    `/login` goes through `/auth/start` rather than straight to the account service, because
    only `/auth/start` mints the request row that makes the round trip verifiable — sending the
    browser to the account service by hand would be a sign-in with no state to check on return.
    """
    r = APIRouter(tags=["sso"], include_in_schema=False)

    @r.get("/login")
    def login(request: Request):
        nxt = safe_next(request.query_params.get("next")) or "/"
        return RedirectResponse(f"/auth/start?next={urllib.parse.quote(nxt, safe='/?=&')}",
                                status_code=303)

    @r.get("/account")
    def account():
        return RedirectResponse(issuer().rstrip("/") + "/account", status_code=303)

    @r.get("/invite")
    def invite(request: Request):
        # The token is what the account service needs; it is carried, not interpreted here.
        q = ("?" + str(request.url.query)) if request.url.query else ""
        return RedirectResponse(issuer().rstrip("/") + "/invite" + q, status_code=303)

    @r.get("/forgot-password")
    def forgot():
        return RedirectResponse(issuer().rstrip("/") + "/forgot-password", status_code=303)

    @r.get("/reset-password")
    def reset(request: Request):
        q = ("?" + str(request.url.query)) if request.url.query else ""
        return RedirectResponse(issuer().rstrip("/") + "/reset-password" + q, status_code=303)

    @r.get("/logged-out")
    def logged_out():
        return RedirectResponse(issuer().rstrip("/") + "/logged-out", status_code=303)

    return r
