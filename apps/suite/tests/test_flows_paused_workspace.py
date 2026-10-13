"""Flow runs that need a paused workspace (A44 review, A44-PAUSE-3).

A workspace the platform froze, or deleted and may still recover within seven days, is *paused*
(`synwevo_core.workspace_availability`). A flow run whose step needs it — where the step runs or
reads, a completed step it takes a value from, or a source whose content it would carry elsewhere
— is *held*: it stays in the state it was waiting in, nothing is dispatched, no call or attempt is
spent, and it looks again later with a bounded backoff. When the workspace is available again the
run goes on by itself, with no resume. A deletion past its recovery window blocks the run with
`workspace_unavailable` (not the application gate's generic `app_access_denied`). Someone who lost
their own access meets the refusal they always met, and learns nothing about the pause; the run's
own deadline still ends it.

Regression for the review probe `suite/test_probe_approvals_pause.py::
test_flow_run_waiting_on_approval_after_pause`, where the run was parked as `blocked` with
`app_access_denied` and needed a manual resume. Fixture accounts and workspaces from
`conftest.People`; the platform's acts go through the core services the console calls; a FIXTURE
clock drives the engine."""
from __future__ import annotations

from synwevo_core import workspace_lifecycle
from synwevo_core.flows import Flows
from synwevo_core.flows.engine import HOLD_MAX_S, HOLD_MIN_S
from synwevo_core.store import now

REASON = "Fixture: workspace lifecycle regression"
HELD = ("waiting", "workspace_unavailable")


class Clock:
    """FIXTURE clock for the engine: starts at the real time (the lifecycle rows use it too)."""

    def __init__(self):
        self.at = now()

    def __call__(self):
        return self.at

    def advance(self, s=61):
        self.at += s


def call(c, name, payload=None, user="alice"):
    return c.caps.invoke("approvals." + name, payload or {}, user_id=c.people.uid(user), workspace_id=c.people.a)


def out(c, name, payload=None, **kw):
    return call(c, name, payload, **kw)["output"]


def _request(c):
    """An approval request in A that bob and oscar must both approve, by alice."""
    c.people.ident.add_member(c.people.a, c.people.uid("oscar"), "admin")
    definition = {"fields": [{"id": "f_amount", "label": "Amount", "type": "money", "currency": "USD", "required": True}],
                  "stages": [{"id": "s_both", "name": "Both review", "mode": "all",
                              "approvers": {"users": [c.people.uid("bob"), c.people.uid("oscar")]}}]}
    t = out(c, "template.create", {"title": "Fixture pause", "definition": definition})
    t = out(c, "template.publish", {"template_id": t["id"], "draft_rev": 1})
    return out(c, "request.create", {"template_id": t["id"], "title": "Fixture claim", "values": {"f_amount": "12.50"},
                                     "basis": "Invoice fixture"})


def _vote(c, r, user):
    task = next(t for t in r["tasks"] if t["assignee_id"] == c.people.uid(user) and t["status"] == "pending")
    return out(c, "request.decide", {"request_id": r["id"], "state_version": r["state_version"], "task_id": task["id"],
                                     "decision": "approve", "note": "Fixture review"}, user=user)


class Runs:
    def __init__(self, c):
        self.c, self.store, self.clock = c, c.people.store, Clock()
        self.e = Flows(self.store, c.caps, clock=self.clock)

    def start(self, steps, *, user="alice", key="fixture-1", **spec):
        uid = self.c.people.uid(user)
        fid = self.e.create(user_id=uid, name="Fixture flow", definition={"steps": steps, **spec})["flow_id"]
        return self.e.start(fid, user_id=uid, version=1, parameters={}, idempotency_key=key)["id"]

    def row(self, rid):
        return self.store.one("SELECT status, error_code, step_id, due_at, calls_used, deadline FROM flow_runs WHERE id=?", (rid,))

    def state(self, rid):
        r = self.row(rid)
        return r["status"], r["error_code"]

    def held_rows(self, rid):
        return self.store.all("SELECT status FROM flow_history WHERE run_id=? AND type=? AND error_code=?", (rid, *HELD))

    def step(self, rid, step_id):
        return self.store.one("SELECT status, attempt, checks, error_code, op_run_id FROM flow_steps WHERE run_id=? AND step_id=?",
                              (rid, step_id))

    def tick_at_due(self, rid):
        """Advance the FIXTURE clock to the run's next look and tick once."""
        self.clock.at = max(self.clock.at, self.row(rid)["due_at"])
        self.e.tick()


