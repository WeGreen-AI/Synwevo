"""Parse uploaded source files into a format-neutral view used for selection and
extraction, while keeping the native bytes for format-specific locators."""
from __future__ import annotations

import html as _html
import json
import re
from dataclasses import dataclass, field

from . import tabular
from .detect import detect
from .limits import LIMITS


@dataclass
class ParsedSource:
    format: str
    family: str
    status: str                      # ready | error | unsupported | needs_ocr
    error: str | None = None
    notes: list[str] = field(default_factory=list)
    structure: dict = field(default_factory=dict)   # summary shown in UI and used for compatibility checks
    signature: str = ""              # structure signature for "same structure" validation
    workbook: dict | None = None     # Workbook.to_json() for tabular families
    working_bytes: bytes | None = None  # converted OOXML (for legacy formats) used for all further processing
    working_format: str | None = None


def parse_source(data: bytes, filename: str, options: dict | None = None) -> ParsedSource:
    options = options or {}
    try:
        info = detect(data, filename)
    except ValueError as e:
        return ParsedSource("unknown", "unknown", "unsupported", str(e))
    fmt, fam = info["format"], info["family"]
    notes = list(info["notes"])
    if info["encrypted"]:
        return ParsedSource(fmt, fam, "error", "文件已加密（有打开密码），无法读取；请移除密码后重新上传", notes)
    working = data
    wfmt = fmt
    try:
        if info.get("convert_via"):
            from .converters import ConversionError, convert
            try:
                working = convert(data, fmt, info["convert_via"])
                wfmt = info["convert_via"]
                notes.append(f"{fmt.upper()} 已通过 LibreOffice 转换为 {wfmt.upper()} 后读取；复杂排版、宏和嵌入对象可能丢失")
            except ConversionError as e:
                if fmt == "xls":
                    wfmt = "xls"
                else:
                    return ParsedSource(fmt, fam, "error", str(e), notes)
        if wfmt in ("csv", "tsv"):
            opts = dict(options.get("csv") or {})
            if wfmt == "tsv" and not opts.get("delimiter"):
                opts["delimiter"] = "\t"
            wb = tabular.read_csv(working, opts)
            return _tabular_result(fmt, fam, wb, notes, working if working is not data else None, wfmt)
        if wfmt in ("xlsx", "xlsm"):
            wb = tabular.read_xlsx(working)
            inv = wb.info.get("inventory", {})
            if inv.get("charts"):
                notes.append(f"含 {inv['charts']} 个图表（作为来源只读取单元格数据）")
            return _tabular_result(fmt, fam, wb, notes, working if working is not data else None, wfmt)
        if wfmt == "xls":
            wb = tabular.read_xls(working)
            return _tabular_result(fmt, fam, wb, notes, None, "xls")
        if wfmt in ("docx", "docm"):
            return _docx_result(fmt, fam, working, notes, working if working is not data else None, wfmt)
        if wfmt == "pdf":
            from . import pdf_engine
            meta = pdf_engine.inspect(working)
            status = "ready"
            if meta["kind"] == "scanned":
                notes.append("扫描型 PDF：没有可用文字层，需要 OCR 识别后才能选择文字")
                status = "needs_ocr"
            elif meta["kind"] == "mixed":
                notes.append("部分页面没有文字层（扫描页），这些页面需要 OCR")
            if meta["has_js"]:
                notes.append("PDF 含 JavaScript，本软件不会执行")
            sig = json.dumps([[p["uw"], p["uh"], p["rotation"]] for p in meta["pages"][:3]])
            return ParsedSource(fmt, fam, status, None, notes, {"pdf": meta}, "pdf:" + str(meta["page_count"] > 0) + sig)
        if fam == "image":
            from .images import inspect_image
            meta = inspect_image(working)
            notes.append("图片需要 OCR 识别后才能选择文字；原图预览与识别文字分开保存")
            return ParsedSource(fmt, fam, "needs_ocr", None, notes, {"image": meta}, f"image:{meta['width']}x{meta['height']}")
        if wfmt in ("pptx", "pptm"):
            from .pptx_engine import inspect_pptx
            meta = inspect_pptx(working)
            return ParsedSource(fmt, fam, "ready", None, notes, {"pptx": meta}, "pptx:" + meta["signature"],
                                working_bytes=working if working is not data else None, working_format=wfmt)
        if wfmt in ("txt", "md", "html"):
            return _text_result(fmt, fam, working, notes)
    except (ValueError, KeyError) as e:
        return ParsedSource(fmt, fam, "error", _friendly(e), notes)
    except Exception as e:  # noqa: BLE001 - surface unknown parser failures with a reason
        return ParsedSource(fmt, fam, "error", f"解析失败：{type(e).__name__}: {e}", notes)
    return ParsedSource(fmt, fam, "unsupported", f"暂不支持读取 {fmt.upper()} 格式", notes)


