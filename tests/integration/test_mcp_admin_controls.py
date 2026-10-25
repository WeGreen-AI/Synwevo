"""A platform administrator's control over the Agent's MCP connections, on the composed deployment
(`synwevo_server.app.create_app`, role `all`): the account service's `/api/admin/mcp` routes against
the connection service of the workplace origin.

* A platform stop holds against the connection's owner: they can neither resume it nor give it a
  credential or a new address, until a platform administrator lifts the stop — and even then the
  connection is the owner's to enable again.
* A stop forgets every credential the connection holds: the token set, the OAuth client secret, a
  dynamic registration, the authorizations in progress (a late callback cannot seal tokens).
* A stopped connection is not authorized again: its server hears nothing from the attempt, and a
  stop that lands while a client is being registered leaves no client secret behind.
* Call records a stopped process left 'running' are settled as 'unknown' in the administration's
  view too.

FIXTURES, each labelled: the external MCP server is built with the official SDK (`MCPServer`) and
served in process through an ASGI transport the runtime is given in place of its pinned network
transport; for the OAuth authorization, a FIXTURE transport (`AuthorizingServer`) plays an MCP
server that requires OAuth and its authorization server; the platform role is granted through
`platform_fixture.make_platform_admin`; the OAuth connection's completed authorization is a FIXTURE
flow row and a sealed token set, as the callback leaves them.
"""
from __future__ import annotations

import asyncio
import importlib
import json

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, new_id, now
from synwevo_core.suite_schema.mcp import STALE_RUNNING_S
from synwevo_suite.agent.mcp.runtime import McpRuntime

from tests.integration.platform_fixture import make_platform_admin

PW = "isolated-mcp-admin-fixture-passphrase"
BASE = "http://testserver"
REASON = "Isolated fixture: abusive MCP server reported"
SERVER_URL = "https://weather.fixture.test/mcp"


def _fixture_server() -> MCPServer:
    """FIXTURE MCP server: one read-only tool."""
    server = MCPServer("Fixture weather")

    @server.tool(name="forecast", description="Tomorrow's forecast for a city.",
                 annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False))
    def forecast(city: str) -> str:
        return f"Sunny in {city}"
    return server


class _LazyApp:
    """The fixture server's ASGI app with its session manager started on first use (an ASGI transport
    runs no lifespan)."""

    def __init__(self, server: MCPServer):
        self.server = server
        self.app = server.streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        self._task = None

    async def __call__(self, scope, receive, send):
        if self._task is None:
            ready, self._stop = asyncio.Event(), asyncio.Event()

            async def serve():
                async with self.server.session_manager.run():
                    ready.set()
                    await self._stop.wait()
            self._task = asyncio.create_task(serve())
            await ready.wait()
        await self.app(scope, receive, send)


class AuthorizingServer(httpx2.AsyncBaseTransport):
    """FIXTURE: an MCP server at SERVER_URL that requires OAuth, its resource metadata, and an
    authorization server at the same origin that offers dynamic registration. Every request is noted;
    `on_register` runs while a registration is being answered (what else happens meanwhile)."""

    ISSUER = "https://weather.fixture.test/"

    def __init__(self, on_register=None):
        self.requests, self.on_register = [], on_register

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append((request.method, request.url.host, request.url.path))
        path, issuer = request.url.path, self.ISSUER
        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx2.Response(200, json={"resource": SERVER_URL, "authorization_servers": [issuer]})
        if path.startswith("/.well-known/oauth-authorization-server"):
            return httpx2.Response(200, json={"issuer": issuer, "authorization_endpoint": issuer + "authorize",
                                              "token_endpoint": issuer + "token", "registration_endpoint": issuer + "register",
                                              "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"]})
        if request.method == "POST" and path == "/register":
            if self.on_register is not None:
                await asyncio.to_thread(self.on_register)
            return httpx2.Response(201, json={"client_id": "fixture-dynamic-client", "client_secret": "fixture-dynamic-secret"})
        return httpx2.Response(401, json={"error": "unauthorized"}, headers={
            "WWW-Authenticate": 'Bearer resource_metadata="https://weather.fixture.test/.well-known/oauth-protected-resource/mcp"'})


