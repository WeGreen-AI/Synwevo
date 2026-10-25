"""Authorizing a connection to an external MCP server that requires OAuth (the Agent as an MCP client).

The flow is the MCP authorization specification's, driven from the server side of Synwevo because
the browser must never hold the external server's credentials: the connection's owner clicks
"authenticate", this module discovers the server's authorization server (RFC 9728 protected
resource metadata from the 401 challenge or the well-known path, then RFC 8414 / OpenID Connect
discovery), obtains a client id (pre-registered by the person, else this platform's Client ID
Metadata Document when the authorization server accepts them, else dynamic registration when it
offers it), and sends the person's browser to the authorization endpoint with PKCE, `state` and
the `resource` of the MCP server. The callback (on the workplace origin, in the person's signed-in
session) checks that this person still manages the connection, `state` and `iss` (RFC 9207),
exchanges the code at the token endpoint — pinned, without redirects — and seals the token set on
the connection. Refresh happens the same way when a token is about to expire. A connection the
platform stopped is not authorized at all, and every write along the way (a registration, a flow, a
token set) holds only while the connection is still the one the authorization began with: a stop, a
new address or a deletion landing meanwhile wins. Those writes, and every seal and unseal, happen on
the calling thread: the runtime's loop only moves bytes.

Every outbound request goes through the runtime's pinned client (policy-checked destination, the
hostname kept for TLS). Nothing from the remote server is trusted beyond its protocol role: its
metadata is validated with the SDK's models (for shape — identifiers are kept as published); the
resource metadata must name the connection's own server (RFC 9728 §3.3); the authorization server
metadata's issuer must be, string for string, the issuer its well-known address was built from
(RFC 8414 §3.3); this platform's Client ID Metadata Document — a client id every server accepts — is
used only with a server that says it sends `iss`, and its callback is refused without it; a client the
person registered is used only at the authorization server it was first authorized at (mix-up,
`_client`); the server must advertise PKCE S256; and a client secret goes only to the authorization
server it belongs to. A server that
refuses a token for want of scope (403 `insufficient_scope`) has the scope it names asked for by the
next authorization (step-up, `record_scope_challenge`), a bounded number of times.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
from urllib.parse import urlencode, urlsplit

import httpx2
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (build_oauth_authorization_server_metadata_discovery_urls,
                                   build_protected_resource_metadata_discovery_urls, extract_resource_metadata_from_www_auth,
                                   extract_scope_from_www_auth, issuers_match)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from pydantic import AnyHttpUrl, AnyUrl, ConfigDict, TypeAdapter, ValidationError

from synwevo_core import env, sealed
from synwevo_core.auth import audit_event
from synwevo_core.errors import ServiceError
from synwevo_core.http import MCP_CLIENT_METADATA_PATH
from synwevo_core.store import new_id, now

from .runtime import MAX_SCOPE_CHARS, MAX_SCOPE_TOKENS, scope_tokens

log = logging.getLogger("synwevo.mcp.client")

FLOW_TTL_S = 600
DISCOVERY_TIMEOUT_S = 20.0
CALLBACK_PATH = "/api/suite/agent/mcp/oauth/callback"
CLIENT_NAME = "Synwevo Agent"
SEAL = "mcp-credential"
#: The connection is still the OAuth connection to the server (`url`, the one parameter) that an
#: authorization began with: not re-pointed or given another authentication, not stopped by the
#: platform, not deleted. Each of those voids the authorization under way — its flows go, and with them
#: what was registered or issued for it (connections.Connections.update, synwevo_identity.admin_mcp) —
#: so a write conditional on this cannot bring back what they forgot.
_CURRENT = "url=? AND auth_kind='oauth' AND platform_disabled_at IS NULL AND deleted_at IS NULL"
#: How many times a server's `insufficient_scope` challenges may widen what the connection's next
#: authorization asks for (record_scope_challenge) — MCP authorization, "Step-Up Authorization Flow":
#: clients SHOULD implement retry limits — until its address or authentication changes. Past it, a
#: challenge is answered as it is (the person re-authenticates for what was asked before).
MAX_STEP_UPS = 5
#: RFC 6749 §5.2: the errors with which a token endpoint refuses a refresh token or the client
#: itself. Only these (and a 401) mean the authorization is gone and the person must give it again.
REFRESH_REFUSALS = ("invalid_grant", "invalid_client", "unauthorized_client")
_TOKEN_ERRORS = ("invalid_request", "invalid_client", "invalid_grant", "unauthorized_client", "unsupported_grant_type",
                 "invalid_scope")
_DEFAULT_PORTS = {"http": 80, "https": 443}


def app_url() -> str:
    return (env.get("APP_URL") or "").strip().rstrip("/")


def client_metadata_document() -> dict:
    """What this platform tells an external authorization server about the Agent as a client."""
    base = app_url()
    return {"client_id": base + MCP_CLIENT_METADATA_PATH, "client_name": CLIENT_NAME, "client_uri": base or None,
            "redirect_uris": [base + CALLBACK_PATH], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "application_type": "web"}


def _url_origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return scheme, parts.hostname.lower().rstrip("."), port or _DEFAULT_PORTS[scheme]


def is_deployment_client_id(client_id: str | None) -> bool:
    """Whether `client_id` is this deployment's Client ID Metadata Document however it is spelled: any
    http(s) URL on this deployment's own origin. An authorization server that fetches client ids resolves
    a variant (another case, a default port written out, a query) to the same document, so the rules that
    hold the document to servers sending `iss` compare by origin, never by the exact string — a
    registration endpoint cannot hand the document back as "its own" client in another spelling."""
    if not isinstance(client_id, str) or not client_id:
        return False
    own = _url_origin(app_url())
    return own is not None and _url_origin(client_id) == own


# How OAuthMetadata renders an issuer URL (host case, default port, an empty path kept empty), so the
# origin of an MCP server compares with a metadata `issuer` as strings.
_ISSUER_URL = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def _origin_issuer(server_url: str) -> str:
    """The issuer identifier a 2025-03-26 server's metadata address is built from: its origin."""
    parts = urlsplit(server_url)
    return str(_ISSUER_URL.validate_python(f"{parts.scheme}://{parts.netloc}"))


