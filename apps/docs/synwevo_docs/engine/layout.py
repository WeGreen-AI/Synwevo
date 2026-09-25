"""Pagination guard.

Word processors, not Synwevo Docs, decide where pages break.  To know the real page
count of an output we lay it out with LibreOffice (PDF export) and count pages;
the template's own page count is the reference.  For DOCX outputs, fields the
user marked 「超长时允许缩小字号」 can be shrunk step by step until the output
has no more pages than the template – every step is re-checked by an actual
layout, nothing is estimated.
"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from collections import OrderedDict

from lxml import etree

from . import docx_core as dc
from .ooxml import NS, Package

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
        raise LayoutUnavailable("排版引擎未启用，无法计算真实分页")
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


def scale_fields(data: bytes, slot_ids: set[str], factor: float, min_pt: dict[str, float]) -> tuple[bytes, dict]:
    """Set the font size of every run inside the given fields (content controls ``dw:<id>…``)
    to ``factor`` × its original size, never below the slot's minimum.  Original sizes are
    remembered on the run (``dw-osz``) so repeated calls scale from the original.

    Returns (bytes, applied) where ``applied`` records what really happened: the smallest
    ratio actually used, the smallest resulting point size, and which slots hit their floor.
    Reporting the requested step instead would tell the user "shrunk to 60%" when a field
    was in fact pinned at its minimum and never got that small."""
    from .docx_render import StyleSheet
    applied = {"ratio": 1.0, "min_pt": float("inf"), "clamped": set()}
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
                if size > 0:
                    ratio = new / size
                    applied["ratio"] = min(applied["ratio"], ratio)
                    applied["min_pt"] = min(applied["min_pt"], new)
                    if new > size * factor + 1e-6:      # the floor, not the factor, decided this run
                        applied["clamped"].add(base)
    applied["clamped"] = sorted(applied["clamped"])
    if applied["min_pt"] == float("inf"):
        applied["min_pt"] = None
    return pkg.save(), applied


_MC = NS["mc"]
_MC_PREFIX_ATTRS = tuple("{%s}%s" % (_MC, a) for a in ("Ignorable", "ProcessContent", "MustUnderstand",
                                                       "PreserveElements", "PreserveAttributes"))


def _mc_prefixes(root) -> list[str]:
    """Prefixes that Markup Compatibility attributes name.  They are referenced only inside
    attribute *values*, so a namespace cleanup sees them as unused — but a document whose
    mc:Ignorable names an undeclared prefix is not conformant, and Word reports it as
    unreadable content."""
    out: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in _MC_PREFIX_ATTRS:
            v = el.get(attr)
            if v:
                out.update(tok.split(":", 1)[0] for tok in v.split())
        if el.tag == "{%s}Choice" % _MC and el.get("Requires"):
            out.update(el.get("Requires").split())
    return sorted(out)


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
        # drops the declaration of our own scratch namespace, keeps every prefix the
        # document's Markup Compatibility attributes still name
        etree.cleanup_namespaces(root, keep_ns_prefixes=_mc_prefixes(root))
    return pkg.save()


def _field_occurrences(data: bytes) -> list[tuple[str, str]]:
    """(slot id, text) for every field control, one entry per occurrence (a detail row, an
    area cell or a field used twice each is its own entry)."""
    out: list[tuple[str, str]] = []
    pkg = Package(data)
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        for tag, sdt in dc.iter_field_sdts(root):
            base = tag.split("#")[0].split("@")[0]
            out.append((base, "".join(t.text or "" for t in sdt.iter(dc.w("t")))))
    return out


def _field_texts(data: bytes) -> dict[str, str]:
    """The text currently inside each field control (all occurrences joined)."""
    out: dict[str, str] = {}
    for base, txt in _field_occurrences(data):
        out[base] = out.get(base, "") + txt
    return out


_INVISIBLE = re.compile(r"[\s\u00ad\u200b-\u200d\u2060\ufeff-]+")


def _visible_key(s: str) -> str:
    """Text reduced to what survives a layout round trip: no whitespace or line breaks
    (wrapping moves them), no hyphens (hyphenation adds them), case folded (w:caps)."""
    return _INVISIBLE.sub("", unicodedata.normalize("NFKC", s or "")).casefold()


def visible_field_check(delivered_docx: bytes, occurrences: list[tuple[str, str]], names: dict[str, str]) -> tuple[list[str], list[str]]:
    """Which fields' text is not fully present in the laid-out document.

    The text is looked up in the PDF LibreOffice lays out from the *delivered* bytes: text
    pushed out of an exact-height table row or a fixed-size frame is not drawn and so is
    not in that PDF, although it is still in the XML.  (Comparing the XML before and after
    shrinking could never find anything: shrinking changes font sizes, not text.)
    Returns (lost field names, truncated field names)."""
    from .pdf_engine import pdf_text
    laid_out = _visible_key(pdf_text(render_pdf(delivered_docx, "docx")))
    lost: list[str] = []
    truncated: list[str] = []
    for base, text in occurrences:
        want = _visible_key(text)
        if not want or want in laid_out:
            continue
        name = names.get(base, base)
        head = want[:max(1, min(6, len(want) // 2))]
        bucket = truncated if head in laid_out else lost
        if name not in bucket:
            bucket.append(name)
    return lost, [n for n in truncated if n not in lost]


def _ladder(slots: list[dict], shrink: set[str], mins: dict[str, float]) -> tuple[float, ...]:
    """Shrink steps, extended down to the smallest ratio the fields' own minimums allow.

    SHRINK_STEPS stops at 0.6, which silently overrode a field configured to go smaller:
    a 16 pt field with shrink_min 4 may legitimately reach 0.25, and stopping at 0.6 failed
    the sample while the user's own constraint still had room."""
    # Only a slot that declares its own font size lets us express its floor as a RATIO.
    # DOCX text fields inherit their size from the paragraph style and declare none, so the
    # ladder stays as-is for them and the per-run clamp in scale_fields enforces min_pt.
    floors = []
    for s in slots:
        if s["id"] not in shrink:
            continue
        base = float(s.get("font_size") or 0)
        if base > 0:
            floors.append(max(0.05, mins.get(s["id"], 7.0) / base))
    steps = list(SHRINK_STEPS)
    if not floors:
        return tuple(steps)
    floor = min(floors)
    steps = [f for f in steps if f >= floor] or []
    last = steps[-1] if steps else 1.0
    if floor < last - 1e-6:                     # the field's own minimum allows going further
        steps += [round(last - i * (last - floor) / 3, 3) for i in range(1, 4)]
        steps = [f for f in steps if f >= floor - 1e-9]
    return tuple(steps or (floor,))


