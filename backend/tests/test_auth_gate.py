"""The authentication gate must have no path that falls past it.

Found by running the app rather than reading it: under DOCWEAVE_UI_AUTH=basic, "/" returned 401
while "/api" and "/apifoo" returned 200. The branch read
`elif path.startswith("/api/") or not path.startswith("/api")`, and a path beginning with "/api"
without the slash satisfied neither disjunct — so the gate was skipped and set_tenant never ran.
"""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def basic_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "basic")
    import importlib

    from docweave import auth as auth_mod
    from docweave.store import Store
    store = Store(tmp_path / "d")
    auth_mod.Auth(store).add_ui_user("probe", "probe-pass-123")

    import docweave.api.main as main
    importlib.reload(main)
    return TestClient(main.app)


HEADER = {"Authorization": "Basic " + base64.b64encode(b"probe:probe-pass-123").decode()}

# paths that start with "/api" but are not "/api/..." — the shape that used to slip through
SLIPPERY = ["/api", "/apifoo", "/apiv1", "/api\\projects"]


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/api/projects", *SLIPPERY])
def test_no_path_reaches_content_without_credentials(basic_client, path):
    assert basic_client.get(path).status_code == 401, f"{path} 绕过了鉴权"


@pytest.mark.parametrize("path", ["/", "/api/projects"])
def test_correct_credentials_still_work(basic_client, path):
    assert basic_client.get(path, headers=HEADER).status_code == 200
