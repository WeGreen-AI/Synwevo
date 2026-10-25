"""The Agent's engine with external MCP tools: whose content a workspace connection's result is, and
what a call whose operation ran but could not be recorded is.

TEST DOUBLES, each labelled: the model is `ScriptedModel` (test_agent.py); the external MCP server is
the FIXTURE server of test_agent_mcp.py, served in process. Where a test needs a result too large to
record, or a run record that cannot be written, a FIXTURE wrapper makes it so after the real call.
"""
from __future__ import annotations

import json

from mcp.server import MCPServer
from mcp_types import CallToolResult, TextContent, ToolAnnotations

from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy
from synwevo_suite.agent.mcp.tools import REGISTERED_CALL

from .test_agent import call, op, say, wait_turn
from .test_agent_mcp import LazyFixtureApp, connect, start, tool_calls, world  # noqa: F401 (fixture)

SECRET = "TEAM-SECRET from workspace A's server"


def processing(c, wid, *, outbound=None, providers=None):
    """FIXTURE: the policy rows as the policy operations write them. The workspace lets the assistant
    process its content; `outbound`, when given, is its data-flow outbound policy (a shared workspace
    keeps content in by default); `providers`, when given, the model providers it allows."""
    store = c.people.store
    store.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,?,?)",
                  (wid, "any", c.people.uid("alice"), 1.0))
    if outbound:
        store.execute("INSERT INTO workspace_transfer_policies (workspace_id, outbound, revision, updated_by, updated_at) "
                      "VALUES (?,?,?,?,?)", (wid, outbound, 1, c.people.uid("alice"), 1.0))
    if providers is not None:
        store.execute("INSERT INTO agent_workspace_model_providers (workspace_id, providers_json, updated_by, updated_at) "
                      "VALUES (?,?,?,?)", (wid, json.dumps(providers), c.people.uid("alice"), 1.0))


def team_and_mine(c, owner="alice", workspace=None) -> None:
    """A workspace's connection (`team`, echo granted auto) and alice's own one (`mine`, add_note at
    the first look's confirm), both bound for alice's Agent."""
    shared = connect(c, owner, owner_kind="workspace", workspace_id=workspace or c.people.a, name="Team notes")
    op(c, "agent.mcp.grant.set", {"connection_id": shared["id"], "tool": "echo", "mode": "auto"})
    c.sign_in("alice")
    op(c, "agent.mcp.binding.set", {"connection_id": shared["id"], "enabled": True, "alias": "team"})
    mine = connect(c, "alice", name="My notes")
    op(c, "agent.mcp.binding.set", {"connection_id": mine["id"], "enabled": True, "alias": "mine"})


def heard(c, step) -> str:
    """What the model was handed as tool results before its step `step` (0-based)."""
    return json.dumps([b for b in c.agent.provider.requests[step]["messages"][-1]["content"] if b.get("type") == "tool_result"],
                      ensure_ascii=False)


def told(c, conv, operation) -> dict:
    row = c.people.store.one("SELECT for_model_json FROM agent_tool_calls WHERE conversation_id=? AND operation=?", (conv, operation))
    return json.loads(row["for_model_json"])


# ---------------------------------------------------------------------------------- S2: a workspace connection's result
def test_a_workspace_connections_result_is_kept_from_the_model_as_the_workspace_keeps_its_content(world):
    """Workspace A keeps its content from the assistant (a shared workspace's default), or allows it
    only for another model provider: what A's connection answers is A's content, withheld alike."""
    store = world.people.store
    team_and_mine(world)
    for allowed in (None, ["deepseek"]):
        if allowed is not None:
            processing(world, world.people.a, providers=allowed)      # processing allowed, not with this provider
        conv, turn = start(world, [call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}),
                                   say("I cannot read that result")])
        assert wait_turn(store, turn["turn_id"]) == "succeeded"
        shown = tool_calls(world, conv)[0]
        assert shown["status"] == "succeeded" and shown["result"]["text"] == SECRET   # the person sees it
        assert told(world, conv, "mcp:team/echo")["withheld"] is True
        assert SECRET not in heard(world, 1) and "withheld" in heard(world, 1)


