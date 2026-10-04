"""C1, C2, C4: the personal Agent across two workspaces, on real Forms and the real engine.

Only the model is scripted (GovernanceModel: a synthetic credential, never sent anywhere). Alice
owns A (with Bob, an editor, and Vera, a viewer) and only views B, which Oscar owns.
"""
import json

import pytest

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


def read_b_by_flow(g, key="fixture-read"):
    """A finished run of Alice's flow that read B, which only Oscar can open up."""
    p = g.people
    g.client.sign_in("oscar")
    op(g.client, "forms.form.create", {"title": "Fixture roster of B"}, ws=p.b)
    g.client.sign_in("alice")  # Alice only views B: no export right
    flows = g.client.api.state.flows
    flow_id = flows.create(user_id=p.uid("alice"), name="Fixture flow", definition={"steps": [
        {"id": "read", "kind": "call", "operation": "forms.form.list", "workspace_id": p.b, "input": {}},
        {"id": "check", "kind": "branch", "condition": {"left": {"$ref": "steps.read.forms.0.title"}, "op": "eq",
                                                         "right": "Fixture roster of B"}, "then": "yes", "otherwise": "no"},
        {"id": "yes", "kind": "fail", "code": "fixture_title_matched"},
        {"id": "no", "kind": "fail", "code": "fixture_title_differs"}]})["flow_id"]
    run_id = flows.start(flow_id, user_id=p.uid("alice"), version=1, parameters={}, idempotency_key=key)["id"]
    flows.tick()
    # In the page she sees the snapshot and the path, as she may read B.
    shown = op(g.client, "agent.flow.run", {"run_id": run_id})["output"]
    assert shown["error_code"] == "fixture_title_matched"
    assert shown["steps"][0]["output"]["forms"][0]["title"] == "Fixture roster of B"
    return run_id


def test_a_flow_run_is_withheld_from_the_model_when_a_workspace_it_read_allows_no_processing(governed):
    """AGENT-DF-2: a run's path and error code were decided on B's content. B allows no model
    processing, so the model is not told them, and nothing of it becomes a personal memory."""
    g = governed
    run_id = read_b_by_flow(g)
    _, turn = begin(g, [call("run_operation", {"operation": "agent.flow.run", "input": {"run_id": run_id}}, tid="read-run"),
                        call("run_operation", {"operation": "agent.memory.add", "input": {"body": "B's title matched"}},
                             tid="remember"),
                        say("Done.")])
    advance(g, turn)
    read = g.store.one("SELECT status, for_model_json FROM agent_tool_calls WHERE tool_use_id='read-run'")
    told = json.loads(read["for_model_json"])
    assert read["status"] == "succeeded" and told.get("withheld") is True
    assert "fixture_title_matched" not in read["for_model_json"] and "Fixture roster of B" not in read["for_model_json"]
    assert calls(g, turn)["remember"] == ("failed", "source_transfer_unavailable")
    assert g.store.one("SELECT COUNT(*) AS n FROM agent_memories")["n"] == 0


def test_a_flow_run_read_by_the_agent_names_what_it_read_and_carries_no_step_snapshots(governed):
    """Where B allows processing, the model gets the run's progress, never the steps' business
    snapshots, and the answer names B as a source of the conversation."""
    g = governed
    policy(g, g.people.b, "platform")
    run_id = read_b_by_flow(g, "fixture-read-allowed")
    cid, turn = begin(g, [call("run_operation", {"operation": "agent.flow.run", "input": {"run_id": run_id}}, tid="read-run"),
                          say("The run finished.")])
    assert advance(g, turn)["status"] == "succeeded"
    read = g.store.one("SELECT status, result_json, for_model_json FROM agent_tool_calls WHERE tool_use_id='read-run'")
    assert read["status"] == "succeeded"
    assert "Fixture roster of B" not in read["for_model_json"] and "Fixture roster of B" not in read["result_json"]
    payload = json.loads(read["for_model_json"])
    assert payload["source_workspaces"] == [g.people.b]
    told = payload["result"]["output"]["steps"][0]
    assert (told["status"], told["output"], told["snapshot_hidden"]) == ("succeeded", None, True)
    message = g.store.one("SELECT sources_json FROM agent_messages WHERE id=(SELECT assistant_message_id FROM agent_turns "
                          "WHERE id=?)", (turn["turn_id"],))
    assert {"workspace_id": g.people.b, "reported_by": "agent.flow.run"} in json.loads(message["sources_json"])
    # The answer stays only while she belongs to B; leaving it hides what depended on it.
    assert g.engine._lost_workspaces(g.people.uid("alice"), cid) == set()
    g.people.ident.remove_member(g.people.b, g.people.uid("alice"))
    assert g.engine._lost_workspaces(g.people.uid("alice"), cid) == {g.people.b}


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


