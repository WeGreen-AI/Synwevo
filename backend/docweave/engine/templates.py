"""Template adapters: turn selections into structured fields (slots) inside a
working copy, render templates, and fill them from an execution plan.

The original uploaded template is never modified; every slot change produces a
new working copy (a new TemplateVersion upstream).
"""
from __future__ import annotations

import copy
import re
import secrets

from lxml import etree

from . import docx_core as dc
from . import tabular
from . import values as V
from .ooxml import Package
from .refshift import Shift


class TemplateError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def new_slot_id() -> str:
    return "f" + secrets.token_hex(5)


def _base_slot(name: str, kind: str, **kw) -> dict:
    d = {"id": new_slot_id(), "name": (name or "字段").strip()[:80], "kind": kind, "data_type": "auto",
         "required": False, "format": [], "default": None}
    d.update(kw)
    d.setdefault("binding", d["id"])
    return d


# =====================================================================  DOCX
def docx_add_slot(work: bytes, req: dict, slots: list[dict]) -> tuple[bytes, dict]:
    pkg = Package(work)
    part = req.get("part") or pkg.main_part()
    if not pkg.has(part):
        raise TemplateError("part_missing", "模板中没有该部件")
    container = dc.part_container(pkg, part)
    kind = req.get("kind", "text")
    name = req.get("name") or "字段"
    try:
        if kind == "text":
            el = dc.get_block(container, req["path"])
            if el.tag != dc.w("p"):
                raise TemplateError("not_paragraph", "请在段落文字中选择")
            text = dc.para_text(el)
            start, end = int(req["start"]), int(req["end"])
            mode = req.get("mode", "replace")
            if mode == "insert":
                start = end
            original = text[start:end]
            if mode == "replace" and not original:
                mode = "insert"
            slot = _base_slot(name, "text", mode=mode, original_text=original,
                              locator={"part": part, "path": req["path"], "start": start, "end": end,
                                       "context": text[max(0, start - 20):start]})
            dc.wrap_range(el, start, end, dc.TAG_PREFIX + slot["id"], slot["name"])
        elif kind == "cell":
            tc = dc.get_table_cell(container, req["table_path"], int(req["row"]), int(req["col"]))
            p = dc.cell_first_paragraph(tc)
            text = dc.para_text(p)
            mode = req.get("mode", "replace")
            slot = _base_slot(name, "text", mode=mode if text else "insert", original_text=dc.cell_text(tc) if mode == "replace" else "",
                              locator={"part": part, "table_path": req["table_path"], "row": int(req["row"]), "col": int(req["col"])})
            if mode == "replace" and text:
                # clear additional paragraphs? No – only the first paragraph is replaced; others kept and reported.
                dc.wrap_range(p, 0, len(text), dc.TAG_PREFIX + slot["id"], slot["name"])
            else:
                dc.wrap_range(p, len(text), len(text), dc.TAG_PREFIX + slot["id"], slot["name"])
        elif kind == "repeat":
            tbl = dc.get_block(container, req["table_path"])
            if tbl.tag != dc.w("tbl"):
                raise TemplateError("not_table", "请选择表格中的一行作为重复明细行")
            rows = list(dc.iter_rows(tbl))
            ri = int(req["row"])
            if ri >= len(rows):
                raise TemplateError("bad_row", "表格中没有该行")
            for other in slots:
                if other["kind"] == "repeat" and other["locator"].get("part") == part and other["locator"].get("table_path") == req["table_path"]:
                    raise TemplateError("duplicate_repeat", f"该表格已有重复明细行「{other['name']}」")
            tr = rows[ri]
            for tc in dc.iter_cells(tr):
                if dc.tc_props(tc)["vmerge"] is not None:
                    raise TemplateError("vertical_merge", "该行包含纵向合并单元格，不能作为重复行")
            header_rows = int(req.get("header_rows", ri))
            header_rows = max(0, min(header_rows, ri))
            hdr = [dc.cell_text(tc) for tc in dc.iter_cells(rows[ri - 1])] if ri > 0 else []
            columns = []
            slot = _base_slot(name, "repeat", locator={"part": part, "table_path": req["table_path"], "row": ri},
                              header_rows=header_rows, empty_policy=req.get("empty_policy", "remove_row"),
                              placeholder=req.get("placeholder", "（无）"), repeat_header=bool(req.get("repeat_header", True)))
            for ci, tc in enumerate(dc.iter_cells(tr)):
                key = f"c{ci}"
                cname = (hdr[ci].strip() if ci < len(hdr) and hdr[ci].strip() else f"第{ci + 1}列")
                p = dc.cell_first_paragraph(tc)
                t = dc.para_text(p)
                tag = f"{dc.TAG_PREFIX}{slot['id']}#{key}"
                if t:
                    dc.wrap_range(p, 0, len(t), tag, cname)
                else:
                    dc.wrap_range(p, 0, 0, tag, cname)
                columns.append({"key": key, "name": cname, "col": ci, "original_text": t})
            slot["columns"] = columns
        elif kind == "repeat_block":
            from . import docx_blocks as db
            slot = _base_slot(name, "repeat", locator={"part": part, "first": req["first"], "last": req["last"], "block": True},
                              block=True, empty_policy=req.get("empty_policy") if req.get("empty_policy") in ("remove", "placeholder", "error") else "remove",
                              placeholder=req.get("placeholder", "（无）"),
                              separator=req.get("separator") if req.get("separator") in ("none", "blank", "page_break") else "none")
            try:
                sdt = db.wrap_blocks(container, req["first"], req["last"], dc.TAG_PREFIX + slot["id"], slot["name"])
                slot["columns"] = db.auto_columns(sdt, slot["id"]) if req.get("auto_columns", True) else []
            except db.BlockError as e:
                raise TemplateError(e.code, str(e)) from e
            slot["original_text"] = "\n".join(dc.para_text(p) for p in db.block_paragraphs(sdt))[:300]
            slot["paragraphs"] = len(db.block_paragraphs(sdt))
        elif kind == "region":
            tbl = dc.get_block(container, req["table_path"])
            r1, c1, r2, c2 = int(req["r1"]), int(req["c1"]), int(req["r2"]), int(req["c2"])
            slot = _base_slot(name, "region", locator={"part": part, "table_path": req["table_path"], "r1": r1, "c1": c1, "r2": r2, "c2": c2},
                              rows=r2 - r1 + 1, cols=c2 - c1 + 1)
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    tc = dc.get_table_cell(container, req["table_path"], r, c)
                    pr = dc.tc_props(tc)
                    if pr["span"] > 1 or pr["vmerge"] is not None:
                        raise TemplateError("merged", "区域内包含合并单元格，无法按行列对应填充；请缩小区域")
                    p = dc.cell_first_paragraph(tc)
                    t = dc.para_text(p)
                    dc.wrap_range(p, 0, len(t), f"{dc.TAG_PREFIX}{slot['id']}@{r - r1},{c - c1}", f"{slot['name']}[{r - r1 + 1},{c - c1 + 1}]")
        else:
            raise TemplateError("bad_kind", f"DOCX 模板不支持的字段类型：{kind}")
    except dc.DocxError as e:
        raise TemplateError(e.code, str(e)) from e
    return pkg.save(), slot


