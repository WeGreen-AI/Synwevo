"""PDF handling.

Two engines, chosen with ``SYNWEVO_DOCS_PDF_ENGINE``:

* ``pymupdf`` (default when installed) – PyMuPDF, AGPL-3.0 or commercial licence from
  Artifex.  Everything: reading, PDF / image templates, redaction, text insertion.
* ``pdfium`` – pypdfium2 (Apache-2.0 / BSD-3) through ``pdfium_shim``.  Reading only
  (sources, previews, OCR input, page counts).  PDF / image templates are unavailable
  and reported as such; nothing silently degrades.

Coordinate conventions
----------------------
* Everything stored in locators is in *unrotated page space* (PDF points,
  origin top-left of the unrotated MediaBox/CropBox as PyMuPDF reports it).
* The browser works with *visual normalised* rectangles: fractions (0..1) of the
  rendered, rotated page image.  Conversion uses the page's rotation matrix, so
  zoom level and rotation never leak into stored rules.
"""
from __future__ import annotations

import io
import os

from synwevo_core import env
import re
import unicodedata

from .limits import LIMITS, LimitError


def _select_engine() -> str:
    want = (env.docs("PDF_ENGINE") or "").strip().lower()
    if want in ("pdfium", "pymupdf"):
        return want
    try:
        import pymupdf  # noqa: F401
        return "pymupdf"
    except ImportError:
        return "pdfium"


ENGINE = _select_engine()
if ENGINE == "pymupdf":
    import pymupdf
else:
    from . import pdfium_shim as pymupdf  # the read-only subset DocWeave needs, on pypdfium2


def use_engine(name: str) -> str:
    """Switch the PDF engine at runtime (tests / diagnostics).  Returns the previous one."""
    global ENGINE, pymupdf
    prev = ENGINE
    if name == "pymupdf":
        import pymupdf as _m
    elif name == "pdfium":
        from . import pdfium_shim as _m
    else:
        raise ValueError(name)
    ENGINE, pymupdf = name, _m
    global _CJK_FONT
    _CJK_FONT = None
    return prev


def pdf_templates_available() -> bool:
    """PDF templates need an engine that can rewrite page content (PyMuPDF today)."""
    return ENGINE == "pymupdf"


def image_templates_available() -> bool:
    """Image templates are pure raster work: they need a CJK-capable font file, not a PDF
    engine.  Capability is per format on purpose — one global "is PyMuPDF importable"
    switch used to disable image templates for a reason that had nothing to do with them."""
    if ENGINE == "pymupdf":
        return True
    from . import fonts
    return bool(fonts.cjk_font_file())


def templates_available() -> bool:      # kept: older callers mean "PDF templates"
    return pdf_templates_available()


TEMPLATES_UNAVAILABLE = ("当前部署使用 pdfium 引擎（未安装或未启用 AGPL 许可的 PyMuPDF），PDF 模板不可用；"
                         "PDF 来源文件的读取、预览、OCR 与取值不受影响。如需 PDF 模板，请按 docs/DEPENDENCIES.md 启用 PyMuPDF")
IMAGE_TEMPLATES_UNAVAILABLE = ("图片模板需要一个可绘制中文的字体文件，但本机没有找到。请安装中文字体"
                               "（Debian/Ubuntu：apt install fonts-noto-cjk），或用 SYNWEVO_DOCS_CJK_FONT_FILE 指定字体文件路径")

_CJK_FONT: pymupdf.Font | None = None
FONT_NAME = "dwcjk"


def cjk_font() -> pymupdf.Font:
    global _CJK_FONT
    if _CJK_FONT is None:
        _CJK_FONT = pymupdf.Font("cjk")  # Droid Sans Fallback shipped with PyMuPDF (Apache-2.0)
    return _CJK_FONT


