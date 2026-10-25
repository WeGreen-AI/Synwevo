"""The Agent's MCP carriers and what they guard: who may reach them, the data-flow question at the
moment a request leaves, run history that never calls a server again, results that follow the
connection they came through, and untrusted tool descriptions and input schemas.

TEST DOUBLES, each labelled: the model is `ScriptedModel` (test_agent.py) replaying fixed steps; the
external MCP server is the FIXTURE server of test_agent_mcp.py (the official SDK's server, served in
process). Some tests change what a fixture server published by writing the discovered tool row the
way `_record_discovery` stores it; that is a fixture of the server's answer, not of the product.
"""
from __future__ import annotations

import dataclasses
import json
import urllib.request
from types import SimpleNamespace

import pytest

from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError
from synwevo_suite.agent.mcp.tools import (CARRIERS, REGISTERED_CALL, REGISTERED_READ, McpTools, McpVirtualOp,
                                           result_connection)
from synwevo_suite.agent.provider import StepResult

from .test_agent import ScriptedModel, call, op, say, wait_turn
from .test_agent_mcp import connect, start, tool_calls, world  # noqa: F401 — `world` is the fixture

ALIAS = "fixture-notes"
CARRIER_INPUTS = {
    REGISTERED_READ: {"connection": ALIAS, "tool": "add_note", "arguments": {"text": "pwned"}},
    REGISTERED_CALL: {"connection": ALIAS, "tool": "add_note", "arguments": {"text": "pwned"}},
    "agent.mcp.resource.read": {"connection": ALIAS, "uri": "notes://all"},
    "agent.mcp.resource.list": {"connection": ALIAS},
}


def calls(*uses):
    """TEST DOUBLE step: several tool calls in one model step, as (tool, input, id) triples."""
    return StepResult(content=[{"type": "tool_use", "id": tid, "name": name, "input": args} for name, args, tid in uses],
                      stop_reason="tool_use", usage={"output_tokens": 7})


def bound(c, *, auto=("echo",), resources=False) -> str:
    """Alice's own fixture connection, bound for her Agent; `auto` tools run without asking."""
    detail = connect(c)
    for tool in auto:
        op(c, "agent.mcp.grant.set", {"connection_id": detail["id"], "tool": tool, "mode": "auto"})
    op(c, "agent.mcp.binding.set", {"connection_id": detail["id"], "enabled": True, "resources_enabled": resources})
    return detail["id"]


def agent_calls(c) -> int:
    return c.people.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE origin='agent'")["n"]


def open_workspace_to_the_model(c, wid, *, outbound=None):
    """FIXTURE: the policy rows as the policy operations write them. The workspace lets the
    assistant process its content; `outbound`, when given, is its data-flow outbound policy (a
    shared workspace defaults to keeping content in)."""
    store = c.people.store
    store.execute("INSERT INTO agent_workspace_policies (workspace_id, processing, updated_by, updated_at) VALUES (?,?,?,?)",
                  (wid, "any", c.people.uid("alice"), 1.0))
    if outbound:
        store.execute("INSERT INTO workspace_transfer_policies (workspace_id, outbound, revision, updated_by, updated_at) "
                      "VALUES (?,?,?,?,?)", (wid, outbound, 1, c.people.uid("alice"), 1.0))


