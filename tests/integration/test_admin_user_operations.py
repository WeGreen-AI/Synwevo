"""Account suspension and recovery observed from the deployed composition and Docs APIs.

Synthetic users have separate browser sessions and personal workspaces. Projects are created
through the real Docs HTTP route; delegations use the existing S256 authorization-code and
consent helper, then the actual token endpoint. These tests do not cover Message streams,
collaboration sockets, multi-process workers or production/physical-browser acceptance.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.apps import bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_server.app import create_app

from tests.integration.platform_fixture import make_platform_admin
from tests.integration.test_machine_identity import PW, REDIRECT, api as machine_call, delegated_token

REASON = "Isolated integration fixture: account lifecycle verification"
ALICE_PROJECT = "Private Alice fixture document"
BOB_PROJECT = "Private Bob fixture document"


def post(client, path, body):
    return client.post(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


@pytest.fixture()
def world(tmp_path, monkeypatch):
    data = tmp_path / "isolated-composition"
    for key, value in {"DATA_DIR": str(data), "UI_AUTH": "session", "COOKIE_SECURE": "0",
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "ISSUER": "http://accounts.localhost:8770"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    app = create_app(str(data), role="all", start_jobs=False)
    store = Store(data)
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    users, clients = {}, {}
    for name in ("alice", "bob", "root"):
        user = ident.create_user(f"{name}@example.com", PW, status="active")
        store.execute("UPDATE users SET email_verified_at=? WHERE id=?", (now(), user["id"]))
        users[name] = make_platform_admin(store, user) if name == "root" else ident.get_user(user["id"])
    oidc.Clients(store).register(client_id="docweave-web", app_id="docweave", name="Fixture Docs delegation",
        redirect_uris=[REDIRECT], scopes="openid profile email offline_access docweave.read docweave.write",
        audiences=["docweave-api"])

    def login(name):
        client = TestClient(app)
        response = post(client, "/api/auth/login", {"email": users[name]["email"], "password": PW})
        assert response.status_code == 200, response.text
        return client

    for name in users:
        clients[name] = login(name)
    projects = {}
    for name, title in (("alice", ALICE_PROJECT), ("bob", BOB_PROJECT)):
        response = post(clients[name], "/api/projects", {"name": title})
        assert response.status_code == 200, response.text
        projects[name] = response.json()["id"]
    assert post(clients["root"], "/api/auth/reauth", {"password": PW, "scope": "admin"}).status_code == 200
    yield {"api": app, "store": store, "ident": ident, "users": users, "clients": clients,
           "projects": projects, "login": login}
    for client in clients.values():
        client.close()


def change_status(w, status):
    admin, uid = w["clients"]["root"], w["users"]["alice"]["id"]
    plan = post(admin, f"/api/admin/users/{uid}/status-preview", {"status": status})
    assert plan.status_code == 200, plan.text
    body = {"status": status, "reason": REASON, "preview_token": plan.json()["preview_token"],
            "idempotency_key": uuid.uuid4().hex}
    response = post(admin, f"/api/admin/users/{uid}/status", body)
    assert response.status_code == 200, response.text
    # A transport retry uses the same command; no new revocation or success audit is created.
    retry = post(admin, f"/api/admin/users/{uid}/status", body)
    assert retry.status_code == 200 and retry.json() == response.json()
    return response.json()


def assert_admin_has_no_content_access(w):
    admin = w["clients"]["root"]
    for name in ("alice", "bob"):
        assert admin.get(f"/api/projects/{w['projects'][name]}").status_code == 404
        workspace = w["ident"].workspaces_for(w["users"][name]["id"])[0]["id"]
        assert post(admin, "/api/auth/workspace", {"workspace_id": workspace}).status_code == 404
    listing = admin.get("/api/projects")
    assert listing.status_code == 200
    assert ALICE_PROJECT not in listing.text and BOB_PROJECT not in listing.text
    for path in ("/api/admin/overview", "/api/admin/audit"):
        response = admin.get(path)
        assert response.status_code == 200
        assert ALICE_PROJECT not in response.text and BOB_PROJECT not in response.text


def test_suspension_ends_docs_sessions_and_real_delegations_and_restore_requires_new_signin(world):
    alice, bob = world["clients"]["alice"], world["clients"]["bob"]
    pid, other_pid = world["projects"]["alice"], world["projects"]["bob"]
    assert alice.get(f"/api/projects/{pid}").status_code == 200
    assert alice.get(f"/api/projects/{other_pid}").status_code == 404
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert_admin_has_no_content_access(world)
    tokens = delegated_token(world, scope="openid offline_access docweave.read docweave.write")
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    response = machine_call(world, access, f"/api/v1/projects/{pid}")
    assert response.status_code == 200 and ALICE_PROJECT in response.text
    assert machine_call(world, access, f"/api/v1/projects/{other_pid}").status_code == 404

    suspended = change_status(world, "suspended")
    assert suspended["state"] == "succeeded" and suspended["status"] == "suspended"
    assert suspended["sessions_revoked"] >= 2 and suspended["tokens_revoked"] >= 1
    assert alice.get(f"/api/projects/{pid}").status_code == 401
    assert machine_call(world, access, f"/api/v1/projects/{pid}").status_code == 401
    with TestClient(world["api"]) as fresh:
        denied = fresh.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": refresh,
                                               "client_id": "docweave-web"})
    assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"
    # Another workspace and its existing user session are unaffected by account governance.
    assert bob.get(f"/api/projects/{other_pid}").status_code == 200
    assert_admin_has_no_content_access(world)

    restored = change_status(world, "active")
    assert restored["status"] == "active"
    assert alice.get(f"/api/projects/{pid}").status_code == 401
    assert machine_call(world, access, f"/api/v1/projects/{pid}").status_code == 401
    with TestClient(world["api"]) as fresh:
        denied = fresh.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": refresh,
                                               "client_id": "docweave-web"})
    assert denied.status_code == 400 and denied.json()["error"] == "invalid_grant"
    with world["login"]("alice") as recovered:
        response = recovered.get(f"/api/projects/{pid}")
        assert response.status_code == 200 and ALICE_PROJECT in response.text
        assert recovered.get(f"/api/projects/{other_pid}").status_code == 404
    replacement = delegated_token(world)["access_token"]
    assert machine_call(world, replacement, f"/api/v1/projects/{pid}").status_code == 200
    assert machine_call(world, replacement, f"/api/v1/projects/{other_pid}").status_code == 404
    assert_admin_has_no_content_access(world)

    audits = world["store"].all("SELECT actor_id,object_id,result,meta FROM audit_events "
                                "WHERE action='admin.user.status' ORDER BY seq")
    assert len(audits) == 2
    assert [json.loads(row["meta"])["operation_id"] for row in audits] == [suspended["operation_id"], restored["operation_id"]]
    assert all(row["actor_id"] == world["users"]["root"]["id"] and row["object_id"] == world["users"]["alice"]["id"]
               and row["result"] == "ok" for row in audits)


def test_workspace_owners_cannot_preview_or_execute_a_platform_account_action(world):
    uid = world["users"]["alice"]["id"]
    for name in ("alice", "bob"):
        client = world["clients"][name]
        workspace = world["ident"].workspaces_for(world["users"][name]["id"])[0]["id"]
        assert world["ident"].membership(workspace, world["users"][name]["id"])["role"] == "owner"
        for suffix in ("status-preview", "status"):
            response = post(client, f"/api/admin/users/{uid}/{suffix}", {"status": "suspended", "reason": REASON})
            assert response.status_code == 404 and response.json()["error"] == "not_found"
    assert world["clients"]["alice"].get(f"/api/projects/{world['projects']['alice']}").status_code == 200
    assert not world["store"].all("SELECT * FROM admin_operations")
