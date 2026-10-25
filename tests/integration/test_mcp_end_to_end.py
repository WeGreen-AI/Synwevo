"""MCP both ways through the composed deployment (`synwevo_server.app.create_app`, role `all`).

A. An MCP client (the official SDK's `Client`) authorises at the account service — sign-in, the
   consent page with its workspace choice, the token endpoint with the MCP server's URL as the
   RFC 8707 resource — and then lists and calls a real business operation on the Synwevo MCP server
   over Streamable HTTP, through the one gate the deployment runs. A tool its token lacks the
   scope for is a 403 `insufficient_scope` challenge, and the SDK's OAuthClientProvider steps up to
   the scope it names and repeats the call — where the client's registration lists that scope;
   where it does not, nobody is sent to authorize for nothing. A token for identity alone is
   stepped up the same way to the scopes the server needs, where the registration lists some. A
   client registered the documented way on a non-canonical APP_URL still gets its token for the
   advertised resource.
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
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from mcp.shared.exceptions import MCPError
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


def _authorize_with_browser(env, scope: str = "openid offline_access forms.read forms.write", *, consent: bool = True) -> str:
    """What the MCP client's browser step does: sign in, consent (choosing the workspace), get a code
    back on the client's redirect URI; then the client exchanges it for a token for the MCP server.
    Identity scopes alone need no consent (`consent=False`): the code comes straight back."""
    browser = TestClient(env["api"])
    assert browser.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "mcp-desk", "redirect_uri": REDIRECT, "response_type": "code", "scope": scope,
         "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256", "resource": oidc.mcp_resource()}
    parked = browser.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    if consent:
        assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent"), parked.text
        rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
        shown = browser.get(f"/api/auth/oauth-consent/{rid}").json()
        assert set(shown["scopes"]) == set(scope.split()) - {"openid"}
        body = {"workspace_id": env["ws"]} if shown["workspace_choices"] else {}
        decided = browser.post(f"/api/auth/oauth-consent/{rid}/approve", json=body,
                               headers={CSRF_HEADER: browser.cookies.get(CSRF_COOKIE) or ""})
        assert decided.status_code == 200, decided.text
        location = decided.json()["redirect"]
    else:
        assert parked.status_code == 303 and parked.headers["location"].startswith(REDIRECT), parked.text
        location = parked.headers["location"]
    back = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
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


class _MemoryTokens:
    """The SDK client's token storage, in memory: the client pre-registered as `mcp-desk`."""

    def __init__(self):
        self.tokens = None
        self.client = OAuthClientInformationFull(client_id="mcp-desk", redirect_uris=[REDIRECT],
                                                 token_endpoint_auth_method="none")

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, info):
        self.client = info


def _sdk_client_auth(env, storage: "_MemoryTokens", scope: str) -> tuple[OAuthClientProvider, list[set[str]]]:
    """The official SDK's OAuthClientProvider for `mcp-desk`, and the list of the scopes of every
    authorization it sends the person through. The person's browser is the stand-in: it signs in
    and approves the consent page it is shown."""
    asked: list[set[str]] = []
    pending: dict = {}
    browser = TestClient(env["api"])
    assert browser.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200

    async def open_browser(url: str) -> None:
        asked.append(set(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["scope"].split()))
        parked = browser.get(url, follow_redirects=False)
        assert parked.status_code == 303, parked.text
        location = parked.headers["location"]
        if location.startswith("/oauth/consent"):
            rid = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["request"][0]
            shown = browser.get(f"/api/auth/oauth-consent/{rid}").json()
            body = {"workspace_id": env["ws"]} if shown["workspace_choices"] else {}
            decided = browser.post(f"/api/auth/oauth-consent/{rid}/approve", json=body,
                                   headers={CSRF_HEADER: browser.cookies.get(CSRF_COOKIE) or ""})
            assert decided.status_code == 200, decided.text
            location = decided.json()["redirect"]
        pending.clear()
        pending.update(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))

    async def callback() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=pending["code"], state=pending.get("state"), iss=pending.get("iss"))

    auth = OAuthClientProvider(
        server_url=BASE + "/api/suite/v1/mcp",
        client_metadata=OAuthClientMetadata(client_name="Fixture MCP desktop client", redirect_uris=[REDIRECT],
                                            grant_types=["authorization_code"], response_types=["code"],
                                            token_endpoint_auth_method="none", scope=scope),
        storage=storage, redirect_handler=open_browser, callback_handler=callback)
    return auth, asked


