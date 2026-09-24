"""Signed webhooks: signature, retries with backoff, duplicate suppression, SSRF allowlist."""
import json
import socket
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from synwevo_docs import webhooks as W


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
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET", "s3cret-for-tests")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_EVENTS", "job.completed,job.failed")
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
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
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
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
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
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        _job(api, fixtures)
        d = W.WebhookDispatcher(api.c.app_state.app.store, start=False)
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "failed" and row["attempts"] == 1 and "400" in row["last_error"]
    finally:
        rx.stop()


def test_private_targets_need_the_allowlist(monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://127.0.0.1:9/hook")
    with pytest.raises(W.UrlRejected):
        W.check_url("http://192.168.0.5/hook")
    with pytest.raises(W.UrlRejected):
        W.check_url("file:///etc/passwd")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.0/8")
    assert W.check_url("http://127.0.0.1:9/hook")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://example.com/hook")            # https required by default


def test_disabled_without_secret(monkeypatch, api):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://example.com/hook")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_SECRET", raising=False)
    d = W.WebhookDispatcher(api.c.app_state.app.store, start=True)
    assert not d.enabled and "SYNWEVO_DOCS_WEBHOOK_SECRET" in (d.last_error or "")


# ---------------------------------------------------------------- SSRF: check and connect must agree
def test_the_validated_address_is_the_one_connected_to(monkeypatch):
    """`check_url` resolved the host, and then urllib resolved it again independently.

    A name under someone else's control can answer with a public address for the check and
    `169.254.169.254` or `127.0.0.1` a moment later for the connection — so the check proved
    nothing about where the request actually went. `resolve_checked` now returns the addresses
    it approved, and the opener connects only to those.
    """
    from synwevo_docs import webhooks

    answers = iter([
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],   # public: passes
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))],  # then flips
    ])
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: next(answers))

    validated = webhooks.resolve_checked("https://rebind.example.com/hook")
    assert validated == ["93.184.216.34"], "第一次解析通过的应当被记下来"

    # The second, hostile answer is never consulted, because the opener is pinned.
    opener = webhooks._pinned_opener(validated)
    connected = {}

    def fake_create_connection(addr, *a, **k):
        connected["addr"] = addr
        raise OSError("连接被测试拦截")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    with pytest.raises(Exception):
        opener.open(urllib.request.Request("https://rebind.example.com/hook", data=b"{}",
                                           method="POST"), timeout=1)
    assert connected["addr"][0] == "93.184.216.34", \
        f"连到了 {connected['addr'][0]}——重新解析出的地址被用上了，检查就白做了"


def test_a_host_resolving_to_a_private_address_is_refused(monkeypatch):
    from synwevo_docs import webhooks
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    with pytest.raises(webhooks.UrlRejected, match="内网"):
        webhooks.resolve_checked("https://localhost-in-disguise.example.com/hook")


def test_the_cloud_metadata_address_is_refused(monkeypatch):
    """The address that turns an SSRF into cloud credentials."""
    from synwevo_docs import webhooks
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))])
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    with pytest.raises(webhooks.UrlRejected):
        webhooks.resolve_checked("http://metadata.example.com/latest/meta-data/")


def test_delivery_does_not_use_the_ambient_proxy(monkeypatch):
    """`build_opener` installs a ProxyHandler from the environment by default. A proxy would
    carry the signed event body somewhere nobody chose — and would do its own resolution,
    bypassing the address check entirely."""
    from synwevo_docs import webhooks
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    opener = webhooks._pinned_opener(["93.184.216.34"])
    # Passing ProxyHandler({}) means it registers no proxy methods and drops out of the handler
    # list entirely — which is exactly "no proxying". The property to assert is that nothing in
    # the opener carries proxy configuration, not that a particular object is present.
    configured = [getattr(h, "proxies", None) for h in opener.handlers]
    assert not any(configured), f"仍然带着代理配置：{[p for p in configured if p]}"

    # And behaviourally: the connection goes to the pinned address, not to the proxy host.
    seen = {}

    def fake_create_connection(addr, *a, **k):
        seen["addr"] = addr
        raise OSError("连接被测试拦截")

    monkeypatch.setattr(socket, "create_connection", fake_create_connection)
    with pytest.raises(Exception):
        opener.open(urllib.request.Request("https://example.com/hook", data=b"{}", method="POST"),
                    timeout=1)
    assert seen["addr"][0] == "93.184.216.34", f"请求发去了 {seen['addr']}"


def test_an_explicitly_allowed_private_target_still_works(monkeypatch):
    """The allow-list exists for real internal receivers; it must keep working."""
    from synwevo_docs import webhooks
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "10.0.0.0/8")
    assert webhooks.resolve_checked("https://internal.example.com/hook") == ["10.0.0.5"]
