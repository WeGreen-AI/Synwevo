"""The connection service's own guarantees (synwevo_suite.agent.mcp.connections): a new address is a
new server, and an answer from the old one is not recorded for it; what a server publishes is stored
bounded, item by item and as a whole, and a tool whose schema is too large, or that the offer has no
more room for, is unusable; management does not reveal what an outsider cannot see, health and the circuit breaker
move only on the server's failures and never because one member alone kept failing, an OAuth token
is refreshed once however many callers need it, tokens of an authorization or refresh that a change
overtook are not sealed, interrupted call records are settled, the carrier
that runs without asking runs only what is granted to run unasked, and this deployment's own address
is not an external server.

TEST DOUBLES, each labelled: the external MCP server is the FIXTURE server of test_agent_mcp.py
(the official SDK's `MCPServer`, in process through an ASGI transport in place of the pinned network
transport), wrapped here by a FIXTURE transport that notes each request's host and Authorization
header; the OAuth token endpoint is a FIXTURE token server that rotates refresh tokens and refuses a
reused one, as OAuth 2.1 requires of public clients.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
from types import SimpleNamespace

import httpx2
import pydantic
import pytest
from mcp_types import ToolAnnotations

from synwevo_core import sealed
from synwevo_core.capabilities import MAX_OUTPUT_BYTES
from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.store import new_id, now
from synwevo_core.suite_schema.mcp import STALE_RUNNING_S
from synwevo_suite.agent.mcp.connections import MAX_OFFER_BYTES, MAX_SCHEMA_CHARS, SERVER_FAILURES, UNUSABLE_SCHEMA, Connections
from synwevo_suite.agent.mcp.runtime import Discovery, McpRuntime
from synwevo_suite.agent.mcp.tools import REGISTERED_CALL, McpTools, McpVirtualOp

from .conftest import People, WORKPLACE, make_client, sign_in
from .test_agent import op
from .test_agent_mcp import FIXTURE_URL, LazyFixtureApp, connect, fixture_server

TOKEN_PATH = "/oauth/token"


class RecordingTransport(httpx2.AsyncBaseTransport):
    """FIXTURE: the in-process transport, noting where each request went and what credential it carried."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, log: list, token_server=None):
        self.inner, self.log, self.token_server = inner, log, token_server

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.log.append({"host": request.url.host, "path": request.url.path, "authorization": request.headers.get("authorization")})
        if self.token_server is not None and request.url.path == TOKEN_PATH:
            return await self.token_server(request)
        return await self.inner.handle_async_request(request)


class RotatingTokenServer:
    """FIXTURE token endpoint: each refresh token works once and is replaced (rotation); a reused one
    is refused with `invalid_grant`. A short pause lets concurrent exchanges overlap."""

    def __init__(self, current: str):
        self.current, self.n, self.exchanges = current, 1, []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        form = dict(urllib.parse.parse_qsl((await request.aread()).decode()))
        self.exchanges.append(form.get("refresh_token"))
        await asyncio.sleep(0.3)
        if form.get("grant_type") != "refresh_token" or form.get("refresh_token") != self.current:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        self.n += 1
        self.current = f"fixture-refresh-{self.n}"
        return httpx2.Response(200, json={"access_token": f"fixture-access-{self.n}", "refresh_token": self.current,
                                          "token_type": "Bearer", "expires_in": 3600})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    server = fixture_server()
    state = {"app": LazyFixtureApp(server), "server": server, "requests": [], "tokens": None}
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", staticmethod(
        lambda url: RecordingTransport(httpx2.ASGITransport(app=state["app"]), state["requests"], state["tokens"])))
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "test-double-key-not-a-real-credential")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", "test-double-model")
    api = make_client(tmp_path, monkeypatch)
    people = People(api)
    with people.store.tx():
        McpPolicy(people.store).append({**McpPolicy.default().model_dump(), "outbound_enabled": True, "call_timeout_s": 5},
                                       expected_revision=0, actor_id=people.uid("alice"), reason="fixture: open outbound MCP")
    from fastapi.testclient import TestClient
    with TestClient(api, base_url=WORKPLACE) as c:
        c.api, c.people, c.state = api, people, state
        c.store = people.store
        c.connections = api.state.mcp_connections
        c.sign_in = lambda key: sign_in(c, key)
        yield c


def publish(c, **changes):
    store = c.store
    with store.tx():
        state = McpPolicy(store).read()
        McpPolicy(store).append({**state["spec"], **changes}, expected_revision=state["revision"],
                                actor_id=c.people.uid("alice"), reason="fixture: policy for this test")


def row(c, cid):
    return c.store.one("SELECT * FROM mcp_connections WHERE id=?", (cid,))


def user(c, key):
    return c.people.users[key]


def fixture_oauth_connection(c, *, name="Fixture OAuth", secret="fixture-client-secret-value", expires_in=3600.0,
                             refresh="fixture-refresh-1") -> str:
    """An OAuth connection as it stands after the person authorized it: a pre-registered client (with a
    secret), a completed flow (FIXTURE row, as oauth.callback leaves it) and a sealed token set."""
    c.sign_in("alice")
    created = op(c, "agent.mcp.connection.create", {
        "owner_kind": "user", "name": name, "url": FIXTURE_URL, "auth_kind": "oauth",
        "oauth_client": {"client_id": "fixture-client", "client_secret": secret}})["output"]
    cid, t = created["id"], now()
    for outcome, completed in (("ok", t), (None, None)):
        c.store.execute(
            "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, authorization_endpoint, "
            "token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, expires_at, completed_at, outcome) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("mcpflow"), cid, c.people.uid("alice"), new_id("fixturestate"), "", "https://fixture-notes.test/",
             "https://fixture-notes.test/oauth/authorize", "https://fixture-notes.test" + TOKEN_PATH, "fixture-client", None,
             FIXTURE_URL, WORKPLACE + "/api/suite/agent/mcp/oauth/callback", 0, t, t + 600, completed, outcome))
    c.connections.store_tokens(cid, {"access_token": "fixture-access-1", "refresh_token": refresh, "token_type": "Bearer",
                                     "expires_at": t + expires_in})
    return cid


def sealed_tokens(c, cid) -> dict:
    return json.loads(sealed.unseal(c.store, "mcp-credential", row(c, cid)["credential_sealed"]))


