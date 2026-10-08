"""Real identity routes over isolated synthetic notifications; no delivery workers run.

Fixture failures are explicitly seeded through the notifier's retry transition. Scheduling
hooks only interleave real database writes; no authentication or core receipt is faked.
"""
from __future__ import annotations

import base64
import concurrent.futures
import inspect
import json
import threading
import types
import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from synwevo_core.notifications import Notifier
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store, now
from synwevo_identity import admin_notification_recovery as recovery

from .platform_fixture import end_platform_grants, make_platform_admin
from .test_admin_operations import PW, post, world  # noqa: F401 -- isolated identity fixture

BASE = "/api/admin/notification-recovery"
ACTION = "admin.notification.recover_enqueue"
REASON = "Isolated fixture: restore notification enqueue eligibility"
PRIVATE = "fixture-private-notification-content"
ITEM_FIELDS = {"notification_id", "user_id", "category", "status", "attempts", "updated_at",
               "error_code", "recoverable", "blocked_reason"}


def failed(w, *, user_id=None):
    notifier = Notifier(w["store"])
    row = notifier.emit(user_id or w["target"]["id"], "security.change",
                        params={"what": PRIVATE}, target={"origin": "accounts", "path": "/" + PRIVATE},
                        dedupe_key="fixture-recovery-" + uuid.uuid4().hex)
    for _ in range(5):
        notifier.retry_delivery(row["id"], "notification_enqueue_failed")
    return row["id"]


@pytest.fixture()
def case(world):
    world["nid"] = failed(world)
    yield world


def preview(w, *, client=None, nid=None):
    response = post(client or w["admin"], f"{BASE}/{nid or w['nid']}/preview", {})
    assert response.status_code == 200, response.text
    return response.json()


def command(plan):
    return {"preview_token": plan["preview_token"], "reason": REASON, "idempotency_key": uuid.uuid4().hex}


def execute(w, body, *, client=None, nid=None, **kwargs):
    return post(client or w["admin"], f"{BASE}/{nid or w['nid']}/execute", body, **kwargs)


def delivery(w, nid=None):
    return w["store"].one("SELECT * FROM notification_deliveries WHERE notification_id=? AND channel='email'",
                           (nid or w["nid"],))


def audits(w):
    return w["store"].all("SELECT * FROM audit_events WHERE action=? ORDER BY seq", (ACTION,))


def assert_no_execution(w):
    assert delivery(w)["status"] == "failed"
    assert not w["store"].all("SELECT * FROM admin_operations WHERE action=?", (ACTION,))
    assert not [row for row in audits(w) if row["result"] == "ok"]


def test_list_and_preview_expose_only_metadata_without_mutation(case):
    before = delivery(case)
    page = case["admin"].get(BASE).json()
    assert page["limit"] == 25 and page["offset"] == 0 and page["total"] == 1
    assert page["next_offset"] is None
    item = page["items"][0]
    assert set(item) == ITEM_FIELDS
    assert item["notification_id"] == case["nid"] and item["user_id"] == case["target"]["id"]
    assert item["status"] == "failed" and item["attempts"] == 5
    assert item["recoverable"] is True and item["blocked_reason"] is None
    assert item["error_code"] == "notification_enqueue_failed"
    plan = preview(case)
    assert set(plan) == {"action", "scope", "risk", "target", "impact", "expires_at", "preview_token"}
    assert plan["action"] == ACTION and plan["scope"] == "platform" and plan["risk"] == "high"
    assert plan["target"] == item and isinstance(plan["impact"], str)
    assert now() + 290 < plan["expires_at"] <= now() + 300
    payload = json.loads(base64.urlsafe_b64decode(plan["preview_token"].split(".")[0] + "=="))
    assert payload["version"] == Notifier(case["store"]).email_recovery_row(case["nid"])["version"]
    assert len(payload["version"]) == 64 and payload["notification_id"] == case["nid"]
    assert PRIVATE not in json.dumps([page, plan, payload])
    assert case["target"]["email"] not in json.dumps([page, plan, payload])
    assert delivery(case) == before and not audits(case)
    assert not case["store"].all("SELECT * FROM admin_operations")