def docx_add_block_column(work: bytes, req: dict, slots: list[dict]) -> tuple[bytes, dict]:
    """Make selected text inside a repeat content block one of its detail columns."""
    from . import docx_blocks as db
    blk = next((s for s in slots if s["id"] == req.get("block_id") and s.get("block")), None)
    if blk is None:
        raise TemplateError("no_block", "找不到该重复内容块")
    pkg = Package(work)
    part = req.get("part") or pkg.main_part()
    container = dc.part_container(pkg, part)
    root = pkg.xml(part)
    sdt = db.find_block(root, blk["id"])
    if sdt is None:
        raise TemplateError("block_lost", "重复内容块在模板中已找不到")
    try:
        p = dc.get_block(container, req["path"])
        start, end = int(req["start"]), int(req["end"])
        if p.tag != dc.w("p") or end <= start:
            raise TemplateError("bad_selection", "请在内容块的段落中选择要作为列的文字")
        name = (req.get("name") or "").strip() or f"第{len(blk.get('columns', [])) + 1}列"
        col = db.add_column(sdt, blk["id"], p, start, end, name[:60], blk.get("columns", []))
    except (db.BlockError, dc.DocxError) as e:
        raise TemplateError(e.code, str(e)) from e
    blk = {**blk, "columns": [*blk.get("columns", []), col]}
    return pkg.save(), blk


def docx_remove_block_column(work: bytes, slot: dict, key: str) -> bytes:
    from . import docx_blocks as db
    pkg = Package(work)
    for info in dc.list_parts(pkg):
        db.remove_column(pkg.xml(info["part"]), slot["id"], key)
    return pkg.save()


def docx_remove_slot(work: bytes, slot: dict) -> bytes:
    pkg = Package(work)
    prefix = dc.TAG_PREFIX + slot["id"]
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        for tag, sdt in list(dc.iter_field_sdts(root)):
            full = dc.TAG_PREFIX + tag
            if full == prefix or full.startswith(prefix + "#") or full.startswith(prefix + "@"):
                content = sdt.find(dc.w("sdtContent"))
                # drop the empty placeholder run of insert-mode fields (never the paragraphs of a content block)
                if content is not None and not dc._is_block_sdt(sdt) and not dc.para_text_from_runs(content):
                    for r in list(content):
                        content.remove(r)
                dc.unwrap_sdt(sdt)
    return pkg.save()


