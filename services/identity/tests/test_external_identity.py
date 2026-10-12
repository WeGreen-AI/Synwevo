"""External OpenID providers: sign-in, registration, linking and the protocol checks.

The provider here is a **test double**: a minimal OpenID Connect issuer (discovery, JWKS, a code
store and a token endpoint with PKCE) that signs real RS256 id_tokens and can be told to produce
tampered ones. It stands in for Google / Microsoft so the consumer's checks can be exercised,
including the negative cases; it is evidence about Synwevo's code, not about any real provider.
"""
from __future__ import annotations

import hashlib
import base64
import importlib
import secrets
import time
import urllib.parse

import pyotp
import pytest

from .platform_fixture import make_platform_admin
from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_identity.external import REQ_COOKIE

PW = "a-sufficiently-long-passphrase"
ISSUER = "http://idp.localhost"
CLIENT_ID, CLIENT_SECRET = "synwevo-test-client", "synwevo-test-secret-0123456789"


class ProviderDouble:
    """A tiny OpenID Connect issuer for tests. `accept()` plays the person consenting."""

    def __init__(self, issuer: str = ISSUER):
        self.issuer = issuer
        self.key = JsonWebKey.generate_key("RSA", 2048, {"kid": "test-1", "use": "sig"}, is_private=True)
        self.codes: dict[str, dict] = {}
        self.tamper: dict = {}
        app = FastAPI()

        @app.get("/.well-known/openid-configuration")
        def discovery():
            return {"issuer": self.issuer, "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token", "jwks_uri": f"{self.issuer}/jwks",
                    "id_token_signing_alg_values_supported": ["RS256"]}

        @app.get("/jwks")
        def jwks():
            return {"keys": [self.key.as_dict(is_private=False)]}

        @app.post("/token")
        def token(grant_type: str = Form(...), code: str = Form(...), redirect_uri: str = Form(...),
                  client_id: str = Form(...), client_secret: str = Form(...), code_verifier: str = Form("")):
            grant = self.codes.pop(code, None)
            if not grant or grant_type != "authorization_code" or client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            if redirect_uri != grant["redirect_uri"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri"}, status_code=400)
            challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
            if challenge != grant["code_challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, status_code=400)
            ts = int(time.time())
            claims = {"iss": self.tamper.get("iss", self.issuer), "aud": self.tamper.get("aud", CLIENT_ID),
                      "sub": grant["subject"], "nonce": self.tamper.get("nonce", grant["nonce"]),
                      "iat": ts, "exp": self.tamper.get("exp", ts + 300), "email": grant["email"],
                      "email_verified": grant["email_verified"], "name": grant["name"]}
            for k in self.tamper.get("drop", ()):
                claims.pop(k, None)
            signer = self.tamper.get("key", self.key)
            raw = jwt.encode({"alg": "RS256", "kid": "test-1"}, claims, signer).decode()
            return {"access_token": secrets.token_urlsafe(8), "token_type": "Bearer", "id_token": raw}

        self.app = app
        self.client = TestClient(app)

    def accept(self, authorize_url: str, *, subject: str, email: str | None, email_verified: bool = True,
               name: str = "Test Person") -> tuple[str, str]:
        """The person signs in at the provider and consents: returns (code, state)."""
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(authorize_url).query))
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256" and q["scope"].startswith("openid")
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"subject": subject, "email": email, "email_verified": email_verified, "name": name,
                            "nonce": q["nonce"], "code_challenge": q["code_challenge"], "redirect_uri": q["redirect_uri"]}
        return code, q["state"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_SECRET", "an-external-key-for-tests-0123456789")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    monkeypatch.setenv("SYNWEVO_REGISTRATION", "open")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_IDP_PROVIDERS", "testidp")
    monkeypatch.setenv("SYNWEVO_IDP_TESTIDP_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SYNWEVO_IDP_TESTIDP_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("SYNWEVO_IDP_TESTIDP_ISSUER", ISSUER)
    monkeypatch.setenv("SYNWEVO_IDP_TESTIDP_LABEL", "Test IdP")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    idp = ProviderDouble()
    api.state.external._http = idp.client
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", PW, status="active")
    store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), alice["id"]))
    return {"api": api, "idp": idp, "store": store, "ident": ident, "alice": alice}