def post(client, path, body=None):
    return client.post(path, json=body if body is not None else {}, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def op(client, name, body, expect=200):
    r = post(client, f"/api/suite/ops/{name}", {"input": body})
    assert r.status_code == expect, (name, r.status_code, r.text)
    return r.json()["output"] if expect == 200 else r.json()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    data = tmp_path / "isolated-composition"
    for key, value in {"DATA_DIR": str(data), "UI_AUTH": "session", "COOKIE_SECURE": "0", "MAIL_MODE": "file",
                       "MAIL_DIR": str(tmp_path / "mail"), "ISSUER": BASE, "APP_URL": BASE, "SUITE_APPS": "all"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    lazy = _LazyApp(_fixture_server())
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", staticmethod(lambda url: httpx2.ASGITransport(app=lazy)))
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(data), role="all", start_jobs=False)
    store = Store(data)
    ident = Identity(store)
    users = {}
    for name in ("alice", "root"):
        u = ident.create_user(f"{name}@example.com", PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
        users[name] = make_platform_admin(store, u) if name == "root" else ident.get_user(u["id"])
    with store.tx():
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "outbound_enabled": True}, expected_revision=0,
                                actor_id=users["root"]["id"], reason="fixture: open outbound MCP")
    clients = {}
    for name in users:
        c = TestClient(api, base_url=BASE)
        assert post(c, "/api/auth/login", {"email": users[name]["email"], "password": PW}).status_code == 200
        clients[name] = c
    assert post(clients["root"], "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    yield {"api": api, "store": store, "users": users, "alice": clients["alice"], "root": clients["root"],
           "connections": api.state.mcp_connections}
    for c in clients.values():
        c.close()


def audited(store, action, cid):
    return store.all("SELECT actor_id, meta FROM audit_events WHERE action=? AND object_id=? ORDER BY at", (action, cid))


