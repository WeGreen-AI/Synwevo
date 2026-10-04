"""A flow run whose stored state no longer fits its definition fails alone (CR-048 DF-B4).

A damaged row, or a version that a later or earlier release stored and this one cannot read, used
to make the worker's loop raise: every tick failed and nobody's runs were picked up again. Real
engine and the isolated fixture application (test_flows.flow); the damage is written straight into
the fixture store, as a damaged or downgraded database would hold it.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core.errors import ServiceError

from .test_flows import flow  # noqa: F401 — the isolated fixture application


def state(f, rid):
    row = f.row(rid)
    return row["status"], row["error_code"]


def test_a_run_at_a_step_its_definition_does_not_have_fails_alone(flow):
    f = flow
    poison = f.start(f.create([f.write()]), key="poison")
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.store.execute("UPDATE flow_runs SET step_id='ghost' WHERE id=?", (poison,))
    f.tick()                                                 # used to raise StopIteration
    assert state(f, poison) == ("failed", "flow_definition_invalid")
    assert state(f, healthy)[0] == "succeeded"
    later = f.start(f.create([f.write()]), key="later")
    f.tick()
    assert state(f, later)[0] == "succeeded", "the worker keeps working"
    view = f.e.run(poison, user_id=f.uid)                     # the owner can still look at it
    assert view["status"] == "failed" and view["error_code"] == "flow_definition_invalid"


def test_a_waiting_run_that_no_longer_fits_does_not_stop_every_tick(flow):
    f = flow
    poison = f.start(f.create([f.write(), f.waiting()]), key="poison")
    f.tick()
    assert f.row(poison)["status"] in ("waiting_input", "waiting_approval")
    f.store.execute("UPDATE flow_runs SET step_id='ghost' WHERE id=?", (poison,))
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.invoke("fixtureflow.create", {"text": "any later event"})   # wakes waits: used to raise every tick
    f.clock.advance(120)
    f.tick()
    assert state(f, healthy)[0] == "succeeded"
    f.clock.advance(3600)
    f.tick()
    assert state(f, poison) == ("failed", "flow_definition_invalid")


def test_a_stored_definition_this_release_cannot_read_fails_its_run_alone(flow):
    f = flow
    fid = f.create([f.write()])
    broken = f.start(fid, key="broken")
    f.store.execute("UPDATE flow_versions SET spec_json='{\"steps\": \"not a list\"}' WHERE flow_id=?", (fid,))
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()                                                 # used to raise ValidationError
    assert state(f, broken) == ("failed", "flow_definition_invalid")
    assert state(f, healthy)[0] == "succeeded"
    assert f.e.run(broken, user_id=f.uid)["status"] == "failed"


# ---------------------------------------------------------------- a refusal while waiting (DF-B3)

def _set_outbound(f, ws, outbound):
    from synwevo_core import data_flow
    data_flow.set_workspace_outbound(f.store, ws, outbound, actor_id=f.uid,
                                     revision=data_flow.workspace_outbound(f.store, ws)["revision"])


def test_a_refusal_while_waiting_for_a_receipt_keeps_the_wait_and_resumes_from_it(flow):
    """CR-048 DF-B3: A is closed while its job runs; the poll is refused. The job then fails
    definitely and A reopens: resuming takes the job's own outcome (on_error), not a reconciliation
    request that never goes away."""
    from synwevo_core.errors import ServiceError
    from .test_flows_outcomes import job, jobs, steps_of
    f = flow
    started = jobs(f)
    _set_outbound(f, f.ws, "any")
    cross = {"result_read": {"operation": "fixtureflow.get", "workspace_id": f.p.b, "input": {"id": {"$ref": "receipt.job"}}}}
    rid = f.start(f.create([job(f, on_error="cleanup", **cross), f.write("after", next="end"), f.write("cleanup")]))
    f.tick()
    assert steps_of(f, rid)["job"][0] == "operation_wait"
    _set_outbound(f, f.ws, "none")
    f.clock.advance(31)
    f.tick()
    assert state(f, rid) == ("blocked", "transfer_source_closed")
    assert steps_of(f, rid)["job"][0] == "operation_wait", "the step still waits for its receipt"
    _set_outbound(f, f.ws, "any")
    f.caps.update_run(started[0], "failed", error=ServiceError(422, "fixture_job_failed", "fixture"))
    assert f.e.resume(rid, user_id=f.uid)["status"] == "queued"
    f.tick()
    assert state(f, rid)[0] in ("succeeded", "partial"), state(f, rid)
    assert steps_of(f, rid)["job"] == ("failed", "flow_operation_failed") and steps_of(f, rid)["cleanup"][0] == "succeeded"


def test_a_role_withdrawn_while_waiting_and_restored_lets_the_run_finish(flow):
    from .test_flows_outcomes import job, jobs, steps_of
    f = flow
    started = jobs(f)
    f.p.ident.add_member(f.ws, f.p.uid("bob"), "owner")
    rid = f.start(f.create([job(f), f.write("after")]))
    f.tick()
    f.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?", (f.ws, f.uid))
    f.clock.advance(31)
    f.tick()
    assert state(f, rid)[0] == "blocked" and steps_of(f, rid)["job"][0] == "operation_wait"
    f.store.execute("UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?", (f.ws, f.uid))
    f.caps.update_run(started[0], "succeeded", output={"job": started[0], "issues": 0})
    f.e.resume(rid, user_id=f.uid)
    f.tick()
    assert state(f, rid)[0] == "succeeded", (state(f, rid), steps_of(f, rid))


def test_an_application_switched_off_while_waiting_for_a_receipt_keeps_the_wait(flow):
    """R3-FE-03: not every check that stops before the receipt is read is a 401/403. An operator
    switches the application off (404 app_unavailable) while its job runs; the job then fails
    definitely and the application comes back. Resuming takes the job's own outcome (on_error), not
    a reconciliation request that never goes away."""
    from .test_flows_outcomes import job, jobs, steps_of
    f = flow
    started = jobs(f)
    rid = f.start(f.create([job(f, on_error="cleanup"), f.write("after", next="end"), f.write("cleanup")]))
    f.tick()
    assert steps_of(f, rid)["job"][0] == "operation_wait"
    f.store.execute("UPDATE apps SET status='disabled' WHERE id='fixtureflow'")
    f.clock.advance(31)
    f.tick()
    assert state(f, rid) == ("blocked", "app_unavailable")
    assert steps_of(f, rid)["job"][0] == "operation_wait", "the step still waits for its receipt"
    with pytest.raises(ServiceError) as err:                 # resuming while it is off asks again
        f.e.resume(rid, user_id=f.uid)
    assert err.value.code == "app_unavailable"
    f.store.execute("UPDATE apps SET status='active' WHERE id='fixtureflow'")
    f.caps.update_run(started[0], "failed", error=ServiceError(422, "fixture_job_failed", "fixture"))
    assert f.e.resume(rid, user_id=f.uid)["status"] == "queued"
    f.tick()
    assert state(f, rid) == ("partial", None)
    assert steps_of(f, rid)["job"] == ("failed", "flow_operation_failed") and steps_of(f, rid)["cleanup"][0] == "succeeded"
    assert "after" not in steps_of(f, rid) and len(started) == 1

# ---------------------------------------------------------------- rows beyond the step (R3-FE-01)
# The same premise as DF-B4 — a row another release stored that this one cannot read — reached
# through a trigger, a run's policy, the chain a run's sources are read over, and the Agent's view.

def _later_release(f, table, column, key, value, **extra):
    """The row as a later release stored it: the same document with fields this one does not know."""
    row = f.store.one(f"SELECT {column} FROM {table} WHERE {key}=?", (value,))
    f.store.execute(f"UPDATE {table} SET {column}=? WHERE {key}=?", (json.dumps(json.loads(row[column]) | extra), value))


def _schedule(f, fid):
    return f.e.create_trigger(fid, user_id=f.uid, version=1, policy=f.policy(),
                              trigger={"kind": "schedule", "timezone": "UTC", "at": "00:01", "misfire": "once"})["trigger_id"]


def test_a_due_schedule_on_a_version_this_release_cannot_read_is_refused_alone(flow):
    f = flow
    fid = f.create([f.write()])
    tid = _schedule(f, fid)
    _later_release(f, "flow_versions", "spec_json", "flow_id", fid, concurrency=2)
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.clock.advance(120)
    f.tick()                                                 # used to raise ValidationError
    assert state(f, healthy)[0] == "succeeded"
    receipts = f.e.deliveries(tid, user_id=f.uid)["deliveries"]
    assert [(d["status"], d["error_code"]) for d in receipts] == [("refused", "flow_definition_invalid")]
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs WHERE trigger_id=?", (tid,))["n"] == 0


def test_an_event_trigger_this_release_cannot_read_is_refused_alone_and_can_be_switched_off(flow):
    f = flow
    tid = f.e.create_trigger(f.create([f.write()]), user_id=f.uid, version=1, policy=f.policy(),
                             trigger={"kind": "event", "event_type": "fixtureflow.decided", "workspace_id": f.ws})["trigger_id"]
    _later_release(f, "flow_triggers", "spec_json", "id", tid, debounce_s=30)
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()                                                 # no event needed: read on every tick
    assert state(f, healthy)[0] == "succeeded"
    assert f.e.trigger(tid, user_id=f.uid)["error_code"] == "flow_trigger_invalid"
    assert f.e.trigger_view(tid, user_id=f.uid)["trigger"]["debounce_s"] == 30   # still shows what it holds
    # Its owner can always switch it off; switching it on asks this release to read it.
    assert f.e.set_trigger_enabled(tid, user_id=f.uid, enabled=False)["enabled"] is False
    with pytest.raises(ServiceError) as err:
        f.e.set_trigger_enabled(tid, user_id=f.uid, enabled=True)
    assert err.value.code == "flow_trigger_invalid"


def test_a_triggered_run_whose_definition_this_release_cannot_read_fails_alone(flow):
    f = flow
    fid = f.create([f.write()])
    _schedule(f, fid)
    f.clock.advance(120)
    f.e.trigger_tick()
    poison = f.store.one("SELECT id FROM flow_runs")["id"]
    _later_release(f, "flow_versions", "spec_json", "flow_id", fid, concurrency=2)
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()                                                 # its policy check read the version first
    assert state(f, poison) == ("failed", "flow_definition_invalid")
    assert state(f, healthy)[0] == "succeeded"


def test_a_triggered_run_whose_policy_this_release_cannot_read_is_blocked_alone(flow):
    f = flow
    fid = f.create([f.write()])
    tid = _schedule(f, fid)
    f.clock.advance(120)
    f.e.trigger_tick()
    poison = f.store.one("SELECT id FROM flow_runs")["id"]
    _later_release(f, "flow_triggers", "policy_json", "id", tid, max_cost=5)
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()
    # The run's authority cannot be read: it is refused, not failed — no on_error path is taken.
    assert state(f, poison) == ("blocked", "flow_trigger_invalid")
    assert state(f, healthy)[0] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_a_chain_with_a_version_this_release_cannot_read_counts_every_workspace_it_names(flow):
    """A run started by an event of a run whose version later became unreadable: which of that
    version's steps ran where cannot be told, so each workspace it names is a source of the chain.
    Alice is a viewer in B (no export right): the child's write into A is refused for B, alone."""
    f = flow
    origin_flow = f.create([f.write()])
    child_flow = f.create([f.write("second", result_read=None)])
    f.e.create_trigger(child_flow, user_id=f.uid, version=1, policy=f.policy(runs=1),
                       trigger={"kind": "event", "event_type": "fixtureflow.created", "workspace_id": f.ws})
    origin = f.start(origin_flow, key="origin")
    f.e.tick(limit=1)
    assert state(f, origin)[0] == "succeeded"
    f.e.trigger_tick()
    child = f.store.one("SELECT id FROM flow_runs WHERE trigger_id IS NOT NULL")["id"]
    assert f.row(child)["correlation_id"] == f.row(origin)["correlation_id"]
    later_step = {"id": "later", "kind": "notify", "workspace_id": f.p.b}      # a step kind this release lacks
    spec = json.loads(f.store.one("SELECT spec_json FROM flow_versions WHERE flow_id=?", (origin_flow,))["spec_json"])
    _later_release(f, "flow_versions", "spec_json", "flow_id", origin_flow, steps=[*spec["steps"], later_step])
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()                                                 # used to raise inside the child's transfer question
    assert state(f, child) == ("blocked", "transfer_export_denied")
    assert state(f, healthy)[0] == "succeeded"
    assert f.e.carried([child], user_id=f.uid) == sorted([f.ws, f.p.b])


