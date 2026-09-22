"""Pagination guard: real LibreOffice layout, page-count check, shrink-to-fit for marked fields."""
import io

import docx
import pytest
from docx.shared import Pt

from docweave.engine.converters import soffice_path

pytestmark = pytest.mark.skipif(not soffice_path(), reason="需要 LibreOffice 计算真实分页")

LONG = "这是一段较长的测试备注内容用于模拟新内容更长" + "的情况" * 3          # ~35 characters


def _template(tmp_path):
    """TEST-ONLY one-page template whose last line is almost at the bottom of the page."""
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.size = Pt(10.5)
    st.paragraph_format.space_after = Pt(0)
    for i in range(43):
        d.add_paragraph(f"第 {i + 1} 行：测试填充内容（本文档为测试数据）。")
    p = d.add_paragraph("备注：")
    p.add_run("示例备注").font.size = Pt(16)
    path = tmp_path / "测试_满页模板.docx"
    d.save(path)
    return path


def _project(api, tmp_path, shrink: bool):
    pid = api.project("分页保护")
    api.upload_bytes(pid, "测试_备注.csv", ("备注\r\n" + LONG + "\r\n短内容\r\n").encode("utf-8"))
    t = api.template(pid, _template(tmp_path))
    slot = api.text_slot(t["id"], "示例备注", "备注")
    if shrink:
        api.ok(api.c.patch(f"/api/templates/{t['id']}/slots/{slot['id']}", json={"shrink": True, "shrink_min": 6}))
    sheet = api.ok(api.c.get(f"/api/projects/{pid}/sources"))[0]["structure"]["sheets"][0]["name"]
    api.rule(pid, {"mode": "per_row", "table": {"sheet": sheet, "header_row": 0}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": sheet, "header": "备注"}], "target": {"binding": slot["binding"]}}])
    return pid, t


def test_warn_mode_reports_extra_pages(api, tmp_path):
    pid, t = _project(api, tmp_path, shrink=False)
    job = api.job(pid, layout="warn")
    assert job["status"] == "completed"
    items = sorted(api.items(job["id"]), key=lambda i: i["seq"])
    assert any(i["code"] == "page_overflow" and "输出有 2 页，模板为 1 页" in i["message"] for i in items[0]["issues"])
    assert not any(i["code"] == "page_overflow" for i in items[1]["issues"])
    assert items[0]["artifacts"][0]["verification"]["layout"]["pages"] == 2


def test_fit_mode_shrinks_marked_fields(api, tmp_path):
    pid, t = _project(api, tmp_path, shrink=True)
    job = api.job(pid, layout="fit")
    assert job["status"] == "completed", api.items(job["id"])
    items = sorted(api.items(job["id"]), key=lambda i: i["seq"])
    lay = items[0]["artifacts"][0]["verification"]["layout"]
    assert lay["pages"] == 1 and lay["factor"] < 1 and any(i["code"] == "shrunk_to_fit" for i in items[0]["issues"])
    out = api.artifact(items[0]["artifacts"][0]["id"])
    d = docx.Document(io.BytesIO(out))
    last = d.paragraphs[-1]
    assert last.text == "备注：" + LONG
    sizes = {r.font.size.pt for r in last.runs if r.font.size and r.text.strip() and r.text != "备注："}
    assert sizes and max(sizes) < 16 and min(sizes) >= 6
    assert b"urn:docweave" not in out and b"w:sdt>" not in out          # helper marks and field controls removed
    short = docx.Document(io.BytesIO(api.artifact(items[1]["artifacts"][0]["id"])))
    assert {r.font.size.pt for r in short.paragraphs[-1].runs if r.font.size} == {16.0}   # untouched when it fits


def test_fit_mode_without_shrinkable_fields_fails_the_item(api, tmp_path):
    pid, t = _project(api, tmp_path, shrink=False)
    job = api.job(pid, layout="fit", error_policy="skip")
    items = sorted(api.items(job["id"]), key=lambda i: i["seq"])
    assert items[0]["status"] == "failed" and items[0]["issues"][0]["code"] == "page_overflow"
    assert items[1]["status"] == "success"


def test_preview_layout_endpoint(api, tmp_path):
    pid, t = _project(api, tmp_path, shrink=False)
    pv = api.ok(api.c.get(f"/api/projects/{pid}/preview"))
    lay = api.ok(api.c.get(f"/api/projects/{pid}/preview-layout", params={"blob": pv["output"]["blob"]}))
    assert lay["available"] and lay["pages"] == 2 and lay["template_pages"] == 1
    png = api.c.get(f"/api/preview-files/{lay['pdf_blob']}/page/1.png")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"


def test_libreoffice_lays_out_chinese_with_a_cjk_font():
    """Headless LibreOffice must use a real CJK font (glyph widths = 1 em), otherwise page
    counts and exported PDFs are wrong for Chinese documents."""
    import pymupdf

    from docweave.engine.converters import convert
    d = docx.Document()
    d.styles["Normal"].font.size = Pt(12)
    d.add_paragraph("测试中文排版宽度")
    b = io.BytesIO()
    d.save(b)
    pdf = pymupdf.open(stream=convert(b.getvalue(), "docx", "pdf"), filetype="pdf")
    spans = [s for bl in pdf[0].get_text("dict")["blocks"] for ln in bl.get("lines", []) for s in ln["spans"] if "测试" in s["text"]]
    assert spans
    width = spans[0]["bbox"][2] - spans[0]["bbox"][0]
    assert abs(width - 8 * 12) < 6, (spans[0]["font"], width)                 # 8 full-width glyphs at 12 pt
    assert spans[0]["font"] and "Libertine" not in spans[0]["font"]
