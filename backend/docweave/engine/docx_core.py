"""DOCX structural model: block paths, paragraph text, run splitting, field
marking (as content controls) and filling.

Invariant: marking a field never changes the text of a paragraph.  In "replace"
mode the content control wraps the original runs; in "insert" mode the control
is empty.  Hence character offsets taken from the rendered working copy are the
same as offsets in the original document.
"""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass

from lxml import etree

from .ooxml import NS, Package, q

W = NS["w"]
TAG_PREFIX = "dw:"


def w(local: str) -> str:
    return "{%s}%s" % (W, local)


class DocxError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ------------------------------------------------------------------ parts
REL_HEADER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header"
REL_FOOTER = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"


def list_parts(pkg: Package) -> list[dict]:
    """Main document part first, then headers/footers in relationship order."""
    main = pkg.main_part()
    parts = [{"part": main, "kind": "body", "label": "正文"}]
    hi = fi = 0
    for rel in pkg.rels(main):
        if rel.external:
            continue
        if rel.type == REL_HEADER:
            hi += 1
            parts.append({"part": pkg.resolve(main, rel.target), "kind": "header", "label": f"页眉 {hi}", "rid": rel.id})
        elif rel.type == REL_FOOTER:
            fi += 1
            parts.append({"part": pkg.resolve(main, rel.target), "kind": "footer", "label": f"页脚 {fi}", "rid": rel.id})
    return parts


def part_container(pkg: Package, part: str) -> etree._Element:
    root = pkg.xml(part)
    body = root.find(w("body"))
    return body if body is not None else root  # headers/footers: root is w:hdr/w:ftr


# ------------------------------------------------------------------ blocks & paths
_TRANSPARENT_BLOCK = {w("sdt"), w("customXml")}


def iter_blocks(container: etree._Element):
    """Yield block-level p/tbl in document order, descending into block sdt/customXml."""
    for child in container:
        tag = child.tag
        if tag == w("p") or tag == w("tbl"):
            yield child
        elif tag == w("sdt"):
            content = child.find(w("sdtContent"))
            if content is not None:
                yield from iter_blocks(content)
        elif tag == w("customXml"):
            yield from iter_blocks(child)


def iter_rows(tbl):
    for child in tbl:
        if child.tag == w("tr"):
            yield child
        elif child.tag in _TRANSPARENT_BLOCK:
            inner = child.find(w("sdtContent")) if child.tag == w("sdt") else child
            if inner is not None:
                for c in inner:
                    if c.tag == w("tr"):
                        yield c


def iter_cells(tr):
    for child in tr:
        if child.tag == w("tc"):
            yield child
        elif child.tag in _TRANSPARENT_BLOCK:
            inner = child.find(w("sdtContent")) if child.tag == w("sdt") else child
            if inner is not None:
                for c in inner:
                    if c.tag == w("tc"):
                        yield c


def parse_path(path: str) -> list[int]:
    try:
        return [int(x) for x in str(path).split(":")]
    except ValueError as e:
        raise DocxError("bad_path", f"无效的文档路径：{path}") from e


def fmt_path(parts: list[int]) -> str:
    return ":".join(str(p) for p in parts)


def get_block(container, path: str):
    """Resolve 'b' or 'b:r:c:b...' to an element (p or tbl)."""
    steps = parse_path(path)
    blocks = list(iter_blocks(container))
    if not steps or steps[0] >= len(blocks):
        raise DocxError("path_missing", f"文档中不存在第 {steps[0] + 1 if steps else '?'} 个内容块")
    el = blocks[steps[0]]
    i = 1
    while i < len(steps):
        if el.tag != w("tbl"):
            raise DocxError("path_missing", "文档结构已变化：期望表格")
        rows = list(iter_rows(el))
        if i + 2 >= len(steps):
            raise DocxError("bad_path", "路径不完整")
        r, c, b = steps[i], steps[i + 1], steps[i + 2]
        if r >= len(rows):
            raise DocxError("path_missing", f"表格没有第 {r + 1} 行")
        cells = list(iter_cells(rows[r]))
        if c >= len(cells):
            raise DocxError("path_missing", f"表格第 {r + 1} 行没有第 {c + 1} 个单元格")
        cblocks = list(iter_blocks(cells[c]))
        if b >= len(cblocks):
            raise DocxError("path_missing", "单元格内容块不存在")
        el = cblocks[b]
        i += 3
    return el


