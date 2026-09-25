"""Web Push for the inbox, against a push-service test double: the VAPID key is stable, a
device subscribes and is bound to its session, a test push carries the RFC 8292 authorization
and the aes128gcm body, an inbox item is pushed once to every live device in the reader's
language, a device the push service reports gone is revoked, quiet hours defer, a category
switched off is skipped, a push service that cannot be reached retries, and signing out ends
the device's subscription. No real push service is contacted."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from synwevo_core.notifications import Notifier
from synwevo_core.push import PushSubscriptions

PW = "harbor lights guide the evening ferry home"


class PushDouble:
    """Accepts pushes like a push service would and remembers them; the status can be steered
    per endpoint path (…/gone → 410, …/busy → 429, otherwise 201)."""

    def __init__(self):
        self.requests: list[dict] = []
        double = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                double.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                code = 410 if self.path.endswith("/gone") else 429 if self.path.endswith("/busy") else 201
                self.send_response(code); self.send_header("Content-Length", "0"); self.end_headers()

            def log_message(self, *a):  # noqa: D401 - quiet
                pass
        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown(); self.server.server_close()


def browser_subscription(endpoint: str) -> dict:
    """What PushManager.subscribe().toJSON() gives: an endpoint and the two client keys."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return {"endpoint": endpoint, "keys": {"p256dh": b64(pub), "auth": b64(b"0123456789abcdef")}}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                 "MAIL_FROM": "synwevo@example.test", "PUBLIC_URL": "http://testserver", "ISSUER": "http://testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    u = ident.create_user("reader@example.test", PW, status="active", display_name="Reader", locale="en")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
    double = PushDouble()
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": "reader@example.test", "password": PW}).status_code == 200
    c.headers.update({"X-Synwevo-CSRF": c.cookies.get("dw_csrf")})
    try:
        yield {"api": api, "c": c, "uid": u["id"], "double": double, "store": ident.store}
    finally:
        c.close(); double.close()


def test_subscribe_test_push_and_remove(world):
    c, d = world["c"], world["double"]
    cfg = c.get("/api/auth/push/config").json()
    assert cfg["enabled"] and len(cfg["public_key"]) > 80 and c.get("/api/auth/push/config").json()["public_key"] == cfg["public_key"], "one key, stable"
    sub = browser_subscription(f"{d.base}/push/device-1")
    r = c.post("/api/auth/push/subscriptions", json={"subscription": sub})
    assert r.status_code == 201, r.text
    dev = r.json()
    assert dev["this_device"] and dev["endpoint_hash"] and "endpoint" not in dev and "keys" not in dev
    listed = c.get("/api/auth/push/subscriptions", params={"endpoint_hash_of": dev["endpoint_hash"]}).json()["items"]
    assert len(listed) == 1 and listed[0]["this_device"] is True and listed[0]["id"] == dev["id"]
    # the same endpoint again is the same device, not a second row
    assert c.post("/api/auth/push/subscriptions", json={"subscription": sub}).json()["id"] == dev["id"]
    # a test push: VAPID authorization for the push origin, aes128gcm body, TTL and urgency
    assert c.post(f"/api/auth/push/subscriptions/{dev['id']}/test", json={}).json()["ok"] is True
    req = d.requests[-1]
    assert req["path"] == "/push/device-1"
    assert req["headers"]["authorization"].startswith("vapid t=") and ",k=" in req["headers"]["authorization"]
    assert req["headers"]["content-encoding"] == "aes128gcm" and req["headers"]["ttl"] == "60" and req["headers"]["urgency"] == "high"
    assert len(req["body"]) > 100 and b"push test" not in req["body"], "the payload is encrypted, not readable by the push service"
    # bad subscriptions are refused; not a leak of what exists
    assert c.post("/api/auth/push/subscriptions", json={"subscription": {"endpoint": "ftp://x", "keys": {}}}).status_code == 400
    assert c.post("/api/auth/push/subscriptions", json={"subscription": {"endpoint": f"{d.base}/x", "keys": {"p256dh": "AAAA", "auth": "AA"}}}).status_code == 400
    # remove
    assert c.delete(f"/api/auth/push/subscriptions/{dev['id']}").json()["ok"]
    assert c.get("/api/auth/push/subscriptions").json()["items"] == []
    assert c.post(f"/api/auth/push/subscriptions/{dev['id']}/test", json={}).status_code == 404


