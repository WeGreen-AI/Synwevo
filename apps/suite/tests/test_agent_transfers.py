"""C1, C2, C4: the personal Agent across two workspaces, on real Forms and the real engine.

Only the model is scripted (GovernanceModel: a synthetic credential, never sent anywhere). Alice
owns A (with Bob, an editor, and Vera, a viewer) and only views B, which Oscar owns.
"""
import json

from .test_agent import agent, call, op, say  # noqa: F401 (fixture)
from .test_agent_governance_boundaries import advance, begin, governed, policy  # noqa: F401 (fixture)
from .test_data_flow import revision


def forms_in(g, ws):
    return [r["title"] for r in g.store.all("SELECT title FROM forms WHERE workspace_id=? ORDER BY created_at", (ws,))]


def calls(g, turn):
    return {r["tool_use_id"]: (r["status"], r["error_code"]) for r in
            g.store.all("SELECT tool_use_id,status,error_code FROM agent_tool_calls WHERE turn_id=?", (turn["turn_id"],))}


def setup(g):
    g.client.sign_in("oscar")
    op(g.client, "forms.form.create", {"title": "B roster"}, ws=g.people.b)
    policy(g, g.people.b, "platform")
    policy(g, g.people.a, "platform")
    op(g.client, "forms.form.create", {"title": "A roster"}, ws=g.people.a)


def read(ws, tid):
    return call("run_operation", {"operation": "forms.form.list", "workspace_id": ws, "input": {}}, tid=tid)


def create(ws, title, tid):
    return call("run_operation", {"operation": "forms.form.create", "workspace_id": ws, "input": {"title": title}}, tid=tid)


def legal_transfer(g):
    """What makes B → A legal: Oscar lets Alice export, and lets B's content reach A's people."""
    g.people.ident.change_role(g.people.b, g.people.uid("alice"), "viewer", can_export=True)
    g.client.sign_in("oscar")
    op(g.client, "agent.transfer_policy.set", {"outbound": "any", "revision": revision(g.store, g.people.b)}, ws=g.people.b)
    g.client.sign_in("alice")


def test_c1_reads_and_compares_both_and_writes_only_where_allowed(governed):
    g = governed
    setup(g)
    cid, turn = begin(g, [read(g.people.a, "read-a"), read(g.people.b, "read-b"),
                          create(g.people.a, "Comparison", "write-a"), create(g.people.b, "Comparison", "write-b"),
                          say("A has A roster, B has B roster; I could not save the comparison.")])
    assert advance(g, turn)["status"] == "succeeded"
    done = calls(g, turn)
    assert done["read-a"] == ("succeeded", None) and done["read-b"] == ("succeeded", None)
    # Into A: B's content would go along, and Alice may only view B. Into B: she cannot write there.
    assert done["write-a"] == ("failed", "transfer_export_denied")
    assert done["write-b"][0] == "failed" and done["write-b"][1] != "transfer_export_denied"
    assert forms_in(g, g.people.a) == ["A roster"] and forms_in(g, g.people.b) == ["B roster"]
    # The model was told why, in words it can pass on.
    told = json.loads(g.store.one("SELECT for_model_json FROM agent_tool_calls WHERE tool_use_id='write-a'")["for_model_json"])
    assert told["error"]["code"] == "transfer_export_denied" and "导出" in told["error"]["message"]
    answer = op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]["messages"][-1]
    assert answer["body"].startswith("A has A roster") and not answer["hidden"]


def test_c2_a_legal_transfer_shows_source_and_target_and_makes_a_real_record(governed):
    g = governed
    setup(g)
    legal_transfer(g)
    cid, turn = begin(g, [read(g.people.b, "read-b"), create(g.people.a, "From B roster", "write-a"), say("saved")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = [c for m in op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]["messages"]
            for c in m["tool_calls"] if c["status"] == "awaiting_confirmation"][0]
    assert card["target_workspace_id"] == g.people.a and card["target_workspace_name"] == "Workspace A"
    assert card["transfer_from"] == [{"workspace_id": g.people.b, "workspace_name": "Workspace B", "outbound": "any"}]
    result = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["confirmation"]})["output"]
    assert result["status"] == "succeeded" and result["link"] and f"/w/{g.people.a}/" in result["link"]
    assert forms_in(g, g.people.a) == ["A roster", "From B roster"]
    run = g.store.one("SELECT workspace_id, via, delegation FROM op_runs WHERE id=?", (result["run_id"],))
    assert run["workspace_id"] == g.people.a and run["via"] == "agent"
    assert json.loads(run["delegation"])["transfer_from"] == [g.people.b]
    assert advance(g, turn)["status"] == "succeeded"


