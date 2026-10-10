"""The document engine as a member-import reader, injected into the account service by the
composition root (`synwevo_server`), which is the only place allowed to see both.

Turns any file the engine can read into the format-neutral shape `synwevo_core.member_import`
plans from: tables (rows of text with their sheet/table/page and row number) and lines (with
their page/line), plus honest status: `needs_ocr` when a scan or image has no text yet,
`error`/`unsupported` with the engine's own reason. Every engine limit (file size, unzipped size,
cells, pages, pixels, converter and OCR timeouts) applies unchanged; this module adds import-sized
caps on top (pages read, pages OCR'd, rows kept). Nothing here touches the Docs store: it is a
pure function of bytes, so the account service never sees a Docs project.

The separate ``extract_context`` contract is for Agent file context: a narrower set of
formats, complete output or a sanitized ServiceError, with no member-import prefix caps.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import unicodedata

from synwevo_core.errors import ServiceError
from synwevo_core.member_import import (MAX_TABLE_ROWS, accept_upload, add_lines, add_table,
                                        csv_extraction, decode_text, empty_extraction, text_extraction)

MAX_TEXT_PAGES = 200
MAX_OCR_PAGES = 20
FORMATS = ("csv", "tsv", "xlsx", "xlsm", "xls", "ods", "json", "txt", "md", "html", "docx", "docm", "doc", "odt", "rtf",
           "pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp", "gif")

# Context is an all-or-nothing reader, deliberately narrower than member import.
# chars counts decoded text (including CSV syntax), or PDF text including line separators;
# output_chars also bounds the ENTIRE compact, ensure_ascii=False JSON result, sources included.
CONTEXT_FORMATS = ("txt", "md", "csv", "tsv", "pdf")
CONTEXT_LIMITS = {"file_bytes": 20 * 1024 * 1024, "pages": 200, "lines": 5000,
                  "tables": 1, "rows": 2000, "columns": 100, "cells": 10000,
                  "line_chars": 10000, "cell_chars": 10000, "chars": 60000,
                  "output_chars": 60000}


def _context_limit(kind: str, count: int) -> None:
    if count > CONTEXT_LIMITS[kind]:
        raise ServiceError(413, "context_limit_exceeded", "文件上下文超过完整解析上限，请拆分文件后重试",
                           {"limit": kind, "maximum": CONTEXT_LIMITS[kind]})


def _context_unsupported() -> None:
    raise ServiceError(400, "context_format_unsupported", "该格式或文档内容暂不支持完整上下文解析") from None


def _context_invalid() -> None:
    raise ServiceError(400, "context_parse_error", "文件损坏、不完整或无法完整读取，请重新导出后重试") from None


def _context_needs_ocr() -> None:
    raise ServiceError(400, "context_needs_ocr", "文件含有图片、扫描或无法验证的文字内容，暂不支持完整上下文解析",
                       {"needsOCR": True})


def _context_line(out: dict, text: str, source: dict) -> None:
    # Keep whitespace and blank lines: neither .strip() nor a prefix slice is lossless.
    _context_limit("lines", len(out["lines"]) + 1)
    _context_limit("line_chars", len(text))
    out["lines"].append({"text": text, "source": source})


def _cjk(ch: str) -> bool:
    return bool(ch) and unicodedata.east_asian_width(ch) in ("W", "F")


def _join(words: list[str]) -> str:
    s = ""
    for w in words:
        if s and not (_cjk(s[-1]) and _cjk(w[:1])):
            s += " "
        s += w
    return s


class DocumentIngest:
    name = "docs"

    def describe(self) -> dict:
        from synwevo_core import malware as M
        from .engine.ocr import available_langs
        try:
            from .engine.converters import soffice_path
            converter = bool(soffice_path())
        except Exception:  # noqa: BLE001 - the resolver's name is an engine detail; fall back to PATH
            converter = bool(shutil.which("soffice") or shutil.which("libreoffice"))
        langs = available_langs()
        try:
            from .engine import pdf_engine
            context_pdf = pdf_engine.ENGINE == "pymupdf"
        except ImportError:
            context_pdf = False
        context_formats = [fmt for fmt in CONTEXT_FORMATS if fmt != "pdf" or context_pdf]
        return {"engine": "docs", "formats": list(FORMATS), "ocr": bool(langs), "ocr_langs": langs,
                "converter": converter, "scanner": M.health(M.policy()),
                "limits": {"text_pages": MAX_TEXT_PAGES, "ocr_pages": MAX_OCR_PAGES, "table_rows": MAX_TABLE_ROWS},
                "context_formats": context_formats, "context_limits": dict(CONTEXT_LIMITS), "context_ocr": False}

    def accept(self, filename: str, data: bytes) -> dict:
        return accept_upload(filename, data)

    def extract_context(self, data: bytes, filename: str, *, ocr: bool = False) -> dict:
        """Return a complete, bounded extraction or raise a content-free ServiceError.

        Supported text is strict UTF-8 (optional BOM); MD stays literal source, including
        code, tables and links. CSV uses comma and TSV uses tab, with standard double-quote
        escaping and multiline cells. No delimiter/encoding guesses or remote link reads.
        Sources are 1-based physical text lines, CSV logical row_numbers, or PDF page/line.
        Final line terminators are structural, as in extract(); whitespace/empty rows survive.

        PDF requires the existing full Docs engine's diagnostics. Only unencrypted, unrepaired
        text pages (and truly blank pages) are accepted: images, drawings, OCR/invisible text,
        annotations, forms, attachments and optional layers cannot be represented completely.
        No OCR is attempted, even when requested. DOCX/XLSX and converters are intentionally
        excluded: their import readers omit parts such as headers, comments or uncached values.

        The caller owns acceptance, authorization and an isolated resource-limited subprocess.
        In particular, PDF diagnostics are process-global; do not run this in request threads.
        """
        from .engine.limits import LIMITS, LimitError

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        ext = {"markdown": "md", "text": "txt", "log": "txt"}.get(ext, ext)
        if ext not in CONTEXT_FORMATS:
            _context_unsupported()
        if ocr:
            raise ServiceError(400, "context_ocr_unsupported", "完整上下文解析暂不支持文字识别")
        _context_limit("file_bytes", len(data))
        try:
            if len(data) > LIMITS.max_file_bytes:
                raise LimitError("file size")
            out = empty_extraction("docs", ext, "pdf" if ext == "pdf" else
                                   ("tabular" if ext in ("csv", "tsv") else "text"))
            if ext == "pdf":
                self._context_pdf(out, data)
            else:
                text = data.decode("utf-8-sig", errors="strict")
                _context_limit("chars", len(text))
                if len(text) > LIMITS.max_text_chars:
                    raise LimitError("text size")
                if any((ord(c) < 32 and c not in "\t\r\n") or ord(c) == 127 for c in text):
                    _context_invalid()
                if ext in ("csv", "tsv"):
                    self._context_csv(out, data, text, "\t" if ext == "tsv" else ",")
                else:
                    for number, line in enumerate(text.splitlines(), start=1):
                        _context_line(out, line, {"line": number})
            _context_limit("output_chars", len(json.dumps(out, ensure_ascii=False, separators=(",", ":"))))
            return out
        except ServiceError:
            raise
        except LimitError:
            raise ServiceError(413, "context_limit_exceeded", "文件超过文档解析引擎上限，请拆分文件后重试") from None
        except Exception:  # noqa: BLE001 - parser messages may contain file content or secrets
            raise ServiceError(400, "context_parse_error", "文件损坏、不完整或无法完整读取，请重新导出后重试") from None

    @staticmethod
    def _context_csv(out: dict, data: bytes, text: str, delimiter: str) -> None:
        from .engine import tabular

        # The mature reader uses strict=False. Validate the SAME dialect first, and reject
        # before handing it a malformed/oversized table; never infer completeness from a slice.
        cells, checked_rows = 0, []
        for number, row in enumerate(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter,
                                               strict=True), start=1):
            _context_limit("rows", number)
            _context_limit("columns", len(row))
            cells += max(1, len(row))
            _context_limit("cells", cells)
            for cell in row:
                _context_limit("cell_chars", len(cell))
            checked_rows.append(row)
        wb = tabular.read_csv(data, {"encoding": "utf-8-sig", "delimiter": delimiter})
        if len(wb.sheets) != 1:
            _context_invalid()
        for sheet in wb.sheets:
            _context_limit("tables", len(out["tables"]) + 1)
            rows = [[cell.text for cell in row] for row in sheet.rows]
            if rows != checked_rows:
                # Fail closed if engine normalization changes a validated value (for example
                # stripping a second BOM). This also guards future changes to the shared reader.
                _context_invalid()
            out["tables"].append({"name": sheet.name, "source": {"sheet": sheet.name}, "rows": rows,
                                  "row_numbers": list(range(1, len(rows) + 1)), "truncated": False})

    @staticmethod
    def _context_pdf(out: dict, data: bytes) -> None:
        try:
            from .engine import pdf_engine as pe
        except ImportError:
            _context_unsupported()

        # The basic engine does not expose repair/content diagnostics: fail closed there.
        if pe.ENGINE != "pymupdf":
            _context_unsupported()
        # MuPDF can recover files lacking the final trailer; recovery is not completeness.
        if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
            _context_invalid()
        diagnostics = pe.pymupdf.TOOLS
        show_errors = diagnostics.mupdf_display_errors()
        show_warnings = diagnostics.mupdf_display_warnings()
        diagnostics.mupdf_display_errors(False)
        diagnostics.mupdf_display_warnings(False)
        diagnostics.reset_mupdf_warnings()
        doc = None
        try:
            try:
                doc = pe.open_pdf(data)
            except pe.PdfError as exc:
                if exc.code == "encrypted":
                    raise ServiceError(400, "context_encrypted", "加密文件不支持完整上下文解析，请先移除加密") from None
                _context_invalid()
            if doc.xref_get_key(-1, "Encrypt")[0] != "null":
                raise ServiceError(400, "context_encrypted", "加密文件不支持完整上下文解析，请先移除加密")
            if doc.is_repaired or diagnostics.mupdf_warnings():
                _context_invalid()
            _context_limit("pages", doc.page_count)
            if pe.damage(data, doc.page_count):
                _context_invalid()
            if (doc.embfile_count() or doc.get_ocgs() or
                    doc.xref_get_key(doc.pdf_catalog(), "AcroForm")[0] != "null"):
                _context_unsupported()
            out["pages"] = doc.page_count
            total_chars = 0
            # Reuse the engine's native text extraction, with no page/line prefix slicing.
            # Infinite clip and no media-box clipping prevent off-page text silently vanishing.
            flags = (pe.pymupdf.TEXTFLAGS_DICT & ~pe.pymupdf.TEXT_PRESERVE_IMAGES &
                     ~pe.pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE & ~pe.pymupdf.TEXT_MEDIABOX_CLIP)
            for page in doc:
                if page.first_annot is not None or page.first_widget is not None or page.get_links():
                    _context_unsupported()
                # A page_kind heuristic alone misses small scans and partly recognized pages.
                if page.get_images(full=True) or page.get_image_info() or page.get_drawings():
                    _context_needs_ocr()
                if any(span.get("type") == 3 or span.get("opacity", 1) != 1 for span in page.get_texttrace()):
                    _context_needs_ocr()
                content = page.get_text("dict", flags=flags, clip=pe.pymupdf.INFINITE_RECT())
                number = 0
                for block in content["blocks"]:
                    if block["type"] != 0:
                        _context_needs_ocr()
                    for line in block["lines"]:
                        text = "".join(span["text"] for span in line["spans"])
                        if "\ufffd" in text or any(ord(c) < 32 and c != "\t" for c in text):
                            _context_needs_ocr()
                        total_chars += len(text) + 1
                        _context_limit("chars", total_chars)
                        number += 1
                        _context_line(out, text, {"page": page.number + 1, "line": number})
            # Includes non-fatal stream/font errors encountered ONLY while reading later pages.
            if diagnostics.mupdf_warnings():
                _context_invalid()
        finally:
            if doc is not None:
                doc.close()
            diagnostics.reset_mupdf_warnings()
            diagnostics.mupdf_display_errors(show_errors)
            diagnostics.mupdf_display_warnings(show_warnings)

    def extract(self, data: bytes, filename: str, *, ocr: bool = False) -> dict:
        from .engine.sources import parse_source
        ext = (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
        if ext == "json":
            text, enc = decode_text(data)
            return text_extraction("docs", "json", text, note=f"编码 {enc}")
        ps = parse_source(data, filename)
        out = empty_extraction("docs", ps.format, ps.family)
        out["notes"] = list(ps.notes)
        if ps.status in ("error", "unsupported"):
            out["status"], out["error"] = ps.status, ps.error
            return out
        working = ps.working_bytes or data
        wfmt = ps.working_format or ps.format
        try:
            if ps.workbook:
                self._tables_from_workbook(out, ps.workbook)
                if ps.family == "text" and wfmt == "html":
                    pass                                    # HTML tables are the useful part; plain text is not re-added
                return out
            if wfmt in ("docx", "docm"):
                self._docx(out, working)
                return out
            if wfmt == "pdf":
                self._pdf(out, working, ocr=ocr)
                return out
            if ps.family == "image":
                self._image(out, working, ocr=ocr)
                return out
            if wfmt in ("csv", "tsv"):
                return csv_extraction("docs", wfmt, working, "\t" if wfmt == "tsv" else None)
            if wfmt in ("txt", "md", "html"):
                text, _ = decode_text(working)
                if wfmt == "html":
                    from .engine.sources import html_to_text
                    text = html_to_text(text)
                    wfmt = "txt"
                return text_extraction("docs", wfmt, text)
        except Exception as e:  # noqa: BLE001 - the engine's own limit/parse errors become a visible status
            out["status"], out["error"] = "error", f"{type(e).__name__}: {str(e)[:200]}"
            return out
        out["status"], out["error"] = "unsupported", f"暂不支持从 {ps.format.upper()} 读取成员"
        return out

    # ------------------------------------------------------------------ per family
    @staticmethod
    def _tables_from_workbook(out: dict, wb: dict) -> None:
        from .engine import tabular
        for sd in wb.get("sheets") or []:
            sh = tabular.Sheet.from_json(sd)
            rows = [[(c.text if c.text else ("" if c.v is None else str(c.v))) for c in row] for row in sh.rows]
            src = {"sheet": sh.name}
            if sh.hidden:
                src["hidden"] = True
                out["notes"].append(f"工作表「{sh.name}」在文件中是隐藏的")
            if sh.formula_without_cache:
                out["notes"].append(f"工作表「{sh.name}」有 {len(sh.formula_without_cache)} 个公式没有缓存值，读作空白（不执行公式）")
            add_table(out, sh.name, src, rows)

    @staticmethod
    def _docx(out: dict, data: bytes) -> None:
        from .engine.docx_core import cell_text, iter_blocks, iter_cells, iter_rows, para_text, part_container, w
        from .engine.ooxml import Package
        pkg = Package(data)
        body = part_container(pkg, pkg.main_part())
        n_tbl, lines = 0, []
        for i, block in enumerate(iter_blocks(body)):
            if block.tag == w("tbl"):
                n_tbl += 1
                rows = [[cell_text(tc).strip() for tc in iter_cells(tr)] for tr in iter_rows(block)]
                add_table(out, f"表格{n_tbl}", {"docx_table": n_tbl, "block": i}, rows)
            else:
                lines.append((para_text(block), {"block": i, "paragraph": True}))
        add_lines(out, lines)
        out["notes"].append("表格按单元格读取；合并单元格按首格计")

    @staticmethod
    def _pdf(out: dict, data: bytes, *, ocr: bool) -> None:
        from .engine import pdf_engine as pe
        meta = pe.inspect(data)
        pages = meta["pages"]
        out["pages"] = len(pages)
        # A damaged file still opens and reads its first lines. What it yields is not the
        # document, so nothing is read from it (`damaged` names the evidence for the caller).
        damaged = pe.damage(data, MAX_TEXT_PAGES)
        if damaged:
            out["status"], out["damaged"] = "error", damaged
            out["error"] = "PDF 文件已损坏或不完整（可能被截断），部分内容无法读取；请用生成它的程序重新导出后再上传"
            return
        if len(pages) > MAX_TEXT_PAGES:
            out["notes"].append(f"只读取前 {MAX_TEXT_PAGES} 页")
        scanned = [p["index"] for p in pages[:MAX_TEXT_PAGES] if p["kind"] in ("scanned", "mixed")]
        lines: list[tuple[str, dict]] = []
        doc = pe.open_pdf(data)
        machine_pages = 0
        try:
            for p in pages[:MAX_TEXT_PAGES]:
                if p["kind"] == "scanned":
                    continue
                words = pe.page_words(data, p["index"])["words"]
                groups: dict[tuple, list[dict]] = {}
                for wd in words:
                    groups.setdefault((wd["b"], wd["l"]), []).append(wd)
                keys = sorted(groups)
                boxes = [(min(w["u"][0] for w in groups[k]), min(w["u"][1] for w in groups[k]),
                          max(w["u"][2] for w in groups[k]), max(w["u"][3] for w in groups[k])) for k in keys]
                # A scan made searchable carries an invisible machine reading over the page image:
                # its lines are OCR (no confidence given), checked by a person like any OCR line.
                machine = pe.machine_read_rects(doc[p["index"]], boxes)
                machine_pages += any(machine)
                for n, key in enumerate(keys, start=1):
                    ws = sorted(groups[key], key=lambda x: x["n"])
                    src = {"page": p["index"] + 1, "line": n}
                    if machine[n - 1]:
                        src.update({"ocr": True, "conf": None, "layer": "scan"})
                    lines.append((_join([x["t"] for x in ws]), src))
        finally:
            doc.close()
        if machine_pages:
            out["notes"].append(f"{machine_pages} 页的文字是扫描件上的识别文字层（机器读出），按识别结果处理")
        if scanned:
            out["notes"].append(f"{len(scanned)} 页没有文字层（扫描页）")
            if ocr:
                DocumentIngest._ocr_pages(out, data, scanned[:MAX_OCR_PAGES], lines)
                if len(scanned) > MAX_OCR_PAGES:
                    out["notes"].append(f"一次最多识别 {MAX_OCR_PAGES} 页扫描页")
            elif not lines:
                out["status"] = "needs_ocr"
        add_lines(out, lines)
        out["notes"].append("PDF 按行读取，没有表格结构；每行按邮箱识别成员")

    @staticmethod
    def _ocr_pages(out: dict, data: bytes, indexes: list[int], lines: list) -> None:
        from .engine import pdf_engine as pe
        from .engine.ocr import OcrError, ocr_png
        try:
            for idx in indexes:
                png = pe.render_page_png(data, idx, zoom=3.0)
                res = ocr_png(png)
                out["ocr"] = {"available": True, "applied": True, "engine": res.get("engine")}
                DocumentIngest._ocr_lines(res, idx + 1, lines)
        except OcrError as e:
            out["ocr"] = {"available": False, "applied": False, "engine": None}
            out["notes"].append(f"OCR 未完成：{e}")
            if not lines:
                out["status"], out["error"] = "error", str(e)

    @staticmethod
    def _ocr_lines(res: dict, page: int, lines: list) -> None:
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for wd in res["words"]:
            if wd["line"] not in groups:
                groups[wd["line"]] = []; order.append(wd["line"])
            groups[wd["line"]].append(wd)
        for n, key in enumerate(order, start=1):
            ws = sorted(groups[key], key=lambda x: x["n"])
            conf = min((x["conf"] for x in ws), default=0)
            lines.append((_join([x["t"] for x in ws]), {"page": page, "line": n, "ocr": True, "conf": conf}))

    @staticmethod
    def _image(out: dict, data: bytes, *, ocr: bool) -> None:
        from .engine.images import to_png
        from .engine.ocr import OcrError, ocr_png
        out["pages"] = 1
        if not ocr:
            out["status"] = "needs_ocr"
            return
        lines: list = []
        try:
            res = ocr_png(to_png(data))
        except OcrError as e:
            out["status"], out["error"] = "error", str(e)
            out["ocr"] = {"available": False, "applied": False, "engine": None}
            return
        out["ocr"] = {"available": True, "applied": True, "engine": res.get("engine")}
        DocumentIngest._ocr_lines(res, 1, lines)
        add_lines(out, lines)
        out["notes"].append("图片文字由 OCR 识别，请逐行核对")
