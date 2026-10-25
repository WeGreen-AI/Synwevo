"""The Agent's outbound OAuth to an MCP server that requires it, end to end, on the composed deployment.

The external server is Synwevo's own MCP endpoint, which is a real OAuth-protected MCP server: the
Agent's connection discovers its authorization server from the 401 challenge and the RFC 9728
metadata, reads the RFC 8414 metadata, uses a client id the person entered (pre-registered at that
authorization server), sends the person's browser to the authorization endpoint with PKCE, `state`
and `resource`, receives the callback in the person's session, checks `state` and `iss`, exchanges
the code, seals the token set, and then discovers the server's tools with it. Expiry is then forced
and the next use refreshes the token.

FIXTURE: the runtime reaches the deployment through an in-process ASGI transport
(`McpRuntime.TEST_TRANSPORT_FOR`) instead of the network; the person's browser is a TestClient.
"""
from __future__ import annotations

import importlib
import json
import urllib.parse

import httpx2
import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc, sealed
from synwevo_core.identity import Identity
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store
from synwevo_suite.agent.mcp.runtime import McpRuntime

BASE = "http://localhost"
CALLBACK = BASE + "/api/suite/agent/mcp/oauth/callback"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for key, value in {"SYNWEVO_DATA_DIR": str(tmp_path / "d"), "SYNWEVO_UI_AUTH": "session", "SYNWEVO_COOKIE_SECURE": "0",
                       "SYNWEVO_ISSUER": BASE, "SYNWEVO_APP_URL": BASE, "SYNWEVO_SUITE_APPS": "all"}.items():
        monkeypatch.setenv(key, value)
    # Set before the application is built: the runtime takes the seam when it is constructed.
    holder: dict = {}
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", staticmethod(lambda url: httpx2.ASGITransport(app=holder["api"])))
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = holder["api"] = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", "a-sufficiently-long-passphrase", status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    with store.tx():
        # a loopback deployment: plain http and the loopback address are allowed by the policy, as an
        # operator would allow them for a private deployment (the default is https and public only).
        # The external server here is this deployment's own endpoint, which the Agent refuses as a
        # destination (mcp_destination_is_self) unless the operator lists that host explicitly.
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "inbound_enabled": True, "outbound_enabled": True,
                                 "allow_http": True, "allow_private_networks": ["localhost", "127.0.0.1"],
                                 "allowed_hosts": [urllib.parse.urlsplit(BASE).hostname]},
                                expected_revision=0, actor_id=alice["id"], reason="fixture: open both directions")
    # The client id the person enters in the connection: registered at the authorization server for
    # the Agent's callback and the MCP server's audience, as an operator would.
    oidc.Clients(store).register(client_id="agent-outbound", app_id="platform", name="Synwevo Agent (fixture registration)",
                                 redirect_uris=[CALLBACK], scopes="openid offline_access forms.read forms.write",
                                 audiences=[oidc.mcp_resource()])
    browser = TestClient(api, base_url=BASE)
    raw, _ = Sessions(store).create(alice["id"], workspace_id=ws, amr="password")
    browser.cookies.set(COOKIE_NAME, raw)
    browser.cookies.set(CSRF_COOKIE, "fixture-csrf")
    return {"api": api, "store": store, "alice": alice, "ws": ws, "browser": browser}


def op(c, name, body, expect=200):
    r = c.post(f"/api/suite/ops/{name}", json={"input": body}, headers={CSRF_HEADER: "fixture-csrf"})
    assert r.status_code == expect, (name, r.status_code, r.text)
    return r.json()["output"] if expect == 200 else r.json()


def authorize_as_the_person(env, authorization_url: str) -> str:
    """What the person's browser does with the address the connection page sends it to; returns the
    page the callback lands on."""
    b = env["browser"]
    parsed = urllib.parse.urlparse(authorization_url)
    q = dict(urllib.parse.parse_qsl(parsed.query))
    assert (q["client_id"], q["redirect_uri"], q["code_challenge_method"], q["resource"]) == (
        "agent-outbound", CALLBACK, "S256", oidc.mcp_resource())
    parked = b.get(parsed.path + "?" + parsed.query, follow_redirects=False)
    assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent"), parked.text
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
    shown = b.get(f"/api/auth/oauth-consent/{rid}").json()
    body = {"workspace_id": env["ws"]} if shown["workspace_choices"] else {}
    decided = b.post(f"/api/auth/oauth-consent/{rid}/approve", json=body, headers={CSRF_HEADER: b.cookies.get(CSRF_COOKIE) or ""})
    assert decided.status_code == 200, decided.text
    back = urllib.parse.urlparse(decided.json()["redirect"])
    assert BASE + back.path == CALLBACK and dict(urllib.parse.parse_qsl(back.query))["iss"] == BASE
    landed = b.get(back.path + "?" + back.query, follow_redirects=False)
    assert landed.status_code == 303, landed.text
    return landed.headers["location"]


