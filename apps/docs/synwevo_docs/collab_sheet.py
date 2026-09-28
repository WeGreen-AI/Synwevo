"""The authoritative live state of a spreadsheet-style built-in template
(docs/platform/docs-sheet-live.md §2 and §3, batch B1 step ①).

Today two people on one table template each edit a private copy and only meet when one saves. The
live room keeps one shared state instead, and this module is that state: plain JSON the server
owns, in which every sheet, row, column and merged range has a stable id, cells and sizes are keyed
by those ids and fields are anchored on them. People's edits arrive as operations; each one is
checked against the generator's own rules *before* it applies (the same checks ``build_xlsx``,
``xlsx_add_slot`` and the workbook writer make), so the shared state can always be saved and
built. The same object — a cell's value, a field, a merged range — written by someone else since
the sender last looked is refused with who wrote what and when, never silently overwritten; a
stale operation cannot hide newer work either (deleting a sheet someone has since written on,
merging over values). Styles, row heights, column widths and sheet names are last-writer-wins per
attribute and attributed to the writer.

``project(state)`` renders the state back to the editor model every other part of Synwevo Docs
reads (A1 references, arrays in order): ``project(seed_state(e)) == normalize(e)``, which for the
models the editor and the API write is exactly what ``validate_editor`` returns. So a room nobody
typed in saves as "unchanged".

Pure: no I/O, no clock, no randomness. Ids come from a hash of the room's epoch and a counter, so
the same seed and the same operations always give the same state, byte for byte; the state is
strict JSON (no NaN) and its memory is bounded (``MAX_GRID_IDS``, ``MAX_LIVE_CELLS``, the
remembered-results budget below).

State layout (``state["content"]`` is what clients mirror; the rest is the server's bookkeeping)::

    content  keys    ["sheets"] when the model had that key
             order   sheet ids in tab order (deleted sheets stay here as tombstones until a save)
             sheets  {sid: {name, gone, keys, nul, rows: [row ids], cols: [col ids], nr, nc,
                            rh: {row id: raw row entry}, cw: {col id: raw col entry},
                            cells: {"rid:cid": {v, t, s}}, merges: [merge ids], fields: [field keys]}}
             merges  {mid: {sheet, at: [r1, c1, r2, c2] ids}}
             fields  {key: {sheet, kind, at: anchor ids, raw: the field's other attributes}}
    seq      the last accepted operation's number; kseq {object key: [seq, by]}; kfloor
    sheetw   {sid: {person: seq}} — the last write of each person on each sheet
    base     {content path: [value at the basis] or []} for every path changed since the basis
    basis    {seq, sheets: [[sid, name]], rows: {sid: [ids]}, cols: {sid: [ids]}} — position → id at
             the last seed / save / outside reconciliation
    clients  {client: {hi, at, r: {n: remembered result}}}; nfloor, pfloor {scope: n} — per-client de-duplication
             (a client named "scope/id" belongs to that scope — the room uses the person — whose
             forgotten clients raise only that scope's floor)

A patch is a list of ``{"p": path, "v": value}`` / ``{"p": path, "d": 1}`` / ``{"p": path,
"push": [...]}`` entries over ``content`` applied in order (``apply_patch``); ``before`` is its
inverse (``{"trim": n}`` undoes a push), applied in order to undo it. Path components are server
ids and field ids; field ids can never be ``__proto__`` or another ``Object.prototype`` name, but a
browser client should still keep ``content`` in ``Map`` / ``Object.create(null)`` objects.

Known limits of B1 (also in docs-sheet-live.md §8):

* An outside version is merged with ``merge_sheets``. On a sheet the room also changed, the outside
  version's sheet order and a shorter row or column list do not take effect (positions only grow
  in B1), and an outside rename of a sheet the room edited arrives as that sheet deleted — kept
  restorable, its editors told — plus a new sheet under the new name.
* Every message builds a position index of the sheets it addresses (O(rows + columns)), and one
  that merges, places a field or writes a value builds a tile index of that sheet's merged ranges
  and fields (O(merges + fields)): milliseconds on ordinary sheets, tens of milliseconds on sheets
  with tens of thousands of merged ranges. Within one group both are built once.
* Models the generator builds but the live state does not hold are refused at seeding and stay in
  CI-47's save-and-merge mode: a repeat field with ``c1`` but no ``c2`` (the generator reads ``c2``
  as 0), a row given as ``2.0``, more than 200 000 row + column ids or 200 000 cells.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import re

from synwevo_core.errors import ServiceError

from .editor_contract import validate_editor
from .engine import builtin as B
from .engine import tabular
from .engine.limits import LIMITS

FORMAT = 1
MAX_TX = 5000                 # operations in one atomic group
MAX_VALUE = 32767             # characters in one cell (Excel's limit)
MAX_FORMULA = 8192            # characters in one formula (Excel's limit)
MAX_APPEND = 5000             # rows or columns one operation may add
MAX_ROWS = 1_048_576          # Excel's grid
MAX_COLS = 16_384
MAX_SHEETS = 100              # live sheets (validate_editor's limit); tombstones count separately
MAX_GRID_IDS = 200_000        # row + column ids across all sheets: bounds the state's memory
MAX_LIVE_CELLS = 200_000      # cells the live state holds (the room admits far fewer, see §3)
MAX_MERGE_CELLS = B.MAX_MERGE_CELLS
MAX_SHEET_NAME = 31
MAX_FIELD_NAME = 100
MAX_NUMBER_FORMAT = 255
ROW_HEIGHT = (1, 1000)        # pixels, as the editor stores them
COL_WIDTH = (1, 2000)
CLIENT_WINDOW = 256           # sequence numbers remembered per client (older ones: resync)
RECORD_FULL = 16              # the newest results per client are remembered whole ...
RECORD_BYTES = 2048           # ... when their JSON is this small; otherwise as {ok, seq | code}
MAX_CLIENTS = 64              # clients remembered per room; together ≈ 3 MiB at most
MAX_CLIENTS_PER_SCOPE = 8     # ... and per scope ("scope/client": the room's person), so one cannot push the others out
MAX_AHEAD = 1 << 20           # a message's number is at most this far past the client's next one ...
MAX_NUMBER = (1 << 53) - 1    # ... and a number a browser can hold exactly
ECHO_MAX = 64                 # client input repeated in a refusal: short strings only

STYLE_KEYS = ("b", "i", "u", "sz", "color", "bg", "ha", "va", "wrap", "bd", "nf")
SHEET_KEYS = ("name", "cols", "rows", "cells", "merges", "fields")
NULLABLE = ("cols", "rows", "merges")          # the editor contract lets these be null
FIELD_KINDS = ("cell", "region", "repeat")
POSITIONAL = {"cell": ("ref",), "region": ("ref",), "repeat": ("row", "c1", "c2")}
FIELD_ATTRS = ("name", "required", "empty_policy")
# Field ids become object keys in the clients' mirror of the state: never a JavaScript prototype name
RESERVED_IDS = frozenset({"__proto__", "constructor", "prototype", "hasOwnProperty", "isPrototypeOf",
                          "propertyIsEnumerable", "toLocaleString", "toString", "valueOf", "__defineGetter__",
                          "__defineSetter__", "__lookupGetter__", "__lookupSetter__"})
_ID = re.compile(r"[0-9a-z]{8}")
_B36 = "0123456789abcdefghijklmnopqrstuvwxyz"
_BAD_TITLE = re.compile(r"[\\/*?:\[\]]")
# What the workbook writer refuses in any text it writes (openpyxl's ILLEGAL_CHARACTERS for cell
# values; lxml for sheet names and number formats; UTF-8 for lone surrogates): C0 controls other
# than tab / line feed / carriage return, U+FFFE, U+FFFF and surrogate halves. Measured, not guessed.
_BAD_TEXT = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]")


class SeedError(ValueError):
    """The model cannot be held live (it breaks an invariant or the live state's size bounds): the
    room stays in the save-and-merge mode of CI-47 for this template."""

    def __init__(self, issues: list[dict]):
        super().__init__(", ".join(sorted({i["code"] for i in issues})) or "invalid")
        self.issues = issues


class _Refused(Exception):
    def __init__(self, code: str, **detail):
        super().__init__(code)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------------------------ JSON helpers
def _copy(x):
    if isinstance(x, dict):
        return {k: _copy(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_copy(v) for v in x]
    return x


def _eq(a, b) -> bool:
    """JSON equality that tells 1 from 1.0 from True (Python's == does not)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    return a == b


def _union(a, b) -> list:
    return list(a) + [k for k in b if k not in a]


def _num(x) -> bool:
    return type(x) in (int, float) and math.isfinite(x)


def _hit(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _title(name) -> str:
    """The worksheet title ``build_xlsx`` makes of a sheet name (compared ignoring case, CI-117)."""
    return _BAD_TITLE.sub("_", (name or "Sheet").strip())[:31] or "Sheet"


def _bad_text(s) -> bool:
    return isinstance(s, str) and _BAD_TEXT.search(s) is not None


def _unwritable(cell: dict) -> str | None:
    """What of a (contract-valid) cell the generator could not write: text with characters the
    workbook writer refuses, or a number too large for a double — decided by the generator's own
    value typing (``builtin._typed``), so the answer is the generator's."""
    v, t = cell.get("v"), cell.get("t")
    if v not in (None, ""):
        try:
            typed = B._typed(v, t)
        except (B.BuiltinError, ValueError, TypeError, OverflowError):
            return "number"
        if isinstance(typed, str):
            if _BAD_TEXT.search(typed):
                return "characters"
        elif type(typed) is int:
            try:
                float(typed)
            except OverflowError:
                return "number"
    if _bad_text((cell.get("s") or {}).get("nf")):
        return "characters"
    return None


def _echo(x):
    """Client input repeated in a refusal: short clean strings and small integers only — never NaN,
    never a megabyte of someone's JSON — so what is remembered stays small and strict JSON."""
    if x is None or isinstance(x, bool):
        return x
    if type(x) is int:
        return x if abs(x) < 2 ** 53 else None
    if isinstance(x, str) and len(x) <= ECHO_MAX and not _BAD_TEXT.search(x):
        return x
    return None


def _cell_ref(ref):
    if not isinstance(ref, str):
        return None
    try:
        r, c = tabular.parse_cell(ref)
    except ValueError:
        return None
    return (r, c) if r >= 0 and c >= 0 else None


def _range_ref(ref):
    if not isinstance(ref, str):
        return None
    try:
        r1, c1, r2, c2 = tabular.parse_range(ref)
    except ValueError:
        return None
    return (r1, c1, r2, c2) if r1 >= 0 and c1 >= 0 else None


def _in_grid(rect) -> bool:
    return rect[2] < MAX_ROWS and rect[3] < MAX_COLS


def _limit(v, t):
    """Which size limit a cell value breaks, if any."""
    if isinstance(v, str):
        if len(v) > MAX_VALUE:
            return "value"
        if t != "text" and v.startswith("=") and len(v) > MAX_FORMULA:
            return "formula"
    return None


def _field_rect(kind, pos):
    if kind == "cell":
        return (pos[0], pos[1], pos[0], pos[1])
    if kind == "region":
        return tuple(pos)
    return (pos[0], pos[1], pos[0], pos[2])


def _field_issue(kind, rect, merges, others):
    """The rule of ``xlsx_add_slot`` a field at ``rect`` breaks against the sheet's merged ranges
    and its other fields, in the order that function checks them: ``(code, the other thing)``.
    ``merges`` / ``others`` may be just the candidates near ``rect`` (see :class:`_Geo`)."""
    r1, c1, _r2, _c2 = rect
    if kind == "cell":
        for m, ident in merges:
            if m[0] <= r1 <= m[2] and m[1] <= c1 <= m[3] and (r1, c1) != (m[0], m[1]):
                return "merged_inner", ident
    elif kind == "region":
        for m, ident in merges:
            if _hit(m, rect):
                return "merged", ident
    else:
        for m, ident in merges:
            if m[0] <= r1 <= m[2] and m[0] != m[2]:
                return "vertical_merge", ident
        for okind, orect, ident in others:
            if okind == "repeat" and orect[0] == r1:
                return "duplicate_repeat", ident
    for _okind, orect, ident in others:
        if _hit(orect, rect):
            return "field_overlap", ident
    return None


class _Geo:
    """The merged ranges and fields of one sheet, findable by position: each is filed under the
    64×64-cell tiles it touches (a very large one is kept in a short list checked every time), so a
    check costs the size of the neighbourhood, not of the sheet. Candidates come back in the order
    they were added, which is the sheet's list order — the same "first" ``xlsx_add_slot`` reports."""

    T = 64
    BIG = 1024                                        # tiles; anything larger goes on the short list

    def __init__(self):
        self.mt: dict = {}
        self.ft: dict = {}
        self.mbig: set = set()
        self.fbig: set = set()
        self.mr: dict = {}                            # merge -> rect
        self.fr: dict = {}                            # field -> (kind, rect)
        self.order: dict = {}
        self.rep: dict = {}                           # row -> repeat fields on it
        self.reprows: list = []
        self.k = 0
        self.maxc = 0

    def _span(self, rect):
        t = self.T
        return rect[0] // t, rect[1] // t, rect[2] // t, rect[3] // t

    def _file(self, table, big, ident, rect, add):
        a, b, c, d = self._span(rect)
        if (c - a + 1) * (d - b + 1) > self.BIG:
            (big.add if add else big.discard)(ident)
            return
        for tr in range(a, c + 1):
            for tc in range(b, d + 1):
                if add:
                    table.setdefault((tr, tc), set()).add(ident)
                else:
                    s = table.get((tr, tc))
                    if s is not None:
                        s.discard(ident)
                        if not s:
                            del table[(tr, tc)]

    def _near(self, table, big, rects, rect):
        a, b, c, d = self._span(rect)
        if (c - a + 1) * (d - b + 1) > len(rects):
            return rects.keys()
        found = set(big)
        for tr in range(a, c + 1):
            for tc in range(b, d + 1):
                s = table.get((tr, tc))
                if s:
                    found |= s
        return found

    def add_merge(self, mid, rect):
        self.k += 1
        self.order[("m", mid)] = self.k
        self.mr[mid] = rect
        self.maxc = max(self.maxc, rect[3])
        self._file(self.mt, self.mbig, mid, rect, True)

    def drop_merge(self, mid):
        rect = self.mr.pop(mid, None)
        if rect is not None:
            self._file(self.mt, self.mbig, mid, rect, False)
            del self.order[("m", mid)]

    def add_field(self, fk, kind, rect):
        self.k += 1
        self.order[("f", fk)] = self.k
        self.fr[fk] = (kind, rect)
        self._file(self.ft, self.fbig, fk, rect, True)
        if kind == "repeat":
            if rect[0] not in self.rep:
                self.rep[rect[0]] = set()
                bisect.insort(self.reprows, rect[0])
            self.rep[rect[0]].add(fk)

    def drop_field(self, fk):
        got = self.fr.pop(fk, None)
        if got is None:
            return
        kind, rect = got
        self._file(self.ft, self.fbig, fk, rect, False)
        del self.order[("f", fk)]
        if kind == "repeat":
            s = self.rep[rect[0]]
            s.discard(fk)
            if not s:
                del self.rep[rect[0]]
                self.reprows.remove(rect[0])

    def merges_in(self, rect, skip=()):
        found = [(self.mr[m], m) for m in self._near(self.mt, self.mbig, self.mr, rect) if m not in skip and _hit(self.mr[m], rect)]
        return sorted(found, key=lambda x: self.order[("m", x[1])])

    def fields_in(self, rect, skip=None):
        found = [(*self.fr[f], f) for f in self._near(self.ft, self.fbig, self.fr, rect) if f != skip and _hit(self.fr[f][1], rect)]
        return sorted(found, key=lambda x: self.order[("f", x[2])])

    def repeats_between(self, r1, r2, skip=None):
        found = []
        i = bisect.bisect_left(self.reprows, r1)
        while i < len(self.reprows) and self.reprows[i] <= r2:
            found += [(*self.fr[f], f) for f in self.rep[self.reprows[i]] if f != skip]
            i += 1
        return sorted(found, key=lambda x: self.order[("f", x[2])])

    def field_issue(self, kind, rect, skip=None):
        """``_field_issue`` for a field placed at ``rect``: ``(code, ("merge" | "field", ident))``."""
        if kind == "repeat":
            merges = [x for x in self.merges_in((rect[0], 0, rect[0], self.maxc)) if x[0][0] != x[0][2]]
            near = {x[2]: x for x in self.fields_in(rect, skip)}
            near.update({x[2]: x for x in self.repeats_between(rect[0], rect[0], skip)})
            others = sorted(near.values(), key=lambda x: self.order[("f", x[2])])
        else:
            merges = self.merges_in(rect)
            others = self.fields_in(rect, skip)
        return _field_issue(kind, rect, [(m, ("merge", i)) for m, i in merges], [(k, r, ("field", i)) for k, r, i in others])

    def merge_issue(self, rect):
        """The first field (in list order) that a merged range at ``rect`` would break:
        ``(code, field, field rect)``. A repeat row is broken by any taller merge across it, even
        outside its columns (``xlsx_add_slot`` checks every merge)."""
        near = {x[2]: x for x in self.fields_in(rect)}
        if rect[0] != rect[2]:
            near.update({x[2]: x for x in self.repeats_between(rect[0], rect[2])})
        for kind, frect, fk in sorted(near.values(), key=lambda x: self.order[("f", x[2])]):
            hit = _field_issue(kind, frect, [(rect, None)], [])
            if hit:
                return hit[0], fk, frect
        return None


def _contract_issue(e: ServiceError, extra: dict | None = None) -> dict:
    where = str(e.detail.get("field") or "")
    issue = {"code": "invalid", "reason": "contract", "message": e.message, "field": where, **(extra or {})}
    m = re.match(r"editor\.sheets\.(\d+)\.", where)
    if m:
        issue["index"] = int(m.group(1))
    return issue


# ------------------------------------------------------------------------------------ invariants
def _parse_sheet(sh: dict, bad) -> dict:
    """Positions of everything in one sheet of an editor model; ``bad(code, **detail)`` hears about
    what cannot be read, placed or written."""
    out = {"nr": 0, "nc": 0, "cells": [], "merges": [], "fields": [], "rext": 0, "cext": 0}
    for part, key, cap in (("cols", "w", MAX_COLS), ("rows", "h", MAX_ROWS)):
        items = sh.get(part)
        if items is None:
            continue
        if not isinstance(items, list):
            bad("invalid", reason=part)
            continue
        if len(items) > cap:
            bad("too_large", what=part, max=cap)
        for k, item in enumerate(items):
            if item is None:
                continue
            if not isinstance(item, dict):
                bad("invalid", reason=part, position=k)
            elif item.get(key) not in (None, 0) and not _num(item.get(key)):
                bad("invalid", reason=part, position=k)
        out["nc" if part == "cols" else "nr"] = len(items)
    seen = set()
    for ref, cell in (sh.get("cells") or {}).items():
        p = _cell_ref(ref)
        if p is None:
            bad("invalid", reason="cell_ref", ref=_echo(ref))
            continue
        if not _in_grid(p + p):
            bad("invalid", reason="out_of_grid", ref=_echo(ref))
            continue
        a1 = tabular.cell_ref(*p)
        if p in seen:
            bad("invalid", reason="duplicate_cell", ref=a1)
            continue
        seen.add(p)
        lim = _limit(cell.get("v"), cell.get("t"))
        if lim:
            bad("too_large", what=lim, ref=a1, max=MAX_VALUE if lim == "value" else MAX_FORMULA)
        nf = (cell.get("s") or {}).get("nf")
        if nf is not None and (not isinstance(nf, str) or len(nf) > MAX_NUMBER_FORMAT):
            bad("invalid", reason="number_format", ref=a1)
        what = _unwritable(cell)
        if what:
            bad("invalid", reason="unwritable", what=what, ref=a1)
        out["cells"].append((p[0], p[1], cell))
    merges = sh.get("merges")
    if merges is not None and not isinstance(merges, list):
        bad("invalid", reason="merges")
        merges = []
    for m in merges or []:
        rect = _range_ref(m)
        if rect is None or not _in_grid(rect):
            bad("invalid", reason="merge_ref", merge=_echo(m))
            continue
        area = (rect[2] - rect[0] + 1) * (rect[3] - rect[1] + 1)
        if area < 2:
            bad("invalid", reason="single_cell_merge", merge=_echo(m))
            continue
        if area > MAX_MERGE_CELLS:
            bad("too_large", what="merge", merge=_echo(m), max=MAX_MERGE_CELLS)
            continue
        out["merges"].append(rect)
    for j, f in enumerate(sh.get("fields") or []):
        kind = f.get("kind", "cell")
        at = {"field": _echo(f.get("id")), "position": j}
        if kind not in FIELD_KINDS:
            bad("invalid", reason="field_kind", **at)
            continue
        name = f.get("name")
        if name is not None and (not isinstance(name, str) or _bad_text(name)):
            bad("invalid", reason="field_name", **at)
            continue
        if kind == "cell":
            pos = _cell_ref(f.get("ref"))
        elif kind == "region":
            pos = _range_ref(f.get("ref"))
        else:
            row, c1, c2 = f.get("row"), f.get("c1", 0), f.get("c2", 0)
            ok = all(type(x) is int and x >= 0 for x in (row, c1, c2)) and c1 <= c2
            pos = (row, c1, c2) if ok else None
        if pos is None:
            bad("invalid", reason="field_ref", **at)
            continue
        rect = _field_rect(kind, pos)
        if not _in_grid(rect):
            bad("invalid", reason="out_of_grid", **at)
            continue
        out["fields"].append((f, kind, pos, rect))
    rext = [out["nr"]] + [r + 1 for r, _c, _v in out["cells"]] + [m[2] + 1 for m in out["merges"]] + [x[3][2] + 1 for x in out["fields"]]
    cext = [out["nc"]] + [c + 1 for _r, c, _v in out["cells"]] + [m[3] + 1 for m in out["merges"]] + [x[3][3] + 1 for x in out["fields"]]
    out["rext"], out["cext"] = max(rext), max(cext)
    return out


def sheet_invariants(editor) -> list[dict]:
    """Everything that keeps a table model from being saved and generated, as a list of issues
    (empty when it can be). The generator's rules — ``validate_editor``, ``build_xlsx`` (including
    sheet names equal ignoring case, CI-117), ``xlsx_add_slot``'s field checks in its order, and
    the characters and numbers the workbook writer refuses — plus what the design adds on top:
    merged ranges do not overlap and span two or more cells, a cell holds at most 32767 characters
    and a formula at most 8192, references stay inside Excel's grid, no cell is written twice.

    Codes: ``invalid``, ``too_large``, ``sheet_name_taken``, ``merge_overlap``, ``field_overlap``,
    ``merged_inner``, ``merged``, ``vertical_merge``, ``duplicate_repeat``. Each issue carries the
    sheet's ``index`` and name where it belongs to one. Cost grows with the model, not its square."""
    try:
        validate_editor("builtin_sheet", editor)
    except ServiceError as e:
        return [_contract_issue(e)]
    issues: list[dict] = []
    titles: dict[str, int] = {}
    for i, sh in enumerate(editor.get("sheets") or []):
        name = sh.get("name")
        where = {"index": i, "sheet": _echo(name) if isinstance(name, str) else None}

        def bad(code, **detail):
            issues.append({"code": code, **where, **detail})

        if name is not None and (not isinstance(name, str) or _bad_text(name)):
            bad("invalid", reason="sheet_name")
        else:
            t = _title(name).casefold()
            if t in titles:
                bad("sheet_name_taken", name=_title(name), other=titles[t])
            else:
                titles[t] = i
        p = _parse_sheet(sh, bad)
        geo = _Geo()
        for k, m in enumerate(p["merges"]):
            hit = geo.merges_in(m)
            if hit:
                bad("merge_overlap", merge=tabular.range_ref(*m), other=tabular.range_ref(*hit[0][0]))
            geo.add_merge(k, m)
        labels = {}
        for j, (f, kind, _pos, rect) in enumerate(p["fields"]):
            hit = geo.field_issue(kind, rect)
            if hit:
                code, (what, ident) = hit
                other = tabular.range_ref(*geo.mr[ident]) if what == "merge" else labels[ident]
                bad(code, field=_echo(f.get("id")), name=_echo(f.get("name")), other=other)
            labels[j] = _echo(f.get("id") or f.get("name"))
            geo.add_field(j, kind, rect)
    return issues


def holdable(editor, *, max_cells: int | None = None) -> list[dict]:
    """Why the live state could not hold ``editor`` (what ``seed_state`` and ``reconcile`` raise
    ``SeedError`` for), without allocating anything; ``[]`` when it can. ``max_cells``: a room's
    own, lower bound on cells (live sheets)."""
    issues = sheet_invariants(editor)
    if issues:
        return issues
    model = normalize(editor)
    issues = _size_issues(model)
    if not issues and max_cells is not None:
        cells = sum(len(sh.get("cells") or {}) for sh in model.get("sheets") or [])
        if cells > max_cells:
            issues = [{"code": "too_large", "what": "cells", "max": max_cells, "size": cells}]
    return issues


def normalize(editor) -> dict:
    """What the live state renders a model as: ``validate_editor``'s result with positions written
    the one way the editor writes them (``B2``, ``A1:C3``). For the models the editor and the API
    write this *is* ``validate_editor``'s result (null rows / columns / merges stay null)."""
    out = _copy(validate_editor("builtin_sheet", editor))
    for sh in out.get("sheets") or []:
        if isinstance(sh.get("cells"), dict):
            cells = {}
            for ref, cell in sh["cells"].items():
                p = _cell_ref(ref)
                cells[tabular.cell_ref(*p) if p else ref] = cell
            sh["cells"] = cells
        if isinstance(sh.get("merges"), list):
            sh["merges"] = [tabular.range_ref(*r) if (r := _range_ref(m)) else m for m in sh["merges"]]
        for f in sh.get("fields") or []:
            kind = f.get("kind", "cell")
            if kind == "cell" and (p := _cell_ref(f.get("ref"))):
                f["ref"] = tabular.cell_ref(*p)
            elif kind == "region" and (r := _range_ref(f.get("ref"))):
                f["ref"] = tabular.range_ref(*r)
    return out


# ------------------------------------------------------------------------------------ ids
def _hid(epoch: str, n: int) -> str:
    h = int.from_bytes(hashlib.sha256(f"{epoch}/{n}".encode()).digest()[:8], "big")
    out = []
    for _ in range(8):
        h, r = divmod(h, 36)
        out.append(_B36[r])
    return "".join(out)


def _gen(state: dict, taken) -> str:
    while True:
        state["ctr"] += 1
        x = _hid(state["epoch"], state["ctr"])
        if not taken(x):
            return x


def _own_key(f: dict) -> str | None:
    """A field's id when it can serve as its key in the state (a string, not a prototype name)."""
    fid = f.get("id")
    return fid if isinstance(fid, str) and fid not in RESERVED_IDS else None


# ------------------------------------------------------------------------------------ model <-> content
def _content(editor: dict, alloc) -> dict:
    """The content of a (valid, normalized) editor model, with ids from ``alloc``."""
    content = {"keys": ["sheets"] if "sheets" in editor else [], "order": [], "sheets": {}, "merges": {}, "fields": {}}
    for i, sh in enumerate(editor.get("sheets") or []):
        sid = alloc.sheet(i, sh)
        p = _parse_sheet(sh, _never)
        rows = alloc.rows(sid, p["rext"])
        cols = alloc.cols(sid, p["cext"])
        s = {"name": sh.get("name"), "gone": False, "keys": list(sh), "nul": [k for k in NULLABLE if k in sh and sh[k] is None],
             "rows": rows, "cols": cols, "nr": p["nr"], "nc": p["nc"],
             "rh": {rows[k]: _copy(v) for k, v in enumerate(sh.get("rows") or [])},
             "cw": {cols[k]: _copy(v) for k, v in enumerate(sh.get("cols") or [])},
             "cells": {f"{rows[r]}:{cols[c]}": _copy(cell) for r, c, cell in p["cells"]},
             "merges": [], "fields": []}
        for m in p["merges"]:
            at = [rows[m[0]], cols[m[1]], rows[m[2]], cols[m[3]]]
            mid = alloc.merge(sid, at)
            content["merges"][mid] = {"sheet": sid, "at": at}
            s["merges"].append(mid)
        for f, kind, pos, _rect in p["fields"]:
            if kind == "cell":
                at = [rows[pos[0]], cols[pos[1]]]
            elif kind == "region":
                at = [rows[pos[0]], cols[pos[1]], rows[pos[2]], cols[pos[3]]]
            else:
                at = [rows[pos[0]], cols[pos[1]], cols[pos[2]]]
            fk = alloc.field(sid, f, kind, at)
            raw = {k: (None if k in POSITIONAL[kind] else _copy(v)) for k, v in f.items()}
            content["fields"][fk] = {"sheet": sid, "kind": kind, "at": at, "raw": raw}
            s["fields"].append(fk)
        content["sheets"][sid] = s
        content["order"].append(sid)
    return content


def _never(code, **detail):
    raise ValueError(f"model is not valid: {code} {detail}")


class _SeedAlloc:
    def __init__(self, state: dict):
        self.st = state
        self.sids: set = set()
        self.grid: dict = {}
        self.mids: set = set()
        self.fks: set = set()

    def sheet(self, _i, _sh):
        sid = _gen(self.st, lambda x: x in self.sids)
        self.sids.add(sid)
        self.grid[sid] = set()
        return sid

    def _ids(self, sid, n):
        taken = self.grid[sid]
        out = []
        for _ in range(n):
            x = _gen(self.st, lambda y: y in taken)
            taken.add(x)
            out.append(x)
        return out

    def rows(self, sid, n):
        return self._ids(sid, n)

    cols = rows

    def merge(self, _sid, _at):
        mid = _gen(self.st, lambda x: x in self.mids)
        self.mids.add(mid)
        return mid

    def field(self, _sid, f, _kind, _at):
        fk = _own_key(f) or "~" + _gen(self.st, lambda x: "~" + x in self.fks)
        self.fks.add(fk)
        return fk


def _grid_ids(content: dict) -> int:
    return sum(len(s["rows"]) + len(s["cols"]) for s in content["sheets"].values())


def _cells(content: dict, *, live_only: bool = False) -> int:
    return sum(len(s["cells"]) for s in content["sheets"].values() if not (live_only and s["gone"]))


def _cell_cap() -> int:
    return min(LIMITS.max_cells, MAX_LIVE_CELLS)


def _size_issues(model: dict) -> list[dict]:
    """The live state's own bounds on a (valid, normalized) model: row + column ids and cells.
    Checked before anything is allocated."""
    need = cells = 0
    for sh in model.get("sheets") or []:
        p = _parse_sheet(sh, _never)
        need += p["rext"] + p["cext"]
        cells += len(p["cells"])
    issues = []
    if need > MAX_GRID_IDS:
        issues.append({"code": "too_large", "what": "grid", "max": MAX_GRID_IDS, "size": need})
    if cells > _cell_cap():
        issues.append({"code": "too_large", "what": "cells", "max": _cell_cap(), "size": cells})
    return issues


def _basis(content: dict, seq: int) -> dict:
    live = [sid for sid in content["order"] if not content["sheets"][sid]["gone"]]
    return {"seq": seq, "sheets": [[sid, content["sheets"][sid]["name"]] for sid in live],
            "rows": {sid: list(content["sheets"][sid]["rows"]) for sid in live},
            "cols": {sid: list(content["sheets"][sid]["cols"]) for sid in live}}


def seed_state(editor, *, epoch: str = "0") -> dict:
    """The live state of a saved model. ``epoch`` names this seeding (a new one every time the room
    is seeded afresh) and salts every id, so clients holding ids from another seeding are told
    apart. Raises :class:`SeedError` for a model the room cannot hold."""
    issues = sheet_invariants(editor)
    if issues:
        raise SeedError(issues)
    model = normalize(editor)
    issues = _size_issues(model)
    if issues:
        raise SeedError(issues)
    state = {"format": FORMAT, "epoch": str(epoch), "seq": 0, "ctr": 0, "content": None,
             "kseq": {}, "kfloor": 0, "sheetw": {}, "base": {}, "basis": None, "clients": {}, "nfloor": 0, "pfloor": {}}
    state["content"] = _content(model, _SeedAlloc(state))
    state["basis"] = _basis(state["content"], 0)
    return state


def _positions(sh: dict):
    return {x: i for i, x in enumerate(sh["rows"])}, {x: i for i, x in enumerate(sh["cols"])}


def _field_out(f: dict, rp: dict, cp: dict, tag: str | None = None) -> dict:
    d = _copy(f["raw"])
    if tag is not None and not isinstance(d.get("id"), str):
        d["id"] = tag                                     # an id-less field, named by its key for merging
    at = f["at"]
    if f["kind"] == "cell":
        d["ref"] = tabular.cell_ref(rp[at[0]], cp[at[1]])
    elif f["kind"] == "region":
        d["ref"] = tabular.range_ref(rp[at[0]], cp[at[1]], rp[at[2]], cp[at[3]])
    else:
        for k, v in (("row", rp[at[0]]), ("c1", cp[at[1]]), ("c2", cp[at[2]])):
            if k in d:
                d[k] = v
    return d


def _sheet_out(content: dict, sid: str, name=None, keyed: bool = False) -> dict:
    sh = content["sheets"][sid]
    rp, cp = _positions(sh)

    def value(k):
        if k == "name":
            return sh["name"] if name is None else name
        if k == "cols":
            return [_copy(sh["cw"][c]) for c in sh["cols"][:sh["nc"]]]
        if k == "rows":
            return [_copy(sh["rh"][r]) for r in sh["rows"][:sh["nr"]]]
        if k == "cells":
            out = {}
            for ck, cell in sh["cells"].items():
                r, c = ck.split(":")
                out[tabular.cell_ref(rp[r], cp[c])] = _copy(cell)
            return out
        if k == "merges":
            out = []
            for mid in sh["merges"]:
                at = content["merges"][mid]["at"]
                out.append(tabular.range_ref(rp[at[0]], cp[at[1]], rp[at[2]], cp[at[3]]))
            return out
        return [_field_out(content["fields"][fk], rp, cp, fk if keyed else None) for fk in sh["fields"]]

    present = {"name": sh["name"] is not None or name is not None, "cols": sh["nc"] > 0, "rows": sh["nr"] > 0,
               "cells": bool(sh["cells"]), "merges": bool(sh["merges"]), "fields": bool(sh["fields"])}
    nul = sh.get("nul") or ()
    keys = list(sh["keys"]) + [k for k in SHEET_KEYS if k not in sh["keys"] and present[k]]
    return {k: (None if k in nul and not present[k] else value(k)) for k in keys}


def _project(content: dict, *, keyed: bool = False) -> dict:
    sheets = [_sheet_out(content, sid, sid if keyed else None, keyed) for sid in content["order"] if not content["sheets"][sid]["gone"]]
    return {"sheets": sheets} if ("sheets" in content["keys"] or sheets) else {}


def project(state: dict) -> dict:
    """The editor model of the live state (what a save writes as the new version)."""
    return _project(state["content"])


def dirty(state: dict) -> bool:
    """Whether a save would write something new: the rendered model differs from the one at the
    last seed / save / outside version. (A sheet added and deleted again is not a change.) Costs a
    rendering of the state when anything was touched since the basis."""
    if not state["base"]:
        return False
    return not _eq(_project(state["content"]), _project(_revert(state)))


def apply_patch(content: dict, patch: list[dict]) -> dict:
    """Apply a patch (or a ``before`` list) to a copy of ``content`` in place — what a client does
    with the server's patches. Entries under a parent that no longer exists are skipped."""
    for e in patch:
        node = content
        for k in e["p"][:-1]:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if node is None:
            continue
        key = e["p"][-1]
        if "v" in e:
            node[key] = _copy(e["v"])
        elif "d" in e:
            node.pop(key, None)
        elif "push" in e:
            node[key].extend(e["push"])
        elif "trim" in e:
            del node[key][e["trim"]:]
    return content


# ------------------------------------------------------------------------------------ transactions
_ABSENT = object()


class _Tx:
    """One accepted-or-refused unit: every write goes through here so it can be undone as a whole,
    turned into the patch and its inverse, and tracked against the basis."""

    def __init__(self, state: dict, by: str, expect: int | None, cap: int | None = None):
        self.st, self.by, self.expect = state, by, expect
        self.c = state["content"]
        self.cap = _cell_cap() if cap is None else min(_cell_cap(), cap)   # cells the state may hold after this group
        self.seq = state["seq"] + 1
        self.undo: list = []
        self.first: dict = {}
        self.pushed: dict = {}
        self.order: list = []
        self.pos: dict = {}
        self.geos: dict = {}
        self.ctr0 = state["ctr"]

    # -- raw writes
    def _node(self, path):
        node = self.c
        for k in path[:-1]:
            node = node[k]
        return node

    def _touch(self, path, old):
        if path not in self.first and path not in self.pushed:
            self.first[path] = [] if old is _ABSENT else [old]
            self.order.append(path)

    def put(self, path: tuple, value):
        node = self._node(path)
        old = node.get(path[-1], _ABSENT)
        self.undo.append(("put", path, old))
        if path in self.pushed:                           # appended to earlier in this group, now replaced whole
            self.first[path] = [list(old[:self.pushed.pop(path)])]
        else:
            self._touch(path, old)
        node[path[-1]] = value

    def delete(self, path: tuple):
        node = self._node(path)
        old = node.get(path[-1], _ABSENT)
        if old is _ABSENT:
            return
        self.undo.append(("put", path, old))
        self._touch(path, old)
        del node[path[-1]]

    def push(self, path: tuple, items: list):
        lst = self._node(path)[path[-1]]
        if path not in self.first and path not in self.pushed:
            self.pushed[path] = len(lst)
            self.order.append(path)
        lst.extend(items)
        self.undo.append(("push", path, len(items)))

    def kset(self, key: str, sid: str):
        """Record the sender as the last writer of an object on sheet ``sid``."""
        ks = self.st["kseq"]
        self.undo.append(("k", key, ks.get(key)))
        ks[key] = [self.seq, self.by]
        sw = self.st["sheetw"]
        self.undo.append(("w", sid, sw.get(sid, {}).get(self.by)))
        sw.setdefault(sid, {})[self.by] = self.seq

    def rollback(self):
        ks, sw = self.st["kseq"], self.st["sheetw"]
        for kind, a, b in reversed(self.undo):
            if kind == "put":
                node = self._node(a)
                if b is _ABSENT:
                    node.pop(a[-1], None)
                else:
                    node[a[-1]] = b
            elif kind == "push":
                lst = self._node(a)[a[-1]]
                del lst[len(lst) - b:]
            elif kind == "k":
                if b is None:
                    ks.pop(a, None)
                else:
                    ks[a] = b
            else:
                if b is None:
                    sw.get(a, {}).pop(self.by, None)
                    if not sw.get(a, True):
                        del sw[a]
                else:
                    sw[a][self.by] = b
        self.st["ctr"] = self.ctr0
        self.pos.clear()
        self.geos.clear()

    def commit(self) -> tuple[list, list]:
        patch, before = [], []
        base = self.st["base"]
        for path in self.order:
            if any(path[:k] in self.first for k in range(1, len(path))):
                continue                                  # a parent was written whole: it carries this
            node = self._node(path)
            if path in self.pushed:
                lst, n0 = node[path[-1]], self.pushed[path]
                if len(lst) == n0:
                    continue
                patch.append({"p": list(path), "push": list(lst[n0:])})
                before.append({"p": list(path), "trim": n0})
                orig, cur = [lst[:n0]], [lst]
            else:
                cur = [node[path[-1]]] if path[-1] in node else []
                orig = self.first[path]
                if _eq(orig, cur):
                    continue
                patch.append({"p": list(path), "v": _copy(cur[0])} if cur else {"p": list(path), "d": 1})
                before.append({"p": list(path), "v": _copy(orig[0])} if orig else {"p": list(path), "d": 1})
            key = "/".join(path)
            if key not in base:
                base[key] = [_copy(orig[0])] if orig else []
            if _eq(base[key], cur):
                del base[key]
        before.reverse()
        return patch, before

    # -- lookups
    def sheet(self, op, *, live=True):
        sid = op.get("sheet")
        if not isinstance(sid, str):
            raise _Refused("invalid", reason="sheet")
        sh = self.c["sheets"].get(sid)
        if sh is None:
            raise _Refused("not_found", what="sheet", sheet=_echo(sid))
        if live and sh["gone"]:
            raise _Refused("sheet_deleted", sheet=sid, name=sh["name"], by=self.who(f"x/{sid}"))
        return sid, sh

    def positions(self, sid):
        p = self.pos.get(sid)
        if p is None:
            p = self.pos[sid] = _positions(self.c["sheets"][sid])
        return p

    def geo(self, sid) -> _Geo:
        """The tile index of a sheet's merged ranges and fields, built once per group."""
        g = self.geos.get(sid)
        if g is None:
            g = self.geos[sid] = _Geo()
            rp, cp = self.positions(sid)
            sh = self.c["sheets"][sid]
            for mid in sh["merges"]:
                at = self.c["merges"][mid]["at"]
                g.add_merge(mid, (rp[at[0]], cp[at[1]], rp[at[2]], cp[at[3]]))
            for fk in sh["fields"]:
                g.add_field(fk, self.c["fields"][fk]["kind"], self.field_rect(sid, self.c["fields"][fk]))
        return g

    def row(self, sid, rid, *, key="row"):
        p = self.positions(sid)[0].get(rid) if isinstance(rid, str) else None
        if p is None:
            raise _Refused("not_found", what="row", sheet=sid, **{key: _echo(rid)})
        return p

    def col(self, sid, cid, *, key="col"):
        p = self.positions(sid)[1].get(cid) if isinstance(cid, str) else None
        if p is None:
            raise _Refused("not_found", what="col", sheet=sid, **{key: _echo(cid)})
        return p

    def rect(self, sid, ids):
        if not isinstance(ids, list) or len(ids) != 4:
            raise _Refused("invalid", reason="range")
        r1, r2 = self.row(sid, ids[0]), self.row(sid, ids[2])
        c1, c2 = self.col(sid, ids[1]), self.col(sid, ids[3])
        return (min(r1, r2), min(c1, c2), max(r1, r2), max(c1, c2))

    def ids_of(self, sid, rect):
        sh = self.c["sheets"][sid]
        return [sh["rows"][rect[0]], sh["cols"][rect[1]], sh["rows"][rect[2]], sh["cols"][rect[3]]]

    def who(self, key):
        k = self.st["kseq"].get(key)
        return k[1] if k else None

    def field_rect(self, sid, f):
        rp, cp = self.positions(sid)
        at = f["at"]
        if f["kind"] == "cell":
            return (rp[at[0]], cp[at[1]], rp[at[0]], cp[at[1]])
        if f["kind"] == "region":
            return (rp[at[0]], cp[at[1]], rp[at[2]], cp[at[3]])
        return (rp[at[0]], cp[at[1]], rp[at[0]], cp[at[2]])

    def describe(self, sid, what, ident):
        if what == "merge":
            at = self.c["merges"][ident]["at"]
            rp, cp = self.positions(sid)
            return {"merge": ident, "range": tabular.range_ref(rp[at[0]], cp[at[1]], rp[at[2]], cp[at[3]]), "by": self.who(f"m/{ident}")}
        return {"field": ident, "name": self.c["fields"][ident]["raw"].get("name"), "by": self.who(f"f/{ident}")}

    # -- first writer wins
    def _checking(self) -> bool:
        if self.expect is None:
            return False
        if self.expect < self.st["kfloor"]:
            raise _Refused("resync", reason="expect_too_old", expect=self.expect)
        return True

    def check(self, key, theirs):
        """Someone else wrote this object after what the sender saw: refused with theirs."""
        if not self._checking():
            return
        k = self.st["kseq"].get(key)
        if k and k[0] > self.expect and k[1] != self.by:
            raise _Refused("conflict", key=key, theirs=theirs(), by=k[1], at=k[0])

    def check_sheet(self, sid):
        """Someone else wrote anything on this sheet after what the sender saw (a stale delete
        would hide it)."""
        if not self._checking():
            return
        later = [(s, who) for who, s in self.st["sheetw"].get(sid, {}).items() if s > self.expect and who != self.by]
        if later:
            s, who = max(later)
            raise _Refused("conflict", key=f"x/{sid}", theirs={"sheet": sid, "name": self.c["sheets"][sid]["name"]}, by=who, at=s)


def _keys(op: dict, allowed: set):
    extra = set(op) - allowed - {"op"}
    if extra:
        raise _Refused("invalid", reason="unknown_key", key=_echo(sorted(map(str, extra))[0]))


def _check_cell(cell: dict):
    try:
        validate_editor("builtin_sheet", {"sheets": [{"cells": {"A1": cell}}]})
    except ServiceError as e:
        raise _Refused("invalid", reason="cell", message=e.message, field=str(e.detail.get("field", "")).rsplit(".", 1)[-1]) from None
    lim = _limit(cell.get("v"), cell.get("t"))
    if lim:
        raise _Refused("too_large", what=lim, max=MAX_VALUE if lim == "value" else MAX_FORMULA)
    nf = (cell.get("s") or {}).get("nf")
    if nf is not None and (not isinstance(nf, str) or len(nf) > MAX_NUMBER_FORMAT):
        raise _Refused("invalid", reason="number_format")
    what = _unwritable(cell)
    if what:
        raise _Refused("invalid", reason="unwritable", what=what)


def _new_id(tx: _Tx, proposed, taken, what: str):
    if proposed is None:
        return _gen(tx.st, taken)
    if not isinstance(proposed, str) or not _ID.fullmatch(proposed):
        raise _Refused("invalid", reason="id", what=what)
    if taken(proposed):
        raise _Refused("id_taken", what=what, id=proposed)
    return proposed


def _grid_ids_ok(tx: _Tx, more: int):
    if _grid_ids(tx.c) + more > MAX_GRID_IDS:
        raise _Refused("too_large", what="grid", max=MAX_GRID_IDS)


# ------------------------------------------------------------------------------------ cell ops
def _value(cell) -> dict | None:
    if not cell:
        return None
    v = {k: cell[k] for k in ("v", "t") if k in cell}
    return v or None


def _filled(cell) -> bool:
    return bool(cell) and cell.get("v") not in (None, "")


def _write_cell(tx: _Tx, sid: str, ck: str, cell: dict):
    path = ("sheets", sid, "cells", ck)
    if not cell:
        tx.delete(path)
        return
    if ck not in tx.c["sheets"][sid]["cells"] and _cells(tx.c) + 1 > tx.cap:
        raise _Refused("too_large", what="cells", max=tx.cap)
    tx.put(path, cell)


def _cell_target(tx: _Tx, op: dict):
    sid, sh = tx.sheet(op)
    r, c = tx.row(sid, op.get("row")), tx.col(sid, op.get("col"))
    return sid, sh, f"{op['row']}:{op['col']}", r, c


def _op_set(tx: _Tx, op: dict, *, clear=False):
    _keys(op, {"sheet", "row", "col"} | (set() if clear else {"v", "t"}))
    sid, sh, ck, r, c = _cell_target(tx, op)
    cur = sh["cells"].get(ck)
    new = {}
    if not clear:
        for k in ("v", "t"):
            if op.get(k) is not None:
                new[k] = op[k]
    new.update({k: v for k, v in (cur or {}).items() if k not in ("v", "t")})
    if _eq(_value(new), _value(cur)):
        return                                            # already so: agreeing is not a conflict
    _check_cell(new)
    key = f"v/{sid}/{ck}"
    tx.check(key, lambda: _value(cur))
    if _filled(new) and sh["merges"]:
        # a value under a merged range would be hidden (the generator drops it): refused, saying whose range
        for m, mid in tx.geo(sid).merges_in((r, c, r, c)):
            if (r, c) != (m[0], m[1]):
                raise _Refused("covered", sheet=sid, ref=tabular.cell_ref(r, c), **tx.describe(sid, "merge", mid))
    _write_cell(tx, sid, ck, new)
    tx.kset(key, sid)


def _op_clear(tx: _Tx, op: dict):
    _op_set(tx, op, clear=True)


def _op_style(tx: _Tx, op: dict):
    _keys(op, {"sheet", "row", "col", "s"})
    sid, sh, ck, _r, _c = _cell_target(tx, op)
    patch = op.get("s")
    if not isinstance(patch, dict) or not patch:
        raise _Refused("invalid", reason="style")
    if set(patch) - set(STYLE_KEYS):
        raise _Refused("invalid", reason="style", key=_echo(sorted(map(str, set(patch) - set(STYLE_KEYS)))[0]))
    cur = sh["cells"].get(ck) or {}
    style = dict(cur.get("s") or {})
    changed = []
    for k, v in patch.items():
        if v is None:
            if k in style:
                del style[k]
                changed.append(k)
        elif k not in style or not _eq(style[k], v):
            style[k] = v
            changed.append(k)
    if not changed:
        return
    new = {k: v for k, v in cur.items() if k != "s"}
    if style:
        new["s"] = style
    _check_cell(new)
    _write_cell(tx, sid, ck, new)
    for k in changed:
        tx.kset(f"s/{sid}/{ck}/{k}", sid)


def _size_value(op, key, bounds):
    v = op.get(key)
    if v is not None and (not _num(v) or not bounds[0] <= v <= bounds[1]):
        raise _Refused("invalid", reason="size", min=bounds[0], max=bounds[1])
    return v


def _op_size(tx: _Tx, op: dict):
    if ("row" in op) == ("col" in op):
        raise _Refused("invalid", reason="size_target")
    part = "row" if "row" in op else "col"
    _keys(op, {"sheet", part, "h" if part == "row" else "w"})
    sid, sh = tx.sheet(op)
    key, bounds, store, count = ("h", ROW_HEIGHT, "rh", "nr") if part == "row" else ("w", COL_WIDTH, "cw", "nc")
    if key not in op:
        raise _Refused("invalid", reason="size")
    size = _size_value(op, key, bounds)
    ident = op[part]
    at = tx.row(sid, ident) if part == "row" else tx.col(sid, ident)
    if at >= sh[count]:
        raise _Refused("invalid", reason="not_in_grid", **{part: ident})
    raw = sh[store][ident]
    new = dict(raw) if isinstance(raw, dict) else ({} if size is not None else None)
    if new is not None:
        if size is None:
            new.pop(key, None)
        else:
            new[key] = size
    if _eq(new, raw):
        return
    tx.put(("sheets", sid, store, ident), new)
    tx.kset(f"{key}/{sid}/{ident}", sid)


def _op_append(tx: _Tx, op: dict, part: str):
    key, bounds, store, count, cap = (("h", ROW_HEIGHT, "rh", "nr", MAX_ROWS) if part == "rows" else ("w", COL_WIDTH, "cw", "nc", MAX_COLS))
    _keys(op, {"sheet", "count", "ids", key})
    sid, sh = tx.sheet(op)
    n = op.get("count")
    if type(n) is not int or not 1 <= n <= MAX_APPEND:
        raise _Refused("invalid", reason="count", max=MAX_APPEND)
    if sh[count] + n > cap:
        raise _Refused("too_large", what=part, max=cap)
    size = _size_value(op, key, bounds)
    proposed = op.get("ids") or []
    if not isinstance(proposed, list) or len(proposed) > n:
        raise _Refused("invalid", reason="ids")
    rp, cp = tx.positions(sid)
    ids = sh[part]
    need = max(0, sh[count] + n - len(ids))
    _grid_ids_ok(tx, need)
    fresh: list = []
    made: set = set()
    for i in range(need):
        x = _new_id(tx, proposed[i] if i < len(proposed) else None, lambda y: y in rp or y in cp or y in made, part[:-1])
        fresh.append(x)
        made.add(x)
    if fresh:
        tx.push(("sheets", sid, part), fresh)
        target = rp if part == "rows" else cp
        for i, x in enumerate(fresh, start=len(ids) - len(fresh)):
            target[x] = i
    for p in range(sh[count], sh[count] + n):
        tx.put(("sheets", sid, store, ids[p]), {key: size} if size is not None else {})
    tx.put(("sheets", sid, count), sh[count] + n)


# ------------------------------------------------------------------------------------ merges
def _values_under(tx: _Tx, sid: str, sh: dict, rect) -> list:
    """Cells a merged range at ``rect`` would hide: filled, not its top-left corner."""
    r1, c1, r2, c2 = rect
    rows, cols, cells = sh["rows"], sh["cols"], sh["cells"]
    held = []
    if (r2 - r1 + 1) * (c2 - c1 + 1) <= len(cells):
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                ck = f"{rows[r]}:{cols[c]}"
                if (r, c) != (r1, c1) and _filled(cells.get(ck)):
                    held.append((r, c, ck))
    else:
        rp, cp = tx.positions(sid)
        for ck, cell in cells.items():
            if _filled(cell):
                rid, cid = ck.split(":")
                r, c = rp[rid], cp[cid]
                if r1 <= r <= r2 and c1 <= c <= c2 and (r, c) != (r1, c1):
                    held.append((r, c, ck))
    return sorted(held)


def _op_merge(tx: _Tx, op: dict):
    _keys(op, {"sheet", "id", "range", "replaces"})
    sid, sh = tx.sheet(op)
    rect = tx.rect(sid, op.get("range"))
    want = tabular.range_ref(*rect)
    area = (rect[2] - rect[0] + 1) * (rect[3] - rect[1] + 1)
    if area < 2:
        raise _Refused("invalid", reason="single_cell_merge")
    if area > MAX_MERGE_CELLS:
        raise _Refused("too_large", what="merge", max=MAX_MERGE_CELLS)
    replaces = op.get("replaces") or []
    if not isinstance(replaces, list) or not all(isinstance(m, str) for m in replaces):
        raise _Refused("invalid", reason="replaces")
    gone = []
    for m in replaces:
        cur = tx.c["merges"].get(m)
        tx.check(f"m/{m}", lambda m=m, cur=cur: tx.describe(cur["sheet"], "merge", m)["range"] if cur else None)
        if cur is None:
            continue                                      # already unmerged (and not by someone we did not see)
        if cur["sheet"] != sid:
            raise _Refused("invalid", reason="replaces", merge=_echo(m))
        gone.append(m)
    # ``want`` is the range asked for; ``range`` / ``ref`` name what is in the way, ``by`` who put it there
    g = tx.geo(sid)
    for _m, other in g.merges_in(rect, skip=set(gone)):
        raise _Refused("merge_overlap", sheet=sid, want=want, **tx.describe(sid, "merge", other))
    hit = g.merge_issue(rect)
    if hit:
        code, fk, frect = hit
        raise _Refused(code, sheet=sid, want=want, ref=tabular.range_ref(*frect), **tx.describe(sid, "field", fk))
    # A merged range keeps only its top-left value (Excel and the generator drop the rest), so
    # merging over values is refused, naming them and whose they are: clear them in the same group
    # first — and a clear is first-writer-wins like any other write.
    held = _values_under(tx, sid, sh, rect)
    if held:
        writers = sorted({w for _r, _c, ck in held if (w := tx.who(f"v/{sid}/{ck}"))})
        raise _Refused("covers_values", sheet=sid, want=want, cells=[tabular.cell_ref(r, c) for r, c, _ck in held[:10]],
                       count=len(held), by=writers[:5])
    mid = _new_id(tx, op.get("id"), lambda x: x in tx.c["merges"] or f"m/{x}" in tx.st["kseq"], "merge")
    for m in gone:
        tx.delete(("merges", m))
        tx.kset(f"m/{m}", sid)
        g.drop_merge(m)
    tx.put(("merges", mid), {"sheet": sid, "at": tx.ids_of(sid, rect)})
    if gone:
        tx.put(("sheets", sid, "merges"), [m for m in sh["merges"] if m not in gone] + [mid])
    else:
        tx.push(("sheets", sid, "merges"), [mid])
    tx.kset(f"m/{mid}", sid)
    g.add_merge(mid, rect)


def _op_unmerge(tx: _Tx, op: dict):
    _keys(op, {"sheet", "id"})
    mid = op.get("id")
    if not isinstance(mid, str):
        raise _Refused("invalid", reason="id")
    cur = tx.c["merges"].get(mid)
    tx.check(f"m/{mid}", lambda: tx.describe(cur["sheet"], "merge", mid)["range"] if cur else None)
    if cur is None:
        raise _Refused("not_found", what="merge", id=_echo(mid))
    sid, sh = tx.sheet({"sheet": op.get("sheet", cur["sheet"])})
    if sid != cur["sheet"]:
        raise _Refused("invalid", reason="sheet")
    tx.delete(("merges", mid))
    tx.put(("sheets", sid, "merges"), [m for m in sh["merges"] if m != mid])
    tx.kset(f"m/{mid}", sid)
    if sid in tx.geos:
        tx.geos[sid].drop_merge(mid)


# ------------------------------------------------------------------------------------ fields
def _field_attrs(raw: dict):
    """Check a field's attributes with the editor contract (enums, id pattern, booleans) and what
    the stored template can hold (a name without characters the writer refuses)."""
    probe = {k: v for k, v in raw.items() if k not in ("ref", "row", "c1", "c2")}
    try:
        validate_editor("builtin_sheet", {"sheets": [{"fields": [probe]}]})
    except ServiceError as e:
        raise _Refused("invalid", reason="field", message=e.message, field=str(e.detail.get("field", "")).rsplit(".", 1)[-1]) from None
    name = raw.get("name")
    if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > MAX_FIELD_NAME or _bad_text(name)):
        raise _Refused("invalid", reason="field_name", max=MAX_FIELD_NAME)


def _field_place(tx: _Tx, sid: str, op: dict, kind: str):
    """Anchors and position rectangle of a field placed by an operation."""
    if kind == "cell":
        r, c = tx.row(sid, op.get("row")), tx.col(sid, op.get("col"))
        return [op["row"], op["col"]], (r, c, r, c)
    if kind == "region":
        rect = tx.rect(sid, op.get("range"))
        return tx.ids_of(sid, rect), rect
    r = tx.row(sid, op.get("row"))
    a, b = tx.col(sid, op.get("c1"), key="c1"), tx.col(sid, op.get("c2"), key="c2")
    lo, hi = min(a, b), max(a, b)
    sh = tx.c["sheets"][sid]
    return [op["row"], sh["cols"][lo], sh["cols"][hi]], (r, lo, r, hi)


_PLACE_KEYS = {"cell": {"row", "col"}, "region": {"range"}, "repeat": {"row", "c1", "c2"}}


def _field_fits(tx: _Tx, sid: str, kind: str, rect, skip=None):
    hit = tx.geo(sid).field_issue(kind, rect, skip)
    if hit:
        code, (what, ident) = hit
        raise _Refused(code, sheet=sid, ref=tabular.range_ref(*rect), **tx.describe(sid, what, ident))


def _op_field_add(tx: _Tx, op: dict):
    f = op.get("field")
    if not isinstance(f, dict) or set(f) - {"id", "name", "kind", "required", "empty_policy"}:
        raise _Refused("invalid", reason="field")
    kind = f.get("kind")
    if kind not in FIELD_KINDS:
        raise _Refused("invalid", reason="field_kind")
    _keys(op, {"sheet", "field"} | _PLACE_KEYS[kind])
    sid, _sh = tx.sheet(op)
    fid = f.get("id")
    if not isinstance(fid, str) or fid in RESERVED_IDS:
        raise _Refused("invalid", reason="field_id")
    if not isinstance(f.get("name"), str):
        raise _Refused("invalid", reason="field_name", max=MAX_FIELD_NAME)
    raw = {"id": fid, "name": f["name"], "kind": kind}
    raw.update({k: None for k in POSITIONAL[kind]})
    raw.update({k: f[k] for k in ("required", "empty_policy") if k in f})
    _field_attrs(raw)
    cur = tx.c["fields"].get(fid)
    # someone else added or deleted this id since the sender looked: not re-created unseen
    tx.check(f"f/{fid}", lambda: _field_out(cur, *tx.positions(cur["sheet"])) if cur else None)
    if cur is not None:
        raise _Refused("id_taken", what="field", id=fid, **tx.describe(cur["sheet"], "field", fid))
    at, rect = _field_place(tx, sid, op, kind)
    _field_fits(tx, sid, kind, rect)
    tx.put(("fields", fid), {"sheet": sid, "kind": kind, "at": at, "raw": raw})
    tx.push(("sheets", sid, "fields"), [fid])
    tx.kset(f"f/{fid}", sid)
    tx.geo(sid).add_field(fid, kind, rect)


def _field_target(tx: _Tx, op: dict):
    fk = op.get("field")
    if not isinstance(fk, str):
        raise _Refused("invalid", reason="field")
    cur = tx.c["fields"].get(fk)
    tx.check(f"f/{fk}", lambda: _field_out(cur, *tx.positions(cur["sheet"])) if cur else None)
    if cur is None:
        raise _Refused("not_found", what="field", field=_echo(fk))
    sid, sh = tx.sheet({"sheet": cur["sheet"]})
    return fk, cur, sid, sh


def _op_field_set(tx: _Tx, op: dict):
    _keys(op, {"field", "set"})
    changes = op.get("set")
    if not isinstance(changes, dict) or not changes or set(changes) - set(FIELD_ATTRS):
        raise _Refused("invalid", reason="set")
    fk, cur, sid, _sh = _field_target(tx, op)
    raw = dict(cur["raw"])
    for k, v in changes.items():
        if v is None:
            raw.pop(k, None)
        else:
            raw[k] = v
    if _eq(raw, cur["raw"]):
        return
    _field_attrs(raw)
    tx.put(("fields", fk), {**cur, "raw": raw})
    tx.kset(f"f/{fk}", sid)


def _op_field_move(tx: _Tx, op: dict):
    fk0 = op.get("field")
    if not isinstance(fk0, str):
        raise _Refused("invalid", reason="field")
    kind = op.get("kind") if "kind" in op else (tx.c["fields"].get(fk0) or {}).get("kind", "cell")
    if kind not in FIELD_KINDS:
        raise _Refused("invalid", reason="field_kind")
    _keys(op, {"field", "kind"} | _PLACE_KEYS[kind])
    fk, cur, sid, _sh = _field_target(tx, op)
    at, rect = _field_place(tx, sid, op, kind)
    if kind == cur["kind"] and at == cur["at"]:
        return
    _field_fits(tx, sid, kind, rect, skip=fk)
    raw = {k: v for k, v in cur["raw"].items() if k not in ("ref", "row", "c1", "c2")}
    if kind != cur["kind"] or "kind" in raw:
        raw["kind"] = kind
    raw.update({k: None for k in POSITIONAL[kind]})
    tx.put(("fields", fk), {"sheet": sid, "kind": kind, "at": at, "raw": raw})
    tx.kset(f"f/{fk}", sid)
    g = tx.geo(sid)
    g.drop_field(fk)
    g.add_field(fk, kind, rect)


def _op_field_del(tx: _Tx, op: dict):
    _keys(op, {"field"})
    fk, _cur, sid, sh = _field_target(tx, op)
    tx.delete(("fields", fk))
    tx.put(("sheets", sid, "fields"), [x for x in sh["fields"] if x != fk])
    tx.kset(f"f/{fk}", sid)
    if sid in tx.geos:
        tx.geos[sid].drop_field(fk)


# ------------------------------------------------------------------------------------ sheets
def _name_free(tx: _Tx, title: str, *, skip=None):
    """No other live sheet has this worksheet title (compared the way Excel does, ignoring case)."""
    for sid in tx.c["order"]:
        sh = tx.c["sheets"][sid]
        if sid != skip and not sh["gone"] and _title(sh["name"]).casefold() == title.casefold():
            raise _Refused("sheet_name_taken", name=title, sheet=sid, by=tx.who(f"n/{sid}"))


def _sheet_name(tx: _Tx, name, *, skip=None) -> str:
    """A name someone types for a sheet: a legal worksheet title as it is (not Excel's reserved
    「History」, no characters the writer refuses), not taken."""
    if (not isinstance(name, str) or not name or name != name.strip() or len(name) > MAX_SHEET_NAME
            or _BAD_TITLE.search(name) or name.startswith("'") or name.endswith("'") or _bad_text(name)
            or name.casefold() == "history"):
        raise _Refused("invalid", reason="sheet_name", max=MAX_SHEET_NAME)
    _name_free(tx, name, skip=skip)
    return name


def _live_sheets(tx: _Tx) -> int:
    return sum(1 for s in tx.c["sheets"].values() if not s["gone"])


def _op_sheet_add(tx: _Tx, op: dict):
    _keys(op, {"id", "name", "rows", "cols", "row_ids", "col_ids", "h", "w"})
    name = _sheet_name(tx, op.get("name"))
    if _live_sheets(tx) >= MAX_SHEETS or len(tx.c["sheets"]) >= 2 * MAX_SHEETS:
        raise _Refused("too_large", what="sheets", max=MAX_SHEETS)
    sid = _new_id(tx, op.get("id"), lambda x: x in tx.c["sheets"] or f"x/{x}" in tx.st["kseq"], "sheet")
    counts = {}
    for part in ("rows", "cols"):
        n = op.get(part, 0)
        if type(n) is not int or not 0 <= n <= MAX_APPEND:
            raise _Refused("invalid", reason="count", what=part, max=MAX_APPEND)
        counts[part] = n
    _grid_ids_ok(tx, counts["rows"] + counts["cols"])
    h, w = _size_value(op, "h", ROW_HEIGHT), _size_value(op, "w", COL_WIDTH)
    taken: set = set()
    ids = {}
    for part in ("rows", "cols"):
        proposed = op.get(part[:-1] + "_ids") or []
        if not isinstance(proposed, list) or len(proposed) > counts[part]:
            raise _Refused("invalid", reason="ids", what=part)
        ids[part] = []
        for i in range(counts[part]):
            x = _new_id(tx, proposed[i] if i < len(proposed) else None, lambda y: y in taken, part[:-1])
            taken.add(x)
            ids[part].append(x)
    sheet = {"name": name, "gone": False, "keys": list(SHEET_KEYS), "nul": [], "rows": ids["rows"], "cols": ids["cols"],
             "nr": counts["rows"], "nc": counts["cols"],
             "rh": {r: ({"h": h} if h is not None else {}) for r in ids["rows"]},
             "cw": {c: ({"w": w} if w is not None else {}) for c in ids["cols"]},
             "cells": {}, "merges": [], "fields": []}
    tx.put(("sheets", sid), sheet)
    tx.put(("order",), tx.c["order"] + [sid])
    tx.kset(f"x/{sid}", sid)
    tx.kset(f"n/{sid}", sid)


def _op_sheet_rename(tx: _Tx, op: dict):
    _keys(op, {"sheet", "name"})
    sid, sh = tx.sheet(op)
    name = _sheet_name(tx, op.get("name"), skip=sid)
    if name == sh["name"]:
        return
    tx.put(("sheets", sid, "name"), name)
    tx.kset(f"n/{sid}", sid)


def _op_sheet_delete(tx: _Tx, op: dict):
    _keys(op, {"sheet"})
    sid, sh = tx.sheet(op, live=False)
    if sh["gone"]:
        return                                            # someone deleted it first: the same outcome
    tx.check_sheet(sid)                                   # a stale delete never hides newer work on the sheet
    if _live_sheets(tx) <= 1:
        raise _Refused("last_sheet", sheet=sid)
    tx.put(("sheets", sid, "gone"), True)
    tx.kset(f"x/{sid}", sid)


def _op_sheet_restore(tx: _Tx, op: dict):
    _keys(op, {"sheet", "name"})
    sid, sh = tx.sheet(op, live=False)
    if not sh["gone"]:
        return
    if "name" in op:
        name = _sheet_name(tx, op["name"], skip=sid)
    else:
        name = sh["name"]
        _name_free(tx, _title(name), skip=sid)            # someone took its name meanwhile: restore under a new one
    if _live_sheets(tx) >= MAX_SHEETS:
        raise _Refused("too_large", what="sheets", max=MAX_SHEETS)
    if _cells(tx.c, live_only=True) + len(sh["cells"]) > tx.cap:
        raise _Refused("too_large", what="cells", max=tx.cap)
    tx.put(("sheets", sid, "gone"), False)
    tx.kset(f"x/{sid}", sid)
    if name != sh["name"]:
        tx.put(("sheets", sid, "name"), name)
        tx.kset(f"n/{sid}", sid)


_OPS = {
    "set": _op_set, "clear": _op_clear, "style": _op_style, "size": _op_size,
    "append_rows": lambda tx, op: _op_append(tx, op, "rows"), "append_cols": lambda tx, op: _op_append(tx, op, "cols"),
    "merge": _op_merge, "unmerge": _op_unmerge,
    "field_add": _op_field_add, "field_set": _op_field_set, "field_move": _op_field_move, "field_del": _op_field_del,
    "sheet_add": _op_sheet_add, "sheet_rename": _op_sheet_rename, "sheet_delete": _op_sheet_delete, "sheet_restore": _op_sheet_restore,
}


def _dispatch(tx: _Tx, op, *, top: bool):
    if not isinstance(op, dict):
        raise _Refused("invalid", reason="op")
    kind = op.get("op")
    if kind == "tx":
        if not top:
            raise _Refused("invalid", reason="nested_tx")
        _keys(op, {"ops"})
        ops = op.get("ops")
        if not isinstance(ops, list) or not ops:
            raise _Refused("invalid", reason="ops")
        if len(ops) > MAX_TX:
            raise _Refused("too_large", what="tx", max=MAX_TX)
        for i, sub in enumerate(ops):
            try:
                _dispatch(tx, sub, top=False)
            except _Refused as r:
                r.detail.setdefault("index", i)
                raise
        return
    fn = _OPS.get(kind) if isinstance(kind, str) else None
    if fn is None:
        raise _Refused("invalid", reason="op", op=_echo(kind))
    fn(tx, op)


# ------------------------------------------------------------------------------------ de-duplication
def _trimmed(result: dict) -> dict:
    if result["ok"]:
        return {"ok": True, "seq": result["seq"], "patch": None, "before": None, "trimmed": True}
    return {"ok": False, "code": result["code"], "detail": None, "trimmed": True}


def _keep(result: dict) -> dict:
    """What is remembered of a result: the whole of it when small, else its outcome."""
    if result["ok"] and len(result["patch"]) + len(result["before"]) > 64:
        return _trimmed(result)
    try:
        small = len(json.dumps(result, allow_nan=False)) <= RECORD_BYTES
    except (TypeError, ValueError):
        small = False                                     # not strict JSON: never remembered as is
    return dict(result) if small else _trimmed(result)


def _record(state: dict, client: str, n: int, result: dict):
    """Remember ``(client, n)``'s answer. Per client: the last ``CLIENT_WINDOW`` numbers, the newest
    ``RECORD_FULL`` of them whole (each at most ``RECORD_BYTES``), older ones as their outcome only.
    At most ``MAX_CLIENTS`` clients; forgetting one raises ``nfloor`` past its numbers, so a client
    the room no longer knows can never have an old message applied again (it is told to resync).
    At most ``MAX_CLIENTS_PER_SCOPE`` of one scope, checked where a record is created: a scope's
    oldest is forgotten first, raising only that scope's floor (``pfloor``), so one person's many
    tabs neither push the others' records out nor move the numbers the others are told."""
    clients = state["clients"]
    rec = clients.get(client)
    if rec is None:
        scope = _scope(client)
        if scope is not None:
            mine = [c for c in clients if _scope(c) == scope]
            while len(mine) >= MAX_CLIENTS_PER_SCOPE:
                oldest = min(mine, key=lambda c: (clients[c]["at"], c))
                floors = state.setdefault("pfloor", {})
                floors[scope] = max(floors.get(scope, 0), clients[oldest]["hi"])
                del clients[oldest]
                mine.remove(oldest)
        rec = clients[client] = {"hi": 0, "at": 0, "r": {}}
    rec["r"][str(n)] = _keep(result)
    rec["hi"] = max(rec["hi"], n)
    rec["at"] = state["seq"]
    for k in list(rec["r"]):
        i = int(k)
        if i <= rec["hi"] - CLIENT_WINDOW:
            del rec["r"][k]
        elif i <= rec["hi"] - RECORD_FULL and not rec["r"][k].get("trimmed"):
            rec["r"][k] = _trimmed(rec["r"][k])
    while len(clients) > MAX_CLIENTS:
        oldest = min((c for c in clients if c != client), key=lambda c: (clients[c]["at"], c))
        state["nfloor"] = max(state["nfloor"], clients[oldest]["hi"])
        del clients[oldest]


def _scope(client: str) -> str | None:
    return client.rsplit("/", 1)[0] if "/" in client else None


def _floor(state: dict, client: str) -> int:
    """The highest number a client the room does not know may have used: the room's own floor, or
    its scope's when higher."""
    scope = _scope(client)
    return max(state["nfloor"], (state.get("pfloor") or {}).get(scope, 0) if scope is not None else 0)


def next_number(state: dict, client: str) -> int:
    """The number ``client``'s next message should carry: after its last one, or — for a client the
    room does not know (new, or forgotten) — above every number a forgotten client used."""
    rec = state["clients"].get(client)
    return rec["hi"] + 1 if rec else _floor(state, client) + 1


def apply(state: dict, op: dict, *, by: str, client: str, n: int, expect: int | None, trusted: bool = False,
          max_cells: int | None = None) -> dict:
    """Apply one operation (or one ``tx`` group) from ``client``'s ``n``-th message, sent by ``by``
    (stamped by the server) after seeing everything up to sequence number ``expect``.

    Returns ``{"ok": True, "seq", "patch", "before"}`` or ``{"ok": False, "code", "detail"}``; the
    state is untouched by a refusal. The same ``(client, n)`` delivered again applies nothing and
    returns the remembered result with ``"duplicate": True``; a number too old to know, or below
    ``next_number`` for a client the room does not know, is answered ``resync``. ``expect`` is
    required: ``None`` (no first-writer-wins check) only for the server's own writes, which say so
    with ``trusted=True``. ``max_cells``: the room's own bound on cells (below ``MAX_LIVE_CELLS``); a
    write that would pass it is refused ``too_large``. A fault inside an operation (a bug, not a
    refusal) undoes the group before it propagates: the state is never left half-written."""
    if not isinstance(by, str) or not by:
        raise ValueError("by must name the person the server authenticated")
    if not isinstance(client, str) or not client or len(client) > 128:
        raise ValueError("client must be a non-empty string")
    if type(n) is not int or n < 1:
        raise ValueError("n must be a positive integer")
    if expect is None and not trusted:
        raise ValueError("expect=None skips the first-writer-wins check: only for the server's own writes (trusted=True)")
    rec = state["clients"].get(client)
    if n > MAX_NUMBER or n >= next_number(state, client) + MAX_AHEAD:
        # a number far past the client's own (a forgotten client's numbers become the floor of new
        # ones: an unbounded one would push everyone's past what a browser can count); certainly not
        # applied: the client renumbers from `next`
        return {"ok": False, "code": "resync", "detail": {"reason": "number_ahead", "applied": False, "next": next_number(state, client)}}
    # A number the room no longer remembers (its client was forgotten, or it is older than the
    # window): the message may have been applied before — or not. Never applied now; the client must
    # not simply renumber it (that could apply it twice) but compare with the state it syncs to
    # (`forgotten`, unlike `number_ahead`, which was certainly not applied).
    if rec is None:
        if n <= _floor(state, client):
            return {"ok": False, "code": "resync", "detail": {"reason": "forgotten", "applied": "unknown", "next": _floor(state, client) + 1}}
    else:
        prior = rec["r"].get(str(n))
        if prior is not None:
            return {**prior, "duplicate": True}
        if n <= rec["hi"] - CLIENT_WINDOW:
            return {"ok": False, "code": "resync", "detail": {"reason": "forgotten", "applied": "unknown", "next": rec["hi"] + 1}}
    if expect is not None and (type(expect) is not int or expect < 0 or expect > state["seq"]):
        return {"ok": False, "code": "resync", "detail": {"reason": "unknown_expect", "expect": _echo(expect), "seq": state["seq"]}}
    tx = _Tx(state, by, expect, max_cells)
    try:
        _dispatch(tx, op, top=True)
    except _Refused as r:
        tx.rollback()
        result = {"ok": False, "code": r.code, "detail": r.detail}
    except BaseException:
        tx.rollback()
        raise
    else:
        patch, before = tx.commit()
        if patch:
            state["seq"] = tx.seq
        else:
            # nothing changed (it already was so): no sequence number, and no ownership either
            tx.rollback()
        result = {"ok": True, "seq": state["seq"], "patch": patch, "before": before}
    _record(state, client, n, result)
    return result


def compact(state: dict, *, upto: int):
    """Forget who last wrote objects at or before ``upto`` (every client has seen that far; clamped
    to the current sequence number); a later operation that claims to have seen less is told to
    resync instead of being checked. Who wrote what since the basis is kept whatever ``upto`` says:
    an outside save still tells those people their values were replaced."""
    upto = min(upto, state["seq"])
    if upto <= state["kfloor"]:
        return
    keep_after = min(upto, state["basis"]["seq"])
    state["kseq"] = {k: v for k, v in state["kseq"].items() if v[0] > keep_after}
    state["sheetw"] = {sid: kept for sid, w in state["sheetw"].items() if (kept := {p: s for p, s in w.items() if s > keep_after})}
    state["kfloor"] = upto


# ------------------------------------------------------------------------------------ versions
_SHEET_PARTS = ("name", "gone", "keys", "nul", "nr", "nc", "rows", "cols", "merges", "fields")


def _diff(a: dict, b: dict) -> list:
    """Every content path whose value differs from ``a`` to ``b``: ``(path, [old] | [], [new] | [])``."""
    out = []

    def cmp(path, x, y):
        if not _eq(x, y):
            out.append((path, x, y))

    for k in ("keys", "order"):
        cmp((k,), [a[k]], [b[k]])
    for sid in _union(a["sheets"], b["sheets"]):
        sa, sb = a["sheets"].get(sid), b["sheets"].get(sid)
        if sa is None or sb is None:
            cmp(("sheets", sid), [sa] if sa is not None else [], [sb] if sb is not None else [])
            continue
        for k in _SHEET_PARTS:
            cmp(("sheets", sid, k), [sa[k]], [sb[k]])
        for m in ("rh", "cw", "cells"):
            ma, mb = sa[m], sb[m]
            for k in _union(ma, mb):
                cmp(("sheets", sid, m, k), [ma[k]] if k in ma else [], [mb[k]] if k in mb else [])
    for top in ("merges", "fields"):
        for k in _union(a[top], b[top]):
            cmp((top, k), [a[top][k]] if k in a[top] else [], [b[top][k]] if k in b[top] else [])
    return out


def _patch_of(changes: list) -> tuple[list, list]:
    patch, before = [], []
    for path, old, new in changes:
        p = list(path)
        grow = path[-1] in ("rows", "cols", "merges", "fields") and len(path) == 3 and old and new and new[0][:len(old[0])] == old[0]
        if grow:
            patch.append({"p": p, "push": list(new[0][len(old[0]):])})
            before.append({"p": p, "trim": len(old[0])})
            continue
        patch.append({"p": p, "v": _copy(new[0])} if new else {"p": p, "d": 1})
        before.append({"p": p, "v": _copy(old[0])} if old else {"p": p, "d": 1})
    before.reverse()
    return patch, before


def _base_of(content: dict, basis_content: dict) -> dict:
    return {"/".join(path): _copy(old) for path, _new, old in _diff(content, basis_content)}


def _revert(state: dict) -> dict:
    """The content as it was at the basis: the live content with every tracked change undone."""
    c = _copy(state["content"])
    for key in sorted(state["base"], key=lambda k: k.count("/")):
        path = key.split("/")
        node = c
        for p in path[:-1]:
            node = node.get(p) if isinstance(node, dict) else None
            if node is None:
                break
        if node is None:
            continue
        val = state["base"][key]
        if val:
            node[path[-1]] = _copy(val[0])
        else:
            node.pop(path[-1], None)
    return c


def snapshot(state: dict) -> dict:
    """What a save captures at the moment it starts: the room's epoch, sequence number and content."""
    return {"epoch": state["epoch"], "seq": state["seq"], "content": _copy(state["content"])}


def mark_saved(state: dict, snap: dict) -> dict:
    """The version built from ``snap`` (see :func:`snapshot`) is written. Sheets deleted before the
    save are gone for good now; what changed after the snapshot stays unsaved (``dirty``). A
    snapshot from another seeding, or older than the current basis (an outside version was folded
    in while the save was being built), changes nothing and says ``stale``."""
    if snap.get("epoch") != state["epoch"] or snap["seq"] < state["basis"]["seq"] or snap["seq"] > state["seq"]:
        return {"seq": None, "patch": [], "before": [], "dirty": dirty(state), "stale": True}
    c = state["content"]
    purge = [sid for sid in snap["content"]["order"]
             if snap["content"]["sheets"][sid]["gone"] and sid in c["sheets"] and c["sheets"][sid]["gone"]]
    tx = _Tx(state, "system", None)
    for sid in purge:
        sh = c["sheets"][sid]
        for fk in sh["fields"]:
            if c["fields"].get(fk, {}).get("sheet") == sid:
                tx.delete(("fields", fk))
        for mid in sh["merges"]:
            tx.delete(("merges", mid))
        tx.delete(("sheets", sid))
    if purge:
        tx.put(("order",), [s for s in c["order"] if s not in purge])
    patch, before = tx.commit()
    if patch:
        state["seq"] = tx.seq
    for sid in purge:
        state["sheetw"].pop(sid, None)
    saved = _copy(snap["content"])
    for sid in purge:
        sh = saved["sheets"].pop(sid)
        for fk in sh["fields"]:
            if saved["fields"].get(fk, {}).get("sheet") == sid:
                saved["fields"].pop(fk, None)
        for mid in sh["merges"]:
            saved["merges"].pop(mid, None)
    saved["order"] = [s for s in saved["order"] if s not in purge]
    state["base"] = _base_of(c, saved)
    state["basis"] = _basis(saved, snap["seq"])
    return {"seq": state["seq"] if patch else None, "patch": patch, "before": before, "dirty": dirty(state)}


class _MapAlloc:
    """Ids for a model that arrived from outside the room: the live ids wherever the same sheet,
    position, merged range or field already has one, fresh ones only for what is new."""

    def __init__(self, state: dict, live: dict, base: dict, sids: list):
        self.st, self.live, self.base, self.sids = state, live, base, sids
        self.grid: dict = {}
        self.merges: dict = {}
        self.taken_m = set(live["merges"]) | set(base["merges"])
        for c in (live, base):
            for mid, m in c["merges"].items():
                self.merges.setdefault((m["sheet"], tuple(m["at"])), mid)
        self.fields: dict = {}
        for c in (live, base):
            for fk, f in c["fields"].items():
                if fk.startswith("~"):
                    self.fields.setdefault((f["sheet"], f["kind"], tuple(f["at"])), fk)

    def sheet(self, i, _sh):
        return self.sids[i]

    def _known(self, sid, part):
        have = self.grid.get((sid, part))
        if have is None:
            sh = self.live["sheets"].get(sid) or self.base["sheets"].get(sid)
            have = self.grid[(sid, part)] = list(sh[part]) if sh else []
        return have

    def _ids(self, sid, part, n):
        have = self._known(sid, part)
        if n > len(have):
            taken = set(have) | set(self._known(sid, "cols" if part == "rows" else "rows"))
            for _ in range(n - len(have)):
                x = _gen(self.st, lambda y: y in taken)
                taken.add(x)
                have.append(x)
        return list(have)

    def rows(self, sid, n):
        return self._ids(sid, "rows", n)

    def cols(self, sid, n):
        return self._ids(sid, "cols", n)

    def merge(self, sid, at):
        key = (sid, tuple(at))
        if key not in self.merges:
            mid = _gen(self.st, lambda x: x in self.taken_m or f"m/{x}" in self.st["kseq"])
            self.taken_m.add(mid)
            self.merges[key] = mid
        return self.merges[key]

    def field(self, sid, f, kind, at):
        own = _own_key(f)
        if own:
            return own
        key = (sid, kind, tuple(at))
        if key not in self.fields:
            self.fields[key] = "~" + _gen(self.st, lambda x: "~" + x in self.live["fields"] or "~" + x in self.base["fields"])
        return self.fields[key]


def _align_keys(target: dict, ref: dict):
    """Which keys a sheet writes even when empty (or as null) is shape, not content: where ``ref``'s
    shape renders a sheet of ``target`` the same, take it, so an unchanged sheet is not a change."""
    for sid in target["order"]:
        if sid not in ref["sheets"]:
            continue
        sh, other = target["sheets"][sid], ref["sheets"][sid]
        if sh["keys"] == other["keys"] and sh["nul"] == other["nul"]:
            continue
        before, own = _sheet_out(target, sid), (sh["keys"], sh["nul"])
        sh["keys"], sh["nul"] = list(other["keys"]), list(other["nul"])
        if not _eq(_sheet_out(target, sid), before):
            sh["keys"], sh["nul"] = own


def _writers(state: dict, keys, exclude: str) -> set:
    since = state["basis"]["seq"]
    out = set()
    for key in keys:
        k = state["kseq"].get(key)
        if k and k[0] > since and k[1] != exclude:
            out.add(k[1])
    return out


def _sheet_keys(state: dict, contents, sid: str, parts=("v", "s", "h", "w", "n", "x", "m", "f")) -> list:
    """Every object key that belongs to one sheet (for who wrote something on it)."""
    keys = [k for k in state["kseq"] if k.split("/", 2)[1:2] == [sid] and k[0] in parts and k[1] == "/"]
    for c in contents:
        if "m" in parts:
            keys += [f"m/{mid}" for mid, m in c["merges"].items() if m["sheet"] == sid]
        if "f" in parts:
            keys += [f"f/{fk}" for fk, f in c["fields"].items() if f["sheet"] == sid]
    return keys


def _place(f: dict):
    kind = f.get("kind", "cell")
    return (kind, f.get("ref")) if kind != "repeat" else (kind, f.get("row"), f.get("c1", 0), f.get("c2", 0))


def _tag_outside(out_by_sid: dict, live_k: dict, base_k: dict, c: dict, base_c: dict):
    """``merge_sheets`` knows fields by id only; a field without one would vanish from the merge.
    Give the outside version's id-less fields the key of the room's field they are (same
    attributes, else same place; the basis first), a new tag otherwise. Returns the tagged sheets
    and the tags whose field carried an explicit ``"id": null``."""
    had_null = {fk for cont in (c, base_c) for fk, f in cont["fields"].items()
                if fk.startswith("~") and "id" in f["raw"] and not isinstance(f["raw"]["id"], str)}
    known: dict = {}
    for editor in (base_k, live_k):
        for s in editor.get("sheets") or []:
            for f in s.get("fields") or []:
                if isinstance(f.get("id"), str) and f["id"].startswith("~"):
                    known.setdefault(s["name"], []).append(f)
    tagged = []
    for i, (sid, sh) in enumerate(out_by_sid.items()):
        sh = {**sh, "name": sid}
        if isinstance(sh.get("fields"), list):
            used: set = set()
            fields = []
            for j, f in enumerate(sh["fields"]):
                if isinstance(f.get("id"), str):
                    fields.append(f)
                    continue
                bare = {k: v for k, v in f.items() if k != "id"}
                tag = None
                for same in (lambda a, b: _eq(a, b), lambda a, b: _place(a) == _place(b)):
                    for cand in known.get(sid, []):
                        other = {k: v for k, v in cand.items() if k != "id"}
                        if cand["id"] not in used and same(other, bare):
                            tag = cand["id"]
                            break
                    if tag:
                        break
                tag = tag or f"~o{i}.{j}"
                used.add(tag)
                if "id" in f:
                    had_null.add(tag)
                fields.append({**bare, "id": tag})
            sh["fields"] = fields
        tagged.append(sh)
    return {"sheets": tagged}, had_null


def _untag(fields: list, had_null: set) -> list:
    out = []
    for f in fields:
        fid = f.get("id")
        if isinstance(fid, str) and fid.startswith("~"):
            f = {k: v for k, v in f.items() if k != "id"}
            if fid in had_null:
                f = {"id": None, **f}
        out.append(f)
    return out


def reconcile(state: dict, current_editor: dict, *, by: str) -> dict:
    """A version was written outside the room (the machine API, a single-person save, a field
    changed on the project page): fold it into the live state (design §3). Where the outside version
    and the room changed the same object since the basis, the outside version wins; everything else
    the room changed stays. The result names who in the room lost a value, so each can be offered
    「用我的值」::

        {"seq", "patch", "before", "overwritten": [people],
         "conflicts": [{kind, sheet_id, sheet_name, ref | id | part, reason, by: [people]}], "reset"}

    The merge is ``merge_sheets`` itself (cell / field / layout / sheet), with sheets keyed by their
    live ids rather than their names, id-less fields keyed by the room's keys for them, and every
    collision decided for the outside version. The result always keeps a sheet when the outside
    version has one. Raises :class:`SeedError` — before allocating anything — when the outside
    version itself cannot be held live (an invariant, more than ``MAX_GRID_IDS`` row + column ids
    or ``MAX_LIVE_CELLS`` cells): the room then leaves live mode (CI-47)."""
    from .builtin_service import merge_sheets          # builtin_service may come to use this module (D2)

    issues = sheet_invariants(current_editor)
    if issues:
        raise SeedError(issues)
    outside = normalize(current_editor)
    issues = _size_issues(outside)
    if issues:
        raise SeedError(issues)
    c = state["content"]
    base_c = _revert(state)

    # which live sheet each outside sheet is: its name at the basis, else a sheet the room added
    # under the same name, else a new one
    basis_sids = {sid for sid, _name in state["basis"]["sheets"]}
    by_title = {_title(name).casefold(): sid for sid, name in state["basis"]["sheets"] if sid in c["sheets"]}
    live_added = {_title(c["sheets"][sid]["name"]).casefold(): sid for sid in c["order"]
                  if sid not in basis_sids and not c["sheets"][sid]["gone"]}
    sids: list = []
    for sh in outside.get("sheets") or []:
        t = _title(sh.get("name")).casefold()
        sid = by_title.get(t)
        if sid is None or sid in sids:
            sid = live_added.get(t)
        if sid is None or sid in sids:
            sid = _gen(state, lambda x: x in c["sheets"] or x in sids or f"x/{x}" in state["kseq"])
        sids.append(sid)
    out_by_sid = dict(zip(sids, outside.get("sheets") or []))

    overwritten: set = set()

    def lose(keys, conflict=None):
        people = _writers(state, keys, by)
        if conflict is not None:
            conflict["by"] = sorted(people)
        overwritten.update(people)

    def sheet_keys(sid, parts=("v", "s", "h", "w", "n", "x", "m", "f")):
        return _sheet_keys(state, (c, base_c), sid, parts) if sid in c["sheets"] else []

    def outside_all():
        return [(sid, _copy(out_by_sid[sid])) for sid in sids]

    conflicts: list[dict] = []
    tombstone: list = []                                  # sheets deleted outside that the room had changed
    if not state["base"]:
        final = outside_all()
    else:
        live_k, base_k = _project(c, keyed=True), _project(base_c, keyed=True)
        out_k, had_null = _tag_outside(out_by_sid, live_k, base_k, c, base_c)
        merged, found = merge_sheets(base_k, live_k, out_k)
        if found:
            force = {(f"cell:{x['sheet']}!{x['ref']}" if x["kind"] == "cell" else f"field:{x['id']}" if x["kind"] == "field"
                      else f"layout:{x['sheet']}" if x["kind"] == "layout" else f"sheet:{x['sheet']}") for x in found}
            merged, _rest = merge_sheets(base_k, live_k, out_k, force)
        live_by_sid = {s["name"]: s for s in live_k.get("sheets") or []}
        base_by_sid = {s["name"]: s for s in base_k.get("sheets") or []}
        final = []
        for msh in merged["sheets"]:
            sid, src = msh["name"], out_by_sid.get(msh["name"])
            if src is not None and _eq(live_by_sid.get(sid), base_by_sid.get(sid)):
                final.append((sid, _copy(src)))           # untouched in the room: exactly theirs
                continue
            live = c["sheets"].get(sid)
            name = live["name"] if live is not None else src.get("name")
            keys = list(src) if src is not None else []
            keys += [k for k in (live["keys"] if live is not None else []) if k not in keys]
            d = {}
            for k in keys + [k for k in SHEET_KEYS if k not in keys]:
                if k == "name":
                    if k in keys or name is not None:
                        d["name"] = name
                elif k in NULLABLE and not msh.get(k) and ((src or {}).get(k, 0) is None or (src is None and k in live["nul"])):
                    d[k] = None                           # null stays null while there is nothing in it
                elif k in keys or msh.get(k):
                    d[k] = msh.get(k, {} if k == "cells" else [])
            if "fields" in d:
                d["fields"] = _untag(d["fields"], had_null)
            final.append((sid, d))
        for x in found:
            sid = x.pop("sheet")
            if x["kind"] == "cell":
                sh = c["sheets"][sid]
                r, col = tabular.parse_cell(x["ref"])
                ck = f"{sh['rows'][r]}:{sh['cols'][col]}" if r < len(sh["rows"]) and col < len(sh["cols"]) else None
                lose(([f"v/{sid}/{ck}"] + [f"s/{sid}/{ck}/{a}" for a in STYLE_KEYS]) if ck else [], x)
            elif x["kind"] == "field":
                lose([f"f/{x['id']}"], x)
                if x["id"].startswith("~"):
                    x["id"] = None                        # a field without an id: known by its name
            elif x["kind"] == "layout":
                lose(sheet_keys(sid, {"cols": ("w",), "rows": ("h",), "merges": ("m",)}[x["part"]]), x)
            else:
                lose(sheet_keys(sid), x)
                if x["reason"] == "deleted_by_you":
                    tombstone.append(sid)
            x["sheet_id"] = sid
            x["sheet_name"] = c["sheets"][sid]["name"] if sid in c["sheets"] else out_by_sid[sid].get("name")
            conflicts.append(x)

    # The two sides together must still be a table the generator can build (CI-116). Where they are
    # not: a sheet name the room gave that the outside version now uses goes back to the outside
    # name; then the outside version of that sheet's merged ranges and fields wins; then the whole
    # outside sheet; and as a last resort the whole outside version. A sheet only the room has is
    # set aside as a deleted sheet people can restore.
    def model(rows):
        return {"sheets": [d for _sid, d in rows]} if ("sheets" in outside or rows) else {}

    def set_aside(i):
        sid = final[i][0]
        lose(sheet_keys(sid))
        tombstone.append(sid)
        del final[i]

    reset = False
    for level in ("name", "layout", "sheet", "all"):
        found_issues = sheet_invariants(model(final))
        if not found_issues:
            break
        if level == "all" or any("index" not in i for i in found_issues):
            reset = True
            break
        if level == "name":
            clash = {j for x in found_issues if x["code"] == "sheet_name_taken" for j in (x["index"], x["other"])}
            for i in sorted(clash, reverse=True):
                sid, d = final[i]
                src = out_by_sid.get(sid)
                if src is None:
                    set_aside(i)
                elif not _eq(d.get("name"), src.get("name")):
                    lose(sheet_keys(sid, ("n",)))
                    d = {k: v for k, v in d.items() if k != "name"}
                    final[i] = (sid, {"name": src["name"], **d} if "name" in src else d)
            continue
        for i in sorted({x["index"] for x in found_issues}, reverse=True):
            sid, d = final[i]
            src = out_by_sid.get(sid)
            if src is None:
                set_aside(i)
            elif level == "layout":
                d = {k: v for k, v in d.items() if k not in ("merges", "fields")}
                d.update({k: _copy(src[k]) for k in ("merges", "fields") if k in src})
                lose(sheet_keys(sid, ("m", "f")))
                final[i] = (sid, d)
            else:
                lose(sheet_keys(sid))
                final[i] = (sid, _copy(src))
    if reset:
        lose(list(state["kseq"]))
        final = outside_all()
        tombstone = []
    if not final:
        if outside.get("sheets"):
            # the room would be left without a sheet (it deleted what the outside version kept):
            # the outside version's sheets stay, and whoever deleted them is told
            for sid in sids:
                lose(sheet_keys(sid, ("x",)))
            final = outside_all()
            tombstone = [s for s in tombstone if s not in sids]
        elif tombstone:
            # the outside version has no sheet left, the room had changed the ones it deleted: keep those
            final = [(sid, _sheet_out(c, sid)) for sid in tombstone]
            tombstone = []

    for attempt in (0, 1):
        new_c = _content(model(final), _MapAlloc(state, c, base_c, [sid for sid, _d in final]))
        # the room's deleted sheets stay restorable, and so does a sheet deleted outside that people in
        # the room had changed (their edits live on in it)
        keep = [] if reset else [sid for sid in c["order"] if sid not in new_c["sheets"] and (c["sheets"][sid]["gone"] or sid in tombstone)]
        for sid in keep:
            sh = _copy(c["sheets"][sid])
            sh["gone"] = True
            sh["fields"] = [fk for fk in sh["fields"] if fk not in new_c["fields"]]
            new_c["fields"].update({fk: _copy(c["fields"][fk]) for fk in sh["fields"]})
            new_c["merges"].update({mid: _copy(c["merges"][mid]) for mid in sh["merges"]})
            new_c["sheets"][sid] = sh
            new_c["order"].append(sid)
        if attempt == 0 and (_grid_ids(new_c) > MAX_GRID_IDS or _cells(new_c) > _cell_cap()):
            # the room's own sheets on top of the outside version would outgrow the live state: the
            # outside version alone (which fits) is what the room holds now
            reset = True
            lose(list(state["kseq"]))
            final, tombstone = outside_all(), []
            continue
        break
    _align_keys(new_c, c)
    out_c = _content(outside, _MapAlloc(state, new_c, base_c, sids))
    _align_keys(out_c, new_c)

    changes = _diff(c, new_c)
    patch, before = _patch_of(changes)
    seq = None
    if patch:
        seq = state["seq"] = state["seq"] + 1
        for path, old, new in changes:
            for key, sid in _changed_keys(path, old, new):
                state["kseq"][key] = [seq, by]
                if sid:
                    state["sheetw"].setdefault(sid, {})[by] = seq
    state["content"] = new_c
    state["base"] = _base_of(new_c, out_c)
    state["basis"] = _basis(out_c, state["seq"])
    for sid in [s for s in state["sheetw"] if s not in new_c["sheets"]]:
        del state["sheetw"][sid]
    return {"seq": seq, "patch": patch, "before": before, "overwritten": sorted(overwritten),
            "conflicts": conflicts, "reset": reset}


def _changed_keys(path: tuple, old: list, new: list):
    """The object keys (and their sheet) an outside change wrote: a cell's value and its style
    attributes separately, so a style-only change is not a value conflict."""
    if path[0] in ("merges", "fields"):
        obj = (new or old)[0]
        return [(f"{path[0][0]}/{path[1]}", obj.get("sheet"))]
    if path[0] != "sheets":
        return []
    sid = path[1]
    if len(path) == 2:
        return [(f"x/{sid}", sid), (f"n/{sid}", sid)]
    if path[2] == "gone":
        return [(f"x/{sid}", sid)]
    if path[2] == "name":
        return [(f"n/{sid}", sid)]
    if len(path) == 4 and path[2] == "cells":
        o, n = (old[0] if old else {}), (new[0] if new else {})
        out = [(f"v/{sid}/{path[3]}", sid)] if not _eq(_value(o), _value(n)) else []
        so, sn = o.get("s") or {}, n.get("s") or {}
        out += [(f"s/{sid}/{path[3]}/{a}", sid) for a in sorted(set(so) | set(sn)) if not _eq(so.get(a), sn.get(a))]
        return out
    if len(path) == 4 and path[2] in ("rh", "cw"):
        return [(f"{'h' if path[2] == 'rh' else 'w'}/{sid}/{path[3]}", sid)]
    return []