# ---------------------------------------------------------------- a new address is a new server
def test_a_new_address_forgets_the_credential_grants_and_bindings_of_the_old_server(world):
    a = world.people.a
    world.people.ident.add_member(a, world.people.uid("oscar"), "admin")      # a second administrator of A
    world.sign_in("alice")
    created = op(world, "agent.mcp.connection.create", {
        "owner_kind": "workspace", "workspace_id": a, "name": "Team notes", "url": FIXTURE_URL,
        "auth_kind": "bearer", "credential": "old-server-secret-token-1234"})["output"]
    cid = created["id"]
    op(world, "agent.mcp.connection.test", {"connection_id": cid})
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True, "resources_enabled": True})
    world.sign_in("bob")
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert [t["tool"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]] == ["echo"]

    # Another administrator, who never saw alice's token, points the connection somewhere else.
    world.sign_in("oscar")
    moved = op(world, "agent.mcp.connection.update", {"connection_id": cid, "url": "https://other-server.test/mcp"})["output"]
    assert moved["host"] == "other-server.test"
    assert (moved["has_credential"], moved["credential_hint"], moved["health"]) == (False, None, "auth_required")
    assert moved["counts"]["granted"] == 0 and moved["oauth_client"] is None
    r = row(world, cid)
    assert r["credential_sealed"] is None and r["oauth_client_secret_sealed"] is None
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_tool_grants WHERE connection_id=?", (cid,))["n"] == 0
    # Every person's Agent stops using it until that person turns it on again for the new server.
    bindings = world.store.all("SELECT user_id, enabled, resources_enabled, alias FROM mcp_agent_bindings WHERE connection_id=?", (cid,))
    assert sorted((b["enabled"], b["resources_enabled"]) for b in bindings) == [(0, 0), (0, 0)]
    world.sign_in("bob")
    assert op(world, "agent.mcp.binding.list", {})["output"]["tools"] == []
    audit = world.store.one("SELECT meta FROM audit_events WHERE action='suite.agent.mcp.connection.update' ORDER BY at DESC, rowid DESC LIMIT 1")
    meta = audit["meta"] if isinstance(audit["meta"], dict) else json.loads(audit["meta"])
    assert meta["credential_forgotten"] is True and "old-server-secret" not in json.dumps(meta)

    # Testing now says what is missing and sends nothing anywhere — least of all the old token.
    world.sign_in("oscar")
    world.state["requests"].clear()
    assert op(world, "agent.mcp.connection.test", {"connection_id": cid}, expect=409)["error"] == "mcp_auth_required"
    assert world.state["requests"] == []
    # The new server gets the credential given for it, and only that one.
    op(world, "agent.mcp.connection.update", {"connection_id": cid, "credential": "new-server-token-5678"})
    detail = op(world, "agent.mcp.connection.test", {"connection_id": cid})["output"]
    assert detail["health"] == "ok" and detail["counts"]["granted"] == 0          # nothing granted for the new server yet
    sent = {(q["host"], q["authorization"]) for q in world.state["requests"]}
    assert sent == {("other-server.test", "Bearer new-server-token-5678")}, sent


def test_a_personal_connection_pointed_elsewhere_gets_a_first_look_at_every_tool(world):
    detail = connect(world)
    cid = detail["id"]
    assert {t["grant"]["mode"] for t in detail["tools"]} == {"confirm"}          # the first look at the first server
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "fail", "mode": "off"})
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    op(world, "agent.mcp.connection.update", {"connection_id": cid, "url": "https://other-server.test/mcp"})
    # The first look is the first successful one: a test the policy refuses first does not use it up.
    publish(world, blocked_hosts=["other-server.test"])
    op(world, "agent.mcp.connection.test", {"connection_id": cid}, expect=403)
    publish(world, blocked_hosts=[])
    again = op(world, "agent.mcp.connection.test", {"connection_id": cid})["output"]
    # The new server's tools are all treated alike, whatever the old server called its own: each one
    # asks before it runs — the old 'auto' is not carried over to a same-named tool, nor the old 'off'.
    modes = {t["name"]: t["grant"]["mode"] for t in again["tools"] if not t["removed"]}
    assert modes == {"echo": "confirm", "add_note": "confirm", "slow": "confirm", "fail": "confirm"}, modes
    # Nothing enters the Agent until the person turns the binding on again for the new server.
    assert op(world, "agent.mcp.binding.list", {})["output"]["tools"] == []
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert {t["mode"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]} == {"confirm"}


def test_a_new_address_forgets_the_oauth_client_token_set_and_flows(world):
    cid = fixture_oauth_connection(world)
    before = row(world, cid)
    assert before["credential_sealed"] and before["oauth_client_secret_sealed"] and before["oauth_client_json"]
    moved = op(world, "agent.mcp.connection.update", {"connection_id": cid, "url": "https://other-server.test/mcp"})["output"]
    assert (moved["has_credential"], moved["oauth_client"], moved["health"]) == (False, None, "auth_required")
    r = row(world, cid)
    assert (r["credential_sealed"], r["oauth_client_json"], r["oauth_client_secret_sealed"]) == (None, None, None)
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_oauth_flows WHERE connection_id=?", (cid,))["n"] == 0
    # A callback of an authorization started for the old server cannot seal its tokens here any more.
    with pytest.raises(Exception) as refused:
        world.connections.store_tokens(cid, {"access_token": "late", "refresh_token": "late", "expires_at": now() + 60})
    assert getattr(refused.value, "code", None) == "mcp_connection_changed"
    # The same change may give the new server its own client.
    again = op(world, "agent.mcp.connection.update", {"connection_id": cid, "url": FIXTURE_URL,
                                                      "oauth_client": {"client_id": "new-client"}})["output"]
    assert again["oauth_client"] == {"kind": "preregistered", "client_id": "new-client"} and again["health"] == "auth_required"
    assert row(world, cid)["oauth_client_secret_sealed"] is None


def test_an_answer_from_the_old_address_is_not_recorded_for_the_new_one(world, monkeypatch):
    detail = connect(world)                                         # alice's own server, her first look taken
    cid = detail["id"]
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    real, alice = world.connections.runtime.discover, user(world, "alice")

    def answered_then_moved(conn, credential, **kwargs):
        # FIXTURE: the old server answers while someone points the connection elsewhere.
        found = real(conn, credential, **kwargs)
        world.connections.update(alice, cid, url="https://other-server.test/mcp")
        return found
    monkeypatch.setattr(world.connections.runtime, "discover", answered_then_moved)
    refused = op(world, "agent.mcp.connection.refresh", {"connection_id": cid}, expect=409)
    assert refused["error"] == "mcp_address_changed", refused
    # Nothing of the old server's answer stands for the new one: no offer, no health, no first look used up.
    r = row(world, cid)
    assert (r["url"], r["discovered_at"], r["health"], r["server_info_json"]) == ("https://other-server.test/mcp", None, "unknown", None)
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_connection_tools WHERE connection_id=? AND removed_at IS NULL",
                           (cid,))["n"] == 0
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_tool_grants WHERE connection_id=?", (cid,))["n"] == 0
    last = world.store.one("SELECT status, error_code FROM mcp_calls WHERE connection_id=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
                           (cid,))
    assert (last["status"], last["error_code"]) == ("failed", "mcp_address_changed")

    # Nor is a failure of the old server's exchange a verdict on the server the connection now names.
    def failed_after_moving(conn, credential, **kwargs):
        world.connections.update(alice, cid, url="https://third-server.test/mcp")    # FIXTURE: moved meanwhile
        raise ServiceError(502, "mcp_server_error", "fixture: the old server failed")
    monkeypatch.setattr(world.connections.runtime, "discover", failed_after_moving)
    op(world, "agent.mcp.connection.refresh", {"connection_id": cid}, expect=502)
    r = row(world, cid)
    assert (r["health"], r["failure_streak"], r["last_error_code"]) == ("unknown", 0, None)

    # The first answer from the server it now points at is the first look: every tool asks before it runs.
    monkeypatch.setattr(world.connections.runtime, "discover", real)
    again = op(world, "agent.mcp.connection.test", {"connection_id": cid})["output"]
    assert again["health"] == "ok"
    modes = {t["name"]: t["grant"]["mode"] for t in again["tools"] if not t["removed"]}
    assert modes == {"echo": "confirm", "add_note": "confirm", "slow": "confirm", "fail": "confirm"}, modes


