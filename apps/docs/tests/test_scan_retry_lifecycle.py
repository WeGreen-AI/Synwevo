"""Deterministic scan-retry shutdown interleavings; synthetic sources, no scanner service."""
import threading

import pytest

from synwevo_core import workers
from synwevo_core.store import Store
from synwevo_docs import malware
from synwevo_docs.services import App


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_MALWARE_SCAN", "off")
    service = App(Store(tmp_path / "isolated"))
    try:
        yield service
    finally:
        # Also clean up on the unfixed implementation when an assertion intentionally fails.
        for timer in list(service._scan_timers.values()):
            timer.cancel()
            timer.join(timeout=1)
        service.close()
        service.parse_pool.shutdown(wait=True, cancel_futures=True)


def test_worker_registry_stop_cancels_pending_scan_retries(app):
    app._schedule_scan_retry("synthetic-source", 60)
    timer = app._scan_timers["synthetic-source"]
    assert workers.stop_all(wait=0) == []
    assert timer.finished.is_set(), "stopping the app must cancel its retry timer"
    timer.join(timeout=1)
    assert not timer.is_alive()
    assert app._scan_timers == {}
    app.stop()  # lifespan and the registry may both close the same app


def test_timer_callback_already_dispatched_before_close_does_not_submit_after_it(app):
    app._schedule_scan_retry("synthetic-source", 60)
    timer = app._scan_timers["synthetic-source"]
    app.close()
    # Timer.cancel cannot stop a callback that already left Timer.run's wait. Invoke the
    # captured callback directly to force that exact ordering, without racing a wall clock.
    timer.function()
    assert app._scan_timers == {}


def test_a_scan_finishing_after_close_keeps_quarantine_but_does_not_restart_a_timer(app):
    entered, release = threading.Event(), threading.Event()

    class UnavailableScanner:
        """Controlled scanner-outage fixture: the scan returns only after App.close()."""
        def scan(self, data, *, sha256):
            entered.set()
            assert release.wait(5), "fixture scan was never released"
            raise malware.ScanUnavailable("synthetic scanner outage")

    app.scanner = UnavailableScanner()
    try:
        project = app.create_project("Synthetic shutdown source")
        source = app.add_source(project["id"], "synthetic.csv", b"name\nfixture\n")
        assert entered.wait(5)
        app.close()
    finally:
        release.set()
        app.parse_pool.shutdown(wait=True, cancel_futures=True)
    stored = app.store.one("SELECT status,scan FROM source_files WHERE id=?", (source["id"],))
    assert stored["status"] == "scan_failed" and stored["scan"]["attempts"] == 1
    assert stored["scan"]["retry_at"] > stored["scan"]["checked_at"], "the next process can resume the quarantined source"
    assert app._scan_timers == {}, "a finishing scan must not recreate work after shutdown"
