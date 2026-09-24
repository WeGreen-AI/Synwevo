"""Turn UI selections into stored locators (server side), and resolve stored
locators back into on-screen positions for any file (highlighting / 定位)."""
from __future__ import annotations

import re

from synwevo_core.errors import ServiceError, not_found

from .engine import tabular
from .engine import values as V
from .engine.resolve import ResolveError, SourceDoc, extract_scalar, resolve_sheet, table_view
from .services import App


def _looks_label(text: str) -> bool:
    t = (text or "").strip()
    if not t or len(t) > 30:
        return False
    return not re.fullmatch(r"[-+]?[\d,.%¥￥$]+", t)


def build_locator(app: App, sid: str, sel: dict, project: dict | None, block: int | None = None) -> dict:
    """Return {'proposals': [ {label, locator, varies, sample} ], 'warnings': [...]}"""
    r = app.get_source(sid, raw=True)
    if project is not None and project.get("id") != r["project_id"]:
        not_found("来源文件")
    src = app.doc_for(r, block)
    _validate_selection(src, sel)
    rule = (project or {}).get("record_rule") or {}
    mode = rule.get("mode")
    kind = sel.get("kind")
    out = {"proposals": [], "warnings": []}
    if kind == "cells":
        sh = resolve_sheet(src, {"sheet": sel["sheet"]})
        r1, c1, r2, c2 = int(sel["r1"]), int(sel["c1"]), int(sel["r2"]), int(sel["c2"])
        table = rule.get("table") or {}
        same_sheet = table.get("sheet") == sh.name
        hr = table.get("header_row", 0)
        if r1 == r2 and c1 == c2:
            if mode in ("per_row", "per_group") and same_sheet and hr is not None and r1 > hr:
                header = sh.cell(hr, c1).text
                occ = None
                heads = [tabular.normalize_header(sh.cell(hr, c).text) for c in range(sh.n_cols)]
                if heads.count(tabular.normalize_header(header)) > 1:
                    occ = heads[:c1 + 1].count(tabular.normalize_header(header))
                    out["warnings"].append(f"表头「{header}」有重名列，已记录为第 {occ} 个同名列；列顺序变化时请核对")
                if header.strip():
                    label = f"随样本变化：每条样本的「{header}」列" + ("（组内取值规则见映射设置）" if mode == "per_group" else "")
                    out["proposals"].append({"label": label, "varies": "per_record",
                                             "locator": {"kind": "column", "sheet": sh.name, "header": header, "occurrence": occ},
                                             "sample": sh.cell(r1, c1).text})
                else:
                    out["warnings"].append(f"第 {tabular.col_letter(c1)} 列在表头行没有列名，无法按列名绑定；可先设置表头行")
            anchor = None
            left = sh.cell(r1, c1 - 1).text if c1 > 0 else ""
            up = sh.cell(r1 - 1, c1).text if r1 > 0 else ""
            if _looks_label(left):
                anchor = {"text": left.strip(), "dr": 0, "dc": -1}
            elif _looks_label(up):
                anchor = {"text": up.strip(), "dr": -1, "dc": 0}
            varies = "per_file" if mode in ("per_file", "per_block") else ("fixed" if mode in ("per_row", "per_group") else "per_file")
            label = f"固定单元格 {sh.name}!{tabular.cell_ref(r1, c1)}" + (f"（以标签「{anchor['text']}」校验位置）" if anchor else "")
            label += {"per_file": "：每个文件取同一位置", "per_block": "：每个表单块取块内同一位置（跟随标签）"}.get(mode, "：同一文件内所有样本取相同值（固定值）")
            out["proposals"].append({"label": label, "varies": varies,
                                     "locator": {"kind": "cell", "sheet": sh.name, "cell": tabular.cell_ref(r1, c1), **({"anchor": anchor} if anchor else {})},
                                     "sample": sh.cell(r1, c1).text})
        else:
            rng = tabular.range_ref(r1, c1, r2, c2)
            out["proposals"].append({"label": f"固定区域 {sh.name}!{rng}（{r2 - r1 + 1} 行 × {c2 - c1 + 1} 列）", "varies": "per_file" if mode in ("per_file", "per_block") else "fixed",
                                     "locator": {"kind": "range", "sheet": sh.name, "range": rng},
                                     "sample": [[sh.cell(r, c).text for c in range(c1, c2 + 1)] for r in range(r1, min(r2, r1 + 4) + 1)]})
            headers = [sh.cell(r1, c).text for c in range(c1, c2 + 1)]
            if all(h.strip() for h in headers) and r2 > r1:
                full = sel.get("to_end", False)
                if mode == "per_group":
                    label = f"明细表（表头：第 {r1 + 1} 行）：每份输出写入当前分组的全部明细行（按分组与排序规则取行，与所选行数无关）"
                else:
                    label = f"明细表：第 {r1 + 1} 行为表头，{'直到数据末尾' if full else f'第 {r1 + 2}–{r2 + 1} 行'}为当前样本的明细（整个区域写入同一份文档）"
                out["proposals"].append({"label": label,
                                         "varies": "per_file", "detail": True,
                                         "locator": {"kind": "table", "sheet": sh.name, "header_row": r1, "data_start": r1 + 1,
                                                     "data_end": None if full else r2, "first_col": c1, "last_col": c2, "headers": headers},
                                         "sample": headers})
        return out
    if kind == "docx_text":
        from .engine import docx_core as dc
        pkg = src.pkg
        part = sel.get("part") or pkg.main_part()
        container = dc.part_container(pkg, part)
        el = dc.get_block(container, sel["path"])
        text = dc.para_text(el)
        s, e = int(sel["start"]), int(sel["end"])
        prefix, suffix = text[:s], text[e:]
        loc = {"kind": "docx_text", "part": part, "path": sel["path"], "start": s, "end": e,
               "prefix": prefix[-24:], "suffix": suffix[:24], "sample": text[s:e]}
        if not prefix.strip() and not suffix.strip():
            from .engine.resolve import _prev_para_text
            loc["context_before"] = _prev_para_text(container, sel["path"]) if ":" not in sel["path"] else None
        try:
            got = extract_scalar(src, loc, {"mode": "per_file"})
            if got.text != text[s:e]:
                out["warnings"].append(f"用前后文定位时取到「{got.text[:30]}」，与选中内容「{text[s:e][:30]}」不同；请扩大或缩小选区，使前后文能唯一确定位置")
        except ResolveError as ex:
            out["warnings"].append(f"定位校验：{ex}")
        out["proposals"].append({"label": f"文档文字：「{prefix[-8:]}」之后" + (f"、「{suffix[:8]}」之前" if suffix else "到段落末尾") + "的内容",
                                 "varies": "per_file", "locator": loc, "sample": text[s:e]})
        return out
    if kind == "docx_cell":
        from .engine import docx_core as dc
        pkg = src.pkg
        part = sel.get("part") or pkg.main_part()
        container = dc.part_container(pkg, part)
        tc = dc.get_table_cell(container, sel["table_path"], int(sel["row"]), int(sel["col"]))
        label = None
        try:
            left = dc.cell_text(dc.get_table_cell(container, sel["table_path"], int(sel["row"]), int(sel["col"]) - 1)) if int(sel["col"]) > 0 else ""
        except dc.DocxError:
            left = ""
        try:
            above = dc.cell_text(dc.get_table_cell(container, sel["table_path"], int(sel["row"]) - 1, int(sel["col"]))) if int(sel["row"]) > 0 else ""
        except dc.DocxError:
            above = ""
        if _looks_label(left):
            label = {"text": left.strip(), "where": "left"}
        elif _looks_label(above):
            label = {"text": above.strip(), "where": "above"}
        loc = {"kind": "docx_cell", "part": part, "table_path": sel["table_path"], "row": int(sel["row"]), "col": int(sel["col"])}
        if label:
            loc["label"] = label
        out["proposals"].append({"label": "表格单元格" + (f"（标签「{label['text']}」{'右侧' if label['where'] == 'left' else '下方'}）" if label else ""),
                                 "varies": "per_file", "locator": loc, "sample": dc.cell_text(tc)})
        return out
    if kind == "docx_table":
        from .engine import docx_core as dc
        pkg = src.pkg
        part = sel.get("part") or pkg.main_part()
        container = dc.part_container(pkg, part)
        tbl = dc.get_block(container, sel["table_path"])
        rows = list(dc.iter_rows(tbl))
        hr = int(sel.get("header_rows", 1))
        headers = [dc.cell_text(tc) for tc in dc.iter_cells(rows[hr - 1])] if hr and rows else []
        out["proposals"].append({"label": f"文档表格明细（表头 {hr} 行，其余 {len(rows) - hr} 行为明细）", "varies": "per_file", "detail": True,
                                 "locator": {"kind": "docx_table", "part": part, "table_path": sel["table_path"], "header_rows": hr, "headers": headers},
                                 "sample": headers})
        if "r1" in sel:
            out["proposals"].append({"label": "表格固定区域", "varies": "per_file",
                                     "locator": {"kind": "docx_table_block", "part": part, "table_path": sel["table_path"],
                                                 "r1": int(sel["r1"]), "c1": int(sel["c1"]), "r2": int(sel["r2"]), "c2": int(sel["c2"])}})
        return out
    if kind == "pdf_region":
        from .engine import pdf_engine as pe
        if src.family == "image" or (src.family == "pdf" and pe.page_kind(src.pdf[int(sel["page"])]) == "scanned"):
            loc = {"kind": "ocr_region", "page": int(sel["page"]), "nrect": [float(x) for x in sel["nrect"]], "min_conf": 80}
            try:
                v = extract_scalar(src, loc, {"mode": "per_file"})
                sample = v.text
            except ResolveError as ex:
                sample = ex.detail.get("value", "") if ex.code == "ocr_low_confidence" else ""
                out["warnings"].append(str(ex))
            out["proposals"].append({"label": f"扫描页第 {int(sel['page']) + 1} 页框选区域（OCR 识别）", "varies": "per_file", "locator": loc, "sample": sample})
            return out
        page = src.pdf[int(sel["page"])]
        rect = pe.visual_norm_to_unrot(page, sel["nrect"])
        anchor = pe.find_anchor(page, rect)
        loc = {"kind": "pdf_region", "page": int(sel["page"]), "rect": [round(x, 2) for x in rect],
               "page_size": [page.mediabox.width, page.mediabox.height], "rotation": page.rotation}
        if anchor:
            loc["anchor"] = anchor
        sample = pe.join_words(pe.words_in_rect(page, rect))
        if not anchor:
            out["warnings"].append("区域附近没有找到定位标签，将只按页面坐标取值；用于其他文件前会校验页面尺寸和旋转")
        out["proposals"].append({"label": f"PDF 第 {int(sel['page']) + 1} 页框选区域" + (f"（标签「{anchor['text']}」）" if anchor else ""),
                                 "varies": "per_file", "locator": loc, "sample": sample})
        return out
    if kind == "text_span":
        lines = src.text.split("\n")
        li = int(sel["line"])
        line = lines[li] if li < len(lines) else ""
        s, e = int(sel["start"]), int(sel["end"])
        loc = {"kind": "text_span", "line": li, "start": s, "end": e, "prefix": line[:s][-24:], "suffix": line[e:][:24], "sample": line[s:e]}
        out["proposals"].append({"label": f"第 {li + 1} 行文字", "varies": "per_file", "locator": loc, "sample": line[s:e]})
        return out
    if kind == "pptx_text":
        from .engine.pptx_engine import _find_shape, _nv, _paras, _txbody, para_text
        _, el = _find_shape(src.pkg, int(sel["slide"]), sel["shape"])
        text = para_text(_paras(_txbody(el))[int(sel.get("para", 0))])
        s, e = int(sel["start"]), int(sel["end"])
        nv = _nv(el)
        loc = {"kind": "pptx_text", "slide": int(sel["slide"]), "shape": str(sel["shape"]), "shape_name": nv.get("name") if nv is not None else None,
               "para": int(sel.get("para", 0)), "start": s, "end": e, "prefix": text[:s][-24:], "suffix": text[e:][:24]}
        out["proposals"].append({"label": f"第 {int(sel['slide']) + 1} 张幻灯片「{loc['shape_name']}」中的文字", "varies": "per_file", "locator": loc,
                                 "sample": text[s:e]})
        return out
    if kind == "pptx_cell":
        from .engine.pptx_engine import _cell, _find_shape, _nv, _table, cell_text
        _, el = _find_shape(src.pkg, int(sel["slide"]), sel["shape"])
        nv = _nv(el)
        loc = {"kind": "pptx_text", "slide": int(sel["slide"]), "shape": str(sel["shape"]), "shape_name": nv.get("name") if nv is not None else None,
               "row": int(sel["row"]), "col": int(sel["col"])}
        out["proposals"].append({"label": "幻灯片表格单元格", "varies": "per_file", "locator": loc,
                                 "sample": cell_text(_cell(_table(el), int(sel["row"]), int(sel["col"])))})
        return out
    raise ServiceError(400, "bad_selection", f"不支持的选择类型：{kind}")