def _dynamic_at(stored: dict | None, issuer: str) -> bool:
    """Whether `stored` is a dynamic registration made at `issuer`, the one authorization server its
    client id and secret belong to (MCP authorization, "Authorization Server Binding"; compared the
    SDK's way, `issuers_match`)."""
    return bool(stored and stored.get("kind") == "dynamic" and stored.get("client_id") and stored.get("issuer")
                and issuers_match(stored["issuer"], issuer))


def _stored(row: dict) -> dict | None:
    """The connection's OAuth client record (`oauth_client_json`): what was registered for it, and
    the step-up its server asked for (`step_up`, record_scope_challenge)."""
    try:
        value = json.loads(row["oauth_client_json"]) if row.get("oauth_client_json") else None
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """The origin `url` names (RFC 6454: scheme, host, port — a host's case and a spelled-out default
    port name the same origin); None for text that names none."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not parts.scheme or not host:
        return None
    scheme = parts.scheme.lower()
    return scheme, host.rstrip("."), port or _DEFAULT_PORTS.get(scheme)


def _probe_request(url: str) -> httpx2.Request:
    body = {"jsonrpc": "2.0", "id": "discover", "method": "server/discover",
            "params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                 "io.modelcontextprotocol/clientInfo": {"name": CLIENT_NAME, "version": "1"},
                                 "io.modelcontextprotocol/clientCapabilities": {}}}}
    return httpx2.Request("POST", url, json=body, headers={"Accept": "application/json, text/event-stream",
                                                          "MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "server/discover"})


class OutboundOAuth:
    def __init__(self, connections):
        self.connections = connections
        self.store = connections.store
        self.runtime = connections.runtime

    # ------------------------------------------------------------------ discovery
    async def _discover(self, conn: dict, *, bound: str | None = None) -> dict:
        """The authorization server of the MCP server at `conn['url']` (its validated metadata, and its
        issuer exactly as published), the scope the server asks for, and the registration endpoint
        that may be used. `bound` is the issuer the person's own client belongs to (`_bound_issuer`):
        when the resource metadata lists several authorization servers, that one is used if it is
        listed — the choice among them is the client's (MCP authorization, "Authorization Server
        Discovery"), and the person's client is only good at its own. Network only: nothing here reads
        or writes the database."""
        owner = conn["owner_id"]
        async with self.runtime.http(conn["url"], owner, read_timeout=DISCOVERY_TIMEOUT_S) as http:
            probe = await http.send(_probe_request(conn["url"]))
            if probe.status_code != 401:
                if probe.status_code < 400 or probe.status_code in (400, 404, 405, 406):
                    # It answered (or complained about the protocol) without asking for a token.
                    raise ServiceError(409, "mcp_auth_not_required", "这个 MCP 服务没有要求认证，无需 OAuth")
                raise ServiceError(502, "mcp_server_error", "MCP 服务没有按预期要求认证", {"http_status": probe.status_code})
            prm_hint = extract_resource_metadata_from_www_auth(probe)
            scope_hint = extract_scope_from_www_auth(probe)
        own_resource = resource_url_from_server_url(conn["url"])
        prm = prm_raw = None
        for url in build_protected_resource_metadata_discovery_urls(prm_hint, conn["url"]):
            got = await self._get_json(url, owner, ProtectedResourceMetadata)
            if got is not None:
                prm, prm_raw = got
                break
        if prm is not None:
            # RFC 9728 §3.3 / §7.3: metadata naming another resource MUST NOT be used — else a server
            # could name another service's resource and authorization server, and the token the person
            # grants there would come back to it as a bearer. The SDK's rule
            # (`OAuthClientProvider._validate_resource_match`): same origin, and the connection's path at
            # or below the one named (metadata at the root names the origin).
            if not check_resource_allowed(requested_resource=own_resource, configured_resource=str(prm.resource)):
                raise ServiceError(502, "mcp_oauth_discovery_failed", "MCP 服务的资源元数据指向的不是这个服务地址，已拒绝",
                                   {"reason": "resource_mismatch"})
            if not prm.authorization_servers:
                raise ServiceError(502, "mcp_oauth_discovery_failed", "MCP 服务的资源元数据没有指明授权服务器")
            # Exactly as published (RFC 8414 §3.3, RFC 9207 §2.4): the string the metadata's issuer is
            # compared with, not the model's rendering of it — which folds a host's case and drops a
            # default port, and would then refuse the server's own, correct `iss`.
            listed = [a for a in (prm_raw.get("authorization_servers") or []) if isinstance(a, str)]
            auth_server = next((a for a in listed if bound is not None and issuers_match(a, bound)), listed[0] if listed else None)
            if not isinstance(auth_server, str):
                raise ServiceError(502, "mcp_oauth_discovery_failed", "MCP 服务的资源元数据没有指明授权服务器")
            resource = str(prm.resource)
        else:
            # Servers of the 2025-03-26 revision had no resource metadata: their authorization
            # server lives at their own origin.
            auth_server, resource = None, own_resource
        got = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server, conn["url"]):
            got = await self._get_json(url, owner, OAuthMetadata)
            if got is not None:
                break
        if got is None:
            raise ServiceError(502, "mcp_oauth_discovery_failed", "找不到 MCP 服务的授权服务器元数据")
        meta, meta_raw = got
        # RFC 8414 §3.3 (and OpenID Connect Discovery §4.3), on every path: the `issuer` MUST be identical
        # to the issuer the well-known address was built from, or the metadata MUST NOT be used — its
        # endpoints would otherwise be the word of whoever answered there (a mix-up: the code, the PKCE
        # verifier and any client secret sent to a token endpoint of the answerer's choosing). Compared
        # and recorded as published: the issuer the callback's `iss` must then equal (RFC 9207 §2.4;
        # MCP authorization: no case folding, default-port elision or trailing-slash normalization).
        issuer = meta_raw.get("issuer")
        if not isinstance(issuer, str):
            raise ServiceError(502, "mcp_oauth_discovery_failed", "授权服务器元数据的 issuer 与发现地址不一致，已拒绝",
                               {"reason": "issuer_mismatch"})
        if auth_server is not None:
            same = issuer == auth_server
        else:
            # The 2025-03-26 path: the issuer this address was built from is the server's origin, an
            # identifier this client made, not one anybody published. `https://host` and `https://host/`
            # both name it (§3.1 drops the terminating "/" before inserting the well-known suffix), and an
            # origin compares as an origin.
            same = issuers_match(str(_ISSUER_URL.validate_python(issuer)), _origin_issuer(conn["url"]))
        if not same:
            raise ServiceError(502, "mcp_oauth_discovery_failed", "授权服务器元数据的 issuer 与发现地址不一致，已拒绝",
                               {"reason": "issuer_mismatch"})
        # Endpoints off the issuer's origin (whether that matters depends on the client: `_client`).
        own = _origin(issuer)
        off = [name for name in ("authorization_endpoint", "token_endpoint") if _origin(str(getattr(meta, name))) != own]
        # A registration's secret goes to the token endpoint of the issuer it was registered at
        # (_auth_headers): a registration endpoint elsewhere would mint, for whoever answers there, a
        # client this deployment then authenticates as at that issuer. Not used at all.
        registration = str(meta.registration_endpoint) if meta.registration_endpoint else None
        if registration and _origin(registration) != own:
            registration = None
        # MCP authorization, "Authorization Code Protection": a document without
        # code_challenge_methods_supported means no PKCE, and the client MUST refuse to proceed.
        if "S256" not in (meta.code_challenge_methods_supported or []):
            raise ServiceError(502, "mcp_oauth_unsupported", "授权服务器没有宣告支持 PKCE S256，已拒绝")
        scope = scope_hint or (" ".join(prm.scopes_supported) if prm is not None and prm.scopes_supported else None)
        if "offline_access" in (meta.scopes_supported or []) and "offline_access" not in (scope or "").split():
            # MCP authorization, "Refresh Tokens": a client MAY ask for offline_access when the
            # authorization server lists it. Without a refresh token the connection would need the
            # person's authorization again each time the access token expires.
            scope = " ".join(filter(None, [scope, "offline_access"]))
        return {"metadata": meta, "issuer": issuer, "resource": resource, "scope": scope, "off_origin": off,
                "registration_endpoint": registration, "registration_refused": bool(meta.registration_endpoint) and not registration}

    async def _get_json(self, url: str, owner: str, model):
        """`model` validated from the JSON document at `url`, with the document as published — or None
        when that candidate address has none."""
        try:
            async with self.runtime.http(url, owner, read_timeout=DISCOVERY_TIMEOUT_S) as http:
                resp = await http.get(url, headers={"Accept": "application/json", "MCP-Protocol-Version": "2026-07-28"})
        except ServiceError:
            raise
        except Exception:  # noqa: BLE001 — one candidate address not answering is not a verdict
            return None
        if resp.status_code != 200:
            return None
        try:
            raw = resp.json()
            return model.model_validate(raw), raw
        except (ValueError, ValidationError):
            return None

    # ------------------------------------------------------------------ registration
    def _client(self, conn: dict, found: dict) -> dict:
        """The client id to use at this authorization server, in the specification's priority:
        pre-registered, Client ID Metadata Document, dynamic registration. On the calling thread: a
        registration goes out on the runtime loop (`_register`, bytes only), and what it answered is
        kept — written and sealed — here, after the loop handed it back.

        Mix-up (RFC 9700 §4.4.1): the server a person names may be an attacker's, whose authorization
        server — its own, under its own issuer, so RFC 8414 §3.3 holds — sends the browser on to an
        honest one: its authorization endpoint is the honest server's, or redirects there. The honest
        server's code and the PKCE verifier then come back to this callback, under the attacker's
        flow, for the attacker's token endpoint. RFC 9700 §4.4.2 has a client of many authorization
        servers prevent this; with one callback address for all of them, only `iss` (RFC 9207) can.
        The MCP specification lets a client proceed when a server neither says it sends `iss` nor
        sends it ("Authorization Response Validation"), and RFC 9207 §2.4 leaves refusing such a
        response to local policy. What the attacker needs is a client id the honest server accepts
        for this callback, so the rule follows whom the client id is valid at:

        * this deployment's Client ID Metadata Document is one public client id that every server
          accepting such documents accepts, the honest one included: the attacker needs nothing more.
          It is used only with a server that says it sends `iss`, and its callback is then refused
          without that issuer (callback) — however the id came to be named here: a registration
          endpoint may hand it out as its own, a person may type it as theirs. With `iss`, endpoints
          off the issuer's origin add nothing an attacker could use; without it, the refusal names them;
        * a client the person registered is valid at the one authorization server they registered it
          at (MCP authorization, "Authorization Server Binding"): kept to the issuer it was first
          authorized at (`_bound_issuer`) and refused at any other (`mcp_oauth_issuer_changed`), it is
          never handed to another server's endpoints — whatever origin its own server's endpoints are
          on (a hosted sign-in domain beside the issuer's, as Amazon Cognito and Google keep them);
        * a dynamic registration is the issuing server's own client, kept to that issuer
          (`_dynamic_at`), and where its endpoints are says nothing about it: to have an honest server
          answer this callback for it, an attacker must first register a client there in this
          deployment's name, after which its own authorization endpoint can send the browser on
          wherever the endpoints are. Against an honest server that offers such open registration
          and sends no `iss`, that mix-up remains (docs/platform/mcp.md §4.3), as the MCP
          specification's "proceed" leaves it."""
        meta: OAuthMetadata = found["metadata"]
        issuer = found["issuer"]
        stored = _stored(conn)
        document = client_metadata_document()
        sends_iss = meta.authorization_response_iss_parameter_supported is True

        def usable(client_id: str) -> None:
            """Refuse this deployment's document id at a server that would not say who answered."""
            if not is_deployment_client_id(client_id) or sends_iss:
                return
            if found["off_origin"]:
                raise ServiceError(502, "mcp_oauth_discovery_failed", "授权服务器的授权或令牌端点不在其 issuer 的源站上，且它没有声明会回传 iss，已拒绝",
                                   {"reason": "endpoint_origin_mismatch", "endpoints": found["off_origin"]})
            raise ServiceError(409, "mcp_oauth_registration_unavailable",
                               "这个授权服务器没有声明会回传 iss，不能使用本平台的自描述客户端；请先在对方处登记客户端，再在连接里填写 client_id",
                               {"issuer": issuer, "reason": "iss_unsupported"})

        if stored and stored.get("kind") == "preregistered" and stored.get("client_id"):
            usable(stored["client_id"])
            bound = self._bound_issuer(conn, stored)
            if bound is not None and not issuers_match(bound, issuer):
                raise _issuer_changed(issuer, bound)
            return {"kind": "preregistered", "client_id": stored["client_id"], "issuer": issuer,
                    "confidential": bool(conn.get("oauth_client_secret_sealed"))}
        if _dynamic_at(stored, issuer):
            usable(stored["client_id"])
            return {**stored, "confidential": bool(conn.get("oauth_client_secret_sealed"))}
        cimd = bool(meta.client_id_metadata_document_supported) and document["client_id"].startswith("https://")
        if cimd and sends_iss:
            return {"kind": "cimd", "client_id": document["client_id"], "issuer": issuer, "confidential": False}
        endpoint = found["registration_endpoint"]
        if endpoint:
            client_id, secret = self.runtime.run(self._register(conn, endpoint, document), timeout=DISCOVERY_TIMEOUT_S * 2)
            usable(client_id)
            record = {"kind": "dynamic", "client_id": client_id, "issuer": issuer, "registered_at": now(),
                      "registration_endpoint": endpoint}
            if stored and isinstance(stored.get("step_up"), dict):
                # What the server's challenges asked for is the resource's, whichever client asks for it.
                record["step_up"] = stored["step_up"]
            # Only on the connection the registration was made for (_CURRENT): a stop, a new address or a
            # deletion that landed meanwhile forgot the client, and its secret is not sealed back on.
            if not self.store.execute("UPDATE mcp_connections SET oauth_client_json=?, oauth_client_secret_sealed=?, updated_at=? "
                                      "WHERE id=? AND " + _CURRENT,
                                      (json.dumps(record), sealed.seal(self.store, SEAL, secret) if secret else None, now(),
                                       conn["id"], conn["url"])):
                self._overtaken(conn["id"])
            return {**record, "confidential": bool(secret)}
        if cimd:
            usable(document["client_id"])                   # it accepts the document, but would not say who answered
        detail = {"issuer": issuer}
        if found.get("registration_refused"):
            detail["reason"] = "endpoint_origin_mismatch"
        raise ServiceError(409, "mcp_oauth_registration_unavailable",
                           "这个授权服务器不接受自描述登记也没有动态登记：请先在对方处登记客户端，再在连接里填写 client_id", detail)

    def _bound_issuer(self, conn: dict, stored: dict) -> str | None:
        """The authorization server the person's pre-registered client belongs to (MCP authorization,
        "Authorization Server Binding": such credentials are specific to the server that issued them),
        as this connection learnt it — the issuer of the client's first completed authorization, kept
        on its record (`issuer`, written by the callback); for a record from before that, the issuer
        of its last completed authorization. None until it has completed one: the person registered it
        at the server the connection's own server named when they gave it. A new client or a new
        address forgets the record and the authorizations with it (connections.Connections.update)."""
        if isinstance(stored.get("issuer"), str) and stored["issuer"]:
            return stored["issuer"]
        last = self.store.one("SELECT issuer FROM mcp_oauth_flows WHERE connection_id=? AND outcome='ok' AND client_id=? "
                              "ORDER BY completed_at DESC LIMIT 1", (conn["id"], stored.get("client_id")))
        return last["issuer"] if last else None

    async def _register(self, conn: dict, endpoint: str, document: dict) -> tuple[str, str | None]:
        """RFC 7591 dynamic registration at `endpoint`: the client id and secret it issued. Network only."""
        metadata = OAuthClientMetadata(client_name=CLIENT_NAME, redirect_uris=[AnyUrl(document["redirect_uris"][0])],
                                       grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                       token_endpoint_auth_method="none", scope=None)
        payload = metadata.model_dump(by_alias=True, mode="json", exclude_none=True)
        payload["application_type"] = "web"
        async with self.runtime.http(endpoint, conn["owner_id"], read_timeout=DISCOVERY_TIMEOUT_S) as http:
            resp = await http.post(endpoint, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code not in (200, 201):
            raise ServiceError(502, "mcp_oauth_registration_failed", "授权服务器拒绝了客户端登记",
                               {"http_status": resp.status_code})
        try:
            body = resp.json()
            client_id = body["client_id"]
        except (ValueError, KeyError, TypeError):
            raise ServiceError(502, "mcp_oauth_registration_failed", "授权服务器返回了无效的登记结果") from None
        if not isinstance(client_id, str) or not client_id or len(client_id) > 512:
            raise ServiceError(502, "mcp_oauth_registration_failed", "授权服务器返回了无效的登记结果")
        return client_id, (body.get("client_secret") if isinstance(body.get("client_secret"), str) else None)

    def _requested_scope(self, conn: dict, hint: str | None, issuer: str) -> str | None:
        """What this authorization asks for (MCP authorization, "Scope Selection Strategy" and "Step-Up
        Authorization Flow"): the server's own word for it now (`hint`, from discovery), the scopes
        its `insufficient_scope` challenges named since (record_scope_challenge) — authoritative for
        the operations they refused — and, at the same authorization server, what the last
        authorization asked for and was granted, so that a re-authorization does not lose what other
        operations need. Bounded (runtime.MAX_SCOPE_TOKENS / MAX_SCOPE_CHARS): beyond the server's
        own hint, what does not fit is not asked for."""
        parts = [(hint or "").split()]
        stored = _stored(conn)
        if stored and isinstance(stored.get("step_up"), dict):
            parts.append(scope_tokens(stored["step_up"].get("scope")) or [])
        last = self.store.one("SELECT issuer, scope FROM mcp_oauth_flows WHERE connection_id=? AND outcome='ok' "
                              "ORDER BY completed_at DESC LIMIT 1", (conn["id"],))
        if last and issuers_match(last["issuer"], issuer):
            parts.append(scope_tokens(last["scope"]) or [])
            tokens = self.connections._tokens(conn) if conn.get("credential_sealed") else None
            parts.append(scope_tokens((tokens or {}).get("scope")) or [])
        out: list[str] = []
        for i, part in enumerate(parts):
            for token in part:
                if token in out:
                    continue
                if i and (len(out) >= MAX_SCOPE_TOKENS or len(" ".join([*out, token])) > MAX_SCOPE_CHARS):
                    continue
                out.append(token)
        return " ".join(out) or None

    # ------------------------------------------------------------------ the flow
    def start(self, user: dict, cid: str) -> dict:
        self.connections.require_outbound()
        conn = self.connections._row(cid)
        self.connections._manage(user["id"], conn)
        # Authenticating is resuming: a connection the platform stopped neither hears from this flow
        # (no probe, no discovery, no registration) nor gets a flow, until the platform lifts the stop.
        self.connections._platform_stop(conn)
        if conn["auth_kind"] != "oauth":
            raise ServiceError(409, "mcp_auth_kind_mismatch", "这个连接不使用 OAuth 认证")
        if not app_url():
            raise ServiceError(503, "mcp_oauth_unavailable", "本部署未配置工作空间地址，无法完成回调")
        stored = _stored(conn)
        bound = (self._bound_issuer(conn, stored) if stored and stored.get("kind") == "preregistered" and stored.get("client_id")
                 else None)
        found = self.runtime.run(self._discover(conn, bound=bound), timeout=DISCOVERY_TIMEOUT_S * 3)
        meta: OAuthMetadata = found["metadata"]
        issuer = found["issuer"]
        client = self._client(conn, found)
        scope = self._requested_scope(conn, found["scope"], issuer)
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)
        redirect_uri = app_url() + CALLBACK_PATH
        t = now()
        with self.store.tx():
            self.store.execute("DELETE FROM mcp_oauth_flows WHERE connection_id=? AND completed_at IS NULL", (cid,))
            # Only for the connection discovery was made for (_CURRENT): a new address landing during
            # discovery forgot the connection's flows, and a flow for the old server recorded after it
            # would let that server's tokens be sealed on the re-pointed connection (callback).
            if not self.store.execute(
                    "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, "
                    "authorization_endpoint, token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, "
                    "expires_at) SELECT ?,?,?,?,?,?,?,?,?,?,?,?,?,?,? "
                    "WHERE EXISTS (SELECT 1 FROM mcp_connections WHERE id=? AND " + _CURRENT + ")",
                    (new_id("mcpflow"), cid, user["id"], state, sealed.seal(self.store, SEAL, pkce.code_verifier), issuer,
                     str(meta.authorization_endpoint), str(meta.token_endpoint), client["client_id"], scope,
                     found["resource"], redirect_uri, int(bool(meta.authorization_response_iss_parameter_supported)), t,
                     t + FLOW_TTL_S, cid, conn["url"])):
                self._overtaken(cid)
            audit_event(self.store, "suite.agent.mcp.oauth.start", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid,
                        meta={"issuer": issuer, "registration": client["kind"]})
        params = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_uri, "state": state,
                  "code_challenge": pkce.code_challenge, "code_challenge_method": "S256", "resource": found["resource"]}
        if scope:
            params["scope"] = scope
        sep = "&" if urlsplit(str(meta.authorization_endpoint)).query else "?"
        return {"authorization_url": f"{meta.authorization_endpoint}{sep}{urlencode(params)}", "issuer": issuer,
                "registration": client["kind"], "expires_at": t + FLOW_TTL_S}

    def callback(self, user: dict, *, state: str | None, code: str | None, iss: str | None, error: str | None,
                 error_description: str | None = None) -> dict:
        if not state:
            raise ServiceError(400, "mcp_oauth_state_missing", "回调缺少 state")
        flow = self.store.one("SELECT * FROM mcp_oauth_flows WHERE state=?", (state,))
        if not flow or flow["user_id"] != user["id"]:
            raise ServiceError(404, "mcp_oauth_flow_not_found", "这次认证不存在或不属于你")
        cid = flow["connection_id"]
        if flow["completed_at"] is not None:
            raise ServiceError(409, "mcp_oauth_flow_decided", "这次认证已经处理过了")
        if flow["expires_at"] <= now():
            self._close(flow, "expired")
            raise ServiceError(410, "mcp_oauth_flow_expired", "这次认证已过期，请重新开始")
        try:
            # Whoever began this authorization decides it only while they may still: someone removed
            # from the connection's workspace or no longer its manager since — or a connection the
            # platform stopped meanwhile — has no external account's tokens sealed on it, and the
            # authorization server is not asked for any. Nor is one for the person's own client at an
            # authorization server it does not belong to (`_owned`).
            conn = self._decider(user, cid)
            self._owned(conn, flow)
        except ServiceError:
            self._close(flow, "refused")
            raise
        # RFC 9207 §2.4: a present `iss` must be the issuer the metadata named; an absent one is
        # refused when the server said it would send it — and always for this deployment's Client ID
        # Metadata Document, which every server accepts, so that only `iss` says whose answer this is
        # (mix-up, `_client`).
        if iss is not None and iss != flow["issuer"]:
            self._close(flow, "issuer_mismatch")
            raise ServiceError(400, "mcp_oauth_issuer_mismatch", "回调的 iss 与授权服务器不一致，已拒绝")
        if iss is None and (flow["iss_supported"] or is_deployment_client_id(flow["client_id"])):
            self._close(flow, "issuer_missing")
            raise ServiceError(400, "mcp_oauth_issuer_mismatch", "回调缺少 iss，已拒绝")
        if error:
            self._close(flow, f"error:{error}"[:80])
            raise ServiceError(400, "mcp_oauth_denied", "授权服务器拒绝了这次认证", {"error": error[:80]})
        if not code:
            raise ServiceError(400, "mcp_oauth_code_missing", "回调缺少授权码")
        # The verifier and any client secret are opened here, on this thread; the loop only sends them.
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"],
                "client_id": flow["client_id"], "code_verifier": sealed.unseal(self.store, SEAL, flow["code_verifier_sealed"]),
                "resource": flow["resource"]}
        headers = self._auth_headers(conn, flow["client_id"], flow["issuer"])
        tokens = self.runtime.run(self._token_request(conn, flow["token_endpoint"], form, headers),
                                  timeout=DISCOVERY_TIMEOUT_S * 2)
        refused = None
        with self.store.tx():
            try:
                # Asked again under the write lock: a removal, a demotion or a stop that landed while the
                # code was being exchanged still means these tokens are nobody's to seal here.
                current = self._decider(user, cid)
                # The person's own client now belongs to the server that issued these tokens (_bind).
                self._bind(current, flow)
            except ServiceError as err:
                refused = err
                self._close(flow, "refused")
            else:
                # Tied to this flow: if a change voided it while the code was being exchanged, a newer flow
                # (for the server the connection now points at) does not vouch for these tokens.
                self.connections.store_tokens(cid, tokens, flow_id=flow["id"])
                self._close(flow, "ok")
                audit_event(self.store, "suite.agent.mcp.oauth.complete", actor_kind="user", actor_id=user["id"],
                            workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                            object_kind="mcp_connection", object_id=cid, meta={"issuer": flow["issuer"], "scope": tokens.get("scope")})
        if refused is not None:
            raise refused
        try:
            self.connections.test(user, cid, origin="discover")
        except ServiceError as err:
            log.info("post-authorization discovery of connection %s failed: %s", cid, err.code)
        return {"connection_id": cid, "ok": True}

    def _decider(self, user: dict, cid: str) -> dict:
        """The connection, while `user` may still decide its authorization: they manage it (in its open
        workspace) and the platform has not stopped it or its destination."""
        conn = self.connections._row(cid)
        self.connections._manage(user["id"], conn)
        self.connections._platform_stop(conn)
        return conn

    def _owned(self, conn: dict, flow: dict) -> dict | None:
        """The person's pre-registered client record `flow` authorizes, if it does — refused when that
        client belongs to another authorization server than the flow's (`_bound_issuer`)."""
        stored = _stored(conn)
        if not (stored and stored.get("kind") == "preregistered" and stored.get("client_id") == flow["client_id"]):
            return None
        bound = self._bound_issuer(conn, stored)
        if bound is not None and not issuers_match(bound, flow["issuer"]):
            raise _issuer_changed(flow["issuer"], bound)
        return stored

    def _bind(self, conn: dict, flow: dict) -> None:
        """Keep the person's pre-registered client to the issuer this completed authorization of it was
        made at (`_bound_issuer`), on its record — or refuse the authorization when that client already
        belongs to another (`_owned`). Inside the callback's transaction, beside the tokens it seals:
        both, or neither."""
        stored = self._owned(conn, flow)
        if stored is not None and not stored.get("issuer"):
            self.store.execute("UPDATE mcp_connections SET oauth_client_json=? WHERE id=? AND oauth_client_json IS ?",
                               (json.dumps({**stored, "issuer": flow["issuer"]}), conn["id"], conn["oauth_client_json"]))

    def _overtaken(self, cid: str) -> None:
        """Refuse a step of the authorization whose connection is no longer the one it began with
        (_CURRENT): a platform stop by its name, anything else — a new address or authentication, a
        deletion — as a change."""
        row = self.store.one("SELECT platform_disabled_at FROM mcp_connections WHERE id=?", (cid,))
        if row:
            self.connections._platform_stop(row)
        raise ServiceError(409, "mcp_connection_changed", "连接在认证期间被修改、停用或删除，请重新认证")

    def _close(self, flow: dict, outcome: str) -> None:
        self.store.execute("UPDATE mcp_oauth_flows SET completed_at=?, outcome=?, code_verifier_sealed='' WHERE id=?",
                           (now(), outcome, flow["id"]))

    def _auth_headers(self, conn: dict, client_id: str, issuer: str) -> dict:
        """Client authentication at `issuer`'s token endpoint as `client_id`: the stored secret only
        when it belongs to that client there — the person's pre-registered client (at the issuer it
        belongs to, once known: `_bound_issuer`), or a dynamic registration made at this same issuer.
        The secret of another authorization server's client (the server switched, or a Client ID
        Metadata Document is in use) is not sent."""
        if not conn.get("oauth_client_secret_sealed"):
            return {}
        stored = _stored(conn)
        if not stored or stored.get("client_id") != client_id:
            return {}
        if stored.get("kind") == "preregistered":
            bound = self._bound_issuer(conn, stored)
            if bound is not None and not issuers_match(bound, issuer):
                return {}
        elif not _dynamic_at(stored, issuer):
            return {}
        secret = sealed.unseal(self.store, SEAL, conn["oauth_client_secret_sealed"])
        raw = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {raw}"}

    async def _token_request(self, conn: dict, endpoint: str, form: dict, headers: dict) -> dict:
        """One token request (the code, or a refresh token) to `endpoint`. Network only: the form and
        the client authentication were made on the calling thread."""
        async with self.runtime.http(endpoint, conn["owner_id"], read_timeout=DISCOVERY_TIMEOUT_S) as http:
            resp = await http.post(endpoint, data=form, headers={"Accept": "application/json", **headers})
        if resp.status_code != 200:
            detail = {"http_status": resp.status_code}
            try:
                body = resp.json()
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    # RFC 6749 §5.2's codes by name; anything else the server wrote is its own text.
                    detail["error"] = body["error"] if body["error"] in _TOKEN_ERRORS else "other"
            except ValueError:
                pass
            raise ServiceError(401 if resp.status_code in (400, 401) else 502, "mcp_oauth_token_failed",
                               "授权服务器没有签发令牌", detail)
        try:
            token = OAuthToken.model_validate(resp.json())
        except (ValueError, ValidationError):
            raise ServiceError(502, "mcp_oauth_token_failed", "授权服务器返回了无效的令牌响应") from None
        out = {"access_token": token.access_token, "refresh_token": token.refresh_token, "scope": token.scope,
               "token_type": token.token_type}
        out["expires_at"] = (now() + int(token.expires_in)) if token.expires_in else None
        return out

    def refresh(self, conn: dict, tokens: dict) -> dict:
        """A new token set from the refresh token, stored sealed; the caller gets the result.

        `mcp_auth_required` only when the authorization is gone — there is none to refresh, or the
        token endpoint refused the refresh token or the client (`_refresh_failure`). An authorization
        server that could not be reached, did not answer in time or failed is its own runtime refusal
        (`mcp_unreachable`, `mcp_timeout`, `mcp_server_error`, `mcp_protocol_error`, …, `cause`
        "token_refresh"): the person's authorization still stands, and asking them for it again would
        not help."""
        flow = self.store.one("SELECT id, token_endpoint, client_id, resource, issuer FROM mcp_oauth_flows WHERE connection_id=? "
                              "AND outcome='ok' ORDER BY completed_at DESC LIMIT 1", (conn["id"],))
        if not flow or not tokens.get("refresh_token"):
            raise ServiceError(401, "mcp_auth_required", "需要重新认证这个连接")
        # The refresh token was issued to the flow's client at the flow's issuer; a registration
        # stored since (another authorization server's) is not that client.
        client_id = flow["client_id"]
        form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id,
                "resource": flow["resource"]}
        headers = self._auth_headers(conn, client_id, flow["issuer"])
        try:
            fresh = self.runtime.run(self._token_request(conn, flow["token_endpoint"], form, headers),
                                     timeout=DISCOVERY_TIMEOUT_S * 2)
        except ServiceError as err:
            raise _refresh_failure(err) from None
        if not fresh.get("refresh_token"):
            fresh["refresh_token"] = tokens["refresh_token"]
        # Tied to the authorization the refresh token came from: a change that voided it meanwhile
        # (its flows go) is not undone by a newer authorization of the connection.
        self.connections.store_tokens(conn["id"], fresh, flow_id=flow["id"])
        return fresh