def docx_check(work: bytes, slots: list[dict]) -> list[dict]:
    pkg = Package(work)
    found = set()
    for info in dc.list_parts(pkg):
        for tag, _ in dc.iter_field_sdts(pkg.xml(info["part"])):
            found.add(re.split(r"[#@]", tag)[0])
    issues = []
    for sl in slots:
        if sl["id"] not in found:
            issues.append({"level": "error", "code": "slot_lost", "slot": sl["id"], "message": f"字段「{sl['name']}」在模板中已找不到"})
    return issues


def docx_fill(work: bytes, slots: list[dict], values: dict[str, dict], options: dict | None = None) -> tuple[bytes, list[dict]]:
    options = options or {}
    keep = bool(options.get("keep_fields"))
    pkg = Package(work)
    issues: list[dict] = []
    slot_by_id = {s["id"]: s for s in slots}
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        # 1) repeat rows and repeat content blocks
        for sl in [s for s in slots if s["kind"] == "repeat"]:
            fv = values.get(sl["id"]) or {"kind": "rows", "rows": []}
            if sl.get("block"):
                from .docx_blocks import fill_block
                fill_block(root, sl, fv["rows"], keep, issues)
            else:
                _docx_fill_repeat(root, sl, fv["rows"], keep, issues)
        # 2) everything else
        for tag, sdt in list(dc.iter_field_sdts(root)):
            base = re.split(r"[#@]", tag)[0]
            sl = slot_by_id.get(base)
            if sl is None:
                dc.unwrap_sdt(sdt)
                continue
            fv = values.get(sl["id"])
            if sl["kind"] == "region":
                m = re.search(r"@(\d+),(\d+)$", tag)
                i, j = int(m.group(1)), int(m.group(2))
                text = ""
                if fv and fv["kind"] == "matrix" and i < len(fv["rows"]) and j < len(fv["rows"][i]):
                    text = fv["rows"][i][j].text
                dc.fill_sdt(sdt, text, keep)
            elif sl["kind"] == "repeat":
                continue  # already handled (leftover means template row missing)
            else:
                val = fv["value"] if fv and fv["kind"] == "scalar" else V.empty()
                dc.fill_sdt(sdt, val.text or "", keep, style=val.style if val.style else None)
    return pkg.save(), issues


def _docx_fill_repeat(root, sl: dict, rows: list[dict], keep: bool, issues: list[dict]) -> None:
    prefix = dc.TAG_PREFIX + sl["id"] + "#"
    sdts = [(t, s) for t, s in dc.iter_field_sdts(root) if (dc.TAG_PREFIX + t).startswith(prefix)]
    if not sdts:
        return
    tr = sdts[0][1]
    while tr is not None and tr.tag != dc.w("tr"):
        tr = tr.getparent()
    if tr is None:
        issues.append({"level": "error", "slot": sl["id"], "code": "repeat_lost", "message": f"重复明细「{sl['name']}」的模板行已丢失"})
        return
    tbl = tr.getparent()
    while tbl is not None and tbl.tag != dc.w("tbl"):
        tbl = tbl.getparent()
    all_rows = list(dc.iter_rows(tbl))
    ri = all_rows.index(tr)
    header_rows = all_rows[max(0, ri - int(sl.get("header_rows", 0))):ri]
    if sl.get("repeat_header", True):
        for h in header_rows:
            dc.set_row_header(h, True)
    cols = {c["key"]: c for c in sl.get("columns", [])}
    if not rows:
        policy = sl.get("empty_policy", "remove_row")
        if policy == "remove_table":
            tbl.getparent().remove(tbl)
            return
        if policy == "remove_row":
            tr.getparent().remove(tr)
            if not sl.get("keep_header_when_empty", True):
                for h in header_rows:
                    h.getparent().remove(h)
            if not list(dc.iter_rows(tbl)):
                tbl.getparent().remove(tbl)
            return
        new = copy.deepcopy(tr)
        first = True
        for t, sdt in list(dc.iter_field_sdts(new)):
            text = sl.get("placeholder", "（无）") if (policy == "placeholder" and first) else ""
            first = False
            dc.fill_sdt(sdt, text, keep)
        tr.addprevious(new)
        tr.getparent().remove(tr)
        return
    anchor = tr
    for row in rows:
        new = copy.deepcopy(tr)
        for t, sdt in list(dc.iter_field_sdts(new)):
            key = t.split("#", 1)[1] if "#" in t else ""
            v = row.get(key)
            dc.fill_sdt(sdt, v.text if v is not None else "", keep)
        dc.set_row_cant_split(new)
        anchor.addnext(new)
        anchor = new
    tr.getparent().remove(tr)