def _validate_selection(src: SourceDoc, sel: dict) -> None:
    """Reject stale or out-of-bounds selections instead of returning clipped/empty values.

    The browser and machine callers use the same source geometry. This validates it
    against the actual parsed document, beyond request-level type and size limits.
    """
    def require(condition):
        if not condition:
            raise ServiceError(400, "bad_selection", "选区与来源内容不符；请重新读取来源结构并选择", {"field": "selection"})

    def index(key):
        value = sel.get(key)
        require(type(value) is int and value >= 0)
        return value

    def span(text):
        require(index("start") < index("end") <= len(text))

    def rectangle(rows, cols):
        r1, r2, c1, c2 = (index(k) for k in ("r1", "r2", "c1", "c2"))
        require(r1 <= r2 < rows and c1 <= c2 < cols)
        require((r2 - r1 + 1) * (c2 - c1 + 1) <= 100000)

    require(isinstance(sel, dict))
    kind = sel.get("kind")
    try:
        if kind == "cells":
            require(src.workbook_json is not None)
            sh = resolve_sheet(src, {"sheet": sel.get("sheet")})
            rectangle(sh.n_rows, sh.n_cols)
        elif kind in ("docx_text", "docx_cell", "docx_table"):
            from .engine import docx_core as dc
            require(src.family == "document")
            part = sel.get("part") or src.pkg.main_part()
            require(part in {p["part"] for p in dc.list_parts(src.pkg)})
            container = dc.part_container(src.pkg, part)
            path = sel.get("path") if kind == "docx_text" else sel.get("table_path")
            require(isinstance(path, str) and re.fullmatch(r"[0-9]+(?::[0-9]+:[0-9]+:[0-9]+)*", path))
            element = dc.get_block(container, path)
            if kind == "docx_text":
                require(element.tag == dc.w("p"))
                span(dc.para_text(element))
            else:
                require(element.tag == dc.w("tbl"))
                rows = list(dc.iter_rows(element))
                if kind == "docx_cell":
                    dc.get_table_cell(container, path, index("row"), index("col"))
                else:
                    require(type(sel.get("header_rows", 1)) is int and 0 <= sel.get("header_rows", 1) <= len(rows))
                    if any(k in sel for k in ("r1", "r2", "c1", "c2")):
                        rectangle(len(rows), max((len(list(dc.iter_cells(row))) for row in rows), default=0))
                        require(all(sel["c2"] < len(list(dc.iter_cells(row))) for row in rows[sel["r1"]:sel["r2"] + 1]))
        elif kind == "pdf_region":
            import math
            from .engine import pdf_engine as pe
            require(src.family in ("pdf", "image"))
            require(index("page") < (pe.page_count(src.data) if src.family == "pdf" else 1))
            rect = sel.get("nrect")
            require(isinstance(rect, list) and len(rect) == 4)
            require(all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1 for v in rect))
            require(rect[0] < rect[2] and rect[1] < rect[3])
        elif kind == "text_span":
            require(src.family == "text")
            lines = src.text.split("\n")
            require(index("line") < len(lines))
            span(lines[sel["line"]])
        elif kind in ("pptx_text", "pptx_cell"):
            from .engine import pptx_engine as ppt
            require(src.family == "slides")
            _, element = ppt._find_shape(src.pkg, index("slide"), sel.get("shape"))
            if kind == "pptx_text":
                paras = ppt._paras(ppt._txbody(element))
                para = sel.get("para", 0)
                require(type(para) is int and 0 <= para < len(paras))
                span(ppt.para_text(paras[para]))
            else:
                table = ppt._table(element)
                require(table is not None)
                ppt._cell(table, index("row"), index("col"))
        else:
            require(False)
    except (ValueError, IndexError, KeyError) as exc:
        raise ServiceError(400, "bad_selection", "来源中找不到该选区；请重新读取来源结构并选择", {"field": "selection"}) from exc


