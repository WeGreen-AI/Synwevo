"""Deployment-level webhooks (SYNWEVO_DOCS_WEBHOOK_URLS): the legacy X-DocWeave-* contract kept
byte for byte, Standard Webhooks headers alongside, retries, duplicate suppression, no replay of
history on first enablement, removed endpoints stop receiving, and the SSRF allowlist."""
import json
import socket
import time
import urllib.request

import pytest

from synwevo_docs import webhooks as W

from .test_webhook_support import Receiver, fixture_event, standard_signature


@pytest.fixture()
def hook_env(client, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET", "s3cret-for-tests")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_EVENTS", "job.completed,job.failed")
    # The application's own dispatcher runs in the background; stop it so every delivery in these
    # tests is one the test drove.
    client.app_state.webhooks.stop(wait=10)
    yield


def _job(api, fixtures):
    pid = api.project("Webhook")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}], "target": {"binding": s["binding"]}}])
    return api.job(pid)


def _dispatcher(store, **kw):
    """A dispatcher that exists *before* the events it is expected to deliver, as in production."""
    d = W.WebhookDispatcher(store, start=False, concurrency=1, **kw)
    d.run_once()
    return d


def test_delivery_is_signed_and_not_duplicated(api, fixtures, hook_env, monkeypatch):
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        d = _dispatcher(api.c.app_state.app.store)
        job = _job(api, fixtures)
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
    # The legacy contract is unchanged: same names, same single-value signature, same user agent.
    h = got["headers"]
    assert h["x-docweave-event"] == "job.completed" and h["x-docweave-signature"].startswith("v1=")
    assert " " not in h["x-docweave-signature"] and h["user-agent"] == "DocWeave-Webhook/1"
    # Standard Webhooks alongside: a non-whsec_ deployment secret signs with its raw bytes.
    assert h["webhook-id"] == body["id"] == h["x-docweave-event-id"]
    assert standard_signature("s3cret-for-tests", h["webhook-id"], h["webhook-timestamp"], got["body"]) == h["webhook-signature"]


def test_temporary_failure_is_retried_with_backoff(api, fixtures, hook_env, monkeypatch):
    rx = Receiver(behaviour=lambda n: 500 if n == 1 else 200)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    store = api.c.app_state.app.store
    try:
        d = _dispatcher(store)
        _job(api, fixtures)
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


def test_client_error_is_retried(client, hook_env, monkeypatch):
    """R30: every non-2xx except 410 is retried. This test used to assert that a 400 fails the
    delivery on its first attempt — which loses the event for good when a receiver answers 400/404
    for a minute during a redeploy (or Make answers 400 "queue is full")."""
    rx = Receiver(behaviour=lambda n: 400 if n == 1 else 200)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        fixture_event(store, "default", "job.completed")
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "pending" and row["attempts"] == 1 and "400" in row["last_error"]
        store.execute("UPDATE webhook_deliveries SET next_attempt_at=? WHERE id=?", (time.time() - 1, row["id"]))
        d.run_once()
        assert d.deliveries()[0]["status"] == "delivered"
    finally:
        rx.stop()


def test_gone_is_not_retried(client, hook_env, monkeypatch):
    rx = Receiver(behaviour=lambda n: 410)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        fixture_event(store, "default", "job.completed")
        d.run_once()
        row = d.deliveries()[0]
        assert row["status"] == "failed" and row["attempts"] == 1 and row["last_code"] == 410
    finally:
        rx.stop()


def test_disabled_without_secret(monkeypatch, api):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://example.com/hook")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_SECRET", raising=False)
    d = W.WebhookDispatcher(api.c.app_state.app.store, start=True)
    try:
        assert not d.enabled and "SYNWEVO_DOCS_WEBHOOK_SECRET" in (d.last_error or "")
    finally:
        d.stop(wait=10)


def test_deployment_endpoints_never_send_unsigned(client, hook_env, monkeypatch):
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_SECRET")
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        fixture_event(store, "default", "job.completed")
        d.run_once()
    finally:
        rx.stop()
    assert rx.received == [] and store.one("SELECT COUNT(*) AS n FROM webhook_deliveries")["n"] == 0


# ---------------------------------------------------------------- F299 / F118: no replay of history
def test_first_enablement_does_not_replay_history(client, hook_env, monkeypatch):
    """Webhooks turned on for a deployment that already has events start from the present."""
    store = client.app_state.app.store
    history = [fixture_event(store, t, "job.completed") for t in ("default", "tenant-b", "default")]
    # A database on which no dispatcher ever ran: no cursor at all.
    store.execute("DELETE FROM meta WHERE key IN (?, ?)", (W.LAST_SEQ_KEY, W.CURSOR_AT_KEY))
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        d = W.WebhookDispatcher(store, start=False, concurrency=1)
        assert d.run_once() == 0
        new = fixture_event(store, "default", "job.completed")
        assert d.run_once() == 1
    finally:
        rx.stop()
    got = [json.loads(r["body"])["id"] for r in rx.received]
    assert got == [new["id"]] and not set(got) & {h["id"] for h in history}