def _para_text_from_runs(el) -> str:
    return "".join(t.text or "" for t in el.iter(dc.w("t")))


dc.para_text_from_runs = _para_text_from_runs  # small helper used by docx_remove_slot


# =====================================================================  XLSX
def xlsx_add_slot(work: bytes, req: dict, slots: list[dict]) -> tuple[bytes, dict]:
    from .xlsx_doc import XlsxDoc, XlsxError
    x = XlsxDoc(work)
    sheet = req.get("sheet")
    try:
        x.sheet_info(sheet)
    except XlsxError as e:
        raise TemplateError(e.code, str(e)) from e
    kind = req.get("kind", "cell")
    name = req.get("name") or "字段"
    merges = x.merges(sheet)
    if kind == "cell":
        r, c = tabular.parse_cell(req["cell"])
        for (r1, c1, r2, c2) in merges:
            if r1 <= r <= r2 and c1 <= c <= c2 and (r, c) != (r1, c1):
                raise TemplateError("merged_inner", f"{req['cell']} 位于合并单元格内部，请选择合并区域左上角 {tabular.cell_ref(r1, c1)}")
        _check_overlap(slots, sheet, r, c, r, c)
        info = x.get_cell(sheet, r, c)
        mode = req.get("mode", "replace")
        slot = _base_slot(name, "cell", mode=mode, original_text=info["text"], had_formula=bool(info["formula"]),
                          locator={"sheet": sheet, "cell": tabular.cell_ref(r, c)})
        if mode not in ("replace", "insert_after", "insert_before"):
            slot["mode"] = "replace"
        x.set_defined_name("_dw_" + slot["id"], sheet, tabular.cell_ref(r, c))
    elif kind == "region":
        r1, c1, r2, c2 = tabular.parse_range(req["range"])
        for (a, b, cc, d) in merges:
            if not (cc < r1 or a > r2 or d < c1 or b > c2):
                raise TemplateError("merged", f"区域 {req['range']} 包含合并单元格，无法按行列一一对应，请调整区域")
        _check_overlap(slots, sheet, r1, c1, r2, c2)
        slot = _base_slot(name, "region", locator={"sheet": sheet, "range": tabular.range_ref(r1, c1, r2, c2)},
                          rows=r2 - r1 + 1, cols=c2 - c1 + 1)
        x.set_defined_name("_dw_" + slot["id"], sheet, tabular.range_ref(r1, c1, r2, c2))
    elif kind == "repeat":
        row = int(req["row"])  # 0-based template row
        c1 = int(req.get("c1", 0))
        c2 = int(req.get("c2", c1))
        for (a, b, cc, d) in merges:
            if a <= row <= cc and a != cc:
                raise TemplateError("vertical_merge", f"第 {row + 1} 行与纵向合并单元格 {tabular.range_ref(a, b, cc, d)} 相交，不能作为重复行")
        for other in slots:
            if other["kind"] == "repeat" and other["locator"]["sheet"] == sheet and other["locator"]["row"] == row:
                raise TemplateError("duplicate_repeat", "该行已是重复明细行")
        _check_overlap(slots, sheet, row, c1, row, c2)
        header_row = req.get("header_row", row - 1 if row > 0 else None)
        cols = []
        for c in range(c1, c2 + 1):
            if any(b <= c <= d and a == cc == row and c != b for (a, b, cc, d) in merges):
                continue  # covered by a horizontal merge
            hname = x.get_cell(sheet, header_row, c)["text"].strip() if header_row is not None and header_row >= 0 else ""
            cols.append({"key": f"c{c}", "name": hname or f"{tabular.col_letter(c)}列", "col": c,
                         "original_text": x.get_cell(sheet, row, c)["text"]})
        slot = _base_slot(name, "repeat", locator={"sheet": sheet, "row": row, "c1": c1, "c2": c2},
                          header_row=header_row, columns=cols, empty_policy=req.get("empty_policy", "remove_row"),
                          placeholder=req.get("placeholder", "（无）"))
        x.set_defined_name("_dw_" + slot["id"], sheet, tabular.range_ref(row, c1, row, c2))
    else:
        raise TemplateError("bad_kind", f"XLSX 模板不支持的字段类型：{kind}")
    x.touched = False  # adding defined names does not invalidate formula caches
    return x.pkg.save(), slot