def test_a_run_whose_definition_this_release_cannot_read_still_names_its_sources(flow):
    f = flow
    from synwevo_suite.flows import install
    install(f.suite.api.state.suite)
    fid = f.create([f.write()])
    broken = f.start(fid, key="broken")
    _later_release(f, "flow_versions", "spec_json", "flow_id", fid, concurrency=2)
    f.tick()
    assert state(f, broken) == ("failed", "flow_definition_invalid")
    # Where its steps would have run is all that can be told of it, and it counts.
    assert f.e.carried([broken], user_id=f.uid) == [f.ws]
    answer = f.caps.invoke("agent.flow.run", {"run_id": broken}, user_id=f.uid, via="agent")["output"]
    assert answer["status"] == "failed" and answer["source_workspaces"] == [f.ws]
    with pytest.raises(ServiceError) as err:                 # a refusal, not a server fault
        f.e.definition(fid, user_id=f.uid, version=1)
    assert err.value.code == "flow_definition_invalid"


def test_a_stored_reader_this_release_cannot_read_refuses_its_source_alone(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.write("copy", input={"text": {"$ref": "steps.write.text"}})]), key="poison")
    f.e.tick(limit=1)
    assert f.row(rid)["step_id"] == "copy"
    row = f.store.one("SELECT reader_json FROM flow_steps WHERE run_id=? AND step_id='write'", (rid,))
    f.store.execute("UPDATE flow_steps SET reader_json=? WHERE run_id=? AND step_id='write'",
                    (json.dumps(json.loads(row["reader_json"]) | {"consistency": "strong"}), rid))
    healthy = f.start(f.create([f.write()]), key="healthy")
    f.tick()                                                 # used to raise while re-reading the source
    assert state(f, rid) == ("blocked", "flow_result_unverifiable")
    assert state(f, healthy)[0] == "succeeded"
    view = f.e.run(rid, user_id=f.uid)                       # metadata stays, the snapshot is hidden
    assert [s["output"] for s in view["steps"] if s["step_id"] == "write"] == [None]


