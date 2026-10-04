"""Governance regressions on real Forms/Capabilities and isolated Store fixtures.

Only the model is scripted; credentials are explicit synthetic fixtures, never sent to a network.
Workers are advanced synchronously so policy/key changes land at exact continuation boundaries.
"""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from synwevo_suite.agent.provider import Credential, ProviderError
from .test_agent import ScriptedModel, agent, call, op, say  # noqa: F401 (fixture)
from .test_data_flow import revision


class GovernanceModel(ScriptedModel):
    name = "deepseek"

    def __init__(self):
        super().__init__([])
        self.credential = Credential("platform", "fixture-governance-key-not-real", "fixture-model",
                                     provider="deepseek", revision="fixture-revision-1")

    def resolve_credential(self, store, user_id):
        return self.credential


@pytest.fixture
def governed(agent, monkeypatch):
    model = GovernanceModel()
    agent.agent.provider = model
    monkeypatch.setattr(agent.agent, "_submit", lambda _: None)
    agent.sign_in("alice")
    return SimpleNamespace(client=agent, engine=agent.agent, people=agent.people,
                           store=agent.people.store, model=model)


def begin(g, steps, conversation=None):
    g.model.steps = list(steps)
    cid = conversation or op(g.client, "agent.conversation.create")["output"]["id"]
    turn = op(g.client, "agent.turn.start", {"conversation_id": cid, "text": "fixture request"})["output"]
    return cid, turn


def advance(g, turn):
    g.engine._run_safely(turn["turn_id"])
    return g.store.one("SELECT * FROM agent_turns WHERE id=?", (turn["turn_id"],))


def policy(g, workspace, processing):
    g.client.sign_in("oscar" if workspace == g.people.b else "alice")
    op(g.client, "agent.workspace_policy.set", {"processing": processing, "providers": ["deepseek"]}, ws=workspace)
    g.client.sign_in("alice")


def pending_copy(g, *, source_policy="platform", target_policy="platform", read_source=False):
    g.client.sign_in("oscar")
    source = op(g.client, "forms.form.create", {"title": "Fixture source definition"}, ws=g.people.b)["output"]["id"]
    # The legal transfer path (synwevo_core.data_flow): Alice may export from B, B lets content go to A.
    op(g.client, "agent.transfer_policy.set", {"outbound": "any", "revision": revision(g.people.store, g.people.b)}, ws=g.people.b)
    g.people.ident.change_role(g.people.b, g.people.uid("alice"), "viewer", can_export=True)
    policy(g, g.people.b, source_policy)
    policy(g, g.people.a, target_policy)
    steps = []
    if read_source:
        steps.append(call("run_operation", {"operation": "forms.form.get", "workspace_id": g.people.b,
                                             "input": {"form_id": source}}, tid="read-source"))
    steps += [call("run_operation", {"operation": "forms.form.duplicate", "workspace_id": g.people.b,
                                     "input": {"form_id": source, "target_workspace_id": g.people.a}}, tid="copy"), say("copied")]
    cid, turn = begin(g, steps)
    assert advance(g, turn)["status"] == "waiting_confirmation"
    card = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=? AND tool_use_id='copy'", (turn["turn_id"],))
    return cid, turn, card, source


def confirm(g, card):
    result = op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"],
                                                    "confirmation": card["input_hash"]})["output"]
    assert result["status"] == "succeeded"
    saved = g.store.one("SELECT * FROM agent_tool_calls WHERE id=?", (card["id"],))
    return saved, json.loads(saved["for_model_json"])


def test_copy_into_closed_target_executes_but_withholds_its_result(governed):
    g = governed
    _, turn, card, _ = pending_copy(g, target_policy="none")
    saved, payload = confirm(g, card)
    result = json.loads(saved["result_json"])
    assert g.store.one("SELECT workspace_id FROM forms WHERE id=?", (result["form_id"],))["workspace_id"] == g.people.a
    assert payload.get("withheld") is True
    assert advance(g, turn)["status"] == "succeeded"
    replay = g.model.requests[-1]["messages"][-1]["content"][0]["content"]
    assert json.loads(replay)["withheld"] is True and result["form_id"] not in replay


