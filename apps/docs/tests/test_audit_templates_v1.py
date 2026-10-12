"""Audit of template, version, field, mapping and definition changes made through the machine
API (/api/v1).

Admin coverage G26, decision D7, work package 5: the service writes the audit row inside the
transaction of the change, attributed to the request principal the gate put in the context. For a
legacy ``dw_`` API key `authority.audit_actor` names that principal ``("machine", "api:<key id>")``.
These tests drive every v1 twin of the template, field, mapping and definition write routes with
such a key and read the rows back from ``audit_events``: exactly one row per change (one per
*version*, from whichever path made it), the machine identity, the key's workspace, the request id,
and identifiers or counts only — never a template or field name, a note, a rule or a cell value.

The live table room saves without a request principal; `audit_event` then names the person the
room acts for (`context.actor()`), never the system. That path is driven here through the same
service call the room makes, under the same actor variable.

TEST FIXTURES: ``SHEET`` is a synthetic table-editor model (the shape test_sheet_collab uses); the
uploaded template is the generated ``测试_合同模板.docx``; the API key is minted in the test's
temporary data directory.
"""
import json

import pytest

from synwevo_core.auth import Auth

FIELDS = "docweave.template-fields"
PROJECT = "docweave.project"
SHEET = {"sheets": [{"name": "结果", "cols": [{"w": 100}] * 3, "rows": [{"h": 24}] * 3,
                     "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}}, "merges": [],
                     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]}]}
TWO_FIELDS = {"schema": FIELDS, "version": 1, "fields": [
    {"kind": "text", "name": "客户名称", "find": "示例客户名称"},
    {"kind": "text", "name": "金额", "find": "0.00"}]}


def edited(cells: dict) -> dict:
    """``SHEET`` with these cells filled in: a model that differs from the head, so a save writes a version."""
    s = {**SHEET["sheets"][0], "cells": {**SHEET["sheets"][0]["cells"], **cells}}
    return {"sheets": [s]}


@pytest.fixture()
def machine(client):
    """One machine credential of the default workspace with every scope the routes need
    (``admin`` for DELETE /templates/{tid})."""
    store = client.app_state.app.store
    token, row = Auth(store).create_key("audit-templates-v1", "default", ("read", "write", "admin"))
    return {"h": {"Authorization": f"Bearer {token}"}, "key_id": row["id"], "tenant": "default", "store": store}


def _ok(r, status=None):
    assert r.status_code < 400, (r.status_code, r.text[:800])
    if status is not None:
        assert r.status_code == status, (r.status_code, r.text[:800])
    return r.json()


class AuditLog:
    """Reads ``audit_events`` after a mark. ``seq`` is the table's autoincrement, so "the rows this
    call wrote" is exact: everything above the mark taken before the call."""

    def __init__(self, machine):
        self.store = machine["store"]
        self.machine = machine
        self.mark()

    def mark(self):
        self.seq = self.store.one("SELECT COALESCE(MAX(seq), 0) AS m FROM audit_events")["m"]

    def new_rows(self):
        rows = [dict(r) for r in self.store.all("SELECT * FROM audit_events WHERE seq>? ORDER BY seq", (self.seq,))]
        for r in rows:
            r["meta_text"] = r["meta"] or ""
            r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        return rows

    def docs_rows(self):
        """Every new ``docs.*`` row, in the order written; the next check counts from here."""
        rows = [r for r in self.new_rows() if r["action"].startswith("docs.")]
        self.mark()
        return rows

    def check(self, row, *, action, object_kind, object_id, actor=None, request_id=True):
        """One row is this action on this object by this actor (the machine key unless said otherwise)."""
        assert row["action"] == action, row
        assert (row["actor_kind"], row["actor_id"]) == (actor or ("machine", f"api:{self.machine['key_id']}")), row
        assert row["workspace_id"] == self.machine["tenant"], row
        assert (row["object_kind"], row["object_id"]) == (object_kind, object_id), row
        assert row["result"] == "ok", row
        if request_id:
            assert row["request_id"], row

    def one(self, action, *, object_kind, object_id, actor=None, request_id=True):
        """Exactly one new ``docs.*`` row, and it is this action on this object by this actor.
        Returns ``(meta, meta_text)``; the next check counts from here."""
        rows = self.new_rows()
        docs = [r for r in rows if r["action"].startswith("docs.")]
        assert [r["action"] for r in docs] == [action], [(r["action"], r["object_id"]) for r in rows]
        self.check(docs[0], action=action, object_kind=object_kind, object_id=object_id, actor=actor, request_id=request_id)
        self.mark()
        return docs[0]["meta"], docs[0]["meta_text"]

    def nothing(self, *, prefix="docs."):
        rows = [r for r in self.new_rows() if r["action"].startswith(prefix)]
        assert rows == [], [(r["action"], r["object_id"]) for r in rows]
        self.mark()


