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

import base64
import binascii
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
from synwevo_core.apps import PLATFORM_APP_ID as PLATFORM_APP_ID_
from synwevo_core.oauth_consent import Consents, needed as consent_needed
from synwevo_core.machine import directory_workspace_live
from synwevo_core.cimd import ClientMetadataDocuments, is_client_id_url, withdrawn_by_policy
from synwevo_core.mcp_policy import McpPolicy


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
    consents = Consents(store)

    # ------------------------------------------------------------------ metadata
    def _metadata():
        cimd = McpPolicy(store).current().client_registration == "cimd_and_preregistered"
        return oidc.discovery(client_id_metadata_documents=cimd)

    @r.get("/.well-known/openid-configuration")
    def discovery():
        return _metadata()

    @r.get("/.well-known/oauth-authorization-server")
    def oauth_metadata():
        """RFC 8414: the same facts as OpenID Connect Discovery, at the address OAuth-only clients
        (MCP clients among them) try first."""
        return _metadata()

    @r.get("/oidc/jwks.json")
    def jwks():
        return keys.jwks()

    # ------------------------------------------------------------------ authorize
    def _sign_in_first(request: Request, locale: str | None) -> RedirectResponse:
        """No central session: go and sign in, then come back to this exact request. The return
        target is built here from our own URL, never from a client parameter."""
        nxt = urllib.parse.quote(str(request.url.path) + "?" + str(request.url.query), safe="")
        language = "&" + urllib.parse.urlencode({"lang": locale}) if locale is not None else ""
        return RedirectResponse(f"/login?next={nxt}{language}", status_code=303)

    def _signed_in_person(request: Request) -> str | None:
        """The active person behind this browser request, if any — without extending the session."""
        session = sessions.load(request.cookies.get(COOKIE_NAME), touch=False)
        user = ident.get_user(session["user_id"]) if session else None
        return user["id"] if user and user["status"] == "active" else None

    @r.get("/oidc/authorize")
    def authorize(request: Request):
        q = request.query_params
        client_id = q.get("client_id") or ""
        redirect_uri = q.get("redirect_uri") or ""
        state = q.get("state")

        client = clients.get(client_id)
        if (client is None and is_client_id_url(client_id)) or (client is not None and client.get("registration") == "cimd"):
            # An MCP client with no prior relationship: its client id is the https address of its
            # metadata document (draft-ietf-oauth-client-id-metadata-document). Fetched, checked and
            # kept as a public-client registration — only while the platform MCP policy allows it.
            # A stored registration comes through here too, every time: the policy is asked again
            # (a withdrawn mechanism or domain ends it) and a day-old document is fetched again (a
            # callback it dropped stops working). Fetching, and the row it writes, wait for a
            # signed-in person: an anonymous request is sent to sign in with nothing fetched.
            person = _signed_in_person(request)
            client, fetched = ClientMetadataDocuments(store).resolve(client_id, may_fetch=person is not None)
            if client is None:
                locale = q.get("ui_locales")
                valid = locale in ("zh-Hans", "zh-Hant", "en") and len(q.getlist("ui_locales")) == 1
                return _sign_in_first(request, locale if valid else None)
            if fetched:
                audit(request, "oidc.client_metadata_document", actor_kind="system", actor_id="oidc",
                      object_kind="oauth_client", object_id=client_id, result="ok",
                      meta={"name": client["name"], "redirect_uris": client["redirect_uris"], "requested_by": person})
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
            # RFC 9207: the response names its issuer, so a client that talks to several
            # authorization servers can tell a mixed-up or injected response from this one.
            params["iss"] = oidc.issuer()
            sep = "&" if urllib.parse.urlparse(redirect_uri).query else "?"
            return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode(params)}", status_code=303)

        if (q.get("response_type") or "") != "code":
            return back(error="unsupported_response_type",
                        error_description="only the authorization code flow is supported")
        requested_workspace = q.get("workspace_id")
        if requested_workspace is not None and (not requested_workspace or len(requested_workspace) > 200
                or len(q.getlist("workspace_id")) != 1
                or any(c.isspace() or ord(c) < 32 for c in requested_workspace)):
            return back(error="invalid_request", error_description="请选择一个有效的工作空间")
        locale = q.get("ui_locales")
        if locale is not None and (locale not in ("zh-Hans", "zh-Hant", "en") or len(q.getlist("ui_locales")) != 1):
            return back(error="invalid_request", error_description="请选择支持的界面语言")
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
        if unregistered and oidc.mcp_resource() in (client.get("audiences") or []):
            # An MCP client chooses its scopes from the MCP server's protected resource metadata
            # (MCP authorization, "Scope Selection Strategy"), which names what the server
            # understands, not what this client was registered for. Refusing would leave a
            # standard client unable to authorize at all; granting would widen the registration.
            # So the request is narrowed to the registration (RFC 6749 §3.3: the server may
            # ignore part of the requested scope; the token response names what was granted),
            # and refused only when no business scope is left.
            scope = " ".join(s for s in scope.split() if s not in unregistered)
            if not consent_needed(scope) or not any(oidc.api_scope_app(s) for s in scope.split()):
                return back(error="invalid_scope",
                            error_description=f"scope not registered for this client: {' '.join(unregistered)}")
            unregistered = []
        if unregistered:
            return back(error="invalid_scope",
                        error_description=f"scope not registered for this client: {' '.join(unregistered)}")

        session = sessions.load(request.cookies.get(COOKIE_NAME))
        if not session:
            return _sign_in_first(request, locale)

        user = ident.get_user(session["user_id"])
        if not user or user["status"] != "active":
            sessions.revoke(session["id"], "user_unavailable")
            return RedirectResponse("/login", status_code=303)

        # Signing in proves identity. Using an application is a separate question.
        entitled, workspace_id, reason = apps.check_access(user["id"], client["app_id"], requested_workspace)
        if not entitled:
            audit(request, "oidc.authorize", actor_id=user["id"], object_kind="app",
                  object_id=client["app_id"], result="denied", meta={"reason": reason})
            return back(error="access_denied", error_description=reason)
        choose_workspace = False
        if requested_workspace is None and consent_needed(scope) and client["app_id"] == PLATFORM_APP_ID_:
            # A delegated token acts in one workspace, and a client that follows only the OAuth
            # protocol (an MCP client) has no way to name it. The one workspace this person already
            # consented to for this client decides; otherwise the person chooses on the consent page
            # rather than silently getting whichever workspace happens to be listed first.
            consented = [c["workspace_id"] for c in consents.list_for_user(user["id"])
                         if c["client_id"] == client_id and c["workspace_id"]]
            spaces = {w["id"] for w in ident.workspaces_for(user["id"]) if w["status"] == "active"}
            live = [w for w in consented if w in spaces]
            if len(live) == 1 and consents.covers(user["id"], client_id, live[0], scope):
                workspace_id = live[0]
            elif len(spaces) <= 1:
                workspace_id = next(iter(spaces), None)
            else:
                workspace_id, choose_workspace = None, True

        # Signing in is not consenting: a request for API scopes or offline access needs this
        # person's stored consent for this client and workspace (API-4). Without it the request is
        # parked here — every parameter already validated — and the person decides on a page of
        # this origin. Nothing the client sends later can change what they are shown or approve.
        if choose_workspace or not consents.covers(user["id"], client_id, workspace_id, scope):
            rid = consents.park(user_id=user["id"], central_session_id=session["id"], client_id=client_id,
                                redirect_uri=redirect_uri, state=state, scope=scope, nonce=q.get("nonce"),
                                code_challenge=challenge, code_challenge_method=method or None,
                                workspace_id=workspace_id, auth_time=session.get("reauth_at") or session["created_at"],
                                amr=session.get("amr") or "password")
            audit(request, "oidc.consent_requested", actor_id=user["id"], object_kind="app",
                  object_id=client["app_id"], result="ok", meta={"client_id": client_id, "scopes": consent_needed(scope)})
            language = "&" + urllib.parse.urlencode({"lang": locale}) if locale is not None else ""
            return RedirectResponse(f"/oauth/consent?{urllib.parse.urlencode({'request': rid})}{language}", status_code=303)

        code = grants.issue_code(
            client_id=client_id, user_id=user["id"], redirect_uri=redirect_uri, scope=scope,
            nonce=q.get("nonce"), code_challenge=challenge, code_challenge_method=method or None,
            auth_time=session.get("reauth_at") or session["created_at"],
            amr=session.get("amr") or "password",
            central_session_id=session["id"], workspace_id=workspace_id)
        audit(request, "oidc.authorize", actor_id=user["id"], object_kind="app",
              object_id=client["app_id"], result="ok", meta={"client_id": client_id})
        return back(code=code)

    # ------------------------------------------------------------------ consent (API-4)
    def _consent_actor(request: Request) -> tuple[dict, dict]:
        session = sessions.load(request.cookies.get(COOKIE_NAME), touch=False)
        if not session:
            raise ServiceError(401, "unauthorized", "请先登录")
        user = ident.get_user(session["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        return session, user

    def _redirect_to(row: dict, **params) -> str:
        params = {k: v for k, v in params.items() if v is not None}
        if row.get("state") is not None:
            params["state"] = row["state"]
        params["iss"] = oidc.issuer()
        sep = "&" if urllib.parse.urlparse(row["redirect_uri"]).query else "?"
        return f"{row['redirect_uri']}{sep}{urllib.parse.urlencode(params)}"

    @r.get("/api/auth/oauth-consent/{rid}")
    def consent_request(request: Request, rid: str):
        session, user = _consent_actor(request)
        row = consents.pending(rid, user_id=user["id"], central_session_id=session["id"])
        client = clients.get(row["client_id"])
        if not client or withdrawn_by_policy(store, client):
            raise ServiceError(410, "consent_expired", "该应用已停用，授权请求不再有效")
        ws = ident.get_workspace(row["workspace_id"]) if row.get("workspace_id") else None
        existing = consents.active(user["id"], row["client_id"], row.get("workspace_id"))
        wanted = consent_needed(row["scope"])
        choices = None
        if ws is None and wanted and client["app_id"] == PLATFORM_APP_ID_:
            # The person chooses which workspace the client may act in (one token, one workspace).
            choices = [{"id": w["id"], "name": w["name"], "role": w.get("role"),
                        "consented": bool(consents.active(user["id"], row["client_id"], w["id"]))}
                       for w in ident.workspaces_for(user["id"]) if w["status"] == "active"]
        return {"id": rid, "client": {"id": client["client_id"], "name": client["name"], "app_id": client["app_id"],
                                      "registration": client.get("registration") or "operator",
                                      "client_uri": client.get("client_uri")},
                "scopes": wanted, "already_granted": sorted(set(wanted) & set((existing or {}).get("scopes") or [])),
                "identity_scopes": sorted(set(row["scope"].split()) - set(wanted)),
                "workspace": {"id": ws["id"], "name": ws["name"]} if ws else None,
                "workspace_choices": choices,
                "redirect_host": urllib.parse.urlparse(row["redirect_uri"]).netloc, "expires_at": row["expires_at"]}

    @r.post("/api/auth/oauth-consent/{rid}/{decision}")
    def consent_decide(request: Request, rid: str, decision: str, body: dict = Body(default=None)):
        if decision not in ("approve", "deny"):
            raise ServiceError(404, "not_found", "页面不存在")
        session, user = _consent_actor(request)
        chosen = (body or {}).get("workspace_id") if isinstance(body, dict) else None
        if chosen is not None and (not isinstance(chosen, str) or not chosen or len(chosen) > 200):
            raise ServiceError(400, "invalid_request", "请选择一个有效的工作空间")
        with store.tx() as c:
            row = consents.pending(rid, user_id=user["id"], central_session_id=session["id"], c=c)
            client = clients.get(row["client_id"])
            if not client or withdrawn_by_policy(store, client):
                # Disabled, or self-registered under a policy that no longer accepts it.
                raise ServiceError(410, "consent_expired", "该应用已停用，授权请求不再有效")
            if not consents.decide(c, rid, decision):
                raise ServiceError(409, "consent_decided", "这个授权请求已经处理过了")
            if decision == "deny":
                audit(request, "oidc.consent_denied", actor_id=user["id"], object_kind="app", object_id=client["app_id"],
                      result="ok", meta={"client_id": client["client_id"]})
                return {"redirect": _redirect_to(row, error="access_denied", error_description="the person declined")}
            wanted_workspace = row.get("workspace_id")
            if wanted_workspace is None and consent_needed(row["scope"]) and client["app_id"] == PLATFORM_APP_ID_:
                # The page offered a choice; the answer must be one of this person's live workspaces.
                if not chosen:
                    raise ServiceError(400, "workspace_required", "请选择客户端可以使用的工作空间")
                wanted_workspace = chosen
            elif chosen is not None and chosen != wanted_workspace:
                raise ServiceError(400, "invalid_request", "这个授权请求不接受更换工作空间")
            # The entitlement may have changed while the page was open: ask again, now.
            entitled, workspace_id, reason = apps.check_access(user["id"], client["app_id"], wanted_workspace)
            if not entitled or workspace_id != wanted_workspace:
                return {"redirect": _redirect_to(row, error="access_denied", error_description=reason or "workspace changed")}
            consents.grant(c, user["id"], client["client_id"], workspace_id, consent_needed(row["scope"]))
            code = grants.issue_code(client_id=client["client_id"], user_id=user["id"], redirect_uri=row["redirect_uri"],
                                     scope=row["scope"], nonce=row["nonce"], code_challenge=row["code_challenge"],
                                     code_challenge_method=row["code_challenge_method"], auth_time=row["auth_time"],
                                     amr=row["amr"] or "password", central_session_id=session["id"], workspace_id=workspace_id)
        audit(request, "oidc.consent_granted", actor_id=user["id"], object_kind="app", object_id=client["app_id"],
              result="ok", meta={"client_id": client["client_id"], "scopes": consent_needed(row["scope"])})
        return {"redirect": _redirect_to(row, code=code)}

    @r.get("/api/auth/connected-apps")
    def connected_apps(request: Request):
        _, user = _consent_actor(request)
        return {"items": [{"id": c["id"], "client": {"id": c["client_id"], "name": c["client_name"], "app_id": c["app_id"]},
                           "workspace": {"id": c["workspace_id"], "name": c["workspace_name"]} if c["workspace_id"] else None,
                           "scopes": c["scopes"], "source": c["source"], "granted_at": c["granted_at"],
                           "last_issued_at": c["last_issued_at"], "last_used_at": c["last_used_at"]}
                          for c in consents.list_for_user(user["id"])]}

    @r.post("/api/auth/connected-apps/{consent_id}/revoke")
    def connected_app_revoke(request: Request, consent_id: str):
        _, user = _consent_actor(request)
        out = consents.withdraw(user["id"], consent_id)
        audit(request, "oidc.consent_withdrawn", actor_id=user["id"], object_kind="oauth_client",
              object_id=out["client_id"], workspace_id=out["workspace_id"], result="ok",
              meta={"tokens_revoked": out["tokens_revoked"]})
        return out

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

    # ------------------------------------------------------------------ client authentication
    def _client_auth(request: Request, form_id: str | None, form_secret: str | None,
                     *, confidential: bool = False) -> tuple[dict | None, JSONResponse | None]:
        """The calling client: `client_secret_post` (form) or `client_secret_basic` (RFC 6749 §2.3.1:
        form-urlencoded id and secret in an HTTP Basic header). One method per request — both is
        refused — and a Basic id that disagrees with a form `client_id` is not this client."""
        def refuse(code="invalid_client", status=401):
            headers = {"WWW-Authenticate": 'Basic realm="synwevo-oidc"'} if status == 401 else None
            return None, JSONResponse({"error": code}, status_code=status, headers=headers)

        header = request.headers.get("authorization") or ""
        basic_id = basic_secret = None
        if header[:6].lower() == "basic ":
            try:
                raw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
                left, sep, right = raw.partition(":")
                if not sep:
                    return refuse()
                basic_id, basic_secret = urllib.parse.unquote_plus(left), urllib.parse.unquote_plus(right)
            except (binascii.Error, UnicodeDecodeError):
                return refuse()
            if form_secret is not None:
                return refuse("invalid_request", 400)
            if form_id is not None and form_id != basic_id:
                return refuse()
        client = clients.get((basic_id if basic_id is not None else form_id) or "")
        if not client:
            return refuse()
        secret = basic_secret if basic_id is not None else form_secret
        if client["token_endpoint_auth_method"] == "none":
            # A public client has no secret to check (anything sent is ignored, as before); it may
            # not use endpoints that exist only for confidential clients.
            if confidential:
                return refuse()
            return client, None
        if not clients.check_secret(client["client_id"], secret or ""):
            return refuse()
        return client, None

    @r.post("/oidc/token")
    def token(request: Request,
              grant_type: str = Form(...), code: str = Form(None), redirect_uri: str = Form(None),
              client_id: str = Form(None), client_secret: str = Form(None),
              code_verifier: str = Form(None), refresh_token: str = Form(None),
              scope: str = Form(None), resource: list[str] | None = Form(None)):
        """Deliberately cookie-blind. Credentials come from the request body, never the browser."""
        if grant_type not in ("authorization_code", "refresh_token", "client_credentials"):
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        client, refused = _client_auth(request, client_id, client_secret)
        if refused:
            return refused
        if withdrawn_by_policy(store, client):
            # A self-registered client lives as long as the MCP policy accepts it: a code or refresh
            # token it obtained earlier is not honoured once the mechanism or its domain is withdrawn.
            return JSONResponse({"error": "invalid_client",
                                 "error_description": "本平台已不再接受这个客户端的自描述登记"},
                                status_code=401, headers={"WWW-Authenticate": 'Basic realm="synwevo-oidc"'})
        audience, bad_target = _target(client, resource)
        if bad_target:
            return bad_target

        if grant_type == "client_credentials":
            return _client_credentials(request, client, scope or "", audience)
        if grant_type == "refresh_token":
            return _refresh(request, client, refresh_token or "", audience)

        # Code consumption, the live sign-in check, token issuance and client linkage share
        # a write transaction with revocation. Logout/reset either wins first and refuses
        # this exchange, or runs afterward and finds every credential created here.
        with store.tx():
            try:
                row = grants.consume_code(code or "", client_id=client["client_id"],
                                          redirect_uri=redirect_uri or "", code_verifier=code_verifier)
            except ServiceError as e:
                # Commit replay-triggered revocation rather than rolling it back with the error.
                return JSONResponse({"error": "invalid_grant", "error_description": e.message}, status_code=400)

            central = store.one("SELECT user_id,revoked_at,idle_expires_at,absolute_expires_at "
                                "FROM sessions WHERE id=?", (row["central_session_id"],))
            if (not central or central["user_id"] != row["user_id"] or central["revoked_at"]
                    or min(central["idle_expires_at"], central["absolute_expires_at"]) <= now()):
                return JSONResponse({"error": "invalid_grant", "error_description": "the sign-in this code came from has ended"},
                                    status_code=400)
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
        unknown = asked - set(oidc.ALL_API_SCOPES)
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
        from synwevo_core.apps import PLATFORM_APP_ID
        if client["app_id"] == PLATFORM_APP_ID:
            # The workspace directory is the platform's own, not a registered application: a live
            # workspace (not frozen, deleted or in an unavailable organization) is the whole question.
            entitled, reason = directory_workspace_live(store, ident, ws)
        else:
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
        holder = clients.get_any(tok["client_id"]) if tok else None
        if not tok or (holder and withdrawn_by_policy(store, holder)):
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
        client, refused = _client_auth(request, client_id, client_secret)
        if refused:
            return refused
        # RFC 7009 §2.1: only a token issued to this client. Revoking anyone's token through any
        # client identity is not what the endpoint promises, even if the holder could.
        grants.revoke_token(token, "client_revoked", client_id=client["client_id"])
        # RFC 7009: an unknown token is still a success. Distinguishing them would turn this
        # endpoint into an oracle for whether a token is valid.
        return JSONResponse({}, status_code=200)

    @r.post("/oidc/introspect")
    def introspect(request: Request, token: str = Form(...), token_type_hint: str = Form(None),
                   client_id: str = Form(None), client_secret: str = Form(None)):
        """RFC 7662 for confidential clients, about their own tokens only: another client's token
        answers exactly like an unknown one, so the endpoint is no oracle for tokens it did not issue
        to the caller. `active` is decided now — revocation, expiry, a disabled client, and for a
        person's token their account status — not by what the token said when it was issued."""
        client, refused = _client_auth(request, client_id, client_secret, confidential=True)
        if refused:
            return refused
        inactive = JSONResponse({"active": False}, headers={"Cache-Control": "no-store"})
        digest = oidc._sha(token or "")
        order = ("refresh_hash", "access_hash") if token_type_hint == "refresh_token" else ("access_hash", "refresh_hash")
        row, kind = None, None
        for column in order:
            row = store.one(f"SELECT * FROM oauth_tokens WHERE {column}=?", (digest,))
            if row:
                kind = column
                break
        t = now()
        if not row or row["client_id"] != client["client_id"] or row.get("revoked_at"):
            return inactive
        exp = row["access_expires_at"] if kind == "access_hash" else row["refresh_expires_at"]
        if not exp or exp <= t or clients.get(row["client_id"]) is None:
            return inactive
        if row.get("user_id"):
            u = ident.get_user(row["user_id"])
            if not u or u["status"] != "active":
                return inactive
        body = {"active": True, "scope": row["scope"], "client_id": row["client_id"],
                "token_type": "Bearer" if kind == "access_hash" else "refresh_token",
                "sub": row.get("user_id") or row["client_id"], "aud": row.get("audience"),
                "iss": oidc.issuer(), "exp": int(exp), "iat": int(row["issued_at"])}
        if row.get("workspace_id"):
            body["workspace_id"] = row["workspace_id"]
        return JSONResponse({k: v for k, v in body.items() if v is not None}, headers={"Cache-Control": "no-store"})

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

    An RP session id is resolved to its central device first. The root, sibling RP sessions,
    grants and client-session linkage are revoked together before any network notification.
    `deliver=None` means the real network deliverer, looked up at call time.

    The two halves are also callable apart, for a caller that journals the revocation in the same
    write transaction (auth_routes' "sign out this device" and "sign out other devices"):
    `revoke_central_session` inside that transaction, `notify_logout` after its COMMIT.
    """
    return notify_logout(store, revoke_central_session(store, central_session_id, reason), deliver=deliver)


def revoke_central_session(store: Store, central_session_id: str, reason: str) -> dict:
    """end_central_session's revocations, in the caller's write transaction (or one of its own):
    the device's central sign-in, its relying-party sessions and grants. Returns the counts and
    `pending`, the client sessions `notify_logout` tells once this has committed — never from
    inside the transaction, whose writer lock no network call may hold."""
    sessions = Sessions(store)
    with store.tx():
        root = sessions.device_root_id(central_session_id)
        if root is None:
            return {"tokens_revoked": 0, "app_sessions_ended": 0, "sessions_revoked": 0, "pending": []}
        selected = store.one("SELECT user_id FROM sessions WHERE id=?", (central_session_id,))
        rows = store.all("SELECT * FROM client_sessions WHERE central_session_id=? "
                         "AND user_id=? AND logged_out_at IS NULL", (root, selected["user_id"]))
        root_live = store.one("SELECT 1 FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL",
                             (root, selected["user_id"]))
        outcome = sessions.revoke_device(central_session_id, reason, rp_reason=f"central_{reason}"[:60])
        rp_sessions = max(0, outcome["sessions_revoked"] - bool(root_live))
    return {"tokens_revoked": outcome["tokens_revoked"], "app_sessions_ended": rp_sessions,
            "sessions_revoked": outcome["sessions_revoked"], "pending": rows}


def notify_logout(store: Store, revoked: dict, *, deliver=None) -> dict:
    """end_central_session's notifications, after `revoke_central_session` committed and outside
    any transaction: a back-channel logout token to each pending client session that registered
    an endpoint, a failure kept on its row. Returns end_central_session's outcome."""
    deliver = oidc.deliver_logout if deliver is None else deliver
    grants, clients = oidc.Grants(store), oidc.Clients(store)
    notified, failed = 0, 0
    for cs in revoked["pending"]:
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
    return {"tokens_revoked": revoked["tokens_revoked"], "clients_notified": notified, "clients_failed": failed,
            "app_sessions_ended": revoked["app_sessions_ended"], "sessions_revoked": revoked["sessions_revoked"]}
