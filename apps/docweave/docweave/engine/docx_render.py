"""Render a DOCX package to a JSON structure the browser can display and select.

The renderer is used for source documents, templates (showing field markers)
and generated outputs (preview) so all three share one interpretation.
"""
from __future__ import annotations

import base64

from lxml import etree

from .docx_core import (MATH_NS, OBJ, TAG_PREFIX, iter_blocks, iter_cells, iter_rows, list_parts, part_container,
                        sdt_tag, tc_props, w, _child_text)
from .ooxml import NS, Package

EMU_PER_PX = 9525
MAX_INLINE_IMAGE = 2 * 1024 * 1024


class StyleSheet:
    def __init__(self, pkg: Package, main: str):
        self.styles: dict[str, dict] = {}
        self.defaults_r: dict = {}
        self.defaults_p: dict = {}
        self.default_para_style = None
        styles_part = None
        for rel in pkg.rels(main):
            if rel.type.endswith("/styles"):
                styles_part = pkg.resolve(main, rel.target)
        if styles_part and pkg.has(styles_part):
            root = pkg.xml(styles_part)
            dd = root.find(w("docDefaults"))
            if dd is not None:
                rpr = dd.find(f"{w('rPrDefault')}/{w('rPr')}")
                ppr = dd.find(f"{w('pPrDefault')}/{w('pPr')}")
                self.defaults_r = run_props(rpr)
                self.defaults_p = para_props(ppr)
            for st in root.findall(w("style")):
                sid = st.get(w("styleId"))
                name_el = st.find(w("name"))
                based = st.find(w("basedOn"))
                self.styles[sid] = {
                    "type": st.get(w("type")),
                    "name": name_el.get(w("val")) if name_el is not None else sid,
                    "based": based.get(w("val")) if based is not None else None,
                    "r": run_props(st.find(w("rPr"))),
                    "p": para_props(st.find(w("pPr"))),
                    "default": st.get(w("default")) in ("1", "true"),
                }
                if self.styles[sid]["default"] and self.styles[sid]["type"] == "paragraph":
                    self.default_para_style = sid
        self._cache: dict[tuple, tuple[dict, dict]] = {}

    def chain(self, sid: str | None) -> list[dict]:
        out, seen = [], set()
        while sid and sid in self.styles and sid not in seen:
            seen.add(sid)
            out.append(self.styles[sid])
            sid = self.styles[sid]["based"]
        return list(reversed(out))

    def resolve(self, pstyle: str | None, rstyle: str | None = None) -> tuple[dict, dict]:
        key = (pstyle, rstyle)
        if key in self._cache:
            return self._cache[key]
        r = dict(self.defaults_r)
        p = dict(self.defaults_p)
        for st in self.chain(pstyle or self.default_para_style):
            r.update(st["r"])
            p.update(st["p"])
        for st in self.chain(rstyle):
            r.update(st["r"])
        self._cache[key] = (r, p)
        return r, p

    def name(self, sid: str | None) -> str | None:
        if sid and sid in self.styles:
            return self.styles[sid]["name"]
        return None


def _val(el, attr="val"):
    return el.get(w(attr)) if el is not None else None


def _on(el) -> bool | None:
    if el is None:
        return None
    v = el.get(w("val"))
    return v not in ("0", "false", "off")


def run_props(rpr) -> dict:
    d: dict = {}
    if rpr is None:
        return d
    for tag, key in (("b", "b"), ("i", "i"), ("strike", "s"), ("caps", "caps"), ("vanish", "hidden")):
        v = _on(rpr.find(w(tag)))
        if v is not None:
            d[key] = v
    u = rpr.find(w("u"))
    if u is not None:
        d["u"] = u.get(w("val"), "single") not in ("none",)
    c = rpr.find(w("color"))
    if c is not None and c.get(w("val")) not in (None, "auto"):
        d["color"] = "#" + c.get(w("val"))
    sz = rpr.find(w("sz"))
    if sz is not None and sz.get(w("val")):
        try:
            d["sz"] = int(sz.get(w("val"))) / 2
        except ValueError:
            pass
    fonts = rpr.find(w("rFonts"))
    if fonts is not None:
        f = fonts.get(w("eastAsia")) or fonts.get(w("ascii")) or fonts.get(w("hAnsi"))
        if f:
            d["font"] = f
        if fonts.get(w("ascii")):
            d["font_ascii"] = fonts.get(w("ascii"))
        if fonts.get(w("eastAsia")):
            d["font_ea"] = fonts.get(w("eastAsia"))
    hl = rpr.find(w("highlight"))
    if hl is not None and hl.get(w("val")) not in (None, "none"):
        d["hl"] = hl.get(w("val"))
    shd = rpr.find(w("shd"))
    if shd is not None and shd.get(w("fill")) not in (None, "auto"):
        d["shd"] = "#" + shd.get(w("fill"))
    va = rpr.find(w("vertAlign"))
    if va is not None:
        d["va"] = va.get(w("val"))
    rs = rpr.find(w("rStyle"))
    if rs is not None:
        d["rstyle"] = rs.get(w("val"))
    return d


