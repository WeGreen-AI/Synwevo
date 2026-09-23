"""Text layout and drawing straight onto pixels, with no PDF engine involved.

Image templates used to be filled by wrapping the picture in a one-page PDF, running the
whole PDF machinery over it and rasterising the result.  That made a purely raster feature
depend on PyMuPDF, so image templates were unavailable in the AGPL-free deployment even
though nothing about them needs a PDF.

This module reproduces the geometry of PyMuPDF's ``insert_textbox`` so existing templates
keep laying out the same way:

* line height  = fontsize x (ascender - descender), from the font's hhea table
* first baseline = box top + fontsize x ascender
* greedy wrap on spaces, falling back to character-by-character for a run that does not fit
  (which is what makes Chinese wrap at all — it has no spaces)
* the "does it fit" answer is box height - text height, negative when it does not

Advance widths come from the font's ``hmtx`` table rather than from a rendered bitmap, so
measuring is exact and independent of the rasteriser.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from . import fonts


@dataclass(frozen=True)
class Metrics:
    ascender: float          # em-relative, positive
    descender: float         # em-relative, negative
    advances: dict           # codepoint -> em-relative advance width


@functools.lru_cache(maxsize=4)
def _metrics(path: str) -> Metrics:
    from fontTools.ttLib import TTCollection, TTFont
    if path.lower().endswith(".ttc"):
        with TTCollection(path, lazy=True) as coll:
            font = coll.fonts[0]
            return _metrics_from(font)
    with TTFont(path, lazy=True) as font:
        return _metrics_from(font)


def _metrics_from(font) -> Metrics:
    upm = float(font["head"].unitsPerEm)
    hhea = font["hhea"]
    cmap = font.getBestCmap() or {}
    hmtx = font["hmtx"].metrics
    adv = {}
    for cp, name in cmap.items():
        m = hmtx.get(name)
        if m:
            adv[cp] = m[0] / upm
    return Metrics(hhea.ascent / upm, hhea.descent / upm, adv)


class RasterFont:
    """A CJK-capable font, measured exactly and drawn with Pillow."""

    def __init__(self, path: str | None = None):
        self.path = path or fonts.require_cjk_font()
        self.m = _metrics(self.path)
        self._fallback = max(self.m.advances.values(), default=0.5)

    @functools.lru_cache(maxsize=64)  # noqa: B019 - bounded by the sizes a template uses
    def _pil(self, size: float) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(self.path, size=max(1, int(round(size))))

    def advance(self, ch: str, size: float) -> float:
        return self.m.advances.get(ord(ch), self._fallback) * size

    def width(self, text: str, size: float) -> float:
        a = self.m.advances
        fb = self._fallback
        return sum(a.get(ord(c), fb) for c in text) * size

    def line_height(self, size: float) -> float:
        return (self.m.ascender - self.m.descender) * size

    # ------------------------------------------------------------------ layout
    def wrap(self, text: str, width: float, size: float) -> list[str]:
        """Greedy wrap.  Words are split on spaces; a word wider than the box is broken
        character by character, which is also how CJK runs get wrapped."""
        out: list[str] = []
        for para in text.split("\n"):
            if not para:
                out.append("")
                continue
            line = ""
            for word in _chunks(para):
                cand = line + word
                if line and self.width(cand.rstrip(), size) > width:
                    out.append(line.rstrip())      # this word starts a new line
                    line = word
                else:
                    line = cand
                # a single run wider than the box (a long CJK sentence has no spaces at all)
                while self.width(line.rstrip(), size) > width and len(line) > 1:
                    cut = _longest_prefix(self, line, width, size)
                    out.append(line[:cut])
                    line = line[cut:]
            out.append(line.rstrip())
        return out

    def fits(self, text: str, box: tuple[float, float, float, float], size: float) -> float:
        """Remaining vertical space, negative when the text does not fit (PyMuPDF's rc)."""
        x0, y0, x1, y1 = box
        lines = self.wrap(text, x1 - x0, size)
        if any(self.width(ln, size) > (x1 - x0) + 0.01 for ln in lines):
            return -1.0
        height = self.line_height(size) * len(lines) - self.m.descender * size
        return (y1 - y0) - height


def _chunks(para: str) -> list[str]:
    """Split into space-delimited words, keeping the separators (MuPDF wraps on spaces)."""
    out, cur = [], ""
    for ch in para:
        cur += ch
        if ch == " ":
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def _longest_prefix(font: RasterFont, s: str, width: float, size: float) -> int:
    n = 1
    while n < len(s) and font.width(s[:n + 1], size) <= width:
        n += 1
    return n


ALIGN = {"left": 0, "center": 1, "right": 2}


def draw_box(im: Image.Image, box: tuple[float, float, float, float], text: str, *,
             font: RasterFont, font_size: float = 11.0, min_font_size: float = 6.0,
             align: str = "left", valign: str = "top", color=(0, 0, 0), rotate: int = 0) -> dict:
    """Shrink-to-fit text into ``box`` (pixel coords).  Mirrors pdf_engine.fill_box.

    ``rotate`` is a multiple of 90; the text is laid out upright in its own space and the
    rendered mask is rotated, which keeps wrapping identical to the unrotated case.
    Returns {'ok', 'font_size', 'overflow', 'lines'}.
    """
    rotate = int(rotate) % 360
    if rotate % 90:
        raise ValueError("只支持 90 度的整数倍旋转")
    x0, y0, x1, y1 = (float(v) for v in box)
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return {"ok": False, "font_size": min_font_size, "overflow": True, "lines": []}
    # in rotated space the box's width/height swap
    lay_w, lay_h = (bh, bw) if rotate in (90, 270) else (bw, bh)

    size = float(font_size)
    while size >= float(min_font_size) - 1e-6:
        rc = font.fits(text, (0, 0, lay_w, lay_h), size)
        if rc >= 0:
            lines = font.wrap(text, lay_w, size)
            _render(im, (x0, y0, x1, y1), lines, font, size, align, valign, color, rotate, rc, lay_w, lay_h)
            return {"ok": True, "font_size": size, "overflow": False, "lines": lines}
        size -= 0.5
    return {"ok": False, "font_size": float(min_font_size), "overflow": True, "lines": []}


def _render(im, box, lines, font: RasterFont, size, align, valign, color, rotate, rc, lay_w, lay_h) -> None:
    x0, y0, x1, y1 = box
    # Draw into a greyscale mask sized to the layout space, then rotate and paste. Painting
    # through a mask keeps the original pixels everywhere the glyphs do not cover.
    mask = Image.new("L", (max(1, int(round(lay_w))), max(1, int(round(lay_h)))), 0)
    d = ImageDraw.Draw(mask)
    pil = font._pil(size)
    lh = font.line_height(size)
    top = font.m.ascender * size + (rc / 2 if valign == "middle" and rc > 0 else 0)
    for i, ln in enumerate(lines):
        if not ln:
            continue
        w = font.width(ln, size)
        x = 0.0 if align == "left" else (lay_w - w) if align == "right" else (lay_w - w) / 2
        # anchor "ls" = left baseline, so our computed baseline is used verbatim
        d.text((x, top + i * lh), ln, font=pil, fill=255, anchor="ls")
    if rotate:
        mask = mask.rotate(rotate, expand=True)
    paint = Image.new(im.mode, mask.size, _color(color, im.mode))
    im.paste(paint, (int(round(x0)), int(round(y0))), mask)


def _color(color, mode: str):
    if isinstance(color, (int, float)):
        v = int(round(float(color) * 255)) if float(color) <= 1 else int(color)
        rgb = (v, v, v)
    else:
        vals = list(color)[:3]
        rgb = tuple(int(round(v * 255)) if v <= 1 else int(v) for v in vals)
    if mode in ("L", "LA"):
        g = int(round(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]))
        return (g, 255) if mode == "LA" else g
    return rgb + (255,) if mode == "RGBA" else rgb
