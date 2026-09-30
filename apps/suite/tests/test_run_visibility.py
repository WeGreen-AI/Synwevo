"""Stored receipts must not become an ACL, deletion or secret-storage bypass.

All data and provider responses here are isolated fixtures, never live integrations.
"""
import json

import pytest

from synwevo_core.capabilities import canonical, fingerprint
from synwevo_core.errors import ServiceError
from synwevo_core.suite_schema.agent import migrate
from .test_agent import agent, call, op, say, start, wait_turn  # noqa: F401
from .test_capabilities import _setup


@pytest.mark.parametrize("change", ["membership", "resource", "application"])
def test_history_and_replay_recheck_access_without_repeating_writes(suite, change):
    store = _setup(suite)
    p = suite.people
    suite.sign_in("bob")
    args = dict(user_id=p.uid("bob"), workspace_id=p.a, idempotency_key="receipt")
    first = suite.caps.invoke("fixture.note.create", {"text": "private-source"}, **args)
    if change == "membership":
        store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (p.a, p.uid("bob")))
    elif change == "resource":
        store.execute("DELETE FROM fixture_notes WHERE id='n1'")
    else:
        store.execute("UPDATE apps SET status='disabled' WHERE id='fixture'")
    count = store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"]
    receipt = suite.get(f"/api/suite/runs/{first['run_id']}").json()
    listing = suite.get("/api/suite/runs").json()
    assert receipt["snapshot_hidden"] and receipt["input"] is None and receipt["output"] is None
    assert "private-source" not in json.dumps(listing)
    with pytest.raises(ServiceError):
        suite.caps.invoke("fixture.note.create", {"text": "private-source"}, **args)
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == count
    assert store.one("SELECT COUNT(*) AS n FROM op_runs WHERE op='fixture.note.create'")["n"] == 1


def test_read_receipt_cannot_restore_fields_hidden_by_role_downgrade(suite):
    suite.sign_in("bob")
    p = suite.people
    fid = op(suite, "forms.form.create", {"title": "Fixture", "definition": {"fields": [
        {"id": "f_name", "type": "text", "label": "Name"}]}}, ws=p.a)["output"]["id"]
    op(suite, "forms.form.publish", {"form_id": fid, "draft_rev": 1}, ws=p.a)
    op(suite, "forms.public_link.set", {"form_id": fid, "enabled": True}, ws=p.a)
    args = dict(user_id=p.uid("bob"), workspace_id=p.a, idempotency_key="read")
    prior = suite.caps.invoke("forms.form.get", {"form_id": fid}, **args)
    assert prior["output"]["public_link"]["url"]
    p.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?", (p.a, p.uid("bob")))
    receipt = suite.caps.run(prior["run_id"], user_id=p.uid("bob"))
    assert receipt["snapshot_hidden"] and receipt["output"] is None
    with pytest.raises(ServiceError) as error:
        suite.caps.invoke("forms.form.get", {"form_id": fid}, **args)
    assert error.value.code == "run_result_unavailable"
    fresh = suite.caps.inspect("forms.form.get", {"form_id": fid}, user_id=p.uid("bob"), workspace_id=p.a)
    assert fresh["public_link"]["url"] is None


def test_conversation_receipt_rechecks_sources_and_inspection_does_not_make_runs(agent):
    agent.sign_in("alice")
    p = agent.people
    fid = op(agent, "forms.form.create", {"title": "Fixture"}, ws=p.a)["output"]["id"]
    op(agent, "agent.workspace_policy.set", {"processing": "platform"}, ws=p.a)
    cid, turn = start(agent, [call("run_operation", {"operation": "forms.form.get", "workspace_id": p.a,
                                                   "input": {"form_id": fid}}), say("source-needle")])
    assert wait_turn(p.store, turn["turn_id"]) == "succeeded"
    args = dict(user_id=p.uid("alice"), idempotency_key="conversation-read")
    old = agent.caps.invoke("agent.conversation.get", {"conversation_id": cid}, **args)
    before = p.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"]
    for _ in range(3):
        agent.caps.inspect("agent.conversation.get", {"conversation_id": cid}, user_id=p.uid("alice"))
    assert p.store.one("SELECT COUNT(*) AS n FROM op_runs")["n"] == before
    p.store.execute("DELETE FROM forms WHERE id=?", (fid,))
    assert agent.caps.run(old["run_id"], user_id=p.uid("alice"))["snapshot_hidden"]
    assert "source-needle" not in json.dumps(agent.get("/api/suite/runs").json())
    with pytest.raises(ServiceError, match="已执行"):
        agent.caps.invoke("agent.conversation.get", {"conversation_id": cid}, **args)


def test_legacy_secret_redaction_is_repeatable_and_preserves_idempotency(agent):
    p = agent.people
    secret = "fixture-legacy-personal-secret-1234567890"
    args = dict(user_id=p.uid("alice"), idempotency_key="legacy-key")
    first = agent.caps.invoke("agent.settings.update", {"personal_key": secret}, **args)
    row = p.store.one("SELECT input FROM op_runs WHERE id=?", (first["run_id"],))
    legacy = json.loads(row["input"])
    legacy["personal_key"] = secret
    p.store.execute("UPDATE op_runs SET input=?,input_hash=? WHERE id=?",
                    (canonical(legacy), fingerprint(legacy), first["run_id"]))
    sealed = p.store.one("SELECT personal_key_sealed FROM agent_settings WHERE user_id=?", (p.uid("alice"),))
    for _ in range(2):
        with p.store.tx() as connection:
            migrate(connection)
    assert secret not in json.dumps(p.store.all("SELECT * FROM op_runs"), default=str)
    assert p.store.one("SELECT personal_key_sealed FROM agent_settings WHERE user_id=?", (p.uid("alice"),)) == sealed
    again = agent.caps.invoke("agent.settings.update", {"personal_key": secret}, **args)
    assert again["replayed"] and again["run_id"] == first["run_id"]


def test_revocation_between_admission_and_write_lock_prevents_mutation(suite, monkeypatch):
    store = _setup(suite)
    p = suite.people
    authorize = suite.caps.authorize
    admission = True

    def revoke_after_first_check(*args, **kwargs):
        nonlocal admission
        authorized = authorize(*args, **kwargs)
        if admission:
            admission = False
            # Deterministic scheduling of a competing permission change after admission.
            store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",
                          (p.a, p.uid("bob")))
        return authorized

    monkeypatch.setattr(suite.caps, "authorize", revoke_after_first_check)
    with pytest.raises(ServiceError) as error:
        suite.caps.invoke("fixture.note.create", {"text": "must not be stored"},
                          user_id=p.uid("bob"), workspace_id=p.a)
    assert error.value.code == "read_only_role"
    assert store.one("SELECT COUNT(*) AS n FROM fixture_notes")["n"] == 0
