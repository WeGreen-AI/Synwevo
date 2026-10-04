"""WP7 isolated Store regression suite.

Forms is the real installed application. ``fixtureflow`` is explicitly a test-only approval /
business-operation application, not production Approvals or an external-service validation.
All business writes, decisions, outbox rows and faults below use the real SQLite Store.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from pydantic import Field

from synwevo_core.apps import Apps
from synwevo_core.capabilities import AppManifest, Capabilities, Operation, record_event
from synwevo_core.errors import ServiceError
from synwevo_core.flows import Flows, Trigger, next_occurrence
from synwevo_core.flows.models import Model
from synwevo_core.store import Store, new_id

TEXT = {"zh-Hans": "测试夹具", "zh-Hant": "測試夾具", "en": "Test fixture"}


class Clock:
    def __init__(self):
        self.at = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc).timestamp()

    def __call__(self):
        return self.at

    def advance(self, seconds=61):
        self.at += seconds


class Crash(BaseException):
    """Process death, deliberately not an application exception."""


class FixtureInput(Model):
    text: str = Field(default="fixture", max_length=200)


class FixtureRef(Model):
    id: str


class FixtureDecision(FixtureRef):
    state: str


def install_fixture(caps, workspaces):
    """Install explicit fixture operations. They never ship with application code."""
    store, registry = caps.store, caps.registry
    apps = Apps(store, caps.identity)
    if not registry.app("agent"):
        registry.register_app(AppManifest(id="agent", name="Agent fixture", path="/agent", icon="agent", summary=TEXT, scope="personal"))
        apps.register(app_id="agent", name="Agent fixture", icon="agent", launch_url=None)
        store.execute("UPDATE apps SET status='active' WHERE id='agent'")
    registry.register_app(AppManifest(id="fixtureflow", name="Approval test fixture", path="/fixtureflow", icon="doc", summary=TEXT))
    apps.register(app_id="fixtureflow", name="Approval test fixture", icon="doc", launch_url=None)
    for ws in workspaces:
        apps.enable_for_workspace(ws, "fixtureflow")
    store.execute("CREATE TABLE IF NOT EXISTS fixture_flow_records (id TEXT PRIMARY KEY,workspace_id TEXT,owner_id TEXT,text TEXT,state TEXT,visible INTEGER DEFAULT 1)")

    def create(c, i):
        rid = new_id("fixture")
        c.store.execute("INSERT INTO fixture_flow_records (id,workspace_id,owner_id,text,state) VALUES (?,?,?,?,'pending')",
                        (rid, c.workspace_id, c.user_id, i.text))
        c.touches("fixture_approval", rid)
        c.emit("fixtureflow.created", object_kind="fixture_approval", object_id=rid)
        return {"id": rid}

    def read(c, i):
        row = c.store.one("SELECT id,text,state FROM fixture_flow_records WHERE id=? AND workspace_id=? AND visible=1", (i.id, c.workspace_id))
        if not row:
            raise ServiceError(404, "fixture_not_found", "测试记录不可见")
        return row

    def decide(c, i):
        row = c.store.one("SELECT * FROM fixture_flow_records WHERE id=? AND workspace_id=?", (i.id, c.workspace_id))
        if not row:
            raise ServiceError(404, "fixture_not_found", "测试记录不存在")
        if c.user_id == row["owner_id"]:
            raise ServiceError(403, "fixture_independent_approver", "测试夹具要求独立审批人")
        if row["state"] != "pending":
            raise ServiceError(409, "fixture_already_decided", "测试记录已经决定")
        if i.state not in ("approved", "rejected"):
            raise ServiceError(422, "fixture_invalid_decision", "测试决定无效")
        c.store.execute("UPDATE fixture_flow_records SET state=? WHERE id=?", (i.state, i.id))
        c.emit("fixtureflow.decided", object_kind="fixture_approval", object_id=i.id, data={"state": i.state})
        return read(c, i)

    for name, model, fn, write in (("create", FixtureInput, create, True), ("get", FixtureRef, read, False), ("decide", FixtureDecision, decide, True)):
        registry.register(Operation(id="fixtureflow." + name, app="fixtureflow", title=TEXT, summary="Isolated test fixture",
                                    input=model, handler=fn, need="write" if write else "read", side_effect="write" if write else "none",
                                    agent="confirm" if write else "auto", unattended=name != "decide"))


@pytest.fixture
def flow(suite):
    install_fixture(suite.caps, (suite.people.a, suite.people.b))
    clock = Clock()
    engine = Flows(suite.people.store, suite.caps, clock=clock)
    return Harness(suite, engine, clock)


class Harness:
    def __init__(self, suite, engine, clock):
        self.suite, self.e, self.clock = suite, engine, clock
        self.p, self.store, self.caps = suite.people, suite.people.store, suite.caps
        self.uid, self.ws = self.p.uid("alice"), self.p.a

    def invoke(self, op, payload, *, who="alice", ws=None):
        return self.caps.invoke(op, payload, user_id=self.p.uid(who), workspace_id=ws or self.ws)["output"]

    def create(self, steps, **spec):
        return self.e.create(user_id=self.uid, name="Isolated flow", definition={"steps": steps, **spec})["flow_id"]

    def start(self, fid, *, key="first", version=1, parameters=None):
        return self.e.start(fid, user_id=self.uid, version=version, parameters=parameters or {}, idempotency_key=key)["id"]

    def row(self, rid):
        return self.store.one("SELECT * FROM flow_runs WHERE id=?", (rid,))

    def result(self, rid, step):
        row = self.store.one("SELECT value_json FROM flow_steps WHERE run_id=? AND step_id=?", (rid, step))
        return json.loads(row["value_json"])

    def tick(self, count=20):
        self.e.tick(limit=count)

    def write(self, id="write", **kw):
        return {"id": id, "kind": "call", "operation": "fixtureflow.create", "workspace_id": self.ws,
                "input": {"text": "real stored fixture"},
                "result_read": {"operation": "fixtureflow.get", "workspace_id": self.ws, "input": {"id": {"$ref": "receipt.id"}}}, **kw}

    def waiting(self, id="wait", source="write", **kw):
        return {"id": id, "kind": "wait", "read": {"operation": "fixtureflow.get", "workspace_id": self.ws,
                                                            "input": {"id": {"$ref": f"steps.{source}.id"}}},
                "state": "waiting_approval", "until": {"left": {"$ref": "record.state"}, "right": ["approved", "rejected"], "op": "in"},
                "event_types": ["fixtureflow.decided"], **kw}

    def policy(self, *, runs=5, **kw):
        return {"tools": ["fixtureflow.create", "fixtureflow.get"], "workspaces": [self.ws], "max_runs": runs, "until_revoked": True, **kw}


def test_forms_wait_approval_condition_restart_and_duplicate_event(flow):
    f = flow
    form = f.invoke("forms.form.create", {"title": "Actual form in isolated Store", "definition": {
        "fields": [{"id": "f_name0001", "type": "text", "label": "Name", "required": True}]}})
    f.invoke("forms.form.publish", {"form_id": form["id"], "draft_rev": 1})
    steps = [
        {"id": "request", "kind": "call", "operation": "forms.request.create", "workspace_id": f.ws,
         "input": {"form_id": form["id"], "version": 1},
         "result_read": {"operation": "forms.request.get", "workspace_id": f.ws, "input": {"request_id": {"$ref": "receipt.request_id"}}}},
        {"id": "collect", "kind": "wait", "read": {"operation": "forms.request.get", "workspace_id": f.ws,
                                                            "input": {"request_id": {"$ref": "steps.request.id"}}},
         "until": {"left": {"$ref": "record.status"}, "right": "submitted"}, "event_types": ["forms.submission.created"]},
        f.write("approval", input={"text": {"$ref": "params.label"}}), f.waiting(source="approval"),
        {"id": "choose", "kind": "branch", "condition": {"left": {"$ref": "steps.wait.state"}, "right": "approved"},
         "then": "business", "otherwise": "reject"},
        f.write("business", next="end"), {"id": "reject", "kind": "fail", "code": "approval_rejected"}]
    fid = f.create(steps, parameters={"label": {"type": "string"}})
    rid = f.start(fid, parameters={"label": "Approval based on a real submission"})
    f.tick()
    assert f.row(rid)["status"] == "waiting_input"
    request = f.result(rid, "request")
    token = request["url"].rsplit("/", 1)[1]
    f.suite.sign_in("bob")
    missing = f.suite.post(f"/api/suite/public/forms/r/{token}/submit", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                          json={"version": 1, "answers": {}, "idempotency_key": "missing-required-0001"})
    assert missing.status_code == 422
    f.tick()
    assert f.row(rid)["status"] == "waiting_input"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 0
    response = f.suite.post(f"/api/suite/public/forms/r/{token}/submit", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                            json={"version": 1, "answers": {"f_name0001": "Isolated test"}, "idempotency_key": "actual-submit-0001"})
    assert response.status_code == 200, response.text
    f.tick()
    assert f.row(rid)["status"] == "waiting_approval"
    aid = f.result(rid, "approval")["id"]
    # Reopen the actual Store through the application factory, with fresh installed services.
    from synwevo_suite.app import create_app
    api2 = create_app(str(f.store.root), start_jobs=False, released=frozenset({"forms"}))
    caps2 = api2.state.capabilities
    install_fixture(caps2, (f.p.a, f.p.b))
    resumed = Flows(caps2.store, caps2, clock=f.clock)
    caps2.invoke("fixtureflow.decide", {"id": aid, "state": "approved"}, user_id=f.p.uid("bob"), workspace_id=f.ws)
    resumed.tick()
    assert f.row(rid)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 2
    # Replay and an older contradictory event are hints, never an approval decision.
    with f.store.tx() as t:
        for state in ("approved", "pending", "approved"):
            record_event(t, type_="fixtureflow.decided", app="fixtureflow", workspace_id=f.ws,
                         object_kind="fixture_approval", object_id=aid, data={"state": state})
    resumed.tick()
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 2
    assert f.store.one("SELECT COUNT(*) AS n FROM form_submissions")["n"] == 1
    assert f.row(rid)["version"] == 1


@pytest.mark.parametrize("decision,expected", [("approved", "succeeded"), ("rejected", "failed")])
def test_branch_does_not_treat_rejection_as_success(flow, decision, expected):
    f = flow
    fid = f.create([f.write(), f.waiting(), {"id": "route", "kind": "branch",
        "condition": {"left": {"$ref": "steps.wait.state"}, "right": "approved"}, "then": "done", "otherwise": "rejected"},
        f.write("done", next="end"), {"id": "rejected", "kind": "fail", "code": "approval_rejected"}])
    rid = f.start(fid)
    f.tick()
    f.invoke("fixtureflow.decide", {"id": f.result(rid, "write")["id"], "state": decision}, who="bob")
    f.tick()
    assert f.row(rid)["status"] == expected
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == (2 if decision == "approved" else 1)


def test_pinned_versions_parameters_idempotency_and_explicit_workspace(flow):
    f = flow
    fid = f.create([f.write(input={"text": {"$ref": "params.text"}})], parameters={"text": {"type": "string"}})
    rid = f.start(fid, parameters={"text": "version one"})
    assert f.start(fid, parameters={"text": "version one"}) == rid
    with pytest.raises(ServiceError, match="流程") as exc:
        f.start(fid, parameters={"text": "changed"})
    assert exc.value.code == "idempotency_conflict"
    f.e.publish(fid, user_id=f.uid, expected_version=1, definition={"steps": [f.write(input={"text": "version two"})]})
    other = f.start(fid, version=2, key="second")
    f.tick()
    assert f.result(rid, "write")["text"] == "version one"
    assert f.result(other, "write")["text"] == "version two"
    assert {x["workspace_id"] for x in f.store.all("SELECT workspace_id FROM fixture_flow_records")} == {f.ws}
    for who in ("bob", "oscar", "out"):
        with pytest.raises(ServiceError) as exc:
            f.e.run(rid, user_id=f.p.uid(who))
        assert exc.value.code == "flow_run_not_found"
    with pytest.raises(ServiceError) as exc:
        f.create([f.write(workspace_id=f.p.b, result_read=None)])  # alice is viewer in B
    assert exc.value.code == "read_only_role"


@pytest.mark.parametrize("mutation", ["membership", "user", "app", "object"])
def test_permission_recheck_and_history_hides_revoked_source(flow, mutation):
    f = flow
    rid = f.start(f.create([f.write(), f.waiting(), f.write("after")]))
    f.tick()
    if mutation == "membership":
        f.store.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (f.ws, f.uid))
    elif mutation == "user":
        f.store.execute("UPDATE users SET status='suspended' WHERE id=?", (f.uid,))
    elif mutation == "app":
        f.store.execute("UPDATE apps SET status='disabled' WHERE id='fixtureflow'")
    else:
        f.store.execute("UPDATE fixture_flow_records SET visible=0")
    f.clock.advance()
    f.tick()
    assert f.row(rid)["status"] in ("blocked", "failed")
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    if mutation != "user":
        public = f.e.run(rid, user_id=f.uid)
        assert public["steps"][0]["output"] is None and public["steps"][0]["snapshot_hidden"]


def test_cancel_and_pause_fence_claim_and_do_not_replay_completed_steps(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.write("second")]))
    first = f.e.claim()
    f.e.pause(rid, user_id=f.uid)
    assert f.e.process(first) is False
    f.e.resume(rid, user_id=f.uid)
    f.tick(1)
    second = f.e.claim()
    f.e.cancel(rid, user_id=f.uid)
    assert f.e.process(second) is False
    f.clock.advance(1000)
    f.tick()
    assert f.row(rid)["status"] == "cancelled"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    with pytest.raises(ServiceError):
        f.e.resume(rid, user_id=f.uid)


def test_stale_lease_and_concurrent_workers_dispatch_once(flow):
    f = flow
    rid = f.start(f.create([f.write()]))
    stale = f.e.claim(worker_id="old")
    f.clock.advance()
    newer = f.e.claim(worker_id="new")
    assert newer["fence"] > stale["fence"]
    assert not f.e.process(stale)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(f.e.process, (newer, newer))) == [False, True]
    assert f.row(rid)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_transaction_fault_rolls_back_business_step_and_outbox(flow, monkeypatch):
    f = flow
    rid = f.start(f.create([f.write()]))
    claim = f.e.claim()
    history = f.e._history
    def crash(t, run, type_, status, **kw):
        history(t, run, type_, status, **kw)
        if type_ == "step_completed":
            raise Crash()
    monkeypatch.setattr(f.e, "_history", crash)
    with pytest.raises(Crash):
        f.e.process(claim)
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 0
    assert f.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='fixtureflow.created'")["n"] == 0
    assert f.store.one("SELECT COUNT(*) AS n FROM op_runs WHERE op='fixtureflow.create'")["n"] == 0
    f.clock.advance()
    Flows(f.store, f.caps, clock=f.clock).tick()
    assert f.row(rid)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    assert f.store.one("SELECT COUNT(*) AS n FROM app_events WHERE type='fixtureflow.created'")["n"] == 1


def fixture_operation(f, name, handler, *, transactional=True):
    f.caps.registry.register(Operation(id="fixtureflow." + name, app="fixtureflow", title=TEXT, summary="Fault injection fixture",
                                      input=FixtureInput, handler=handler, need="write", side_effect="write", agent="confirm", transactional=transactional))


def test_finite_retry_rolls_back_partial_write_then_resumes_safely(flow):
    f = flow
    calls = []
    def fault(c, i):
        calls.append(c.run_id)
        c.store.execute("INSERT INTO fixture_flow_records VALUES (?, ?, ?, 'fault', 'pending', 1)", (new_id("fixture"), c.workspace_id, c.user_id))
        if len(calls) < 2:
            raise ServiceError(503, "fixture_temporary", "故障注入")
        return {"ok": True}
    fixture_operation(f, "fault", fault)
    rid = f.start(f.create([f.write(operation="fixtureflow.fault", result_read=None, max_attempts=2, retry_codes=["fixture_temporary"])]))
    f.tick()
    assert f.row(rid)["status"] == "retrying"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 0
    f.tick()
    assert len(calls) == 1
    f.clock.advance()
    f.tick()
    assert f.row(rid)["status"] == "succeeded" and len(calls) == 2
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    assert f.store.one("SELECT attempt FROM flow_steps WHERE run_id=?", (rid,))["attempt"] == 2


@pytest.mark.parametrize("outcome", ["timeout", "crash", "return_then_crash"])
def test_unknown_external_never_blindly_retries(flow, monkeypatch, outcome):
    f = flow
    sent = []
    def external(c, i):
        sent.append(c.run_id)  # Explicit external-system fixture; not a real send.
        if outcome == "timeout":
            raise TimeoutError("fixture timeout after effect")
        if outcome == "crash":
            raise Crash()
        return {"ok": True}
    fixture_operation(f, "external", external, transactional=False)
    rid = f.start(f.create([f.write(operation="fixtureflow.external", result_read=None, max_attempts=5, retry_codes=["operation_failed"])]))
    original = f.e._complete_call
    if outcome == "return_then_crash":
        monkeypatch.setattr(f.e, "_complete_call", lambda *a, **kw: (_ for _ in ()).throw(Crash()))
    if outcome == "timeout":
        f.tick()
    else:
        with pytest.raises(Crash):
            f.tick()
    f.clock.advance()
    restarted = Flows(f.store, f.caps, clock=f.clock)
    restarted.tick()
    assert len(sent) == 1
    if outcome == "return_then_crash":
        assert f.row(rid)["status"] == "succeeded"
    else:
        assert f.row(rid)["status"] == "unknown"
        with pytest.raises(ServiceError) as exc:
            restarted.resume(rid, user_id=f.uid)
        assert exc.value.code == "flow_reconciliation_required"
        # Only the business owner resolving the existing op record makes resume safe.
        op = f.store.one("SELECT id FROM op_runs WHERE op='fixtureflow.external'")
        f.caps.update_run(op["id"], "succeeded", output={"ok": True})
        restarted.resume(rid, user_id=f.uid)
        restarted.tick()
        assert f.row(rid)["status"] == "succeeded" and len(sent) == 1


def test_wait_timeout_failure_exit_and_budget(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.waiting(timeout_s=10, on_error="timeout"),
                            {"id": "timeout", "kind": "fail", "code": "approval_timeout"}]))
    f.tick()
    f.clock.advance(11)
    f.tick()
    assert f.row(rid)["error_code"] == "approval_timeout"
    other = f.start(f.create([f.write(), f.write("over_budget")], max_calls=2))
    f.tick()
    assert f.row(other)["error_code"] == "flow_budget_exhausted"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 2


def test_event_trigger_cursor_dedup_recursion_and_revocation(flow):
    f = flow
    fid = f.create([f.write(input={"text": {"$ref": "params.source"}})], parameters={"source": {"type": "string"}})
    tid = f.e.create_trigger(fid, user_id=f.uid, version=1, trigger={"kind": "event", "event_type": "fixtureflow.created",
                            "workspace_id": f.ws, "input": {"source": {"$ref": "event.object_id"}}}, policy=f.policy(runs=3))["trigger_id"]
    f.invoke("fixtureflow.create", {"text": "seed"})
    f.tick()
    f.tick()
    f.tick()
    f.tick()
    assert f.store.one("SELECT runs_used FROM flow_triggers WHERE id=?", (tid,))["runs_used"] == 3
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs WHERE trigger_id=?", (tid,))["n"] == 3
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 4
    f.e.set_trigger_enabled(tid, user_id=f.uid, enabled=False)
    f.invoke("fixtureflow.create", {"text": "after revocation"})
    f.tick()
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs WHERE trigger_id=?", (tid,))["n"] == 3


def test_trigger_atomic_cursor_and_delivery_fault(flow, monkeypatch):
    f = flow
    fid = f.create([f.write()])
    tid = f.e.create_trigger(fid, user_id=f.uid, version=1, trigger={"kind": "event", "event_type": "fixtureflow.created", "workspace_id": f.ws}, policy=f.policy())["trigger_id"]
    before = f.e.trigger(tid, user_id=f.uid)["cursor"]
    f.invoke("fixtureflow.create", {})
    delivery = f.e._deliver
    def crash(*args, **kw):
        delivery(*args, **kw)
        raise Crash()
    monkeypatch.setattr(f.e, "_deliver", crash)
    with pytest.raises(Crash):
        f.e.trigger_tick()
    assert f.e.trigger(tid, user_id=f.uid)["cursor"] == before
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == 0
    restarted = Flows(f.store, f.caps, clock=f.clock)
    restarted.trigger_tick()
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == 1
    restarted.trigger_tick()
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == 1


@pytest.mark.parametrize("misfire,expected", [("skip", 0), ("once", 1)])
def test_timezone_schedule_misfire_restart_and_two_workers(flow, misfire, expected):
    f = flow
    tid = f.e.create_trigger(f.create([f.write()]), user_id=f.uid, version=1,
                            trigger={"kind": "schedule", "timezone": "Asia/Hong_Kong", "at": "09:00", "misfire": misfire}, policy=f.policy())["trigger_id"]
    due = f.e.trigger(tid, user_id=f.uid)["next_at"]
    assert datetime.fromtimestamp(due, timezone.utc).hour == 1
    f.clock.at = due + 3 * 86400 + 600
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: Flows(f.store, f.caps, clock=f.clock).trigger_tick(), range(2)))
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == expected
    assert f.e.trigger(tid, user_id=f.uid)["next_at"] > f.clock()
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_trigger_deliveries")["n"] == 1


def test_dst_gap_and_fold_are_explicit():
    def stamp(s):
        return datetime.fromisoformat(s).timestamp()
    gap = Trigger(kind="schedule", timezone="America/New_York", at="02:30")
    assert next_occurrence(gap, stamp("2026-03-08T00:00:00-05:00")) == stamp("2026-03-09T02:30:00-04:00")
    fold = Trigger(kind="schedule", timezone="America/New_York", at="01:30")
    first = next_occurrence(fold, stamp("2026-11-01T00:00:00-04:00"))
    assert first == stamp("2026-11-01T01:30:00-04:00")
    assert next_occurrence(fold, first) == stamp("2026-11-02T01:30:00-05:00")


def test_policy_revocation_stops_existing_wait_and_expiry_rechecks(flow):
    f = flow
    tid = f.e.create_trigger(f.create([f.write(), f.waiting(), f.write("last")]), user_id=f.uid, version=1,
                            trigger={"kind": "schedule", "timezone": "UTC", "at": "00:01", "misfire": "once"}, policy=f.policy())["trigger_id"]
    f.clock.advance()
    f.tick()
    run = f.store.one("SELECT * FROM flow_runs WHERE trigger_id=?", (tid,))
    assert run["status"] == "waiting_approval"
    f.e.set_trigger_enabled(tid, user_id=f.uid, enabled=False)
    f.invoke("fixtureflow.decide", {"id": f.result(run["id"], "write")["id"], "state": "approved"}, who="bob")
    f.tick()
    assert f.row(run["id"])["status"] == "blocked"
    with pytest.raises(ServiceError) as exc:
        f.e.resume(run["id"], user_id=f.uid)
    assert exc.value.code == "flow_policy_revoked"


@pytest.mark.parametrize("bad", [
    {"steps": [{"id": "x", "kind": "shell", "command": "id"}]},
    {"steps": [{"id": "x", "kind": "fail", "code": "bad", "next": "x"}]},
    {"steps": [{"id": "x", "kind": "fail", "code": "bad", "on_error": "missing"}]},
])
def test_rejects_executable_language_cycles_and_missing_targets(flow, bad):
    with pytest.raises(ServiceError) as exc:
        flow.e.create(user_id=flow.uid, name="bad", definition=bad)
    assert exc.value.code == "flow_definition_invalid"


def test_operations_install_is_personal_without_enabling_apps(flow):
    f = flow
    from synwevo_suite.flows import install
    engine = install(f.suite.api.state.suite)
    assert install(f.suite.api.state.suite) is engine
    made = f.caps.invoke("agent.flow.create", {"name": "Via user operation", "definition": {"steps": [f.write()]}}, user_id=f.uid)
    assert made["output"]["flow_id"].startswith("flow_")
    with pytest.raises(ServiceError) as exc:
        f.caps.invoke("agent.flow.list", {}, user_id=f.uid, workspace_id=f.ws)
    assert exc.value.code == "workspace_not_applicable"
    with pytest.raises(ServiceError) as exc:
        f.caps.invoke("agent.flow.trigger.create", {}, user_id=f.uid, via="agent")
    assert exc.value.code == "not_for_agent"


def test_schema_additive_repeated_store_open(flow):
    f = flow
    fid = f.create([f.write()])
    rid = f.start(fid)
    f.tick()
    first = f.store.one("SELECT spec_json FROM flow_versions WHERE flow_id=?", (fid,))
    reopened = Store(f.store.root)
    reopened_again = Store(f.store.root)
    assert reopened.one("SELECT spec_json FROM flow_versions WHERE flow_id=?", (fid,)) == first
    assert reopened_again.one("SELECT status FROM flow_runs WHERE id=?", (rid,))["status"] == "succeeded"


def test_async_receipt_waits_and_never_reinvokes(flow):
    f = flow
    calls = []
    def enqueue(c, i):
        calls.append(c.run_id)
        return {"__run_status__": "queued", "job": "explicit-async-fixture"}
    fixture_operation(f, "async", enqueue)
    rid = f.start(f.create([f.write(operation="fixtureflow.async", result_read=None), f.write("next")]))
    f.tick()
    assert f.row(rid)["status"] == "waiting_input" and len(calls) == 1
    f.e.pause(rid, user_id=f.uid)
    f.e.resume(rid, user_id=f.uid)
    f.clock.advance()
    Flows(f.store, f.caps, clock=f.clock).tick()
    assert len(calls) == 1 and f.row(rid)["status"] == "waiting_input"
    f.caps.update_run(calls[0], "succeeded", output={"job": "finished-fixture"})
    f.clock.advance()
    f.tick()
    assert len(calls) == 1 and f.row(rid)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_cancel_during_external_call_preserves_receipt_without_next_step(flow):
    f = flow
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(c, i):
        calls.append(c.run_id)
        entered.set()
        assert release.wait(5), "test fixture was not released"
        return {"sent": True}
    fixture_operation(f, "slow", slow, transactional=False)
    rid = f.start(f.create([f.write(operation="fixtureflow.slow", result_read=None), f.write("next")]))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(f.e.tick)
        assert entered.wait(5)
        f.e.cancel(rid, user_id=f.uid)
        release.set()
        future.result(timeout=5)
    f.clock.advance()
    f.tick()
    public = f.e.run(rid, user_id=f.uid)
    assert public["status"] == "cancelled" and len(calls) == 1
    assert public["steps"][0]["operation_status"] == "succeeded"
    assert public["steps"][0]["op_run_id"] == calls[0]
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 0


def test_separate_store_workers_use_database_fence(flow):
    f = flow
    from synwevo_core.identity import Identity
    store2 = Store(f.store.root)
    caps2 = Capabilities(store2, Identity(store2), f.caps.registry)
    e2 = Flows(store2, caps2, clock=f.clock)
    rid = f.start(f.create([f.write()]))
    stale = f.e.claim()
    f.clock.advance()
    current = e2.claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(f.e.process, stale), pool.submit(e2.process, current)]
        assert [x.result(timeout=5) for x in results] == [False, True]
    assert f.row(rid)["status"] == "succeeded"
    assert store2.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_outbox_listener_is_after_commit_and_wait_missed_event_still_polls(flow, monkeypatch):
    f = flow
    notified = []
    def listener(events):
        for event in events:
            if event["type"].startswith("agent.flow."):
                assert not f.store._local.depth
                assert f.store.one("SELECT 1 FROM app_events WHERE id=?", (event["id"],))
                notified.append(event["type"])
    f.caps.listeners.append(listener)
    rid = f.start(f.create([f.write(), f.waiting(), f.write("next")]))
    f.tick()
    f.invoke("fixtureflow.decide", {"id": f.result(rid, "write")["id"], "state": "approved"}, who="bob")
    # Simulate lost wake-up/cursor already advanced. The persistent poll remains sufficient.
    monkeypatch.setattr(f.e, "_wake_waits", lambda: None)
    f.clock.advance()
    f.tick()
    assert f.row(rid)["status"] == "succeeded" and "agent.flow.step_completed" in notified


def test_other_peoples_events_cannot_spend_a_waiting_run_faster_than_half_its_polling(flow):
    """Review R3-FE-04: every committed event of a listed type in the read workspace pulled a waiting
    run's check to now, and each check costs the run calls — so other members' unrelated decisions
    (or outsiders' public submissions) spent its budget with no time passing. An event still brings
    the check forward, but never closer than half the wait's poll_s to the previous check."""
    f = flow
    rid = f.start(f.create([f.write(), f.waiting(poll_s=60), f.write("next")]))
    f.tick()
    assert f.row(rid)["status"] == "waiting_approval"
    used = f.row(rid)["calls_used"]

    def unrelated(n):
        with f.store.tx() as t:
            for i in range(n):
                record_event(t, type_="fixtureflow.decided", app="fixtureflow", workspace_id=f.ws,
                             object_kind="fixture_approval", object_id=f"fixture-unrelated-{i}", data={"state": "approved"})
        f.tick()
    for _ in range(20):                       # twenty unrelated decisions, the clock standing still
        unrelated(1)
    assert f.row(rid)["calls_used"] == used, "no check sooner than half the polling interval"
    f.clock.advance(31)                       # past half of poll_s: the next event is answered at once
    unrelated(1)
    assert f.row(rid)["calls_used"] > used and f.row(rid)["status"] == "waiting_approval"
    unrelated(1)                              # right after that check: held back again
    assert f.row(rid)["calls_used"] == (used := f.row(rid)["calls_used"])
    # the decision on the record the wait reads is answered at once, however recent the last check
    f.invoke("fixtureflow.decide", {"id": f.result(rid, "write")["id"], "state": "approved"}, who="bob")
    f.tick()
    assert f.row(rid)["status"] == "succeeded"


def test_wrong_workspace_or_forged_event_cannot_approve_wait(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.waiting(), f.write("next")]))
    f.tick()
    aid = f.result(rid, "write")["id"]
    with f.store.tx() as t:
        for ws in (f.p.b, f.ws):
            record_event(t, type_="fixtureflow.decided", app="fixtureflow", workspace_id=ws,
                         object_kind="fixture_approval", object_id=aid, data={"state": "approved"})
    f.tick()
    assert f.row(rid)["status"] == "waiting_approval"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_person_required_and_indirect_targets_are_not_automated(flow):
    f = flow
    with pytest.raises(ServiceError) as exc:
        f.create([f.write(operation="fixtureflow.decide", result_read=None)])
    assert exc.value.code == "person_required"
    with pytest.raises(ServiceError) as exc:
        f.create([f.write(operation="forms.form.duplicate", result_read=None)])
    assert exc.value.code == "flow_indirect_target_unsupported"


