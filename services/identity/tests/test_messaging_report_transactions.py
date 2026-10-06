"""D13: real report decisions, current authority and durable outcomes share one commit.

The scheduling hook commits an independent Store transaction immediately before the review's
writer lock. Failure triggers act on the fixture database; no authorization or persistence is
replaced with a successful stub, and no external notifications are sent.
"""
from __future__ import annotations

import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from synwevo_core.messaging import Messaging
from synwevo_core.org_roles import OrgRoles
from synwevo_core.sessions import REAUTH_SECONDS, Sessions
from synwevo_core.store import Store, now

from .test_messaging_reports import PW, R, login, org  # noqa: F401 -- isolated account fixture


@pytest.fixture()
def report(org):
    c = org
    store, users, notifier = c.ident.store, c.users, c.api.state.notifier
    roles = OrgRoles(store)
    role = roles.create(c.oid, users["owner"]["id"], name="Fixture report reviewer",
                        permissions=["org.messaging.manage"])
    roles.assign(c.oid, users["owner"]["id"], users["ann"]["id"], [role["id"]])
    svc = Messaging(store, c.ident)
    group = svc.create_group(c.oid, users["owner"]["id"], name="Fixture reported conversation",
                             member_ids=[users["ben"]["id"], users["cid"]["id"]])
    # Ann moderates for the organization without belonging to this conversation.
    msg, _ = svc.send(group["id"], users["owner"]["id"], body="Fixture disputed message")
    svc.react(msg["id"], users["ben"]["id"], "👍")
    reports = [svc.report(msg["id"], users[key]["id"], "other") for key in ("ben", "cid")]
    for rep in reports:
        for key in ("owner", "ann"):
            uid = users[key]["id"]
            notifier.emit(uid, "conversation.reported", params={"who": "Fixture reporter", "conversation": group["name"]},
                          organization_id=c.oid, object_kind="message_report", object_id=rep["id"],
                          dedupe_key=f"report:{rep['id']}:{uid}", actionable=True,
                          target={"origin": "accounts", "path": f"/organizations/{c.oid}?report={rep['id']}"})
    headers = login(c, "ann")
    assert c.post("/api/auth/reauth", headers=headers, json={"password": PW, "scope": "admin"}).status_code == 200
    sid = store.one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (users["ann"]["id"],))["id"]
    return {"client": c, "store": store, "svc": svc, "message": msg, "reports": reports,
            "headers": headers, "sid": sid, "role": role, "notifier": notifier}


def review(r, decision="removed"):
    return r["client"].post(f"{R}/{r['reports'][0]['id']}/review", headers=r["headers"],
                             json={"decision": decision, "note": "Fixture review decision"})


