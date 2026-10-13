"""Deliveries to webhook subscriptions: Standard Webhooks signatures (checked with an independent
implementation), rotation, retries with jitter, status-code handling, authority re-checked at
delivery, workspace isolation, restore safety, SSRF at delivery, and atomic claims."""
import json
import random
import socket
import threading
import time

import pytest

from synwevo_docs import webhooks as W

from .test_webhook_support import (fixture_event, hub, journal_entries, signed_by,  # noqa: F401  (hub is a fixture)
                                   standard_signature, sub_rows)

BASE = "/api/v1/webhooks/subscriptions"


def _job(api, fixtures):
    pid = api.project("Webhook 订阅")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    s = api.text_slot(t["id"], "示例客户名称", "客户名称")
    api.rule(pid, {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0, "data_end": 2}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": "姓名"}],
                    "target": {"binding": s["binding"]}}])
    return api.job(pid)


def _drain(hub, d, rows_of, until, max_rounds=20):
    """Run the dispatcher, moving the fixture clock to each next attempt, until `until(rows)`."""
    for _ in range(max_rounds):
        d.run_once()
        rows = rows_of()
        if until(rows):
            return rows
        pending = [r["next_attempt_at"] for r in rows if r["status"] == "pending"]
        if pending:
            hub.clock.advance(max(0.0, min(pending) - hub.clock()) + 1)
    raise AssertionError(f"did not settle: {rows_of()}")


# ------------------------------------------------------------------ signatures and payload
def test_a_real_job_is_delivered_signed_to_the_standard(hub, api, fixtures):
    rx = hub.receiver()
    sub = hub.create(rx.url)
    d = hub.dispatcher()
    job = _job(api, fixtures)
    assert job["status"] == "completed"
    assert d.run_once() == 1
    assert len(rx.received) == 1
    req = rx.received[0]
    h, body = req["headers"], json.loads(req["body"])
    assert body["type"] == "job.completed" and body["job"]["id"] == job["id"]
    assert h["webhook-id"] == body["id"] and h["x-synwevo-event"] == "job.completed"
    row = sub_rows(hub.store, sub["id"])[0]
    assert h["x-synwevo-delivery"] == row["id"] and row["status"] == "delivered" and row["duration_ms"] is not None
    # Independent verification: base64(HMAC-SHA256(b64decode(secret minus whsec_), id.ts.body)).
    assert signed_by(sub["secret"], req)
    assert abs(int(h["webhook-timestamp"]) - time.time()) < 60
    assert not signed_by("whsec_" + "A" * 43 + "=", req)
    tampered = dict(req, body=req["body"] + b" ")
    assert not signed_by(sub["secret"], tampered)
    # The module's own verifier agrees, and rejects a stale timestamp.
    assert W.verify_standard(req["body"], h["webhook-id"], h["webhook-timestamp"], h["webhook-signature"], sub["secret"])
    assert not W.verify_standard(req["body"], h["webhook-id"], int(h["webhook-timestamp"]) - 3600,
                                 h["webhook-signature"], sub["secret"])
    # Payload: identifiers, status and counts only.
    assert set(body) == {"id", "type", "version", "occurred_at", "tenant", "data", "job"}
    assert set(body["job"]) == {"id", "project_id", "status", "counts", "run_no"}
    assert body["tenant"] == "default" and body["version"] == 1
    # Subscription deliveries do not carry the deployment's legacy headers.
    assert not any(k.startswith("x-docweave-") for k in h)
    # The same event is not delivered twice.
    assert d.run_once() == 0 and len(rx.received) == 1


def test_payload_never_carries_links_or_tokens(hub):
    rx = hub.receiver()
    hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default", "job.completed",
                  data={"status": "completed", "download_url": "https://x/api/v1/download/abc", "token": "t",
                        "counts": {"total": 1}})
    d.run_once()
    body = json.loads(rx.received[0]["body"])
    assert body["data"] == {"status": "completed", "counts": {"total": 1}}
    assert b"download" not in rx.received[0]["body"]


