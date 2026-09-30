"""Finished deployment factories release real routes, workers and temporary Stores."""
from __future__ import annotations

import weakref

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("start_jobs", [False, True], ids=["passive-workers", "running-workers"])
@pytest.mark.parametrize("context", [False, True], ids=["no-lifespan", "completed-lifespan"])
def test_test_teardown_releases_completed_factories(tmp_path, monkeypatch, release_factory_resources, context, start_jobs):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "default-fixture"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_TEST_INSTANCE", "1")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    from synwevo_core import messaging_bridge, workers
    from synwevo_server.app import create_app

    baseline = len(messaging_bridge._wakers)

    def build(index):
        api = create_app(str(tmp_path / str(index)), role="all", start_jobs=start_jobs)
        if start_jobs:
            assert api.state.bridge_worker._thread.is_alive()
        if context:
            with TestClient(api) as client:
                assert client.get("/healthz").status_code == 200
        else:
            client = TestClient(api)
            assert client.get("/healthz").status_code == 200
            client.close()
        return weakref.ref(api), weakref.ref(api.state.identity.store), weakref.ref(api.state.bridge_worker)

    for index in range(3):
        refs = build(index)
        release_factory_resources()
        assert all(ref() is None for ref in refs)
        assert len(messaging_bridge._wakers) == baseline
        assert workers.live() == []
