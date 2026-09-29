"""The automation chain: machine identity, with no browser cookie anywhere in it.

Two kinds of caller, and the difference is the point. A service account acts as itself and has
its own lifecycle — a person closing a browser must not stop its work. A delegation acts on
someone's behalf, and its authority is the intersection of what the client may ask for, what
that person can do *now*, and what the grant covered.

The tests that matter most here are the ones where the token is still perfectly valid and the
call must fail anyway, because something about the world changed after it was issued.
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core.identity import Identity
from synwevo_core import oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.store import Store
from services.identity.tests.oauth_consent_helper import through_consent

PW = "a-sufficiently-long-passphrase"
REDIRECT = "http://docweave.localhost:8765/auth/callback"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    apps = Apps(store, ident)
    bootstrap_docs_app(store, ident)

    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]

    clients = oidc.Clients(store)
    clients.register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                     redirect_uris=[REDIRECT], scopes="openid profile email docweave.read docweave.write",
                     audiences=["docweave-api"])
    _, secret = clients.register(client_id="nightly-batch", app_id="docweave", name="夜间批处理",
                                 redirect_uris=[], confidential=True, kind="service",
                                 workspace_id=ws, api_scopes="docweave.read docweave.write",
                                 audiences=["docweave-api"])
    return {"api": api, "c": TestClient(api), "store": store, "ident": ident, "apps": apps,
            "alice": alice, "ws": ws, "secret": secret}


def service_token(env, scope: str | None = None) -> dict:
    data = {"grant_type": "client_credentials", "client_id": "nightly-batch",
            "client_secret": env["secret"]}
    if scope is not None:
        data["scope"] = scope
    return env["c"].post("/oidc/token", data=data)


def delegated_token(env, scope: str = "openid docweave.read docweave.write") -> dict:
    """A real authorization-code flow, then the access token it produced."""
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code",
         "scope": scope, "state": "s", "code_challenge": ch, "code_challenge_method": "S256"}
    r = through_consent(c, c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False))
    loc = urllib.parse.urlparse(r.headers["location"])
    code = dict(urllib.parse.parse_qsl(loc.query))["code"]
    out = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code,
                                      "redirect_uri": REDIRECT, "client_id": "docweave-web",
                                      "code_verifier": v})
    assert out.status_code == 200, out.text
    return out.json()


def api(env, token: str, path="/api/v1/projects", method="GET", **kw):
    """A machine call: bearer token, no cookies at all."""
    fresh = TestClient(env["api"])
    return fresh.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)


# ---------------------------------------------------------------- service accounts
def test_a_service_account_gets_a_token_and_can_call_the_api(env):
    r = service_token(env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and "refresh_token" not in body
    assert set(body["scope"].split()) == {"docweave.read", "docweave.write"}

    out = api(env, body["access_token"])
    assert out.status_code == 200, out.text
    assert out.json()["items"] == []


def test_no_browser_cookie_is_involved_anywhere(env):
    """The whole point of the automation chain: it does not depend on a session."""
    tok = service_token(env).json()["access_token"]
    fresh = TestClient(env["api"])
    out = fresh.get("/api/v1/projects", headers={"Authorization": f"Bearer {tok}"})
    assert out.status_code == 200
    assert not fresh.cookies, f"机器调用不应产生任何 Cookie：{dict(fresh.cookies)}"


def test_a_service_account_acts_in_its_registered_workspace(env):
    """It has no membership to resolve a tenant from, so the registration is the answer."""
    tok = service_token(env).json()["access_token"]
    # Something in another workspace must not be visible.
    other = env["ident"].create_user("bob@example.com", PW, status="active")
    other_ws = env["ident"].workspaces_for(other["id"])[0]["id"]
    env["store"].execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p_other','别人的项目',?, 'upload',1,1,0,0,0)", (other_ws,))
    env["store"].execute(
        "INSERT INTO projects (id,name,tenant,step,rev,mapping_rev,created_at,updated_at,deleted) "
        "VALUES ('p_mine','自己的项目',?, 'upload',1,1,0,0,0)", (env["ws"],))
    names = [p["name"] for p in api(env, tok).json()["items"]]
    assert names == ["自己的项目"]
    assert api(env, tok, "/api/v1/projects/p_other").status_code == 404


def test_the_granted_scope_is_the_intersection_not_the_union(env):
    assert set(service_token(env, "docweave.read").json()["scope"].split()) == {"docweave.read"}
    # asking for something the registration does not allow yields only what it does
    env["store"].execute("UPDATE oauth_clients SET api_scopes='docweave.read' WHERE client_id='nightly-batch'")
    body = service_token(env, "docweave.read docweave.write").json()
    assert set(body["scope"].split()) == {"docweave.read"}


def test_a_read_only_service_account_cannot_write(env):
    env["store"].execute("UPDATE oauth_clients SET api_scopes='docweave.read' WHERE client_id='nightly-batch'")
    tok = service_token(env).json()["access_token"]
    assert api(env, tok).status_code == 200
    out = api(env, tok, "/api/v1/projects", method="POST", json={"name": "新项目"})
    assert out.status_code == 403


def test_a_public_client_cannot_be_a_service_account(env):
    """Its secret is the whole credential, so a public service account is a published one."""
    from synwevo_core.errors import ServiceError
    with pytest.raises(ServiceError, match="机密"):
        oidc.Clients(env["store"]).register(
            client_id="oops", app_id="docweave", name="x", redirect_uris=[],
            kind="service", workspace_id=env["ws"], api_scopes="docweave.read")


def test_a_service_account_must_name_a_workspace(env):
    from synwevo_core.errors import ServiceError
    with pytest.raises(ServiceError, match="工作空间"):
        oidc.Clients(env["store"]).register(
            client_id="oops2", app_id="docweave", name="x", redirect_uris=[],
            confidential=True, kind="service", api_scopes="docweave.read")


def test_an_ordinary_client_cannot_use_the_client_credentials_grant(env):
    r = env["c"].post("/oidc/token", data={"grant_type": "client_credentials",
                                           "client_id": "docweave-web"})
    assert r.status_code in (400, 401)
    assert "unauthorized_client" in r.text or "invalid_client" in r.text


def test_the_wrong_secret_is_refused(env):
    r = env["c"].post("/oidc/token", data={"grant_type": "client_credentials",
                                           "client_id": "nightly-batch",
                                           "client_secret": "not-it"})
    assert r.status_code == 401


def test_disabling_the_application_for_that_workspace_stops_the_service_account(env):
    tok = service_token(env).json()["access_token"]
    assert api(env, tok).status_code == 200
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (env["ws"],))
    out = api(env, tok)
    assert out.status_code == 403, "撤权必须在下一次调用生效，不是等令牌过期"
    # ...and it cannot get a new one either
    assert service_token(env).status_code == 400


# ---------------------------------------------------------------- user delegation
def test_a_delegated_token_acts_as_that_person(env):
    body = delegated_token(env)
    out = api(env, body["access_token"])
    assert out.status_code == 200


def test_an_identity_token_is_not_an_api_credential(env):
    """The classic mistake: openid/profile/email proves who signed in, nothing more."""
    body = delegated_token(env, scope="openid profile email")
    out = api(env, body["access_token"])
    assert out.status_code == 403 and "身份凭据" in out.text


def test_suspending_the_person_stops_their_delegation_immediately(env):
    tok = delegated_token(env)["access_token"]
    assert api(env, tok).status_code == 200
    env["ident"].set_status(env["alice"]["id"], "suspended")
    assert api(env, tok).status_code == 401


def test_removing_them_from_the_workspace_stops_their_delegation(env):
    tok = delegated_token(env)["access_token"]
    assert api(env, tok).status_code == 200
    env["store"].execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?",
                         (env["ws"], env["alice"]["id"]))
    assert api(env, tok).status_code == 403


def test_demoting_them_narrows_the_delegation_without_revoking_it(env):
    """The token still says write. Their role no longer does, and the role wins."""
    tok = delegated_token(env)["access_token"]
    assert api(env, tok, "/api/v1/projects", method="POST",
               json={"name": "写得进去"}).status_code in (200, 201)
    env["store"].execute("UPDATE memberships SET role='viewer' WHERE workspace_id=? AND user_id=?",
                         (env["ws"], env["alice"]["id"]))
    assert api(env, tok).status_code == 200, "读仍然可以"
    assert api(env, tok, "/api/v1/projects", method="POST",
               json={"name": "写不进去"}).status_code == 403


def test_revoking_the_token_stops_it(env):
    tok = delegated_token(env)["access_token"]
    assert api(env, tok).status_code == 200
    env["c"].post("/oidc/revoke", data={"token": tok, "client_id": "docweave-web"})
    assert api(env, tok).status_code == 401


def test_a_service_account_survives_the_persons_browser_logout(env):
    """独立服务账户的任务不应因为有人退出浏览器而中断。"""
    tok = service_token(env).json()["access_token"]
    browser = TestClient(env["api"])
    browser.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    browser.get("/oidc/logout?client_id=docweave-web", follow_redirects=False)
    assert api(env, tok).status_code == 200


def test_revoking_one_persons_sessions_does_not_touch_the_service_account(env):
    from synwevo_core.oidc import Grants
    svc = service_token(env).json()["access_token"]
    dele = delegated_token(env)["access_token"]
    Grants(env["store"]).revoke_for_user(env["alice"]["id"], "admin_revoked")
    assert api(env, dele).status_code == 401
    assert api(env, svc).status_code == 200


# ---------------------------------------------------------------- legacy keys
def test_existing_api_keys_keep_working(env):
    """They predate the platform. Revoking them all at once would break running automation."""
    from synwevo_core.auth import Auth
    token, row = Auth(env["store"]).create_key("ci", env["ws"], ("read", "write"), None)
    out = api(env, token)
    assert out.status_code == 200
    assert api(env, token, "/api/v1/projects", method="POST",
               json={"name": "老密钥建的"}).status_code in (200, 201)


def test_an_unknown_token_is_refused_without_saying_which_kind_it_was(env):
    for bad in ("nonsense", "dw_x_y", ""):
        out = api(env, bad)
        assert out.status_code == 401, bad
