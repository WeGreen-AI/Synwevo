"""Signed webhooks: signature, retries with backoff, duplicate suppression, SSRF allowlist."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from docweave import webhooks as W


class Receiver:
    def __init__(self, behaviour=None):
        self.received = []
        self.behaviour = behaviour or (lambda n: 200)
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.received.append({"body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
                code = outer.behaviour(len(outer.received))
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()


@pytest.fixture()
def hook_env(monkeypatch):
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_SECRET", "s3cret-for-tests")
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_EVENTS", "job.completed,job.failed")
    yield


def _job(api, fixtures):
    pid = api.project("Webhook")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return api.job(pid)


def test_delivery_is_signed_and_not_duplicated(api, fixtures, hook_env, monkeypatch):
    rx = Receiver()
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_URLS", rx.url)
    try:
        job = _job(api, fixtures)
        d = W.WebhookDispatcher(api.c.app_state.app.store, start=False)
        assert d.run_once() >= 1
        assert d.run_once() == 0                      # the same event is not delivered twice
    finally:
        rx.stop()
    assert len(rx.received) == 1
    got = rx.received[0]
    body = json.loads(got["body"])
    assert body["type"] == "job.completed" and body["job"]["id"] == job["id"] and body["version"] == 1
    assert W.verify(got["body"], got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "s3cret-for-tests")
    assert not W.verify(got["body"], got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "wrong-secret")
    assert not W.verify(got["body"] + b"x", got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "s3cret-for-tests")
    assert not W.verify(got["body"], int(time.time()) - 10_000, got["headers"]["x-docweave-signature"], "s3cret-for-tests")
    assert got["headers"]["x-docweave-event-id"].startswith("ev_") and got["headers"]["x-docweave-delivery"]


def test_temporary_failure_is_retried_with_backoff(api, fixtures, hook_env, monkeypatch):
    rx = Receiver(behaviour=lambda n: 500 if n == 1 else 200)
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_URLS", rx.url)
    store = api.c.app_state.app.store
    try:
        _job(api, fixtures)
        d = W.WebhookDispatcher(store, start=False)
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "pending" and row["attempts"] == 1 and row["last_code"] == 500
        assert row["next_attempt_at"] > time.time()                 # backoff, not a busy loop
        store.execute("UPDATE webhook_deliveries SET next_attempt_at=? WHERE id=?", (time.time() - 1, row["id"]))
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "delivered" and row["attempts"] == 2 and row["delivered_at"]
    finally:
        rx.stop()
    assert len(rx.received) == 2


def test_client_error_is_not_retried(api, fixtures, hook_env, monkeypatch):
    rx = Receiver(behaviour=lambda n: 400)
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_URLS", rx.url)
    try:
        _job(api, fixtures)
        d = W.WebhookDispatcher(api.c.app_state.app.store, start=False)
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "failed" and row["attempts"] == 1 and "400" in row["last_error"]
    finally:
        rx.stop()


def test_private_targets_need_the_allowlist(monkeypatch):
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("DOCWEAVE_WEBHOOK_ALLOW_PRIVATE", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://127.0.0.1:9/hook")
    with pytest.raises(W.UrlRejected):
        W.check_url("http://192.168.0.5/hook")
    with pytest.raises(W.UrlRejected):
        W.check_url("file:///etc/passwd")
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_ALLOW_PRIVATE", "127.0.0.0/8")
    assert W.check_url("http://127.0.0.1:9/hook")
    monkeypatch.delenv("DOCWEAVE_WEBHOOK_ALLOW_HTTP", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://example.com/hook")            # https required by default


def test_disabled_without_secret(monkeypatch, api):
    monkeypatch.setenv("DOCWEAVE_WEBHOOK_URLS", "https://example.com/hook")
    monkeypatch.delenv("DOCWEAVE_WEBHOOK_SECRET", raising=False)
    d = W.WebhookDispatcher(api.c.app_state.app.store, start=True)
    assert not d.enabled and "DOCWEAVE_WEBHOOK_SECRET" in (d.last_error or "")
