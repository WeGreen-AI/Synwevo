"""A platform token must have been meant for the Docs API, by a client registered for Docs.

The Docs API used to accept any platform access token that carried a `docweave.*` scope. It
never looked at the token's audience, and it checked the *client's* application for access —
so a token obtained through the public portal client (application `platform`, whose rule is
"any member may open it") reached /api/v1 with write access in workspaces where the person had
no Synwevo Docs entitlement at all. These tests pin the refusals down from the resource
server's side, independent of what the token endpoint lets through.

Tokens here are either obtained through the real token endpoint or, where the point is what
the resource server does with a token it should never have been handed, written straight into
the grant store with `Grants.issue_*` (a labelled test fixture, not a mock: it is the same code
path the token endpoint uses to mint them).
"""
from __future__ import annotations

import base64
import hashlib
import importlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.machine import LEGACY_DOCS_AUDIENCE, MachineAuth, docs_resource
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
DOCS_CB = "http://docweave.localhost:8765/auth/callback"
PORTAL_CB = "http://portal.localhost:8780/auth/callback"
OTHER_API = "https://reports.example/api"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    monkeypatch.delenv("SYNWEVO_DOCS_RESOURCE", raising=False)
    monkeypatch.delenv("SYNWEVO_DOCS_REQUIRE_AUDIENCE", raising=False)
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]

    clients = oidc.Clients(store)
    # Exactly as scripts/platform.py registers them: the portal is a public client of the
    # platform application, Docs' web client belongs to Docs.
    clients.register(client_id="portal-web", app_id="platform", name="平台门户", redirect_uris=[PORTAL_CB])
    clients.register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs",
                     redirect_uris=[DOCS_CB],
                     scopes="openid profile email docweave.read docweave.write docweave.manage")
    _, secret = clients.register(client_id="nightly-batch", app_id="docweave", name="夜间批处理",
                                 redirect_uris=[], confidential=True, kind="service", workspace_id=ws,
                                 api_scopes="docweave.read docweave.write",
                                 audiences=[docs_resource(), OTHER_API])
    return {"api": api, "store": store, "ident": ident, "apps": Apps(store, ident),
            "clients": clients, "alice": alice, "ws": ws, "secret": secret}


def call(env, token: str, path="/api/v1/projects", method="GET", **kw):
    return TestClient(env["api"]).request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)


def err(resp) -> str:
    return (resp.json().get("error") or {}).get("code")


def minted_delegation(env, client_id: str, scope: str, audience: str | None = None) -> str:
    """FIXTURE: an access token for alice as the grant store would issue it for `client_id`."""
    client = env["clients"].get(client_id)
    code_row = {"user_id": env["alice"]["id"], "scope": scope, "central_session_id": None,
                "workspace_id": env["ws"]}
    return oidc.Grants(env["store"]).issue_tokens(client=client, code_row=code_row, with_refresh=True,
                                                 audience=audience)["access_token"]


def minted_service(env, client_id: str, scope: str, audience: str | None = None) -> str:
    """FIXTURE: a client-credentials access token as the grant store would issue it."""
    client = env["clients"].get(client_id)
    return oidc.Grants(env["store"]).issue_for_service(client=client, scope=scope,
                                                      audience=audience)["access_token"]


def pkce_code_flow(env, client_id: str, redirect: str, scope: str):
    """The real browser flow a person can run by hand against any public client."""
    c = TestClient(env["api"])
    c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW})
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": scope,
         "state": "s", "code_challenge": ch, "code_challenge_method": "S256"}
    r = c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))
    if "code" not in params:
        return None, params
    out = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": params["code"],
                                      "redirect_uri": redirect, "client_id": client_id, "code_verifier": v})
    return out, params


# ------------------------------------------------------------------ the portal-client bypass (F005/F333)
def test_a_portal_client_delegation_is_refused_even_with_docs_scopes(env):
    """The resource server's own refusal: a token from another application's client."""
    tok = minted_delegation(env, "portal-web", "openid docweave.read docweave.write")
    r = call(env, tok)
    assert r.status_code == 403 and err(r) == "wrong_audience", r.text
    w = call(env, tok, method="POST", json={"name": "经门户写入"})
    assert w.status_code == 403 and err(w) == "wrong_audience"
    assert not env["store"].one("SELECT 1 FROM projects WHERE name='经门户写入'")


def test_the_portal_bypass_does_not_work_end_to_end(env):
    """The reported chain: PKCE against the public portal client, then Docs with the token —
    in a workspace where Synwevo Docs was never enabled. Whatever the token endpoint grants,
    Docs must not accept it."""
    env["store"].execute("DELETE FROM workspace_apps WHERE workspace_id=?", (env["ws"],))
    out, params = pkce_code_flow(env, "portal-web", PORTAL_CB, "openid offline_access docweave.write")
    if out is None:
        # The provider already refused to grant a scope the portal is not registered for.
        assert params.get("error") in ("invalid_scope", "access_denied"), params
        return
    assert out.status_code == 200, out.text
    tok = out.json()["access_token"]
    r = call(env, tok, method="POST", json={"name": "绕过"})
    assert r.status_code in (401, 403), r.text
    assert not env["store"].one("SELECT 1 FROM projects WHERE name='绕过'")