def test_a_platform_stop_holds_against_the_owner_until_the_platform_lifts_it(env):
    alice, root, store = env["alice"], env["root"], env["store"]
    created = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather", "url": SERVER_URL})
    cid = created["id"]
    assert op(alice, "agent.mcp.connection.test", {"connection_id": cid})["health"] == "ok"
    op(alice, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert [t["tool"] for t in op(alice, "agent.mcp.binding.list", {})["tools"]] == ["forecast"]

    stopped = post(root, f"/api/admin/mcp/connections/{cid}/disable", {"reason": REASON})
    assert stopped.status_code == 200 and stopped.json()["status"] == "disabled", stopped.text

    # The owner cannot undo it: not by enabling, not by a new credential or address.
    assert op(alice, "agent.mcp.connection.enable", {"connection_id": cid}, expect=409)["error"] == "mcp_platform_disabled"
    assert stopped.json() == {"id": cid, "status": "disabled", "platform_disabled": True}
    row = store.one("SELECT status, platform_disabled_at, platform_disabled_by FROM mcp_connections WHERE id=?", (cid,))
    assert row["status"] == "disabled" and row["platform_disabled_at"] and row["platform_disabled_by"] == env["users"]["root"]["id"]
    assert [a["actor_id"] for a in audited(store, "admin.mcp.connection.disable", cid)] == [env["users"]["root"]["id"]]
    assert op(alice, "agent.mcp.connection.update", {"connection_id": cid, "auth_kind": "bearer", "credential": "fixture-token-abcdefgh"},
              expect=409)["error"] == "mcp_platform_disabled"
    assert op(alice, "agent.mcp.connection.update", {"connection_id": cid, "url": "https://weather-two.fixture.test/mcp"},
              expect=409)["error"] == "mcp_platform_disabled"
    assert op(alice, "agent.mcp.connection.test", {"connection_id": cid}, expect=409)["error"] == "mcp_platform_disabled"
    assert op(alice, "agent.mcp.binding.list", {})["tools"] == []
    assert store.one("SELECT status FROM mcp_connections WHERE id=?", (cid,))["status"] == "disabled"
    # What is not a resumption stays the owner's: the name, their own off switch, the record.
    seen = op(alice, "agent.mcp.connection.update", {"connection_id": cid, "name": "Weather (stopped)"})
    assert seen["platform_disabled"] is True and seen["status"] == "disabled"
    assert op(alice, "agent.mcp.connection.disable", {"connection_id": cid})["status"] == "disabled"
    overview = root.get("/api/admin/mcp/overview").json()
    assert next(i for i in overview["items"] if i["id"] == cid)["platform_disabled"] is True

    # Lifting is the platform's alone, dangerous, reasoned and audited.
    assert post(alice, f"/api/admin/mcp/connections/{cid}/lift", {"reason": REASON}).status_code == 404
    assert post(root, f"/api/admin/mcp/connections/{cid}/lift", {}).json()["error"] == "missing_reason"
    lifted = post(root, f"/api/admin/mcp/connections/{cid}/lift", {"reason": REASON})
    assert lifted.status_code == 200, lifted.text
    assert lifted.json() == {"id": cid, "status": "disabled", "platform_disabled": False}
    assert [a["actor_id"] for a in audited(store, "admin.mcp.connection.lift", cid)] == [env["users"]["root"]["id"]]
    assert post(root, f"/api/admin/mcp/connections/{cid}/lift", {"reason": REASON}).json()["error"] == "mcp_not_platform_disabled"
    # The connection is the owner's again: still off until they turn it on.
    assert op(alice, "agent.mcp.binding.list", {})["tools"] == []
    assert op(alice, "agent.mcp.connection.enable", {"connection_id": cid})["status"] == "active"
    assert op(alice, "agent.mcp.connection.test", {"connection_id": cid})["health"] == "ok"
    assert [t["tool"] for t in op(alice, "agent.mcp.binding.list", {})["tools"]] == ["forecast"]


def test_a_platform_stop_forgets_every_credential_the_connection_holds(env):
    alice, root, store, connections = env["alice"], env["root"], env["store"], env["connections"]
    pre = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather OAuth", "url": SERVER_URL,
                                                    "auth_kind": "oauth", "oauth_client": {"client_id": "fixture-client",
                                                                                           "client_secret": "fixture-client-secret"}})
    dyn = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather dynamic", "url": SERVER_URL,
                                                    "auth_kind": "oauth"})
    t = now()
    # FIXTURE: a dynamic registration (with its secret) as oauth._client stores one.
    store.execute("UPDATE mcp_connections SET oauth_client_json=?, oauth_client_secret_sealed=? WHERE id=?",
                  (json.dumps({"kind": "dynamic", "client_id": "fixture-dynamic", "issuer": "https://weather.fixture.test/"}),
                   store.one("SELECT oauth_client_secret_sealed FROM mcp_connections WHERE id=?", (pre["id"],))["oauth_client_secret_sealed"],
                   dyn["id"]))
    for cid in (pre["id"], dyn["id"]):
        # FIXTURE: a completed authorization and one in progress, as oauth.callback / oauth.start leave them.
        for outcome, completed in (("ok", t), (None, None)):
            store.execute(
                "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, authorization_endpoint, "
                "token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, expires_at, completed_at, outcome) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("mcpflow"), cid, env["users"]["alice"]["id"], new_id("fixturestate"), "", "https://weather.fixture.test/",
                 "https://weather.fixture.test/authorize", "https://weather.fixture.test/token", "fixture-client", None, SERVER_URL,
                 BASE + "/api/suite/agent/mcp/oauth/callback", 0, t, t + 600, completed, outcome))
        connections.store_tokens(cid, {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_at": t + 3600})

    for cid in (pre["id"], dyn["id"]):
        assert post(root, f"/api/admin/mcp/connections/{cid}/disable", {"reason": REASON}).status_code == 200
        row = store.one("SELECT * FROM mcp_connections WHERE id=?", (cid,))
        assert (row["credential_sealed"], row["credential_hint"], row["oauth_client_secret_sealed"]) == (None, None, None)
        assert row["health"] == "auth_required"
        assert store.one("SELECT COUNT(*) AS n FROM mcp_oauth_flows WHERE connection_id=?", (cid,))["n"] == 0
        # A callback that arrives late cannot seal its tokens on the stopped connection.
        with pytest.raises(ServiceError) as late:
            connections.store_tokens(cid, {"access_token": "late", "refresh_token": "late", "expires_at": t + 3600})
        assert late.value.code == "mcp_connection_changed"
        assert store.one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (cid,))["credential_sealed"] is None
    # The person's own client id at the other side is no secret and stays, still bound to the server it
    # was authorized at (read from the completed authorization the stop deletes, so it is kept on the
    # record); the dynamic registration goes.
    assert json.loads(store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (pre["id"],))["oauth_client_json"]) == {
        "kind": "preregistered", "client_id": "fixture-client", "issuer": "https://weather.fixture.test/"}
    assert store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (dyn["id"],))["oauth_client_json"] is None
    # Nor can the owner start a new authorization while it is stopped.
    assert op(alice, "agent.mcp.connection.update", {"connection_id": pre["id"], "oauth_client": {"client_id": "other"}},
              expect=409)["error"] == "mcp_platform_disabled"


