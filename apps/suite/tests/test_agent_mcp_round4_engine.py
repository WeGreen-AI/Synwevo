"""The Agent's engine with external MCP tools, fourth review round: a workspace connection's result
may go back to the server it came from (and to no other), one the workspace keeps from the model
says which workspace keeps it and why, and a resource list — which asks no server anything — is
bounded to its run record and never reported as possibly carried out.

TEST DOUBLES, each labelled: the model is `ScriptedModel` (test_agent.py); the external MCP server is
the FIXTURE server of test_agent_mcp.py, served in process; the policy rows are written as the
policy operations write them (`processing`, round 3). Where a test needs more resources than a run
record holds, FIXTURE rows are written as a discovery of such a server would store them.
"""
from __future__ import annotations

import json

from synwevo_core.capabilities import MAX_OUTPUT_BYTES, canonical
from synwevo_core.store import now
from synwevo_suite.agent.mcp.connections import RESULT_BUDGET

from .test_agent import call, op, say, wait_turn
from .test_agent_mcp import connect, start, tool_calls, world  # noqa: F401 (fixture)
from .test_agent_mcp_round3_engine import SECRET, processing, team_and_mine, told


def echo_calls(c) -> int:
    return c.people.store.one("SELECT COUNT(*) AS n FROM mcp_calls WHERE name='echo' AND origin='agent'")["n"]


# ---------------------------------------------------------------------------------- back where it came from
def test_a_workspace_connections_result_may_go_back_to_the_same_connection(world):
    """Search, then fetch: under a shared workspace's default outbound policy ('audience'), what the
    workspace's connection answered goes back to that connection's server — it came from there —
    while another server still may not have it."""
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)                       # A lets the assistant read its content; outbound stays 'audience'
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "again: " + SECRET}}, tid="tu_2"),
        call("run_operation", {"operation": "mcp:mine/add_note", "input": {"text": SECRET}}, tid="tu_3"),
        say("done"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    calls = tool_calls(world, conv)
    assert [(tc["operation"], tc["status"]) for tc in calls] == [
        ("mcp:team/echo", "succeeded"), ("mcp:team/echo", "succeeded"), ("mcp:mine/add_note", "failed")]
    assert calls[2]["error"]["code"] == "mcp_transfer_refused"
    assert echo_calls(world) == 2 and world.state["server"].notes == []


def test_content_the_workspace_gave_otherwise_still_holds_its_own_connection_back(world):
    """The exemption is for that connection's own results only: content of the workspace read
    natively in the same branch may not leave through its connection either."""
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "forms.form.list", "workspace_id": a, "input": {}}, tid="tu_2"),
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "and the forms"}}, tid="tu_3"),
        say("done"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    last = tool_calls(world, conv)[-1]
    assert (last["operation"], last["status"], last["error"]["code"]) == ("mcp:team/echo", "failed", "mcp_transfer_refused")
    assert echo_calls(world) == 1


def test_the_same_connection_at_a_new_address_is_another_server(world):
    """A manager points the workspace's connection at another server, and its people turn it on
    again there: what the old server answered in the conversation is not sent to the new one."""
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
                               say("read it")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    assert told(world, conv, "mcp:team/echo")["source_workspaces"] == [a]
    team = store.one("SELECT connection_id FROM mcp_agent_bindings WHERE alias='team'")["connection_id"]
    op(world, "agent.mcp.connection.update", {"connection_id": team, "url": "https://fixture-notes-two.test/mcp"})
    op(world, "agent.mcp.connection.test", {"connection_id": team})
    op(world, "agent.mcp.grant.set", {"connection_id": team, "tool": "echo", "mode": "auto"})
    op(world, "agent.mcp.binding.set", {"connection_id": team, "enabled": True, "alias": "team"})
    _, turn2 = start(world, [call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "to the new server"}}, tid="tu_2"),
                             say("done")], conv=conv)
    assert wait_turn(store, turn2["turn_id"]) == "succeeded"
    last = tool_calls(world, conv)[-1]
    assert (last["status"], last["error"]["code"]) == ("failed", "mcp_transfer_refused")
    assert echo_calls(world) == 1