def _issuer_changed(issuer: str, bound: str) -> ServiceError:
    """The connection's server now names `issuer`, and the person's own client belongs to `bound`
    (MCP authorization, "Authorization Server Binding": surface an error rather than use it there)."""
    return ServiceError(409, "mcp_oauth_issuer_changed",
                        "MCP 服务现在指向另一个授权服务器；连接里登记的客户端属于原来的授权服务器，不会交给新的。请在新的授权服务器登记客户端，再更新连接的 client_id",
                        {"issuer": issuer, "registered_issuer": bound})


# ---------------------------------------------------------------------- refresh failures
_REFRESH_MESSAGES = {"mcp_unreachable": "刷新令牌时无法连接授权服务器",
                     "mcp_unresolvable": "刷新令牌时无法解析授权服务器地址",
                     "mcp_timeout": "授权服务器没有在限定时间内完成令牌刷新",
                     "mcp_response_too_large": "授权服务器刷新令牌的响应超过允许的大小"}


def _refresh_failure(err: ServiceError) -> ServiceError:
    """What a refresh's token request that failed with `err` is to whoever needed the token. The
    token endpoint refusing the grant or the client (RFC 6749 §5.2) ends the authorization:
    `mcp_auth_required`. Anything else is not the person's to fix by authorizing again, and keeps a
    runtime code of its own: the authorization server unreachable, slow or failing, or an answer that
    is not a token response."""
    detail = {**(err.detail or {}), "cause": "token_refresh"}
    if err.code != "mcp_oauth_token_failed":
        return ServiceError(err.status, err.code, _REFRESH_MESSAGES.get(err.code, err.message), detail)
    status = detail.get("http_status")
    if status == 401 or (status == 400 and detail.get("error") in REFRESH_REFUSALS):
        return ServiceError(401, "mcp_auth_required", "连接的令牌无法刷新，需要重新认证", detail)
    if isinstance(status, int) and status >= 500:
        return ServiceError(502, "mcp_server_error", "授权服务器刷新令牌时出错", {**detail, "retryable": True})
    if status == 429:
        return ServiceError(503, "mcp_rate_limited", "授权服务器暂时限制了令牌刷新", {**detail, "retryable": True})
    # Not a token response, or a refusal of the request itself (invalid_request, unsupported_grant_type,
    # another status): the exchange went wrong, not the authorization.
    return ServiceError(502, "mcp_protocol_error", "授权服务器返回了无效的令牌刷新响应", {**detail, "retryable": False})