# ---------------------------------------------------------------------------------- who reaches the carriers
def test_the_model_can_neither_see_nor_name_the_carriers(world):
    bound(world, resources=True)                 # add_note stays at the first look's "confirm"
    # The first query is every word of the carriers' ids, which ranks them first wherever they are
    # offered; the second finds the external tool the model does reach, by its own name.
    uses = [("find_operations", {"query": "mcp tool resource read call list"}, "tu_q"),
            ("find_operations", {"query": "fixture notes echo"}, "tu_e")]
    uses += [("find_operations", {"operation": name}, f"tu_f{n}") for n, name in enumerate(CARRIER_INPUTS)]
    uses += [("run_operation", {"operation": name, "input": inp}, f"tu_r{n}") for n, (name, inp) in enumerate(CARRIER_INPUTS.items())]
    conv, turn = start(world, [calls(*uses), say("done")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    by_use = {tc["id"]: tc for tc in tool_calls(world, conv)}
    rows = world.people.store.all("SELECT id, tool_use_id FROM agent_tool_calls WHERE conversation_id=?", (conv,))
    by_tid = {r["tool_use_id"]: by_use[r["id"]] for r in rows}
    listed = {o["id"] for o in by_tid["tu_q"]["result"]["operations"]}
    # what the model reaches resources through is the virtual operation, never its carrier
    assert not listed & CARRIERS and {"mcp:fixture-notes/resources.list", "mcp:fixture-notes/resources.read"} <= listed
    assert "mcp:fixture-notes/echo" in {o["id"] for o in by_tid["tu_e"]["result"]["operations"]}
    for n in range(len(CARRIER_INPUTS)):
        assert by_tid[f"tu_f{n}"]["result"]["error"]["code"] == "unknown_operation"
        assert by_tid[f"tu_r{n}"]["status"] == "failed" and by_tid[f"tu_r{n}"]["error"]["code"] == "unknown_operation"
    system = world.agent.provider.requests[0]["system"][1]["text"]
    assert "mcp:fixture-notes/add_note" in system and not any(name in system for name in CARRIERS)
    # nothing left the platform, and no carrier ran
    assert world.state["server"].notes == [] and agent_calls(world) == 0
    assert world.people.store.one(f"SELECT COUNT(*) AS n FROM op_runs WHERE op IN ({','.join('?' * len(CARRIERS))})",
                                  tuple(CARRIERS))["n"] == 0


def test_a_person_or_script_cannot_call_the_carriers_directly(world):
    """Decision: the carriers exist for the engine's execution of a model's mcp: operation. A page
    or script calling them would make an "agent" call with no conversation behind it, no card, no
    data-flow question; the person uses a connection through their Agent or its page operations."""
    bound(world, resources=True)
    for name, inp in {**CARRIER_INPUTS, REGISTERED_READ: {"connection": ALIAS, "tool": "echo", "arguments": {"text": "x"}}}.items():
        assert op(world, name, inp, expect=403)["error"] == "mcp_agent_only", name
    assert world.state["server"].notes == [] and agent_calls(world) == 0


def test_the_read_carrier_runs_only_a_tool_granted_auto_and_marked_read_only(world, monkeypatch):
    """The engine's view of a tool can be a moment old (the grant was auto when the call was
    prepared). The read carrier asks the service again as the call is made (contract C1)."""
    bound(world)
    real_get = McpTools.get

    def stale(self, op_id):
        vop = real_get(self, op_id)
        if vop is not None and vop.tool == "add_note":
            # FIXTURE: what an auto grant of add_note looked like a moment before it was set back to confirm.
            return dataclasses.replace(vop, side_effect="none", agent="auto", registered=REGISTERED_READ)
        return vop
    monkeypatch.setattr(McpTools, "get", stale)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "x"}}),
                               say("done")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    tc = tool_calls(world, conv)[0]
    assert tc["status"] == "failed" and tc["error"]["code"] == "mcp_confirmation_required", tc
    assert world.state["server"].notes == []
    assert world.people.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE name='add_note'")["n"] == 0


