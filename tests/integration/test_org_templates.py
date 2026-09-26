"""Organization-shared templates on the composed server: who may publish (editors of the source
workspace, in an organization workspace), what a release freezes, who can see and use it (members
of the organization, from any of its workspaces, never from a personal workspace or another
organization), that a copy made from a release is an ordinary template of the reader's workspace
which later releases and deprecation do not touch, and that maintenance (new release, deprecate,
restore) needs the maintainer's authority."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF, WS = "X-Synwevo-CSRF", "X-Synwevo-Workspace"


def _docx() -> bytes:
    import docx
    d = docx.Document(); d.add_paragraph("尊敬的示例客户名称：您好。")
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


def _walk(blocks):
    for b in blocks:
        if b["t"] == "p":
            yield b
        else:
            for row in b["rows"]:
                for cell in row["cells"]:
                    yield from _walk(cell["blocks"])


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                 "MAIL_FROM": "synwevo@example.test", "PUBLIC_URL": "http://testserver", "ROLE": "all"}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("ann", "Ann Editor"), ("bob", "Bob Viewer"), ("cid", "Cid Editor"), ("dan", "Dan Owner"), ("eve", "Eve Elsewhere")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name, with_personal_workspace=True)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    orgs = Organizations(ident.store, ident)
    created = orgs.create(users["dan"]["id"], "Template Org", "Team A", "fixture-idem-tpl-org-0001")
    oid, wa = created["organization"]["id"], created["workspace"]["id"]
    wb = orgs.create_workspace(oid, users["dan"]["id"], "Team B")["id"]
    other = orgs.create(users["eve"]["id"], "Other Org", "Other Team", "fixture-idem-tpl-org-0002")
    for key, wid, role in (("ann", wa, "editor"), ("bob", wa, "viewer"), ("cid", wb, "editor")):
        ident.store.execute("INSERT OR IGNORE INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (oid, users[key]["id"], users["dan"]["id"]))
        ident.store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,?,1,1,1,?)",
                            (wid, users[key]["id"], role, users["dan"]["id"]))
    clients = {}
    for key, wid in (("ann", wa), ("bob", wa), ("cid", wb), ("dan", wa), ("eve", other["workspace"]["id"])):
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW}).status_code == 200
        c.headers.update({CSRF: c.cookies.get("dw_csrf"), WS: wid})
        clients[key] = c
    try:
        yield {"api": api, "ident": ident, "c": clients, "u": users, "oid": oid, "wa": wa, "wb": wb,
               "personal": {k: ident.ensure_personal_workspace(u["id"])["id"] for k, u in users.items()}}
    finally:
        for c in clients.values():
            c.close()


def build(c) -> tuple[str, str]:
    pid = c.post("/api/projects", json={"name": "共享模板项目"}).json()["id"]
    t = c.post("/api/templates/upload", files={"file": ("模板.docx", _docx())}, data={"project_id": pid}).json()
    view = c.get(f"/api/templates/{t['id']}/view").json()
    block = next(b for part in view["doc"]["parts"] for b in _walk(part["blocks"]) if "示例客户名称" in b["text"])
    s = block["text"].index("示例客户名称")
    assert c.post(f"/api/templates/{t['id']}/slots", json={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 6, "name": "客户名称"}).status_code == 200
    return pid, t["id"]


def test_publish_use_release_and_deprecate(world):
    C, U, W = world["c"], world["u"], world
    ann, bob, cid, dan, eve = (C[k] for k in ("ann", "bob", "cid", "dan", "eve"))
    pid, tid = build(ann)
    # who may publish: a viewer is refused at the route gate; a personal workspace has no organization
    assert bob.post(f"/api/templates/{tid}/publish", json={}).status_code == 403
    ann.headers.update({WS: W["personal"]["ann"]})
    ppid = ann.post("/api/projects", json={"name": "个人"}).json()["id"]
    pt = ann.post("/api/templates/upload", files={"file": ("个人.docx", _docx())}, data={"project_id": ppid}).json()
    assert ann.post(f"/api/templates/{pt['id']}/publish", json={}).json()["error"] == "no_organization"
    assert ann.get("/api/organization-templates").json() == {"organization": None, "items": []}
    ann.headers.update({WS: W["wa"]})
    # publish freezes the head version (v2: upload + one field)
    r = ann.post(f"/api/templates/{tid}/publish", json={"description": "  客户函件标准模板 "})
    assert r.status_code == 201, r.text
    pub = r.json()
    assert pub["version_no"] == 2 and pub["release_no"] == 1 and pub["slot_count"] == 1 and pub["description"] == "客户函件标准模板"
    assert pub["published_by"]["name"] == "Ann Editor" and pub["source_workspace"]["name"] == "Team A" and pub["can_manage"] is True and pub["source_ahead"] is False
    assert ann.post(f"/api/templates/{tid}/publish", json={}).status_code == 409
    assert ann.get("/api/templates").json()[0]["publication"]["release_no"] == 1
    # visible to the organization from another workspace; not from another organization
    seen = cid.get("/api/organization-templates").json()
    assert seen["organization"]["name"] == "Template Org" and [x["id"] for x in seen["items"]] == [pub["id"]]
    assert seen["items"][0]["can_manage"] is False and seen["items"][0]["source_ahead"] is None and seen["items"][0]["source_template_id"] is None
    assert eve.get("/api/organization-templates").json()["items"] == []
    assert eve.get(f"/api/organization-templates/{pub['id']}").status_code == 404
    assert eve.post(f"/api/organization-templates/{pub['id']}/use", json={}).status_code == 404
    # a viewer may browse but not copy; the owner (org owner, admin of Team A) may maintain
    assert bob.get(f"/api/organization-templates/{pub['id']}").status_code == 200
    assert bob.post(f"/api/organization-templates/{pub['id']}/use", json={}).status_code == 403
    assert cid.post(f"/api/organization-templates/{pub['id']}/update", json={}).status_code == 403
    # cid uses it in Team B: an ordinary template of Team B with the field and the origin recorded
    bpid = cid.post("/api/projects", json={"name": "B 项目"}).json()["id"]
    r = cid.post(f"/api/organization-templates/{pub['id']}/use", json={"project_id": bpid})
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["id"] != tid and copy["head"] == 1 and [s["name"] for s in copy["version"]["slots"]] == ["客户名称"]
    assert copy["info"]["origin"]["publication_id"] == pub["id"] and copy["info"]["origin"]["release_no"] == 1
    assert cid.get(f"/api/projects/{bpid}").json()["template_id"] == copy["id"]
    assert cid.get(f"/api/organization-templates/{pub['id']}").json()["use_count"] == 1
    # the source moves on (a rename → new version? no: a new field) — the release and the copy do not
    view = ann.get(f"/api/templates/{tid}/view").json()
    block = next(b for part in view["doc"]["parts"] for b in _walk(part["blocks"]) if "您好" in b["text"])
    s = block["text"].index("您好")
    assert ann.post(f"/api/templates/{tid}/slots", json={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 2, "name": "问候"}).status_code == 200
    assert ann.get(f"/api/organization-templates/{pub['id']}").json()["version_no"] == 2
    assert ann.get(f"/api/organization-templates/{pub['id']}").json()["source_ahead"] is True
    assert len(cid.get(f"/api/templates/{copy['id']}").json()["version"]["slots"]) == 1
    # a new release: explicit; then the organization sees release 2 with two fields
    rel = ann.post(f"/api/organization-templates/{pub['id']}/update", json={}).json()
    assert rel["release_no"] == 2 and rel["version_no"] == 3 and rel["slot_count"] == 2 and rel["released"] is True
    assert ann.post(f"/api/organization-templates/{pub['id']}/update", json={}).json()["error"] == "unchanged"
    assert ann.post(f"/api/organization-templates/{pub['id']}/update", json={"description": "第二版"}).json()["description"] == "第二版"
    assert [x["release_no"] for x in cid.get(f"/api/organization-templates/{pub['id']}").json()["releases"]] == [2, 1]
    assert len(cid.get(f"/api/templates/{copy['id']}").json()["version"]["slots"]) == 1, "the copy keeps the release it was made from"
    # deprecate: hidden from the picker, refused for new uses, existing copy untouched; the org owner may restore
    assert ann.post(f"/api/organization-templates/{pub['id']}/deprecate", json={"note": "改用新版"}).json()["deprecated"] is True
    assert cid.get("/api/organization-templates").json()["items"] == []
    assert cid.get("/api/organization-templates?include_deprecated=1").json()["items"][0]["deprecation_note"] == "改用新版"
    assert cid.post(f"/api/organization-templates/{pub['id']}/use", json={}).json()["error"] == "deprecated"
    assert cid.get(f"/api/templates/{copy['id']}").status_code == 200
    assert dan.post(f"/api/organization-templates/{pub['id']}/deprecate", json={"deprecated": False}).json()["deprecated"] is False
    assert dan.post(f"/api/organization-templates/{pub['id']}/deprecate", json={"deprecated": False}).status_code == 409
    # the source template is deleted: the release stays usable, a new release is refused
    assert ann.patch(f"/api/projects/{pid}", json={"template_id": None}).status_code == 200
    assert ann.delete(f"/api/templates/{tid}").status_code == 200
    got = cid.get(f"/api/organization-templates/{pub['id']}").json()
    assert got["source_gone"] is True and got["slot_count"] == 2
    assert cid.post(f"/api/organization-templates/{pub['id']}/use", json={}).status_code == 201
    assert ann.post(f"/api/organization-templates/{pub['id']}/update", json={}).json()["error"] == "source_gone"
