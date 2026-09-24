"""Repeating content blocks in DOCX templates (not table rows).

A block is a run of consecutive block-level elements (paragraphs, tables) in one
container – the body, a header/footer or a table cell.  In the working copy it is
wrapped in a *block-level* content control tagged ``dw:<slot id>``; its detail
columns are inline content controls tagged ``dw:<slot id>#<key>`` inside it.
When filling, the block's content is copied once per detail row, the column
controls in each copy receive that row's values, and the wrapper disappears.
Block-level content controls do not change the flattened block paths used
elsewhere (``iter_blocks`` looks through them).
"""
from __future__ import annotations

import copy
import re

from lxml import etree

from . import docx_core as dc

_LABEL_VALUE = re.compile(r"([^\s：:，,；;。、]{1,12})\s*[：:]\s*")
_SEP_TRAIL = " \t，,；;。、"
_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


class BlockError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _parent_prefix(path: str) -> tuple[list[int], int]:
    steps = dc.parse_path(path)
    return steps[:-1], steps[-1]


def wrap_blocks(container, first: str, last: str, tag: str, alias: str):
    """Wrap the blocks ``first``..``last`` (flattened paths, same container) in a block-level
    content control.  Returns the new ``w:sdt``."""
    p1, i1 = _parent_prefix(first)
    p2, i2 = _parent_prefix(last)
    if p1 != p2:
        raise BlockError("different_container", "所选段落不在同一个位置（例如一部分在表格单元格内），不能组成一个内容块")
    if i2 < i1:
        first, last, i1, i2 = last, first, i2, i1
    a = dc.get_block(container, first)
    b = dc.get_block(container, last)
    parent = a.getparent()
    if b.getparent() is not parent:
        raise BlockError("different_parent", "所选内容跨越了已有的内容控件边界，请调整选择范围")
    kids = list(parent)
    ia, ib = kids.index(a), kids.index(b)
    members = kids[ia:ib + 1]
    for el in members:
        if el.find(f".//{dc.w('sectPr')}") is not None:
            raise BlockError("section_break", "所选内容包含分节符，分节符不能随内容块重复；请不要选中节的最后一段")
        for sdt in el.iter(dc.w("sdt")):
            t = dc.sdt_tag(sdt) or ""
            if t.startswith(dc.TAG_PREFIX) and dc._is_block_sdt(sdt):
                raise BlockError("nested_block", "所选内容中已包含另一个重复内容块，不能嵌套")
        if el.tag == dc.w("tbl"):
            for sdt in el.iter(dc.w("sdt")):
                t = dc.sdt_tag(sdt) or ""
                if t.startswith(dc.TAG_PREFIX) and "#" in t:
                    raise BlockError("contains_repeat_row", "所选内容中的表格已有重复明细行，不能再放进重复内容块")
    anc = parent
    while anc is not None:
        if anc.tag == dc.w("sdt") and (dc.sdt_tag(anc) or "").startswith(dc.TAG_PREFIX):
            raise BlockError("nested_block", "所选内容已在另一个重复内容块中，不能嵌套")
        anc = anc.getparent()
    sdt = dc.make_sdt(tag, alias)
    a.addprevious(sdt)
    content = sdt.find(dc.w("sdtContent"))
    for el in members:
        content.append(el)
    return sdt


def find_block(root, slot_id: str):
    for tag, sdt in dc.iter_field_sdts(root):
        if tag == slot_id and dc._is_block_sdt(sdt):
            return sdt
    return None


def block_paragraphs(sdt) -> list:
    return [p for p in sdt.iter(dc.w("p"))]


def auto_columns(sdt, slot_id: str) -> list[dict]:
    """Wrap the value of every 「标签：值」 in the block's paragraphs as a column field.
    Values already inside another field are left alone."""
    cols: list[dict] = []
    used_names: dict[str, int] = {}
    for p in block_paragraphs(sdt):
        # never inside an existing field, and skip paragraphs that already carry fields
        if any((dc.sdt_tag(s) or "").startswith(dc.TAG_PREFIX) for s in p.iter(dc.w("sdt"))):
            continue
        text = dc.para_text(p)
        found = list(_LABEL_VALUE.finditer(text))
        spans = []
        for k, m in enumerate(found):
            start = m.end()
            end = found[k + 1].start() if k + 1 < len(found) else len(text)
            while end > start and (text[end - 1] in _SEP_TRAIL or text[end - 1].isspace()):
                end -= 1
            while start < end and (text[start] in _SEP_TRAIL or text[start].isspace()):
                start += 1
            if end > start:
                spans.append((m.group(1), start, end))
        planned = []
        for label, start, end in spans:              # keys and names in reading order
            n = used_names.get(label, 0)
            used_names[label] = n + 1
            planned.append((f"c{len(cols) + len(planned)}", label if n == 0 else f"{label}{n + 1}", start, end))
        for key, name, start, end in reversed(planned):   # wrap right-to-left
            dc.wrap_range(p, start, end, f"{dc.TAG_PREFIX}{slot_id}#{key}", name)
        cols.extend({"key": key, "name": name, "original_text": text[start:end]} for key, name, start, end in planned)
    return cols