def _check_overlap(slots, sheet, r1, c1, r2, c2):
    for s in slots:
        loc = s.get("locator", {})
        if loc.get("sheet") != sheet:
            continue
        if s["kind"] == "cell":
            a, b, c, d = tabular.parse_range(loc["cell"])
        elif s["kind"] == "region":
            a, b, c, d = tabular.parse_range(loc["range"])
        elif s["kind"] == "repeat":
            a, b, c, d = loc["row"], loc.get("c1", 0), loc["row"], loc.get("c2", 0)
        else:
            continue
        if not (c < r1 or a > r2 or d < c1 or b > c2):
            raise TemplateError("overlap", f"与已有字段「{s['name']}」重叠")


def xlsx_remove_slot(work: bytes, slot: dict) -> bytes:
    from .ooxml import q
    pkg = Package(work)
    wb = pkg.xml(pkg.main_part())
    dn = wb.find(q("s", "definedNames"))
    if dn is not None:
        for el in list(dn):
            if el.get("name") == "_dw_" + slot["id"]:
                dn.remove(el)
        if not len(dn):
            wb.remove(dn)
    return pkg.save()


def xlsx_fill(work: bytes, slots: list[dict], values: dict[str, dict], options: dict | None = None) -> tuple[bytes, list[dict]]:
    from .ooxml import q
    from .xlsx_doc import XlsxDoc, XlsxError
    x = XlsxDoc(work)
    issues: list[dict] = []
    dn = x.wb.find(q("s", "definedNames"))
    if dn is not None:
        for el in list(dn):
            if (el.get("name") or "").startswith("_dw_"):
                dn.remove(el)
        if not len(dn):
            x.wb.remove(dn)
    shifts: dict[str, list[Shift]] = {}
    repeats = sorted([s for s in slots if s["kind"] == "repeat"], key=lambda s: (s["locator"]["sheet"], -s["locator"]["row"]))
    try:
        for sl in repeats:
            sheet, row = sl["locator"]["sheet"], sl["locator"]["row"]
            rows = (values.get(sl["id"]) or {"rows": []})["rows"]
            n = len(rows)
            if n == 0:
                policy = sl.get("empty_policy", "remove_row")
                if policy == "remove_row":
                    x.delete_row(sheet, row + 1)
                    shifts.setdefault(sheet, []).append(Shift(row + 1, -1))
                    broken = [c.get("r") for c in x.sheet_root(sheet).iter("{%s}c" % "http://schemas.openxmlformats.org/spreadsheetml/2006/main")
                              if (c.findtext("{%s}f" % "http://schemas.openxmlformats.org/spreadsheetml/2006/main") or "").find("#REF!") >= 0]
                    if broken:
                        issues.append({"level": "warning", "slot": sl["id"], "code": "ref_removed",
                                       "message": f"重复明细「{sl['name']}」没有数据，模板行已删除；引用该行的公式 {'、'.join(broken[:5])} 变为 #REF!。如需保留公式，请把零条明细处理方式改为「保留一行空白」"})
                else:
                    for i, col in enumerate(sl.get("columns", [])):
                        text = sl.get("placeholder", "（无）") if (policy == "placeholder" and i == 0) else ""
                        if text:
                            issues.extend(_w(x.set_cell(sheet, row, col["col"], V.text(text))))
                        else:
                            x.clear_cell(sheet, row, col["col"])
                continue
            if n > 1:
                x.insert_copies_of_row(sheet, row + 1, n - 1)
                shifts.setdefault(sheet, []).append(Shift(row + 2, n - 1, expand_row=row + 1))
            for i, rv in enumerate(rows):
                for col in sl.get("columns", []):
                    v = rv.get(col["key"])
                    if v is None:
                        continue
                    if v.is_empty:
                        x.clear_cell(sheet, row + i, col["col"])
                    else:
                        issues.extend(_w(x.set_cell(sheet, row + i, col["col"], v, data_type=col.get("data_type", "auto"))))

        def mapped_row(sheet: str, r0: int) -> int | None:
            r = r0 + 1
            for sh in shifts.get(sheet, []):
                r = sh.row(r)
                if r is None:
                    return None
            return r - 1

        for sl in slots:
            fv = values.get(sl["id"])
            if sl["kind"] == "cell":
                sheet = sl["locator"]["sheet"]
                r, c = tabular.parse_cell(sl["locator"]["cell"])
                nr = mapped_row(sheet, r)
                if nr is None:
                    issues.append({"level": "error", "slot": sl["id"], "code": "slot_removed", "message": f"字段「{sl['name']}」所在行已被删除"})
                    continue
                val = fv["value"] if fv and fv.get("kind") == "scalar" else V.empty()
                use_display = (fv or {}).get("value_mode") == "display"
                issues.extend(_w(x.set_cell(sheet, nr, c, val, mode=sl.get("mode", "replace"), data_type=sl.get("data_type", "auto"),
                                            use_display=use_display), sl))
            elif sl["kind"] == "region":
                sheet = sl["locator"]["sheet"]
                r1, c1, r2, c2 = tabular.parse_range(sl["locator"]["range"])
                mat = (fv or {}).get("rows")
                if mat is None:
                    continue
                if len(mat) != r2 - r1 + 1 or any(len(rw) != c2 - c1 + 1 for rw in mat):
                    issues.append({"level": "error", "slot": sl["id"], "code": "region_size", "message": f"区域「{sl['name']}」尺寸不一致"})
                    continue
                for i, rw in enumerate(mat):
                    nr = mapped_row(sheet, r1 + i)
                    for j, v in enumerate(rw):
                        if v.is_empty:
                            x.clear_cell(sheet, nr, c1 + j)
                        else:
                            issues.extend(_w(x.set_cell(sheet, nr, c1 + j, v, data_type=sl.get("data_type", "auto")), sl))
        out = x.finalize()
        issues.extend(calc_issues(x.calc_report))
    except XlsxError as e:
        issues.append({"level": "error", "code": e.code, "message": str(e)})
        return b"", issues
    return out, issues