def test_c2_a_changed_policy_voids_the_confirmation_and_nothing_is_written(governed):
    g = governed
    setup(g)
    legal_transfer(g)
    cid, turn = begin(g, [read(g.people.b, "read-b"), create(g.people.a, "From B roster", "write-a"), say("stopped")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE tool_use_id='write-a'")
    g.client.sign_in("oscar")
    op(g.client, "agent.transfer_policy.set", {"outbound": "audience", "revision": revision(g.store, g.people.b)}, ws=g.people.b)  # Bob and Vera are not in B
    g.client.sign_in("alice")
    result = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]})["output"]
    assert result["status"] == "failed" and result["error"]["code"] == "transfer_audience_denied"
    assert forms_in(g, g.people.a) == ["A roster"]
    assert advance(g, turn)["status"] == "succeeded"  # the model hears the refusal and answers


def test_c4_losing_b_stops_later_b_work_keeps_a_and_hides_what_depended_on_b(governed):
    g = governed
    setup(g)
    legal_transfer(g)
    cid, turn = begin(g, [read(g.people.b, "read-b"), create(g.people.a, "From B roster", "write-a"), say("saved")])
    advance(g, turn)
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE tool_use_id='write-a'")
    op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]})
    assert advance(g, turn)["status"] == "succeeded"
    copied = g.store.one("SELECT id FROM forms WHERE workspace_id=? AND title='From B roster'", (g.people.a,))["id"]
    # A second answer in the same conversation prepares another write, then Oscar removes Alice from B.
    _, later = begin(g, [create(g.people.a, "Second note", "write-again"), say("never")], conversation=cid)
    assert advance(g, later)["status"] == "waiting_confirmation"
    pending = g.store.one("SELECT * FROM agent_tool_calls WHERE tool_use_id='write-again'")
    g.people.ident.remove_member(g.people.b, g.people.uid("alice"))
    refused = op(g.client, "agent.tool_call.confirm", {"tool_call_id": pending["id"],
                                                      "confirmation": pending["input_hash"]}, expect=409)
    assert refused["error"] == "source_access_changed"
    assert forms_in(g, g.people.a) == ["A roster", "From B roster"]  # nothing new; the completed A part is kept
    shown = op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]
    assert shown["sealed"] == "access_changed"
    assert all(m["hidden"] for m in shown["messages"] if m["role"] == "assistant")
    # The legally transferred record now belongs to A and follows A's access, not Alice's view of B.
    g.client.sign_in("bob")
    assert op(g.client, "forms.form.get", {"form_id": copied}, ws=g.people.a)["output"]["title"] == "From B roster"


def test_a_flow_run_read_by_the_agent_carries_no_step_snapshots(governed):
    """A personal operation's answer names no source workspace: workspace content given to the
    model through it would be untracked, and would pass the workspace's AI-processing policy too."""
    g = governed
    p = g.people
    g.client.sign_in("oscar")
    op(g.client, "forms.form.create", {"title": "Fixture roster of B"}, ws=p.b)
    g.client.sign_in("alice")  # Alice only views B: no export right, and B allows no model processing
    flows = g.client.api.state.flows
    flow_id = flows.create(user_id=p.uid("alice"), name="Fixture flow", definition={"steps": [
        {"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": p.b, "input": {}}]})["flow_id"]
    run_id = flows.start(flow_id, user_id=p.uid("alice"), version=1, parameters={}, idempotency_key="fixture-read")["id"]
    flows.tick()
    # In the page she sees the snapshot, as she may read B.
    shown = op(g.client, "agent.flow.run", {"run_id": run_id})["output"]
    assert shown["status"] == "succeeded" and shown["steps"][0]["output"]["forms"][0]["title"] == "Fixture roster of B"
    _, turn = begin(g, [call("run_operation", {"operation": "agent.flow.run", "input": {"run_id": run_id}}, tid="read-run"),
                        say("The run finished.")])
    assert advance(g, turn)["status"] == "succeeded"
    read = g.store.one("SELECT status, result_json, for_model_json FROM agent_tool_calls WHERE tool_use_id='read-run'")
    assert read["status"] == "succeeded"
    assert "Fixture roster of B" not in read["for_model_json"] and "Fixture roster of B" not in read["result_json"]
    told = json.loads(read["for_model_json"])["result"]["output"]["steps"][0]
    assert (told["status"], told["output"], told["snapshot_hidden"]) == ("succeeded", None, True)


def test_audience_lets_a_record_through_but_not_a_send_to_other_people(governed):
    """A's default policy vouches for the target's members; a fill-in request goes to whoever it names."""
    g = governed
    setup(g)
    g.client.sign_in("alice")
    small = g.people.ident.create_workspace("Fixture Alice only", owner_id=g.people.uid("alice"))["id"]
    form = op(g.client, "forms.form.create", {"title": "Fixture request form"}, ws=small)["output"]["id"]
    request = call("run_operation", {"operation": "forms.request.create", "workspace_id": small,
                                     "input": {"form_id": form, "title": "A roster", "recipient": {"email": "outside@example.test"}}},
                   tid="send")
    _, turn = begin(g, [read(g.people.a, "read-a"), request, create(small, "From A roster", "write"), say("One was not sent.")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    done = calls(g, turn)
    assert done["send"] == ("failed", "transfer_audience_external")
    assert done["write"] == ("awaiting_confirmation", None)
    assert g.store.one("SELECT COUNT(*) AS n FROM form_requests")["n"] == 0