def _start(c, **params):
    r = c.get("/auth/external/testidp/start", params=params, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith(ISSUER + "/authorize?")
    assert any(h.startswith(REQ_COOKIE + "=") for h in r.headers.get_list("set-cookie"))
    return r.headers["location"]


def _callback(c, code, state):
    r = c.get("/auth/external/testidp/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert r.status_code != 500, r.text
    return r


def _csrf(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def test_configured_providers_are_listed_and_the_button_is_never_shown_otherwise(env, tmp_path, monkeypatch):
    c = TestClient(env["api"])
    assert c.get("/api/auth/state").json()["external_providers"] == [{"id": "testidp", "label": "Test IdP"}]
    monkeypatch.setenv("SYNWEVO_IDP_PROVIDERS", "")
    import synwevo_identity.app as app_mod
    bare = TestClient(app_mod.create_app(str(tmp_path / "bare"), start_jobs=False))
    assert bare.get("/api/auth/state").json()["external_providers"] == []
    # A navigation, so the refusal lands on the sign-in page, which words its code (CI-85) —
    # not a JSON body in the tab.
    started = bare.get("/auth/external/testidp/start", params={"lang": "en"}, follow_redirects=False)
    assert started.status_code == 303 and started.headers["location"] == "/login?ext_error=unknown_provider&lang=en"


def test_a_half_configured_provider_refuses_to_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_IDP_PROVIDERS", "google")
    monkeypatch.setenv("SYNWEVO_IDP_GOOGLE_CLIENT_ID", "x")
    monkeypatch.delenv("SYNWEVO_IDP_GOOGLE_CLIENT_SECRET", raising=False)
    from synwevo_identity.external import load_providers
    with pytest.raises(Exception) as e:
        load_providers()
    assert "SYNWEVO_IDP_GOOGLE_CLIENT_SECRET" in str(e.value)


def test_a_new_verified_identity_registers_an_account_and_signs_in(env):
    c = TestClient(env["api"])
    url = _start(c, next="/onboarding", lang="en", remember="1")
    code, state = env["idp"].accept(url, subject="idp-sub-1", email="new.person@example.com", name="New Person")
    r = _callback(c, code, state)
    assert r.status_code == 303 and r.headers["location"] == "/onboarding", r.headers
    me = c.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "new.person@example.com"
    assert me.json()["email_verified"] is True and me.json()["locale"] == "en"
    user = env["ident"].by_email("new.person@example.com")
    assert user["status"] == "active" and user["display_name"] == "New Person"
    assert env["store"].one("SELECT 1 FROM user_passwords WHERE user_id=?", (user["id"],)) is None
    row = env["store"].one("SELECT * FROM external_identities WHERE user_id=?", (user["id"],))
    assert row["provider"] == "testidp" and row["subject"] == "idp-sub-1" and row["email_verified"] == 1
    sess = c.get("/api/auth/sessions").json()["items"][0]
    assert sess["amr"] == "idp:testidp" and sess["persistent"] is True and sess["auth_provider"] == "testidp"
    # The request row is single use: replaying the callback does nothing.
    assert _callback(TestClient(env["api"]), code, state).status_code in (303, 400)
    assert env["store"].one("SELECT COUNT(*) AS n FROM users WHERE email_key='new.person@example.com'")["n"] == 1


def test_a_linked_identity_signs_in_again_without_touching_the_account(env):
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-sub-2", email="second@example.com")
    _callback(c, code, state)
    uid = env["ident"].by_email("second@example.com")["id"]
    other = TestClient(env["api"])
    code, state = env["idp"].accept(_start(other, next="/account"), subject="idp-sub-2", email="renamed@example.com")
    r = _callback(other, code, state)
    assert r.status_code == 303 and r.headers["location"] == "/account"
    assert other.get("/api/auth/me").json()["id"] == uid
    assert env["ident"].get_user(uid)["email"] == "second@example.com", "the provider's e-mail never rewrites ours"


def test_a_verified_email_match_is_an_offer_not_a_merge(env):
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c, next="/account"), subject="idp-alice", email="alice@example.com")
    r = _callback(c, code, state)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?link=testidp#offer=")
    assert c.get("/api/auth/me").status_code == 401, "nothing was merged on the strength of the address"
    assert env["store"].one("SELECT COUNT(*) AS n FROM external_identities")["n"] == 0
    offer = urllib.parse.unquote(r.headers["location"].split("#offer=", 1)[1])
    preview = c.post("/api/auth/external/offer/preview", json={"offer": offer})
    assert preview.status_code == 200 and preview.json()["email"] == "alice@example.com" and preview.json()["totp"] is False
    wrong = c.post("/api/auth/external/offer/confirm", json={"offer": offer, "password": "not-the-password"})
    assert wrong.status_code == 401 and c.get("/api/auth/me").status_code == 401
    ok = c.post("/api/auth/external/offer/confirm", json={"offer": offer, "password": PW})
    assert ok.status_code == 200 and ok.json()["next"] == "/account" and ok.json()["session"]["amr"] == "password"
    assert c.get("/api/auth/me").json()["id"] == env["alice"]["id"]
    linked = env["store"].one("SELECT * FROM external_identities WHERE user_id=?", (env["alice"]["id"],))
    assert linked["subject"] == "idp-alice"
    assert c.post("/api/auth/external/offer/confirm", json={"offer": offer, "password": PW}).status_code == 410


def test_an_unverified_provider_email_neither_registers_nor_matches(env):
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-unv", email="alice@example.com", email_verified=False)
    r = _callback(c, code, state)
    assert r.status_code == 303 and "ext_error=email_unverified" in r.headers["location"]
    assert c.get("/api/auth/me").status_code == 401
    assert env["store"].one("SELECT COUNT(*) AS n FROM external_identities")["n"] == 0
    assert env["store"].one("SELECT COUNT(*) AS n FROM external_link_offers")["n"] == 0


def test_registration_policy_applies_to_provider_sign_ups(env, monkeypatch):
    env["store"].execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('registration.mode', 'invite')")
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-new-closed", email="closed@example.com")
    r = _callback(c, code, state)
    assert "ext_error=registration_closed" in r.headers["location"]
    assert env["ident"].by_email("closed@example.com") is None


@pytest.mark.parametrize("tamper, expected", [
    ({"iss": "http://evil.localhost"}, "bad_id_token"),
    ({"aud": "someone-else"}, "bad_id_token"),
    ({"nonce": "not-the-nonce"}, "bad_id_token"),
    ({"exp": int(time.time()) - 5}, "bad_id_token"),
    ({"drop": ("sub",)}, "bad_id_token"),
    ({"key": JsonWebKey.generate_key("RSA", 2048, {"kid": "test-1"}, is_private=True)}, "bad_id_token"),
])
def test_tampered_id_tokens_are_refused(env, tamper, expected):
    c = TestClient(env["api"])
    env["idp"].tamper = tamper
    code, state = env["idp"].accept(_start(c), subject="idp-tamper", email="tamper@example.com")
    r = _callback(c, code, state)
    assert r.status_code == 303 and f"ext_error={expected}" in r.headers["location"], r.headers
    assert c.get("/api/auth/me").status_code == 401
    assert env["ident"].by_email("tamper@example.com") is None


def test_forged_state_and_missing_request_cookie_are_refused(env):
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-state", email="state@example.com")
    r = _callback(c, code, "not-the-state")
    assert "ext_error=bad_state" in r.headers["location"]
    fresh = TestClient(env["api"])                              # no request cookie at all
    r = _callback(fresh, code, state)
    assert "ext_error=bad_callback" in r.headers["location"]
    assert env["ident"].by_email("state@example.com") is None


def test_provider_sign_in_still_requires_totp_and_records_the_pair(env):
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    # Linking from the account page needs a fresh, scoped re-authentication: a plain sign-in
    # does not count, exactly as for adding a passkey.
    assert c.post("/api/auth/external/testidp/link", json={}, headers=_csrf(c)).status_code == 403
    assert c.post("/api/auth/mfa/reauth", json={"password": PW}, headers=_csrf(c)).status_code == 200
    begin = c.post("/api/auth/mfa/totp/begin", json={}, headers=_csrf(c)).json()
    assert c.post("/api/auth/mfa/totp/confirm", json={"code": pyotp.TOTP(begin["secret"]).now()}, headers=_csrf(c)).status_code == 200
    link = c.post("/api/auth/external/testidp/link", json={}, headers=_csrf(c))
    assert link.status_code == 200
    code, state = env["idp"].accept(link.json()["url"], subject="idp-alice-2", email="alice@example.com")
    r = _callback(c, code, state)
    assert r.status_code == 303 and r.headers["location"].startswith("/account?linked=testidp")
    assert c.get("/api/auth/external/identities").json()["items"][0]["subject" if False else "provider"] == "testidp"

    fresh = TestClient(env["api"])
    code, state = env["idp"].accept(_start(fresh, next="/account", remember="1"), subject="idp-alice-2", email="alice@example.com")
    r = _callback(fresh, code, state)
    assert r.status_code == 303 and r.headers["location"].startswith("/login#") and "ticket=" in r.headers["location"]
    assert fresh.cookies.get(COOKIE_NAME) is None, "no session before the second factor"
    frag = dict(urllib.parse.parse_qsl(r.headers["location"].split("#", 1)[1]))
    assert frag["next"] == "/account" and frag["remember"] == "1" and "totp" in frag["methods"]
    totp = pyotp.TOTP(begin["secret"]).at(int(time.time()) + 30)
    done = fresh.post("/api/auth/login/mfa", json={"ticket": frag["ticket"], "code": totp, "remember": True})
    assert done.status_code == 200 and done.json()["session"]["amr"] == "idp:testidp+totp"
    assert done.json()["session"]["persistent"] is True


def test_linking_needs_the_same_session_and_refuses_an_identity_linked_elsewhere(env):
    # Bob owns idp-sub-b already.
    bob = TestClient(env["api"])
    code, state = env["idp"].accept(_start(bob), subject="idp-sub-b", email="bob@example.com")
    _callback(bob, code, state)
    # Alice tries to link the same provider identity to her account.
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    assert c.post("/api/auth/reauth", json={"password": PW, "scope": "mfa"}, headers=_csrf(c)).status_code == 200
    url = c.post("/api/auth/external/testidp/link", json={}, headers=_csrf(c)).json()["url"]
    code, state = env["idp"].accept(url, subject="idp-sub-b", email="bob@example.com")
    r = _callback(c, code, state)
    assert "ext_error=identity_linked_elsewhere" in r.headers["location"]
    assert c.get("/api/auth/external/identities").json()["items"] == []
    # And a link started in one session cannot be completed by another.
    url = c.post("/api/auth/external/testidp/link", json={}, headers=_csrf(c)).json()["url"]
    code, state = env["idp"].accept(url, subject="idp-sub-c", email="alice@example.com")
    stranger = TestClient(env["api"])
    stranger.cookies.set(REQ_COOKIE, c.cookies.get(REQ_COOKIE))
    r = _callback(stranger, code, state)
    assert "ext_error=link_session_changed" in r.headers["location"]
    assert env["store"].one("SELECT COUNT(*) AS n FROM external_identities WHERE subject='idp-sub-c'")["n"] == 0


def test_unlinking_needs_reauth_and_never_removes_the_last_way_in(env):
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-only", email="only@example.com")
    _callback(c, code, state)
    listing = c.get("/api/auth/external/identities").json()
    iid = listing["items"][0]["id"]
    assert listing["other_methods"] == 1 and listing["reauth_fresh"] is False
    # No password on this account, so the provider is the only way in: refuse even after re-auth
    # (which cannot happen without a password; the identity list is the proof).
    r = c.delete(f"/api/auth/external/identities/{iid}", headers=_csrf(c))
    assert r.status_code == 403 and r.json()["error"] == "reauth_required"
    env["store"].execute("UPDATE sessions SET reauth_at=?, reauth_scope='mfa'", (now(),))
    r = c.delete(f"/api/auth/external/identities/{iid}", headers=_csrf(c))
    assert r.status_code == 409 and r.json()["error"] == "last_sign_in_method"
    # Alice has a password, so she may unlink after re-authenticating.
    a = TestClient(env["api"])
    a.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    a.post("/api/auth/reauth", json={"password": PW, "scope": "mfa"}, headers=_csrf(a))
    url = a.post("/api/auth/external/testidp/link", json={}, headers=_csrf(a)).json()["url"]
    code, state = env["idp"].accept(url, subject="idp-alice-3", email="alice@example.com")
    _callback(a, code, state)
    aid = a.get("/api/auth/external/identities").json()["items"][0]["id"]
    assert a.delete(f"/api/auth/external/identities/{aid}", headers=_csrf(a)).status_code == 200
    assert a.get("/api/auth/external/identities").json()["items"] == []
    notices = env["store"].all("SELECT subject FROM mail_outbox WHERE kind='security' AND to_addr='alice@example.com'")
    assert len(notices) == 2


def test_a_removed_passkey_does_not_keep_the_last_way_in_open(env):
    """Review C6: the passkey count behind `last_sign_in_method` sees only passkeys that can
    still sign in here. A soft-deleted key, or one bound to an earlier domain, is no way in and
    the unlink is refused; a live key bound to this RP ID is, and the unlink goes through."""
    from synwevo_identity.passkeys import rp_id
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-only-passkey", email="only-passkey@example.com")
    _callback(c, code, state)
    user = env["ident"].by_email("only-passkey@example.com")
    assert env["store"].one("SELECT 1 FROM user_passwords WHERE user_id=?", (user["id"],)) is None
    iid = c.get("/api/auth/external/identities").json()["items"][0]["id"]
    env["store"].execute("UPDATE sessions SET reauth_at=?, reauth_scope='mfa' WHERE user_id=?", (now(), user["id"]))

    def passkey(pid: str, *, deleted: bool, rp: str | None = None) -> None:
        """A passkey row (fixture: public material only, never used to sign)."""
        env["store"].execute(
            "INSERT INTO passkeys (id,user_id,credential_id,public_key,rp_id,uv_capable,name,created_at,deleted_at) "
            "VALUES (?,?,?,?,?,1,'fixture key',?,?)",
            (pid, user["id"], f"cred-{pid}", "fixture-public-key", rp or rp_id(), now(), now() if deleted else None))

    # The listing counts every way in, the identity itself included (1 here); the delete route
    # counts the ways that would remain without it.
    passkey("pk_removed_fixture", deleted=True)
    assert c.get("/api/auth/external/identities").json()["other_methods"] == 1
    r = c.delete(f"/api/auth/external/identities/{iid}", headers=_csrf(c))
    assert r.status_code == 409 and r.json()["error"] == "last_sign_in_method", r.text
    passkey("pk_elsewhere_fixture", deleted=False, rp="old-accounts.example.test")
    assert c.get("/api/auth/external/identities").json()["other_methods"] == 1
    r = c.delete(f"/api/auth/external/identities/{iid}", headers=_csrf(c))
    assert r.status_code == 409 and r.json()["error"] == "last_sign_in_method", r.text
    assert env["store"].one("SELECT COUNT(*) AS n FROM external_identities WHERE user_id=?", (user["id"],))["n"] == 1
    passkey("pk_live_fixture", deleted=False)
    assert c.get("/api/auth/external/identities").json()["other_methods"] == 2
    r = c.delete(f"/api/auth/external/identities/{iid}", headers=_csrf(c))
    assert r.status_code == 200, r.text
    assert c.get("/api/auth/external/identities").json()["items"] == []


def test_platform_administrators_through_a_provider_are_restricted_until_a_second_factor(env):
    root = make_platform_admin(env["ident"].store, env["ident"].create_user("root@example.com", PW, status="active"))
    env["store"].execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), root["id"]))
    c = TestClient(env["api"])
    code, state = env["idp"].accept(_start(c), subject="idp-root", email="root@example.com")
    r = _callback(c, code, state)
    assert r.headers["location"].startswith("/login?link=testidp#offer=")
    offer = urllib.parse.unquote(r.headers["location"].split("#offer=", 1)[1])
    linked = c.post("/api/auth/external/offer/confirm", json={"offer": offer, "password": PW})
    assert linked.status_code == 200 and linked.json()["session"]["amr"] == "password"
    fresh = TestClient(env["api"])
    code, state = env["idp"].accept(_start(fresh), subject="idp-root", email="root@example.com")
    r = _callback(fresh, code, state)
    assert r.status_code == 303 and r.headers["location"] == "/account"
    me = fresh.get("/api/auth/me").json()
    assert me["platform_role"] == "admin" and me["permissions"] == [] and me["admin_locked"] is True
    assert fresh.get("/api/admin/overview").status_code == 403
    csrf = {CSRF_HEADER: fresh.cookies.get(CSRF_COOKIE)}
    assert fresh.post("/api/auth/reauth", json={"password": PW, "scope": "admin"}, headers=csrf).status_code == 200
    assert fresh.get("/api/auth/me").json()["permissions"] == ["api_platform.manage"]
    assert fresh.get("/api/admin/overview").status_code == 200
    assert fresh.get("/api/auth/sessions").json()["items"][0]["amr"] == "idp:testidp+password"