def test_expiry_and_user_suspension_stop_trigger_without_business_writes(flow):
    f = flow
    tid = f.e.create_trigger(f.create([f.write()]), user_id=f.uid, version=1,
                            trigger={"kind": "schedule", "timezone": "UTC", "at": "00:01", "misfire": "once"},
                            policy=f.policy(until_revoked=False, expires_at=f.clock() + 30))["trigger_id"]
    f.clock.advance()
    f.tick()
    receipt = f.e.deliveries(tid, user_id=f.uid)["deliveries"][0]
    assert receipt["status"] == "refused" and receipt["error_code"] == "flow_policy_expired"
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == 0
    f.clock.advance(86400)
    f.tick()
    assert len(f.e.deliveries(tid, user_id=f.uid)["deliveries"]) == 1


@pytest.mark.parametrize("override", [{"until_revoked": False}, {"expires_at": 9999999999}, {"tools": ["fixtureflow.get"]}, {"workspaces": []}])
def test_unbounded_or_out_of_scope_policy_is_refused(flow, override):
    f = flow
    with pytest.raises(ServiceError):
        f.e.create_trigger(f.create([f.write()]), user_id=f.uid, version=1,
                           trigger={"kind": "schedule", "timezone": "UTC", "at": "12:00"}, policy=f.policy(**override))


