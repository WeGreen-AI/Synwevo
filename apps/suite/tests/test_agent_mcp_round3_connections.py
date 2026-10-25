"""The connection service's guarantees (synwevo_suite.agent.mcp.connections), round 3:

* a workspace's connection serves nobody while the workspace is closed to them as to every native
  operation — a frozen organization's included: not the page, not the Agent, not a stored result;
* a credential typed for the address a page showed is never sealed on the address another manager
  pointed the connection at meanwhile, and a caller holding the connection as it was before a change
  never gets the new server's token nor sends its call to the new address;
* a refresh of the old token family cannot seal its result over a newer authorization, or over any
  credential other than the one it started from;
* a token endpoint that cannot be reached is the server's failure, not a call to authenticate again;
* a call's result is cut to what its run record can hold, so a write the server carried out is never
  recorded as one that failed.

TEST DOUBLES, each labelled: the external MCP server is the FIXTURE server of test_agent_mcp.py (the
official SDK's `MCPServer`, in process through an ASGI transport), behind the FIXTURE recording
transport and token endpoints of test_agent_mcp_connections.py; the model is the scripted FIXTURE
model of test_agent.py; organizations are created and frozen through `Organizations` on the isolated
fixture database.
"""
from __future__ import annotations

import json

import httpx2
import pytest
from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations

from synwevo_core.capabilities import MAX_OUTPUT_BYTES, canonical
from synwevo_core.errors import ServiceError
from synwevo_core.organizations import Organizations
from synwevo_core.store import now
from synwevo_suite.agent.mcp import connections as connections_module
from synwevo_suite.agent.mcp.oauth import OutboundOAuth
from synwevo_suite.agent.mcp.runtime import ResourceOutcome, ToolOutcome

from .test_agent import call, op, say, wait_turn
from .test_agent_mcp import FIXTURE_URL, LazyFixtureApp, connect, start, tool_calls
from .test_agent_mcp_connections import (OvertakenTokenServer, fixture_flow, fixture_oauth_connection, publish, row,  # noqa: F401
                                         sealed_tokens, user, world)

ELSEWHERE = "https://other-server.test/mcp"


def hosts(c) -> set:
    return {q["host"] for q in c.state["requests"]}