class PdfError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def open_pdf(data: bytes) -> pymupdf.Document:
    if len(data) > LIMITS.max_file_bytes:
        raise LimitError("PDF 超过大小上限")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:
        raise PdfError("corrupt", f"无法打开 PDF：{e}") from e
    if doc.needs_pass:
        raise PdfError("encrypted", "PDF 已加密，需要密码才能打开；请先在原软件中移除密码后重新上传")
    if doc.page_count > LIMITS.max_pages:
        raise LimitError(f"PDF 页数 {doc.page_count} 超过上限 {LIMITS.max_pages}")
    return doc


def page_kind(page: pymupdf.Page) -> str:
    text = page.get_text("text").strip()
    images = page.get_images(full=False)
    area = abs(page.rect)
    img_cover = 0.0
    try:
        for info in page.get_image_info():
            img_cover += abs(pymupdf.Rect(info["bbox"]) & page.rect)
    except Exception:
        pass
    if len(text) >= 3:
        if img_cover > 0.8 * area and len(text) < 40:
            return "mixed"
        return "text"
    if images:
        return "scanned"
    return "empty"


def inspect(data: bytes) -> dict:
    doc = open_pdf(data)
    pages = []
    kinds = set()
    for p in doc:
        k = page_kind(p)
        kinds.add(k)
        pages.append({"index": p.number, "w": p.rect.width, "h": p.rect.height, "rotation": p.rotation,
                      "uw": p.mediabox.width, "uh": p.mediabox.height, "kind": k})
    fields = []
    try:
        for p in doc:
            for wdg in p.widgets() or []:
                fields.append({"name": wdg.field_name, "type": wdg.field_type_string, "page": p.number,
                               "rect": list(wdg.rect), "value": wdg.field_value})
    except Exception:
        pass
    if fields:
        kind = "form"
    elif kinds <= {"scanned", "empty"} and "scanned" in kinds:
        kind = "scanned"
    elif "scanned" in kinds or "mixed" in kinds:
        kind = "mixed"
    else:
        kind = "text"
    meta = {"page_count": doc.page_count, "kind": kind, "pages": pages, "fields": fields,
            "has_js": _has_javascript(doc), "signed": _has_signature(doc)}
    doc.close()
    return meta


def _has_javascript(doc) -> bool:
    if hasattr(doc, "javascript_count"):
        return doc.javascript_count() > 0
    try:
        for xref in range(1, doc.xref_length()):
            obj = doc.xref_object(xref, compressed=False) or ""
            if "/JavaScript" in obj or "/JS " in obj:
                return True
    except Exception:
        return False
    return False


def _has_signature(doc) -> bool:
    if hasattr(doc, "signature_count"):
        return doc.signature_count() > 0
    try:
        return bool(doc.get_sigflags() and doc.get_sigflags() > 0)
    except Exception:
        return False


def render_page_png(data: bytes, index: int, zoom: float = 1.5) -> bytes:
    doc = open_pdf(data)
    if not 0 <= index < doc.page_count:
        raise PdfError("page_missing", f"没有第 {index + 1} 页")
    page = doc[index]
    zoom = max(0.2, min(zoom, 4.0))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    out = pix.tobytes("png")
    doc.close()
    return out


# ------------------------------------------------------------------ coordinates
def visual_norm_to_unrot(page: pymupdf.Page, nrect: list[float]) -> pymupdf.Rect:
    vw, vh = page.rect.width, page.rect.height
    x0, y0, x1, y1 = nrect
    vr = pymupdf.Rect(min(x0, x1) * vw, min(y0, y1) * vh, max(x0, x1) * vw, max(y0, y1) * vh)
    ur = vr * page.derotation_matrix
    ur.normalize()
    return ur


def unrot_to_visual_norm(page: pymupdf.Page, rect) -> list[float]:
    vr = pymupdf.Rect(rect) * page.rotation_matrix
    vr.normalize()
    vw, vh = page.rect.width, page.rect.height
    return [vr.x0 / vw, vr.y0 / vh, vr.x1 / vw, vr.y1 / vh]


