"""D13 producer races: real Message reports compete with real Account reviews.

Composed factory, synthetic people and isolated storage. Event barriers only schedule actual
requests/Store transactions; SQLite triggers inject storage failures. No worker sends mail or
push, no authorization/decision result is stubbed, and no production configuration is loaded.
"""
from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.messaging import Messaging
from synwevo_core.organizations import Organizations
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER, Sessions
from synwevo_core.store import Store, now

PW = "isolated-message-report-creation-fixture-51"
M = "/api/messaging"
R = "/api/auth/messaging/reports"


def post(client, path, body):
    return client.post(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"DATA_DIR": str(tmp_path / "data"), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"), "ROLE": "all",
                       "APP_URL": "http://testserver", "PUBLIC_URL": "http://testserver",
                       "ISSUER": "http://testserver", "SCANNER_MODE": "off"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "data"), role="all", start_jobs=False)
    ident, clients, users = api.state.identity, {}, {}
    with TestClient(api, raise_server_exceptions=False):
        for key in ("owner", "moderator", "ann", "ben", "cid"):
            users[key] = ident.create_user(f"{key}@example.test", PW, status="active")
            ident.store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), users[key]["id"]))
            clients[key] = TestClient(api, raise_server_exceptions=False)
            assert post(clients[key], "/api/auth/login", {"email": users[key]["email"], "password": PW}).status_code == 200
        org = Organizations(ident.store).create(users["owner"]["id"], "Fixture report org", "Fixture space", "d13-create-races")
        oid = org["organization"]["id"]
        for key in ("moderator", "ann", "ben", "cid"):
            ident.store.insert("organization_memberships", {"organization_id": oid, "user_id": users[key]["id"],
                "role": "admin" if key == "moderator" else "member", "created_at": now(), "updated_at": now()})
        group = post(clients["owner"], M + "/groups", {"organization_id": oid, "name": "Fixture disputed messages",
                      "member_ids": [users[key]["id"] for key in ("ann", "ben", "cid")]})
        assert group.status_code == 201, group.text
        cid = group.json()["id"]
        message = post(clients["ann"], f"{M}/conversations/{cid}/messages", {"body": "Fixture reported message"})
        assert message.status_code == 201, message.text
        assert post(clients["owner"], "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
        try:
            yield {"api": api, "store": ident.store, "clients": clients, "users": users, "oid": oid,
                   "cid": cid, "message": message.json()["message"]}
        finally:
            for client in clients.values():
                client.close()


def report(w, who="ben"):
    return post(w["clients"][who], f"{M}/messages/{w['message']['id']}/report", {"reason": "other", "note": "Fixture report"})


def review(w, rid, decision="removed"):
    return post(w["clients"]["owner"], f"{R}/{rid}/review", {"decision": decision, "note": "Fixture review"})


def snapshot(store):
    return {
        "reports": store.all("SELECT * FROM message_reports ORDER BY id"),
        "notices": store.all("SELECT * FROM notifications WHERE object_kind='message_report' ORDER BY seq"),
        "deliveries": store.all("SELECT d.* FROM notification_deliveries d JOIN notifications n ON n.id=d.notification_id "
                                "WHERE n.object_kind='message_report' ORDER BY d.notification_id,d.channel"),
        "push": store.all("SELECT q.* FROM push_queue q JOIN notifications n ON n.id=q.notification_id "
                          "WHERE n.object_kind='message_report' ORDER BY q.notification_id"),
        "audit": store.all("SELECT * FROM audit_events WHERE action IN ('messaging.message.report','messaging.report.review') ORDER BY seq"),
    }


@pytest.mark.parametrize("decision", ["removed", "dismissed"])
def test_review_cannot_overtake_reporter_todo_creation(world, monkeypatch, decision):
    w = world
    first = report(w)
    assert first.status_code == 201, first.text
    observer = Store(w["store"].root)
    created, release, reviewing = Event(), Event(), Event()
    original_report, original_tx = Messaging.report, Store.tx

    def pause_after_report(svc, *args, **kwargs):
        out = original_report(svc, *args, **kwargs)
        if args[1] == w["users"]["cid"]["id"]:
            created.set()
            assert release.wait(10), "fixture producer release timed out"
        return out

    @contextmanager
    def observe_review(store):
        if any(f.function == "review_report" and f.frame.f_globals.get("__name__") ==
               "synwevo_identity.messaging_reports_routes" for f in inspect.stack()):
            reviewing.set()
        with original_tx(store) as tx:
            yield tx

    monkeypatch.setattr(Messaging, "report", pause_after_report)
    monkeypatch.setattr(Store, "tx", observe_review)
    with ThreadPoolExecutor(max_workers=2) as pool:
        producer = pool.submit(report, w, "cid")
        try:
            assert created.wait(10)
            visible = snapshot(observer)
            reviewer = pool.submit(review, w, first.json()["id"], decision)
            assert reviewing.wait(10)
            # If a partial report escaped its request, deterministically let review finish
            # before its late to-dos. Otherwise the writer lock must serialize both requests.
            if len(visible["reports"]) == 2:
                assert reviewer.result(timeout=10).status_code == 200
        finally:
            release.set()
        made, done = producer.result(timeout=10), reviewer.result(timeout=10)
    assert made.status_code == 201 and done.status_code == 200, (made.text, done.text)
    final = snapshot(observer)
    assert {row["status"] for row in final["reports"]} == {decision}
    todos = [row for row in final["notices"] if row["kind"] == "conversation.reported"]
    assert len(todos) == 4 and {row["action_state"] for row in todos} == {"done"}, todos
    assert len([row for row in final["notices"] if row["kind"] == f"conversation.report_{decision}"]) == 2
    assert len(visible["reports"]) == 1, "a report must not become visible before all its required to-dos"


def test_service_report_rechecks_message_after_waiting_for_review_commit(world, monkeypatch):
    w = world
    first = report(w)
    assert first.status_code == 201
    independent = Store(w["store"].root)
    svc = Messaging(independent, Identity(independent))
    waiting, release, scheduled = Event(), Event(), []
    original = Store.tx

    @contextmanager
    def pause_before_writer(store):
        if store is independent and not scheduled:
            scheduled.append(True)
            waiting.set()
            assert release.wait(10)
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", pause_before_writer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(svc.report, w["message"]["id"], w["users"]["cid"]["id"], "other")
        try:
            assert waiting.wait(10)
            done = review(w, first.json()["id"])
            assert done.status_code == 200, done.text
        finally:
            release.set()
        with pytest.raises(ServiceError) as caught:
            pending.result(timeout=10)
    assert (caught.value.status, caught.value.code) == (404, "no_message")
    assert [row["status"] for row in snapshot(independent)["reports"]] == ["removed"]


@pytest.mark.parametrize("loss,status,error", [
    ("session", 401, "unauthorized"), ("expiry", 401, "unauthorized"),
    ("account", 404, "no_conversation"), ("organization_member", 404, "no_conversation"),
    ("conversation_member", 404, "no_conversation"), ("history", 404, "no_message"),
    ("frozen", 409, "organization_unavailable"), ("deleted", 409, "organization_unavailable"),
])
def test_report_rechecks_current_authority_inside_writer(world, monkeypatch, loss, status, error):
    w = world
    uid = w["users"]["ben"]["id"]
    sid = w["store"].one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (uid,))["id"]
    independent, original, scheduled = Store(w["store"].root), Store.tx, []
    before = snapshot(independent)

    @contextmanager
    def withdraw_before_writer(store):
        stack = inspect.stack()
        if (store is w["store"] and not scheduled and not any(f.function == "window_allows" for f in stack)
                and any(f.function == "report" and f.frame.f_globals.get("__name__") in
                        ("synwevo_messages.routes", "synwevo_core.messaging") for f in stack)):
            scheduled.append(True)
            if loss == "session":
                Sessions(independent).revoke(sid, "fixture withdrawal")
            elif loss == "expiry":
                independent.execute("UPDATE sessions SET absolute_expires_at=? WHERE id=?", (now()-1, sid))
            elif loss == "account":
                independent.execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
            elif loss == "organization_member":
                independent.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (w["oid"], uid))
            elif loss == "conversation_member":
                independent.execute("UPDATE conversation_members SET left_at=? WHERE conversation_id=? AND user_id=?", (now(), w["cid"], uid))
            elif loss == "history":
                independent.execute("UPDATE conversation_members SET joined_seq=? WHERE conversation_id=? AND user_id=?", (w["message"]["seq"]+1, w["cid"], uid))
            else:
                independent.execute("UPDATE organizations SET status=? WHERE id=?", (loss, w["oid"]))
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", withdraw_before_writer)
    response = report(w)
    assert scheduled == [True]
    assert response.status_code == status and response.json()["error"] == error, response.text
    assert snapshot(independent) == before


