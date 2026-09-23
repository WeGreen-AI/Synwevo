"""Refresh rotation, cross-application logout and revocation propagation.

These are the parts where a plausible-looking implementation is still wrong: rotation without
replay detection, a logout that only clears the portal's own cookie, a back-channel notification
treated as the enforcement rather than as a notification.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from authlib.jose import JsonWebKey
from fastapi.testclient import TestClient

from docweave.identity import Identity
from docweave.platform import oidc
from docweave.platform.apps import Apps, bootstrap_docweave
from docweave.services import ServiceError
from docweave.store import Store

PW = "a-sufficiently-long-passphrase"
REDIRECT = "http://docweave.localhost:8765/auth/callback"
LOGOUT_BACK = "http://docweave.localhost:8765/auth/backchannel-logout"
POST_LOGOUT = "http://portal.localhost:8769/signed-out"


class Deliveries(list):
    """A stand-in for the back-channel POST. Explicitly a test double: it records what would
    have been sent so the *content* can be checked, and can be told to fail."""

    fail = False

    def __call__(self, url, token, timeout=3.0):
        self.append({"url": url, "token": token})
        return (False, "HTTP 500") if self.fail else (True, None)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_ISSUER", "http://accounts.localhost:8770")
    import docweave.api.app as app_mod
    importlib.reload(app_mod)

    deliveries = Deliveries()
    import docweave.platform.routes as routes_mod
    real_build = routes_mod.build_router
    monkeypatch.setattr(routes_mod, "build_router",
                        lambda *a, **k: real_build(*a, **{**k, "deliver": deliveries}))

    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    bootstrap_docweave(store, ident)
    apps.enable_for_workspace(ws, "docweave")
    oidc.Clients(store).register(
        client_id="docweave-web", app_id="docweave", name="DocWeave",
        redirect_uris=[REDIRECT], post_logout_redirect_uris=[POST_LOGOUT],
        backchannel_logout_uri=LOGOUT_BACK, scopes="openid profile email offline_access")
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "apps": apps,
            "user": user, "ws": ws, "deliveries": deliveries}


def _grant(c, scope="openid offline_access"):
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": scope, "state": "s", "nonce": "n", "code_challenge": ch,
         "code_challenge_method": "S256"}
    resp = c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(resp.headers["location"]).query))["code"]
    out = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": REDIRECT, "client_id": "docweave-web",
                                      "code_verifier": v})
    assert out.status_code == 200, out.text
    return out.json()


def _refresh(c, rt):
    return c.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": rt,
                                       "client_id": "docweave-web"})


# ---------------------------------------------------------------- rotation
def test_a_refresh_returns_a_new_pair_and_retires_the_old_one(env):
    c = env["c"]
    first = _grant(c)
    second = _refresh(c, first["refresh_token"]).json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["access_token"] != first["access_token"]
    # the old access token stops working the moment its refresh token is spent
    assert c.get("/oidc/userinfo",
                 headers={"Authorization": "Bearer " + second["access_token"]}).status_code == 200


def test_replaying_a_spent_refresh_token_kills_the_whole_family(env):
    """The legitimate holder already has the successor, so a second use means it leaked."""
    c = env["c"]
    first = _grant(c)
    second = _refresh(c, first["refresh_token"]).json()
    assert _refresh(c, second["refresh_token"]).status_code == 200

    replay = _refresh(c, first["refresh_token"])
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    # and everything descended from that first grant is now dead, including the newest token
    assert c.get("/oidc/userinfo",
                 headers={"Authorization": "Bearer " + second["access_token"]}).status_code == 401
    live = env["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE revoked_at IS NULL")
    assert live["n"] == 0


def test_a_refresh_token_belongs_to_one_client_only(env):
    c = env["c"]
    oidc.Clients(env["store"]).register(client_id="other", app_id="docweave", name="Other",
                                        redirect_uris=["http://other.localhost:9/cb"])
    rt = _grant(c)["refresh_token"]
    out = c.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": rt,
                                      "client_id": "other"})
    assert out.status_code == 400 and "不属于该客户端" in out.text


def test_revoking_the_entitlement_stops_the_next_refresh(env):
    """A short access token is only worth anything if revocation lands within one refresh."""
    c, apps = env["c"], env["apps"]
    rt = _grant(c)["refresh_token"]
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (env["ws"],))
    out = _refresh(c, rt)
    assert out.status_code == 400 and "尚未启用" in out.text
    assert env["store"].one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE revoked_at IS NULL")["n"] == 0


def test_suspending_the_account_stops_the_next_refresh(env):
    c = env["c"]
    rt = _grant(c)["refresh_token"]
    env["ident"].set_status(env["user"]["id"], "suspended")
    assert _refresh(c, rt).status_code == 400


def test_a_grant_without_offline_access_has_no_refresh_token(env):
    assert "refresh_token" not in _grant(env["c"], scope="openid")


# ---------------------------------------------------------------- logout
def test_logout_revokes_tokens_before_it_notifies_anyone(env):
    """The database is the enforcement; the notification is a courtesy."""
    c, store = env["c"], env["store"]
    tok = _grant(c)
    out = c.get("/oidc/logout?" + urllib.parse.urlencode(
        {"client_id": "docweave-web", "post_logout_redirect_uri": POST_LOGOUT, "state": "xyz"}),
        follow_redirects=False)
    assert out.status_code == 303
    assert out.headers["location"] == POST_LOGOUT + "?state=xyz"

    assert store.one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE revoked_at IS NULL")["n"] == 0
    assert c.get("/oidc/userinfo",
                 headers={"Authorization": "Bearer " + tok["access_token"]}).status_code == 401
    assert _refresh(c, tok["refresh_token"]).status_code == 400
    assert len(env["deliveries"]) == 1 and env["deliveries"][0]["url"] == LOGOUT_BACK


def test_logout_also_ends_the_central_session(env):
    c = env["c"]
    _grant(c)
    c.get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    assert c.get("/api/auth/state").json().get("user") is None
    # ...and the next authorize does not silently sign them straight back in
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": "openid", "code_challenge": "x" * 43, "code_challenge_method": "S256"}
    resp = c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    assert resp.headers["location"].startswith("/login?next=")


def test_an_unregistered_post_logout_target_is_refused_not_redirected_to(env):
    c = env["c"]
    _grant(c)
    out = c.get("/oidc/logout?" + urllib.parse.urlencode(
        {"client_id": "docweave-web", "post_logout_redirect_uri": "http://evil.example.com/x"}),
        follow_redirects=False)
    assert out.status_code == 400
    assert "evil.example.com" not in (out.headers.get("location") or "")
    # the session still ends — refusing the redirect must not leave the person signed in
    assert c.get("/api/auth/state").json().get("user") is None


def test_logout_without_a_session_is_harmless(env):
    fresh = TestClient(env["api"])
    out = fresh.get("/oidc/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/logged-out"


def test_a_failed_notification_is_recorded_and_does_not_undo_the_revocation(env):
    c, store = env["c"], env["store"]
    env["deliveries"].fail = True
    tok = _grant(c)
    out = c.get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    assert out.status_code == 303
    assert store.one("SELECT COUNT(*) AS n FROM oauth_tokens WHERE revoked_at IS NULL")["n"] == 0
    row = store.one("SELECT logged_out_at, logout_error FROM client_sessions")
    assert row["logged_out_at"] and row["logout_error"] == "HTTP 500"
    assert c.get("/oidc/userinfo",
                 headers={"Authorization": "Bearer " + tok["access_token"]}).status_code == 401


# ---------------------------------------------------------------- logout token
def test_the_logout_token_is_valid_and_is_not_an_id_token(env):
    c = env["c"]
    _grant(c)
    c.get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    raw = env["deliveries"][0]["token"]
    jwks = c.get("/oidc/jwks.json").json()
    claims = oidc.verify_logout_token(raw, jwks=jwks, client_id="docweave-web")
    assert claims["sid"] and claims["sub"] == env["user"]["id"] and claims["jti"]
    assert "nonce" not in claims
    assert "http://schemas.openid.net/event/backchannel-logout" in claims["events"]
    sid = env["store"].one("SELECT sid FROM client_sessions")["sid"]
    assert claims["sid"] == sid


def test_a_logout_token_for_another_client_is_rejected(env):
    c = env["c"]
    _grant(c)
    c.get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    raw = env["deliveries"][0]["token"]
    jwks = c.get("/oidc/jwks.json").json()
    with pytest.raises(ServiceError, match="受众"):
        oidc.verify_logout_token(raw, jwks=jwks, client_id="somebody-else")
    with pytest.raises(ServiceError, match="发行者"):
        oidc.verify_logout_token(raw, jwks=jwks, client_id="docweave-web",
                                 expected_issuer="http://not-us.example")


def test_a_token_signed_by_someone_else_is_rejected(env):
    """The signature check is the whole defence — without it anyone can log anyone out."""
    from authlib.jose import jwt as _jwt
    other = JsonWebKey.generate_key("RSA", 2048, is_private=True)
    d = other.as_dict(is_private=True)
    d["kid"] = env["c"].get("/oidc/jwks.json").json()["keys"][0]["kid"]     # same kid, wrong key
    forged = _jwt.encode({"alg": "RS256", "kid": d["kid"]}, {
        "iss": "http://accounts.localhost:8770", "sub": env["user"]["id"], "aud": "docweave-web",
        "iat": 1, "exp": 2, "jti": "x", "sid": "cs_whatever",
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}}}, d).decode()
    with pytest.raises(Exception):
        oidc.verify_logout_token(forged, jwks=env["c"].get("/oidc/jwks.json").json(),
                                 client_id="docweave-web")


def test_delivery_refuses_a_non_local_plain_http_target(env):
    """Registration validates this, but the deliverer checks again: a bad registration should
    not become a way to make the server issue requests to arbitrary addresses."""
    ok, err = oidc.deliver_logout("http://10.0.0.5/internal", "tok")
    assert ok is False and "https" in err