@pytest.mark.parametrize("receipt_withheld", [False, True])
def test_a_copy_made_in_the_conversation_keeps_its_source_for_a_later_write(governed, receipt_withheld):
    """CR-047 AGENT-DF-3: B keeps its default ``audience``. Copying B's form into Alice's personal
    workspace is legal (only she reads it there), and the model never reads B itself. Writing the
    copy's content on into a workspace with someone outside B is still a transfer from B — also when
    the copy's receipt was withheld from the model and the copy is read back in a later turn."""
    g, p = governed, governed.people
    alice = p.uid("alice")
    g.client.sign_in("oscar")
    source = op(g.client, "forms.form.create", {"title": "Fixture B salary review"}, ws=p.b)["output"]["id"]
    p.ident.change_role(p.b, alice, "viewer", can_export=True)
    g.client.sign_in("alice")
    personal = p.ident.create_workspace("Fixture personal", owner_id=alice, kind="personal")["id"]
    wide = p.ident.create_workspace("Fixture wide team", owner_id=alice)["id"]
    p.ident.add_member(wide, p.uid("out"), "viewer")
    policy(g, personal, "none" if receipt_withheld else "platform")
    policy(g, wide, "platform")
    cid, turn = begin(g, [call("run_operation", {"operation": "forms.form.duplicate", "workspace_id": p.b,
                                               "input": {"form_id": source, "target_workspace_id": personal}}, tid="copy"),
                        say("copied")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=? AND tool_use_id='copy'", (turn["turn_id"],))
    assert op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"],
                                                    "confirmation": card["input_hash"]})["output"]["status"] == "succeeded"
    steps = [read(personal, "read-copy"), create(wide, "Fixture B salary review", "land"),
             create(personal, "Fixture note for myself", "keep"), say("stopped")]
    if receipt_withheld:
        assert advance(g, turn)["status"] == "succeeded"
        saved = g.store.one("SELECT for_model_json FROM agent_tool_calls WHERE id=?", (card["id"],))
        assert json.loads(saved["for_model_json"]).get("withheld") is True
        policy(g, personal, "platform")
        _, turn = begin(g, steps, conversation=cid)
    else:
        g.model.steps = steps
    advance(g, turn)
    # The copy is personal content to read; landing it in wide is refused on B's policy, before any card.
    assert calls(g, turn)["read-copy"] == ("succeeded", None)
    assert calls(g, turn)["land"] == ("failed", "transfer_audience_denied")
    assert forms_in(g, wide) == []
    # Writing back where the copy already is carries nothing new.
    assert calls(g, turn)["keep"][0] == "awaiting_confirmation"
    # Leaving B does not take the copy away from its reader: answers about it stay visible.
    p.ident.remove_member(p.b, alice)
    shown = op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]
    assert not any(m["hidden"] for m in shown["messages"] if m["role"] == "assistant")


def test_a_card_proposed_before_a_read_in_the_same_step_is_reissued_not_stuck(governed):
    """CR-047 AGENT-DF-4: the model proposes a write into A and reads B in one step. At
    confirmation B is a source the card did not show, so that confirmation is void — and the card
    is shown again with B, rather than answering 409 forever."""
    from synwevo_suite.agent.provider import StepResult
    g = governed
    setup(g)
    legal_transfer(g)
    write, look = create(g.people.a, "Written before reading B", "write-a"), read(g.people.b, "read-b")
    both = StepResult(content=write.content + look.content, stop_reason="tool_use", usage={"output_tokens": 9})
    _, turn = begin(g, [both, say("done")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE tool_use_id='write-a'")
    assert card["transfer_json"] is None                       # nothing was read when it was proposed
    refused = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]},
                 expect=409)
    assert refused["error"] == "confirmation_mismatch"
    assert forms_in(g, g.people.a) == ["A roster"]            # the old confirmation ran nothing
    again = g.store.one("SELECT * FROM agent_tool_calls WHERE id=?", (card["id"],))
    assert again["status"] == "awaiting_confirmation" and again["input_hash"] != card["input_hash"]
    assert [b["source"] for b in json.loads(again["transfer_json"])] == [g.people.b]
    done = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": again["input_hash"]})["output"]
    assert done["status"] == "succeeded"
    assert forms_in(g, g.people.a) == ["A roster", "Written before reading B"]


