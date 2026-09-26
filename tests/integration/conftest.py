"""Fixtures for cross-module verification.

These tests use `synwevo_server.app.create_app` — the deployment composer — because what they
check spans modules: a sign-in that starts on the account service and ends in Synwevo Docs, a
machine token that must be refused outside its workspace, a gate that has to hold for both.
Testing that through one module's factory would be testing something nobody deploys.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def composed(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    from synwevo_server.app import create_app
    return create_app(str(tmp_path / "d"), start_jobs=False)


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    """An application built outside `with TestClient(...)` never runs its shutdown hook. Without
    this net every such test left its runner, dispatcher and sweeper threads — and through them
    its store — alive; the verify container ran out of memory a third of the way through."""
    yield
    from synwevo_core.workers import stop_all
    stop_all(wait=3.0)
