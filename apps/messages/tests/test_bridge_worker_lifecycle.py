"""Bridge lifecycle owns no fixture state once it has stopped or never started."""
from __future__ import annotations

import gc
import time
import weakref

from synwevo_core import messaging_bridge as bridge, workers
from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.identity import Identity
from synwevo_core.messaging import Messaging
from synwevo_core.store import Store


def _worker(path, *, start=False):
    store = Store(path)
    ident = Identity(store)
    return bridge.BridgeWorker(store, ident, Messaging(store, ident), start=start)


def _emit(store, event_id):
    # Explicit orphan-object fixture: a real outbox item must receive a recorded skip.
    with store.tx() as tx:
        assert bridge.emit(tx, app=DOCS_APP_ID, event_id=event_id, kind="docs.comment",
                           object_kind="project", object_id="fixture-no-conversation")


def test_passive_workers_release_their_stores_without_lifespan(tmp_path):
    original = tuple(bridge._wakers)

    def build(index):
        worker = _worker(tmp_path / str(index))
        return weakref.ref(worker), weakref.ref(worker.store)

    refs = [build(i) for i in range(3)]
    try:
        assert workers.stop_all(wait=0.1) == []
        gc.collect()
        assert all(worker() is None and store() is None for worker, store in refs)
        assert tuple(bridge._wakers) == original
    finally:
        # Keep a failing regression from retaining its temporary databases in this process.
        for callback in tuple(bridge._wakers):
            if callback not in original:
                callback.__self__.stop()


def test_passive_worker_still_processes_real_outbox_manually(tmp_path):
    worker = _worker(tmp_path / "passive")
    try:
        _emit(worker.store, "fixture-manual-delivery")
        assert worker.run_once() == {"delivered": 0, "skipped": 1, "failed": 0}
        row = worker.store.one("SELECT * FROM messaging_outbox WHERE event_id=?", ("fixture-manual-delivery",))
        assert row["delivered_at"] is not None
        assert row["last_error"] == bridge.SKIPPED_NO_CONVERSATION
        assert row["attempts"] == 1
        assert worker.run_once() == {"delivered": 0, "skipped": 0, "failed": 0}
    finally:
        worker.stop()


def test_running_worker_wakes_on_emit_and_stop_unsubscribes(tmp_path, monkeypatch):
    # A completion within two seconds must come from emit's wake, not the polling fallback.
    monkeypatch.setattr(bridge.BridgeWorker, "POLL_S", 30.0)
    baseline = tuple(bridge._wakers)
    worker = _worker(tmp_path / "running", start=True)
    worker_ref, store_ref = weakref.ref(worker), weakref.ref(worker.store)
    try:
        _emit(worker.store, "fixture-background-delivery")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            row = worker.store.one("SELECT * FROM messaging_outbox WHERE event_id=?", ("fixture-background-delivery",))
            if row["delivered_at"] is not None:
                break
            time.sleep(0.01)
        assert row["delivered_at"] is not None
        assert row["last_error"] == bridge.SKIPPED_NO_CONVERSATION
        assert row["attempts"] == 1
        worker.stop()
        worker.stop()
        assert not worker._thread.is_alive()
        assert tuple(bridge._wakers) == baseline
        worker._wake.clear()
        _emit(worker.store, "fixture-after-stop")
        assert not worker._wake.is_set()
        assert worker.store.one("SELECT delivered_at FROM messaging_outbox WHERE event_id=?", ("fixture-after-stop",))["delivered_at"] is None
    finally:
        worker.stop()
    del worker
    gc.collect()
    assert worker_ref() is None and store_ref() is None
