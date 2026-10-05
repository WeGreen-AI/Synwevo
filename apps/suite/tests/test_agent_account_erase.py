"""Deleting an account erases what the person said to and heard from the Agent — everywhere.

Every Agent operation goes through the capability layer, which keeps a run record per call with
its canonical input and its output (``op_runs``): a conversation's title, each message sent, each
memory, a report's note, and a full copy of every transcript the person opened. Account deletion
(``synwevo_core.agent_records.erase``, run by the account service at the end of the grace period
and again by the security journal after a restore) must erase those copies too, and keep only the
receipt: who ran which operation, when, with what result.

Real Agent operations through ``/api/suite/ops`` with the scripted test-double model (no network).
"""
from __future__ import annotations

import json
import time

from synwevo_core import journal
from synwevo_core.agent_records import ERASED_RUN_INPUT, ERASED_RUN_INPUT_HASH, erase
from synwevo_core.capabilities import canonical, fingerprint

from .test_agent import CSRF, agent, op, say, start, wait_turn  # noqa: F401 — fixture

PRIVATE = ("PRIVATE-TITLE-FIXTURE", "PRIVATE-MESSAGE-FIXTURE", "PRIVATE-ANSWER-FIXTURE",
           "PRIVATE-MEMORY-FIXTURE", "PRIVATE-NOTE-FIXTURE")


def _runs(store, uid):
    return store.all("SELECT id,op,app,status,user_id,created_at,input,output,error_detail FROM op_runs "
                     "WHERE user_id=? ORDER BY created_at, id", (uid,))


def _leaks(rows):
    text = json.dumps(rows, ensure_ascii=False)
    return [p for p in PRIVATE if p in text]


def test_account_deletion_erases_agent_content_from_operation_runs(agent):
    p = agent.people
    store, uid = p.store, p.uid("alice")
    agent.sign_in("alice")
    conv = op(agent, "agent.conversation.create", {"title": "PRIVATE-TITLE-FIXTURE"})["output"]["id"]
    _, out = start(agent, [say("PRIVATE-ANSWER-FIXTURE")], text="PRIVATE-MESSAGE-FIXTURE", conv=conv)
    assert wait_turn(store, out["turn_id"]) == "succeeded"
    op(agent, "agent.memory.add", {"body": "PRIVATE-MEMORY-FIXTURE"})
    op(agent, "agent.conversation.get", {"conversation_id": conv})
    op(agent, "agent.message.report", {"conversation_id": conv, "message_id": out["assistant_message_id"],
                                       "category": "wrong", "note": "PRIVATE-NOTE-FIXTURE"})
    # Business work the person did in a workspace is that workspace's record, not Agent content.
    form = op(agent, "forms.form.create", {"title": "Fixture workspace form"}, ws=p.a)
    before = _runs(store, uid)
    assert set(_leaks(before)) == set(PRIVATE), "fixture: the run records hold every piece of Agent content"

    with store.tx():
        erase(store, uid)

    after = _runs(store, uid)
    assert _leaks(after) == [], "no Agent content survives in the run records"
    receipt = lambda rows: [(r["id"], r["op"], r["status"], r["created_at"]) for r in rows]  # noqa: E731
    assert receipt(after) == receipt(before), "every run keeps its receipt"
    agent_runs = [r for r in after if r["app"] == "agent"]
    assert agent_runs and all(r["output"] is None and r["error_detail"] is None for r in agent_runs)
    marker = json.loads(ERASED_RUN_INPUT)
    assert (canonical(marker), fingerprint(marker)) == (ERASED_RUN_INPUT, ERASED_RUN_INPUT_HASH)
    assert {r["input_hash"] for r in store.all("SELECT input_hash FROM op_runs WHERE user_id=? AND app='agent'",
                                               (uid,))} == {ERASED_RUN_INPUT_HASH}, "the run stays self-consistent"
    kept = next(r for r in after if r["id"] == form["run_id"])
    assert "Fixture workspace form" in json.dumps(kept["input"], ensure_ascii=False), \
        "a workspace's business record is not the Agent's"
    with store.tx():
        assert erase(store, uid) == 0, "erasing again changes nothing"

    # A restore from before the deletion brings the run records back; the replayed deletion erases them again.
    raw = lambda v: v if v is None or isinstance(v, str) else json.dumps(v, ensure_ascii=False)  # noqa: E731
    with store.tx() as t:
        for r in before:
            t.execute("UPDATE op_runs SET input=?, output=?, error_detail=? WHERE id=?",
                      (raw(r["input"]), raw(r["output"]), raw(r["error_detail"]), r["id"]))
    assert _leaks(_runs(store, uid))
    journal.apply(store, [{"at": time.time(), "kind": "user_deleted", "user_id": uid}])
    assert _leaks(_runs(store, uid)) == []
