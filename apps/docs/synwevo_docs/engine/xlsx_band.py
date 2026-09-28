"""Repeat content blocks in XLSX templates: a band of whole rows copied once per group, with
detail rows inside it that expand within each copy (docs/platform/docs-xlsx-nested.md).

A block is a ``repeat`` slot with ``block: true`` and ``locator: {sheet, first, last}`` (0-based
rows). Detail rows inside it are ordinary XLSX ``repeat`` slots; each copy ``k`` gets the rows of
its sub-group (``groups[k]``, made by ``plan._nest``).

How references follow the copies (block = rows b1..b2, 1-based):

* a formula inside the block that points inside it follows its own copy — a range ending on a
  detail row ends on that copy's last detail row, so a block subtotal covers only its own lines;
  on a detail row, a relative reference to its own row stays on the same line, an absolute one
  (``$5``) points at the copy's first line, and a relative reference to a row above it in the
  block is filled down like Excel does (line i looks at line i-1; the first line at the row above
  the detail row) — running balances such as ``N(C4)+B5`` work as in a single detail row;
* a formula inside the block that points outside it stays where it points, with or without
  ``$`` (rows below the block move by the rows inserted) — unlike a single detail row, which
  keeps Excel's fill-down behaviour;
* a range that contains the whole block grows to contain every copy;
* a reference from outside to part of the block that is a whole argument of SUM, COUNT,
  COUNTA, AVERAGE, MIN, MAX, PRODUCT or SUBTOTAL (after its first argument) becomes one argument
  per copy: ``SUM(D6)`` → ``SUM(D7,D11)``;
* anything else that points at part of the block, or across its edge, is refused (``scan``);
* conditional formats and data validations inside the block follow the same rules: a rule whose
  formulas only look at each cell's own row (or at fixed rows outside the block) is one rule over
  every copy; any other rule inside the block is written for each copy (and each of its template
  rows), its references mapped as a cell formula there would be (``_RuleSet``).

The rest of the workbook moves by one row shift (``XlsxDoc.shift_rows``); the block's own rows
are taken out first and generated copy by copy from formulas tokenised once.
"""
from __future__ import annotations

import copy
import re

from lxml import etree
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.formula.translate import Translator

from . import values as V
from .limits import LIMITS
from .ooxml import NS
from .refshift import _PART_RE, _REF_RE, _ROWS_RE, MAX_ROW, Shift, split_sheet
from .tabular import col_letter, parse_cell, parse_range
from .xlsx_doc import XDR, XlsxDoc, XlsxError, _CELLREF, s

AGGREGATES = {"SUM", "COUNT", "COUNTA", "AVERAGE", "MIN", "MAX", "PRODUCT", "SUBTOTAL"}
MAX_ARGS = 255              # arguments of one Excel function
MAX_FORMULA = 8192          # characters of one Excel formula
PLACEHOLDER = "_dwband"     # stands in for an expanded argument while the rows move
X14 = NS["x14"]
XM = NS["xm"]
CHART = "http://schemas.openxmlformats.org/drawingml/2006/chart"


# ------------------------------------------------------------------ slots
def is_block(slot: dict) -> bool:
    return slot.get("kind") == "repeat" and bool(slot.get("block"))


def rows_of(slot: dict) -> tuple[int, int]:
    """A block's rows, 1-based."""
    loc = slot["locator"]
    return int(loc["first"]) + 1, int(loc["last"]) + 1


def nesting(slots: list[dict]) -> dict[str, str]:
    """{detail row slot id: block slot id} for the detail rows inside an XLSX block."""
    out = {}
    blocks = [b for b in slots if is_block(b) and "first" in (b.get("locator") or {})]
    for sl in slots:
        loc = sl.get("locator") or {}
        if sl.get("kind") != "repeat" or sl.get("block") or "row" not in loc:
            continue
        for b in blocks:
            b1, b2 = rows_of(b)
            if b["locator"].get("sheet") == loc.get("sheet") and b1 <= int(loc["row"]) + 1 <= b2:
                out[sl["id"]] = b["id"]
    return out


def check_slots(slots: list[dict]) -> None:
    """What the fields themselves must not do around a block (the file is checked by ``scan``)."""
    blocks = [b for b in slots if is_block(b) and "first" in (b.get("locator") or {})]
    nest = nesting(slots)
    for i, b in enumerate(blocks):
        b1, b2 = rows_of(b)
        if b2 <= b1:
            raise XlsxError("band_rows", "重复内容块至少要有两行；只重复一行请用「重复明细行」")
        for o in blocks[i + 1:]:
            o1, o2 = rows_of(o)
            if o["locator"]["sheet"] == b["locator"]["sheet"] and not (o2 < b1 or o1 > b2):
                raise XlsxError("band_overlap", f"第 {o1}–{o2} 行与重复内容块「{b['name']}」（第 {b1}–{b2} 行）重叠：内容块之间不能交叉或嵌套")
        details = {int(s_["locator"]["row"]) + 1 for s_ in slots if nest.get(s_["id"]) == b["id"]}
        for sl in slots:
            loc = sl.get("locator") or {}
            if loc.get("sheet") != b["locator"]["sheet"]:
                continue
            if sl.get("kind") == "region":
                r1, _, r2, _ = parse_range(loc["range"])
                if not (r2 + 1 < b1 or r1 + 1 > b2):
                    raise XlsxError("band_region", f"区域字段「{sl['name']}」与重复内容块「{b['name']}」相交：区域字段不能放在内容块里")
            elif sl.get("kind") == "cell":
                r, _ = parse_cell(loc["cell"])
                if r + 1 in details:
                    raise XlsxError("band_detail_cell", f"单元格 {loc['cell']} 在重复内容块「{b['name']}」的明细行上：明细行上的单元格只能是明细列")
        # the block's own columns, whichever was made first: a column on a row that later became a
        # detail row would be written once per copy onto whatever row that turned into
        for col in b.get("columns") or []:
            r, _ = parse_cell(col["cell"])
            if r + 1 in details:
                raise XlsxError("band_detail_cell", f"单元格 {col['cell']} 在重复内容块「{b['name']}」的明细行上：明细行上的单元格只能是明细列")


# ------------------------------------------------------------------ references
class Ref:
    """One A1 reference as written: endpoints keep their columns and `$` flags."""

    def __init__(self, prefix: str, a: list, b: list | None, rows_only: bool):
        self.prefix, self.a, self.b, self.rows_only = prefix, a, b, rows_only

    @property
    def lo(self) -> int:
        return self.a[3]

    @property
    def hi(self) -> int:
        return (self.b or self.a)[3]

    @property
    def abs_lo(self) -> bool:
        return self.a[2] == "$"

    @property
    def abs_hi(self) -> bool:
        return (self.b or self.a)[2] == "$"

    def render(self, lo: int, hi: int) -> str:
        a, b = self.a, self.b
        if self.rows_only:
            return f"{self.prefix}{a[2]}{lo}:{b[2]}{hi}"
        if b is None:
            return f"{self.prefix}{a[0]}{a[1]}{a[2]}{lo}"
        return f"{self.prefix}{a[0]}{a[1]}{a[2]}{lo}:{b[0]}{b[1]}{b[2]}{hi}"

    def text(self) -> str:
        return self.render(self.lo, self.hi)

    def render_rows(self, lo: int, hi: int, pin_lo: bool, pin_hi: bool) -> str:
        """Like ``render``, with each end's row marked absolute (`$`) or relative as given."""
        a, b = self.a, self.b
        da, db = "$" if pin_lo else "", "$" if pin_hi else ""
        if self.rows_only:
            return f"{self.prefix}{da}{lo}:{db}{hi}"
        if b is None:
            return f"{self.prefix}{a[0]}{a[1]}{da}{lo}"
        return f"{self.prefix}{a[0]}{a[1]}{da}{lo}:{b[0]}{b[1]}{db}{hi}"


