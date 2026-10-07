"""D20: isolated HTTP requests and real Store transactions; no delivery/network mocks.

The receiver URL is a synthetic, unused loopback destination. Workers stay passive.
Barriers only schedule real operations; notification probes still call the real wake.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event

import pytest
from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.machine import MachineAuth
from synwevo_core.store import Store, now
from synwevo_docs import webhooks as W
from synwevo_docs.api.app import create_app
from .test_webhook_support import fixture_event

BASE = "/api/v1/webhooks/subscriptions"
KEY = "fixture-redelivery-receipt"


@pytest.fixture()
def redelivery(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "none")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_URLS", raising=False)
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = api.state.app.store
    token, key = Auth(store).create_key("D20 synthetic fixture", "default", ("admin",))
    h = {"Authorization": f"Bearer {token}"}
    with TestClient(api, raise_server_exceptions=False) as c:
        sub = c.post(BASE, headers=h, json={"url": "http://127.0.0.1:9/fixture", "events": ["*"]})
        assert sub.status_code == 201, sub.text
        sid = sub.json()["id"]
        api.state.webhooks.enqueue_new_events()
        ev = fixture_event(store, "default")
        api.state.webhooks.enqueue_new_events()
        orig = store.one("SELECT * FROM webhook_deliveries WHERE subscription_id=?", (sid,))
        assert orig
        observer = Store(store.root)
        yield {"api": api, "c": c, "store": store, "observer": observer, "h": h,
               "sid": sid, "did": orig["id"], "event": ev, "key_id": key["id"],
               "path": f"{BASE}/{sid}/deliveries/{orig['id']}/redeliver"}
        observer._c().close()


def post(e, key=KEY, path=None):
    return e["c"].post(path or e["path"], headers={**e["h"], **({"Idempotency-Key": key} if key is not None else {})})


def counts(store):
    return (store.one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE redelivery_of IS NOT NULL")["n"],
            store.one("SELECT COUNT(*) AS n FROM idempotency")["n"])


def probe_notify(e, monkeypatch):
    seen = []
    actual = e["api"].state.webhooks.notify

    def notify():
        seen.append((e["store"]._c().in_transaction, counts(e["observer"])))
        actual()

    monkeypatch.setattr(e["api"].state.webhooks, "notify", notify)
    return seen


def test_lost_response_replays_original_receipt_but_unkeyed_calls_create_again(redelivery, monkeypatch):
    e = redelivery
    seen = probe_notify(e, monkeypatch)
    first = post(e)
    assert first.status_code == 202, first.text
    # The caller lost this response. The real delivery has progressed before its retry.
    e["store"].execute("UPDATE webhook_deliveries SET status='delivered', attempts=1 WHERE id=?", (first.json()["id"],))
    replay = post(e)
    assert replay.status_code == 202 and replay.content == first.content
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["status"] == "pending"
    current = e["c"].get(f"{BASE}/{e['sid']}/deliveries/{first.json()['id']}", headers=e["h"])
    assert current.json()["status"] == "delivered"
    assert seen == [(False, (1, 1))]
    plain = [post(e, None), post(e, None)]
    assert all(r.status_code == 202 and "Idempotent-Replayed" not in r.headers for r in plain)
    assert len({r.json()["id"] for r in [first, *plain]}) == 3
    assert counts(e["store"]) == (3, 1)


@pytest.mark.parametrize("change", ["paused", "aged"])
def test_receipt_replay_uses_history_permission_not_new_delivery_eligibility(redelivery, monkeypatch, change):
    e = redelivery
    first = post(e)
    assert first.status_code == 202, first.text
    seen = probe_notify(e, monkeypatch)
    if change == "paused":
        assert e["c"].post(f"{BASE}/{e['sid']}/pause", headers=e["h"]).status_code == 200
    else:
        e["store"].execute("UPDATE job_events SET occurred_at=? WHERE id=?", (now()-8*86400, e["event"]["id"]))
    again = post(e)
    assert again.status_code == 202 and again.content == first.content
    assert again.headers["Idempotent-Replayed"] == "true"
    assert post(e, "new-intent").status_code == 409
    assert counts(e["store"]) == (1, 1) and seen == []


@pytest.mark.parametrize("change", ["subscription_deleted", "receipt_deleted", "key_revoked", "scope_narrowed"])
def test_cached_receipt_does_not_survive_loss_of_current_access(redelivery, change):
    e = redelivery
    first = post(e)
    assert first.status_code == 202, first.text
    if change == "subscription_deleted":
        assert e["c"].delete(f"{BASE}/{e['sid']}", headers=e["h"]).status_code == 200
    elif change == "receipt_deleted":
        e["store"].execute("DELETE FROM webhook_deliveries WHERE id=?", (first.json()["id"],))
    elif change == "key_revoked":
        e["store"].execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (now(), e["key_id"]))
    else:
        e["store"].execute("UPDATE api_keys SET scopes='read' WHERE id=?", (e["key_id"],))
    denied = post(e)
    assert denied.status_code in (401, 403, 404), denied.text
    assert "Idempotent-Replayed" not in denied.headers and first.json()["id"] not in denied.text


def test_conflict_checks_cached_delivery_subscription_before_disclosing_metadata(redelivery):
    e = redelivery
    first = post(e)
    assert first.status_code == 202, first.text
    other = e["c"].post(BASE, headers=e["h"], json={"url": "http://127.0.0.1:9/other"}).json()["id"]
    foreign_path = f"{BASE}/{other}/deliveries/wh_missing/redeliver"
    conflict = post(e, path=foreign_path)
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "idempotency_conflict"
    assert conflict.json()["error"]["details"]["resource_id"] == first.json()["id"]
    assert e["c"].delete(f"{BASE}/{e['sid']}", headers=e["h"]).status_code == 200
    refused = post(e, path=foreign_path)
    assert refused.status_code == 404 and first.json()["id"] not in refused.text
    assert counts(e["store"]) == (1, 1)


def test_parallel_http_duplicates_wait_for_one_atomic_receipt(redelivery, monkeypatch):
    e = redelivery
    start, entered, release = Barrier(3), Event(), Event()
    actual = W.Subscriptions.redeliver
    executions = []
    seen = probe_notify(e, monkeypatch)

    def hold(self, *args, **kwargs):
        out = actual(self, *args, **kwargs)
        executions.append(out["id"])
        entered.set()
        assert release.wait(5)
        return out

    monkeypatch.setattr(W.Subscriptions, "redeliver", hold)

    def call():
        start.wait(5)
        return post(e)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call) for _ in range(2)]
        try:
            start.wait(5)
            assert entered.wait(3), "request never reached the real redelivery operation"
            assert counts(e["observer"]) == (0, 0) and not seen
        finally:
            release.set()
        responses = [f.result(10) for f in futures]
    assert len(executions) == 1
    assert all(r.status_code == 202 for r in responses)
    assert responses[0].content == responses[1].content
    assert [r.headers.get("Idempotent-Replayed") for r in responses].count("true") == 1
    assert seen == [(False, (1, 1))]


def test_response_cache_failure_rolls_back_delivery_and_suppresses_notify(redelivery, monkeypatch):
    e = redelivery
    seen = probe_notify(e, monkeypatch)
    actual = Store.execute

    def fail_response(self, sql, args=()):
        if sql.startswith("UPDATE idempotency SET response_status="):
            raise RuntimeError("synthetic response persistence failure")
        return actual(self, sql, args)

    with monkeypatch.context() as patch:
        patch.setattr(Store, "execute", fail_response)
        failed = post(e)
        assert failed.status_code == 500, failed.text
    assert counts(e["observer"]) == (0, 0) and not seen
    assert post(e).status_code == 202
    assert seen == [(False, (1, 1))]


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("change", ["revoke", "pause", "delete"])
def test_authority_is_refreshed_after_gateway_admission(redelivery, monkeypatch, cached, change):
    e = redelivery
    if cached:
        assert post(e).status_code == 202
    entered, release = Event(), Event()
    actual = MachineAuth.verify

    def admitted(self, header):
        p = actual(self, header)
        # Pause the gateway's real admission only, never a transaction's recheck.
        if not self.store._c().in_transaction:
            entered.set()
            assert release.wait(5)
        return p

    monkeypatch.setattr(MachineAuth, "verify", admitted)
    with ThreadPoolExecutor(max_workers=1) as pool:
        call = pool.submit(post, e)
        try:
            assert entered.wait(3)
            if change == "revoke":
                e["store"].execute("UPDATE api_keys SET revoked_at=? WHERE id=?", (now(), e["key_id"]))
            elif change == "delete":
                e["store"].execute("UPDATE webhook_subscriptions SET deleted_at=? WHERE id=?", (now(), e["sid"]))
            else:
                e["store"].execute("UPDATE webhook_subscriptions SET status='paused' WHERE id=?", (e["sid"],))
        finally:
            release.set()
        result = call.result(10)
    if cached and change == "pause":
        assert result.status_code == 202 and result.headers["Idempotent-Replayed"] == "true"
    else:
        assert result.status_code in (401, 403, 404, 409), result.text
    assert counts(e["store"]) == ((1, 1) if cached else (0, 0))


def test_unkeyed_service_checks_subscription_after_acquiring_writer(redelivery, monkeypatch):
    e = redelivery
    other = Store(e["store"].root)
    svc = W.Subscriptions(other)
    entered, release = Event(), Event()
    actual = Store.tx

    @contextmanager
    def waiting(store):
        if store is other:
            entered.set()
            assert release.wait(5)
        with actual(store) as tx:
            yield tx

    monkeypatch.setattr(Store, "tx", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        call = pool.submit(svc.redeliver, "default", e["sid"], e["did"])
        try:
            assert entered.wait(3)
            e["store"].execute("UPDATE webhook_subscriptions SET status='paused' WHERE id=?", (e["sid"],))
        finally:
            release.set()
        from synwevo_core.errors import ServiceError
        with pytest.raises(ServiceError) as caught:
            call.result(5)
    assert caught.value.code == "subscription_not_active"
    assert counts(e["store"]) == (0, 0)
