"""Every browser write to a Synwevo Docs template — its file, its fields, its versions — and to a
project's mapping list leaves exactly one audit row, written by the service inside the transaction
of the change (admin coverage G26, decision D7, work package WP5): identifiers and counts only —
never a template or field name, a version note, a mapping's label or the editor's content; a
failed audit undoes the version it belongs to; a refused write leaves no ``docs.template.*`` row.

The composed server with real sessions (``team`` from test_gate_roles): the gate puts the signed-in
person in the request context, which is the only way the service learns who acted.
"""
from __future__ import annotations

import json

from tests.integration.test_gate_roles import _send, _xlsx, team  # noqa: F401  (team is a fixture)

#: The browser write routes under /api/templates and /api/projects/{pid}/mappings that this work
#: package audits, with the action each one leaves. Every path that makes a version — an editor
#: save, a field added, changed or removed — leaves one ``docs.template.version`` row whose
#: ``reason`` names the path.
AUDITED = [
    ("POST", "/api/templates/upload", "docs.template.upload"),
    ("POST", "/api/templates/builtin", "docs.template.create"),
    ("PUT", "/api/templates/{tid}/builtin", "docs.template.version"),
    ("PATCH", "/api/templates/{tid}", "docs.template.rename"),
    ("POST", "/api/templates/{tid}/duplicate", "docs.template.duplicate"),
    ("POST", "/api/templates/{tid}/slots", "docs.template.version"),
    ("PATCH", "/api/templates/{tid}/slots/{slot_id}", "docs.template.version"),
    ("DELETE", "/api/templates/{tid}/slots/{slot_id}", "docs.template.version"),
    ("POST", "/api/templates/{tid}/undo", "docs.template.undo"),
    ("POST", "/api/templates/{tid}/redo", "docs.template.redo"),
    ("POST", "/api/templates/{tid}/restore", "docs.template.restore"),
    ("PUT", "/api/projects/{pid}/mappings", "docs.mappings.save"),
    ("POST", "/api/projects/{pid}/mappings/undo", "docs.mappings.undo"),
    ("POST", "/api/projects/{pid}/mappings/redo", "docs.mappings.redo"),
    ("DELETE", "/api/templates/{tid}", "docs.template.delete"),
]
#: Audited by the same service calls but needing a state this test does not build: a live table
#: room (``docs.template.version`` with reason ``live_save``; the collab tests). The v1 definition
#: routes (``docs.template.fields.import``, ``docs.project.import``) are the machine API and have
#: their own coverage.
COVERED_ELSEWHERE = {("POST", "/api/templates/{tid}/collab/save")}

#: The editor document of the built-in template this test authors (a test fixture): one paragraph
#: with one field. Its text and the field's name are content and must never reach a row.
DOC = {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [
    {"type": "text", "text": "审计夹具正文："},
    {"type": "field", "attrs": {"id": "customer", "name": "审计夹具字段", "required": True}}]}]}}

#: Content that must never reach a row: the uploaded file's name, the names this test gives the
#: templates and fields, the version note, the mapping's label, the fixture workbook's sheet and
#: cell text, the duplicate's name suffix, and the team project's name.
CONTENT = ("模板夹具", "审计内置模板", "改名内置模板", "审计夹具字段", "审计夹具正文", "审计表格字段", "改过的审计表格字段",
           "审计版本说明", "审计映射标签", "客户", "夹具客户甲", "副本", "团队项目")

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _mark(store) -> int:
    return store.one("SELECT COALESCE(MAX(seq), 0) AS n FROM audit_events")["n"]


def _since(store, mark: int, prefix: str = "") -> list[dict]:
    return [dict(r) for r in store.all("SELECT * FROM audit_events WHERE seq > ? AND action LIKE ? ORDER BY seq",
                                       (mark, prefix + "%"))]


def _upload(owner):
    return _send(owner, "post", "/api/templates/upload", files={"file": ("模板夹具.xlsx", _xlsx(), XLSX_TYPE)})


