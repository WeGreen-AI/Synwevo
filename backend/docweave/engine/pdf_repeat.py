"""Repeat-detail areas in PDF templates.

A repeat area is a rectangle on one page.  It is divided into columns (from the
table's vertical rules, else from the header words above the area and the
sample row inside it) and fixed-height rows (from horizontal rules, else from
the sample text line spacing).  Each detail record is written into one row;
each cell uses the same shrink-to-fit rules as single PDF boxes.

Layout numbers are stored in *table space*: unrotated page space rotated by the
direction of the text around the area (``trot``, the same value PyMuPDF's
``insert_textbox(rotate=…)`` uses).  In table space the text reads left to
right and rows run downwards, whatever the page /Rotate or the content
orientation is.
"""
from __future__ import annotations

import math
import re
import statistics

import pymupdf

from . import pdf_engine as pe

PAD = 2.0
_NUMERIC = re.compile(r"^[-+(]?[¥$€£]?\s*[\d,]+(\.\d+)?%?\)?$")


def table_matrix(trot: int) -> pymupdf.Matrix:
    """Unrotated page space → table space (text direction becomes +x)."""
    return pymupdf.Matrix(int(trot) % 360)


def _to_t(r, m: pymupdf.Matrix) -> pymupdf.Rect:
    v = pymupdf.Rect(r) * m
    v.normalize()
    return v


def to_unrot(r, trot: int) -> pymupdf.Rect:
    return _to_t(r, ~table_matrix(trot))


def _segments(page: pymupdf.Page, m: pymupdf.Matrix) -> tuple[list[tuple], list[tuple]]:
    """Horizontal (y, x0, x1) and vertical (x, y0, y1) rules in table space."""
    hs, vs = [], []

    def add(p, q):
        p, q = pymupdf.Point(p) * m, pymupdf.Point(q) * m
        if abs(p.y - q.y) <= 0.8 and abs(p.x - q.x) >= 4:
            hs.append(((p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x)))
        elif abs(p.x - q.x) <= 0.8 and abs(p.y - q.y) >= 4:
            vs.append(((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y)))

    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 - broken content streams: no rules
        return hs, vs
    for d in drawings:
        for it in d.get("items", []):
            op = it[0]
            if op == "l":
                add(it[1], it[2])
            elif op == "re":
                r = pymupdf.Rect(it[1])
                tr = _to_t(r, m)
                if tr.height <= 2.5 and tr.width >= 4:          # thin filled bar used as a rule
                    hs.append(((tr.y0 + tr.y1) / 2, tr.x0, tr.x1))
                elif tr.width <= 2.5 and tr.height >= 4:
                    vs.append(((tr.x0 + tr.x1) / 2, tr.y0, tr.y1))
                else:
                    add(r.tl, r.tr), add(r.bl, r.br), add(r.tl, r.bl), add(r.tr, r.br)
            elif op == "qu":
                q = it[1]
                add(q.ul, q.ur), add(q.ll, q.lr), add(q.ul, q.ll), add(q.ur, q.lr)
    return hs, vs


def _add_seg(hs: list, vs: list, p: pymupdf.Point, q: pymupdf.Point) -> None:
    if abs(p.y - q.y) <= 0.8:
        hs.append(((p.y + q.y) / 2, min(p.x, q.x), max(p.x, q.x)))
    elif abs(p.x - q.x) <= 0.8:
        vs.append(((p.x + q.x) / 2, min(p.y, q.y), max(p.y, q.y)))


def _cluster(vals: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(c) / len(c) for c in out]


def _words(page: pymupdf.Page, m: pymupdf.Matrix) -> list[dict]:
    return [{"r": _to_t(w[:4], m), "t": w[4]} for w in page.get_text("words")]


def _ocr_cell(page: pymupdf.Page, trect: pymupdf.Rect, trot: int) -> str:
    """Text of one table cell without a text layer (scans, image templates), read with the
    local Tesseract as a single line.  Only used to *suggest* column names / alignment;
    returns "" when OCR is unavailable or unsure."""
    try:
        from .image_template import region_pixels
        from .ocr import OcrError, ocr_png, tesseract_path
        if not tesseract_path() or trect.width < 6 or trect.height < 6:
            return ""
        ur = to_unrot(trect, trot) & page.mediabox
        zoom = max(1.5, min(4.0, 60 / max(trect.height, 1)))       # ≥1.5× and a ~60 px cell suit Tesseract best
        from PIL import ImageOps
        im = ImageOps.autocontrast(region_pixels(page, ur, zoom=zoom).convert("L"), cutoff=1)
        if trot % 360:
            im = im.rotate(trot, expand=True, fillcolor=255)             # make the text upright
        im = ImageOps.expand(im, border=12, fill=255)
        import io as _io
        buf = _io.BytesIO()
        im.save(buf, "PNG")
        res = ocr_png(buf.getvalue(), psm=7)
    except (OcrError, OSError, ValueError):
        return ""
    ws = [w for w in res["words"] if w.get("conf", 0) >= 50]
    if not ws:
        return ""
    txt = ""
    for w in ws:
        if txt and not (pe._is_cjk(txt[-1]) and pe._is_cjk(w["t"][:1])):
            txt += " "
        txt += w["t"]
    return txt.strip()


