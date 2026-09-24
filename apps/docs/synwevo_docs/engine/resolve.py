"""Resolve source locators against a concrete source file (and sample context).

Locators never store screen coordinates or DOM indices.  Each resolver checks
that the located content still matches the recorded anchors; when it does not,
it raises :class:`ResolveError` with a specific code instead of silently picking
nearby content.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from . import tabular
from . import values as V


class ResolveError(ValueError):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


@dataclass
class SourceDoc:
    file_id: str
    filename: str
    format: str
    family: str
    data: bytes | None = None                 # working bytes (converted OOXML for legacy formats)
    workbook_json: dict | None = None
    ocr: dict | None = None                   # {page_index: {"words": [...], "w":..., "h":...}}
    corrections: dict = field(default_factory=dict)  # {mapping_id: {"value": str, "confirmed": bool}}
    _wb: tabular.Workbook | None = None
    _pkg: object | None = None
    _pdf: object | None = None
    _text: str | None = None

    @property
    def workbook(self) -> tabular.Workbook:
        if self._wb is None:
            if self.workbook_json is None:
                raise ResolveError("not_table", f"「{self.filename}」不是表格文件")
            self._wb = tabular.Workbook.from_json(self.workbook_json)
        return self._wb

    @property
    def pkg(self):
        if self._pkg is None:
            from .ooxml import Package
            self._pkg = Package(self.data)
        return self._pkg

    @property
    def pdf(self):
        if self._pdf is None:
            from .pdf_engine import open_pdf
            self._pdf = open_pdf(self.data)
        return self._pdf

    @property
    def text(self) -> str:
        if self._text is None:
            from .sources import text_content
            self._text = text_content(self.data, self.format)
        return self._text


# ------------------------------------------------------------------ sheets & tables
def resolve_sheet(src: SourceDoc, spec: dict) -> tabular.Sheet:
    wb = src.workbook
    name = spec.get("sheet")
    if name is None and spec.get("sheet_index") is not None:
        sh = wb.sheet(index=int(spec["sheet_index"]))
        if sh is None:
            raise ResolveError("missing_sheet", f"「{src.filename}」没有第 {int(spec['sheet_index']) + 1} 个工作表")
        return sh
    if len(wb.sheets) == 1 and src.family == "table" and src.format in ("csv", "tsv"):
        return wb.sheets[0]
    sh = wb.sheet(name=name)
    if sh is not None:
        return sh
    norm = tabular.normalize_header(name or "").lower()
    cands = [s for s in wb.sheets if tabular.normalize_header(s.name).lower() == norm]
    if len(cands) == 1:
        return cands[0]
    if len(cands) > 1:
        raise ResolveError("ambiguous_sheet", f"「{src.filename}」中有 {len(cands)} 个工作表名称与「{name}」相近，无法确定使用哪一个",
                           {"candidates": [c.name for c in cands]})
    if spec.get("sheet_fallback") == "index" and spec.get("sheet_index") is not None:
        sh = wb.sheet(index=int(spec["sheet_index"]))
        if sh is not None:
            return sh
    raise ResolveError("missing_sheet", f"「{src.filename}」中找不到工作表「{name}」", {"available": [s.name for s in wb.sheets]})


def table_view(src: SourceDoc, spec: dict) -> tabular.TableView:
    sh = resolve_sheet(src, spec)
    hr = spec.get("header_row", 0)
    return tabular.build_table_view(sh, hr if hr is not None and hr >= 0 else None, spec.get("data_start"),
                                    spec.get("data_end"), spec.get("skip_blank", True), spec.get("filters"),
                                    spec.get("first_col", 0) or 0, spec.get("last_col"))


def column_index(tv: tabular.TableView, loc: dict, src: SourceDoc) -> int:
    try:
        return tabular.resolve_column(tv, loc)
    except tabular.ColumnError as e:
        raise ResolveError(e.code, f"「{src.filename}」：{e}", {"candidates": e.candidates}) from e


def _with_src(v: V.Value, src: SourceDoc, **kw) -> V.Value:
    v = V.Value(v.t, v.v, v.text, v.fmt, {"file_id": src.file_id, "file": src.filename, **kw}, v.style)
    return v


# ------------------------------------------------------------------ scalar extraction
def extract_scalar(src: SourceDoc, loc: dict, ctx: dict) -> V.Value:
    kind = loc.get("kind")
    if kind == "constant":
        return V.text(str(loc.get("value", "")))
    if kind == "record_meta":
        return _record_meta(src, loc, ctx)
    if kind == "column":
        if ctx.get("mode") != "per_row":
            raise ResolveError("mode_mismatch", "列来源只能在「每行一份」中作为单值使用；分组模式由系统按组汇总")
        tv = ctx["table_view"]
        c = column_index(tv, loc, src)
        r = ctx["row"]
        return _with_src(tv.sheet.cell(r, c), src, sheet=tv.sheet.name, cell=tabular.cell_ref(r, c))
    if kind == "cell":
        sh = resolve_sheet(src, loc)
        r, c = tabular.parse_cell(loc["cell"])
        anchor = loc.get("anchor")
        if anchor:
            r, c = _check_cell_anchor(src, sh, r, c, anchor)
        return _with_src(sh.cell(r, c), src, sheet=sh.name, cell=tabular.cell_ref(r, c))
    if kind == "docx_text":
        return _docx_text(src, loc)
    if kind == "docx_cell":
        return _docx_cell(src, loc)
    if kind in ("pdf_region", "ocr_region"):
        return _pdf_region(src, loc, ctx)
    if kind == "text_span":
        return _text_span(src, loc)
    if kind == "pptx_text":
        from .pptx_engine import extract_text as pptx_extract
        return _with_src(V.text(pptx_extract(src.pkg, loc)), src, slide=loc.get("slide"))
    raise ResolveError("bad_locator", f"不支持的来源定位类型：{kind}")


def _record_meta(src: SourceDoc, loc: dict, ctx: dict) -> V.Value:
    f = loc.get("field")
    if f == "file_name":
        return V.text(src.filename.rsplit(".", 1)[0])
    if f == "record_index":
        n = str(ctx.get("index", 0) + 1)
        return V.Value(V.INT, n, n)
    if f == "group_key":
        return V.text(ctx.get("group_label", ""))
    if f == "sheet":
        return V.text(ctx.get("sheet", ""))
    raise ResolveError("bad_locator", f"未知的样本信息字段：{f}")


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or ""))


def _check_cell_anchor(src: SourceDoc, sh: tabular.Sheet, r: int, c: int, anchor: dict) -> tuple[int, int]:
    """Verify the label next to a fixed cell; if the label moved, follow it uniquely."""
    label = _norm(anchor.get("text", ""))
    dr, dc = anchor.get("dr", 0), anchor.get("dc", -1)  # label offset relative to the value cell
    if _norm(sh.cell(r + dr, c + dc).text) == label:
        return r, c
    hits = [(rr, cc) for rr, row in enumerate(sh.rows) for cc, v in enumerate(row) if v.text and _norm(v.text) == label]
    if len(hits) == 1:
        return hits[0][0] - dr, hits[0][1] - dc
    if not hits:
        raise ResolveError("anchor_missing", f"「{src.filename}」[{sh.name}] 找不到标签「{anchor.get('text')}」，原位置 {tabular.cell_ref(r, c)} 旁的内容已变化")
    raise ResolveError("anchor_ambiguous", f"「{src.filename}」[{sh.name}] 标签「{anchor.get('text')}」出现 {len(hits)} 次，无法确定取值位置",
                       {"candidates": [tabular.cell_ref(a, b) for a, b in hits[:10]]})


# ------------------------------------------------------------------ DOCX
def _docx_container(src: SourceDoc, part: str | None):
    from .docx_core import part_container
    pkg = src.pkg
    part = part or pkg.main_part()
    if not pkg.has(part):
        # header/footer part names can differ between files: fall back by kind order
        raise ResolveError("part_missing", f"「{src.filename}」中没有对应的页眉/页脚部件")
    return part_container(pkg, part)


def _docx_text(src: SourceDoc, loc: dict) -> V.Value:
    from .docx_core import DocxError, get_block, iter_blocks, para_text, w
    container = _docx_container(src, loc.get("part"))
    prefix = loc.get("prefix", "")
    suffix = loc.get("suffix", "")
    pre_anchor = prefix[-24:].lstrip() if prefix else ""
    suf_anchor = suffix[:24].rstrip() if suffix else ""

    def extract_from(text: str) -> list[str]:
        out = []
        if pre_anchor:
            start_positions = [m.end() for m in re.finditer(re.escape(pre_anchor), text)]
        else:
            start_positions = [0]
        for sp in start_positions:
            if suf_anchor:
                ep = text.find(suf_anchor, sp)
                if ep < 0:
                    continue
                out.append(text[sp:ep])
            else:
                out.append(text[sp:])
        return out

    # 1) same structural path, validated by anchors
    try:
        el = get_block(container, loc["path"])
        if el.tag == w("p"):
            text = para_text(el)
            if not pre_anchor and not suf_anchor:
                ctx_before = loc.get("context_before")
                if ctx_before is None or _prev_para_text(container, loc["path"]) == ctx_before:
                    return _with_src(V.text(text), src, path=loc["path"])
            else:
                found = extract_from(text)
                if len(found) == 1:
                    return _with_src(V.text(found[0]), src, path=loc["path"])
                if len(found) > 1:
                    raise ResolveError("anchor_ambiguous", f"「{src.filename}」同一段落中标签「{pre_anchor}」出现多次")
    except DocxError:
        pass
    # 2) search every paragraph for the anchors
    if not pre_anchor and not suf_anchor:
        raise ResolveError("anchor_missing", f"「{src.filename}」文档结构与代表文件不同，无法定位整段内容（位置 {loc['path']}）")
    hits = []
    for p in container.iter(w("p")):
        text = para_text(p)
        for f in extract_from(text):
            hits.append(f)
    if len(hits) == 1:
        return _with_src(V.text(hits[0]), src, anchor=pre_anchor or suf_anchor)
    if not hits:
        raise ResolveError("anchor_missing", f"「{src.filename}」中找不到标签「{pre_anchor or suf_anchor}」")
    raise ResolveError("anchor_ambiguous", f"「{src.filename}」中标签「{pre_anchor or suf_anchor}」出现 {len(hits)} 次，无法确定取值位置",
                       {"candidates": hits[:10]})


def _prev_para_text(container, path: str) -> str | None:
    from .docx_core import get_block, iter_blocks, para_text, w
    parts = path.split(":")
    if len(parts) != 1:
        return None
    idx = int(parts[0])
    blocks = list(iter_blocks(container))
    j = idx - 1
    while j >= 0:
        if blocks[j].tag == w("p") and para_text(blocks[j]).strip():
            return para_text(blocks[j])
        j -= 1
    return ""


def _docx_cell(src: SourceDoc, loc: dict) -> V.Value:
    from .docx_core import DocxError, cell_text, get_table_cell, iter_blocks, iter_cells, iter_rows, w
    container = _docx_container(src, loc.get("part"))
    label = loc.get("label")  # {"text": "...", "where": "left"|"above"}
    try:
        tc = get_table_cell(container, loc["table_path"], loc["row"], loc["col"])
        if not label or _label_matches(container, loc["table_path"], loc["row"], loc["col"], label):
            return _with_src(V.text(cell_text(tc)), src, table=loc["table_path"], row=loc["row"], col=loc["col"])
    except DocxError:
        pass
    if not label:
        raise ResolveError("path_missing", f"「{src.filename}」中找不到对应表格单元格（表格 {loc['table_path']} 第 {loc['row'] + 1} 行第 {loc['col'] + 1} 列）")
    target = _norm(label["text"])
    hits = []
    for tbl in container.iter(w("tbl")):
        rows = list(iter_rows(tbl))
        grid = [list(iter_cells(tr)) for tr in rows]
        for ri, cells in enumerate(grid):
            for ci, tc in enumerate(cells):
                if _norm(cell_text(tc)) != target:
                    continue
                if label.get("where", "left") == "left" and ci + 1 < len(cells):
                    hits.append(cell_text(cells[ci + 1]))
                elif label.get("where") == "above" and ri + 1 < len(grid) and ci < len(grid[ri + 1]):
                    hits.append(cell_text(grid[ri + 1][ci]))
    if len(hits) == 1:
        return _with_src(V.text(hits[0]), src, label=label["text"])
    if not hits:
        raise ResolveError("anchor_missing", f"「{src.filename}」中找不到表格标签「{label['text']}」")
    raise ResolveError("anchor_ambiguous", f"「{src.filename}」中表格标签「{label['text']}」出现 {len(hits)} 次")


def _label_matches(container, table_path, r, c, label) -> bool:
    from .docx_core import DocxError, cell_text, get_table_cell
    try:
        if label.get("where", "left") == "left":
            return c > 0 and _norm(cell_text(get_table_cell(container, table_path, r, c - 1))) == _norm(label["text"])
        return r > 0 and _norm(cell_text(get_table_cell(container, table_path, r - 1, c))) == _norm(label["text"])
    except DocxError:
        return False


def docx_table_rows(src: SourceDoc, loc: dict) -> tuple[list[str], list[list[V.Value]]]:
    from .docx_core import DocxError, cell_text, get_block, iter_cells, iter_rows, w
    container = _docx_container(src, loc.get("part"))
    try:
        tbl = get_block(container, loc["table_path"])
    except DocxError as e:
        tbl = None
    header_rows = int(loc.get("header_rows", 1))
    want = [_norm(h) for h in loc.get("headers", [])]
    if tbl is None or tbl.tag != w("tbl") or (want and _docx_headers(tbl, header_rows) != want):
        # search by header signature
        cands = [t for t in container.iter(w("tbl")) if want and _docx_headers(t, header_rows) == want]
        if len(cands) == 1:
            tbl = cands[0]
        elif not cands:
            raise ResolveError("anchor_missing", f"「{src.filename}」中找不到表头为「{' | '.join(loc.get('headers', []))}」的表格")
        else:
            raise ResolveError("anchor_ambiguous", f"「{src.filename}」中有 {len(cands)} 个表头相同的表格")
    rows = [list(iter_cells(tr)) for tr in iter_rows(tbl)]
    headers = [cell_text(tc) for tc in rows[header_rows - 1]] if header_rows and rows else []
    data = []
    for cells in rows[header_rows:]:
        vals = [V.text(cell_text(tc)) if cell_text(tc) else V.empty() for tc in cells]
        if loc.get("skip_blank", True) and all(v.is_empty for v in vals):
            continue
        data.append(vals)
    return headers, data


def _docx_headers(tbl, header_rows: int) -> list[str]:
    from .docx_core import cell_text, iter_cells, iter_rows
    rows = list(iter_rows(tbl))
    if not header_rows or len(rows) < header_rows:
        return []
    return [_norm(cell_text(tc)) for tc in iter_cells(rows[header_rows - 1])]


# ------------------------------------------------------------------ PDF / OCR
def _pdf_region(src: SourceDoc, loc: dict, ctx: dict) -> V.Value:
    mid = ctx.get("mapping_id")
    corr = src.corrections.get(mid) if mid else None
    if corr is not None and corr.get("value") is not None:
        v = V.text(corr["value"])
        return _with_src(v, src, page=loc.get("page"), corrected=True)
    if loc.get("kind") == "ocr_region" or src.family == "image":
        return _ocr_region(src, loc)
    from . import pdf_engine
    doc = src.pdf
    pno = loc["page"]
    if pno >= doc.page_count:
        raise ResolveError("page_missing", f"「{src.filename}」只有 {doc.page_count} 页，没有第 {pno + 1} 页")
    page = doc[pno]
    if pdf_engine.page_kind(page) == "scanned":
        if src.ocr and str(pno) in src.ocr:
            return _ocr_region(src, loc)
        raise ResolveError("needs_ocr", f"「{src.filename}」第 {pno + 1} 页是扫描页，需要先进行 OCR 识别")
    rect, err = pdf_engine.locate_with_anchor(page, loc)
    if err:
        raise ResolveError("layout_mismatch", f"「{src.filename}」第 {pno + 1} 页版式验证失败：{err}")
    text = pdf_engine.join_words(pdf_engine.words_in_rect(page, rect))
    return _with_src(V.text(text) if text else V.empty(), src, page=pno, rect=list(rect))


def _ocr_region(src: SourceDoc, loc: dict) -> V.Value:
    pno = loc.get("page", 0)
    data = (src.ocr or {}).get(str(pno))
    if not data:
        raise ResolveError("needs_ocr", f"「{src.filename}」第 {pno + 1} 页尚未进行 OCR 识别")
    x0, y0, x1, y1 = loc["nrect"]
    words = []
    for w in data["words"]:
        r = w["r"]
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            words.append(w)
    from .ocr import join_ocr_words
    text = join_ocr_words(words)
    conf = min((w.get("conf", 100) for w in words), default=100)
    v = V.text(text) if text else V.empty()
    v = _with_src(v, src, page=pno, ocr=True, conf=conf)
    threshold = loc.get("min_conf", 80)
    if words and conf < threshold:
        raise ResolveError("ocr_low_confidence", f"「{src.filename}」第 {pno + 1} 页识别结果「{text}」置信度 {conf:.0f} 低于 {threshold}，需要人工确认",
                           {"value": text, "conf": conf})
    return v


# ------------------------------------------------------------------ plain text
def _text_span(src: SourceDoc, loc: dict) -> V.Value:
    lines = src.text.split("\n")
    li = loc.get("line", 0)
    prefix, suffix = loc.get("prefix", ""), loc.get("suffix", "")
    pre = prefix[-24:].lstrip()
    suf = suffix[:24].rstrip()

    def ext(line: str):
        out = []
        starts = [m.end() for m in re.finditer(re.escape(pre), line)] if pre else [0]
        for sp in starts:
            if suf:
                ep = line.find(suf, sp)
                if ep >= 0:
                    out.append(line[sp:ep])
            else:
                out.append(line[sp:])
        return out

    if 0 <= li < len(lines):
        f = ext(lines[li])
        if len(f) == 1 and (pre or suf or lines[li] == loc.get("sample")):
            return _with_src(V.text(f[0]), src, line=li)
    if not pre and not suf:
        raise ResolveError("anchor_missing", f"「{src.filename}」第 {li + 1} 行内容已变化，且没有可用的定位标签")
    hits = [x for ln in lines for x in ext(ln)]
    if len(hits) == 1:
        return _with_src(V.text(hits[0]), src)
    if not hits:
        raise ResolveError("anchor_missing", f"「{src.filename}」中找不到标签「{pre or suf}」")
    raise ResolveError("anchor_ambiguous", f"「{src.filename}」中标签「{pre or suf}」出现 {len(hits)} 次")


# ------------------------------------------------------------------ blocks (regions) and detail rows
def extract_matrix(src: SourceDoc, loc: dict, ctx: dict) -> list[list[V.Value]]:
    kind = loc.get("kind")
    if kind == "range":
        sh = resolve_sheet(src, loc)
        r1, c1, r2, c2 = tabular.parse_range(loc["range"])
        if loc.get("anchor"):
            ar, ac = _check_cell_anchor(src, sh, r1, c1, loc["anchor"])
            r2, c2 = r2 + (ar - r1), c2 + (ac - c1)
            r1, c1 = ar, ac
        return [[_with_src(sh.cell(r, c), src, sheet=sh.name, cell=tabular.cell_ref(r, c)) for c in range(c1, c2 + 1)]
                for r in range(r1, r2 + 1)]
    if kind == "docx_table_block":
        from .docx_core import cell_text, get_block, iter_cells, iter_rows
        container = _docx_container(src, loc.get("part"))
        tbl = get_block(container, loc["table_path"])
        rows = [list(iter_cells(tr)) for tr in iter_rows(tbl)]
        out = []
        for r in range(loc["r1"], loc["r2"] + 1):
            if r >= len(rows):
                raise ResolveError("path_missing", f"「{src.filename}」表格行数不足")
            row = []
            for c in range(loc["c1"], loc["c2"] + 1):
                if c >= len(rows[r]):
                    raise ResolveError("path_missing", f"「{src.filename}」表格列数不足")
                row.append(V.text(cell_text(rows[r][c])))
            out.append(row)
        return out
    raise ResolveError("bad_locator", f"「{kind}」不能作为固定区域来源")


def extract_detail(src: SourceDoc, loc: dict, ctx: dict) -> tuple[list[str], list[dict[str, V.Value]]]:
    """Rows for repeat mappings.  Returns (header names, rows as {normalized header: Value})."""
    kind = loc.get("kind")
    mode = ctx.get("mode")
    if kind in ("table", "column_set"):
        if mode == "per_group" and ctx.get("group_rows") is not None:
            return _rows_from_group(ctx)
        if mode == "per_row":
            raise ResolveError("mode_mismatch", "「每行一份」模式下每条样本只有一行，不能作为重复明细来源；请改用「每文件一份」或「每组一份」")
        spec = loc if kind == "table" else ctx.get("table_spec", loc)
        tv = table_view(src, spec)
        heads = [tabular.normalize_header(h) for h in tv.headers]
        rows = []
        for r in tv.data_rows:
            rows.append({h: _with_src(tv.sheet.cell(r, i + tv.first_col), src, sheet=tv.sheet.name,
                                      cell=tabular.cell_ref(r, i + tv.first_col)) for i, h in enumerate(heads) if h})
        return heads, rows
    if kind == "docx_table":
        headers, data = docx_table_rows(src, loc)
        heads = [tabular.normalize_header(h) for h in headers]
        rows = [{h: (vals[i] if i < len(vals) else V.empty()) for i, h in enumerate(heads) if h} for vals in data]
        return heads, rows
    raise ResolveError("bad_locator", f"「{kind}」不能作为重复明细来源")


def _rows_from_group(ctx: dict) -> tuple[list[str], list[dict[str, V.Value]]]:
    heads = None
    rows = []
    for src, tv, r in ctx["group_rows"]:
        h = [tabular.normalize_header(x) for x in tv.headers]
        heads = heads or h
        rows.append({name: _with_src(tv.sheet.cell(r, i + tv.first_col), src, sheet=tv.sheet.name,
                                     cell=tabular.cell_ref(r, i + tv.first_col)) for i, name in enumerate(h) if name})
    return heads or [], rows
