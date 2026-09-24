"""Delivery log, manual redelivery and ping through /api/v1: same webhook-id on redelivery, a
bounded redelivery window, cursor pagination without duplicates, scope and workspace checks."""
import json
import time

from .test_webhook_support import fixture_event, hub, signed_by, sub_rows  # noqa: F401  (hub is a fixture)

BASE = "/api/v1/webhooks/subscriptions"


def test_redelivery_keeps_the_webhook_id(hub, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_BACKOFF_S", "0,0")      # a short schedule for the test
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    ev = fixture_event(hub.store, "default")
    for _ in range(4):
        d.run_once()
    first = sub_rows(hub.store, sub["id"])[0]
    assert first["status"] == "failed" and first["attempts"] == 3
    # The receiver is fixed; the integrator asks for the event again.
    rx.behaviour = lambda n: 200
    r = hub.c.post(f"{BASE}/{sub['id']}/deliveries/{first['id']}/redeliver", headers=hub.keys["admin"])
    assert r.status_code == 202
    again = r.json()
    assert again["id"] != first["id"] and again["webhook_id"] == ev["id"]
    assert again["redelivery"] is True and again["redelivery_of"] == first["id"] and again["status"] == "pending"
    d.run_once()
    last = rx.received[-1]
    assert last["headers"]["webhook-id"] == ev["id"] == json.loads(last["body"])["id"]
    assert last["headers"]["x-synwevo-delivery"] == again["id"] != first["id"]
    assert signed_by(sub["secret"], last)
    got = hub.c.get(f"{BASE}/{sub['id']}/deliveries/{again['id']}", headers=hub.keys["admin"]).json()
    assert got["status"] == "delivered" and got["attempts"] == 1 and got["last_code"] == 200
    # A second redelivery of the same event is its own row too.
    r2 = hub.c.post(f"{BASE}/{sub['id']}/deliveries/{first['id']}/redeliver", headers=hub.keys["admin"]).json()
    assert r2["id"] not in (first["id"], again["id"]) and r2["webhook_id"] == ev["id"]


def test_redelivery_window_is_seven_days(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    old = fixture_event(hub.store, "default", occurred_at=time.time() - 8 * 86400)
    t = time.time()
    hub.store.execute("INSERT INTO webhook_deliveries (id, event_id, endpoint, status, attempts, created_at, subscription_id, "
                      "tenant, event_type) VALUES ('wh_old', ?, ?, 'failed', 10, ?, ?, 'default', 'job.completed')",
                      (old["id"], f"sub:{sub['id']}", t, sub["id"]))                      # TEST FIXTURE
    r = hub.c.post(f"{BASE}/{sub['id']}/deliveries/wh_old/redeliver", headers=hub.keys["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "redelivery_window_expired"
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE event_id=?", (old["id"],))["n"] == 1


def test_redelivery_needs_admin_scope_and_the_right_workspace(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    did = sub_rows(hub.store, sub["id"])[0]["id"]
    path = f"{BASE}/{sub['id']}/deliveries/{did}/redeliver"
    for key in ("ro", "rw"):
        r = hub.c.post(path, headers=hub.keys[key])
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert hub.c.post(path, headers=hub.keys["b_admin"]).status_code == 404
    assert hub.c.get(f"{BASE}/{sub['id']}/deliveries/{did}", headers=hub.keys["b_admin"]).status_code == 404
    # a delivery id that belongs to another subscription is not reachable through this one
    other = hub.create(rx.url, events=["*"])
    assert hub.c.get(f"{BASE}/{other['id']}/deliveries/{did}", headers=hub.keys["admin"]).status_code == 404
    # a paused subscription cannot be redelivered to
    hub.c.post(f"{BASE}/{sub['id']}/pause", headers=hub.keys["admin"])
    r = hub.c.post(path, headers=hub.keys["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "subscription_not_active"


def test_delivery_log_pages_without_duplicates(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    t = time.time()
    with hub.store.tx() as c:                                                             # TEST FIXTURE rows
        for i in range(150):
            c.execute("INSERT INTO webhook_deliveries (id, event_id, endpoint, status, attempts, created_at, "
                      "subscription_id, tenant, event_type, last_code) VALUES (?,?,?,?,1,?,?,'default','job.completed',?)",
                      (f"wh_fx{i:03d}", f"ev_fx{i:03d}", f"sub:{sub['id']}", "failed" if i % 3 == 0 else "delivered",
                       t + i, sub["id"], 500 if i % 3 == 0 else 200))
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 40, **({"cursor": cursor} if cursor else {})}
        page = hub.c.get(f"{BASE}/{sub['id']}/deliveries", params=params, headers=hub.keys["admin"]).json()
        seen += [x["id"] for x in page["items"]]
        pages += 1
        if not page["has_more"]:
            assert page["next_cursor"] is None
            break
        cursor = page["next_cursor"]
    assert pages == 4 and len(seen) == 150 and len(set(seen)) == 150
    assert seen[0] == "wh_fx149"                                                          # newest first
    failed = hub.c.get(f"{BASE}/{sub['id']}/deliveries", params={"status": "failed", "limit": 100},
                       headers=hub.keys["admin"]).json()
    assert len(failed["items"]) == 50 and {x["status"] for x in failed["items"]} == {"failed"}
    item = failed["items"][0]
    assert set(item) >= {"id", "webhook_id", "event_type", "status", "attempts", "last_code", "duration_ms",
                         "created_at", "delivered_at", "redelivery"}
    for bad in ({"limit": 101}, {"limit": 0}, {"status": "weird"}, {"cursor": "not-a-cursor"}):
        r = hub.c.get(f"{BASE}/{sub['id']}/deliveries", params=bad, headers=hub.keys["admin"])
        assert r.status_code == 400, bad


def test_ping_sends_one_signed_event_now(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url)
    r = hub.c.post(f"{BASE}/{sub['id']}/ping", headers=hub.keys["admin"])
    assert r.status_code == 200
    d = r.json()
    assert d["event_type"] == "webhook.ping" and d["status"] == "delivered" and d["last_code"] == 200
    assert len(rx.received) == 1
    body = json.loads(rx.received[0]["body"])
    assert body["type"] == "webhook.ping" and body["data"] == {"subscription_id": sub["id"]} and body["job"] is None
    assert rx.received[0]["headers"]["webhook-id"] == body["id"] == d["webhook_id"]
    assert signed_by(sub["secret"], rx.received[0])
    # Pinging is allowed while paused (to test before resuming), not once disabled.
    hub.c.post(f"{BASE}/{sub['id']}/pause", headers=hub.keys["admin"])
    assert hub.c.post(f"{BASE}/{sub['id']}/ping", headers=hub.keys["admin"]).json()["status"] == "delivered"
    hub.store.execute("UPDATE webhook_subscriptions SET status='disabled', disabled_reason='gone' WHERE id=?", (sub["id"],))
    r = hub.c.post(f"{BASE}/{sub['id']}/ping", headers=hub.keys["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "subscription_disabled"


def test_a_failed_ping_is_one_attempt_and_does_not_count_against_the_subscription(hub):
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url)
    d = hub.c.post(f"{BASE}/{sub['id']}/ping", headers=hub.keys["admin"]).json()
    assert d["status"] == "failed" and d["attempts"] == 1 and d["last_code"] == 500
    got = hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).json()
    assert got["consecutive_failures"] == 0 and got["status"] == "active"