def page_words(data: bytes, index: int) -> dict:
    doc = open_pdf(data)
    if not 0 <= index < doc.page_count:
        raise PdfError("page_missing", f"没有第 {index + 1} 页")
    page = doc[index]
    words = []
    for w in page.get_text("words"):
        words.append({"t": w[4], "r": unrot_to_visual_norm(page, w[:4]), "u": [round(x, 2) for x in w[:4]],
                      "b": w[5], "l": w[6], "n": w[7]})
    out = {"index": index, "w": page.rect.width, "h": page.rect.height, "rotation": page.rotation,
           "kind": page_kind(page), "words": words}
    doc.close()
    return out


def _is_cjk(ch: str) -> bool:
    return bool(ch) and unicodedata.east_asian_width(ch) in ("W", "F")


def join_words(words: list[tuple]) -> str:
    """Join PyMuPDF word tuples (x0,y0,x1,y1,text,block,line,wordno) into text by lines."""
    if not words:
        return ""
    words = sorted(words, key=lambda w: (w[5], w[6], w[7]))
    lines: list[list[tuple]] = []
    key = None
    for w in words:
        k = (w[5], w[6])
        if k != key:
            lines.append([])
            key = k
        lines[-1].append(w)
    out = []
    for ln in lines:
        s = ""
        for w in ln:
            if s and not (_is_cjk(s[-1]) and _is_cjk(w[4][:1])):
                s += " "
            s += w[4]
        out.append(s)
    return "\n".join(out)


