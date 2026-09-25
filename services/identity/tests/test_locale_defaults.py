"""Server-composed text follows the account's language.

Pinned: session rows carry the device as product names (`client_parts`) next to the legacy
zh-Hans sentence, so the page composes "Chrome on macOS" itself; the default personal workspace
is named in the language the person registered in, and an account without a locale keeps the
Simplified Chinese default.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity, _personal_name
from synwevo_core.sessions import client_parts, describe_client
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"


@pytest.mark.parametrize("ua,browser,platform,legacy", [
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36", "Chrome", "macOS", "macOS 上的 Chrome"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1", "Safari", "iPhone", "iPhone 上的 Safari"),
    ("", None, None, "未知设备"),
    ("curl/8.4", None, None, "未知设备"),
])
def test_device_parts_are_product_names_and_the_legacy_sentence_is_unchanged(ua, browser, platform, legacy):
    assert client_parts(ua) == {"browser": browser, "platform": platform}
    assert describe_client(ua) == legacy


@pytest.mark.parametrize("locale,expected", [("zh-Hans", "Ada 的空间"), ("zh-Hant", "Ada 的空間"), ("en", "Ada's space"), (None, "Ada 的空间"), ("xx", "Ada 的空间")])
def test_the_personal_workspace_is_named_in_the_account_language(locale, expected):
    assert _personal_name("Ada", "ada@example.test", locale) == expected
    assert _personal_name(None, "ada@example.test", "en") == "ada's space"


def test_registration_and_creation_carry_the_locale_into_the_workspace_name(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    direct = ident.create_user("grace@example.test", PW, display_name="Grace", status="active", locale="en")
    assert ident.get_user(direct["id"])["locale"] == "en"
    assert ident.workspaces_for(direct["id"])[0]["name"] == "Grace's space"
    c = TestClient(api)
    r = c.post("/api/auth/register", json={"email": "lin@example.test", "password": PW, "display_name": "Lin", "locale": "zh-Hant", "intent": "personal"})
    assert r.status_code in (200, 201, 202), r.text
    user = ident.by_email("lin@example.test")
    store.execute("UPDATE users SET status='active', email_verified_at=? WHERE id=?", (now(), user["id"]))
    ident.ensure_personal_workspace(user["id"])
    assert ident.workspaces_for(user["id"])[0]["name"] == "Lin 的空間"
    assert c.post("/api/auth/login", json={"email": "grace@example.test", "password": PW},
                  headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/152.0 Safari/537.36"}).status_code == 200
    row = c.get("/api/auth/sessions").json()["items"][0]
    assert row["client_parts"] == {"browser": "Chrome", "platform": "Windows"} and row["client"] == "Windows 上的 Chrome"
