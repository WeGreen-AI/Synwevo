"""Splitting one source file into several form blocks ("每块一份" sample mode).

A file that contains several filled-in forms one after another (a register with
one form per person, a scan batch with one form per page, a sheet where each
record occupies a small irregular block of rows...) is cut at every occurrence
of a *start marker* – a text that begins each block, such as 「姓名：」 or
「检测报告」.  Every block becomes a virtual source document of its own:

* spreadsheets / CSV – the rows from one marker row to the next;
* DOCX – the body paragraphs / tables from one marker to the next (a real
  DOCX containing only that part, headers and footers kept);
* PDF – from the marker's line to the next marker, on the same page or across
  pages (a real PDF made of the clipped page parts, text layer kept);
* text – the lines from one marker to the next.

Mappings are drawn on a block and address it relative to its own start, so the
same locator reads every block.  A block that lacks a field fails on its own,
with the block named in the error; nothing is guessed.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass

from . import tabular
from .limits import LIMITS
from .resolve import ResolveError, SourceDoc

MAX_BLOCKS = 5000
REGEX_TIMEOUT_S = 10
_MAX_MATCH_TEXT = 2000       # a block marker sits at the start of its line; longer lines are cut for matching
_REGEX_CHILD = r"""
import json, re, sys
req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
rx = re.compile(req["pattern"])
sys.stdout.write(json.dumps([bool(rx.search(t)) for t in req["texts"]]))
"""


@dataclass
class Block:
    index: int
    label: str           # first text of the block, for lists
    where: str           # human position ("第 3–7 行", "第 2 页 y 210–420")
    doc: SourceDoc


class _RegexMarker:
    """A user-written marker regex, evaluated in a separate process with a time limit.

    Python's ``re`` backtracks without bound and holds the GIL while it does: one pattern
    like ``(a+)+$`` against a long line stalls every thread of the server — every workspace
    — for as long as it runs.  The child process is killed at the limit instead, and all
    lines of a split are matched in one batch so the process is started once per split."""

    def __init__(self, pattern: str):
        if len(pattern) > LIMITS.max_regex_chars:
            raise ResolveError("bad_regex", f"标记的正则表达式最多 {LIMITS.max_regex_chars} 个字符")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ResolveError("bad_regex", f"标记的正则表达式无效：{e}") from e
        self.pattern = pattern
        self.cache: dict[str, bool] = {}

    def prime(self, texts) -> None:
        todo = list(dict.fromkeys((t or "")[:_MAX_MATCH_TEXT] for t in texts))
        todo = [t for t in todo if t not in self.cache]
        if not todo:
            return
        req = json.dumps({"pattern": self.pattern, "texts": todo}, ensure_ascii=False).encode("utf-8")
        try:
            proc = subprocess.run([sys.executable, "-I", "-S", "-c", _REGEX_CHILD], input=req, capture_output=True,
                                  timeout=REGEX_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise ResolveError("regex_timeout", f"标记的正则表达式在 {REGEX_TIMEOUT_S} 秒内没有完成匹配（可能存在嵌套重复等写法），"
                                                "请改用更简单的写法或「包含 / 开头是」匹配") from e
        if proc.returncode != 0:
            raise ResolveError("bad_regex", "标记的正则表达式无法执行：" + proc.stderr.decode("utf-8", "ignore")[-200:])
        for t, hit in zip(todo, json.loads(proc.stdout.decode("utf-8"))):
            self.cache[t] = bool(hit)

    def __call__(self, s) -> bool:
        t = (s or "")[:_MAX_MATCH_TEXT]
        if t not in self.cache:
            self.prime([t])
        return self.cache[t]


def _prime(m, texts) -> None:
    if isinstance(m, _RegexMarker):
        m.prime(texts)


def matcher(spec: dict):
    text = (spec.get("anchor") or "").strip()
    if not text:
        raise ResolveError("no_anchor", "请填写每个表单块开头的标记文字（例如「姓名：」或表单标题）")
    how = spec.get("match", "contains")
    if how == "regex":
        return _RegexMarker(text)
    if how == "exact":
        return lambda s: (s or "").strip() == text
    if how == "starts":
        return lambda s: (s or "").strip().startswith(text)
    return lambda s: text in (s or "")


def spec_key(spec: dict) -> str:
    return f"{spec.get('anchor')}|{spec.get('match', 'contains')}|{spec.get('sheet')}|{spec.get('end_anchor') or ''}"


def split(src: SourceDoc, spec: dict) -> list[Block]:
    """Blocks of ``src`` (cached on the document object)."""
    cache = src.__dict__.setdefault("_block_cache", {})
    key = spec_key(spec)
    if key not in cache:
        cache[key] = _split(src, spec)
    return cache[key]


def block_doc(srcs: dict[str, SourceDoc], vid: str, spec: dict) -> SourceDoc:
    """The virtual document ``<file id>~<n>`` (n is 0-based)."""
    parent_id, _, n = vid.rpartition("~")
    parent = srcs.get(parent_id)
    if parent is None:
        raise ResolveError("file_missing", "样本对应的来源文件已被移除")
    blocks = split(parent, spec)
    k = int(n)
    if k >= len(blocks):
        raise ResolveError("block_missing", f"「{parent.filename}」现在只有 {len(blocks)} 个表单块，找不到第 {k + 1} 块")
    return blocks[k].doc


def _split(src: SourceDoc, spec: dict) -> list[Block]:
    is_start = matcher(spec)
    end_m = matcher({**spec, "anchor": spec["end_anchor"]}) if spec.get("end_anchor") else None
    if src.workbook_json is not None and src.family in ("table",):
        return _split_table(src, spec, is_start, end_m)
    if src.family == "document":
        return _split_docx(src, is_start, end_m)
    if src.family == "pdf":
        return _split_pdf(src, is_start, end_m)
    if src.family == "text":
        return _split_text(src, is_start, end_m)
    raise ResolveError("cannot_split", f"「{src.filename}」（{src.format.upper()}）不能按表单块切分；图片请先转为 PDF 或按「每文件一份」处理")


def _virtual(src: SourceDoc, k: int, **kw) -> SourceDoc:
    # Only the values confirmed on *this* block (stored as "<mapping id>~<k>", see
    # resolve.correction_key).  Handing the file's corrections to every block wrote one
    # form's confirmed OCR value into every other form of the same file.
    corr = {}
    for key, c in (src.corrections or {}).items():
        mid, sep, n = key.rpartition("~")
        if sep and n == str(k):                  # exactly this block: "m~1" must not match "m~11"
            corr[mid] = c
    return SourceDoc(file_id=f"{src.file_id}~{k}", filename=f"{src.filename} · 第 {k + 1} 块", format=src.format, family=src.family,
                     corrections=corr, **kw)


def _cap(n: int, src: SourceDoc):
    if n > MAX_BLOCKS:
        raise ResolveError("too_many_blocks", f"「{src.filename}」中找到 {n} 个表单块，超过上限 {MAX_BLOCKS}，请检查标记文字是否过于宽泛")


# ------------------------------------------------------------------ tables
def _split_table(src: SourceDoc, spec: dict, is_start, end_m) -> list[Block]:
    wb = src.workbook
    sh = wb.sheet(spec["sheet"]) if spec.get("sheet") else (wb.sheets[0] if wb.sheets else None)
    if sh is None:
        raise ResolveError("sheet_missing", f"「{src.filename}」中没有工作表「{spec.get('sheet')}」")
    texts = [v.text for row in sh.rows for v in row]
    _prime(is_start, texts)
    _prime(end_m, texts)
    starts = [r for r, row in enumerate(sh.rows) if any(is_start(v.text) for v in row)]
    if not starts:
        raise ResolveError("no_blocks", f"「{src.filename}」的工作表「{sh.name}」中找不到标记「{spec['anchor']}」")
    _cap(len(starts), src)
    out = []
    for k, r0 in enumerate(starts):
        r1 = starts[k + 1] if k + 1 < len(starts) else len(sh.rows)
        if end_m:
            for r in range(r0 + 1, r1):
                if any(end_m(v.text) for v in sh.rows[r]):
                    r1 = r + 1
                    break
        rows = sh.rows[r0:r1]
        merged = [[a - r0, b, c - r0, d] for a, b, c, d in sh.merged if a >= r0 and c < r1]
        vs = tabular.Sheet(sh.name, 0, [list(x) for x in rows], merged)
        vwb = tabular.Workbook([vs], {"block_of": src.file_id, "rows": [r0, r1]})
        label = next((v.text for row in rows for v in row if v.text.strip()), "")[:60]
        out.append(Block(k, label, f"{sh.name} 第 {r0 + 1}–{r1} 行", _virtual(src, k, workbook_json=vwb.to_json())))
    return out


# ------------------------------------------------------------------ DOCX
def _split_docx(src: SourceDoc, is_start, end_m) -> list[Block]:
    from . import docx_core as dc
    from .ooxml import Package
    pkg = Package(src.data)
    main = pkg.main_part()
    body = dc.part_container(pkg, main)
    children = [c for c in body if isinstance(c.tag, str)]
    top = [c for c in children if c.tag in (dc.w("p"), dc.w("tbl"), dc.w("sdt"))]
    texts = [dc.block_text(c) for c in top]
    _prime(is_start, texts)
    _prime(end_m, texts)
    starts = [i for i, t in enumerate(texts) if is_start(t)]
    if not starts:
        raise ResolveError("no_blocks", f"「{src.filename}」中找不到标记文字")
    _cap(len(starts), src)
    out = []
    for k, i0 in enumerate(starts):
        i1 = starts[k + 1] if k + 1 < len(starts) else len(top)
        if end_m:
            for i in range(i0 + 1, i1):
                if end_m(texts[i]):
                    i1 = i + 1
                    break
        p2 = Package(src.data)
        b2 = dc.part_container(p2, main)
        top2 = [c for c in b2 if isinstance(c.tag, str) and c.tag in (dc.w("p"), dc.w("tbl"), dc.w("sdt"))]
        for idx, c in enumerate(top2):
            if not (i0 <= idx < i1):
                b2.remove(c)
        data = p2.save()
        label = next((t for t in texts[i0:i1] if t.strip()), "")[:60]
        out.append(Block(k, label, f"第 {i0 + 1}–{i1} 个段落/表格", _virtual(src, k, data=data)))
    return out


# ------------------------------------------------------------------ PDF
def _split_pdf(src: SourceDoc, is_start, end_m) -> list[Block]:
    from . import pdf_engine as pe
    if pe.ENGINE != "pymupdf":
        return _split_pdf_pages(src, is_start, end_m)
    import pymupdf
    doc = pe.open_pdf(src.data)
    marks: list[tuple[int, float, str]] = []     # (page, y in unrotated coords, line text)
    all_lines = [t for pno in range(doc.page_count) for _, t in _pdf_lines(doc[pno], (src.ocr or {}).get(str(pno)))]
    _prime(is_start, all_lines)
    _prime(end_m, all_lines)
    for pno in range(doc.page_count):
        page = doc[pno]
        lines = _pdf_lines(page, (src.ocr or {}).get(str(pno)))
        page_marks = [(pno, y, t) for y, t in lines if is_start(t)]
        if len(page_marks) > 1 and page.rotation:
            raise ResolveError("rotated_split", f"「{src.filename}」第 {pno + 1} 页是旋转页面且包含多个表单块，暂不支持在旋转页内按位置切分")
        marks.extend(page_marks)
    if not marks:
        raise ResolveError("no_blocks", f"「{src.filename}」中找不到标记文字（扫描页请先运行 OCR）")
    _cap(len(marks), src)
    out = []
    for k, (p0, y0, text) in enumerate(marks):
        p1, y1 = (marks[k + 1][0], marks[k + 1][1]) if k + 1 < len(marks) else (doc.page_count - 1, None)
        if end_m:
            for pno in range(p0, p1 + 1):
                for y, t in _pdf_lines(doc[pno], (src.ocr or {}).get(str(pno))):
                    if (pno > p0 or y > y0) and (pno < p1 or y1 is None or y < y1) and end_m(t):
                        p1, y1 = pno, y + _line_h(doc[pno], y)
                        break
                else:
                    continue
                break
        new = pymupdf.open()
        top_pad = 4.0
        for pno in range(p0, p1 + 1):
            if pno == p1 and y1 is not None and pno > p0 and y1 - top_pad <= 1:
                break                                  # next block starts at the very top of this page
            page = doc[pno]
            full = page.rect * page.derotation_matrix if page.rotation else page.rect
            full = pymupdf.Rect(full)
            full.normalize()
            a = max(full.y0, y0 - top_pad) if pno == p0 else full.y0
            b = (y1 - top_pad) if (pno == p1 and y1 is not None) else full.y1
            if b - a < 2:
                continue
            clip = pymupdf.Rect(full.x0, a, full.x1, b)
            if pno > p0 and not _has_content(page, clip, (src.ocr or {}).get(str(pno))):
                continue                               # only margin before the next block: no empty page
            # every block page keeps the original page size: the part is placed at the top, so
            # positions are measured from the block's start and all blocks share one geometry
            np_ = new.new_page(width=full.width, height=full.height)
            np_.show_pdf_page(pymupdf.Rect(0, 0, clip.width, clip.height), doc, pno, clip=clip)
            if page.rotation:
                np_.set_rotation(page.rotation)
        data = new.tobytes(garbage=3, deflate=True)
        new.close()
        where = f"第 {p0 + 1} 页" + (f"–第 {p1 + 1} 页" if p1 > p0 else "")
        out.append(Block(k, text[:60], where, _virtual(src, k, data=data, ocr=_shift_ocr(src, doc, p0, y0, p1, y1))))
    doc.close()
    return out


def _split_pdf_pages(src: SourceDoc, is_start, end_m) -> list[Block]:
    """pdfium engine: blocks made of whole pages (a marker must start each block's first
    page).  Splitting inside a page needs page-content editing, which only PyMuPDF offers."""
    from . import pdf_engine as pe
    doc = pe.open_pdf(src.data)
    starts = []
    _prime(is_start, [t for pno in range(doc.page_count) for _, t in _pdf_lines(doc[pno], (src.ocr or {}).get(str(pno)))])
    for pno in range(doc.page_count):
        lines = _pdf_lines(doc[pno], (src.ocr or {}).get(str(pno)))
        hits = [t for _, t in lines if is_start(t)]
        if len(hits) > 1:
            raise ResolveError("engine_limited", f"「{src.filename}」第 {pno + 1} 页有 {len(hits)} 个表单块；当前 pdfium 引擎只能按整页切分（页内切分需要启用 PyMuPDF）")
        if hits:
            starts.append((pno, hits[0]))
    if not starts:
        raise ResolveError("no_blocks", f"「{src.filename}」中找不到标记文字（扫描页请先运行 OCR）")
    _cap(len(starts), src)
    out = []
    for k, (p0, text) in enumerate(starts):
        p1 = starts[k + 1][0] - 1 if k + 1 < len(starts) else doc.page_count - 1
        new = pe.pymupdf.open()
        new.insert_pdf(doc, from_page=p0, to_page=p1)
        data = new.tobytes()
        new.close()
        ocr = {str(i - p0): src.ocr[str(i)] for i in range(p0, p1 + 1) if src.ocr and str(i) in src.ocr} or None
        out.append(Block(k, text[:60], f"第 {p0 + 1} 页" + (f"–第 {p1 + 1} 页" if p1 > p0 else ""), _virtual(src, k, data=data, ocr=ocr)))
    doc.close()
    return out


def _pdf_lines(page, ocr: dict | None) -> list[tuple[float, str]]:
    """(top y in unrotated coords, text) per text line; OCR words for pages without a text layer."""
    from . import pdf_engine as pe
    words = page.get_text("words")
    if not words and ocr:
        pymupdf = pe.pymupdf
        W, H = page.rect.width, page.rect.height
        words = []
        for i, w in enumerate(ocr.get("words", [])):
            r = pymupdf.Rect(w["r"][0] * W, w["r"][1] * H, w["r"][2] * W, w["r"][3] * H) * page.derotation_matrix
            r.normalize()
            ln = w.get("line", "0.0.0").split(".")
            words.append((r.x0, r.y0, r.x1, r.y1, w["t"], int(ln[0]), int(ln[-1]), i))
    by: dict[tuple, list] = {}
    for w in words:
        by.setdefault((w[5], w[6]), []).append(w)
    out = []
    for ws in by.values():
        ws.sort(key=lambda w: w[0])
        out.append((min(w[1] for w in ws), pe.join_words(ws)))
    out.sort()
    return out


def _has_content(page, clip, ocr: dict | None) -> bool:
    import pymupdf
    if page.get_text("words", clip=clip):
        return True
    if ocr:
        H = page.rect.height
        if any(clip.y0 <= (w["r"][1] + w["r"][3]) / 2 * H <= clip.y1 for w in ocr.get("words", [])):
            return True
    try:
        for d in page.get_drawings():
            if pymupdf.Rect(d["rect"]).intersects(clip):
                return True
        for info in page.get_image_info():
            if pymupdf.Rect(info["bbox"]).intersects(clip):
                return True
    except Exception:  # noqa: BLE001
        return True
    return False


def _line_h(page, y: float) -> float:
    for w in page.get_text("words"):
        if abs(w[1] - y) < 1:
            return w[3] - w[1] + 2
    return 14.0


def _shift_ocr(src: SourceDoc, doc, p0: int, y0: float, p1: int, y1: float | None) -> dict | None:
    """OCR words of scanned pages, re-expressed on the block's own pages."""
    if not src.ocr:
        return None
    out = {}
    k = 0
    for pno in range(p0, p1 + 1):
        o = src.ocr.get(str(pno))
        page = doc[pno]
        H = page.rect.height
        a = max(0.0, y0 - 4) if pno == p0 else 0.0
        b = (y1 - 4) if (pno == p1 and y1 is not None) else H
        if b - a < 2:
            continue
        if o:
            words = []
            for w in o["words"]:
                cy = (w["r"][1] + w["r"][3]) / 2 * H
                if a <= cy <= b:
                    words.append({**w, "r": [w["r"][0], (w["r"][1] * H - a) / H, w["r"][2], (w["r"][3] * H - a) / H]})
            out[str(k)] = {**o, "words": words}
        k += 1
    return out or None


# ------------------------------------------------------------------ text
def _split_text(src: SourceDoc, is_start, end_m) -> list[Block]:
    lines = src.text.split("\n")
    _prime(is_start, lines)
    _prime(end_m, lines)
    starts = [i for i, t in enumerate(lines) if is_start(t)]
    if not starts:
        raise ResolveError("no_blocks", f"「{src.filename}」中找不到标记文字")
    _cap(len(starts), src)
    out = []
    for k, i0 in enumerate(starts):
        i1 = starts[k + 1] if k + 1 < len(starts) else len(lines)
        if end_m:
            for i in range(i0 + 1, i1):
                if end_m(lines[i]):
                    i1 = i + 1
                    break
        d = _virtual(src, k, data=None)
        d._text = "\n".join(lines[i0:i1])
        out.append(Block(k, lines[i0].strip()[:60], f"第 {i0 + 1}–{i1} 行", d))
    return out