def _count(store, action, object_id):
    return store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action=? AND object_id=?", (action, object_id))["n"]


def _upload(c, h, fixtures, headers=None):
    data = (fixtures / "测试_合同模板.docx").read_bytes()
    r = c.post("/api/v1/templates", files={"file": ("测试_合同模板.docx", data)}, headers=headers or h)
    return r, data


def test_each_v1_template_and_mapping_write_leaves_one_machine_row(client, machine, fixtures):
    c, h, app, store = client, machine["h"], client.app_state.app, machine["store"]
    log = AuditLog(machine)

    # ---- POST /templates → docs.template.upload (format, size and the scan outcome; never the name)
    r, docx = _upload(c, h, fixtures)
    tid = _ok(r, 201)["id"]
    meta, text = log.one("docs.template.upload", object_kind="template", object_id=tid)
    assert meta == {"format": "docx", "size": len(docx), "converted": False, "macros_removed": False,
                    "scan": "not_configured" if app.scanner is None else meta["scan"]}
    assert meta["scan"], meta                                    # a scan outcome, configured or not
    assert "合同模板" not in text and "docx" not in json.dumps({k: v for k, v in meta.items() if k != "format"})

    # ---- POST /templates/builtin → docs.template.create (kind and format; never the name)
    bid = _ok(c.post("/api/v1/templates/builtin", json={"kind": "builtin_sheet", "name": "审计表格", "editor": SHEET}, headers=h), 201)["id"]
    meta, text = log.one("docs.template.create", object_kind="template", object_id=bid)
    assert meta == {"kind": "builtin_sheet", "format": "xlsx"}
    assert "审计表格" not in text

    # ---- PUT /templates/{tid}/builtin → docs.template.version, reason "save" (never the note or a cell)
    saved = _ok(c.put(f"/api/v1/templates/{bid}/builtin", headers=h,
                      json={"editor": edited({"A2": {"v": "张三"}}), "base_version": 1, "note": "审计检查点"}))
    assert saved["head"] == 2 and saved["version"]["no"] == 2
    meta, text = log.one("docs.template.version", object_kind="template", object_id=bid)
    assert meta == {"version_no": 2, "previous_head": 1, "reason": "save", "slots": 1, "discard_live": False, "base_head_given": True}
    assert "审计检查点" not in text and "张三" not in text

    # ---- POST /templates/{tid}/fields with two definitions → ONE docs.template.fields.import row,
    #      count 2, folded, and no docs.template.version row for the intermediate versions
    out = _ok(c.post(f"/api/v1/templates/{tid}/fields", headers=h, json=TWO_FIELDS))
    assert out["template"] == 2 and [x["name"] for x in out["created"]] == ["客户名称", "金额"]
    meta, text = log.one("docs.template.fields.import", object_kind="template", object_id=tid)
    assert meta == {"count": 2, "version_no": 2, "previous_head": 1, "folded": True}
    assert "客户名称" not in text and "金额" not in text and "示例客户名称" not in text
    assert _count(store, "docs.template.version", tid) == 0
    assert store.one("SELECT COUNT(*) AS n FROM template_versions WHERE template_id=?", (tid,))["n"] == 2
    fields = _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["fields"]
    kept, fid = fields[0]["id"], fields[1]["id"]

    # ---- PATCH /templates/{tid}/fields/{field_id} → docs.template.version, reason "field.update"
    out = _ok(c.patch(f"/api/v1/templates/{tid}/fields/{fid}", headers=h, json={"base_head": 2, "name": "合同金额", "required": True}))
    assert out["head"] == 3 and next(f for f in out["fields"] if f["id"] == fid)["name"] == "合同金额"
    meta, text = log.one("docs.template.version", object_kind="template", object_id=tid)
    assert meta == {"version_no": 3, "previous_head": 2, "reason": "field.update", "slots": 2, "discard_live": False, "base_head_given": True}
    assert "合同金额" not in text and fid not in text

    # ---- DELETE /templates/{tid}/fields/{field_id} → docs.template.version, reason "field.remove"
    out = _ok(c.delete(f"/api/v1/templates/{tid}/fields/{fid}?base_head=3", headers=h))
    assert out["head"] == 4 and [f["id"] for f in out["fields"]] == [kept] and out["removed_mappings"] == []
    meta, text = log.one("docs.template.version", object_kind="template", object_id=tid)
    assert meta == {"version_no": 4, "previous_head": 3, "reason": "field.remove", "slots": 1, "discard_live": False, "base_head_given": True}
    assert fid not in text

    # ---- POST /templates/{tid}/restore → docs.template.restore (a new version copied from v1)
    out = _ok(c.post(f"/api/v1/templates/{tid}/restore", headers=h, json={"version": 1, "base_head": 4}), 201)
    assert out["head"] == 5 and out["version"] == 5 and out["fields"] == []
    meta, _ = log.one("docs.template.restore", object_kind="template", object_id=tid)
    assert meta == {"version_no": 5, "previous_head": 4, "source_version": 1, "discard_live": False}
    assert _count(store, "docs.template.version", tid) == 2, "a restore is its own row, not a version row"

    # ---- PUT /projects/{pid}/mappings → docs.mappings.save with revision numbers and counts only
    pid = _ok(c.post("/api/v1/projects", json={"name": "审计映射"}, headers=h), 201)["id"]
    log.one("docs.project.create", object_kind="project", object_id=pid)
    saved = _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"note": "审计映射说明", "mappings": [
        {"sources": [{"kind": "constant", "value": "甲"}], "target": {"binding": "audit-target-1"}},
        {"sources": [{"kind": "constant", "value": "乙"}], "target": {"binding": "audit-target-2"}}]}))
    assert saved["rev"] == 1 and len(saved["mappings"]) == 2
    meta, text = log.one("docs.mappings.save", object_kind="project", object_id=pid)
    assert meta == {"rev": 1, "previous_rev": 0, "base_rev": None, "merged_with": None, "unchanged": False,
                    "count": 2, "added": 2, "removed": 0, "modified": 0}
    assert "甲" not in text and "audit-target" not in text and "审计映射说明" not in text and "constant" not in text
    # one mapping changed, one dropped, from the revision just read: the counts say so, the content does not
    m1 = saved["mappings"][0]
    saved2 = _ok(c.put(f"/api/v1/projects/{pid}/mappings", headers=h, json={"base_rev": 1, "mappings": [
        {**m1, "sources": [{"kind": "constant", "value": "丙"}]}]}))
    assert saved2["rev"] == 2 and [m["id"] for m in saved2["mappings"]] == [m1["id"]]
    meta, text = log.one("docs.mappings.save", object_kind="project", object_id=pid)
    assert meta == {"rev": 2, "previous_rev": 1, "base_rev": 1, "merged_with": None, "unchanged": False,
                    "count": 1, "added": 0, "removed": 1, "modified": 1}
    assert "丙" not in text and m1["id"] not in text

    # ---- DELETE /templates/{tid} (admin scope) → docs.template.delete
    assert _ok(c.delete(f"/api/v1/templates/{bid}", headers=h)) == {"deleted": bid}
    meta, text = log.one("docs.template.delete", object_kind="template", object_id=bid)
    assert meta == {"kind": "builtin_sheet", "format": "xlsx"}
    assert "审计表格" not in text
    assert c.get(f"/api/v1/templates/{bid}", headers=h).status_code == 404
    log.nothing()