def test_rotation_signs_with_both_secrets_until_the_overlap_ends(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    old = sub["secret"]
    new = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": 600},
                     headers=hub.keys["admin"]).json()["secret"]
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    req = rx.received[-1]
    sigs = req["headers"]["webhook-signature"].split()
    assert len(sigs) == 2
    assert signed_by(old, req) and signed_by(new, req)          # each secret alone verifies
    hub.clock.advance(601)
    fixture_event(hub.store, "default")
    d.run_once()
    req = rx.received[-1]
    assert len(req["headers"]["webhook-signature"].split()) == 1
    assert signed_by(new, req) and not signed_by(old, req)


def test_rotation_without_overlap_drops_the_old_secret_at_once(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    new = hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": 0}, headers=hub.keys["admin"]).json()["secret"]
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    assert signed_by(new, rx.received[0]) and not signed_by(sub["secret"], rx.received[0])


# ------------------------------------------------------------------ retries
def test_backoff_schedule_has_bounded_jitter():
    d = W.WebhookDispatcher.__new__(W.WebhookDispatcher)
    d.rng = random.Random(1234)
    for _ in range(50):
        for k, base in enumerate(W.BACKOFF_S, start=1):
            delay = d.next_delay(k)
            assert 0.8 * base <= delay <= 1.2 * base, (k, delay)
    assert d.next_delay(len(W.BACKOFF_S) + 1) is None
    # Retry-After is a minimum, never a reason to retry sooner.
    assert d.next_delay(1, retry_after=600) >= 600
    assert d.next_delay(9, retry_after=5) >= 0.8 * W.BACKOFF_S[8]


def test_every_attempt_follows_the_schedule_until_it_is_exhausted(hub, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_DISABLE_AFTER_S", str(30 * 86400))   # isolate the schedule
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    ev = fixture_event(hub.store, "default")
    for k in range(1, len(W.BACKOFF_S) + 1):
        d.run_once()
        row = sub_rows(hub.store, sub["id"])[0]
        assert row["status"] == "pending" and row["attempts"] == k and row["last_code"] == 500
        wait = row["next_attempt_at"] - hub.clock()
        assert 0.8 * W.BACKOFF_S[k - 1] - 2 <= wait <= 1.2 * W.BACKOFF_S[k - 1], (k, wait)
        d.run_once()                                   # not due yet: nothing is sent early
        assert len(rx.received) == k
        hub.clock.advance(wait + 1)
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "failed" and row["attempts"] == len(W.BACKOFF_S) + 1
    assert len(rx.received) == len(W.BACKOFF_S) + 1
    # every retry is the same event, signed afresh
    assert {r["headers"]["webhook-id"] for r in rx.received} == {ev["id"]}
    assert len({r["headers"]["webhook-timestamp"] for r in rx.received}) == len(rx.received)
    assert all(signed_by(sub["secret"], r) for r in rx.received)
    assert b"{}" not in (row["last_error"] or "").encode()        # the response body is never stored


def test_a_client_error_is_retried_and_then_delivered(hub):
    rx = hub.receiver(lambda n: 404 if n == 1 else 200)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    rows = _drain(hub, d, lambda: sub_rows(hub.store, sub["id"]), lambda rs: rs[0]["status"] == "delivered")
    assert rows[0]["attempts"] == 2 and rows[0]["last_code"] == 200 and rows[0]["delivered_at"]


def test_retry_after_is_honoured_as_a_minimum(hub):
    rx = hub.receiver(lambda n: (429, {"Retry-After": "600"}))
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "pending" and row["next_attempt_at"] >= hub.clock() + 599
    # an HTTP-date works too
    import email.utils
    when = email.utils.formatdate(hub.clock() + 900, usegmt=True)
    rx.behaviour = lambda n: (503, {"Retry-After": when})
    hub.clock.advance(601)
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["attempts"] == 2 and row["next_attempt_at"] >= hub.clock() + 290


@pytest.mark.parametrize("header", ["²", "1³"], ids=["a superscript digit", "digits and a superscript"])
def test_an_unreadable_retry_after_is_no_retry_after(hub, header):
    """Whatever a receiver writes in Retry-After, its 429 is a 429: recorded with its status,
    retried on the schedule, and counted against the subscription like any other refusal."""
    rx = hub.receiver(lambda n: (429, {"Retry-After": header}))
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert (row["status"], row["attempts"], row["last_code"], row["last_error"]) == ("pending", 1, 429, "HTTP 429")
    assert 0.8 * W.BACKOFF_S[0] - 2 <= row["next_attempt_at"] - hub.clock() <= 1.2 * W.BACKOFF_S[0]
    health = hub.store.one("SELECT consecutive_failures, last_failure_at FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert health["consecutive_failures"] == 1 and health["last_failure_at"] is not None


def test_a_retry_after_date_without_its_zone_is_utc_wherever_the_server_is(hub, monkeypatch):
    """An HTTP-date is UTC whether or not it says so. Read in the server's local time it would be
    another moment on every machine, and the last day of the calendar could not be read at all."""
    import email.utils
    try:
        with monkeypatch.context() as zone:
            zone.setenv("TZ", "Asia/Hong_Kong")          # TEST FIXTURE: a server that is not in UTC
            time.tzset()
            soon = email.utils.formatdate(hub.clock() + 900, usegmt=True).replace(" GMT", "")
            rx = hub.receiver(lambda n: (503, {"Retry-After": soon if n == 1 else "Fri, 31 Dec 9999 23:59:59"}))
            sub = hub.create(rx.url, events=["*"])
            d = hub.dispatcher()
            fixture_event(hub.store, "default")
            d.run_once()
            row = sub_rows(hub.store, sub["id"])[0]
            assert (row["attempts"], row["last_code"]) == (1, 503) and 890 <= row["next_attempt_at"] - hub.clock() <= 901
            hub.clock.advance(901)
            d.run_once()
            row = sub_rows(hub.store, sub["id"])[0]
            assert (row["status"], row["attempts"], row["last_code"]) == ("pending", 2, 503)
            assert row["next_attempt_at"] - hub.clock() == pytest.approx(W.RETRY_AFTER_CAP_S, abs=5), "a minimum, capped at a day"
    finally:
        time.tzset()


def test_410_disables_the_subscription_and_stops_its_queue(hub):
    rx = hub.receiver(lambda n: 410)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    fixture_event(hub.store, "default")
    d.run_once()
    assert len(rx.received) == 1
    rows = sub_rows(hub.store, sub["id"])
    assert rows[0]["status"] == "failed" and rows[0]["last_code"] == 410
    assert rows[1]["status"] == "dropped" and not [r for r in rows if r["status"] == "pending"]
    got = hub.c.get(f"{BASE}/{sub['id']}", headers=hub.keys["admin"]).json()
    assert got["status"] == "disabled" and got["disabled_reason"] == "gone"
    assert any(e["kind"] == "webhook_disabled" and e["subscription_id"] == sub["id"] for e in journal_entries(hub.store))
    fixture_event(hub.store, "default")
    d.run_once()
    assert len(sub_rows(hub.store, sub["id"])) == 2 and len(rx.received) == 1


def test_three_days_of_failures_disable_the_subscription(hub):
    rx = hub.receiver(lambda n: 503)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    start = hub.clock()                                # the first failure happens in the first round
    for _ in range(40):
        fixture_event(hub.store, "default")            # traffic keeps arriving, and keeps failing
        d.run_once()
        s = hub.store.one("SELECT * FROM webhook_subscriptions WHERE id=?", (sub["id"],))
        if s["status"] == "disabled":
            break
        assert hub.clock() - start < W.disable_after_s(), "未满三天就停用了"
        hub.clock.advance(3 * 3600)
    assert s["status"] == "disabled" and s["disabled_reason"] == "failing"
    assert hub.clock() - start >= W.disable_after_s()
    assert not [r for r in sub_rows(hub.store, sub["id"]) if r["status"] == "pending"]
    assert s["consecutive_failures"] >= 5 and s["last_failure_at"]
    assert any(e["kind"] == "webhook_disabled" and e["subscription_id"] == sub["id"] for e in journal_entries(hub.store))
    # Re-enabling starts a fresh clock: the next failure does not disable it again at once.
    assert hub.c.post(f"{BASE}/{sub['id']}/resume", headers=hub.keys["admin"]).json()["status"] == "active"
    fixture_event(hub.store, "default")
    d.run_once()
    assert hub.store.one("SELECT status FROM webhook_subscriptions WHERE id=?", (sub["id"],))["status"] == "active"


# ------------------------------------------------------------------ authority at delivery
def test_a_revoked_creator_stops_delivery(hub):
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()                                       # one failed attempt; a retry is queued
    assert sub_rows(hub.store, sub["id"])[0]["status"] == "pending"
    hub.auth.revoke_key(hub.key_ids["admin"])
    rx.behaviour = lambda n: 200
    hub.clock.advance(60)
    fixture_event(hub.store, "default")
    d.run_once()
    assert len(rx.received) == 1                       # nothing after the revocation
    rows = sub_rows(hub.store, sub["id"])
    assert len(rows) == 2 and all(r["status"] == "dropped" for r in rows)
    assert all("authorization_revoked:credential_revoked" in r["last_error"] for r in rows)
    s = hub.store.one("SELECT status, disabled_reason FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert s["status"] == "disabled" and s["disabled_reason"] == "authorization_revoked:credential_revoked"
    assert any(e["kind"] == "webhook_disabled" and e["subscription_id"] == sub["id"] for e in journal_entries(hub.store))


def test_a_frozen_workspace_stops_delivery(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, key="b_admin", events=["*"])
    d = hub.dispatcher()
    t = time.time()
    hub.store.execute("INSERT INTO workspaces (id, name, kind, status, created_at, updated_at) "
                      "VALUES ('tenant-b', 'B', 'org', 'frozen', ?, ?)", (t, t))       # TEST FIXTURE
    fixture_event(hub.store, "tenant-b")
    d.run_once()
    assert rx.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "dropped" and row["last_error"] == "workspace_frozen"
    s = hub.store.one("SELECT status, disabled_reason FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert s["status"] == "disabled" and s["disabled_reason"] == "workspace_frozen"


def test_a_deleted_workspace_disables_its_subscriptions_for_good(hub):
    """A44: the platform deletes the workspace; its subscription is disabled at its next delivery
    and stays disabled when the workspace is recovered (the preview's `webhooks_disabled_for_good`)."""
    from synwevo_core import workspace_lifecycle
    rx = hub.receiver()
    sub = hub.create(rx.url, key="b_admin", events=["*"])
    d = hub.dispatcher()
    t = time.time()
    hub.store.execute("INSERT INTO workspaces (id, name, kind, status, created_at, updated_at) "
                      "VALUES ('tenant-b', 'B', 'org', 'active', ?, ?)", (t, t))       # TEST FIXTURE
    workspace_lifecycle.platform_transition(hub.store, "tenant-b", "fixture-platform-admin", "delete",
                                            reason="Isolated fixture: deletion")
    fixture_event(hub.store, "tenant-b")
    d.run_once()
    assert rx.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "dropped" and row["last_error"] == "workspace_deleted"
    workspace_lifecycle.platform_transition(hub.store, "tenant-b", "fixture-platform-admin", "recover",
                                            reason="Isolated fixture: recovery")
    s = hub.store.one("SELECT status, disabled_reason FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert s["status"] == "disabled" and s["disabled_reason"] == "workspace_deleted"
    fixture_event(hub.store, "tenant-b")
    d.run_once()
    assert rx.received == []


def test_a_workspace_state_nobody_has_decided_about_stops_delivery(hub, monkeypatch):
    """The dispatcher's workspace check is an allow-list (`authority.RUNNABLE_WORKSPACE_STATES`):
    a status added later disables the subscription until someone decides otherwise. The schema's
    CHECK refuses an unknown status, so the row is stubbed (TEST FIXTURE) at the dispatcher's read."""
    rx = hub.receiver()
    sub = hub.create(rx.url, key="b_admin", events=["*"])
    d = hub.dispatcher()
    t = time.time()
    hub.store.execute("INSERT INTO workspaces (id, name, kind, status, created_at, updated_at) "
                      "VALUES ('tenant-b', 'B', 'org', 'active', ?, ?)", (t, t))       # TEST FIXTURE
    original = type(hub.store).one

    def stubbed(self, sql, args=()):
        found = original(self, sql, args)
        if sql.startswith("SELECT status FROM workspaces") and found:
            return {**found, "status": "archived"}
        return found
    monkeypatch.setattr(type(hub.store), "one", stubbed)
    try:
        fixture_event(hub.store, "tenant-b")
        d.run_once()
    finally:
        monkeypatch.setattr(type(hub.store), "one", original)
    assert rx.received == []
    s = hub.store.one("SELECT status, disabled_reason FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert s["status"] == "disabled" and s["disabled_reason"] == "workspace_archived"


# ------------------------------------------------------------------ isolation, filters, pause
def test_each_workspace_receives_only_its_own_events(hub):
    rx_a, rx_b = hub.receiver(), hub.receiver()
    a = hub.create(rx_a.url, events=["*"])
    b = hub.create(rx_b.url, key="b_admin", events=["*"])
    d = hub.dispatcher()
    ea = fixture_event(hub.store, "default", "job.completed")
    eb = fixture_event(hub.store, "tenant-b", "job.failed")
    d.run_once()
    assert [x["id"] for x in rx_a.bodies()] == [ea["id"]]
    assert [x["id"] for x in rx_b.bodies()] == [eb["id"]]
    assert {r["tenant"] for r in sub_rows(hub.store, a["id"])} == {"default"}
    assert {r["tenant"] for r in sub_rows(hub.store, b["id"])} == {"tenant-b"}
    assert signed_by(a["secret"], rx_a.received[0]) and not signed_by(b["secret"], rx_a.received[0])


def test_event_filter_pause_and_resume(hub):
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["job.failed"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default", "job.completed")
    fixture_event(hub.store, "default", "job.failed")
    d.run_once()
    rows = sub_rows(hub.store, sub["id"])
    assert [r["event_type"] for r in rows] == ["job.failed"] and rows[0]["status"] == "pending"
    # pausing stops the queued retry …
    hub.c.post(f"{BASE}/{sub['id']}/pause", headers=hub.keys["admin"])
    assert sub_rows(hub.store, sub["id"])[0]["status"] == "dropped"
    during = fixture_event(hub.store, "default", "job.failed")
    hub.clock.advance(3600)
    d.run_once()
    assert len(rx.received) == 1
    # … and resuming delivers what happens afterwards, not what happened while paused.
    rx.behaviour = lambda n: 200
    hub.c.post(f"{BASE}/{sub['id']}/resume", headers=hub.keys["admin"])
    after = fixture_event(hub.store, "default", "job.failed")
    d.run_once()
    ids = [x["id"] for x in rx.bodies()]
    assert after["id"] in ids and during["id"] not in ids


def test_deleting_a_subscription_stops_pending_retries(hub):
    rx = hub.receiver(lambda n: 500)
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    hub.c.delete(f"{BASE}/{sub['id']}", headers=hub.keys["admin"])
    hub.clock.advance(3600)
    d.run_once()
    assert len(rx.received) == 1
    assert sub_rows(hub.store, sub["id"])[0]["status"] == "dropped"


# ------------------------------------------------------------------ restore safety
def test_events_older_than_a_subscription_are_never_sent(hub):
    rx = hub.receiver()
    d = hub.dispatcher()
    old = fixture_event(hub.store, "default", occurred_at=time.time() - 5)
    sub = hub.create(rx.url, events=["*"])
    d.run_once()
    assert rx.received == [] and sub_rows(hub.store, sub["id"]) == []
    # A restored database whose cursor is far behind: still nothing from before the subscription.
    hub.store.execute("UPDATE meta SET value='0' WHERE key=?", (W.LAST_SEQ_KEY,))
    hub.store.execute("DELETE FROM meta WHERE key=?", (W.CURSOR_AT_KEY,))
    d.run_once()
    assert rx.received == []
    # A queue row that the enqueue rule would never have produced is dropped at send time.
    t = time.time()
    hub.store.execute("INSERT INTO webhook_deliveries (id, event_id, endpoint, status, attempts, next_attempt_at, created_at, "
                      "subscription_id, tenant, event_type) VALUES ('wh_restored', ?, ?, 'pending', 0, ?, ?, ?, 'default', "
                      "'job.completed')", (old["id"], f"sub:{sub['id']}:restored", t, t, sub["id"]))   # TEST FIXTURE
    d.run_once()
    assert rx.received == []
    assert hub.store.one("SELECT status, last_error FROM webhook_deliveries WHERE id='wh_restored'") == \
        {"status": "dropped", "last_error": "predates_subscription"}
    new = fixture_event(hub.store, "default")
    d.run_once()
    assert [x["id"] for x in rx.bodies()] == [new["id"]]


def test_a_cursor_ahead_of_a_restored_log_comes_back(hub):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    top = hub.store.one("SELECT COALESCE(MAX(seq),0) AS m FROM job_events")["m"]
    hub.store.execute("UPDATE meta SET value=? WHERE key=?", (str(top + 1000), W.LAST_SEQ_KEY))
    d.run_once()
    assert int(hub.store.one("SELECT value FROM meta WHERE key=?", (W.LAST_SEQ_KEY,))["value"]) == top
    ev = fixture_event(hub.store, "default")
    d.run_once()
    assert [x["id"] for x in rx.bodies()] == [ev["id"]] and sub_rows(hub.store, sub["id"])


# ------------------------------------------------------------------ SSRF at delivery
def test_redirects_are_not_followed(hub):
    target = hub.receiver()
    rx = hub.receiver(lambda n: (302, {"Location": target.url}))
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    assert len(rx.received) == 1 and target.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "pending" and row["last_code"] == 302 and "跳转" in row["last_error"]


def test_the_address_is_checked_again_at_delivery(hub, monkeypatch):
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    monkeypatch.delenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE")      # the operator withdrew the exception
    fixture_event(hub.store, "default")
    d.run_once()
    assert rx.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "failed" and "内网" in row["last_error"]


def test_a_host_that_starts_resolving_to_metadata_is_refused(hub, monkeypatch):
    real = socket.getaddrinfo
    answers = {"hooks.fixture.test": "93.184.216.34"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (
        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], 443))] if host in answers else real(host, *a, **k)))
    sub = hub.create("https://hooks.fixture.test/hook", events=["*"])
    answers["hooks.fixture.test"] = "169.254.169.254"                # DNS now points at the metadata service
    connected = []

    def no_connect(addr, *a, **k):
        connected.append(addr)
        raise OSError("连接被测试拦截")

    monkeypatch.setattr(W, "_connect_pinned", no_connect)
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    assert connected == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "failed" and "内网" in row["last_error"]
    assert "169.254.169.254" not in row["last_error"]  # no private DNS oracle for workspace admins


def test_a_transient_resolver_failure_is_retried(hub, monkeypatch):
    """F356: a DNS hiccup used to mark the delivery permanently failed."""
    state = {"up": True}

    def fake(host, *a, **k):
        if not state["up"]:
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    sub = hub.create("https://hooks.fixture.test/hook", events=["*"])
    state["up"] = False
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and "无法解析" in row["last_error"]


# ------------------------------------------------------------------ concurrency
def test_two_workers_never_send_the_same_delivery(hub):
    rx = hub.receiver(delay=0.2)
    sub = hub.create(rx.url, events=["*"])
    seed = hub.dispatcher()
    for _ in range(6):
        fixture_event(hub.store, "default")
    seed.enqueue_new_events()
    d1 = hub.dispatcher(worker_id="worker-1", concurrency=3)
    d2 = hub.dispatcher(worker_id="worker-2", concurrency=3)
    # Both see every row as due; only the claim decides who sends.
    threads = [threading.Thread(target=x.deliver_due) for x in (d1, d2, d1, d2)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(30)
    delivered = [r["headers"]["x-synwevo-delivery"] for r in rx.received]
    assert len(delivered) == 6 and len(set(delivered)) == 6
    rows = sub_rows(hub.store, sub["id"])
    assert all(r["status"] == "delivered" and r["attempts"] == 1 and r["claimed_by"] is None for r in rows)


def test_the_cursor_does_not_advance_past_a_failed_enqueue(hub, monkeypatch):
    """F310: the cursor used to advance even when inserting a delivery raised."""
    rx = hub.receiver()
    hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    before = hub.store.one("SELECT value FROM meta WHERE key=?", (W.LAST_SEQ_KEY,))["value"]
    fixture_event(hub.store, "default")

    def boom(*a, **k):
        raise RuntimeError("disk full (fixture)")

    monkeypatch.setattr(W.WebhookDispatcher, "_insert", staticmethod(boom))
    with pytest.raises(RuntimeError):
        d.enqueue_new_events()
    assert hub.store.one("SELECT value FROM meta WHERE key=?", (W.LAST_SEQ_KEY,))["value"] == before
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_deliveries")["n"] == 0


# ------------------------------------------------------------------ signing metadata (G26 / D7 §8)
def test_each_attempt_records_which_key_signed_it_and_when(hub):
    """The delivery row says which secret signed an attempt — a short fingerprint, never the secret
    or the signature — and when; the public views say neither; a rotation changes the fingerprint
    of later attempts only, and the receiver can tell nothing from it."""
    import re
    from .test_webhook_support import sub_rows
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    assert d.run_once() == 1
    first = sub_rows(hub.store, sub["id"])[0]
    h = rx.received[0]["headers"]
    assert re.fullmatch(r"[0-9a-f]{12}", first["signing_key"]) and int(first["signed_at"]) == int(h["webhook-timestamp"])
    assert first["signing_key"] not in sub["secret"] and first["signing_key"] not in h["webhook-signature"]
    assert sub["secret"] not in json.dumps(dict(first), default=str)
    listed = hub.c.get(f"{BASE}/{sub['id']}/deliveries", headers=hub.keys["admin"]).json()
    one = hub.c.get(f"{BASE}/{sub['id']}/deliveries/{first['id']}", headers=hub.keys["admin"]).json()
    for view in (listed, one):
        text = json.dumps(view)
        assert "signing_key" not in text and "signed_at" not in text and first["signing_key"] not in text
    hub.c.post(f"{BASE}/{sub['id']}/rotate-secret", json={"overlap_s": 600}, headers=hub.keys["admin"])
    fixture_event(hub.store, "default")
    assert d.run_once() == 1
    rows = sub_rows(hub.store, sub["id"])
    assert rows[0]["signing_key"] == first["signing_key"] and rows[1]["signing_key"] != first["signing_key"]
    assert re.fullmatch(r"[0-9a-f]{12}", rows[1]["signing_key"]) and rows[1]["signed_at"] >= first["signed_at"]
