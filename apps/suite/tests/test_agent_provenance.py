"""Agent source proofs on isolated Stores and real registry operations.

The inherited GovernanceModel is a scripted provider fixture; no model/network calls occur.
"""
import json

import pytest

from synwevo_core.capabilities import canonical
from .test_agent import agent, call, op, say  # noqa: F401 (fixture)
from .test_agent_governance_boundaries import advance, begin, governed, policy  # noqa: F401 (fixture)
from .test_capabilities import _setup
from .test_tables import field, get, out, record, table


def read(g, operation="forms.form.list", payload=None, tid="list"):
    return call("run_operation", {"operation": operation, "workspace_id": g.people.a,
                                  "input": payload or {}}, tid=tid)


def create(g, title):
    return op(g.client, "forms.form.create", {"title": title}, ws=g.people.a)["output"]["id"]


def shown(g, cid):
    return op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]


def assert_sealed(g, cid):
    conversation = shown(g, cid)
    assert conversation["sealed"] == "access_changed"
    assistants = [m for m in conversation["messages"] if m["role"] == "assistant"]
    assert assistants and all(m["hidden"] and not m["body"] for m in assistants)
    assert all(c["hidden"] and c["result"] is None for m in assistants for c in m["tool_calls"])
    requests = len(g.model.requests)
    assert op(g.client, "agent.turn.start", {"conversation_id": cid, "text": "continue"},
              expect=409)["error"] == "conversation_sealed"
    assert len(g.model.requests) == requests


@pytest.mark.parametrize("original_count", [0, 2])
@pytest.mark.parametrize("previous_turn", [False, True])
def test_list_then_confirm_create_continues_without_hiding(governed, original_count, previous_turn):
    g = governed
    policy(g, g.people.a, "platform")
    originals = [create(g, f"Fixture original {i}") for i in range(original_count)]
    steps = [read(g)]
    if originals:
        steps.append(read(g, "forms.form.get", {"form_id": originals[0]}, tid="get"))
    write = call("run_operation", {"operation": "forms.form.create", "workspace_id": g.people.a,
                                   "input": {"title": "Fixture confirmed draft"}}, tid="create")
    if previous_turn:
        cid, first = begin(g, steps + [say("Fixture originals read")])
        assert advance(g, first)["status"] == "succeeded"
        cid, turn = begin(g, [write, say("Fixture draft created")], conversation=cid)
    else:
        cid, turn = begin(g, steps + [write, say("Fixture draft created")])
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=? AND tool_use_id='create'", (turn["turn_id"],))
    result = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"],
                                                     "confirmation": card["input_hash"]})["output"]
    assert result["status"] == "succeeded"
    finished = advance(g, turn)
    assert finished["status"] == "succeeded", finished["error_code"]
    assert not result["hidden"] and result["result"]["published_version"] is None
    assert result["result"]["public_link"] is None
    assert g.store.one("SELECT COUNT(*) AS n FROM forms WHERE title='Fixture confirmed draft'")["n"] == 1
    assert g.store.one("SELECT COUNT(*) AS n FROM op_runs WHERE op='forms.form.create' AND via='agent'")["n"] == 1
    _, later = begin(g, [say("Fixture continuation")], conversation=cid)
    assert advance(g, later)["status"] == "succeeded"
    conversation = shown(g, cid)
    assert conversation["sealed"] is None and not any(m["hidden"] for m in conversation["messages"])
    before = g.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"]
    for _ in range(2):
        assert g.engine._lost_workspaces(g.people.uid("alice"), cid) == set()
    assert g.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == before


@pytest.mark.parametrize("change", ["delete", "archive", "content", "membership", "application"])
def test_original_list_item_or_access_loss_still_seals_after_addition(governed, change):
    g = governed
    policy(g, g.people.a, "platform")
    create(g, "Fixture first original")
    original = create(g, "Fixture second original")
    # Only the list was read: every original entry needs proof, not just a selected get.
    cid, turn = begin(g, [read(g), say("Fixture list read")])
    assert advance(g, turn)["status"] == "succeeded"
    create(g, "Fixture later addition")
    assert shown(g, cid)["sealed"] is None
    if change == "delete":
        g.store.execute("DELETE FROM forms WHERE id=?", (original,))
    elif change == "archive":
        op(g.client, "forms.form.archive", {"form_id": original, "archived": True}, ws=g.people.a)
    elif change == "content":
        op(g.client, "forms.form.update_draft", {"form_id": original, "draft_rev": 1,
                                                "title": "Fixture changed original"}, ws=g.people.a)
    elif change == "membership":
        g.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                        (g.people.a, g.people.uid("alice")))
    else:
        g.store.execute("UPDATE apps SET status='disabled' WHERE id='forms'")
    assert_sealed(g, cid)
    events = g.engine.events_after(g.people.uid("alice"), cid, 0)
    assert all(e["data"]["hidden"] and not e["data"]["delta"] for e in events if e["type"] == "text")