def test_a_definition_import_writes_its_own_row_beside_the_changes_it_makes(client, machine):
    c, h = client, machine["h"]
    log = AuditLog(machine)
    pid = _ok(c.post("/api/v1/projects", json={"name": "定义导入"}, headers=h), 201)["id"]
    log.one("docs.project.create", object_kind="project", object_id=pid)
    doc = {"schema": PROJECT, "version": 1,
           "rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}},
           "output": {"naming": "{序号}-审计", "formats": ["docx"], "error_policy": "skip"},
           "mappings": [{"sources": [{"kind": "constant", "value": "甲"}], "target": {"binding": "audit-target"}}]}

    out = _ok(c.put(f"/api/v1/projects/{pid}/definition", json=doc, headers=h))
    assert out["rule"] == doc["rule"] and out["output"] == doc["output"] and len(out["mappings"]) == 1
    assert "template" not in out                                    # nothing was bound

    rows = log.docs_rows()
    # the constituent changes in the order the import makes them, then the import's own row
    assert [r["action"] for r in rows] == ["docs.project.update", "docs.project.update", "docs.mappings.save", "docs.project.import"], \
        [(r["action"], r["object_id"]) for r in rows]
    for r in rows:
        log.check(r, action=r["action"], object_kind="project", object_id=pid)
    assert len({r["request_id"] for r in rows}) == 1, "one request, one id on every row it wrote"

    rule_row, output_row, maps_row, import_row = rows
    assert rule_row["meta"]["fields"] == ["record_rule"] and output_row["meta"]["fields"] == ["output"]
    assert maps_row["meta"]["count"] == 1 and maps_row["meta"]["added"] == 1
    assert maps_row["meta"]["rev"] == 1 and maps_row["meta"]["previous_rev"] == 0 and maps_row["meta"]["base_rev"] is None
    assert import_row["meta"] == {"binding": False, "rule": True, "output": True, "mappings": 1, "base_rev": None}
    everything = " ".join(r["meta_text"] for r in rows)
    for content in ("per_row", "客户", "header_row", "审计", "{序号}", "skip", "甲", "audit-target", "定义导入"):
        assert content not in everything, (content, everything)
    assert _count(log.store, "docs.project.import", pid) == 1