def test_a_stale_cursor_does_not_replay_the_gap(client, hook_env, monkeypatch):
    """A cursor left behind while webhooks were off: events from the gap are not replayed."""
    store = client.app_state.app.store
    d0 = _dispatcher(store)
    assert d0.run_once() == 0
    two_days = time.time() - 2 * 86400
    store.execute("UPDATE meta SET value=? WHERE key=?", (repr(two_days), W.CURSOR_AT_KEY))
    gap = [fixture_event(store, "default", "job.completed", occurred_at=time.time() - 86400) for _ in range(3)]
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        d = W.WebhookDispatcher(store, start=False, concurrency=1)
        assert d.run_once() == 0
        new = fixture_event(store, "default", "job.completed")
        d.run_once()
    finally:
        rx.stop()
    got = [json.loads(r["body"])["id"] for r in rx.received]
    assert got == [new["id"]] and not set(got) & {g["id"] for g in gap}


def test_a_restart_still_delivers_what_happened_just_before_it(client, hook_env, monkeypatch):
    """The gap rule must not eat the events a worker had not yet sent when it stopped."""
    store = client.app_state.app.store
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        _dispatcher(store)                                   # the old process: cursor is current
        ev = fixture_event(store, "default", "job.completed")   # happens; the process stops before sending
        d = W.WebhookDispatcher(store, start=False, concurrency=1, clock=lambda: time.time() + 3600)
        d.run_once()                                         # the new process, an hour later
    finally:
        rx.stop()
    assert [json.loads(r["body"])["id"] for r in rx.received] == [ev["id"]]


def test_a_removed_endpoint_stops_receiving(client, hook_env, monkeypatch):
    rx = Receiver(behaviour=lambda n: 500)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        fixture_event(store, "default", "job.completed")
        d.run_once()
        assert d.deliveries()[0]["status"] == "pending"
        monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://replacement.example.com/hook")
        store.execute("UPDATE webhook_deliveries SET next_attempt_at=?", (time.time() - 1,))
        d.deliver_due()
    finally:
        rx.stop()
    assert len(rx.received) == 1
    row = d.deliveries()[0]
    assert row["status"] == "dropped" and row["last_error"] == "endpoint_removed"


def test_a_transient_dns_failure_is_retried(client, hook_env, monkeypatch):
    """F356: a resolver hiccup used to fail the delivery permanently."""
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://hooks.fixture.test/hook")
    store = client.app_state.app.store
    d = _dispatcher(store)
    fixture_event(store, "default", "job.completed")
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror(socket.EAI_AGAIN, "temporary failure")))
    d.run_once()
    row = d.deliveries()[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and "无法解析" in row["last_error"]


def test_deployment_endpoints_receive_every_workspace(client, hook_env, monkeypatch):
    """Operator-level by design: documented, and different from subscriptions."""
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        a = fixture_event(store, "default", "job.completed")
        b = fixture_event(store, "tenant-b", "job.failed")
        fixture_event(store, "tenant-b", "job.started")          # not in SYNWEVO_DOCS_WEBHOOK_EVENTS
        d.run_once()
    finally:
        rx.stop()
    assert sorted(json.loads(r["body"])["id"] for r in rx.received) == sorted([a["id"], b["id"]])


def test_previous_deployment_secret_signs_until_it_expires(client, hook_env, monkeypatch):
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET_PREVIOUS", "old-secret")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET_PREVIOUS_EXPIRES_AT", str(time.time() + 600))
    store = client.app_state.app.store
    try:
        d = _dispatcher(store)
        fixture_event(store, "default", "job.completed")
        d.run_once()
        monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET_PREVIOUS_EXPIRES_AT", str(time.time() - 1))
        fixture_event(store, "default", "job.completed")
        d.run_once()
    finally:
        rx.stop()
    first, second = rx.received
    h = first["headers"]
    sigs = h["webhook-signature"].split()
    assert len(sigs) == 2
    for key in ("s3cret-for-tests", "old-secret"):
        assert standard_signature(key, h["webhook-id"], h["webhook-timestamp"], first["body"]) in sigs
    # The legacy header stays one value: receivers compare it whole.
    assert W.verify(first["body"], h["x-docweave-timestamp"], h["x-docweave-signature"], "s3cret-for-tests")
    assert len(second["headers"]["webhook-signature"].split()) == 1


