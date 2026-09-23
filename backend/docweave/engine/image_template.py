"""Image templates (PNG / JPEG, plus BMP / TIFF / WEBP converted to PNG).

The image is wrapped into a one-page PDF whose page size equals the pixel size
(1 px = 1 pt), so the whole PDF template machinery (boxes, repeat areas,
redaction, shrink-to-fit text, rotation handling) applies unchanged.  Filling
produces that PDF, which is then rasterised at exactly the original pixel size
and encoded in the original format with the original DPI and ICC profile.

The original upload is kept byte-for-byte in the blob store; the wrapper PDF is
only the working representation.
"""
from __future__ import annotations

import base64
import io

import pymupdf
from PIL import Image, ImageOps

from . import images as _images  # noqa: F401  sets Image.MAX_IMAGE_PIXELS from LIMITS
from .limits import LIMITS, LimitError

NATIVE = {"png": "png", "jpeg": "jpg"}
# The ceiling is configurable (DOCWEAVE_MAX_IMAGE_MPIX); it used to be hardcoded at 60 MP
# here, so raising or lowering the documented limit had no effect on image templates.


class ImageTemplateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def open_image(data: bytes) -> Image.Image:
    if len(data) > LIMITS.max_file_bytes:
        raise LimitError("图片超过大小上限")
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > LIMITS.max_image_pixels:
            raise ImageTemplateError("too_large", f"图片像素 {im.width}×{im.height} 超过上限（{LIMITS.max_image_pixels // 1_000_000} MP）")
        im.load()
    except ImageTemplateError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImageTemplateError("corrupt", f"无法打开图片：{e}") from e
    return im


def make_wrapper(data: bytes) -> tuple[bytes, dict, list[str]]:
    """Image bytes → (wrapper PDF, meta, notes).  ``meta`` is stored on the template."""
    im = open_image(data)
    src_fmt = (im.format or "").upper()
    notes: list[str] = []
    n_frames = getattr(im, "n_frames", 1)
    if n_frames > 1:
        notes.append(f"图片有 {n_frames} 帧，只使用第一帧")
    try:
        orientation = int(im.getexif().get(0x0112, 1) or 1)
    except Exception:  # noqa: BLE001
        orientation = 1
    rotated = orientation not in (0, 1)
    oriented = ImageOps.exif_transpose(im) if rotated else im
    if rotated:
        notes.append("已按 EXIF 方向信息把图片转正；输出图片不再带方向标记")
    im2 = oriented
    native = "jpeg" if src_fmt == "JPEG" else "png"
    if src_fmt not in ("PNG", "JPEG"):
        notes.append(f"{src_fmt or '该'} 图片模板输出为 PNG（无损）")
    alpha = im2.mode in ("RGBA", "LA", "PA") or (im2.mode == "P" and "transparency" in im2.info)
    if native == "jpeg" and im2.mode not in ("RGB", "L"):
        im2 = im2.convert("RGB")
    elif native == "png" and im2.mode not in ("RGB", "RGBA", "L", "LA"):
        im2 = im2.convert("RGBA" if alpha else "RGB")
    dpi = im.info.get("dpi") or (72, 72)
    try:
        dpi = (float(dpi[0]) or 72.0, float(dpi[1]) or 72.0)
    except Exception:  # noqa: BLE001
        dpi = (72.0, 72.0)
    icc = im.info.get("icc_profile")
    w, h = im2.size
    # embed losslessly: original JPEG stream when untouched, else PNG
    if native == "jpeg" and not rotated and im.mode in ("RGB", "L"):
        stream = data
    else:
        buf = io.BytesIO()
        im2.save(buf, "PNG")
        stream = buf.getvalue()
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.insert_image(page.rect, stream=stream)
    pdf = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    meta = {"native": native, "width": w, "height": h, "dpi": [round(dpi[0], 3), round(dpi[1], 3)], "alpha": bool(alpha),
            "mode": im2.mode, "source_format": src_fmt.lower(), "icc": base64.b64encode(icc).decode() if icc else None}
    notes.append(f"图片模板：{w}×{h} 像素，输出为同尺寸 {NATIVE[native].upper()}；框选区域会按周围背景色清除原像素后写入新内容")
    if native == "jpeg":
        notes.append("JPEG 输出会重新压缩（质量 95，4:4:4 色度采样），与原图有极小的压缩差异；需要无损请上传 PNG")
    return pdf, meta, notes


