"""Table templates as shared work: two people's saves from the same version merge cell by cell,
field by field; a real collision on one cell, one field or one sheet's layout is refused with what
collided and resolved by choosing a side; the template's room carries presence (who, on which
cell) and tells everyone about saves, with no shared document to write to."""
from __future__ import annotations

import pytest
from pycrdt import YMessageType, read_message

from synwevo_docs.builtin_service import merge_sheets
from synwevo_docs.collab import decode_awareness

from .test_collab import PW, Peer


def sheet(name="S", cells=None, fields=None, cols=3, rows=3, merges=None):
    return {"name": name, "cols": [{"w": 100}] * cols, "rows": [{"h": 24}] * rows, "cells": cells or {}, "merges": merges or [], "fields": fields or []}


def doc(*sheets):
    return {"sheets": list(sheets)}


# ------------------------------------------------------------------ the merge, without a server
def test_cells_and_fields_touched_by_one_side_each_merge():
    base = doc(sheet(cells={"A1": {"v": "客户"}, "B1": {"v": "待填"}}, fields=[{"id": "f1", "kind": "cell", "name": "客户", "ref": "B1"}]))
    current = doc(sheet(cells={"A1": {"v": "客户名称"}, "B1": {"v": "待填"}}, fields=[{"id": "f1", "kind": "cell", "name": "客户", "ref": "B1"}]))
    incoming = doc(sheet(cells={"A1": {"v": "客户"}, "B1": {"v": "待填"}, "C1": {"v": "金额"}},
                         fields=[{"id": "f1", "kind": "cell", "name": "客户", "ref": "B1"}, {"id": "f2", "kind": "cell", "name": "金额", "ref": "C2"}]))
    merged, conflicts = merge_sheets(base, current, incoming)
    assert conflicts == []
    s = merged["sheets"][0]
    assert s["cells"] == {"A1": {"v": "客户名称"}, "B1": {"v": "待填"}, "C1": {"v": "金额"}}
    assert [f["id"] for f in s["fields"]] == ["f1", "f2"]
    # deleting a cell nobody else touched
    merged, conflicts = merge_sheets(base, current, doc(sheet(cells={"A1": {"v": "客户"}}, fields=base["sheets"][0]["fields"])))
    assert conflicts == [] and merged["sheets"][0]["cells"] == {"A1": {"v": "客户名称"}}


def test_the_same_cell_field_or_layout_changed_by_both_is_a_conflict_unless_forced():
    base = doc(sheet(cells={"A1": {"v": "x"}}, fields=[{"id": "f1", "kind": "cell", "name": "a", "ref": "B1"}]))
    current = doc(sheet(cells={"A1": {"v": "theirs"}}, fields=[{"id": "f1", "kind": "cell", "name": "a", "ref": "B1", "required": True}], cols=4))
    incoming = doc(sheet(cells={"A1": {"v": "mine"}}, fields=[{"id": "f1", "kind": "cell", "name": "a2", "ref": "B1"}], cols=5))
    merged, conflicts = merge_sheets(base, current, incoming)
    # padding the grid with default-sized columns on both sides is not a collision
    assert {(c["kind"], c.get("ref") or c.get("id") or c.get("part")) for c in conflicts} == {("cell", "A1"), ("field", "f1")}
    merged, conflicts = merge_sheets(base, current, incoming, {"cell:S!A1", "field:f1"})
    assert conflicts == []
    s = merged["sheets"][0]
    assert s["cells"]["A1"] == {"v": "mine"} and s["fields"][0]["name"] == "a2" and len(s["cols"]) == 5
    # one side widened the grid, the other added rows: both go through (what the two editors did in the browser proof)
    merged, conflicts = merge_sheets(base, doc(sheet(cells={"A1": {"v": "x"}}, fields=base["sheets"][0]["fields"], cols=6)),
                                     doc(sheet(cells={"A1": {"v": "x"}}, fields=base["sheets"][0]["fields"], rows=9)))
    assert conflicts == [] and len(merged["sheets"][0]["cols"]) == 6 and len(merged["sheets"][0]["rows"]) == 9
    # both padded rows (to different lengths) as they typed further down: the longer grid, no conflict;
    # the same column resized differently by both is a conflict on that part only
    fields = base["sheets"][0]["fields"]
    merged, conflicts = merge_sheets(base, doc(sheet(cells={"A1": {"v": "x"}}, fields=fields, rows=10)), doc(sheet(cells={"A1": {"v": "x"}}, fields=fields, rows=14)))
    assert conflicts == [] and len(merged["sheets"][0]["rows"]) == 14
    wide = sheet(cells={"A1": {"v": "x"}}, fields=fields); wide["cols"] = [{"w": 180}, {"w": 100}, {"w": 100}]
    wider = sheet(cells={"A1": {"v": "x"}}, fields=fields); wider["cols"] = [{"w": 240}, {"w": 100}, {"w": 100}, {"w": 100}]
    merged, conflicts = merge_sheets(base, doc(wide), doc(wider))
    assert conflicts == [{"kind": "layout", "sheet": "S", "part": "cols", "reason": "both_changed"}] and len(merged["sheets"][0]["cols"]) == 4
    assert merge_sheets(base, doc(wide), doc(wider), {"layout:S"})[0]["sheets"][0]["cols"][0] == {"w": 240}
    # the same change from both sides is not a conflict; a delete against an edit is
    assert merge_sheets(base, current, current)[1] == []
    assert merge_sheets(base, doc(sheet(cells={}, fields=base["sheets"][0]["fields"])), incoming)[1][0]["reason"] == "deleted_by_other"
    # whole sheets: added on one side, deleted on one side while untouched on the other, deleted against edited
    assert [s["name"] for s in merge_sheets(base, doc(*base["sheets"], sheet("T")), doc(*base["sheets"], sheet("U")))[0]["sheets"]] == ["S", "T", "U"]
    assert merge_sheets(base, doc(), base)[0]["sheets"] == []
    assert merge_sheets(base, doc(), incoming)[1] == [{"kind": "sheet", "sheet": "S", "reason": "deleted_by_other"}]