def test_a_workspace_connections_result_the_model_read_is_that_workspaces_content_for_external_calls(world):
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)                       # A lets the assistant read its content; outbound stays 'audience'
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:mine/add_note", "input": {"text": SECRET}}, tid="tu_2"),
        say("A keeps its content in"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    assert SECRET in heard(world, 1)
    assert told(world, conv, "mcp:team/echo")["source_workspaces"] == [a]
    refused = {tc["operation"]: tc for tc in tool_calls(world, conv)}["mcp:mine/add_note"]
    assert refused["status"] == "failed" and refused["error"]["code"] == "mcp_transfer_refused"
    assert world.state["server"].notes == []
    assert store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE name='add_note'")["n"] == 0     # nothing left
    # A lets its content leave the platform: the same call is shown to alice to confirm
    store.execute("INSERT INTO workspace_transfer_policies (workspace_id, outbound, revision, updated_by, updated_at) "
                  "VALUES (?,?,?,?,?)", (a, "any", 1, world.people.uid("alice"), 1.0))
    conv2, turn2 = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:mine/add_note", "input": {"text": SECRET}}, tid="tu_2"),
    ])
    assert wait_turn(store, turn2["turn_id"]) == "waiting_confirmation"
    assert {tc["operation"]: tc for tc in tool_calls(world, conv2)}["mcp:mine/add_note"]["status"] == "awaiting_confirmation"


def test_a_personal_connections_result_is_still_nobodys_workspace_content(world):
    store = world.people.store
    team_and_mine(world)
    mine = store.one("SELECT connection_id FROM mcp_agent_bindings WHERE alias='mine'")["connection_id"]
    op(world, "agent.mcp.grant.set", {"connection_id": mine, "tool": "echo", "mode": "auto"})
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:mine/echo", "input": {"text": "mine"}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:mine/add_note", "input": {"text": "mine"}}, tid="tu_2"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "waiting_confirmation"
    echo = told(world, conv, "mcp:mine/echo")
    assert echo["text"] == "mine" and echo["source_workspaces"] == []
    assert {tc["operation"]: tc for tc in tool_calls(world, conv)}["mcp:mine/add_note"]["status"] == "awaiting_confirmation"


def test_a_workspace_connections_result_counts_for_a_write_into_another_workspace(world):
    """Oscar's workspace B owns a connection that alice, a viewer in B without the right to export,
    may use: what it answers is B's content, which a write into alice's workspace A would carry."""
    people = world.people
    team_and_mine(world, owner="oscar", workspace=people.b)
    processing(world, people.b)
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "B's roster"}}, tid="tu_1"),
        call("run_operation", {"operation": "forms.form.create", "workspace_id": people.a, "input": {"title": "B's roster"}},
             tid="tu_2"),
        say("B's content stays in B"),
    ])
    assert wait_turn(people.store, turn["turn_id"]) == "succeeded"
    write = {tc["operation"]: tc for tc in tool_calls(world, conv)}["forms.form.create"]
    assert write["status"] == "failed" and write["error"]["code"] == "transfer_export_denied"


# ---------------------------------------------------------------------------------- C1: ran, but not recorded
def oversized(c, monkeypatch, tool):
    """FIXTURE: the server's answer to `tool`, after the real call, made larger than a run record holds
    (about 1.2 MB in UTF-8), whatever the connection service cut it to."""
    connections = c.agent.connections
    real = connections.call_tool

    def call_tool(user, alias, name, arguments, **kw):
        out = real(user, alias, name, arguments, **kw)
        return {**out, "text": "中" * 400_000} if name == tool else out
    monkeypatch.setattr(connections, "call_tool", call_tool)


def personal(c, *, auto=()) -> str:
    detail = connect(c)
    for tool in auto:
        op(c, "agent.mcp.grant.set", {"connection_id": detail["id"], "tool": tool, "mode": "auto"})
    op(c, "agent.mcp.binding.set", {"connection_id": detail["id"], "enabled": True})
    return detail["id"]


def confirm_the_card(c, conv) -> dict:
    card = next(tc for tc in tool_calls(c, conv) if tc["status"] == "awaiting_confirmation")
    return op(c, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["confirmation"]})["output"]


def assert_unknown(c, conv, turn, operation, cause):
    store = c.people.store
    assert wait_turn(store, turn["turn_id"], states=("succeeded", "failed", "cancelled")) == "failed"
    row = store.one("SELECT * FROM agent_tool_calls WHERE conversation_id=? AND operation=?", (conv, operation))
    assert row["status"] == "unknown" and row["error_code"] == "outcome_unresolved" and row["run_id"]
    payload = json.loads(row["for_model_json"])
    assert payload["outcome"] == "unknown" and payload["cause"] == cause and "Do not run it again" in payload["error"]["message"]
    # The run is on record as the handler left it, never failed; the turn stops on the open outcome,
    # so the model is not asked again in it and cannot repeat the call blindly.
    assert store.one("SELECT status FROM op_runs WHERE id=?", (row["run_id"],))["status"] == "running"
    assert store.one("SELECT error_code FROM agent_turns WHERE id=?", (turn["turn_id"],))["error_code"] == "outcome_unresolved"
    assert len(c.agent.provider.requests) == 1
    shown = next(tc for tc in tool_calls(c, conv) if tc["operation"] == operation)
    assert shown["status"] == "unknown" and shown["error"]["code"] == "outcome_unresolved"