def test_copy_receipt_and_provenance_belong_to_target_independently_of_source(governed):
    g = governed
    _, turn, card, source = pending_copy(g, source_policy="none")
    saved, payload = confirm(g, card)
    assert not payload.get("withheld")
    assert payload["source"]["workspace_id"] == g.people.a
    sources = json.loads(g.store.one("SELECT sources_json FROM agent_messages WHERE id=?", (turn["assistant_message_id"],))["sources_json"])
    # What the answer depends on is the copy in A. Where it came from is kept only for the transfer
    # question of a later write in this conversation (CR-047 AGENT-DF-3); it never hides the copy.
    assert {s["workspace_id"] for s in sources if "carried_into" not in s} == {g.people.a}
    assert {"workspace_id": g.people.b, "carried_into": g.people.a} in sources
    result = json.loads(saved["result_json"])
    g.client.sign_in("oscar")
    op(g.client, "forms.form.update_draft", {"form_id": source, "draft_rev": 1, "title": "Changed source"}, ws=g.people.b)
    g.client.sign_in("alice")
    assert advance(g, turn)["status"] == "succeeded"
    copied = op(g.client, "forms.form.get", {"form_id": result["form_id"]}, ws=g.people.a)["output"]
    assert copied["title"] == "Fixture source definition"


@pytest.mark.parametrize("later_turn", [False, True])
@pytest.mark.parametrize("legacy_payload", [False, True])
def test_target_policy_revocation_blocks_copy_result_replay(governed, later_turn, legacy_payload):
    g = governed
    cid, turn, card, _ = pending_copy(g)
    saved, payload = confirm(g, card)
    if legacy_payload:
        # Persisted pre-fix result: tagged by the invocation's source, without result ownership.
        payload.pop("source_workspaces", None)
        payload["source"]["workspace_id"] = g.people.b
        g.store.execute("UPDATE agent_tool_calls SET for_model_json=? WHERE id=?", (json.dumps(payload), saved["id"]))
    if later_turn:
        assert advance(g, turn)["status"] == "succeeded"
    policy(g, g.people.a, "none")
    if later_turn:
        _, turn = begin(g, [say("must not dispatch")], conversation=cid)
    sent = len(g.model.requests)
    refused = advance(g, turn)
    assert refused["status"] == "failed" and refused["error_code"] == "source_policy_changed"
    assert len(g.model.requests) == sent


def test_separately_read_source_still_constrains_copy_continuation(governed):
    g = governed
    _, turn, card, _ = pending_copy(g, read_source=True)
    confirm(g, card)
    policy(g, g.people.b, "none")
    sent = len(g.model.requests)
    refused = advance(g, turn)
    assert refused["error_code"] == "source_policy_changed" and len(g.model.requests) == sent


@pytest.mark.parametrize("outbound, outcome", [("none", "transfer_source_closed"), ("audience", None)])
def test_copy_result_keeps_target_policy_dependency_for_subsequent_writes(governed, outbound, outcome):
    """The copy now belongs to A: a later write elsewhere carries A's content, under A's policy.
    The third workspace has only Alice, so A's "audience" policy lets it go there; "none" does not.
    Inside this conversation the copy also keeps where it came from, so B is asked too (B allows
    "any" here)."""
    g = governed
    third = g.people.ident.create_workspace("Fixture third workspace", owner_id=g.people.uid("alice"))["id"]
    _, turn, card, _ = pending_copy(g)
    saved, _ = confirm(g, card)
    copied_id = json.loads(saved["result_json"])["form_id"]
    op(g.client, "agent.transfer_policy.set", {"outbound": outbound, "revision": revision(g.people.store, g.people.a)}, ws=g.people.a)
    g.model.steps = [call("run_operation", {"operation": "forms.form.create", "workspace_id": third,
                                           "input": {"title": copied_id}}, tid="export-receipt"), say("stopped")]
    status = advance(g, turn)["status"]
    later = g.store.one("SELECT * FROM agent_tool_calls WHERE turn_id=? AND tool_use_id='export-receipt'",
                        (turn["turn_id"],))
    if outcome:
        assert status == "succeeded" and later["error_code"] == outcome
        assert not g.store.one("SELECT 1 FROM forms WHERE workspace_id=?", (third,))
        return
    assert status == "waiting_confirmation" and later["status"] == "awaiting_confirmation"
    assert sorted(b["source"] for b in json.loads(later["transfer_json"])) == sorted([g.people.a, g.people.b])
    # The card was confirmed against A's policy as it was; tightening it voids the confirmation.
    op(g.client, "agent.transfer_policy.set", {"outbound": "none", "revision": revision(g.people.store, g.people.a)}, ws=g.people.a)
    result = op(g.client, "agent.tool_call.confirm", {"tool_call_id": later["id"],
                                                    "confirmation": later["input_hash"]})["output"]
    assert result["status"] == "failed" and result["error"]["code"] == "transfer_source_closed"
    assert not g.store.one("SELECT 1 FROM forms WHERE workspace_id=?", (third,))


