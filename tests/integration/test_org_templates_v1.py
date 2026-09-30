"""Organization-shared templates over the machine API (`/api/v1`): a delegated token acts as the
person it represents (organization membership, workspace role), a service account has no person
behind it and is refused by name, publishing and using are idempotent creates, and lists page."""
from __future__ import annotations

import base64
import hashlib
import importlib
import io
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.organizations import Organizations
from synwevo_core.store import Store
from services.identity.tests.oauth_consent_helper import through_consent

PW = "a-sufficiently-long-passphrase"
REDIRECT = "http://docweave.localhost:8765/auth/callback"


def _docx() -> bytes:
    import docx
    d = docx.Document(); d.add_paragraph("尊敬的示例客户名称：您好。")
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


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
    bootstrap_docs_app(store, ident)
    ann = ident.create_user("ann@example.com", PW, status="active", display_name="Ann Editor", with_personal_workspace=True)
    bob = ident.create_user("bob@example.com", PW, status="active", display_name="Bob Editor", with_personal_workspace=True)
    orgs = Organizations(store, ident)
    created = orgs.create(ann["id"], "Machine Org", "Team A", "fixture-idem-v1-org-0001")
    oid, wa = created["organization"]["id"], created["workspace"]["id"]
    wb = orgs.create_workspace(oid, ann["id"], "Team B")["id"]
    store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)", (oid, bob["id"], ann["id"]))
    store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'editor',1,1,1,?)", (wb, bob["id"], ann["id"]))
    clients = oidc.Clients(store)
    clients.register(client_id="docweave-web", app_id="docweave", name="Synwevo Docs", redirect_uris=[REDIRECT],
                     scopes="openid profile email docweave.read docweave.write", audiences=["docweave-api"])
    _, secret = clients.register(client_id="nightly-batch", app_id="docweave", name="夜间批处理", redirect_uris=[], confidential=True, kind="service",
                                 workspace_id=wa, api_scopes="docweave.read docweave.write", audiences=["docweave-api"])
    return {"api": api, "store": store, "ident": ident, "ann": ann, "bob": bob, "oid": oid, "wa": wa, "wb": wb, "secret": secret,
            "apps": Apps(store, ident)}


def delegated(env, email: str, workspace_id: str) -> str:
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    v = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": "docweave-web", "redirect_uri": REDIRECT, "response_type": "code", "scope": "openid docweave.read docweave.write",
         "state": "s", "code_challenge": ch, "code_challenge_method": "S256", "workspace_id": workspace_id}
    r = through_consent(c, c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False))
    assert r.status_code in (302, 303), r.text
    code = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))["code"]
    out = c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": "docweave-web", "code_verifier": v})
    assert out.status_code == 200, out.text
    return out.json()["access_token"]


def service(env) -> str:
    r = TestClient(env["api"]).post("/oidc/token", data={"grant_type": "client_credentials", "client_id": "nightly-batch", "client_secret": env["secret"]})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def call(env, token: str, method: str, path: str, **kw):
    return TestClient(env["api"]).request(method, path, headers={"Authorization": f"Bearer {token}", **kw.pop("headers", {})}, **kw)