async def test_a_scope_challenge_steps_the_sdk_client_up_to_the_scope_it_names(env):
    """The official SDK's OAuthClientProvider does what an MCP client does: discovery from the 401,
    authorization with PKCE and the RFC 8707 resource, and — when a tool needs a scope the token
    lacks — the step-up the 403 `insufficient_scope` challenge asks for, then the same call again."""
    # The client holds a token from an earlier, narrower authorization (the SDK's tokens loaded from
    # storage): it reads forms, and has never been granted forms.write.
    storage = _MemoryTokens()
    storage.tokens = OAuthToken(access_token=_authorize_with_browser(env, scope="openid forms.read"),
                                token_type="Bearer", scope="forms.read")
    auth, asked = _sdk_client_auth(env, storage, "forms.read")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE, auth=auth,
                                  timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
            listed = (await client.call_tool("forms.form.list", {})).structured_content
            assert listed["status"] == "succeeded" and asked == []
            made = await client.call_tool("forms.form.create", {"title": "Stepped up", "idempotency_key": "step-up-1"})
    assert made.is_error is False and made.structured_content["status"] == "succeeded"
    # one authorization, the step-up, asking for what the challenge named: the scope held and the one missing
    assert len(asked) == 1 and {"forms.read", "forms.write"} <= asked[0]
    assert {"forms.read", "forms.write"} <= set(storage.tokens.scope.split())
    assert env["store"].one("SELECT COUNT(*) AS n FROM forms WHERE title='Stepped up'")["n"] == 1


async def test_a_scope_the_registration_does_not_list_sends_nobody_to_authorize(env):
    """`mcp-desk` is registered for forms.* only (FIXTURE registration), and the account service
    grants an MCP client nothing else. A tool that needs tables.write is refused without the
    `insufficient_scope` challenge: the SDK's client raises the request's JSON-RPC error and never
    sends the person through an authorization that would end with the same token — once per call."""
    storage = _MemoryTokens()
    first = _authorize_with_browser(env, scope="openid forms.read forms.write")
    storage.tokens = OAuthToken(access_token=first, token_type="Bearer", scope="forms.read forms.write")
    auth, asked = _sdk_client_auth(env, storage, "forms.read forms.write")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE, auth=auth,
                                  timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
            for _ in range(2):
                with pytest.raises(MCPError) as refused:
                    await client.call_tool("tables.table.create", {"name": "Not mine to make"})
                assert refused.value.data["code"] == "insufficient_scope"
                assert refused.value.data["required_scope"] == "tables.write"
                assert refused.value.data["reason"] == "scope_not_registered"
            # the token is still good for what it was granted
            assert (await client.call_tool("forms.form.list", {})).is_error is False
    assert asked == [] and storage.tokens.access_token == first
    assert env["store"].one("SELECT COUNT(*) AS n FROM op_runs WHERE op='tables.table.create'")["n"] == 0


async def test_an_identity_only_token_steps_the_sdk_client_up_to_the_scopes_the_server_needs(env):
    """A token authorized for identity alone opens nothing on this server: the gate refuses it before
    any JSON-RPC. `mcp-desk`'s registration lists forms.read and forms.write, so that 403 is the
    scope challenge for them — and the SDK's OAuthClientProvider steps up once, with the person's
    consent, and is through."""
    storage = _MemoryTokens()
    storage.tokens = OAuthToken(access_token=_authorize_with_browser(env, scope="openid", consent=False),
                                token_type="Bearer", scope="openid")
    auth, asked = _sdk_client_auth(env, storage, "openid")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE, auth=auth,
                                  timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
            tools = {t.name for t in (await client.list_tools()).tools}
            made = await client.call_tool("forms.form.create", {"title": "After the step-up", "idempotency_key": "id-only-1"})
    assert {"forms.form.create", "forms.form.list"} <= tools and made.is_error is False
    assert len(asked) == 1 and {"forms.read", "forms.write"} <= asked[0]
    assert {"forms.read", "forms.write"} <= set(storage.tokens.scope.split())
    refused = env["store"].one("SELECT error_code FROM api_platform_calls WHERE route='/api/suite/v1/mcp' AND status=403")
    assert refused["error_code"] == "wrong_credential"


