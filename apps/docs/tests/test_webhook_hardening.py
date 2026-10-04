"""Dispatcher hardening: one slow or dead receiver cannot hold the loop, a send never outlives its
claim, an attempt in flight cannot undo a pause, the restore fence is never crossed, and
workspace-facing payloads carry no exception text or nested links."""
import json
import socket
import threading
import time
from types import SimpleNamespace

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


def test_no_free_resolver_is_this_servers_wait_not_the_receivers_failure(hub, monkeypatch):
    """Every resolver slot of the process is held by lookups that have not returned. The attempt
    waits its whole deadline for one; the receiver was never tried, so nothing is charged to it —
    no attempt, no failure on the subscription's record (R3-WH-2: Docs counted both, where the
    suite has waited untried since bb52dbc). The delivery comes back shortly; a ping, which nobody
    would come back to, ends there and says why."""
    from synwevo_core import webhook_delivery
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_TIMEOUT_S", "1")
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")
    # TEST FIXTURE: the real pool, emptied. Every slot — a lookup an earlier test left running
    # gives its own back when it returns, and is waited for rather than left to free one mid-test.
    held, patience = 0, time.monotonic() + 10
    while held < webhook_delivery.DNS_SLOTS and webhook_delivery._DNS_SLOTS.acquire(timeout=max(0.0, patience - time.monotonic())):
        held += 1
    try:
        assert held == webhook_delivery.DNS_SLOTS
        d.run_once()
        ping = hub.c.post(f"{BASE}/{sub['id']}/ping", headers=hub.keys["admin"]).json()
    finally:
        for _ in range(held):
            webhook_delivery._DNS_SLOTS.release()
    assert rx.received == []
    row = sub_rows(hub.store, sub["id"])[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("pending", 0, "resolver_busy")
    assert row["next_attempt_at"] <= hub.clock() + W.LOCAL_RETRY_S
    assert (ping["status"], ping["attempts"], ping["last_error"]) == ("failed", 0, "resolver_busy")
    s = hub.store.one("SELECT consecutive_failures, last_failure_at FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert (s["consecutive_failures"], s["last_failure_at"]) == (0, None)
    # With a resolver free again it goes out, on its first attempt.
    hub.clock.advance(W.LOCAL_RETRY_S)
    d.run_once()
    row = sub_rows(hub.store, sub["id"])[0]
    assert (row["status"], row["attempts"], len(rx.received)) == ("delivered", 1, 1)


# ------------------------------------------------------------------ the resolver pool is shared
@pytest.fixture()
def slow_names(monkeypatch):
    """TEST FIXTURE: a name under ``slow.fixture.test`` is never answered while the test runs (an
    authoritative server that does not reply); every other name resolves as usual. A lookup given
    up at its deadline keeps its resolver until then, as one inside libc does. On the way out the
    lookups are answered and waited for, so no later test meets a resolver they still hold."""
    from synwevo_core import webhook_delivery
    state = SimpleNamespace(release=threading.Event(), asked=threading.Event())
    real = socket.getaddrinfo

    def lookup(host, *a, **k):
        if str(host).endswith(".slow.fixture.test"):
            state.asked.set()
            state.release.wait(30)
            raise socket.gaierror(socket.EAI_NONAME, "fixture")
        return real(host, *a, **k)

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    patience = time.monotonic() + 10                # lookups an earlier test left running end first
    while (webhook_delivery._DNS_SLOTS._value < webhook_delivery.DNS_SLOTS or webhook_delivery._OWNERS) \
            and time.monotonic() < patience:
        time.sleep(0.02)
    yield state
    state.release.set()
    patience = time.monotonic() + 10
    while (webhook_delivery._DNS_SLOTS._value < webhook_delivery.DNS_SLOTS or webhook_delivery._OWNERS) \
            and time.monotonic() < patience:
        time.sleep(0.02)


def test_one_workspace_cannot_take_the_resolvers_of_every_other(hub, monkeypatch, slow_names):
    """R3-WH-1: Docs named nobody to the shared resolver pool. A check of a name that never answers
    is given up at its deadline but keeps its resolver, so one workspace making such checks one
    after another emptied the pool for every other Docs workspace and every business application.
    Its lookups are counted for it now, as the suite's are: past its own few it is told to come
    back (429, not a verdict on the address), and the others are untouched."""
    from synwevo_core import webhook_delivery
    monkeypatch.setattr(W, "timeout_s", lambda: 0.3)
    free = webhook_delivery._DNS_SLOTS._value
    answers = [hub.c.post(BASE, json={"url": f"https://h{n}.slow.fixture.test/hook"}, headers=hub.keys["admin"])
               for n in range(webhook_delivery.OWNER_LOOKUPS + 2)]
    codes = [(r.status_code, r.json()["error"]["code"]) for r in answers]
    assert codes == [(400, "invalid_url")] * webhook_delivery.OWNER_LOOKUPS + [(429, "destination_check_busy")] * 2, codes
    assert [r.headers.get("retry-after") for r in answers[-2:]] == [str(W.LOCAL_RETRY_S)] * 2
    assert webhook_delivery._DNS_SLOTS._value >= free - webhook_delivery.OWNER_LOOKUPS
    # Another workspace has its address checked and its events delivered as before.
    rx = hub.receiver()
    hub.create(rx.url, key="b_admin", events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "tenant-b")
    d.run_once()
    assert len(rx.received) == 1
    assert hub.store.one("SELECT COUNT(*) AS n FROM webhook_subscriptions")["n"] == 1


def test_a_workspace_has_one_address_checked_at_a_time(hub, monkeypatch, slow_names):
    """R3-WH-1: a management check resolves on the request's own thread. As in the suite, a
    workspace has one checked at a time and another waits for it (429), whatever its address;
    other workspaces are not held up."""
    monkeypatch.setattr(W, "timeout_s", lambda: 2.0)
    first = {}
    slow = threading.Thread(target=lambda: first.setdefault("r", hub.c.post(
        BASE, json={"url": "https://h.slow.fixture.test/hook"}, headers=hub.keys["admin"])))
    slow.start()
    try:
        assert slow_names.asked.wait(5)
        rx = hub.receiver()
        r = hub.c.post(BASE, json={"url": rx.url}, headers=hub.keys["admin"])
        assert r.status_code == 429, r.text
        assert (r.json()["error"]["code"], r.headers.get("retry-after")) == ("destination_check_busy", "2")
        assert hub.c.post(BASE, json={"url": rx.url}, headers=hub.keys["b_admin"]).status_code == 201
    finally:
        slow.join(10)
    assert first["r"].status_code == 400 and first["r"].json()["error"]["code"] == "invalid_url"
    # Once it has been answered, the next check of the workspace goes ahead.
    assert hub.c.post(BASE, json={"url": rx.url}, headers=hub.keys["admin"]).status_code == 201


def test_owners_that_fill_the_pool_between_them_cost_another_workspaces_receiver_nothing(hub, monkeypatch, slow_names):
    """R3-WH-2, the reviewer's case: eight workspaces, each inside its own budget of lookups,
    between them hold every resolver of the process (a business application's or Docs'). A healthy
    Docs receiver of another workspace waits for one untried: it loses no attempt, its event is not
    given up, and three days of it do not disable the subscription as failing."""
    from synwevo_core import webhook_delivery as wd
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_TIMEOUT_S", "1")
    rx = hub.receiver()
    sub = hub.create(rx.url, events=["*"])
    d = hub.dispatcher()
    fixture_event(hub.store, "default")

    def give_up(owner):
        try:
            wd.resolve_checked("https://h.slow.fixture.test/hook", owner=owner, timeout=0.2)
        except wd.UrlUnresolvable:
            pass

    owners = [f"w_fixture_{n}" for n in range(wd.DNS_SLOTS // wd.OWNER_LOOKUPS)]
    lookups = [threading.Thread(target=give_up, args=(o,)) for o in owners for _ in range(wd.OWNER_LOOKUPS)]
    for t in lookups:
        t.start()
    for t in lookups:
        t.join(5)
    assert wd._DNS_SLOTS._value == 0 and [wd._OWNERS.get(o) for o in owners] == [wd.OWNER_LOOKUPS] * len(owners)
    for _ in range(2):
        d.run_once()
        hub.clock.advance(W.disable_after_s() + W.LOCAL_RETRY_S)
    row = sub_rows(hub.store, sub["id"])[0]
    assert (row["status"], row["attempts"], row["last_error"]) == ("pending", 0, "resolver_busy")
    s = hub.store.one("SELECT status, consecutive_failures FROM webhook_subscriptions WHERE id=?", (sub["id"],))
    assert (s["status"], s["consecutive_failures"]) == ("active", 0)
    # The lookups end; the event goes out.
    slow_names.release.set()
    patience = time.monotonic() + 10
    while wd._DNS_SLOTS._value < wd.DNS_SLOTS and time.monotonic() < patience:
        time.sleep(0.02)
    d.run_once()
    assert len(rx.received) == 1 and sub_rows(hub.store, sub["id"])[0]["status"] == "delivered"


def test_a_busy_resolver_is_not_a_verdict_on_the_address(hub, monkeypatch):
    """R3-WH-1, as in the suite: creating, moving and resuming check the address on the request's
    thread. With no resolver free the answer is "come back" (429) — Docs said the URL was invalid —
    and nothing is changed."""
    from synwevo_core import webhook_delivery
    rx = hub.receiver()
    sid = hub.create(rx.url, events=["*"])["id"]
    hub.c.post(f"{BASE}/{sid}/pause", headers=hub.keys["admin"])
    before = hub.store.all("SELECT * FROM webhook_subscriptions")
    monkeypatch.setattr(W, "timeout_s", lambda: 0.3)
    # TEST FIXTURE: the real pool, emptied (waiting for any lookup an earlier test left running).
    held, patience = 0, time.monotonic() + 10
    while held < webhook_delivery.DNS_SLOTS and webhook_delivery._DNS_SLOTS.acquire(timeout=max(0.0, patience - time.monotonic())):
        held += 1
    try:
        assert held == webhook_delivery.DNS_SLOTS
        for call, path, body in ((hub.c.post, BASE, {"url": rx.url + "/new"}), (hub.c.patch, f"{BASE}/{sid}", {"url": rx.url + "/moved"}),
                                 (hub.c.post, f"{BASE}/{sid}/resume", None)):
            r = call(path, headers=hub.keys["admin"], **({"json": body} if body is not None else {}))
            assert r.status_code == 429, (path, r.text)
            assert (r.json()["error"]["code"], r.headers.get("retry-after")) == ("destination_check_busy", str(W.LOCAL_RETRY_S))
    finally:
        for _ in range(held):
            webhook_delivery._DNS_SLOTS.release()
    assert hub.store.all("SELECT * FROM webhook_subscriptions") == before
    # the same calls a moment later, with a resolver free
    assert hub.c.post(f"{BASE}/{sid}/resume", headers=hub.keys["admin"]).json()["status"] == "active"


def test_sends_count_lookups_for_their_destination_and_wait_briefly_for_their_own(hub, monkeypatch):
    """R3-WH-1: a subscription's lookups are its workspace's, a deployment endpoint's are the
    endpoint's. Lookups that answer in a moment are not refused for running beside others of the
    same workspace: a workspace with several healthy receivers has all of them delivered in one
    round, though the dispatcher looks their addresses up at the same time."""
    from synwevo_core import webhook_delivery
    owners, real_resolve, real_lookup = [], webhook_delivery._resolve, socket.getaddrinfo
    monkeypatch.setattr(webhook_delivery, "_resolve", lambda h, p, d, owner: owners.append(owner) or real_resolve(h, p, d, owner))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: time.sleep(0.2) or real_lookup(*a, **k))
    rxs = [hub.receiver() for _ in range(4)]
    for rx in rxs:
        hub.create(rx.url, events=["*"])
    endpoint = hub.receiver()
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_URLS", endpoint.url)
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_SECRET", "s3cret-for-tests-0123456789")
    d = hub.dispatcher(concurrency=4)
    owners.clear()
    fixture_event(hub.store, "default")
    # A lookup that ends lets a waiting one of its owner go at once, not when the wait runs out.
    monkeypatch.setattr(webhook_delivery, "OWNER_WAIT_S", 5.0)
    started = time.monotonic()
    d.run_once()
    assert time.monotonic() - started < 3
    assert [len(rx.received) for rx in rxs + [endpoint]] == [1] * 5
    assert not hub.store.all("SELECT id FROM webhook_deliveries WHERE status!='delivered'")
    assert sorted(set(owners)) == sorted({"default", W.DEPLOYMENT_OWNER + endpoint.url}), owners


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