# ---------------------------------------------------------------- what a server may store
def hostile_tools(server) -> None:
    """FIXTURE: what a hostile (or careless) server publishes beside the fixture's own tools — an
    input schema and an output schema far beyond what may be kept, and a usable tool whose schema is
    in Chinese but whose title, description and annotation title run on for thousands of characters."""
    padding = pydantic.create_model("Padding", **{f"field_{i}": (str, pydantic.Field("", description="padding " * 40))
                                                  for i in range(80)})

    def bloated(payload) -> str:
        return "never runs"
    bloated.__annotations__ = {"payload": padding, "return": str}
    server.tool(name="bloated", description="An enormous input schema.", annotations=ToolAnnotations(read_only_hint=True))(bloated)

    def bloated_out() -> dict:
        return {}
    bloated_out.__annotations__ = {"return": padding}
    server.tool(name="bloated_out", description="An enormous output schema.",
                annotations=ToolAnnotations(read_only_hint=True))(bloated_out)
    chinese = pydantic.create_model("Chinese", **{f"字段{i}": (str, pydantic.Field("", description="中文说明" * 50)) for i in range(25)})

    def wordy(payload) -> str:
        return "ok"
    wordy.__annotations__ = {"payload": chinese, "return": str}
    server.tool(name="wordy", title="标题" * 3000, description="说明" * 25_000,
                annotations=ToolAnnotations(read_only_hint=True, title="注" * 5000))(wordy)


def test_a_tool_whose_schema_is_too_large_is_recorded_as_unusable_and_reaches_no_agent(world):
    hostile_tools(world.state["server"])
    detail = connect(world)                                         # alice's own server: her first look grants 'confirm'
    cid = detail["id"]
    tools = {t["name"]: t for t in detail["tools"]}
    for name in ("bloated", "bloated_out"):
        t = tools[name]
        # The page says why, and has no schema to show.
        assert (t["unusable"]["code"], t["unusable"]["max_chars"]) == ("mcp_schema_too_large", MAX_SCHEMA_CHARS), t
        assert str(MAX_SCHEMA_CHARS) in t["unusable"]["message"] and t["input_schema"] is None and t["output_schema"] is None
        assert t["grant"]["mode"] == "off"                          # the first look does not grant what cannot be used
    assert {tools[n]["grant"]["mode"] for n in ("echo", "add_note", "wordy")} == {"confirm"}
    assert tools["echo"]["unusable"] is None
    # Nothing of the oversized schemas is in the database: the row holds a placeholder.
    stored = {r["name"]: r for r in world.store.all(
        "SELECT name, title, description, input_schema_json, output_schema_json, annotations_json, unusable_code "
        "FROM mcp_connection_tools WHERE connection_id=?", (cid,))}
    for name in ("bloated", "bloated_out"):
        assert stored[name]["unusable_code"] == "mcp_schema_too_large"
        assert json.loads(stored[name]["input_schema_json"]) == UNUSABLE_SCHEMA and stored[name]["output_schema_json"] is None
    assert max(sum(len(v) for v in r.values() if isinstance(v, str)) for r in stored.values()) < MAX_SCHEMA_CHARS + 5_000
    # A usable tool keeps bounded text; its Chinese schema is measured as stored, not as \u escapes.
    wordy = stored["wordy"]
    assert wordy["unusable_code"] is None and (len(wordy["title"]), len(wordy["description"])) == (200, 4_000)
    assert json.loads(wordy["annotations_json"]) == {"readOnlyHint": True, "title": "注" * 200}
    assert len(json.dumps(json.loads(wordy["input_schema_json"]))) > MAX_SCHEMA_CHARS >= len(wordy["input_schema_json"])
    # Nobody can grant an unusable tool to run; switching it off stays possible.
    for mode in ("confirm", "auto"):
        refused = op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "bloated", "mode": mode}, expect=409)
        assert refused["error"] == "mcp_tool_unusable" and str(MAX_SCHEMA_CHARS) in refused["message"], refused
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "bloated", "mode": "off"})
    # FIXTURE: a grant written before the bound existed, for this very shape, still reaches no Agent.
    world.store.execute("INSERT OR REPLACE INTO mcp_tool_grants (connection_id, tool, mode, schema_hash, granted_by, granted_at, "
                        "updated_at) SELECT connection_id, name, 'auto', schema_hash, ?, ?, ? FROM mcp_connection_tools "
                        "WHERE connection_id=? AND name='bloated'", (world.people.uid("alice"), now(), now(), cid))
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    listed = {t["tool"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]}
    assert {"echo", "add_note", "wordy"} <= listed and not {"bloated", "bloated_out"} & listed, listed
    assert op(world, "agent.mcp.connection.get", {"connection_id": cid})["output"]["counts"]["granted"] == len(listed)
    world.state["requests"].clear()
    with pytest.raises(Exception) as unavailable:
        world.connections.call_tool(user(world, "alice"), "fixture-notes", "bloated", {"payload": {}})
    assert unavailable.value.code == "mcp_tool_unavailable" and world.state["requests"] == []
    # Were the placeholder ever handed to the Agent, checking an input against it is a tool error.
    agent = McpTools(SimpleNamespace(store=None), {"id": "fixture-user"})          # FIXTURE: checking an input reads no store
    vop = McpVirtualOp(id="mcp:fixture-notes/bloated", alias="fixture-notes", tool="bloated", connection_name="Fixture notes",
                       title={}, summary="", side_effect="external", agent="confirm", registered=REGISTERED_CALL,
                       schema=json.loads(stored["bloated"]["input_schema_json"]))
    assert agent.prepare(vop, {"payload": {}})["code"] == "mcp_schema_invalid"


