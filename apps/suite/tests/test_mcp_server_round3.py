"""The MCP server's inbound edges, held to what the official SDK (mcp 2.3.0) and the specs check:

* the protected resource metadata names the authorization server by the very string that server
  calls itself — its metadata's `issuer`, the RFC 9207 `iss` — and the workplace does not start
  with an issuer a client would read back as another string (RFC 8414 §3.3, RFC 9207 §2.4: simple
  string comparison; the SDK parses the metadata through pydantic first);
* the scope check that runs ahead of the SDK answers 403 only for a call the SDK would serve: a
  request it refuses as malformed is its own 400 (or, for an id that is no request id on a
  handshake revision, its 202), never a step-up challenge for a call that cannot run;
* the endpoint written with a trailing slash is the MCP server reached by a 308 before any token
  is read, never the operations API refusing an MCP token without a challenge.

The application is the business applications alone (`synwevo_suite.app.create_app`). FIXTURES: the
web shell is a one-file fixture build; the MCP policy is opened and one MCP client registered as an
operator would; tokens are minted directly, as the account service would issue them after consent.
"""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from mcp.client.auth.utils import (extract_field_from_www_auth, validate_authorization_response_iss,
                                   validate_metadata_issuer)
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthMetadata, ProtectedResourceMetadata

from synwevo_core import oidc
from synwevo_core.mcp_policy import McpPolicy

from .conftest import ACCOUNTS, People, WORKPLACE

pytestmark = pytest.mark.anyio

MCP_PATH = "/api/suite/v1/mcp"
MCP_URL = WORKPLACE + MCP_PATH
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28"}
TOOL = "tables.table.list"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def build(tmp_path, monkeypatch, *, issuer: str = ACCOUNTS, scopes: str = "openid forms.read tables.read"):
    """The applications alone on WORKPLACE with `issuer`, the MCP server open (FIXTURE policy) and
    one registered MCP client (FIXTURE registration)."""
    shell = tmp_path / "fixture-shell"
    shell.mkdir(exist_ok=True)
    (shell / "index.html").write_text("<!doctype html><title>fixture shell</title>", encoding="utf-8")
    for name, value in (("UI_AUTH", "session"), ("COOKIE_SECURE", "0"), ("DATA_DIR", str(tmp_path / "d")),
                        ("ROLE", "portal"), ("APP_URL", WORKPLACE), ("ISSUER", issuer), ("OIDC_CLIENT_ID", "portal-web"),
                        ("OIDC_APP_ID", "platform"), ("SUITE_APPS", "all"), ("SUITE_UI_DIR", str(shell))):
        monkeypatch.setenv("SYNWEVO_" + name, value)
    monkeypatch.delenv("SYNWEVO_MCP_RESOURCE", raising=False)
    from synwevo_suite.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    people = People(api)
    store = people.store
    with store.tx():
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "inbound_enabled": True},
                                expected_revision=0, actor_id=people.uid("alice"), reason="fixture: open the MCP server")
    client, _ = oidc.Clients(store).register(
        client_id="fixture-mcp-client", app_id="platform", name="Fixture MCP client",
        redirect_uris=["http://127.0.0.1:3030/callback"], scopes=scopes, audiences=[oidc.mcp_resource()])
    return {"api": api, "people": people, "store": store, "client": client}


def mint(world, *, scope: str, audience: str | None = None) -> str:
    out = oidc.Grants(world["store"]).issue_tokens(
        client=world["client"], code_row={"user_id": world["people"].uid("alice"), "scope": scope,
                                          "central_session_id": None, "workspace_id": world["people"].a},
        with_refresh=False, audience=audience or oidc.mcp_resource())
    return out["access_token"]


def http(world, token: str | None) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=world["api"]), base_url=WORKPLACE, headers=headers,
                              timeout=httpx2.Timeout(30.0, read=60.0))


def rpc(method: str, params: dict | None = None, *, id=1) -> dict:
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": {**(params or {}), "_meta": meta}}


def _steps_up(response) -> bool:
    """The official SDK client's own test for a scope step-up (mcp/client/auth/oauth2.py)."""
    return response.status_code == 403 and extract_field_from_www_auth(response, "error") == "insufficient_scope"