# ------------------------------------------------------------------ over HTTP with real sessions
SHEET = {"sheets": [{"name": "结果", "cols": [{"w": 100}] * 3, "rows": [{"h": 24}] * 3, "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}}, "merges": [],
                     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]}]}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
    from synwevo_docs.api.app import create_app

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_COLLAB_RECHECK_S", "0.2")
    app = create_app(str(tmp_path / "data"), start_jobs=False)
    store = app.state.app.store
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    sessions = Sessions(store)

    def person(email, wid=None, role=None):
        u = ident.create_user(email, PW, status="active", display_name=email.split("@")[0].title())
        if wid and role:
            ident.add_member(wid, u["id"], role)
        return u

    owner = person("owner@example.com")
    wid = ident.workspaces_for(owner["id"])[0]["id"]
    editor = person("editor@example.com", wid, "editor")
    viewer = person("viewer@example.com", wid, "viewer")

    def client_for(user):
        raw, _ = sessions.create(user["id"], workspace_id=wid, amr="password")
        c = TestClient(app)
        c.cookies.set(COOKIE_NAME, raw)
        c.cookies.set(CSRF_COOKIE, "t")
        c.headers[CSRF_HEADER] = "t"
        return c

    with TestClient(app):
        co = client_for(owner)
        t = co.post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": SHEET, "name": "表格"})
        assert t.status_code == 200, t.text
        yield {"app": app, "store": store, "ident": ident, "wid": wid, "tid": t.json()["id"], "owner": owner, "editor": editor, "viewer": viewer, "client_for": client_for, "co": co}


def edited(cells: dict, fields=None):
    s = {**SHEET["sheets"][0], "cells": {**SHEET["sheets"][0]["cells"], **cells}}
    if fields is not None:
        s["fields"] = fields
    return {"sheets": [s]}


