"""Fill an image template entirely in pixels — no PDF engine involved.

Image templates were filled by wrapping the picture in a one-page PDF (1 px = 1 pt),
running the PDF machinery over it and rasterising the result.  That made a purely raster
feature depend on PyMuPDF, so image templates were refused outright in the AGPL-free
deployment even though nothing about them needs a PDF.

This path works on the original pixels:

* clear a field's area by reconstructing the background from its own edges
  (:func:`image_template.background_patch_px`), falling back to a flat paper colour;
* draw the new text with :mod:`raster_text`, whose layout matches PyMuPDF's
  ``insert_textbox`` geometry, so existing templates keep wrapping and shrinking the same;
* re-encode with the template's original DPI and ICC profile.

Because the wrapper page is exactly the pixel grid, a slot's rect is already in pixel
coordinates and needs no transform.
"""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps

from . import raster_text
from .image_template import ImageTemplateError, background_color_px, background_patch_px, ink_metrics_px, open_image


def _normalised(original: bytes, meta: dict) -> Image.Image:
    """Reproduce make_wrapper's normalisation: first frame, EXIF orientation applied, the
    mode the template was registered with.  The stored pixels are already upright, so the
    orientation tag must not be applied a second time on output."""
    im = open_image(original)
    try:
        if int(im.getexif().get(0x0112, 1) or 1) not in (0, 1):
            im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001
        pass
    want = meta.get("mode") or ("RGBA" if meta.get("alpha") else "RGB")
    if im.mode != want:
        im = im.convert(want)
    size = (int(meta["width"]), int(meta["height"]))
    if im.size != size:
        raise ImageTemplateError("size_changed",
                                 f"模板图片尺寸与登记值不一致（现在 {im.size[0]}×{im.size[1]}，登记为 {size[0]}×{size[1]}）")
    return im


def _clear(im: Image.Image, box: tuple[int, int, int, int]) -> tuple[float, float, float]:
    """Erase a field's old content and return the paper colour underneath."""
    crop = im.crop(box)
    bg = background_color_px(crop)
    patch = background_patch_px(crop)
    if patch is not None:
        im.paste(patch.convert(im.mode), (box[0], box[1]))
    else:
        flat = tuple(int(round(c * 255)) for c in bg)
        im.paste(Image.new(im.mode, (box[2] - box[0], box[3] - box[1]), flat[:3] if im.mode.startswith("RGB") else flat[0]),
                 (box[0], box[1]))
    return bg


def _box(slot: dict) -> tuple[int, int, int, int]:
    r = (slot.get("locator") or {}).get("rect")
    if not r:
        raise ImageTemplateError("no_rect", f"字段「{slot.get('name')}」没有区域坐标")
    x0, y0, x1, y1 = (float(v) for v in r)
    return (int(round(min(x0, x1))), int(round(min(y0, y1))), int(round(max(x0, x1))), int(round(max(y0, y1))))


def _texts(slot: dict, filled: dict) -> list[str]:
    fv = filled.get(slot["id"])
    if not fv:
        return []
    if fv.get("kind") == "scalar":
        return [fv["value"].text]
    if fv.get("kind") == "rows":
        return [v.text for row in fv["rows"] for v in row.values()]
    return []


def fill(original: bytes, slots: list[dict], values: dict[str, dict], meta: dict) -> tuple[bytes, list[dict]]:
    """Original image bytes -> filled image bytes, plus issues.  No PDF engine is used."""
    if not meta:
        raise ImageTemplateError("image_meta", "图片模板缺少尺寸信息，请重新上传模板")
    im = _normalised(original, meta)
    font = raster_text.RasterFont()
    issues: list[dict] = []

    for slot in slots:
        texts = _texts(slot, values)
        if not any(t for t in texts):
            continue
        if slot.get("layout"):
            issues.append({"level": "error", "slot": slot["id"], "code": "raster_repeat_unsupported",
                           "message": f"字段「{slot.get('name')}」是重复明细区域：无 PyMuPDF 的部署暂不支持图片模板的重复明细，"
                                      f"请改用单值字段，或按 docs/DEPENDENCIES.md 启用 PyMuPDF"})
            continue
        box = _box(slot)
        if box[2] - box[0] < 1 or box[3] - box[1] < 1:
            issues.append({"level": "error", "slot": slot["id"], "code": "empty_rect",
                           "message": f"字段「{slot.get('name')}」的区域太小"})
            continue
        crop = im.crop(box)
        bg = background_color_px(crop)
        ink, ink_h = ink_metrics_px(crop, bg)
        _clear(im, box)

        colour = slot.get("color")
        rgb = _hex_rgb(colour) if colour else (ink or (0.0, 0.0, 0.0))
        size = float(slot.get("font_size") or (ink_h * 0.92 if ink_h else 0) or 12.0)
        res = raster_text.draw_box(
            im, box, "\n".join(t for t in texts if t), font=font,
            font_size=size, min_font_size=float(slot.get("min_font_size") or 5),
            align=slot.get("align") or "left", valign=slot.get("valign") or "top",
            color=tuple(v * 255 for v in rgb), rotate=int(slot.get("rotate") or 0))
        if not res["ok"]:
            issues.append({"level": "error", "slot": slot["id"], "code": "overflow",
                           "message": f"字段「{slot.get('name')}」的内容在区域内放不下（已缩到最小字号 {res['font_size']}）"})
    if any(i["level"] == "error" for i in issues):
        return b"", issues
    return encode(im, meta), issues


def _hex_rgb(v: str) -> tuple[float, float, float]:
    v = (v or "").lstrip("#")
    if len(v) != 6:
        return (0.0, 0.0, 0.0)
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))


def encode(im: Image.Image, meta: dict) -> bytes:
    """Same encoder contract as image_template.rasterise: native format, DPI, ICC."""
    if meta.get("mode") in ("L", "LA"):
        im = im.convert("LA" if meta.get("alpha") else "L")
    icc = base64.b64decode(meta["icc"]) if meta.get("icc") else None
    dpi = tuple(meta.get("dpi") or (72, 72))
    buf = io.BytesIO()
    if meta["native"] == "jpeg":
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        kw = {"quality": 95, "subsampling": 0, "dpi": dpi, "optimize": True}
        if icc:
            kw["icc_profile"] = icc
        im.save(buf, "JPEG", **kw)
    else:
        kw = {"dpi": dpi, "optimize": True}
        if icc:
            kw["icc_profile"] = icc
        im.save(buf, "PNG", **kw)
    return buf.getvalue()
