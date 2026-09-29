"""Keep me signed in: a persistent cookie with a longer idle window, nothing else.

The properties worth pinning: the choice is opt-in and only a JSON `true` counts; a remembered
session gets a Max-Age on both cookies bounded by the absolute deadline; an ordinary session
gets browser-session cookies; the absolute clock is identical either way; platform
administrators keep their short leash; and the device list says which sessions are remembered.
"""
from __future__ import annotations

import importlib
import time

import pyotp
import pytest

from .platform_fixture import make_platform_admin
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import (ABSOLUTE_SECONDS, ADMIN_IDLE_SECONDS, COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER,
                                   IDLE_SECONDS, PERSISTENT_IDLE_SECONDS, Sessions)
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    ident.create_user("alice@example.com", PW, status="active")
    make_platform_admin(ident.store, ident.create_user("root@example.com", PW, status="active"))
    return {"api": api, "store": store, "ident": ident}


def _cookie_max_age(response, name: str) -> int | None:
    for header in response.headers.get_list("set-cookie"):
        if header.startswith(name + "="):
            for part in header.split(";"):
                k, _, v = part.strip().partition("=")
                if k.lower() == "max-age":
                    return int(v)
            return None
    raise AssertionError(f"no {name} cookie in {response.headers}")


def _row(env, c):
    sid = Sessions(env["store"]).load(c.cookies.get(COOKIE_NAME))
    assert sid
    return sid


def test_default_sign_in_is_a_browser_session_cookie(env):
    c = TestClient(env["api"])
    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    assert r.status_code == 200 and r.json()["session"]["persistent"] is False
    assert _cookie_max_age(r, COOKIE_NAME) is None and _cookie_max_age(r, CSRF_COOKIE) is None
    row = _row(env, c)
    assert row["persistent"] == 0
    assert row["idle_expires_at"] - row["last_seen_at"] == pytest.approx(IDLE_SECONDS, abs=2)


def test_remember_extends_idle_and_persists_both_cookies_within_the_absolute_deadline(env):
    c = TestClient(env["api"])
    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW, "remember": True})
    assert r.status_code == 200 and r.json()["session"]["persistent"] is True
    max_age = _cookie_max_age(r, COOKIE_NAME)
    assert max_age is not None and 0 < max_age <= ABSOLUTE_SECONDS
    assert _cookie_max_age(r, CSRF_COOKIE) == max_age
    row = _row(env, c)
    assert row["persistent"] == 1
    assert row["idle_expires_at"] - row["last_seen_at"] == pytest.approx(PERSISTENT_IDLE_SECONDS, abs=2)
    # Remembering a device never removes the absolute clock.
    assert row["absolute_expires_at"] - row["created_at"] == pytest.approx(ABSOLUTE_SECONDS, abs=2)
    assert max_age <= row["absolute_expires_at"] - now() + 2


@pytest.mark.parametrize("value", ["true", 1, "yes", "on", [True], {"remember": True}])
def test_only_a_json_true_counts_as_remember(env, value):
    c = TestClient(env["api"])
    r = c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW, "remember": value})
    assert r.status_code == 200 and r.json()["session"]["persistent"] is False
    assert _cookie_max_age(r, COOKIE_NAME) is None


def test_platform_administrators_are_never_remembered(env):
    c = TestClient(env["api"])
    r = c.post("/api/auth/login", json={"email": "root@example.com", "password": PW, "remember": True})
    assert r.status_code == 200 and r.json()["session"]["persistent"] is False
    assert _cookie_max_age(r, COOKIE_NAME) is None
    row = _row(env, c)
    assert row["idle_expires_at"] - row["last_seen_at"] == pytest.approx(ADMIN_IDLE_SECONDS, abs=2)


def test_remember_survives_the_second_factor_step_and_keeps_the_factor_in_amr(env):
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    csrf = {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=csrf).status_code == 200
    begin = c.post("/api/auth/mfa/totp/begin", json={}, headers=csrf).json()
    assert c.post("/api/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(begin["secret"]).now()}, headers=csrf).status_code == 200
    c.post("/api/auth/logout", headers=csrf)

    fresh = TestClient(env["api"])
    first = fresh.post("/api/auth/login", json={"email": "alice@example.com", "password": PW, "remember": True}).json()
    assert first["mfa_required"]
    code = pyotp.TOTP(begin["secret"]).at(int(time.time()) + 30)
    r = fresh.post("/api/auth/login/mfa", json={"ticket": first["ticket"], "code": code, "remember": True})
    assert r.status_code == 200, r.text
    assert r.json()["session"] == {**r.json()["session"], "amr": "password+totp", "persistent": True}
    assert _cookie_max_age(r, COOKIE_NAME)


def test_device_list_says_which_sessions_are_remembered(env):
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW, "remember": True})
    other = TestClient(env["api"])
    other.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    rows = c.get("/api/auth/sessions").json()["items"]
    assert sorted((r["current"], r["persistent"]) for r in rows) == [(False, False), (True, True)]


def test_a_remembered_cookie_is_refused_once_the_server_row_expired(env):
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW, "remember": True})
    assert c.get("/api/auth/me").status_code == 200
    # The cookie is still in the jar; the row's absolute deadline is what decides.
    env["store"].execute("UPDATE sessions SET absolute_expires_at=?", (now() - 1,))
    assert c.get("/api/auth/me").status_code == 401
