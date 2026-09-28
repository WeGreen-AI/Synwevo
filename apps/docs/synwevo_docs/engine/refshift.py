"""Shift A1-style references when rows are inserted into / removed from a sheet.

Rules (documented in docs/FORMATS.md):
* inserting ``delta`` rows before ``start`` moves every reference to a row >=
  ``start`` down by ``delta`` (relative and absolute alike, as Excel does);
* a *range* whose last row is the template row (``expand_row``) and whose first
  row is <= it grows to cover the inserted rows – so ``SUM(D5:D5)`` over a
  one-row detail template becomes ``SUM(D5:D9)``;
* removing rows turns references to removed cells into ``#REF!`` and shrinks
  ranges that partially cover them;
* a sheet has ``MAX_ROW`` rows: inserting pushes nothing past it. A range whose end would go
  past the last row stops at the last row (``SUM(C2:C1048576)`` or a validation over
  ``B2:B1048576`` stays as it is, as in Excel); a single cell pushed off becomes ``#REF!``.
"""
from __future__ import annotations

import re

from openpyxl.formula.tokenizer import Token, Tokenizer

_CELL = r"\$?[A-Za-z]{1,3}\$?\d+"
_COL = r"\$?[A-Za-z]{1,3}"
_ROW = r"\$?\d+"
_REF_RE = re.compile(rf"^(?P<a>{_CELL})(?::(?P<b>{_CELL}))?$")
_ROWS_RE = re.compile(rf"^(?P<a>{_ROW}):(?P<b>{_ROW})$")
_COLS_RE = re.compile(rf"^{_COL}:{_COL}$")
_PART_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")

MAX_ROW = 1_048_576         # rows in an Excel worksheet


class Shift:
    def __init__(self, start: int, delta: int, expand_row: int | None = None, expand_single: bool = False):
        self.start = start          # first affected row (1-based)
        self.delta = delta          # >0 insert, <0 delete
        self.expand_row = expand_row
        self.expand_single = expand_single  # sqref lists: a single cell on the template row grows too

    def row(self, r: int) -> int | None:
        """Map a single row; None means the row was deleted (or pushed past the last row)."""
        if self.delta >= 0:
            if r < self.start:
                return r
            return r + self.delta if r + self.delta <= MAX_ROW else None
        lo, hi = self.start, self.start - self.delta - 1
        if lo <= r <= hi:
            return None
        return r + self.delta if r > hi else r

    def span(self, a: int, b: int) -> tuple[int, int] | None:
        if self.delta >= 0:
            na = a + self.delta if a >= self.start else a
            if b >= self.start:
                nb = b + self.delta
            elif self.expand_row is not None and b == self.expand_row and a <= self.expand_row:
                nb = b + self.delta
            else:
                nb = b
            if na > MAX_ROW:
                return None             # the whole range was pushed off the sheet
            return na, min(nb, MAX_ROW)
        lo, hi = self.start, self.start - self.delta - 1
        if a >= lo and b <= hi:
            return None
        na = a if a < lo else (lo if a <= hi else a + self.delta)
        nb = b if b < lo else (lo - 1 if b <= hi else b + self.delta)
        return na, nb


def _shift_cell_ref(ref: str, row: int) -> str:
    m = _PART_RE.match(ref)
    return f"{m.group(1)}{m.group(2)}{m.group(3)}{row}"


def _row_of(ref: str) -> int:
    return int(_PART_RE.match(ref).group(4))


def shift_area(ref: str, sh: Shift) -> str:
    """Shift a bare area reference (no sheet prefix).  Returns '#REF!' on deletion."""
    m = _REF_RE.match(ref)
    if m:
        a, b = m.group("a"), m.group("b")
        if b is None:
            ra = _row_of(a)
            if sh.expand_single and sh.delta > 0 and sh.expand_row is not None and ra == sh.expand_row:
                return f"{a}:{_shift_cell_ref(a, min(ra + sh.delta, MAX_ROW))}"
            nr = sh.row(ra)
            return "#REF!" if nr is None else _shift_cell_ref(a, nr)
        ra, rb = _row_of(a), _row_of(b)
        lo, hi = (ra, rb) if ra <= rb else (rb, ra)
        res = sh.span(lo, hi)
        if res is None:
            return "#REF!"
        na, nb = res
        if ra <= rb:
            return f"{_shift_cell_ref(a, na)}:{_shift_cell_ref(b, nb)}"
        return f"{_shift_cell_ref(a, nb)}:{_shift_cell_ref(b, na)}"
    m = _ROWS_RE.match(ref)
    if m:
        a, b = m.group("a"), m.group("b")
        ra, rb = int(a.lstrip("$")), int(b.lstrip("$"))
        res = sh.span(min(ra, rb), max(ra, rb))
        if res is None:
            return "#REF!"
        return f"{'$' if a.startswith('$') else ''}{res[0]}:{'$' if b.startswith('$') else ''}{res[1]}"
    return ref


def split_sheet(ref: str) -> tuple[str | None, str]:
    """Split "'My Sheet'!A1:B2" into ("My Sheet", "A1:B2")."""
    if "!" not in ref:
        return None, ref
    idx = ref.rfind("!")
    sheet, area = ref[:idx], ref[idx + 1:]
    if sheet.startswith("'") and sheet.endswith("'"):
        sheet = sheet[1:-1].replace("''", "'")
    return sheet, area


def shift_sqref(sqref: str, sh: Shift) -> str:
    """Space separated list of areas (sqref attribute).  Deleted areas are dropped."""
    out = []
    for part in sqref.split():
        s = shift_area(part, sh)
        if s != "#REF!":
            out.append(s)
    return " ".join(out)


def shift_formula(formula: str, sh: Shift, target_sheet: str, host_sheet: str) -> str:
    """Shift references to ``target_sheet`` inside ``formula`` (without leading '=')
    evaluated on ``host_sheet``."""
    if not formula:
        return formula
    try:
        tok = Tokenizer("=" + formula)
    except Exception:
        return formula
    changed = False
    for t in tok.items:
        if t.type != Token.OPERAND or t.subtype != Token.RANGE:
            continue
        sheet, area = split_sheet(t.value)
        if sheet is not None and ":" in sheet:
            continue  # 3-D reference; left untouched (documented limitation)
        eff = sheet if sheet is not None else host_sheet
        if eff != target_sheet:
            continue
        if "[" in area or not (_REF_RE.match(area) or _ROWS_RE.match(area) or _COLS_RE.match(area)):
            continue  # names / structured refs
        new_area = shift_area(area, sh)
        if new_area != area:
            prefix = t.value[: len(t.value) - len(area)]
            t.value = "#REF!" if new_area == "#REF!" else prefix + new_area
            changed = True
    if not changed:
        return formula
    rendered = tok.render()
    return rendered[1:] if rendered.startswith("=") else rendered


def refers_to_sheet(formula: str, sheet: str, host_sheet: str) -> bool:
    try:
        tok = Tokenizer("=" + formula)
    except Exception:
        return False
    for t in tok.items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            s, _ = split_sheet(t.value)
            if (s if s is not None else host_sheet) == sheet:
                return True
    return False