@pytest.mark.parametrize("failure", ["audit", "notification", "delivery", "push"])
def test_report_and_all_todos_roll_back_together_on_persistence_failure(world, failure):
    w, store = world, world["store"]
    managers = Messaging(store, Identity(store)).messaging_managers(w["oid"])
    second = managers[1]
    # Queue-only fixture subscription: workers are stopped and this endpoint is never called.
    sid = store.one("SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL", (second,))["id"]
    store.insert("push_subscriptions", {"id": "fixture-report-device", "user_id": second, "session_id": sid,
        "endpoint": "https://push.example.test/fixture", "endpoint_hash": "fixture-report-endpoint",
        "p256dh": "fixture-unused-key", "auth": "fixture-unused-auth", "created_at": now(), "updated_at": now()})
    before = snapshot(store)
    triggers = {
        "audit": ("audit_events", "NEW.action='messaging.message.report'"),
        "notification": ("notifications", f"NEW.kind='conversation.reported' AND NEW.user_id='{second}'"),
        "delivery": ("notification_deliveries", "EXISTS (SELECT 1 FROM notifications WHERE id=NEW.notification_id "
                     f"AND kind='conversation.reported' AND user_id='{second}')"),
        "push": ("push_queue", f"NEW.user_id='{second}'"),
    }
    table, predicate = triggers[failure]
    store.execute(f"CREATE TRIGGER fixture_report_failure BEFORE INSERT ON {table} WHEN {predicate} "
                  "BEGIN SELECT RAISE(ABORT, 'fixture required write failure'); END")
    try:
        response = report(w)
    finally:
        store.execute("DROP TRIGGER fixture_report_failure")
    assert response.status_code == 500, response.text
    assert snapshot(store) == before
    retried = report(w)
    assert retried.status_code == 201, retried.text
    after = snapshot(store)
    assert len(after["reports"]) == len(after["audit"]) == 1
    assert len(after["notices"]) == len(after["deliveries"]) == 2
    assert len(after["push"]) == 1
    assert report(w).status_code == 409
    assert snapshot(store) == after


def test_concurrent_duplicate_reports_create_one_report_and_one_set_of_todos(world, monkeypatch):
    w = world
    waiting, release, first_seen = Event(), Event(), []
    original = Messaging.report

    def pause_one(svc, *args, **kwargs):
        if not first_seen:
            first_seen.append(True)
            waiting.set()
            assert release.wait(10)
        return original(svc, *args, **kwargs)

    monkeypatch.setattr(Messaging, "report", pause_one)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(report, w)
        try:
            assert waiting.wait(10)
            second = pool.submit(report, w)
        finally:
            release.set()
        responses = [first.result(timeout=10), second.result(timeout=10)]
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    state = snapshot(w["store"])
    assert len(state["reports"]) == len(state["audit"]) == 1
    assert len(state["notices"]) == len(state["deliveries"]) == 2