# ---------------------------------------------------------------------- P2: one issuer string
@pytest.mark.parametrize("issuer", [ACCOUNTS, "https://accounts.synwevo.test", "https://accounts.synwevo.test/tenant"])
async def test_the_resource_metadata_names_the_issuer_by_the_string_the_authorization_server_uses(tmp_path, monkeypatch, issuer):
    world = build(tmp_path, monkeypatch, issuer=issuer)
    async with http(world, None) as h:
        prm = (await h.get("/.well-known/oauth-protected-resource" + MCP_PATH)).json()
    authorization_server = oidc.discovery()["issuer"]
    # the raw strings a strict client compares, and the iss every authorization response carries
    assert prm["authorization_servers"] == [authorization_server] == [oidc.issuer()]
    # the official SDK client's own checks, on the documents as it parses them
    parsed = ProtectedResourceMetadata.model_validate(prm)
    metadata = OAuthMetadata.model_validate(oidc.discovery())
    validate_metadata_issuer(metadata, str(parsed.authorization_servers[0]))
    validate_authorization_response_iss(oidc.issuer(), metadata)


def test_the_metadata_never_re_renders_the_issuer(monkeypatch):
    """Even for an issuer startup refuses (set after startup, or by a caller that skipped it), the
    resource metadata names the authorization server exactly as that server names itself."""
    monkeypatch.setenv("SYNWEVO_APP_URL", WORKPLACE)
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://Accounts.Synwevo.test:443")
    from synwevo_suite.mcp_server.asgi import protected_resource_metadata
    assert protected_resource_metadata()["authorization_servers"] == [oidc.discovery()["issuer"]]


@pytest.mark.parametrize("written, canonical", [
    ("https://Accounts.Synwevo.test", "https://accounts.synwevo.test"),
    ("https://accounts.synwevo.test:443", "https://accounts.synwevo.test"),
    ("HTTPS://accounts.synwevo.test", "https://accounts.synwevo.test"),
    ("http://accounts.synwevo.test:80/id", "http://accounts.synwevo.test/id"),
    ("https://accounts.synwevo.test?tenant=1", "https://accounts.synwevo.test"),
])
def test_the_workplace_does_not_start_with_an_issuer_clients_would_read_as_another_string(tmp_path, monkeypatch, written, canonical):
    """Such an issuer is advertised as written by the authorization server and re-rendered by every
    client that parses the metadata, so the SDK's RFC 9207 check fails on every authorization.
    Refused, with the form to write, rather than rewritten under tokens that already carry it."""
    with pytest.raises(RuntimeError) as refused:
        build(tmp_path, monkeypatch, issuer=written)
    assert "SYNWEVO_ISSUER" in str(refused.value) and repr(canonical) in str(refused.value)
    assert not (tmp_path / "d").exists() or not any((tmp_path / "d").iterdir())


