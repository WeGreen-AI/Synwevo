"""Fixtures for the business applications' own tests.

The application under test is `synwevo_suite.app.create_app` — the applications alone, as the
`portal` role serves them, with no account service, no portal pages and no document engine. A
signed-in caller is made the way the platform makes one after single sign-on: a session row
minted through `synwevo_core.sessions.Sessions` and handed to the client as the two cookies
(docs/integration/app-onboarding.md §10). Tests that need other modules belong in
`tests/integration/`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
WORKPLACE = "http://workplace.testserver"
ACCOUNTS = "http://accounts.testserver"


def make_client(tmp_path, monkeypatch, *, released: str = "all"):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_ROLE", "portal")
    monkeypatch.setenv("SYNWEVO_APP_URL", WORKPLACE)
    monkeypatch.setenv("SYNWEVO_ISSUER", ACCOUNTS)
    monkeypatch.setenv("SYNWEVO_OIDC_CLIENT_ID", "portal-web")
    monkeypatch.setenv("SYNWEVO_OIDC_APP_ID", "platform")
    monkeypatch.setenv("SYNWEVO_SUITE_APPS", released)
    from synwevo_suite.app import create_app
    return create_app(str(tmp_path / "d"), start_jobs=False)


class People:
    """Two workspaces, A and B, and the people in them (fixture accounts, isolated data).

    * ``alice`` — owner of A, viewer in B
    * ``bob`` — editor in A
    * ``vera`` — viewer in A
    * ``oscar`` — owner of B
    * ``out`` — belongs to neither
    """

    def __init__(self, api):
        self.api = api
        self.ident = api.state.identity
        self.store = self.ident.store
        self.users: dict[str, dict] = {}
        for key, name in (("alice", "Alice Owner"), ("bob", "Bob Editor"), ("vera", "Vera Viewer"),
                          ("oscar", "Oscar Other"), ("out", "Oz Outsider")):
            u = self.ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
            self.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
            self.users[key] = u
        self.a = self.ident.create_workspace("Workspace A", owner_id=self.users["alice"]["id"])["id"]
        self.b = self.ident.create_workspace("Workspace B", owner_id=self.users["oscar"]["id"])["id"]
        self.ident.add_member(self.a, self.users["bob"]["id"], "editor")
        self.ident.add_member(self.a, self.users["vera"]["id"], "viewer")
        self.ident.add_member(self.b, self.users["alice"]["id"], "viewer")

    def uid(self, key: str) -> str:
        return self.users[key]["id"]


def sign_in(c: TestClient, key: str) -> dict:
    """Become `key` on this origin; returns the CSRF header for state-changing calls."""
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
    raw, _ = Sessions(c.people.store).create(c.people.uid(key), workspace_id=None, amr="password")
    c.cookies.clear()
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "fixture-csrf")
    return {CSRF: "fixture-csrf"}


@pytest.fixture()
def suite(tmp_path, monkeypatch):
    api = make_client(tmp_path, monkeypatch)
    people = People(api)
    with TestClient(api, base_url=WORKPLACE) as c:
        c.api, c.people = api, people
        c.sign_in = lambda key: sign_in(c, key)
        c.caps = api.state.capabilities
        yield c


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    yield
    from synwevo_core.workers import stop_all
    stop_all(wait=3.0)
