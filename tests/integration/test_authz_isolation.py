"""Cross-workspace isolation, driven through the real HTTP surface.

Every row of docs/security/permissions.md §5.1 is a case here. That list was not produced by
reading the code — it came from running the app with two tenants and watching eleven endpoints
return another workspace's document content — so these tests reproduce the attack rather than
assert the fix in the abstract.

The shape throughout: build something real in workspace A, then ask for it as workspace B using
the identifiers A was handed. Every one must refuse, and A must still be able to read its own.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from platform_core.context import as_tenant
from platform_core.identity import Identity
from platform_core.sessions import CSRF_COOKIE, CSRF_HEADER
from platform_core.store import Store


def _xlsx(name: str = "机密") -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "客户"
    ws.append(["客户名称", "金额"])
    ws.append([f"{name}客户甲", 12345.67])
    ws.append([f"{name}客户乙", 999.00])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PW = "a-sufficiently-long-passphrase"


@pytest.fixture()
def two(tmp_path, monkeypatch):
    """Two real accounts, each in their own workspace, exactly as the product creates them.

    Driven through session auth rather than by setting the tenant around the call: the gate
    derives the workspace from the session, so a test that forced a ContextVar would be testing
    something the server never does — and ContextVars do not cross TestClient's portal thread
    anyway, so it would silently have tested nothing.
    """
    monkeypatch.setenv("DOCWEAVE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("DOCWEAVE_UI_AUTH", "session")
    monkeypatch.setenv("DOCWEAVE_COOKIE_SECURE", "0")
    import importlib
    import platform_server.app as app_mod
    importlib.reload(app_mod)
    api = app_mod.create_app(str(tmp_path / "d"))   # job routes need the job service
    store = Store(tmp_path / "d")
    app_, ident = api.state.app, Identity(store)

    ident.create_user("a@example.com", PW, status="active")
    ident.create_user("b@example.com", PW, status="active")
    ca, cb = TestClient(api), TestClient(api)
    ca.post("/api/auth/login", json={"email": "a@example.com", "password": PW})
    cb.post("/api/auth/login", json={"email": "b@example.com", "password": PW})
    ws_a = ca.get("/api/auth/me").json()["workspaces"][0]["id"]

    # A's real project and a really parsed source file, created inside A's workspace.
    with as_tenant(ws_a):
        pid = app_.create_project("A 的项目")["id"]
        sid = app_.add_source(pid, "机密名单.xlsx", _xlsx())["id"]
        app_._parse_source(sid)      # synchronous parse; the API normally does this on a thread
        src = app_.get_source(sid, raw=True)
    return {"ca": ca, "cb": cb, "store": store, "app": app_, "ws_a": ws_a, "pid": pid, "sid": sid,
            "blob": src["blob"], "parsed_blob": src.get("parsed_blob")}


def _csrf(c):
    return {CSRF_HEADER: c.cookies.get(CSRF_COOKIE)}


def as_b(c_or_two, method: str, path: str, **kw):
    c = c_or_two["cb"]
    if method in ("put", "post", "delete", "patch"):
        kw.setdefault("headers", {}).update(_csrf(c))
    return getattr(c, method)(path, **kw)


def as_a(c_or_two, method: str, path: str, **kw):
    c = c_or_two["ca"]
    if method in ("put", "post", "delete", "patch"):
        kw.setdefault("headers", {}).update(_csrf(c))
    return getattr(c, method)(path, **kw)


# ---------------------------------------------------------------- §5.1, one row at a time
def test_source_listing_does_not_cross_workspaces(two):
    pid = two["pid"]
    assert as_a(two, "get", f"/api/projects/{pid}/sources").status_code == 200
    assert as_b(two, "get", f"/api/projects/{pid}/sources").status_code == 404


@pytest.mark.parametrize("suffix", [
    "/view", "/download", "/search?q=客户", "/page/0.png", "/words/0", "/blocks",
])
def test_every_source_read_path_refuses_another_workspace(two, suffix):
    sid = two["sid"]
    r = as_b(two, "get", f"/api/sources/{sid}{suffix}")
    assert r.status_code == 404, f"{suffix} 泄漏了：{r.status_code} {r.text[:200]}"
    assert "机密" not in r.text


def test_source_writes_refuse_another_workspace(two):
    sid = two["sid"]
    assert as_b(two, "delete", f"/api/sources/{sid}").status_code == 404
    assert as_b(two, "put", f"/api/sources/{sid}/options", json={"sheet": "客户"}).status_code == 404
    assert as_b(two, "post", f"/api/sources/{sid}/representative").status_code == 404
    # and A's file is untouched by the attempts
    assert as_a(two, "get", f"/api/sources/{sid}/view").status_code == 200


def test_raw_blob_routes_refuse_another_workspace(two):
    """The structural one. Blob ids are published in ordinary responses, so they are not secret."""
    blob = two["blob"]
    for path in (f"/api/preview-files/{blob}",
                 f"/api/preview-files/{blob}/view?fmt=xlsx",
                 f"/api/desensitized/{blob}"):
        r = as_b(two, "get", path)
        assert r.status_code == 404, f"{path} 泄漏了：{r.status_code}"
        assert "机密" not in r.text
    # the owner can still read it
    assert as_a(two, "get", f"/api/preview-files/{blob}").status_code == 200


def test_parsed_blob_is_also_protected(two):
    pb = two["parsed_blob"]
    if not pb:
        pytest.skip("该文件没有独立的解析产物")
    assert as_b(two, "get", f"/api/preview-files/{pb}").status_code == 404
    assert as_a(two, "get", f"/api/preview-files/{pb}").status_code == 200


def test_locate_cannot_run_your_mappings_over_someone_elses_document(two):
    """The worst of the eleven: it returned resolved field values, not just the file.

    Asserted at the service layer as well as over HTTP. Through HTTP alone an empty project in B
    refuses for its own reasons, so the test would pass without the fix — it has to be the
    pairing of ids that is rejected, which is what locate_all now checks.
    """
    from docweave.locators import locate_all
    from platform_core.errors import ServiceError
    app_, sid, pid, ws_a = two["app"], two["sid"], two["pid"], two["ws_a"]

    # Across workspaces get_source already refuses, so the interesting case is the one it cannot
    # see: two projects inside the SAME workspace. A project id the caller legitimately owns plus
    # a source id from a different project is still a pairing that must not resolve.
    with as_tenant(ws_a):
        other = app_.create_project("A 的另一个项目")["id"]
        with pytest.raises(ServiceError) as e:
            locate_all(app_, other, sid)            # own workspace, wrong project
        assert e.value.status == 404
    assert as_a(two, "get", f"/api/projects/{other}/locate?file={sid}").status_code == 404
    # the matching pair still works
    assert as_a(two, "get", f"/api/projects/{pid}/locate?file={sid}").status_code == 200
    # and B cannot use it at all
    assert as_b(two, "get", f"/api/projects/{pid}/locate?file={sid}").status_code == 404


def test_job_items_and_report_refuse_another_workspace(two):
    app_, pid = two["app"], two["pid"]
    # a job row in A's workspace, so the routes have something real to refuse B
    from platform_core.store import new_id, now
    jid = new_id("j")
    app_.store.execute(
        "INSERT INTO jobs (id,project_id,status,created_at,tenant) VALUES (?,?,'completed',?,'ws_a')",
        (jid, pid, now()))
    assert as_b(two, "get", f"/api/jobs/{jid}/items").status_code == 404
    assert as_b(two, "get", f"/api/jobs/{jid}/report.csv").status_code == 404


def test_project_and_template_reads_were_already_scoped_and_stay_so(two):
    pid = two["pid"]
    assert as_b(two, "get", f"/api/projects/{pid}").status_code == 404
    assert as_a(two, "get", f"/api/projects/{pid}").status_code == 200


# ---------------------------------------------------------------- the owner is unaffected
def test_the_owning_workspace_can_still_do_everything(two):
    pid, sid, blob = two["pid"], two["sid"], two["blob"]
    assert as_a(two, "get", f"/api/projects/{pid}/sources").json()[0]["id"] == sid
    view = as_a(two, "get", f"/api/sources/{sid}/view")
    assert view.status_code == 200 and "机密客户甲" in view.text
    assert as_a(two, "get", f"/api/sources/{sid}/search?q=机密").json()["total"] >= 1
    assert as_a(two, "get", f"/api/sources/{sid}/download").status_code == 200
    assert as_a(two, "get", f"/api/preview-files/{blob}").status_code == 200


def test_reorder_refuses_another_workspace(two):
    pid, sid = two["pid"], two["sid"]
    assert as_b(two, "put", f"/api/projects/{pid}/sources/order", json={"ids": [sid]}).status_code == 404


# ---------------------------------------------------------------- blob grants
def test_a_derived_preview_blob_is_readable_only_by_the_workspace_that_made_it(two):
    """Previews have no owning row, so they are covered by an explicit grant instead."""
    from docweave import authz
    store, blob = two["store"], "a" * 64
    with as_tenant("ws_a"):
        authz.grant_blob(store, blob, "preview")
        assert authz.blob_readable(store, blob) is True
    with as_tenant("ws_b"):
        assert authz.blob_readable(store, blob) is False


def test_identical_content_in_two_workspaces_needs_two_grants(two):
    """Content-addressed storage deduplicates, so one blob legitimately serves both — and each
    side must hold its own grant rather than inheriting the other's."""
    from docweave import authz
    store, blob = two["store"], "b" * 64
    with as_tenant("ws_a"):
        authz.grant_blob(store, blob, "preview")
    with as_tenant("ws_b"):
        assert authz.blob_readable(store, blob) is False
        authz.grant_blob(store, blob, "preview")
        assert authz.blob_readable(store, blob) is True
    with as_tenant("ws_a"):
        assert authz.blob_readable(store, blob) is True, "授予 B 不应影响 A"


def test_an_unknown_blob_is_refused_everywhere(two):
    from docweave import authz
    with as_tenant("ws_a"):
        assert authz.blob_readable(two["store"], "c" * 64) is False
        assert authz.blob_readable(two["store"], "") is False
