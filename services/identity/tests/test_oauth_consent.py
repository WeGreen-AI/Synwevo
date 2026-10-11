"""Consent is its own step (API-4): signing in does not let an integration act for someone; the
person approves the client, scopes and workspace, can see and withdraw it, and a withdrawal holds
across a backup restore. Plus client_secret_basic and RFC 7662 introspection."""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.oauth_consent import Consents, grandfather
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"
REDIRECT = "https://integration.example.test/callback"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    import synwevo_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    bootstrap_docs_app(store, ident)
    apps.enable_for_workspace(ws, "docweave")
    clients = oidc.Clients(store)
    clients.register(client_id="report-bot", app_id="docweave", name="Quarterly report bot",
                     redirect_uris=[REDIRECT], scopes="openid profile offline_access docweave.read docweave.write")
    _, secret = clients.register(client_id="server-app", app_id="docweave", name="Server integration",
                                 redirect_uris=[REDIRECT], scopes="openid offline_access docweave.read", confidential=True)
    c = TestClient(api)
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return {"api": api, "c": c, "store": store, "ident": ident, "apps": apps, "user": user, "ws": ws,
            "secret": secret, "root": tmp_path / "d"}


def pkce():
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    return v, base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def authorize(c, client_id="report-bot", scope="openid docweave.read", **extra):
    v, ch = pkce()
    q = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "scope": scope,
         "state": "st-1", "nonce": "no-1", "code_challenge": ch, "code_challenge_method": "S256", **extra}
    return c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False), v


def params(location: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))


def decide(c, rid, decision):
    return c.post(f"/api/auth/oauth-consent/{rid}/{decision}", headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def exchange(c, code, verifier, client_id="report-bot", **auth):
    return c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
                                        "client_id": client_id, "code_verifier": verifier, **auth})


def consent_flow(env, scope="openid docweave.read offline_access"):
    r, v = authorize(env["c"], scope=scope)
    rid = params(r.headers["location"])["request"]
    out = decide(env["c"], rid, "approve")
    assert out.status_code == 200, out.text
    tok = exchange(env["c"], params(out.json()["redirect"])["code"], v)
    assert tok.status_code == 200, tok.text
    return tok.json()


def test_signing_in_alone_needs_no_consent(env):
    r, _ = authorize(env["c"], scope="openid profile")
    assert r.status_code == 303 and "code" in params(r.headers["location"])


def test_api_scopes_wait_for_the_person_and_show_what_is_asked(env):
    r, v = authorize(env["c"], scope="openid docweave.read docweave.write")
    assert r.status_code == 303 and r.headers["location"].startswith("/oauth/consent?request=")
    assert "code" not in params(r.headers["location"])
    rid = params(r.headers["location"])["request"]
    shown = env["c"].get(f"/api/auth/oauth-consent/{rid}").json()
    assert shown["client"] == {"id": "report-bot", "name": "Quarterly report bot", "app_id": "docweave"}
    assert shown["scopes"] == ["docweave.read", "docweave.write"] and shown["identity_scopes"] == ["openid"]
    assert shown["workspace"]["id"] == env["ws"] and shown["redirect_host"] == "integration.example.test"
    out = decide(env["c"], rid, "approve").json()
    got = params(out["redirect"])
    assert got["state"] == "st-1" and got["code"]
    assert exchange(env["c"], got["code"], v).status_code == 200
    # The same client, workspace and scopes again: no second question.
    again, _ = authorize(env["c"], scope="openid docweave.read")
    assert "code" in params(again.headers["location"])
    # Asking for more than was approved asks again.
    wider, _ = authorize(env["c"], scope="openid docweave.read offline_access")
    assert wider.headers["location"].startswith("/oauth/consent?")


def test_declining_sends_access_denied_and_no_code(env):
    r, _ = authorize(env["c"])
    rid = params(r.headers["location"])["request"]
    back = params(decide(env["c"], rid, "deny").json()["redirect"])
    assert back["error"] == "access_denied" and back["state"] == "st-1" and "code" not in back
    assert decide(env["c"], rid, "approve").status_code == 409, "a decided request cannot be decided again"
    assert Consents(env["store"]).active(env["user"]["id"], "report-bot", env["ws"]) is None


