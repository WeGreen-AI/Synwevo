"""Pagination guard.

Word processors, not DocWeave, decide where pages break.  To know the real page
count of an output we lay it out with LibreOffice (PDF export) and count pages;
the template's own page count is the reference.  For DOCX outputs, fields the
user marked 「超长时允许缩小字号」 can be shrunk step by step until the output
has no more pages than the template – every step is re-checked by an actual
layout, nothing is estimated.
"""
from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

from lxml import etree

from . import docx_core as dc
from .ooxml import Package

_PDF_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_LOCK = threading.Lock()
_MAX = 48
SHRINK_STEPS = (0.92, 0.84, 0.76, 0.68, 0.6)


class LayoutUnavailable(RuntimeError):
    pass


def render_pdf(data: bytes, fmt: str) -> bytes:
    """The document laid out by LibreOffice (cached by content)."""
    from .converters import ConversionError, convert, soffice_path
    if fmt == "pdf":
        return data
    if not soffice_path():
        raise LayoutUnavailable("未安装 LibreOffice，无法计算真实分页")
    key = hashlib.sha256(data).hexdigest() + fmt
    with _LOCK:
        if key in _PDF_CACHE:
            _PDF_CACHE.move_to_end(key)
            return _PDF_CACHE[key]
    try:
        pdf = convert(data, fmt, "pdf")
    except ConversionError as e:
        raise LayoutUnavailable(f"排版计算失败：{e}") from e
    with _LOCK:
        _PDF_CACHE[key] = pdf
        while len(_PDF_CACHE) > _MAX:
            _PDF_CACHE.popitem(last=False)
    return pdf


def page_count(data: bytes, fmt: str) -> int:
    from .pdf_engine import page_count as pdf_pages
    return pdf_pages(render_pdf(data, fmt))


def page_issue(out_pages: int, tpl_pages: int, strict: bool, slot_hint: bool) -> dict | None:
    if out_pages <= tpl_pages:
        return None
    hint = "；已尝试缩小允许缩小的字段仍放不下" if slot_hint else "；可在字段设置中勾选「超长时允许缩小字号」，或精简内容"
    return {"level": "error" if strict else "warning", "code": "page_overflow",
            "message": f"输出有 {out_pages} 页，模板为 {tpl_pages} 页：新内容更长导致分页增加{hint}"}


# ------------------------------------------------------------------ DOCX shrink-to-fit
def _effective_size(styles, run, para) -> float:
    rpr = run.find(dc.w("rPr"))
    if rpr is not None:
        sz = rpr.find(dc.w("sz"))
        if sz is not None and sz.get(dc.w("val")):
            try:
                return int(sz.get(dc.w("val"))) / 2
            except ValueError:
                pass
    ppr = para.find(dc.w("pPr")) if para is not None else None
    pst = ppr.find(dc.w("pStyle")) if ppr is not None else None
    rst = rpr.find(dc.w("rStyle")) if rpr is not None else None
    r, _ = styles.resolve(pst.get(dc.w("val")) if pst is not None else None, rst.get(dc.w("val")) if rst is not None else None)
    return float(r.get("sz") or 10.5)


def scale_fields(data: bytes, slot_ids: set[str], factor: float, min_pt: dict[str, float]) -> bytes:
    """Set the font size of every run inside the given fields (content controls ``dw:<id>…``)
    to ``factor`` × its original size, never below the slot's minimum.  Original sizes are
    remembered on the run (``dw-osz``) so repeated calls scale from the original."""
    from .docx_render import StyleSheet
    pkg = Package(data)
    styles = StyleSheet(pkg, pkg.main_part())
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        for tag, sdt in list(dc.iter_field_sdts(root)):
            base = tag.split("#")[0].split("@")[0]
            if base not in slot_ids:
                continue
            para = sdt
            while para is not None and para.tag != dc.w("p"):
                para = para.getparent()
            for run in sdt.iter(dc.w("r")):
                orig = run.get("{urn:docweave}osz")
                size = float(orig) if orig else _effective_size(styles, run, para)
                if not orig:
                    run.set("{urn:docweave}osz", str(size))
                new = max(min_pt.get(base, 7.0), size * factor)
                rpr = run.find(dc.w("rPr"))
                if rpr is None:
                    rpr = etree.Element(dc.w("rPr"))
                    run.insert(0, rpr)
                for tagname in ("sz", "szCs"):
                    el = rpr.find(dc.w(tagname))
                    if el is None:
                        el = etree.SubElement(rpr, dc.w(tagname))
                    el.set(dc.w("val"), str(max(2, int(round(new * 2)))))
    return pkg.save()


def _strip_marks(data: bytes, unwrap: bool) -> bytes:
    pkg = Package(data)
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        for run in root.iter(dc.w("r")):
            if "{urn:docweave}osz" in run.attrib:
                del run.attrib["{urn:docweave}osz"]
        if unwrap:
            for _, sdt in list(dc.iter_field_sdts(root)):
                dc.unwrap_sdt(sdt)
        etree.cleanup_namespaces(root)
    return pkg.save()


def fit_docx(filled_keep: bytes, slots: list[dict], tpl_pages: int, keep_fields: bool) -> tuple[bytes, dict]:
    """``filled_keep`` was filled with the field controls kept.  Shrink shrinkable fields until the
    layout has at most ``tpl_pages`` pages.  Returns (final bytes, report)."""
    shrink = {s["id"] for s in slots if s.get("shrink")}
    mins = {s["id"]: float(s.get("shrink_min") or 7) for s in slots if s.get("shrink")}
    pages = page_count(filled_keep, "docx")
    report = {"template_pages": tpl_pages, "pages": pages, "factor": 1.0, "renders": 1}
    data = filled_keep
    if pages > tpl_pages and shrink:
        for f in SHRINK_STEPS:
            trial = scale_fields(data, shrink, f, mins)
            p = page_count(_strip_marks(trial, False), "docx")
            report["renders"] += 1
            if p <= tpl_pages:
                data, report["pages"], report["factor"] = trial, p, f
                break
            data, report["pages"], report["factor"] = trial, p, f
    return _strip_marks(data, not keep_fields), report
