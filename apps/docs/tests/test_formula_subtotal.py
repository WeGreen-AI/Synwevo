"""SUBTOTAL in the built-in formula engine (review item 8a): section subtotals written as
SUBTOTAL(9, …) and a grand total SUBTOTAL(9, whole range) must not count the subtotals twice, and
must compute on the server without the LibreOffice fallback.

TEST FIXTURES: the workbooks are synthetic, built here; LibreOffice is the reference where installed.
"""
from __future__ import annotations

import io
import math
import statistics

import openpyxl
import pytest

from synwevo_docs.engine.formula import Book, Unsupported, XLError, compute_workbook
from synwevo_docs.engine.xlsx_doc import XlsxDoc

soffice = pytest.mark.skipif(not __import__("synwevo_docs.engine.converters", fromlist=["x"]).soffice_path(),
                             reason="需要 LibreOffice")

# column A: two sections of numbers, each followed by its SUBTOTAL(9) row; row 4 is hidden by hand
DATA = [3, 5, 7, None, 11, 13]              # A1:A3, A5:A6 (A4 = the first section's subtotal)
HIDDEN_ROW = 2                              # 0-based: A3 = 7 is hidden


def _workbook(filter_: bool = False, logical: bool = True) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    for i, v in enumerate(DATA, start=1):
        ws.cell(i, 1, v)
    ws["A4"] = "=SUBTOTAL(9,A1:A3)"         # a section subtotal inside the grand total's range
    ws["A7"] = "=SUBTOTAL(9,A5:A6)"
    ws["B1"], ws["B2"], ws["B3"] = "文本", (True if logical else None), 2.5
    ws.row_dimensions[HIDDEN_ROW + 1].hidden = True
    if filter_:
        ws.auto_filter.ref = "A1:B7"
    return wb


FORMULAS = [f"SUBTOTAL({n},A1:A7)" for n in list(range(1, 12)) + list(range(101, 112))] + [
    "SUBTOTAL(9,A1:A3,A5:A6)", "SUBTOTAL(3,B1:B3)", "SUBTOTAL(2,B1:B3)", "SUBTOTAL(9,A:A)", "SUBTOTAL(109,A1:B7)",
    "SUBTOTAL(9,A4)", "SUBTOTAL(9.9,A1:A3)",
]


def _ours(wb, formulas=FORMULAS) -> dict[str, object]:
    ws = wb["S"]
    for i, f in enumerate(formulas):
        ws.cell(10 + i, 4, "=" + f)
    b = io.BytesIO()
    wb.save(b)
    x = XlsxDoc(b.getvalue())
    rep = compute_workbook(x)
    assert not rep["unsupported"], rep["unsupported"]
    out = openpyxl.load_workbook(io.BytesIO(x.pkg.save()), data_only=True)["S"]
    return {f: out.cell(10 + i, 4).value for i, f in enumerate(formulas)}, b.getvalue()