def test_the_carrier_asks_the_same_question_as_the_request_leaves(world, monkeypatch):
    """The engine's check before the call, and the carrier's when the request would leave, name the
    same destination: the carrier is not stricter (which would refuse the second call) nor looser."""
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)
    asked = []
    real = world.agent.mcp_sources

    def mcp_sources(t, *, to=None):
        asked.append(to)
        return real(t, to=to)
    monkeypatch.setattr(world.agent, "mcp_sources", mcp_sources)
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "again"}}, tid="tu_2"),
        say("done"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    team = store.one("SELECT connection_id FROM mcp_agent_bindings WHERE alias='team'")["connection_id"]
    assert asked and all(to and to["connection_id"] == team for to in asked), asked
    assert len({json.dumps(to, sort_keys=True) for to in asked}) == 1 and echo_calls(world) == 2


# ---------------------------------------------------------------------------------- kept from the model, said why
def test_a_result_the_workspace_keeps_from_the_model_says_which_workspace_and_why(world):
    """A's Agent processing policy does not allow this model (a shared workspace's default): the
    result through A's connection is withheld, and both the model and the page are told that it came
    through A's connection and that A's policy keeps it — so the person, who sees the result, learns
    why the Agent did not read it."""
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    name = store.one("SELECT name FROM workspaces WHERE id=?", (a,))["name"]
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
                               call("run_operation", {"operation": "mcp:team/echo", "input": {"text": "again"}}, tid="tu_2"),
                               say("I cannot read that result")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    heard = told(world, conv, "mcp:team/echo")
    assert heard["withheld"] is True and heard["code"] == "mcp_result_withheld" and heard["workspace_id"] == a
    assert name in heard["reason"] and "processing policy" in heard["reason"] and SECRET not in json.dumps(heard)
    first, second = tool_calls(world, conv)
    assert first["status"] == "succeeded" and first["result"]["text"] == SECRET          # the person sees it
    assert first["mcp"]["withheld"] == {"code": "mcp_result_withheld", "workspace_id": a, "workspace_name": name}
    # Nothing reached the model, so nothing of A goes along with the next call either.
    assert second["status"] == "succeeded" and echo_calls(world) == 2
    # Once A lets the assistant process its content, the result is read and nothing is marked.
    processing(world, a)
    conv2, turn2 = start(world, [call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}),
                                 say("read it")])
    assert wait_turn(store, turn2["turn_id"]) == "succeeded"
    assert tool_calls(world, conv2)[0]["mcp"]["withheld"] is None
    assert told(world, conv2, "mcp:team/echo")["source_workspaces"] == [a]


def test_a_refusal_to_send_a_workspace_connections_result_elsewhere_says_it_may_go_back_only(world):
    store, a = world.people.store, world.people.a
    team_and_mine(world)
    processing(world, a)
    conv, turn = start(world, [
        call("run_operation", {"operation": "mcp:team/echo", "input": {"text": SECRET}}, tid="tu_1"),
        call("run_operation", {"operation": "mcp:mine/add_note", "input": {"text": SECRET}}, tid="tu_2"),
        say("done"),
    ])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    refused = tool_calls(world, conv)[-1]
    assert (refused["status"], refused["error"]["code"]) == ("failed", "mcp_transfer_refused")
    said = told(world, conv, "mcp:mine/add_note")["error"]["message"]
    assert "shared MCP connections" in said and "same connection" in said


# ---------------------------------------------------------------------------------- a resource list
def many_resources(c, cid: str, n: int = 500) -> None:
    """FIXTURE rows: `n` resources as a discovery of such a server keeps them past the offer's room
    (each one's address of 2,048 characters and its name of 200)."""
    for i in range(n):
        c.people.store.execute("INSERT INTO mcp_connection_resources (connection_id, uri, name, title, description, mime_type, "
                               "size, first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                               (cid, f"notes://fixture/{i:04d}/" + "x" * (2048 - 20), "资" * 200, "标题", "说明" * 50, None, None,
                                now(), now()))


def test_a_resource_list_is_cut_to_its_run_record_and_the_turn_goes_on(world):
    store = world.people.store
    cid = connect(world)["id"]
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True, "resources_enabled": True, "alias": "notes"})
    many_resources(world, cid)
    assert len(canonical(world.agent.connections.list_resources({"id": world.people.uid("alice")}, cid)).encode()) > MAX_OUTPUT_BYTES
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:notes/resources.list", "input": {}}), say("done")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    listed = tool_calls(world, conv)[0]
    assert listed["status"] == "succeeded", listed
    result = listed["result"]
    assert result["truncated"] is True and 0 < len(result["resources"]) < 501
    assert len(canonical(result).encode()) <= RESULT_BUDGET + 1024
    assert all(set(r) <= {"uri", "name", "mime_type", "size", "last_seen_at"} for r in result["resources"])
    # The model reads what fits the policy's characters of it, cut the way a tool's text is.
    heard = told(world, conv, "mcp:notes/resources.list")
    assert heard["truncated"] is True and 0 < len(heard["resources"]) < len(result["resources"])
    assert len(canonical(heard["resources"])) <= world.agent.connections.policy.current().max_result_chars
    # The page's browse is bounded the same way.
    browsed = op(world, "agent.mcp.resource.browse", {"connection_id": cid})["output"]
    assert browsed["truncated"] is True and len(browsed["items"]) == len(result["resources"])


def test_a_resource_list_that_cannot_be_recorded_failed_and_is_never_unknown(world, monkeypatch):
    """A list answers from what discovery stored: nothing external acted, so whatever stops it after
    its handler ran is a plain failure the model hears, not an outcome to check elsewhere."""
    store = world.people.store
    cid = connect(world)["id"]
    op(world, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True, "resources_enabled": True, "alias": "notes"})
    monkeypatch.setattr(world.agent.connections, "list_resources",
                        lambda user, cid: [{"uri": f"notes://{i}", "name": "中" * 2000} for i in range(300)])   # FIXTURE: unbounded
    from synwevo_suite.agent.mcp import ops as ops_module
    monkeypatch.setattr(ops_module, "bounded_resources", lambda rows: (rows, False), raising=False)      # FIXTURE: no cut
    conv, turn = start(world, [call("run_operation", {"operation": "mcp:notes/resources.list", "input": {}}), say("go on")])
    assert wait_turn(store, turn["turn_id"]) == "succeeded"
    row = store.one("SELECT status, error_code FROM agent_tool_calls WHERE conversation_id=?", (conv,))
    assert (row["status"], row["error_code"]) == ("failed", "output_too_large")
    assert len(world.agent.provider.requests) == 2                          # the model heard it and answered