def parse_ref(value: str, host_sheet: str) -> tuple[str, Ref] | None:
    """(the sheet it points at, the reference) for an A1 cell, range or row range; None for
    names, structured and column-only references and 3-D references (left as they are)."""
    sheet, area = split_sheet(value)
    if sheet is not None and ":" in sheet:
        return None
    if "[" in area:
        return None
    prefix = value[: len(value) - len(area)]
    m = _REF_RE.match(area)
    if m:
        a = list(_PART_RE.match(m.group("a")).groups())
        a[3] = int(a[3])
        b = None
        if m.group("b"):
            b = list(_PART_RE.match(m.group("b")).groups())
            b[3] = int(b[3])
            if b[3] < a[3]:                  # D6:C5 is rows 5–6: keep each row with its own `$`
                a[2], a[3], b[2], b[3] = b[2], b[3], a[2], a[3]
        return (host_sheet if sheet is None else sheet), Ref(prefix, a, b, False)
    m = _ROWS_RE.match(area)
    if m:
        ga, gb = m.group("a"), m.group("b")
        a = ["", "", "$" if ga.startswith("$") else "", int(ga.lstrip("$"))]
        b = ["", "", "$" if gb.startswith("$") else "", int(gb.lstrip("$"))]
        if b[3] < a[3]:
            a, b = b, a
        return (host_sheet if sheet is None else sheet), Ref(prefix, a, b, True)
    return None


def where(lo: int, hi: int, b1: int, b2: int, within: bool = False) -> str:
    """Rows lo..hi against the block b1..b2: outside, inside, covering (contains the whole block)
    or crossing (its edge). Exactly the block's rows is "covering" seen from outside it (it grows
    to every copy) and "inside" seen from within (``within``: it follows its own copy)."""
    if hi < b1 or lo > b2:
        return "outside"
    if lo <= b1 and hi >= b2 and not (within and lo == b1 and hi == b2):
        return "covering"
    if lo >= b1 and hi <= b2:
        return "inside"
    return "crossing"


def _items(text: str):
    return Tokenizer("=" + text).items


def _is(t, typ, sub) -> bool:
    return t.type == typ and t.subtype == sub


def aggregate_args(items) -> set[int]:
    """Indices of the range operands that are a whole argument of an aggregate function
    (SUBTOTAL: not its first argument, the function number)."""
    out: set[int] = set()
    stack: list[list] = []
    sig = [i for i, t in enumerate(items) if t.type != Token.WSPACE]
    for pos, i in enumerate(sig):
        t = items[i]
        if _is(t, Token.FUNC, Token.OPEN):
            name = t.value[:-1].upper()
            stack.append([name.rsplit(".", 1)[-1] if name.startswith("_XLFN.") else name, 0])
        elif _is(t, Token.PAREN, Token.OPEN):
            stack.append([None, 0])
        elif t.subtype == Token.CLOSE and t.type in (Token.FUNC, Token.PAREN):
            if stack:
                stack.pop()
        elif _is(t, Token.SEP, Token.ARG):
            if stack:
                stack[-1][1] += 1
        elif t.type == Token.OPERAND and t.subtype == Token.RANGE and stack and stack[-1][0] in AGGREGATES:
            prev = items[sig[pos - 1]] if pos else None
            nxt = items[sig[pos + 1]] if pos + 1 < len(sig) else None
            whole_start = prev is not None and (_is(prev, Token.FUNC, Token.OPEN) or _is(prev, Token.SEP, Token.ARG))
            whole_end = nxt is not None and (_is(nxt, Token.SEP, Token.ARG) or _is(nxt, Token.FUNC, Token.CLOSE))
            if whole_start and whole_end and (stack[-1][0] != "SUBTOTAL" or stack[-1][1] >= 1):
                out.add(i)
    return out


def most_args(text: str) -> int:
    """The largest number of arguments any function call in ``text`` has."""
    stack: list = []
    most = 0
    items = [t for t in _items(text) if t.type != Token.WSPACE]
    for n, t in enumerate(items):
        if _is(t, Token.FUNC, Token.OPEN):
            empty = n + 1 < len(items) and _is(items[n + 1], Token.FUNC, Token.CLOSE)
            stack.append(0 if empty else 1)
        elif _is(t, Token.PAREN, Token.OPEN):
            stack.append(None)
        elif _is(t, Token.SEP, Token.ARG) and stack and stack[-1] is not None:
            stack[-1] += 1
        elif t.subtype == Token.CLOSE and t.type in (Token.FUNC, Token.PAREN) and stack:
            got = stack.pop()
            if got is not None:
                most = max(most, got)
    return most


# ------------------------------------------------------------------ checking the file
def _sqref_areas(sqref: str) -> list[tuple[int, int, int, int]]:
    """0-based (r1, c1, r2, c2) of each area of a space-separated sqref."""
    out = []
    for part in (sqref or "").split():
        try:
            out.append(parse_range(part.replace("$", "")))
        except ValueError:
            continue
    return out


def _first_cell(ref: str) -> str:
    try:
        r1, c1, _, _ = parse_range(ref.replace("$", ""))
    except ValueError:
        return ref
    return f"{col_letter(c1)}{r1 + 1}"


def _span(ref: str | None) -> tuple[int, int] | None:
    """The rows (1-based) an area in the file covers: a cell or range, whole rows (`3:5`) or whole
    columns (`A:B`, every row — how Excel stores a pivot source picked by column headers). None when
    it is not an area at all; the caller then has nothing to check."""
    area = (ref or "").replace("$", "").strip()
    try:
        r1, _, r2, _ = parse_range(area)
        return r1 + 1, r2 + 1
    except ValueError:
        pass
    a, sep, b = area.partition(":")
    if sep and a.isdigit() and b.isdigit():
        return min(int(a), int(b)), max(int(a), int(b))
    if sep and a.isalpha() and b.isalpha():
        return 1, MAX_ROW
    return None


def _rule_error(kind: str, code: str, sqref: str, ref: str, b1: int, b2: int) -> XlsxError:
    """The refusals for a conditional format (kind "cf") or a data validation ("dv")."""
    if code == "cross":
        if kind == "cf":
            return XlsxError("band_cf", f"条件格式 {sqref} 的公式引用的 {ref} 与重复内容块（第 {b1}–{b2} 行）相交：请让条件格式的区域和公式引用都在块内，或都在块外")
        return XlsxError("band_dv", f"数据验证 {sqref} 的公式引用的 {ref} 与重复内容块（第 {b1}–{b2} 行）相交：请让数据验证的区域和公式引用都在块内，或都在块外")
    if code == "split":
        if kind == "cf":
            return XlsxError("band_cf_split", f"条件格式 {sqref} 既在重复内容块（第 {b1}–{b2} 行）内又在块外，而它的公式引用 {ref} 在每一份里指向的行不同：请把块内和块外分成两条规则，或只引用本行")
        return XlsxError("band_dv_split", f"数据验证 {sqref} 既在重复内容块（第 {b1}–{b2} 行）内又在块外，而它的公式引用 {ref} 在每一份里指向的行不同：请把块内和块外分成两条规则，或只引用本行")
    if kind == "cf":
        return XlsxError("band_cf_map", f"条件格式 {sqref} 使用了 Excel 2010 扩展设置（如数据条、图标集），它的公式引用 {ref} 无法为每一份分别改写：请只引用本行，或把规则移到块外")
    return XlsxError("band_dv_map", f"数据验证 {sqref} 是 Excel 2010 扩展格式，它的公式引用 {ref} 无法为每一份分别改写：请只引用本行，或把规则移到块外")


