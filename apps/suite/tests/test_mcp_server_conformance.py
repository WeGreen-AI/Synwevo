"""Synwevo's MCP server held to the protocol's own rules, through the real gate and the official
SDK's client (mcp 2.3.0), where the SDK is the one that checks:

* a result too large to return still matches the tool's outputSchema (the client validates every
  non-error result), still names its run, and keeps as much of its output as fits;
* every method but POST is 405 with `Allow: POST` (Streamable HTTP), not the web shell's 404;
* the `WWW-Authenticate` challenge says `insufficient_scope` only for a real scope refusal that a
  new authorization can grant, with the scopes to ask for — the one the SDK's client steps up on:
  a tool or a run the token lacks the scope for, and a token with no business scope left at all —
  and never for refusals more scope cannot fix, a scope the client's registration does not list
  included (RFC 6750 §3.1, MCP authorization "Scope Challenge Handling");
* `idempotentHint` is true only where the same arguments cannot act twice;
* the audience is the canonical resource URI, the very string the metadata advertises (RFC 8707),
  also for a client the operator registered with the URL written as configured.

The application is the business applications alone (`synwevo_suite.app.create_app`), as in
test_mcp_server.py. FIXTURES: the web shell is a one-file fixture build, so the shells'
`GET /{full_path:path}` catch-all is mounted whatever was built; tokens are minted directly, as the
account service would issue them after consent; the large forms are fixture data made through the
real operation.
"""
from __future__ import annotations

import json

import httpx2
import jsonschema
import pytest
from mcp import Client
from mcp.client.auth.utils import extract_field_from_www_auth, extract_scope_from_www_auth
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from pydantic import AnyHttpUrl

from synwevo_core import oidc
from synwevo_core.capabilities import canonical
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.oidc import SUITE_API_SCOPES
from synwevo_suite.mcp_server import server as mcp_server

from .conftest import ACCOUNTS, People, WORKPLACE

pytestmark = pytest.mark.anyio

MCP_URL = WORKPLACE + "/api/suite/v1/mcp"
HEADERS = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def build(tmp_path, monkeypatch, *, app_url: str = WORKPLACE, scopes: str = "openid forms.read forms.write tables.read"):
    """The applications alone, as `conftest.make_client` builds them but on `app_url`, with the
    MCP server open (FIXTURE policy) and one registered MCP client (FIXTURE registration)."""
    shell = tmp_path / "fixture-shell"
    shell.mkdir(exist_ok=True)
    (shell / "index.html").write_text("<!doctype html><title>fixture shell</title>", encoding="utf-8")
    for name, value in (("UI_AUTH", "session"), ("COOKIE_SECURE", "0"), ("DATA_DIR", str(tmp_path / "d")),
                        ("ROLE", "portal"), ("APP_URL", app_url), ("ISSUER", ACCOUNTS), ("OIDC_CLIENT_ID", "portal-web"),
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


def mint(world, *, scope: str, who: str = "alice", audience: str | None = None) -> str:
    out = oidc.Grants(world["store"]).issue_tokens(
        client=world["client"], code_row={"user_id": world["people"].uid(who), "scope": scope, "central_session_id": None,
                                          "workspace_id": world["people"].a},
        with_refresh=False, audience=audience or oidc.mcp_resource())
    return out["access_token"]


def http(world, token: str | None, *, base: str = WORKPLACE) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=world["api"]), base_url=base, headers=headers,
                              timeout=httpx2.Timeout(30.0, read=60.0))


def rpc(method: str, params: dict | None = None, *, id: int = 1) -> dict:
    """One JSON-RPC request of revision 2026-07-28, with the envelope keys it carries in `_meta`."""
    meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {}}
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": {**(params or {}), "_meta": meta}}