# ---------------------------------------------------------------- a refusal inside an attempt (R3-FE-05)

def test_a_refusal_inside_a_transactional_attempt_does_not_spend_it(flow):
    """The handler refuses (a record-level 403) inside the attempt, which rolls back: nothing was
    done, so nothing of max_attempts (1) is spent. Once access is granted, resuming asks again and
    the write is made, once."""
    from .test_flows import fixture_operation
    f = flow
    allowed = {"yes": False}

    def guarded(c, i):
        if not allowed["yes"]:
            raise ServiceError(403, "fixture_record_forbidden", "fixture record-level refusal")
        c.store.execute("INSERT INTO fixture_flow_records (id,workspace_id,owner_id,text,state) VALUES (?,?,?,?,'pending')",
                        ("fixture_guarded", c.workspace_id, c.user_id, i.text))
        return {"ok": True}
    fixture_operation(f, "guarded", guarded)
    rid = f.start(f.create([f.write(operation="fixtureflow.guarded", result_read=None)]))
    f.tick()
    assert state(f, rid) == ("blocked", "fixture_record_forbidden")
    allowed["yes"] = True
    assert f.e.resume(rid, user_id=f.uid)["status"] == "queued"   # used to answer flow_attempts_exhausted
    f.tick()
    assert state(f, rid) == ("succeeded", None)
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records WHERE id='fixture_guarded'")["n"] == 1


