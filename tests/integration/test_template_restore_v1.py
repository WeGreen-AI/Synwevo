"""Template usage and version restore over the machine API: a service account in its workspace
reads who follows the template and restores an earlier version as a new head; a stale base is
refused; the answer is the same shape as GET /templates/{tid}."""
from __future__ import annotations

import io
import importlib

import pytest
from fastapi.testclient import TestClient

from synwevo_core import oidc
from synwevo_core.apps import Apps, bootstrap_docs_app
from synwevo_core.identity import Identity
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


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
    store = Store(tmp_path / "d"); ident = Identity(store); bootstrap_docs_app(store, ident)
    user = ident.create_user("ann@example.com", PW, status="active")
    ws = ident.workspaces_for(user["id"])[0]["id"]
    Apps(store, ident).enable_for_workspace(ws, "docweave")
    _, secret = oidc.Clients(store).register(client_id="nightly-batch", app_id="docweave", name="夜间批处理", redirect_uris=[], confidential=True, kind="service",
                                             workspace_id=ws, api_scopes="docweave.read docweave.write", audiences=["docweave-api"])
    c = TestClient(api)
    r = c.post("/oidc/token", data={"grant_type": "client_credentials", "client_id": "nightly-batch", "client_secret": secret})
    assert r.status_code == 200, r.text
    return {"api": api, "token": r.json()["access_token"]}


def call(env, method, path, **kw):
    return TestClient(env["api"]).request(method, path, headers={"Authorization": f"Bearer {env['token']}"}, **kw)


def test_v1_usage_and_restore(env):
    r = call(env, "POST", "/api/v1/templates", files={"file": ("模板.docx", _docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    fields = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "客户名称", "find": "示例客户名称"}]}
    assert call(env, "POST", f"/api/v1/templates/{tid}/fields", json=fields).status_code in (200, 201)          # v2
    fields2 = {"schema": "docweave.template-fields", "version": 1, "fields": [{"kind": "text", "name": "问候", "find": "您好"}]}
    assert call(env, "POST", f"/api/v1/templates/{tid}/fields", json=fields2).status_code in (200, 201)         # v3
    pid = call(env, "POST", "/api/v1/projects", json={"name": "跟随"}).json()["id"]
    assert call(env, "PUT", f"/api/v1/projects/{pid}/template", json={"template_id": tid}).status_code == 200
    use = call(env, "GET", f"/api/v1/templates/{tid}/usage").json()
    assert use["following"] == 1 and use["pinned"] == 0 and use["projects"][0]["id"] == pid
    assert call(env, "POST", f"/api/v1/templates/{tid}/restore", json={"version": 2, "base_head": 2}).json()["error"]["code"] == "stale_template"
    assert call(env, "POST", f"/api/v1/templates/{tid}/restore", json={"version": "2"}).status_code == 400
    r = call(env, "POST", f"/api/v1/templates/{tid}/restore", json={"version": 2, "base_head": 3})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["head"] == 4 and out["version"] == 4 and [f["name"] for f in out["fields"]] == ["客户名称"] and [v["version_no"] for v in out["versions"]] == [1, 2, 3, 4]
    assert call(env, "GET", f"/api/v1/templates/{tid}").json()["head"] == 4