def test_event_depth_limit_is_a_persistent_refusal(flow):
    f = flow
    tid = f.e.create_trigger(f.create([f.write()]), user_id=f.uid, version=1,
                            trigger={"kind": "event", "event_type": "fixtureflow.created", "workspace_id": f.ws}, policy=f.policy())["trigger_id"]
    with f.store.tx() as t:
        record_event(t, type_="fixtureflow.created", app="fixtureflow", workspace_id=f.ws,
                     object_kind="fixture_approval", object_id="fixture-depth", depth=8)
    f.tick()
    assert f.e.deliveries(tid, user_id=f.uid)["deliveries"][0]["error_code"] == "run_depth_exceeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM flow_runs")["n"] == 0


def test_independent_workspace_targets_work_but_content_transfer_needs_policy(flow):
    from synwevo_core import data_flow
    f = flow
    f.store.execute("UPDATE memberships SET role='editor' WHERE workspace_id=? AND user_id=?", (f.p.b, f.uid))
    second = f.write("second", workspace_id=f.p.b, result_read=None)
    # Two writes and no read in between: the run holds nothing of A when it writes into B.
    rid = f.start(f.create([f.write(result_read=None), second]))
    f.tick()
    assert f.row(rid)["status"] == "succeeded"
    assert {r["workspace_id"] for r in f.store.all("SELECT workspace_id FROM fixture_flow_records")} == {f.ws, f.p.b}
    # Once the run has read A (the first step's result read), the same literal write into B carries
    # whatever was read. B has a member A does not (Oscar): A's default policy keeps its content from him.
    literal = f.start(f.create([f.write(), second]), key="literal")
    f.tick()
    assert (f.row(literal)["status"], f.row(literal)["error_code"]) == ("blocked", "transfer_audience_denied")
    second["input"] = {"text": {"$ref": "steps.write.text"}}
    flow_id = f.create([f.write(), second])
    blocked = f.start(flow_id, key="blocked")
    f.tick()
    assert f.row(blocked)["error_code"] == "transfer_audience_denied"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records WHERE workspace_id=?", (f.p.b,))["n"] == 1
    data_flow.set_workspace_outbound(f.store, f.ws, "any", actor_id=f.uid, revision=data_flow.workspace_outbound(f.store, f.ws)["revision"])
    carried = f.start(flow_id, key="carried")
    f.tick()
    assert f.row(carried)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records WHERE workspace_id=?", (f.p.b,))["n"] == 2
    f.e.resume(literal, user_id=f.uid)  # the literal write is asked again, with the policy as it is now
    f.tick()
    assert f.row(literal)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records WHERE workspace_id=?", (f.p.b,))["n"] == 3
    data_flow.set_workspace_outbound(f.store, f.ws, "none", actor_id=f.uid, revision=data_flow.workspace_outbound(f.store, f.ws)["revision"])  # rechecked on every run
    closed = f.start(flow_id, key="closed")
    f.tick()
    assert f.row(closed)["error_code"] == "transfer_source_closed"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records WHERE workspace_id=?", (f.p.b,))["n"] == 3


