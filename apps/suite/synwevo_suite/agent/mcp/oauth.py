"""Authorizing a connection to an external MCP server that requires OAuth (the Agent as an MCP client).

The flow is the MCP authorization specification's, driven from the server side of Synwevo because
the browser must never hold the external server's credentials: the connection's owner clicks
"authenticate", this module discovers the server's authorization server (RFC 9728 protected
resource metadata from the 401 challenge or the well-known path, then RFC 8414 / OpenID Connect
discovery), obtains a client id (pre-registered by the person, else this platform's Client ID
Metadata Document when the authorization server accepts them, else dynamic registration when it
offers it), and sends the person's browser to the authorization endpoint with PKCE, `state` and
the `resource` of the MCP server. The callback (on the workplace origin, in the person's signed-in
session) checks `state` and `iss` (RFC 9207), exchanges the code at the token endpoint — pinned,
without redirects — and seals the token set on the connection. Refresh happens the same way when a
token is about to expire.

Every outbound request goes through the runtime's pinned client (policy-checked destination, the
hostname kept for TLS). Nothing from the remote server is trusted beyond its protocol role: its
metadata is validated with the SDK's models, its issuer must match where the metadata was read.
"""
from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx2
from mcp.client.auth.oauth2 import PKCEParameters
from mcp.client.auth.utils import (build_oauth_authorization_server_metadata_discovery_urls,
                                   build_protected_resource_metadata_discovery_urls, extract_resource_metadata_from_www_auth,
                                   extract_scope_from_www_auth, validate_metadata_issuer)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from pydantic import AnyUrl, ValidationError

from synwevo_core import env, sealed
from synwevo_core.auth import audit_event
from synwevo_core.errors import ServiceError
from synwevo_core.http import MCP_CLIENT_METADATA_PATH
from synwevo_core.store import new_id, now

log = logging.getLogger("synwevo.mcp.client")

FLOW_TTL_S = 600
DISCOVERY_TIMEOUT_S = 20.0
CALLBACK_PATH = "/api/suite/agent/mcp/oauth/callback"
CLIENT_NAME = "Synwevo Agent"
SEAL = "mcp-credential"


def app_url() -> str:
    return (env.get("APP_URL") or "").strip().rstrip("/")


