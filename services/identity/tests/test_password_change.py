"""Changing the password from inside the account.

Pinned: the current password is proved under the re-authentication limits; the new one must
meet the policy; the session making the change survives while every other session and token
ends; a security notice is queued; an account without a password is sent to the mailed reset.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
NEW = "quiet lantern river crossing autumn"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    ident.create_user("alice@example.com", PW, status="active")
    ident.create_user("nopass@example.com", None, status="active")
    return {"api": api, "store": store, "ident": ident}


def _signed_in(env, email=  "alice@example.com", pw=PW):
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": email, "password": pw}).status_code == 200
    return c, {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def test_change_keeps_this_session_and_ends_every_other_one(env):
    c, csrf = _signed_in(env)
    other, _ = _signed_in(env)
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": NEW}, headers=csrf)
    assert r.status_code == 200 and r.json()["sessions_revoked"] == 1
    assert c.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert env["ident"].authenticate("alice@example.com", NEW)["ok"]
    assert not env["ident"].authenticate("alice@example.com", PW)["ok"]
    notices = env["store"].all("SELECT subject FROM mail_outbox WHERE kind='security' AND to_addr='alice@example.com'")
    assert len(notices) == 1
    assert env["store"].one("SELECT 1 FROM audit_events WHERE action='auth.password.changed' AND result='ok'")


def test_wrong_current_password_changes_nothing(env):
    c, csrf = _signed_in(env)
    r = c.post("/api/auth/password", json={"current_password": "not-it", "new_password": NEW}, headers=csrf)
    assert r.status_code == 401
    assert env["ident"].authenticate("alice@example.com", PW)["ok"]
    assert not env["ident"].authenticate("alice@example.com", NEW)["ok"]


@pytest.mark.parametrize("bad", ["short", PW, "alice@example.com is my password!!", ""])
def test_the_new_password_must_meet_the_policy(env, bad):
    c, csrf = _signed_in(env)
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": bad}, headers=csrf)
    assert r.status_code == 400 and r.json()["error"] == "weak_password", r.text
    assert env["ident"].authenticate("alice@example.com", PW)["ok"]


def test_an_account_without_a_password_is_sent_to_the_mailed_reset(env):
    c = TestClient(env["api"])
    uid = env["ident"].by_email("nopass@example.com")["id"]
    from synwevo_core.sessions import COOKIE_NAME, Sessions
    raw, _ = Sessions(env["store"]).create(uid, workspace_id=None, amr="idp:test")
    c.cookies.set(COOKIE_NAME, raw)
    csrf_raw = "x" * 32
    c.cookies.set(CSRF_COOKIE, csrf_raw)
    r = c.post("/api/auth/password", json={"current_password": "", "new_password": NEW}, headers={CSRF_HEADER: csrf_raw})
    assert r.status_code == 409 and r.json()["error"] == "no_password"


def test_unknown_fields_and_csrf_are_enforced(env):
    c, csrf = _signed_in(env)
    assert c.post("/api/auth/password", json={"current_password": PW, "new_password": NEW, "extra": 1}, headers=csrf).status_code == 400
    assert c.post("/api/auth/password", json={"current_password": PW, "new_password": NEW}).status_code in (401, 403)
    assert env["ident"].authenticate("alice@example.com", PW)["ok"]
