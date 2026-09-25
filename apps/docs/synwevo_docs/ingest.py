"""The document engine as a member-import reader, injected into the account service by the
composition root (`synwevo_server`), which is the only place allowed to see both.

Turns any file the engine can read into the format-neutral shape `synwevo_core.member_import`
plans from: tables (rows of text with their sheet/table/page and row number) and lines (with
their page/line), plus honest status: `needs_ocr` when a scan or image has no text yet,
`error`/`unsupported` with the engine's own reason. Every engine limit (file size, unzipped size,
cells, pages, pixels, converter and OCR timeouts) applies unchanged; this module adds import-sized
caps on top (pages read, pages OCR'd, rows kept). Nothing here touches the Docs store: it is a
pure function of bytes, so the account service never sees a Docs project.
"""
from __future__ import annotations

import shutil
import unicodedata

from synwevo_core.member_import import (MAX_TABLE_ROWS, accept_upload, add_lines, add_table,
                                        csv_extraction, decode_text, empty_extraction, text_extraction)

MAX_TEXT_PAGES = 200
MAX_OCR_PAGES = 20
FORMATS = ("csv", "tsv", "xlsx", "xlsm", "xls", "ods", "json", "txt", "md", "html", "docx", "docm", "doc", "odt", "rtf",
           "pdf", "png", "jpg", "jpeg", "tif", "tiff", "webp", "bmp", "gif")


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
        return {"engine": "docs", "formats": list(FORMATS), "ocr": bool(langs), "ocr_langs": langs,
                "converter": converter, "scanner": M.health(M.policy()),
                "limits": {"text_pages": MAX_TEXT_PAGES, "ocr_pages": MAX_OCR_PAGES, "table_rows": MAX_TABLE_ROWS}}

    def accept(self, filename: str, data: bytes) -> dict:
        return accept_upload(filename, data)

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
        if len(pages) > MAX_TEXT_PAGES:
            out["notes"].append(f"只读取前 {MAX_TEXT_PAGES} 页")
        scanned = [p["index"] for p in pages[:MAX_TEXT_PAGES] if p["kind"] in ("scanned", "mixed")]
        lines: list[tuple[str, dict]] = []
        for p in pages[:MAX_TEXT_PAGES]:
            if p["kind"] == "scanned":
                continue
            words = pe.page_words(data, p["index"])["words"]
            groups: dict[tuple, list[dict]] = {}
            for wd in words:
                groups.setdefault((wd["b"], wd["l"]), []).append(wd)
            for n, key in enumerate(sorted(groups), start=1):
                ws = sorted(groups[key], key=lambda x: x["n"])
                lines.append((_join([x["t"] for x in ws]), {"page": p["index"] + 1, "line": n}))
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