def get_table_cell(container, table_path: str, r: int, c: int):
    tbl = get_block(container, table_path)
    if tbl.tag != w("tbl"):
        raise DocxError("path_missing", "目标位置不是表格")
    rows = list(iter_rows(tbl))
    if r >= len(rows):
        raise DocxError("path_missing", f"表格没有第 {r + 1} 行")
    cells = list(iter_cells(rows[r]))
    if c >= len(cells):
        raise DocxError("path_missing", f"表格第 {r + 1} 行没有第 {c + 1} 个单元格")
    return cells[c]


def cell_first_paragraph(tc):
    for b in iter_blocks(tc):
        if b.tag == w("p"):
            return b
    p = etree.SubElement(tc, w("p"))
    return p


# ------------------------------------------------------------------ paragraph text
_P_CONTAINERS = {w("hyperlink"), w("ins"), w("smartTag"), w("customXml"), w("fldSimple"), w("bdo"), w("dir"),
                 w("moveTo")}
_P_SKIP = {w("pPr"), w("del"), w("moveFrom"), w("proofErr"), w("bookmarkStart"), w("bookmarkEnd"),
           w("commentRangeStart"), w("commentRangeEnd"), w("permStart"), w("permEnd")}
OBJ = "￼"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


@dataclass
class Seg:
    start: int
    end: int
    run: etree._Element
    child: etree._Element
    kind: str
    text: str


def _child_text(child) -> tuple[str, str] | None:
    tag = child.tag
    if tag == w("t"):
        return "t", child.text or ""
    if tag == w("tab"):
        return "tab", "\t"
    if tag in (w("br"), w("cr")):
        return "br", "\n"
    if tag == w("noBreakHyphen"):
        return "nbh", "-"
    if tag == w("sym"):
        ch = child.get(w("char")) or ""
        try:
            code = int(ch, 16)
            if 0xF000 <= code <= 0xF0FF:
                code -= 0xF000
            return "sym", chr(code) if code else OBJ
        except ValueError:
            return "sym", OBJ
    if tag in (w("drawing"), w("pict"), w("object"), w("footnoteReference"), w("endnoteReference"),
               "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"):
        return "obj", OBJ
    return None


def _runs_in(el, in_field_code: list[bool]):
    """Yield runs (and math objects) in document order within a paragraph-level container."""
    for child in el:
        tag = child.tag
        if not isinstance(tag, str):
            continue
        if tag == w("r"):
            yield child
        elif tag == w("sdt"):
            content = child.find(w("sdtContent"))
            if content is not None:
                yield from _runs_in(content, in_field_code)
        elif tag in _P_CONTAINERS:
            yield from _runs_in(child, in_field_code)
        elif tag.startswith("{%s}" % MATH_NS):
            yield child  # OMML is treated as one object character
        elif tag in _P_SKIP:
            continue


def para_segments(p) -> tuple[str, list[Seg]]:
    segs: list[Seg] = []
    buf = []
    pos = 0
    field_depth = 0
    in_code = False
    for run in _runs_in(p, [False]):
        if run.tag.startswith("{%s}" % MATH_NS):
            segs.append(Seg(pos, pos + 1, run, run, "obj", OBJ))
            buf.append(OBJ)
            pos += 1
            continue
        for child in run:
            if child.tag == w("fldChar"):
                t = child.get(w("fldCharType"))
                if t == "begin":
                    field_depth += 1
                    in_code = True
                elif t == "separate":
                    in_code = False
                elif t == "end":
                    field_depth = max(0, field_depth - 1)
                    in_code = False
                continue
            if child.tag == w("instrText") or (in_code and field_depth):
                continue
            ct = _child_text(child)
            if ct is None:
                continue
            kind, s = ct
            if not s:
                continue
            segs.append(Seg(pos, pos + len(s), run, child, kind, s))
            buf.append(s)
            pos += len(s)
    return "".join(buf), segs


def para_text(p) -> str:
    return para_segments(p)[0]


def block_text(el) -> str:
    if el.tag == w("p"):
        return para_text(el)
    if el.tag == w("tbl"):
        rows = []
        for tr in iter_rows(el):
            rows.append("\t".join("\n".join(block_text(b) for b in iter_blocks(tc)) for tc in iter_cells(tr)))
        return "\n".join(rows)
    return ""


def cell_text(tc) -> str:
    return "\n".join(block_text(b) for b in iter_blocks(tc))


# ------------------------------------------------------------------ run splitting
def _new_t(text: str):
    t = etree.Element(w("t"))
    t.text = text
    if text != text.strip() or "  " in text:
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    return t