def calc_issues(rep: dict | None) -> list[dict]:
    if not rep:
        return []
    out = []
    if rep.get("computed"):
        out.append({"level": "info", "code": "formula_computed", "message": f"内置公式引擎已计算 {rep['computed']} 个公式并写入结果"})
    if rep.get("unsupported"):
        cells = "、".join(f"{s}!{r}" for s, r, _ in rep["unsupported"][:5])
        reasons = "、".join(sorted({why for _, _, why in rep["unsupported"]}))[:120]
        fb = rep.get("fallback")
        if fb == "libreoffice":
            out.append({"level": "info", "code": "formula_fallback", "message": f"{len(rep['unsupported'])} 个公式（{reasons}）由 LibreOffice 计算：{cells}"})
        else:
            out.append({"level": "warning", "code": "formula_unsupported",
                        "message": f"{len(rep['unsupported'])} 个公式无法在服务器计算（{reasons}），将由 Excel 打开时计算：{cells}"})
    if rep.get("errors"):
        cells = "、".join(f"{s}!{r}={c}" for s, r, c in rep["errors"][:5])
        out.append({"level": "warning", "code": "formula_error", "message": f"{len(rep['errors'])} 个公式的计算结果是错误值：{cells}"})
    return out


def _w(warns: list[str], sl: dict | None = None) -> list[dict]:
    return [{"level": "warning", "code": "xlsx", "slot": sl["id"] if sl else None, "message": w} for w in warns]


# =====================================================================  PDF
def pdf_add_slot(original: bytes, req: dict, slots: list[dict]) -> dict:
    import pymupdf
    from . import pdf_engine as pe
    doc = pe.open_pdf(original)
    kind = req.get("kind", "pdf_box")
    name = req.get("name") or "字段"
    if kind == "pdf_field":
        fname = req["field"]
        for p in doc:
            for wdg in p.widgets() or []:
                if wdg.field_name == fname:
                    slot = _base_slot(name or fname, "pdf_field", locator={"page": p.number, "field": fname, "rect": list(wdg.rect)},
                                      font_size=req.get("font_size") or (wdg.text_fontsize or 10), min_font_size=req.get("min_font_size", 6),
                                      align=req.get("align", "left"), original_text=str(wdg.field_value or ""))
                    doc.close()
                    return slot
        doc.close()
        raise TemplateError("field_missing", f"PDF 中没有表单字段「{fname}」")
    pno = int(req["page"])
    if not 0 <= pno < doc.page_count:
        raise TemplateError("page_missing", f"没有第 {pno + 1} 页")
    page = doc[pno]
    rect = pe.visual_norm_to_unrot(page, req["nrect"]) if "nrect" in req else pymupdf.Rect(req["rect"])
    if rect.width < 4 or rect.height < 4:
        raise TemplateError("too_small", "框选区域太小")
    if kind == "pdf_repeat":
        try:
            return _pdf_repeat_slot(page, rect, req, slots)
        finally:
            doc.close()
    removed = pe.join_words(pe.words_in_rect(page, rect))
    partial = [w for w in pe.words_in_rect(page, rect, "intersect") if w not in pe.words_in_rect(page, rect)]
    kind_page = pe.page_kind(page)
    clear = req.get("clear") or ("text_pixels" if kind_page == "scanned" else "text")
    size = req.get("font_size")
    if not size:
        # default: size of the removed text, else 11
        sizes = []
        for b in page.get_text("dict", clip=rect).get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    sizes.append(sp["size"])
        size = round(max(sizes), 1) if sizes else 11
    text_rot = pe.text_rotation_in(page, rect)
    color, fill, ink_size = _ink_and_fill(page, rect, clear)
    if ink_size and not req.get("font_size") and not sizes:
        size = ink_size
    slot = _base_slot(name, "pdf_box", locator={"page": pno, "rect": [round(x, 2) for x in rect], "page_size": [page.mediabox.width, page.mediabox.height],
                                                "rotation": page.rotation},
                      text_rotate=text_rot if text_rot is not None else page.rotation,
                      font_size=size, min_font_size=req.get("min_font_size", 6), align=req.get("align", "left"),
                      valign=req.get("valign", "middle"), clear=clear, original_text=removed,
                      color=_valid_hex(req.get("color")) or color, fill=fill)
    if partial:
        slot["warning"] = f"有 {len(partial)} 个文字只部分位于框内，它们不会被移除：{pe.join_words(partial)[:40]}"
    doc.close()
    return slot