# ---------------------------------------------------------------- second review round (R2)

def _confirm_pending(g, turn):
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=? AND status='awaiting_confirmation'", (turn["turn_id"],))
    out = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]})["output"]
    return card, out


def test_an_agent_write_keeps_its_sources_in_the_flows_its_event_starts(governed):
    """R2-F2: the Agent read A and published a form in Alice's personal workspace; her trigger on
    that event copied the title into ``wide``. The Agent's run record named A, the flow did not."""
    from .test_data_flow_runs import SECRET, World, second_flow
    from .test_forms import DEFINITION
    g = governed
    w = World(g.client)
    g.client.sign_in("alice")
    policy(g, g.people.a, "platform")
    second = second_flow(w)
    form = op(g.client, "forms.form.create", {"title": "Fixture empty", "definition": DEFINITION}, ws=w.personal)["output"]
    steps = [read(g.people.a, "read-a"),
             call("run_operation", {"operation": "forms.form.update_draft", "workspace_id": w.personal,
                                    "input": {"form_id": form["id"], "draft_rev": 1, "title": SECRET}}, tid="upd"),
             call("run_operation", {"operation": "forms.form.publish", "workspace_id": w.personal,
                                    "input": {"form_id": form["id"], "draft_rev": 2}}, tid="pub"), say("done")]
    _, turn = begin(g, steps)
    for _ in range(2):
        assert advance(g, turn)["status"] == "waiting_confirmation"
        _confirm_pending(g, turn)
    advance(g, turn)
    assert json.loads(g.store.one("SELECT delegation FROM op_runs WHERE op='forms.form.publish' AND via='agent'")["delegation"])[
        "transfer_from"] == [g.people.a]
    for _ in range(4):
        w.flows.tick()
    runs = w.runs_of(second)
    assert [(r["status"], r["error_code"]) for r in runs] == [("blocked", "transfer_audience_denied")]
    assert SECRET not in w.titles(w.wide)


def test_a_sibling_branch_does_not_publish_what_another_branch_carried(governed):
    """R2-F3: what a write left in a workspace is that workspace's state, in every branch of the
    conversation; a branch that never read A still publishes A's content when it publishes it."""
    from .test_data_flow_runs import SECRET, World
    from .test_forms import DEFINITION, token_of
    from .test_forms_exposure import page
    g = governed
    w = World(g.client)
    g.client.sign_in("alice")
    policy(g, g.people.a, "platform")
    policy(g, w.small, "platform")
    form = op(g.client, "forms.form.create", {"title": "Fixture travel claim", "definition": DEFINITION}, ws=w.small)["output"]
    op(g.client, "forms.form.publish", {"form_id": form["id"], "draft_rev": 1}, ws=w.small)
    link = op(g.client, "forms.public_link.set", {"form_id": form["id"], "enabled": True}, ws=w.small)["output"]
    cid, t0 = begin(g, [say("hello")])
    assert advance(g, t0)["status"] == "succeeded"
    upd = call("run_operation", {"operation": "forms.form.update_draft", "workspace_id": w.small,
                                 "input": {"form_id": form["id"], "draft_rev": 1, "title": SECRET}}, tid="upd")
    _, t1 = begin(g, [read(g.people.a, "read-a"), upd, say("saved")], conversation=cid)
    assert advance(g, t1)["status"] == "waiting_confirmation"
    _confirm_pending(g, t1)
    assert advance(g, t1)["status"] == "succeeded"
    # A sibling branch from the first answer, which never read A.
    first = g.store.one("SELECT assistant_message_id FROM agent_turns WHERE id=?", (t0["turn_id"],))["assistant_message_id"]
    g.model.steps = [call("run_operation", {"operation": "forms.form.publish", "workspace_id": w.small,
                                            "input": {"form_id": form["id"], "draft_rev": 2}}, tid="pub-branch"), say("published")]
    t3 = op(g.client, "agent.turn.start", {"conversation_id": cid, "text": "publish it", "parent_message_id": first})["output"]
    assert advance(g, t3)["status"] == "waiting_confirmation"
    card, out = _confirm_pending(g, t3)
    assert [b["source"] for b in json.loads(card["transfer_json"])] == [g.people.a], "the card names where the draft came from"
    assert out["status"] == "failed" and out["error"]["code"] == "transfer_audience_external"
    assert page(w, token_of(link["url"]))["form"]["title"] == "Fixture travel claim"


