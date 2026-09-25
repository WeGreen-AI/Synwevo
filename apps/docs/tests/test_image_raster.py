"""Image templates must work with no PDF engine at all.

They are raster work — clear a region, draw text, re-encode — but they used to be filled by
wrapping the picture in a one-page PDF and running the PDF machinery over it, so a single
global "is PyMuPDF importable" switch disabled them for a reason that had nothing to do with
images.  These tests pin the raster path: authoring, filling, pixel fidelity and metadata.
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from synwevo_docs.engine import fonts
from synwevo_docs.engine import pdf_engine as pe

pytestmark = pytest.mark.skipif(not fonts.cjk_font_file(), reason="本机没有可用的中文字体文件")


@pytest.fixture()
def pdfium():
    prev = pe.use_engine("pdfium")
    yield
    pe.use_engine(prev)


def test_capability_is_per_format_not_one_global_switch(pdfium):
    assert pe.pdf_templates_available() is False
    assert pe.image_templates_available() is True, "图片模板不应被 PDF 引擎的缺失连累"


def test_full_image_template_flow_without_a_pdf_engine(api, fixtures, pdfium, tmp_path):
    src = fixtures / "测试_发货单图片模板.png"
    original = Image.open(src).convert("RGB")
    W, H = original.size

    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "来源"
    ws.append(["客户"]); ws.append(["测试客户甲"]); ws.append(["测试客户乙"])
    buf = io.BytesIO(); wb.save(buf)

    pid = api.project("无 PDF 引擎的图片模板")
    api.upload_bytes(pid, "来源.xlsx", buf.getvalue())
    t = api.ok(api.c.post("/api/templates/upload", files={"file": ("t.png", src.read_bytes())}, data={"project_id": pid}))
    assert t["format"] == "png"

    box = [230, 215, 520, 250]
    slot = api.slot(t["id"], kind="pdf_box", page=0, rect=box, name="客户名称")
    assert slot["locator"]["rect"][:2] == [230.0, 215.0]

    api.rule(pid, {"mode": "per_row", "table": {"sheet": "来源", "header_row": 0, "data_start": 1}})
    api.maps(pid, [{"type": "single", "sources": [{"kind": "column", "sheet": "来源", "header": "客户"}],
                    "target": {"binding": slot["binding"]}}])
    job = api.job(pid, formats=["png"])
    assert job["status"] == "completed", api.items(job["id"])
    assert job["counts"]["success"] == 2

    import zipfile
    z = zipfile.ZipFile(io.BytesIO(api.c.get(f"/api/jobs/{job['id']}/zip").content))
    names = sorted(n for n in z.namelist() if n.lower().endswith(".png"))
    assert len(names) == 2
    out = Image.open(io.BytesIO(z.read(names[0])))

    assert out.size == (W, H), "输出尺寸必须与模板一致"
    assert out.format == "PNG"
    assert round(out.info["dpi"][0]) == 150, f"DPI 未保留：{out.info.get('dpi')}"

    # PNG is lossless: every pixel outside the field must be bit-identical
    a, b = original, out.convert("RGB")
    outside = inside = 0
    for x in range(0, W, 2):
        for y in range(0, H, 2):
            if a.getpixel((x, y)) != b.getpixel((x, y)):
                if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
                    inside += 1
                else:
                    outside += 1
    assert outside == 0, f"字段外有 {outside} 个像素被改动"
    assert inside > 0, "字段内没有任何变化，说明没真的写入"


def test_repeat_areas_are_refused_with_a_specific_reason_not_a_generic_error(api, fixtures, pdfium):
    """An unsupported case must name itself, not fall back to a blanket engine_unavailable."""
    from synwevo_docs.engine.image_raster import fill
    from synwevo_docs.engine.values import Value, STR
    meta = {"native": "png", "width": 40, "height": 20, "mode": "RGB", "dpi": [72, 72], "alpha": False}
    im = Image.new("RGB", (40, 20), "white"); buf = io.BytesIO(); im.save(buf, "PNG")
    slot = {"id": "s1", "name": "明细", "layout": {"cols": [], "trect": [0, 0, 10, 10], "row_height": 5}}
    out, issues = fill(buf.getvalue(), [slot], {"s1": {"kind": "rows", "rows": [{"c0": Value(STR, "x", "x")}]}}, meta)
    assert out == b""
    codes = [i["code"] for i in issues]
    assert "raster_repeat_unsupported" in codes, issues
    assert "完整模式" in issues[0]["message"], "必须说明如何取得该能力"


def test_overflowing_text_fails_instead_of_being_silently_clipped(api, fixtures, pdfium):
    from synwevo_docs.engine.image_raster import fill
    from synwevo_docs.engine.values import Value, STR
    meta = {"native": "png", "width": 120, "height": 40, "mode": "RGB", "dpi": [72, 72], "alpha": False}
    im = Image.new("RGB", (120, 40), "white"); buf = io.BytesIO(); im.save(buf, "PNG")
    slot = {"id": "s1", "name": "备注", "locator": {"rect": [5, 5, 40, 15]}, "font_size": 12, "min_font_size": 10}
    long_text = "这段中文内容远远超出这个很小的框所能容纳的范围无论如何都放不下去"
    out, issues = fill(buf.getvalue(), [slot], {"s1": {"kind": "scalar", "value": Value(STR, long_text, long_text)}}, meta)
    assert out == b"" and [i["code"] for i in issues] == ["overflow"]