def test_a_service_account_of_another_application_is_refused(env):
    env["store"].execute("INSERT INTO apps (id,name,icon,launch_url,status,access_policy,created_at,updated_at) "
                         "VALUES ('reports','报表',NULL,'http://reports.localhost/', 'active','members',0,0)")
    env["store"].execute("INSERT INTO workspace_apps (workspace_id,app_id,enabled_at,enabled_by) VALUES (?,?,0,NULL)",
                         (env["ws"], "reports"))
    env["store"].execute(
        "INSERT INTO oauth_clients (client_id,app_id,name,secret_hash,redirect_uris,post_logout_redirect_uris,"
        "scopes,audiences,token_endpoint_auth_method,require_pkce,kind,workspace_id,api_scopes,created_at,updated_at) "
        "VALUES ('reports-batch','reports','报表批处理','x','[]','[]','', '[]','client_secret_post',1,'service',?,"
        "'docweave.read docweave.write',0,0)", (env["ws"],))
    tok = minted_service(env, "reports-batch", "docweave.read docweave.write")
    r = call(env, tok)
    assert r.status_code == 403 and err(r) == "wrong_audience", r.text


def test_entitlement_is_checked_for_docs_not_for_the_clients_app(env):
    """Revoking alice's Docs entitlement stops her delegation, even though she is still a member."""
    tok = minted_delegation(env, "docweave-web", "docweave.read")
    assert call(env, tok).status_code == 200
    env["store"].execute("UPDATE apps SET access_policy='explicit' WHERE id='docweave'")
    env["store"].execute("DELETE FROM app_entitlements WHERE user_id=?", (env["alice"]["id"],))
    assert call(env, tok).status_code == 403


# ------------------------------------------------------------------ audience (R37)
def test_a_token_for_another_resource_is_refused_with_invalid_audience(env):
    tok = minted_service(env, "nightly-batch", "docweave.read", audience=OTHER_API)
    r = call(env, tok)
    assert r.status_code == 401 and err(r) == "invalid_audience", r.text
    assert docs_resource() in r.json()["error"]["message"]


def test_a_token_for_the_docs_resource_is_accepted(env):
    assert call(env, minted_service(env, "nightly-batch", "docweave.read", audience=docs_resource())).status_code == 200


def test_the_documented_legacy_audience_still_names_the_docs_api(env):
    """`docweave-api` is written into issued tokens and into registrations made from the docs."""
    tok = minted_service(env, "nightly-batch", "docweave.read", audience=LEGACY_DOCS_AUDIENCE)
    assert call(env, tok).status_code == 200


