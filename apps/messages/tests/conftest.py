"""Fixtures for Synwevo Message's own tests.

The application under test is `synwevo_messages.app.create_app` — the messages application alone,
as the `messages` role, with no account service and no document engine mounted. A relying party
serves no `/api/auth/login`, so a signed-in caller is made the way the platform makes one after
the single-sign-on round trip: a session row minted through `synwevo_core.sessions.Sessions` and
handed to the client as the two cookies (docs/integration/app-onboarding.md §10). A test that
needs the account service's routes to pass belongs in `tests/integration/`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
#: This origin's own address and the account service's, as the process would be configured.
MESSAGES = "http://message.testserver"
ACCOUNTS = "http://accounts.testserver"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    """One organization with an owner and three members, plus an outsider; the application
    registered under its own address, so inbox items and `/api/auth/state` name this origin."""
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_ROLE", "messages")
    monkeypatch.setenv("SYNWEVO_APP_URL", MESSAGES)
    monkeypatch.setenv("SYNWEVO_ISSUER", ACCOUNTS)
    monkeypatch.setenv("SYNWEVO_OIDC_CLIENT_ID", "messages-web")
    monkeypatch.setenv("SYNWEVO_OIDC_APP_ID", "messages")
    from synwevo_messages.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member"), ("cid", "Cid Member"), ("outsider", "Oz Outsider")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["owner"]["id"], "Chat Org", "Chat Team", "fixture-idem-chat-org-0001")
    oid = created["organization"]["id"]
    for key in ("ann", "ben", "cid"):
        ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (oid, users[key]["id"], users["owner"]["id"]))
    with TestClient(api) as c:
        c.api, c.ident, c.users, c.oid = api, ident, users, oid
        c.sign_in = lambda key: sign_in(c, key)          # pytest's importlib mode keeps conftest unimportable
        yield c


def sign_in(c: TestClient, key: str) -> dict:
    """Become `key` on this origin: a session row of this application (what the SSO callback
    mints), the session cookie and the double-submit CSRF cookie. Returns the CSRF header."""
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, Sessions
    raw, _ = Sessions(c.ident.store).create(c.users[key]["id"], workspace_id=None, amr="password")
    c.cookies.clear()
    c.cookies.set(COOKIE_NAME, raw)
    c.cookies.set(CSRF_COOKIE, "fixture-csrf")
    return {CSRF: "fixture-csrf"}


@pytest.fixture(autouse=True)
def _no_worker_outlives_its_test():
    """An application built outside `with TestClient(...)` never runs its shutdown hook. Without
    this net every such test left its threads — and through them its store — alive; the verify
    container ran out of memory a third of the way through."""
    yield
    from synwevo_core.workers import stop_all
    stop_all(wait=3.0)