def fit_docx(filled_keep: bytes, slots: list[dict], tpl_pages: int, keep_fields: bool) -> tuple[bytes, dict]:
    """``filled_keep`` was filled with the field controls kept.  Shrink shrinkable fields until the
    layout has at most ``tpl_pages`` pages.  Returns (final bytes, report).

    The page count in the report is measured on the bytes that are actually DELIVERED, not on
    the intermediate that still carries the field controls — unwrapping them can change the
    layout, so the two are not interchangeable.
    """
    shrink = {s["id"] for s in slots if s.get("shrink")}
    mins = {s["id"]: float(s.get("shrink_min") or 7) for s in slots if s.get("shrink")}
    names = {s["id"]: s.get("name") or s["id"] for s in slots}
    before = [(b, t) for b, t in _field_occurrences(filled_keep) if b in shrink]

    def deliver(b: bytes) -> bytes:
        return _strip_marks(b, not keep_fields)

    data = filled_keep
    final = deliver(data)
    pages = page_count(final, "docx")
    report = {"template_pages": tpl_pages, "pages": pages, "factor": 1.0, "effective_factor": 1.0,
              "min_pt": None, "clamped": [], "renders": 1, "steps": []}
    if pages > tpl_pages and shrink:
        for f in _ladder(slots, shrink, mins):
            trial, applied = scale_fields(data, shrink, f, mins)
            cand = deliver(trial)
            p = page_count(cand, "docx")
            report["renders"] += 1
            report["steps"].append({"factor": f, "effective": round(applied["ratio"], 3), "pages": p})
            data, final, report["pages"] = trial, cand, p
            report["factor"] = f
            report["effective_factor"] = round(applied["ratio"], 3)
            report["min_pt"] = applied["min_pt"]
            report["clamped"] = [names.get(i, i) for i in applied["clamped"]]
            if p <= tpl_pages:
                break

    # Page count alone is not success: text of a shrinkable field can sit outside an exact-height
    # row or a fixed-size frame, and the document would still have the right number of pages.
    # Checked on the laid-out delivered document (the page count above already rendered it,
    # so this reuses the cached PDF).
    lost, truncated = visible_field_check(final, before, names) if before else ([], [])
    report["lost_fields"] = lost
    report["truncated_fields"] = truncated
    return final, report