def test_source_changed_after_wait_stops_cached_branch(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.waiting(), {"id": "route", "kind": "branch",
        "condition": {"left": {"$ref": "steps.wait.state"}, "right": "approved"}, "then": "end", "otherwise": "end"}]))
    f.tick()
    aid = f.result(rid, "write")["id"]
    f.invoke("fixtureflow.decide", {"id": aid, "state": "approved"}, who="bob")
    f.tick(1)  # Store the approved wait result, stop before the branch.
    f.store.execute("UPDATE fixture_flow_records SET state='rejected' WHERE id=?", (aid,))
    f.tick()
    assert f.row(rid)["status"] == "blocked" and f.row(rid)["error_code"] == "flow_source_changed"


def test_retry_limit_is_not_reset_by_resume_and_run_pages_are_complete(flow):
    f = flow
    calls = []
    def fail(c, i):
        calls.append(c.run_id)
        raise ServiceError(503, "fixture_temporary", "故障注入")
    fixture_operation(f, "always_fail", fail)
    fid = f.create([f.write(operation="fixtureflow.always_fail", result_read=None, max_attempts=2, retry_codes=["fixture_temporary"])])
    rid = f.start(fid)
    f.tick()
    f.clock.advance()
    f.tick()
    assert f.row(rid)["status"] == "failed" and len(calls) == 2
    with pytest.raises(ServiceError) as exc:
        f.e.resume(rid, user_id=f.uid)
    assert exc.value.code == "flow_attempts_exhausted"
    f.start(fid, key="another")
    page = f.e.runs(fid, user_id=f.uid, limit=1)
    second = f.e.runs(fid, user_id=f.uid, after=page["next"], limit=1)
    assert len(page["runs"]) == len(second["runs"]) == 1
    assert second["next"] is None


