"""Workspace roles on Synwevo Docs' browser API: viewers read, editors write, exporting is its own
permission (docs/security/permissions.md §4.2).

Before this, the gate resolved the session and checked that a membership row existed — and
nothing else. A viewer could create, edit and delete projects, templates and mappings, run
generation jobs, and download every file in the workspace. These tests drive the real composed
application with real accounts in one workspace, one per role, and ask each of them to do the
same things.
"""
from __future__ import annotations

import io

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from synwevo_core.context import as_tenant
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"


def _xlsx() -> bytes:
    """Test fixture: a tiny workbook, generated here."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户"
    ws.append(["客户名称", "金额"])
    ws.append(["夹具客户甲", 1])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csrf(c: TestClient) -> dict:
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


@pytest.fixture()
def team(tmp_path, monkeypatch):
    """One workspace with an owner, an editor, a viewer, and a viewer allowed to export."""
    d = tmp_path / "d"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(d), role="all", start_jobs=False)
    store = Store(d)
    ident = Identity(store)

    owner = ident.create_user("owner@example.com", PW, status="active")
    ws = ident.workspaces_for(owner["id"])[0]["id"]
    people = {"owner": owner}
    for name, role, export in (("editor", "editor", None), ("viewer", "viewer", None),
                               ("exporter", "viewer", True)):
        u = ident.create_user(f"{name}@example.com", PW, status="active")
        ident.add_member(ws, u["id"], role, can_export=export)
        people[name] = u

    app_ = api.state.app
    with as_tenant(ws):
        pid = app_.create_project("团队项目")["id"]
        sid = app_.add_source(pid, "名单.xlsx", _xlsx())["id"]
        blob = app_.get_source(sid, raw=True)["blob"]

    clients = {}
    for name in people:
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{name}@example.com", "password": PW}).status_code == 200
        r = c.post("/api/auth/workspace", json={"workspace_id": ws}, headers=_csrf(c))
        assert r.status_code == 200, r.text
        clients[name] = c
    return {"api": api, "store": store, "ident": ident, "ws": ws, "pid": pid, "sid": sid,
            "blob": blob, "c": clients, "people": people}


def _post(c, path, **kw):
    kw.setdefault("headers", {}).update(_csrf(c))
    return c.post(path, **kw)


def _send(c, method, path, **kw):
    if method != "get":
        kw.setdefault("headers", {}).update(_csrf(c))
    return getattr(c, method)(path, **kw)


# ---------------------------------------------------------------- viewers read
def test_a_viewer_can_read_everything_on_the_platform(team):
    v, pid, sid = team["c"]["viewer"], team["pid"], team["sid"]
    assert [p["id"] for p in v.get("/api/projects").json()] == [pid]
    assert v.get(f"/api/projects/{pid}").status_code == 200
    assert v.get(f"/api/projects/{pid}/sources").status_code == 200
    view = v.get(f"/api/sources/{sid}/view")
    assert view.status_code == 200 and "夹具客户甲" in view.text
    # A computed preview is reading, even though it is a POST.
    assert _post(v, f"/api/projects/{pid}/rule-preview", json={"mode": "per_file"}).status_code == 200


WRITES = [
    ("post", "/api/projects", {"json": {"name": "查看者新建"}}),
    ("patch", "/api/projects/{pid}", {"json": {"name": "查看者改名"}}),
    ("delete", "/api/projects/{pid}", {}),
    ("put", "/api/projects/{pid}/mappings", {"json": {"mappings": []}}),
    ("post", "/api/projects/{pid}/jobs", {"json": {"formats": ["docx"]}}),
    ("delete", "/api/sources/{sid}", {}),
    ("put", "/api/sources/{sid}/options", {"json": {"sheet": "客户"}}),
    ("post", "/api/templates/builtin", {"json": {"name": "x", "kind": "builtin_doc"}}),
    ("post", "/api/projects/{pid}/desensitize", {"json": {"file_ids": []}}),
]


@pytest.mark.parametrize("method,path,kw", WRITES, ids=[f"{m} {p}" for m, p, _ in WRITES])
def test_a_viewer_cannot_write_or_run_anything(team, method, path, kw):
    v = team["c"]["viewer"]
    r = _send(v, method, path.format(pid=team["pid"], sid=team["sid"]), **kw)
    assert r.status_code == 403, f"{method} {path} → {r.status_code} {r.text[:200]}"
    assert r.json()["error"] == "read_only_role"


def test_a_refused_write_changes_nothing(team):
    v, pid, store = team["c"]["viewer"], team["pid"], team["store"]
    _send(v, "patch", f"/api/projects/{pid}", json={"name": "改掉"})
    _send(v, "delete", f"/api/projects/{pid}")
    _post(v, "/api/projects", json={"name": "多出来的"})
    rows = store.all("SELECT name, deleted FROM projects WHERE tenant=?", (team["ws"],))
    assert rows == [{"name": "团队项目", "deleted": 0}]


def test_a_viewer_can_upload_nothing(team):
    v = team["c"]["viewer"]
    r = _post(v, f"/api/projects/{team['pid']}/sources",
              files={"files": ("x.xlsx", _xlsx())})
    assert r.status_code == 403 and r.json()["error"] == "read_only_role"


# ---------------------------------------------------------------- exporting is separate
EXPORTS = ["/api/sources/{sid}/download", "/api/preview-files/{blob}", "/api/desensitized/{blob}",
           "/api/jobs/j_any/zip", "/api/jobs/j_any/report.csv", "/api/artifacts/a_any",
           "/api/templates/t_any/download", "/api/regression-cases/c_any/download"]


@pytest.mark.parametrize("path", EXPORTS)
def test_a_viewer_without_export_cannot_take_files_off_the_platform(team, path):
    r = team["c"]["viewer"].get(path.format(sid=team["sid"], blob=team["blob"]))
    assert r.status_code == 403 and r.json()["error"] == "export_not_allowed", f"{path} → {r.status_code}"


def test_a_viewer_with_export_permission_can_download(team):
    c = team["c"]["exporter"]
    r = c.get(f"/api/sources/{team['sid']}/download")
    assert r.status_code == 200 and r.content == team["api"].state.app.store.get_blob(team["blob"])
    # ...but exporting is not writing.
    assert _post(c, "/api/projects", json={"name": "x"}).status_code == 403


@pytest.mark.parametrize("who", ["owner", "editor"])
def test_editors_and_owners_write_and_export(team, who):
    c = team["c"][who]
    assert _post(c, "/api/projects", json={"name": f"{who} 的项目"}).status_code == 200
    assert c.get(f"/api/sources/{team['sid']}/download").status_code == 200


def test_a_role_change_takes_effect_on_the_next_request(team):
    ident, ws, c = team["ident"], team["ws"], team["c"]["editor"]
    assert _post(c, "/api/projects", json={"name": "降级前"}).status_code == 200
    ident.change_role(ws, team["people"]["editor"]["id"], "viewer")
    r = _post(c, "/api/projects", json={"name": "降级后"})
    assert r.status_code == 403 and r.json()["error"] == "read_only_role"


def test_account_routes_do_not_depend_on_the_workspace_role(team):
    v = team["c"]["viewer"]
    assert v.get("/api/auth/me").status_code == 200
    assert v.post("/api/auth/logout", headers=_csrf(v)).status_code == 200


# ---------------------------------------------------------------- the table covers every route
ACCOUNT_LEVEL = ("/api/auth/", "/api/admin/", "/api/portal/", "/api/v1")


def test_every_browser_route_is_classified(team):
    """A route registered without an entry would fall back to "safe method = any member", which
    is exactly how a new download route would quietly become readable by viewers. So the table
    must name every one of them."""
    from synwevo_docs import authz
    missing = []
    for route in team["api"].routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if route.path.startswith(ACCOUNT_LEVEL) or route.path == "/api/health":
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if not authz.classified(method, route.path):
                missing.append(f"{method} {route.path}")
    assert not missing, "这些浏览器路由没有在 authz.ROUTE_POLICY 里分类：\n  " + "\n  ".join(sorted(missing))


def test_an_unclassified_route_fails_closed_for_writes():
    from synwevo_docs import authz
    assert authz.route_policy("POST", "/api/something-new") == "write"
    assert authz.route_policy("DELETE", "/api/projects/p1/whatever") == "write"
    assert authz.route_policy("GET", "/api/templates/upload") is None      # literal beats {tid}
    assert authz.route_policy("POST", "/api/templates/upload") == "write"
    assert authz.route_policy("HEAD", "/api/jobs/j1/zip") == "export"


# ---------------------------------------------------------------- legacy browser modes
def test_the_shared_password_mode_is_unaffected(tmp_path, monkeypatch):
    """`basic` has no per-person roles: the operator's password is the whole authority, as before."""
    import base64
    from synwevo_core.auth import Auth
    d = tmp_path / "b"
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(d))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "basic")
    Auth(Store(d)).add_ui_user("probe", "probe-pass-123")
    from synwevo_server.app import create_app
    c = TestClient(create_app(str(d), role="all", start_jobs=False))
    h = {"Authorization": "Basic " + base64.b64encode(b"probe:probe-pass-123").decode()}
    r = c.post("/api/projects", json={"name": "口令模式"}, headers=h)
    assert r.status_code == 200, r.text
    assert c.get(f"/api/projects/{r.json()['id']}", headers=h).status_code == 200