def words_in_rect(page: pymupdf.Page, rect: pymupdf.Rect, mode: str = "center") -> list[tuple]:
    out = []
    for w in page.get_text("words"):
        wr = pymupdf.Rect(w[:4])
        if mode == "center":
            c = pymupdf.Point((wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2)
            if rect.contains(c):
                out.append(w)
        elif mode == "intersect" and wr.intersects(rect):
            out.append(w)
    return out


def extract_region_text(data_or_doc, index: int, rect) -> str:
    doc = open_pdf(data_or_doc) if isinstance(data_or_doc, (bytes, bytearray)) else data_or_doc
    page = doc[index]
    return join_words(words_in_rect(page, pymupdf.Rect(rect)))


def find_anchor(page: pymupdf.Page, rect: pymupdf.Rect, max_dist: float = 200) -> dict | None:
    """Find a label to the left on the same line (preferred) or directly above the region."""
    words = page.get_text("words")
    cy = (rect.y0 + rect.y1) / 2
    best = None
    for w in words:
        wr = pymupdf.Rect(w[:4])
        c = pymupdf.Point((wr.x0 + wr.x1) / 2, (wr.y0 + wr.y1) / 2)
        if rect.contains(c):
            continue
        same_line = wr.y1 > rect.y0 and wr.y0 < rect.y1 and wr.x1 <= rect.x0 + 2 and (wr.y0 - 2 <= cy <= wr.y1 + 2 or rect.height > wr.height)
        above = wr.y1 <= rect.y0 + 2 and wr.x0 < rect.x1 and wr.x1 > rect.x0
        if same_line:
            d = rect.x0 - wr.x1
            score = d
        elif above:
            d = rect.y0 - wr.y1
            score = d + 1000
        else:
            continue
        if d > max_dist:
            continue
        if best is None or score < best[0]:
            best = (score, w)
    if best is None:
        return None
    w = best[1]
    # include the whole line segment of the label word (same block/line, left of region)
    line = [x for x in words if x[5] == w[5] and x[6] == w[6] and pymupdf.Rect(x[:4]).x1 <= rect.x0 + 2]
    if not line:
        line = [w]
    text = join_words(line[-3:])
    lr = pymupdf.Rect(line[-1][:4])
    for x in line[-3:]:
        lr |= pymupdf.Rect(x[:4])
    return {"text": text, "rect": [lr.x0, lr.y0, lr.x1, lr.y1]}


def locate_with_anchor(page: pymupdf.Page, locator: dict, tol: float = 3.0) -> tuple[pymupdf.Rect | None, str | None]:
    """Validate layout and return the effective rect on ``page``.

    Returns (rect, None) or (None, error message).  Moves the rect with its
    label when the label moved; errors when the label is missing or ambiguous."""
    rect = pymupdf.Rect(locator["rect"])
    size = locator.get("page_size")
    if size and (abs(page.mediabox.width - size[0]) > tol or abs(page.mediabox.height - size[1]) > tol):
        return None, f"页面尺寸不同（{page.mediabox.width:.0f}×{page.mediabox.height:.0f} ≠ {size[0]:.0f}×{size[1]:.0f}）"
    if locator.get("rotation") is not None and page.rotation != locator["rotation"]:
        return None, f"页面旋转不同（{page.rotation}° ≠ {locator['rotation']}°）"
    anchor = locator.get("anchor")
    if not anchor:
        return rect, None
    atext = _norm(anchor["text"])
    ar = pymupdf.Rect(anchor["rect"])
    words = page.get_text("words")
    # candidate label positions: sequences of words on a line whose joined text ends with the anchor text
    cands = []
    by_line: dict[tuple, list] = {}
    for w in words:
        by_line.setdefault((w[5], w[6]), []).append(w)
    for ln in by_line.values():
        ln.sort(key=lambda w: w[7])
        for i in range(len(ln)):
            for j in range(i, min(i + 4, len(ln))):
                seg = ln[i:j + 1]
                if _norm(join_words(seg)) == atext:
                    r = pymupdf.Rect(seg[0][:4])
                    for x in seg:
                        r |= pymupdf.Rect(x[:4])
                    cands.append(r)
    if not cands:
        return None, f"找不到定位标签「{anchor['text']}」"
    # prefer the candidate closest to the original label position
    cands.sort(key=lambda r: abs(r.x0 - ar.x0) + abs(r.y0 - ar.y0))
    best = cands[0]
    if len(cands) > 1:
        d0 = abs(best.x0 - ar.x0) + abs(best.y0 - ar.y0)
        d1 = abs(cands[1].x0 - ar.x0) + abs(cands[1].y0 - ar.y0)
        if d0 > tol * 4 and d1 - d0 < 20:
            return None, f"定位标签「{anchor['text']}」出现 {len(cands)} 次，无法确定位置"
    dx, dy = best.x0 - ar.x0, best.y0 - ar.y0
    if abs(dx) > 150 or abs(dy) > 150:
        return None, f"定位标签「{anchor['text']}」位置变化过大，版式可能不同"
    return pymupdf.Rect(rect.x0 + dx, rect.y0 + dy, rect.x1 + dx, rect.y1 + dy), None


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def rgb(hexstr: str | None) -> tuple[float, float, float]:
    """'#1f2937' → (r, g, b) in 0..1; anything invalid → black."""
    s = (hexstr or "").lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16) / 255, int(s[2:4], 16) / 255, int(s[4:6], 16) / 255)
        except ValueError:
            pass
    return (0.0, 0.0, 0.0)


def hexcolor(c) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(float(x) * 255))) for x in c[:3])


def span_color(page: pymupdf.Page, rect) -> str | None:
    """Colour of the largest text span inside ``rect`` (as '#rrggbb')."""
    best = None
    try:
        for b in page.get_text("dict", clip=pymupdf.Rect(rect)).get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("text", "").strip() and (best is None or sp["size"] > best["size"]):
                        best = sp
    except Exception:  # noqa: BLE001
        return None
    if best is None:
        return None
    c = int(best.get("color", 0))
    return "#%06x" % c


def as_pdf_bytes(data: bytes) -> bytes:
    """PDF stays PDF; a PNG / JPEG output image becomes a one-page PDF (for page rendering)."""
    if data[:4] == b"%PDF":
        return data
    if data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff":
        from .image_template import to_pdf
        return to_pdf(data)
    return data


# ------------------------------------------------------------------ template operations
REDACT_TEXT_ONLY = "text"
REDACT_TEXT_AND_IMAGE_PIXELS = "text_pixels"


