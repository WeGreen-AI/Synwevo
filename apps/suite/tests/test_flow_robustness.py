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