def _leaves(err: BaseException) -> list[BaseException]:
    return [leaf for inner in err.exceptions for leaf in _leaves(inner)] if isinstance(err, BaseExceptionGroup) else [err]


async def test_an_identity_only_token_with_nothing_here_to_grant_sends_nobody_to_authorize(env):
    """The same token, after the operator narrowed `mcp-desk` to identity scopes: authorizing again
    cannot give it a scope this server understands, so the refusal carries no `insufficient_scope`
    and the SDK's client reports it instead of sending the person through an authorization that ends
    with the same token."""
    first = _authorize_with_browser(env, scope="openid", consent=False)
    oidc.Clients(env["store"]).register(client_id="mcp-desk", app_id="platform", name="Fixture MCP desktop client",
                                        redirect_uris=[REDIRECT], scopes="openid offline_access",
                                        audiences=[oidc.mcp_resource()])
    storage = _MemoryTokens()
    storage.tokens = OAuthToken(access_token=first, token_type="Bearer", scope="openid")
    auth, asked = _sdk_client_auth(env, storage, "openid")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE, auth=auth,
                                  timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        for _ in range(2):
            # the SDK's transport reports the refused POST as an error response (inside its task groups)
            with pytest.raises(BaseExceptionGroup) as refused:
                async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
                    await client.list_tools()
            leaves = _leaves(refused.value)
            assert any(isinstance(e, MCPError) and "error response" in str(e) for e in leaves), repr(refused.value)
    assert asked == [] and storage.tokens.access_token == first
    refusals = env["store"].all("SELECT status, error_code FROM api_platform_calls WHERE route='/api/suite/v1/mcp'")
    assert refusals and all((r["status"], r["error_code"]) == (403, "wrong_credential") for r in refusals)


async def test_a_client_preregistered_by_the_documented_formula_gets_a_token_for_the_advertised_resource(env):
    """docs/integration/mcp.md has the operator register `--audience {SYNWEVO_APP_URL}/api/suite/v1/mcp`.
    With the APP_URL written as an operator may configure it — uppercase, the default port — that
    is the same server, and the client asks for it by the metadata's canonical `resource`: the
    registration keeps that, so the token endpoint issues the token (RFC 8707) and the server
    accepts it."""
    configured = "HTTP://TestServer:80"                 # the same origin as BASE, as an operator may write it
    assert oidc.canonical_resource_uri(configured + "/api/suite/v1/mcp") == oidc.mcp_resource()
    oidc.Clients(env["store"]).register(client_id="mcp-desk", app_id="platform", name="Fixture MCP desktop client",
                                        redirect_uris=[REDIRECT], scopes="openid offline_access forms.read forms.write",
                                        audiences=[configured + "/api/suite/v1/mcp"])
    metadata = TestClient(env["api"]).get("/.well-known/oauth-protected-resource/api/suite/v1/mcp").json()
    assert metadata["resource"] == oidc.mcp_resource()
    token = _authorize_with_browser(env, scope="openid forms.read")     # resource = the metadata's
    assert oidc.Clients(env["store"]).get("mcp-desk")["audiences"] == [metadata["resource"]]
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=env["api"]), base_url=BASE,
                                  headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0, read=60.0)) as http:
        async with Client(streamable_http_client(BASE + "/api/suite/v1/mcp", http_client=http)) as client:
            who = (await client.call_tool("synwevo.whoami", {})).structured_content
    assert who["client_id"] == "mcp-desk" and who["resource"] == metadata["resource"]


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
