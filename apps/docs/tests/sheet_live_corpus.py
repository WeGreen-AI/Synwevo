"""TEST FIXTURE GENERATOR — the shared corpus that holds the browser's live-table model
(``apps/docs/web/src/lib/sheetmodel.ts``) to the server's (``synwevo_docs.collab_sheet``),
docs-sheet-live.md §6: "乐观应用与 Python 生成的共享语料一致".

Every step is an operation applied by the server's own ``collab_sheet.apply`` (trusted, no
first-writer check: that one needs what only the server knows) to a state seeded from a fixture
model, and recorded with its exact result — the patch and its inverse, or the refusal code and
detail. ``tests/sheetmodel.test.ts`` replays each sequence in the browser model and requires the
same results step by step, the same content and the same rendered model at the end. The sequences
are hand-written cases aimed at every refusal the model can give, plus seeded random ones.

Regenerate after changing ``collab_sheet.py`` (from the repository root)::

    .venv/bin/python apps/docs/tests/sheet_live_corpus.py --write

``test_sheet_live_corpus.py`` fails while the checked-in corpus differs from what this builds;
the node test then says whether the browser model still agrees.

TEST FIXTURES: the models below are synthetic (shapes of test_collab_sheet's corpus and the
SheetEditor's new sheet); ids come from fixed epochs, random operations from fixed seeds."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from synwevo_docs import collab_sheet as cs

CORPUS_PATH = Path(__file__).resolve().parents[1] / "web" / "tests" / "fixtures" / "sheet_live_corpus.json"
PEOPLE = ("u1", "u2", "u3")
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"

# ------------------------------------------------------------------ fixture models (synthetic)
BASIC = {"sheets": [
    {"name": "报价", "cols": [{"w": 120}, {"w": 100}, {"w": 100}, {"w": 80}, {"w": 80}], "rows": [{"h": 24}] * 8,
     "cells": {"A1": {"v": "报价单", "s": {"b": 1, "sz": 14}}, "A2": {"v": "客户"}, "B2": {"v": "待填"},
               "A3": {"v": 12, "t": "number"}, "B3": {"v": 3.5}, "C3": {"v": True}, "A4": {"v": "=SUM(A3:B3)"},
               "D5": {"v": "", "s": {"bd": 1, "nf": "0.00"}}},
     "merges": ["A1:C1", "D7:E8"],
     "fields": [{"id": "cust", "name": "客户", "kind": "cell", "ref": "B2", "required": True},
                {"id": "area", "name": "区域", "kind": "region", "ref": "B5:C6"},
                {"id": "items", "name": "明细", "kind": "repeat", "row": 3, "c1": 0, "c2": 2, "empty_policy": "remove_row"}]},
    {"name": "第二页", "cells": {"B2": {"v": "x"}}},
]}
REPEAT = {"sheets": [{"name": "明细", "cols": [{"w": 100}] * 4, "rows": [{"h": 24}] * 6,
                      "cells": {"A1": {"v": "名称"}, "B1": {"v": "数量"}, "C1": {"v": "=SUM(B2:B2)"}},
                      "merges": ["C3:D4", "A5:B5"],
                      "fields": [{"id": "items", "name": "明细", "kind": "repeat", "row": 1, "c1": 0, "c2": 1, "empty_policy": "remove_row"},
                                 {"id": "who", "name": "经办", "kind": "cell", "ref": "C3"},
                                 {"name": "无 id 的字段", "ref": "A6"}]},
                     {"name": "第二页", "cells": {"B7": {"v": 3.5, "t": "number"}, "C9": {"v": True}}}]}
NEW_SHEET = {"sheets": [{"name": "Sheet1", "cols": [{"w": 100}] * 10, "rows": [{"h": 24}] * 30,
                         "cells": {}, "merges": [], "fields": []}]}
NULL_PARTS = {"sheets": [{"name": "S", "rows": None, "cols": None, "merges": None, "cells": {"A1": {"v": 1}}}]}
NULL_ENTRIES = {"sheets": [{"name": "S", "rows": [None, {"h": 30}, {}], "cols": [None, {"w": 50}]}]}
NO_SHEETS = {"sheets": []}
EMPTY_MODEL: dict = {}
THREE = {"sheets": [{"name": "一", "cells": {"A1": {"v": "a"}}}, {"name": "二", "cells": {"B2": {"v": "b"}}},
                    {"name": "三", "rows": [{"h": 20}] * 3, "cols": [{"w": 60}] * 3}]}

SEEDS = {"basic": BASIC, "repeat": REPEAT, "new_sheet": NEW_SHEET, "null_parts": NULL_PARTS, "null_entries": NULL_ENTRIES,
         "no_sheets": NO_SHEETS, "empty_model": EMPTY_MODEL, "three": THREE}


# ------------------------------------------------------------------ recording
class Seq:
    """One sequence: a seeded state and the operations applied to it, each recorded with its result."""

    def __init__(self, name: str, seed: str, *, cap: int | None = None):
        self.name, self.cap = name, cap
        self.st = cs.seed_state(SEEDS[seed], epoch=f"corpus/{name}")
        self.content = json.loads(json.dumps(self.st["content"]))
        self.steps: list = []
        self.n: dict = {}

    # ids of the state, by position
    def sid(self, i: int) -> str:
        return self.st["content"]["order"][i]

    def row(self, i: int, r: int) -> str:
        return self.st["content"]["sheets"][self.sid(i)]["rows"][r]

    def col(self, i: int, c: int) -> str:
        return self.st["content"]["sheets"][self.sid(i)]["cols"][c]

    def rng(self, i: int, r1: int, c1: int, r2: int, c2: int) -> list:
        return [self.row(i, r1), self.col(i, c1), self.row(i, r2), self.col(i, c2)]

    def merge(self, i: int, k: int) -> str:
        return self.st["content"]["sheets"][self.sid(i)]["merges"][k]

    def do(self, op, by: str = "u1") -> dict:
        client = f"{by}/corpus"
        n = cs.next_number(self.st, client)
        res = cs.apply(self.st, op, by=by, client=client, n=n, expect=None, trusted=True, max_cells=self.cap)
        result = ({"ok": True, "patch": res["patch"], "before": res["before"]} if res["ok"]
                  else {"ok": False, "code": res["code"], "detail": res["detail"]})
        self.steps.append({"by": by, "op": op, "result": result})
        return result

    def out(self) -> dict:
        return {"name": self.name, "cap": self.cap, "content": self.content, "steps": self.steps,
                "final": cs.project(self.st), "final_content": self.st["content"]}


def _id(rng: random.Random) -> str:
    return "".join(rng.choice(B36) for _ in range(8))


# ------------------------------------------------------------------ hand-written cases: each refusal, each op
def cells_and_styles() -> Seq:
    s = Seq("cells_and_styles", "basic")
    sid, r, c = s.sid(0), s.row, s.col
    at = lambda rr, cc: {"sheet": sid, "row": r(0, rr), "col": c(0, cc)}   # noqa: E731
    s.do({"op": "set", **at(1, 1), "v": "张三"})
    s.do({"op": "set", **at(1, 1), "v": "张三"}, by="u2")                       # already so: no change, no owner
    s.do({"op": "set", **at(2, 0), "v": "42", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": " 1_000 ", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": "１２", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": "1e3", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": "abc", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": "inf", "t": "number"})
    s.do({"op": "set", **at(2, 0), "v": True, "t": "number"})
    s.do({"op": "set", **at(2, 1), "v": "x", "t": "bogus"})
    s.do({"op": "set", **at(2, 1), "v": [1]})
    s.do({"op": "set", **at(2, 1), "v": "tab\tok"})
    s.do({"op": "set", **at(2, 1), "v": "bell\x07"})
    s.do({"op": "set", **at(2, 1), "v": "half\ud800"})
    s.do({"op": "set", **at(2, 1), "v": "pair\U0001f600"})
    s.do({"op": "set", **at(2, 1), "v": "x" * (cs.MAX_VALUE + 1)})
    s.do({"op": "set", **at(2, 1), "v": "=" + "1" * cs.MAX_FORMULA})
    s.do({"op": "set", **at(2, 1), "v": "=" + "1" * cs.MAX_FORMULA, "t": "text"})
    s.do({"op": "set", **at(2, 1), "v": "short"})
    s.do({"op": "set", **at(0, 1), "v": "被合并"})                               # B1 inside A1:C1
    s.do({"op": "set", **at(0, 0), "v": "左上角可以写"})
    s.do({"op": "set", **at(0, 1), "v": ""})                                    # empty under a merge: allowed
    s.do({"op": "clear", **at(1, 1)})
    s.do({"op": "clear", **at(1, 1)})                                           # nothing there: no change
    s.do({"op": "clear", **at(3, 0), "v": "x"})                                 # unknown key for clear
    s.do({"op": "set", "sheet": sid, "row": "nosuchrw", "col": c(0, 0), "v": 1})
    s.do({"op": "set", "sheet": sid, "row": r(0, 0), "col": 7, "v": 1})
    s.do({"op": "set", "sheet": "nosheet!", "row": r(0, 0), "col": c(0, 0), "v": 1})
    s.do({"op": "set", "sheet": 5, "row": r(0, 0), "col": c(0, 0), "v": 1})
    s.do({"op": "set", **at(4, 3), "v": 7, "zz": 1, "aa": 2})
    # styles
    s.do({"op": "style", **at(1, 0), "s": {"b": 1, "color": "#ff0000", "sz": 12}})
    s.do({"op": "style", **at(1, 0), "s": {"b": 1}})                            # already so
    s.do({"op": "style", **at(1, 0), "s": {"b": None, "i": True, "ha": "center"}})
    s.do({"op": "style", **at(1, 0), "s": {"ha": "middle"}})
    s.do({"op": "style", **at(1, 0), "s": {"va": "middle"}})
    s.do({"op": "style", **at(1, 0), "s": {"b": 2}})
    s.do({"op": "style", **at(1, 0), "s": {"color": "red"}})
    s.do({"op": "style", **at(1, 0), "s": {"sz": 200}})
    s.do({"op": "style", **at(1, 0), "s": {"sz": "12"}})
    s.do({"op": "style", **at(1, 0), "s": {"nf": "0.00%"}})
    s.do({"op": "style", **at(1, 0), "s": {"nf": 5}})
    s.do({"op": "style", **at(1, 0), "s": {"nf": "a\x02"}})
    s.do({"op": "style", **at(1, 0), "s": {"nf": "0" * 256}})
    s.do({"op": "style", **at(1, 0), "s": {"zz": 1}})
    s.do({"op": "style", **at(1, 0), "s": {}})
    s.do({"op": "style", **at(1, 0), "s": "bold"})
    s.do({"op": "style", **at(6, 4), "s": {"bg": "#00FF00", "bd": 1, "wrap": 0}})   # a new cell with style only
    s.do({"op": "style", **at(6, 4), "s": {"bg": None, "bd": None, "wrap": None}})  # style gone: the cell goes
    s.do({"op": "style", **at(4, 3), "s": {"nf": None}}, by="u2")
    return s


def sizes_and_appends() -> Seq:
    s = Seq("sizes_and_appends", "basic")
    sid = s.sid(0)
    s.do({"op": "size", "sheet": sid, "row": s.row(0, 0), "h": 40})
    s.do({"op": "size", "sheet": sid, "row": s.row(0, 0), "h": 40})
    s.do({"op": "size", "sheet": sid, "row": s.row(0, 0), "h": None})
    s.do({"op": "size", "sheet": sid, "col": s.col(0, 1), "w": 2001})
    s.do({"op": "size", "sheet": sid, "col": s.col(0, 1), "w": 0})
    s.do({"op": "size", "sheet": sid, "col": s.col(0, 1), "w": 12.5})
    s.do({"op": "size", "sheet": sid, "col": s.col(0, 1), "w": True})
    s.do({"op": "size", "sheet": sid, "col": s.col(0, 1)})
    s.do({"op": "size", "sheet": sid, "row": s.row(0, 1), "col": s.col(0, 1), "h": 3})
    s.do({"op": "size", "sheet": sid})
    s.do({"op": "size", "sheet": sid, "row": s.row(0, 1), "w": 30})
    s.do({"op": "append_rows", "sheet": sid, "count": 2, "ids": ["newrow01", "newrow02"], "h": 30})
    s.do({"op": "append_cols", "sheet": sid, "count": 1, "ids": ["newcol01"]})
    s.do({"op": "size", "sheet": sid, "col": "newcol01", "w": 90})
    s.do({"op": "append_rows", "sheet": sid, "count": 0, "ids": []})
    s.do({"op": "append_rows", "sheet": sid, "count": 5001})
    s.do({"op": "append_rows", "sheet": sid, "count": 2, "ids": ["a", "b", "c"]})
    s.do({"op": "append_rows", "sheet": sid, "count": 1, "ids": ["BADIDXXX"]})
    s.do({"op": "append_rows", "sheet": sid, "count": 1, "ids": ["newrow01"]})      # taken by a row
    s.do({"op": "append_rows", "sheet": sid, "count": 1, "ids": ["newcol01"]})      # taken by a column
    s.do({"op": "append_rows", "sheet": sid, "count": 1, "ids": ["newrow03"], "h": 0})
    s.do({"op": "append_rows", "sheet": sid, "count": True, "ids": ["newrow03"]})
    s.do({"op": "append_rows", "sheet": sid, "count": 1, "ids": ["newrow03"], "x": 1})
    # sizes on rows that exist only as ids (beyond the grid): not in the grid
    t = Seq("beyond_grid", "null_parts")
    t.do({"op": "size", "sheet": t.sid(0), "row": t.row(0, 0), "h": 20})
    t.do({"op": "append_cols", "sheet": t.sid(0), "count": 1, "ids": {}})              # {} is "none": the id beyond the grid serves
    t.do({"op": "append_rows", "sheet": t.sid(0), "count": 2, "ids": ["gridrow1"]})
    t.do({"op": "size", "sheet": t.sid(0), "row": t.row(0, 0), "h": 20})
    t.do({"op": "size", "sheet": t.sid(0), "row": "gridrow1", "h": 22})
    s.steps += []                                                       # (t is its own sequence)
    return s, t


def merges() -> Seq:
    s = Seq("merges", "basic")
    sid, rng = s.sid(0), s.rng
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 6, 1, 6, 1), "id": "mergeaa1"})    # single cell
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 0, 3, 1, 3), "id": "mergeaa1"})    # overlaps A1:C1
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 1, 0, 2, 1), "id": "mergeaa1"})    # covers values A2, B2 (and field B2)
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 4, 1, 5, 2), "id": "mergeaa1"})    # region field B5:C6
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 2, 3, 3, 3), "id": "mergeaa1"})    # vertical across repeat row 4
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa2"})    # D6:E6: fine
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa3"})    # again: overlap
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa3", "replaces": ["mergeaa2"]})
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa2", "replaces": ["mergeaa3"]})  # an id used before
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa4", "replaces": ["mergeaa3", "gonegone"]})
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa5", "replaces": "x"})
    s.do({"op": "merge", "sheet": s.sid(1), "range": rng(1, 0, 0, 0, 1), "id": "othermg1"})              # on the second sheet
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 5, 3, 5, 4), "id": "mergeaa5", "replaces": ["othermg1"]})  # not this sheet's
    s.do({"op": "merge", "sheet": sid, "range": ["x"], "id": "mergeaa5"})
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 7, 0, 7, 1), "id": "Bad-Id!"})
    s.do({"op": "merge", "sheet": sid, "range": rng(0, 7, 0, 7, 1), "id": s.merge(0, 0)})  # taken
    s.do({"op": "merge", "sheet": sid, "range": [s.row(0, 7), s.col(0, 1), s.row(0, 7), s.col(0, 0)], "id": "mergeaa6"})  # reversed corners
    s.do({"op": "unmerge", "sheet": sid, "id": "mergeaa6"})
    s.do({"op": "unmerge", "id": "mergeaa6"})
    s.do({"op": "unmerge", "id": 7})
    s.do({"op": "unmerge", "sheet": s.sid(1), "id": s.merge(0, 0)})
    s.do({"op": "unmerge", "sheet": None, "id": s.merge(0, 0)})
    s.do({"op": "unmerge", "id": s.merge(0, 0)}, by="u2")
    # clear then merge over values in one group: allowed; a group refused part-way changes nothing
    s.do({"op": "tx", "ops": [{"op": "clear", "sheet": sid, "row": s.row(0, 1), "col": s.col(0, 0)},
                              {"op": "clear", "sheet": sid, "row": s.row(0, 1), "col": s.col(0, 1)},
                              {"op": "merge", "sheet": sid, "range": rng(0, 1, 0, 1, 1), "id": "mergeaa7"}]})
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 4), "v": "先写"},
                              {"op": "merge", "sheet": sid, "range": rng(0, 7, 3, 7, 4), "id": "mergeaa8"}]})
    s.do({"op": "tx", "ops": [{"op": "merge", "sheet": sid, "range": rng(0, 7, 2, 7, 3), "id": "mergeaa9"},
                              {"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 3), "v": "被盖"}]})
    return s


def fields() -> Seq:
    s = Seq("fields", "basic")
    sid, r, c, rng = s.sid(0), s.row, s.col, s.rng
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "total", "name": "合计", "kind": "cell"}, "row": r(0, 6), "col": c(0, 0)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "total", "name": "合计", "kind": "cell"}, "row": r(0, 7), "col": c(0, 0)})  # id taken
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "inner", "name": "内", "kind": "cell"}, "row": r(0, 0), "col": c(0, 1)})     # merged_inner
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "corner", "name": "角", "kind": "cell"}, "row": r(0, 0), "col": c(0, 0)})    # merge corner: ok
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "reg2", "name": "区", "kind": "region"}, "range": rng(0, 6, 3, 7, 4)})     # merged
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "reg3", "name": "区", "kind": "region"}, "range": rng(0, 4, 2, 5, 3)})     # overlaps area
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "rep2", "name": "明细2", "kind": "repeat"}, "row": r(0, 3), "c1": c(0, 3), "c2": c(0, 4)})  # duplicate repeat row
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "rep3", "name": "明细3", "kind": "repeat"}, "row": r(0, 6), "c1": c(0, 4), "c2": c(0, 0)})  # vertical merge D7:E8 on row 7
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "rep4", "name": "明细4", "kind": "repeat", "empty_policy": "blank_row"}, "row": r(0, 5), "c1": c(0, 0), "c2": c(0, 0)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "constructor", "name": "x", "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "1bad", "name": "x", "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "  ", "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x" * 101, "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": 5, "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x", "kind": "cell", "required": "yes"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x", "kind": "cell", "empty_policy": "bogus"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x", "kind": "blob"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x", "kind": "cell", "ref": "B8"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "x", "kind": "cell"}, "range": rng(0, 7, 1, 7, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": "okid"})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "名\x01", "kind": "cell"}, "row": r(0, 7), "col": c(0, 1)})
    s.do({"op": "field_add", "sheet": sid, "field": {"id": "okid", "name": "好", "kind": "cell", "required": False}, "row": r(0, 7), "col": c(0, 1)}, by="u2")
    s.do({"op": "field_set", "field": "okid", "set": {"name": "改名", "required": True}})
    s.do({"op": "field_set", "field": "okid", "set": {"name": "改名", "required": True}})
    s.do({"op": "field_set", "field": "okid", "set": {"required": None}})
    s.do({"op": "field_set", "field": "okid", "set": {"kind": "region"}})
    s.do({"op": "field_set", "field": "okid", "set": {}})
    s.do({"op": "field_set", "field": "okid", "set": {"empty_policy": "error"}})
    s.do({"op": "field_set", "field": "nope", "set": {"name": "x"}})
    s.do({"op": "field_set", "field": ["okid"], "set": {"name": "x"}})
    s.do({"op": "field_move", "field": "okid", "row": r(0, 7), "col": c(0, 2)})
    s.do({"op": "field_move", "field": "okid", "row": r(0, 7), "col": c(0, 2)})                  # already there
    s.do({"op": "field_move", "field": "okid", "kind": "region", "range": rng(0, 7, 2, 7, 1)})    # kind change
    s.do({"op": "field_move", "field": "okid", "kind": "repeat", "row": r(0, 7), "c1": c(0, 0), "c2": c(0, 1)})
    s.do({"op": "field_move", "field": "okid", "kind": "repeat", "row": r(0, 3), "c1": c(0, 3), "c2": c(0, 4)})  # duplicate repeat
    s.do({"op": "field_move", "field": "okid", "kind": "cell", "row": r(0, 4), "col": c(0, 1)})  # into the region field
    s.do({"op": "field_move", "field": "okid", "kind": "banana", "row": r(0, 4), "col": c(0, 1)})
    s.do({"op": "field_move", "field": "ghost", "row": r(0, 4), "col": c(0, 1)})
    s.do({"op": "field_move", "field": "okid", "row": r(0, 4)})
    s.do({"op": "field_del", "field": "okid"})
    s.do({"op": "field_del", "field": "okid"})
    s.do({"op": "field_del", "field": "total", "x": 1})
    # a group that moves a field and then adds one in its old place: the moved field is checked last
    s.do({"op": "tx", "ops": [{"op": "field_move", "field": "total", "row": r(0, 7), "col": c(0, 4)},
                              {"op": "field_add", "sheet": sid, "field": {"id": "again", "name": "再", "kind": "cell"}, "row": r(0, 6), "col": c(0, 0)},
                              {"op": "field_add", "sheet": sid, "field": {"id": "clash", "name": "撞", "kind": "region"}, "range": rng(0, 6, 0, 7, 4)}]})
    return s


def id_less_fields() -> Seq:
    s = Seq("id_less_fields", "repeat")
    content = s.st["content"]
    idless = [fk for fk in content["fields"] if fk.startswith("~")][0]
    s.do({"op": "field_set", "field": idless, "set": {"name": "有了名字"}})
    s.do({"op": "field_move", "field": idless, "row": s.row(0, 5), "col": s.col(0, 2)})
    s.do({"op": "field_move", "field": "who", "row": s.row(0, 2), "col": s.col(0, 3)})    # C3:D4 inner cell
    s.do({"op": "field_move", "field": "items", "row": s.row(0, 4), "c1": s.col(0, 0), "c2": s.col(0, 1)})  # A5:B5 is horizontal: fine
    s.do({"op": "field_move", "field": "items", "row": s.row(0, 2), "c1": s.col(0, 0), "c2": s.col(0, 1)})  # C3:D4 vertical on row 3
    s.do({"op": "field_del", "field": idless})
    return s


def sheets() -> Seq:
    s = Seq("sheets", "three")
    s.do({"op": "sheet_add", "id": "sheetaa1", "name": "四", "rows": 2, "cols": 2, "row_ids": ["r4aaaaa1", "r4aaaaa2"], "col_ids": ["c4aaaaa1", "c4aaaaa2"], "h": 30})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "四"})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "History"})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "a/b"})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": " 前后空格"})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "'引号"})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "x" * 32})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": ""})
    s.do({"op": "sheet_add", "id": "sheetaa1", "name": "五"})                                   # id taken
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "五", "rows": -1})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "五", "rows": 1, "row_ids": ["dupdup01"], "cols": 1, "col_ids": ["dupdup01"]})  # one id twice
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "五", "rows": 1, "row_ids": ["a", "b"]})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "五", "w": 0})
    s.do({"op": "sheet_add", "id": "sheetaa2", "name": "Straße"})
    s.do({"op": "sheet_add", "id": "sheetaa3", "name": "STRASSE"})                              # the same ignoring case
    s.do({"op": "sheet_rename", "sheet": s.sid(0), "name": "壹"})
    s.do({"op": "sheet_rename", "sheet": s.sid(0), "name": "壹"})
    s.do({"op": "sheet_rename", "sheet": s.sid(0), "name": "二"})
    s.do({"op": "sheet_rename", "sheet": s.sid(0), "name": "Sheet?"})
    s.do({"op": "sheet_delete", "sheet": s.sid(1)})
    s.do({"op": "sheet_delete", "sheet": s.sid(1)})                                            # already gone
    s.do({"op": "set", "sheet": s.sid(1), "row": s.row(1, 1), "col": s.col(1, 1), "v": "删了"})
    s.do({"op": "sheet_add", "id": "sheetaa4", "name": "二"})                                   # its name is free now
    s.do({"op": "sheet_restore", "sheet": s.sid(1)})                                           # name taken meanwhile
    s.do({"op": "sheet_restore", "sheet": s.sid(1), "name": "二（恢复）"})
    s.do({"op": "sheet_restore", "sheet": s.sid(1)})                                           # not deleted
    s.do({"op": "sheet_delete", "sheet": "sheetaa1"}, by="u2")
    s.do({"op": "sheet_delete", "sheet": "sheetaa4"})
    s.do({"op": "sheet_delete", "sheet": "sheetaa2"})
    s.do({"op": "sheet_delete", "sheet": s.sid(0)})
    s.do({"op": "sheet_delete", "sheet": s.sid(1)})
    s.do({"op": "sheet_delete", "sheet": s.sid(2)})                                            # the last live sheet
    s.do({"op": "sheet_delete", "sheet": s.sid(2), "force": True})
    s.do({"op": "sheet_restore", "sheet": "sheetaa1"})
    return s


def groups_and_invalid() -> Seq:
    s = Seq("groups_and_invalid", "new_sheet")
    sid = s.sid(0)
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 0), "col": s.col(0, 0), "v": "甲"},
                              {"op": "set", "sheet": sid, "row": s.row(0, 0), "col": s.col(0, 1), "v": "乙"},
                              {"op": "style", "sheet": sid, "row": s.row(0, 0), "col": s.col(0, 0), "s": {"b": 1}}]})
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 1), "col": s.col(0, 0), "v": "丙"},
                              {"op": "set", "sheet": sid, "row": "zzzzzzzz", "col": s.col(0, 1), "v": "丁"}]})
    s.do({"op": "tx", "ops": [{"op": "tx", "ops": []}]})
    s.do({"op": "tx", "ops": []})
    s.do({"op": "tx", "ops": "x"})
    s.do({"op": "tx", "ops": [1]})
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 2), "col": s.col(0, 2), "v": 1}], "extra": 1})
    s.do({"op": "tx", "ops": [{"op": "append_rows", "sheet": sid, "count": 1, "ids": ["grprow01"]},
                              {"op": "set", "sheet": sid, "row": "grprow01", "col": s.col(0, 0), "v": "新行"},
                              {"op": "size", "sheet": sid, "row": "grprow01", "h": 44}]})
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 3), "col": s.col(0, 3), "v": "回滚"},
                              {"op": "append_cols", "sheet": sid, "count": 1, "ids": ["grpcol01"]},
                              {"op": "merge", "sheet": sid, "range": [s.row(0, 3), s.col(0, 3), s.row(0, 3), "grpcol01"], "id": "grpmrg01"}]})
    s.do({"op": "tx", "ops": [{"op": "set", "sheet": sid, "row": s.row(0, 4), "col": s.col(0, 4), "v": "写"},
                              {"op": "clear", "sheet": sid, "row": s.row(0, 4), "col": s.col(0, 4)}]})    # nets out: no change
    s.do({"op": "nope"})
    s.do({"op": 5})
    s.do({})
    s.do("not an object")
    s.do([{"op": "set"}])
    s.do({"op": "set"})
    s.do({"op": "__proto__"})
    s.do({"op": "toString", "sheet": sid})
    return s


def cell_cap() -> Seq:
    s = Seq("cell_cap", "basic", cap=12)
    sid = s.sid(0)
    for k, v in enumerate(("第十", "第十一", "第十二")):                                            # 9 cells seeded: up to 12
        s.do({"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, k), "v": v})
    s.do({"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 3), "v": "第十三"})       # over the room's bound
    s.do({"op": "style", "sheet": sid, "row": s.row(0, 6), "col": s.col(0, 3), "s": {"b": 1}})     # a styled new cell counts too
    s.do({"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 0), "v": "改一个已有的"})
    s.do({"op": "sheet_delete", "sheet": s.sid(1)})
    s.do({"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 3), "v": "还是不行"})     # gone sheets still count
    s.do({"op": "clear", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 1)})
    s.do({"op": "set", "sheet": sid, "row": s.row(0, 7), "col": s.col(0, 3), "v": "腾出一格"})
    s.do({"op": "sheet_restore", "sheet": s.sid(1)})
    return s


def empty_models() -> list:
    out = []
    for seed in ("empty_model", "no_sheets"):
        s = Seq(f"from_{seed}", seed)
        s.do({"op": "set", "sheet": "anything", "row": "r", "col": "c", "v": 1})
        s.do({"op": "sheet_add", "id": "firstsh1", "name": "第一张", "rows": 3, "cols": 2,
              "row_ids": ["fr000001", "fr000002", "fr000003"], "col_ids": ["fc000001", "fc000002"], "w": 90})
        s.do({"op": "set", "sheet": "firstsh1", "row": "fr000002", "col": "fc000002", "v": "有了"})
        out.append(s)
    s = Seq("null_entries", "null_entries")
    s.do({"op": "size", "sheet": s.sid(0), "row": s.row(0, 0), "h": 18})
    s.do({"op": "size", "sheet": s.sid(0), "row": s.row(0, 2), "h": None})
    s.do({"op": "size", "sheet": s.sid(0), "col": s.col(0, 0), "w": None})
    s.do({"op": "size", "sheet": s.sid(0), "col": s.col(0, 1), "w": None})
    out.append(s)
    return out


# ------------------------------------------------------------------ random sequences
VALUES = ["客户", "", "=SUM(A1:A3)", 12, 3.5, -7, True, False, 0, "00123", "1e3", " 7 ", "１２", "1_000", "abc\x01",
          "x" * 40, None, "多行\n文本", "=A1&\"!\"", 1e21]
TYPES = [None, None, None, "text", "number", "auto", "bogus"]
STYLE_VALUES = {"b": [1, 0, True, None, 2], "i": [1, None], "u": [True, None], "sz": [11, 3, 200, None, "12", 10.5],
                "color": ["#ff0000", "red", None], "bg": ["#00ff00", "123456", None], "ha": ["left", "center", "middle", None],
                "va": ["top", "middle", None], "wrap": [1, None], "bd": [1, None], "nf": ["0.00", "@", None, "a\x02", 5], "zz": [1]}
NAMES = ["Sheet2", "明细", "history", "bad/name", " x", "S", "s", "第二页", "報價", "Straße", "STRASSE"]
FIELD_IDS = ["fA", "fB", "item_1", "constructor", "1bad", "cust", "area", "items"]


def _pick_sheet(st, rng, live_only=True):
    c = st["content"]
    sids = [s for s in c["order"] if not (live_only and c["sheets"][s]["gone"])]
    if not sids or rng.random() < 0.03:
        return "nosuchsh", None
    sid = rng.choice(sids)
    return sid, c["sheets"][sid]


def _pick(rng, ids, bogus="zzzzzzzz"):
    return bogus if not ids or rng.random() < 0.04 else rng.choice(ids)


def _random_op(st, rng: random.Random, depth=0):
    c = st["content"]
    sid, sh = _pick_sheet(st, rng)
    rows, cols = (sh["rows"], sh["cols"]) if sh else ([], [])
    kind = rng.choices(["set", "clear", "style", "size", "append_rows", "append_cols", "merge", "unmerge", "field_add",
                        "field_set", "field_move", "field_del", "sheet_add", "sheet_rename", "sheet_delete", "sheet_restore",
                        "tx", "invalid"],
                       weights=[22, 6, 10, 5, 3, 3, 8, 3, 6, 3, 4, 2, 3, 2, 2, 2, 5 if depth == 0 else 0, 2])[0]
    if kind in ("set", "clear", "style"):
        op = {"op": kind, "sheet": sid, "row": _pick(rng, rows), "col": _pick(rng, cols)}
        if kind == "set":
            v = rng.choice(VALUES)
            if v is not None or rng.random() < 0.5:
                op["v"] = v
            t = rng.choice(TYPES)
            if t is not None:
                op["t"] = t
        elif kind == "style":
            keys = rng.sample(sorted(STYLE_VALUES), rng.randint(1, 3))
            op["s"] = {k: rng.choice(STYLE_VALUES[k]) for k in keys}
        return op
    if kind == "size":
        if rng.random() < 0.5:
            return {"op": "size", "sheet": sid, "row": _pick(rng, rows), "h": rng.choice([30, 0, 5000, None, 24, 12.5])}
        return {"op": "size", "sheet": sid, "col": _pick(rng, cols), "w": rng.choice([90, 0, 2500, None, 100, "x"])}
    if kind in ("append_rows", "append_cols"):
        n = rng.choice([1, 1, 2, 3, 0])
        op = {"op": kind, "sheet": sid, "count": n, "ids": [_id(rng) for _ in range(max(n, 0))]}
        if rng.random() < 0.3:
            op["h" if kind == "append_rows" else "w"] = rng.choice([30, 0, 60])
        return op
    if kind == "merge":
        if not rows or not cols:
            return {"op": "merge", "sheet": sid, "range": ["a", "b", "c", "d"], "id": _id(rng)}
        r1, c1 = rng.randrange(len(rows)), rng.randrange(len(cols))
        r2, c2 = min(len(rows) - 1, r1 + rng.randint(0, 2)), min(len(cols) - 1, c1 + rng.randint(0, 2))
        op = {"op": "merge", "sheet": sid, "range": [rows[r1], cols[c1], rows[r2], cols[c2]], "id": _id(rng)}
        if sh and sh["merges"] and rng.random() < 0.3:
            op["replaces"] = rng.sample(sh["merges"], 1)
        return op
    if kind == "unmerge":
        mids = list(c["merges"])
        return {"op": "unmerge", "id": _pick(rng, mids, "nomergex")}
    if kind == "field_add":
        fk = rng.choice(FIELD_IDS + ["f" + _id(rng)] * 4)
        fkind = rng.choice(["cell", "cell", "region", "repeat", "blob"])
        f = {"id": fk, "name": rng.choice(["名称", "", "  ", "ok", "x" * 120]), "kind": fkind}
        if rng.random() < 0.3:
            f["required"] = rng.choice([True, False, "yes"])
        if rng.random() < 0.2:
            f["empty_policy"] = rng.choice(["remove_row", "bogus"])
        op = {"op": "field_add", "sheet": sid, "field": f}
        op.update(_place(rng, fkind, rows, cols))
        return op
    if kind in ("field_set", "field_move", "field_del"):
        fk = _pick(rng, list(c["fields"]), "nofield1")
        if kind == "field_del":
            return {"op": "field_del", "field": fk}
        if kind == "field_set":
            ch = rng.choice([{"name": "新名"}, {"required": True}, {"required": None}, {"empty_policy": "error"}, {"name": ""}, {"kind": "x"}])
            return {"op": "field_set", "field": fk, "set": ch}
        f = c["fields"].get(fk)
        fsid = f["sheet"] if f else sid
        fsh = c["sheets"].get(fsid)
        fkind = rng.choice(["cell", "region", "repeat"]) if rng.random() < 0.4 else (f["kind"] if f else "cell")
        op = {"op": "field_move", "field": fk}
        if f is None or fkind != f["kind"] or rng.random() < 0.3:
            op["kind"] = fkind
        op.update(_place(rng, fkind, fsh["rows"] if fsh else [], fsh["cols"] if fsh else []))
        return op
    if kind == "sheet_add":
        n, m = rng.choice([0, 1, 2]), rng.choice([0, 1, 2])
        op = {"op": "sheet_add", "id": _id(rng), "name": rng.choice(NAMES), "rows": n, "cols": m,
              "row_ids": [_id(rng) for _ in range(n)], "col_ids": [_id(rng) for _ in range(m)]}
        return op
    if kind == "sheet_rename":
        return {"op": "sheet_rename", "sheet": sid, "name": rng.choice(NAMES)}
    if kind == "sheet_delete":
        return {"op": "sheet_delete", "sheet": sid}
    if kind == "sheet_restore":
        gone = [s for s in c["order"] if c["sheets"][s]["gone"]]
        op = {"op": "sheet_restore", "sheet": _pick(rng, gone, sid)}
        if rng.random() < 0.3:
            op["name"] = rng.choice(NAMES)
        return op
    if kind == "tx":
        return {"op": "tx", "ops": [_random_op(st, rng, depth + 1) for _ in range(rng.randint(1, 4))]}
    return rng.choice([{"op": "nope"}, {"op": "set", "sheet": sid}, {"op": "set", "sheet": sid, "row": _pick(rng, rows), "col": _pick(rng, cols), "q": 1},
                       "x", {"op": "tx", "ops": [{"op": "tx", "ops": [1]}]}])


def _place(rng, kind, rows, cols):
    if not rows or not cols:
        return {"row": "zzzzzzzz", "col": "zzzzzzzz"}
    r, c1, c2 = rng.randrange(len(rows)), rng.randrange(len(cols)), rng.randrange(len(cols))
    if kind == "region":
        r2 = min(len(rows) - 1, r + rng.randint(0, 2))
        return {"range": [rows[r], cols[c1], rows[r2], cols[c2]]}
    if kind == "repeat":
        return {"row": rows[r], "c1": cols[c1], "c2": cols[c2]}
    return {"row": rows[r], "col": cols[c1]}


def random_sequences() -> list:
    out = []
    for i, seed in enumerate(("basic", "repeat", "new_sheet", "three", "basic", "null_parts", "repeat", "three")):
        s = Seq(f"random_{i}_{seed}", seed, cap=None if i % 3 else 60)
        rng = random.Random(1000 + i)
        for _ in range(140):
            s.do(_random_op(s.st, rng), by=rng.choice(PEOPLE))
        out.append(s)
    return out


def build() -> dict:
    size_seq, grid_seq = sizes_and_appends()
    sequences = [cells_and_styles(), size_seq, grid_seq, merges(), fields(), id_less_fields(), sheets(), groups_and_invalid(),
                 cell_cap(), *empty_models(), *random_sequences()]
    return {"format": 1, "generator": "apps/docs/tests/sheet_live_corpus.py", "model": "apps/docs/synwevo_docs/collab_sheet.py",
            "sequences": [s.out() for s in sequences]}


def dump(corpus: dict) -> str:
    """Strict JSON (lone surrogates escaped), one step per line so a change reads as a diff."""
    def j(x):
        return json.dumps(x, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    lines = ["{" + j("format") + ":" + j(corpus["format"]) + "," + j("generator") + ":" + j(corpus["generator"]) + ","
             + j("model") + ":" + j(corpus["model"]) + "," + j("sequences") + ":["]
    for k, s in enumerate(corpus["sequences"]):
        lines.append("{" + j("name") + ":" + j(s["name"]) + "," + j("cap") + ":" + j(s["cap"]) + "," + j("content") + ":" + j(s["content"]) + ","
                     + j("steps") + ":[")
        for i, step in enumerate(s["steps"]):
            lines.append(j(step) + ("," if i < len(s["steps"]) - 1 else ""))
        lines.append("]," + j("final") + ":" + j(s["final"]) + "," + j("final_content") + ":" + j(s["final_content"])
                     + "}" + ("," if k < len(corpus["sequences"]) - 1 else ""))
    lines.append("]}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    text = dump(build())
    if "--write" in sys.argv:
        CORPUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        CORPUS_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {CORPUS_PATH} ({len(text) // 1024} KiB)")
    else:
        sys.stdout.write(text)
