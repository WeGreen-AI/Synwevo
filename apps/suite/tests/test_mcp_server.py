"""Synwevo as an MCP server, through the real gate and the official SDK's client.

The application is the business applications alone (`synwevo_suite.app.create_app`), driven as an
ASGI application by the SDK's Streamable HTTP client: the gate verifies the bearer token for the
MCP server's own audience, the SDK speaks the protocol (revision 2026-07-28, and the 2025-era
initialize handshake in `mode="legacy"`), and every tool call is `Capabilities.invoke`.

Tokens are minted directly (the account service that issues them is another module; the full
sign-in → consent → token → MCP chain is in tests/integration/test_mcp_end_to_end.py).
"""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from synwevo_core import oidc
from synwevo_core.mcp_policy import McpPolicy

from .conftest import People, WORKPLACE, make_client

pytestmark = pytest.mark.anyio

MCP_URL = WORKPLACE + "/api/suite/v1/mcp"
METADATA_URL = WORKPLACE + "/.well-known/oauth-protected-resource/api/suite/v1/mcp"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    api = make_client(tmp_path, monkeypatch)
    people = People(api)
    store = people.store
    with store.tx():
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "inbound_enabled": True},
                                expected_revision=0, actor_id=people.uid("alice"), reason="fixture: open the MCP server")
    clients = oidc.Clients(store)
    client, _ = clients.register(
        client_id="fixture-mcp-client", app_id="platform", name="Fixture MCP client",
        redirect_uris=["http://127.0.0.1:3030/callback"],
        scopes="openid offline_access forms.read forms.write tables.read",
        audiences=[oidc.mcp_resource(), oidc.suite_resource()])
    return {"api": api, "people": people, "store": store, "client": client}


def mint(world, *, scope: str, audience: str | None = None, who: str = "alice", workspace: str | None = None) -> str:
    """A delegated access token as the account service would issue it after consent."""
    grants = oidc.Grants(world["store"])
    out = grants.issue_tokens(client=world["client"],
                              code_row={"user_id": world["people"].uid(who), "scope": scope, "central_session_id": None,
                                        "workspace_id": workspace or world["people"].a},
                              with_refresh=False, audience=audience or oidc.mcp_resource())
    return out["access_token"]


def http(world, token: str | None, *, host: str = WORKPLACE) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=world["api"]), base_url=host, headers=headers,
                              timeout=httpx2.Timeout(30.0, read=60.0))


def structured(result) -> dict:
    assert result.structured_content is not None
    # The text block carries the same JSON for clients without structured content support.
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


async def test_metadata_and_challenge_lead_a_client_to_the_authorization_server(world):
    async with http(world, None) as h:
        meta = await h.get(METADATA_URL)
        assert meta.status_code == 200, meta.text
        doc = meta.json()
        assert doc["resource"] == oidc.mcp_resource() == WORKPLACE + "/api/suite/v1/mcp"
        assert doc["authorization_servers"] == [oidc.issuer()]
        assert "forms.read" in doc["scopes_supported"] and "offline_access" not in doc["scopes_supported"]
        refused = await h.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                        "Mcp-Method": "tools/list"})
        assert refused.status_code == 401
        challenge = refused.headers["www-authenticate"]
        assert challenge.startswith("Bearer ") and 'error="invalid_token"' in challenge
        assert f'resource_metadata="{METADATA_URL}"' in challenge
        assert refused.json()["error"]["code"] == "unauthorized"


async def test_a_token_for_the_operations_api_does_not_open_the_mcp_server(world):
    token = mint(world, scope="openid forms.read", audience=oidc.suite_resource())
    async with http(world, token) as h:
        refused = await h.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                        "Mcp-Method": "tools/list"})
        assert refused.status_code == 401 and refused.json()["error"]["code"] == "invalid_audience"
        assert 'error="invalid_token"' in refused.headers["www-authenticate"]
    # and the MCP token does not open the REST operations API
    mcp_token = mint(world, scope="openid forms.read")
    async with http(world, mcp_token) as h:
        rest = await h.get(WORKPLACE + "/api/suite/v1/whoami")
        assert rest.status_code == 401 and rest.json()["error"]["code"] == "invalid_audience"


