"""The business applications as deployed: served by the workplace origin, refused everywhere else.

`synwevo_suite` is mounted by the composer in the `portal` role and in `all`
(docs/agent-work/DECISIONS.md D1). On `all` its API must not be mistaken for a Synwevo Docs route
(the Docs gate would check the wrong application and read the workspace from the tab binding); on
Synwevo Docs' and the account service's own origins it must be refused by name.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _app(tmp_path, monkeypatch, role: str, released: str = "all"):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", released)
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://workplace.testserver")
    from synwevo_server.app import create_app
    return create_app(str(tmp_path / "d"), role=role, start_jobs=False)


def _signed_in(c: TestClient, api) -> dict:
    ident = api.state.identity
    u = ident.create_user("mount@example.test", "harbor lights guide the evening ferry home", status="active")
    ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
    ws = ident.create_workspace("Mount W", owner_id=u["id"])
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
    raw, _ = Sessions(ident.store).create(u["id"], workspace_id=ws["id"], amr="password")
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "fixture-csrf")
    return {"user": u, "workspace": ws}


@pytest.mark.parametrize("role", ["portal", "all"])
def test_workplace_roles_serve_the_business_applications(tmp_path, monkeypatch, role):
    api = _app(tmp_path, monkeypatch, role)
    with TestClient(api, base_url="http://workplace.testserver") as c:
        _signed_in(c, api)
        r = c.get("/api/suite/catalog")
        assert r.status_code == 200, r.text
        assert isinstance(r.json()["apps"], list)
        assert c.get("/api/suite/runs").status_code == 200


@pytest.mark.parametrize("role", ["app", "accounts", "messages"])
def test_other_roles_refuse_the_business_applications_by_name(tmp_path, monkeypatch, role):
    api = _app(tmp_path, monkeypatch, role)
    with TestClient(api, base_url="http://other.testserver") as c:
        r = c.get("/api/suite/catalog")
        assert r.status_code == 404
        assert r.json()["error"] in ("not_in_this_role", "not_found")


def test_unreleased_applications_are_disabled_in_the_registry(tmp_path, monkeypatch):
    api = _app(tmp_path, monkeypatch, "portal", released="")
    store = api.state.identity.store
    for app_id in ("forms", "tables", "dataprep", "approvals", "extract", "agent"):
        row = store.one("SELECT status FROM apps WHERE id=?", (app_id,))
        assert row is None or row["status"] == "disabled"


def test_docs_routes_are_unaffected_on_all(tmp_path, monkeypatch):
    api = _app(tmp_path, monkeypatch, "all")
    with TestClient(api, base_url="http://workplace.testserver") as c:
        _signed_in(c, api)
        assert c.get("/api/projects").status_code == 200