def _cx(w: dict) -> float:
    return (w["r"].x0 + w["r"].x1) / 2


def _cy(w: dict) -> float:
    return (w["r"].y0 + w["r"].y1) / 2


def _group_x(ws: list[dict]) -> list[list[dict]]:
    """Words of one band → column groups (split at wide horizontal gaps)."""
    ws = sorted(ws, key=lambda w: w["r"].x0)
    groups: list[list[dict]] = []
    for w in ws:
        h = max(w["r"].height, 4)
        if groups:
            last = max(x["r"].x1 for x in groups[-1])
            if w["r"].x0 - last < max(5.0, 0.9 * h):
                groups[-1].append(w)
                continue
        groups.append([w])
    return groups


def _text(ws: list[dict]) -> str:
    ws = sorted(ws, key=lambda w: (round(w["r"].y0 / 3), w["r"].x0))
    s = ""
    for w in ws:
        if s and not (pe._is_cjk(s[-1]) and pe._is_cjk(w["t"][:1])):
            s += " "
        s += w["t"]
    return s.strip()


def _span_sizes(page: pymupdf.Page, urect) -> list[float]:
    sizes = []
    try:
        for b in page.get_text("dict", clip=pymupdf.Rect(urect)).get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp.get("text", "").strip():
                        sizes.append(sp["size"])
    except Exception:  # noqa: BLE001
        pass
    return sizes


def _split_by_header(groups: list[list[dict]], sample: list[dict], x0: float, x1: float) -> list[float]:
    """Column bounds from header groups when the table has no vertical rules.

    Headers may be left-aligned, centred or right-aligned over their columns; the
    candidate that keeps every sample word inside one column and leaves no column
    without sample text wins (ties → centred)."""
    ext = [(min(w["r"].x0 for w in g), max(w["r"].x1 for w in g)) for g in groups]
    cands = {
        "center": [x0] + [(a[1] + b[0]) / 2 for a, b in zip(ext, ext[1:])] + [x1],
        "left": [x0] + [b[0] - 3 for b in ext[1:]] + [x1],
        "right": [x0] + [a[1] + 3 for a in ext[:-1]] + [x1],
    }
    best, best_score = cands["center"], None
    for key in ("center", "left", "right"):
        bounds = cands[key]
        if any(b2 - b1 < 6 for b1, b2 in zip(bounds, bounds[1:])):
            continue
        cross = sum(1 for w in sample for b in bounds[1:-1] if w["r"].x0 < b - 0.5 and w["r"].x1 > b + 0.5)
        filled = [sum(1 for w in sample if b1 <= _cx(w) <= b2) for b1, b2 in zip(bounds, bounds[1:])]
        empty = sum(1 for f in filled if f == 0) if sample else 0
        crowded = sum(f - 1 for f in filled if f > 1)
        score = cross * 3 + empty * 2 + crowded
        if best_score is None or score < best_score:
            best, best_score = bounds, score
    return best


