"""The live state of a table template (docs-sheet-live.md §2–§3, B1 step ①), without a server:
the state renders back to exactly the model it was seeded from; every operation is checked against
the generator's rules before it applies and refused with a reason otherwise; the same object
written by someone else since the sender looked is a conflict, never a silent overwrite; a group
applies whole or not at all; a message delivered twice applies once; a version written outside the
room is folded in with the outside version winning on the same object; and random interleaved
edits from three people never leave a table the generator cannot build.

TEST FIXTURES: the synthetic table models of test_sheet_collab, test_api_builtin, test_formats,
test_collab and test_engine_untrusted (copied where they live inside a test function), the
SheetEditor's new-sheet shape, and randomly generated operations from fixed seeds."""
from __future__ import annotations

import json
import random

import pytest

from synwevo_docs import collab_sheet as cs
from synwevo_docs.builtin_service import _build
from synwevo_docs.collab_sheet import (SeedError, apply, apply_patch, dirty, mark_saved, normalize, project, reconcile,
                                       seed_state, sheet_invariants, snapshot)
from synwevo_docs.editor_contract import validate_editor
from synwevo_core.errors import ServiceError

from .test_api_builtin import SHEET as API_SHEET
from .test_sheet_collab import SHEET as COLLAB_SHEET, doc, sheet