def test_v1_shared_templates_follow_the_person_behind_the_token(env):
    ann, bob, svc = delegated(env, "ann@example.com", env["wa"]), delegated(env, "bob@example.com", env["wb"]), service(env)
    # ann (Team A) uploads a template through the machine API and publishes it, idempotently
    r = call(env, ann, "POST", "/api/v1/templates", files={"file": ("模板.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    r = call(env, ann, "POST", f"/api/v1/templates/{tid}/publish", json={"description": "机器发布"}, headers={"Idempotency-Key": "pub-1"})
    assert r.status_code == 201, r.text
    pub = r.json()
    assert pub["release_no"] == 1 and pub["version_no"] == 1 and pub["description"] == "机器发布" and pub["can_manage"] is True and "working_blob" not in pub
    again = call(env, ann, "POST", f"/api/v1/templates/{tid}/publish", json={"description": "机器发布"}, headers={"Idempotency-Key": "pub-1"})
    assert again.status_code == 201 and again.json()["id"] == pub["id"], "a replayed key returns the same publication"
    assert call(env, ann, "POST", f"/api/v1/templates/{tid}/publish", json={}).status_code == 409
    # a service account has no organization membership: refused by name, with read and write alike
    r = call(env, svc, "GET", "/api/v1/organization-templates")
    assert r.status_code == 403 and r.json()["error"]["code"] == "delegated_required", r.text
    assert call(env, svc, "POST", f"/api/v1/organization-templates/{pub['id']}/use", json={}).json()["error"]["code"] == "delegated_required"
    # bob (Team B) lists and pages, sees no maintenance rights, and uses it into a Team B project
    page = call(env, bob, "GET", "/api/v1/organization-templates?limit=1").json()
    assert page["organization"]["name"] == "Machine Org" and [x["id"] for x in page["items"]] == [pub["id"]] and page["items"][0]["can_manage"] is False
    assert page["total"] == 1 and page["next_cursor"] is None
    detail = call(env, bob, "GET", f"/api/v1/organization-templates/{pub['id']}").json()
    assert [x["release_no"] for x in detail["releases"]] == [1] and detail["source_workspace"]["name"] == "Team A"
    assert call(env, bob, "POST", f"/api/v1/organization-templates/{pub['id']}/releases", json={}).status_code == 403
    pid = call(env, bob, "POST", "/api/v1/projects", json={"name": "B"}).json()["id"]
    r = call(env, bob, "POST", f"/api/v1/organization-templates/{pub['id']}/use", json={"project_id": pid}, headers={"Idempotency-Key": "use-1"})
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["origin"]["publication_id"] == pub["id"] and copy["project_id"] == pid and copy["head"] == 1
    assert call(env, bob, "POST", f"/api/v1/organization-templates/{pub['id']}/use", json={"project_id": pid}, headers={"Idempotency-Key": "use-1"}).json()["id"] == copy["id"]
    assert call(env, bob, "GET", f"/api/v1/projects/{pid}").json()["template"]["id"] == copy["id"]
    assert call(env, bob, "GET", f"/api/v1/templates/{copy['id']}").status_code == 200
    assert call(env, ann, "GET", f"/api/v1/templates/{copy['id']}").status_code == 404, "the copy belongs to Team B"
    # a personal workspace has no organization
    r = call(env, delegated(env, "bob@example.com", env["ident"].ensure_personal_workspace(env["bob"]["id"])["id"]), "GET", "/api/v1/organization-templates")
    assert r.status_code == 200 and r.json()["organization"] is None and r.json()["items"] == []
    # release: nothing new → 409; add a field through the machine API → release 2; deprecate → hidden, use refused; restore
    assert call(env, ann, "POST", f"/api/v1/organization-templates/{pub['id']}/releases", json={}).json()["error"]["code"] == "unchanged"
    fields = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "客户名称", "find": "示例客户名称"}]}
    r = call(env, ann, "POST", f"/api/v1/templates/{tid}/fields", json=fields)
    assert r.status_code in (200, 201), r.text
    rel = call(env, ann, "POST", f"/api/v1/organization-templates/{pub['id']}/releases", json={}).json()
    assert rel["release_no"] == 2 and rel["released"] is True and rel["slot_count"] == 1
    assert call(env, ann, "POST", f"/api/v1/organization-templates/{pub['id']}/deprecate", json={"note": "改版"}).json()["deprecated"] is True
    assert call(env, bob, "GET", "/api/v1/organization-templates").json()["items"] == []
    assert call(env, bob, "GET", "/api/v1/organization-templates?include_deprecated=true").json()["items"][0]["deprecation_note"] == "改版"
    assert call(env, bob, "POST", f"/api/v1/organization-templates/{pub['id']}/use", json={}).json()["error"]["code"] == "deprecated"
    assert call(env, ann, "POST", f"/api/v1/organization-templates/{pub['id']}/deprecate", json={"deprecated": False}).json()["deprecated"] is False
    assert call(env, bob, "GET", f"/api/v1/templates/{copy['id']}").json()["version"] == 1, "the copy keeps release 1"


def test_shared_template_cursor_binds_workspace_and_filter(env):
    ann = delegated(env, "ann@example.com", env["wa"])
    bob = delegated(env, "bob@example.com", env["wb"])
    for i in range(2):
        uploaded = call(env, ann, "POST", "/api/v1/templates", files={"file": (f"fixture-{i}.docx", _docx())})
        assert uploaded.status_code == 201
        assert call(env, ann, "POST", f"/api/v1/templates/{uploaded.json()['id']}/publish", json={}).status_code == 201
    first = call(env, bob, "GET", "/api/v1/organization-templates?limit=1").json()
    cursor = first["next_cursor"]
    assert cursor
    tail = call(env, bob, "GET", "/api/v1/organization-templates", params={"cursor": cursor})
    assert tail.status_code == 200 and len(tail.json()["items"]) == 1
    assert tail.json()["items"][0]["id"] != first["items"][0]["id"]
    # Even two workspaces in the same organization restart their own view.
    for token, params in ((ann, {"cursor": cursor}), (bob, {"cursor": cursor, "include_deprecated": "true"})):
        response = call(env, token, "GET", "/api/v1/organization-templates", params=params)
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"
    personal = env["ident"].ensure_personal_workspace(env["bob"]["id"])["id"]
    response = call(env, delegated(env, "bob@example.com", personal), "GET", "/api/v1/organization-templates", params={"cursor": cursor})
    assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_cursor"
