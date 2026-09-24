"""Image templates: PNG / JPEG in, same-size images out; pixel clearing that follows the
background; pixel tables (rules and header names from the image itself)."""
import io

import openpyxl
import pymupdf
import pytest
from PIL import Image

from synwevo_docs.engine.ocr import ocr_png, tesseract_path

W, H = 1190, 900
NO_BOX = [226 / W, 214 / H, 520 / W, 254 / H]          # "DD-示例" (pixels = points in the wrapper)
AREA = [146 / W, 346 / H, 1044 / W, 606 / H]           # the 6 body rows of the pixel table
INK = (31, 58, 147)


def _orders(fixtures):
    ws = openpyxl.load_workbook(fixtures / "测试_订单明细100.xlsx")["明细"]
    out: dict[str, list] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        out.setdefault(r[0], []).append(r)
    for v in out.values():
        v.sort(key=lambda r: r[2])
    return out


def _setup(api, fixtures, name, overflow="shrink"):
    pid = api.project(f"图片模板-{name}")
    api.upload(pid, fixtures / "测试_订单明细100.xlsx")
    t = api.template(pid, fixtures / name)
    tid = t["id"]
    det = api.ok(api.c.post(f"/api/templates/{tid}/pdf-repeat-detect", json={"page": 0, "nrect": AREA}))
    no = api.slot(tid, kind="pdf_box", page=0, nrect=NO_BOX, name="单号")
    rep = api.slot(tid, kind="pdf_repeat", page=0, nrect=AREA, name="明细", overflow=overflow)
    api.rule(pid, {"mode": "per_group", "table": {"sheet": "明细", "header_row": 0},
                   "group": {"keys": [{"header": "订单号"}], "sort": [{"header": "行号", "dir": "asc", "numeric": True}]}})
    api.maps(pid, [
        {"type": "single", "sources": [{"kind": "column", "sheet": "明细", "header": "订单号"}], "target": {"binding": no["binding"]}},
        {"type": "repeat", "sources": [{"kind": "table", "sheet": "明细", "header_row": 0}], "target": {"binding": rep["binding"]},
         "columns": [{"slot_col": "c0", "source": {"header": "行号"}}, {"slot_col": "c1", "source": {"header": "品名"}},
                     {"slot_col": "c2", "source": {"header": "数量"}}, {"slot_col": "c3", "source": {"header": "单价"}}]},
    ])
    return pid, t, det, no, rep


def _dark(im: Image.Image, box, thr=110) -> int:
    return sum(1 for c in im.crop(box).convert("L").getdata() if c < thr)