def test_a_confirmed_external_write_whose_result_cannot_be_recorded_is_unknown_not_failed(world, monkeypatch):
    personal(world)
    oversized(world, monkeypatch, "add_note")
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "once"}}),
                               say("it failed, I will try again")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "waiting_confirmation"
    assert confirm_the_card(world, conv)["status"] == "unknown"
    assert world.state["server"].notes == ["once"]
    assert_unknown(world, conv, turn, "mcp:fixture-notes/add_note", "output_too_large")


def test_a_read_without_asking_whose_result_cannot_be_recorded_is_unknown_too(world, monkeypatch):
    """The server's read-only mark is its own claim: what its tool did is not known either."""
    personal(world, auto=("echo",))
    oversized(world, monkeypatch, "echo")
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "x"}}),
                               say("unused")])
    assert_unknown(world, conv, turn, "mcp:fixture-notes/echo", "output_too_large")


def test_any_failure_after_the_handler_ran_is_unknown(world, monkeypatch):
    personal(world)
    caps = world.agent.caps
    real = caps._finish

    def finish(t, call_, result):
        if call_.op.id == REGISTERED_CALL:
            raise RuntimeError("FIXTURE: the run record could not be written")
        return real(t, call_, result)
    monkeypatch.setattr(caps, "_finish", finish)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "once"}}),
                               say("unused")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "waiting_confirmation"
    assert confirm_the_card(world, conv)["status"] == "unknown"
    assert world.state["server"].notes == ["once"]
    assert_unknown(world, conv, turn, "mcp:fixture-notes/add_note", "operation_failed")


def test_a_carriers_own_error_is_still_a_failure_the_model_hears(world, monkeypatch):
    """Only what follows a handler that ran is unknown: the handler's own error fails its run."""
    personal(world)

    def call_tool(user, alias, name, arguments, **kw):
        raise ServiceError(502, "mcp_server_error", "外部服务出错")       # FIXTURE: the server answered with an error
    monkeypatch.setattr(world.agent.connections, "call_tool", call_tool)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "once"}}),
                               say("the server failed")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "waiting_confirmation"
    assert confirm_the_card(world, conv)["status"] == "failed"
    assert wait_turn(world.people.store, turn["turn_id"], states=("succeeded", "failed")) == "succeeded"
    row = world.people.store.one("SELECT status, error_code FROM agent_tool_calls WHERE conversation_id=?", (conv,))
    assert (row["status"], row["error_code"]) == ("failed", "mcp_server_error")


def test_a_long_answer_within_the_policys_characters_never_records_a_write_as_failed(world):
    """The verifier's case: a platform administrator allows 200,000 characters, and a write answers
    with 190,000 Chinese characters as text and as structured content (about 1.1 MB together). The
    connection service fits it to its run record, or else the engine says the outcome is unknown:
    never that a write the server carried out failed."""
    store = world.people.store
    with store.tx():
        state = McpPolicy(store).read()
        McpPolicy(store).append({**state["spec"], "max_result_chars": 200_000}, expected_revision=state["revision"],
                                actor_id=world.people.uid("alice"), reason="fixture: larger results")
    server = MCPServer("Fixture big")
    server.notes = []

    @server.tool(name="add_note", description="Store a note. Changes data.", annotations=ToolAnnotations(read_only_hint=False))
    def add_note(text: str) -> CallToolResult:
        server.notes.append(text)
        body = "中" * 190_000                       # FIXTURE: a long Chinese answer, as text and as structured content
        return CallToolResult(content=[TextContent(type="text", text=body)], structured_content={"t": body}, is_error=False)
    world.state["app"] = LazyFixtureApp(server)
    personal(world)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "once"}}),
                               say("done")])
    assert wait_turn(store, turn["turn_id"]) == "waiting_confirmation"
    assert confirm_the_card(world, conv)["status"] in ("succeeded", "unknown")
    wait_turn(store, turn["turn_id"], states=("succeeded", "failed"))
    row = store.one("SELECT status FROM agent_tool_calls WHERE conversation_id=?", (conv,))
    assert server.notes == ["once"] and row["status"] in ("succeeded", "unknown")
