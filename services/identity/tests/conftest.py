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