def test_png_template_end_to_end(api, fixtures):
    pid, t, det, no, rep = _setup(api, fixtures, "测试_发货单图片模板.png")
    assert t["format"] == "png" and t["info"]["image"]["width"] == W and t["info"]["image"]["dpi"][0] == pytest.approx(150, abs=0.1)
    assert [e["format"] for e in t["exports"] if e["available"]] == ["png", "pdf"]
    # the table is drawn in pixels: rules come from the image, names from local OCR when available
    assert det["source"]["raster"] and det["rows_fit"] == 6 and len(det["cols"]) == 5
    if tesseract_path():
        assert [c["name"] for c in det["cols"]] == ["序号", "品名", "数量", "单价", "金额"]
    # ink colour and background sampled from the image
    r, g, b = (int(no["color"][i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - INK[0]) < 40 and abs(g - INK[1]) < 40 and abs(b - INK[2]) < 40
    assert no["clear"] == "text_pixels" and min(no["fill"]) > 0.85
    job = api.job(pid, naming="{订单号}")
    exp = _orders(fixtures)
    assert job["status"] == "completed" and job["counts"]["success"] == len(exp)
    outs = api.outputs(job["id"])
    key = next(k for k, v in exp.items() if len(v) <= 6)
    im = Image.open(io.BytesIO(outs[f"{key}.png"]))
    assert im.size == (W, H) and im.format == "PNG" and round(im.info["dpi"][0]) == 150
    tpl = Image.open(fixtures / "测试_发货单图片模板.png").convert("RGB")
    # outside the edited areas the image is unchanged
    assert list(im.convert("RGB").crop((140, 110, 700, 180)).getdata()) == list(tpl.crop((140, 110, 700, 180)).getdata())
    # grid lines inside the cleared table survive (cell interiors cleared, rules kept)
    col_line = (584, 400, 586, 600)
    assert _dark(im, col_line) >= _dark(tpl, col_line) * 0.9
    # the cleared background follows the gradient instead of a flat white box
    top_px = im.convert("RGB").getpixel((600, 225))
    assert abs(top_px[2] - tpl.getpixel((600, 212))[2]) <= 6
    # detail rows, checked without OCR: every row of the product-name column that has a
    # record now carries ink (the template has a sample in row 1 only), and row 1 is not the
    # template's sample any more
    x0, y0, _x1, _y1 = det["trect"]
    name_col = det["cols"][1]
    boxes = [(int(name_col["x0"]) + 3, int(y0 + i * det["row_height"]) + 3, int(name_col["x1"]) - 3,
              int(y0 + (i + 1) * det["row_height"]) - 3) for i in range(det["rows_fit"])]
    n = len(exp[key])
    assert all(_dark(im, b) > 100 for b in boxes[:n]), [_dark(im, b) for b in boxes]
    assert all(_dark(im, b) == 0 for b in boxes[n:]), [_dark(im, b) for b in boxes]
    assert list(im.convert("RGB").crop(boxes[0]).getdata()) != list(tpl.crop(boxes[0]).getdata())
    if tesseract_path():
        buf = io.BytesIO()
        im.crop((140, 200, 1050, 620)).save(buf, "PNG")
        txt = "".join(w["t"] for w in ocr_png(buf.getvalue())["words"])
        assert key in txt.replace(" ", "") or key.replace("-", "") in txt.replace("-", "")
        assert exp[key][0][3].replace("测试", "") in txt                        # first detail row's product name
    items = api.items(job["id"])
    ver = items[0]["artifacts"][0]["verification"]
    assert ver["reopened"] and ver["engine"] == "Pillow"


def test_jpeg_template_and_pdf_export(api, fixtures):
    pid, t, det, no, rep = _setup(api, fixtures, "测试_发货单图片模板.jpg")
    assert t["format"] == "jpeg" and [e["format"] for e in t["exports"] if e["available"]] == ["jpg", "pdf"]
    job = api.job(pid, naming="{订单号}", formats=["jpg", "pdf"])
    assert job["status"] == "completed", api.items(job["id"])[0]
    outs = api.outputs(job["id"])
    key = sorted(_orders(fixtures))[0]
    im = Image.open(io.BytesIO(outs[f"{key}.jpg"]))
    assert im.format == "JPEG" and im.size == (W, H)
    doc = pymupdf.open(stream=outs[f"{key}.pdf"], filetype="pdf")
    assert doc.page_count == 1 and len(doc[0].get_images()) == 1


def test_image_rules(api, fixtures):
    pid = api.project("图片模板规则")
    t = api.template(pid, fixtures / "测试_发货单图片模板.png")
    r = api.c.post(f"/api/templates/{t['id']}/slots", json={"kind": "pdf_repeat", "page": 0, "nrect": AREA, "name": "明细", "overflow": "continue"})
    assert r.status_code == 400 and "图片模板只有一页" in r.text
    rep = api.slot(t["id"], kind="pdf_repeat", page=0, nrect=AREA, name="明细")
    r = api.c.patch(f"/api/templates/{t['id']}/slots/{rep['id']}", json={"overflow": "continue"})
    assert r.status_code == 400
    r = api.c.patch(f"/api/templates/{t['id']}/slots/{rep['id']}", json={"color": "red"})
    assert r.status_code == 400
    ok = api.ok(api.c.patch(f"/api/templates/{t['id']}/slots/{rep['id']}", json={"color": "#AA0000"}))
    assert next(s for s in ok["version"]["slots"] if s["id"] == rep["id"])["color"] == "#aa0000"
    # the original upload is kept unchanged
    tpl = api.ok(api.c.get(f"/api/templates/{t['id']}"))
    assert "icc" in tpl["info"]["image"] and "original_blob" not in tpl["info"]["image"]
    page = api.c.get(f"/api/templates/{t['id']}/page/0.png?zoom=1&which=original")
    assert page.status_code == 200 and Image.open(io.BytesIO(page.content)).size == (W, H)


def test_exif_orientation_and_alpha(api, tmp_path):
    from synwevo_docs.engine.image_template import make_wrapper, rasterise
    im = Image.new("RGBA", (300, 200), (255, 0, 0, 0))
    for x in range(300):
        for y in range(0, 20):
            im.putpixel((x, y), (0, 0, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    pdf, meta, notes = make_wrapper(buf.getvalue())
    assert meta["alpha"] and meta["width"] == 300
    out = Image.open(io.BytesIO(rasterise(pdf, meta)))
    assert out.mode == "RGBA" and out.getpixel((10, 100))[3] == 0 and out.getpixel((10, 5))[:3] == (0, 0, 255)
    # EXIF orientation 6 (rotate 90° clockwise to view): the wrapper is upright
    jp = Image.new("RGB", (400, 100), "white")
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    jp.save(buf, "JPEG", exif=exif.tobytes())
    pdf, meta, notes = make_wrapper(buf.getvalue())
    assert (meta["width"], meta["height"]) == (100, 400) and any("EXIF" in n for n in notes)