def classify_rule(kind: str, sqref: str, formulas: list[str], sheet: str, b1: int, b2: int,
                  extended: bool = False) -> str:
    """How a conditional format / data validation follows the copies of the block b1..b2:
    "untouched" (none of its areas meets the block: the row shift moves it), "copy" (its formulas
    only look at each cell's own row, at fixed rows outside the block or at other sheets: one rule
    over every copy), or "map" (all its areas are inside the block and a formula points elsewhere:
    written per copy, each reference mapped as in a cell formula). Refused with a sentence: a
    reference across the block's edge, a rule outside the block pointing into part of it, a rule
    both inside and outside the block that needs mapping, and an Excel 2010 rule that would.

    The formulas are relative to the top-left cell of the rule's first area."""
    areas = _sqref_areas(sqref)
    if not areas:
        return "untouched"
    r0 = areas[0][0] + 1
    places = [where(r1 + 1, r2 + 1, b1, b2, True) for r1, _, r2, _ in areas]
    touches = any(p != "outside" for p in places)
    positional = None
    for text in formulas:
        if not text:
            continue
        try:
            items = _items(text)
        except Exception:  # noqa: BLE001 - not a formula the row shift can read either
            if touches:
                raise _rule_error(kind, "split" if not all(p == "inside" for p in places) else "map", sqref, text[:40], b1, b2) from None
            continue
        for t in items:
            if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                continue
            got = parse_ref(t.value, sheet)
            if got is None or got[0] != sheet:
                continue
            ref = got[1]
            w = where(ref.lo, ref.hi, b1, b2)
            if w == "crossing":
                raise _rule_error(kind, "cross", sqref, t.value, b1, b2)
            own = not ref.abs_lo and not ref.abs_hi and ref.lo == ref.hi == r0
            fixed_outside = ref.abs_lo and ref.abs_hi and w in ("outside", "covering")
            if own or fixed_outside:
                continue
            if not touches:
                if w == "inside":            # a rule outside the block looking at part of it
                    raise _rule_error(kind, "cross", sqref, t.value, b1, b2)
                continue
            positional = positional or t.value
    if not touches:
        return "untouched"
    if positional is None:
        return "copy"
    if not all(p == "inside" for p in places):
        raise _rule_error(kind, "split", sqref, positional, b1, b2)
    if extended:
        raise _rule_error(kind, "map", sqref, positional, b1, b2)
    return "map"


def _rules(root) -> list[dict]:
    """Every conditional format and data validation of a sheet: its element, where its areas are
    written, its formula nodes, and whether it is an Excel 2010 (x14) rule or linked to one."""
    out = []
    for cf in root.findall(s("conditionalFormatting")):
        out.append({"kind": "cf", "el": cf, "attr": "sqref", "sq_el": cf, "formulas": list(cf.iter(s("formula"))),
                    "extended": any(True for _ in cf.iter("{%s}id" % X14))})
    for dv in root.iter(s("dataValidation")):
        out.append({"kind": "dv", "el": dv, "attr": "sqref", "sq_el": dv,
                    "formulas": [f for f in dv if f.tag in (s("formula1"), s("formula2"))], "extended": False})
    for tag, kind in (("conditionalFormatting", "cf"), ("dataValidation", "dv")):
        for el in root.iter("{%s}%s" % (X14, tag)):
            sq = el.find("{%s}sqref" % XM)
            if sq is not None:
                out.append({"kind": kind, "el": el, "attr": None, "sq_el": sq, "formulas": list(el.iter("{%s}f" % XM)),
                            "extended": True})
    for r in out:
        r["sqref"] = (r["sq_el"].get(r["attr"]) if r["attr"] else r["sq_el"].text) or ""
    return out