def test_pagination_uses_notification_high_water_and_searches_only_ids(case):
    ids = [case["nid"], failed(case), failed(case)]
    page = case["admin"].get(BASE, params={"limit": 2}).json()
    assert [r["notification_id"] for r in page["items"]] == ids[:0:-1]
    assert page["next_offset"] == 2 and page["total"] == 3
    later = failed(case)
    next_page = case["admin"].get(BASE, params={"limit": 2, "offset": 2, "snapshot": page["snapshot"]}).json()
    assert [r["notification_id"] for r in next_page["items"]] == ids[:1]
    assert next_page["total"] == 3 and next_page["next_offset"] is None
    assert case["admin"].get(BASE).json()["total"] == 4
    for query, expected in [(later, 1), (case["target"]["id"], 4), (PRIVATE, 0),
                            (case["target"]["email"], 0), ("%", 0), ("' OR 1=1--", 0)]:
        result = case["admin"].get(BASE, params={"q": query}).json()
        assert result["total"] == expected, result
    assert case["admin"].get(BASE, params={"snapshot": 0}).json()["items"] == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"snapshot": -1},
                                   {"snapshot": 2**63}, {"q": "x" * 101}])
def test_paging_bounds(case, params):
    assert case["admin"].get(BASE, params=params).status_code == 422


@pytest.mark.parametrize("change,blocked", [
    ("historical", "historical_delivery"), ("error", "unverified_failure"),
    ("attempts", "retry_not_exhausted"), ("mail_id", "mail_already_queued"),
    ("outbox", "mail_already_queued"), ("pending", "not_failed"),
    ("queued", "not_failed"), ("skipped", "not_failed"),
])
def test_ineligible_rows_have_stable_codes_and_cannot_be_previewed(case, change, blocked):
    store, nid = case["store"], case["nid"]
    if change == "historical":
        store.execute("UPDATE notification_deliveries SET enqueue_protocol=NULL WHERE notification_id=?", (nid,))
    elif change == "error":
        store.execute("UPDATE notification_deliveries SET last_error=? WHERE notification_id=?", (PRIVATE, nid))
    elif change == "attempts":
        store.execute("UPDATE notification_deliveries SET attempts=4 WHERE notification_id=?", (nid,))
    elif change == "mail_id":
        store.execute("UPDATE notification_deliveries SET mail_id='fixture-mail' WHERE notification_id=?", (nid,))
    elif change == "outbox":
        insert_outbox(case)
    else:
        store.execute("UPDATE notification_deliveries SET status=? WHERE notification_id=?", (change, nid))
    before = delivery(case)
    page = case["admin"].get(BASE).json()
    assert bool(page["items"]) == (before["status"] == "failed")
    if page["items"]:
        assert page["items"][0]["blocked_reason"] == blocked and not page["items"][0]["recoverable"]
        if change == "error":
            assert page["items"][0]["error_code"] == "notification_delivery_failed"
    response = post(case["admin"], f"{BASE}/{nid}/preview", {})
    assert response.status_code == 409 and response.json()["error"] == "notification_not_recoverable"
    assert response.json()["detail"]["blocked_reason"] == blocked
    assert PRIVATE not in json.dumps([page, response.json(), audits(case)])
    assert delivery(case) == before


def insert_outbox(w):
    # Synthetic SMTP-unknown outbox metadata. Recovery must never enqueue or retry this mail.
    w["store"].execute("INSERT INTO mail_outbox(id,to_addr,subject,body,kind,ref_id,status,last_error,created_at) "
                       "VALUES ('fixture-mail',?,?,?,'notification',?,'failed','delivery_unknown',?)",
                       ("private@example.test", PRIVATE, PRIVATE, w["nid"], now()))


def test_push_only_or_missing_delivery_is_not_available(case):
    body = command(preview(case))
    store, nid = case["store"], case["nid"]
    # Synthetic push-only failure uses the real, independent push table. Email's schema
    # remains email-only, and an already issued email preview cannot recover this row.
    with store.tx():
        store.execute("DELETE FROM notification_deliveries WHERE notification_id=?", (nid,))
        store.execute("INSERT INTO push_queue(notification_id,user_id,status,attempts,last_error,updated_at) "
                      "VALUES (?,?,'failed',5,?,?)", (nid, case["target"]["id"], PRIVATE, now()))
    push_before = store.one("SELECT * FROM push_queue WHERE notification_id=?", (nid,))
    assert case["admin"].get(BASE).json()["items"] == []
    for nid in (case["nid"], "fixture-missing"):
        response = post(case["admin"], f"{BASE}/{nid}/preview", {})
        assert response.status_code == 404 and response.json()["error"] == "notification_delivery_missing"
    response = execute(case, body)
    assert response.status_code == 404 and response.json()["error"] == "notification_delivery_missing"
    assert store.one("SELECT * FROM push_queue WHERE notification_id=?", (case["nid"],)) == push_before
    assert not store.all("SELECT * FROM admin_operations")
    assert not [row for row in audits(case) if row["result"] == "ok"]
    assert PRIVATE not in json.dumps([response.json(), audits(case)])


