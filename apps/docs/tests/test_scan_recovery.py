"""Synthetic scanner outages through the real durable source service and HTTP contract."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from synwevo_core.context import as_tenant
from synwevo_core.errors import ServiceError
from synwevo_core.malware import ScanResult, ScanUnavailable
from synwevo_core.store import Store
from synwevo_docs import services

from .test_api_v1 import keys  # noqa: F401
from .test_scan_retry_lifecycle import app  # noqa: F401


class Outage:
    """Fault injection only; never installed outside these isolated tests."""
    def __init__(self):
        self.calls = 0

    def scan(self, data, *, sha256):
        self.calls += 1
        raise ScanUnavailable("synthetic outage")


def _source(app, monkeypatch, *, scan=None, status="scanning"):
    monkeypatch.setattr(app.parse_pool, "submit", lambda *args, **kwargs: None)
    project = app.create_project("Synthetic scan recovery")
    source = app.add_source(project["id"], "synthetic.csv", b"name,amount\nfixture,12\n")
    app.store.update("source_files", {"id": source["id"]}, {"status": status, "scan": scan or {"status": "pending"}})
    return source["id"]


def test_six_attempts_are_bounded_and_restart_keeps_exhaustion(app, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(services, "now", lambda: clock[0])
    scheduled = []
    monkeypatch.setattr(app, "_schedule_scan_retry", lambda sid, delay: scheduled.append(delay))
    scanner = Outage()
    app.scanner = scanner
    sid = _source(app, monkeypatch)
    for attempt in range(1, 7):
        app._scan_then_parse(sid)
        row = app.get_source(sid)
        assert row["status"] == "scan_failed"
        assert row["scan"]["attempts"] == attempt
        assert row["scan"]["cycle_attempts"] == attempt
        assert row["scan"]["retry_exhausted"] is (attempt == 6)
        with pytest.raises(ServiceError) as held:
            app.require_accepted(row)
        assert held.value.code == "not_yet_accepted"
        if attempt < 6:
            app._scan_then_parse(sid)  # early delivery does not spend another attempt
            assert scanner.calls == attempt
            clock[0] = row["scan"]["retry_at"]
    assert "retry_at" not in row["scan"] and scanner.calls == 6
    assert set(scheduled) == {30, 60, 120, 300, 600}
    app.close()
    restarted = services.App(Store(app.store.root))
    try:
        restarted.scanner = scanner
        restarted.resume_parsing()
        restarted.parse_pool.shutdown(wait=True)
        assert scanner.calls == 6
        assert restarted.get_source(sid)["scan"]["retry_exhausted"] is True
        assert restarted._scan_timers == {}
    finally:
        restarted.close()


@pytest.mark.parametrize("verdict", ["too_large", "error"])
def test_restart_respects_terminal_size_and_persisted_future_deadline(app, monkeypatch, verdict):
    sid = _source(app, monkeypatch, status="scan_failed",
                  scan={"status": verdict, "attempts": 2, "retry_at": 1500.0})
    monkeypatch.setattr(services, "now", lambda: 1000.0)
    app.close()
    restarted = services.App(Store(app.store.root))
    scanner = Outage()
    restarted.scanner = scanner
    try:
        restarted.resume_parsing()
        restarted.parse_pool.shutdown(wait=True)
        assert scanner.calls == 0
        if verdict == "error":
            assert restarted._scan_timers[sid].interval == 500.0
        else:
            assert restarted._scan_timers == {}
        assert restarted.get_source(sid)["scan"]["attempts"] == 2
    finally:
        restarted.close()


def test_legacy_unbounded_attempts_become_exhausted_without_another_scan(app, monkeypatch):
    sid = _source(app, monkeypatch, status="scan_failed", scan={"status": "error", "attempts": 97, "retry_at": 1})
    app.scanner = Outage()
    app._scan_then_parse(sid)
    rec = app.get_source(sid)["scan"]
    assert app.scanner.calls == 0 and rec["attempts"] == 97 and rec["cycle_attempts"] == 97
    assert rec["retry_exhausted"] is True and "retry_at" not in rec


def test_inflight_attempt_is_durable_and_duplicate_dispatch_does_not_scan_twice(app, monkeypatch):
    sid = _source(app, monkeypatch)
    entered, finish = threading.Event(), threading.Event()
    calls = []
    class BlockingScanner:
        def scan(self, data, *, sha256):
            calls.append(sha256)
            entered.set()
            assert finish.wait(5)
            return ScanResult("clean", sha256=sha256)
    app.scanner = BlockingScanner()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(app._scan_then_parse, sid)
        try:
            assert entered.wait(5)
            assert app.get_source(sid)["scan"]["attempts"] == 1
            app._scan_then_parse(sid)
            assert len(calls) == 1
        finally:
            finish.set()
        future.result(5)
    assert app.get_source(sid)["status"] == "ready"


@pytest.mark.parametrize("status", ["scanning", "scan_failed"])
def test_disabling_scanner_cannot_accept_previously_held_bytes(app, monkeypatch, status):
    sid = _source(app, monkeypatch, status=status, scan={"status": "error", "attempts": 1})
    app.scanner = None
    app._scan_then_parse(sid)
    row = app.get_source(sid)
    assert row["status"] == "scan_failed" and row["scan"]["retry_exhausted"] is True
    with pytest.raises(ServiceError) as refused:
        app.retry_source_scan(sid, row["version"], actor_kind="user", actor_id=None)
    assert refused.value.code == "scan_not_configured"


def _exhausted(app, monkeypatch):
    sid = _source(app, monkeypatch, status="scan_failed",
                  scan={"status": "error", "attempts": 12, "cycle_attempts": 6, "retry_exhausted": True})
    app.scanner = Outage()
    return sid


def test_manual_retry_is_atomic_audited_and_preserves_total_attempts(app, monkeypatch):
    sid = _exhausted(app, monkeypatch)
    scheduled = []
    monkeypatch.setattr(app, "_schedule_scan_retry", lambda sid, delay: scheduled.append((sid, delay)))
    def retry():
        try:
            return app.retry_source_scan(sid, 1, actor_kind="machine", actor_id="synthetic-key")
        except ServiceError as e:
            return e.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: retry(), range(2)))
    assert sum(isinstance(x, dict) for x in results) == 1 and "source_changed" in results
    assert scheduled == [(sid, 0)]
    row = app.get_source(sid)
    assert row["version"] == 2 and row["status"] == "scanning"
    assert row["scan"]["attempts"] == 12 and row["scan"]["cycle_attempts"] == 0
    audit = app.store.all("SELECT actor_kind,actor_id,meta FROM audit_events WHERE action='docs.source.scan.retry'")
    assert len(audit) == 1 and audit[0]["actor_id"] == "synthetic-key"
    app._scan_then_parse(sid)
    rec = app.get_source(sid)["scan"]
    assert rec["attempts"] == 13 and rec["cycle_attempts"] == 1 and not rec["retry_exhausted"]


def test_audit_failure_rolls_back_manual_retry_and_does_not_queue(app, monkeypatch):
    from synwevo_core import auth
    sid = _exhausted(app, monkeypatch)
    before = app.get_source(sid)
    monkeypatch.setattr(app, "_schedule_scan_retry", lambda *a: pytest.fail("uncommitted retry queued"))
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    monkeypatch.setattr(auth, "audit_event", fail)
    with pytest.raises(RuntimeError, match="synthetic"):
        app.retry_source_scan(sid, 1, actor_kind="machine", actor_id="synthetic-key")
    assert app.get_source(sid) == before


@pytest.mark.parametrize("surface", ["ui", "machine"])
def test_http_retry_contract_duplicate_and_write_scope(api, keys, monkeypatch, surface):
    app = api.c.app_state.app
    sid = _exhausted(app, monkeypatch)
    monkeypatch.setattr(app, "_schedule_scan_retry", lambda *a: None)
    path = f"/api/sources/{sid}/scan/retry" if surface == "ui" else f"/api/v1/files/{sid}/scan/retry"
    headers = {} if surface == "ui" else keys["rw"]
    if surface == "machine":
        assert api.c.post(path, json={"version": 1}, headers=keys["ro"]).status_code == 403
    assert api.c.get(path, headers=headers).status_code == 404  # the app's final GET catch-all
    assert app.get_source(sid)["scan"]["retry_exhausted"] is True
    for body in ({"version": True}, {"version": 0}, {"version": "1"}, {"version": 1, "force": True}):
        assert api.c.post(path, json=body, headers=headers).status_code == 400
    response = api.c.post(path, json={"version": 1}, headers=headers)
    assert response.status_code == 202, response.text
    assert response.json()["version"] == 2 and response.json()["scan"]["attempts"] == 12
    assert api.c.post(path, json={"version": 1}, headers=headers).status_code == 409
    assert app.store.one("SELECT COUNT(*) n FROM audit_events WHERE action='docs.source.scan.retry'")["n"] == 1


@pytest.mark.parametrize("status,verdict", [("rejected", "infected"), ("error", "encrypted"), ("scan_failed", "too_large"), ("ready", "clean")])
def test_manual_retry_cannot_reopen_other_verdicts(app, monkeypatch, status, verdict):
    sid = _source(app, monkeypatch, status=status, scan={"status": verdict, "retry_exhausted": True})
    app.scanner = Outage()
    with pytest.raises(ServiceError) as refused:
        app.retry_source_scan(sid, 1, actor_kind="user", actor_id=None)
    assert refused.value.code == "scan_not_retryable" and app.scanner.calls == 0


def test_retry_cannot_reach_another_workspace_or_removed_source(app, monkeypatch):
    sid = _exhausted(app, monkeypatch)
    with as_tenant("other-synthetic-workspace"):
        with pytest.raises(ServiceError) as refused:
            app.retry_source_scan(sid, 1, actor_kind="user", actor_id=None)
        assert refused.value.status == 404
    app.store.update("source_files", {"id": sid}, {"removed": 1})
    with pytest.raises(ServiceError) as refused:
        app.retry_source_scan(sid, 1, actor_kind="user", actor_id=None)
    assert refused.value.status == 404