def test_a_tool_that_shrinks_back_is_usable_again_but_not_granted_by_it(world):
    server = world.state["server"]
    hostile_tools(server)
    detail = connect(world)
    cid = detail["id"]
    server.remove_tool("bloated")

    @server.tool(name="bloated", description="Now a reasonable schema.", annotations=ToolAnnotations(read_only_hint=True))
    def bloated(text: str) -> str:
        return text
    again = {t["name"]: t for t in op(world, "agent.mcp.connection.refresh", {"connection_id": cid})["output"]["tools"]}
    assert again["bloated"]["unusable"] is None and again["bloated"]["input_schema"]["properties"]["text"]["type"] == "string"
    assert again["bloated"]["grant"]["mode"] == "off"               # tools that turn up later are not enabled by it
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "bloated", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert world.connections.call_tool(user(world, "alice"), "fixture-notes", "bloated", {"text": "back"})["text"] == "back"
    assert row(world, cid)["health"] == "ok"


def test_what_a_server_says_of_itself_and_its_offer_is_stored_bounded(world, monkeypatch):
    # FIXTURE: the discovery a hostile server's answers would make (built here, since the SDK's own
    # models already refuse some of these shapes on the wire).
    found = Discovery(
        protocol_version="2025-11-25",
        server_info={"name": "n" * 5_000, "version": "1.0", "websiteUrl": "https://fixture-notes.test/",
                     "icons": [{"src": "data:image/png;base64," + "A" * 100_000}]},
        capabilities={"tools": {"listChanged": True}, "resources": {"subscribe": True, "padding": "P" * 5_000},
                      "experimental": {"fixture": {"blob": "B" * 100_000}}},
        instructions="I" * 10_000,
        tools=[{"name": "odd", "inputSchema": ["not", "an", "object"], "description": "D" * 10_000,
                "annotations": {"readOnlyHint": "yes", "destructiveHint": False, "padding": "E" * 10_000}}],
        resources=[{"uri": "notes://big", "name": "r" * 5_000, "title": "t" * 5_000, "description": "d" * 10_000,
                    "mimeType": "m" * 5_000}],
        prompts=[{"name": "long", "title": "t" * 5_000, "description": "d" * 10_000,
                  "arguments": [{"name": "x" * 500}] + [{"name": f"a{i}", "description": "x" * 5_000, "required": i == 0}
                                                         for i in range(500)]}])
    monkeypatch.setattr(world.connections.runtime, "discover", lambda *args, **kwargs: found)
    detail = connect(world)
    cid = detail["id"]
    assert detail["server_info"] == {"name": "n" * 200, "version": "1.0", "websiteUrl": "https://fixture-notes.test/"}
    assert detail["capabilities"] == {"tools": {"listChanged": True}, "resources": {}}
    assert len(detail["instructions"]) == 4_000
    odd = next(t for t in detail["tools"] if t["name"] == "odd")
    assert odd["unusable"]["code"] == "mcp_schema_invalid" and odd["grant"]["mode"] == "off"
    assert odd["annotations"] == {"destructiveHint": False} and len(odd["description"]) == 4_000
    (res,) = detail["resources"]
    assert (len(res["name"]), len(res["title"]), len(res["description"]), len(res["mime_type"])) == (200, 200, 2_000, 200)
    (prompt,) = detail["prompts"]
    assert (len(prompt["title"]), len(prompt["description"])) == (200, 2_000)
    args = prompt["arguments"]
    assert len(args) == 50 and args[0] == {"name": "a0", "required": True, "description": "x" * 500}
    assert all(len(a["description"]) == 500 and set(a) == {"name", "required", "description"} for a in args)
    r = row(world, cid)
    assert len(r["server_info_json"]) < 1_000 and len(r["capabilities_json"]) < 1_000


def fixture_offer(*, lead: bool = False) -> Discovery:
    """FIXTURE: the discovery a large server's answer makes, built in place of one: sixty tools whose
    input schemas each stay within MAX_SCHEMA_CHARS — in Chinese, three bytes a character as stored —
    then a small tool, then resources and prompts with long texts. `lead` puts one more such tool first."""
    def big(name: str) -> dict:
        return {"name": name, "description": "说明" * 100, "annotations": {"readOnlyHint": True},
                "inputSchema": {"type": "object", "properties": {"text": {"type": "string", "description": "说" * (MAX_SCHEMA_CHARS - 200)}}}}
    tools = ([big("lead_0")] if lead else []) + [big(f"big_{i:02d}") for i in range(60)]
    tools.append({"name": "small", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}})
    resources = [{"uri": f"notes://r{i:02d}", "name": f"r{i:02d}", "title": "资源", "description": "资" * 2_000,
                  "mimeType": "text/plain", "size": 10} for i in range(100)]
    prompts = [{"name": f"p{i:02d}", "title": "提示", "description": "提" * 2_000,
                "arguments": [{"name": f"a{j}", "description": "参" * 500, "required": j == 0} for j in range(50)]} for i in range(20)]
    return Discovery(protocol_version="2025-11-25", server_info={"name": "fixture-large"}, capabilities={"tools": {}},
                     instructions=None, tools=tools, resources=resources, prompts=prompts)


def stored_offer_bytes(c, cid) -> int:
    """The UTF-8 size of what the connection's live offer stores."""
    total = 0
    for sql in ("SELECT name, title, description, input_schema_json, output_schema_json, annotations_json FROM mcp_connection_tools",
                "SELECT uri, name, title, description, mime_type FROM mcp_connection_resources",
                "SELECT name, title, description, arguments_json FROM mcp_connection_prompts"):
        for r in c.store.all(sql + " WHERE connection_id=? AND removed_at IS NULL", (cid,)):
            total += sum(len(v.encode("utf-8")) for v in r.values() if isinstance(v, str))
    return total