def snapshot(r, store=None):
    store = store or r["store"]
    mid = r["message"]["id"]
    return {
        "message": store.one("SELECT body,deleted_at FROM messages WHERE id=?", (mid,)),
        "reactions": store.all("SELECT * FROM message_reactions WHERE message_id=?", (mid,)),
        "reports": store.all("SELECT id,status,reviewed_by,reviewed_at,decision_note FROM message_reports WHERE message_id=? ORDER BY id", (mid,)),
        "audit": store.all("SELECT * FROM audit_events WHERE action='messaging.report.review' ORDER BY seq"),
        "notifications": store.all("SELECT * FROM notifications WHERE object_kind='message_report' ORDER BY seq"),
        "deliveries": store.all("SELECT d.* FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                                "WHERE n.object_kind='message_report' ORDER BY d.notification_id,d.channel"),
        "push": store.all("SELECT q.* FROM push_queue q JOIN notifications n ON n.id=q.notification_id "
                          "WHERE n.object_kind='message_report' ORDER BY q.notification_id"),
    }


def record_events(r, monkeypatch):
    """Observe the actual route service's events from a separate database connection."""
    observer, events = Store(r["store"].root), []
    original = Messaging.review_report

    def watched(svc, *args, **kwargs):
        def publish(users, event):
            events.append({"users": users, "event": event, "committed": snapshot(r, observer)})
        svc.publish = publish
        return original(svc, *args, **kwargs)

    monkeypatch.setattr(Messaging, "review_report", watched)
    return events


@pytest.mark.parametrize("withdrawal,status,error", [
    ("permission", 403, "forbidden"),
    ("membership", 404, "no_organization"),
    ("frozen", 409, "organization_unavailable"),
    ("deleted", 409, "organization_unavailable"),
    ("account", 401, "unauthorized"),
    ("session", 401, "unauthorized"),
    ("idle_expiry", 401, "unauthorized"),
    ("absolute_expiry", 401, "unauthorized"),
    ("reauth", 403, "reauth_required"),
    ("reauth_scope", 403, "reauth_required"),
])
def test_review_rechecks_withdrawals_before_its_writer_lock(report, monkeypatch, withdrawal, status, error):
    r, c = report, report["client"]
    before = snapshot(r)
    independent, scheduled = Store(r["store"].root), []
    original = Store.tx

    @contextmanager
    def delayed(store):
        if (store is r["store"] and not scheduled
                and any(frame.function == "review_report" for frame in inspect.stack())):
            scheduled.append(True)
            # Fixture withdrawal commits independently, after middleware admission and before
            # this decision acquires its write lock. Keep the real Store transaction semantics.
            with original(independent):
                if withdrawal == "permission":
                    OrgRoles(independent).assign(c.oid, c.users["owner"]["id"], c.users["ann"]["id"], [])
                elif withdrawal == "membership":
                    independent.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?",
                                        (c.oid, c.users["ann"]["id"]))
                elif withdrawal in ("frozen", "deleted"):
                    independent.execute("UPDATE organizations SET status=? WHERE id=?", (withdrawal, c.oid))
                elif withdrawal == "account":
                    independent.execute("UPDATE users SET status='suspended' WHERE id=?", (c.users["ann"]["id"],))
                elif withdrawal == "session":
                    Sessions(independent).revoke(r["sid"], "fixture_withdrawal")
                elif withdrawal == "idle_expiry":
                    independent.execute("UPDATE sessions SET idle_expires_at=? WHERE id=?", (now()-1, r["sid"]))
                elif withdrawal == "absolute_expiry":
                    independent.execute("UPDATE sessions SET absolute_expires_at=? WHERE id=?", (now()-1, r["sid"]))
                elif withdrawal == "reauth":
                    independent.execute("UPDATE sessions SET reauth_at=? WHERE id=?", (now()-REAUTH_SECONDS-1, r["sid"]))
                else:
                    independent.execute("UPDATE sessions SET reauth_scope='password' WHERE id=?", (r["sid"],))
        with original(store) as connection:
            yield connection

    monkeypatch.setattr(Store, "tx", delayed)
    response = review(r)
    assert scheduled == [True]
    assert response.status_code == status and response.json()["error"] == error, response.text
    assert snapshot(r) == before, "a withdrawn reviewer must not change reports, content, audit or notification state"


@pytest.mark.parametrize("failure", ["audit", "todo", "notification", "delivery"])
def test_review_rolls_back_all_durable_effects_when_a_required_write_fails(report, monkeypatch, failure):
    r = report
    before = snapshot(r)
    events = record_events(r, monkeypatch)
    # Fail the second reporter's notification so earlier outcomes and to-dos must roll back too.
    second = r["reports"][1]["id"]
    triggers = {
        "audit": ("INSERT", "audit_events", "NEW.action='messaging.report.review'"),
        "todo": ("UPDATE", "notifications", f"NEW.object_id='{second}' AND NEW.action_state='done'"),
        "notification": ("INSERT", "notifications", f"NEW.object_id='{second}' AND NEW.kind='conversation.report_removed'"),
        "delivery": ("INSERT", "notification_deliveries", "EXISTS (SELECT 1 FROM notifications WHERE id=NEW.notification_id "
                     f"AND object_id='{second}' AND kind='conversation.report_removed')"),
    }
    op, table, predicate = triggers[failure]
    r["store"].execute(f"CREATE TRIGGER fixture_report_failure BEFORE {op} ON {table} WHEN {predicate} "
                       "BEGIN SELECT RAISE(ABORT, 'fixture required write failure'); END")
    try:
        response = review(r)
    finally:
        r["store"].execute("DROP TRIGGER fixture_report_failure")
    assert response.status_code == 500, response.text
    assert snapshot(r) == before, "failure must leave every report open and no partial success/notification"
    assert events == [], "rolled-back message removal must never reach an event subscriber"
    # A failed operation remains retryable, and does not strand already_reviewed or dedupe rows.
    assert review(r).status_code == 200
    assert len(events) == 1


