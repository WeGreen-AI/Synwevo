"""A flow run whose stored state no longer fits its definition fails alone (CR-048 DF-B4).

A damaged row, or a version that a later or earlier release stored and this one cannot read, used
to make the worker's loop raise: every tick failed and nobody's runs were picked up again. Real
engine and the isolated fixture application (test_flows.flow); the damage is written straight into
the fixture store, as a damaged or downgraded database would hold it.
"""
from __future__ import annotations

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
