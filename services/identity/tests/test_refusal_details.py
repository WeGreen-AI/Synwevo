"""CI-85: a refusal of the account service whose sentence carries a number or a name carries that
number or name in `detail` as well, so the account shell words it in the reader's language (a
changed limit or a provider's name never falls back to a bare code), and a refusal names only
controls that exist. The shell half — every refusal a person can meet is catalogued, worded by
code or templated — is `services/identity/web/tests/refusals.test.mjs`."""
from __future__ import annotations

import base64
import importlib
import time

import pyotp
import pytest
from fastapi.testclient import TestClient

from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store
from synwevo_identity.mfa import TICKET_MAX_ATTEMPTS

PW = "a-sufficiently-long-passphrase"


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
    user = ident.create_user("alice@example.com", PW, status="active")
    return {"api": api, "store": store, "ident": ident, "user": user}


def _signed_in(env):
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return c, {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def test_a_refused_password_names_its_rule(env):
    c, h = _signed_in(env)
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": "short"}, headers=h)
    assert r.status_code == 400 and r.json()["error"] == "weak_password"
    assert r.json()["detail"] == {"field": "new_password", "reason": "too_short", "min": 15, "length": 5}
    r = c.post("/api/auth/password", json={"current_password": PW, "new_password": "password123456789"}, headers=h)
    assert r.status_code == 400 and r.json()["detail"] == {"field": "new_password", "reason": "common"}
    assert r.json()["message"] == "这个口令在常见泄露口令名单里，请换一个。"


def test_a_wrong_second_factor_code_says_how_many_tries_are_left(env):
    c, h = _signed_in(env)
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=h).status_code == 200
    secret = c.post("/api/auth/mfa/totp/begin", headers=h).json()["secret"]
    assert c.post("/api/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=h).status_code == 200
    fresh = TestClient(env["api"])
    ticket = fresh.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).json()["ticket"]
    seen = [fresh.post("/api/auth/login/mfa", json={"ticket": ticket, "code": "000000"}).json() for _ in range(TICKET_MAX_ATTEMPTS)]
    assert [x["detail"] for x in seen] == [{"remaining": TICKET_MAX_ATTEMPTS - 1 - i, "exhausted": "sign_in"} for i in range(TICKET_MAX_ATTEMPTS)]
    assert seen[-1]["message"] == "验证码不正确，请重新登录" and seen[0]["message"] == f"验证码不正确，还可尝试 {TICKET_MAX_ATTEMPTS - 1} 次"


def test_numbered_limits_carry_their_number(env, monkeypatch):
    store, user = env["store"], env["user"]
    import synwevo_identity.passkeys as passkeys
    monkeypatch.setattr(passkeys, "MAX_PASSKEYS", 0)
    with pytest.raises(ServiceError) as e:
        passkeys.Passkeys(store).begin_registration(user)
    assert e.value.code == "too_many" and e.value.detail == {"limit": 0, "kind": "passkey"}

    import synwevo_core.push as push
    monkeypatch.setattr(push, "MAX_SUBSCRIPTIONS_PER_USER", 0)
    key = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    subscription = {"endpoint": "https://push.example.test/fixture", "keys": {"p256dh": key(b"\x04" + b"\x01" * 64), "auth": key(b"\x02" * 16)}}
    with pytest.raises(ServiceError) as e:
        push.PushSubscriptions(store).add(user["id"], None, subscription)
    assert e.value.code == "too_many_devices" and e.value.detail == {"limit": 0}

    import synwevo_core.member_import as member_import
    monkeypatch.setattr(member_import, "MAX_FILE_BYTES", 4)
    with pytest.raises(ServiceError) as e:
        member_import.accept_upload("members.csv", b"email\n")
    assert e.value.code == "file_too_large" and e.value.detail == {"limit": 4}

    import synwevo_core.org_roles as org_roles
    from synwevo_core.organizations import Organizations
    env["ident"].store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (user["id"],))
    oid = Organizations(store, env["ident"]).create(user["id"], "Fixture Org", "Fixture Team", "fixture-idem-ci85-0001")["organization"]["id"]
    monkeypatch.setattr(org_roles, "MAX_ROLES", 0)
    with pytest.raises(ServiceError) as e:
        org_roles.OrgRoles(store).create(oid, user["id"], name="Fixture role", permissions=[sorted(org_roles.DELEGABLE)[0]])
    assert e.value.code == "too_many_roles" and e.value.detail == {"limit": 0}


def test_an_unreachable_provider_sign_in_lands_on_the_sign_in_page(env, monkeypatch):
    """The start of an external sign-in is a browser navigation: a refusal there is a code the
    sign-in page words, not a JSON body in the tab."""
    started = TestClient(env["api"]).get("/auth/external/nowhere/start", params={"lang": "zh-Hant"}, follow_redirects=False)
    assert started.status_code == 303
    assert started.headers["location"] == "/login?ext_error=unknown_provider&lang=zh-Hant"
    assert env["store"].one("SELECT 1 FROM audit_events WHERE action='auth.external.start' AND result='denied'")