def detect_layout(page: pymupdf.Page, urect) -> dict:
    """Propose columns / row height for a repeat area (``urect`` in unrotated space).

    Returns {"trot", "trect", "cols": [{key,name,x0,x1,align,sample}], "row_height", "font_size",
    "rows_fit", "source": {...}}; all numbers in table space (points)."""
    urect = pymupdf.Rect(urect)
    trot = pe.text_rotation_in(page, urect)
    if trot is None:        # empty area: use the header / labels around it
        trot = pe.text_rotation_in(page, pymupdf.Rect(urect.x0 - 45, urect.y0 - 45, urect.x1 + 45, urect.y1 + 45))
    if trot is None:
        trot = page.rotation
    m = table_matrix(trot)
    tr = _to_t(urect, m)
    hs, vs = _segments(page, m)
    raster = False
    if not hs and not vs and pe.page_kind(page) in ("scanned", "mixed"):
        # rules drawn in pixels (scans, image templates): row/column darkness projections
        from .image_template import raster_rules
        probe = pymupdf.Rect(urect.x0 - 6, urect.y0 - 64, urect.x1 + 6, urect.y1 + 6) & page.mediabox
        rh_, rv_ = raster_rules(page, probe)
        for y in rh_:
            p, q = pymupdf.Point(probe.x0, y) * m, pymupdf.Point(probe.x1, y) * m
            _add_seg(hs, vs, p, q)
        for x in rv_:
            p, q = pymupdf.Point(x, probe.y0) * m, pymupdf.Point(x, probe.y1) * m
            _add_seg(hs, vs, p, q)
        raster = bool(rh_ or rv_)
    words = _words(page, m)
    ocr_used = False
    # snap the area to rules that run along its edges
    near = [x for x, y0, y1 in vs if abs(x - tr.x0) <= 3 and y1 > tr.y0 and y0 < tr.y1]
    x0 = min(near, key=lambda x: abs(x - tr.x0)) if near else tr.x0
    near = [x for x, y0, y1 in vs if abs(x - tr.x1) <= 3 and y1 > tr.y0 and y0 < tr.y1]
    x1 = min(near, key=lambda x: abs(x - tr.x1)) if near else tr.x1
    near = [y for y, a, b in hs if abs(y - tr.y0) <= 3 and b > x0 and a < x1]
    top = min(near, key=lambda y: abs(y - tr.y0)) if near else tr.y0
    bottom = tr.y1
    width = max(x1 - x0, 1)
    area = pymupdf.Rect(x0, top, x1, bottom)

    # ---- header band above the area
    above = [w for w in words if top - 45 <= w["r"].y1 <= top + 2 and x0 - 2 <= _cx(w) <= x1 + 2]
    header: list[dict] = []
    if above:
        hy = max(w["r"].y1 for w in above)
        line_top = min(w["r"].y0 for w in above if w["r"].y1 >= hy - 2)
        rules = [y for y, a, b in hs if top - 45 <= y < line_top - 0.5 and (min(b, x1) - max(a, x0)) >= 0.6 * width]
        band_top = max(rules) if rules else line_top - 0.5
        header = [w for w in above if w["r"].y0 >= band_top - 0.5]
    inside = [w for w in words if area.contains(pymupdf.Point(_cx(w), _cy(w)))]

    # ---- rows
    hy_in = _cluster([y for y, a, b in hs if top - 0.5 <= y <= bottom + 1 and (min(b, x1) - max(a, x0)) >= 0.6 * width], 1.0)
    diffs = [b - a for a, b in zip(hy_in, hy_in[1:]) if b - a >= 6]
    lines = _cluster([_cy(w) for w in inside], 2.5)
    if diffs:
        row_h, rsrc = statistics.median(diffs), "rules"
        first = [w for w in inside if w["r"].y0 >= top - 1 and w["r"].y1 <= top + row_h + 1]
    else:
        ldiff = [b - a for a, b in zip(lines, lines[1:]) if b - a >= 6]
        hts = [w["r"].height for w in inside] or [w["r"].height for w in header] or [10.0]
        if ldiff:
            row_h, rsrc = statistics.median(ldiff), "text"
        elif lines and header and max(hts) * 1.1 <= lines[0] - statistics.mean(_cy(w) for w in header) <= max(hts) * 4:
            row_h, rsrc = lines[0] - statistics.mean(_cy(w) for w in header), "header"   # same pitch as header → first row
        else:
            row_h, rsrc = max(hts) * 1.8, ("text" if inside else "default")
        first = [w for w in inside if abs(_cy(w) - lines[0]) <= 2.5] if lines else []
    row_h = max(6.0, round(row_h, 2))
    # a drag that stops just short of (or just past) a row rule means that rule
    below = [y for y, a, b in hs if abs(y - bottom) <= max(3.0, 0.3 * row_h) and (min(b, x1) - max(a, x0)) >= 0.6 * width]
    if below:
        bottom = min(below, key=lambda y: abs(y - bottom))
    elif rsrc != "rules":
        k = round((bottom - top) / row_h)
        if k >= 1 and abs(top + k * row_h - bottom) <= 0.3 * row_h:
            bottom = top + k * row_h

    # ---- columns
    vx = _cluster([x for x, y0, y1 in vs if x0 + 3 < x < x1 - 3 and min(y1, bottom) - max(y0, top - 30) >= min(6, bottom - top)], 1.5)
    if vx:
        bounds, csrc = [x0, *vx, x1], "rules"
    else:
        hg, sg = _group_x(header), _group_x(first)
        if len(hg) >= 2:
            bounds, csrc = _split_by_header(hg, first, x0, x1), "header"
        elif len(sg) >= 2:
            ext = [(min(w["r"].x0 for w in g), max(w["r"].x1 for w in g)) for g in sg]
            bounds, csrc = [x0] + [(a[1] + b[0]) / 2 for a, b in zip(ext, ext[1:])] + [x1], "sample"
        else:
            bounds, csrc = [x0, x1], "single"
    pixel_page = not words and pe.page_kind(page) in ("scanned", "mixed")
    head_top = max([y for y, a, b in hs if top - 60 <= y < top - 6 and (min(b, x1) - max(a, x0)) >= 0.6 * width], default=top - 30)
    cols = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        hw = [w for w in header if a - 1 <= _cx(w) <= b + 1]
        sw = [w for w in first if a - 1 <= _cx(w) <= b + 1]
        sample = _text(sw)
        name = _text(hw)
        if pixel_page:
            name = _ocr_cell(page, pymupdf.Rect(a + 3, head_top + 3, b - 3, top - 3), trot)
            sample = _ocr_cell(page, pymupdf.Rect(a + 3, top + 3, b - 3, top + row_h - 3), trot)
            ocr_used = ocr_used or bool(name or sample)
        name = name or f"第{i + 1}列"
        align = "right" if sample and _NUMERIC.match(sample.replace(" ", "")) else "left"
        cols.append({"key": f"c{i}", "name": name[:40], "x0": round(a, 2), "x1": round(b, 2), "align": align, "sample": sample[:60]})
    sizes = _span_sizes(page, to_unrot(pymupdf.Rect(x0, top, x1, top + row_h), trot)) or \
        _span_sizes(page, to_unrot(pymupdf.Rect(x0, top - 45, x1, top), trot))
    font = round(min(max(sizes) if sizes else 10.0, row_h * 0.72), 1)
    trect = [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)]
    return {"trot": int(trot) % 360, "trect": trect, "cols": cols, "row_height": row_h, "font_size": font,
            "rows_fit": capacity(trect, row_h), "source": {"cols": csrc, "rows": rsrc, "raster": raster, "ocr": ocr_used},
            "visual_aligned": int(trot) % 360 == page.rotation}