def test_a_configured_resource_identifier_is_the_one_enforced(env, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DOCS_RESOURCE", "https://docs.example.com/api")
    ok = minted_service(env, "nightly-batch", "docweave.read", audience="https://docs.example.com/api")
    assert call(env, ok).status_code == 200
    stale = minted_service(env, "nightly-batch", "docweave.read", audience="urn:synwevo:app:docweave")
    assert call(env, stale).status_code == 401


def test_no_audience_is_accepted_unless_the_deployment_requires_one(env, monkeypatch):
    env["store"].execute("UPDATE oauth_clients SET audiences='[]' WHERE client_id='nightly-batch'")
    tok = minted_service(env, "nightly-batch", "docweave.read")
    row = env["store"].one("SELECT audience FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")
    assert row["audience"] is None
    assert call(env, tok).status_code == 200
    monkeypatch.setenv("SYNWEVO_DOCS_REQUIRE_AUDIENCE", "1")
    r = call(env, tok)
    assert r.status_code == 401 and err(r) == "invalid_audience"


def test_resource_indicator_end_to_end(env):
    """R37 acceptance through the real token endpoint (RFC 8707 `resource`)."""
    def token(resource):
        return TestClient(env["api"]).post("/oidc/token", data={
            "grant_type": "client_credentials", "client_id": "nightly-batch", "client_secret": env["secret"],
            "scope": "docweave.read", "resource": resource})
    other = token(OTHER_API)
    assert other.status_code == 200, other.text
    r = call(env, other.json()["access_token"])
    assert r.status_code == 401 and err(r) == "invalid_audience"
    mine = token(docs_resource())
    assert mine.status_code == 200 and call(env, mine.json()["access_token"]).status_code == 200
    unknown = token("https://unknown.example")
    assert unknown.status_code == 400 and unknown.json()["error"] == "invalid_target"


# ------------------------------------------------------------------ registration caps the token
def test_narrowing_a_service_accounts_registration_takes_effect_on_the_next_call(env):
    tok = minted_service(env, "nightly-batch", "docweave.read docweave.write")
    assert call(env, tok, method="POST", json={"name": "写入一"}).status_code in (200, 201)
    env["store"].execute("UPDATE oauth_clients SET api_scopes='docweave.read' WHERE client_id='nightly-batch'")
    assert call(env, tok).status_code == 200
    w = call(env, tok, method="POST", json={"name": "写入二"})
    assert w.status_code == 403, "令牌里写着 write，但登记已经收回——登记说了算"
    env["store"].execute("UPDATE oauth_clients SET api_scopes='' WHERE client_id='nightly-batch'")
    assert call(env, tok).status_code == 403


def test_a_service_account_scope_outside_its_registration_is_ignored(env):
    """A token row carrying a scope the account was never registered for (for instance minted
    before a registration was narrowed) cannot use it."""
    tok = minted_service(env, "nightly-batch", "docweave.read docweave.manage")
    who = call(env, tok, "/api/v1/whoami").json()
    assert who["scopes"] == ["docweave.read"]


def test_a_delegation_is_capped_by_what_the_client_is_registered_for(env):
    tok = minted_delegation(env, "docweave-web", "openid docweave.read docweave.write")
    assert call(env, tok, method="POST", json={"name": "委托写入"}).status_code in (200, 201)
    env["store"].execute("UPDATE oauth_clients SET scopes='openid profile email docweave.read' "
                         "WHERE client_id='docweave-web'")
    assert call(env, tok).status_code == 200
    assert call(env, tok, method="POST", json={"name": "委托写入二"}).status_code == 403


def test_a_service_account_that_moved_workspace_needs_a_new_token(env):
    tok = minted_service(env, "nightly-batch", "docweave.read")
    other = env["ident"].create_user("bob@example.com", PW, status="active")
    bob_ws = env["ident"].workspaces_for(other["id"])[0]["id"]
    env["store"].execute("UPDATE oauth_clients SET workspace_id=? WHERE client_id='nightly-batch'", (bob_ws,))
    assert call(env, tok).status_code == 401


# ------------------------------------------------------------------ docweave.manage and principal shape
def test_manage_follows_the_persons_admin_role(env):
    tok = minted_delegation(env, "docweave-web", "docweave.read docweave.manage")
    p = MachineAuth(env["store"], env["ident"], None).verify(f"Bearer {tok}")
    assert set(p["scopes"]) == {"docweave.read", "docweave.manage"}      # alice owns her workspace
    MachineAuth.require(p, "admin")                                       # manage <-> legacy admin
    env["store"].execute("UPDATE memberships SET role='editor' WHERE user_id=?", (env["alice"]["id"],))
    p = MachineAuth(env["store"], env["ident"], None).verify(f"Bearer {tok}")
    assert set(p["scopes"]) == {"docweave.read"}
    from synwevo_core.errors import ServiceError
    with pytest.raises(ServiceError):
        MachineAuth.require(p, "admin")


def test_manage_does_not_imply_read_or_write(env):
    from synwevo_core.errors import ServiceError
    p = {"kind": "service_account", "scopes": ("docweave.manage",)}
    MachineAuth.require(p, "admin")
    for need in ("read", "write"):
        with pytest.raises(ServiceError):
            MachineAuth.require(p, need)


def test_a_delegated_principal_carries_its_grant_family_role_and_export_right(env):
    tok = minted_delegation(env, "docweave-web", "docweave.read")
    p = MachineAuth(env["store"], env["ident"], None).verify(f"Bearer {tok}")
    row = env["store"].one("SELECT id, family_id FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")
    assert p["grant_family"] == (row["family_id"] or row["id"])
    assert p["role"] == "owner" and p["tenant"] == env["ws"] and p["lineage"] is None


def test_export_needs_the_persons_export_right(env):
    """A viewer who may look but not take files away cannot delegate downloading either."""
    from synwevo_core.errors import ServiceError
    tok = minted_delegation(env, "docweave-web", "docweave.read")
    env["store"].execute("UPDATE memberships SET role='viewer', can_export=0 WHERE user_id=?", (env["alice"]["id"],))
    p = MachineAuth(env["store"], env["ident"], None).verify(f"Bearer {tok}")
    MachineAuth.require(p, "read")
    with pytest.raises(ServiceError) as e:
        MachineAuth.require(p, "export")
    assert e.value.code == "export_not_allowed"
    env["store"].execute("UPDATE memberships SET can_export=1 WHERE user_id=?", (env["alice"]["id"],))
    p = MachineAuth(env["store"], env["ident"], None).verify(f"Bearer {tok}")
    MachineAuth.require(p, "export")