def scan(x: XlsxDoc, sheet: str, b1: int, b2: int) -> list[tuple]:
    """Refuse what cannot follow the copies of the block on rows b1..b2 of ``sheet`` (an
    XlsxError with the sentence); return the formulas outside the block whose references into it
    are aggregate arguments: [(f element, host sheet, host cell, token indices)].

    Shared formulas must already be expanded (``XlsxDoc.expand_shared_formulas``)."""
    root = x.sheet_root(sheet)
    whole = f"{b1}:{b2}"
    # merged cells: inside the block (copied) or outside it
    for (r1, c1, r2, c2) in x.merges(sheet):
        if where(r1 + 1, r2 + 1, b1, b2, True) not in ("outside", "inside"):
            merged = f"{col_letter(c1)}{r1 + 1}:{col_letter(c2)}{r2 + 1}"
            raise XlsxError("band_merge", f"合并单元格 {merged} 跨过了重复内容块的边界（第 {b1}–{b2} 行）：请让它完全在块内或块外")
    # objects the copies would not carry
    part = x.sheet_info(sheet)["part"]
    for rel in x.pkg.rels(part):
        if rel.external:
            continue
        target = x.pkg.resolve(part, rel.target)
        if not x.pkg.has(target):
            continue
        if rel.type.endswith("/drawing"):
            droot = x.pkg.xml(target)
            for anchor in droot:
                frm, to = anchor.find("{%s}from" % XDR), anchor.find("{%s}to" % XDR)
                if frm is None:
                    continue
                r1 = int(frm.findtext("{%s}row" % XDR) or 0) + 1
                r2 = int(to.findtext("{%s}row" % XDR) or 0) + 1 if to is not None else r1
                if not (r2 < b1 or r1 > b2):
                    cell = f"{col_letter(int(frm.findtext('{%s}col' % XDR) or 0))}{r1}"
                    raise XlsxError("band_objects", f"第 {r1} 行有图片、图形或图表（{cell}）：复制内容块时不会复制它，请移到块外或删除")
        elif rel.type.endswith("/comments") or rel.type.endswith("/threadedComment"):
            croot = x.pkg.xml(target)
            notes = croot.iter(s("comment")) if rel.type.endswith("/comments") else \
                croot.iter("{http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments}threadedComment")
            for cm in notes:
                got = _span(cm.get("ref"))
                if got and not (got[1] < b1 or got[0] > b2):
                    raise XlsxError("band_comment", f"单元格 {cm.get('ref')} 有批注：复制内容块时不会复制批注，请移到块外或删除")
        elif rel.type.endswith("/table"):
            troot = x.pkg.xml(target)
            got = _span(troot.get("ref"))
            if got and not (got[1] < b1 or got[0] > b2):
                name = troot.get("displayName") or troot.get("name") or ""
                raise XlsxError("band_table", f"表格「{name}」（{troot.get('ref')}）与重复内容块相交：Excel 表格不能随内容块复制，请转换为普通区域或移到块外")
    for pv in x.pivot_tables(sheet):
        if not (pv["rows"][1] < b1 or pv["rows"][0] > b2):
            raise XlsxError("band_pivot", f"数据透视表「{pv['name']}」（{pv['ref']}）与重复内容块相交：请把透视表移到块外")
    for hl in root.iter(s("hyperlink")):
        ref = hl.get("ref") or ""
        got = _span(ref)
        if got:
            if not (got[1] < b1 or got[0] > b2):
                raise XlsxError("band_hyperlink", f"单元格 {_first_cell(ref)} 有超链接：复制内容块时不会复制超链接，请移到块外或删除")
    af = root.find(s("autoFilter"))
    got = _span(af.get("ref")) if af is not None else None
    if got:
        if where(got[0], got[1], b1, b2) in ("inside", "crossing"):
            raise XlsxError("band_filter", f"筛选范围 {af.get('ref')} 只包含重复内容块（第 {b1}–{b2} 行）的一部分：请让筛选范围包含整个内容块，或移到块外")
    # conditional formats, validations (x14 too), sparklines
    for rule in _rules(root):
        classify_rule(rule["kind"], rule["sqref"], [f.text for f in rule["formulas"]], sheet, b1, b2, rule["extended"])
    for sp in root.iter("{%s}sparkline" % X14):
        sq = sp.findtext("{%s}sqref" % XM) or ""
        hit = any(not (r2 + 1 < b1 or r1 + 1 > b2) for r1, _, r2, _ in _sqref_areas(sq))
        data = parse_ref(sp.findtext("{%s}f" % XM) or "", sheet) if sp.findtext("{%s}f" % XM) else None
        if data and data[0] == sheet and where(data[1].lo, data[1].hi, b1, b2) in ("inside", "crossing"):
            hit = True
        if hit:
            raise XlsxError("band_sparkline", f"迷你图 {sq} 与重复内容块（第 {b1}–{b2} 行）相交：复制内容块时不会复制迷你图，请移到块外或删除")
    # every formula of the workbook
    cand = []
    for sh in x.sheets:
        name = sh["name"]
        for c in x.sheet_root(name).iter(s("c")):
            f = c.find(s("f"))
            if f is None:
                continue
            m = _CELLREF.match(c.get("r") or "")
            host_row = int(m.group(2)) if m else 0
            host_in = name == sheet and b1 <= host_row <= b2
            cell = f"{name}!{c.get('r')}"
            got = _span(f.get("ref")) if name == sheet and f.get("t") == "array" else None
            if got:
                if got[0] != got[1] and not (got[1] < b1 or got[0] > b2):
                    raise XlsxError("band_array", f"数组公式 {f.get('ref')} 跨越多行且与重复内容块相交，无法随内容块复制")
            if not f.text:
                continue
            try:
                items = _items(f.text)
            except Exception:  # noqa: BLE001
                if host_in:
                    raise XlsxError("band_formula", f"单元格 {cell} 的公式无法解析，不能随重复内容块复制") from None
                continue
            agg = None
            idxs = []
            for i, t in enumerate(items):
                if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                    continue
                got = parse_ref(t.value, name)
                if got is None or got[0] != sheet:
                    continue
                w = where(got[1].lo, got[1].hi, b1, b2, host_in)
                if w == "crossing":
                    raise XlsxError("band_cross", f"单元格 {cell} 的公式引用的 {t.value} 跨过了重复内容块的边界（第 {b1}–{b2} 行）：请让引用完全在块内，或包含整个内容块")
                if w == "inside" and not host_in:
                    if agg is None:
                        agg = aggregate_args(items)
                    if i not in agg:
                        raise XlsxError("band_reference", f"单元格 {cell} 的公式引用了重复内容块内的 {t.value}：内容块会按段复制，这个引用只能指向其中一份。"
                                                          f"请改为引用整个内容块（如 {whole}），或把它写成 SUM、COUNT、COUNTA、AVERAGE、MIN、MAX、PRODUCT、SUBTOTAL 的一个参数（生成时展开到每一份）")
                    idxs.append(i)
            if idxs:
                cand.append((f, name, c.get("r"), idxs))
    # defined names (print areas and titles included), charts, pivot sources
    dn = x.wb.find(s("definedNames"))
    for el in (dn.findall(s("definedName")) if dn is not None else []):
        nm = el.get("name") or ""
        if nm.startswith("_dw_") or not el.text:
            continue
        for ref in _refs_to(el.text, sheet):
            if where(ref.lo, ref.hi, b1, b2) in ("inside", "crossing"):
                shown = nm.split(".", 1)[1] if nm.startswith("_xlnm.") else nm
                raise XlsxError("band_name", f"名称「{shown}」引用的 {ref.text()} 只包含重复内容块（第 {b1}–{b2} 行）的一部分：名称只能指向其中一份。请让它包含整个内容块，或移到块外")
    for pname in x.pkg.names:
        if pname.startswith("xl/charts/chart") and pname.endswith(".xml"):
            for fel in x.pkg.xml(pname).iter("{%s}f" % CHART):
                for ref in _refs_to(fel.text or "", sheet):
                    if where(ref.lo, ref.hi, b1, b2) in ("inside", "crossing"):
                        chart = pname.rsplit("/", 1)[1][:-4]
                        raise XlsxError("band_chart", f"图表「{chart}」的数据系列引用的 {ref.text()} 只包含重复内容块（第 {b1}–{b2} 行）的一部分：图表不会随内容块复制。请让数据系列包含整个内容块，或移到块外")
        elif pname.startswith("xl/pivotCache/pivotCacheDefinition") and pname.endswith(".xml"):
            for ws in x.pkg.xml(pname).iter(s("worksheetSource")):
                got = _span(ws.get("ref")) if ws.get("sheet") == sheet else None
                if got:
                    if where(got[0], got[1], b1, b2) in ("inside", "crossing"):
                        raise XlsxError("band_pivot_source", f"数据透视表的数据源 {ws.get('ref')} 只包含重复内容块（第 {b1}–{b2} 行）的一部分：请让数据源包含整个内容块，或移到块外")
    return cand


def _refs_to(text: str, sheet: str) -> list[Ref]:
    """References to ``sheet`` in a sheet-qualified formula (defined names, chart series)."""
    try:
        items = _items(text)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for t in items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            got = parse_ref(t.value, "\0")
            if got is not None and got[0] == sheet:
                out.append(got[1])
    return out


# ------------------------------------------------------------------ layout of the copies
class Layout:
    """Where each template row of the block lands in copy ``k`` (1-based rows).

    ``m[k][i]``: the rows detail row ``details[i]`` has in copy k (0 = the row is gone)."""

    def __init__(self, b1: int, b2: int, details: list[int], m: list[list[int]], sep: int):
        self.b1, self.b2, self.details, self.m = b1, b2, details, m
        self.idx = {d: i for i, d in enumerate(details)}
        height = b2 - b1 + 1
        self.starts: list[int] = []
        self.heights: list[int] = []
        at = b1
        for mk in m:
            h = height + sum(n - 1 for n in mk)
            self.starts.append(at)
            self.heights.append(h)
            at += h + sep
        self.total = (at - sep - b1) if m else 0

    def row(self, k: int, r: int) -> int:
        return self.starts[k] + (r - self.b1) + sum(self.m[k][i] - 1 for i, d in enumerate(self.details) if d < r)

    def first(self, k: int, i: int) -> int:
        return self.starts[k] + (self.details[i] - self.b1) + sum(self.m[k][j] - 1 for j in range(i))

    def last(self, k: int, i: int) -> int:
        return self.first(k, i) + self.m[k][i] - 1

    def end(self, k: int) -> int:
        return self.starts[k] + self.heights[k] - 1

    def map(self, k: int, r: int, which: str, host: tuple[int, int] | None = None, absolute: bool = False) -> int | None:
        """Row ``r`` of the template block in copy k, as the ``which`` end of a reference
        ("start", "end" or "single"); ``host``: (detail index, line) of the cell writing it.
        From a detail line, a relative reference to a row above the detail row is filled down:
        line j looks j rows further (the previous line, or the row above for the first line)."""
        i = self.idx.get(r)
        if i is None:
            if host is not None and not absolute and r < self.details[host[0]]:
                return self.row(k, r) + host[1]
            return self.row(k, r)
        if host is not None and host[0] == i:
            return self.first(k, i) + (0 if absolute else host[1])
        if self.m[k][i] == 0:
            if which == "single":
                return None
            return self.first(k, i) if which == "start" else self.first(k, i) - 1
        return self.last(k, i) if which == "end" else self.first(k, i)

    def follows_line(self, r: int, host, absolute: bool) -> bool:
        """Whether a reference end at template row ``r``, written on detail line ``host``, moves
        with the line (its own row, or a row above it — filled down) rather than staying put."""
        if host is None or absolute:
            return False
        d = self.details[host[0]]
        return r == d or (r < d and r not in self.idx)

    def span(self, k: int, ref: Ref, host=None) -> tuple[int, int] | None:
        if ref.b is None:
            r = self.map(k, ref.lo, "single", host, ref.abs_lo)
            return None if r is None else (r, r)
        lo = self.map(k, ref.lo, "start", host, ref.abs_lo)
        hi = self.map(k, ref.hi, "end", host, ref.abs_hi)
        if lo is None or hi is None or lo > hi:
            return None
        return lo, hi