@pytest.mark.parametrize("kind,expected", [("anonymous", 401), ("ordinary", 404), ("wrong_role", 403)])
def test_identity_and_permissions_apply_to_every_endpoint(case, kind, expected):
    body = command(preview(case))
    if kind == "anonymous":
        client = TestClient(case["api"])
    elif kind == "ordinary":
        client = case["ordinary"]
    else:
        user = make_platform_admin(case["store"], case["ident"].create_user(
            "wrong-role@example.test", PW, status="active"), role="api_platform_admin")
        client = case["login"](user)
        case["reauth"](client)
    try:
        assert client.get(BASE).status_code == expected
        assert post(client, f"{BASE}/{case['nid']}/preview", {}).status_code == expected
        assert execute(case, body, client=client).status_code == expected
        assert_no_execution(case)
    finally:
        if kind == "anonymous":
            client.close()


def test_view_permission_does_not_grant_preview_or_execute(case):
    user = make_platform_admin(case["store"], case["ident"].create_user(
        "view-only@example.test", PW, status="active"), role="support")
    client = case["login"](user)
    case["reauth"](client)
    assert client.get(BASE).status_code == 200
    assert post(client, f"{BASE}/{case['nid']}/preview", {}).status_code == 403
    response = execute(case, command(preview(case)), client=client)
    assert response.status_code == 403 and response.json()["error"] == "platform_permission_denied"
    assert_no_execution(case)


def test_execute_requires_strict_admin_reauthentication(case):
    client = case["login"](case["root"])
    body = command(preview(case, client=client))
    response = execute(case, body, client=client)
    assert response.status_code == 403 and response.json()["error"] == "reauth_required"
    assert_no_execution(case)
    case["reauth"](client)
    assert execute(case, body, client=client).status_code == 200