def test_a_parked_request_belongs_to_its_session_and_expires(env):
    r, _ = authorize(env["c"])
    rid = params(r.headers["location"])["request"]
    stranger = TestClient(env["api"])
    env["ident"].create_user("bob@example.com", PW, status="active")
    stranger.post("/api/auth/login", json={"email": "bob@example.com", "password": PW})
    assert stranger.get(f"/api/auth/oauth-consent/{rid}").status_code == 404
    assert decide(stranger, rid, "approve").status_code == 404
    anonymous = TestClient(env["api"])
    assert anonymous.get(f"/api/auth/oauth-consent/{rid}").status_code == 401
    env["store"].execute("UPDATE oauth_consent_requests SET expires_at=? WHERE id=?", (now() - 1, rid))  # fixture: time passes
    assert decide(env["c"], rid, "approve").status_code == 410


def test_losing_the_application_while_the_page_is_open_denies(env):
    r, _ = authorize(env["c"])
    rid = params(r.headers["location"])["request"]
    env["apps"].disable_for_workspace(env["ws"], "docweave")
    back = params(decide(env["c"], rid, "approve").json()["redirect"])
    assert back["error"] == "access_denied" and "code" not in back
    assert Consents(env["store"]).active(env["user"]["id"], "report-bot", env["ws"]) is None


def test_withdrawing_a_connected_app_revokes_its_tokens_at_once(env):
    tok = consent_flow(env)
    grants = oidc.Grants(env["store"])
    assert grants.load_access(tok["access_token"]) is not None
    listed = env["c"].get("/api/auth/connected-apps").json()["items"]
    assert len(listed) == 1 and listed[0]["client"]["name"] == "Quarterly report bot"
    assert listed[0]["scopes"] == ["docweave.read", "offline_access"] and listed[0]["source"] == "consent"
    # The token's use just above is the consent's last use; a second use within the minute is not
    # rewritten (api_keys rule), a later one is.
    first_use = listed[0]["last_used_at"]
    assert first_use is not None and first_use >= listed[0]["last_issued_at"]
    assert grants.load_access(tok["access_token"]) is not None
    assert env["c"].get("/api/auth/connected-apps").json()["items"][0]["last_used_at"] == first_use
    env["store"].execute("UPDATE oauth_tokens SET last_used_at=last_used_at-120 WHERE user_id=?", (env["user"]["id"],))
    assert grants.load_access(tok["access_token"]) is not None
    assert env["c"].get("/api/auth/connected-apps").json()["items"][0]["last_used_at"] >= first_use
    r = env["c"].post(f"/api/auth/connected-apps/{listed[0]['id']}/revoke",
                      headers={CSRF_HEADER: env["c"].cookies.get(CSRF_COOKIE) or ""})
    assert r.status_code == 200 and r.json()["tokens_revoked"] >= 1
    assert grants.load_access(tok["access_token"]) is None
    refreshed = env["c"].post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                                   "client_id": "report-bot"})
    assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
    assert refreshed.json()["error_description"] == "该授权已被撤销，请重新授权", "a withdrawal is not a replay"
    assert env["c"].get("/api/auth/connected-apps").json()["items"] == []
    # Next time the client asks, the person is asked again.
    r, _ = authorize(env["c"])
    assert r.headers["location"].startswith("/oauth/consent?")