class _Formula:
    """A formula of the block's template rows, tokenised once and rendered per copy."""

    def __init__(self, text: str, sheet: str, b1: int, b2: int, where_: str):
        try:
            items = _items(text)
        except Exception:  # noqa: BLE001
            raise XlsxError("band_formula", f"单元格 {where_} 的公式无法解析，不能随重复内容块复制") from None
        self.text = text
        self.values = [t.value for t in items]
        self.refs: list[tuple[int, Ref, str]] = []
        for i, t in enumerate(items):
            if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                continue
            got = parse_ref(t.value, sheet)
            if got is None or got[0] != sheet:
                continue
            ref = got[1]
            w = where(ref.lo, ref.hi, b1, b2, True)
            if w == "outside" and ref.hi < b1:
                continue                     # above the block: never moves
            self.refs.append((i, ref, w))

    def render(self, lay: Layout, k: int, host, delta: int) -> tuple[str, bool]:
        if not self.refs:
            return self.text, False
        vals = list(self.values)
        broken = False
        for i, ref, w in self.refs:
            if w == "inside":
                got = lay.span(k, ref, host)
            elif w == "covering":
                got = (ref.lo, min(ref.hi + delta, MAX_ROW))
            elif ref.lo + delta > MAX_ROW:        # below the block, pushed off the sheet
                got = None
            else:                            # below the block; a range to the last row stays there
                got = (ref.lo + delta, min(ref.hi + delta, MAX_ROW))
            if got is None:
                vals[i] = "#REF!"
                broken = broken or w == "inside"      # a detail row this copy does not have
            else:
                vals[i] = ref.render(*got)
        return "".join(vals), broken


# ------------------------------------------------------------------ conditional formats and validations
CANON = "HHH500000"          # a cell far from any edge: formulas are compared as seen from here


def _translate(text: str, frm: str, to: str) -> str | None:
    """A formula written at cell ``frm`` as it reads at cell ``to`` (relative references move)."""
    try:
        return Translator("=" + text, origin=frm).translate_formula(to)[1:]
    except Exception:  # noqa: BLE001 - a formula the translator cannot read
        return None


def _outside(ref: Ref, b1: int, b2: int, delta: int) -> tuple[int, int] | None:
    """Rows of a reference outside the block (or containing it) after the rows below moved by delta."""
    lo, hi = ref.lo, ref.hi
    if hi < b1:
        return lo, hi
    if lo > b2:
        if lo + delta > MAX_ROW:
            return None
        return lo + delta, min(hi + delta, MAX_ROW)
    got = (lo, min(hi + delta, MAX_ROW))                 # contains the block: grows or shrinks with it
    return None if got[1] < got[0] else got