def test_the_agent_authorizes_a_connection_through_oauth_and_refreshes_its_token(env):
    c = env["browser"]
    created = op(c, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Synwevo itself",
                                                    "url": oidc.mcp_resource(), "auth_kind": "oauth",
                                                    "oauth_client": {"client_id": "agent-outbound"}})
    assert created["health"] == "auth_required" and created["has_credential"] is False
    # Testing before authorizing says what is missing, and makes no request.
    assert op(c, "agent.mcp.connection.test", {"connection_id": created["id"]}, expect=409)["error"] == "mcp_auth_required"

    started = op(c, "agent.mcp.oauth.start", {"connection_id": created["id"]})
    assert started["issuer"].rstrip("/") == BASE and started["registration"] == "preregistered"
    landed = authorize_as_the_person(env, started["authorization_url"])
    assert landed == f"/agent/integrations/{created['id']}?auth=ok", landed

    detail = op(c, "agent.mcp.connection.get", {"connection_id": created["id"]})
    assert detail["has_credential"] is True and detail["health"] == "ok", detail["last_error"]
    assert detail["protocol_version"] == "2026-07-28" and detail["server_info"]["name"] == "Synwevo"
    assert {"synwevo.whoami", "forms.form.list", "forms.form.create"} <= {t["name"] for t in detail["tools"]}
    # The token set is sealed; nothing of it appears in what the page or the record shows.
    row = env["store"].one("SELECT credential_sealed, credential_hint FROM mcp_connections WHERE id=?", (created["id"],))
    tokens = json.loads(sealed.unseal(env["store"], "mcp-credential", row["credential_sealed"]))
    assert tokens["access_token"] and tokens["refresh_token"] and set(tokens["scope"].split()) >= {"forms.read", "forms.write"}
    assert tokens["access_token"] not in json.dumps(detail) and row["credential_hint"] == "oauth"
    flow = env["store"].one("SELECT outcome, code_verifier_sealed FROM mcp_oauth_flows WHERE connection_id=?", (created["id"],))
    assert flow["outcome"] == "ok" and flow["code_verifier_sealed"] == ""     # the verifier is gone once used
    # The token is the MCP server's own: audience is its URL, workspace the one the person chose.
    issued = env["store"].one("SELECT audience, workspace_id, client_id FROM oauth_tokens WHERE access_hash=?",
                              (__import__("hashlib").sha256(tokens["access_token"].encode()).hexdigest(),))
    assert issued == {"audience": oidc.mcp_resource(), "workspace_id": env["ws"], "client_id": "agent-outbound"}

    # The access token expires: the next use refreshes it and keeps working.
    expired = {**tokens, "expires_at": 1.0}
    env["store"].execute("UPDATE mcp_connections SET credential_sealed=? WHERE id=?",
                         (sealed.seal(env["store"], "mcp-credential", json.dumps(expired)), created["id"]))
    again = op(c, "agent.mcp.connection.refresh", {"connection_id": created["id"]})
    assert again["health"] == "ok"
    fresh = json.loads(sealed.unseal(env["store"], "mcp-credential", env["store"].one(
        "SELECT credential_sealed FROM mcp_connections WHERE id=?", (created["id"],))["credential_sealed"]))
    assert fresh["access_token"] != tokens["access_token"] and fresh["refresh_token"] != tokens["refresh_token"]
    assert fresh["expires_at"] > 1.0

    # Revoking the credential on the connection: it needs authentication again, and says so.
    revoked = op(c, "agent.mcp.credential.revoke", {"connection_id": created["id"]})
    assert revoked["health"] == "auth_required" and revoked["has_credential"] is False


def test_a_callback_that_does_not_match_the_flow_is_refused(env):
    c = env["browser"]
    created = op(c, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Synwevo itself", "url": oidc.mcp_resource(),
                                                    "auth_kind": "oauth", "oauth_client": {"client_id": "agent-outbound"}})
    started = op(c, "agent.mcp.oauth.start", {"connection_id": created["id"]})
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(started["authorization_url"]).query))["state"]
    # an unknown state, a mismatched issuer, an error from the authorization server: never a token
    for query, code in (({"state": "not-the-flow", "code": "x"}, "mcp_oauth_flow_not_found"),
                        ({"state": state, "code": "x", "iss": "https://evil.example.test"}, "mcp_oauth_issuer_mismatch")):
        landed = c.get("/api/suite/agent/mcp/oauth/callback?" + urllib.parse.urlencode(query), follow_redirects=False)
        assert landed.status_code == 303 and f"code={code}" in landed.headers["location"], landed.headers["location"]
    assert env["store"].one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (created["id"],))["credential_sealed"] is None
    # the flow whose issuer did not match is closed: the same state cannot be used again
    reused = c.get("/api/suite/agent/mcp/oauth/callback?" + urllib.parse.urlencode({"state": state, "code": "x", "iss": BASE}),
                   follow_redirects=False)
    assert "code=mcp_oauth_flow_decided" in reused.headers["location"]


def test_the_deployments_own_endpoint_is_an_external_server_only_when_the_operator_lists_it(env):
    c, store = env["browser"], env["store"]
    body = {"owner_kind": "user", "name": "Synwevo itself", "url": oidc.mcp_resource(), "auth_kind": "oauth",
            "oauth_client": {"client_id": "agent-outbound"}}
    listed = op(c, "agent.mcp.connection.create", body)                      # the fixture policy names the host
    with store.tx():
        state = McpPolicy(store).read()
        McpPolicy(store).append({**state["spec"], "allowed_hosts": None}, expected_revision=state["revision"],
                                actor_id=env["alice"]["id"], reason="fixture: any public host, nothing named")
    # Saving refuses it now, and so does every use of the connection saved before.
    assert op(c, "agent.mcp.connection.create", {**body, "name": "Synwevo again"}, expect=403)["error"] == "mcp_destination_is_self"
    assert op(c, "agent.mcp.oauth.start", {"connection_id": listed["id"]}, expect=403)["error"] == "mcp_destination_is_self"