def test_new_turn_persists_binding_without_disclosing_it(governed):
    g = governed
    cid, turn = begin(g, [say("done")])
    stored = g.store.one("SELECT credential_binding FROM agent_turns WHERE id=?", (turn["turn_id"],))["credential_binding"]
    assert stored == g.model.credential.binding
    assert advance(g, turn)["status"] == "succeeded"
    public = op(g.client, "agent.conversation.get", {"conversation_id": cid})["output"]
    events = g.engine.events_after(g.people.uid("alice"), cid, 0)
    assert stored not in json.dumps([public, events])
    assert "credential_binding" not in json.dumps([public, events])


def test_dispatch_authority_check_uses_persisted_binding_inside_store_transaction(governed):
    g = governed
    _, turn = begin(g, [say("must not dispatch")])
    g.model.credential = replace(g.model.credential, revision="fixture-revision-2")
    with g.store.tx():
        current_turn = g.store.one("SELECT * FROM agent_turns WHERE id=?", (turn["turn_id"],))
        with pytest.raises(ProviderError) as refused:
            # Both arguments are freshly loaded, as in the dispatch-authority callback.
            g.engine._check_context(current_turn, g.model.resolve_credential(g.store, g.people.uid("alice")))
        assert refused.value.code == "model_configuration_changed"
    assert not g.model.requests


@pytest.mark.parametrize("change", [{"api_key": "fixture-replacement-not-real"},
                                   {"revision": "fixture-revision-2"}, {"provider": "anthropic"}])
@pytest.mark.parametrize("between_steps", [False, True])
def test_binding_change_blocks_queued_or_next_step(governed, change, between_steps):
    g = governed
    def rotate(_):
        g.model.credential = replace(g.model.credential, **change)
        return call("find_operations", {"query": "form"})
    _, turn = begin(g, [rotate, say("must not dispatch")] if between_steps else [say("must not dispatch")])
    if not between_steps:
        g.model.credential = replace(g.model.credential, **change)
    refused = advance(g, turn)
    assert refused["error_code"] == "model_configuration_changed"
    assert len(g.model.requests) == int(between_steps)


@pytest.mark.parametrize("stage", ["queued", "confirm", "resume", "recover"])
def test_null_legacy_binding_never_gets_backfilled_or_dispatches(governed, stage):
    g = governed
    if stage == "queued":
        _, turn = begin(g, [say("must not dispatch")])
    else:
        _, turn, card, _ = pending_copy(g)
        if stage == "resume":
            confirm(g, card)
    g.store.execute("UPDATE agent_turns SET credential_binding=NULL WHERE id=?", (turn["turn_id"],))
    sent = len(g.model.requests)
    if stage == "confirm":
        assert op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]},
                  expect=409)["error"] == "model_configuration_changed"
        assert not g.store.one("SELECT 1 FROM forms WHERE workspace_id=?", (g.people.a,))
    elif stage == "recover":
        assert g.engine.recover() == 1
        assert g.store.one("SELECT error_code FROM agent_turns WHERE id=?", (turn["turn_id"],))["error_code"] == "model_configuration_changed"
    else:
        assert advance(g, turn)["error_code"] == "model_configuration_changed"
    assert len(g.model.requests) == sent
    assert g.store.one("SELECT credential_binding FROM agent_turns WHERE id=?", (turn["turn_id"],))["credential_binding"] is None


@pytest.mark.parametrize("stage", ["confirm", "resume", "recover"])
def test_rotation_while_waiting_preserves_original_binding_and_stops_continuation(governed, stage):
    g = governed
    _, turn, card, _ = pending_copy(g)
    original = g.model.credential.binding
    if stage == "resume":
        confirm(g, card)
    g.model.credential = replace(g.model.credential, revision="fixture-revision-2")
    sent = len(g.model.requests)
    if stage == "confirm":
        assert op(g.client, "agent.tool_call.confirm", {"tool_call_id": card["id"], "confirmation": card["input_hash"]},
                  expect=409)["error"] == "model_configuration_changed"
        assert not g.store.one("SELECT 1 FROM forms WHERE workspace_id=?", (g.people.a,))
    elif stage == "recover":
        assert g.engine.recover() == 1
        assert g.store.one("SELECT error_code FROM agent_turns WHERE id=?", (turn["turn_id"],))["error_code"] == "model_configuration_changed"
    else:
        assert advance(g, turn)["error_code"] == "model_configuration_changed"
    assert len(g.model.requests) == sent
    assert g.store.one("SELECT credential_binding FROM agent_turns WHERE id=?", (turn["turn_id"],))["credential_binding"] == original


def test_waiting_turn_with_same_binding_survives_recovery_and_can_confirm(governed):
    g = governed
    _, turn, card, _ = pending_copy(g)
    g.model.credential = replace(g.model.credential)  # Reloaded credentials, no original object retained.
    assert g.engine.recover() == 0
    confirm(g, card)
    assert advance(g, turn)["status"] == "succeeded"
    assert len(g.model.requests) == 2