def test_an_offer_too_large_to_keep_whole_is_kept_within_bounds_and_its_page_says_why(world, monkeypatch):
    found = {"offer": fixture_offer()}
    monkeypatch.setattr(world.connections.runtime, "discover", lambda *args, **kwargs: found["offer"])
    world.sign_in("alice")
    cid = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Fixture notes", "url": FIXTURE_URL})["output"]["id"]
    # The page opens: it is one operation's answer, and the offer it shows was kept within its room.
    for name in ("agent.mcp.connection.test", "agent.mcp.connection.get"):
        r = world.post(f"/api/suite/ops/{name}", json={"input": {"connection_id": cid}}, headers={"X-Synwevo-CSRF": "fixture-csrf"})
        assert r.status_code == 200 and len(r.content) <= MAX_OUTPUT_BYTES, (name, r.status_code, len(r.content), r.text[:200])
    detail = r.json()["output"]
    assert stored_offer_bytes(world, cid) <= MAX_OFFER_BYTES + 64 * 1024
    tools = {t["name"]: t for t in detail["tools"]}
    big = [f"big_{i:02d}" for i in range(60)]
    kept = [n for n in big if tools[n]["unusable"] is None]
    # The server's own order decides: the first tools are kept whole and asked about on the first look;
    # a later one that still fits (the small tool) is kept too.
    assert 0 < len(kept) < len(big) and kept == big[:len(kept)], kept
    assert tools["small"]["unusable"] is None and {tools[n]["grant"]["mode"] for n in kept + ["small"]} == {"confirm"}
    assert tools[kept[0]]["input_schema"]["properties"]["text"]["description"].startswith("说")
    for n in big[len(kept):]:
        t = tools[n]
        # The rest keep their name and hints only, and the page says why each cannot be granted.
        assert (t["unusable"]["code"], t["unusable"]["max_bytes"]) == ("mcp_offer_too_large", MAX_OFFER_BYTES), t["unusable"]
        assert f"{MAX_OFFER_BYTES // 1024} KB" in t["unusable"]["message"]
        assert (t["input_schema"], t["output_schema"], t["title"], t["description"]) == (None, None, None, "")
        assert t["annotations"] == {"readOnlyHint": True} and t["grant"]["mode"] == "off"
    refused = op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": big[-1], "mode": "confirm"}, expect=409)
    assert refused["error"] == "mcp_tool_unusable" and refused["detail"]["reason"] == "mcp_offer_too_large", refused
    # Resources keep what reading them needs, prompts what rendering them needs.
    resources = {x["uri"]: x for x in detail["resources"]}
    assert len(resources) == 100 and all(x["name"] == x["uri"][len("notes://"):] and x["size"] == 10 for x in resources.values())
    assert all((x["title"], x["description"], x["mime_type"]) in (("资源", "资" * 2_000, "text/plain"), (None, "", None))
               for x in resources.values())
    assert (resources["notes://r99"]["description"], resources["notes://r99"]["mime_type"]) == ("", None)
    prompts = {x["name"]: x for x in detail["prompts"]}
    assert len(prompts) == 20
    assert all(x["arguments"] == [{"name": f"a{j}", "required": j == 0} for j in range(50)] and x["description"] == ""
               for x in prompts.values())
    # Only what was kept whole reaches the Agent.
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert {t["tool"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]} == set(kept) | {"small"}

    # Room in the offer is not part of a tool's shape: pushed out by a tool the server lists first, the
    # last kept tool is unusable for as long as that lasts, and back under the grant it had afterwards.
    found["offer"] = fixture_offer(lead=True)
    pushed = {t["name"]: t for t in op(world, "agent.mcp.connection.refresh", {"connection_id": cid})["output"]["tools"]}
    assert pushed[kept[-1]]["unusable"]["code"] == "mcp_offer_too_large" and pushed["lead_0"]["grant"]["mode"] == "off"
    assert kept[-1] not in {t["tool"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]}
    found["offer"] = fixture_offer()
    back = {t["name"]: t for t in op(world, "agent.mcp.connection.refresh", {"connection_id": cid})["output"]["tools"]}
    assert back[kept[-1]]["unusable"] is None and back[kept[-1]]["grant"] == {**back[kept[-1]]["grant"], "mode": "confirm", "stale": False}
    assert {t["tool"] for t in op(world, "agent.mcp.binding.list", {})["output"]["tools"]} == set(kept) | {"small"}


def test_a_resource_size_that_cannot_be_stored_is_not_kept(world, monkeypatch):
    # FIXTURE: sizes a hostile server's answer could announce (built in place of the answer).
    found = Discovery(protocol_version="2025-11-25", server_info={"name": "fixture"}, capabilities={"resources": {}},
                      instructions=None, tools=[{"name": "echo", "inputSchema": {"type": "object"}}],
                      resources=[{"uri": "notes://huge", "name": "huge", "size": 10 ** 30}, {"uri": "notes://negative", "size": -1},
                                 {"uri": "notes://flag", "size": True}, {"uri": "notes://text", "size": "12"},
                                 {"uri": "notes://fine", "size": 42}])
    monkeypatch.setattr(world.connections.runtime, "discover", lambda *args, **kwargs: found)
    detail = connect(world)
    assert detail["health"] == "ok"
    assert {x["uri"]: x["size"] for x in detail["resources"]} == {"notes://huge": None, "notes://negative": None, "notes://flag": None,
                                                                  "notes://text": None, "notes://fine": 42}


# ---------------------------------------------------------------- what outsiders learn
def test_managing_a_connection_one_cannot_see_answers_as_if_it_did_not_exist(world):
    mine = connect(world)                                           # alice's personal connection
    shared = connect(world, "alice", owner_kind="workspace", workspace_id=world.people.a, name="Team notes")
    missing = "mcpc_" + "0" * 12
    attempts = [("agent.mcp.connection.delete", {}), ("agent.mcp.connection.disable", {}), ("agent.mcp.connection.enable", {}),
                ("agent.mcp.connection.update", {"name": "Mine now"}), ("agent.mcp.credential.revoke", {}),
                ("agent.mcp.grant.set", {"tool": "echo", "mode": "confirm"}), ("agent.mcp.connection.test", {}),
                ("agent.mcp.oauth.start", {})]
    for who, cid in (("bob", mine["id"]), ("oscar", shared["id"]), ("out", shared["id"])):
        world.sign_in(who)
        for name, extra in attempts:
            seen = op(world, name, {"connection_id": cid, **extra}, expect=404)
            absent = op(world, name, {"connection_id": missing, **extra}, expect=404)
            assert seen["error"] == absent["error"] == "mcp_connection_not_found", (who, name, seen, absent)
    # A member who may use the connection but not manage it is told why.
    world.sign_in("vera")
    for name, extra in attempts:
        assert op(world, name, {"connection_id": shared["id"], **extra}, expect=403)["error"] == "mcp_manage_forbidden", name
    assert row(world, mine["id"])["deleted_at"] is None and row(world, shared["id"])["status"] == "active"


