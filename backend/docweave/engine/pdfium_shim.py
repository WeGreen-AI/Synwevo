"""A small, read-only PyMuPDF-compatible surface on top of pypdfium2 (Apache-2.0 / BSD-3).

Selected with ``DOCWEAVE_PDF_ENGINE=pdfium`` (or automatically when PyMuPDF is not
installed) so DocWeave can be deployed without the AGPL-licensed PyMuPDF.  It covers
what *reading* PDFs needs – page geometry and rotation, words with positions, plain
text, image placement, rendering, page import – with PyMuPDF's conventions:

* unrotated page space, origin at the top-left of the CropBox, y growing downwards;
* ``page.rect`` is the page as displayed (rotated); ``rotation_matrix`` maps
  unrotated → displayed coordinates exactly like PyMuPDF's;
* ``get_text("words")`` yields ``(x0, y0, x1, y1, text, block, line, word)`` with
  line-height ("loose") boxes.

Writing PDFs (redaction, text insertion, form flattening) is *not* offered: PDF and
image templates are unavailable in this mode and say so in the capability matrix.
"""
from __future__ import annotations

import atexit
import io
import math
import weakref
from contextlib import contextmanager

import pypdfium2 as pdfium
import pypdfium2.raw as raw

_LIVE: "weakref.WeakSet[Document]" = weakref.WeakSet()


@atexit.register
def _close_all():          # cached documents: close them before PDFium shuts down
    for d in list(_LIVE):
        d.close()


ENGINE_NAME = "pdfium"


class _Unsupported(RuntimeError):
    pass


# ------------------------------------------------------------------ geometry
class Point:
    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=None):
        if y is None:
            x, y = x
        self.x, self.y = float(x), float(y)

    def __mul__(self, m: "Matrix") -> "Point":
        return Point(self.x * m.a + self.y * m.c + m.e, self.x * m.b + self.y * m.d + m.f)

    def __iter__(self):
        return iter((self.x, self.y))


class Matrix:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, *args):
        if len(args) == 6:
            self.a, self.b, self.c, self.d, self.e, self.f = (float(v) for v in args)
        elif len(args) == 2:
            self.a, self.b, self.c, self.d, self.e, self.f = float(args[0]), 0.0, 0.0, float(args[1]), 0.0, 0.0
        elif len(args) == 1:
            deg = float(args[0]) % 360
            cs = {0: (1, 0), 90: (0, 1), 180: (-1, 0), 270: (0, -1)}.get(deg)
            c, s = cs if cs else (math.cos(math.radians(deg)), math.sin(math.radians(deg)))
            self.a, self.b, self.c, self.d, self.e, self.f = float(c), float(s), float(-s), float(c), 0.0, 0.0
        else:
            self.a, self.b, self.c, self.d, self.e, self.f = 1.0, 0.0, 0.0, 1.0, 0.0, 0.0

    def __invert__(self) -> "Matrix":
        det = self.a * self.d - self.b * self.c
        if not det:
            return Matrix()
        a, b, c, d = self.d / det, -self.b / det, -self.c / det, self.a / det
        return Matrix(a, b, c, d, -(self.e * a + self.f * c), -(self.e * b + self.f * d))

    def __repr__(self):
        return f"Matrix({self.a}, {self.b}, {self.c}, {self.d}, {self.e}, {self.f})"


Identity = Matrix()