# ---------------------------------------------------------------- the organization gate
def test_a_frozen_organizations_workspace_connection_serves_nobody_until_it_is_unfrozen(world):
    store, alice = world.store, world.people.uid("alice")
    world.agent = world.api.state.agent
    orgs = Organizations(store, world.people.ident)
    created = orgs.create(alice, "Fixture Org", "Fixture Org main", "fixture-org-freeze-1")
    oid, main = created["organization"]["id"], created["workspace"]["id"]
    cid = connect(world, "alice", owner_kind="workspace", workspace_id=main)["id"]
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    alias = op(world, "agent.mcp.binding.list", {})["output"]["tools"][0]["alias"]
    conv, turn = start(world, [call("run_operation", {"operation": f"mcp:{alias}/echo", "input": {"text": "said before the freeze"}}),
                               say("done")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    assert "said before the freeze" in str(op(world, "agent.conversation.get", {"conversation_id": conv})["output"]["messages"])

    orgs.transition(oid, alice, "freeze", reason="Fixture lifecycle check")
    assert store.one("SELECT status FROM workspaces WHERE id=?", (main,))["status"] == "active"   # the workspace row itself stays
    world.state["requests"].clear()
    connection = row(world, cid)
    assert not world.connections.may_use(alice, connection) and not world.connections.manages(alice, "workspace", main)
    # The page: not listed, and answered as if it did not exist.
    assert op(world, "agent.mcp.connection.list", {})["output"]["items"] == []
    for name in ("agent.mcp.connection.get", "agent.mcp.connection.test", "agent.mcp.calls", "agent.mcp.credential.revoke"):
        assert op(world, name, {"connection_id": cid}, expect=404)["error"] == "mcp_connection_not_found", name
    # Nor can a connection be added for it.
    refused = op(world, "agent.mcp.connection.create", {"owner_kind": "workspace", "workspace_id": main, "name": "Another",
                                                        "url": FIXTURE_URL}, expect=404)
    assert refused["error"] == "workspace_not_found"
    # The Agent: no tool, and a call that names it is not made.
    assert op(world, "agent.mcp.binding.list", {})["output"]["tools"] == []
    conv2, turn2 = start(world, [call("run_operation", {"operation": f"mcp:{alias}/echo", "input": {"text": "said during the freeze"}}),
                                 say("done")])
    wait_turn(store, turn2["turn_id"], states=("succeeded", "failed"))
    assert tool_calls(world, conv2)[0]["status"] != "succeeded"
    # The stored result of the call made before is no longer readable.
    assert "said before the freeze" not in str(op(world, "agent.conversation.get", {"conversation_id": conv})["output"]["messages"])
    assert world.state["requests"] == [], "nothing reached the server during the freeze"

    orgs.transition(oid, alice, "unfreeze", reason="Fixture lifecycle check over")
    assert [i["id"] for i in op(world, "agent.mcp.connection.list", {})["output"]["items"]] == [cid]
    assert op(world, "agent.mcp.connection.test", {"connection_id": cid})["output"]["health"] == "ok"


# ---------------------------------------------------------------- a credential for the address shown
def bearer_workspace_connection(c) -> str:
    """Alice's workspace connection with a token for FIXTURE_URL; bob is a second administrator of A."""
    p = c.people
    p.ident.change_role(p.a, p.uid("bob"), "admin")
    c.sign_in("alice")
    return op(c, "agent.mcp.connection.create", {"owner_kind": "workspace", "workspace_id": p.a, "name": "Team API", "url": FIXTURE_URL,
                                                "auth_kind": "bearer", "credential": "fixture-token-server-1-AAAA"})["output"]["id"]


def test_a_token_typed_on_a_page_that_shows_the_old_address_is_not_given_to_the_new_one(world):
    cid = bearer_workspace_connection(world)
    world.sign_in("bob")
    assert op(world, "agent.mcp.connection.update", {"connection_id": cid, "url": ELSEWHERE})["output"]["has_credential"] is False
    # Alice's page still shows the first address: her new token for it is refused, not sealed for the other server.
    world.sign_in("alice")
    refused = op(world, "agent.mcp.connection.update", {"connection_id": cid, "credential": "fixture-token-server-1-BBBB",
                                                        "expected_url": FIXTURE_URL}, expect=409)
    assert refused["error"] == "mcp_address_changed" and "BBBB" not in json.dumps(refused)
    assert row(world, cid)["credential_sealed"] is None
    world.sign_in("bob")
    world.state["requests"].clear()
    assert op(world, "agent.mcp.connection.test", {"connection_id": cid}, expect=409)["error"] == "mcp_auth_required"
    assert world.state["requests"] == []
    # A token given for the address the connection has now is written.
    world.sign_in("alice")
    saved = op(world, "agent.mcp.connection.update", {"connection_id": cid, "credential": "fixture-token-server-2-CCCC",
                                                      "expected_url": ELSEWHERE})["output"]
    assert saved["has_credential"] is True and saved["url"] == ELSEWHERE


def test_a_credential_write_holds_only_for_the_address_it_was_given_for(world, monkeypatch):
    cid = bearer_workspace_connection(world)
    real_seal = connections_module.sealed.seal
    moved = []

    def seal_while_moved(store, purpose, value):
        if not moved:
            # FIXTURE: another manager's new address lands between the update's read and its write.
            moved.append(store.execute("UPDATE mcp_connections SET url=?, credential_sealed=NULL WHERE id=?", (ELSEWHERE, cid)))
        return real_seal(store, purpose, value)
    monkeypatch.setattr(connections_module.sealed, "seal", seal_while_moved)
    with pytest.raises(ServiceError) as refused:
        world.connections.update(user(world, "alice"), cid, credential="fixture-token-server-1-DDDD", expected_url=FIXTURE_URL)
    assert refused.value.code == "mcp_address_changed" and moved == [1]
    assert row(world, cid)["url"] == ELSEWHERE and row(world, cid)["credential_sealed"] is None


# ---------------------------------------------------------------- a caller holding the connection as it was
def test_a_caller_holding_the_connection_as_it_was_gets_neither_the_new_servers_token_nor_a_refresh(world):
    cid = fixture_oauth_connection(world, expires_in=5)                       # due for a refresh
    stale = row(world, cid)                                                   # as a call read it
    world.connections.update(user(world, "alice"), cid, url=ELSEWHERE)
    fid = fixture_flow(world, cid, url=ELSEWHERE, completed=True)
    world.connections.store_tokens(cid, {"access_token": "fixture-token-for-server-2", "refresh_token": "r2", "expires_at": now() + 3600},
                                   flow_id=fid)
    world.state["requests"].clear()
    with pytest.raises(ServiceError) as refused:
        world.connections.credential(stale)
    assert refused.value.code == "mcp_connection_changed"
    assert world.state["requests"] == [], "no refresh was attempted for the old address"
    # The connection as it is now gets its own token.
    assert world.connections.credential(row(world, cid)) == "fixture-token-for-server-2"


def test_a_call_admitted_before_a_new_address_is_not_sent_there(world, monkeypatch):
    cid = connect(world)["id"]
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    alias = op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})["output"]["alias"]
    real_row = connections_module.Connections._row
    armed = [True]

    def row_after_a_new_address(self, connection_id):
        if armed and connection_id == cid:
            armed.clear()
            # FIXTURE: a manager's new address lands after the grant was read, before the call is sent.
            self.store.execute("UPDATE mcp_connections SET url=? WHERE id=?", (ELSEWHERE, cid))
        return real_row(self, connection_id)
    monkeypatch.setattr(connections_module.Connections, "_row", row_after_a_new_address)
    world.state["requests"].clear()
    with pytest.raises(ServiceError) as refused:
        world.connections.call_tool(user(world, "alice"), alias, "echo", {"text": "for the first server"}, require_mode="auto")
    assert refused.value.code == "mcp_tool_unavailable" and not armed
    assert "other-server.test" not in hosts(world) and world.state["requests"] == []
    assert world.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE connection_id=? AND kind='tools/call'", (cid,))["n"] == 0