def test_operation_version_drift_blocks_execution(flow):
    f = flow
    rid = f.start(f.create([f.write()]))
    from dataclasses import replace
    f.caps.registry._ops["fixtureflow.create"] = replace(f.caps.registry.get("fixtureflow.create"), version=2)
    f.tick()
    assert f.row(rid)["error_code"] == "flow_operation_version_unavailable"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 0


def test_failed_reads_still_consume_run_budget_after_rollback(flow):
    f = flow
    # The second call resolves two source fields, using the two remaining reads, then
    # exhausts its budget before dispatch. Resuming must not refund those reads.
    second = f.write("second", input={"text": [{"$ref": "steps.write.text"}, {"$ref": "steps.write.id"}]})
    rid = f.start(f.create([f.write(), second], max_calls=4))
    f.tick()
    assert f.row(rid)["error_code"] == "flow_budget_exhausted"
    assert f.row(rid)["calls_used"] == 4
    f.e.resume(rid, user_id=f.uid)
    f.tick()
    assert f.row(rid)["calls_used"] == 4
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1


def test_crash_between_external_marker_and_dispatch_requires_reconciliation(flow, monkeypatch):
    f = flow
    calls = []
    fixture_operation(f, "not_started", lambda c, i: calls.append(c.run_id), transactional=False)
    rid = f.start(f.create([f.write(operation="fixtureflow.not_started", result_read=None)]))
    monkeypatch.setattr(f.e, "_external", lambda *a: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        f.tick()
    f.clock.advance()
    restarted = Flows(f.store, f.caps, clock=f.clock)
    restarted.tick()
    assert f.row(rid)["status"] == "unknown" and calls == []
    with pytest.raises(ServiceError) as exc:
        restarted.resume(rid, user_id=f.uid)
    assert exc.value.code == "flow_reconciliation_required"


def test_handled_failure_exit_reports_partial_without_replaying_prior_write(flow):
    f = flow
    def fail(c, i):
        raise ServiceError(422, "fixture_business_failure", "测试业务失败")
    fixture_operation(f, "refuse", fail)
    rid = f.start(f.create([f.write(), f.write("bad", operation="fixtureflow.refuse", result_read=None, on_error="cleanup"),
                            f.write("cleanup")]))
    f.tick()
    assert f.row(rid)["status"] == "partial"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 2
    assert f.store.one("SELECT attempt FROM flow_steps WHERE run_id=? AND step_id='bad'", (rid,))["attempt"] == 1


def test_manual_resume_preserves_successful_steps_and_retry_identity(flow):
    f = flow
    attempts = []
    def transient(c, i):
        attempts.append(c.run_id)
        if len(attempts) == 1:
            raise ServiceError(503, "fixture_temporary", "故障注入")
        return {"completed": True}
    fixture_operation(f, "manual", transient)
    rid = f.start(f.create([f.write(), f.write("recover", operation="fixtureflow.manual", result_read=None, max_attempts=2)]))
    f.tick()
    assert f.row(rid)["status"] == "failed"
    original = f.store.one("SELECT op_run_id FROM flow_steps WHERE run_id=? AND step_id='write'", (rid,))["op_run_id"]
    f.e.resume(rid, user_id=f.uid)
    f.tick()
    assert f.row(rid)["status"] == "succeeded" and len(attempts) == 2
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    assert f.store.one("SELECT op_run_id FROM flow_steps WHERE run_id=? AND step_id='write'", (rid,))["op_run_id"] == original


def test_role_is_rechecked_between_steps_and_restored_permission_allows_resume(flow):
    f = flow
    rid = f.start(f.create([f.write(), f.write("second")]))
    f.tick(1)
    f.store.execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?", (f.ws, f.uid))
    f.tick()
    assert f.row(rid)["status"] == "blocked" and f.row(rid)["error_code"] == "read_only_role"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 1
    f.store.execute("UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?", (f.ws, f.uid))
    f.e.resume(rid, user_id=f.uid)
    f.tick()
    assert f.row(rid)["status"] == "succeeded"
    assert f.store.one("SELECT COUNT(*) AS n FROM fixture_flow_records")["n"] == 2