# ---------------------------------------------------------------- one call, one row
def test_every_template_and_mapping_write_route_leaves_exactly_one_row(team):
    from synwevo_docs import authz
    for method, path, _action in AUDITED:
        assert authz.ROUTE_POLICY.get((method, path)) == "write", f"{method} {path} is not a classified write route"
    for method, path in COVERED_ELSEWHERE:
        assert authz.ROUTE_POLICY.get((method, path)) == "write", f"{method} {path} is not a classified write route"

    store, ws, pid = team["store"], team["ws"], team["pid"]
    owner, owner_id = team["c"]["owner"], team["people"]["owner"]["id"]
    seen: list[tuple[str, str]] = []

    def one(method: str, path: str, action: str, object_kind: str, object_id, **kw):
        """One successful call as the owner, then exactly one new audit row, attributed and clean."""
        label = f"{method} {path}"
        names = {"pid": pid, **kw.pop("ids", {})}
        mark = _mark(store)
        r = _send(owner, method.lower(), path.format(**names), **kw)
        assert r.status_code == 200, f"{label} → {r.status_code} {r.text[:300]}"
        new = _since(store, mark)
        assert [x["action"] for x in new] == [action], f"{label}: expected one {action} row, got {[x['action'] for x in new]}"
        row = new[0]
        want_id = object_id(r) if callable(object_id) else object_id
        assert (row["actor_kind"], row["actor_id"], row["workspace_id"], row["object_kind"], row["object_id"], row["result"]) \
            == ("user", owner_id, ws, object_kind, want_id, "ok"), f"{label}: {row}"
        assert row["request_id"], f"{label}: the row does not name the request"
        for text in CONTENT:
            assert text not in row["meta"], f"{label}: {text!r} reached the audit row: {row['meta']}"
        seen.append((method, path))
        return r, json.loads(row["meta"])

    def version_meta(no: int, previous: int, reason: str, slots: int) -> dict:
        return {"version_no": no, "previous_head": previous, "reason": reason, "slots": slots,
                "discard_live": False, "base_head_given": True}

    # An uploaded workbook: the file's format and size, whether it was converted or stripped of
    # macros, and the scan verdict — not its name.
    r, meta = one("POST", "/api/templates/upload", "docs.template.upload", "template", lambda r: r.json()["id"],
                  files={"file": ("模板夹具.xlsx", _xlsx(), XLSX_TYPE)})
    tid_x = r.json()["id"]
    assert r.json()["name"] == "模板夹具" and r.json()["head"] == 1, r.text[:300]
    assert meta == {"format": "xlsx", "size": len(_xlsx()), "converted": False, "macros_removed": False, "scan": "not_configured"}, meta

    # A template authored in the built-in editor, then saved once more: kind and format, then the
    # version numbers — not the editor's document.
    r, meta = one("POST", "/api/templates/builtin", "docs.template.create", "template", lambda r: r.json()["id"],
                  json={"kind": "builtin_doc", "editor": DOC, "name": "审计内置模板"})
    tid_b = r.json()["id"]
    assert meta == {"kind": "builtin_doc", "format": "docx"}, meta
    r, meta = one("PUT", "/api/templates/{tid}/builtin", "docs.template.version", "template", tid_b, ids={"tid": tid_b},
                  json={"editor": DOC, "base_version": 1, "note": "审计版本说明"})
    assert r.json()["head"] == 2 and r.json()["version"]["note"] == "审计版本说明", r.text[:300]
    assert meta == version_meta(2, 1, "save", 1), meta

    # Renaming records that a rename happened; the name itself is content. A duplicate is a new
    # template whose row names its source and the version it copied.
    _, meta = one("PATCH", "/api/templates/{tid}", "docs.template.rename", "template", tid_b, ids={"tid": tid_b},
                  json={"name": "改名内置模板"})
    assert meta == {}
    r, meta = one("POST", "/api/templates/{tid}/duplicate", "docs.template.duplicate", "template", lambda r: r.json()["id"],
                  ids={"tid": tid_b}, json={})
    tid_dup = r.json()["id"]
    assert tid_dup != tid_b and r.json()["name"] == "改名内置模板 副本", r.text[:300]
    assert meta == {"source_template_id": tid_b, "source_version": 2, "kind": "builtin_doc", "format": "docx"}, meta

    # A field added to, changed on and removed from the uploaded workbook: each is a version, and
    # the row says which path made it and how many fields the version holds.
    r, meta = one("POST", "/api/templates/{tid}/slots", "docs.template.version", "template", tid_x, ids={"tid": tid_x},
                  json={"kind": "cell", "name": "审计表格字段", "sheet": "客户", "cell": "B2"})
    slot_id = r.json()["created_slot"]["id"]
    assert r.json()["head"] == 2 and [s["name"] for s in r.json()["version"]["slots"]] == ["审计表格字段"], r.text[:300]
    assert meta == version_meta(2, 1, "field.add", 1), meta
    r, meta = one("PATCH", "/api/templates/{tid}/slots/{slot_id}", "docs.template.version", "template", tid_x,
                  ids={"tid": tid_x, "slot_id": slot_id}, json={"name": "改过的审计表格字段", "required": True})
    assert r.json()["head"] == 3 and r.json()["version"]["slots"][0]["name"] == "改过的审计表格字段", r.text[:300]
    assert meta == version_meta(3, 2, "field.update", 1), meta
    r, meta = one("DELETE", "/api/templates/{tid}/slots/{slot_id}", "docs.template.version", "template", tid_x,
                  ids={"tid": tid_x, "slot_id": slot_id})
    assert r.json()["head"] == 4 and r.json()["version"]["slots"] == [], r.text[:300]
    assert meta == version_meta(4, 3, "field.remove", 0), meta

    # Undo moves the head back to the parent, redo forward again, and a restore copies an old
    # version into a new head: version numbers only.
    r, meta = one("POST", "/api/templates/{tid}/undo", "docs.template.undo", "template", tid_x, ids={"tid": tid_x}, json={"head": 4})
    assert r.json()["head"] == 3, r.text[:300]
    assert meta == {"version_no": 3, "previous_head": 4, "discard_live": False}, meta
    r, meta = one("POST", "/api/templates/{tid}/redo", "docs.template.redo", "template", tid_x, ids={"tid": tid_x}, json={"head": 3})
    assert r.json()["head"] == 4, r.text[:300]
    assert meta == {"version_no": 4, "previous_head": 3, "discard_live": False}, meta
    r, meta = one("POST", "/api/templates/{tid}/restore", "docs.template.restore", "template", tid_x, ids={"tid": tid_x},
                  json={"version": 1, "base_head": 4})
    assert r.json()["head"] == 5 and [v["version_no"] for v in r.json()["versions"]] == [1, 2, 3, 4, 5], r.text[:300]
    assert meta == {"version_no": 5, "previous_head": 4, "source_version": 1, "discard_live": False}, meta

    # The team project's mapping list: one mapping saved, undone, redone. Revision numbers and
    # counts; the mapping's sources, target and label are the project's content.
    mapping = {"sources": [{"kind": "cell", "sheet": "客户", "cell": "A2"}], "target": {"binding": "audit-target"},
               "label": "审计映射标签"}
    r, meta = one("PUT", "/api/projects/{pid}/mappings", "docs.mappings.save", "project", pid,
                  json={"mappings": [mapping], "base_rev": 0})
    assert r.json()["rev"] == 1 and len(r.json()["mappings"]) == 1, r.text[:300]
    assert meta == {"rev": 1, "previous_rev": 0, "base_rev": 0, "merged_with": None, "unchanged": False,
                    "count": 1, "added": 1, "removed": 0, "modified": 0}, meta
    r, meta = one("POST", "/api/projects/{pid}/mappings/undo", "docs.mappings.undo", "project", pid, json={"base_rev": 1})
    assert r.json()["rev"] == 0 and r.json()["mappings"] == [], r.text[:300]
    assert meta == {"rev": 0, "previous_rev": 1}, meta
    r, meta = one("POST", "/api/projects/{pid}/mappings/redo", "docs.mappings.redo", "project", pid, json={"base_rev": 0})
    assert r.json()["rev"] == 1 and len(r.json()["mappings"]) == 1, r.text[:300]
    assert meta == {"rev": 1, "previous_rev": 0}, meta

    # Deleting the duplicate, which no project uses: kind and format, not the name.
    r, meta = one("DELETE", "/api/templates/{tid}", "docs.template.delete", "template", tid_dup, ids={"tid": tid_dup})
    assert r.json() == {"ok": True}
    assert meta == {"kind": "builtin_doc", "format": "docx"}, meta
    assert store.one("SELECT deleted FROM templates WHERE id=?", (tid_dup,))["deleted"] == 1

    assert sorted(seen) == sorted((m, p) for m, p, _ in AUDITED), "the inventory and the calls made disagree"


