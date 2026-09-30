"""The workspace directory API (API-5): an owner-approved service account reads its workspace's members
and invitations; it is its own resource, so neither Docs nor directory credentials cross over."""
from __future__ import annotations

from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.machine import SCOPE_READ
from synwevo_core.oidc import docs_resource, workspace_resource
from tests.integration.test_api_platform import (BASE, OWNER, REASON, approve, issue, plane, post, reauth,  # noqa: F401
                                                  request_approval)

D = "/api/workspace/v1"


def directory_credential(e, scopes=("workspace.read",)):
    row = request_approval(e, kind="service_account", scopes=list(scopes), resources=[workspace_resource()])
    approve(e, row)
    out = issue(e, row)
    return row, out


def token(e, cred, resource=None):
    r = TestClient(e["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"],
                                                       "client_secret": cred["client_secret"],
                                                       "resource": resource or workspace_resource()})
    assert r.status_code == 200, r.text
    return r.json()


def get(e, tok, path, **kw):
    return TestClient(e["api"]).get(path, headers={"Authorization": f"Bearer {tok}"}, **kw)


def test_an_approved_service_account_reads_its_workspace_directory(plane):
    e = plane
    e["ident"].add_member(e["ws"], e["users"]["stranger"]["id"], "editor")
    row, cred = directory_credential(e)
    tok = token(e, cred)
    assert tok["scope"] == "workspace.read"
    who = get(e, tok["access_token"], f"{D}/whoami")
    assert who.status_code == 200, who.text
    assert who.json()["workspace"]["id"] == e["ws"] and who.json()["scopes"] == ["workspace.read"]
    members = get(e, tok["access_token"], f"{D}/members").json()
    by_email = {m["email"]: m for m in members["items"]}
    assert by_email[e["users"]["owner"]["email"]]["role"] == "owner"
    assert by_email[e["users"]["stranger"]["email"]]["role"] == "editor"
    assert members["owner_count"] == 1 and set(members["items"][0]) == {"user_id", "email", "display_name", "role",
                                                                          "account_status", "joined_at"}
    editors = get(e, tok["access_token"], f"{D}/members?role=editor").json()
    assert [m["email"] for m in editors["items"]] == [e["users"]["stranger"]["email"]]
    assert get(e, tok["access_token"], f"{D}/invitations?state=pending").json()["total"] == 0
    r = get(e, tok["access_token"], f"{D}/members?role=superuser")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid"
    # The call record names the directory route template and the service account.
    calls = e["admin"].get(f"{BASE}/calls?credential_id={cred['client_id']}").json()["items"]
    assert {c["route"] for c in calls} >= {f"{D}/whoami", f"{D}/members"}
    assert "RateLimit" in who.headers


def test_directory_and_docs_credentials_do_not_cross(plane):
    e = plane
    _, cred = directory_credential(e)
    tok = token(e, cred)["access_token"]
    r = get(e, tok, "/api/v1/projects")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_audience"
    # A Docs service account cannot read the directory, even from the same workspace.
    drow = request_approval(e, kind="service_account", scopes=[SCOPE_READ])
    approve(e, drow)
    dcred = issue(e, drow)
    dtok = token(e, dcred, docs_resource())["access_token"]
    r = get(e, dtok, f"{D}/members")
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_audience"
    # Nor can a legacy access key, nor nothing at all.
    key, _ = Auth(e["store"]).create_key("legacy fixture", e["ws"], ("read", "write", "admin"))
    r = get(e, key, f"{D}/members")
    assert r.status_code == 403 and r.json()["error"]["code"] == "wrong_credential"
    assert TestClient(e["api"]).get(f"{D}/members").status_code == 401
    # A directory client asking for a Docs token is refused at the token endpoint.
    bad = TestClient(e["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"],
                                                         "client_secret": cred["client_secret"], "resource": docs_resource()})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_target"


def test_approval_rules_for_the_directory(plane):
    e = plane
    reauth(e["admin"])
    body = {"workspace_id": e["ws"], "name": "Directory fixture", "reason": REASON, "expires_in_days": 30}
    r = post(e["admin"], BASE + "/approvals", {**body, "credential_kind": "api_key", "scopes": ["workspace.read"],
                                               "resources": [workspace_resource()]})
    assert r.status_code == 400 and r.json()["error"] == "service_account_required"
    r = post(e["admin"], BASE + "/approvals", {**body, "credential_kind": "service_account", "scopes": ["docweave.read"],
                                               "resources": [workspace_resource()]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_field"
    r = post(e["admin"], BASE + "/approvals", {**body, "credential_kind": "service_account", "scopes": ["workspace.read"],
                                               "resources": [workspace_resource(), docs_resource()]})
    assert r.status_code == 400 and r.json()["error"] == "one_resource"
    overview = e["admin"].get(BASE + "/overview").json()
    assert set(overview["resources"][workspace_resource()]) == {"workspace.read", "workspace.members.write"}


def test_withdrawing_the_approval_or_freezing_the_workspace_stops_it(plane):
    e = plane
    row, cred = directory_credential(e)
    tok = token(e, cred)["access_token"]
    assert get(e, tok, f"{D}/members").status_code == 200
    reauth(e["owner"])
    assert post(e["owner"], f"{OWNER}/{row['id']}/revoke", {"reason": REASON}).status_code == 200
    assert get(e, tok, f"{D}/members").status_code in (401, 403)
    row2, cred2 = directory_credential(e)
    tok2 = token(e, cred2)["access_token"]
    e["ident"].set_workspace_frozen(e["ws"], True)
    # Freezing revokes the workspace's tokens (401); a fresh token cannot be had while frozen either.
    assert get(e, tok2, f"{D}/members").status_code in (401, 403)
    refused = TestClient(e["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": cred2["client_id"],
                                                             "client_secret": cred2["client_secret"], "resource": workspace_resource()})
    assert refused.status_code in (400, 403)