def test_field_acl_denial_still_blocks_history_after_form_list_grows(governed):
    g = governed
    policy(g, g.people.a, "platform")
    tid = table(g.client)
    fid = field(g.client, tid, "Fixture restricted field")
    record(g.client, tid, {fid: "Fixture private value"})
    g.client.sign_in("bob")
    cid, turn = begin(g, [read(g), read(g, "tables.record.query", {"table_id": tid}, tid="records"),
                          say("Fixture private value")])
    assert advance(g, turn)["status"] == "succeeded"
    create(g, "Fixture addition")
    assert shown(g, cid)["sealed"] is None
    # Bob retains workspace membership and table access but loses the original field.
    out(g.client, "field.save", {"table_id": tid, "schema_rev": get(g.client, tid)["schema_rev"],
                                "field_id": fid, "field": {"name": "Fixture restricted field", "read_role": "admin"}})
    fresh = op(g.client, "tables.record.query", {"table_id": tid}, ws=g.people.a)["output"]
    assert fid not in fresh["records"][0]["cells"]
    assert_sealed(g, cid)


def test_loss_of_original_list_item_invalidates_pending_creation(governed):
    g = governed
    policy(g, g.people.a, "platform")
    original = create(g, "Fixture original")
    cid, first = begin(g, [read(g), say("Fixture original read")])
    assert advance(g, first)["status"] == "succeeded"
    create(g, "Fixture independent addition")
    _, turn = begin(g, [call("run_operation", {"operation": "forms.form.create", "workspace_id": g.people.a,
                                             "input": {"title": "Fixture must not be created"}}),
                       say("Fixture must not resume")], conversation=cid)
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=?", (turn["turn_id"],))
    g.store.execute("DELETE FROM forms WHERE id=?", (original,))
    requests = len(g.model.requests)
    refused = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"],
                                                      "confirmation": card["input_hash"]}, expect=409)
    assert refused["error"] == "source_access_changed"
    assert len(g.model.requests) == requests
    assert g.store.one("SELECT COUNT(*) AS n FROM forms WHERE title='Fixture must not be created'")["n"] == 0


@pytest.mark.parametrize("operation", ["tables.record.query", "fixture.note.list"])
def test_other_collection_snapshots_remain_exact(governed, operation):
    g = governed
    policy(g, g.people.a, "platform")
    if operation == "tables.record.query":
        tid = table(g.client)
        payload = {"table_id": tid}
        add = lambda: record(g.client, tid)
    else:
        _setup(g.client)
        payload = {}
        add = lambda: op(g.client, "fixture.note.create", {"text": "Fixture note"}, ws=g.people.a)
    add()
    cid, turn = begin(g, [read(g, operation, payload), say("Fixture records")])
    assert advance(g, turn)["status"] == "succeeded"
    add()
    assert_sealed(g, cid)


def test_legacy_list_hash_without_original_ids_remains_conservative(governed):
    g = governed
    policy(g, g.people.a, "platform")
    create(g, "Fixture original")
    cid, turn = begin(g, [read(g), say("Fixture legacy list")])
    assert advance(g, turn)["status"] == "succeeded"
    row = g.store.one("SELECT sources_json FROM agent_messages WHERE id=?", (turn["assistant_message_id"],))
    # Exact pre-fix source format; do not recover a historical item set from later data.
    source = json.loads(row["sources_json"])[0]
    legacy = {k: source[k] for k in ("workspace_id", "operation", "input", "snapshot_hash")}
    g.store.execute("UPDATE agent_messages SET sources_json=? WHERE id=?",
                    (canonical([legacy]), turn["assistant_message_id"]))
    assert shown(g, cid)["sealed"] is None
    create(g, "Fixture later addition")
    assert_sealed(g, cid)


@pytest.mark.parametrize("change", ["redacted_field", "duplicate_id", "missing_id", "envelope"])
def test_list_proof_rejects_filtered_fields_or_ambiguous_contract(governed, monkeypatch, change):
    g = governed
    policy(g, g.people.a, "platform")
    create(g, "Fixture original")
    cid, turn = begin(g, [read(g), say("Fixture original snapshot")])
    assert advance(g, turn)["status"] == "succeeded"
    inspect = g.client.caps.inspect

    def changed_read(operation, *args, **kwargs):
        result = inspect(operation, *args, **kwargs)
        if operation == "forms.form.list":
            # Fixture for an ACL-filtered response or an incompatible future read contract.
            if change == "redacted_field":
                result["forms"][0].pop("description")
            elif change == "duplicate_id":
                result["forms"].append(dict(result["forms"][0]))
            elif change == "missing_id":
                result["forms"][0].pop("id")
            else:
                result["total"] = len(result["forms"])
        return result

    monkeypatch.setattr(g.client.caps, "inspect", changed_read)
    assert_sealed(g, cid)