def oauth_state(store, cid):
    row = store.one("SELECT oauth_client_json, oauth_client_secret_sealed, platform_disabled_at FROM mcp_connections WHERE id=?",
                    (cid,))
    flows = store.one("SELECT COUNT(*) AS n FROM mcp_oauth_flows WHERE connection_id=?", (cid,))["n"]
    return row, flows


def test_a_stopped_connection_is_not_authorized_and_its_server_hears_nothing(env, monkeypatch):
    alice, root, store = env["alice"], env["root"], env["store"]
    server = AuthorizingServer()
    monkeypatch.setattr(env["connections"].runtime, "_transport_for", lambda url: server)
    cid = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather OAuth", "url": SERVER_URL,
                                                    "auth_kind": "oauth"})["id"]
    assert post(root, f"/api/admin/mcp/connections/{cid}/disable", {"reason": REASON}).status_code == 200

    # Authenticating would resume the connection: refused before its server is asked anything —
    # no probe, no metadata, no registration — and nothing is stored.
    assert op(alice, "agent.mcp.oauth.start", {"connection_id": cid}, expect=409)["error"] == "mcp_platform_disabled"
    assert server.requests == []
    row, flows = oauth_state(store, cid)
    assert (row["oauth_client_json"], row["oauth_client_secret_sealed"], flows) == (None, None, 0)
    assert audited(store, "suite.agent.mcp.oauth.start", cid) == []

    # It was the stop that refused: once the platform lifts it, the owner authorizes as before.
    assert post(root, f"/api/admin/mcp/connections/{cid}/lift", {"reason": REASON}).status_code == 200
    assert op(alice, "agent.mcp.oauth.start", {"connection_id": cid})["registration"] == "dynamic"
    assert ("POST", "weather.fixture.test", "/register") in server.requests
    row, flows = oauth_state(store, cid)
    assert json.loads(row["oauth_client_json"])["client_id"] == "fixture-dynamic-client"
    assert row["oauth_client_secret_sealed"] and flows == 1


def test_a_stop_that_lands_during_a_registration_leaves_no_client_secret(env, monkeypatch):
    alice, root, store = env["alice"], env["root"], env["store"]
    cid = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather OAuth", "url": SERVER_URL,
                                                    "auth_kind": "oauth"})["id"]
    stops = []
    # The platform administrator stops the connection while its authorization server is registering the client.
    server = AuthorizingServer(on_register=lambda: stops.append(
        post(root, f"/api/admin/mcp/connections/{cid}/disable", {"reason": REASON}).status_code))
    monkeypatch.setattr(env["connections"].runtime, "_transport_for", lambda url: server)

    assert op(alice, "agent.mcp.oauth.start", {"connection_id": cid}, expect=409)["error"] == "mcp_platform_disabled"
    assert stops == [200] and ("POST", "weather.fixture.test", "/register") in server.requests
    # The registration the other side made is not kept: no client, no secret, no flow on the stopped connection.
    row, flows = oauth_state(store, cid)
    assert row["platform_disabled_at"] and (row["oauth_client_json"], row["oauth_client_secret_sealed"], flows) == (None, None, 0)
    assert audited(store, "suite.agent.mcp.oauth.start", cid) == []


def test_the_administrations_call_log_settles_records_a_stopped_process_left_running(env):
    alice, root, store = env["alice"], env["root"], env["store"]
    cid = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather", "url": SERVER_URL})["id"]
    t = now()
    ids = []
    for started in (t - STALE_RUNNING_S - 30, t - 3):                 # FIXTURE rows: one long dead, one maybe in flight
        ids.append(new_id("mcpcall"))
        store.execute("INSERT INTO mcp_calls (id, connection_id, user_id, origin, kind, name, status, started_at) VALUES (?,?,?,?,?,?,?,?)",
                      (ids[-1], cid, env["users"]["alice"]["id"], "agent", "tools/call", "forecast", "running", started))
    items = {i["id"]: i for i in root.get(f"/api/admin/mcp/connections/{cid}/calls").json()["items"]}
    assert (items[ids[0]]["status"], items[ids[0]]["error_code"]) == ("unknown", "mcp_call_interrupted")
    assert items[ids[1]]["status"] == "running"