# ---------------------------------------------------------------------------------- data flow when the request leaves
def test_a_confirmed_call_is_refused_when_the_outbound_policy_tightened_while_the_card_waited(world):
    bound(world, auto=())
    a = world.people.a
    open_workspace_to_the_model(world, a, outbound="any")
    conv, turn = start(world, [
        call("run_operation", {"operation": "forms.form.list", "workspace_id": a, "input": {}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:fixture-notes/add_note", "input": {"text": "workspace content"}}, tid="tu_2"),
        say("done"),
    ])
    assert wait_turn(world.people.store, turn["turn_id"]) == "waiting_confirmation"
    note = {tc["operation"]: tc for tc in tool_calls(world, conv)}["mcp:fixture-notes/add_note"]
    assert note["status"] == "awaiting_confirmation"
    # the owner stops content leaving the workspace before the person answers the card
    world.people.store.execute("UPDATE workspace_transfer_policies SET outbound='audience', revision=2 WHERE workspace_id=?", (a,))
    done = op(world, "agent.tool_call.confirm", {"tool_call_id": note["id"], "confirmation": note["confirmation"]})["output"]
    assert done["status"] == "failed" and done["error"]["code"] == "mcp_transfer_refused"
    assert wait_turn(world.people.store, turn["turn_id"], states=("succeeded", "failed")) == "succeeded"
    assert world.state["server"].notes == [] and agent_calls(world) == 0
    # the decision itself refused it: the carrier (which would ask again) was never started
    assert world.people.store.one("SELECT COUNT(*) AS n FROM op_runs WHERE op=?", (REGISTERED_CALL,))["n"] == 0


def test_the_carrier_asks_the_data_flow_question_itself(world, monkeypatch):
    """Whatever the engine decided before, the carrier asks again at the moment the request would
    leave: an engine path that skipped the question still cannot send workspace content out."""
    bound(world)
    a = world.people.a
    open_workspace_to_the_model(world, a)        # outbound stays at the shared workspace's default
    # FIXTURE: an engine that forgot the question before running the call.
    monkeypatch.setattr(McpTools, "outbound_refusal", lambda self, sources: None)
    conv, turn = start(world, [
        call("run_operation", {"operation": "forms.form.list", "workspace_id": a, "input": {}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "leak"}}, tid="tu_2"),
        say("done"),
    ])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    echo = {tc["operation"]: tc for tc in tool_calls(world, conv)}["mcp:fixture-notes/echo"]
    assert echo["status"] == "failed" and echo["error"]["code"] == "mcp_transfer_refused", echo
    assert agent_calls(world) == 0


# ---------------------------------------------------------------------------------- history never calls again
def test_run_history_and_replays_never_call_the_server_again(world):
    bound(world, resources=True)
    conv, turn = start(world, [calls(("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "ping"}}, "tu_1"),
                                     ("run_operation", {"operation": "mcp:fixture-notes/resources.list", "input": {}}, "tu_2"),
                                     ("run_operation", {"operation": "mcp:fixture-notes/resources.read",
                                                        "input": {"uri": "notes://all"}}, "tu_3")),
                               say("ok")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    store, caps = world.people.store, world.api.state.capabilities
    requests, recorded = world.state["app"].requests, store.one("SELECT COUNT(*) AS n FROM mcp_calls")["n"]
    runs = [r for r in world.get("/api/suite/runs").json()["runs"] if r["operation"] in CARRIERS]
    assert sorted(r["operation"] for r in runs) == ["agent.mcp.resource.list", "agent.mcp.resource.read", "agent.mcp.tool.read"]
    for r in runs:
        # the person may still use the connection: the stored result is shown as it was, not rebuilt
        assert r["snapshot_hidden"] is False and r["output"] is not None, r
        assert world.get(f"/api/suite/runs/{r['id']}").json()["snapshot_hidden"] is False
    echo = next(r for r in runs if r["operation"] == REGISTERED_READ)
    assert echo["output"]["text"] == "ping"
    # an idempotent replay hands back the stored result
    row = store.one("SELECT * FROM op_runs WHERE id=?", (echo["id"],))
    again = caps.invoke(REGISTERED_READ, json.loads(row["input"]), user_id=row["user_id"], via="agent",
                        idempotency_key=row["idempotency_key"])
    assert again["replayed"] is True and again["output"]["call_id"] == echo["output"]["call_id"]
    # and nothing re-reads a carrier to look at it
    with pytest.raises(ServiceError) as refused:
        caps.inspect(REGISTERED_READ, json.loads(row["input"]), user_id=row["user_id"])
    assert refused.value.code == "source_unverifiable"
    assert world.state["app"].requests == requests and store.one("SELECT COUNT(*) AS n FROM mcp_calls")["n"] == recorded
    for name in CARRIERS:
        carrier = caps.registry.get(name)
        assert carrier.result_guard is not None and carrier.rereadable is False, name


def test_a_read_that_is_never_run_again_must_say_how_its_result_is_checked():
    from pydantic import BaseModel
    with pytest.raises(ValueError, match="result_guard"):
        Operation(id="fixture.read", app="fixture", title={"en": "x", "zh-Hans": "x", "zh-Hant": "x"}, summary="fixture",
                  input=BaseModel, handler=lambda c, i: None, rereadable=False)


# ---------------------------------------------------------------------------------- results follow the connection
@pytest.mark.parametrize("change", ["left the workspace", "workspace frozen"])
def test_results_through_a_workspace_connection_follow_the_persons_use_of_it(world, change):
    people = world.people
    shared = connect(world, "alice", owner_kind="workspace", workspace_id=people.a, name="Team notes")
    op(world, "agent.mcp.grant.set", {"connection_id": shared["id"], "tool": "echo", "mode": "auto"})
    world.sign_in("bob")
    alias = op(world, "agent.mcp.binding.set", {"connection_id": shared["id"], "enabled": True})["output"]["alias"]
    conv, turn = start(world, [call("run_operation", {"operation": f"mcp:{alias}/echo", "input": {"text": "secret-ping"}}),
                               say("the server said secret-ping")])
    assert wait_turn(people.store, turn["turn_id"]) == "succeeded"
    before = op(world, "agent.conversation.get", {"conversation_id": conv})["output"]
    assert before["sealed"] is None and tool_calls(world, conv)[0]["result"]["text"] == "secret-ping"
    if change == "left the workspace":
        people.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (people.a, people.uid("bob")))
    else:
        people.store.execute("UPDATE workspaces SET status='frozen' WHERE id=?", (people.a,))
    op(world, "agent.mcp.connection.get", {"connection_id": shared["id"]}, expect=404)
    after = op(world, "agent.conversation.get", {"conversation_id": conv})["output"]
    assert after["sealed"] == "access_changed"
    answer = next(m for m in after["messages"] if m["role"] == "assistant")
    assert answer["hidden"] and answer["body"] == ""
    tc = answer["tool_calls"][0]
    assert tc["hidden"] and tc["result"] is None and tc["mcp"] is None and tc["input"] is None
    # nothing of it reaches the model again: the conversation goes no further
    world.agent.provider = ScriptedModel([say("x")])
    assert op(world, "agent.turn.start", {"conversation_id": conv, "text": "more"}, expect=409)["error"] == "conversation_sealed"
    assert world.agent.provider.requests == []
    run = next(r for r in world.get("/api/suite/runs").json()["runs"] if r["operation"] == REGISTERED_READ)
    assert run["snapshot_hidden"] is True and run["output"] is None


def test_a_personal_connections_results_do_not_depend_on_any_workspace(world):
    bound(world)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "mine"}}),
                               say("ok")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    asst = world.people.store.one("SELECT sources_json FROM agent_messages WHERE conversation_id=? AND role='assistant'", (conv,))
    assert json.loads(asst["sources_json"] or "[]") == []
    # alice leaves the workspace she is a viewer in: her own connection's result stays hers
    world.people.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (world.people.b, world.people.uid("alice")))
    assert op(world, "agent.conversation.get", {"conversation_id": conv})["output"]["sealed"] is None