# ---------------------------------------------------------------- a refresh overtaken
def test_a_refresh_cannot_seal_over_a_newer_authorization(world):
    cid = fixture_oauth_connection(world, expires_in=5)
    newer = {"access_token": "fixture-newer-authorization-access", "refresh_token": "fixture-newer-authorization-refresh",
             "token_type": "Bearer", "expires_at": now() + 3600}

    def meanwhile():
        # FIXTURE: what oauth.callback does for a new authorization of the same connection.
        fid = fixture_flow(world, cid, url=row(world, cid)["url"], completed=True)
        world.connections.store_tokens(cid, newer, flow_id=fid)
    world.state["tokens"] = OvertakenTokenServer("fixture-refresh-1", meanwhile)
    with pytest.raises(ServiceError) as refused:
        world.connections.credential(row(world, cid))
    assert refused.value.code == "mcp_connection_changed"
    stored = sealed_tokens(world, cid)
    assert (stored["access_token"], stored["refresh_token"]) == (newer["access_token"], newer["refresh_token"])
    assert row(world, cid)["refresh_claim_until"] is None
    assert world.connections.credential(row(world, cid)) == newer["access_token"]


def test_a_refresh_seals_only_over_the_credential_it_started_from(world):
    cid = fixture_oauth_connection(world, expires_in=5)
    other = {"access_token": "fixture-other-access", "refresh_token": "fixture-other-refresh", "expires_at": now() + 3600}

    def meanwhile():
        # FIXTURE: a token set written meanwhile by a path that leaves the refresh lease in place.
        world.connections.store_tokens(cid, other)
    world.state["tokens"] = OvertakenTokenServer("fixture-refresh-1", meanwhile)
    with pytest.raises(ServiceError) as refused:
        world.connections.credential(row(world, cid))
    assert refused.value.code == "mcp_connection_changed"
    assert sealed_tokens(world, cid)["access_token"] == "fixture-other-access"


# ---------------------------------------------------------------- what a failed refresh says
@pytest.mark.parametrize("failure, health", [
    (ServiceError(502, "mcp_unreachable", "fixture: the authorization server cannot be reached", {"retryable": True}), "failed"),
    (ServiceError(504, "mcp_timeout", "fixture: the authorization server did not answer", {"retryable": True}), "failed"),
    (ServiceError(401, "mcp_auth_required", "fixture: the grant was refused", {"cause": "invalid_grant"}), "auth_required"),
])
def test_a_failed_refresh_is_reported_as_the_refresh_said_it(world, monkeypatch, failure, health):
    cid = fixture_oauth_connection(world, expires_in=5)

    def refresh(self, conn, tokens):
        raise failure
    monkeypatch.setattr(OutboundOAuth, "refresh", refresh)
    with pytest.raises(ServiceError) as refused:
        world.connections.test(user(world, "alice"), cid, origin="discover")
    assert (refused.value.code, refused.value.detail) == (failure.code, failure.detail)
    r = row(world, cid)
    assert (r["health"], r["last_error_code"]) == (health, failure.code)
    assert r["failure_streak"] == (1 if health == "failed" else 0)


# ---------------------------------------------------------------- results the run record can hold
def large_write_server():
    server = MCPServer("Fixture large answers")
    server.notes = []

    @server.tool(name="add_note", description="Store a note. Changes data.", annotations=ToolAnnotations(read_only_hint=False))
    def add_note(text: str) -> CallToolResult:
        server.notes.append(text)
        body = "中" * 190_000                       # FIXTURE: a long answer, as text and as structured content
        return CallToolResult(content=[TextContent(type="text", text=body)], structured_content={"t": body}, is_error=False)
    return server