class Rect:
    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, *args):
        if len(args) == 1:
            a = args[0]
            x0, y0, x1, y1 = (a.x0, a.y0, a.x1, a.y1) if isinstance(a, Rect) else tuple(a)[:4]
        elif len(args) == 2:
            (x0, y0), (x1, y1) = args
        elif len(args) == 4:
            x0, y0, x1, y1 = args
        else:
            x0 = y0 = x1 = y1 = 0.0
        self.x0, self.y0, self.x1, self.y1 = float(x0), float(y0), float(x1), float(y1)

    width = property(lambda s: abs(s.x1 - s.x0))
    height = property(lambda s: abs(s.y1 - s.y0))
    tl = property(lambda s: Point(s.x0, s.y0))
    tr = property(lambda s: Point(s.x1, s.y0))
    bl = property(lambda s: Point(s.x0, s.y1))
    br = property(lambda s: Point(s.x1, s.y1))

    @property
    def is_empty(self) -> bool:
        return self.x1 <= self.x0 or self.y1 <= self.y0

    def normalize(self) -> "Rect":
        self.x0, self.x1 = min(self.x0, self.x1), max(self.x0, self.x1)
        self.y0, self.y1 = min(self.y0, self.y1), max(self.y0, self.y1)
        return self

    def get_area(self) -> float:
        return 0.0 if self.is_empty else self.width * self.height

    def __abs__(self):
        return self.get_area()

    def contains(self, o) -> bool:
        if isinstance(o, Point):
            return self.x0 <= o.x <= self.x1 and self.y0 <= o.y <= self.y1
        r = Rect(o)
        return self.x0 <= r.x0 and self.y0 <= r.y0 and r.x1 <= self.x1 and r.y1 <= self.y1

    def intersects(self, o) -> bool:
        r = Rect(o)
        return self.x0 < r.x1 and r.x0 < self.x1 and self.y0 < r.y1 and r.y0 < self.y1

    def __and__(self, o) -> "Rect":
        r = Rect(o)
        out = Rect(max(self.x0, r.x0), max(self.y0, r.y0), min(self.x1, r.x1), min(self.y1, r.y1))
        return out if not out.is_empty else Rect(0, 0, 0, 0)

    def __or__(self, o) -> "Rect":
        r = Rect(o)
        if self.is_empty:
            return Rect(r)
        return Rect(min(self.x0, r.x0), min(self.y0, r.y0), max(self.x1, r.x1), max(self.y1, r.y1))

    def __mul__(self, m: Matrix) -> "Rect":
        pts = [Point(self.x0, self.y0) * m, Point(self.x1, self.y0) * m, Point(self.x0, self.y1) * m, Point(self.x1, self.y1) * m]
        return Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))

    def __getitem__(self, i):
        return (self.x0, self.y0, self.x1, self.y1)[i]

    def __len__(self):
        return 4

    def __repr__(self):
        return f"Rect({self.x0}, {self.y0}, {self.x1}, {self.y1})"


# ------------------------------------------------------------------ pixmap
class Pixmap:
    def __init__(self, pil_image):
        self._im = pil_image
        self.width, self.height = pil_image.size
        self.alpha = pil_image.mode == "RGBA"
        self.samples = pil_image.tobytes()

    def tobytes(self, fmt: str = "png") -> bytes:
        buf = io.BytesIO()
        self._im.save(buf, "PNG")
        return buf.getvalue()


# ------------------------------------------------------------------ document / page
class Page:
    def __init__(self, doc: "Document", index: int):
        self.parent = doc
        self.number = index
        with self._page() as p:
            l, b, r, t = p.get_cropbox()
            rot = p.get_rotation()
        self._crop = (l, b, r, t)
        w, h = r - l, t - b
        self.mediabox = Rect(0, 0, w, h)
        self.cropbox = Rect(0, 0, w, h)
        self.rotation = int(rot or 0) % 360
        self.rect = Rect(0, 0, h, w) if self.rotation in (90, 270) else Rect(0, 0, w, h)
        self._words = None
        self._text = None

    # PyMuPDF's matrices: unrotated → displayed and back
    def _page(self):
        return _opened(self.parent._pdf[self.number])

    @property
    def rotation_matrix(self) -> Matrix:
        w, h = self.mediabox.width, self.mediabox.height
        return {0: Matrix(), 90: Matrix(0, 1, -1, 0, h, 0), 180: Matrix(-1, 0, 0, -1, w, h), 270: Matrix(0, -1, 1, 0, 0, w)}[self.rotation]

    @property
    def derotation_matrix(self) -> Matrix:
        return ~self.rotation_matrix

    def _to_top(self, box) -> tuple[float, float, float, float]:
        l0, b0, r0, t0 = self._crop
        left, bottom, right, top = box
        return (left - l0, t0 - top, right - l0, t0 - bottom)

    def _build(self):
        with self._page() as page:
            self._build_from(page)

    def _build_from(self, page):
        tp = page.get_textpage()
        n = tp.count_chars()
        words, cur = [], None
        line = word = 0
        last_base = None
        text_all = []
        for i in range(n):
            ch = tp.get_text_range(i, 1)
            generated = bool(raw.FPDFText_IsGenerated(tp.raw, i))
            text_all.append(ch)
            if ch in ("\r", "\n") or (generated and ch in (" ", "\r", "\n")) or ch.isspace():
                if cur:
                    words.append(cur)
                    cur = None
                    word += 1
                if ch == "\n":
                    line, word, last_base = line + 1, 0, None
                continue
            x0, y0, x1, y1 = self._to_top(tp.get_charbox(i, loose=True))
            base = y1
            if cur is not None and last_base is not None and abs(base - last_base) > max(2.0, (y1 - y0) * 0.6):
                words.append(cur)
                cur = None
                line, word = line + 1, 0
            if cur is not None and x0 - cur[2] > max(1.5, (y1 - y0) * 0.35):
                words.append(cur)                   # wide gap without a space character: separate words
                cur = None
                word += 1
            if cur is None:
                cur = [x0, y0, x1, y1, ch, 0, line, word]
            else:
                cur[0], cur[1], cur[2], cur[3] = min(cur[0], x0), min(cur[1], y0), max(cur[2], x1), max(cur[3], y1)
                cur[4] += ch
            last_base = base
        if cur:
            words.append(cur)
        self._words = [tuple(w) for w in words]
        self._text = "".join(text_all).replace("\r\n", "\n").replace("\r", "\n")
        tp.close()

    def get_text(self, kind: str = "text", clip=None):
        if self._words is None:
            self._build()
        if kind == "words":
            if clip is None:
                return list(self._words)
            c = Rect(clip)
            return [w for w in self._words if Rect(w[:4]).intersects(c)]
        if kind == "text":
            return self._text
        if kind == "dict":
            raise _Unsupported("pdfium 引擎不提供文字样式信息")
        raise _Unsupported(f"不支持的文字提取方式：{kind}")

    def _image_boxes(self):
        with self._page() as page:
            return [self._to_top(o.get_bounds()) for o in page.get_objects(filter=[raw.FPDF_PAGEOBJ_IMAGE], max_depth=4)]

    def get_images(self, full: bool = False):
        return [(i,) for i, _ in enumerate(self._image_boxes())]

    def get_image_info(self):
        return [{"bbox": b} for b in self._image_boxes()]

    def get_drawings(self):
        return []           # vector rules are only needed by template features

    def widgets(self):
        return []

    def get_pixmap(self, matrix: Matrix | None = None, alpha: bool = False, clip=None, dpi=None, colorspace=None):
        scale = (dpi / 72.0) if dpi else (matrix.a if matrix is not None else 1.0)
        with self._page() as page:
            bm = page.render(scale=max(0.05, abs(scale)), may_draw_forms=True, fill_color=(255, 255, 255, 0 if alpha else 255))
            im = bm.to_pil().copy()
            bm.close()
        im = im.convert("RGBA" if alpha else "RGB")
        if clip is not None:
            r = Rect(clip) * self.rotation_matrix
            im = im.crop((int(r.x0 * scale), int(r.y0 * scale), int(math.ceil(r.x1 * scale)), int(math.ceil(r.y1 * scale))))
        return Pixmap(im)