def para_props(ppr) -> dict:
    d: dict = {}
    if ppr is None:
        return d
    jc = ppr.find(w("jc"))
    if jc is not None:
        d["align"] = {"both": "justify", "distribute": "justify", "start": "left", "end": "right"}.get(jc.get(w("val")), jc.get(w("val")))
    ind = ppr.find(w("ind"))
    if ind is not None:
        for a, k in (("left", "ind_left"), ("start", "ind_left"), ("firstLine", "ind_first"), ("hanging", "ind_hanging"), ("right", "ind_right")):
            if ind.get(w(a)):
                try:
                    d[k] = int(ind.get(w(a))) / 20
                except ValueError:
                    pass
        if ind.get(w("firstLineChars")):
            try:
                d["ind_first_chars"] = int(ind.get(w("firstLineChars"))) / 100
            except ValueError:
                pass
    sp = ppr.find(w("spacing"))
    if sp is not None:
        for a, k in (("before", "sp_before"), ("after", "sp_after")):
            if sp.get(w(a)):
                try:
                    d[k] = int(sp.get(w(a))) / 20
                except ValueError:
                    pass
        if sp.get(w("line")) and sp.get(w("lineRule"), "auto") == "auto":
            try:
                d["line"] = int(sp.get(w("line"))) / 240
            except ValueError:
                pass
    if ppr.find(w("pageBreakBefore")) is not None and _on(ppr.find(w("pageBreakBefore"))):
        d["page_break_before"] = True
    shd = ppr.find(w("shd"))
    if shd is not None and shd.get(w("fill")) not in (None, "auto"):
        d["shd"] = "#" + shd.get(w("fill"))
    return d


class Numbering:
    def __init__(self, pkg: Package, main: str):
        self.nums: dict[str, str] = {}
        self.abstract: dict[str, dict[int, dict]] = {}
        self.counters: dict[tuple, int] = {}
        part = None
        for rel in pkg.rels(main):
            if rel.type.endswith("/numbering"):
                part = pkg.resolve(main, rel.target)
        if not part or not pkg.has(part):
            return
        root = pkg.xml(part)
        for an in root.findall(w("abstractNum")):
            levels = {}
            for lvl in an.findall(w("lvl")):
                ilvl = int(lvl.get(w("ilvl"), "0"))
                fmt = _val(lvl.find(w("numFmt"))) or "decimal"
                text = _val(lvl.find(w("lvlText"))) or ""
                start = int(_val(lvl.find(w("start"))) or 1)
                levels[ilvl] = {"fmt": fmt, "text": text, "start": start}
            self.abstract[an.get(w("abstractNumId"))] = levels
        for num in root.findall(w("num")):
            aid = _val(num.find(w("abstractNumId")))
            self.nums[num.get(w("numId"))] = aid

    def marker(self, num_id: str, ilvl: int) -> str | None:
        aid = self.nums.get(num_id)
        levels = self.abstract.get(aid or "", {})
        lvl = levels.get(ilvl)
        if not lvl:
            return None
        if lvl["fmt"] == "bullet":
            t = lvl["text"]
            return ("•" if not t or ord(t[0]) >= 0xF000 else t)
        if lvl["fmt"] == "none":
            return ""
        key = (num_id, ilvl)
        self.counters[key] = self.counters.get(key, lvl["start"] - 1) + 1
        # reset deeper levels
        for k in list(self.counters):
            if k[0] == num_id and k[1] > ilvl:
                del self.counters[k]
        text = lvl["text"]
        for l in range(0, ilvl + 1):
            val = self.counters.get((num_id, l), levels.get(l, {}).get("start", 1))
            text = text.replace(f"%{l + 1}", _fmt_num(val, levels.get(l, {}).get("fmt", "decimal")))
        return text


_CN_DIGITS = "零一二三四五六七八九"