# ---------------------------------------------------------------- third review round (R3)

def test_a_flow_the_conversation_started_keeps_its_sources_for_a_later_write(governed):
    """R3-DF-3: the Agent started Alice's flow, which copied A's form title into her personal
    workspace (A's ``audience`` allows that). Later in the same conversation the Agent read the
    personal workspace and wrote the copy into ``wide``, where Oz is not a member of A: asked only
    for the personal workspace. Had the Agent copied it itself, the write would be asked for A."""
    from .test_data_flow_runs import SECRET, TITLE, World, create as flow_create, read as flow_read
    g = governed
    w = World(g.client)
    g.client.sign_in("alice")
    policy(g, w.personal, "platform")
    policy(g, w.wide, "platform")
    copier = w.flow([flow_read(g.people.a), flow_create("hop", w.personal, TITLE)], "copier")
    start = call("run_operation", {"operation": "agent.flow.start",
                                   "input": {"flow_id": copier, "version": 1, "idempotency_key": "fixture-r3-df3"}},
                 tid="start")
    cid, turn = begin(g, [start, say("started")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    _, out = _confirm_pending(g, turn)
    assert out["status"] == "succeeded"
    assert advance(g, turn)["status"] == "succeeded"
    w.flows.tick()
    assert w.titles(w.personal) == [SECRET]           # A's title reached the personal workspace, legally
    _, later = begin(g, [read(w.personal, "read-copy"), create(w.wide, SECRET, "land"), say("stopped")], conversation=cid)
    advance(g, later)
    assert calls(g, later)["read-copy"] == ("succeeded", None)
    assert calls(g, later)["land"] == ("failed", "transfer_audience_denied")
    assert w.titles(w.wide) == []
    # Writing back where the copy already is carries nothing new.
    _, again = begin(g, [read(w.personal, "reread"), create(w.personal, "Fixture note for myself", "keep"), say("ok")],
                     conversation=cid)
    advance(g, again)
    assert calls(g, again)["keep"] == ("awaiting_confirmation", None)


def test_a_flow_an_agent_write_started_keeps_its_sources_for_a_later_write(governed):
    """R3-DF-3, the same through an event: the Agent published a form in the personal workspace
    (nothing read), and Alice's trigger on that event copied A's title next to it."""
    from .test_data_flow_runs import SECRET, TITLE, World, create as flow_create, read as flow_read
    from .test_forms import DEFINITION
    g = governed
    w = World(g.client)
    g.client.sign_in("alice")
    policy(g, w.personal, "platform")
    policy(g, w.wide, "platform")
    w.on_event([flow_read(g.people.a), flow_create("hop", w.personal, TITLE)], "on-publish", w.personal,
               tools=["forms.form.list", "forms.form.create"], workspaces=[g.people.a, w.personal])
    form = op(g.client, "forms.form.create", {"title": "Fixture empty", "definition": DEFINITION}, ws=w.personal)["output"]
    pub = call("run_operation", {"operation": "forms.form.publish", "workspace_id": w.personal,
                                 "input": {"form_id": form["id"], "draft_rev": 1}}, tid="pub")
    cid, turn = begin(g, [pub, say("published")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    assert _confirm_pending(g, turn)[1]["status"] == "succeeded"
    advance(g, turn)
    for _ in range(3):
        w.flows.tick()
    assert SECRET in w.titles(w.personal)
    _, later = begin(g, [read(w.personal, "read-copy"), create(w.wide, SECRET, "land"), say("stopped")], conversation=cid)
    advance(g, later)
    assert calls(g, later)["land"] == ("failed", "transfer_audience_denied")
    assert w.titles(w.wide) == []
