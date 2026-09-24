"""Webhook subscriptions through /api/v1: scope, workspace isolation, secrets shown once,
optimistic concurrency, the per-workspace cap, and address validation (SSRF) at creation."""
import re
import socket

from synwevo_docs import webhooks as W

from .test_webhook_support import hub  # noqa: F401  (fixture)

BASE = "/api/v1/webhooks/subscriptions"


def test_management_needs_admin_scope(hub):
    rx = hub.receiver()
    for key in ("ro", "rw"):
        r = hub.c.post(BASE, json={"url": rx.url}, headers=hub.keys[key])
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", key
        assert hub.c.get(BASE, headers=hub.keys[key]).status_code == 403
    sub = hub.create(rx.url)
    for key in ("ro", "rw"):
        h = hub.keys[key]
        assert hub.c.get(f"{BASE}/{sub['id']}", headers=h).status_code == 403
        assert hub.c.patch(f"{BASE}/{sub['id']}", json={"description": "x"}, headers=h).status_code == 403
        assert hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={}, headers=h).status_code == 403
        assert hub.c.post(f"{BASE}/{sub['id']}/ping", headers=h).status_code == 403
        assert hub.c.get(f"{BASE}/{sub['id']}/deliveries", headers=h).status_code == 403
        assert hub.c.delete(f"{BASE}/{sub['id']}", headers=h).status_code == 403
    assert hub.c.get(BASE).status_code == 401


def test_secret_is_returned_only_at_creation_and_rotation(hub):
    rx = hub.receiver()
    r = hub.c.post(BASE, json={"url": rx.url, "description": "n8n 生产"}, headers=hub.keys["admin"])
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    sub = r.json()
    assert re.fullmatch(r"whsec_[A-Za-z0-9+/]{43}=", sub["secret"])
    import base64
    assert len(base64.b64decode(sub["secret"][6:])) == 32
    assert sub["events"] == list(W.DEFAULT_EVENTS) and sub["status"] == "active" and sub["version"] == 1
    assert sub["authorized_by"] == f"api:{hub.key_ids['admin']}"
    got = hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"])
    assert "secret" not in got.json() and got.headers["etag"] == '"1"'
    listed = hub.c.get(BASE, headers=hub.keys["admin"]).json()["items"]
    assert [s["id"] for s in listed] == [sub["id"]] and all("secret" not in s for s in listed)
    # Nothing that looks like the secret is stored in clear.
    row = hub.store.one("SELECT * FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert sub["secret"] not in str(row)
    rot = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": 600}, headers=hub.keys["admin"])
    assert rot.status_code == 200 and rot.headers["cache-control"] == "no-store"
    new = rot.json()
    assert new["secret"].startswith("whsec_") and new["secret"] != sub["secret"]
    assert new["previous_secret_expires_at"] and new["version"] == 2
    assert "secret" not in hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).json()


def test_another_workspace_sees_nothing(hub):
    rx = hub.receiver()
    mine = hub.create(rx.url)
    theirs = hub.create(rx.url, key="b_admin")
    b = hub.keys["b_admin"]
    assert [s["id"] for s in hub.c.get(BASE, headers=b).json()["items"]] == [theirs["id"]]
    for method, path, kw in (("get", f"{BASE}/{mine['id']}", {}),
                             ("patch", f"{BASE}/{mine['id']}", {"json": {"description": "x"}}),
                             ("post", f"{BASE}/{mine['id']}/pause", {}),
                             ("post", f"{BASE}/{mine['id']}/resume", {}),
                             ("post", f"{BASE}/{mine['id']}/rotate-secret", {"json": {}}),
                             ("post", f"{BASE}/{mine['id']}/ping", {}),
                             ("get", f"{BASE}/{mine['id']}/deliveries", {}),
                             ("delete", f"{BASE}/{mine['id']}", {})):
        r = getattr(hub.c, method)(path, headers=b, **kw)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found", (method, path, r.text)
    # and nothing about it changed
    assert hub.c.get(f"{BASE}/{mine['id']}", headers=hub.keys["admin"]).json()["version"] == 1
    assert rx.received == []


