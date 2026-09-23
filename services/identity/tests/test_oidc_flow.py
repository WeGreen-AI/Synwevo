"""The authorization code flow end to end, plus application eligibility.

Driven through the real HTTP endpoints. The sandboxed browser in this environment allows one
*.localhost host and denies its siblings, so the redirect chain is exercised with a client that
follows redirects and enforces cookie scoping rather than by clicking — which also makes the
negative cases (forged state, replayed code, wrong client) expressible at all.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from authlib.jose import JsonWebKey, jwt
from fastapi.testclient import TestClient

from platform_core.identity import Identity
from platform_core import oidc
from platform_core.apps import Apps, bootstrap_docweave
from platform_core.sessions import CSRF_COOKIE, CSRF_HEADER
from platform_core.store import Store, now

PW = "a-sufficiently-long-passphrase"
REDIRECT = "http://docweave.localhost:8765/auth/callback"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    monkeypatch.setenv("DOCWEAVE_ISSUER", "http://accounts.localhost:8770")
    import platform_identity.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)

    user = ident.create_user("alice@example.com", PW, display_name="Alice", status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    bootstrap_docweave(store, ident)
    apps.enable_for_workspace(ws, "docweave")
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="DocWeave",
                                 redirect_uris=[REDIRECT], audiences=["docweave-api"])
    c = TestClient(api)
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    return {"api": api, "c": c, "store": store, "ident": ident, "apps": apps,
            "user": user, "ws": ws}


def _pkce():
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, ch


def _authorize(c, **over):
    v, ch = _pkce()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": "openid profile email", "state": "st-abc", "nonce": "no-xyz",
         "code_challenge": ch, "code_challenge_method": "S256"}
    q.update(over)
    q = {k: val for k, val in q.items() if val is not None}
    return c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False), v


def _params(resp) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(resp.headers["location"]).query))


# ---------------------------------------------------------------- metadata
def test_discovery_and_jwks_are_reachable_without_a_session(env):
    fresh = TestClient(env["api"])
    d = fresh.get("/.well-known/openid-configuration")
    assert d.status_code == 200 and d.json()["issuer"] == "http://accounts.localhost:8770"
    j = fresh.get("/oidc/jwks.json")
    assert j.status_code == 200 and len(j.json()["keys"]) == 1


# ---------------------------------------------------------------- happy path
def test_full_authorization_code_flow(env):
    c = env["c"]
    resp, verifier = _authorize(c)
    assert resp.status_code == 303
    p = _params(resp)
    assert p["state"] == "st-abc" and p.get("code"), p

    tok = c.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": p["code"], "redirect_uri": REDIRECT,
        "client_id": "docweave-web", "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    body = tok.json()
    assert body["token_type"] == "Bearer" and body["access_token"] and body["id_token"]
    assert "refresh_token" not in body, "没有申请 offline_access 就不该给刷新令牌"
    assert tok.headers["Cache-Control"] == "no-store"

    claims = jwt.decode(body["id_token"], JsonWebKey.import_key_set(c.get("/oidc/jwks.json").json()))
    assert claims["iss"] == "http://accounts.localhost:8770"
    assert claims["sub"] == env["user"]["id"] and claims["aud"] == "docweave-web"
    assert claims["nonce"] == "no-xyz" and claims["amr"] == ["password"] and claims["sid"]

    info = c.get("/oidc/userinfo", headers={"Authorization": "Bearer " + body["access_token"]})
    assert info.status_code == 200
    assert info.json() == {"sub": env["user"]["id"], "email": "alice@example.com",
                           "email_verified": False, "name": "Alice"}


def test_offline_access_yields_a_refresh_token_only_when_asked(env):
    c = env["c"]
    resp, verifier = _authorize(c, scope="openid offline_access")
    body = c.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": _params(resp)["code"], "redirect_uri": REDIRECT,
        "client_id": "docweave-web", "code_verifier": verifier}).json()
    assert body.get("refresh_token")


# ---------------------------------------------------------------- signed out
def test_authorize_without_a_session_goes_to_login_and_comes_back(env):
    fresh = TestClient(env["api"])
    resp, _ = _authorize(fresh)
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/login?next=")
    # the return target is our own authorize URL, rebuilt server-side — not a client parameter
    nxt = urllib.parse.unquote(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["next"])
    assert nxt.startswith("/oidc/authorize?") and "client_id=docweave-web" in nxt


# ---------------------------------------------------------------- negative: redirects
def test_an_unregistered_redirect_is_never_redirected_to(env):
    """The error must not be delivered to the address being tested — that is an open redirect."""
    resp, _ = _authorize(env["c"], redirect_uri="http://evil.example.com/steal")
    assert resp.status_code == 400
    assert "location" not in {k.lower() for k in resp.headers}
    assert "evil.example.com" not in (resp.headers.get("location") or "")


def test_an_unknown_client_is_refused_without_redirecting(env):
    resp, _ = _authorize(env["c"], client_id="not-a-client")
    assert resp.status_code == 400 and "location" not in {k.lower() for k in resp.headers}


# ---------------------------------------------------------------- negative: protocol
def test_missing_pkce_is_refused(env):
    resp, _ = _authorize(env["c"], code_challenge=None, code_challenge_method=None)
    assert resp.status_code == 303 and _params(resp)["error"] == "invalid_request"
    assert _params(resp)["state"] == "st-abc", "错误也要带回 state，否则客户端无法对应请求"


def test_plain_pkce_is_refused_at_authorize(env):
    resp, _ = _authorize(env["c"], code_challenge_method="plain")
    assert _params(resp)["error"] == "invalid_request"


def test_implicit_flow_is_refused(env):
    resp, _ = _authorize(env["c"], response_type="token")
    assert _params(resp)["error"] == "unsupported_response_type"


def test_an_unknown_scope_is_refused(env):
    resp, _ = _authorize(env["c"], scope="openid admin:everything")
    assert _params(resp)["error"] == "invalid_scope"


def test_a_code_cannot_be_exchanged_twice(env):
    c = env["c"]
    resp, verifier = _authorize(c)
    data = {"grant_type": "authorization_code", "code": _params(resp)["code"],
            "redirect_uri": REDIRECT, "client_id": "docweave-web", "code_verifier": verifier}
    assert c.post("/oidc/token", data=data).status_code == 200
    second = c.post("/oidc/token", data=data)
    assert second.status_code == 400 and second.json()["error"] == "invalid_grant"


def test_the_token_endpoint_ignores_cookies(env):
    """A token endpoint that honoured a session cookie would be a CSRF-shaped token mint."""
    c = env["c"]
    resp, verifier = _authorize(c)
    code = _params(resp)["code"]
    # same browser session, but the wrong verifier: the cookie must not rescue it
    bad = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": REDIRECT, "client_id": "docweave-web",
                                      "code_verifier": "wrong"})
    assert bad.status_code == 400


def test_userinfo_requires_a_bearer_token_not_a_session(env):
    c = env["c"]
    assert c.get("/oidc/userinfo").status_code == 401
    assert c.get("/oidc/userinfo", headers={"Authorization": "Bearer nope"}).status_code == 401


# ---------------------------------------------------------------- revocation
def test_revoking_an_access_token_stops_userinfo(env):
    c = env["c"]
    resp, verifier = _authorize(c)
    body = c.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": _params(resp)["code"], "redirect_uri": REDIRECT,
        "client_id": "docweave-web", "code_verifier": verifier}).json()
    at = body["access_token"]
    assert c.get("/oidc/userinfo", headers={"Authorization": "Bearer " + at}).status_code == 200
    assert c.post("/oidc/revoke", data={"token": at, "client_id": "docweave-web"}).status_code == 200
    assert c.get("/oidc/userinfo", headers={"Authorization": "Bearer " + at}).status_code == 401


def test_revoking_an_unknown_token_still_succeeds(env):
    """RFC 7009: distinguishing unknown from revoked would make this a validity oracle."""
    assert env["c"].post("/oidc/revoke", data={"token": "never-existed",
                                               "client_id": "docweave-web"}).status_code == 200


def test_a_suspended_account_cannot_exchange_its_code(env):
    c = env["c"]
    resp, verifier = _authorize(c)
    env["ident"].set_status(env["user"]["id"], "suspended")
    out = c.post("/oidc/token", data={
        "grant_type": "authorization_code", "code": _params(resp)["code"], "redirect_uri": REDIRECT,
        "client_id": "docweave-web", "code_verifier": verifier})
    assert out.status_code == 400 and "account unavailable" in out.text


# ---------------------------------------------------------------- entitlement
def test_signing_in_does_not_by_itself_grant_an_application(env):
    """Identity and eligibility are different questions with different answers.

    A new workspace starts with DocWeave enabled, so the separation is shown by taking it away:
    a live account, a live workspace, a live membership — and still no access, because
    eligibility is its own record rather than something inferred from being signed in.
    """
    ident, apps, store = env["ident"], env["apps"], env["store"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    bob_ws = ident.workspaces_for(bob["id"])[0]["id"]
    assert apps.check_access(bob["id"], "docweave")[0] is True

    store.execute("DELETE FROM workspace_apps WHERE workspace_id=?", (bob_ws,))
    ok, wid, reason = apps.check_access(bob["id"], "docweave")
    assert ok is False and wid is None and "尚未启用" in reason
    assert ident.membership(bob_ws, bob["id"]), "成员关系还在——被拒绝的是资格，不是身份"

    apps.enable_for_workspace(bob_ws, "docweave")
    ok, wid, _ = apps.check_access(bob["id"], "docweave")
    assert ok is True and wid == bob_ws


def test_authorize_refuses_a_user_without_the_application(env):
    ident = env["ident"]
    u = ident.create_user("bob@example.com", PW, status="active")
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?",
                         (ident.workspaces_for(u["id"])[0]["id"],))
    bob = TestClient(env["api"])
    bob.post("/api/auth/login", json={"email": "bob@example.com", "password": PW})
    resp, _ = _authorize(bob)
    assert resp.status_code == 303 and _params(resp)["error"] == "access_denied"
    assert _params(resp)["state"] == "st-abc"


def test_a_disabled_application_is_refused(env):
    env["store"].execute("UPDATE apps SET status='disabled' WHERE id='docweave'")
    ok, _, reason = env["apps"].check_access(env["user"]["id"], "docweave")
    assert ok is False and "停用" in reason


def test_revoking_an_entitlement_takes_effect_immediately(env):
    apps, uid, ws = env["apps"], env["user"]["id"], env["ws"]
    env["store"].execute("UPDATE apps SET access_policy='explicit' WHERE id='docweave'")
    apps.grant(ws, "docweave", uid)
    assert apps.check_access(uid, "docweave")[0] is True
    apps.revoke(ws, "docweave", uid)
    assert apps.check_access(uid, "docweave")[0] is False


def test_the_portal_listing_shows_only_usable_apps_and_no_project_data(env):
    listed = env["apps"].apps_for(env["user"]["id"])
    assert [a["id"] for a in listed] == ["docweave"]
    blob = str(listed)
    assert "project" not in blob and "p_" not in blob, "门户不得聚合业务数据"


# ---------------------------------------------------------------- migration
def test_adopting_existing_members_does_not_touch_the_legacy_workspace(env):
    """The entitlement table starts empty, and empty means nobody under default-deny — so
    existing members must be adopted explicitly or everyone is locked out."""
    store, ident, apps = env["store"], env["ident"], env["apps"]
    ts = now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    out = apps.adopt_existing_members()
    assert out["entitlements"] >= 1

    assert store.one("SELECT COUNT(*) AS n FROM memberships WHERE workspace_id='default'")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM app_entitlements WHERE workspace_id='default'")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM workspace_apps WHERE workspace_id='default'")["n"] == 0


def test_adoption_is_idempotent(env):
    first = env["apps"].adopt_existing_members()
    second = env["apps"].adopt_existing_members()
    assert second["workspaces_enabled"] == 0          # already enabled
    assert second["entitlements"] == first["entitlements"]
    assert env["store"].one("SELECT COUNT(*) AS n FROM app_entitlements")["n"] == first["entitlements"]


# ---------------------------------------------------------------- provisioning
def test_a_newly_created_workspace_can_open_docweave_immediately(env):
    """Found live: `adopt_existing_members` is a one-time migration, so every account created
    after it ran owned a workspace with no applications enabled — sign-in succeeded and then
    said "you have no access to anything"."""
    ident, apps = env["ident"], env["apps"]
    bob = ident.create_user("bob@example.com", PW, status="active")
    ok, wid, reason = apps.check_access(bob["id"], "docweave")
    assert ok is True, reason
    assert wid == ident.workspaces_for(bob["id"])[0]["id"]

    ws = ident.create_workspace("第二个空间", owner_id=bob["id"])
    ok, _, reason = apps.check_access(bob["id"], "docweave", ws["id"])
    assert ok is True, reason


def test_provisioning_does_not_reach_the_legacy_workspace(env):
    """It is seeded by the migration, not by _create_workspace, and must stay empty."""
    store, ts = env["store"], now()
    store.execute("INSERT INTO workspaces (id,name,kind,status,created_at,updated_at) "
                  "VALUES ('default','历史数据（待认领）','legacy','pending_claim',?,?)", (ts, ts))
    env["ident"].create_user("carol@example.com", PW, status="active")
    for table in ("memberships", "workspace_apps", "app_entitlements"):
        n = store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE workspace_id='default'")["n"]
        assert n == 0, f"{table} 不应因为新建账户而碰到 default 空间"


@pytest.mark.parametrize("role", ["accounts", "portal"])
def test_only_docweaves_own_process_sets_its_launch_url(env, monkeypatch, role):
    """Every process registers the app at startup, and DOCWEAVE_APP_URL means *this service's*
    address — so the portal or the account service must not repoint the card at themselves.

    Keyed on the role, not on an app id: `DOCWEAVE_OIDC_APP_ID` is unset on the account service
    and its default is "docweave", so an app-id test passes here while the real deployment
    repoints the card at the login page. That is what happened.
    """
    from platform_core.apps import bootstrap_docweave
    store, ident = env["store"], env["ident"]

    monkeypatch.setenv("DOCWEAVE_ROLE", "app")
    monkeypatch.setenv("DOCWEAVE_APP_URL", "http://docweave.localhost:8775")
    assert bootstrap_docweave(store, ident)["launch_url"] == "http://docweave.localhost:8775"

    monkeypatch.setenv("DOCWEAVE_ROLE", role)
    monkeypatch.setenv("DOCWEAVE_APP_URL", f"http://{role}.localhost:8769")
    monkeypatch.delenv("DOCWEAVE_OIDC_APP_ID", raising=False)
    assert bootstrap_docweave(store, ident)["launch_url"] == "http://docweave.localhost:8775"


def test_the_single_process_deployment_still_sets_its_own_launch_url(env, monkeypatch):
    """`all` is the existing deployment's shape and must keep working unchanged."""
    from platform_core.apps import bootstrap_docweave
    monkeypatch.setenv("DOCWEAVE_ROLE", "all")
    monkeypatch.setenv("DOCWEAVE_APP_URL", "https://docweave.example.com")
    assert bootstrap_docweave(env["store"], env["ident"])["launch_url"] == "https://docweave.example.com"