# ---------------------------------------------------------------- the row and the version are one transaction
def test_a_failed_audit_undoes_a_version_save(team, monkeypatch):
    from synwevo_core import auth
    store, owner = team["store"], team["c"]["owner"]
    r = _send(owner, "post", "/api/templates/builtin", json={"kind": "builtin_doc", "editor": DOC, "name": "审计内置模板"})
    assert r.status_code == 200, r.text[:300]
    tid = r.json()["id"]

    def state() -> tuple[int, int]:
        head = store.one("SELECT head FROM templates WHERE id=?", (tid,))["head"]
        n = store.one("SELECT COUNT(*) AS n FROM template_versions WHERE template_id=?", (tid,))["n"]
        return head, n

    before, mark = state(), _mark(store)
    assert before == (1, 1)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", fail)
    r = _send(owner, "put", f"/api/templates/{tid}/builtin", json={"editor": DOC, "base_version": 1, "note": "不会落地的版本"})
    assert r.status_code >= 500 and "synthetic" not in r.text, r.text
    monkeypatch.setattr(auth, "audit_event", original)

    assert state() == before, "a version whose audit row failed must not exist and must not be the head"
    assert _since(store, mark) == [], "a change that was undone has no audit row"
    assert _send(owner, "get", f"/api/templates/{tid}").json()["head"] == 1

    # With the writer back, the same save goes through, as the version it would have been.
    r = _send(owner, "put", f"/api/templates/{tid}/builtin", json={"editor": DOC, "base_version": 1, "note": "会落地的版本"})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text[:300]
    assert state() == (2, 2)
    rows = _since(store, mark)
    assert [x["action"] for x in rows] == ["docs.template.version"]
    meta = json.loads(rows[0]["meta"])
    assert (meta["version_no"], meta["previous_head"], meta["reason"]) == (2, 1, "save"), meta
    assert "会落地的版本" not in rows[0]["meta"] and "审计内置模板" not in rows[0]["meta"], rows[0]["meta"]