def test_patch_uses_optimistic_concurrency(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url)
    path, h = f"{BASE}/{sub['id']}", hub.keys["admin"]
    r = hub.c.patch(path, json={"description": "旧版本"}, headers={**h, "If-Match": '"0"'})
    assert r.status_code == 409 and r.json()["error"]["code"] == "version_conflict"
    assert r.json()["error"]["details"]["current_version"] == 1
    r = hub.c.patch(path, json={"description": "改名", "version": 1}, headers=h)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.headers["etag"] == '"2"'
    # the first writer's version is stale now
    r = hub.c.patch(path, json={"events": ["job.failed"]}, headers={**h, "If-Match": 'W/"1"'})
    assert r.status_code == 409
    r = hub.c.patch(path, json={"events": ["job.failed"], "version": 1}, headers=h)
    assert r.status_code == 409
    r = hub.c.patch(path, json={"events": ["job.failed", "job.completed"]}, headers={**h, "If-Match": '"2"'})
    assert r.status_code == 200 and r.json()["events"] == ["job.failed", "job.completed"]
    # fields that have their own endpoints are refused rather than silently ignored
    r = hub.c.patch(path, json={"status": "paused"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "status"
    r = hub.c.patch(path, json={"secret": "whsec_x"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "secret"


def test_event_filter_is_validated(hub):
    rx = hub.receiver()
    r = hub.c.post(BASE, json={"url": rx.url, "events": ["job.complete"]}, headers=hub.keys["admin"])
    assert r.status_code == 400 and r.json()["error"]["field"] == "events"
    assert "job.completed" in r.json()["error"]["details"]["allowed"]
    assert hub.c.post(BASE, json={"url": rx.url, "events": []}, headers=hub.keys["admin"]).status_code == 400
    assert hub.create(rx.url, events=["*"])["events"] == ["*"]


def test_at_most_sixteen_per_workspace(hub):
    rx = hub.receiver()
    for i in range(W.MAX_SUBSCRIPTIONS):
        hub.create(rx.url, description=f"#{i}")
    r = hub.c.post(BASE, json={"url": rx.url}, headers=hub.keys["admin"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "subscription_limit_reached"
    # the cap is per workspace, and a deleted subscription frees its slot
    hub.create(rx.url, key="b_admin")
    first = hub.c.get(BASE, headers=hub.keys["admin"]).json()["items"][0]
    assert hub.c.delete(f"{BASE}/{first['id']}", headers=hub.keys["admin"]).status_code == 200
    hub.create(rx.url)


def test_delete_stops_it_and_is_journalled(hub):
    from .test_webhook_support import journal_entries
    rx = hub.receiver()
    sub = hub.create(rx.url)
    assert hub.c.delete(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).json() == {"deleted": sub["id"]}
    assert hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).status_code == 404
    assert hub.c.delete(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).status_code == 404
    assert {"kind": "webhook_disabled", "subscription_id": sub["id"]}.items() <= journal_entries(hub.store)[-1].items()


# ------------------------------------------------------------------ SSRF at creation
def test_metadata_and_private_addresses_are_refused_at_creation(hub, monkeypatch):
    h = hub.keys["admin"]
    r = hub.c.post(BASE, json={"url": "http://169.254.169.254/latest/meta-data/"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_url" and r.json()["error"]["field"] == "url"
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE")
    r = hub.c.post(BASE, json={"url": "http://127.0.0.1:9/hook"}, headers=h)
    assert r.status_code == 400 and "内网" in r.json()["error"]["message"]
    r = hub.c.post(BASE, json={"url": "file:///etc/passwd"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "url"
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP")
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))])
    r = hub.c.post(BASE, json={"url": "http://example.com/hook"}, headers=h)
    assert r.status_code == 400 and "https" in r.json()["error"]["message"]
    r = hub.c.post(BASE, json={"url": "https://user:pw@example.com/hook"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["field"] == "url"
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_subscriptions")["n"] == 0


def test_a_hostname_resolving_to_a_private_address_is_refused(hub, monkeypatch):
    with monkeypatch.context() as m:
        m.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE")
        m.setattr(socket, "getaddrinfo",
                  lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443))])
        r = hub.c.post(BASE, json={"url": "https://innocent.example.com/hook"}, headers=hub.keys["admin"])
        assert r.status_code == 400 and r.json()["error"]["field"] == "url"
        assert "10.1.2.3" not in r.text
    # the metadata address is refused on update too
    rx = hub.receiver()
    sub = hub.create(rx.url)
    r = hub.c.patch(f"{BASE}/{sub['id']}", json={"url": "http://169.254.169.254/"}, headers=hub.keys["admin"])
    assert r.status_code == 400 and r.json()["error"]["field"] == "url"


def test_rotation_overlap_is_bounded(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url)
    for bad in (-1, 86401, "soon"):
        r = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": bad}, headers=hub.keys["admin"])
        assert r.status_code == 400 and r.json()["error"]["field"] == "overlap_s", bad
    r = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": 0}, headers=hub.keys["admin"])
    assert r.status_code == 200 and r.json()["previous_secret_expires_at"] is None


def test_pause_resume_and_resume_reattributes(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url)
    path, h = f"{BASE}/{sub['id']}", hub.keys["admin"]
    assert hub.c.post(f"{path}/pause", headers=h).json()["status"] == "paused"
    assert hub.c.post(f"{path}/pause", headers=h).json()["status"] == "paused"      # idempotent
    tok, row = hub.auth.create_key("second admin", "default", ("admin",))
    resumed = hub.c.post(f"{path}/resume", headers={"Authorization": f"Bearer {tok}"}).json()
    assert resumed["status"] == "active" and resumed["authorized_by"] == f"api:{row['id']}"


def test_webhook_openapi_scopes_match_enforcement(hub):
    paths = hub.c.app.openapi()["paths"]
    for path, methods in paths.items():
        if path.startswith("/api/v1/webhooks/") or path == "/api/v1/events":
            for method, operation in methods.items():
                if method in ("get", "post", "patch", "delete"):
                    assert operation["x-scope"] == ("read" if path.endswith("/events") else "admin")


def test_secret_producing_posts_refuse_unsupported_idempotency_before_mutation(hub):
    rx = hub.receiver()
    h = {**hub.keys["admin"], "Idempotency-Key": "fixture-retry"}
    r = hub.c.post(BASE, json={"url": rx.url}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_not_supported"
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_subscriptions")["n"] == 0
    sub = hub.create(rx.url)
    r = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "idempotency_not_supported"
    assert hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).json()["version"] == 1