# ---------------------------------------------------------------- health and the circuit breaker
def test_local_refusals_leave_health_and_the_breaker_alone(world):
    publish(world, max_concurrent_calls_per_connection=1)
    detail = connect(world)
    cid = detail["id"]
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    alice = user(world, "alice")
    slow = threading.Thread(target=lambda: world.connections.call_tool(alice, "fixture-notes", "slow", {"seconds": 3}))
    slow.start()
    end = time.time() + 5
    while not world.store.one("SELECT 1 FROM mcp_calls WHERE connection_id=? AND name='slow' AND status='running'", (cid,)):
        assert time.time() < end, "the slow call never started"
        time.sleep(0.01)
    time.sleep(0.5)                 # admitted by now: it holds the connection's only slot for three seconds
    # The connection is at its cap: each further call is refused here, before any request leaves.
    for _ in range(7):
        with pytest.raises(Exception) as busy:
            world.connections.call_tool(alice, "fixture-notes", "echo", {"text": "x"})
        assert busy.value.code == "mcp_connection_busy"
    r = row(world, cid)
    assert (r["failure_streak"], r["open_until"], r["health"]) == (0, None, "ok"), dict(r)
    slow.join(10)
    # A destination the policy now refuses is the platform's decision, not the server's failure.
    publish(world, blocked_hosts=["fixture-notes.test"])
    for _ in range(6):
        with pytest.raises(Exception) as blocked:
            world.connections.call_tool(alice, "fixture-notes", "echo", {"text": "x"})
        assert blocked.value.code not in SERVER_FAILURES and blocked.value.code != "mcp_circuit_open", blocked.value.code
    r = row(world, cid)
    assert (r["failure_streak"], r["open_until"], r["health"]) == (0, None, "ok"), dict(r)
    calls = world.store.all("SELECT status, error_code FROM mcp_calls WHERE connection_id=? AND name='echo' AND status<>'succeeded'",
                            (cid,))
    assert len(calls) == 13 and all(c["status"] == "failed" for c in calls)     # each refusal is still on record


def test_one_member_alone_cannot_open_a_shared_connections_circuit(world):
    shared = connect(world, "alice", owner_kind="workspace", workspace_id=world.people.a, name="Team notes")
    cid = shared["id"]
    world.sign_in("vera")                                            # a viewer of A: may use, not manage
    for i in range(8):
        failed = op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": f"notes://missing-{i}"}, expect=502)
        assert failed["error"] in SERVER_FAILURES, failed
    r = row(world, cid)
    assert r["failure_streak"] == 8 and r["open_until"] is None and r["health"] == "failed"
    # Everybody else keeps using the connection.
    world.sign_in("bob")
    assert op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": "notes://all"})["output"]["text"] == "(no notes)"
    # Failures two people met, each a real share of them, do open it, for everyone.
    world.sign_in("vera")
    for i in range(3):
        op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": f"notes://missing-again-{i}"}, expect=502)
    world.sign_in("bob")
    for i in range(2):
        op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": f"notes://missing-bob-{i}"}, expect=502)
    assert row(world, cid)["open_until"] is not None
    assert op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": "notes://all"}, expect=503)["error"] == "mcp_circuit_open"
    # An administrator of the connection may stop it by their own failures (they could disable it anyway).
    other = connect(world, "alice", owner_kind="workspace", workspace_id=world.people.a, name="Team notes two")
    for i in range(5):
        op(world, "agent.mcp.resource.read_page", {"connection_id": other["id"], "uri": f"notes://missing-{i}"}, expect=502)
    assert row(world, other["id"])["open_until"] is not None


def test_a_lone_member_neither_tips_a_shared_circuit_nor_keeps_it_open(world):
    """Two people open a shared connection's circuit only when each accounts for a real share of the
    failures (a member counts for at most half the threshold), and an opening starts the count
    again: a viewer can neither ride on someone else's one stray failure nor, each time the window
    ends, stop the connection again for everyone by herself."""
    shared = connect(world, "alice", owner_kind="workspace", workspace_id=world.people.a, name="Team notes")
    cid = shared["id"]

    def fail(who, n, tag):
        world.sign_in(who)
        for i in range(n):
            failed = op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": f"notes://missing-{tag}-{i}"},
                        expect=502)
            assert failed["error"] in SERVER_FAILURES, failed
    fail("bob", 1, "stray")                          # one stray failure of an editor's
    fail("vera", 6, "vera")                          # and a viewer whose reads keep failing
    r = row(world, cid)
    assert r["failure_streak"] == 7 and r["open_until"] is None, dict(r)
    fail("bob", 1, "bob")                            # 2 of bob's and 3 that count of vera's: 5 of 5
    assert row(world, cid)["open_until"] is not None
    # The window ends (FIXTURE: time passes). However often the viewer alone fails now, it stays usable.
    world.store.execute("UPDATE mcp_connections SET open_until=? WHERE id=?", (now() - 1, cid))
    fail("vera", 8, "after")
    r = row(world, cid)
    assert (r["open_until"] or 0) < now() and r["health"] == "failed", dict(r)
    world.sign_in("bob")
    assert op(world, "agent.mcp.resource.read_page", {"connection_id": cid, "uri": "notes://all"})["output"]["text"] == "(no notes)"
    # A manager's one stray failure does not let a viewer stop it either.
    fail("alice", 1, "alice")
    fail("vera", 6, "vera-again")
    assert (row(world, cid)["open_until"] or 0) < now()
    fail("alice", 1, "alice-again")                  # 2 of the owner's and 3 of the viewer's
    assert (row(world, cid)["open_until"] or 0) > now()