def test_a_restored_backup_does_not_bring_a_withdrawn_consent_back(env):
    consent_flow(env)
    store = env["store"]
    snapshot = store.all("SELECT * FROM oauth_consents")          # fixture: what a backup would hold
    cid = snapshot[0]["id"]
    env["c"].post(f"/api/auth/connected-apps/{cid}/revoke", headers={CSRF_HEADER: env["c"].cookies.get(CSRF_COOKIE) or ""})
    entries = [e for e in journal.read(store.root) if e["kind"] == "consent_withdrawn"]
    assert entries and entries[-1]["client_id"] == "report-bot" and entries[-1]["user_id"] == env["user"]["id"]
    store.execute("UPDATE oauth_consents SET revoked_at=NULL, revoked_reason=NULL WHERE id=?", (cid,))  # fixture: restore
    journal.apply(store, entries)
    assert Consents(store).active(env["user"]["id"], "report-bot", env["ws"]) is None


def test_live_grants_from_before_consent_are_listed_as_legacy(env):
    store = env["store"]
    tok = consent_flow(env)
    store.execute("DELETE FROM oauth_consents")                    # fixture: a database from before API-4
    store.execute("DELETE FROM meta WHERE key='oauth_consent_legacy_recorded'")
    with store.tx() as c:
        grandfather(c)
        grandfather(c)                                             # once only
    rows = store.all("SELECT * FROM oauth_consents")
    assert len(rows) == 1 and rows[0]["source"] == "legacy" and rows[0]["workspace_id"] == env["ws"]
    items = env["c"].get("/api/auth/connected-apps").json()["items"]
    assert items[0]["source"] == "legacy" and items[0]["scopes"] == ["docweave.read", "offline_access"]
    assert oidc.Grants(store).load_access(tok["access_token"]) is not None, "recording changes no grant"


def basic_token(env) -> str:
    r, v = authorize(env["c"], client_id="server-app", scope="openid docweave.read")
    rid = params(r.headers["location"])["request"]
    code = params(decide(env["c"], rid, "approve").json()["redirect"])["code"]
    basic = "Basic " + base64.b64encode(f"server-app:{urllib.parse.quote_plus(env['secret'])}".encode()).decode()
    both = env["c"].post("/oidc/token", headers={"Authorization": basic}, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": v,
        "client_secret": env["secret"]})
    assert both.status_code == 400 and both.json()["error"] == "invalid_request"
    wrong = env["c"].post("/oidc/token", headers={"Authorization": "Basic " + base64.b64encode(b"server-app:nope").decode()},
                          data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": v})
    assert wrong.status_code == 401 and wrong.headers["www-authenticate"].startswith("Basic")
    ok = env["c"].post("/oidc/token", headers={"Authorization": basic},
                       data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "code_verifier": v})
    assert ok.status_code == 200, ok.text
    env["tok"] = ok.json()
    return basic


def test_client_secret_basic_and_one_method_per_request(env):
    basic_token(env)
    assert env["tok"]["token_type"].lower() == "bearer" and env["tok"]["access_token"]


def test_introspection_answers_only_about_the_callers_own_live_tokens(env):
    basic = basic_token(env)
    access = env["tok"]["access_token"]
    live = env["c"].post("/oidc/introspect", headers={"Authorization": basic}, data={"token": access}).json()
    assert live["active"] is True and live["client_id"] == "server-app" and live["sub"] == env["user"]["id"]
    assert live["scope"] == "openid docweave.read" and live["token_type"] == "Bearer" and live["exp"] > now()
    assert live.get("workspace_id") == env["ws"]
    # A public client cannot introspect, and another client's token looks unknown.
    other = consent_flow(env)
    public = env["c"].post("/oidc/introspect", data={"token": other["access_token"], "client_id": "report-bot"})
    assert public.status_code == 401
    assert env["c"].post("/oidc/introspect", headers={"Authorization": basic},
                         data={"token": other["access_token"]}).json() == {"active": False}
    # Revocation is visible at once.
    oidc.Grants(env["store"]).revoke_for_client("server-app", "fixture")
    assert env["c"].post("/oidc/introspect", headers={"Authorization": basic}, data={"token": access}).json() == {"active": False}
    meta = env["c"].get("/.well-known/openid-configuration").json()
    assert meta["introspection_endpoint"].endswith("/oidc/introspect")
    assert "client_secret_basic" in meta["token_endpoint_auth_methods_supported"]
