"""Finished deployment factories release real routes, workers and temporary Stores."""
from __future__ import annotations

import threading
import weakref

import pytest
from fastapi.testclient import TestClient


def _factory_refs(api, previous_workers):
    from synwevo_core import workers

    return (weakref.ref(api), weakref.ref(api.state.identity.store), weakref.ref(api.state.bridge_worker),
            *(weakref.ref(worker) for worker in workers.live() if worker not in previous_workers))


@pytest.mark.parametrize("retain_previous", [False, True], ids=["no-held-factory", "held-stopped-factory"])
@pytest.mark.parametrize("start_jobs", [False, True], ids=["passive-workers", "running-workers"])
@pytest.mark.parametrize("context", [False, True], ids=["no-lifespan", "completed-lifespan"])
def test_test_teardown_releases_completed_factories(tmp_path, monkeypatch, release_factory_resources,
                                                    context, start_jobs, retain_previous):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "default-fixture"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    from synwevo_core import messaging_bridge, workers
    from synwevo_server.app import create_app

    initial_workers = weakref.WeakSet(workers.live())
    if retain_previous:
        # A caller may keep an already-stopped factory for inspection. live() documents
        # that it includes these referenced objects; it does not mean their threads run.
        previous = create_app(str(tmp_path / "held-previous"), role="all", start_jobs=start_jobs)
        previous_refs = _factory_refs(previous, initial_workers)
        release_factory_resources()
        assert workers.stop_all(wait=3.0) == []
        assert any(worker not in initial_workers for worker in workers.live())

    baseline_workers = weakref.WeakSet(workers.live())
    baseline = len(messaging_bridge._wakers)

    def build(index):
        before_threads = weakref.WeakSet(threading.enumerate())
        api = create_app(str(tmp_path / str(index)), role="all", start_jobs=start_jobs)
        threads = tuple(thread for thread in threading.enumerate() if thread not in before_threads)
        if start_jobs:
            assert api.state.bridge_worker._thread.is_alive()
        if context:
            with TestClient(api) as client:
                assert client.get("/healthz").status_code == 200
        else:
            client = TestClient(api)
            assert client.get("/healthz").status_code == 200
            client.close()
        return _factory_refs(api, baseline_workers), threads

    for index in range(3):
        refs, threads = build(index)
        release_factory_resources()
        assert all(ref() is None for ref in refs)
        assert len(messaging_bridge._wakers) == baseline
        assert set(workers.live()) <= set(baseline_workers)
        assert workers.stop_all(wait=3.0) == []
        assert all(not thread.is_alive() for thread in threads)

    if retain_previous:
        # The baseline is not exempt from collection once its last intentional owner leaves.
        del previous
        release_factory_resources()
        assert all(ref() is None for ref in previous_refs)
        assert set(workers.live()) <= set(initial_workers)
        assert len(messaging_bridge._wakers) == baseline
        assert workers.stop_all(wait=3.0) == []