def test_stored_results_follow_the_connection_they_came_through_not_the_alias(world):
    """A tool's result names its call record, and a resource list (it makes no request) names its
    connection: an alias is only a name the person can give another connection afterwards."""
    people = world.people
    shared = connect(world, "alice", owner_kind="workspace", workspace_id=people.a, name="Team notes")
    op(world, "agent.mcp.grant.set", {"connection_id": shared["id"], "tool": "echo", "mode": "auto"})
    world.sign_in("bob")
    alias = op(world, "agent.mcp.binding.set", {"connection_id": shared["id"], "enabled": True,
                                                "resources_enabled": True})["output"]["alias"]
    conv, turn = start(world, [calls(("run_operation", {"operation": f"mcp:{alias}/echo", "input": {"text": "team"}}, "tu_1"),
                                     ("run_operation", {"operation": f"mcp:{alias}/resources.list", "input": {}}, "tu_2")),
                               say("done")])
    assert wait_turn(people.store, turn["turn_id"]) == "succeeded"
    by_op = {tc["operation"]: tc for tc in tool_calls(world, conv)}
    assert by_op[f"mcp:{alias}/echo"]["result"]["text"] == "team"
    listed = by_op[f"mcp:{alias}/resources.list"]
    assert listed["status"] == "succeeded" and listed["result"]["connection_id"] == shared["id"]
    assert [r["uri"] for r in listed["result"]["resources"]] == ["notes://all"]
    # bob renames that binding and gives the alias to a connection of his own; then he leaves A
    op(world, "agent.mcp.binding.set", {"connection_id": shared["id"], "alias": "old-team"})
    mine = connect(world, "bob")
    op(world, "agent.mcp.grant.set", {"connection_id": mine["id"], "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": mine["id"], "enabled": True, "resources_enabled": True, "alias": alias})
    people.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (people.a, people.uid("bob")))
    runs = [r for r in world.get("/api/suite/runs").json()["runs"] if r["operation"] in CARRIERS]
    assert sorted(r["operation"] for r in runs) == ["agent.mcp.resource.list", REGISTERED_READ]
    assert all(r["snapshot_hidden"] is True and r["output"] is None for r in runs), runs
    assert op(world, "agent.conversation.get", {"conversation_id": conv})["output"]["sealed"] == "access_changed"


