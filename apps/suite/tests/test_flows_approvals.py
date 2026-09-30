"""Production-operation integration gate for the coordinator's assembled suite.

No approval/tool stand-ins are installed here. The WP7-only baseline explicitly skips this
test because production Agent and Approvals are outside its write set. After composition the
same test must run, using the applications' real operations, real human decision and Store.
"""
import json

import pytest

from synwevo_core.flows import Flows


@pytest.mark.parametrize("decision,expected", [("approve", "succeeded"), ("reject", "failed")])
def test_real_forms_approvals_restart_and_business_continuation(suite, decision, expected):
    caps, people = suite.caps, suite.people
    required = ("forms.request.create", "forms.submission.get", "approvals.template.create",
                "approvals.request.create", "approvals.request.get", "approvals.request.decide")
    if not caps.registry.app("agent") or any(not caps.registry.get(op) for op in required):
        pytest.skip("Production Agent/Approvals are not composed in this WP7 baseline; coordinator must run this gate after integration")
    store, uid, ws = people.store, people.uid("alice"), people.a

    def op(name, payload, *, who="alice", capabilities=None):
        return (capabilities or caps).invoke(name, payload, user_id=people.uid(who), workspace_id=ws)["output"]

    form = op("forms.form.create", {"title": "WP7 isolated real collection", "definition": {
        "fields": [{"id": "f_name0001", "type": "text", "label": "Name", "required": True}]}})
    op("forms.form.publish", {"form_id": form["id"], "draft_rev": 1})
    approval = op("approvals.template.create", {"title": "WP7 isolated real approval", "definition": {
        "fields": [{"id": "f_name0001", "type": "text", "label": "Name", "required": True}],
        "stages": [{"id": "s_first", "name": "Independent review", "mode": "all", "approvers": {"users": [people.uid("bob")]}}]}})
    op("approvals.template.publish", {"template_id": approval["id"], "draft_rev": 1})
    definition = {"steps": [
        {"id": "request", "kind": "call", "operation": "forms.request.create", "workspace_id": ws,
         "input": {"form_id": form["id"], "version": 1},
         "result_read": {"operation": "forms.request.get", "workspace_id": ws, "input": {"request_id": {"$ref": "receipt.request_id"}}}},
        {"id": "collect", "kind": "wait", "read": {"operation": "forms.request.get", "workspace_id": ws,
            "input": {"request_id": {"$ref": "steps.request.id"}}},
         "until": {"left": {"$ref": "record.status"}, "right": "submitted"}, "event_types": ["forms.submission.created"]},
        {"id": "submission", "kind": "call", "operation": "forms.submission.get", "workspace_id": ws,
         "input": {"submission_id": {"$ref": "steps.collect.submissions.0.id"}}},
        {"id": "approval", "kind": "call", "operation": "approvals.request.create", "workspace_id": ws,
         "input": {"template_id": approval["id"], "version": 1, "title": "Review actual submission",
                   "values": {"f_name0001": {"$ref": "steps.submission.answers.f_name0001"}},
                   "basis": {"$ref": "steps.submission.answers.f_name0001"}},
         "result_read": {"operation": "approvals.request.get", "workspace_id": ws, "input": {"request_id": {"$ref": "receipt.id"}}}},
        {"id": "review", "kind": "wait", "state": "waiting_approval",
         "read": {"operation": "approvals.request.get", "workspace_id": ws, "input": {"request_id": {"$ref": "steps.approval.id"}}},
         "until": {"left": {"$ref": "record.status"}, "op": "in", "right": ["approved", "rejected"]},
         "reject": {"left": {"$ref": "record.status"}, "op": "in", "right": ["returned", "withdrawn"]},
         "event_types": ["approvals.request.approved", "approvals.request.rejected"]},
        {"id": "route", "kind": "branch", "condition": {"left": {"$ref": "steps.review.status"}, "right": "approved"},
         "then": "close", "otherwise": "rejected"},
        {"id": "close", "kind": "call", "operation": "forms.request.close", "workspace_id": ws,
         "input": {"request_id": {"$ref": "steps.request.id"}, "mode": "close", "reason": "Actual independent approval"}, "next": "end"},
        {"id": "rejected", "kind": "fail", "code": "approval_rejected"}
    ]}
    engine = Flows(store, caps)
    fid = engine.create(user_id=uid, name="Forms → Approvals → business operation", definition=definition)["flow_id"]
    rid = engine.start(fid, user_id=uid, version=1, parameters={}, idempotency_key="real-chain")["id"]
    engine.tick()
    assert engine.run(rid, user_id=uid)["status"] == "waiting_input"
    request = json.loads(store.one("SELECT value_json FROM flow_steps WHERE run_id=? AND step_id='request'", (rid,))["value_json"])
    token = request["url"].rsplit("/", 1)[1]
    suite.sign_in("bob")
    submitted = suite.post(f"/api/suite/public/forms/r/{token}/submit", headers={"X-Synwevo-CSRF": "fixture-csrf"},
                           json={"version": 1, "answers": {"f_name0001": "Real isolated submission"}, "idempotency_key": "actual-wp7-form-submit"})
    assert submitted.status_code == 200, submitted.text
    engine.tick()
    assert engine.run(rid, user_id=uid)["status"] == "waiting_approval"
    request2 = json.loads(store.one("SELECT value_json FROM flow_steps WHERE run_id=? AND step_id='approval'", (rid,))["value_json"])
    assert request2["snapshot"]["fields"]["f_name0001"] == "Real isolated submission"

    # Reopen the database through the real application factory, no borrowed module paths.
    from synwevo_suite.app import create_app
    rebuilt = create_app(str(store.root), start_jobs=False)
    caps2 = rebuilt.state.capabilities
    resumed = Flows(caps2.store, caps2)
    current = op("approvals.request.get", {"request_id": request2["id"]}, who="bob", capabilities=caps2)
    task = next(t for t in current["tasks"] if t["assignee_id"] == people.uid("bob") and t["status"] == "pending")
    op("approvals.request.decide", {"request_id": current["id"], "task_id": task["id"],
       "state_version": current["state_version"], "decision": decision, "note": "Isolated real human decision"}, who="bob", capabilities=caps2)
    resumed.tick()
    assert resumed.run(rid, user_id=uid)["status"] == expected
    expected_calls = 1 if decision == "approve" else 0
    assert store.one("SELECT COUNT(*) AS n FROM op_runs WHERE parent_run_id=? AND op='forms.request.close'", (rid,))["n"] == expected_calls
    assert op("forms.request.get", {"request_id": request["id"]}, capabilities=caps2)["status"] == ("closed" if expected_calls else "submitted")
    # Fault injection replays the durable event page; the completed flow never dispatches twice.
    store.execute("UPDATE app_event_cursors SET seq=0 WHERE consumer='flows.wait.v1'")
    resumed.tick()
    assert store.one("SELECT COUNT(*) AS n FROM op_runs WHERE parent_run_id=? AND op='forms.request.close'", (rid,))["n"] == expected_calls