# ---------------------------------------------------------------- one refresh for everyone
def test_concurrent_callers_refresh_an_oauth_token_once(world):
    cid = fixture_oauth_connection(world, expires_in=5)            # due: within the 30 s before expiry
    tokens = world.state["tokens"] = RotatingTokenServer("fixture-refresh-1")
    stale = row(world, cid)                                          # each caller holds its own copy of the row
    got, errors = [], []

    def use():
        try:
            got.append(world.connections.credential(dict(stale)))
        except Exception as exc:  # noqa: BLE001 — collected for the assertion
            errors.append(exc)
    threads = [threading.Thread(target=use) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert errors == [] and got == ["fixture-access-2"] * 4, (got, errors)
    assert tokens.exchanges == ["fixture-refresh-1"]                 # the refresh token was spent once
    stored = sealed_tokens(world, cid)
    assert (stored["access_token"], stored["refresh_token"]) == ("fixture-access-2", "fixture-refresh-2")
    r = row(world, cid)
    assert r["refresh_claim_until"] is None and r["health"] != "auth_required" and r["failure_streak"] == 0


def test_a_refresh_under_way_in_another_process_is_waited_for_not_repeated(world):
    cid = fixture_oauth_connection(world, expires_in=-1)           # already expired
    tokens = world.state["tokens"] = RotatingTokenServer("fixture-refresh-1")
    # FIXTURE: another process holds the lease and is mid-exchange.
    world.store.execute("UPDATE mcp_connections SET refresh_claim_until=? WHERE id=?", (now() + 60, cid))
    stale = row(world, cid)

    def other_process_finishes():
        time.sleep(0.5)
        world.connections.store_tokens(cid, {"access_token": "fixture-access-elsewhere", "refresh_token": "fixture-refresh-elsewhere",
                                             "token_type": "Bearer", "expires_at": now() + 3600})
        world.store.execute("UPDATE mcp_connections SET refresh_claim_until=NULL WHERE id=?", (cid,))
    threading.Thread(target=other_process_finishes).start()
    assert world.connections.credential(dict(stale)) == "fixture-access-elsewhere"
    assert tokens.exchanges == []
    # A token that is due but not yet expired still serves while the other process refreshes.
    cid2 = fixture_oauth_connection(world, name="Fixture OAuth two", expires_in=10)
    world.store.execute("UPDATE mcp_connections SET refresh_claim_until=? WHERE id=?", (now() + 60, cid2))
    assert world.connections.credential(row(world, cid2)) == "fixture-access-1" and tokens.exchanges == []


def test_a_refresh_another_process_finished_just_before_the_claim_is_not_repeated(world, monkeypatch):
    cid = fixture_oauth_connection(world, expires_in=5)            # due
    tokens = world.state["tokens"] = RotatingTokenServer("fixture-refresh-1")
    store = world.connections.store
    real_one = store.one
    raced = []

    def one(sql, args=()):
        out = real_one(sql, args)
        if not raced and sql.startswith("SELECT * FROM mcp_connections WHERE id=? AND deleted_at IS NULL") and args == (cid,):
            raced.append(sql)
            # FIXTURE "other process": its whole refresh — claim, exchange, store, release — lands between
            # this caller's read of the row and its own claim.
            tokens.current = "fixture-refresh-elsewhere"
            world.connections.store_tokens(cid, {"access_token": "fixture-access-elsewhere", "token_type": "Bearer",
                                                 "refresh_token": "fixture-refresh-elsewhere", "expires_at": now() + 3600})
        return out
    monkeypatch.setattr(store, "one", one)
    assert world.connections.credential(row(world, cid)) == "fixture-access-elsewhere"
    assert raced and tokens.exchanges == []                          # the spent refresh token was not sent again
    assert sealed_tokens(world, cid)["refresh_token"] == "fixture-refresh-elsewhere"
    assert row(world, cid)["refresh_claim_until"] is None


class OvertakenTokenServer(RotatingTokenServer):
    """FIXTURE token endpoint during whose exchange something else happens to the connection."""

    def __init__(self, current: str, meanwhile):
        super().__init__(current)
        self.meanwhile = meanwhile

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        await asyncio.to_thread(self.meanwhile)
        return await super().__call__(request)


def fixture_flow(c, cid, *, url: str, completed: bool = False) -> str:
    """FIXTURE: an authorization of the connection as oauth.start records it (and oauth.callback closes it)."""
    fid, t = new_id("mcpflow"), now()
    host = urllib.parse.urlsplit(url).hostname
    c.store.execute(
        "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, authorization_endpoint, "
        "token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, expires_at, completed_at, outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fid, cid, c.people.uid("alice"), new_id("fixturestate"), "", f"https://{host}/", f"https://{host}/oauth/authorize",
         f"https://{host}" + TOKEN_PATH, "fixture-client", None, url, WORKPLACE + "/api/suite/agent/mcp/oauth/callback", 0,
         t, t + 600, t if completed else None, "ok" if completed else None))
    return fid


@pytest.mark.parametrize("change", ["address", "revocation"])
def test_a_refresh_overtaken_by_a_new_address_or_a_revocation_cannot_seal_its_tokens(world, change):
    cid = fixture_oauth_connection(world, expires_in=5)            # due
    alice = user(world, "alice")

    def meanwhile():
        if change == "address":
            world.connections.update(alice, cid, url="https://other-server.test/mcp")
            fixture_flow(world, cid, url="https://other-server.test/mcp")    # and the new server's authorization begins
        else:
            world.connections.revoke_credential(alice, cid)
    world.state["tokens"] = OvertakenTokenServer("fixture-refresh-1", meanwhile)
    with pytest.raises(Exception) as refused:
        world.connections.credential(row(world, cid))
    assert refused.value.code == "mcp_auth_required" and refused.value.detail["cause"] == "mcp_connection_changed", refused.value
    r = row(world, cid)
    assert r["credential_sealed"] is None and r["refresh_claim_until"] is None
    if change == "address":
        # Nothing the old server issued reaches the new one.
        world.state["requests"].clear()
        assert op(world, "agent.mcp.connection.test", {"connection_id": cid}, expect=409)["error"] == "mcp_auth_required"
        assert world.state["requests"] == []


def test_tokens_of_an_authorization_the_connection_no_longer_has_are_refused(world):
    cid = fixture_oauth_connection(world)
    old = world.store.one("SELECT id FROM mcp_oauth_flows WHERE connection_id=? AND completed_at IS NULL", (cid,))["id"]
    world.connections.update(user(world, "alice"), cid, url="https://other-server.test/mcp")
    new = fixture_flow(world, cid, url="https://other-server.test/mcp")
    # The old server's code is exchanged only now: the newer flow for the other server does not vouch for it.
    with pytest.raises(Exception) as refused:
        world.connections.store_tokens(cid, {"access_token": "old-server-access", "refresh_token": "r", "expires_at": now() + 60},
                                       flow_id=old)
    assert refused.value.code == "mcp_connection_changed"
    assert row(world, cid)["credential_sealed"] is None
    world.connections.store_tokens(cid, {"access_token": "new-server-access", "refresh_token": "r", "expires_at": now() + 60},
                                   flow_id=new)
    assert sealed_tokens(world, cid)["access_token"] == "new-server-access"


def test_a_failed_refresh_is_an_authentication_refusal_and_sends_nothing(world):
    cid = fixture_oauth_connection(world, expires_in=-1, refresh="fixture-refresh-revoked")
    world.state["tokens"] = RotatingTokenServer("fixture-refresh-1")
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    # The FIXTURE row stands for a connection whose tools were discovered before; tools are recorded by a test.
    world.store.execute("INSERT INTO mcp_connection_tools (connection_id, name, input_schema_json, annotations_json, schema_hash, "
                        "first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
                        (cid, "echo", json.dumps({"type": "object"}), json.dumps({"readOnlyHint": True}), "h", now(), now()))
    world.store.execute("INSERT INTO mcp_tool_grants (connection_id, tool, mode, schema_hash, granted_by, granted_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?)", (cid, "echo", "confirm", "h", world.people.uid("alice"), now(), now()))
    world.state["requests"].clear()
    with pytest.raises(Exception) as refused:
        world.connections.call_tool(user(world, "alice"), "fixture-oauth", "echo", {"text": "x"})
    assert refused.value.code == "mcp_auth_required"
    assert [q["path"] for q in world.state["requests"]] == [TOKEN_PATH]          # no unauthenticated call went out
    r = row(world, cid)
    assert (r["health"], r["failure_streak"], r["open_until"]) == ("auth_required", 0, None)