def _split_run_at(run, offset_in_run: int):
    """Split ``run`` so that the first ``offset_in_run`` characters stay in it and the
    rest move to a new sibling run which is returned.  Returns None if no split needed."""
    rpr = run.find(w("rPr"))
    content = [c for c in run if c.tag != w("rPr")]
    acc = 0
    split_at_child = None
    for idx, child in enumerate(content):
        ct = _child_text(child)
        ln = len(ct[1]) if ct else 0
        if child.tag == w("instrText"):
            ln = 0
        if acc == offset_in_run and ln > 0:
            split_at_child = (idx, 0)
            break
        if acc < offset_in_run < acc + ln:
            split_at_child = (idx, offset_in_run - acc)
            break
        acc += ln
    if split_at_child is None:
        return None
    idx, inner = split_at_child
    new_run = etree.Element(w("r"))
    for k, v in run.attrib.items():
        new_run.set(k, v)
    if rpr is not None:
        new_run.append(copy.deepcopy(rpr))
    child = content[idx]
    if inner > 0:
        # split a w:t text node
        text = child.text or ""
        child.text = text[:inner]
        if child.text != child.text.strip():
            child.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        new_run.append(_new_t(text[inner:]))
        moving = content[idx + 1:]
    else:
        moving = content[idx:]
    for m in moving:
        new_run.append(m)  # lxml moves the element
    run.addnext(new_run)
    return new_run


def split_at(p, offset: int) -> None:
    """Ensure a run boundary exists at text ``offset`` in paragraph ``p``."""
    _, segs = para_segments(p)
    for s in segs:
        if s.start < offset < s.end or (s.start == offset):
            if s.start == offset:
                # boundary at a segment start: split the run before this child if needed
                run_segs = [x for x in segs if x.run is s.run]
                if run_segs and run_segs[0].start == offset:
                    return
                _split_run_at(s.run, offset - run_segs[0].start)
                return
            run_segs = [x for x in segs if x.run is s.run]
            _split_run_at(s.run, offset - run_segs[0].start)
            return


# ------------------------------------------------------------------ content controls
def _sdt_id(key: str) -> str:
    h = int(hashlib.sha1(key.encode()).hexdigest()[:8], 16)
    return str(h % 2_000_000_000 + 1)


def make_sdt(tag: str, alias: str, rpr=None, runs=None):
    sdt = etree.Element(w("sdt"))
    pr = etree.SubElement(sdt, w("sdtPr"))
    if rpr is not None:
        pr.append(copy.deepcopy(rpr))
    a = etree.SubElement(pr, w("alias"))
    a.set(w("val"), alias[:250] or "字段")
    t = etree.SubElement(pr, w("tag"))
    t.set(w("val"), tag)
    i = etree.SubElement(pr, w("id"))
    i.set(w("val"), _sdt_id(tag))
    content = etree.SubElement(sdt, w("sdtContent"))
    for r in runs or []:
        content.append(r)
    return sdt


def sdt_tag(sdt) -> str | None:
    pr = sdt.find(w("sdtPr"))
    if pr is None:
        return None
    t = pr.find(w("tag"))
    return t.get(w("val")) if t is not None else None


def iter_field_sdts(root):
    for sdt in root.iter(w("sdt")):
        tag = sdt_tag(sdt)
        if tag and tag.startswith(TAG_PREFIX):
            yield tag[len(TAG_PREFIX):], sdt


def _ancestor_chain(el, stop):
    chain = []
    cur = el
    while cur is not None and cur is not stop:
        chain.append(cur)
        cur = cur.getparent()
    return chain