def capacity(trect, row_h: float) -> int:
    return max(0, int(math.floor((trect[3] - trect[1]) / row_h + 1e-6)))


def validate_layout(lay: dict, page: pymupdf.Page) -> None:
    from .templates import TemplateError
    x0, y0, x1, y1 = lay["trect"]
    cols = lay["cols"]
    if not cols:
        raise TemplateError("no_columns", "明细区域至少需要一列")
    if x1 - x0 < 12 or y1 - y0 < 6:
        raise TemplateError("too_small", "明细区域太小")
    if lay["row_height"] < 6 or lay["row_height"] > (y1 - y0) + 0.01:
        raise TemplateError("row_height", f"行高 {lay['row_height']} 不合适：应在 6 到区域高度 {y1 - y0:.1f} 之间")
    prev = x0 - 0.01
    for c in cols:
        if c["x1"] - c["x0"] < 6:
            raise TemplateError("narrow_column", f"列「{c['name']}」太窄（{c['x1'] - c['x0']:.1f} 点）")
        if c["x0"] < prev - 0.5 or c["x1"] > x1 + 0.5 or c["x0"] < x0 - 0.5:
            raise TemplateError("column_order", "列边界必须从左到右排列并位于区域内")
        prev = c["x1"]
    ur = to_unrot(pymupdf.Rect(lay["trect"]), lay["trot"])
    full = page.rect * page.derotation_matrix
    full.normalize()
    if not pymupdf.Rect(full.x0 - 1, full.y0 - 1, full.x1 + 1, full.y1 + 1).contains(ur):
        raise TemplateError("outside", "明细区域超出页面")