def dumps(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


# ------------------------------------------------------------------ the corpus
FORMATS_SHEET = {"sheets": [{"name": "报表", "cols": [{"w": 120}] * 6, "rows": [{"h": 28}] * 12,        # test_formats
                             "cells": {"A1": {"v": "内置表格模板", "s": {"b": 1, "sz": 14, "bg": "#DDEBF7"}}, "A3": {"v": "标题", "s": {"bd": 1}}, "B3": {"v": "", "s": {"bd": 1, "nf": "@"}}},
                             "merges": ["A1:F1"],
                             "fields": [{"id": "fT", "name": "标题", "kind": "cell", "ref": "B3", "required": True},
                                        {"id": "fR", "name": "区域", "kind": "region", "ref": "B5:E7"}]}]}
TYPED_SHEET = {"sheets": [{"name": "类型", "cells": {                                                       # test_api_builtin
    "A1": {"v": "=1+2", "t": "text"}, "B1": {"v": "=1+2", "t": "auto"}, "A2": {"v": "00123", "t": "text"}, "B2": {"v": "1e3", "t": "number"}}}]}
COLLAB_S = {"sheets": [{"name": "S", "cells": {"A1": {"v": "x"}}, "fields": []}]}                           # test_collab
NEW_SHEET = {"sheets": [{"name": "Sheet1", "cols": [{"w": 100}] * 10, "rows": [{"h": 24}] * 30,            # SheetEditor newSheet
                         "cells": {}, "merges": [], "fields": []}]}
_base = COLLAB_SHEET["sheets"][0]
CI116_REGION = {"sheets": [{**_base, "fields": _base["fields"] + [{"id": "area", "kind": "region", "name": "区域", "ref": "B2:C3"}]}]}
CI116_MERGED = {"sheets": [{**_base, "merges": ["B2:C2"]}]}
_f1 = [{"id": "f1", "kind": "cell", "name": "a", "ref": "B1"}]
_wide = sheet(cells={"A1": {"v": "x"}}, fields=_f1)
_wide["cols"] = [{"w": 180}, {"w": 100}, {"w": 100}]
REPEAT_SHEET = {"sheets": [{"name": "明细", "cols": [{"w": 100}] * 4, "rows": [{"h": 24}] * 6,
                            "cells": {"A1": {"v": "名称"}, "B1": {"v": "数量"}, "C1": {"v": "=SUM(B2:B2)"}},
                            "merges": ["C3:D4", "A5:B5"],
                            "fields": [{"id": "items", "name": "明细", "kind": "repeat", "row": 1, "c1": 0, "c2": 1, "empty_policy": "remove_row"},
                                       {"id": "who", "name": "经办", "kind": "cell", "ref": "C3"},
                                       {"name": "无 id 的字段", "ref": "A6"}]},
                           {"name": "第二页", "cells": {"B7": {"v": 3.5, "t": "number"}, "C9": {"v": True}}}]}

CORPUS = {
    "sheet_collab.SHEET": COLLAB_SHEET,
    "api_builtin.SHEET": API_SHEET,
    "formats": FORMATS_SHEET,
    "typed": TYPED_SHEET,
    "collab": COLLAB_S,
    "editor_new_sheet": NEW_SHEET,
    "ci116_region": CI116_REGION,
    "ci116_merged": CI116_MERGED,
    "merge_base": doc(sheet(cells={"A1": {"v": "客户"}, "B1": {"v": "待填"}}, fields=[{"id": "f1", "kind": "cell", "name": "客户", "ref": "B1"}])),
    "merge_incoming": doc(sheet(cells={"A1": {"v": "客户"}, "B1": {"v": "待填"}, "C1": {"v": "金额"}},
                                fields=[{"id": "f1", "kind": "cell", "name": "客户", "ref": "B1"}, {"id": "f2", "kind": "cell", "name": "金额", "ref": "C2"}])),
    "merge_current_cols4": doc(sheet(cells={"A1": {"v": "theirs"}}, fields=[{**_f1[0], "required": True}], cols=4)),
    "merge_wide": doc(_wide),
    "multi_sheet": doc(sheet(cells={"A1": {"v": "x"}}, fields=_f1), sheet("T"), sheet("U")),
    "no_sheets": doc(),
    "empty_model": {},
    "repeat_and_merges": REPEAT_SHEET,
}

INVALID = {
    # test_api_builtin: refused by the editor contract
    "number_text": ({"sheets": [{"cells": {"A1": {"v": "金额待定", "t": "number"}}}]}, "invalid"),
    "number_overflow": ({"sheets": [{"cells": {"A1": {"v": "1e500", "t": "number"}}}]}, "invalid"),
    "bad_alignment": ({"sheets": [{"cells": {"A1": {"v": "文字", "s": {"ha": "nonsense"}}}}]}, "invalid"),
    "same_field_id": ({"sheets": [{"fields": [{"id": "same", "ref": "A1"}, {"id": "same", "ref": "A2"}]}]}, "invalid"),
    # test_engine_untrusted: a merge the generator refuses to materialise
    "huge_merge": ({"sheets": [{"name": "S", "merges": ["A1:XFD1048576"]}]}, "too_large"),
    # CI-116: each side fine alone, together a region over merged cells
    "ci116_both": ({"sheets": [{**_base, "merges": ["B2:C2"], "fields": CI116_REGION["sheets"][0]["fields"]}]}, "merged"),
}


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_every_model_renders_back_exactly_and_still_builds(name):
    model = CORPUS[name]
    snapshot_before = dumps(model)
    assert sheet_invariants(model) == []
    # normalize is what validate_editor returns for every model the editor and the API write
    assert dumps(normalize(model)) == dumps(validate_editor("builtin_sheet", json.loads(json.dumps(model))))
    st = seed_state(model, epoch="corpus")
    out = project(st)
    assert out == normalize(model) and dumps(out) == dumps(normalize(model))
    assert not dirty(st) and st["seq"] == 0
    assert dumps(model) == snapshot_before, "seeding never touches the caller's model"
    # plain JSON: survives a round trip through storage and still renders the same
    again = json.loads(json.dumps(st))
    assert dumps(project(again)) == dumps(out)
    _build("builtin_sheet", out)


def test_seeding_is_deterministic_per_epoch_and_ids_are_short_base36():
    a, b, c = (seed_state(REPEAT_SHEET, epoch=e) for e in ("one", "one", "two"))
    assert dumps(a) == dumps(b)
    assert a["content"]["order"] != c["content"]["order"]
    for sid in a["content"]["order"]:
        sh = a["content"]["sheets"][sid]
        for x in [sid, *sh["rows"], *sh["cols"], *sh["merges"]]:
            assert cs._ID.fullmatch(x)
        assert len(set(sh["rows"]) | set(sh["cols"])) == len(sh["rows"]) + len(sh["cols"])
    # the basis records position → id at seeding; cells beyond a sheet's declared rows get ids too
    first = a["content"]["sheets"][a["content"]["order"][0]]
    assert a["basis"]["rows"][a["content"]["order"][0]] == first["rows"] and first["nr"] == 6
    second = a["content"]["sheets"][a["content"]["order"][1]]
    assert second["nr"] == 0 and len(second["rows"]) == 9 and len(second["cols"]) == 3


def test_a_model_the_room_cannot_hold_is_refused_at_seeding_with_what_is_wrong():
    for name, (model, code) in INVALID.items():
        issues = sheet_invariants(model)
        assert issues and issues[0]["code"] == code, (name, issues)
        with pytest.raises(SeedError) as e:
            seed_state(model)
        assert e.value.issues == issues
        with pytest.raises(ServiceError):
            _build("builtin_sheet", model)
    # non-canonical references are what the generator reads them as
    odd = {"sheets": [{"name": "S", "cells": {"b2": {"v": 1}, "$C$3": {"v": 2}}, "merges": ["d5:c4"],
                       "fields": [{"id": "r", "kind": "region", "ref": "f9:e8"}]}]}
    assert sheet_invariants(odd) == []
    out = project(seed_state(odd))
    assert out == normalize(odd) and set(out["sheets"][0]["cells"]) == {"B2", "C3"}
    assert out["sheets"][0]["merges"] == ["C4:D5"] and out["sheets"][0]["fields"][0]["ref"] == "E8:F9"
    # the live state's own bound: ids for every row and column it has to address
    with pytest.raises(SeedError) as e:
        seed_state({"sheets": [{"name": "S", "cells": {"A300000": {"v": 1}}}]})
    assert e.value.issues[0]["code"] == "too_large" and e.value.issues[0]["what"] == "grid"


# ------------------------------------------------------------------ invariants, one code at a time
def one(**sheet_):
    return {"sheets": [{"name": "S", **sheet_}]}


INVARIANT_CASES = [
    ("invalid", one(cells={"A0": {"v": 1}}), True),
    ("invalid", one(merges=["A1"]), False),
    ("invalid", one(fields=[{"id": "a", "kind": "cell", "ref": "A1:B2"}]), True),
    ("invalid", one(fields=[{"id": "a", "kind": "repeat", "row": 1, "c1": 3, "c2": 1}]), False),
    ("invalid", one(cols=[{"w": "wide"}]), True),
    ("too_large", one(cells={"A1": {"v": "x" * (cs.MAX_VALUE + 1)}}), False),
    ("too_large", one(cells={"A1": {"v": "=" + "1+" * 4100 + "1"}}), False),
    ("too_large", one(merges=["A1:Z5000"]), True),
    ("sheet_name_taken", {"sheets": [{"name": "A"}, {"name": "A "}]}, True),
    ("merge_overlap", one(merges=["A1:B2", "B2:C3"]), False),
    ("field_overlap", one(fields=[{"id": "a", "kind": "region", "ref": "A1:B2"}, {"id": "b", "kind": "cell", "ref": "B2"}]), True),
    ("merged_inner", one(merges=["A1:B1"], fields=[{"id": "a", "kind": "cell", "ref": "B1"}]), True),
    ("merged", one(merges=["C3:D3"], fields=[{"id": "a", "kind": "region", "ref": "A1:C3"}]), True),
    ("vertical_merge", one(merges=["F1:F3"], fields=[{"id": "a", "kind": "repeat", "row": 1, "c1": 0, "c2": 2}]), True),
    ("duplicate_repeat", one(fields=[{"id": "a", "kind": "repeat", "row": 1, "c1": 0, "c2": 1}, {"id": "b", "kind": "repeat", "row": 1, "c1": 3, "c2": 4}]), True),
]


@pytest.mark.parametrize("code,model,generator_refuses", INVARIANT_CASES, ids=[f"{c}-{i}" for i, (c, _m, _g) in enumerate(INVARIANT_CASES)])
def test_each_invariant_has_its_refusal_code_and_matches_the_generator(code, model, generator_refuses):
    issues = sheet_invariants(model)
    assert [i["code"] for i in issues] == [code], issues
    assert isinstance(issues[0]["index"], int)
    if generator_refuses:
        with pytest.raises(ServiceError):
            _build("builtin_sheet", model)
    # the fields' own order does not hide an overlap: the later field is the one named
    if code == "field_overlap":
        assert issues[0]["field"] == "b"
    # a merge that merely touches the allowed cell is fine: the field sits on its top-left corner
    assert sheet_invariants(one(merges=["A1:B1"], fields=[{"id": "a", "kind": "cell", "ref": "A1"}])) == []


# ------------------------------------------------------------------ operations
class Room:
    """One seeded state and the people typing into it (each person one tab unless said otherwise)."""

    def __init__(self, model=FORMATS_SHEET, epoch="t"):
        self.st = seed_state(model, epoch=epoch)
        self.n: dict = {}
        self.mirror = json.loads(json.dumps(self.st["content"]))

    def do(self, op, by="ann", *, client=None, expect="now"):
        client = client or f"{by}-1"
        self.n[client] = self.n.get(client, 0) + 1
        seen = self.st["seq"] if expect == "now" else expect
        r = apply(self.st, op, by=by, client=client, n=self.n[client], expect=seen)
        if r["ok"] and not r.get("duplicate"):
            apply_patch(self.mirror, r["patch"])
            assert self.mirror == self.st["content"], "a client applying the patch sees the server's state"
        assert sheet_invariants(project(self.st)) == []
        return r

    def ok(self, op, by="ann", **kw):
        r = self.do(op, by, **kw)
        assert r["ok"], r
        return r

    def no(self, op, code, by="ann", **kw):
        before = dumps({k: self.st[k] for k in ("content", "seq", "kseq", "base", "ctr")})
        r = self.do(op, by, **kw)
        assert not r["ok"] and r["code"] == code, r
        assert dumps({k: self.st[k] for k in ("content", "seq", "kseq", "base", "ctr")}) == before, "a refusal changes nothing"
        return r["detail"]

    @property
    def sid(self):
        return self.st["content"]["order"][0]

    def r(self, i, sid=None):
        return self.st["content"]["sheets"][sid or self.sid]["rows"][i]

    def c(self, j, sid=None):
        return self.st["content"]["sheets"][sid or self.sid]["cols"][j]

    def at(self, ref, sid=None):
        r, c = cs.tabular.parse_cell(ref)
        return {"sheet": sid or self.sid, "row": self.r(r, sid), "col": self.c(c, sid)}

    def rng(self, a, b, sid=None):
        (r1, c1), (r2, c2) = cs.tabular.parse_cell(a), cs.tabular.parse_cell(b)
        return [self.r(r1, sid), self.c(c1, sid), self.r(r2, sid), self.c(c2, sid)]

    def sheet(self, i=0):
        return project(self.st)["sheets"][i]


def test_set_and_clear_write_a_cells_value_and_type_as_one_and_undo_restores_it():
    room = Room()
    r = room.ok({"op": "set", **room.at("C3"), "v": "42", "t": "number"})
    assert r["seq"] == 1 and room.sheet()["cells"]["C3"] == {"v": "42", "t": "number"}
    # a set replaces value and type together; style stays
    room.ok({"op": "set", **room.at("B3"), "v": "标题值"})
    assert room.sheet()["cells"]["B3"] == {"v": "标题值", "s": {"bd": 1, "nf": "@"}}
    r = room.ok({"op": "set", **room.at("C3"), "v": "abc"})
    assert room.sheet()["cells"]["C3"] == {"v": "abc"}
    # clearing the last thing in a cell removes it; the same value again changes nothing (no seq)
    room.ok({"op": "clear", **room.at("C3")})
    assert "C3" not in room.sheet()["cells"]
    seq = room.st["seq"]
    r = room.ok({"op": "clear", **room.at("C3")})
    assert r["seq"] == seq and r["patch"] == [] and room.st["seq"] == seq
    # typing then clearing a cell leaves nothing unsaved
    fresh = Room()
    fresh.ok({"op": "set", **fresh.at("F12"), "v": "临时"})
    assert dirty(fresh.st)
    fresh.ok({"op": "clear", **fresh.at("F12")})
    assert not dirty(fresh.st) and project(fresh.st) == normalize(FORMATS_SHEET)
    # before undoes a patch exactly
    room2 = Room()
    content0 = json.loads(json.dumps(room2.st["content"]))
    r = room2.ok({"op": "tx", "ops": [{"op": "set", **room2.at("A3"), "v": "新标题"}, {"op": "style", **room2.at("A3"), "s": {"i": True}},
                                      {"op": "append_rows", "sheet": room2.sid, "count": 2, "h": 24}]})
    assert apply_patch(json.loads(json.dumps(room2.st["content"])), r["before"]) == content0
    # the contract's own checks and the size limits
    assert room.no({"op": "set", **room.at("A2"), "v": "文字", "t": "number"}, "invalid")["reason"] == "cell"
    assert room.no({"op": "set", **room.at("A2"), "v": "x" * (cs.MAX_VALUE + 1)}, "too_large")["what"] == "value"
    assert room.no({"op": "set", **room.at("A2"), "v": "=" + "A1+" * 3000 + "1"}, "too_large")["what"] == "formula"
    room.ok({"op": "set", **room.at("A2"), "v": "=" + "A1+" * 3000 + "1", "t": "text"})     # text that only looks like a formula
    assert room.no({"op": "set", "sheet": room.sid, "row": "nope0000", "col": room.c(0), "v": 1}, "not_found")["what"] == "row"
    assert room.no({"op": "set", **room.at("A2"), "v": 1, "colour": 2}, "invalid")["reason"] == "unknown_key"
    assert room.no({"op": "paint", "sheet": room.sid}, "invalid")["reason"] == "op"


def test_style_is_last_writer_wins_per_attribute_and_attributed():
    room = Room()
    room.ok({"op": "style", **room.at("A3"), "s": {"b": 1, "color": "#ff0000"}}, "ann")
    # bob had not seen ann's change: no conflict for styles, each attribute to its last writer
    room.ok({"op": "style", **room.at("A3"), "s": {"color": "#0000ff", "ha": "center"}}, "bob", expect=0)
    assert room.sheet()["cells"]["A3"] == {"v": "标题", "s": {"bd": 1, "b": 1, "color": "#0000ff", "ha": "center"}}
    ck = f"{room.r(2)}:{room.c(0)}"
    ks = room.st["kseq"]
    assert ks[f"s/{room.sid}/{ck}/b"][1] == "ann" and ks[f"s/{room.sid}/{ck}/color"][1] == "bob"
    room.ok({"op": "style", **room.at("A3"), "s": {"b": None, "bd": None}}, "ann", expect=0)
    assert room.sheet()["cells"]["A3"]["s"] == {"color": "#0000ff", "ha": "center"}
    # style on an empty cell creates it; removing the last style removes it again
    room.ok({"op": "style", **room.at("F9"), "s": {"bg": "#ffffff"}})
    assert room.sheet()["cells"]["F9"] == {"s": {"bg": "#ffffff"}}
    room.ok({"op": "style", **room.at("F9"), "s": {"bg": None}})
    assert "F9" not in room.sheet()["cells"]
    assert room.no({"op": "style", **room.at("A1"), "s": {"ha": "sideways"}}, "invalid")["reason"] == "cell"
    assert room.no({"op": "style", **room.at("A1"), "s": {"font": "x"}}, "invalid")["reason"] == "style"
    assert room.no({"op": "style", **room.at("A1"), "s": {"nf": "0" * 300}}, "invalid")["reason"] == "number_format"


def test_row_heights_and_column_widths():
    room = Room()
    room.ok({"op": "size", "sheet": room.sid, "row": room.r(0), "h": 40})
    room.ok({"op": "size", "sheet": room.sid, "col": room.c(2), "w": 200}, "bob", expect=0)
    s = room.sheet()
    assert s["rows"][0] == {"h": 40} and s["cols"][2] == {"w": 200} and len(s["rows"]) == 12
    room.ok({"op": "size", "sheet": room.sid, "row": room.r(0), "h": None})
    assert room.sheet()["rows"][0] == {}
    assert room.no({"op": "size", "sheet": room.sid, "row": room.r(1), "h": 0}, "invalid")["reason"] == "size"
    assert room.no({"op": "size", "sheet": room.sid, "row": room.r(1), "col": room.c(1), "h": 20}, "invalid")["reason"] == "size_target"
    # a row that holds cells but is not part of the sheet's row list has no height of its own yet
    wide = Room(REPEAT_SHEET)
    second = wide.st["content"]["order"][1]
    assert wide.no({"op": "size", "sheet": second, "row": wide.r(6, second), "h": 30}, "invalid")["reason"] == "not_in_grid"


def test_appending_rows_and_columns_keeps_everyone_s_ids(monkeypatch):
    room = Room()
    mine = ["aaaaaaa1", "aaaaaaa2"]
    r = room.ok({"op": "append_rows", "sheet": room.sid, "count": 2, "ids": mine, "h": 24})
    assert room.st["content"]["sheets"][room.sid]["rows"][12:] == mine and len(room.sheet()["rows"]) == 14
    assert r["patch"][0] == {"p": ["sheets", room.sid, "rows"], "push": mine}
    # bob appended at the same time from the same starting point: both land, his after mine
    theirs = ["bbbbbbb1", "bbbbbbb2", "bbbbbbb3"]
    room.ok({"op": "append_rows", "sheet": room.sid, "count": 3, "ids": theirs, "h": 24}, "bob", expect=0)
    assert room.st["content"]["sheets"][room.sid]["rows"][14:] == theirs
    # and his ids work: he types into the row he made
    room.ok({"op": "set", "sheet": room.sid, "row": theirs[0], "col": room.c(0), "v": "鲍勃"}, "bob")
    assert room.sheet()["cells"]["A15"] == {"v": "鲍勃"}
    room.ok({"op": "append_cols", "sheet": room.sid, "count": 1, "w": 100})
    assert len(room.sheet()["cols"]) == 7
    assert room.no({"op": "append_rows", "sheet": room.sid, "count": 1, "ids": [mine[0]]}, "id_taken")["id"] == mine[0]
    assert room.no({"op": "append_rows", "sheet": room.sid, "count": 1, "ids": ["NOTBASE3"]}, "invalid")["reason"] == "id"
    assert room.no({"op": "append_rows", "sheet": room.sid, "count": cs.MAX_APPEND + 1}, "invalid")["reason"] == "count"
    monkeypatch.setattr(cs, "MAX_COLS", 8)                   # Excel's 16384, made small
    assert room.no({"op": "append_cols", "sheet": room.sid, "count": 2, "w": 100}, "too_large")["what"] == "cols"
    monkeypatch.undo()
    # a sheet whose cells run past its declared rows: appending declares the rows that already have ids first
    wide = Room(REPEAT_SHEET)
    second = wide.st["content"]["order"][1]
    before = list(wide.st["content"]["sheets"][second]["rows"])
    wide.ok({"op": "append_rows", "sheet": second, "count": 10, "ids": ["ccccccc1"]})
    rows = wide.st["content"]["sheets"][second]["rows"]
    assert rows[:9] == before and rows[9] == "ccccccc1" and len(rows) == 10 and len(wide.sheet(1)["rows"]) == 10


def test_merging_checks_overlaps_and_fields_and_says_whose_they_are():
    room = Room()                        # A1:F1 merged; field fT on B3; region fR on B5:E7
    r = room.ok({"op": "merge", "sheet": room.sid, "range": room.rng("A9", "B10")}, "bob")
    mid = r["patch"][0]["p"][1]
    assert room.sheet()["merges"] == ["A1:F1", "A9:B10"]
    d = room.no({"op": "merge", "sheet": room.sid, "range": room.rng("B10", "C11")}, "merge_overlap", "ann")
    assert d["want"] == "B10:C11" and d["merge"] == mid and d["range"] == "A9:B10" and d["by"] == "bob"
    # merging over an existing range replaces it when the sender saw it
    room.ok({"op": "merge", "sheet": room.sid, "range": room.rng("A9", "C11"), "replaces": [mid]}, "ann")
    assert room.sheet()["merges"] == ["A1:F1", "A9:C11"]
    # the generator's field rules from the merge's side
    d = room.no({"op": "merge", "sheet": room.sid, "range": room.rng("A3", "B3")}, "merged_inner")
    assert d["field"] == "fT" and d["name"] == "标题" and d["ref"] == "B3"
    assert room.no({"op": "merge", "sheet": room.sid, "range": room.rng("E7", "F8")}, "merged")["field"] == "fR"
    room.ok({"op": "merge", "sheet": room.sid, "range": room.rng("B3", "C3")})          # the field is the top-left: fine
    rep = Room(REPEAT_SHEET)
    assert rep.no({"op": "merge", "sheet": rep.sid, "range": rep.rng("D1", "D2")}, "vertical_merge")["field"] == "items"
    assert room.no({"op": "merge", "sheet": room.sid, "range": room.rng("D11", "D11")}, "invalid")["reason"] == "single_cell_merge"
    room.ok({"op": "append_rows", "sheet": room.sid, "count": 5000})
    room.ok({"op": "append_cols", "sheet": room.sid, "count": 30})
    assert room.no({"op": "merge", "sheet": room.sid, "range": room.rng("A20", "AF5000")}, "too_large")["what"] == "merge"
    # unmerging
    first = room.st["content"]["sheets"][room.sid]["merges"][0]
    room.ok({"op": "unmerge", "sheet": room.sid, "id": first})
    assert "A1:F1" not in room.sheet()["merges"]
    assert room.no({"op": "unmerge", "id": first}, "not_found")["what"] == "merge"


def test_fields_are_added_changed_moved_and_removed_under_the_generators_rules():
    room = Room()
    room.ok({"op": "field_add", **room.at("D3"), "field": {"id": "amount", "name": "金额", "kind": "cell", "required": True}})
    room.ok({"op": "field_add", "sheet": room.sid, "field": {"id": "rows", "name": "明细", "kind": "repeat", "empty_policy": "remove_row"},
             "row": room.r(9), "c1": room.c(0), "c2": room.c(3)})
    fields = room.sheet()["fields"]
    assert fields[2] == {"id": "amount", "name": "金额", "kind": "cell", "ref": "D3", "required": True}
    assert fields[3] == {"id": "rows", "name": "明细", "kind": "repeat", "row": 9, "c1": 0, "c2": 3, "empty_policy": "remove_row"}
    d = room.no({"op": "field_add", **room.at("C6"), "field": {"id": "x", "name": "X", "kind": "cell"}}, "field_overlap")
    assert d["field"] == "fR" and d["ref"] == "C6"
    assert room.no({"op": "field_add", **room.at("C1"), "field": {"id": "x", "name": "X", "kind": "cell"}}, "merged_inner")["merge"]
    assert room.no({"op": "field_add", "sheet": room.sid, "range": room.rng("A1", "B2"), "field": {"id": "x", "name": "X", "kind": "region"}}, "merged")["range"] == "A1:F1"
    assert room.no({"op": "field_add", "sheet": room.sid, "field": {"id": "x", "name": "X", "kind": "repeat"}, "row": room.r(9), "c1": room.c(5), "c2": room.c(5)},
                   "duplicate_repeat")["field"] == "rows"
    room.ok({"op": "merge", "sheet": room.sid, "range": room.rng("F11", "F12")}, "bob")
    assert room.no({"op": "field_add", "sheet": room.sid, "field": {"id": "y", "name": "Y", "kind": "repeat"}, "row": room.r(10), "c1": room.c(0), "c2": room.c(1)},
                   "vertical_merge", "ann")["by"] == "bob"
    assert room.no({"op": "field_add", **room.at("F2"), "field": {"id": "fT", "name": "again", "kind": "cell"}}, "id_taken")["id"] == "fT"
    assert room.no({"op": "field_add", **room.at("F2"), "field": {"id": "1bad", "name": "x", "kind": "cell"}}, "invalid")["reason"] == "field"
    assert room.no({"op": "field_add", **room.at("F2"), "field": {"id": "z", "name": "x", "kind": "repeat"}}, "invalid")["reason"] == "unknown_key"
    # change attributes, move, remove
    room.ok({"op": "field_set", "field": "fT", "set": {"name": "报表标题", "required": None}})
    assert room.sheet()["fields"][0] == {"id": "fT", "name": "报表标题", "kind": "cell", "ref": "B3"}
    assert room.no({"op": "field_set", "field": "fT", "set": {"empty_policy": "nonsense"}}, "invalid")["reason"] == "field"
    room.ok({"op": "field_move", "field": "fR", "range": room.rng("B11", "C12")})
    assert room.sheet()["fields"][1]["ref"] == "B11:C12"
    room.ok({"op": "field_move", "field": "amount", "kind": "region", "range": room.rng("E3", "F4")})
    assert room.sheet()["fields"][2] == {"id": "amount", "name": "金额", "kind": "region", "ref": "E3:F4", "required": True}
    assert room.no({"op": "field_move", "field": "amount", "range": room.rng("B11", "B11")}, "field_overlap")["field"] == "fR"
    room.ok({"op": "field_del", "field": "rows"})
    assert [f["id"] for f in room.sheet()["fields"]] == ["fT", "fR", "amount"]
    assert room.no({"op": "field_del", "field": "rows"}, "not_found")["what"] == "field"
    # a field without an id (API models) keeps its place and is addressed by its server key
    rep = Room(REPEAT_SHEET)
    key = rep.st["content"]["sheets"][rep.sid]["fields"][2]
    assert key.startswith("~")
    rep.ok({"op": "field_set", "field": key, "set": {"name": "经手人"}})
    assert rep.sheet()["fields"][2] == {"name": "经手人", "ref": "A6"}
    _build("builtin_sheet", project(room.st))


def test_sheets_are_added_renamed_deleted_to_a_tombstone_and_restored():
    room = Room()
    room.ok({"op": "sheet_add", "id": "sheet002", "name": "Sheet2", "rows": 30, "cols": 10, "h": 24, "w": 100})
    assert room.sheet(1) == {"name": "Sheet2", "cols": [{"w": 100}] * 10, "rows": [{"h": 24}] * 30, "cells": {}, "merges": [], "fields": []}
    assert room.no({"op": "sheet_add", "name": "sheet2"}, "sheet_name_taken")["sheet"] == "sheet002"      # Excel ignores case
    assert room.no({"op": "sheet_add", "name": "a/b"}, "invalid")["reason"] == "sheet_name"
    assert room.no({"op": "sheet_add", "name": " pad"}, "invalid")["reason"] == "sheet_name"
    room.ok({"op": "sheet_rename", "sheet": "sheet002", "name": "汇总"}, "bob", expect=0)                  # names: last writer wins
    room.ok({"op": "sheet_rename", "sheet": "sheet002", "name": "合计"}, "ann", expect=0)
    assert [s["name"] for s in project(room.st)["sheets"]] == ["报表", "合计"]
    # a new sheet typed into in the same group
    room.ok({"op": "tx", "ops": [{"op": "sheet_add", "id": "sheet003", "name": "草稿", "rows": 1, "cols": 1, "row_ids": ["r0000001"], "col_ids": ["c0000001"]},
                                 {"op": "set", "sheet": "sheet003", "row": "r0000001", "col": "c0000001", "v": "hi"},
                                 {"op": "field_add", "sheet": "sheet003", "row": "r0000001", "col": "c0000001", "field": {"id": "draft", "name": "草稿", "kind": "cell"}}]})
    assert room.sheet(2)["cells"] == {"A1": {"v": "hi"}}
    # delete: a tombstone (not in the model), restorable until the next save; its field id stays reserved
    room.ok({"op": "sheet_delete", "sheet": "sheet003"})
    assert [s["name"] for s in project(room.st)["sheets"]] == ["报表", "合计"]
    assert room.no({"op": "set", "sheet": "sheet003", "row": "r0000001", "col": "c0000001", "v": "x"}, "sheet_deleted")["by"] == "ann"
    assert room.no({"op": "field_add", **room.at("F2"), "field": {"id": "draft", "name": "x", "kind": "cell"}}, "id_taken")
    room.ok({"op": "sheet_delete", "sheet": "sheet003"}, "bob")                                          # already deleted: same outcome
    room.ok({"op": "sheet_add", "name": "草稿"}, "bob")
    assert room.no({"op": "sheet_restore", "sheet": "sheet003"}, "sheet_name_taken")["name"] == "草稿"
    room.ok({"op": "sheet_restore", "sheet": "sheet003", "name": "草稿（恢复）"})
    assert project(room.st)["sheets"][2] == {"name": "草稿（恢复）", "cols": [{}], "rows": [{}], "cells": {"A1": {"v": "hi"}}, "merges": [],
                                             "fields": [{"id": "draft", "name": "草稿", "kind": "cell", "ref": "A1"}]}
    for sid in list(room.st["content"]["order"])[1:]:
        room.ok({"op": "sheet_delete", "sheet": sid})
    assert room.no({"op": "sheet_delete", "sheet": room.sid}, "last_sheet")
    # a save makes the deletions final
    snap = snapshot(room.st)
    out = mark_saved(room.st, snap)
    assert room.st["content"]["order"] == [room.sid] and not dirty(room.st)
    apply_patch(room.mirror, out["patch"])
    assert room.mirror == room.st["content"]
    assert "draft" not in room.st["content"]["fields"]
    room.ok({"op": "field_add", **room.at("F2"), "field": {"id": "draft", "name": "可以再用", "kind": "cell"}})


def test_the_same_object_written_by_someone_else_since_is_a_conflict_but_never_with_yourself():
    room = Room()
    room.ok({"op": "set", **room.at("B3"), "v": "鲍勃写的"}, "bob")                               # seq 1
    d = room.no({"op": "set", **room.at("B3"), "v": "安写的"}, "conflict", "ann", expect=0)
    assert d["theirs"] == {"v": "鲍勃写的"} and d["by"] == "bob" and d["at"] == 1
    # 「用我的值」: having seen theirs, she writes over it
    room.ok({"op": "set", **room.at("B3"), "v": "安写的"}, "ann", expect=1)
    # the same person in another tab that had not caught up is not in conflict with herself
    room.ok({"op": "set", **room.at("B3"), "v": "安在另一页写的"}, "ann", client="ann-2", expect=0)
    assert room.sheet()["cells"]["B3"]["v"] == "安在另一页写的"
    # writing what is already there is agreement, not a conflict
    room.ok({"op": "set", **room.at("B3"), "v": "安在另一页写的"}, "bob", expect=0)
    # fields and merges are first-writer-wins too
    room.ok({"op": "field_set", "field": "fT", "set": {"name": "鲍勃的名字"}}, "bob")
    seq = room.st["seq"]
    d = room.no({"op": "field_set", "field": "fT", "set": {"required": False}}, "conflict", "ann", expect=seq - 1)
    assert d["theirs"]["name"] == "鲍勃的名字" and d["by"] == "bob"
    d = room.no({"op": "field_del", "field": "fT"}, "conflict", "ann", expect=seq - 1)
    merge = room.st["content"]["sheets"][room.sid]["merges"][0]
    room.ok({"op": "unmerge", "id": merge}, "bob")
    d = room.no({"op": "merge", "sheet": room.sid, "range": room.rng("A1", "F2"), "replaces": [merge]}, "conflict", "ann", expect=seq)
    assert d["key"] == f"m/{merge}" and d["theirs"] is None and d["by"] == "bob"
    # and a conflict inside a group refuses the group
    d = room.no({"op": "tx", "ops": [{"op": "set", **room.at("E2"), "v": 1}, {"op": "field_del", "field": "fT"}]}, "conflict", "ann", expect=seq - 1)
    assert d["index"] == 1
    # expect=None is the server's own write: no check
    room.ok({"op": "field_set", "field": "fT", "set": {"name": "系统"}}, "system", expect=None)
    # a client claiming to have seen the future is out of step
    assert room.do({"op": "set", **room.at("A2"), "v": 1}, "ann", expect=room.st["seq"] + 5)["code"] == "resync"
    # after compaction a sender that saw less than the floor is told to resync instead of being trusted
    cs.compact(room.st, upto=room.st["seq"])
    assert room.do({"op": "set", **room.at("B3"), "v": "旧"}, "bob", expect=0)["code"] == "resync"
    room.ok({"op": "set", **room.at("B3"), "v": "新"}, "bob")


def test_a_group_applies_whole_or_not_at_all():
    room = Room()
    ops = [{"op": "set", **room.at("A2"), "v": "one"}, {"op": "append_rows", "sheet": room.sid, "count": 3},
           {"op": "merge", "sheet": room.sid, "range": room.rng("A9", "B9")}, {"op": "field_add", **room.at("A9"), "field": {"id": "q", "name": "Q", "kind": "cell"}},
           {"op": "field_add", **room.at("B9"), "field": {"id": "w", "name": "W", "kind": "cell"}}]          # inside the merge just made
    d = room.no({"op": "tx", "ops": ops}, "merged_inner")
    assert d["index"] == 4 and d["range"] == "A9:B9" and d["ref"] == "B9"
    assert room.st["ctr"] == seed_state(FORMATS_SHEET, epoch="t")["ctr"], "no id was spent"
    r = room.ok({"op": "tx", "ops": ops[:4]})
    assert r["seq"] == 1 and room.sheet()["cells"]["A2"] == {"v": "one"} and len(room.sheet()["rows"]) == 15
    assert room.no({"op": "tx", "ops": [{"op": "tx", "ops": ops}]}, "invalid")["reason"] == "nested_tx"
    assert room.no({"op": "tx", "ops": []}, "invalid")["reason"] == "ops"
    big = [{"op": "style", **room.at("A2"), "s": {"b": i % 2}} for i in range(cs.MAX_TX + 1)]
    assert room.no({"op": "tx", "ops": big}, "too_large")["what"] == "tx"
    r = room.ok({"op": "tx", "ops": big[:cs.MAX_TX]})
    assert len(r["patch"]) == 1, "many writes to one cell in a group are one change"


def test_a_message_delivered_twice_applies_once(monkeypatch):
    room = Room()
    op = {"op": "append_rows", "sheet": room.sid, "count": 1}
    first = apply(room.st, op, by="ann", client="tab", n=1, expect=0)
    state = dumps(room.st)
    again = apply(room.st, op, by="ann", client="tab", n=1, expect=0)
    assert again == {**first, "duplicate": True} and dumps(room.st) == state
    assert len(project(room.st)["sheets"][0]["rows"]) == 13
    # refusals are remembered too: the same message gets the same answer
    bad = {"op": "set", **room.at("A1"), "v": "x" * (cs.MAX_VALUE + 1)}
    r1 = apply(room.st, bad, by="ann", client="tab", n=2, expect=1)
    assert apply(room.st, bad, by="ann", client="tab", n=2, expect=1) == {**r1, "duplicate": True}
    # out of order within the window is fine; far behind it asks for a resync
    assert apply(room.st, {"op": "set", **room.at("A2"), "v": 4}, by="ann", client="tab", n=4, expect=1)["ok"]
    assert apply(room.st, {"op": "set", **room.at("A3"), "v": 3}, by="ann", client="tab", n=3, expect=1)["ok"]
    assert apply(room.st, {"op": "set", **room.at("A2"), "v": 4}, by="ann", client="tab", n=4, expect=1)["duplicate"]
    far = 5 + cs.CLIENT_WINDOW
    assert apply(room.st, {"op": "set", **room.at("A4"), "v": 1}, by="ann", client="tab", n=far, expect=3)["ok"]
    assert apply(room.st, {"op": "set", **room.at("A5"), "v": 1}, by="ann", client="tab", n=5, expect=3)["code"] == "resync"
    assert set(room.st["clients"]["tab"]["r"]) == {str(far)}, "only the window is remembered"
    # a big group's remembered copy drops its patch; the answer keeps its sequence number
    monkeypatch.setattr(cs, "RECORD_ENTRIES", 4)
    rows = [{"op": "set", **room.at(f"D{i}"), "v": i} for i in range(1, 13)]
    big = apply(room.st, {"op": "tx", "ops": rows}, by="ann", client="tab2", n=1, expect=room.st["seq"])
    replay = apply(room.st, {"op": "tx", "ops": rows}, by="ann", client="tab2", n=1, expect=0)
    assert big["ok"] and len(big["patch"]) == 12
    assert replay == {"ok": True, "seq": big["seq"], "patch": None, "before": None, "trimmed": True, "duplicate": True}
    with pytest.raises(ValueError):
        apply(room.st, op, by="", client="tab", n=9)
    with pytest.raises(ValueError):
        apply(room.st, op, by="ann", client="tab", n=0)


# ------------------------------------------------------------------ versions: save and outside saves
def test_a_save_captures_a_moment_and_what_came_after_stays_unsaved():
    room = Room()
    room.ok({"op": "set", **room.at("C3"), "v": "保存前"})
    snap = snapshot(room.st)
    saved_model = project(room.st)
    room.ok({"op": "set", **room.at("C4"), "v": "保存期间"}, "bob")
    out = mark_saved(room.st, snap)
    assert out["patch"] == [] and out["dirty"] is True
    assert room.st["basis"]["seq"] == snap["seq"]
    # reverting what came after the snapshot gives exactly what was saved
    assert cs._project(cs._revert(room.st)) == saved_model
    room.ok({"op": "clear", **room.at("C4")}, "bob")
    assert not dirty(room.st)


def test_an_outside_version_is_folded_in_and_wins_on_the_same_object():
    base = COLLAB_SHEET                                         # 结果: A1 客户, B1 待填, field customer on B1
    room = Room(base)
    # untouched room: it becomes exactly the outside version, still clean
    outside = json.loads(json.dumps(base))
    outside["sheets"][0]["cells"]["A2"] = {"v": "外部"}
    outside["sheets"][0]["rows"] = [{"h": 24}] * 2
    res = reconcile(room.st, outside, by="api")
    assert project(room.st) == normalize(outside) and not dirty(room.st) and res["overwritten"] == []
    apply_patch(room.mirror, res["patch"])
    assert room.mirror == room.st["content"]
    # ann and bob type; the API saves a version changing another cell and the one ann changed
    room.ok({"op": "set", **room.at("C1"), "v": "安"}, "ann")
    room.ok({"op": "set", **room.at("C2"), "v": "鲍勃"}, "bob")
    room.ok({"op": "style", **room.at("A1"), "s": {"b": True}}, "bob")
    outside2 = json.loads(json.dumps(outside))
    outside2["sheets"][0]["cells"]["C1"] = {"v": "接口"}
    outside2["sheets"][0]["cells"]["B1"] = {"v": "接口填的"}
    seq_before = room.st["seq"]
    res = reconcile(room.st, outside2, by="api")
    apply_patch(room.mirror, res["patch"])
    assert room.mirror == room.st["content"]
    cells = project(room.st)["sheets"][0]["cells"]
    assert cells["C1"] == {"v": "接口"} and cells["B1"] == {"v": "接口填的"} and cells["C2"] == {"v": "鲍勃"}
    assert cells["A1"] == {"v": "客户", "s": {"b": True}}
    assert res["overwritten"] == ["ann"] and res["seq"] == seq_before + 1
    assert res["conflicts"] == [{"kind": "cell", "ref": "C1", "reason": "both_added", "by": ["ann"], "sheet_id": room.sid, "sheet_name": "结果"}]
    assert dirty(room.st)
    # ann is offered 「用我的值」: her stale write is a conflict with the API's, then goes through once she has seen it
    d = room.no({"op": "set", **room.at("C1"), "v": "安"}, "conflict", "ann", expect=seq_before)
    assert d["by"] == "api"
    room.ok({"op": "set", **room.at("C1"), "v": "安"}, "ann")
    _build("builtin_sheet", project(room.st))


def test_outside_and_room_changes_that_cannot_be_built_together_keep_the_outside_layout():
    """CI-116 in the live room: the room added a region field B2:C3 while a version merging B2:C2
    was saved outside. Together the generator cannot build them; the outside version's merged ranges
    and fields win on that sheet, the rest of the room's work stays, and the field's author is told."""
    room = Room(COLLAB_SHEET)
    room.ok({"op": "field_add", "sheet": room.sid, "range": room.rng("B2", "C3"), "field": {"id": "area", "name": "区域", "kind": "region"}}, "ann")
    room.ok({"op": "set", **room.at("A3"), "v": "保留"}, "bob")
    res = reconcile(room.st, CI116_MERGED, by="api")
    out = project(room.st)["sheets"][0]
    assert out["merges"] == ["B2:C2"] and [f["id"] for f in out["fields"]] == ["customer"]
    assert out["cells"]["A3"] == {"v": "保留"}
    assert res["overwritten"] == ["ann"] and not res["reset"]
    assert sheet_invariants(project(room.st)) == []
    _build("builtin_sheet", project(room.st))
    apply_patch(room.mirror, res["patch"])
    assert room.mirror == room.st["content"]


def test_a_sheet_deleted_outside_that_the_room_changed_is_kept_restorable():
    room = Room(REPEAT_SHEET)
    second = room.st["content"]["order"][1]
    room.ok({"op": "set", **room.at("A1", second), "v": "房间里写的"}, "bob")
    outside = {"sheets": [REPEAT_SHEET["sheets"][0]]}
    res = reconcile(room.st, outside, by="api")
    apply_patch(room.mirror, res["patch"])
    assert [s["name"] for s in project(room.st)["sheets"]] == ["明细"]
    assert room.st["content"]["sheets"][second]["gone"] and res["overwritten"] == ["bob"]
    room.ok({"op": "sheet_restore", "sheet": second}, "bob")
    assert project(room.st)["sheets"][1]["cells"]["A1"] == {"v": "房间里写的"}
    # a sheet the room did not touch simply goes
    clean = Room(REPEAT_SHEET)
    reconcile(clean.st, outside, by="api")
    assert clean.st["content"]["order"] == [clean.sid] and not dirty(clean.st)
    # an outside version the room cannot hold is refused as a whole
    with pytest.raises(SeedError):
        reconcile(clean.st, INVALID["ci116_both"][0], by="api")


# ------------------------------------------------------------------ random interleaved edits
SEED_MODEL = {"sheets": [FORMATS_SHEET["sheets"][0], REPEAT_SHEET["sheets"][0]]}


def _random_op(rng: random.Random, st: dict, depth: int = 0) -> dict:
    c = st["content"]
    live = [s for s in c["order"] if not c["sheets"][s]["gone"]]
    gone = [s for s in c["order"] if c["sheets"][s]["gone"]]
    sid = rng.choice(live) if live and rng.random() < 0.9 else rng.choice(list(c["order"]))   # now and then a deleted one
    sh = c["sheets"][sid]
    rows, cols = sh["rows"], sh["cols"]

    def cell():
        return {"sheet": sid, "row": rng.choice(rows[:24] or ["missing0"]), "col": rng.choice(cols[:8] or ["missing0"])}

    def rect():
        i, j = rng.randrange(min(len(rows), 24) or 1), rng.randrange(min(len(cols), 8) or 1)
        di, dj = rng.randrange(3), rng.randrange(3)
        return [rows[min(i, len(rows) - 1)] if rows else "missing0", cols[min(j, len(cols) - 1)] if cols else "missing0",
                rows[min(i + di, len(rows) - 1)] if rows else "missing0", cols[min(j + dj, len(cols) - 1)] if cols else "missing0"]

    fields = list(c["fields"])
    merges = [m for m in sh["merges"]]
    kind = rng.choice(["set"] * 6 + ["clear"] * 2 + ["style"] * 3 + ["size", "append_rows", "append_cols", "merge", "merge", "unmerge",
                       "field_add", "field_add", "field_set", "field_move", "field_del", "sheet_add", "sheet_rename", "sheet_delete",
                       "sheet_restore"] + (["tx", "tx"] if depth == 0 else []))
    if kind == "set":
        v = rng.choice(["甲", "乙", "=A1+1", "12", 7, 3.25, True, "", "x" * rng.choice([1, 40])])
        op = {"op": "set", **cell(), "v": v}
        if isinstance(v, str) and v.isdigit() and rng.random() < 0.5:
            op["t"] = "number"
        return op
    if kind == "clear":
        return {"op": "clear", **cell()}
    if kind == "style":
        return {"op": "style", **cell(), "s": {rng.choice(["b", "i", "bd", "wrap"]): rng.choice([1, True, None]),
                                              rng.choice(["color", "bg"]): rng.choice(["#112233", "#ffffff", None])}}
    if kind == "size":
        if rng.random() < 0.5 and rows:
            return {"op": "size", "sheet": sid, "row": rng.choice(rows), "h": rng.choice([12, 24, 40, None])}
        return {"op": "size", "sheet": sid, "col": rng.choice(cols or ["missing0"]), "w": rng.choice([20, 100, 180, None])}
    if kind in ("append_rows", "append_cols"):
        n = rng.randint(1, 3)
        op = {"op": kind, "sheet": sid, "count": n}
        if rng.random() < 0.5:
            op["ids"] = ["".join(rng.choice(cs._B36) for _ in range(8)) for _ in range(n)]
        return op
    if kind == "merge":
        return {"op": "merge", "sheet": sid, "range": rect(), "replaces": rng.sample(merges, min(len(merges), rng.randint(0, 1)))}
    if kind == "unmerge":
        return {"op": "unmerge", "id": rng.choice(merges or ["missing0"])}
    if kind == "field_add":
        fk = rng.choice(["cell", "region", "repeat"])
        f = {"id": f"f{rng.randrange(40)}", "name": rng.choice(["客户", "金额", "明细"]), "kind": fk}
        if fk == "cell":
            return {"op": "field_add", **cell(), "field": f}
        if fk == "region":
            return {"op": "field_add", "sheet": sid, "range": rect(), "field": f}
        r = rect()
        return {"op": "field_add", "sheet": sid, "row": r[0], "c1": r[1], "c2": r[3], "field": f}
    if kind == "field_set":
        return {"op": "field_set", "field": rng.choice(fields or ["none"]), "set": {rng.choice(["name", "required"]): rng.choice(["新名", True, False])}}
    if kind == "field_move":
        fk = rng.choice(fields or ["none"])
        f = c["fields"].get(fk)
        if not f or f["sheet"] != sid:
            return {"op": "field_move", "field": fk, "kind": "cell", **{k: v for k, v in cell().items() if k != "sheet"}}
        r = rect()
        if f["kind"] == "cell":
            return {"op": "field_move", "field": fk, "row": r[0], "col": r[1]}
        if f["kind"] == "region":
            return {"op": "field_move", "field": fk, "range": r}
        return {"op": "field_move", "field": fk, "row": r[0], "c1": r[1], "c2": r[3]}
    if kind == "field_del":
        return {"op": "field_del", "field": rng.choice(fields or ["none"])}
    if kind == "sheet_add":
        return {"op": "sheet_add", "name": rng.choice(["新表", "汇总", "附表", "报表"]), "rows": rng.randint(0, 4), "cols": rng.randint(0, 3), "h": 24}
    if kind == "sheet_rename":
        return {"op": "sheet_rename", "sheet": sid, "name": rng.choice(["新表", "汇总", "附表", "明细", "Sheet"])}
    if kind == "sheet_delete":
        return {"op": "sheet_delete", "sheet": sid}
    if kind == "sheet_restore":
        return {"op": "sheet_restore", "sheet": rng.choice(gone or [sid]), **({"name": rng.choice(["恢复", "恢复2"])} if rng.random() < 0.5 else {})}
    return {"op": "tx", "ops": [_random_op(rng, st, 1) for _ in range(rng.randint(2, 4))]}


@pytest.mark.parametrize("seed", [1, 7, 2026])
def test_random_interleaved_edits_from_three_people_always_leave_a_buildable_table(seed):
    """300 rounds × 3 simulated people: each composes operations (valid or not) and sends them with
    the sequence number it last caught up to, so many are stale; messages are delivered out of
    order, some twice; every 50 rounds a version is saved outside the room and folded in, every 75
    a save happens with one more edit landing while it is written. After every accepted operation
    the table satisfies every invariant; regularly it builds; a message delivered again never
    applies again; a client that applies the patches in order holds exactly the server's state;
    every accepted operation's ``before`` undoes it; after an outside save or a room save, undoing
    the room's pending edits gives exactly that version."""
    rng = random.Random(seed)
    st = seed_state(SEED_MODEL, epoch=f"prop-{seed}")
    mirror = json.loads(json.dumps(st["content"]))
    people = ["ann", "bob", "cyd"]
    seen = {p: 0 for p in people}
    outbox = {p: [] for p in people}
    sent: list = []
    n = {p: 0 for p in people}
    accepted = refused = duplicates = outsides = 0
    codes: set = set()
    for rnd in range(300):
        for p in rng.sample(people, 3):
            if rng.random() < 0.65 or not outbox[p]:
                n[p] += 1
                outbox[p].append((_random_op(rng, st), n[p], seen[p]))
                if rng.random() < 0.5:
                    continue
            op, k, expect = outbox[p].pop(rng.randrange(len(outbox[p])))        # out of order
            state_before = dumps(st["content"])
            res = apply(st, op, by=p, client=f"{p}-tab", n=k, expect=expect)
            assert not res.get("duplicate")
            sent.append((op, p, k, expect, res))
            if res["ok"]:
                if res["patch"]:
                    accepted += 1
                    apply_patch(mirror, res["patch"])
                    undone = apply_patch(json.loads(json.dumps(st["content"])), res["before"])
                    assert dumps(undone) == state_before, "before undoes the operation exactly"
                model = project(st)
                assert sheet_invariants(model) == [], (rnd, op)
                if accepted % 15 == 0:
                    _build("builtin_sheet", model)
            else:
                refused += 1
                codes.add(res["code"])
                assert dumps(st["content"]) == state_before
            if rng.random() < 0.3:
                seen[p] = st["seq"]
            # a duplicate delivery of something already sent
            if sent and rng.random() < 0.15:
                dop, dp, dk, dexp, dres = rng.choice(sent[-40:])
                frozen = dumps({k: st[k] for k in ("content", "seq", "kseq", "base", "ctr")})
                again = apply(st, dop, by=dp, client=f"{dp}-tab", n=dk, expect=dexp)
                duplicates += 1
                assert again["duplicate"] and again["ok"] == dres["ok"] and again.get("seq") == dres.get("seq")
                assert dumps({k: st[k] for k in ("content", "seq", "kseq", "base", "ctr")}) == frozen
        assert mirror == st["content"]
        if rnd % 50 == 49:
            # someone saves outside the room: a version made from the last saved one with a few edits
            fork = seed_state(cs._project(cs._revert(st)), epoch=f"outside-{rnd}")
            for i in range(rng.randint(1, 6)):
                apply(fork, _random_op(rng, fork), by="api", client="api", n=i + 1)
            outside = project(fork)
            res = reconcile(st, outside, by="api")
            outsides += 1
            apply_patch(mirror, res["patch"])
            assert mirror == st["content"]
            assert sheet_invariants(project(st)) == []
            assert cs._project(cs._revert(st)) == normalize(outside), "the outside version is the new basis"
            _build("builtin_sheet", project(st))
        if rnd % 75 == 74:
            # a save in the room, with one more edit landing while it is written
            snap = snapshot(st)
            saved = project(st)
            n["ann"] += 1
            late = apply(st, _random_op(rng, st), by="ann", client="ann-tab", n=n["ann"], expect=st["seq"])
            if late["ok"]:
                apply_patch(mirror, late["patch"])
            out = mark_saved(st, snap)
            apply_patch(mirror, out["patch"])                 # deleted sheets are gone for good
            assert mirror == st["content"]
            assert cs._project(cs._revert(st)) == saved and out["dirty"] == dirty(st)
            assert dirty(st) == bool(late["ok"] and late["patch"]), "only the edit that landed after the snapshot is unsaved"
    assert outsides == 6
    _build("builtin_sheet", project(st))
    assert json.loads(json.dumps(st)) == st
    # the run exercised what it claims to
    assert accepted > 200 and refused > 100 and duplicates > 50, (accepted, refused, duplicates)
    assert {"conflict", "not_found", "sheet_deleted", "sheet_name_taken", "invalid", "id_taken"} <= codes, codes
    assert len(codes & {"field_overlap", "merged_inner", "merged", "vertical_merge", "duplicate_repeat", "merge_overlap"}) >= 3, codes
    # a save of the result and a reconciliation with it leave a clean, identical room
    snap = snapshot(st)
    saved = project(st)
    mark_saved(st, snap)
    assert not dirty(st) and project(st) == saved
    res = reconcile(st, saved, by="api")
    assert res["patch"] == [] and project(st) == saved and not dirty(st)