@pytest.mark.parametrize("decision", ["removed", "dismissed"])
def test_review_commits_one_audit_all_outcomes_and_only_then_publishes(report, monkeypatch, decision):
    r = report
    events = record_events(r, monkeypatch)
    response = review(r, decision)
    assert response.status_code == 200, response.text
    assert response.json()["settled_with"] == [r["reports"][1]["id"]]
    after = snapshot(r)
    assert {x["status"] for x in after["reports"]} == {decision}
    assert len(after["audit"]) == 1 and after["audit"][0]["result"] == "ok"
    todos = [x for x in after["notifications"] if x["kind"] == "conversation.reported"]
    outcomes = [x for x in after["notifications"] if x["kind"] == f"conversation.report_{decision}"]
    assert len(todos) == 4 and {x["action_state"] for x in todos} == {"done"}
    assert {x["object_id"] for x in outcomes} == {x["id"] for x in r["reports"]}
    assert len(outcomes) == 2
    assert {x["id"] for x in outcomes} <= {x["notification_id"] for x in after["deliveries"]}
    if decision == "removed":
        assert len(events) == 1 and events[0]["event"]["message"]["deleted"] is True
        assert events[0]["committed"] == after, "subscribers must see audit and all notifications already committed"
        assert r["client"].users["ann"]["id"] not in events[0]["users"], "org moderation does not join the conversation"
    else:
        assert events == [] and after["message"] == {"body": r["message"]["body"], "deleted_at": None}
    assert review(r, decision).json()["error"] == "already_reviewed"
    assert snapshot(r) == after, "a duplicate decision creates no second audit or notice"


def test_review_respects_reporter_notification_preferences(report):
    r = report
    r["notifier"].set_preferences(r["client"].users["cid"]["id"], {"categories": {"conversation": {"in_app": False}}})
    assert review(r).status_code == 200
    after = snapshot(r)
    assert len([x for x in after["notifications"] if x["kind"] == "conversation.report_removed"]) == 1
    assert {x["action_state"] for x in after["notifications"] if x["kind"] == "conversation.reported"} == {"done"}


def test_concurrent_reviewers_of_different_reports_commit_only_one_outcome(report, monkeypatch):
    r = report
    barrier, local = threading.Barrier(2), threading.local()
    original = Store.tx

    @contextmanager
    def simultaneous(store):
        if (store is r["store"] and not getattr(local, "entered", False)
                and any(frame.function == "review_report" for frame in inspect.stack())):
            local.entered = True
            barrier.wait(timeout=5)
        with original(store) as connection:
            yield connection

    with TestClient(r["client"].api) as owner:
        headers = login(owner, "owner")
        assert owner.post("/api/auth/reauth", headers=headers, json={"password": PW, "scope": "admin"}).status_code == 200
        monkeypatch.setattr(Store, "tx", simultaneous)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(review, r, "removed")
            second = pool.submit(owner.post, f"{R}/{r['reports'][1]['id']}/review", headers=headers,
                                 json={"decision": "dismissed"})
            responses = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(x.status_code for x in responses) == [200, 409], [x.text for x in responses]
    winner = next(x.json() for x in responses if x.status_code == 200)
    assert next(x.json()["error"] for x in responses if x.status_code == 409) == "already_reviewed"
    state = snapshot(r)
    assert {x["status"] for x in state["reports"]} == {winner["status"]}
    assert len(state["audit"]) == 1
    outcomes = [x for x in state["notifications"] if x["kind"] != "conversation.reported"]
    assert len(outcomes) == 2 and {x["kind"] for x in outcomes} == {f"conversation.report_{winner['status']}"}
    assert {x["action_state"] for x in state["notifications"] if x["kind"] == "conversation.reported"} == {"done"}
    assert state["message"]["body"] == ("" if winner["status"] == "removed" else r["message"]["body"])


@pytest.mark.parametrize("outcome", ["commit", "rollback", "standalone"])
def test_service_review_events_follow_the_outermost_commit(report, outcome):
    r, events = report, []
    before = snapshot(r)
    observer = Store(r["store"].root)
    r["svc"].publish = lambda users, event: events.append((event, snapshot(r, observer)))

    def decide():
        return r["svc"].review_report(r["reports"][0]["id"], r["client"].users["ann"]["id"], "removed", "Fixture service caller")

    if outcome == "standalone":
        assert decide()["status"] == "removed"
    elif outcome == "commit":
        with r["store"].tx():
            assert decide()["status"] == "removed"
            assert events == []
            assert snapshot(r, observer) == before
    else:
        with pytest.raises(RuntimeError, match="fixture outer rollback"):
            with r["store"].tx():
                assert decide()["status"] == "removed"
                assert events == []
                raise RuntimeError("fixture outer rollback")
        assert snapshot(r) == before and events == []
        return
    assert len(events) == 1 and events[0][1] == snapshot(r)
    assert events[0][0]["message"]["deleted"] is True
