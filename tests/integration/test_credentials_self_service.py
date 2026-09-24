"""Governance routes with platform tokens: what a service account or a delegation may do to
its own credential through /api/v1.

A service account's secret is rotated by the operator in the account service, never through the
Docs API; revoking "self" ends every outstanding token of the account (its secret stays, and
says so). A delegation's self-revocation ends its whole grant family, refresh token included.
"""
from __future__ import annotations

import importlib
import time

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal, oidc
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.machine import docs_resource
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
DOCS_CB = "http://docweave.localhost:8765/auth/callback"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    import synwevo_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    alice = ident.create_user("alice@example.com", PW, status="active")
    ws = ident.workspaces_for(alice["id"])[0]["id"]
    clients = oidc.Clients(store)
    clients.register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs", redirect_uris=[DOCS_CB],
                     scopes="openid profile email offline_access docweave.read docweave.write docweave.manage")
    _, secret = clients.register(client_id="nightly-batch", app_id="docweave", name="夜间批处理",
                                 redirect_uris=[], confidential=True, kind="service", workspace_id=ws,
                                 api_scopes="docweave.read docweave.write docweave.manage")
    return {"api": api, "store": store, "clients": clients, "alice": alice, "ws": ws, "secret": secret,
            "data": str(tmp_path / "d")}


def call(env, token, path, method="GET", **kw):
    return TestClient(env["api"]).request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)


def service_token(env, scope="docweave.read docweave.manage") -> str:
    r = TestClient(env["api"]).post("/oidc/token", data={
        "grant_type": "client_credentials", "client_id": "nightly-batch", "client_secret": env["secret"],
        "scope": scope})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def delegation(env, scope="openid offline_access docweave.read") -> dict:
    """FIXTURE: a grant for alice through Docs' web client, as the token endpoint would issue it."""
    client = env["clients"].get("docweave-web")
    return oidc.Grants(env["store"]).issue_tokens(
        client=client, with_refresh=True, audience=docs_resource(),
        code_row={"user_id": env["alice"]["id"], "scope": scope, "central_session_id": None,
                  "workspace_id": env["ws"]})


def test_a_service_account_describes_itself_without_secrets(env):
    tok = service_token(env)
    r = call(env, tok, "/api/v1/credentials/self")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "service_account" and body["client_id"] == "nightly-batch"
    assert body["workspace"] == env["ws"] and "docweave.manage" in body["registered_scopes"]
    assert body["rotation"]["self_service"] is False
    assert env["secret"] not in r.text and tok not in r.text


def test_a_service_account_cannot_rotate_its_secret_through_the_docs_api(env):
    r = call(env, service_token(env), "/api/v1/credentials/self/rotate", method="POST", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "not_rotatable"


def test_a_service_account_revoking_itself_ends_its_outstanding_tokens(env):
    t1, t2 = service_token(env), service_token(env, "docweave.read")
    before = time.time()
    r = call(env, t1, "/api/v1/credentials/self/revoke", method="POST")
    assert r.status_code == 200, r.text
    assert r.json()["tokens_revoked"] == 2 and "client_secret" in r.json()["notice"]
    assert call(env, t1, "/api/v1/projects").status_code == 401
    assert call(env, t2, "/api/v1/projects").status_code == 401
    entries = [e for e in journal.read(env["data"], since=before - 1) if e["kind"] == "tokens_revoked"]
    assert entries and entries[-1]["client_id"] == "nightly-batch"
    audit = env["store"].one("SELECT * FROM audit_events WHERE action='api.credential.revoke'")
    assert audit["actor_id"] == "service:nightly-batch" and audit["workspace_id"] == env["ws"]
    # The registration itself is the operator's: a fresh token still works.
    assert call(env, service_token(env), "/api/v1/projects").status_code == 200


def test_a_delegation_revoking_itself_ends_the_whole_grant_family(env):
    grant = delegation(env)
    assert call(env, grant["access_token"], "/api/v1/projects").status_code == 200
    me = call(env, grant["access_token"], "/api/v1/credentials/self").json()
    assert me["kind"] == "delegated" and me["grant_family"] and me["role"] == "owner"
    r = call(env, grant["access_token"], "/api/v1/credentials/self/revoke", method="POST")
    assert r.status_code == 200, r.text
    assert call(env, grant["access_token"], "/api/v1/projects").status_code == 401
    refreshed = TestClient(env["api"]).post("/oidc/token", data={
        "grant_type": "refresh_token", "client_id": "docweave-web", "refresh_token": grant["refresh_token"]})
    assert refreshed.status_code == 400, "刷新令牌属于同一授权链，必须一起失效"
    entries = [e for e in journal.read(env["data"]) if e["kind"] == "tokens_revoked"]
    assert entries and entries[-1]["family_id"] == me["grant_family"]


def test_another_grant_of_the_same_person_is_not_touched(env):
    mine, other = delegation(env), delegation(env)
    call(env, mine["access_token"], "/api/v1/credentials/self/revoke", method="POST")
    assert call(env, other["access_token"], "/api/v1/projects").status_code == 200


def test_manage_scope_on_a_platform_token_satisfies_admin_routes(env):
    """`docweave.manage` is the platform vocabulary for the legacy `admin` scope."""
    tok = service_token(env, "docweave.read docweave.write docweave.manage")
    pid = call(env, tok, "/api/v1/projects", method="POST", json={"name": "待删"}).json()["id"]
    assert call(env, tok, f"/api/v1/projects/{pid}", method="DELETE").status_code == 200
    rw = service_token(env, "docweave.read docweave.write")
    pid2 = call(env, rw, "/api/v1/projects", method="POST", json={"name": "删不掉"}).json()["id"]
    assert call(env, rw, f"/api/v1/projects/{pid2}", method="DELETE").status_code == 403