def apply_slot_redactions(doc: pymupdf.Document, slots: list[dict]) -> list[str]:
    """Remove original content under template boxes.  Returns warnings."""
    warns = []
    by_page: dict[int, list[dict]] = {}
    for sl in slots:
        if sl.get("kind") in ("pdf_box", "repeat") and sl.get("clear", "text") != "none":
            by_page.setdefault(sl["locator"]["page"], []).append(sl)
    for pno, items in by_page.items():
        if pno >= doc.page_count:
            warns.append(f"模板没有第 {pno + 1} 页")
            continue
        page = doc[pno]
        pixels = False
        patches: list[tuple[pymupdf.Rect, bytes]] = []
        for sl in items:
            r = pymupdf.Rect(sl["locator"]["rect"])
            fill = False if sl.get("clear", "text") == "text" else tuple(sl.get("fill") or (1, 1, 1))
            if sl["kind"] == "repeat" and sl.get("clear") == "text_pixels" and sl.get("layout"):
                # scanned / image table: clear each cell's interior so the drawn grid lines survive
                from .pdf_repeat import cell_rects
                rects = cell_rects(sl["layout"], inset=1.6)
            else:
                rects = [r]
            for cr in rects:
                if sl.get("clear") == "text_pixels" and sl.get("patch", True):
                    from .image_template import background_patch
                    png = background_patch(page, cr)       # sampled from the page *before* clearing
                    if png:
                        patches.append((cr, png))
                page.add_redact_annot(cr, fill=fill)
            if sl.get("clear") == "text_pixels":
                pixels = True
        kwargs = {"images": pymupdf.PDF_REDACT_IMAGE_PIXELS if pixels else pymupdf.PDF_REDACT_IMAGE_NONE}
        try:
            page.apply_redactions(graphics=pymupdf.PDF_REDACT_LINE_ART_NONE, **kwargs)
        except TypeError:
            page.apply_redactions(**kwargs)
        for cr, png in patches:
            page.insert_image(cr, stream=png, keep_proportion=False)
        for sl in items:
            left = words_in_rect(page, pymupdf.Rect(sl["locator"]["rect"])) if sl["kind"] == "pdf_box" else []
            if left:
                warns.append(f"字段「{sl.get('name')}」区域内仍有无法移除的文字：{join_words(left)[:40]}")
    return warns


class _Measurer:
    """Scratch document used to dry-run text layout (PyMuPDF has no pure measuring call
    for insert_textbox).  One scratch page per page geometry, font inserted once."""

    def __init__(self):
        self.doc = pymupdf.open()
        self.pages: dict[tuple, pymupdf.Page] = {}

    def fits(self, page: pymupdf.Page, rect, text: str, size: float, align: int, rotate: int | None = None) -> float:
        rotate = page.rotation if rotate is None else rotate
        key = (round(page.mediabox.width, 2), round(page.mediabox.height, 2), page.rotation)
        tp = self.pages.get(key)
        if tp is None:
            tp = self.doc.new_page(width=page.mediabox.width, height=page.mediabox.height)
            tp.set_rotation(page.rotation)
            tp.insert_font(fontname=FONT_NAME, fontbuffer=cjk_font().buffer)
            self.pages[key] = tp
        return tp.insert_textbox(rect, text, fontname=FONT_NAME, fontsize=size, align=align, rotate=rotate)

    def close(self):
        self.doc.close()


