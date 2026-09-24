"""Dispatcher hardening: one slow or dead receiver cannot hold the loop, a send never outlives its
claim, an attempt in flight cannot undo a pause, the restore fence is never crossed, and
workspace-facing payloads carry no exception text or nested links."""
import json
import socket
import threading
import time

import pytest

from synwevo_docs import webhooks as W

from .test_webhook_support import fixture_event, hub, sub_rows  # noqa: F401  (hub is a fixture)

BASE = "/api/v1/webhooks/subscriptions"


class Trickler:
    """TEST FIXTURE: a loopback HTTP server that reads the request and then sends its response
    headers one byte at a time — never idle long enough for a per-read socket timeout to fire."""

    def __init__(self, seconds: float, gap: float = 0.25):
        self.seconds, self.gap = seconds, gap
        self.requests = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.url = f"http://127.0.0.1:{self.sock.getsockname()[1]}/hook"
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn):
        try:
            conn.settimeout(10)
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            self.requests += 1
            conn.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
            end = time.monotonic() + self.seconds
            while time.monotonic() < end:
                conn.sendall(b"a")
                time.sleep(self.gap)
            conn.sendall(b"\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    def stop(self):
        self._stop = True
        self.sock.close()


# ------------------------------------------------------------------ the loop is not held hostage
def test_a_dead_receiver_does_not_hold_the_loop_or_other_workspaces(hub, monkeypatch):
    """Reviewer repro: 8 deliveries to a receiver that never answers in time kept deliver_due()
    busy for 8 x timeout, and workspace B's event waited behind all of them."""
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_TIMEOUT_S", "1")
    dead = hub.receiver(delay=4)
    live = hub.receiver()
    a = hub.create(dead.url, events=["*"])
    hub.create(live.url, key="b_admin", events=["*"])
    d = hub.dispatcher(concurrency=2)
    for _ in range(8):
        fixture_event(hub.store, "default")
    started = time.monotonic()
    d.run_once()
    first_round = time.monotonic() - started
    assert first_round < 5, f"一个不应答的接收端占住了整轮投递 {first_round:.1f}s"
    # Workspace B's event, happening now, goes out on the next round without waiting for A.
    ev_b = fixture_event(hub.store, "tenant-b")
    started = time.monotonic()
    d.run_once()
    assert time.monotonic() - started < 2
    assert [b["id"] for b in live.bodies()] == [ev_b["id"]]
    # A's other events were not spent: one attempt was made, the rest wait for the destination.
    rows = sub_rows(hub.store, a["id"])
    assert sorted(r["attempts"] for r in rows) == [0] * 7 + [1]
    assert all(r["status"] == "pending" for r in rows)
    # Once its back-off has passed the destination is tried again.
    hub.clock.advance(3600)
    dead.delay = 0
    for _ in range(3):
        d.run_once()
        hub.clock.advance(3600)
    assert all(r["status"] == "delivered" for r in sub_rows(hub.store, a["id"]))


def test_a_round_stops_claiming_after_its_deadline(hub, monkeypatch):
    """A slow but healthy receiver is served repeatedly within a round, but not forever: a round
    ends and the loop gets to enqueue new events and heartbeat."""
    monkeypatch.setattr(W, "ROUND_S", 1.0, raising=False)
    rx = hub.receiver(delay=0.4)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    for _ in range(10):
        fixture_event(hub.store, "default")
    started = time.monotonic()
    d.run_once()
    took = time.monotonic() - started
    assert took < 2.5, f"一轮投递持续了 {took:.1f}s"
    assert 1 <= len(rx.received) < 10
    for _ in range(10):
        d.run_once()
    assert all(r["status"] == "delivered" for r in sub_rows(hub.store, sub["id"]))


def test_a_destination_with_a_backlog_does_not_starve_another(hub, monkeypatch):
    """Within a round every destination with due work gets a turn before any gets a second."""
    monkeypatch.setattr(W, "ROUND_S", 60.0, raising=False)
    big, small = hub.receiver(), hub.receiver()
    hub.create(big.url, events=["*"])
    s = hub.create(small.url, events=["job.failed"])
    d = hub.dispatcher()
    for _ in range(30):
        fixture_event(hub.store, "default", "job.completed")
    ev = fixture_event(hub.store, "default", "job.failed")
    d.enqueue_new_events()
    d.deliver_due(limit=5)
    assert [b["id"] for b in small.bodies()] == [ev["id"]]
    assert sub_rows(hub.store, s["id"])[0]["status"] == "delivered"


# ------------------------------------------------------------------ a send never outlives its claim
def test_a_trickling_receiver_is_cut_off_at_the_timeout(hub, monkeypatch):
    """urllib's timeout applies to each socket read; a receiver that sends one header byte every
    quarter second kept a send going indefinitely, past the claim."""
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_TIMEOUT_S", "1")
    tr = Trickler(seconds=5)
    try:
        sub = hub.create(tr.url, events=["*"])
        d = hub.dispatcher()
        fixture_event(hub.store, "default")
        started = time.monotonic()
        d.run_once()
        took = time.monotonic() - started
    finally:
        tr.stop()
    assert tr.requests == 1
    assert took < 3, f"一次发送持续了 {took:.1f}s，超过了 1s 的总时限"
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and "超时" in row["last_error"]


def test_a_hanging_resolver_counts_against_the_timeout(hub, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_TIMEOUT_S", "1")
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    real = socket.getaddrinfo

    def slow(*a, **k):
        time.sleep(3)
        return real(*a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", slow)
    started = time.monotonic()
    d.run_once()
    took = time.monotonic() - started
    assert took < 2.5, f"域名解析没有时限：{took:.1f}s"
    assert rx.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and "无法解析" in row["last_error"]


def test_a_send_checks_it_still_holds_the_claim(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher(worker_id="worker-1")
    fixture_event(hub.store, "default")
    d.enqueue_new_events()
    row = d.claim_next()
    assert row and row["claimed_by"] == "worker-1"
    # The claim expired and another worker took the row before this one got to connect.
    hub.store.execute("UPDATE webhook_deliveries SET claimed_by='worker-2', claimed_until=? WHERE id=?",
                      (hub.clock() + 100, row["id"]))
    assert d.send_one(row) is False
    assert rx.received == []
    got = sub_rows(hub.store, sub["id"])[0]
    assert got["claimed_by"] == "worker-2" and got["attempts"] == 0 and got["status"] == "pending"


# ------------------------------------------------------------------ an attempt in flight cannot undo a pause
@pytest.mark.parametrize("code", [200, 500])
@pytest.mark.parametrize("action", ["pause", "delete", "disable"])
def test_stopping_a_subscription_during_an_attempt_sticks(hub, action, code):
    svc = W.Subscriptions(hub.store)
    box = {}

    def behaviour(n):
        if n == 1:                                  # the admin acts while the request is in flight
            if action == "pause":
                svc.pause("default", box["sid"])
            elif action == "delete":
                svc.delete("default", box["sid"])
            else:
                W.WebhookDispatcher(hub.store, start=False).disable_subscription(box["sid"], "failing")
        return code

    rx = hub.receiver(behaviour)
    sub = hub.create(rx.url, events=["*"])
    box["sid"] = sub["id"]
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "dropped", row
    assert row["last_error"] in ("subscription_paused", "subscription_deleted", "subscription_disabled:failing")
    assert row["attempts"] == 1 and row["last_code"] == code and row["claimed_by"] is None
    assert row["next_attempt_at"] is None and row["delivered_at"] is None
    if action == "pause":
        hub.c.post(f"{BASE}/{sub['id']}/resume", headers=hub.keys["admin"])
    hub.clock.advance(86400)
    d.run_once()
    assert len(rx.received) == 1, "暂停/删除前的重试在恢复后又被发送了"


# ------------------------------------------------------------------ restore fence
def test_the_restore_fence_is_never_crossed(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    for _ in range(2):
        fixture_event(hub.store, "default")
    top = hub.store.one("SELECT MAX(seq) AS m FROM job_events")["m"]
    # What `snapshot.py restore` leaves behind, and then a cursor that went back behind it.
    hub.store.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (W.RESTORE_FENCE_KEY, str(top)))
    hub.store.execute("UPDATE meta SET value='0' WHERE key=?", (W.LAST_SEQ_KEY,))
    d.run_once()
    assert rx.received == [] and sub_rows(hub.store, sub["id"]) == []
    after = fixture_event(hub.store, "default")
    d.run_once()
    assert [b["id"] for b in rx.bodies()] == [after["id"]]


# ------------------------------------------------------------------ workspace-facing payloads
def test_subscription_payloads_carry_codes_not_exception_text(hub):
    rx = hub.receiver()
    hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    counts = {"total": 2, "failed": 2, "success": 0}
    fixture_event(hub.store, "default", "job.failed",
                  data={"status": "failed", "run_no": 1, "counts": counts,
                        "error": "KeyError: '客户张三 13800000000'"})
    fixture_event(hub.store, "default", "job.failed",
                  data={"status": "failed", "run_no": 1, "counts": counts, "error_code": "has_errors", "invalid": 2,
                        "error": "2 个样本存在错误，已按「有错误则不开始」停止"})
    fixture_event(hub.store, "default", "job.cancelled",
                  data={"status": "cancelled", "run_no": 1, "counts": counts,
                        "halt_reason": "authorization_revoked:credential_revoked"})
    fixture_event(hub.store, "default", "job.completed",
                  data={"status": "completed", "run_no": 1, "counts": {"total": 1},
                        "extra": {"preview_url": "https://x/p", "items": [{"token": "t", "ok": 1}]}})
    d.run_once()
    raw = b"".join(r["body"] for r in rx.received)
    assert b"13800000000" not in raw and "张三".encode() not in raw and b"preview" not in raw
    b1, b2, b3, b4 = rx.bodies()
    assert b1["data"] == {"status": "failed", "run_no": 1, "counts": counts, "error_code": "job_error"}
    assert b2["data"] == {"status": "failed", "run_no": 1, "counts": counts, "error_code": "has_errors", "invalid": 2}
    assert b3["data"]["halt_reason"] == "authorization_revoked:credential_revoked"
    assert b4["data"] == {"status": "completed", "run_no": 1, "counts": {"total": 1}, "extra": {"items": [{"ok": 1}]}}


def test_every_emitted_event_type_can_be_named_in_a_filter(hub):
    """`job.planned` was emitted by jobs.py but refused by every exact filter."""
    rx = hub.receiver()
    r = hub.c.post(BASE, json={"url": rx.url, "events": ["job.planned"]}, headers=hub.keys["admin"])
    assert r.status_code == 201, r.text
    d = hub.dispatcher()
    ev = fixture_event(hub.store, "default", "job.planned",
                       data={"status": "running", "phase": "generating", "run_no": 1, "counts": {"total": 3}})
    d.run_once()
    assert [b["id"] for b in rx.bodies()] == [ev["id"]]
    assert rx.bodies()[0]["data"]["phase"] == "generating"


def test_restored_pending_rows_are_dropped_even_without_enqueue(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.enqueue_new_events()
    top = hub.store.one("SELECT MAX(seq) AS m FROM job_events")["m"]
    hub.store.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (W.RESTORE_FENCE_KEY, str(top)))
    d.deliver_due()
    row = sub_rows(hub.store, sub["id"])[0]
    assert rx.received == [] and row["status"] == "dropped" and row["attempts"] == 0
    assert row["last_error"] == "restored_from_backup"


def test_a_claim_lost_during_dns_does_not_connect_or_overwrite(hub, monkeypatch):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher(worker_id="original")
    fixture_event(hub.store, "default")
    d.enqueue_new_events()
    row = d.claim_next()
    real = socket.getaddrinfo

    def resolve(*args, **kwargs):
        hub.store.execute("UPDATE webhook_deliveries SET claimed_by='replacement' WHERE id=?", (row["id"],))
        return real(*args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert d.send_one(row) is False
    got = sub_rows(hub.store, sub["id"])[0]
    assert rx.received == [] and got["attempts"] == 0 and got["claimed_by"] == "replacement"


def test_resuming_without_a_running_dispatcher_skips_the_pause_window(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    hub.c.post(f"{BASE}/{sub['id']}/pause", headers=hub.keys["admin"])
    fixture_event(hub.store, "default")
    hub.c.post(f"{BASE}/{sub['id']}/resume", headers=hub.keys["admin"])
    after = fixture_event(hub.store, "default")
    d.run_once()
    assert [x["id"] for x in rx.bodies()] == [after["id"]]


@pytest.mark.parametrize("hostname", ["hooks.fixture.test", "wrong.fixture.test"])
def test_pinned_https_keeps_sni_and_certificate_validation(hub, monkeypatch, tmp_path, hostname):
    """TEST FIXTURE: real loopback TLS, a local self-signed certificate and a controlled DNS
    answer. Trust only this fixture cert; the wrong hostname must still fail verification."""
    import datetime
    import ssl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hooks.fixture.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("hooks.fixture.test")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "fixture-cert.pem", tmp_path / "fixture-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    received, sni, lookups = [], [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_file, key_file)
    server_ctx.set_servername_callback(lambda sock, host, ctx: sni.append(host))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.socket = server_ctx.wrap_socket(srv.socket, server_side=True)
    serving = threading.Thread(target=srv.serve_forever, daemon=True)
    serving.start()
    client_ctx = ssl.create_default_context(cafile=str(cert_file))
    monkeypatch.setattr(ssl, "_create_default_https_context", lambda *args, **kwargs: client_ctx)

    def resolve(host, port, **kwargs):
        lookups.append(host)
        assert host == hostname, "connect must not resolve a second time, even for a numeric address"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    try:
        sub = hub.create(f"https://{hostname}:{srv.server_port}/hook", events=["*"])
        lookups.clear()
        d = hub.dispatcher()
        fixture_event(hub.store, "default")
        d.run_once()
        row = sub_rows(hub.store, sub["id"])[0]
        assert lookups == [hostname] and sni == [hostname]
        if hostname == "hooks.fixture.test":
            assert len(received) == 1 and row["status"] == "delivered" and row["last_code"] == 204
        else:
            assert received == [] and row["status"] == "pending" and row["last_code"] is None
    finally:
        srv.shutdown()
        srv.server_close()
        serving.join(5)