def _friendly(e: Exception) -> str:
    s = str(e)
    return s if s else type(e).__name__


def _tabular_result(fmt, fam, wb: tabular.Workbook, notes, working, wfmt) -> ParsedSource:
    sheets = []
    for sh in wb.sheets:
        header = [c.text for c in sh.rows[0]] if sh.rows else []
        sheets.append({"name": sh.name, "rows": sh.n_rows, "cols": sh.n_cols, "hidden": sh.hidden,
                       "merged": len(sh.merged), "notes": sh.notes, "first_row": header[:60]})
        for n in sh.notes:
            if n not in notes:
                notes.append(f"[{sh.name}] {n}")
    sig = json.dumps([[s["name"], [tabular.normalize_header(h) for h in s["first_row"]]] for s in sheets], ensure_ascii=False)
    struct = {"sheets": sheets, "info": {k: v for k, v in wb.info.items() if k != "inventory"}}
    return ParsedSource(fmt, fam, "ready", None, notes, struct, "table:" + sig, wb.to_json(), working, wfmt)


def _docx_result(fmt, fam, data: bytes, notes, working, wfmt) -> ParsedSource:
    from .docx_core import iter_blocks, list_parts, para_text, part_container, w
    from .ooxml import Package
    pkg = Package(data)
    parts = list_parts(pkg)
    body = part_container(pkg, pkg.main_part())
    blocks = list(iter_blocks(body))
    paragraphs = sum(1 for b in blocks if b.tag == w("p"))
    tables = sum(1 for b in blocks if b.tag == w("tbl"))
    labels = []
    for b in blocks[:400]:
        if b.tag == w("p"):
            t = para_text(b)
            m = re.match(r"^\s*([^：:]{1,20})[：:]", t)
            if m:
                labels.append(m.group(1).strip())
    inv = pkg.inventory()
    if inv.get("comments"):
        notes.append("文档含批注（读取正文时忽略批注）")
    struct = {"docx": {"blocks": len(blocks), "paragraphs": paragraphs, "tables": tables, "parts": [p["label"] for p in parts],
                       "inventory": inv}}
    sig = "docx:" + json.dumps(labels[:40], ensure_ascii=False)
    return ParsedSource(fmt, fam, "ready", None, notes, struct, sig, None, working, wfmt)


def _text_result(fmt, fam, data: bytes, notes) -> ParsedSource:
    enc, _ = tabular.detect_encoding(data)
    text = data.decode(enc, errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    struct: dict = {"text": {"encoding": enc, "lines": text.count("\n") + 1, "chars": len(text)}}
    wbjson = None
    if fmt == "html":
        tables = html_tables(text)
        if tables:
            struct["text"]["tables"] = len(tables)
            wb = tabular.Workbook([tabular.Sheet(f"表格{i + 1}", i, t) for i, t in enumerate(tables)])
            wbjson = wb.to_json()
            notes.append(f"HTML 中识别到 {len(tables)} 个表格，可按表格方式选择；脚本不会被执行")
        else:
            notes.append("HTML 按纯文本读取；脚本和样式不会被执行或加载")
    return ParsedSource(fmt, fam, "ready", None, notes, struct, f"text:{fmt}", wbjson)


# ------------------------------------------------------------------ text helpers
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(src: str) -> str:
    src = re.sub(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>", "", src)
    src = re.sub(r"(?i)<br\s*/?>", "\n", src)
    src = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|table|section|article)>", "\n", src)
    src = re.sub(r"(?i)</t[dh]>", "\t", src)
    text = _TAG_RE.sub("", src)
    text = _html.unescape(text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out, blank = [], 0
    for ln in lines:
        if not ln.strip():
            blank += 1
            if blank > 1:
                continue
        else:
            blank = 0
        out.append(ln)
    return "\n".join(out).strip("\n")


def html_tables(src: str):
    from . import values as V
    src = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", src)
    tables = []
    for tm in re.finditer(r"(?is)<table[^>]*>(.*?)</table>", src):
        rows = []
        for rm in re.finditer(r"(?is)<tr[^>]*>(.*?)</tr>", tm.group(1)):
            cells = []
            for cm in re.finditer(r"(?is)<t([dh])([^>]*)>(.*?)</t[dh]>", rm.group(1)):
                txt = _html.unescape(_TAG_RE.sub("", re.sub(r"(?i)<br\s*/?>", "\n", cm.group(3)))).strip()
                span = re.search(r'colspan\s*=\s*"?(\d+)', cm.group(2) or "", re.I)
                cells.append(V.text(txt) if txt else V.empty())
                if span:
                    for _ in range(int(span.group(1)) - 1):
                        cells.append(V.empty())
            rows.append(cells)
        if rows:
            tables.append(rows)
    return tables


def text_content(data: bytes, fmt: str) -> str:
    enc, _ = tabular.detect_encoding(data)
    text = data.decode(enc, errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if fmt == "html":
        return html_to_text(text)
    return text