class _RuleSet:
    """The conditional formats and validations of the block's sheet that meet the block.

    Taken off before the row shift (so it cannot rewrite them against the block's old rows) and put
    back after the copies exist: a "copy" rule as one rule over every copy (its own-row references
    re-anchored to where its first area now starts), a "map" rule as one rule per copy and template
    row, its references mapped like a cell formula's — outside rows and rows the copy does not move
    with pinned (`$`), a detail line's own row and the rows above it relative (filled down).
    Rules whose mapped formulas read the same from their own top-left cell are merged back into one."""

    def __init__(self, root, sheet: str, b1: int, b2: int):
        self.root, self.sheet, self.b1, self.b2 = root, sheet, b1, b2
        self.copy: list[dict] = []
        self.mapped: list[dict] = []
        self.plain: list[tuple] = []          # ignoredError / protectedRange: areas only
        for rule in _rules(root):
            texts = [f.text for f in rule["formulas"]]
            mode = classify_rule(rule["kind"], rule["sqref"], texts, sheet, b1, b2, rule["extended"])
            if mode == "untouched":
                continue
            areas = _sqref_areas(rule["sqref"])
            rule["r0"], rule["c0"] = areas[0][0] + 1, areas[0][1]
            rule["texts"] = texts
            if mode == "map":
                el = rule["el"]
                rule["proto"] = copy.deepcopy(el)
                rule["mark"] = etree.Comment("synwevo-block-rule")
                el.addprevious(rule["mark"])
                el.getparent().remove(el)
                rule["areas"] = [(r1 + 1, c1, r2 + 1, c2) for r1, c1, r2, c2 in areas]
                self.mapped.append(rule)
            else:
                for f in rule["formulas"]:
                    f.text = None             # kept out of the row shift; written again after the copies
                rule["pieces"] = self._park(rule["sq_el"], rule["attr"], rule["sqref"])
                self.copy.append(rule)
        for tag in ("ignoredError", "protectedRange"):
            for el in root.iter(s(tag)):
                sq = el.get("sqref") or ""
                if _sqref_areas(sq) and any(where(r1 + 1, r2 + 1, b1, b2) != "outside" for r1, _, r2, _ in _sqref_areas(sq)):
                    self.plain.append((el, "sqref", self._park(el, "sqref", sq)))

    def _park(self, el, attr, sq: str) -> list:
        """Split the areas at the block's edges: the outside parts stay (the row shift moves them),
        the inside parts are kept aside as template rows for the copies."""
        pieces, outside = [], []
        b1, b2 = self.b1, self.b2
        for r1, c1, r2, c2 in _sqref_areas(sq):
            lo, hi = r1 + 1, r2 + 1
            if hi < b1 or lo > b2:
                pieces.append(("out", None))
                outside.append(_area(lo, c1, hi, c2))
                continue
            if lo < b1:
                pieces.append(("out", None))
                outside.append(_area(lo, c1, b1 - 1, c2))
            pieces.append(("in", (max(lo, b1), c1, min(hi, b2), c2)))
            if hi > b2:
                pieces.append(("out", None))
                outside.append(_area(b2 + 1, c1, hi, c2))
        if attr:
            el.set(attr, " ".join(outside))
        else:
            el.text = " ".join(outside)
        return pieces

    @staticmethod
    def _areas_back(el, attr, pieces, copies_of) -> list[str]:
        now = ((el.get(attr) if attr else el.text) or "").split()
        out = []
        if len(now) == sum(1 for p in pieces if p[0] == "out"):
            it = iter(now)
            for kind, area in pieces:
                out.extend([next(it)] if kind == "out" else copies_of(*area))
        else:                                # the shift dropped an outside part: keep what is left
            out = now + [a for kind, area in pieces if kind == "in" for a in copies_of(*area)]
        return out

    def restore(self, lay: Layout, K: int, delta: int, copies_of) -> None:
        cloned = False
        for el, attr, pieces in self.plain:
            out = self._areas_back(el, attr, pieces, copies_of)
            if out:
                el.set(attr, " ".join(out))
            else:
                _drop(el, attr)
        for rule in self.copy:
            out = self._areas_back(rule["sq_el"], rule["attr"], rule["pieces"], copies_of)
            if not out:
                for f, text in zip(rule["formulas"], rule["texts"]):
                    f.text = text
                _drop(rule["sq_el"], rule["attr"])
                continue
            if rule["attr"]:
                rule["sq_el"].set(rule["attr"], " ".join(out))
            else:
                rule["sq_el"].text = " ".join(out)
            new_r0 = _sqref_areas(out[0])[0][0] + 1
            for f, text in zip(rule["formulas"], rule["texts"]):
                f.text = self._copy_text(text, rule["r0"], new_r0, delta) if text else text
        for rule in self.mapped:
            cloned = self._emit(rule, lay, K, delta) or cloned
        if cloned:
            _renumber_priorities(self.root)
        for cont in list(self.root.iter(s("dataValidations"), "{%s}dataValidations" % X14)):
            if len(cont):
                cont.set("count", str(len(cont)))
            else:
                cont.getparent().remove(cont)

    def _copy_text(self, text: str, r0: int, new_r0: int, delta: int) -> str:
        """A "copy" rule's formula for its new first area: own-row references re-anchored, fixed
        rows outside the block moved with the rows (as the row shift would have)."""
        try:
            items = _items(text)
        except Exception:  # noqa: BLE001
            return text
        for t in items:
            if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                continue
            got = parse_ref(t.value, self.sheet)
            if got is None:
                continue
            target, ref = got
            if target != self.sheet:           # another sheet: relative rows keep their offset from the cell
                lo = ref.lo if ref.abs_lo else ref.lo + new_r0 - r0
                hi = ref.hi if ref.abs_hi else ref.hi + new_r0 - r0
                t.value = ref.render(lo, hi) if 1 <= lo <= hi <= MAX_ROW else "#REF!"
            elif not ref.abs_lo and not ref.abs_hi:
                t.value = ref.render(new_r0, new_r0)
            else:
                span = _outside(ref, self.b1, self.b2, delta)
                t.value = "#REF!" if span is None else ref.render(*span)
        return "".join(t.value for t in items)

    def _map_text(self, text: str, lay: Layout, k: int, host, delta: int) -> str:
        """A "map" rule's formula, already translated to its template row, for copy k."""
        items = _items(text)
        b1, b2 = self.b1, self.b2
        for t in items:
            if t.type != Token.OPERAND or t.subtype != Token.RANGE:
                continue
            got = parse_ref(t.value, self.sheet)
            if got is None:
                continue
            target, ref = got
            if target != self.sheet:
                t.value = ref.render_rows(ref.lo, ref.hi, True, True)      # other sheets stay put
                continue
            w = where(ref.lo, ref.hi, b1, b2, True)
            if w == "inside":
                span = lay.span(k, ref, host)
                pin_lo = not lay.follows_line(ref.lo, host, ref.abs_lo)
                pin_hi = not lay.follows_line(ref.hi, host, ref.abs_hi)
            else:
                span = _outside(ref, b1, b2, delta)
                pin_lo = pin_hi = True
            t.value = "#REF!" if span is None else ref.render_rows(span[0], span[1], pin_lo, pin_hi)
        return "".join(t.value for t in items)

    def _emit(self, rule: dict, lay: Layout, K: int, delta: int) -> bool:
        frm = f"{col_letter(rule['c0'])}{rule['r0']}"
        groups: dict[tuple, dict] = {}
        for r1, c1, r2, c2 in rule["areas"]:
            for r in range(r1, r2 + 1):
                at = f"{col_letter(c1)}{r}"
                texts = [None if t is None else _translate(t, frm, at) if t else t for t in rule["texts"]]
                i = lay.idx.get(r)
                for k in range(K):
                    if i is None:
                        h, h2, host = lay.row(k, r), lay.row(k, r), None
                    else:
                        if not lay.m[k][i]:
                            continue
                        h, h2, host = lay.first(k, i), lay.last(k, i), (i, 0)
                    mapped = [t if not t else self._map_text(t, lay, k, host, delta) for t in texts]
                    here = f"{col_letter(c1)}{h}"
                    key = (c1, tuple(t if not t else (_translate(t, here, CANON) or f"{here}:{t}") for t in mapped))
                    g = groups.setdefault(key, {"areas": [], "texts": mapped, "at": (h, c1)})
                    if (h, c1) < g["at"]:
                        g["at"], g["texts"] = (h, c1), mapped
                    g["areas"].append((h, c1, h2, c2))
        mark = rule["mark"]
        for g in sorted(groups.values(), key=lambda g: g["at"]):
            areas = sorted(g["areas"])
            first = [a for a in areas if (a[0], a[1]) == g["at"]] + [a for a in areas if (a[0], a[1]) != g["at"]]
            el = copy.deepcopy(rule["proto"])
            for node in el.iter():               # identifiers Excel 365 expects to be unique
                for key in [k_ for k_ in node.attrib if k_.rsplit("}", 1)[-1] == "uid"]:
                    del node.attrib[key]
            sq = " ".join(_area(*a) for a in first)
            if rule["attr"]:
                el.set(rule["attr"], sq)
            else:
                el.find("{%s}sqref" % XM).text = sq
            nodes = list(el.iter(s("formula"))) if rule["kind"] == "cf" else [f for f in el if f.tag in (s("formula1"), s("formula2"))]
            for node, text in zip(nodes, g["texts"]):
                node.text = text
            mark.addprevious(el)
        mark.getparent().remove(mark)
        return rule["kind"] == "cf" and bool(groups)


def _renumber_priorities(root) -> None:
    """Rules written per copy share their template rule's priority; give every rule of the sheet its
    own number again, in the same order (an Excel 2010 rule linked to a rule keeps its twin's)."""
    normal = list(root.iter(s("cfRule")))
    ext = list(root.iter("{%s}cfRule" % X14))
    twins = {}
    for i, rule in enumerate(normal):
        for link in rule.iter("{%s}id" % X14):
            if link.text:
                twins[link.text.strip()] = rule
    own = [(int(r.get("priority") or 0), 0, i, r) for i, r in enumerate(normal)] + \
          [(int(r.get("priority") or 0), 1, i, r) for i, r in enumerate(ext) if (r.get("id") or "").strip() not in twins]
    for n, (_, _, _, rule) in enumerate(sorted(own, key=lambda x: x[:3]), start=1):
        rule.set("priority", str(n))
    for rule in ext:
        twin = twins.get((rule.get("id") or "").strip())
        if twin is not None:
            rule.set("priority", twin.get("priority"))


# ------------------------------------------------------------------ filling
_WS_AFTER_ROWBREAKS = ("sheetPr", "dimension", "sheetViews", "sheetFormatPr", "cols", "sheetData", "sheetCalcPr",
                       "sheetProtection", "protectedRanges", "scenarios", "autoFilter", "sortState", "dataConsolidate",
                       "customSheetViews", "mergeCells", "phoneticPr", "conditionalFormatting", "dataValidations",
                       "hyperlinks", "printOptions", "pageMargins", "pageSetup", "headerFooter")


