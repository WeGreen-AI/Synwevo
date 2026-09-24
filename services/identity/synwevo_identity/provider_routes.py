"""OIDC provider HTTP endpoints.

Mounted on the account service. The browser-facing half (`/oidc/authorize`, `/oidc/logout`)
relies on the central session cookie; the machine-facing half (`/oidc/token`, `/oidc/userinfo`,
`/oidc/revoke`) never looks at cookies at all — a token endpoint that accepted a session cookie
would be a CSRF-shaped way to mint tokens.

Errors follow OAuth's own shape: parameters the client got wrong come back on the redirect URI
so the client can show something useful, but only *after* the redirect URI itself has been
validated. An unvalidated redirect is never used to report an error — that is how an open
redirect gets built by accident.
"""
from __future__ import annotations

import json
import secrets
import urllib.parse
from fastapi import APIRouter, Body, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
from synwevo_core.store import Store, new_id, now

from synwevo_core import oidc
from synwevo_core.apps import Apps


def build_router(store: Store, ident: Identity, sessions: Sessions, audit, deliver=None) -> APIRouter:
    """`deliver` is the back-channel POST. Injected so tests can observe delivery without a
    second live server, and so a deployment can swap in its own transport later."""
    # `is None`, not `or`: a callable that happens to be falsy (a callable collection, a mock
    # with __len__) would otherwise be silently replaced by the real network deliverer.
    deliver = oidc.deliver_logout if deliver is None else deliver
    r = APIRouter(tags=["oidc"])
    keys = oidc.Keys(store)
    clients = oidc.Clients(store)
    grants = oidc.Grants(store)
    apps = Apps(store, ident)

    # ------------------------------------------------------------------ metadata
    @r.get("/.well-known/openid-configuration")
    def discovery():
        return oidc.discovery()

    @r.get("/oidc/jwks.json")
    def jwks():
        return keys.jwks()

    # ------------------------------------------------------------------ authorize
    @r.get("/oidc/authorize")
    def authorize(request: Request):
        q = request.query_params
        client_id = q.get("client_id") or ""
        redirect_uri = q.get("redirect_uri") or ""
        state = q.get("state")

        client = clients.get(client_id)
        if not client:
            # Nothing validated yet, so nothing may be redirected to.
            raise ServiceError(400, "invalid_client", "未知的客户端")
        if not oidc.Clients.redirect_allowed(client, redirect_uri):
            raise ServiceError(400, "invalid_redirect_uri",
                               "回调地址未登记。这个错误不会被重定向回去——未经校验的地址不能用来报错。")

        # From here the redirect URI is trusted, so protocol errors go back to the client.
        def back(**params):
            params = {k: v for k, v in params.items() if v is not None}
            if state is not None:
                params["state"] = state
            sep = "&" if urllib.parse.urlparse(redirect_uri).query else "?"
            return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}", status_code=303)

        if (q.get("response_type") or "") != "code":
            return back(error="unsupported_response_type",
                        error_description="only the authorization code flow is supported")
        challenge, method = q.get("code_challenge"), (q.get("code_challenge_method") or "").upper()
        if client["require_pkce"] and (not challenge or method != "S256"):
            return back(error="invalid_request", error_description="PKCE with S256 is required")

        scope = q.get("scope") or "openid"
        unknown = [s for s in scope.split() if s not in oidc.SUPPORTED_SCOPES]
        if unknown:
            return back(error="invalid_scope", error_description=f"unsupported scope: {' '.join(unknown)}")
        # Supported by the platform is not the same as granted to this client. Checking only the
        # global list let any public client — the portal's, say — ask a signed-in person for
        # docweave.write and offline_access, minting API credentials outside the CLI-only
        # registration that is supposed to decide who may hold them.
        unregistered = [s for s in scope.split() if s not in oidc.Clients.allowed_scopes(client)]
        if unregistered:
            return back(error="invalid_scope",
                        error_description=f"scope not registered for this client: {' '.join(unregistered)}")

        session = sessions.load(request.cookies.get(COOKIE_NAME))
        if not session:
            # No central session: go and sign in, then come back to this exact request. The
            # return target is built here from our own URL, never from a client parameter.
            nxt = urllib.parse.quote(str(request.url.path) + "?" + str(request.url.query), safe="")
            return RedirectResponse(f"/login?next={nxt}", status_code=303)

        user = ident.get_user(session["user_id"])
        if not user or user["status"] != "active":
            sessions.revoke(session["id"], "user_unavailable")
            return RedirectResponse("/login", status_code=303)

        # Signing in proves identity. Using an application is a separate question.
        entitled, workspace_id, reason = apps.check_access(user["id"], client["app_id"])
        if not entitled:
            audit(request, "oidc.authorize", actor_id=user["id"], object_kind="app",
                  object_id=client["app_id"], result="denied", meta={"reason": reason})
            return back(error="access_denied", error_description=reason)

        code = grants.issue_code(
            client_id=client_id, user_id=user["id"], redirect_uri=redirect_uri, scope=scope,
            nonce=q.get("nonce"), code_challenge=challenge, code_challenge_method=method or None,
            auth_time=session.get("reauth_at") or session["created_at"],
            amr=session.get("amr") or "password",
            central_session_id=session["id"], workspace_id=workspace_id)
        audit(request, "oidc.authorize", actor_id=user["id"], object_kind="app",
              object_id=client["app_id"], result="ok", meta={"client_id": client_id})
        return back(code=code)

    # ------------------------------------------------------------------ token
    def _target(client: dict, resources: list[str] | None) -> tuple[str | None, JSONResponse | None]:
        """RFC 8707 `resource`: which API the access token is for. Returns (audience, error).

        Every requested resource must be one the client was registered for; anything else is
        `invalid_target`. A token names exactly one resource: several distinct values are
        refused rather than folded into one token that several APIs would each accept, which is
        the cross-API replay the audience exists to stop (RFC 9700 §2.3; RFC 8707 §2 lets the
        server refuse a combination it will not issue).
        """
        wanted = sorted({(v or "").strip() for v in (resources or []) if (v or "").strip()})
        if not wanted:
            return None, None
        registered = set(client.get("audiences") or [])
        unknown = [v for v in wanted if v not in registered]
        if unknown:
            return None, JSONResponse({"error": "invalid_target",
                                       "error_description": f"resource not registered for this client: {' '.join(unknown)}"},
                                      status_code=400)
        if len(wanted) > 1:
            return None, JSONResponse({"error": "invalid_target",
                                       "error_description": "one access token is issued for one resource; request them separately"},
                                      status_code=400)
        return wanted[0], None

    @r.post("/oidc/token")
    def token(request: Request,
              grant_type: str = Form(...), code: str = Form(None), redirect_uri: str = Form(None),
              client_id: str = Form(None), client_secret: str = Form(None),
              code_verifier: str = Form(None), refresh_token: str = Form(None),
              scope: str = Form(None), resource: list[str] | None = Form(None)):
        """Deliberately cookie-blind. Credentials come from the request body, never the browser."""
        if grant_type not in ("authorization_code", "refresh_token", "client_credentials"):
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        client = clients.get(client_id or "")
        if not client:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if client["token_endpoint_auth_method"] != "none":
            if not clients.check_secret(client["client_id"], client_secret or ""):
                return JSONResponse({"error": "invalid_client"}, status_code=401)
        audience, bad_target = _target(client, resource)
        if bad_target:
            return bad_target

        if grant_type == "client_credentials":
            return _client_credentials(request, client, scope or "", audience)
        if grant_type == "refresh_token":
            return _refresh(request, client, refresh_token or "", audience)

        try:
            row = grants.consume_code(code or "", client_id=client["client_id"],
                                      redirect_uri=redirect_uri or "", code_verifier=code_verifier)
        except ServiceError as e:
            return JSONResponse({"error": "invalid_grant", "error_description": e.message}, status_code=400)

        user = ident.get_user(row["user_id"])
        if not user or user["status"] != "active":
            return JSONResponse({"error": "invalid_grant", "error_description": "account unavailable"},
                                status_code=400)

        want_refresh = "offline_access" in (row["scope"] or "").split()
        out = grants.issue_tokens(client=client, code_row=row, with_refresh=want_refresh,
                                  audience=audience)

        # One client session per grant, so a platform-wide logout can find it later.
        sid = new_id("cs")
        store.execute("INSERT INTO client_sessions (id,central_session_id,client_id,user_id,sid,created_at) "
                      "VALUES (?,?,?,?,?,?)",
                      (new_id("cl"), row["central_session_id"], client["client_id"], user["id"], sid, now()))
        if "openid" in (row["scope"] or "").split():
            out["id_token"] = grants.id_token(client=client, user=user, code_row=row, sid=sid,
                                              access_token=out["access_token"])
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    def _client_credentials(request: Request, client: dict, requested: str, audience: str | None):
        """A machine identity authenticating as itself.

        Refused for anything that is not a registered service account, and refused for a public
        client — the secret is the whole credential, so a public service account is a published
        one. The granted scope is the intersection of what was asked for and what the
        registration allows, never the union.
        """
        if client.get("kind") != "service":
            return JSONResponse({"error": "unauthorized_client",
                                 "error_description": "只有服务账户可以使用客户端凭据流程"},
                                status_code=400)
        if client["token_endpoint_auth_method"] == "none":
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        allowed = set((client.get("api_scopes") or "").split())
        asked = set(requested.split()) if requested.strip() else set(allowed)
        unknown = asked - set(oidc.API_SCOPES)
        if unknown:
            return JSONResponse({"error": "invalid_scope",
                                 "error_description": f"unsupported scope: {' '.join(sorted(unknown))}"},
                                status_code=400)
        granted = asked & allowed
        if not granted:
            return JSONResponse({"error": "invalid_scope",
                                 "error_description": "该服务账户没有被授予任何可用的接口权限"},
                                status_code=400)
        ws = client.get("workspace_id")
        if not ws or not ident.get_workspace(ws):
            return JSONResponse({"error": "invalid_client",
                                 "error_description": "服务账户绑定的工作空间不存在"}, status_code=400)
        entitled, _, reason = apps.check_access_for_workspace(ws, client["app_id"])
        if not entitled:
            return JSONResponse({"error": "invalid_client", "error_description": reason},
                                status_code=400)
        out = grants.issue_for_service(client=client, scope=" ".join(sorted(granted)), audience=audience)
        audit(request, "oidc.client_credentials", actor_kind="machine",
              actor_id=client["client_id"], workspace_id=ws, object_kind="app",
              object_id=client["app_id"], result="ok",
              meta={"scope": out["scope"], "audience": audience or grants.default_audience(client)})
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    def _refresh(request: Request, client: dict, raw: str, audience: str | None = None):
        """Exchange a refresh token for a new pair — re-checking everything that can change.

        A refresh is not a formality. Between issue and use, the account can be suspended, the
        person can be removed from the workspace, or the workspace can lose the application. The
        whole point of a short access token is that these take effect within one refresh cycle,
        so they are all checked here rather than trusted from the original grant.
        """
        try:
            out, old = grants.rotate_refresh(raw, client=client, audience=audience)
        except ServiceError as e:
            return JSONResponse({"error": "invalid_grant", "error_description": e.message}, status_code=400)

        # A sign-in that was deliberately ended — sign out, "end this device", an administrator
        # revoking sessions — ends what it authorised. Expiry is not revocation: an offline grant
        # is meant to outlive an idle browser, so only an explicit revocation counts here.
        if old.get("central_session_id"):
            cs = store.one("SELECT revoked_at, revoked_reason FROM sessions WHERE id=?",
                           (old["central_session_id"],))
            if cs and cs["revoked_at"]:
                grants.revoke_family(old["family_id"] or old["id"], "session_ended")
                return JSONResponse({"error": "invalid_grant",
                                     "error_description": "the sign-in this grant came from has ended"},
                                    status_code=400)

        user = ident.get_user(old["user_id"])
        if not user or user["status"] != "active":
            grants.revoke_family(old["family_id"] or old["id"], "user_unavailable")
            return JSONResponse({"error": "invalid_grant", "error_description": "account unavailable"},
                                status_code=400)
        entitled, workspace_id, reason = apps.check_access(user["id"], client["app_id"],
                                                           old["workspace_id"])
        if not entitled:
            grants.revoke_family(old["family_id"] or old["id"], "entitlement_revoked")
            audit(request, "oidc.refresh", actor_id=user["id"], object_kind="app",
                  object_id=client["app_id"], result="denied", meta={"reason": reason})
            return JSONResponse({"error": "invalid_grant", "error_description": reason}, status_code=400)

        audit(request, "oidc.refresh", actor_id=user["id"], object_kind="app",
              object_id=client["app_id"], workspace_id=workspace_id, result="ok")
        return JSONResponse(out, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    # ------------------------------------------------------------------ userinfo
    @r.get("/oidc/userinfo")
    def userinfo(request: Request):
        auth = request.headers.get("Authorization") or ""
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "invalid_token"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        tok = grants.load_access(auth.split(None, 1)[1].strip())
        if not tok:
            return JSONResponse({"error": "invalid_token"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        user = ident.get_user(tok["user_id"])
        if not user or user["status"] != "active":
            return JSONResponse({"error": "invalid_token"}, status_code=401)
        scope = (tok["scope"] or "").split()
        out = {"sub": user["id"]}
        if "email" in scope:
            out["email"] = user["email"]
            out["email_verified"] = bool(user.get("email_verified_at"))
        if "profile" in scope:
            out["name"] = user.get("display_name") or user["email"].split("@")[0]
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ revocation
    @r.post("/oidc/revoke")
    def revoke(request: Request, token: str = Form(...), client_id: str = Form(None),
               client_secret: str = Form(None)):
        client = clients.get(client_id or "")
        if not client:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if client["token_endpoint_auth_method"] != "none" and \
                not clients.check_secret(client["client_id"], client_secret or ""):
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        # RFC 7009 §2.1: only a token issued to this client. Revoking anyone's token through any
        # client identity is not what the endpoint promises, even if the holder could.
        grants.revoke_token(token, "client_revoked", client_id=client["client_id"])
        # RFC 7009: an unknown token is still a success. Distinguishing them would turn this
        # endpoint into an oracle for whether a token is valid.
        return JSONResponse({}, status_code=200)

    # ------------------------------------------------------------------ logout
    def _end_central_session(request: Request, session: dict, reason: str) -> dict:
        out = end_central_session(store, session["id"], reason, deliver=deliver)
        sessions.revoke(session["id"], reason)
        audit(request, "oidc.logout", actor_id=session["user_id"], result="ok", meta=out)
        return out

    @r.get("/oidc/logout")
    def end_session(request: Request):
        """RP-initiated logout (OIDC RP-Initiated Logout 1.0).

        `post_logout_redirect_uri` is matched exactly against the requesting client's registered
        list. An unregistered target is not redirected to and not echoed back — the whole value
        of this endpoint to an attacker is as an open redirect on a trusted domain.
        """
        q = request.query_params
        hint, state = q.get("id_token_hint"), q.get("state")
        target = q.get("post_logout_redirect_uri")
        client = clients.get(q.get("client_id") or "")
        if not client and hint:
            client = _client_from_hint(hint)

        # Parameters are validated before anything is ended: a request with an unregistered
        # target used to sign the person out and only then answer 400, so a malformed link was
        # still a working logout link.
        if target and (not client or target not in (client.get("post_logout_redirect_uris") or [])):
            # Deliberately not a redirect and deliberately not echoed into the page.
            raise ServiceError(400, "invalid_redirect_uri", "登出后跳转地址未登记")

        session = sessions.load(request.cookies.get(COOKIE_NAME))
        if session:
            _end_central_session(request, session, "rp_initiated_logout")

        dest = "/logged-out"
        if target:
            dest = target
            if state:
                sep = "&" if urllib.parse.urlparse(dest).query else "?"
                dest = f"{dest}{sep}{urllib.parse.urlencode({'state': state})}"
        resp = RedirectResponse(dest, status_code=303)
        resp.delete_cookie(COOKIE_NAME, path="/")
        resp.delete_cookie(CSRF_COOKIE, path="/")
        return resp

    def _client_from_hint(hint: str) -> dict | None:
        """Read `aud` out of an id_token hint *without* trusting it for anything but lookup.

        The hint may be expired — it names a session that is ending — so it is not verified as a
        live credential. It only selects which client's registered logout targets apply, and the
        target itself is then matched exactly against that list.
        """
        try:
            import base64 as _b64
            payload = hint.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(_b64.urlsafe_b64decode(payload))
        except Exception:
            return None
        aud = claims.get("aud")
        aud = aud if isinstance(aud, str) else (aud or [None])[0]
        return clients.get(aud or "")

    r.end_central_session = _end_central_session      # reused by "sign out everywhere"
    return r


def end_central_session(store: Store, central_session_id: str, reason: str, *, deliver=None) -> dict:
    """End everything a central sign-in authorised: tokens, relying-party sessions, then notify.

    Order matters. Revocation is recorded before any notification goes out, so a client that
    never receives the notification — offline, slow, misconfigured — still fails its next token
    check. The relying parties' own sessions live in the same database (there is one account
    authority and one store), so they are ended here directly by the `sid` each was created
    with: a back-channel POST that fails used to leave the Synwevo Docs session alive for up to
    its idle lifetime after "sign out everywhere", because nothing ever retried it. The POST is
    still sent — it is how a relying party with state of its own finds out promptly — but it is
    no longer the enforcement.

    The central session row itself is revoked by the caller, which knows the reason to record.
    `deliver=None` means the real network deliverer, looked up at call time.
    """
    deliver = oidc.deliver_logout if deliver is None else deliver
    grants, clients = oidc.Grants(store), oidc.Clients(store)
    n_tokens = grants.revoke_for_central_session(central_session_id, reason)
    rows = store.all("SELECT * FROM client_sessions WHERE central_session_id=? "
                     "AND logged_out_at IS NULL", (central_session_id,))
    notified, failed, rp_sessions = 0, 0, 0
    for cs in rows:
        rp_sessions += store.execute(
            "UPDATE sessions SET revoked_at=?, revoked_reason=? WHERE oidc_sid=? AND revoked_at IS NULL",
            (now(), f"central_{reason}"[:60], cs["sid"]))
        store.execute("UPDATE client_sessions SET logged_out_at=? WHERE id=?", (now(), cs["id"]))
        client = clients.get(cs["client_id"])
        if not client or not client.get("backchannel_logout_uri"):
            continue
        tok = grants.logout_token(client=client, user_id=cs["user_id"], sid=cs["sid"])
        ok, err = deliver(client["backchannel_logout_uri"], tok)
        if ok:
            notified += 1
        else:
            failed += 1
            store.execute("UPDATE client_sessions SET logout_error=? WHERE id=?", (err, cs["id"]))
    return {"tokens_revoked": n_tokens, "clients_notified": notified, "clients_failed": failed,
            "app_sessions_ended": rp_sessions}
