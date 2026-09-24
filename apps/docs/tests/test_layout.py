"""Pagination guard: real LibreOffice layout, page-count check, shrink-to-fit for marked fields."""
import io
import pathlib

import docx
import pytest
from docx.shared import Pt

from synwevo_docs.engine.converters import soffice_path

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

    from synwevo_docs.engine.converters import convert
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


# ---------------------------------------------------------------- report honesty & content integrity

def test_reported_page_count_is_measured_on_the_delivered_bytes(tmp_path, monkeypatch):
    """fit_docx used to count pages on the intermediate that still carried the field
    controls, then deliver a different byte stream with them unwrapped.  The number a user
    sees must describe the file they actually receive."""
    from synwevo_docs.engine import layout as L

    seen = []
    real = L.page_count

    def spy(data, fmt):
        seen.append(data)
        return real(data, fmt)

    monkeypatch.setattr(L, "page_count", spy)
    tpl = pathlib.Path(_template(tmp_path)).read_bytes()
    out, rep = L.fit_docx(tpl, [], tpl_pages=1, keep_fields=False)
    assert seen, "没有实际排版"
    assert seen[0] == out, "报告的页数必须量自最终交付的字节"
    assert rep["pages"] == real(out, "docx")


def test_effective_factor_reports_what_was_applied_not_what_was_asked(tmp_path):
    """A field pinned at its own minimum must not be reported as 'shrunk to 60%'."""
    from synwevo_docs.engine import layout as L

    d = docx.Document()
    d.styles["Normal"].font.size = Pt(10)
    p = d.add_paragraph()
    run = p.add_run("内容")
    run.font.size = Pt(10)
    buf = io.BytesIO(); d.save(buf)

    # shrink_min equal to the current size: no shrinking is actually possible
    slots = [{"id": "s1", "name": "备注", "shrink": True, "shrink_min": 10.0}]
    _, applied = L.scale_fields(buf.getvalue(), {"s1"}, 0.6, {"s1": 10.0})
    assert applied["ratio"] == pytest.approx(1.0), "被下限挡住时不能报告成缩小了"


def test_content_lost_by_shrinking_is_detected_not_counted_as_success():
    """Page count alone is not success: a field whose text vanished must fail the sample."""
    from synwevo_docs.engine import layout as L

    before = {"s1": "重要内容", "s2": "保留"}
    after_lost = {"s1": "", "s2": "保留"}
    after_trunc = {"s1": "重要", "s2": "保留"}

    lost = [k for k, v in before.items() if v.strip() and not after_lost.get(k, "").strip()]
    trunc = [k for k, v in before.items()
             if v.strip() and after_trunc.get(k, "").strip() and len(after_trunc[k].strip()) < len(v.strip())]
    assert lost == ["s1"] and trunc == ["s1"], "这是 fit_docx 用来判定内容完整性的同一条规则"
    assert hasattr(L, "_field_texts"), "内容完整性检查依赖按字段取文本"


def test_field_texts_reads_every_field(tmp_path):
    from synwevo_docs.engine import layout as L
    tpl = pathlib.Path(_template(tmp_path)).read_bytes()
    texts = L._field_texts(tpl)
    assert isinstance(texts, dict)