def _area(r1: int, c1: int, r2: int, c2: int) -> str:
    a, b = f"{col_letter(c1)}{r1}", f"{col_letter(c2)}{r2}"
    return a if a == b else f"{a}:{b}"


def fill_band(x: XlsxDoc, band: dict, kids: list[tuple[dict, dict]], values: dict[str, dict], cells: list[dict],
              issues: list[dict], warn) -> Shift | None:
    """Replace the block's rows with one filled copy per row of its value (``values[band id]``).

    ``kids``: the detail row slots inside it with their values (``groups[k]`` = copy k's rows);
    ``cells``: single-value cell slots inside it (the same value in every copy).
    Returns the row shift the rest of the sheet went through (for the fields written later),
    or None when nothing moved. ``warn(warnings, slot)`` turns writer warnings into issues."""
    sheet = band["locator"]["sheet"]
    b1, b2 = rows_of(band)
    height = b2 - b1 + 1
    details = sorted(((int(ks["locator"]["row"]) + 1, ks, kv or {}) for ks, kv in kids), key=lambda d: d[0])
    drows = [d for d, _, _ in details]
    bv = values.get(band["id"]) or {}
    rows: list = list(bv.get("rows") or [])
    placeholder = None
    if not rows and band.get("empty_policy", "remove") == "placeholder":
        rows = [None]
        placeholder = band.get("placeholder", "（无）")
    K = len(rows)
    m, data = [], []
    for k in range(K):
        mk, dk = [], []
        for _, ks, kv in details:
            groups = kv.get("groups") or []
            grp = groups[k] if (placeholder is None and k < len(groups)) else []
            dk.append(grp)
            mk.append(len(grp) if grp else (0 if ks.get("empty_policy", "remove_row") in ("remove_row", "error") else 1))
        m.append(mk)
        data.append(dk)
    sep = 1 if band.get("separator") == "blank" else 0
    lay = Layout(b1, b2, drows, m, sep)
    delta = lay.total - height
    name = band.get("name") or ""
    total = max(x.last_content_row(sheet), b2) + delta
    if total > MAX_ROW:
        raise XlsxError("band_row_limit", f"重复内容块「{name}」展开后工作表「{sheet}」共 {total} 行，超过 Excel 工作表的上限 1,048,576 行")

    x.expand_shared_formulas()
    cand = scan(x, sheet, b1, b2)
    root = x.sheet_root(sheet)
    sd = x._sheet_data(root)
    proto: dict[int, object] = {}
    for row in sd.findall(s("row")):
        r = int(row.get("r"))
        if b1 <= r <= b2:
            proto[r] = row
    count = sum(len(proto[r].findall(s("c"))) * (sum(m[k][lay.idx[r]] for k in range(K)) if r in lay.idx else K)
                for r in proto)
    if count > LIMITS.max_cells:
        raise XlsxError("band_cell_limit", f"重复内容块「{name}」展开后要写入 {count} 个单元格，超过上限 {LIMITS.max_cells}（SYNWEVO_DOCS_MAX_CELLS）")

    # 0. references from outside that expand to every copy: parked as names the row shift leaves alone
    entries: list[Ref] = []
    pending = []
    for f, host_sheet, host_cell, idxs in cand:
        items = _items(f.text)
        for i in idxs:
            entries.append(parse_ref(items[i].value, host_sheet)[1])
            items[i].value = f"{PLACEHOLDER}{len(entries) - 1}"
        f.text = "".join(t.value for t in items)
        pending.append((f, host_sheet, host_cell))
    # the block's rows, merged cells, formatting areas and page breaks come off the sheet
    for r in proto:
        sd.remove(proto[r])
    x._rows_changed()
    inner_merges = []
    mc = root.find(s("mergeCells"))
    if mc is not None:
        for el in list(mc.findall(s("mergeCell"))):
            r1, c1, r2, c2 = parse_range(el.get("ref"))
            if b1 <= r1 + 1 and r2 + 1 <= b2:
                inner_merges.append((r1 + 1, c1, r2 + 1, c2))
                mc.remove(el)
    rules = _RuleSet(root, sheet, b1, b2)
    rb = root.find(s("rowBreaks"))
    inner_breaks, after_band = [], None
    if rb is not None:
        for brk in list(rb.findall(s("brk"))):
            i = int(brk.get("id", "0"))
            if b1 <= i < b2:
                inner_breaks.append((i, dict(brk.attrib)))
                rb.remove(brk)
            elif i == b2:
                after_band = dict(brk.attrib)
                rb.remove(brk)

    # 1. the rest of the workbook moves once
    if K == 0:
        moved = Shift(b1, -height)
    elif delta > 0:
        moved = Shift(b2 + 1, delta, expand_row=b2, expand_from=b1)
    elif delta < 0:
        moved = Shift(b2 + 1 + delta, delta)
    else:
        moved = None
    if moved is not None:
        x.shift_rows(sheet, moved)
    x.touched = True

    # 2. the copies
    tpls: dict[tuple[int, str], _Formula] = {}
    for r, row in proto.items():
        for c in row.findall(s("c")):
            f = c.find(s("f"))
            if f is not None and f.text:
                col = _CELLREF.match(c.get("r")).group(1)
                tpls[(r, col)] = _Formula(f.text, sheet, b1, b2, f"{sheet}!{c.get('r')}")
    broken: dict[int, list[str]] = {}

    def clone(r: int, nr: int, k: int, host) -> None:
        src = proto.get(r)
        if src is None:
            return
        new = copy.deepcopy(src)
        new.set("r", str(nr))
        for c in new.findall(s("c")):
            col = _CELLREF.match(c.get("r")).group(1)
            c.set("r", f"{col}{nr}")
            f = c.find(s("f"))
            if f is None:
                continue
            tpl = tpls.get((r, col))
            if tpl is not None:
                text, bad = tpl.render(lay, k, host, delta)
                f.text = text
                if bad and "#REF!" not in tpl.text:
                    broken.setdefault(k, []).append(f"{col}{nr}")
            if f.get("t") == "array" and f.get("ref"):
                a1, _, a2 = f.get("ref").partition(":")
                c1_, c2_ = parse_cell(a1)[1], parse_cell(a2 or a1)[1]
                f.set("ref", f"{col_letter(c1_)}{nr}" + (f":{col_letter(c2_)}{nr}" if c2_ != c1_ else ""))
        out_rows.append(new)

    out_rows: list = []
    for k in range(K):
        for r in range(b1, b2 + 1):
            i = lay.idx.get(r)
            if i is None:
                clone(r, lay.row(k, r), k, None)
            else:
                for j in range(m[k][i]):
                    clone(r, lay.first(k, i) + j, k, (i, j))
    anchor = None
    for row in sd.findall(s("row")):
        if int(row.get("r")) < b1:
            anchor = row
        else:
            break
    for new in out_rows:
        if anchor is None:
            sd.insert(0, new)
        else:
            anchor.addnext(new)
        anchor = new
    x._rows_changed()

    def copies_of(r1: int, c1: int, r2: int, c2: int) -> list[str]:
        out = []
        for k in range(K):
            lo = lay.map(k, r1, "start")
            hi = lay.map(k, r2, "end")
            if lo is not None and hi is not None and lo <= hi:
                out.append(_area(lo, c1, hi, c2))
        return out

    for r1, c1, r2, c2 in inner_merges:
        for k in range(K):
            i = lay.idx.get(r1)
            if r1 == r2 and i is not None:
                spots = [(lay.first(k, i) + j,) * 2 for j in range(m[k][i])]
            else:
                spots = [(lay.map(k, r1, "start"), lay.map(k, r2, "end"))]
            for lo, hi in spots:
                x._add_merge(root, _area(lo, c1, hi, c2), recount=False)
    mc = root.find(s("mergeCells"))
    if mc is not None:
        if len(mc.findall(s("mergeCell"))):
            mc.set("count", str(len(mc.findall(s("mergeCell")))))
        else:
            root.remove(mc)
    rules.restore(lay, K, delta, copies_of)
    _page_breaks(root, lay, K, inner_breaks, after_band, band.get("separator") == "page_break")

    # 3. aggregate arguments from outside: one per copy
    removed: list[str] = []
    for f, host_sheet, _ in pending:
        host_cell = f.getparent().get("r")    # where the formula is now, after the rows moved

        def expand(mt):
            ref = entries[int(mt.group(1))]
            parts = []
            for k in range(K):
                got = lay.span(k, ref)
                if got is not None:
                    parts.append(ref.render(*got))
            if not parts:
                removed.append(f"{host_sheet}!{host_cell}")
                return "#REF!"
            return ",".join(parts)
        text = re.sub(rf"{PLACEHOLDER}(\d+)", expand, f.text)
        if len(text) > MAX_FORMULA or most_args(text) > MAX_ARGS:
            cell = f"{host_sheet}!{host_cell}"
            raise XlsxError("band_formula_limit", f"公式 {cell} 展开到每一份后超过 Excel 的限制（每个函数最多 255 个参数、每个公式最多 8,192 个字符）：请改为引用整个内容块")
        f.text = text
    if removed:
        uniq = "、".join(list(dict.fromkeys(removed))[:5])
        if K == 0:
            issues.append({"level": "warning", "slot": band["id"], "code": "ref_removed",
                           "message": f"重复内容块「{name}」没有数据，已删除；引用它的公式 {uniq} 变为 #REF!。如需保留公式，请把零条明细处理方式改为「保留一份」"})
        else:
            issues.append({"level": "warning", "slot": band["id"], "code": "ref_removed",
                           "message": f"重复内容块「{name}」的每一段都没有明细行；引用明细行的公式 {uniq} 变为 #REF!。如需保留公式，请把明细行的零条明细处理方式改为「保留一行空白」"})

    # 4. values: the block's columns, fields inside it (same in every copy), its detail rows
    sections = bv.get("sections") or []
    for k in range(K):
        rowv = rows[k]
        for ci, col in enumerate(band.get("columns") or []):
            r, c = parse_cell(col["cell"])
            nr = lay.row(k, r + 1) - 1
            if rowv is None:
                if ci == 0:
                    warn(x.set_cell(sheet, nr, c, V.text(placeholder)), band)
                else:
                    x.clear_cell(sheet, nr, c)
                continue
            v = rowv.get(col["key"])
            if v is None:
                continue
            if v.is_empty:
                x.clear_cell(sheet, nr, c)
            else:
                warn(x.set_cell(sheet, nr, c, v, data_type=col.get("data_type", "auto")), band)
        for cs in cells:
            r, c = parse_cell(cs["locator"]["cell"])
            fv = values.get(cs["id"]) or {}
            val = fv["value"] if fv.get("kind") == "scalar" else V.empty()
            w = x.set_cell(sheet, lay.row(k, r + 1) - 1, c, val, mode=cs.get("mode", "replace"),
                           data_type=cs.get("data_type", "auto"), use_display=fv.get("value_mode") == "display")
            if k == 0:
                warn(w, cs)
        for i, (_, ks, _) in enumerate(details):
            kcols = ks.get("columns") or []
            grp = data[k][i]
            for j, rv in enumerate(grp):
                for col in kcols:
                    v = rv.get(col["key"])
                    if v is None:
                        continue
                    if v.is_empty:
                        x.clear_cell(sheet, lay.first(k, i) + j - 1, col["col"])
                    else:
                        warn(x.set_cell(sheet, lay.first(k, i) + j - 1, col["col"], v, data_type=col.get("data_type", "auto")), ks)
            if not grp and m[k][i] == 1:
                pol = ks.get("empty_policy")
                for ci, col in enumerate(kcols):
                    text = ks.get("placeholder", "（无）") if (pol == "placeholder" and ci == 0) else ""
                    if text:
                        warn(x.set_cell(sheet, lay.first(k, i) - 1, col["col"], V.text(text)), ks)
                    else:
                        x.clear_cell(sheet, lay.first(k, i) - 1, col["col"])
        if broken.get(k):
            sec = sections[k] if k < len(sections) else str(k + 1)
            cells_ = "、".join(broken[k][:5])
            issues.append({"level": "warning", "slot": band["id"], "code": "ref_removed",
                           "message": f"「{sec}」这一段没有明细行，明细行已删除；该段的公式 {cells_} 变为 #REF!。如需保留公式，请把明细行的零条明细处理方式改为「保留一行空白」"})
    return moved