# ---------------------------------------------------------------- records left running
def test_call_records_left_running_by_a_stopped_process_are_settled_as_unknown(world):
    detail = connect(world)
    cid, t = detail["id"], now()

    def running(started_at):
        call_id = new_id("mcpcall")
        world.store.execute("INSERT INTO mcp_calls (id, connection_id, user_id, origin, kind, name, status, started_at) "
                            "VALUES (?,?,?,?,?,?,?,?)", (call_id, cid, world.people.uid("alice"), "agent", "tools/call", "echo",
                                                         "running", started_at))
        return call_id
    orphan, live = running(t - STALE_RUNNING_S - 60), running(t - 5)        # FIXTURE rows: one long dead, one maybe in flight
    calls = {c["id"]: c for c in op(world, "agent.mcp.calls", {"connection_id": cid})["output"]["items"]}
    assert calls[orphan]["status"] == "unknown" and calls[orphan]["error"]["code"] == "mcp_call_interrupted"
    assert calls[orphan]["finished_at"] is not None
    assert calls[live]["status"] == "running" and calls[live]["error"] is None
    # A process that starts settles what earlier ones left, whatever the connection.
    other = running(t - STALE_RUNNING_S - 1)
    Connections(world.store, world.people.ident, world.connections.runtime)
    assert world.store.one("SELECT status FROM mcp_calls WHERE id=?", (other,))["status"] == "unknown"


def test_a_discovery_that_cannot_be_recorded_still_ends_its_call_record(world, monkeypatch):
    detail = connect(world)
    cid = detail["id"]

    def broken(self, conn, found, *, grant_new, granted_by):
        raise RuntimeError("fixture: the record could not be written")
    monkeypatch.setattr(Connections, "_record_discovery", broken)
    r = world.post("/api/suite/ops/agent.mcp.connection.refresh", json={"input": {"connection_id": cid}},
                   headers={"X-Synwevo-CSRF": "fixture-csrf"})
    assert r.status_code == 500, r.text
    last = world.store.one("SELECT status, error_code FROM mcp_calls WHERE connection_id=? ORDER BY started_at DESC LIMIT 1", (cid,))
    assert (last["status"], last["error_code"]) == ("failed", "mcp_record_failed")
    assert row(world, cid)["health"] == "ok"                                   # not the server's failure


# ---------------------------------------------------------------- the carrier that does not ask (C1)
def test_the_carrier_that_does_not_ask_runs_only_tools_granted_auto_and_marked_read_only(world):
    detail = connect(world)
    cid = detail["id"]
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    alice = user(world, "alice")
    assert world.connections.call_tool(alice, "fixture-notes", "echo", {"text": "ping"}, require_mode="auto")["text"] == "ping"
    before = world.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE origin='agent'")["n"]
    # add_note changes data and is granted 'confirm': never run unasked.
    with pytest.raises(Exception) as refused:
        world.connections.call_tool(alice, "fixture-notes", "add_note", {"text": "x"}, require_mode="auto")
    assert refused.value.code == "mcp_confirmation_required" and world.state["server"].notes == []
    # The person narrowed echo to 'confirm' since: the same refusal.
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "tool_modes": {"echo": "confirm"}})
    with pytest.raises(Exception) as narrowed:
        world.connections.call_tool(alice, "fixture-notes", "echo", {"text": "ping"}, require_mode="auto")
    assert narrowed.value.code == "mcp_confirmation_required"
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE origin='agent'")["n"] == before   # refused before any record
    # The confirmed carrier passes no requirement.
    assert world.connections.call_tool(alice, "fixture-notes", "echo", {"text": "pong"})["text"] == "pong"


# ---------------------------------------------------------------- this deployment is not an external server (C3)
def test_this_deployments_own_address_is_not_an_external_server(world):
    world.sign_in("alice")
    own = urllib.parse.urlsplit(WORKPLACE).hostname
    for url in (f"https://{own}/api/suite/v1/mcp", "https://accounts.testserver/mcp"):
        refused = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Myself", "url": url}, expect=403)
        assert refused["error"] == "mcp_destination_is_self", refused
    existing = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Fixture", "url": FIXTURE_URL})["output"]
    assert op(world, "agent.mcp.connection.update", {"connection_id": existing["id"], "url": f"https://{own}/mcp"},
              expect=403)["error"] == "mcp_destination_is_self"
    # A suffix entry is not an explicit listing; the host itself is.
    publish(world, allowed_hosts=[".testserver", "fixture-notes.test"])
    assert op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Myself", "url": f"https://{own}/mcp"},
              expect=403)["error"] == "mcp_destination_is_self"
    publish(world, allowed_hosts=[own, "fixture-notes.test"])
    made = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Myself", "url": f"https://{own}/mcp"})["output"]
    assert made["host"] == own


# ---------------------------------------------------------------- the schema change is additive
def test_a_database_from_before_gains_the_new_columns_and_keeps_its_rows(tmp_path):
    from synwevo_core.store import Store
    from synwevo_core.suite_schema.mcp import COLUMNS
    store = Store(tmp_path / "d")
    store.execute("INSERT INTO mcp_connections (id, owner_kind, owner_id, slug, name, url, auth_kind, status, created_by, "
                  "created_at, updated_at) VALUES ('mcpc_aaaaaaaaaaaa','user','u_fixture','fixture','Fixture',?, 'none','active',"
                  "'u_fixture',1.0,1.0)", (FIXTURE_URL,))
    store.execute("INSERT INTO mcp_connection_tools (connection_id, name, input_schema_json, schema_hash, first_seen_at, last_seen_at) "
                  "VALUES ('mcpc_aaaaaaaaaaaa','echo','{\"type\": \"object\"}','fixture-hash',1.0,1.0)")
    for table, column, _ in COLUMNS:                     # FIXTURE: the tables as the previous release left them
        store.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    reopened = Store(tmp_path / "d")
    for table in {t for t, _, _ in COLUMNS}:
        have = {r["name"] for r in reopened.all(f"PRAGMA table_info({table})")}
        assert {column for t, column, _ in COLUMNS if t == table} <= have, table
    kept = reopened.one("SELECT * FROM mcp_connections WHERE id='mcpc_aaaaaaaaaaaa'")
    assert kept["name"] == "Fixture" and kept["platform_disabled_at"] is None and kept["refresh_claim_until"] is None
    tool = reopened.one("SELECT * FROM mcp_connection_tools WHERE connection_id='mcpc_aaaaaaaaaaaa'")
    assert tool["name"] == "echo" and tool["unusable_code"] is None          # a tool recorded before stays usable
