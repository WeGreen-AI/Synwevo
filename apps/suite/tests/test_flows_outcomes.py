"""How a flow continues after an operation reaches a definite outcome other than plain success.

``fixtureflow.job`` is a test-only asynchronous operation (see test_flows.py): it queues, and the
test reports its outcome through the same ``Capabilities.update_run`` a real worker uses.
"""
from __future__ import annotations

import pytest

from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError
from .test_flows import TEXT, FixtureInput, flow  # noqa: F401


def jobs(f, *, mode="async", status="queued"):
    started = []

    def start(c, i):
        started.append(c.run_id)
        return {"__run_status__": status, "job": c.run_id, "issues": 2}
    f.caps.registry.register(Operation(id="fixtureflow.job", app="fixtureflow", title=TEXT, summary="Asynchronous test fixture",
                                       input=FixtureInput, handler=start, need="write", side_effect="write",
                                       agent="confirm", mode=mode, transactional=mode == "sync"))
    return started


def job(f, **kw):
    return {"id": "job", "kind": "call", "operation": "fixtureflow.job", "workspace_id": f.ws, "input": {"text": "x"}, **kw}


def steps_of(f, rid):
    return {r["step_id"]: (r["status"], r["error_code"]) for r in
            f.store.all("SELECT step_id,status,error_code FROM flow_steps WHERE run_id=?", (rid,))}


def report(f, run_id, status, **kw):
    f.caps.update_run(run_id, status, **kw)
    f.clock.advance(31)
    f.tick()


def test_partial_completes_the_step_branches_on_its_outcome_and_the_run_ends_partial(flow):
    f = flow
    started = jobs(f)
    rid = f.start(f.create([
        job(f),
        {"id": "choose", "kind": "branch", "condition": {"left": {"$ref": "outcome.job"}, "right": "partial"},
         "then": "issues", "otherwise": "clean"},
        f.write("issues", next="end"), f.write("clean")]))
    f.tick()
    assert f.row(rid)["status"] == "waiting_input"
    report(f, started[0], "partial", output={"job": started[0], "issues": 2})
    assert f.row(rid)["status"] == "partial"  # Never reported as plain success after row issues.
    done = steps_of(f, rid)
    assert done["job"][0] == "partial" and done["issues"][0] == "succeeded" and "clean" not in done
    assert f.result(rid, "job")["issues"] == 2
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_a_clean_outcome_takes_the_other_branch_and_succeeds(flow):
    f = flow
    started = jobs(f)
    rid = f.start(f.create([
        job(f),
        {"id": "choose", "kind": "branch", "condition": {"left": {"$ref": "outcome.job"}, "right": "partial"},
         "then": "issues", "otherwise": "clean"},
        f.write("issues", next="end"), f.write("clean")]))
    f.tick()
    report(f, started[0], "succeeded", output={"job": started[0], "issues": 0})
    assert f.row(rid)["status"] == "succeeded"
    assert "issues" not in steps_of(f, rid) and steps_of(f, rid)["clean"][0] == "succeeded"


def test_a_failed_operation_is_a_definite_failure_that_takes_on_error(flow):
    f = flow
    started = jobs(f)
    rid = f.start(f.create([job(f, on_error="cleanup"), f.write("after", next="end"), f.write("cleanup")]))
    f.tick()
    report(f, started[0], "failed", error=ServiceError(422, "fixture_job_failed", "测试任务失败"))
    assert f.row(rid)["status"] == "partial"
    done = steps_of(f, rid)
    assert done["job"] == ("failed", "flow_operation_failed")
    assert "after" not in done and done["cleanup"][0] == "succeeded"
    assert len(started) == 1  # The failed job is not sent again.


def test_a_cancelled_operation_without_on_error_fails_the_run_instead_of_leaving_it_unknown(flow):
    f = flow
    started = jobs(f)
    rid = f.start(f.create([job(f), f.write("after")]))
    f.tick()
    report(f, started[0], "cancelled")
    run = f.row(rid)
    assert run["status"] == "failed" and run["error_code"] == "flow_operation_cancelled"
    assert steps_of(f, rid)["job"] == ("failed", "flow_operation_cancelled") and len(started) == 1


def test_an_unknown_outcome_stays_unknown_and_needs_reconciliation(flow):
    f = flow
    started = jobs(f)
    rid = f.start(f.create([job(f), f.write("after")]))
    f.tick()
    report(f, started[0], "unknown")
    assert f.row(rid)["status"] == "unknown"
    with pytest.raises(ServiceError) as err:
        f.e.resume(rid, user_id=f.uid)
    assert err.value.code == "flow_reconciliation_required" and len(started) == 1


def test_a_synchronous_partial_result_completes_the_step_at_once(flow):
    f = flow
    jobs(f, mode="sync", status="partial")
    rid = f.start(f.create([job(f), f.write("after")]))
    f.tick()
    assert f.row(rid)["status"] == "partial"
    assert steps_of(f, rid)["job"][0] == "partial" and steps_of(f, rid)["after"][0] == "succeeded"


@pytest.mark.parametrize("ref", ["outcome.later", "outcome.job.status", "outcome.missing"])
def test_outcome_references_name_exactly_an_earlier_step(flow, ref):
    f = flow
    jobs(f)
    with pytest.raises(ServiceError):
        f.create([job(f), {"id": "choose", "kind": "branch", "condition": {"left": {"$ref": ref}, "right": "partial"},
                           "then": "later", "otherwise": "later"}, f.write("later")])