def _waiting_on_approval(c):
    """The probe's flow: one call step, `approvals.request.wait`, on an in-review request."""
    r = _request(c)
    runs = Runs(c)
    rid = runs.start([{"id": "wait", "kind": "call", "operation": "approvals.request.wait", "workspace_id": c.people.a,
                       "input": {"request_id": r["id"], "revision": 1}}])
    runs.e.tick()
    assert runs.state(rid) == ("waiting_approval", None)
    return r, runs, rid


def _waits_on_a(c, user="alice", **spec):
    """A wait step reading A whose condition never holds: the run polls A until its deadline."""
    runs = Runs(c)
    rid = runs.start([{"id": "watch", "kind": "wait", "poll_s": 60,
                       "read": {"operation": "approvals.template.list", "workspace_id": c.people.a, "input": {}},
                       "until": {"left": {"$ref": "record.fixture_never"}, "op": "exists"}}], user=user, **spec)
    runs.e.tick()
    assert runs.state(rid) == ("waiting_input", None)
    return runs, rid


def _approved(c, r, runs, rid):
    """Back: both votes complete the request, and the next ticks finish the run — no resume."""
    c.api.state.approvals.sweep()
    fresh = _vote(c, out(c, "request.get", {"request_id": r["id"]}), "bob")
    assert _vote(c, fresh, "oscar")["status"] == "approved"
    runs.tick_at_due(rid)
    assert runs.state(rid) == ("succeeded", None)
    op_run = runs.store.one("SELECT status FROM op_runs WHERE id=?", (runs.step(rid, "wait")["op_run_id"],))
    assert op_run["status"] == "succeeded"


def test_a_run_waiting_on_an_approval_is_held_through_a_freeze_and_goes_on_by_itself(suite):
    c = suite
    r, runs, rid = _waiting_on_approval(c)
    before = runs.step(rid, "wait")
    c.people.ident.set_workspace_frozen(c.people.a, True)
    runs.clock.advance(31)
    runs.e.tick()
    # held, not blocked: the state it waited in, no error, a later look, the step untouched
    assert runs.state(rid) == ("waiting_approval", None)
    assert runs.row(rid)["due_at"] == runs.clock() + HOLD_MIN_S
    assert runs.step(rid, "wait") == before
    assert [h["status"] for h in runs.held_rows(rid)] == ["waiting_approval"]
    for _ in range(3):                                       # still paused: no new history row, nothing spent
        runs.tick_at_due(rid)
    assert runs.state(rid) == ("waiting_approval", None) and len(runs.held_rows(rid)) == 1
    assert runs.step(rid, "wait") == before
    c.api.state.approvals.sweep()
    c.people.ident.set_workspace_frozen(c.people.a, False)
    _approved(c, r, runs, rid)


def test_a_run_is_held_while_its_workspace_is_deleted_and_goes_on_once_it_is_recovered(suite):
    c = suite
    r, runs, rid = _waiting_on_approval(c)
    workspace_lifecycle.platform_transition(c.people.store, c.people.a, c.people.uid("out"), "delete", reason=REASON)
    runs.clock.advance(31)
    runs.e.tick()
    assert runs.state(rid) == ("waiting_approval", None) and len(runs.held_rows(rid)) == 1
    workspace_lifecycle.platform_transition(c.people.store, c.people.a, c.people.uid("out"), "recover", reason=REASON)
    _approved(c, r, runs, rid)


def test_a_deletion_past_its_recovery_window_blocks_the_run_with_workspace_unavailable(suite):
    c = suite
    runs, rid = _waits_on_a(c)
    workspace_lifecycle.platform_transition(c.people.store, c.people.a, c.people.uid("out"), "delete", reason=REASON)
    runs.tick_at_due(rid)
    assert runs.state(rid) == ("waiting_input", None) and len(runs.held_rows(rid)) == 1
    # FIXTURE: the seven days are over (the row as it is once recover_until has passed)
    c.people.store.execute("UPDATE workspaces SET recover_until=? WHERE id=?", (now() - 1, c.people.a))
    runs.tick_at_due(rid)
    assert runs.state(rid) == ("blocked", "workspace_unavailable")
    last = runs.store.one("SELECT type, status, error_code FROM flow_history WHERE run_id=? ORDER BY seq DESC LIMIT 1", (rid,))
    assert dict(last) == {"type": "step_failed", "status": "blocked", "error_code": "workspace_unavailable"}