def rasterise(pdf: bytes, meta: dict) -> bytes:
    """Filled wrapper PDF → image bytes in the template's native format."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        page = doc[0]
        alpha = bool(meta.get("alpha"))
        pix = page.get_pixmap(matrix=pymupdf.Identity, alpha=alpha, colorspace=pymupdf.csRGB)
        mode = "RGBA" if pix.alpha else "RGB"
        im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    want = (int(meta["width"]), int(meta["height"]))
    if im.size != want:           # rounding guard; the page is exactly w×h points
        im = im.resize(want, Image.LANCZOS)
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


def to_pdf(image_bytes: bytes) -> bytes:
    """An output image as a one-page PDF (for PDF export and page rendering)."""
    src = pymupdf.open(stream=image_bytes)
    try:
        pdf = src.convert_to_pdf()
    finally:
        src.close()
    return pdf


def check(data: bytes, meta: dict) -> dict:
    """Reopen an output image; its size must equal the template's."""
    im = Image.open(io.BytesIO(data))
    im.verify()
    im = Image.open(io.BytesIO(data))
    ok = im.size == (int(meta["width"]), int(meta["height"]))
    return {"ok": ok, "size": list(im.size), "format": im.format}


# ------------------------------------------------------------------ pixel helpers (scanned PDFs and images)
def region_pixels(page: pymupdf.Page, rect, zoom: float = 1.0) -> Image.Image:
    """Render ``rect`` (unrotated coords) of the page's *unrotated* content."""
    r = pymupdf.Rect(rect) & page.mediabox
    if r.is_empty:
        return Image.new("RGB", (1, 1), "white")
    m = pymupdf.Matrix(zoom, zoom).prerotate(0)
    rot = page.rotation
    if rot:
        page.set_rotation(0)
    try:
        pix = page.get_pixmap(matrix=m, clip=r, alpha=False, colorspace=pymupdf.csRGB)
    finally:
        if rot:
            page.set_rotation(rot)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def background_color(page: pymupdf.Page, rect) -> tuple[float, float, float]:
    """The paper colour of ``rect``: the bright majority of its pixels (text, table rules and
    stamps are the darker minority).  Used as the flat fallback fill when clearing pixels."""
    im = region_pixels(page, pymupdf.Rect(rect))
    w, h = im.size
    if w * h == 0:
        return (1.0, 1.0, 1.0)
    if w * h > 40_000:
        f = (40_000 / (w * h)) ** 0.5
        im = im.resize((max(1, int(w * f)), max(1, int(h * f))), Image.NEAREST)
    px = list(im.getdata())

    def lum(c):
        return c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114

    px.sort(key=lum)
    ref = lum(px[int((len(px) - 1) * 0.85)])
    bright = [c for c in px if lum(c) >= ref - 25] or px
    med = bright[len(bright) // 2]
    return (round(med[0] / 255, 4), round(med[1] / 255, 4), round(med[2] / 255, 4))


def text_color(page: pymupdf.Page, rect, bg: tuple[float, float, float]) -> tuple[float, float, float] | None:
    """Colour of the original ink inside ``rect`` (pixels clearly darker than the background)."""
    return ink_metrics(page, rect, bg)[0]


def ink_metrics(page: pymupdf.Page, rect, bg: tuple[float, float, float]) -> tuple[tuple[float, float, float] | None, float | None]:
    """(ink colour, height of the tallest ink line in points) inside ``rect`` – the colour and
    size new text should use to look like the text it replaces."""
    r = pymupdf.Rect(rect)
    zoom = 2.0 if r.width * r.height * 4 <= 1_000_000 else 1.0
    im = region_pixels(page, r, zoom=zoom).convert("RGB")
    w, h = im.size
    if w * h == 0:
        return None, None
    bl = (bg[0] * 0.299 + bg[1] * 0.587 + bg[2] * 0.114) * 255
    data = list(im.getdata())
    is_ink = [bl - (c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114) > 70 for c in data]
    ink = [c for c, k in zip(data, is_ink) if k]
    if len(ink) < 12:
        return None, None
    # rows containing ink, ignoring rows that are almost fully dark (rules crossing the area)
    rows = []
    for y in range(h):
        n = sum(is_ink[y * w:(y + 1) * w])
        rows.append(0 < n < 0.6 * w)
    best = cur = 0
    for v in rows:
        cur = cur + 1 if v else 0
        best = max(best, cur)
    height = best / zoom if best else None
    ink.sort(key=lambda c: c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114)
    core = ink[: max(1, len(ink) // 4)]            # darkest quarter: anti-aliased edges excluded
    n = len(core)
    color = (round(sum(c[0] for c in core) / n / 255, 4), round(sum(c[1] for c in core) / n / 255, 4), round(sum(c[2] for c in core) / n / 255, 4))
    return color, height


def raster_rules(page: pymupdf.Page, rect, min_frac: float = 0.55) -> tuple[list[float], list[float]]:
    """Horizontal (y) and vertical (x) ruling lines drawn *in pixels* inside ``rect``
    (unrotated coords), found by row / column darkness projections."""
    r = pymupdf.Rect(rect)
    im = region_pixels(page, r, zoom=2.0).convert("L")
    w, h = im.size
    if w < 8 or h < 8:
        return [], []
    bw = im.point(lambda v: 255 if v < 140 else 0)
    rows = list(bw.resize((1, h), Image.BOX).getdata())
    cols = list(bw.resize((w, 1), Image.BOX).getdata())

    def runs(vals, n_px, origin, scale):
        out, start = [], None
        for i, v in enumerate(vals + [0]):
            if v / 255 >= min_frac and start is None:
                start = i
            elif v / 255 < min_frac and start is not None:
                if i - start <= max(8, n_px // 40):         # thin: a rule, not a filled block
                    out.append(origin + (start + i - 1) / 2 / scale)
                start = None
        return out

    hs = runs(rows, h, r.y0, h / r.height)
    vs = runs(cols, w, r.x0, w / r.width)
    return hs, vs


def background_patch(page: pymupdf.Page, rect, max_grid: int = 48) -> bytes | None:
    """A PNG that reconstructs the background under ``rect`` from the pixels around it
    (Coons-patch interpolation of the four edge colours, dark pixels – lines, text –
    replaced by their neighbours).  Follows gradients and uneven scan lighting that a
    flat fill would not.  Returns None when the area is too small to matter."""
    r = pymupdf.Rect(rect)
    if r.width < 2 or r.height < 2:
        return None
    # the outermost pixels *of the area itself*: outside it there may be table rules or labels,
    # inside the edge band there is normally only background (the text sits further in)
    ring = r
    im = region_pixels(page, ring, zoom=1.0)
    w, h = im.size
    if w < 5 or h < 5:
        return None
    px = im.load()

    def lum(c):
        return c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114

    def clean(edge: list[tuple]) -> list[tuple]:
        if not edge:
            return edge
        ls = sorted(lum(c) for c in edge)
        ref = ls[int((len(ls) - 1) * 0.9)]            # background = the bright majority
        good = [c for c in edge if lum(c) >= ref - 40]
        fallback = good[len(good) // 2] if good else edge[len(edge) // 2]
        out, last = [], None
        for c in edge:
            if lum(c) >= ref - 40:
                last = c
                out.append(c)
            else:
                out.append(last or fallback)
        return out

    top = clean([px[x, 0] for x in range(w)])
    bot = clean([px[x, h - 1] for x in range(w)])
    left = clean([px[0, y] for y in range(h)])
    right = clean([px[w - 1, y] for y in range(h)])
    gw, gh = max(2, min(max_grid, w)), max(2, min(max_grid, h))

    def sample(edge, t):
        i = min(len(edge) - 1, max(0, int(round(t * (len(edge) - 1)))))
        return edge[i]

    grid = Image.new("RGB", (gw, gh))
    g = grid.load()
    tl, tr_, bl, br = top[0], top[-1], bot[0], bot[-1]
    for j in range(gh):
        v = j / (gh - 1)
        lv, rv = sample(left, v), sample(right, v)
        for i in range(gw):
            u = i / (gw - 1)
            tu, bu = sample(top, u), sample(bot, u)
            c = []
            for k in range(3):
                val = ((1 - v) * tu[k] + v * bu[k] + (1 - u) * lv[k] + u * rv[k]
                       - ((1 - u) * (1 - v) * tl[k] + u * (1 - v) * tr_[k] + (1 - u) * v * bl[k] + u * v * br[k]))
                c.append(max(0, min(255, int(round(val)))))
            g[i, j] = tuple(c)
    inner = grid.resize((w, h), Image.BICUBIC)
    buf = io.BytesIO()
    inner.save(buf, "PNG")
    return buf.getvalue()