def fill_box(page: pymupdf.Page, rect, text: str, *, font_size: float = 11, min_font_size: float = 6,
             align: str = "left", color: tuple = (0, 0, 0), valign: str = "top", measurer: _Measurer | None = None,
             rotate: int | None = None) -> dict:
    """Write text into ``rect`` (unrotated coords), shrinking down to ``min_font_size``.
    ``rotate`` is the text direction in unrotated page space (default: upright as displayed).
    Returns {'ok': bool, 'font_size': float, 'overflow': bool}."""
    rect = pymupdf.Rect(rect)
    rotate = page.rotation if rotate is None else int(rotate) % 360
    if FONT_NAME not in [f[4] for f in page.get_fonts()]:
        page.insert_font(fontname=FONT_NAME, fontbuffer=cjk_font().buffer)
    al = {"left": pymupdf.TEXT_ALIGN_LEFT, "center": pymupdf.TEXT_ALIGN_CENTER, "right": pymupdf.TEXT_ALIGN_RIGHT}.get(align, 0)
    own = measurer is None
    m = measurer or _Measurer()
    try:
        size = float(font_size)
        while size >= float(min_font_size) - 1e-6:
            rc = m.fits(page, rect, text, size, al, rotate)
            if rc >= 0:
                r = rect
                if valign == "middle" and rc > 0 and rotate == page.rotation:
                    vr = rect * page.rotation_matrix
                    vr.normalize()
                    vr = pymupdf.Rect(vr.x0, vr.y0 + rc / 2, vr.x1, vr.y1 + rc / 2)
                    r = vr * page.derotation_matrix
                    r.normalize()
                page.insert_textbox(r, text, fontname=FONT_NAME, fontsize=size, align=al, color=color, rotate=rotate)
                return {"ok": True, "font_size": size, "overflow": False}
            size -= 0.5
        return {"ok": False, "font_size": min_font_size, "overflow": True}
    finally:
        if own:
            m.close()


def fill_pdf(template_bytes: bytes, slots: list[dict], values: dict[str, str],
             rows: dict[str, list[dict[str, str]]] | None = None) -> tuple[bytes, list[dict]]:
    """Fill pdf_box / pdf_field slots (``values``: slot id -> text) and repeat areas
    (``rows``: slot id -> list of {column key: text}).  Returns (pdf bytes, issues).

    Order matters for repeat areas that continue on copies of their page: single
    fields are filled first, then the page is duplicated, so every continuation page
    carries the same header values; finally the detail rows are split over the copies."""
    from . import pdf_repeat as pr
    rows = rows or {}
    doc = open_pdf(template_bytes)
    issues: list[dict] = []
    measurer = _Measurer()
    for sl in slots:
        sid = sl["id"]
        if sl["kind"] == "repeat" or sid not in values:
            continue
        text = values[sid]
        if sl["kind"] == "pdf_field":
            page = doc[sl["locator"]["page"]]
            target = None
            for wdg in page.widgets() or []:
                if wdg.field_name == sl["locator"]["field"]:
                    target = wdg
                    break
            if target is None:
                issues.append({"level": "error", "slot": sid, "code": "field_missing", "message": f"模板中找不到表单字段「{sl['locator']['field']}」"})
                continue
            rect = pymupdf.Rect(target.rect)
            fs = sl.get("font_size") or (target.text_fontsize or 11)
            page.delete_widget(target)
            res = fill_box(page, rect, text, font_size=fs or 11, min_font_size=sl.get("min_font_size", 6),
                           align=sl.get("align", "left"), measurer=measurer, color=rgb(sl.get("color")))
        else:
            page = doc[sl["locator"]["page"]]
            res = fill_box(page, sl["locator"]["rect"], text, font_size=sl.get("font_size", 11),
                           min_font_size=sl.get("min_font_size", 6), align=sl.get("align", "left"),
                           valign=sl.get("valign", "top"), measurer=measurer, rotate=sl.get("text_rotate"), color=rgb(sl.get("color")))
        if not res["ok"]:
            issues.append({"level": "error", "slot": sid, "code": "overflow",
                           "message": f"字段「{sl.get('name')}」内容过长，缩小到 {sl.get('min_font_size', 6)} 号字仍无法放入区域"})
        elif res["font_size"] < float(sl.get("font_size", 11)):
            issues.append({"level": "info", "slot": sid, "code": "shrunk",
                           "message": f"字段「{sl.get('name')}」字号已从 {sl.get('font_size', 11)} 缩小到 {res['font_size']}"})

    reps = [s for s in slots if s["kind"] == "repeat"]
    plans = {}
    extra: dict[int, int] = {}
    for sl in reps:
        rr = list(rows.get(sl["id"]) or [])
        if not rr and sl.get("empty_policy") == "placeholder" and sl["layout"]["cols"]:
            rr = [{sl["layout"]["cols"][0]["key"]: sl.get("placeholder", "（无）")}]
        pl = pr.plan_pages(sl, len(rr))
        pl["rows"] = rr
        plans[sl["id"]] = pl
        if pl["mode"] == "error":
            issues.append({"level": "error", "slot": sl["id"], "code": "repeat_overflow", "message": pl["message"]})
        elif pl["mode"] == "continue":
            p = sl["locator"]["page"]
            extra[p] = max(extra.get(p, 0), pl["pages"] - 1)
        elif pl["mode"] == "shrink":
            issues.append({"level": "warning", "slot": sl["id"], "code": "rows_shrunk",
                           "message": f"明细「{sl.get('name')}」{len(rr)} 行超过区域容量，行高已压缩为原来的 {pl['scale']:.0%}"})
    for p in sorted(extra, reverse=True):
        for _ in range(extra[p]):
            doc.fullcopy_page(p, to=p + 1 if p + 1 < doc.page_count else -1)
        if extra[p]:
            issues.append({"level": "warning", "code": "continued_pages",
                           "message": f"第 {p + 1} 页的明细超出区域，已复制该页续写 {extra[p]} 页（页上的其他字段在续页中同样填写）"})

    def new_index(q: int) -> int:
        return q + sum(e for p, e in extra.items() if p < q)

    for sl in reps:
        pl = plans[sl["id"]]
        if pl["mode"] == "error":
            continue
        p0 = new_index(sl["locator"]["page"])
        rr = pl["rows"]
        for i, r in enumerate(rr):
            r.setdefault("__n", str(i + 1))
        chunks = [rr[i:i + pl["per_page"]] for i in range(0, len(rr), pl["per_page"])] if pl["mode"] == "continue" else [rr]
        for k, chunk in enumerate(chunks):
            issues.extend(pr.fill_rows(doc[p0 + k], sl, chunk, measurer, row_h=pl["row_height"], font_scale=min(1.0, pl["scale"])))
    measurer.close()
    try:
        doc.subset_fonts()
    except Exception:
        pass
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out, issues


