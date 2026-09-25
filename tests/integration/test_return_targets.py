"""The account page's "back to my work" targets come from the server.

Pinned: `/api/auth/me` lists the configured portal and only the applications the person can open
(`Apps.apps_for`), each as its sign-in start URL on the application's own origin; a person with no
entitlement gets the portal alone; the list is the account service's answer and carries nothing
taken from the request.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.apps import DOCS_APP_ID, Apps
from synwevo_core.identity import Identity
from synwevo_core.store import Store
from synwevo_server.app import create_app

PW = "isolated-return-targets-passphrase"
ACCOUNTS = "http://accounts.localhost:8770"
DOCS = "http://docweave.localhost:8765"
PORTAL = "http://portal.localhost:8769"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    root = tmp_path / "isolated"
    for key, value in {"SYNWEVO_DATA_DIR": str(root), "SYNWEVO_UI_AUTH": "session", "SYNWEVO_TEST_INSTANCE": "1",
                       "SYNWEVO_COOKIE_SECURE": "0", "SYNWEVO_ISSUER": ACCOUNTS, "SYNWEVO_APP_URL": DOCS,
                       "SYNWEVO_PORTAL_URL": PORTAL}.items():
        monkeypatch.setenv(key, value)
    api = create_app(str(root), role="accounts", start_jobs=False)
    store = Store(root)
    ident = Identity(store)
    alice = ident.create_user("alice@example.test", PW, status="active")
    nobody = ident.create_user("nobody@example.test", PW, status="active")
    apps = Apps(store, ident)
    personal = ident.workspaces_for(alice["id"])[0]["id"]
    return {"api": api, "root": root, "store": store, "ident": ident, "apps": apps, "alice": alice, "nobody": nobody, "personal": personal}


def _login(api, email):
    c = TestClient(api, base_url=ACCOUNTS)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_targets_are_the_portal_plus_the_applications_this_person_may_open(world):
    c = _login(world["api"], world["alice"]["email"])
    me = c.get("/api/auth/me").json()
    ids = [t["id"] for t in me["return_targets"]]
    assert ids[0] == "portal" and me["return_targets"][0]["url"] == PORTAL + "/workspace"
    docs = [t for t in me["return_targets"] if t["id"] == DOCS_APP_ID]
    if world["apps"].check_access(world["alice"]["id"], DOCS_APP_ID, world["personal"])[0]:
        assert len(docs) == 1
        assert docs[0]["url"].startswith(DOCS + "/auth/start?") and "workspace_id=" in docs[0]["url"]
        assert docs[0]["workspace_id"] == world["personal"]
    else:
        assert docs == []


def test_the_application_target_follows_the_entitlement_check(world):
    c = _login(world["api"], world["nobody"]["email"])
    me = c.get("/api/auth/me").json()
    docs = [t for t in me["return_targets"] if t["id"] == DOCS_APP_ID]
    ok, _, _ = world["apps"].check_access(world["nobody"]["id"], DOCS_APP_ID, None)
    assert (len(docs) == 1) == ok
    assert me["return_targets"][0]["id"] == "portal"


def test_without_a_portal_url_the_list_holds_applications_only(world, monkeypatch):
    monkeypatch.delenv("SYNWEVO_PORTAL_URL")
    c = _login(world["api"], world["alice"]["email"])
    me = c.get("/api/auth/me").json()
    assert all(t["id"] != "portal" for t in me["return_targets"])
    for t in me["return_targets"]:
        assert t["url"].startswith("http://") and "/auth/start?" in t["url"]


def test_the_public_state_names_the_application_for_the_back_link(world):
    accounts = TestClient(world["api"], base_url=ACCOUNTS)
    assert accounts.get("/api/auth/state").json()["app_id"] is None
    docs = TestClient(create_app(str(world["root"]), role="app", start_jobs=False), base_url=DOCS)
    assert docs.get("/api/auth/state").json()["app_id"] == DOCS_APP_ID