def add_column(sdt, slot_id: str, p, start: int, end: int, name: str, existing: list[dict]) -> dict:
    if not _inside(p, sdt):
        raise BlockError("outside_block", "所选文字不在该重复内容块内")
    n = 0
    keys = {c["key"] for c in existing}
    while f"c{n}" in keys:
        n += 1
    key = f"c{n}"
    text = dc.para_text(p)
    dc.wrap_range(p, start, end, f"{dc.TAG_PREFIX}{slot_id}#{key}", name)
    return {"key": key, "name": name, "original_text": text[start:end]}


def _inside(el, anc) -> bool:
    cur = el
    while cur is not None:
        if cur is anc:
            return True
        cur = cur.getparent()
    return False


def remove_column(root, slot_id: str, key: str) -> bool:
    hit = False
    for tag, sdt in list(dc.iter_field_sdts(root)):
        if tag == f"{slot_id}#{key}":
            dc.unwrap_sdt(sdt)
            hit = True
    return hit


def _page_break_paragraph():
    p = etree.Element(dc.w("p"))
    r = etree.SubElement(p, dc.w("r"))
    br = etree.SubElement(r, dc.w("br"))
    br.set(dc.w("type"), "page")
    return p


def _max_docpr_id(root) -> int:
    ids = [0]
    for el in root.iter("{%s}docPr" % _WP_NS):
        try:
            ids.append(int(el.get("id", "0")))
        except ValueError:
            pass
    return max(ids)


def fill_block(root, sl: dict, rows: list[dict], keep: bool, issues: list[dict], report_missing: bool = True) -> bool:
    """Replace the block with one filled copy per row (``rows``: column key → Value).

    Returns False when ``root`` does not contain the block.  A caller that visits every
    part of a document (body, headers, footers) passes ``report_missing=False`` and
    reports ``block_lost`` only when no part had it: a block lives in exactly one part."""
    sdt = find_block(root, sl["id"])
    if sdt is None:
        if report_missing:
            issues.append({"level": "error", "slot": sl["id"], "code": "block_lost", "message": f"重复内容块「{sl['name']}」在模板中已找不到"})
        return False
    content = sdt.find(dc.w("sdtContent"))
    proto = list(content)
    policy = sl.get("empty_policy", "remove")
    if not rows:
        if policy == "placeholder":
            rows = [{(sl.get("columns") or [{"key": ""}])[0]["key"]: None}]
            placeholder = sl.get("placeholder", "（无）")
        else:
            parent = sdt.getparent()
            parent.remove(sdt)
            _keep_valid(parent)
            return True
    else:
        placeholder = None
    sep = sl.get("separator", "none")
    next_docpr = _max_docpr_id(root) + 1
    out = []
    for i, row in enumerate(rows):
        if i > 0 and sep == "page_break":
            out.append(_page_break_paragraph())
        elif i > 0 and sep == "blank":
            out.append(etree.Element(dc.w("p")))
        clones = [copy.deepcopy(el) for el in proto]
        for el in clones:
            if i > 0:
                # bookmarks must stay unique: only the first copy keeps them
                for bm in list(el.iter(dc.w("bookmarkStart"))) + list(el.iter(dc.w("bookmarkEnd"))):
                    bm.getparent().remove(bm)
                for dp in el.iter("{%s}docPr" % _WP_NS):
                    dp.set("id", str(next_docpr))
                    next_docpr += 1
            for tag, fsdt in list(dc.iter_field_sdts(el)):
                if not tag.startswith(sl["id"] + "#"):
                    continue            # other fields inside the block are filled by the caller
                key = tag.split("#", 1)[1]
                v = row.get(key)
                text = v.text if v is not None else (placeholder if placeholder and key == next(iter(row)) else "")
                dc.fill_sdt(fsdt, text or "", keep)
                if keep:
                    idel = fsdt.find(f"{dc.w('sdtPr')}/{dc.w('id')}")
                    if idel is not None:
                        idel.set(dc.w("val"), dc._sdt_id(f"{tag}/{i}"))
        out.extend(clones)
    for el in out:
        sdt.addprevious(el)
    parent = sdt.getparent()
    parent.remove(sdt)
    _keep_valid(parent)
    return True


def _keep_valid(parent) -> None:
    """A table cell must end with a paragraph, and headers/footers need at least one."""
    kids = [c for c in parent if isinstance(c.tag, str) and c.tag in (dc.w("p"), dc.w("tbl"), dc.w("sdt"))]
    if parent.tag == dc.w("tc") and (not kids or kids[-1].tag != dc.w("p")):
        etree.SubElement(parent, dc.w("p"))
    elif parent.tag in (dc.w("hdr"), dc.w("ftr")) and not kids:
        etree.SubElement(parent, dc.w("p"))