def wrap_range(p, start: int, end: int, tag: str, alias: str):
    """Wrap text [start, end) of paragraph ``p`` in a run-level content control.
    ``start == end`` inserts an empty control at that position."""
    text, _ = para_segments(p)
    if start < 0 or end > len(text) or start > end:
        raise DocxError("bad_range", f"选区超出段落范围（段落长度 {len(text)}）")
    for existing_tag, sdt in iter_field_sdts(p):
        # overlap check against existing fields in this paragraph
        s0, e0 = sdt_range(p, sdt)
        if start == end:
            if s0 < start < e0:
                raise DocxError("overlap", "插入位置位于已有字段内部")
        elif not (end <= s0 or start >= e0) or (s0 == e0 and start < s0 < end):
            raise DocxError("overlap", "选区与已有字段重叠，请先删除原字段")
    if start == end:
        return _insert_empty(p, start, tag, alias)
    split_at(p, start)
    split_at(p, end)
    _, segs = para_segments(p)
    inside = [s for s in segs if s.start >= start and s.end <= end]
    if not inside:
        raise DocxError("bad_range", "选区中没有可替换的文字")
    runs = []
    for s in inside:
        if s.run not in runs:
            runs.append(s.run)
    # lowest common ancestor under p
    chains = [_ancestor_chain(r, p) for r in runs]
    # top-level ancestors (direct children of the common container)
    common = p
    depth = 0
    while True:
        cand = {id(c[-1 - depth]) if len(c) > depth else None for c in chains}
        if len(cand) == 1 and None not in cand and all(len(c) > depth + 1 for c in chains):
            common = chains[0][-1 - depth]
            depth += 1
            continue
        break
    tops = []
    for c in chains:
        top = c[-1 - depth] if len(c) > depth else c[0]
        if top not in tops:
            tops.append(top)
    siblings = list(common)
    idxs = sorted(siblings.index(t) for t in tops)
    span = siblings[idxs[0]: idxs[-1] + 1]
    # every run within span must be fully inside the selection
    for el in span:
        for r in ([el] if el.tag == w("r") else list(el.iter(w("r")))):
            rs = [s for s in segs if s.run is r]
            if rs and (rs[0].start < start or rs[-1].end > end):
                raise DocxError("cross_boundary", "选区跨越了超链接、修订或其他结构的边界，请缩小或扩大选区使其完整包含该结构")
    first_run = runs[0]
    rpr = first_run.find(w("rPr"))
    sdt = make_sdt(tag, alias, rpr)
    span[0].addprevious(sdt)
    content = sdt.find(w("sdtContent"))
    for el in span:
        content.append(el)
    return sdt


def _insert_empty(p, offset: int, tag: str, alias: str):
    split_at(p, offset)
    _, segs = para_segments(p)
    before = [s for s in segs if s.end <= offset]
    after = [s for s in segs if s.start >= offset]
    ref_run = before[-1].run if before else (after[0].run if after else None)
    rpr = ref_run.find(w("rPr")) if ref_run is not None else None
    empty_run = etree.Element(w("r"))
    if rpr is not None:
        empty_run.append(copy.deepcopy(rpr))
    sdt = make_sdt(tag, alias, rpr, [empty_run])
    if after:
        anchor = after[0].run
        # insert before the top-level element holding anchor
        while anchor.getparent() is not p and anchor.getparent() is not None and anchor.getparent().tag != w("sdtContent"):
            anchor = anchor.getparent()
        anchor.addprevious(sdt)
    elif before:
        anchor = before[-1].run
        while anchor.getparent() is not p and anchor.getparent() is not None and anchor.getparent().tag != w("sdtContent"):
            anchor = anchor.getparent()
        anchor.addnext(sdt)
    else:
        ppr = p.find(w("pPr"))
        if ppr is not None:
            ppr.addnext(sdt)
        else:
            p.insert(0, sdt)
    return sdt


def sdt_range(p, sdt) -> tuple[int, int]:
    """Text range of an inline sdt within paragraph p."""
    _, segs = para_segments(p)
    content = sdt.find(w("sdtContent"))
    inside = [s for s in segs if content is not None and _is_descendant(s.run, content)]
    if inside:
        return inside[0].start, inside[-1].end
    # empty control: position = end of preceding text
    pos = 0
    for s in segs:
        if _precedes(s.run, sdt):
            pos = s.end
    return pos, pos


def _is_descendant(el, anc) -> bool:
    cur = el
    while cur is not None:
        if cur is anc:
            return True
        cur = cur.getparent()
    return False


def _precedes(a, b) -> bool:
    """True if element a comes before element b in document order."""
    return _doc_index(a) < _doc_index(b)


def _doc_index(el) -> tuple:
    cur = el
    path = []
    while cur is not None:
        parent = cur.getparent()
        if parent is None:
            break
        path.append(list(parent).index(cur))
        cur = parent
    # lexicographic compare via tuple
    return tuple(reversed(path))


def unwrap_sdt(sdt) -> None:
    content = sdt.find(w("sdtContent"))
    parent = sdt.getparent()
    if content is not None:
        for child in list(content):
            sdt.addprevious(child)
    parent.remove(sdt)


def value_runs(text: str, rpr) -> list:
    """Build runs for ``text`` using run properties ``rpr``; newlines → w:br, tabs → w:tab."""
    runs = []
    r = etree.Element(w("r"))
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    lines = text.split("\n")
    for li, line in enumerate(lines):
        if li:
            etree.SubElement(r, w("br"))
        parts = line.split("\t")
        for pi, part in enumerate(parts):
            if pi:
                etree.SubElement(r, w("tab"))
            if part:
                r.append(_new_t(part))
    runs.append(r)
    return runs