def cell_rects(lay: dict, inset: float = 1.6) -> list[pymupdf.Rect]:
    """Interior of every cell (unrotated coords) – used to clear pixels without erasing grid lines."""
    x0, y0, x1, y1 = lay["trect"]
    out = []
    n = capacity(lay["trect"], lay["row_height"])
    rows = [(y0 + i * lay["row_height"], y0 + (i + 1) * lay["row_height"]) for i in range(n)]
    if rows and rows[-1][1] < y1 - 1:
        rows.append((rows[-1][1], y1))
    for a, b in rows:
        for c in lay["cols"]:
            r = pymupdf.Rect(c["x0"] + inset, a + inset, c["x1"] - inset, b - inset)
            if r.width > 0.5 and r.height > 0.5:
                out.append(to_unrot(r, lay["trot"]))
    return out


def grid_lines(lay: dict) -> list[tuple[pymupdf.Rect, str]]:
    """Column dividers and row lines as (rect in unrotated space, kind) for display."""
    x0, y0, x1, y1 = lay["trect"]
    out = []
    for c in lay["cols"][:-1]:
        out.append((to_unrot(pymupdf.Rect(c["x1"], y0, c["x1"], y1), lay["trot"]), "col"))
    n = capacity(lay["trect"], lay["row_height"])
    for i in range(1, n + 1):
        y = y0 + i * lay["row_height"]
        if y < y1 - 0.5:
            out.append((to_unrot(pymupdf.Rect(x0, y, x1, y), lay["trot"]), "row"))
    return out


def fill_rows(page: pymupdf.Page, sl: dict, rows: list[dict[str, str]], measurer, row_h: float | None = None,
              font_scale: float = 1.0) -> list[dict]:
    """Write ``rows`` (column key → text) into the area on ``page``.  Returns issues."""
    lay = sl["layout"]
    rh = row_h or lay["row_height"]
    x0, top, x1, bottom = lay["trect"]
    trot = lay["trot"]
    issues = []
    names = {c["key"]: c.get("name") for c in sl.get("columns", [])}
    base = float(sl.get("font_size") or lay.get("font_size") or 10)
    size = max(float(sl.get("min_font_size", 5)), round(base * font_scale * 2) / 2)
    for i, row in enumerate(rows):
        y0 = top + i * rh
        for c in lay["cols"]:
            text = row.get(c["key"], "")
            if not text:
                continue
            pad = max(PAD, min(8.0, lay["row_height"] * 0.18))          # like the sample text's own margin
            cell = pymupdf.Rect(c["x0"] + pad, y0 + 0.4, c["x1"] - pad, y0 + rh - 0.4)
            res = pe.fill_box(page, to_unrot(cell, trot), text, font_size=size, min_font_size=sl.get("min_font_size", 5),
                              align=c.get("align", "left"), valign="middle", measurer=measurer, rotate=trot, color=pe.rgb(sl.get("color")))
            if not res["ok"]:
                issues.append({"level": "error", "slot": sl["id"], "code": "overflow",
                               "message": f"明细「{sl.get('name')}」第 {row.get('__n', i + 1)} 行「{names.get(c['key']) or c['name']}」内容过长，缩小到 {sl.get('min_font_size', 5)} 号字仍放不下"})
    return issues


def plan_pages(sl: dict, n: int) -> dict:
    """How ``n`` rows are laid out.  {"mode": "fit"|"shrink"|"continue"|"error", "pages": k, "row_height", "scale", "per_page"}."""
    lay = sl["layout"]
    rh = lay["row_height"]
    cap = capacity(lay["trect"], rh)
    policy = sl.get("overflow", "error")
    if n <= cap:
        return {"mode": "fit", "pages": 1, "row_height": rh, "scale": 1.0, "per_page": max(cap, 1)}
    if policy == "shrink":
        h = lay["trect"][3] - lay["trect"][1]
        new_rh = h / n
        scale = new_rh / rh
        min_scale = float(sl.get("min_row_scale", 0.5))
        if scale >= min_scale:
            return {"mode": "shrink", "pages": 1, "row_height": new_rh, "scale": scale, "per_page": n}
        return {"mode": "error", "pages": 1, "row_height": rh, "scale": 1.0, "per_page": cap,
                "message": f"明细「{sl.get('name')}」有 {n} 行，压缩到最小行高（{min_scale:.0%}）仍只能容纳 {int(h / (rh * min_scale) + 1e-6)} 行"}
    if policy == "continue" and cap > 0:
        return {"mode": "continue", "pages": math.ceil(n / cap), "row_height": rh, "scale": 1.0, "per_page": cap}
    return {"mode": "error", "pages": 1, "row_height": rh, "scale": 1.0, "per_page": cap,
            "message": f"明细「{sl.get('name')}」有 {n} 行，区域只能容纳 {cap} 行。可在字段设置中把「超出时」改为「复制本页续写」或「压缩行高」"}
