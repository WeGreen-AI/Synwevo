"""Real composition, isolated accounts/data/file mail; never the developer's data directory."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core import authority, journal
from synwevo_core.api_platform import Approvals, SCOPES, record_call
from synwevo_core.auth import Auth
from synwevo_core.context import as_tenant
from synwevo_core.identity import Identity
from synwevo_core.machine import SCOPE_READ, SCOPE_WRITE, SCOPE_MANAGE
from synwevo_core.oidc import docs_resource
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now
from synwevo_server.app import create_app

BASE = "/api/admin/api-platform"
OWNER = "/api/auth/api-approvals"
PW = "isolated-fixture-long-passphrase-47"
REASON = "Isolated integration fixture approval"


def post(c, path, body=None):
    return c.post(path, json=body or {}, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def reauth(c):
    r = post(c, "/api/auth/reauth", {"password": PW, "scope": "admin"})
    assert r.status_code == 200, r.text


@pytest.fixture()
def plane(tmp_path, monkeypatch):
    data = tmp_path / "isolated"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(data))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://testserver")
    api = create_app(str(data), role="all", start_jobs=False)
    store = Store(data)
    ident = Identity(store)
    users, clients = {}, {}
    for name, role in (("administrator", "admin"), ("owner", "none"), ("stranger", "none"), ("operator", "operator")):
        users[name] = ident.create_user(f"{name}@example.test", PW, status="active", platform_role=role)
        c = TestClient(api)
        r = post(c, "/api/auth/login", {"email": users[name]["email"], "password": PW})
        assert r.status_code == 200, r.text
        clients[name] = c
    ws = ident.workspaces_for(users["owner"]["id"])[0]["id"]
    other_ws = ident.workspaces_for(users["stranger"]["id"])[0]["id"]
    with as_tenant(ws):
        project = api.state.app.create_project("Private approval fixture document")
    yield {"api": api, "store": store, "ident": ident, "users": users, "clients": clients,
           "admin": clients["administrator"], "owner": clients["owner"], "ws": ws,
           "other_ws": other_ws, "project": project}
    for c in clients.values():
        c.close()


def request_approval(e, kind="service_account", scopes=None, **extra):
    reauth(e["admin"])
    body = {"workspace_id": e["ws"], "name": "Fixture automation", "credential_kind": kind,
            "scopes": scopes or [SCOPE_READ, SCOPE_WRITE], "resources": [docs_resource()],
            "expires_in_days": 30, "reason": REASON, **extra}
    r = post(e["admin"], BASE + "/approvals", body)
    assert r.status_code == 201, r.text
    return r.json()


def approve(e, row, scopes=None):
    reauth(e["owner"])
    r = post(e["owner"], f"{OWNER}/{row['id']}/approve", {"reason": REASON,
        "scopes": scopes or row["requested_scopes"], "resources": row["requested_resources"]})
    assert r.status_code == 200, r.text
    return r.json()


def issue(e, row):
    r = post(e["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON})
    assert r.status_code == 201, r.text
    assert r.headers["cache-control"] == "no-store"
    return r.json()


def service_token(e, credential):
    return TestClient(e["api"]).post("/oidc/token", data={"grant_type": "client_credentials",
        "client_id": credential["client_id"], "client_secret": credential["client_secret"],
        "resource": docs_resource()})


def call(e, token, path="/api/v1/projects", **kw):
    return TestClient(e["api"]).get(path, headers={"Authorization": f"Bearer {token}"}, **kw)


@pytest.mark.parametrize("role", ["owner", "stranger", "operator"])
def test_only_platform_admin_has_management_permission(plane, role):
    c = plane["clients"][role]
    for path in ("overview", "credentials", "approvals", "calls", "operations", "openapi"):
        assert c.get(f"{BASE}/{path}").status_code == 404
    assert post(c, BASE + "/approvals", {"reason": REASON}).status_code == 404
    assert post(c, BASE + "/request", {"reason": REASON}).status_code == 404


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_workspace_roles_cannot_use_console_or_approve(plane, role):
    e = plane
    uid = e["users"]["stranger"]["id"]
    e["ident"].add_member(e["ws"], uid, role)
    row = request_approval(e)
    c = e["clients"]["stranger"]
    reauth(c)
    assert c.get(BASE + "/overview").status_code == 404
    assert post(c, f"{OWNER}/{row['id']}/approve", {"reason": REASON,
        "scopes": row["requested_scopes"], "resources": row["requested_resources"]}).status_code == 403


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_organization_administrators_cannot_access_platform_console(plane, role):
    from synwevo_core.organizations import Organizations
    e = plane
    org = Organizations(e["store"], e["ident"]).create(e["users"]["owner"]["id"],
        "API approval fixture organization", "Fixture workspace", "api-platform-fixture-org")
    if role == "owner":
        client = e["owner"]
    else:
        # Explicit organization membership fixture; no workspace membership is implied.
        e["store"].insert("organization_memberships", {"organization_id": org["organization"]["id"],
            "user_id": e["users"]["stranger"]["id"], "role": "admin", "created_at": now(), "updated_at": now()})
        client = e["clients"]["stranger"]
    assert client.get(BASE + "/overview").status_code == 404
    assert post(client, BASE + "/approvals", {"reason": REASON}).status_code == 404


def test_signed_out_and_live_admin_demotion(plane):
    e = plane
    assert TestClient(e["api"]).get(BASE + "/overview").status_code == 401
    assert e["admin"].get(BASE + "/overview").json()["permission"] == "api_platform.manage"
    e["store"].execute("UPDATE users SET platform_role='none' WHERE id=?", (e["users"]["administrator"]["id"],))
    assert e["admin"].get(BASE + "/overview").status_code == 404


def test_reauth_reason_csrf_and_no_data_granted(plane):
    e = plane
    assert post(e["admin"], BASE + "/approvals", {"reason": REASON}).json()["error"] == "reauth_required"
    reauth(e["admin"])
    assert post(e["admin"], BASE + "/approvals", {"reason": "short"}).status_code == 400
    assert e["admin"].post(BASE + "/approvals", json={"reason": REASON}).status_code == 403
    assert e["admin"].get(f"/api/projects/{e['project']['id']}").status_code == 404
    assert e["admin"].get("/api/v1/projects").status_code == 401
    response = post(e["admin"], BASE + "/request", {"reason": REASON, "path": "/api/v1/projects"})
    assert response.status_code == 400
    assert "Private approval fixture" not in e["admin"].get(BASE + "/overview").text


def test_owner_consent_issue_is_one_use_and_scope_intersection(plane):
    e = plane
    row = request_approval(e)
    assert post(e["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON}).status_code == 403
    assert post(e["admin"], f"{OWNER}/{row['id']}/approve", {"reason": REASON}).status_code == 403
    approve(e, row, [SCOPE_READ])
    assert post(e["owner"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON}).status_code == 404
    assert post(e["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON, "scopes": list(SCOPES)}).status_code == 400
    credential = issue(e, row)
    assert post(e["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON}).status_code == 409
    token = service_token(e, credential)
    assert token.status_code == 200, token.text
    token = token.json()
    assert token["scope"] == SCOPE_READ
    assert call(e, token["access_token"]).json()["items"][0]["name"] == "Private approval fixture document"
    response = TestClient(e["api"]).post("/api/v1/projects", headers={"Authorization": f"Bearer {token['access_token']}"}, json={"name": "denied"})
    assert response.status_code == 403
    listed = e["admin"].get(BASE + "/credentials").text
    assert credential["client_secret"] not in listed and "secret_hash" not in listed
    audit = e["store"].all("SELECT action,meta FROM audit_events WHERE action LIKE 'api_platform.%'")
    assert credential["client_secret"] not in str(audit)
    assert any(x["action"] == "api_platform.credential.issue" for x in audit)


@pytest.mark.parametrize("change", ["demote", "suspend", "revoke_entitlement", "expire", "revoke"])
def test_live_approval_authority_checked_at_issue_use_and_deferred_work(plane, change):
    e = plane
    row = request_approval(e)
    approve(e, row)
    credential = issue(e, row)
    access = service_token(e, credential).json()["access_token"]
    assert call(e, access).status_code == 200
    desc = {"kind": "service_account", "client_id": credential["client_id"], "tenant": e["ws"]}
    assert authority.check(e["store"], desc, need="write")[0]
    if change == "demote":
        e["store"].execute("UPDATE memberships SET role='admin' WHERE user_id=? AND workspace_id=?", (e["users"]["owner"]["id"], e["ws"]))
    elif change == "suspend":
        e["store"].execute("UPDATE users SET status='suspended' WHERE id=?", (e["users"]["owner"]["id"],))
    elif change == "revoke_entitlement":
        from synwevo_core.apps import Apps, DOCS_APP_ID
        Apps(e["store"], e["ident"]).revoke(e["ws"], DOCS_APP_ID, e["users"]["owner"]["id"])
    elif change == "expire":
        e["store"].execute("UPDATE api_platform_approvals SET expires_at=? WHERE id=?", (now() - 1, row["id"]))
    else:
        assert post(e["owner"], f"{OWNER}/{row['id']}/revoke", {"reason": REASON}).status_code == 200
    assert call(e, access).status_code in (401, 403)
    assert service_token(e, credential).status_code in (400, 401, 403)
    assert not authority.check(e["store"], desc, need="write")[0]


def test_resource_change_and_unapproved_scopes_cannot_expand_access(plane):
    e = plane
    row = request_approval(e)
    approve(e, row, [SCOPE_READ])
    credential = issue(e, row)
    # Simulate a later operator registration change: live approval still caps the client.
    e["store"].execute("UPDATE oauth_clients SET api_scopes=? WHERE client_id=?", (" ".join(SCOPES), credential["client_id"]))
    assert service_token(e, credential).json()["scope"] == SCOPE_READ
    e["store"].execute("UPDATE oauth_clients SET workspace_id=? WHERE client_id=?", (e["other_ws"], credential["client_id"]))
    assert service_token(e, credential).status_code in (400, 403)


def test_owner_loses_authority_before_credential_issue(plane):
    row = request_approval(plane)
    approve(plane, row)
    plane["store"].execute("DELETE FROM memberships WHERE user_id=? AND workspace_id=?", (plane["users"]["owner"]["id"], plane["ws"]))
    assert post(plane["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON}).status_code == 403
    assert not plane["store"].one("SELECT * FROM api_platform_bindings WHERE approval_id=?", (row["id"],))


def test_api_key_rotation_keeps_approval_and_revoke_stops_lineage(plane):
    e = plane
    row = request_approval(e, "api_key", list(SCOPES))
    approve(e, row)
    credential = issue(e, row)
    assert call(e, credential["key"]).status_code == 200
    rotated = post(e["admin"], f"{BASE}/credentials/api_key/{credential['credential']['id']}/rotate", {"reason": REASON, "grace_seconds": 0})
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()
    assert new["credential"]["lineage"] == credential["credential"]["lineage"]
    assert call(e, credential["key"]).status_code == 401
    assert call(e, new["key"]).status_code == 200
    assert post(e["owner"], f"{OWNER}/{row['id']}/revoke", {"reason": REASON}).status_code == 200
    assert call(e, new["key"]).status_code in (401, 403)


def test_historical_credentials_not_reclassified_or_rotated_for_secret(plane):
    e = plane
    token, key = Auth(e["store"]).create_key("fixture historical", e["ws"], ("read",))
    assert call(e, token).status_code == 200
    reauth(e["admin"])
    assert post(e["admin"], f"{BASE}/credentials/api_key/{key['id']}/rotate", {"reason": REASON}).status_code == 403
    assert e["store"].one("SELECT COUNT(*) AS n FROM api_platform_bindings")["n"] == 0


@pytest.mark.parametrize("path", ["http://127.0.0.1:8770/api/v1/projects", "file:///etc/passwd", "/etc/passwd",
    "/api/v1/../auth/me", "/api/v1/artifacts/fixture/content", "/api/v1/projects?token=secret", "/api/admin/users"])
def test_debugger_refuses_arbitrary_urls_paths_downloads(plane, path):
    e = plane
    reauth(e["admin"])
    out = post(e["admin"], BASE + "/request", {"reason": REASON, "credential": "fixture-token-not-valid", "path": path})
    assert out.status_code == 400, out.text


def test_debugger_uses_only_explicit_machine_and_object_scope(plane):
    e = plane
    reauth(e["admin"])
    token, _ = Auth(e["store"]).create_key("other workspace fixture", e["other_ws"], ("read",))
    out = post(e["admin"], BASE + "/request", {"reason": REASON, "credential": token,
        "path": f"/api/v1/projects/{e['project']['id']}"})
    assert out.status_code == 200 and out.json()["status"] == 404, out.text
    out = post(e["admin"], BASE + "/request", {"reason": REASON, "credential": token, "path": "/api/v1/projects"})
    assert out.json()["data"]["items"] == []
    assert token not in str(e["store"].all("SELECT * FROM audit_events"))
    assert token not in str(e["store"].all("SELECT * FROM api_platform_calls"))


def test_contract_pagination_and_real_call_statistics(plane):
    e = plane
    spec = e["admin"].get(BASE + "/openapi")
    assert spec.status_code == 200, spec.text
    assert "/api/v1/projects" in spec.json()["paths"]
    assert not any(path.startswith("/api/admin") for path in spec.json()["paths"])
    ops = e["admin"].get(BASE + "/operations", params={"q": "/projects", "limit": 2}).json()
    assert len(ops["items"]) == 2 and ops["next_offset"] == 2
    record_call(e["store"], {"kind": "api_key", "key_id": "fixture", "tenant": e["ws"]},
                "GET", "/api/v1/projects/{pid}", 404, 12, "fixture-request")
    stats = e["admin"].get(BASE + "/overview").json()["calls"]
    assert stats["calls"] >= 1 and stats["errors"] >= 1
    logged = e["admin"].get(BASE + "/calls", params={"errors_only": True, "limit": 1}).json()
    assert logged["items"][0]["status"] == 404


def test_real_gateway_populates_call_log_without_content(plane):
    e = plane
    token, key = Auth(e["store"]).create_key("call metadata fixture", e["ws"], ("read",))
    result = call(e, token, f"/api/v1/projects/{e['project']['id']}")
    assert result.status_code == 200
    row = e["store"].one("SELECT * FROM api_platform_calls WHERE request_id=?", (result.headers["x-request-id"],))
    assert row and row["route"] == "/api/v1/projects/{pid}"
    assert row["credential_id"] == key["id"] and row["status"] == 200
    assert token not in str(row) and e["project"]["name"] not in str(row)
    assert row["duration_ms"] >= 0


def test_bad_scope_resource_empty_intersection_and_rejection(plane):
    e = plane
    reauth(e["admin"])
    data = {"workspace_id": e["ws"], "name": "Invalid fixture", "credential_kind": "service_account",
            "scopes": [SCOPE_READ], "resources": ["https://other-api.example.test"], "reason": REASON}
    assert post(e["admin"], BASE + "/approvals", data).status_code == 400
    row = request_approval(e, scopes=[SCOPE_READ])
    reauth(e["owner"])
    assert post(e["owner"], f"{OWNER}/{row['id']}/approve", {"reason": REASON,
        "scopes": [SCOPE_WRITE], "resources": row["requested_resources"]}).status_code == 400
    assert post(e["owner"], f"{OWNER}/{row['id']}/reject", {"reason": REASON}).status_code == 200
    assert post(e["admin"], f"{BASE}/approvals/{row['id']}/issue", {"reason": REASON}).status_code == 403


def test_bound_credentials_listing_reports_lost_approval(plane):
    e = plane
    row = request_approval(e)
    approve(e, row)
    issued = issue(e, row)
    e["store"].execute("UPDATE api_platform_approvals SET expires_at=? WHERE id=?", (now() - 1, row["id"]))
    listed = e["admin"].get(BASE + "/credentials").json()["items"]
    item = next(x for x in listed if x["id"] == issued["client_id"])
    assert item["status"] == "approval_inactive" and item["approval_error"] == "approval_inactive"


def test_service_rotation_disable_enable_and_token_revocation(plane):
    e = plane
    row = request_approval(e)
    approve(e, row)
    credential = issue(e, row)
    old_access = service_token(e, credential).json()["access_token"]
    path = f"{BASE}/credentials/service_account/{credential['client_id']}"
    rotated = post(e["admin"], path + "/rotate", {"reason": REASON, "grace_seconds": 0})
    assert rotated.status_code == 200, rotated.text
    assert "secret_hash" not in rotated.text
    assert service_token(e, credential).status_code == 401
    assert call(e, old_access).status_code == 401
    credential["client_secret"] = rotated.json()["client_secret"]
    access = service_token(e, credential).json()["access_token"]
    assert call(e, access).status_code == 200
    assert post(e["admin"], path + "/revoke-tokens", {"reason": REASON}).status_code == 200
    assert call(e, access).status_code == 401
    assert service_token(e, credential).status_code == 200
    assert post(e["admin"], path + "/disable", {"reason": REASON}).status_code == 200
    assert service_token(e, credential).status_code == 401
    assert post(e["admin"], path + "/enable", {"reason": REASON}).status_code == 200
    assert service_token(e, credential).status_code == 200


def test_webhook_real_subscription_rotation_pause_retry_and_scope(plane):
    e = plane
    reauth(e["admin"])
    token, _ = Auth(e["store"]).create_key("webhook fixture", e["ws"], ("admin",))
    def request(method, path, body=None, credential=token):
        out = post(e["admin"], BASE + "/request", {"reason": REASON, "credential": credential,
            "method": method, "path": path, "body": body})
        assert out.status_code == 200, out.text
        return out.json()

    # Numeric public destination: URL validation only. Workers stay stopped and no HTTP
    # dispatch/ping is performed; this test never sends data to an external receiver.
    created = request("POST", "/api/v1/webhooks/subscriptions", {"url": "https://1.1.1.1/isolated-fixture", "events": ["job.completed"]})
    assert created["status"] == 201, created
    sub = created["data"]
    sid, secret = sub["id"], sub["secret"]
    path = "/api/v1/webhooks/subscriptions/" + sid
    listed = request("GET", "/api/v1/webhooks/subscriptions")
    assert listed["data"]["items"][0]["id"] == sid and secret not in str(listed)
    version = listed["data"]["items"][0]["version"]
    assert request("PATCH", path, {"version": version + 1, "description": "fixture"})["status"] == 409
    assert request("PATCH", path, {"version": version, "description": "fixture"})["status"] == 200
    assert request("POST", path + "/pause")["data"]["status"] == "paused"
    assert request("POST", path + "/resume")["data"]["status"] == "active"
    rotated = request("POST", path + "/rotate-secret", {"overlap_s": 0})
    assert rotated["data"]["secret"] != secret
    other, _ = Auth(e["store"]).create_key("other webhook fixture", e["other_ws"], ("admin",))
    assert request("GET", path, credential=other)["status"] == 404
    read, _ = Auth(e["store"]).create_key("read-only webhook fixture", e["ws"], ("read",))
    assert request("GET", path, credential=read)["status"] == 403
    # A documented fixture delivery, no network call. Retrying creates a real queued row.
    e["store"].insert("webhook_deliveries", {"id": "wh_fixture", "event_id": "ev_fixture", "endpoint": "fixture",
        "status": "failed", "attempts": 1, "created_at": now(), "subscription_id": sid,
        "tenant": e["ws"], "event_type": "webhook.ping"})
    assert request("GET", path + "/deliveries")["data"]["items"][0]["id"] == "wh_fixture"
    retried = request("POST", path + "/deliveries/wh_fixture/redeliver")
    assert retried["status"] == 202 and retried["data"]["redelivery_of"] == "wh_fixture"
    assert request("DELETE", path)["status"] == 200
    assert request("GET", path)["status"] == 404
    assert secret not in str(e["store"].all("SELECT * FROM audit_events"))


def test_unconsumed_revocation_survives_restore_journal(plane):
    row = request_approval(plane)
    approve(plane, row)
    assert post(plane["owner"], f"{OWNER}/{row['id']}/revoke", {"reason": REASON}).status_code == 200
    plane["store"].execute("UPDATE api_platform_approvals SET status='approved',revoked_at=NULL WHERE id=?", (row["id"],))
    journal.apply(plane["store"], [entry for entry, _ in journal.validated_lines(journal.path(plane["store"].root))])
    assert Approvals(plane["store"]).get(row["id"])["status"] == "revoked"


def test_accounts_private_adapter_does_not_mount_a_public_data_surface(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    data = tmp_path / "account-fixture"
    api = create_app(str(data), role="accounts", start_jobs=False)
    user = Identity(Store(data)).create_user("isolated-admin@example.test", PW, status="active", platform_role="admin")
    c = TestClient(api)
    assert post(c, "/api/auth/login", {"email": user["email"], "password": PW}).status_code == 200
    assert c.get(BASE + "/overview").json()["adapter_available"] is True
    assert c.get(BASE + "/openapi").status_code == 200
    assert c.get("/api/v1/projects").status_code == 404
