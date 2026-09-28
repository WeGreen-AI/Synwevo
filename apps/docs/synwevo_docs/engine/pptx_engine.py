"""PPTX: slides, shapes, text frames and tables (DrawingML), edited as XML.

Locators: slide index → shape id (cNvPr/@id) → paragraph index → character offsets,
or table cell (row, col).  Word-style pagination rules are *not* applied and nothing
flows to a new slide automatically.  Tables that grow past the slide bottom are
reported; whether text fits its shape or cell is *not* measured (PowerPoint's own
autofit settings of the shape apply when the deck is opened).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json

from lxml import etree

from . import values as V


def _tpl_enum(req: dict) -> str:
    from .templates import EMPTY_POLICIES, _enum
    return _enum(req, "empty_policy", EMPTY_POLICIES["table"], "remove_row")

from .ooxml import NS, Package

A = NS["a"]
P = NS["p"]
EMU_PER_PX = 9525


def a(local):
    return "{%s}%s" % (A, local)


def p_(local):
    return "{%s}%s" % (P, local)


class PptxError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def slide_parts(pkg: Package) -> list[str]:
    main = pkg.main_part()
    pres = pkg.xml(main)
    out = []
    lst = pres.find(p_("sldIdLst"))
    if lst is None:
        return out
    for sid in lst.findall(p_("sldId")):
        rid = sid.get("{%s}id" % NS["r"])
        t = pkg.rel_target(main, rid)
        if t:
            out.append(t)
    return out


def slide_size(pkg: Package) -> tuple[int, int]:
    pres = pkg.xml(pkg.main_part())
    sz = pres.find(p_("sldSz"))
    if sz is None:
        return 9144000, 6858000
    return int(sz.get("cx")), int(sz.get("cy"))


def _shapes(tree):
    """Yield (shape element, offset) for sp/graphicFrame/pic, descending into groups."""
    for el in tree:
        tag = el.tag
        if tag in (p_("sp"), p_("graphicFrame"), p_("pic"), p_("cxnSp")):
            yield el
        elif tag == p_("grpSp"):
            yield from _shapes(el)


def _nv(el):
    for child in el:
        if child.tag.startswith("{%s}nv" % P):
            c = child.find(p_("cNvPr"))
            if c is not None:
                return c
    return None


def shape_id(el) -> str | None:
    nv = _nv(el)
    return nv.get("id") if nv is not None else None


def _xfrm(el):
    x = el.find(f"{p_('spPr')}/{a('xfrm')}")
    if x is None:
        x = el.find(p_("xfrm"))
    if x is None:
        return None
    off, ext = x.find(a("off")), x.find(a("ext"))
    if off is None or ext is None:
        return None
    return int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))


def para_runs(p):
    """Runs of a DrawingML paragraph with offsets.  a:br counts as '\n', a:fld as its text."""
    out = []
    pos = 0
    for child in p:
        if child.tag in (a("r"), a("fld")):
            t = child.find(a("t"))
            s = (t.text or "") if t is not None else ""
            out.append((pos, pos + len(s), child, s))
            pos += len(s)
        elif child.tag == a("br"):
            out.append((pos, pos + 1, child, "\n"))
            pos += 1
    return out


def para_text(p) -> str:
    return "".join(s for _, _, _, s in para_runs(p))


def _paras(txbody):
    return txbody.findall(a("p")) if txbody is not None else []


def _txbody(el):
    tb = el.find(p_("txBody"))
    return tb


def _find_shape(pkg: Package, slide: int, sid: str):
    parts = slide_parts(pkg)
    if not 0 <= slide < len(parts):
        raise PptxError("slide_missing", f"没有第 {slide + 1} 张幻灯片")
    root = pkg.xml(parts[slide])
    tree = root.find(f"{p_('cSld')}/{p_('spTree')}")
    for el in _shapes(tree):
        if shape_id(el) == str(sid):
            return parts[slide], el
    raise PptxError("shape_missing", f"第 {slide + 1} 张幻灯片中找不到形状 #{sid}")


def _table(el):
    return el.find(f"{a('graphic')}/{a('graphicData')}/{a('tbl')}")


def _cell(tbl, r, c):
    rows = tbl.findall(a("tr"))
    if not 0 <= r < len(rows):                  # a negative index would silently pick from the end
        raise PptxError("row_missing", f"表格没有第 {r + 1} 行")
    cells = rows[r].findall(a("tc"))
    if not 0 <= c < len(cells):
        raise PptxError("col_missing", f"表格没有第 {c + 1} 列")
    return cells[c]


def cell_text(tc) -> str:
    return "\n".join(para_text(p) for p in tc.findall(f"{a('txBody')}/{a('p')}"))


# ------------------------------------------------------------------ inspect / render
def inspect_pptx(data: bytes) -> dict:
    pkg = Package(data)
    parts = slide_parts(pkg)
    labels = []
    for part in parts[:30]:
        root = pkg.xml(part)
        tree = root.find(f"{p_('cSld')}/{p_('spTree')}")
        for el in _shapes(tree):
            nv = _nv(el)
            labels.append(nv.get("name") if nv is not None else "")
    inv = pkg.inventory()
    return {"slides": len(parts), "size": slide_size(pkg), "signature": hashlib.sha1(json.dumps(labels).encode()).hexdigest()[:16],
            "inventory": inv}


def render_pptx(data: bytes, slots: list[dict] | None = None, images: bool = True) -> dict:
    """Slides as JSON for the viewer.  ``images=False`` skips embedding pictures as data URLs
    (up to 2 MB each): callers that only read text (verification, search) should pass it."""
    pkg = Package(data)
    cx, cy = slide_size(pkg)
    slides = []
    marks = {}
    for sl in slots or []:
        loc = sl.get("locator", {})
        marks.setdefault((loc.get("slide"), str(loc.get("shape"))), []).append(sl)
    for si, part in enumerate(slide_parts(pkg)):
        root = pkg.xml(part)
        tree = root.find(f"{p_('cSld')}/{p_('spTree')}")
        shapes = []
        for el in _shapes(tree):
            sid = shape_id(el)
            nv = _nv(el)
            box = _xfrm(el)
            item = {"id": sid, "name": nv.get("name") if nv is not None else "", "box": box}
            if el.tag == p_("sp"):
                tb = _txbody(el)
                item["kind"] = "text" if tb is not None else "shape"
                item["paras"] = [_render_para(p) for p in _paras(tb)]
            elif el.tag == p_("graphicFrame") and _table(el) is not None:
                tbl = _table(el)
                item["kind"] = "table"
                item["rows"] = [[{"paras": [_render_para(p) for p in tc.findall(f"{a('txBody')}/{a('p')}")],
                                  "span": int(tc.get("gridSpan", "1")), "rspan": int(tc.get("rowSpan", "1")),
                                  "hmerge": tc.get("hMerge") == "1", "vmerge": tc.get("vMerge") == "1"}
                                 for tc in tr.findall(a("tc"))] for tr in tbl.findall(a("tr"))]
            elif el.tag == p_("pic"):
                item["kind"] = "picture"
                item["img"] = _pic_data(pkg, part, el) if images else None
            else:
                item["kind"] = "other"
            item["slots"] = [s["id"] for s in marks.get((si, sid), [])]
            shapes.append(item)
        slides.append({"index": si, "part": part, "shapes": shapes})
    return {"size": [cx, cy], "slides": slides}


def _render_para(p) -> dict:
    runs = []
    for s, e, el, txt in para_runs(p):
        st = {}
        rpr = el.find(a("rPr")) if el.tag != a("br") else None
        if rpr is not None:
            if rpr.get("b") == "1":
                st["b"] = 1
            if rpr.get("i") == "1":
                st["i"] = 1
            if rpr.get("sz"):
                st["sz"] = int(rpr.get("sz")) / 100
            col = rpr.find(f"{a('solidFill')}/{a('srgbClr')}")
            if col is not None:
                st["color"] = "#" + col.get("val")
        runs.append({"text": txt, "off": s, **st})
    ppr = p.find(a("pPr"))
    algn = ppr.get("algn") if ppr is not None else None
    return {"text": para_text(p), "runs": runs, "align": {"ctr": "center", "r": "right", "just": "justify"}.get(algn, "left")}


def _pic_data(pkg, part, el) -> str | None:
    blip = el.find(f".//{a('blip')}")
    if blip is None:
        return None
    rid = blip.get("{%s}embed" % NS["r"])
    t = pkg.rel_target(part, rid) if rid else None
    if not t or not pkg.has(t):
        return None
    data = pkg.read(t)
    ext = t.rsplit(".", 1)[-1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif"}.get(ext)
    if not mime or len(data) > 2 * 1024 * 1024:
        return None
    return f"data:{mime};base64," + base64.b64encode(data).decode()


# ------------------------------------------------------------------ source extraction
def extract_text(pkg: Package, loc: dict) -> str:
    try:
        _, el = _find_shape(pkg, int(loc["slide"]), loc["shape"])
    except PptxError:
        el = None
    if el is None and loc.get("shape_name"):
        # shape ids can differ between files made from the same deck – fall back to the unique shape name
        parts = slide_parts(pkg)
        if int(loc["slide"]) < len(parts):
            tree = pkg.xml(parts[int(loc["slide"])]).find(f"{p_('cSld')}/{p_('spTree')}")
            cands = [s for s in _shapes(tree) if (_nv(s) is not None and _nv(s).get("name") == loc["shape_name"])]
            if len(cands) == 1:
                el = cands[0]
    if el is None:
        from .resolve import ResolveError
        raise ResolveError("shape_missing", f"找不到第 {int(loc['slide']) + 1} 张幻灯片的形状「{loc.get('shape_name') or loc['shape']}」")
    if "row" in loc:
        tbl = _table(el)
        return cell_text(_cell(tbl, int(loc["row"]), int(loc["col"])))
    paras = _paras(_txbody(el))
    pi = int(loc.get("para", 0))
    if pi >= len(paras):
        from .resolve import ResolveError
        raise ResolveError("path_missing", f"第 {int(loc['slide']) + 1} 张幻灯片的形状「{loc.get('shape_name') or loc['shape']}」有 {len(paras)} 段，"
                                           f"代表文件中取值的是第 {pi + 1} 段")
    text = para_text(paras[pi])
    pre, suf = loc.get("prefix", ""), loc.get("suffix", "")
    s = len(pre) if pre and text.startswith(pre) else int(loc.get("start", 0))
    if pre and not text.startswith(pre):
        idx = text.find(pre[-12:])
        if idx < 0:
            from .resolve import ResolveError
            raise ResolveError("anchor_missing", f"形状文字中找不到标签「{pre[-12:]}」")
        s = idx + len(pre[-12:])
    e = len(text)
    if suf:
        k = text.find(suf[:12], s)
        e = k if k >= 0 else e
    elif loc.get("end") is not None and not pre:
        e = int(loc["end"])
    return text[s:e]


# ------------------------------------------------------------------ template slots
def _split_run(run, offset):
    t = run.find(a("t"))
    s = t.text or ""
    if offset <= 0 or offset >= len(s):
        return None
    new = copy.deepcopy(run)
    t.text = s[:offset]
    new.find(a("t")).text = s[offset:]
    run.addnext(new)
    return new


def split_para_at(p, offset):
    for s, e, el, txt in para_runs(p):
        if s < offset < e and el.tag == a("r"):
            _split_run(el, offset - s)
            return


def pptx_add_slot(work: bytes, req: dict, slots: list[dict]) -> tuple[bytes, dict]:
    from .templates import TemplateError, _base_slot
    pkg = Package(work)
    kind = req.get("kind", "text")
    name = req.get("name") or "字段"
    try:
        part, el = _find_shape(pkg, int(req["slide"]), req["shape"])
    except PptxError as e:
        raise TemplateError(e.code, str(e)) from e
    nv = _nv(el)
    loc = {"slide": int(req["slide"]), "shape": str(req["shape"]), "shape_name": nv.get("name") if nv is not None else None}
    if kind == "text":
        paras = _paras(_txbody(el))
        pi = int(req.get("para", 0))
        if pi >= len(paras):
            raise TemplateError("bad_para", "段落不存在")
        text = para_text(paras[pi])
        start, end = int(req["start"]), int(req["end"])
        mode = req.get("mode", "replace")
        if mode == "insert":
            start = end
        for s in slots:
            l2 = s.get("locator", {})
            if s["kind"] == "pptx_text" and l2.get("slide") == loc["slide"] and l2.get("shape") == loc["shape"] and l2.get("para") == pi:
                if not (end <= l2["start"] or start >= l2["end"]) or (start == end and l2["start"] < start < l2["end"]):
                    raise TemplateError("overlap", f"与已有字段「{s['name']}」重叠")
        split_para_at(paras[pi], start)
        split_para_at(paras[pi], end)
        slot = _base_slot(name, "pptx_text", mode=mode, original_text=text[start:end],
                          locator={**loc, "para": pi, "start": start, "end": end})
    elif kind in ("cell", "repeat", "region"):
        tbl = _table(el)
        if tbl is None:
            raise TemplateError("not_table", "该形状不是表格")
        _check_table_rows(req, kind, loc, slots)
        if kind == "cell":
            tc = _cell(tbl, int(req["row"]), int(req["col"]))
            slot = _base_slot(name, "pptx_text", mode="replace", original_text=cell_text(tc),
                              locator={**loc, "row": int(req["row"]), "col": int(req["col"])})
        elif kind == "repeat":
            rows = tbl.findall(a("tr"))
            ri = int(req["row"])
            if ri >= len(rows):
                raise TemplateError("bad_row", "表格中没有该行")
            hdr = [cell_text(tc) for tc in rows[ri - 1].findall(a("tc"))] if ri > 0 else []
            cols = [{"key": f"c{ci}", "name": (hdr[ci].strip() if ci < len(hdr) and hdr[ci].strip() else f"第{ci + 1}列"), "col": ci,
                     "original_text": cell_text(tc)} for ci, tc in enumerate(rows[ri].findall(a("tc")))]
            slot = _base_slot(name, "repeat", locator={**loc, "row": ri}, columns=cols,
                              empty_policy=_tpl_enum(req), placeholder=req.get("placeholder", "（无）"))
        else:
            r1, c1, r2, c2 = int(req["r1"]), int(req["c1"]), int(req["r2"]), int(req["c2"])
            slot = _base_slot(name, "region", locator={**loc, "r1": r1, "c1": c1, "r2": r2, "c2": c2}, rows=r2 - r1 + 1, cols=c2 - c1 + 1)
    else:
        raise TemplateError("bad_kind", f"PPTX 模板不支持的字段类型：{kind}")
    return pkg.save(), slot


def _slot_rows(sl: dict) -> set[int] | None:
    """Template table rows a table slot occupies (None for non-table slots)."""
    loc = sl.get("locator") or {}
    if sl.get("kind") == "repeat" and "row" in loc:
        return {int(loc["row"])}
    if sl.get("kind") == "pptx_text" and "row" in loc:
        return {int(loc["row"])}
    if sl.get("kind") == "region" and "r1" in loc:
        return set(range(int(loc["r1"]), int(loc["r2"]) + 1))
    return None


def _check_table_rows(req: dict, kind: str, loc: dict, slots: list[dict]) -> None:
    """A detail row is copied once per record and the template row is dropped, so a single
    cell or area on that row would have no defined target.  Refuse it when the field is made."""
    from .templates import TemplateError
    if kind == "repeat":
        mine = {int(req["row"])}
    elif kind == "cell":
        mine = {int(req["row"])}
    else:
        mine = set(range(int(req["r1"]), int(req["r2"]) + 1))
    for s in slots:
        l2 = s.get("locator") or {}
        if l2.get("slide") != loc["slide"] or str(l2.get("shape")) != loc["shape"]:
            continue
        rows = _slot_rows(s)
        if not rows or not rows & mine:
            continue
        if kind == "repeat" or s.get("kind") == "repeat":
            raise TemplateError("overlap", f"与已有字段「{s['name']}」位于同一表格行；重复明细行会按记录复制，不能再放单值或固定区域")


def pptx_remove_slot(work: bytes, slot: dict) -> bytes:
    return work  # slots are kept in the slot list; the working copy only has split runs (text unchanged)


def _set_para_range(p, start: int, end: int, text: str) -> None:
    from .docx_core import xml_text
    text = xml_text(text or "")
    split_para_at(p, start)
    split_para_at(p, end)
    runs = para_runs(p)
    inside = [x for x in runs if x[0] >= start and x[1] <= end and x[2].tag == a("r")]
    if not inside:
        # insertion point: clone the run before (or after) the position
        before = [x for x in runs if x[1] <= start and x[2].tag == a("r")]
        after = [x for x in runs if x[0] >= start and x[2].tag == a("r")]
        ref = (before[-1] if before else (after[0] if after else None))
        new = etree.Element(a("r"))
        if ref is not None and ref[2].find(a("rPr")) is not None:
            new.append(copy.deepcopy(ref[2].find(a("rPr"))))
        t = etree.SubElement(new, a("t"))
        t.text = text
        if before:
            before[-1][2].addnext(new)
        elif after:
            after[0][2].addprevious(new)
        else:
            end_para = p.find(a("endParaRPr"))
            if end_para is not None:
                end_para.addprevious(new)
            else:
                p.append(new)
        return
    first = inside[0][2]
    first.find(a("t")).text = text
    for x in inside[1:]:
        p.remove(x[2])


def _set_cell_text(tc, text: str) -> None:
    from .docx_core import xml_text
    text = xml_text(text or "")
    body = tc.find(a("txBody"))
    ps = body.findall(a("p"))
    p0 = ps[0]
    runs = [x for x in para_runs(p0) if x[2].tag == a("r")]
    rpr = runs[0][2].find(a("rPr")) if runs else p0.find(a("endParaRPr"))
    for child in list(p0):
        if child.tag in (a("r"), a("br"), a("fld")):
            p0.remove(child)
    for extra in ps[1:]:
        body.remove(extra)
    lines = text.split("\n")
    anchor_end = p0.find(a("endParaRPr"))
    for i, line in enumerate(lines):
        if i:
            br = etree.Element(a("br"))
            if rpr is not None:
                # a:br only takes a:rPr; the properties may have come from a:endParaRPr
                br_rpr = copy.deepcopy(rpr)
                br_rpr.tag = a("rPr")
                br.append(br_rpr)
            (anchor_end.addprevious(br) if anchor_end is not None else p0.append(br))
        r = etree.Element(a("r"))
        if rpr is not None:
            rr = copy.deepcopy(rpr)
            rr.tag = a("rPr")
            r.append(rr)
        t = etree.SubElement(r, a("t"))
        t.text = line
        (anchor_end.addprevious(r) if anchor_end is not None else p0.append(r))


def pptx_fill(work: bytes, slots: list[dict], values: dict[str, dict], options: dict | None = None) -> tuple[bytes, list[dict]]:
    pkg = Package(work)
    issues: list[dict] = []
    cx, cy = slide_size(pkg)
    # layout=fit ("keep the template's pages, or that copy fails"): a table running off the slide is
    # that copy not fitting, even though the slide count stays the same
    keep_layout = bool(options and options.get("layout_mode") == "fit")

    def err(sl, code, message):
        issues.append({"level": "error", "slot": sl["id"], "code": code, "message": message})

    # 1) Resolve every table target (a:tr / a:tc elements) against the *template* rows before
    #    anything is duplicated.  Locators hold template row indices; once detail rows are
    #    expanded those indices point at copied detail rows instead of the total or
    #    signature row below them.
    repeats: list[tuple[dict, object, object, object]] = []      # (slot, graphicFrame, tbl, template tr)
    targets: list[tuple[dict, object]] = []                      # (slot, tc or matrix of tc)
    repeat_rows: dict[int, dict] = {}                             # id(tr) -> repeat slot (tr kept alive in `repeats`)
    for sl in slots:
        loc = sl.get("locator", {})
        is_cell = sl["kind"] == "pptx_text" and "row" in loc
        if sl["kind"] not in ("repeat", "region") and not is_cell:
            continue
        try:
            _, el = _find_shape(pkg, loc["slide"], loc["shape"])
        except PptxError as e:
            err(sl, e.code, str(e))
            continue
        tbl = _table(el)
        if tbl is None:
            err(sl, "not_table", f"字段「{sl['name']}」所在的形状已不是表格")
            continue
        try:
            if sl["kind"] == "repeat":
                rows_el = tbl.findall(a("tr"))
                if not 0 <= int(loc["row"]) < len(rows_el):
                    raise PptxError("row_missing", f"表格没有第 {int(loc['row']) + 1} 行")
                tr = rows_el[int(loc["row"])]
                if id(tr) in repeat_rows:
                    raise PptxError("overlap", f"与重复明细「{repeat_rows[id(tr)]['name']}」使用同一表格行")
                repeat_rows[id(tr)] = sl
                repeats.append((sl, el, tbl, tr))
            elif is_cell:
                targets.append((sl, _cell(tbl, int(loc["row"]), int(loc["col"]))))
            else:
                targets.append((sl, [[_cell(tbl, int(loc["r1"]) + i, int(loc["c1"]) + j) for j in range(int(sl["cols"]))]
                                     for i in range(int(sl["rows"]))]))
        except PptxError as e:
            err(sl, e.code, f"字段「{sl['name']}」：{e}")
    # a single cell / area on a detail row has no defined target once the row is copied
    ok_targets = []
    for sl, tgt in targets:
        tcs = [tgt] if not isinstance(tgt, list) else [tc for row in tgt for tc in row]
        rep = next((repeat_rows[id(tc.getparent())] for tc in tcs if id(tc.getparent()) in repeat_rows), None)
        if rep is not None:
            err(sl, "overlap", f"字段「{sl['name']}」与重复明细「{rep['name']}」位于同一表格行，明细行按记录复制后无法确定写入位置；请把该字段移到明细行以外")
            continue
        ok_targets.append((sl, tgt))

    # 2) cells and areas, written into the resolved elements
    for sl, tgt in ok_targets:
        fv = values.get(sl["id"])
        if not isinstance(tgt, list):
            _set_cell_text(tgt, fv["value"].text if fv and fv.get("kind") == "scalar" else "")
            continue
        mat = (fv or {}).get("rows") or []
        for i, row in enumerate(tgt):
            for j, tc in enumerate(row):
                _set_cell_text(tc, mat[i][j].text if i < len(mat) and j < len(mat[i]) else "")

    # 3) detail rows: copies of the template row, inserted after it; the template row goes
    removed_frames: set[int] = set()
    for sl, el, tbl, tr in repeats:
        if id(el) in removed_frames:
            continue
        data = (values.get(sl["id"]) or {"rows": []})["rows"]
        h_tpl = int(tr.get("h", "0"))
        if not data:
            policy = sl.get("empty_policy", "remove_row")
            if policy == "remove_row":
                tbl.remove(tr)
                _grow_frame(el, -h_tpl)
            elif policy == "remove_table":
                el.getparent().remove(el)
                removed_frames.add(id(el))
            else:   # blank_row / placeholder (error never reaches the fill: the plan blocks it)
                tcs = tr.findall(a("tc"))
                for col in sl.get("columns", []):
                    if col["col"] < len(tcs):
                        _set_cell_text(tcs[col["col"]], sl.get("placeholder", "（无）") if (policy == "placeholder" and col["col"] == 0) else "")
            continue
        anchor = tr
        for rv in data:
            new = copy.deepcopy(tr)
            tcs = new.findall(a("tc"))
            for col in sl.get("columns", []):
                v = rv.get(col["key"])
                if col["col"] < len(tcs):
                    _set_cell_text(tcs[col["col"]], v.text if v is not None else "")
            anchor.addnext(new)
            anchor = new
        tbl.remove(tr)
        _grow_frame(el, h_tpl * (len(data) - 1))
        box = _xfrm(el)
        if box and box[1] + box[3] > cy and keep_layout:
            err(sl, "slide_overflow", f"重复明细「{sl['name']}」共 {len(data)} 行，表格超出幻灯片底部；已选择保持模板页数，"
                                      f"因此不产出这一份。请减少行数或调整模板")
        elif box and box[1] + box[3] > cy:
            issues.append({"level": "warning", "slot": sl["id"], "code": "slide_overflow",
                           "message": f"重复明细「{sl['name']}」共 {len(data)} 行，表格超出幻灯片底部；演示文稿不会自动分页，请减少行数或调整模板"})

    # 4) in-paragraph text, right-to-left per paragraph so earlier offsets stay valid
    by_para: dict[tuple, list[dict]] = {}
    for sl in slots:
        loc = sl.get("locator", {})
        if sl["kind"] == "pptx_text" and "row" not in loc:
            by_para.setdefault((loc["slide"], loc["shape"], loc["para"]), []).append(sl)
    for (slide, shape, para), sls in by_para.items():
        try:
            _, el = _find_shape(pkg, slide, shape)
        except PptxError as e:
            issues.append({"level": "error", "code": e.code, "message": str(e)})
            continue
        paras = _paras(_txbody(el))
        if para >= len(paras):
            issues.append({"level": "error", "code": "para_missing", "message": "模板段落已变化"})
            continue
        p = paras[para]
        text = para_text(p)
        for sl in sorted(sls, key=lambda s: -s["locator"]["start"]):
            loc = sl["locator"]
            if text[loc["start"]:loc["end"]] != sl.get("original_text", "") and sl.get("mode") != "insert":
                issues.append({"level": "error", "slot": sl["id"], "code": "anchor_changed",
                               "message": f"字段「{sl['name']}」位置的原文已变化，请重新设置该字段"})
                continue
            fv = values.get(sl["id"])
            _set_para_range(p, loc["start"], loc["end"], fv["value"].text if fv and fv.get("kind") == "scalar" else "")
    return pkg.save(), issues


def _grow_frame(el, delta_emu: int) -> None:
    x = el.find(p_("xfrm"))
    if x is None:
        return
    ext = x.find(a("ext"))
    if ext is not None:
        ext.set("cy", str(max(0, int(ext.get("cy", "0")) + delta_emu)))