@pytest.mark.parametrize("alteration", ["actor", "notification", "origin", "signature", "expiry"])
def test_preview_binding(case, monkeypatch, alteration):
    plan = preview(case)
    body, kwargs = command(plan), {}
    if alteration == "actor":
        user = make_platform_admin(case["store"], case["ident"].create_user(
            "other-operator@example.test", PW, status="active"))
        kwargs["client"] = case["login"](user)
        case["reauth"](kwargs["client"])
    elif alteration == "notification":
        kwargs["nid"] = failed(case)
    elif alteration == "origin":
        kwargs["headers"] = {"host": "localhost"}
    elif alteration == "signature":
        body["preview_token"] += "tampered"
    else:
        monkeypatch.setattr(recovery, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    response = execute(case, body, **kwargs)
    assert response.status_code == 409, response.text
    assert response.json()["error"] == ("admin_preview_expired" if alteration == "expiry" else "admin_preview_invalid")
    assert_no_execution(case)


@pytest.mark.parametrize("change", ["updated_at", "attempts", "not_before", "defer_reason", "policy_revision",
                                  "last_error", "enqueue_protocol", "mail_id", "outbox", "status"])
def test_changed_delivery_invalidates_preview(case, change):
    body = command(preview(case))
    if change == "outbox":
        insert_outbox(case)
    else:
        values = {"updated_at": now() + 1, "attempts": 6, "not_before": now() + 900,
                  "defer_reason": "quiet", "policy_revision": 9, "last_error": PRIVATE,
                  "enqueue_protocol": None, "mail_id": "fixture-mail", "status": "queued"}
        case["store"].execute(f"UPDATE notification_deliveries SET {change}=? WHERE notification_id=?",
                              (values[change], case["nid"]))
    before = delivery(case)
    response = execute(case, body)
    assert response.status_code == 409 and response.json()["error"] == "admin_preview_stale", response.text
    assert delivery(case) == before
    assert not case["store"].all("SELECT * FROM admin_operations")
    assert PRIVATE not in json.dumps([response.json(), audits(case)])


def test_success_preserves_identity_attempts_and_commits_safe_audit_with_receipt(case):
    body = command(preview(case))
    before = delivery(case)
    notification = case["store"].one("SELECT * FROM notifications WHERE id=?", (case["nid"],))
    response = execute(case, body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert set(result) == {"ok", "operation_id", "state", "notification_id", "status", "attempts", "recovery_scheduled"}
    assert result == {"ok": True, "operation_id": result["operation_id"], "state": "succeeded",
                      "notification_id": case["nid"], "status": "pending", "attempts": 5, "recovery_scheduled": True}
    after = delivery(case)
    for field in ("notification_id", "channel", "attempts", "mail_id", "enqueue_protocol", "last_error"):
        assert after[field] == before[field]
    assert after["status"] == "pending" and after["not_before"] <= now()
    assert after["defer_reason"] is None and after["policy_revision"] is None
    assert case["store"].one("SELECT * FROM notifications WHERE id=?", (case["nid"],)) == notification
    assert not case["store"].all("SELECT * FROM mail_outbox")
    saved = case["store"].one("SELECT * FROM admin_operations")
    assert saved["id"] == result["operation_id"] and json.loads(saved["result_json"]) == result
    event, = audits(case)
    assert event["result"] == "ok" and event["actor_id"] == case["root"]["id"]
    assert event["object_id"] == case["nid"] and event["object_kind"] == "notification"
    assert event["workspace_id"] is None and event["request_id"]
    meta = json.loads(event["meta"])
    assert meta["operation_id"] == result["operation_id"] and meta["reason"] == REASON
    assert set(meta["before"]) == ITEM_FIELDS and meta["before"]["status"] == "failed"
    assert meta["after"]["status"] == "pending" and meta["after"]["recovery_scheduled"] is True
    rendered = json.dumps([response.json(), event, saved])
    for secret in (PRIVATE, PW, body["preview_token"], case["target"]["email"]):
        assert secret not in rendered


def test_idempotent_receipt_survives_new_app_and_preview_expiry(case, monkeypatch):
    plan = preview(case)
    body = command(plan)
    first = execute(case, body)
    assert first.status_code == 200, first.text
    from synwevo_identity.app import create_app
    # Reopen the same isolated database with a distinct application and store.
    api = create_app(str(case["store"].root), start_jobs=False)
    monkeypatch.setattr(recovery, "time", types.SimpleNamespace(time=lambda: plan["expires_at"] + 1))
    with TestClient(api, cookies=case["admin"].cookies) as client:
        retry = execute(case, {**body, "reason": "  " + REASON + "  "}, client=client)
    assert retry.status_code == 200 and retry.json() == first.json(), retry.text
    assert len(audits(case)) == len(case["store"].all("SELECT * FROM admin_operations")) == 1
    end_platform_grants(case["store"], case["root"])
    assert execute(case, body).status_code == 404


@pytest.mark.parametrize("field", ["reason", "preview_token", "notification", "origin"])
def test_same_key_with_changed_request_is_a_conflict(case, field):
    body = command(preview(case))
    assert execute(case, body).status_code == 200
    kwargs = {}
    if field == "notification":
        kwargs["nid"] = failed(case)
    elif field == "origin":
        kwargs["headers"] = {"host": "localhost"}
    else:
        body[field] += " changed"
    response = execute(case, body, **kwargs)
    assert response.status_code == 409 and response.json()["error"] == "idempotency_conflict", response.text
    assert len(case["store"].all("SELECT * FROM admin_operations")) == 1
    assert len([a for a in audits(case) if a["result"] == "ok"]) == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_operators_commit_once(case, same_key):
    user = case["root"] if same_key else make_platform_admin(case["store"], case["ident"].create_user(
        "competing-operator@example.test", PW, status="active"), role="access_admin")
    other = case["login"](user)
    case["reauth"](other)
    clients = [case["admin"], other]
    first = command(preview(case))
    bodies = [first, first if same_key else command(preview(case, client=other))]
    barrier = threading.Barrier(2)

    def submit(index):
        barrier.wait(timeout=10)
        return execute(case, bodies[index], client=clients[index])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in replies) == ([200, 200] if same_key else [200, 409])
    if same_key:
        assert replies[0].json() == replies[1].json()
    else:
        assert next(r for r in replies if r.status_code == 409).json()["error"] == "admin_preview_stale"
    assert delivery(case)["attempts"] == 5 and delivery(case)["status"] == "pending"
    assert len(case["store"].all("SELECT * FROM admin_operations")) == 1
    assert len([a for a in audits(case) if a["result"] == "ok"]) == 1
    assert not case["store"].all("SELECT * FROM mail_outbox")


@pytest.mark.parametrize("change,expected", [("grant", 404), ("suspended", 401), ("session", 401),
                                          ("reauth", 403), ("factor", 403)])
def test_authorization_is_rechecked_inside_writer_lock(case, monkeypatch, change, expected):
    body = command(preview(case))
    original, scheduled = Store.tx, []
    independent = Store(case["store"].root)

    @contextmanager
    def interleaved(store):
        if (not scheduled and store.root == independent.root and any(
                frame.filename == recovery.__file__ and frame.function == "execute" for frame in inspect.stack())):
            scheduled.append(True)
            uid = case["root"]["id"]
            if change == "grant":
                end_platform_grants(independent, uid)
            elif change == "suspended":
                independent.execute("UPDATE users SET status='suspended' WHERE id=?", (uid,))
            elif change == "session":
                Sessions(independent).revoke_all_for_user(uid, "fixture_revoke")
            elif change == "reauth":
                independent.execute("UPDATE sessions SET reauth_at=0 WHERE user_id=?", (uid,))
            else:
                independent.execute("UPDATE sessions SET amr='email' WHERE user_id=?", (uid,))
        with original(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", interleaved)
    response = execute(case, body)
    assert scheduled == [True]
    assert response.status_code == expected, response.text
    assert_no_execution(case)
    assert len(audits(case)) == 1 and audits(case)[0]["result"] == "denied"


@pytest.mark.parametrize("stage", ["audit", "receipt"])
def test_persistence_failure_rolls_back_transition_and_success_audit(case, monkeypatch, stage):
    body = command(preview(case))
    before = delivery(case)
    if stage == "audit":
        original = Store.execute

        def after_audit(store, sql, params=()):
            result = original(store, sql, params)
            if sql.startswith("INSERT INTO audit_events") and ACTION in params and "ok" in params:
                raise OSError(PRIVATE)
            return result

        monkeypatch.setattr(Store, "execute", after_audit)
    else:
        case["store"].execute("CREATE TRIGGER fixture_receipt_failure AFTER INSERT ON admin_operations "
                              "BEGIN SELECT RAISE(FAIL,'fixture receipt failure'); END")
    response = execute(case, body)
    assert response.status_code == 500 and response.json()["error"] == "admin_operation_failed", response.text
    assert delivery(case) == before
    assert_no_execution(case)
    event, = audits(case)
    assert event["result"] == "error" and json.loads(event["meta"])["code"] == "admin_operation_failed"
    assert PRIVATE not in json.dumps([response.json(), event])


@pytest.mark.parametrize("field,value,code", [
    ("reason", {"private": PRIVATE}, "missing_reason"), ("reason", "short", "missing_reason"),
    ("reason", PRIVATE * 30, "missing_reason"), ("idempotency_key", "short", "admin_confirmation_required"),
    ("preview_token", PRIVATE, "admin_preview_invalid"), ("preview_token", {"private": PRIVATE}, "admin_preview_invalid"),
    ("preview", PRIVATE, "notification_recovery_bad_request"),
])
def test_invalid_confirmation_does_not_echo_or_audit_payload(case, field, value, code):
    body = {**command(preview(case)), field: value}
    response = execute(case, body)
    assert response.status_code in (400, 409) and response.json()["error"] == code, response.text
    assert_no_execution(case)
    rendered = json.dumps([response.json(), audits(case)])
    assert PRIVATE not in rendered
    if field != "preview_token":
        assert body["preview_token"] not in rendered


def test_empty_preview_and_safe_malformed_body_errors(case):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    headers = {CSRF_HEADER: case["admin"].cookies.get(CSRF_COOKIE)}
    path = f"{BASE}/{case['nid']}"
    assert case["admin"].post(path + "/preview", headers=headers).status_code == 200
    for suffix in ("preview", "execute"):
        for raw in ('{"private":"' + PRIVATE, '["' + PRIVATE + '"]'):
            response = case["admin"].post(path + "/" + suffix, content=raw,
                                          headers={**headers, "Content-Type": "application/json"})
            assert response.status_code == 400, response.text
            assert PRIVATE not in response.text
    assert_no_execution(case)