def make_working_copy(original: bytes, slots: list[dict]) -> tuple[bytes, list[str]]:
    """Template working copy: original content under boxes removed (true redaction)."""
    doc = open_pdf(original)
    warns = apply_slot_redactions(doc, slots)
    out = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return out, warns


def preview_removed_text(original: bytes, page_index: int, rect) -> str:
    doc = open_pdf(original)
    txt = join_words(words_in_rect(doc[page_index], pymupdf.Rect(rect)))
    doc.close()
    return txt


def page_count(data: bytes) -> int:
    doc = open_pdf(data)
    n = doc.page_count
    doc.close()
    return n


def pdf_text(data: bytes) -> str:
    """All text of a PDF (used to verify produced files)."""
    doc = open_pdf(data)
    try:
        return "\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


_DIR_TO_ROTATE = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}


def text_rotation_in(page: pymupdf.Page, rect) -> int | None:
    """Direction of existing text inside ``rect`` expressed as insert_textbox ``rotate``."""
    counts: dict[int, int] = {}
    try:
        d = page.get_text("dict", clip=pymupdf.Rect(rect))
    except Exception:
        return None
    for b in d.get("blocks", []):
        for ln in b.get("lines", []):
            dx, dy = ln.get("dir", (1, 0))
            key = (round(dx), round(dy))
            if key in _DIR_TO_ROTATE:
                n = sum(len(sp.get("text", "")) for sp in ln.get("spans", []))
                counts[_DIR_TO_ROTATE[key]] = counts.get(_DIR_TO_ROTATE[key], 0) + n
    if not counts:
        return None
    return max(counts, key=counts.get)
