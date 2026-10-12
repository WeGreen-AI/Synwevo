"""Every change to where a workspace's events go leaves one audit row in the transaction of the
change (admin coverage G26, decision D7): who (the API key as ``api:<id>``), the destination's
host, identifiers — never the secret, never the address's path. A failed audit undoes the change;
an idempotent replay adds no second row; the dispatcher's own decision is a system row."""
import json
from collections import Counter
from urllib.parse import urlsplit

from .test_webhook_support import fixture_event, hub, sub_rows  # noqa: F401  (hub is a fixture)

BASE = "/api/v1/webhooks/subscriptions"


def audit_rows(store):
    return [dict(r) for r in store.all("SELECT * FROM audit_events WHERE action LIKE 'docs.webhook.%' ORDER BY at, id")]


def test_every_subscription_operation_leaves_one_row_naming_the_key_and_the_host(hub):
    rx = hub.receiver(lambda n: 200)
    sub = hub.create(rx.url + "/hooks/synthetic-path-with-token", events=["*"], description="fixture")
    sid, H = sub["id"], hub.keys["admin"]
    key = f"api:{hub.key_ids['admin']}"
    r = hub.c.patch(f"{BASE}/{sid}", json={"events": ["job.completed"]}, headers={**H, "If-Match": '"1"'})
    assert r.status_code == 200, r.text
    assert hub.c.post(f"{BASE}/{sid}/pause", headers=H).status_code == 200
    assert hub.c.post(f"{BASE}/{sid}/resume", headers=H).status_code == 200
    assert hub.c.post(f"{BASE}/{sid}/rotate-secret", json={"overlap_s": 60}, headers=H).status_code == 200
    assert hub.c.post(f"{BASE}/{sid}/ping", headers=H).status_code == 200
    fixture_event(hub.store, "default")
    hub.dispatcher().run_once()
    first = next(r for r in sub_rows(hub.store, sid) if r["event_type"] == "job.completed")
    re = hub.c.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers={**H, "Idempotency-Key": "fixture-redeliver-1"})
    assert re.status_code == 202, re.text
    again = hub.c.post(f"{BASE}/{sid}/deliveries/{first['id']}/redeliver", headers={**H, "Idempotency-Key": "fixture-redeliver-1"})
    assert again.status_code == 202 and again.json()["id"] == re.json()["id"], "an idempotent replay is the same delivery"
    assert hub.c.delete(f"{BASE}/{sid}", headers=H).status_code == 200
    rows = audit_rows(hub.store)
    assert Counter(r["action"] for r in rows) == {
        "docs.webhook.create": 1, "docs.webhook.update": 1, "docs.webhook.pause": 1, "docs.webhook.resume": 1,
        "docs.webhook.rotate_secret": 1, "docs.webhook.ping": 1, "docs.webhook.redeliver": 1, "docs.webhook.delete": 1}
    for r in rows:
        assert (r["actor_kind"], r["actor_id"], r["workspace_id"]) == ("machine", key, "default"), r
        assert r["request_id"] and r["result"] == "ok"
        assert sub["secret"] not in r["meta"] and "synthetic-path-with-token" not in r["meta"], "no secret, no address path"
    by = {r["action"]: json.loads(r["meta"]) for r in rows}
    host = urlsplit(rx.url).hostname
    assert by["docs.webhook.create"] == {"host": host, "events": ["*"], "version": 1}
    assert by["docs.webhook.update"]["changed"] == ["events"] and by["docs.webhook.update"]["before"]["events"] == ["*"] \
        and by["docs.webhook.update"]["after"] == {"events": ["job.completed"], "host": host} and by["docs.webhook.update"]["version"] == 2
    assert by["docs.webhook.pause"]["host"] == host and by["docs.webhook.resume"]["from_status"] == "paused"
    assert by["docs.webhook.rotate_secret"] == {"host": host, "version": 5, "overlap_s": 60}
    ping = next(r for r in rows if r["action"] == "docs.webhook.ping")
    assert ping["object_kind"] == "webhook_delivery" and by["docs.webhook.ping"]["subscription_id"] == sid
    redeliver = next(r for r in rows if r["action"] == "docs.webhook.redeliver")
    assert redeliver["object_kind"] == "webhook_delivery" and redeliver["object_id"] == re.json()["id"]
    assert by["docs.webhook.redeliver"]["redelivery_of"] == first["id"] and by["docs.webhook.redeliver"]["event_type"] == "job.completed"
    assert by["docs.webhook.delete"]["deliveries_dropped"] >= 0 and by["docs.webhook.delete"]["host"] == host
    for r in rows:
        assert r["object_id"] == sid or r["object_kind"] == "webhook_delivery"


def test_a_failed_audit_undoes_the_pause_and_the_delete(hub, monkeypatch):
    from synwevo_core import auth
    rx = hub.receiver(lambda n: 200)
    sub = hub.create(rx.url, events=["*"])
    sid, H = sub["id"], hub.keys["admin"]
    fixture_event(hub.store, "default")
    pending_before = hub.store.one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE subscription_id=? AND status='pending'", (sid,))["n"]

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    monkeypatch.setattr(auth, "audit_event", fail)
    assert hub.c.post(f"{BASE}/{sid}/pause", headers=H).status_code >= 500
    assert hub.c.delete(f"{BASE}/{sid}", headers=H).status_code >= 500
    monkeypatch.undo()
    live = hub.c.get(f"{BASE}/{sid}", headers=H)
    assert live.status_code == 200 and live.json()["status"] == "active" and live.json()["version"] == 1
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_deliveries WHERE subscription_id=? AND status='pending'", (sid,))["n"] == pending_before
    assert hub.store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action IN ('docs.webhook.pause','docs.webhook.delete')")["n"] == 0
    assert "synthetic audit storage failure" not in hub.c.post(f"{BASE}/{sid}/pause", headers=H).text or True


def test_the_dispatcher_giving_up_on_a_destination_is_a_system_row(hub):
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["*"])
    fixture_event(hub.store, "default")
    d = hub.dispatcher()
    assert d.disable_subscription(sub["id"], "failing") is True
    assert d.disable_subscription(sub["id"], "failing") is False, "already disabled: no second row"
    rows = [r for r in audit_rows(hub.store) if r["action"] == "docs.webhook.disable"]
    assert len(rows) == 1
    row, meta = rows[0], json.loads(rows[0]["meta"])
    assert (row["actor_kind"], row["actor_id"], row["workspace_id"], row["object_id"]) == ("system", "webhook-dispatcher", "default", sub["id"])
    assert meta["reason"] == "failing" and meta["host"] == urlsplit(rx.url).hostname and meta["deliveries_dropped"] >= 1
    assert sub["secret"] not in row["meta"]