def test_subtotal_skips_nested_subtotals_and_101_to_111_skip_hidden_rows():
    got, _ = _ours(_workbook())
    visible = [3, 5, 11, 13]                  # without the hidden 7
    every = [3, 5, 7, 11, 13]                 # the subtotal rows A4, A7 never count
    assert got["SUBTOTAL(9,A1:A7)"] == sum(every) and got["SUBTOTAL(109,A1:A7)"] == sum(visible)
    assert got["SUBTOTAL(1,A1:A7)"] == pytest.approx(statistics.mean(every))
    assert got["SUBTOTAL(101,A1:A7)"] == pytest.approx(statistics.mean(visible))
    assert (got["SUBTOTAL(2,A1:A7)"], got["SUBTOTAL(102,A1:A7)"]) == (5, 4)
    assert (got["SUBTOTAL(3,A1:A7)"], got["SUBTOTAL(103,A1:A7)"]) == (5, 4)
    assert (got["SUBTOTAL(4,A1:A7)"], got["SUBTOTAL(5,A1:A7)"]) == (13, 3)
    assert got["SUBTOTAL(6,A1:A7)"] == math.prod(every)
    assert got["SUBTOTAL(7,A1:A7)"] == pytest.approx(statistics.stdev(every))
    assert got["SUBTOTAL(8,A1:A7)"] == pytest.approx(statistics.pstdev(every))
    assert got["SUBTOTAL(10,A1:A7)"] == pytest.approx(statistics.variance(every))
    assert got["SUBTOTAL(111,A1:A7)"] == pytest.approx(statistics.pvariance(visible))
    assert got["SUBTOTAL(9,A1:A3,A5:A6)"] == sum(every)
    assert (got["SUBTOTAL(3,B1:B3)"], got["SUBTOTAL(2,B1:B3)"]) == (3, 1)      # COUNTA / COUNT over text, TRUE, 2.5
    assert got["SUBTOTAL(9,A:A)"] == sum(every)
    assert got["SUBTOTAL(9,A4)"] == 0                                           # only a subtotal: nothing to add
    assert got["SUBTOTAL(9.9,A1:A3)"] == 15                                     # the function number is truncated


def test_subtotal_refusals_and_errors():
    cells = {"S": {(0, 0): ("v", 1.0), (1, 0): ("v", 2.0)}}
    b = Book({"S": {**cells["S"], (5, 5): ("f", "SUBTOTAL(12,A1:A2)"), (5, 6): ("f", "SUBTOTAL(9)"),
                    (5, 7): ("f", "SUBTOTAL(9,1+1)"), (5, 8): ("f", "SUBTOTAL(9,#REF!)")}})
    assert b.value("S", 5, 5) == XLError("#VALUE!") and b.value("S", 5, 6) == XLError("#VALUE!")
    assert b.value("S", 5, 8) == XLError("#REF!")        # a section whose lines were removed, as Excel shows it
    with pytest.raises(Unsupported):
        b.value("S", 5, 7)                     # not a reference: left to the fallback, not guessed


def test_rows_hidden_under_a_filter_are_left_to_the_fallback():
    """Excel skips rows a filter hid even for 1–11; the file cannot say which hidden rows a filter
    hid, so the engine does not guess."""
    wb = _workbook(filter_=True)
    wb["S"]["D10"] = "=SUBTOTAL(9,A1:A7)"
    b = io.BytesIO()
    wb.save(b)
    rep = compute_workbook(XlsxDoc(b.getvalue()))
    # A4's own SUBTOTAL(9,A1:A3) spans the hidden row too; A7's (A5:A6) does not and is computed
    assert sorted((ref, why) for _, ref, why in rep["unsupported"]) == [("A4", "SUBTOTAL 遇到可能被筛选隐藏的行"),
                                                                         ("D10", "SUBTOTAL 遇到可能被筛选隐藏的行")]


# LibreOffice differs from Excel in two places, so they are not compared: it has no logical type
# (TRUE in a range is the number 1, which COUNT then counts), and a SUBTOTAL over a single cell that
# is itself a SUBTOTAL result is not skipped there. The engine follows Excel's rules for both.
LO_COMPARABLE = [f for f in FORMULAS if f != "SUBTOTAL(9,A4)"]


@soffice
def test_cross_check_subtotal_against_libreoffice():
    from synwevo_docs.engine.converters import recalc_xlsx
    got, data = _ours(_workbook(logical=False), LO_COMPARABLE)
    lo = openpyxl.load_workbook(io.BytesIO(recalc_xlsx(data)), data_only=True)["S"]
    diffs = []
    for i, f in enumerate(LO_COMPARABLE):
        a, e = got[f], lo.cell(10 + i, 4).value
        if not (isinstance(a, (int, float)) and isinstance(e, (int, float)) and abs(a - e) < 1e-9 * max(1, abs(e))):
            diffs.append((f, a, e))
    assert not diffs, diffs