# ---------------------------------------------------------------------- truncation (outputSchema)
async def test_a_result_too_large_to_return_keeps_its_envelope_and_matches_the_output_schema(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    caps, alice, a = world["api"].state.capabilities, world["people"].uid("alice"), world["people"].a
    for i in range(220):                              # FIXTURE data: ~2 kB each, ~460 k characters listed
        caps.invoke("forms.form.create", {"title": f"Fixture form {i}", "description": "d" * 2000},
                    user_id=alice, workspace_id=a)
    token = mint(world, scope="openid forms.read forms.write")
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            # the SDK's client validates this result against the tool's outputSchema and raises
            # RuntimeError("Invalid structured content …") when it does not match
            listed = await client.call_tool("forms.form.list", {})
    assert listed.is_error is False
    body = listed.structured_content
    jsonschema.validate(body, tools["forms.form.list"].output_schema)
    assert body["truncated"] is True and body["operation"] == "forms.form.list" and body["status"] == "succeeded"
    assert body["replayed"] is False and body["workspace_id"] == a
    assert set(body["output"]) == {"preview", "note"}
    assert len(canonical(body)) <= mcp_server.MAX_RESULT_CHARS
    assert json.loads(listed.content[0].text) == body
    # the run is named and its output is the one cut: the preview is a prefix of it, never a rewrite
    run = world["store"].one("SELECT op, output FROM op_runs WHERE id=?", (body["run_id"],))
    assert run["op"] == "forms.form.list"
    stored = run["output"]
    full = canonical(json.loads(stored) if isinstance(stored, str) else stored)
    assert len(full) > mcp_server.MAX_RESULT_CHARS and full.startswith(body["output"]["preview"])
    assert len(body["output"]["preview"]) > mcp_server.MAX_RESULT_CHARS // 2


async def test_a_cut_write_result_still_names_the_run_it_committed(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server, "MAX_RESULT_CHARS", 2_000)
    token = mint(world, scope="openid forms.read forms.write")
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            made = await client.call_tool("forms.form.create", {"title": "Leave request", "description": "x" * 1_900,
                                                                "idempotency_key": "leave-1"})
            body = made.structured_content
            assert made.is_error is False and body["truncated"] is True and body["status"] == "succeeded"
            row = world["store"].one("SELECT op, object_id FROM op_runs WHERE id=?", (body["run_id"],))
            assert row["op"] == "forms.form.create" and row["object_id"]
            again = (await client.call_tool("forms.form.create", {"title": "Leave request", "description": "x" * 1_900,
                                                                   "idempotency_key": "leave-1"})).structured_content
            assert again["replayed"] is True and again["run_id"] == body["run_id"]
            # a run read back has no outputSchema; it is cut the same way and still says which run
            read = (await client.call_tool("synwevo.run.get", {"run_id": body["run_id"]})).structured_content
            assert read["truncated"] is True and read["id"] == body["run_id"]
    assert world["store"].one("SELECT COUNT(*) AS n FROM forms")["n"] == 1


@pytest.mark.parametrize("char", ['"', "\n", "\\", "a", "字"])
def test_a_cut_output_keeps_the_longest_preview_that_fits(monkeypatch, char):
    """The preview is the longest prefix of the output's JSON text that fits — also where nearly
    every character is escaped twice over (a quote in the output is `\\"` in its JSON text and
    `\\\\\\"` in the result), which used to leave an empty preview."""
    monkeypatch.setattr(mcp_server, "MAX_RESULT_CHARS", 2_000)
    envelope = {"run_id": "run_fixture", "operation": "forms.form.list", "status": "succeeded",   # FIXTURE envelope
                "replayed": False, "workspace_id": "ws_fixture"}
    value, cut = mcp_server._bounded({**envelope, "output": {"items": [char * 5_000]}})
    assert cut is True and value["truncated"] is True and {k: value[k] for k in envelope} == envelope
    preview, text = value["output"]["preview"], canonical({"items": [char * 5_000]})
    assert len(canonical(value)) <= mcp_server.MAX_RESULT_CHARS and text.startswith(preview)
    one_more = {**value, "output": {"preview": text[:len(preview) + 1], "note": mcp_server.TRUNCATED_NOTE}}
    assert len(canonical(one_more)) > mcp_server.MAX_RESULT_CHARS
    # and a result without an envelope is cut the same way
    bare, _ = mcp_server._bounded([char * 5_000])
    assert len(canonical(bare)) <= mcp_server.MAX_RESULT_CHARS
    longer = {**bare, "preview": canonical([char * 5_000])[:len(bare["preview"]) + 1]}
    assert len(canonical(longer)) > mcp_server.MAX_RESULT_CHARS


@pytest.mark.parametrize("char, at_least", [('"', 199_000), ("\n", 266_000), ("a", 399_000)])
def test_a_cut_output_at_the_real_cap_keeps_the_longest_preview_that_fits(char, at_least):
    """The cases that used to come out short, at MAX_RESULT_CHARS itself: 500,000 quotes gave an
    empty preview, 500,000 newlines one of 199,810 characters in a 299,905-character result."""
    envelope = {"run_id": "run_fixture", "operation": "tables.record.query", "status": "succeeded",   # FIXTURE envelope
                "replayed": False, "workspace_id": "ws_fixture"}
    value, cut = mcp_server._bounded({**envelope, "output": char * 500_000})
    preview, text = value["output"]["preview"], canonical(char * 500_000)
    assert cut is True and text.startswith(preview) and len(preview) >= at_least
    size = len(canonical(value))
    assert size <= mcp_server.MAX_RESULT_CHARS
    # one more character of the output's JSON text would not fit
    one_more = {**value, "output": {"preview": text[:len(preview) + 1], "note": mcp_server.TRUNCATED_NOTE}}
    assert len(canonical(one_more)) > mcp_server.MAX_RESULT_CHARS
    bare, _ = mcp_server._bounded(char * 500_000)
    assert len(bare["preview"]) >= at_least - 1_000 and len(canonical(bare)) <= mcp_server.MAX_RESULT_CHARS


# ---------------------------------------------------------------------- methods
async def test_every_method_but_post_is_405_with_allow_post_not_the_shell(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    token = mint(world, scope="openid forms.read")
    async with http(world, token) as h:
        # the shells' catch-all is mounted: an application page is the fixture shell
        page = await h.get(WORKPLACE + "/forms")
        assert page.status_code == 200 and "fixture shell" in page.text
        # what a 2025-era client sends after initialize to open its listening stream
        listen = await h.get(MCP_URL, headers={"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-06-18"})
        assert listen.status_code == 405, listen.text
        assert listen.headers["allow"] == "POST" and listen.json()["error"]["code"] == "method_not_allowed"
        for method in ("HEAD", "DELETE", "PUT", "PATCH"):
            answer = await h.request(method, MCP_URL, headers={"MCP-Protocol-Version": "2025-06-18"})
            assert (method, answer.status_code, answer.headers.get("allow")) == (method, 405, "POST")
        # POST is still the protocol
        ok = await h.post(MCP_URL, json=rpc("tools/list"), headers={**HEADERS, "Mcp-Method": "tools/list"})
        assert ok.status_code == 200, ok.text
    # the method is the endpoint's to refuse; who is asking is still the gate's, first
    async with http(world, None) as h:
        anonymous = await h.get(MCP_URL)
        assert anonymous.status_code == 401 and "resource_metadata=" in anonymous.headers["www-authenticate"]


# ---------------------------------------------------------------------- challenges
def _steps_up(response) -> bool:
    """The official SDK client's own test for a scope step-up (mcp/client/auth/oauth2.py)."""
    return response.status_code == 403 and extract_field_from_www_auth(response, "error") == "insufficient_scope"


async def test_a_tool_the_token_lacks_the_scope_for_is_a_403_scope_challenge(tmp_path, monkeypatch):
    # the registration lists tables.write, so authorizing again can grant it: a real step-up
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write tables.read tables.write")
    token = mint(world, scope="openid forms.read forms.write")
    async with http(world, token) as h:
        refused = await h.post(MCP_URL, json=rpc("tools/call", {"name": "tables.table.create", "arguments": {"name": "x"}}, id=7),
                               headers={**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": "tables.table.create"})
        assert refused.status_code == 403, refused.text
        challenge = refused.headers["www-authenticate"]
        assert _steps_up(refused)
        # the scopes to ask for: the ones held and the one missing, so stepping up loses nothing
        assert extract_scope_from_www_auth(refused) == "forms.read forms.write tables.write"
        assert 'error_description="insufficient_scope"' in challenge and "resource_metadata=" in challenge
        # the body is that request's JSON-RPC error
        answer = refused.json()
        assert answer["jsonrpc"] == "2.0" and answer["id"] == 7
        assert answer["error"]["data"]["code"] == "insufficient_scope"
        assert answer["error"]["data"]["required_scope"] == "tables.write"
        assert answer["error"]["data"]["request_id"] == refused.headers["x-request-id"]
        # and it is the body the published contract documents for that 403
        from synwevo_suite.common.machine_contract import contract
        documented = contract(world["api"].state.capabilities.registry)["paths"]["/api/suite/v1/mcp"]["post"]["responses"]["403"]
        assert "WWW-Authenticate" in documented["headers"]
        jsonschema.validate(answer, documented["content"]["application/json"]["schema"]["anyOf"][1])
        # a tool it does hold is served as before
        listed = await h.post(MCP_URL, json=rpc("tools/call", {"name": "forms.form.list", "arguments": {}}),
                              headers={**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": "forms.form.list"})
        assert listed.status_code == 200
    assert world["store"].one("SELECT COUNT(*) AS n FROM op_runs WHERE op='tables.table.create'")["n"] == 0
    recorded = world["store"].one("SELECT status, error_code FROM api_platform_calls WHERE route='/api/suite/v1/mcp' "
                                  "AND status=403")
    assert recorded["error_code"] == "insufficient_scope"


async def test_a_malformed_call_is_refused_as_malformed_before_any_scope_challenge(tmp_path, monkeypatch):
    """The scope check runs ahead of the SDK, so it must not answer what the SDK refuses first:
    revision 2026-07-28 routing headers that disagree with the body are a 400 (a MUST), an Accept
    without the event stream a 406 — never a step-up challenge for a request that cannot run."""
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write tables.read tables.write")
    async with http(world, mint(world, scope="openid forms.read")) as h:
        body = rpc("tools/call", {"name": "tables.table.create", "arguments": {"name": "x"}})
        for headers, status in (
                ({**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": "forms.form.list"}, 400),       # names another tool
                ({**HEADERS, "Mcp-Method": "tools/list", "Mcp-Name": "tables.table.create"}, 400),   # another method
                ({**HEADERS, "MCP-Protocol-Version": "2025-01-01", "Mcp-Method": "tools/call",       # no such revision
                  "Mcp-Name": "tables.table.create"}, 400),
                ({**HEADERS, "Accept": "application/json", "Mcp-Method": "tools/call", "Mcp-Name": "tables.table.create"}, 406)):
            answer = await h.post(MCP_URL, json=body, headers=headers)
            assert answer.status_code == status, (headers, answer.status_code, answer.text)
            assert "insufficient_scope" not in answer.headers.get("www-authenticate", "")
        # the same call, well formed, is the scope challenge
        good = await h.post(MCP_URL, json=body, headers={**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": "tables.table.create"})
        assert good.status_code == 403 and _steps_up(good)
    assert world["store"].one("SELECT COUNT(*) AS n FROM op_runs WHERE op='tables.table.create'")["n"] == 0


def _call(name: str, arguments: dict, *, id: int = 1) -> tuple[dict, dict]:
    return (rpc("tools/call", {"name": name, "arguments": arguments}, id=id),
            {**HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": name})


def _refused_without_a_step_up(answer, required: str) -> None:
    """403, the JSON-RPC error naming the scope, and a challenge that only says where the metadata
    is: nothing the SDK's client would re-authorize on."""
    assert answer.status_code == 403, answer.text
    assert not _steps_up(answer) and "error=" not in answer.headers["www-authenticate"]
    assert answer.headers["www-authenticate"].startswith("Bearer resource_metadata=")
    data = answer.json()["error"]["data"]
    assert data["code"] == "insufficient_scope" and data["required_scope"] == required
    assert data["reason"] == "scope_not_registered"


async def test_a_scope_a_new_authorization_cannot_grant_is_refused_without_a_step_up(tmp_path, monkeypatch):
    """The account service grants an MCP client only what its registration lists. A challenge for
    any other scope sends the person through an authorization that ends with the same token — once
    per call — so those refusals carry no `insufficient_scope`."""
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write tables.read")
    # never registered: tables.write
    writer = mint(world, scope="openid forms.read forms.write")
    async with http(world, writer) as h:
        body, headers = _call("tables.table.create", {"name": "x"}, id=3)
        _refused_without_a_step_up(await h.post(MCP_URL, json=body, headers=headers), "tables.write")
    # granted, then withdrawn by the registration: asking again gives the narrowed token
    reader = mint(world, scope="openid forms.read tables.read")
    oidc.Clients(world["store"]).register(client_id="fixture-mcp-client", app_id="platform", name="Fixture MCP client",
                                          redirect_uris=["http://127.0.0.1:3030/callback"],
                                          scopes="openid forms.read forms.write", audiences=[oidc.mcp_resource()])
    async with http(world, reader) as h:
        body, headers = _call("tables.table.list", {})
        _refused_without_a_step_up(await h.post(MCP_URL, json=body, headers=headers), "tables.read")
        # what the registration does list is still a step-up, for the scopes it can grant
        body, headers = _call("forms.form.create", {"title": "x"})
        grantable = await h.post(MCP_URL, json=body, headers=headers)
        assert _steps_up(grantable) and extract_scope_from_www_auth(grantable) == "forms.read forms.write"
    assert world["store"].one("SELECT COUNT(*) AS n FROM op_runs WHERE op LIKE 'tables.%' OR op='forms.form.create'")["n"] == 0
    codes = {r["error_code"] for r in world["store"].all(
        "SELECT error_code FROM api_platform_calls WHERE route='/api/suite/v1/mcp' AND status=403")}
    assert codes == {"insufficient_scope"}


async def test_reading_back_a_run_names_the_scope_its_operation_needs(tmp_path, monkeypatch):
    """synwevo.run.get is a scope refusal too: a run of forms.form.create is read with forms.write.
    It is challenged like a tool call — and only for a run this caller may see, so a guessed run id
    learns nothing."""
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write")
    async with http(world, mint(world, scope="openid forms.read forms.write")) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            made = (await client.call_tool("forms.form.create", {"title": "Leave", "idempotency_key": "leave-1"})).structured_content
    async with http(world, mint(world, scope="openid forms.read")) as h:
        body, headers = _call("synwevo.run.get", {"run_id": made["run_id"]}, id=9)
        refused = await h.post(MCP_URL, json=body, headers=headers)
        assert refused.status_code == 403, refused.text
        assert _steps_up(refused) and extract_scope_from_www_auth(refused) == "forms.read forms.write"
        assert refused.json()["id"] == 9 and refused.json()["error"]["data"]["required_scope"] == "forms.write"
        # no such run (or not this caller's): the same not-found as before, no challenge
        body, headers = _call("synwevo.run.get", {"run_id": "run_does_not_exist"})
        unknown = await h.post(MCP_URL, json=body, headers=headers)
        assert unknown.status_code == 200 and "run_not_found" in unknown.text
        # the run is read with the scope its operation needs
    async with http(world, mint(world, scope="openid forms.write")) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            read = (await client.call_tool("synwevo.run.get", {"run_id": made["run_id"]})).structured_content
    assert read["id"] == made["run_id"]


def _register(world, scopes: str, audiences: list[str] | None = None) -> None:
    """Change the FIXTURE registration of the one MCP client, as an operator would."""
    oidc.Clients(world["store"]).register(client_id="fixture-mcp-client", app_id="platform", name="Fixture MCP client",
                                          redirect_uris=["http://127.0.0.1:3030/callback"], scopes=scopes,
                                          audiences=[oidc.mcp_resource()] if audiences is None else audiences)


async def _list(world, token):
    async with http(world, token) as h:
        return await h.post(MCP_URL, json=rpc("tools/list"), headers={**HEADERS, "Mcp-Method": "tools/list"})


def _gate_refusal_without_a_step_up(answer, code: str) -> None:
    assert answer.status_code == 403 and answer.json()["error"]["code"] == code, answer.text
    assert not _steps_up(answer) and "error=" not in answer.headers["www-authenticate"]
    assert answer.headers["www-authenticate"].startswith("Bearer resource_metadata=")
    # the challenge is the header's: the body does not carry a hint the header contradicts
    assert "www_authenticate" not in answer.text


async def test_refusals_more_scope_cannot_fix_do_not_ask_for_a_step_up(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write")
    reading = mint(world, scope="openid forms.read")
    identity_only = mint(world, scope="openid")

    # the registration no longer lists the scope the token was granted, nor any other this server
    # understands: authorizing again gives a token without one (the account service narrows the
    # request to the registration and refuses what is left without a business scope)
    _register(world, "openid")
    _gate_refusal_without_a_step_up(await _list(world, reading), "forbidden")
    # the same for a credential minted for nothing this server serves
    _gate_refusal_without_a_step_up(await _list(world, identity_only), "wrong_credential")

    # the server is closed by policy: no token and no scope opens it
    _register(world, "openid forms.read forms.write")
    with world["store"].tx():
        McpPolicy(world["store"]).append({**McpPolicy.default().model_dump(), "inbound_enabled": False},
                                         expected_revision=1, actor_id=world["people"].uid("alice"), reason="fixture: close it")
    _gate_refusal_without_a_step_up(await _list(world, reading), "mcp_inbound_disabled")
    _gate_refusal_without_a_step_up(await _list(world, identity_only), "mcp_inbound_disabled")


async def test_a_token_with_no_business_scope_left_steps_up_where_the_registration_can_grant_one(tmp_path, monkeypatch):
    """An identity-only token, or one whose every scope the registration has withdrawn, opens nothing
    here: a real scope refusal (RFC 6750 §3.1). Where authorizing again can grant scopes this server
    needs, the 403 is the challenge for them — the metadata's default scopes the registration lists,
    never a `*.decide` the client did not name — so the SDK's client steps up once and is through."""
    world = build(tmp_path, monkeypatch, scopes="openid forms.read forms.write approvals.decide")
    identity_only = mint(world, scope="openid")
    only_identity = await _list(world, identity_only)
    assert only_identity.status_code == 403 and only_identity.json()["error"]["code"] == "wrong_credential"
    assert _steps_up(only_identity) and extract_scope_from_www_auth(only_identity) == "forms.read forms.write"
    assert 'error_description="wrong_credential"' in only_identity.headers["www-authenticate"]
    assert "www_authenticate" not in only_identity.text
    # what that step-up gets — those scopes, for this client — is through
    assert (await _list(world, mint(world, scope="openid forms.read forms.write"))).status_code == 200

    # granted forms.read; the registration now lists forms.write (and a decision) instead
    reading = mint(world, scope="openid forms.read")
    _register(world, "openid forms.write approvals.decide")
    withdrawn = await _list(world, reading)
    assert withdrawn.status_code == 403 and withdrawn.json()["error"]["code"] == "forbidden"
    assert withdrawn.json()["error"]["details"] == {"reason": "scope_withdrawn"}
    assert _steps_up(withdrawn) and extract_scope_from_www_auth(withdrawn) == "forms.write"

    # a client no longer registered for this server gets nothing here from authorizing again
    _register(world, "openid forms.read forms.write", audiences=["urn:synwevo:fixture:another-resource"])
    _gate_refusal_without_a_step_up(await _list(world, identity_only), "wrong_credential")


def test_what_a_step_up_can_reach_is_what_the_registration_lists_for_this_server(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, scopes="openid offline_access forms.read approvals.decide")
    client = oidc.Clients(world["store"]).get("fixture-mcp-client")
    assert oidc.mcp_grantable_scopes(client) == {"forms.read", "approvals.decide"}
    assert oidc.mcp_grantable_scopes({**client, "audiences": [oidc.suite_resource()]}) == set()
    assert oidc.mcp_grantable_scopes(None) == set()
    # the metadata's default request is the same list the gate's step-up draws from
    from synwevo_suite.mcp_server.asgi import protected_resource_metadata
    assert protected_resource_metadata()["scopes_supported"] == list(oidc.MCP_DEFAULT_SCOPES)
    assert not any(s.endswith(".decide") for s in oidc.MCP_DEFAULT_SCOPES)


async def test_a_challenge_names_an_error_only_when_a_token_was_presented(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch)
    async with http(world, None) as h:
        bare = await h.post(MCP_URL, json=rpc("tools/list"), headers=HEADERS)
        basic = await h.post(MCP_URL, json=rpc("tools/list"), headers={**HEADERS, "Authorization": "Basic Zm9vOmJhcg=="})
        bogus = await h.post(MCP_URL, json=rpc("tools/list"), headers={**HEADERS, "Authorization": "Bearer not-issued-here"})
    for anonymous in (bare, basic):
        assert anonymous.status_code == 401
        assert anonymous.headers["www-authenticate"].startswith("Bearer resource_metadata=")
        assert "error=" not in anonymous.headers["www-authenticate"]
    assert bogus.status_code == 401
    assert extract_field_from_www_auth(bogus, "error") == "invalid_token"


# ---------------------------------------------------------------------- annotations
async def test_idempotent_hint_is_true_only_where_a_repeat_cannot_act_twice(tmp_path, monkeypatch):
    world = build(tmp_path, monkeypatch, scopes="openid " + " ".join(SUITE_API_SCOPES))
    token = mint(world, scope="openid " + " ".join(SUITE_API_SCOPES))
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            tools = (await client.list_tools()).tools
            seen = {"read": 0, "keyed": 0, "optional": 0}
            for t in tools:
                if t.name.startswith("synwevo."):
                    continue
                required = "idempotency_key" in (t.input_schema.get("required") or [])
                if t.annotations.read_only_hint:
                    seen["read"] += 1
                    assert t.annotations.idempotent_hint is True, t.name
                elif required:
                    seen["keyed"] += 1
                    assert t.annotations.idempotent_hint is True, t.name
                else:
                    seen["optional"] += 1
                    assert t.annotations.idempotent_hint is False, t.name
            assert all(seen.values()), seen
            create = next(t for t in tools if t.name == "forms.form.create")
            assert create.annotations.idempotent_hint is False
            # why: the same arguments without a key, sent twice, make two forms
            for _ in range(2):
                assert (await client.call_tool("forms.form.create", {"title": "Same"})).is_error is False
    assert world["store"].one("SELECT COUNT(*) AS n FROM forms WHERE title='Same'")["n"] == 2


# ---------------------------------------------------------------------- canonical resource URI
@pytest.mark.parametrize("app_url", ["HTTP://Workplace.Testserver:80", "http://workplace.TESTSERVER:80/"])
async def test_the_audience_is_the_canonical_uri_the_metadata_advertises(tmp_path, monkeypatch, app_url):
    world = build(tmp_path, monkeypatch, app_url=app_url)
    async with http(world, None) as h:
        doc = (await h.get(WORKPLACE + "/.well-known/oauth-protected-resource/api/suite/v1/mcp")).json()
    assert doc["resource"] == oidc.mcp_resource() == MCP_URL
    # a client sends back the metadata's `resource` (RFC 8707); the token minted for it opens the
    # server at the address a client writes, Host header and all
    token = mint(world, scope="openid forms.read", audience=doc["resource"])
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            who = (await client.call_tool("synwevo.whoami", {})).structured_content
    assert who["resource"] == doc["resource"] and who["workspace_id"] == world["people"].a
    async with http(world, None) as h:
        refused = await h.post(MCP_URL, json=rpc("tools/list"), headers=HEADERS)
    assert f'resource_metadata="{WORKPLACE}/.well-known/oauth-protected-resource/api/suite/v1/mcp"' in refused.headers["www-authenticate"]


@pytest.mark.parametrize("app_url", ["HTTP://Workplace.Testserver:80", "http://workplace.testserver"])
async def test_a_client_registered_by_the_documented_formula_names_the_canonical_resource(tmp_path, monkeypatch, app_url):
    """docs/integration/mcp.md tells an operator to register `--audience {SYNWEVO_APP_URL}/api/suite/v1/mcp`.
    Written with the APP_URL as configured, that is still the server the metadata names: kept as
    the canonical `resource`, so the account service finds it (RFC 8707 `resource`, compared
    exactly) and recognises the client as an MCP client. Other audiences stay as written."""
    world = build(tmp_path, monkeypatch, app_url=app_url)
    async with http(world, None) as h:
        resource = (await h.get(WORKPLACE + "/.well-known/oauth-protected-resource/api/suite/v1/mcp")).json()["resource"]
    clients = oidc.Clients(world["store"])
    as_written = f"{app_url}/api/suite/v1/mcp"
    other = "https://Docs.Example.com/api/"                         # FIXTURE: another resource server's setting
    client, _ = clients.register(client_id="fixture-desk", app_id="platform", name="Fixture desk",
                                 redirect_uris=["http://127.0.0.1:3030/callback"], scopes="openid forms.read",
                                 audiences=[as_written, other])
    assert client["audiences"] == [resource, other] == [MCP_URL, other]
    assert json.loads(world["store"].one("SELECT audiences FROM oauth_clients WHERE client_id='fixture-desk'")["audiences"]) \
        == [resource, other]
    # a registration kept as written before this (FIXTURE row) reads as the same server
    world["store"].execute("UPDATE oauth_clients SET audiences=? WHERE client_id='fixture-desk'",
                           (json.dumps([as_written, other]),))
    assert clients.get("fixture-desk")["audiences"] == [resource, other]
    # and the token minted for that resource opens the server
    world["client"] = clients.get("fixture-desk")
    token = mint(world, scope="openid forms.read", audience=resource)
    async with http(world, token) as h:
        async with Client(streamable_http_client(MCP_URL, http_client=h)) as client:
            who = (await client.call_tool("synwevo.whoami", {})).structured_content
    assert who["client_id"] == "fixture-desk" and who["resource"] == resource


@pytest.mark.parametrize("setting, canonical_uri, metadata_url", [
    ("HTTPS://MCP.Example.COM:443/mcp/", "https://mcp.example.com/mcp",
     "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"),
    ("https://mcp.example.com/mcp#section", "https://mcp.example.com/mcp",
     "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"),
    ("https://mcp.example.com:8443/Path/MCP", "https://mcp.example.com:8443/Path/MCP",
     "https://mcp.example.com:8443/.well-known/oauth-protected-resource/Path/MCP"),
    ("https://mcp.example.com", "https://mcp.example.com/",
     "https://mcp.example.com/.well-known/oauth-protected-resource"),
])
def test_an_explicit_resource_setting_is_canonicalised_like_a_client_parses_it(monkeypatch, setting, canonical_uri,
                                                                              metadata_url):
    from synwevo_core.http import mcp_metadata_url
    from synwevo_suite.mcp_server.asgi import protected_resource_metadata
    monkeypatch.setenv("SYNWEVO_MCP_RESOURCE", setting)
    assert oidc.mcp_resource() == canonical_uri
    # a fixed point of the parser clients and the metadata document use
    assert str(AnyHttpUrl(canonical_uri)) == canonical_uri
    assert protected_resource_metadata()["resource"] == canonical_uri
    # RFC 9728 §3.1: the well-known path goes between the host and the path, and a resource at the
    # root (written `https://host/` in canonical form) leaves no slash after it
    assert mcp_metadata_url() == metadata_url