def test_webhook_test_command_signs_like_a_delivery(hook_env):
    rx = Receiver()
    try:
        out = W.send_test(rx.url)
    finally:
        rx.stop()
    assert out["status"] == 200
    got = rx.received[0]
    assert W.verify(got["body"], got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "s3cret-for-tests")
    assert json.loads(got["body"])["type"] == "webhook.ping"
    # Re-serialising the body breaks the signature: receivers must use the raw bytes.
    compact = json.dumps(json.loads(got["body"]), separators=(",", ":")).encode()
    assert not W.verify(compact, got["headers"]["x-docweave-timestamp"], got["headers"]["x-docweave-signature"], "s3cret-for-tests")


def test_private_targets_need_the_allowlist(monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://127.0.0.1:9/hook", owner="default")
    with pytest.raises(W.UrlRejected):
        W.check_url("http://192.168.0.5/hook", owner="default")
    with pytest.raises(W.UrlRejected):
        W.check_url("file:///etc/passwd", owner="default")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.0/8")
    assert W.check_url("http://127.0.0.1:9/hook", owner="default")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", raising=False)
    with pytest.raises(W.UrlRejected):
        W.check_url("http://example.com/hook", owner="default")  # https required by default


def test_disabled_without_secret_without_a_job_runner(monkeypatch, api):
    """Was a second `test_disabled_without_secret` that shadowed the first one (pytest collected
    only this copy) and leaked its polling thread. Renamed so both run, and stopped."""
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", "https://example.com/hook")
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_SECRET", raising=False)
    d = W.WebhookDispatcher(api.c.app_state.app.store, start=True)
    try:
        assert not d.enabled and "SYNWEVO_DOCS_WEBHOOK_SECRET" in (d.last_error or "")
    finally:
        d.stop(wait=10)


def test_no_webhook_test_is_shadowed_by_a_duplicate_name():
    """A second `def test_x` silently replaces the first: pytest never runs the earlier one."""
    import ast
    from pathlib import Path
    here = Path(__file__).resolve().parent
    files = [here / "test_webhooks.py", here / "test_events_feed.py", *sorted(here.glob("test_webhook_*.py"))]
    for f in files:
        names = [n.name for n in ast.parse(f.read_text("utf-8")).body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")]
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert not dupes, f"{f.name} 中重复定义的测试：{dupes}"


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

    validated = webhooks.resolve_checked("https://rebind.example.com/hook", owner="default")
    assert validated == ["93.184.216.34"], "第一次解析通过的应当被记下来"

    # The second, hostile answer is never consulted, because the opener is pinned.
    opener = webhooks._pinned_opener(validated)
    connected = {}

    def fake_create_connection(addr, *a, **k):
        connected["addr"] = addr
        raise OSError("连接被测试拦截")

    monkeypatch.setattr(webhooks, "_connect_pinned", fake_create_connection)
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
        webhooks.resolve_checked("https://localhost-in-disguise.example.com/hook", owner="default")


def test_the_cloud_metadata_address_is_refused(monkeypatch):
    """The address that turns an SSRF into cloud credentials."""
    from synwevo_docs import webhooks
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))])
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    with pytest.raises(webhooks.UrlRejected):
        webhooks.resolve_checked("http://metadata.example.com/latest/meta-data/", owner="default")


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

    monkeypatch.setattr(webhooks, "_connect_pinned", fake_create_connection)
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
    assert webhooks.resolve_checked("https://internal.example.com/hook", owner="default") == ["10.0.0.5"]


def test_worker_downtime_preserves_backlog_for_unchanged_endpoints(client, hook_env, monkeypatch):
    rx = Receiver()
    store = client.app_state.app.store
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        old = _dispatcher(store, clock=lambda: time.time() - 2 * 86400)
        # API-only processes kept emitting events while workers were offline for a day.
        event = fixture_event(store, "default", occurred_at=time.time() - 86400)
        d = W.WebhookDispatcher(store, start=False, concurrency=1)
        d.run_once()
        assert [body["id"] for body in rx.bodies()] == [event["id"]]
    finally:
        rx.stop()


def test_deployment_body_retains_the_legacy_event_data(client, hook_env, monkeypatch):
    rx = Receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", rx.url)
    try:
        d = _dispatcher(client.app_state.app.store)
        data = {"error": "synthetic legacy error", "extra": {"preview_url": "fixture://only"}}
        fixture_event(d.store, "default", "job.failed", data=data)
        d.run_once()
        assert rx.bodies()[0]["data"] == data
    finally:
        rx.stop()