# ---------------------------------------------------------------------- P3: the SDK's 400 first
def _variants():
    h = {**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": TOOL, "Content-Type": "application/json"}
    legacy = {**h, "MCP-Protocol-Version": "2025-06-18"}
    plain = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": TOOL, "arguments": {}}}
    modern = rpc("tools/call", {"name": TOOL, "arguments": {}}, id=5)
    return [
        ("handshake revision, no jsonrpc member", {k: v for k, v in plain.items() if k != "jsonrpc"}, legacy),
        ("handshake revision, jsonrpc 1.0", {**plain, "jsonrpc": "1.0"}, legacy),
        ("handshake revision, id true", {**plain, "id": True}, legacy),
        ("handshake revision, id a float", {**plain, "id": 5.5}, legacy),
        ("handshake revision, arguments a list", {**plain, "params": {"name": TOOL, "arguments": [1, 2]}}, legacy),
        ("handshake revision, name not a string", {**plain, "params": {"name": 7, "arguments": {}}}, legacy),
        ("2026-07-28, jsonrpc 1.0", {**modern, "jsonrpc": "1.0"}, h),
        ("2026-07-28, id true", {**modern, "id": True}, h),
        ("2026-07-28, arguments a list", rpc("tools/call", {"name": TOOL, "arguments": [1, 2]}, id=5), h),
    ]


@pytest.mark.parametrize("label, body, headers", _variants(), ids=[v[0] for v in _variants()])
async def test_a_call_the_sdk_refuses_as_malformed_gets_the_sdks_answer_not_a_scope_challenge(tmp_path, monkeypatch,
                                                                                               label, body, headers):
    world = build(tmp_path, monkeypatch, scopes="openid forms.read tables.read")
    async with http(world, mint(world, scope="openid tables.read")) as h:
        sdk = await h.post(MCP_URL, content=json.dumps(body), headers=headers)
    async with http(world, mint(world, scope="openid forms.read")) as h:
        lacking = await h.post(MCP_URL, content=json.dumps(body), headers=headers)
    # the SDK does not run it: a 400, a 202 for what it reads as a notification, or (handshake
    # revisions, answered on the event stream) the JSON-RPC error for invalid parameters
    assert sdk.status_code in (400, 202) or '"error":{"code":-32602' in sdk.text, (label, sdk.status_code, sdk.text)
    assert (lacking.status_code, lacking.text) == (sdk.status_code, sdk.text), label
    assert "insufficient_scope" not in lacking.headers.get("www-authenticate", "")


@pytest.mark.parametrize("version", ["2026-07-28", "2025-06-18", None])
async def test_a_well_formed_call_without_the_scope_is_still_the_step_up(tmp_path, monkeypatch, version):
    world = build(tmp_path, monkeypatch, scopes="openid forms.read tables.read")
    body = rpc("tools/call", {"name": TOOL, "arguments": {}}, id="call-1")
    headers = {**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": TOOL}
    if version is None:
        headers.pop("MCP-Protocol-Version")
    elif version != "2026-07-28":
        headers["MCP-Protocol-Version"] = version
    async with http(world, mint(world, scope="openid forms.read")) as h:
        refused = await h.post(MCP_URL, json=body, headers=headers)
    assert _steps_up(refused), (refused.status_code, refused.headers.get("www-authenticate"))
    answer = refused.json()
    assert answer["id"] == "call-1" and answer["error"]["data"]["required_scope"] == "tables.read"


# ---------------------------------------------------------------------- P5: the trailing slash
async def test_the_endpoint_with_a_trailing_slash_is_redirected_before_any_token_is_read(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    token = mint(world, scope="openid forms.read tables.read")
    body, headers = rpc("tools/list"), {**HEADERS, "Mcp-Method": "tools/list"}
    async with http(world, token) as h:
        moved = await h.post(MCP_URL + "/", json=body, headers=headers)
        moved_with_query = await h.get(MCP_PATH + "/?x=1", headers=headers)
    assert moved.status_code == 308 and moved.headers["location"] == MCP_PATH
    assert "www-authenticate" not in moved.headers and moved.headers["cache-control"] == "no-store"
    assert moved_with_query.status_code == 308 and moved_with_query.headers["location"] == MCP_PATH + "?x=1"
    # followed, it is the MCP server: with the MCP token it answers, without one it is the MCP challenge
    async with http(world, token) as h:
        served = await h.post(MCP_URL + "/", json=body, headers=headers, follow_redirects=True)
    assert served.status_code == 200 and served.json()["result"]["tools"], served.text
    async with http(world, None) as h:
        anonymous = await h.post(MCP_URL + "/", json=body, headers=headers, follow_redirects=True)
    assert anonymous.status_code == 401
    assert anonymous.headers["www-authenticate"].startswith("Bearer resource_metadata=")
    # a token for the operations API is refused as the MCP server refuses it, with its challenge
    async with http(world, mint(world, scope="openid forms.read", audience=oidc.suite_resource())) as h:
        other = await h.post(MCP_URL + "/", json=body, headers=headers, follow_redirects=True)
    assert other.status_code == 401 and extract_field_from_www_auth(other, "error") == "invalid_token"


async def test_an_sdk_client_configured_with_the_trailing_slash_reaches_the_server(tmp_path, monkeypatch):
    """The SDK's transport follows a 308 that stays within the endpoint's origin."""
    world = build(tmp_path, monkeypatch)
    async with http(world, mint(world, scope="openid forms.read tables.read")) as h:
        async with Client(streamable_http_client(MCP_URL + "/", http_client=h)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            listed = await client.call_tool(TOOL, {})
    assert TOOL in names and listed.is_error is False