def test_an_idempotent_template_upload_replay_writes_no_second_row(client, machine, fixtures):
    c, h = client, machine["h"]
    log = AuditLog(machine)
    k = {**h, "Idempotency-Key": "audit-template-upload-1"}
    first, _ = _upload(c, h, fixtures, headers=k)
    again, _ = _upload(c, h, fixtures, headers=k)
    assert first.status_code == 201 and again.status_code == 201, (first.text[:300], again.text[:300])
    tid = first.json()["id"]
    assert again.json()["id"] == tid
    assert again.headers.get("Idempotent-Replayed") == "true" and "Idempotent-Replayed" not in first.headers
    meta, _ = log.one("docs.template.upload", object_kind="template", object_id=tid)
    assert meta["format"] == "docx"
    assert _count(log.store, "docs.template.upload", tid) == 1
    assert log.store.one("SELECT COUNT(*) AS n FROM templates WHERE original_name=? AND deleted=0", ("测试_合同模板.docx",))["n"] == 1


def test_a_live_room_save_names_the_person_not_the_system(client, machine):
    """The live table room (collab_sheet_room.SheetRoom.save) has no request principal: it sets
    `context.set_actor(user_id)` and calls `save_live_sheet` under the template's tenant. The same
    call here, under the same variables, must attribute the version to that person."""
    from synwevo_core import auth, context
    from synwevo_docs.builtin_service import save_live_sheet

    c, h, app = client, machine["h"], client.app_state.app
    log = AuditLog(machine)
    bid = _ok(c.post("/api/v1/templates/builtin", json={"kind": "builtin_sheet", "editor": SHEET}, headers=h), 201)["id"]
    log.one("docs.template.create", object_kind="template", object_id=bid)
    assert context.principal() is None and context.actor() is None, "the test thread carries no request"

    token = context.set_actor("u_fixture")
    try:
        with context.as_tenant(machine["tenant"]):
            detail = save_live_sheet(app, bid, edited({"A2": {"v": "李四"}}), base_head=1, note="实时保存")
    finally:
        context.reset_actor(token)
    assert detail["head"] == 2 and not detail.get("unchanged"), detail.get("head")
    meta, text = log.one("docs.template.version", object_kind="template", object_id=bid, actor=("user", "u_fixture"), request_id=False)
    assert meta == {"version_no": 2, "previous_head": 1, "reason": "live_save", "slots": 1, "discard_live": False, "base_head_given": True}
    assert "李四" not in text and "实时保存" not in text
    assert _count(log.store, "docs.template.version", bid) == 1

    # The fallback itself, stated plainly: an actor and no principal is that person; neither is the system.
    token = context.set_actor("u_fixture")
    try:
        auth.audit_event(app.store, "docs.test.actor", object_kind="x", object_id="y")
    finally:
        context.reset_actor(token)
    assert context.actor() is None
    auth.audit_event(app.store, "docs.test.actor", object_kind="x", object_id="y")
    rows = log.docs_rows()
    assert [(r["action"], r["actor_kind"], r["actor_id"]) for r in rows] == \
        [("docs.test.actor", "user", "u_fixture"), ("docs.test.actor", "system", None)], rows