# ---------------------------------------------------------------- a refusal is not a change
def test_a_refused_delete_of_a_template_in_use_leaves_no_row(team):
    store, pid, owner = team["store"], team["pid"], team["c"]["owner"]
    up = _upload(owner)
    assert up.status_code == 200, up.text[:300]
    tid = up.json()["id"]

    # Binding the template to the team project is a project change with its own row (WP4).
    mark = _mark(store)
    r = _send(owner, "patch", f"/api/projects/{pid}", json={"template_id": tid})
    assert r.status_code == 200 and r.json()["template_id"] == tid, r.text[:300]
    assert "docs.project.update" in [x["action"] for x in _since(store, mark)]

    mark = _mark(store)
    r = _send(owner, "delete", f"/api/templates/{tid}")
    assert r.status_code == 409 and r.json()["error"] == "template_in_use", r.text[:300]
    assert _since(store, mark) == [], "a delete the service refused is not a change"
    assert store.one("SELECT deleted FROM templates WHERE id=?", (tid,))["deleted"] == 0
    assert store.one("SELECT template_id FROM projects WHERE id=?", (pid,))["template_id"] == tid


def test_a_viewer_refusal_leaves_no_row(team):
    store, ws, viewer = team["store"], team["ws"], team["c"]["viewer"]
    count = store.one("SELECT COUNT(*) AS n FROM templates WHERE tenant=? AND deleted=0", (ws,))["n"]
    mark = _mark(store)
    r = _send(viewer, "post", "/api/templates/builtin", json={"kind": "builtin_doc", "editor": DOC, "name": "查看者的模板"})
    assert r.status_code == 403 and r.json()["error"] == "read_only_role", r.text
    assert _since(store, mark, "docs.template.") == [], "the gate refused before the service ran"
    assert store.one("SELECT COUNT(*) AS n FROM templates WHERE tenant=? AND deleted=0", (ws,))["n"] == count