async def test_tools_follow_the_granted_scopes_and_calls_are_the_operations(world):
    token = mint(world, scope="openid forms.read forms.write")
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            assert client.protocol_version == "2026-07-28"
            assert client.server_info is not None and client.server_info.name == "Synwevo"
            listed = await client.list_tools()
            names = [t.name for t in listed.tools]
            assert names == sorted(names)
            assert {"synwevo.whoami", "synwevo.run.get", "forms.form.list", "forms.form.create"} <= set(names)
            # tables.read was registered for the client but not granted to this token
            assert not any(n.startswith("tables.") for n in names)
            create = next(t for t in listed.tools if t.name == "forms.form.create")
            assert create.annotations.read_only_hint is False and "idempotency_key" in create.input_schema["properties"]
            listing = next(t for t in listed.tools if t.name == "forms.form.list")
            assert listing.annotations.read_only_hint is True

            who = structured(await client.call_tool("synwevo.whoami", {}))
            assert who["user_id"] == world["people"].uid("alice") and who["workspace_id"] == world["people"].a
            assert who["scopes"] == ["forms.read", "forms.write"] and "forms" in who["apps"]

            made = await client.call_tool("forms.form.create", {"title": "Leave request", "idempotency_key": "leave-1"})
            assert made.is_error is False
            body = structured(made)
            assert body["status"] == "succeeded" and body["output"]["title"] == "Leave request" and body["replayed"] is False
            again = structured(await client.call_tool("forms.form.create", {"title": "Leave request", "idempotency_key": "leave-1"}))
            assert again["replayed"] is True and again["output"]["id"] == body["output"]["id"]
            assert world["store"].one("SELECT COUNT(*) AS n FROM forms")["n"] == 1

            forms = structured(await client.call_tool("forms.form.list", {}))
            assert [f["id"] for f in forms["output"]["forms"]] == [body["output"]["id"]]

            run = structured(await client.call_tool("synwevo.run.get", {"run_id": body["run_id"]}))
            assert run["id"] == body["run_id"] and run["status"] == "succeeded"

            # a tool execution error the model can read: wrong input
            bad = await client.call_tool("forms.form.create", {"nonsense": 1, "idempotency_key": "x"})
            assert bad.is_error is True and bad.structured_content["error"]["code"] == "invalid_input"
            assert bad.structured_content["error"]["request_id"]
            # an operation the token's scopes do not cover: refused by name, with the scope to step up to
            ungranted = await client.call_tool("tables.table.create", {"name": "x"})
            assert ungranted.is_error is True
            assert ungranted.structured_content["error"]["code"] == "insufficient_scope"
            assert ungranted.structured_content["error"]["details"]["required_scope"] == "tables.write"
            # no such tool at all: a JSON-RPC error (-32602), which the SDK's client raises
            with pytest.raises(MCPError) as unknown:
                await client.call_tool("nothing.like.this", {})
            assert unknown.value.code == -32602 and "nothing.like.this" in str(unknown.value)

            resources = await client.list_resources()
            assert {str(r.uri) for r in resources.resources} == {"synwevo://whoami", "synwevo://operations"}
            read = await client.read_resource("synwevo://operations")
            doc = json.loads(read.contents[0].text)
            granted = {o["id"]: o["granted"] for o in doc["operations"]}
            assert granted["forms.form.create"] is True and granted["forms.form.list"] is True
            assert any(k.startswith("tables.") and not v for k, v in granted.items())

    # the run and the audit say the call came through MCP for that client
    run_row = world["store"].one("SELECT via, delegation FROM op_runs WHERE op='forms.form.create'")
    assert run_row["via"] == "api"
    delegation = json.loads(run_row["delegation"])
    assert delegation["client_id"] == "fixture-mcp-client" and delegation["transport"] == "mcp"
    recorded = world["store"].one("SELECT COUNT(*) AS n FROM api_platform_calls WHERE route='/api/suite/v1/mcp'")
    assert recorded["n"] >= 1


async def test_a_2025_era_client_is_served_through_the_initialize_handshake(world):
    token = mint(world, scope="openid forms.read")
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h), mode="legacy") as client:
            assert client.protocol_version != "2026-07-28"
            listed = await client.list_tools()
            assert "forms.form.list" in {t.name for t in listed.tools}
            assert "forms.form.create" not in {t.name for t in listed.tools}
            who = structured(await client.call_tool("synwevo.whoami", {}))
            assert who["workspace_id"] == world["people"].a


async def test_a_viewer_cannot_write_whatever_the_token_says(world):
    token = mint(world, scope="openid forms.read forms.write", who="vera")
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            refused = await client.call_tool("forms.form.create", {"title": "No", "idempotency_key": "v1"})
            assert refused.is_error is True and refused.structured_content["error"]["code"] in ("read_only_role", "forbidden")


async def test_the_server_is_closed_while_the_policy_says_so(world):
    store = world["store"]
    with store.tx():
        McpPolicy(store).append({**McpPolicy.default().model_dump(), "inbound_enabled": False},
                                expected_revision=1, actor_id=world["people"].uid("alice"), reason="fixture: close it")
    token = mint(world, scope="openid forms.read")
    async with http(world, token) as h:
        refused = await h.post(MCP_URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                               headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                        "Mcp-Method": "tools/list"})
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "mcp_inbound_disabled"
        assert "www-authenticate" in refused.headers


async def test_another_host_name_is_not_this_server(world):
    token = mint(world, scope="openid forms.read")
    async with http(world, token, host="http://evil.testserver") as h:
        answer = await h.post(MCP_URL.replace(WORKPLACE, "http://evil.testserver"),
                              json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                              headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                       "Mcp-Method": "tools/list"})
        assert answer.status_code == 421