# ---------------------------------------------------------------------- step-up
def record_scope_challenge(store, conn: dict, scope: str) -> str | None:
    """Keep the scope an MCP server's `insufficient_scope` refusal named (runtime.challenged_scope) for
    the connection's next authorization (OutboundOAuth._requested_scope) — MCP authorization, "Step-Up
    Authorization Flow": the union of what was asked for before and what the challenge names.

    Kept on the connection's OAuth client record (`oauth_client_json`, under `step_up`), which a new
    address or another authentication forgets with the rest of the old authorization; and only while
    the connection still points where the refused exchange went (_CURRENT). At most `MAX_STEP_UPS`
    challenges widen it, within runtime.MAX_SCOPE_TOKENS / MAX_SCOPE_CHARS, so a server cannot have
    the Agent ask the person for ever more. Returns what became of the challenge: "requested" (the
    next authorization asks for it), "already_requested" (the last authorization asked for all of it:
    authorizing again may not change the server's answer), "capped", or None when nothing was kept."""
    tokens = scope_tokens(scope)
    if not tokens:
        return None
    row = store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=? AND " + _CURRENT, (conn["id"], conn["url"]))
    if row is None:
        return None
    last = store.one("SELECT client_id, issuer, scope FROM mcp_oauth_flows WHERE connection_id=? AND outcome='ok' "
                     "ORDER BY completed_at DESC LIMIT 1", (conn["id"],))
    stored = _stored(row)
    if not (stored and stored.get("client_id")):
        # A Client ID Metadata Document is no registration, and nothing of it was stored: the step-up
        # is kept with a record of that client (which the connection's page names as such).
        if not last or not is_deployment_client_id(last["client_id"]):
            return None
        stored = {"kind": "cimd", "client_id": last["client_id"], "issuer": last["issuer"]}
    step = stored.get("step_up") if isinstance(stored.get("step_up"), dict) else {}
    held = scope_tokens(step.get("scope")) or []
    asked = (scope_tokens(last["scope"]) or []) if last else []
    new = [t for t in tokens if t not in held and t not in asked]
    if not new:
        return "already_requested" if all(t in asked for t in tokens) else "requested"
    count = step.get("count") if isinstance(step.get("count"), int) else 0
    union = held + new
    if count >= MAX_STEP_UPS or len(union) > MAX_SCOPE_TOKENS or len(" ".join(union)) > MAX_SCOPE_CHARS:
        log.info("mcp step-up of connection %s not recorded: its limit is reached", conn["id"])
        return "capped"
    record = {**stored, "step_up": {"scope": " ".join(union), "count": count + 1, "at": now()}}
    # Conditional on the record read above: a registration or a new client stored meanwhile is not
    # overwritten with the old one.
    if not store.execute("UPDATE mcp_connections SET oauth_client_json=? WHERE id=? AND oauth_client_json IS ? AND " + _CURRENT,
                         (json.dumps(record), conn["id"], row["oauth_client_json"], conn["url"])):
        return None
    return "requested"