def _valid_hex(c) -> str | None:
    return c.lower() if isinstance(c, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", c) else None


def _ink_and_fill(page, rect, clear: str, sample_rect=None) -> tuple[str, list[float] | None, float | None]:
    """Text colour to write with (the original ink's colour), for pixel clearing the flat
    fallback fill colour sampled around the area, and – for pixel pages, which have no font
    information – a font size matching the height of the original ink."""
    from . import pdf_engine as pe
    if clear == "text_pixels":
        from .image_template import background_color, ink_metrics
        bg = background_color(page, rect)
        ink, height = ink_metrics(page, sample_rect or rect, bg)
        size = round(min(72.0, max(5.0, height / 0.86)) * 2) / 2 if height else None   # CJK ink ≈ 0.86 em
        return (pe.hexcolor(ink) if ink else "#000000"), list(bg), size
    return (pe.span_color(page, rect) or "#000000"), None, None


def _pdf_repeat_slot(page, rect, req: dict, slots: list[dict]) -> dict:
    import pymupdf
    from . import pdf_engine as pe
    from . import pdf_repeat as pr
    auto = pr.detect_layout(page, rect)
    lay = req.get("layout") or {}
    trect = [float(x) for x in (lay.get("trect") or auto["trect"])]
    cols = []
    for i, c in enumerate(lay.get("cols") or auto["cols"]):
        cols.append({"key": f"c{i}", "name": (str(c.get("name") or "").strip() or f"第{i + 1}列")[:40],
                     "x0": round(float(c["x0"]), 2), "x1": round(float(c["x1"]), 2),
                     "align": c.get("align") if c.get("align") in ("left", "center", "right") else "left"})
    layout = {"trot": auto["trot"], "trect": trect, "cols": cols,
              "row_height": round(float(lay.get("row_height") or auto["row_height"]), 2),
              "font_size": float(req.get("font_size") or auto["font_size"])}
    pr.validate_layout(layout, page)
    urect = pr.to_unrot(pymupdf.Rect(trect), layout["trot"])
    for other in slots:
        loc = other.get("locator") or {}
        if loc.get("page") == page.number and "rect" in loc and (pymupdf.Rect(loc["rect"]) & urect).get_area() > 4:
            raise TemplateError("overlap", f"明细区域与字段「{other['name']}」重叠")
    clear = req.get("clear") or ("text_pixels" if pe.page_kind(page) == "scanned" else "text")
    if req.get("image_template") and req.get("overflow") == "continue":
        raise TemplateError("no_continue", "图片模板只有一页，不能复制页面续写；请选择「视为错误」或「压缩行高」")
    partial = [w for w in pe.words_in_rect(page, urect, "intersect") if w not in pe.words_in_rect(page, urect)]
    color, fill, _ = _ink_and_fill(page, urect, clear)
    if clear == "text_pixels":
        # pixel table: colour and size of the sample row's ink, cell by cell (cells exclude the rules)
        first_row = pr.cell_rects({**layout, "trect": [trect[0], trect[1], trect[2], trect[1] + layout["row_height"]]}, inset=2.5)
        best = None
        for cr in first_row:
            c, f_, sz = _ink_and_fill(page, cr, clear)
            if sz and (best is None or sz > best[1]):
                best = (c, sz)
        if best:
            color = color if req.get("color") else best[0]
            if not req.get("font_size"):
                layout["font_size"] = round(min(best[1], layout["row_height"] * 0.72) * 2) / 2
    slot = _base_slot(req.get("name") or "明细", "repeat",
                      locator={"page": page.number, "rect": [round(x, 2) for x in urect], "page_size": [page.mediabox.width, page.mediabox.height],
                               "rotation": page.rotation},
                      layout=layout, columns=[{"key": c["key"], "name": c["name"]} for c in cols],
                      overflow=req.get("overflow") if req.get("overflow") in ("error", "shrink", "continue") else "error",
                      min_row_scale=min(1.0, max(0.3, float(req.get("min_row_scale", 0.5)))), font_size=layout["font_size"],
                      min_font_size=float(req.get("min_font_size", 5)), clear=clear,
                      empty_policy=req.get("empty_policy") if req.get("empty_policy") in ("blank", "placeholder", "error") else "blank",
                      placeholder=req.get("placeholder", "（无）"), original_text=pe.join_words(pe.words_in_rect(page, urect))[:200],
                      capacity=pr.capacity(trect, layout["row_height"]), color=_valid_hex(req.get("color")) or color, fill=fill)
    if partial:
        slot["warning"] = f"有 {len(partial)} 个文字只部分位于明细区域内，它们不会被移除：{pe.join_words(partial)[:40]}"
    return slot


def pdf_detect_repeat(original: bytes, page_no: int, nrect: list[float]) -> dict:
    """Layout proposal shown in the dialog before a PDF repeat area is created."""
    import pymupdf
    from . import pdf_engine as pe
    from . import pdf_repeat as pr
    doc = pe.open_pdf(original)
    try:
        if not 0 <= page_no < doc.page_count:
            raise TemplateError("page_missing", f"没有第 {page_no + 1} 页")
        page = doc[page_no]
        rect = pe.visual_norm_to_unrot(page, nrect)
        if rect.width < 12 or rect.height < 8:
            raise TemplateError("too_small", "明细区域太小")
        out = pr.detect_layout(page, rect)
        out["page_w"], out["page_h"] = page.rect.width, page.rect.height
        # the proposal in visual normalised coordinates, for drawing on the page image
        out["nrect"] = pe.unrot_to_visual_norm(page, pr.to_unrot(pymupdf.Rect(out["trect"]), out["trot"]))
        return out
    finally:
        doc.close()


def pdf_working_copy(original: bytes, slots: list[dict]) -> tuple[bytes, list[str]]:
    from . import pdf_engine as pe
    return pe.make_working_copy(original, slots)


def image_fill(work: bytes, slots: list[dict], values: dict[str, dict], options: dict | None = None) -> tuple[bytes, list[dict]]:
    """Image template: fill the wrapper PDF, check the values are in it, then rasterise."""
    from .image_template import rasterise
    meta = (options or {}).get("image")
    if not meta:
        raise TemplateError("image_meta", "图片模板缺少尺寸信息，请重新上传模板")
    pdf, issues = pdf_fill(work, slots, values, options)
    if any(i.get("level") == "error" for i in issues):
        return b"", issues
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        if doc.page_count != 1:
            return b"", issues + [{"level": "error", "code": "image_pages", "message": "图片模板只能输出一页；明细超出时请改为「压缩行高」或「视为错误」"}]
        text = re.sub(r"\s+", "", doc[0].get_text())
    finally:
        doc.close()
    for sl in slots:                    # the text must be in the page before it becomes pixels
        fv = values.get(sl["id"])
        vals = [fv["value"].text] if fv and fv.get("kind") == "scalar" else \
            [v.text for r in fv["rows"] for v in r.values()] if fv and fv.get("kind") == "rows" else []
        for v in vals:
            if v and re.sub(r"\s+", "", v) not in text:
                return b"", issues + [{"level": "error", "slot": sl["id"], "code": "not_written", "message": f"字段「{sl['name']}」的值「{v[:40]}」未能写入图片"}]
    return rasterise(pdf, meta), issues


def pdf_fill(work: bytes, slots: list[dict], values: dict[str, dict], options: dict | None = None) -> tuple[bytes, list[dict]]:
    from . import pdf_engine as pe
    texts, rows = {}, {}
    for sl in slots:
        fv = values.get(sl["id"])
        if sl["kind"] == "repeat":
            rows[sl["id"]] = [{k: (v.text or "") for k, v in r.items()} for r in (fv["rows"] if fv and fv.get("kind") == "rows" else [])]
        else:
            texts[sl["id"]] = fv["value"].text if fv and fv.get("kind") == "scalar" else ""
    out, issues = pe.fill_pdf(work, [s for s in slots if s["kind"] in ("pdf_box", "pdf_field", "repeat")], texts, rows)
    return out, issues