# ------------------------------------------------------------------ locate for highlighting
def locate_all(app: App, pid: str, sid: str, block: int | None = None) -> dict:
    project = app.get_project(pid)
    rule = project.get("record_rule") or {}
    maps = app.get_mappings(pid)["mappings"]
    r = app.get_source(sid, raw=True)
    # Both ids are checked, but they also have to belong together. Checking the project and then
    # taking the source id on trust let a caller run their own mappings over someone else's
    # document and read the resolved field values straight back out.
    if r.get("project_id") != pid:
        not_found("来源文件")
    src = app.doc_for(r, block)
    if block is not None:
        sid = src.file_id          # per-block overrides are stored under the block's id
    out = {}
    for m in maps:
        res = []
        for i, loc0 in enumerate(m.get("sources") or []):
            ov = (m.get("overrides") or {}).get(sid)
            loc = ov["locator"] if ov and ov.get("source_index", 0) == i and ov.get("locator") else loc0
            try:
                res.append({"ok": True, **locate(src, loc, rule), "overridden": bool(ov and ov.get("source_index", 0) == i)})
            except ResolveError as e:
                res.append({"ok": False, "error": str(e), "code": e.code})
            except Exception as e:  # noqa: BLE001
                res.append({"ok": False, "error": str(e), "code": "error"})
        out[m["id"]] = res
    return out


