"""Approval requests of a paused workspace (A44 review, A44-ACC-3).

A workspace the platform froze, or deleted and may still recover within seven days, is *paused*:
the approvals sweep leaves its requests exactly as they are — no task revoked, no request stalled,
no wait failed, no reminder — and when the workspace is back the request goes on from there: votes
already cast still count and the waiting `request.wait` operation run gets its result. (A flow run
whose step waits on that operation is held by the flow engine meanwhile and goes on by itself, with
no resume: `test_flows_paused_workspace.py`.) A deletion past its recovery window is lost, as
before: every approver loses access. A person who loses their own access during a pause still
does, once the sweep can act again.

Fixture accounts and workspaces from `conftest.People`; the platform's acts go through the core
services the console calls. Regression for the review probes `test_probe_suite_approvals.py` and
`test_probe_suite_approvals_refute.py`."""
from synwevo_core import workspace_lifecycle
from synwevo_core.store import now

REASON = "Fixture: workspace lifecycle regression"


def call(c, name, payload=None, user="alice"):
    return c.caps.invoke("approvals." + name, payload or {}, user_id=c.people.uid(user), workspace_id=c.people.a)


def out(c, name, payload=None, **kw):
    return call(c, name, payload, **kw)["output"]


def _pending_vote(c):
    """Two approvers, both must approve; bob approves, oscar has not yet; alice's operation run
    waits for the outcome (`request.wait`, the operation a flow run's approval step dispatches)."""
    c.people.ident.add_member(c.people.a, c.people.uid("oscar"), "admin")
    definition = {"fields": [{"id": "f_amount", "label": "Amount", "type": "money", "currency": "USD", "required": True}],
                  "stages": [{"id": "s_both", "name": "Both review", "mode": "all",
                              "approvers": {"users": [c.people.uid("bob"), c.people.uid("oscar")]}}]}
    t = out(c, "template.create", {"title": "Fixture pause", "definition": definition})
    t = out(c, "template.publish", {"template_id": t["id"], "draft_rev": 1})
    r = out(c, "request.create", {"template_id": t["id"], "title": "Fixture claim", "values": {"f_amount": "12.50"},
                                  "basis": "Invoice fixture"})
    wait = call(c, "request.wait", {"request_id": r["id"], "revision": 1})
    assert wait["status"] == "waiting_approval"
    r = _vote(c, r, "bob")
    return r, wait["run_id"]


def _vote(c, r, user):
    task = next(t for t in r["tasks"] if t["assignee_id"] == c.people.uid(user) and t["status"] == "pending")
    return out(c, "request.decide", {"request_id": r["id"], "state_version": r["state_version"], "task_id": task["id"],
                                     "decision": "approve", "note": "Fixture review"}, user=user)


def _state(c, rid, run_id):
    store, p = c.people.store, c.people
    names = {p.uid("bob"): "bob", p.uid("oscar"): "oscar"}
    req = store.one("SELECT status FROM approval_requests WHERE id=?", (rid,))["status"]
    tasks = {names[t["assignee_id"]]: (t["status"], t["close_reason"])
             for t in store.all("SELECT * FROM approval_tasks WHERE request_id=?", (rid,))}
    run = store.one("SELECT status, error_code FROM op_runs WHERE id=?", (run_id,))
    return req, tasks, (run["status"], run["error_code"])


UNTOUCHED = ("in_review", {"bob": ("approved", None), "oscar": ("pending", None)}, ("waiting_approval", None))


def _goes_on(c, r, run_id):
    """Back: oscar's vote completes the request and the waiting operation run succeeds."""
    fresh = out(c, "request.get", {"request_id": r["id"]})
    assert _vote(c, fresh, "oscar")["status"] == "approved"
    assert c.people.store.one("SELECT status FROM op_runs WHERE id=?", (run_id,))["status"] == "succeeded"


def test_a_frozen_workspace_leaves_votes_and_waits_as_they_are(suite):
    r, run_id = _pending_vote(suite)
    p, sweep = suite.people, suite.api.state.approvals.sweep
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    p.ident.set_workspace_frozen(p.a, True)
    sweep(); sweep(now() + 30 * 24 * 3600)            # however long it lasts: no reminder, no timeout, no revocation
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    assert not p.store.one("SELECT COUNT(*) AS n FROM approval_alerts WHERE request_id=?", (r["id"],))["n"]
    p.ident.set_workspace_frozen(p.a, False)
    sweep()
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    _goes_on(suite, r, run_id)


def test_a_deleted_workspace_leaves_them_until_it_is_recovered(suite):
    r, run_id = _pending_vote(suite)
    p, sweep = suite.people, suite.api.state.approvals.sweep
    workspace_lifecycle.platform_transition(p.store, p.a, p.uid("out"), "delete", reason=REASON)
    sweep()
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    workspace_lifecycle.platform_transition(p.store, p.a, p.uid("out"), "recover", reason=REASON)
    assert p.ident.get_workspace(p.a)["status"] == "active"
    sweep()
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    _goes_on(suite, r, run_id)


def test_a_deletion_past_its_recovery_window_revokes_as_before(suite):
    r, run_id = _pending_vote(suite)
    p, sweep = suite.people, suite.api.state.approvals.sweep
    workspace_lifecycle.platform_transition(p.store, p.a, p.uid("out"), "delete", reason=REASON)
    sweep()
    assert _state(suite, r["id"], run_id) == UNTOUCHED
    # FIXTURE clock: the seven days are over (the row as it is once recover_until has passed)
    p.store.execute("UPDATE workspaces SET recover_until=? WHERE id=?", (now() - 1, p.a))
    sweep()
    assert _state(suite, r["id"], run_id) == (
        "stalled", {"bob": ("revoked", "access_revoked"), "oscar": ("revoked", "access_revoked")}, ("failed", "access_revoked"))


def test_a_person_who_loses_their_own_access_during_a_pause_is_revoked_once_it_ends(suite):
    r, run_id = _pending_vote(suite)
    p, sweep = suite.people, suite.api.state.approvals.sweep
    p.ident.set_workspace_frozen(p.a, True)
    p.ident.remove_member(p.a, p.uid("bob"))
    sweep()
    assert _state(suite, r["id"], run_id) == UNTOUCHED     # nothing changes while nobody can act
    p.ident.set_workspace_frozen(p.a, False)
    sweep()
    req, tasks, run = _state(suite, r["id"], run_id)
    assert tasks == {"bob": ("revoked", "access_revoked"), "oscar": ("pending", None)}
    assert req == "stalled" and run == ("waiting_approval", None)   # the requester's own wait still stands
