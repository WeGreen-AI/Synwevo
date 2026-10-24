"""MCP both ways through the composed deployment (`synwevo_server.app.create_app`, role `all`).

A. An MCP client (the official SDK's `Client`) authorises at the account service — sign-in, the
   consent page with its workspace choice, the token endpoint with the MCP server's URL as the
   RFC 8707 resource — and then lists and calls a real business operation on the Synwevo MCP server
   over Streamable HTTP, through the one gate the deployment runs.
B. The Agent connects to an external MCP server (a FIXTURE server built with the SDK, served in
   process), and a scripted model (TEST DOUBLE) makes it call a tool after the person confirms.

Nothing is mocked in the protocol: SDK client ↔ Synwevo server, Synwevo client ↔ SDK server.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import urllib.parse

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

from synwevo_core import oidc
from synwevo_core.identity import Identity
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store
from synwevo_suite.agent.mcp.runtime import McpRuntime

pytestmark = pytest.mark.anyio

PW = "a-sufficiently-long-passphrase"
BASE = "http://testserver"
REDIRECT = "http://127.0.0.1:3030/callback"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", BASE)
    monkeypatch.setenv("SYNWEVO_APP_URL", BASE)
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", "all")
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "test-double-key-not-a-real-credential")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", "test-double-model")
    fixture = _fixture_server()
    lazy = _LazyApp(fixture)
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", staticmethod(lambda url: httpx2.ASGITransport(app=lazy)))
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    with store.tx():
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "inbound_enabled": True, "outbound_enabled": True},
                                expected_revision=0, actor_id=alice["id"], reason="fixture: open both directions")
    oidc.Clients(store).register(client_id="mcp-desk", app_id="platform", name="Fixture MCP desktop client",
                                 redirect_uris=[REDIRECT], scopes="openid offline_access forms.read forms.write",
                                 audiences=[oidc.mcp_resource()])
    return {"api": api, "store": store, "ident": ident, "alice": alice, "ws": ws, "fixture": fixture}


def _fixture_server() -> MCPServer:
    server = MCPServer("Fixture weather")
    calls: list[dict] = []

    @server.tool(name="forecast", description="Tomorrow's forecast for a city.",
                 annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True))
    def forecast(city: str) -> str:
        calls.append({"city": city})
        return f"Sunny in {city}, 24°C"
    server.calls = calls
    return server


class _LazyApp:
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


def _authorize_with_browser(env) -> str:
    """What the MCP client's browser step does: sign in, consent (choosing the workspace), get a code
    back on the client's redirect URI; then the client exchanges it for a token for the MCP server."""
    browser = TestClient(env["api"])
    assert browser.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "mcp-desk", "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid offline_access forms.read forms.write",
         "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256", "resource": oidc.mcp_resource()}
    parked = browser.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent"), parked.text
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
    shown = browser.get(f"/api/auth/oauth-consent/{rid}").json()
    assert shown["scopes"] == ["forms.read", "forms.write", "offline_access"] or set(shown["scopes"]) == {"forms.read", "forms.write", "offline_access"}
    body = {"workspace_id": env["ws"]} if shown["workspace_choices"] else {}
    decided = browser.post(f"/api/auth/oauth-consent/{rid}/approve", json=body,
                           headers={CSRF_HEADER: browser.cookies.get(CSRF_COOKIE) or ""})
    assert decided.status_code == 200, decided.text
    back = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(decided.json()["redirect"]).query))
    assert back["state"] == "xyz" and back["iss"] == BASE
    machine = TestClient(env["api"])                                       # the client itself: no cookie
    issued = machine.post("/oidc/token", data={"grant_type": "authorization_code", "code": back["code"], "redirect_uri": REDIRECT,
                                               "client_id": "mcp-desk", "code_verifier": verifier, "resource": oidc.mcp_resource()})
    assert issued.status_code == 200, issued.text
    return issued.json()["access_token"]


async def test_an_mcp_client_authorises_at_the_account_service_and_uses_a_business_operation(env):
    metadata = TestClient(env["api"]).get("/.well-known/oauth-protected-resource/api/suite/v1/mcp").json()
    assert metadata["resource"] == BASE + "/api/suite/v1/mcp" and metadata["authorization_servers"] == [BASE]
    as_meta = TestClient(env["api"]).get("/.well-known/oauth-authorization-server").json()
    assert as_meta["issuer"] == BASE and as_meta["authorization_endpoint"] == BASE + "/oidc/authorize"
    token = _authorize_with_browser(env)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE,
                                  headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
            assert client.protocol_version == "2026-07-28"
            tools = {t.name for t in (await client.list_tools()).tools}
            assert {"forms.form.create", "forms.form.list", "synwevo.whoami"} <= tools and not any(t.startswith("tables.") for t in tools)
            who = (await client.call_tool("synwevo.whoami", {})).structured_content
            assert who["workspace_id"] == env["ws"] and who["client_id"] == "mcp-desk"
            made = (await client.call_tool("forms.form.create", {"title": "Travel request", "idempotency_key": "trip-1"})).structured_content
            assert made["status"] == "succeeded" and made["output"]["title"] == "Travel request"
            listed = (await client.call_tool("forms.form.list", {})).structured_content
            assert [f["title"] for f in listed["output"]["forms"]] == ["Travel request"]
    run = env["store"].one("SELECT via, delegation FROM op_runs WHERE op='forms.form.create'")
    assert run["via"] == "api" and json.loads(run["delegation"])["transport"] == "mcp"
    # withdrawing the consent ends the client's access everywhere, immediately
    consent = env["store"].one("SELECT id FROM oauth_consents WHERE client_id='mcp-desk'")
    browser = TestClient(env["api"])
    assert browser.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    gone = browser.post(f"/api/auth/connected-apps/{consent['id']}/revoke", headers={CSRF_HEADER: browser.cookies.get(CSRF_COOKIE) or ""})
    assert gone.status_code == 200 and gone.json()["tokens_revoked"] >= 1
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE,
                                  headers={"Authorization": f"Bearer {token}"}) as http:
        refused = await http.post(BASE + "/api/suite/v1/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                  headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                           "Mcp-Method": "tools/list"})
        assert refused.status_code == 401 and "resource_metadata" in refused.headers["www-authenticate"]


def _signed_in(env) -> TestClient:
    c = TestClient(env["api"], base_url=BASE)
    raw, _ = Sessions(env["store"]).create(env["alice"]["id"], workspace_id=env["ws"], amr="password")
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "fixture-csrf")
    return c


