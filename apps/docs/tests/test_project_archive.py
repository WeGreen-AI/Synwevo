"""Archive hides a project from the default list without destroying it.

`projects.deleted` is the soft delete — a deleted project 404s.  Archiving had no column
of its own, so the homepage could only offer delete, which is why a destructive action was
the most prominent control on every card.  archived_at keeps the project fully usable.
"""
from __future__ import annotations


def test_archive_hides_from_the_default_list_but_keeps_the_project(api):
    pid = api.project("要归档的项目")
    assert any(p["id"] == pid for p in api.ok(api.c.get("/api/projects")))

    api.ok(api.c.patch(f"/api/projects/{pid}", json={"archived": True}))

    default = api.ok(api.c.get("/api/projects"))
    assert not any(p["id"] == pid for p in default), "归档后不应出现在默认列表"

    with_arch = api.ok(api.c.get("/api/projects", params={"archived": 1}))
    row = next(p for p in with_arch if p["id"] == pid)
    assert row["archived_at"], "归档时间必须记录下来"

    # the project itself is untouched and still fully usable
    detail = api.ok(api.c.get(f"/api/projects/{pid}"))
    assert detail["id"] == pid and detail["name"] == "要归档的项目"


def test_archive_is_reversible(api):
    pid = api.project("来回归档")
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"archived": True}))
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"archived": False}))
    assert any(p["id"] == pid for p in api.ok(api.c.get("/api/projects")))
    row = next(p for p in api.ok(api.c.get("/api/projects")) if p["id"] == pid)
    assert row["archived_at"] is None


def test_archive_is_not_delete(api):
    """Deleting makes the project 404; archiving must not."""
    a, b = api.project("归档的"), api.project("删除的")
    api.ok(api.c.patch(f"/api/projects/{a}", json={"archived": True}))
    api.ok(api.c.delete(f"/api/projects/{b}"))
    assert api.c.get(f"/api/projects/{a}").status_code == 200, "归档的项目仍应可以打开"
    assert api.c.get(f"/api/projects/{b}").status_code == 404, "删除的项目应当 404"


def test_list_returns_what_the_dense_list_renders(api, fixtures):
    """The homepage shows template name, source count, rule mode and the last job."""
    pid = api.project("列表字段")
    api.upload(pid, fixtures / "测试_客户100.xlsx")
    t = api.template(pid, fixtures / "测试_合同模板.docx")
    api.ok(api.c.patch(f"/api/projects/{pid}", json={"template_id": t["id"]}))
    row = next(p for p in api.ok(api.c.get("/api/projects")) if p["id"] == pid)
    assert row["template_name"] == t["name"]
    assert row["sources"] == 1
    assert "origin" in row and "archived_at" in row
    assert row["origin"] == "ui", "界面创建的项目应记录来源为 ui"


def test_api_created_projects_record_their_key_as_origin(api, client):
    """Provenance is what a cleanup tool acts on; a name prefix is not evidence."""
    from synwevo_core.auth import Auth
    token, _ = Auth(client.app_state.app.store).create_key("prov", "default", ("read", "write"))
    made = api.ok(api.c.post("/api/v1/projects", json={"name": "自动化建的"},
                             headers={"Authorization": f"Bearer {token}"}))
    row = next(p for p in api.ok(api.c.get("/api/projects")) if p["id"] == made["id"])
    assert (row["origin"] or "").startswith("api:"), row["origin"]