def _fmt_num(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter",):
        return chr(96 + ((n - 1) % 26) + 1)
    if fmt in ("upperLetter",):
        return chr(64 + ((n - 1) % 26) + 1)
    if fmt in ("lowerRoman", "upperRoman"):
        vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out, x = "", n
        for v, s in vals:
            while x >= v:
                out += s
                x -= v
        return out.upper() if fmt == "upperRoman" else out
    if fmt.startswith("chinese") or fmt in ("ideographTraditional", "taiwaneseCounting", "japaneseCounting"):
        if n < 10:
            return _CN_DIGITS[n]
        if n < 20:
            return "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
        if n < 100:
            return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
        return str(n)
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


class DocxRenderer:
    def __init__(self, pkg: Package, *, include_images: bool = True):
        self.pkg = pkg
        self.main = pkg.main_part()
        self.styles = StyleSheet(pkg, self.main)
        self.numbering = Numbering(pkg, self.main)
        self.include_images = include_images
        self.images: dict[str, str] = {}
        self.fields: dict[str, dict] = {}

    def render(self) -> dict:
        parts = []
        for info in list_parts(self.pkg):
            container = part_container(self.pkg, info["part"])
            self.numbering.counters = {}
            blocks = self._blocks(container, info["part"], [])
            parts.append({**info, "blocks": blocks})
        return {"parts": parts, "page": self._page(), "images": self.images, "fields": self.fields}

    def _page(self) -> dict:
        body = part_container(self.pkg, self.main)
        sect = body.find(w("sectPr"))
        page = {"w": 595.3, "h": 841.9, "margin": {"top": 72, "bottom": 72, "left": 90, "right": 90}, "orient": "portrait"}
        if sect is not None:
            pgsz = sect.find(w("pgSz"))
            if pgsz is not None:
                try:
                    page["w"] = int(pgsz.get(w("w"))) / 20
                    page["h"] = int(pgsz.get(w("h"))) / 20
                except (TypeError, ValueError):
                    pass
                page["orient"] = pgsz.get(w("orient"), "portrait")
            mar = sect.find(w("pgMar"))
            if mar is not None:
                for k in ("top", "bottom", "left", "right", "header", "footer"):
                    if mar.get(w(k)):
                        try:
                            page["margin"][k] = abs(int(mar.get(w(k)))) / 20
                        except ValueError:
                            pass
        return page

    def _blocks(self, container, part: str, prefix: list[int]) -> list[dict]:
        out = []
        for i, el in enumerate(iter_blocks(container)):
            path = prefix + [i]
            if el.tag == w("p"):
                out.append(self._para(el, part, path))
            else:
                out.append(self._table(el, part, path))
            blk = _owner_block(el, container)
            if blk:
                out[-1]["blk"] = blk
        return out

    def _para(self, p, part: str, path: list[int]) -> dict:
        ppr = p.find(w("pPr"))
        pstyle = _val(ppr.find(w("pStyle"))) if ppr is not None else None
        base_r, base_p = self.styles.resolve(pstyle)
        props = dict(base_p)
        props.update(para_props(ppr))
        para_rpr = run_props(ppr.find(w("rPr"))) if ppr is not None else {}
        marker = None
        if ppr is not None:
            numpr = ppr.find(w("numPr"))
            if numpr is not None:
                nid = _val(numpr.find(w("numId")))
                ilvl = int(_val(numpr.find(w("ilvl"))) or 0)
                if nid and nid != "0":
                    marker = self.numbering.marker(nid, ilvl)
        runs = []
        pos = 0
        field_depth = 0
        in_code = False
        for item, field in self._runs_with_fields(p, None):
            if item.tag.startswith("{%s}" % MATH_NS):
                runs.append({"text": OBJ, "off": pos, "obj": "math", "field": field})
                pos += 1
                continue
            rpr = item.find(w("rPr"))
            direct = run_props(rpr)
            rstyle = direct.get("rstyle")
            r_eff, _ = self.styles.resolve(pstyle, rstyle)
            style = dict(r_eff)
            style.update(direct)
            style.pop("rstyle", None)
            buf = []
            start = pos
            for child in item:
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
                if kind == "obj":
                    if buf:
                        runs.append({"text": "".join(buf), "off": start, **_style_out(style), "field": field})
                        buf = []
                    img = self._image(child) if self.include_images else None
                    runs.append({"text": OBJ, "off": pos, "obj": "image" if img else "object", "img": img, "field": field})
                    pos += 1
                    start = pos
                    continue
                buf.append(s)
                pos += len(s)
            if buf:
                runs.append({"text": "".join(buf), "off": start, **_style_out(style), "field": field})
            elif field and not any(r.get("field") == field for r in runs):
                runs.append({"text": "", "off": pos, "field": field, "empty": True})
        text = "".join(r["text"] for r in runs)
        out = {"t": "p", "path": ":".join(map(str, path)), "part": part, "text": text, "runs": runs,
               "style": _para_out(props, self.styles.name(pstyle)), "rbase": _style_out({**base_r, **para_rpr})}
        if marker is not None:
            out["marker"] = marker
        return out

    def _runs_with_fields(self, el, field):
        for child in el:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag == w("r"):
                yield child, field
            elif tag == w("sdt"):
                t = sdt_tag(child)
                f = t[len(TAG_PREFIX):] if t and t.startswith(TAG_PREFIX) else field
                if f and f != field:
                    alias = child.find(f"{w('sdtPr')}/{w('alias')}")
                    self.fields.setdefault(f, {"alias": alias.get(w("val")) if alias is not None else f})
                content = child.find(w("sdtContent"))
                if content is not None:
                    yielded = False
                    for x in self._runs_with_fields(content, f):
                        yielded = True
                        yield x
                    if not yielded and f:
                        # empty control: emit a zero-length marker through a fake empty run
                        yield etree.Element(w("r")), f
            elif tag in (w("hyperlink"), w("ins"), w("smartTag"), w("customXml"), w("fldSimple"), w("bdo"), w("dir"), w("moveTo")):
                yield from self._runs_with_fields(child, field)
            elif tag.startswith("{%s}" % MATH_NS):
                yield child, field

    def _image(self, child) -> dict | None:
        blip = None
        for el in child.iter("{%s}blip" % NS["a"]):
            blip = el
            break
        extent = None
        for el in child.iter("{%s}extent" % NS["wp"]):
            extent = el
            break
        if blip is None:
            return None
        rid = blip.get("{%s}embed" % NS["r"])
        if not rid:
            return None
        target = None
        # images may live in header/footer parts too; find owning part
        part = self._owner_part(child)
        target = self.pkg.rel_target(part, rid) if part else None
        if not target or not self.pkg.has(target):
            return None
        key = target
        if key not in self.images:
            data = self.pkg.read(target)
            ext = target.rsplit(".", 1)[-1].lower()
            mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
                    "svg": "image/svg+xml", "webp": "image/webp"}.get(ext)
            if mime and len(data) <= MAX_INLINE_IMAGE and mime != "image/svg+xml":
                self.images[key] = f"data:{mime};base64," + base64.b64encode(data).decode()
            else:
                self.images[key] = ""
        wpx = hpx = None
        if extent is not None:
            try:
                wpx = int(extent.get("cx")) / EMU_PER_PX
                hpx = int(extent.get("cy")) / EMU_PER_PX
            except (TypeError, ValueError):
                pass
        return {"key": key, "w": wpx, "h": hpx}

    def _owner_part(self, el) -> str | None:
        root = el.getroottree().getroot()
        for name in self.pkg.names:
            if name.endswith(".xml") and name in self.pkg._xml and self.pkg._xml[name] is root:
                return name
        return None

    def _table(self, tbl, part: str, path: list[int]) -> dict:
        rows = []
        grid = [int(g.get(w("w"), "0")) / 20 for g in tbl.findall(f"{w('tblGrid')}/{w('gridCol')}")]
        tblpr = tbl.find(w("tblPr"))
        borders = False
        if tblpr is not None:
            b = tblpr.find(w("tblBorders"))
            if b is not None and any(x.get(w("val")) not in ("nil", "none") for x in b):
                borders = True
            st = tblpr.find(w("tblStyle"))
            if st is not None and not borders:
                borders = "Grid" in (st.get(w("val")) or "") or "网格" in (self.styles.name(st.get(w("val"))) or "")
        for ri, tr in enumerate(iter_rows(tbl)):
            cells = []
            trpr = tr.find(w("trPr"))
            header = trpr is not None and trpr.find(w("tblHeader")) is not None
            for ci, tc in enumerate(iter_cells(tr)):
                props = tc_props(tc)
                tcpr = tc.find(w("tcPr"))
                shd = None
                if tcpr is not None:
                    s = tcpr.find(w("shd"))
                    if s is not None and s.get(w("fill")) not in (None, "auto"):
                        shd = "#" + s.get(w("fill"))
                blocks = self._blocks(tc, part, path + [ri, ci])
                cells.append({"span": props["span"], "vmerge": props["vmerge"], "shd": shd, "blocks": blocks})
            rows.append({"cells": cells, "header": header})
        return {"t": "tbl", "path": ":".join(map(str, path)), "part": part, "rows": rows, "grid": grid, "borders": borders}


def _owner_block(el, container) -> str | None:
    """Id of the repeat content block (block-level ``dw:`` content control) holding ``el``."""
    cur = el.getparent()
    while cur is not None and cur is not container:
        if cur.tag == w("sdt"):
            t = sdt_tag(cur)
            if t and t.startswith(TAG_PREFIX) and "#" not in t and "@" not in t:
                return t[len(TAG_PREFIX):]
        cur = cur.getparent()
    return None


def _style_out(s: dict) -> dict:
    out = {}
    for k in ("b", "i", "u", "s", "color", "sz", "font", "hl", "shd", "va", "hidden", "caps"):
        if k in s and s[k] not in (None, False):
            out[k] = s[k]
    return out


def _para_out(p: dict, style_name: str | None) -> dict:
    out = {k: v for k, v in p.items() if v not in (None, False)}
    if style_name:
        out["name"] = style_name
    return out


def render_docx(data: bytes) -> dict:
    pkg = Package(data)
    return DocxRenderer(pkg).render()