def client_metadata_document() -> dict:
    """What this platform tells an external authorization server about the Agent as a client."""
    base = app_url()
    return {"client_id": base + MCP_CLIENT_METADATA_PATH, "client_name": CLIENT_NAME, "client_uri": base or None,
            "redirect_uris": [base + CALLBACK_PATH], "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none", "application_type": "web"}


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
    async def _discover(self, conn: dict) -> dict:
        """The authorization server of the MCP server at `conn['url']`, and the scope it asks for."""
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
        prm = None
        for url in build_protected_resource_metadata_discovery_urls(prm_hint, conn["url"]):
            prm = await self._get_json(url, owner, ProtectedResourceMetadata)
            if prm is not None:
                break
        if prm is not None:
            if not prm.authorization_servers:
                raise ServiceError(502, "mcp_oauth_discovery_failed", "MCP 服务的资源元数据没有指明授权服务器")
            auth_server = str(prm.authorization_servers[0]).rstrip("/")
            resource = str(prm.resource)
        else:
            # Servers of the 2025-03-26 revision had no resource metadata: their authorization
            # server lives at their own origin.
            auth_server, resource = None, conn["url"]
        meta = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server, conn["url"]):
            meta = await self._get_json(url, owner, OAuthMetadata)
            if meta is not None:
                if auth_server:
                    validate_metadata_issuer(meta, auth_server)
                break
        if meta is None:
            raise ServiceError(502, "mcp_oauth_discovery_failed", "找不到 MCP 服务的授权服务器元数据")
        if "S256" not in (meta.code_challenge_methods_supported or ["S256"]):
            raise ServiceError(502, "mcp_oauth_unsupported", "授权服务器不支持 PKCE S256")
        scope = scope_hint or (" ".join(prm.scopes_supported) if prm is not None and prm.scopes_supported else None)
        if "offline_access" in (meta.scopes_supported or []) and "offline_access" not in (scope or "").split():
            # MCP authorization, "Refresh Tokens": a client MAY ask for offline_access when the
            # authorization server lists it. Without a refresh token the connection would need the
            # person's authorization again each time the access token expires.
            scope = " ".join(filter(None, [scope, "offline_access"]))
        return {"metadata": meta, "resource": resource, "scope": scope}

    async def _get_json(self, url: str, owner: str, model):
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
            return model.model_validate(resp.json())
        except (ValueError, ValidationError):
            return None

    # ------------------------------------------------------------------ registration
    async def _client(self, conn: dict, meta: OAuthMetadata) -> dict:
        """The client id to use at this authorization server, in the specification's priority:
        pre-registered, Client ID Metadata Document, dynamic registration."""
        stored = json.loads(conn["oauth_client_json"]) if conn.get("oauth_client_json") else None
        issuer = str(meta.issuer).rstrip("/")
        if stored and stored.get("kind") == "preregistered" and stored.get("client_id"):
            return {"kind": "preregistered", "client_id": stored["client_id"], "issuer": issuer,
                    "confidential": bool(conn.get("oauth_client_secret_sealed"))}
        if stored and stored.get("kind") == "dynamic" and stored.get("client_id") and stored.get("issuer") == issuer:
            return {**stored, "confidential": bool(conn.get("oauth_client_secret_sealed"))}
        document = client_metadata_document()
        if meta.client_id_metadata_document_supported and document["client_id"].startswith("https://"):
            return {"kind": "cimd", "client_id": document["client_id"], "issuer": issuer, "confidential": False}
        if meta.registration_endpoint:
            metadata = OAuthClientMetadata(client_name=CLIENT_NAME, redirect_uris=[AnyUrl(document["redirect_uris"][0])],
                                           grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                           token_endpoint_auth_method="none", scope=None)
            payload = metadata.model_dump(by_alias=True, mode="json", exclude_none=True)
            payload["application_type"] = "web"
            async with self.runtime.http(str(meta.registration_endpoint), conn["owner_id"], read_timeout=DISCOVERY_TIMEOUT_S) as http:
                resp = await http.post(str(meta.registration_endpoint), json=payload, headers={"Content-Type": "application/json"})
            if resp.status_code not in (200, 201):
                raise ServiceError(502, "mcp_oauth_registration_failed", "授权服务器拒绝了客户端登记",
                                   {"http_status": resp.status_code})
            try:
                body = resp.json()
                client_id = body["client_id"]
            except (ValueError, KeyError, TypeError):
                raise ServiceError(502, "mcp_oauth_registration_failed", "授权服务器返回了无效的登记结果") from None
            secret = body.get("client_secret") if isinstance(body.get("client_secret"), str) else None
            record = {"kind": "dynamic", "client_id": client_id, "issuer": issuer,
                      "registered_at": now(), "registration_endpoint": str(meta.registration_endpoint)}
            self.store.execute("UPDATE mcp_connections SET oauth_client_json=?, oauth_client_secret_sealed=?, updated_at=? WHERE id=?",
                               (json.dumps(record), sealed.seal(self.store, SEAL, secret) if secret else None, now(), conn["id"]))
            return {**record, "confidential": bool(secret)}
        raise ServiceError(409, "mcp_oauth_registration_unavailable",
                           "这个授权服务器不接受自描述登记也没有动态登记：请先在对方处登记客户端，再在连接里填写 client_id",
                           {"issuer": issuer})

    # ------------------------------------------------------------------ the flow
    def start(self, user: dict, cid: str) -> dict:
        self.connections.require_outbound()
        conn = self.connections._row(cid)
        self.connections._manage(user["id"], conn)
        if conn["auth_kind"] != "oauth":
            raise ServiceError(409, "mcp_auth_kind_mismatch", "这个连接不使用 OAuth 认证")
        if not app_url():
            raise ServiceError(503, "mcp_oauth_unavailable", "本部署未配置工作空间地址，无法完成回调")
        found = self.runtime.run(self._discover(conn), timeout=DISCOVERY_TIMEOUT_S * 3)
        meta: OAuthMetadata = found["metadata"]
        client = self.runtime.run(self._client(conn, meta), timeout=DISCOVERY_TIMEOUT_S * 2)
        pkce = PKCEParameters.generate()
        state = secrets.token_urlsafe(32)
        redirect_uri = app_url() + CALLBACK_PATH
        t = now()
        self.store.execute("DELETE FROM mcp_oauth_flows WHERE connection_id=? AND completed_at IS NULL", (cid,))
        self.store.execute(
            "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, authorization_endpoint, "
            "token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, expires_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("mcpflow"), cid, user["id"], state, sealed.seal(self.store, SEAL, pkce.code_verifier), str(meta.issuer).rstrip("/"),
             str(meta.authorization_endpoint), str(meta.token_endpoint), client["client_id"], found["scope"], found["resource"],
             redirect_uri, int(bool(meta.authorization_response_iss_parameter_supported)), t, t + FLOW_TTL_S))
        params = {"response_type": "code", "client_id": client["client_id"], "redirect_uri": redirect_uri, "state": state,
                  "code_challenge": pkce.code_challenge, "code_challenge_method": "S256", "resource": found["resource"]}
        if found["scope"]:
            params["scope"] = found["scope"]
        sep = "&" if urlsplit(str(meta.authorization_endpoint)).query else "?"
        audit_event(self.store, "suite.agent.mcp.oauth.start", actor_kind="user", actor_id=user["id"],
                    workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                    object_kind="mcp_connection", object_id=cid,
                    meta={"issuer": str(meta.issuer), "registration": client["kind"]})
        return {"authorization_url": f"{meta.authorization_endpoint}{sep}{urlencode(params)}", "issuer": str(meta.issuer),
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
        # RFC 9207 §2.4: a present `iss` must be the issuer the metadata named; an absent one is
        # refused when the server said it would send it.
        if iss is not None and iss != flow["issuer"]:
            self._close(flow, "issuer_mismatch")
            raise ServiceError(400, "mcp_oauth_issuer_mismatch", "回调的 iss 与授权服务器不一致，已拒绝")
        if iss is None and flow["iss_supported"]:
            self._close(flow, "issuer_missing")
            raise ServiceError(400, "mcp_oauth_issuer_mismatch", "回调缺少 iss，已拒绝")
        if error:
            self._close(flow, f"error:{error}"[:80])
            raise ServiceError(400, "mcp_oauth_denied", "授权服务器拒绝了这次认证", {"error": error[:80]})
        if not code:
            raise ServiceError(400, "mcp_oauth_code_missing", "回调缺少授权码")
        conn = self.connections._row(cid)
        verifier = sealed.unseal(self.store, SEAL, flow["code_verifier_sealed"])
        tokens = self.runtime.run(self._exchange(conn, flow, code, verifier), timeout=DISCOVERY_TIMEOUT_S * 2)
        with self.store.tx():
            self.connections.store_tokens(cid, tokens)
            self._close(flow, "ok")
            audit_event(self.store, "suite.agent.mcp.oauth.complete", actor_kind="user", actor_id=user["id"],
                        workspace_id=conn["owner_id"] if conn["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid, meta={"issuer": flow["issuer"], "scope": tokens.get("scope")})
        try:
            self.connections.test(user, cid, origin="discover")
        except ServiceError as err:
            log.info("post-authorization discovery of connection %s failed: %s", cid, err.code)
        return {"connection_id": cid, "ok": True}

    def _close(self, flow: dict, outcome: str) -> None:
        self.store.execute("UPDATE mcp_oauth_flows SET completed_at=?, outcome=?, code_verifier_sealed='' WHERE id=?",
                           (now(), outcome, flow["id"]))

    def _auth_headers(self, conn: dict, client_id: str) -> dict:
        if conn.get("oauth_client_secret_sealed"):
            secret = sealed.unseal(self.store, SEAL, conn["oauth_client_secret_sealed"])
            raw = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
            return {"Authorization": f"Basic {raw}"}
        return {}

    async def _exchange(self, conn: dict, flow: dict, code: str, verifier: str) -> dict:
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": flow["redirect_uri"],
                "client_id": flow["client_id"], "code_verifier": verifier, "resource": flow["resource"]}
        return await self._token_request(conn, flow["token_endpoint"], form, self._auth_headers(conn, flow["client_id"]))

    async def _token_request(self, conn: dict, endpoint: str, form: dict, headers: dict) -> dict:
        async with self.runtime.http(endpoint, conn["owner_id"], read_timeout=DISCOVERY_TIMEOUT_S) as http:
            resp = await http.post(endpoint, data=form, headers={"Accept": "application/json", **headers})
        if resp.status_code != 200:
            detail = {"http_status": resp.status_code}
            try:
                body = resp.json()
                if isinstance(body, dict) and isinstance(body.get("error"), str):
                    detail["error"] = body["error"][:80]
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
        """A new token set from the refresh token, stored sealed; the caller gets the result."""
        stored = json.loads(conn["oauth_client_json"]) if conn.get("oauth_client_json") else None
        flow = self.store.one("SELECT token_endpoint, client_id, resource FROM mcp_oauth_flows WHERE connection_id=? AND outcome='ok' "
                              "ORDER BY completed_at DESC LIMIT 1", (conn["id"],))
        if not flow or not tokens.get("refresh_token"):
            raise ServiceError(401, "mcp_auth_required", "需要重新认证这个连接")
        client_id = (stored or {}).get("client_id") or flow["client_id"]
        form = {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id,
                "resource": flow["resource"]}
        try:
            fresh = self.runtime.run(self._token_request(conn, flow["token_endpoint"], form, self._auth_headers(conn, client_id)),
                                     timeout=DISCOVERY_TIMEOUT_S * 2)
        except ServiceError as err:
            raise ServiceError(401, "mcp_auth_required", "连接的令牌无法刷新，需要重新认证", {"cause": err.code}) from None
        if not fresh.get("refresh_token"):
            fresh["refresh_token"] = tokens["refresh_token"]
        self.connections.store_tokens(conn["id"], fresh)
        return fresh