def test_a_result_read_that_would_be_refused_is_asked_before_its_step_runs(flow):
    """A read step in A with literal input, whose result read in A takes a value read in B: the
    B -> A question is asked before the step runs, so the refused step spends neither its attempt
    nor a call. Once B's owner opens B's policy, resuming finishes the run."""
    f = flow
    f.store.execute("UPDATE memberships SET role='editor' WHERE workspace_id=? AND user_id=?", (f.p.b, f.uid))
    mine = f.invoke("fixtureflow.create", {"text": "A"})["id"]
    pointer = f.invoke("fixtureflow.create", {"text": mine}, who="oscar", ws=f.p.b)["id"]
    rid = f.start(f.create([
        {"id": "readb", "kind": "call", "operation": "fixtureflow.get", "workspace_id": f.p.b, "input": {"id": pointer}},
        {"id": "reada", "kind": "call", "operation": "fixtureflow.get", "workspace_id": f.ws, "input": {"id": mine},
         "result_read": {"operation": "fixtureflow.get", "workspace_id": f.ws, "input": {"id": {"$ref": "steps.readb.text"}}}},
    ]))
    f.e.tick(limit=1)
    assert f.row(rid)["step_id"] == "reada"
    spent = f.row(rid)["calls_used"]
    f.tick()
    assert state(f, rid) == ("blocked", "transfer_audience_denied")      # B shares only with its audience
    assert f.row(rid)["calls_used"] == spent
    from synwevo_core import data_flow
    data_flow.set_workspace_outbound(f.store, f.p.b, "any", actor_id=f.p.uid("oscar"),
                                     revision=data_flow.workspace_outbound(f.store, f.p.b)["revision"])
    assert f.e.resume(rid, user_id=f.uid)["status"] == "queued"
    f.tick()
    assert state(f, rid) == ("succeeded", None)
    assert f.result(rid, "reada")["id"] == mine