def test_a_confirmed_write_whose_answer_is_too_large_to_record_is_recorded_as_done(world):
    store = world.store
    world.agent = world.api.state.agent
    publish(world, max_result_chars=200_000)
    server = large_write_server()
    world.state["app"] = LazyFixtureApp(server)
    cid = connect(world)["id"]
    alias = op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})["output"]["alias"]
    conv, turn = start(world, [call("run_operation", {"operation": f"mcp:{alias}/add_note", "input": {"text": "once"}}), say("done")])
    assert wait_turn(store, turn["turn_id"]) == "waiting_confirmation"
    card = next(tc for tc in tool_calls(world, conv) if tc["status"] == "awaiting_confirmation")
    op(world, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["confirmation"]})
    wait_turn(store, turn["turn_id"], states=("succeeded", "failed"))
    assert server.notes == ["once"]
    recorded = store.one("SELECT status, error_code FROM agent_tool_calls WHERE id=?", (card["id"],))
    assert (recorded["status"], recorded["error_code"]) == ("succeeded", None)
    run = store.one("SELECT status, output FROM op_runs WHERE op='agent.mcp.tool.call'")
    output = json.loads(run["output"]) if isinstance(run["output"], str) else run["output"]
    assert run["status"] == "succeeded" and len(canonical(output).encode()) <= MAX_OUTPUT_BYTES
    assert output["truncated"] is True and output["structured"] is None and output["text"].startswith("中")


def test_a_tool_result_is_cut_to_the_run_records_room_text_last(world, monkeypatch):
    cid = connect(world)["id"]
    op(world, "agent.mcp.grant.set", {"connection_id": cid, "tool": "echo", "mode": "auto"})
    alias = op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})["output"]["alias"]
    publish(world, max_result_chars=200_000)
    # FIXTURE answers, each within the policy's characters: text and structured content that only
    # together exceed what a run record holds, and a text that does alone once escaped in JSON.
    wide, escaped = "中" * 190_000, "\x01" * 200_000
    for structured, text in (({"t": wide}, wide), (None, escaped)):
        monkeypatch.setattr(world.connections.runtime, "call_tool", lambda *a, structured=structured, text=text, **k: ToolOutcome(
            is_error=False, content=[{"type": "text", "text": text}], structured=structured, text=text, duration_ms=1))
        out = world.connections.call_tool(user(world, "alice"), alias, "echo", {"text": "x"}, require_mode="auto")
        size = len(canonical(out).encode())
        assert size <= MAX_OUTPUT_BYTES and out["truncated"] is True and out["structured"] is None
        assert size <= connections_module.RESULT_BUDGET                 # with room to spare
        if structured is not None:
            assert out["text"] == wide                                  # the structured copy goes first, the text stays whole
        else:
            assert 0 < len(out["text"]) < len(escaped) and escaped.startswith(out["text"])
    # One that fits is untouched.
    monkeypatch.setattr(world.connections.runtime, "call_tool", lambda *a, **k: ToolOutcome(
        is_error=False, content=[], structured={"ok": True}, text="small", duration_ms=1))
    out = world.connections.call_tool(user(world, "alice"), alias, "echo", {"text": "x"}, require_mode="auto")
    assert (out["text"], out["structured"], out["truncated"]) == ("small", {"ok": True}, False)


def test_a_resource_read_is_cut_to_the_run_records_room_its_contents_metadata_first(world, monkeypatch):
    cid = connect(world)["id"]
    publish(world, max_result_chars=200_000)
    contents = [{"uri": f"notes://fixture/{i}", "mimeType": "text/plain", "_meta": {"pad": "中" * 1000}} for i in range(400)]
    monkeypatch.setattr(world.connections.runtime, "read_resource", lambda *a, **k: ResourceOutcome(
        contents=contents, text="中" * 190_000, bytes_total=570_000, duration_ms=1))
    out = world.connections.read_resource_direct(user(world, "alice"), row(world, cid), "notes://fixture/0")
    size = len(canonical(out).encode())
    assert size <= MAX_OUTPUT_BYTES and out["truncated"] is True
    assert size <= connections_module.RESULT_BUDGET                     # with room to spare
    assert out["contents"][0] == {"uri": "notes://fixture/0", "mimeType": "text/plain"} and len(out["contents"]) == 400
    assert out["text"] == "中" * 190_000


def test_a_busy_resolver_is_this_process_refusing_and_leaves_the_connections_health_alone(world, monkeypatch):
    cid = connect(world)["id"]
    before = row(world, cid)

    def busy(*args, **kwargs):
        # FIXTURE: this process's resolver pool refuses the lookup (runtime: webhook_delivery.ResolverBusy).
        raise ServiceError(429, "mcp_resolver_busy", "fixture: resolver pool full", {"retryable": True, "retry_after": 2})
    monkeypatch.setattr(world.connections.runtime, "discover", busy)
    with pytest.raises(ServiceError) as refused:
        world.connections.test(user(world, "alice"), cid)
    assert refused.value.code == "mcp_resolver_busy" and "mcp_resolver_busy" not in connections_module.SERVER_FAILURES
    after = row(world, cid)
    assert (after["health"], after["failure_streak"], after["last_error_code"]) == (before["health"], 0, before["last_error_code"])
