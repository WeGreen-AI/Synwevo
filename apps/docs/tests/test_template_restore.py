"""Restoring a template version makes it current as a *new* version (history kept, undo chain
linear), refuses a stale base, and tells how many projects follow or are pinned."""
from __future__ import annotations

import io


def _docx() -> bytes:
    import docx
    d = docx.Document(); d.add_paragraph("尊敬的示例客户名称：您好。")
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


def test_restore_creates_a_new_head_from_an_old_version_and_reports_usage(api, tmp_path):
    pid = api.project("恢复版本")
    tpl = tmp_path / "模板.docx"; tpl.write_bytes(_docx())
    t = api.template(pid, tpl); tid = t["id"]
    api.text_slot(tid, "示例客户名称", "客户名称")          # v2: one field
    api.text_slot(tid, "您好", "问候")                     # v3: two fields
    d = api.ok(api.c.get(f"/api/templates/{tid}"))
    assert d["head"] == 3 and len(d["version"]["slots"]) == 2
    # usage: this project follows the head; a second project pins v2
    p2 = api.project("固定版本的项目")
    api.ok(api.c.patch(f"/api/projects/{p2}", json={"template_id": tid}))
    api.ok(api.c.patch(f"/api/projects/{p2}", json={"template_version": 2}))
    use = api.ok(api.c.get(f"/api/templates/{tid}/usage"))
    assert use["following"] == 1 and use["pinned"] == 1 and {x["name"]: x["pinned_version"] for x in use["projects"]} == {"恢复版本": None, "固定版本的项目": 2}
    # a stale base is refused; the current version cannot be "restored"
    r = api.c.post(f"/api/templates/{tid}/restore", json={"version": 2, "base_head": 2})
    assert r.status_code == 409 and r.json()["error"] == "stale_template"
    assert api.c.post(f"/api/templates/{tid}/restore", json={"version": 3, "base_head": 3}).status_code == 400
    assert api.c.post(f"/api/templates/{tid}/restore", json={"version": "x"}).status_code == 400
    assert api.c.post(f"/api/templates/{tid}/restore", json={"version": 99, "base_head": 3}).status_code == 404
    # restore v2: v4 is created with v2's fields, history keeps v3, undo goes back to v3
    out = api.ok(api.c.post(f"/api/templates/{tid}/restore", json={"version": 2, "base_head": 3}))
    assert out["head"] == 4 and [s["name"] for s in out["version"]["slots"]] == ["客户名称"] and out["version"]["note"] == "恢复自 v2"
    assert [v["version_no"] for v in out["versions"]] == [1, 2, 3, 4] and out["can_undo"] is True
    back = api.ok(api.c.post(f"/api/templates/{tid}/undo", json={"base_head": 4}))
    assert back["head"] == 3 and len(back["version"]["slots"]) == 2
    # the following project sees the head; the pinned one keeps v2
    assert api.ok(api.c.get(f"/api/projects/{p2}"))["template_version"] == 2