def test_inbox_items_are_pushed_to_live_devices_and_gone_devices_are_revoked(world):
    api, c, d, uid = world["api"], world["c"], world["double"], world["uid"]
    n: Notifier = api.state.notifier
    phone = c.post("/api/auth/push/subscriptions", json={"subscription": browser_subscription(f"{d.base}/push/phone")}).json()
    laptop = c.post("/api/auth/push/subscriptions", json={"subscription": browser_subscription(f"{d.base}/push/gone")}).json()
    n.emit(uid, "copy.approved", params={"project": "Contracts", "decider": "Bob", "reason": "fine"}, dedupe_key="t1",
           target={"origin": "accounts", "path": "/organizations/o_1?approval=cp_1"})
    out = api.state.notify_dispatcher.run_once()
    assert out["pushed"] == 1, out
    paths = sorted(r["path"] for r in d.requests)
    assert paths == ["/push/gone", "/push/phone"], "every live device, once"
    hdr = next(r for r in d.requests if r["path"] == "/push/phone")["headers"]
    assert hdr["urgency"] == "normal" and hdr["ttl"] == str(24 * 3600)
    # the laptop's push service said 410: that device is revoked, the phone stays
    devices = {x["id"]: x for x in c.get("/api/auth/push/subscriptions").json()["items"]}
    assert phone["id"] in devices and laptop["id"] not in devices
    q = world["store"].one("SELECT status FROM push_queue WHERE notification_id=(SELECT id FROM notifications WHERE user_id=? AND dedupe_key='t1')", (uid,))
    assert q["status"] == "sent"
    # a second run does nothing: the item was pushed once
    before = len(d.requests)
    assert api.state.notify_dispatcher.run_once()["pushed"] == 0 and len(d.requests) == before


def test_preferences_quiet_hours_and_retries(world, monkeypatch):
    api, c, d, uid = world["api"], world["c"], world["double"], world["uid"]
    n: Notifier = api.state.notifier
    c.post("/api/auth/push/subscriptions", json={"subscription": browser_subscription(f"{d.base}/push/phone")})
    # the docs category's push switch off: no queue row; security cannot be switched off
    n.set_preferences(uid, {"categories": {"docs": {"push": False}, "security": {"push": False}}})
    n.emit(uid, "docs.job_finished", params={"project": "P", "outcome": "finished", "done": "1", "failed": "0"}, dedupe_key="j1",
           target={"origin": "docs", "path": "/p/p_1/jobs/j_1"})
    n.emit(uid, "security.change", params={"what": "password_changed", "when": "now", "where": "here"}, dedupe_key="s1")
    kinds = {r["kind"]: r for r in world["store"].all("SELECT n.kind, q.status FROM push_queue q JOIN notifications n ON n.id=q.notification_id WHERE q.user_id=?", (uid,))}
    assert "docs.job_finished" not in kinds and "security.change" in kinds
    assert api.state.notify_dispatcher.run_once()["pushed"] == 1
    # quiet hours: an ordinary item waits, a critical one does not
    from datetime import datetime
    from zoneinfo import ZoneInfo
    hh = datetime.now(ZoneInfo("Asia/Shanghai")).hour
    n.set_preferences(uid, {"quiet_start": f"{hh:02d}:00", "quiet_end": f"{(hh + 2) % 24:02d}:00", "timezone": "Asia/Shanghai"})
    n.emit(uid, "copy.done", params={"project": "P", "workspace": "W"}, dedupe_key="c1", target={"origin": "docs", "path": "/p/p_2"})
    n.emit(uid, "security.change", params={"what": "password_changed", "when": "now", "where": "here"}, dedupe_key="s2")
    out = api.state.notify_dispatcher.run_once()
    assert out["push_deferred"] == 1 and out["pushed"] == 1
    n.set_preferences(uid, {"quiet_start": None, "quiet_end": None, "timezone": "Asia/Shanghai"})
    # a push service that cannot be reached: the item goes back with backoff, the device is kept
    n.emit(uid, "copy.done", params={"project": "P2", "workspace": "W"}, dedupe_key="c2", target={"origin": "docs", "path": "/p/p_3"})
    d.server.shutdown(); d.server.server_close()
    out = api.state.notify_dispatcher.run_once()
    assert out["push_failed"] >= 1
    rows = world["store"].all("SELECT status, attempts, not_before FROM push_queue q JOIN notifications n ON n.id=q.notification_id WHERE n.dedupe_key='c2'")
    assert rows[0]["status"] == "pending" and rows[0]["attempts"] == 1 and rows[0]["not_before"] > 0
    assert c.get("/api/auth/push/subscriptions").json()["items"], "an unreachable push service is not the device's fault"


def test_signing_out_ends_this_sessions_devices(world):
    c, d, uid = world["c"], world["double"], world["uid"]
    c.post("/api/auth/push/subscriptions", json={"subscription": browser_subscription(f"{d.base}/push/phone")})
    assert PushSubscriptions(world["store"]).has_active(uid)
    assert c.post("/api/auth/logout", json={}).status_code == 200
    assert not PushSubscriptions(world["store"]).has_active(uid)
    row = world["store"].one("SELECT revoked_reason FROM push_subscriptions WHERE user_id=?", (uid,))
    assert row["revoked_reason"] == "signed_out"
