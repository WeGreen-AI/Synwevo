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


def _client(data_dir, monkeypatch, *, mode: str, role: str) -> TestClient:
    """A server in one (mode, role) combination, with its own empty data directory."""
    import importlib
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", mode)
    monkeypatch.setenv("DOCWEAVE_ROLE", role)
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    import docweave.api.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.create_app(str(data_dir), start_jobs=False))


HEADER = {"Authorization": "Basic " + base64.b64encode(b"probe:probe-pass-123").decode()}

# paths that start with "/api" but are not "/api/..." — the shape that used to slip through
SLIPPERY = ["/api", "/apifoo", "/apiv1", "/api\\projects"]


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/api/projects", *SLIPPERY])
def test_no_path_reaches_content_without_credentials(basic_client, path):
    assert basic_client.get(path).status_code == 401, f"{path} 绕过了鉴权"


@pytest.mark.parametrize("path", ["/", "/api/projects"])
def test_correct_credentials_still_work(basic_client, path):
    assert basic_client.get(path, headers=HEADER).status_code == 200


# ---------------------------------------------------------------- health probes
@pytest.mark.parametrize("mode", ["none", "basic", "proxy", "session"])
@pytest.mark.parametrize("role", ["all", "accounts", "portal", "app"])
def test_the_probes_are_anonymous_in_every_mode_and_role(tmp_path, monkeypatch, mode, role):
    """A container's restart policy must not depend on a shared password.

    Measured before the fix: role never mattered, and `session` was already fine — the real
    contradiction was `basic`, where both probes answered 401 while ports.md, the committed
    OpenAPI (their security is []) and permissions.md §5.3 all said anonymous. The backlog entry
    said "basic/session"; only half of it was true.
    """
    c = _client(tmp_path / f"{mode}-{role}", monkeypatch, mode=mode, role=role)
    assert c.get("/healthz").status_code == 200
    assert c.get("/readyz").status_code in (200, 503)      # 503 = no worker in this harness


@pytest.mark.parametrize("path", ["/healthzfoo", "/readyzfoo", "/healthz/", "/readyz/extra",
                                  "/api/health"])
def test_only_the_two_exact_probe_paths_are_exempt(tmp_path, monkeypatch, path):
    """An exact set, not a prefix. A route added later called /healthz-debug must not inherit
    anonymity, and /api/health is an undocumented duplicate that stays behind the gate rather
    than carving a hole in the /api/ prefix that protects everything else."""
    c = _client(tmp_path / "strict", monkeypatch, mode="basic", role="all")
    assert c.get(path).status_code == 401, f"{path} 不应当匿名可达"