def test_two_editors_saves_from_one_version_both_land_and_a_collision_is_explained(world):
    co, ce, tid = world["co"], world["client_for"](world["editor"]), world["tid"]
    r = co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A2": {"v": "张三"}}), "base_version": 1})
    assert r.status_code == 200 and r.json()["version"]["no"] == 2, r.text
    # the editor, still on v1, filled a different cell and added a field
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"C1": {"v": "金额"}}, SHEET["sheets"][0]["fields"] + [{"id": "amount", "kind": "cell", "name": "金额", "ref": "C2"}]), "base_version": 1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["version"]["no"] == 3 and out["merged_with"] == 2 and "已与第 2 版合并" in out["version"]["note"]
    cells = out["version"]["editor"]["sheets"][0]["cells"]
    assert cells["A2"] == {"v": "张三"} and cells["C1"] == {"v": "金额"}, "both people's cells are in v3"
    assert [s["name"] for s in out["version"]["slots"]] == ["客户", "金额"], "the generated workbook carries both fields"
    # the same cell from one version on both sides: refused with the cell, then kept by choice
    assert co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A2": {"v": "张三"}, "C1": {"v": "金额（元）"}}, out["version"]["editor"]["sheets"][0]["fields"]), "base_version": 3}).status_code == 200
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A2": {"v": "张三"}, "C1": {"v": "金额（万元）"}}, out["version"]["editor"]["sheets"][0]["fields"]), "base_version": 3})
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["head"] == 4 and d["conflicts"] == [{"kind": "cell", "sheet": "结果", "ref": "C1", "reason": "both_changed"}]
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A2": {"v": "张三"}, "C1": {"v": "金额（万元）"}}, out["version"]["editor"]["sheets"][0]["fields"]), "base_version": 3, "force_ids": ["cell:结果!C1"]})
    assert r.status_code == 200 and r.json()["version"]["editor"]["sheets"][0]["cells"]["C1"] == {"v": "金额（万元）"}
    # saving exactly what is already saved from an old version adds no version
    head = co.get(f"/api/templates/{tid}").json()
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": head["version"]["editor"], "base_version": 3})
    assert r.status_code == 200 and r.json().get("unchanged") is True and r.json()["version"]["no"] == head["version"]["no"]
    # a base version that never existed, and a document template, keep the whole-document refusal
    assert ce.put(f"/api/templates/{tid}/builtin", json={"editor": SHEET, "base_version": 99}).status_code == 409
    assert ce.put(f"/api/templates/{tid}/builtin", json={"editor": SHEET, "force_ids": "x"}).status_code == 400


def test_the_table_room_carries_presence_and_version_frames_but_no_document(world):
    tid = world["tid"]
    a = Peer(world["client_for"](world["owner"]), tid)
    a.hello = a.connect()
    assert a.hello["kind"] == "template" and a.hello["writable"] is False and a.hello["head"] == 1
    v = Peer(world["client_for"](world["viewer"]), tid)
    v.hello = v.connect()
    assert v.hello["readonly"] is True and [p["name"] for p in v.hello["peers"]] == ["Owner"]
    a.recv_meta("joined")
    a.send_awareness(601, 1, {"user": {"id": world["owner"]["id"], "name": "Owner"}, "focus": {"kind": "cell", "id": "结果!B2"}})
    frame = v.recv(want="awareness")
    assert decode_awareness(read_message(frame[1:]))[0][2]["focus"] == {"kind": "cell", "id": "结果!B2"}
    # a save by the owner tells the viewer which version is current now, and who saved it
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A2": {"v": "李四"}}), "base_version": 1})
    assert r.status_code == 200
    head = v.recv_meta("head")
    assert head["head"] == 2 and head["reason"] == "save" and head["by"] == "Owner"
    # document frames are ignored here without a refusal notice
    a.ws.send_bytes(bytes([YMessageType.SYNC, 2]) + b"\x02\x00\x00")
    a.send_awareness(601, 2, None)
    assert v.recv(want="awareness") is not None
    assert not any(x.get("type") == "refused" for x in a.meta)
    assert world["store"].one("SELECT 1 AS x FROM collab_docs WHERE room=?", (f"template:{tid}",)) is None, "nothing is persisted for a presence-only room"
    a.close(); v.close()
    assert world["store"].one("SELECT 1 AS x FROM collab_docs WHERE room=?", (f"template:{tid}",)) is None


def test_edits_that_merge_into_a_table_the_template_cannot_hold_are_a_choice_not_an_error(world):
    """CI-116: one person added a region field B2:C3, another merged B2:C2 from the same version.
    Each was fine alone and the cell-by-cell merge accepted both, but together they are a region
    over merged cells, which the template cannot hold: the second save got a raw 400 the conflict
    dialog could not resolve. It is the stale-version choice now, with the reason."""
    co, ce, tid = world["co"], world["client_for"](world["editor"]), world["tid"]
    base = SHEET["sheets"][0]
    region = {**base, "fields": base["fields"] + [{"id": "area", "kind": "region", "name": "区域", "ref": "B2:C3"}]}
    assert co.put(f"/api/templates/{tid}/builtin", json={"editor": {"sheets": [region]}, "base_version": 1}).status_code == 200
    merged = {**base, "merges": ["B2:C2"]}
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": {"sheets": [merged]}, "base_version": 1})
    assert r.status_code == 409 and r.json()["error"] == "stale_template", r.text
    d = r.json()["detail"]
    assert d["head"] == 2 and "合并单元格" in d["merge_error"] and "合并单元格" in r.json()["message"]
    # saving over it (against the head the refusal named) keeps this person's table
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": {"sheets": [merged]}, "base_version": 2})
    assert r.status_code == 200 and r.json()["version"]["no"] == 3
    assert r.json()["version"]["editor"]["sheets"][0]["merges"] == ["B2:C2"]
