"""What an administrator may see about the external sign-in providers.

Pinned: the status route lists configured providers with the redirect URI, scopes, issuer and
whether id and secret are present — never the secret, never the whole client id; the known but
unconfigured providers are listed with the variable names that are missing; an ordinary account
gets the administrator routes' 404 (the one-factor administrator's 403 is pinned in test_email_code_login).
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"
CLIENT_ID, CLIENT_SECRET = "synwevo-status-client-0123456789", "synwevo-status-secret-0123456789"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    monkeypatch.setenv("SYNWEVO_IDP_PROVIDERS", "google")
    monkeypatch.setenv("SYNWEVO_IDP_GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SYNWEVO_IDP_GOOGLE_CLIENT_SECRET", CLIENT_SECRET)
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    for email, role in (("alice@example.com", "none"), ("root@example.com", "admin")):
        u = ident.create_user(email, PW, status="active", platform_role=role)
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), u["id"]))
    return {"api": api, "store": store}


def _login(api, email):
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_administrators_see_masked_configuration_and_the_exact_callback(env):
    c = _login(env["api"], "root@example.com")
    r = c.get("/api/admin/identity-providers")
    assert r.status_code == 200, r.text
    assert CLIENT_SECRET not in r.text and CLIENT_ID not in r.text
    items = {i["id"]: i for i in r.json()["items"]}
    google = items["google"]
    assert google["configured"] is True and google["client_secret_set"] is True
    assert google["client_id_hint"] == CLIENT_ID[:8] + "…"
    assert google["redirect_uri"] == "https://accounts.example.test/auth/external/google/callback"
    assert google["scopes"] == "openid email profile" and google["issuer"] == "https://accounts.google.com"
    assert google["discovery"] == {"checked": False}
    assert google["env"]["client_secret"] == "SYNWEVO_IDP_GOOGLE_CLIENT_SECRET"
    microsoft = items["microsoft"]
    assert microsoft["configured"] is False
    assert microsoft["missing"] == ["SYNWEVO_IDP_PROVIDERS", "SYNWEVO_IDP_MICROSOFT_CLIENT_ID", "SYNWEVO_IDP_MICROSOFT_CLIENT_SECRET"]
    assert microsoft["redirect_uri"] == "https://accounts.example.test/auth/external/microsoft/callback"


def test_a_probe_reports_an_unreachable_provider_without_failing_the_page(env):
    c = _login(env["api"], "root@example.com")

    class Down:
        def get(self, url, **kw):
            raise ConnectionError("no route")
    env["api"].state.external._http = Down()
    r = c.get("/api/admin/identity-providers", params={"probe": 1})
    assert r.status_code == 200
    google = next(i for i in r.json()["items"] if i["id"] == "google")
    assert google["discovery"]["checked"] is True and google["discovery"]["ok"] is False
    assert google["discovery"]["error"] == "idp_unreachable"
    # the fault and the provider travel as data, so the page words them in the reader's language (CI-85)
    assert google["discovery"]["detail"] == {"provider": "Google", "reason": "network"}

    class Refusing:
        def get(self, url, **kw):
            return type("R", (), {"status_code": 502})()
    env["api"].state.external._http = Refusing()
    google = next(i for i in c.get("/api/admin/identity-providers", params={"probe": 1}).json()["items"] if i["id"] == "google")
    assert google["discovery"]["message"] == "无法连接 Google，请稍后再试"
    assert google["discovery"]["detail"] == {"provider": "Google", "reason": "http_status"}


def test_ordinary_accounts_get_the_administrator_routes_404_and_anonymous_401(env):
    assert _login(env["api"], "alice@example.com").get("/api/admin/identity-providers").status_code == 404
    assert TestClient(env["api"]).get("/api/admin/identity-providers").status_code == 401