def test_the_hold_backs_off_within_bounds_and_spends_nothing(suite):
    c = suite
    runs, rid = _waits_on_a(c)
    spent, checks = runs.row(rid)["calls_used"], runs.step(rid, "watch")["checks"]
    c.people.ident.set_workspace_frozen(c.people.a, True)
    gaps = []
    for _ in range(8):
        runs.tick_at_due(rid)
        gaps.append(round(runs.row(rid)["due_at"] - runs.clock()))
    assert gaps == [30, 30, 60, 120, 240, 480, 600, 600]
    assert min(gaps) == HOLD_MIN_S and max(gaps) == HOLD_MAX_S
    assert runs.row(rid)["calls_used"] == spent and runs.step(rid, "watch")["checks"] == checks
    assert runs.state(rid) == ("waiting_input", None) and len(runs.held_rows(rid)) == 1
    c.people.ident.set_workspace_frozen(c.people.a, False)
    runs.tick_at_due(rid)
    assert runs.state(rid) == ("waiting_input", None)        # polling again: the read ran
    assert runs.step(rid, "watch")["checks"] == checks + 1 and runs.row(rid)["calls_used"] == spent + 1


def test_the_runs_own_deadline_still_ends_a_held_run(suite):
    c = suite
    runs, rid = _waits_on_a(c, timeout_s=300)
    c.people.ident.set_workspace_frozen(c.people.a, True)
    for _ in range(6):
        runs.tick_at_due(rid)
        assert runs.row(rid)["due_at"] <= runs.row(rid)["deadline"]
    assert runs.state(rid) == ("failed", "flow_deadline_exceeded")


def test_someone_who_lost_their_own_access_is_refused_as_before_and_learns_nothing_of_the_pause(suite):
    c = suite
    runs, rid = _waits_on_a(c, user="bob")
    c.people.ident.set_workspace_frozen(c.people.a, True)
    c.people.ident.remove_member(c.people.a, c.people.uid("bob"))
    runs.tick_at_due(rid)
    status, code = runs.state(rid)
    assert status == "blocked" and code != "workspace_unavailable"
    assert runs.held_rows(rid) == []


def test_a_write_whose_source_is_paused_is_held_and_goes_on_by_itself(suite):
    """The transfer question, which never names the workspace it refuses: oscar's run read A and
    would write in B. While A is paused the write waits, without dispatching; then it runs."""
    c = suite
    p = c.people
    p.ident.add_member(p.a, p.uid("oscar"), "admin")
    form = {"title": "Fixture paused source", "definition": {
        "fields": [{"id": "f_name0001", "type": "text", "label": "Name", "required": True}]}}
    runs = Runs(c)
    rid = runs.start([
        {"id": "look", "kind": "call", "operation": "approvals.template.list", "workspace_id": p.a, "input": {}},
        {"id": "make", "kind": "call", "operation": "forms.form.create", "workspace_id": p.b, "input": form}], user="oscar")
    forms = lambda: p.store.one("SELECT COUNT(*) AS n FROM forms WHERE workspace_id=?", (p.b,))["n"]  # noqa: E731
    made = forms()
    runs.e.process(runs.e.claim())                           # the read in A
    assert runs.state(rid) == ("queued", None) and runs.row(rid)["step_id"] == "make"
    p.ident.set_workspace_frozen(p.a, True)
    runs.e.process(runs.e.claim())                           # the write in B carries A's content
    assert runs.state(rid) == ("queued", None) and len(runs.held_rows(rid)) == 1
    assert runs.step(rid, "make")["attempt"] == 0 and forms() == made
    p.ident.set_workspace_frozen(p.a, False)
    runs.tick_at_due(rid)
    assert runs.state(rid) == ("succeeded", None) and forms() == made + 1
