"""D20: the restricted console dispatches to real Docs HTTP with an approved service token."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest

from synwevo_core.machine import SCOPE_MANAGE, SCOPE_READ
from synwevo_core.store import new_id, now
from synwevo_docs.control_plane import DocsControlPlane
from tests.integration.test_api_platform import (
    BASE, OWNER, REASON, approve, issue, plane, post, reauth, request_approval, service_token,
)

SUBS = "/api/v1/webhooks/subscriptions"


@pytest.fixture()
def controlled(plane, monkeypatch):
    e = dict(plane)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    approval = request_approval(e, scopes=[SCOPE_READ, SCOPE_MANAGE])
    approve(e, approval)
    credential = issue(e, approval)
    response = service_token(e, credential)
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    sub = e["admin"].post(SUBS, headers=h, json={"url": "http://127.0.0.1:9/d20-fixture", "events": ["*"]})
    assert sub.status_code == 201, sub.text
    sid = sub.json()["id"]
    # Explicit isolated job/event fixture; real dispatcher creates the initial delivery.
    dispatcher = e["api"].state.webhooks
    dispatcher.enqueue_new_events()
    jid, eid = new_id("j"), new_id("ev")
    e["store"].insert("jobs", {"id": jid, "project_id": e["project"]["id"], "status": "completed",
        "created_at": now(), "counts": "{}", "tenant": e["ws"], "run_no": 1})
    e["store"].insert("job_events", {"id": eid, "job_id": jid, "tenant": e["ws"], "type": "job.completed",
        "version": 1, "occurred_at": now(), "data": "{}"})
    dispatcher.enqueue_new_events()
    did = e["store"].one("SELECT id FROM webhook_deliveries WHERE subscription_id=?", (sid,))["id"]
    e.update(token=token, approval=approval, credential=credential, sid=sid, did=did, h=h,
             path=f"{SUBS}/{sid}/deliveries/{did}/redeliver")
    return e


def controlled_post(e, **extra):
    return post(e["admin"], BASE + "/request", {"reason": REASON, "credential": e["token"],
        "method": "POST", "path": e["path"], **extra})


def test_console_and_direct_http_share_one_receipt_and_replay_header(controlled):
    e = controlled
    first = controlled_post(e, idempotency_key="fixture-console-retry")
    assert first.status_code == 200 and first.json()["status"] == 202, first.text
    again = controlled_post(e, idempotency_key="fixture-console-retry")
    assert again.status_code == 200 and again.json()["data"] == first.json()["data"], again.text
    assert again.json()["headers"]["idempotent-replayed"] == "true"
    direct = e["admin"].post(e["path"], headers={**e["h"], "Idempotency-Key": "fixture-console-retry"})
    assert direct.status_code == 202 and direct.json() == first.json()["data"]
    assert direct.headers["Idempotent-Replayed"] == "true"
    assert e["store"].one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE redelivery_of IS NOT NULL")["n"] == 1
    assert first.headers["Cache-Control"] == "no-store"
    assert e["token"] not in str(e["store"].all("SELECT * FROM idempotency"))
    assert e["token"] not in str(e["store"].all("SELECT * FROM audit_events"))


@pytest.mark.parametrize("key", [None, 42, "", "中文", "x"*256, "fixture\r\nHeader: forged"])
def test_console_validates_optional_key_before_dispatch(controlled, key):
    response = controlled_post(controlled, idempotency_key=key)
    assert response.status_code == 400 and response.json()["error"] == "invalid_idempotency_key", response.text
    assert not controlled["store"].one("SELECT 1 FROM webhook_deliveries WHERE redelivery_of IS NOT NULL")


@pytest.mark.parametrize("method,path", [("GET", SUBS), ("POST", SUBS),
    ("POST", SUBS + "/fixture/rotate-secret"), ("POST", "https://example.test/api/v1/webhooks/subscriptions/x/deliveries/y/redeliver")])
def test_console_key_is_allowed_only_on_the_exact_redelivery_surface(controlled, method, path):
    response = controlled_post(controlled, method=method, path=path, idempotency_key="fixture-only-redeliver")
    assert response.status_code == 400 and response.json()["error"] == "request_not_allowed", response.text
    assert not controlled["store"].one("SELECT 1 FROM idempotency")


def test_console_does_not_accept_arbitrary_headers(controlled):
    result = controlled_post(controlled, headers={"Idempotency-Key": "forged"})
    assert result.status_code == 400 and result.json()["error"] == "unknown_field"


def test_omitting_field_keeps_the_old_adapter_signature(controlled, monkeypatch):
    e = controlled
    original = DocsControlPlane.request
    calls = []

    async def old_adapter(self, credential, method, path, query, body):
        calls.append(path)
        return await original(self, credential, method, path, query, body)

    monkeypatch.setattr(DocsControlPlane, "request", old_adapter)
    first, second = controlled_post(e), controlled_post(e)
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == 202
    assert first.json()["data"]["id"] != second.json()["data"]["id"]
    assert calls == [e["path"], e["path"]]


@pytest.mark.parametrize("change", ["approval", "scope", "subscription_deleted", "receipt_deleted", "conflict_deleted"])
def test_inflight_cached_response_is_withheld_after_service_authority_changes(controlled, monkeypatch, change):
    e = controlled
    first = controlled_post(e, idempotency_key="fixture-inflight")
    assert first.status_code == 200 and first.json()["status"] == 202, first.text
    completed, release = Event(), Event()
    actual = httpx.ASGITransport.handle_async_request

    async def hold_response(self, request):
        response = await actual(self, request)
        if request.url.host == "control-plane.internal":
            completed.set()
            assert await asyncio.to_thread(release.wait, 5)
        return response

    monkeypatch.setattr(httpx.ASGITransport, "handle_async_request", hold_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        options = {"path": e["path"].replace(e["did"], "wh_missing")} if change == "conflict_deleted" else {}
        result = pool.submit(controlled_post, e, idempotency_key="fixture-inflight", **options)
        try:
            assert completed.wait(3)
            if change == "approval":
                revoked = post(e["owner"], f"{OWNER}/{e['approval']['id']}/revoke", {"reason": REASON})
                assert revoked.status_code == 200, revoked.text
            elif change == "scope":
                e["store"].execute("UPDATE api_platform_approvals SET approved_scopes=? WHERE id=?",
                    (json.dumps([SCOPE_READ]), e["approval"]["id"]))
            elif change in ("subscription_deleted", "conflict_deleted"):
                e["store"].execute("UPDATE webhook_subscriptions SET deleted_at=? WHERE id=?", (now(), e["sid"]))
            else:
                e["store"].execute("DELETE FROM webhook_deliveries WHERE id=?", (first.json()["data"]["id"],))
        finally:
            release.set()
        response = result.result(10)
    assert response.status_code in (401, 403, 404), response.text
    assert first.json()["data"]["id"] not in response.text
    assert e["store"].one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE redelivery_of IS NOT NULL")["n"] == (0 if change == "receipt_deleted" else 1)