def test_a_result_naming_a_call_record_that_is_gone_is_never_given_the_aliass_connection(world):
    cid = bound(world)
    store, alice = world.people.store, world.people.uid("alice")
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "mine"}}),
                               say("ok")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    stored = tool_calls(world, conv)[0]["result"]
    assert result_connection(store, alice, ALIAS, stored)["id"] == cid
    # FIXTURE: a call id with no record (none is ever removed today; the rule must not depend on that)
    assert result_connection(store, alice, ALIAS, {**stored, "call_id": "mcpcall_000000000000"}) is None
    # someone else's call record is not theirs to name either
    assert result_connection(store, world.people.uid("bob"), ALIAS, stored) is None
    # a result that names no record at all is the alias's (what was recorded before results named one)
    assert result_connection(store, alice, ALIAS, {"text": "mine"})["id"] == cid


# ---------------------------------------------------------------------------------- untrusted descriptions and schemas
@pytest.mark.parametrize("description,title,shown", [
    ("  \n ", None, "echo"),
    ("", "   ", "echo"),
    ("\n\n  Echo text back.  \nSecond line.", None, "Echo text back."),
])
def test_blank_or_many_line_tool_descriptions_never_break_a_turn(world, description, title, shown):
    cid = bound(world)
    # FIXTURE: the server published these for echo (stored as discovery stores them).
    world.people.store.execute("UPDATE mcp_connection_tools SET description=?, title=? WHERE connection_id=? AND name='echo'",
                               (description, title, cid))
    conv, turn = start(world, [call("find_operations", {"operation": "mcp:fixture-notes/echo"}, tid="tu_1"),
                               call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "ping"}}, tid="tu_2"),
                               say("ok")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    system = world.agent.provider.requests[0]["system"][1]["text"]
    assert f"- mcp:fixture-notes/echo: [Fixture notes] {shown}\n" in system + "\n"
    found, ran = sorted(tool_calls(world, conv), key=lambda tc: tc["created_at"])
    assert found["result"]["summary"].strip() and ran["status"] == "succeeded" and ran["result"]["text"] == "ping"


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"text": {"$ref": "#/$defs/Missing"}}},
    {"type": "object", "properties": {"text": {"$ref": "https://fixture-schemas.test/text.json"}}},
    {"type": "object", "properties": {"text": {"$ref": "http://169.254.169.254/latest/meta-data/"}}},
    {"$schema": "https://fixture-schemas.test/not-a-dialect", "type": "object"},
    {"$schema": 7, "type": "object"},
], ids=["dangling-local-ref", "remote-ref", "metadata-ref", "unknown-dialect", "non-string-dialect"])
def test_an_unusable_input_schema_is_a_tool_error_and_never_fetched(world, monkeypatch, schema):
    cid = bound(world)
    world.people.store.execute("UPDATE mcp_connection_tools SET input_schema_json=? WHERE connection_id=? AND name='echo'",
                               (json.dumps(schema), cid))
    fetched = []

    def no_fetch(*args, **kwargs):
        fetched.append(args)
        raise AssertionError("a $ref made the server fetch an address")
    monkeypatch.setattr(urllib.request, "urlopen", no_fetch)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:fixture-notes/echo", "input": {"text": "x"}}),
                               say("ok")])
    assert wait_turn(world.people.store, turn["turn_id"]) == "succeeded"
    tc = tool_calls(world, conv)[0]
    assert tc["status"] == "failed" and tc["error"]["code"] == "mcp_schema_invalid", tc
    assert fetched == [] and agent_calls(world) == 0


