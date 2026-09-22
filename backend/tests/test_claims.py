"""Tests backing specific claims in docs/FORMATS.md."""
import io
import re
import zipfile

import openpyxl
import pymupdf
from openpyxl.chart import BarChart, Reference
from openpyxl.comments import Comment
from openpyxl.worksheet.table import Table

from docweave.engine import pdf_engine as pe
from docweave.engine import values as V
from docweave.engine.templates import pdf_add_slot, pdf_fill, pdf_working_copy
from docweave.engine.xlsx_doc import XlsxDoc


def test_xlsx_row_insert_shifts_tables_names_charts_comments():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "数据"
    ws.append(["品名", "数量"])           # row 1 header
    ws.append(["模板", 1])                # row 2 template row
    ws["A4"] = "下方备注"
    ws["A4"].comment = Comment("批注", "测试")
    ws.add_table(Table(displayName="明细表", ref="A1:B2"))
    wb.defined_names["_xlnm.Print_Area"] = openpyxl.workbook.defined_name.DefinedName("_xlnm.Print_Area", localSheetId=0, attr_text="'数据'!$A$1:$B$4")
    ch = BarChart()
    ch.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    ws.add_chart(ch, "D6")
    b = io.BytesIO()
    wb.save(b)
    x = XlsxDoc(b.getvalue())
    x.insert_copies_of_row("数据", 2, 3)
    for i in range(4):
        x.set_cell("数据", 1 + i, 0, V.text(f"项{i + 1}"))
    out = x.finalize()
    z = zipfile.ZipFile(io.BytesIO(out))
    table_xml = z.read([n for n in z.namelist() if n.startswith("xl/tables/")][0]).decode()
    assert 'ref="A1:B5"' in table_xml                                   # table grew over the new detail rows
    wbxml = z.read("xl/workbook.xml").decode()
    assert "$A$1:$B$7" in wbxml                                          # print area moved/grew
    chart_xml = z.read([n for n in z.namelist() if n.startswith("xl/charts/chart")][0]).decode()
    assert re.search(r"\$B\$2:\$B\$5", chart_xml), chart_xml[:500]       # chart series covers all detail rows
    comments = z.read([n for n in z.namelist() if "comments" in n and n.endswith(".xml")][0]).decode()
    assert 'ref="A7"' in comments                                        # comment moved with its cell
    drawing = z.read([n for n in z.namelist() if n.startswith("xl/drawings/drawing")][0]).decode()
    from lxml import etree
    X = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
    rows = [int(e.text) for e in etree.fromstring(drawing.encode()).iter(X + "row")]
    assert rows[0] == 8, rows                                             # chart anchor (row 6 → 9, 0-based 8) moved down
    wb2 = openpyxl.load_workbook(io.BytesIO(out))
    assert wb2["数据"]["A7"].value == "下方备注"


def test_scanned_pdf_template_clears_pixels(fixtures):
    data = (fixtures / "测试_扫描单据.pdf").read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    page = doc[0]
    assert pe.page_kind(page) == "scanned"
    # the name line is drawn at y≈200/700 of the image → normalised coords
    nrect = [0.28, 0.27, 0.60, 0.36]
    slot = pdf_add_slot(data, {"kind": "pdf_box", "page": 0, "nrect": nrect, "name": "姓名"}, [])
    assert slot["clear"] == "text_pixels"
    work, warns = pdf_working_copy(data, [slot])
    out, issues = pdf_fill(work, [slot], {slot["id"]: {"kind": "scalar", "value": V.text("新姓名")}})
    res = pymupdf.open(stream=out, filetype="pdf")[0]
    assert "新姓名" in res.get_text()
    # pixels of the old handwriting/print inside the box are gone (clip excluding the new text line area)
    r = pe.visual_norm_to_unrot(res, nrect)
    before = page.get_pixmap(clip=r, dpi=72)
    after_doc = pymupdf.open(stream=work, filetype="pdf")
    after = after_doc[0].get_pixmap(clip=r, dpi=72)
    dark = lambda pm: sum(1 for i in range(0, len(pm.samples), pm.n) if pm.samples[i] < 128)
    assert dark(before) > 50 and dark(after) == 0                        # original dark pixels removed in the working copy