def locate(src: SourceDoc, loc: dict, rule: dict) -> dict:
    kind = loc.get("kind")
    if kind == "column":
        tv = table_view(src, rule.get("table") or {"sheet": loc.get("sheet"), "header_row": 0})
        from .engine.resolve import column_index
        c = column_index(tv, loc, src)
        rows = tv.data_rows
        return {"pos": {"kind": "cells", "sheet": tv.sheet.name, "r1": rows[0] if rows else (tv.header_row or 0) + 1,
                        "c1": c, "r2": rows[-1] if rows else (tv.header_row or 0) + 1, "c2": c, "header": tv.header_row}}
    if kind in ("cell",):
        v = extract_scalar(src, loc, {"mode": "per_file"})
        r, c = tabular.parse_cell(v.src["cell"])
        return {"pos": {"kind": "cells", "sheet": v.src["sheet"], "r1": r, "c1": c, "r2": r, "c2": c}, "value": v.text}
    if kind == "range":
        sh = resolve_sheet(src, loc)
        r1, c1, r2, c2 = tabular.parse_range(loc["range"])
        return {"pos": {"kind": "cells", "sheet": sh.name, "r1": r1, "c1": c1, "r2": r2, "c2": c2}}
    if kind == "table":
        tv = table_view(src, loc)
        last = tv.data_rows[-1] if tv.data_rows else (tv.header_row or 0)
        return {"pos": {"kind": "cells", "sheet": tv.sheet.name, "r1": tv.header_row or 0, "c1": tv.first_col,
                        "r2": last, "c2": tv.last_col if tv.last_col is not None else tv.sheet.n_cols - 1}, "rows": len(tv.data_rows)}
    if kind == "docx_text":
        pos = docx_text_position(src, loc)
        return {"pos": pos, "value": pos.get("text")}
    if kind == "docx_cell":
        v = extract_scalar(src, loc, {"mode": "per_file"})
        s = v.src or {}
        if "table" in s:
            return {"pos": {"kind": "docx_cell", "part": loc.get("part"), "table_path": s["table"], "row": s["row"], "col": s["col"]}, "value": v.text}
        return {"pos": {"kind": "docx_cell", "part": loc.get("part"), "table_path": loc["table_path"], "row": loc["row"], "col": loc["col"]}, "value": v.text}
    if kind in ("docx_table", "docx_table_block"):
        return {"pos": {"kind": "docx_table", "part": loc.get("part"), "table_path": loc["table_path"]}}
    if kind == "pdf_region":
        from .engine import pdf_engine as pe
        page = src.pdf[loc["page"]]
        rect, err = pe.locate_with_anchor(page, loc)
        if err:
            raise ResolveError("layout_mismatch", err)
        return {"pos": {"kind": "rect", "page": loc["page"], "nrect": pe.unrot_to_visual_norm(page, rect)},
                "value": pe.join_words(pe.words_in_rect(page, rect))}
    if kind == "ocr_region":
        return {"pos": {"kind": "rect", "page": loc["page"], "nrect": loc["nrect"]}}
    if kind == "text_span":
        return {"pos": {"kind": "text_span", "line": loc["line"], "start": loc["start"], "end": loc["end"]}}
    if kind == "pptx_text":
        return {"pos": {"kind": "pptx", "slide": loc["slide"], "shape": loc["shape"], "para": loc.get("para"), "start": loc.get("start"),
                        "end": loc.get("end"), "row": loc.get("row"), "col": loc.get("col")}}
    if kind in ("constant", "record_meta"):
        return {"pos": None}
    raise ResolveError("bad_locator", f"无法定位：{kind}")


