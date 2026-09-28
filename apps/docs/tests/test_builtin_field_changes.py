"""CI-112: changing a field of a built-in template on the project page (name, required…) saved
the new version without its editor model. The spreadsheet editor then refused the template
(「这个模板不是用表格编辑器创建的」), the document editor opened an empty document — and a live
room reset itself to one — and the next editor save reverted the field's name. Adding or removing
a field there changed only the file, so the next editor save undid it.
TEST FIXTURES: the synthetic built-in models of test_api_builtin."""
from __future__ import annotations

import json

import pytest

from .test_api_builtin import DOC, SHEET


def _fields(kind, editor):
    if kind == "builtin_sheet":
        return {f["id"]: (f["name"], f.get("required")) for sh in editor["sheets"] for f in sh.get("fields") or []}
    out = {}

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "field":
                out[n["attrs"]["id"]] = (n["attrs"].get("name"), n["attrs"].get("required"))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(editor)
    return out


@pytest.mark.parametrize("kind,model", [("builtin_sheet", SHEET), ("builtin_doc", DOC)])
def test_a_field_change_on_the_project_page_keeps_the_editor_model(api, kind, model):
    c = api.c
    t = api.ok(c.post("/api/templates/builtin", json={"kind": kind, "editor": model, "name": "字段修改"}))
    tid, slot = t["id"], t["version"]["slots"][0]
    api.ok(c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"name": "客户名称", "required": False}))
    d = api.ok(c.get(f"/api/templates/{tid}"))
    assert d["head"] == 2 and d["version"]["editor"], "the editors can open it"
    assert _fields(kind, d["version"]["editor"]) == {"customer": ("客户名称", False)}
    # saving from the editor keeps what was set on the project page
    saved = api.ok(c.put(f"/api/templates/{tid}/builtin", json={"editor": d["version"]["editor"], "base_version": 2}))
    after = {s["id"]: (s["name"], s.get("required")) for s in saved["version"]["slots"]}
    assert after["customer"] == ("客户名称", False) or saved.get("unchanged")


@pytest.mark.parametrize("kind,model", [("builtin_sheet", SHEET), ("builtin_doc", DOC)])
def test_adding_or_removing_a_field_of_a_builtin_template_is_done_in_its_editor(api, kind, model):
    c = api.c
    t = api.ok(c.post("/api/templates/builtin", json={"kind": kind, "editor": model, "name": "字段增删"}))
    tid, slot = t["id"], t["version"]["slots"][0]
    r = c.delete(f"/api/templates/{tid}/slots/{slot['id']}")
    assert r.status_code == 409 and r.json()["error"] == "builtin_fields" and "编辑器" in r.json()["message"]
    r = c.post(f"/api/templates/{tid}/slots", json={"kind": "text", "part": "word/document.xml", "path": "0", "start": 0, "end": 2, "name": "新字段"})
    assert r.status_code == 409 and r.json()["error"] == "builtin_fields"
    assert api.ok(c.get(f"/api/templates/{tid}"))["head"] == 1


@pytest.mark.parametrize("kind,model", [("builtin_sheet", SHEET), ("builtin_doc", DOC)])
def test_a_version_saved_without_its_model_gets_it_back(api, kind, model):
    """TEST FIXTURE: a head version as the old code saved it (no editor model)."""
    c = api.c
    store = c.app_state.app.store
    t = api.ok(c.post("/api/templates/builtin", json={"kind": kind, "editor": model, "name": "旧版本"}))
    tid, slot = t["id"], t["version"]["slots"][0]
    api.ok(c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"name": "旧改名"}))
    store.execute("UPDATE template_versions SET editor=NULL WHERE template_id=? AND version_no=2", (tid,))
    d = api.ok(c.get(f"/api/templates/{tid}"))
    assert _fields(kind, d["version"]["editor"])["customer"][0] == "旧改名"
    # a further change on top of it carries the recovered model, and restoring it stores one again
    api.ok(c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"required": False}))
    v3 = api.ok(c.get(f"/api/templates/{tid}"))["version"]["editor"]
    assert _fields(kind, v3)["customer"] == ("旧改名", False)
    api.ok(c.post(f"/api/templates/{tid}/restore", json={"version": 2}))
    head = api.ok(c.get(f"/api/templates/{tid}"))["head"]
    row = store.one("SELECT editor FROM template_versions WHERE template_id=? AND version_no=?", (tid, head))
    ed = row["editor"] if isinstance(row["editor"], dict) else json.loads(row["editor"])
    assert _fields(kind, ed)["customer"][0] == "旧改名"