def test_a_failed_audit_undoes_the_field_import(client, machine, fixtures, monkeypatch):
    from synwevo_core import auth

    c, h, store = client, machine["h"], machine["store"]
    r, _ = _upload(c, h, fixtures)
    tid = _ok(r, 201)["id"]
    log = AuditLog(machine)                                          # past the upload row
    head = store.one("SELECT head FROM templates WHERE id=?", (tid,))["head"]
    versions = store.one("SELECT COUNT(*) AS n FROM template_versions WHERE template_id=?", (tid,))["n"]
    assert (head, versions) == (1, 1)

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")

    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", fail)
    r = c.post(f"/api/v1/templates/{tid}/fields", headers=h, json=TWO_FIELDS)
    assert r.status_code >= 500 and "synthetic" not in r.text, (r.status_code, r.text[:300])
    monkeypatch.setattr(auth, "audit_event", original)

    # the fields, the folded version and the head all went with the row
    assert store.one("SELECT head FROM templates WHERE id=?", (tid,))["head"] == head
    assert store.one("SELECT COUNT(*) AS n FROM template_versions WHERE template_id=?", (tid,))["n"] == versions
    assert _ok(c.get(f"/api/v1/templates/{tid}", headers=h))["fields"] == []
    log.nothing()
    assert store.one("SELECT COUNT(*) AS n FROM audit_events WHERE action LIKE 'docs.template.%' AND object_id=? "
                     "AND action!='docs.template.upload'", (tid,))["n"] == 0

    # with the writer back, the same import goes through once, with its one row
    out = _ok(c.post(f"/api/v1/templates/{tid}/fields", headers=h, json=TWO_FIELDS))
    assert out["template"] == head + 1 and len(out["created"]) == 2
    meta, _ = log.one("docs.template.fields.import", object_kind="template", object_id=tid)
    assert meta == {"count": 2, "version_no": head + 1, "previous_head": head, "folded": True}