def docx_text_position(src: SourceDoc, loc: dict) -> dict:
    """Like resolve._docx_text but returns the concrete path/offsets."""
    from .engine import docx_core as dc
    container = dc.part_container(src.pkg, loc.get("part") or src.pkg.main_part())
    pre = (loc.get("prefix") or "")[-24:].lstrip()
    suf = (loc.get("suffix") or "")[:24].rstrip()

    def spans(text):
        res = []
        starts = [m.end() for m in re.finditer(re.escape(pre), text)] if pre else [0]
        for sp in starts:
            if suf:
                ep = text.find(suf, sp)
                if ep >= 0:
                    res.append((sp, ep))
            else:
                res.append((sp, len(text)))
        return res

    try:
        el = dc.get_block(container, loc["path"])
        if el.tag == dc.w("p"):
            t = dc.para_text(el)
            if not pre and not suf:
                return {"kind": "docx_text", "part": loc.get("part"), "path": loc["path"], "start": 0, "end": len(t), "text": t}
            sp = spans(t)
            if len(sp) == 1:
                return {"kind": "docx_text", "part": loc.get("part"), "path": loc["path"], "start": sp[0][0], "end": sp[0][1], "text": t[sp[0][0]:sp[0][1]]}
    except dc.DocxError:
        pass
    hits = []
    for path, p in _walk_paragraphs(container):
        t = dc.para_text(p)
        for s, e in spans(t):
            hits.append({"kind": "docx_text", "part": loc.get("part"), "path": path, "start": s, "end": e, "text": t[s:e]})
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise ResolveError("anchor_missing", f"找不到标签「{pre or suf}」")
    raise ResolveError("anchor_ambiguous", f"标签「{pre or suf}」出现 {len(hits)} 次")


def _walk_paragraphs(container, prefix=None):
    from .engine import docx_core as dc
    prefix = prefix or []
    for i, el in enumerate(dc.iter_blocks(container)):
        if el.tag == dc.w("p"):
            yield ":".join(map(str, prefix + [i])), el
        else:
            for ri, tr in enumerate(dc.iter_rows(el)):
                for ci, tc in enumerate(dc.iter_cells(tr)):
                    yield from _walk_paragraphs(tc, prefix + [i, ri, ci])