def _drop(el, attr) -> None:
    """A formatting / validation / ignored-error element left without any area goes, and so do
    the containers it leaves empty (an empty <dataValidations> or x14 list is not valid)."""
    if attr is None:                          # x14: the xm:sqref's rule element goes
        el = el.getparent()
    parent = el.getparent()
    parent.remove(el)
    while parent is not None and parent.tag != s("worksheet"):
        if parent.tag in (s("dataValidations"), "{%s}dataValidations" % X14):
            n = len(parent)
            if n:
                parent.set("count", str(n))
                return
        elif len(parent):
            return
        up = parent.getparent()
        if up is None:
            return
        up.remove(parent)
        parent = up


def _page_breaks(root, lay: Layout, K: int, inner: list, after_band: dict | None, between: bool) -> None:
    brks: dict[int, dict] = {}
    for k in range(K):
        for i, attrs in inner:
            idx = lay.idx.get(i)
            at = (lay.last(k, idx) if lay.m[k][idx] else lay.first(k, idx) - 1) if idx is not None else lay.row(k, i)
            brks[at] = attrs
        if between and k < K - 1:
            brks[lay.end(k)] = {"max": "16383", "man": "1"}
    if after_band is not None and K:
        brks[lay.end(K - 1)] = after_band
    rb = root.find(s("rowBreaks"))
    if not brks:
        if rb is not None:                 # breaks inside the block went with it: recount, or drop the list
            _count_breaks(root, rb)
        return
    if rb is None:
        rb = etree.Element(s("rowBreaks"))
        wanted = {s(t) for t in _WS_AFTER_ROWBREAKS}
        last = None
        for child in root:
            if child.tag in wanted:
                last = child
        last.addnext(rb)
    have = {int(b.get("id", "0")): b for b in rb.findall(s("brk"))}
    for at, attrs in brks.items():
        if at in have:
            continue
        el = etree.Element(s("brk"))
        for key, val in attrs.items():
            el.set(key, val)
        el.set("id", str(at))
        have[at] = el
    for b in rb.findall(s("brk")):
        rb.remove(b)
    for at in sorted(have):
        rb.append(have[at])
    _count_breaks(root, rb)


def _count_breaks(root, rb) -> None:
    brks = rb.findall(s("brk"))
    if not brks:
        root.remove(rb)
        return
    rb.set("count", str(len(brks)))
    rb.set("manualBreakCount", str(sum(1 for b in brks if b.get("man") in ("1", "true"))))