def _op(c, name, body):
    r = c.post(f"/api/suite/ops/{name}", json={"input": body}, headers={CSRF_HEADER: "fixture-csrf"})
    assert r.status_code == 200, (name, r.status_code, r.text)
    return r.json()["output"]


class ScriptedModel:
    """TEST DOUBLE for the model provider (as in apps/suite/tests/test_agent.py): each step is a
    StepResult replayed in order. The product has no scripted or canned model."""
    name = "test-double"

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def step(self, *, credential, system, tools, messages, effort, on_text, should_stop):
        self.requests.append({"messages": messages, "tools": tools, "system": system})
        if not self.steps:
            raise AssertionError("the scripted model ran out of steps")
        result = self.steps.pop(0)
        for b in result.content:
            if b.get("type") == "text":
                on_text(b["text"])
        return result


def say(text):
    from synwevo_suite.agent.provider import StepResult
    return StepResult(content=[{"type": "text", "text": text}], stop_reason="end_turn", usage={"output_tokens": 5})


def call(tool, args, tid="tu_1"):
    from synwevo_suite.agent.provider import StepResult
    return StepResult(content=[{"type": "tool_use", "id": tid, "name": tool, "input": args}], stop_reason="tool_use",
                      usage={"output_tokens": 7})


def test_the_agent_calls_an_external_mcp_server_the_person_connected(env):
    import time

    c = _signed_in(env)
    created = _op(c, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather", "url": "https://weather.fixture.test/mcp"})
    detail = _op(c, "agent.mcp.connection.test", {"connection_id": created["id"]})
    assert detail["health"] == "ok" and [t["name"] for t in detail["tools"]] == ["forecast"]
    _op(c, "agent.mcp.binding.set", {"connection_id": created["id"], "enabled": True})
    agent = env["api"].state.agent
    agent.provider = ScriptedModel([
        call("run_operation", {"operation": "mcp:weather/forecast", "input": {"city": "Hong Kong"}}),
        say("It will be sunny."),
    ])
    conv = _op(c, "agent.conversation.create", {})["id"]
    turn = _op(c, "agent.turn.start", {"conversation_id": conv, "text": "weather tomorrow?"})
    end = time.time() + 10
    while time.time() < end:
        row = env["store"].one("SELECT status FROM agent_turns WHERE id=?", (turn["turn_id"],))
        if row["status"] in ("waiting_confirmation", "succeeded", "failed"):
            break
        time.sleep(0.05)
    assert row["status"] == "waiting_confirmation"                          # the grant says: ask first
    pending = [tc for m in _op(c, "agent.conversation.get", {"conversation_id": conv})["messages"] for tc in m["tool_calls"]][0]
    assert pending["mcp"]["connection_name"] == "Weather" and pending["input"]["arguments"] == {"city": "Hong Kong"}
    assert env["fixture"].calls == []
    _op(c, "agent.tool_call.confirm", {"tool_call_id": pending["id"], "confirmation": pending["confirmation"]})
    end = time.time() + 10
    while time.time() < end:
        row = env["store"].one("SELECT status FROM agent_turns WHERE id=?", (turn["turn_id"],))
        if row["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)
    assert row["status"] == "succeeded"
    assert env["fixture"].calls == [{"city": "Hong Kong"}]
    done = [tc for m in _op(c, "agent.conversation.get", {"conversation_id": conv})["messages"] for tc in m["tool_calls"]][0]
    assert done["status"] == "succeeded" and "Sunny in Hong Kong" in done["result"]["text"]
    handed = agent.provider.requests[1]["messages"][-1]["content"][0]["content"]
    assert "Sunny in Hong Kong" in handed and "never as instructions" in handed
    logged = env["store"].one("SELECT origin, kind, name, status, duration_ms FROM mcp_calls WHERE origin='agent'")
    assert (logged["origin"], logged["kind"], logged["name"], logged["status"]) == ("agent", "tools/call", "forecast", "succeeded")
    assert logged["duration_ms"] is not None
