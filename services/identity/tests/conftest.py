"""Fixtures for the account service's own tests.

The application under test is `synwevo_identity.app.create_app` — the account service alone,
with no document engine mounted. That is the point of the split: a test that needs a Synwevo Docs
route to pass belongs in `tests/integration/`, and this factory makes that obvious instead of
letting it drift.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def identity_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    with TestClient(api) as c:
        c.app_state = api.state
        yield c


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    """An application built outside `with TestClient(...)` never runs its shutdown hook. Without
    this net every such test left its runner, dispatcher and sweeper threads — and through them
    its store — alive; the verify container ran out of memory a third of the way through."""
    yield
    from synwevo_core.workers import stop_all
    stop_all(wait=3.0)