def sdt_template_rpr(sdt):
    content = sdt.find(w("sdtContent"))
    if content is not None:
        for r in content.iter(w("r")):
            rpr = r.find(w("rPr"))
            return rpr
    pr = sdt.find(w("sdtPr"))
    if pr is not None:
        return pr.find(w("rPr"))
    return None


def apply_style_overrides(rpr, style: dict | None):
    """Explicit 'copy source style' option: bold/italic/underline/color only."""
    if not style:
        return rpr
    rpr = copy.deepcopy(rpr) if rpr is not None else etree.Element(w("rPr"))

    def _set(tag, val=None):
        el = rpr.find(w(tag))
        if el is None:
            el = etree.SubElement(rpr, w(tag))
        if val is not None:
            el.set(w("val"), val)
        return el

    if style.get("b"):
        _set("b")
    if style.get("i"):
        _set("i")
    if style.get("u"):
        _set("u", "single")
    if style.get("color"):
        _set("color", style["color"].lstrip("#"))
    return rpr


def fill_sdt(sdt, text: str, keep_control: bool = False, style: dict | None = None) -> None:
    rpr = sdt_template_rpr(sdt)
    rpr = apply_style_overrides(rpr, style)
    content = sdt.find(w("sdtContent"))
    parent = sdt.getparent()
    runs = value_runs(text, rpr)
    if content is not None and content.getparent() is not None and _is_block_sdt(sdt):
        # block-level: fill first paragraph only
        ps = list(content.iter(w("p")))
        if ps:
            p = ps[0]
            for child in list(p):
                if child.tag != w("pPr"):
                    p.remove(child)
            for r in runs:
                p.append(r)
            for extra in ps[1:]:
                extra.getparent().remove(extra)
        if not keep_control:
            unwrap_sdt(sdt)
        return
    if keep_control:
        for child in list(content):
            content.remove(child)
        for r in runs:
            content.append(r)
        pr = sdt.find(w("sdtPr"))
        ph = pr.find(w("showingPlcHdr")) if pr is not None else None
        if ph is not None:
            pr.remove(ph)
        return
    for r in runs:
        sdt.addprevious(r)
    parent.remove(sdt)


def _is_block_sdt(sdt) -> bool:
    content = sdt.find(w("sdtContent"))
    return content is not None and any(c.tag in (w("p"), w("tbl")) for c in content)


def remove_fields(root) -> None:
    """Remove field wrappers restoring original content (used for 'clean' copies)."""
    for _, sdt in list(iter_field_sdts(root)):
        unwrap_sdt(sdt)


# ------------------------------------------------------------------ table helpers
def tc_props(tc) -> dict:
    pr = tc.find(w("tcPr"))
    span, vmerge = 1, None
    if pr is not None:
        gs = pr.find(w("gridSpan"))
        if gs is not None:
            span = int(gs.get(w("val"), "1"))
        vm = pr.find(w("vMerge"))
        if vm is not None:
            vmerge = vm.get(w("val"), "continue")
    return {"span": span, "vmerge": vmerge}


def set_row_header(tr, on: bool = True) -> None:
    trpr = tr.find(w("trPr"))
    if trpr is None:
        trpr = etree.Element(w("trPr"))
        tblpr_ex = tr.find(w("tblPrEx"))
        if tblpr_ex is not None:
            tblpr_ex.addnext(trpr)
        else:
            tr.insert(0, trpr)
    el = trpr.find(w("tblHeader"))
    if on and el is None:
        etree.SubElement(trpr, w("tblHeader"))
    elif not on and el is not None:
        trpr.remove(el)


def set_row_cant_split(tr) -> None:
    trpr = tr.find(w("trPr"))
    if trpr is None:
        trpr = etree.Element(w("trPr"))
        tblpr_ex = tr.find(w("tblPrEx"))
        if tblpr_ex is not None:
            tblpr_ex.addnext(trpr)
        else:
            tr.insert(0, trpr)
    if trpr.find(w("cantSplit")) is None:
        trpr.insert(0, etree.Element(w("cantSplit")))


def clear_cell_text(tc) -> None:
    for p in list(iter_blocks(tc)):
        if p.tag != w("p"):
            continue
        for child in list(p):
            if child.tag != w("pPr"):
                p.remove(child)


_WS_RE = re.compile(r"\s+")


def norm_text(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace(OBJ, "")).strip()