def test_input_schemas_are_checked_in_the_dialect_they_declare():
    tools = McpTools(SimpleNamespace(store=None), {"id": "fixture-user"})   # FIXTURE: checking an input reads no store

    def tool(schema):
        return McpVirtualOp(id="mcp:fx/pair", alias="fx", tool="pair", connection_name="Fixture", title={}, summary="",
                            side_effect="external", agent="confirm", registered=REGISTERED_CALL, schema=schema)
    draft7 = {"$schema": "http://json-schema.org/draft-07/schema#", "type": "object", "required": ["pair"],
              "properties": {"pair": {"type": "array", "items": [{"type": "string"}, {"type": "number"}]}}}
    ok = tools.prepare(tool(draft7), {"pair": ["a", 1]})
    assert isinstance(ok, tuple) and ok[2] == {"connection": "fx", "tool": "pair", "arguments": {"pair": ["a", 1]}}
    bad = tools.prepare(tool(draft7), {"pair": ["a", "b"]})
    assert bad["code"] == "invalid_input" and bad["detail"][0]["loc"] == ["pair", "1"]
    # no $schema: Draft 2020-12, the MCP default, in which an array of `items` is not a schema
    undeclared = {k: v for k, v in draft7.items() if k != "$schema"}
    assert tools.prepare(tool(undeclared), {"pair": ["a", 1]})["code"] == "mcp_schema_invalid"
    prefix = {"type": "object", "properties": {"pair": {"type": "array", "prefixItems": [{"type": "string"}, {"type": "number"}]}}}
    assert tools.prepare(tool(prefix), {"pair": ["a", "b"]})["code"] == "invalid_input"
    # a reference inside the schema is followed
    local = {"type": "object", "$defs": {"T": {"type": "string"}}, "properties": {"text": {"$ref": "#/$defs/T"}}}
    assert isinstance(tools.prepare(tool(local), {"text": "x"}), tuple)
    assert tools.prepare(tool(local), {"text": 1})["code"] == "invalid_input"