@contextmanager
def _opened(page):
    try:
        yield page
    finally:
        page.close()


class Document:
    def __init__(self, stream: bytes | None = None, filetype: str | None = None):
        _LIVE.add(self)
        self._closed = False
        self.needs_pass = False
        if stream is None:
            self._pdf = pdfium.PdfDocument.new()
        else:
            try:
                self._pdf = pdfium.PdfDocument(bytes(stream))
            except pdfium.PdfiumError as e:
                if raw.FPDF_GetLastError() == raw.FPDF_ERR_PASSWORD or "password" in str(e).lower():
                    self.needs_pass = True
                    self._pdf = pdfium.PdfDocument.new()
                    return
                raise
        self._pages: dict[int, Page] = {}

    @property
    def page_count(self) -> int:
        return len(self._pdf)

    def __len__(self):
        return self.page_count

    def __getitem__(self, i: int) -> Page:
        if i < 0:
            i += self.page_count
        if not 0 <= i < self.page_count:
            raise IndexError("page out of range")
        if i not in self._pages:
            self._pages[i] = Page(self, i)
        return self._pages[i]

    def __iter__(self):
        for i in range(self.page_count):
            yield self[i]

    def insert_pdf(self, src: "Document", from_page: int = 0, to_page: int = -1):
        to_page = src.page_count - 1 if to_page < 0 else to_page
        self._pdf.import_pages(src._pdf, list(range(from_page, to_page + 1)))
        self._pages.clear()

    def tobytes(self, **_kw) -> bytes:
        buf = io.BytesIO()
        self._pdf.save(buf)
        return buf.getvalue()

    def signature_count(self) -> int:
        try:
            return int(raw.FPDF_GetSignatureCount(self._pdf.raw))
        except Exception:  # noqa: BLE001
            return 0

    def javascript_count(self) -> int:
        try:
            return int(raw.FPDFDoc_GetJavaScriptActionCount(self._pdf.raw))
        except Exception:  # noqa: BLE001
            return 0

    def close(self):
        if self._closed:
            return
        self._closed = True
        if hasattr(self, "_pages"):
            self._pages.clear()
        try:
            self._pdf.close()
        except Exception:  # noqa: BLE001
            pass


def open(stream: bytes | None = None, filetype: str | None = None) -> Document:  # noqa: A001 - PyMuPDF's name
    return Document(stream=stream, filetype=filetype)
