"""Direct XML editing of XLSX workbooks.

Only the cells, rows and references that a fill needs are touched; every other
part of the package (charts, drawings, pivot tables, VBA, custom XML ...) is
copied unchanged.  Formula cached values in the workbook are cleared after a
fill and ``fullCalcOnLoad`` is set, so readers never see stale results; this
module does not evaluate formulas.
"""
from __future__ import annotations

import bisect
import copy
import re

from lxml import etree
from openpyxl.formula.translate import Translator

from . import values as V
from .numfmt import datetime_to_serial, is_date_format
from .ooxml import NS, Package, q
from .refshift import MAX_ROW, Shift, shift_area, shift_formula, shift_sqref, split_sheet
from .tabular import cell_ref, col_index, col_letter, parse_cell, parse_range

S = NS["s"]
R_NS = NS["r"]
XDR = NS["xdr"]
REL_SST = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings"
CT_SST = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_CELLREF = re.compile(r"^([A-Z]{1,3})(\d+)$")
_STRING_LIT = re.compile(r'"(?:[^"]|"")*"')
# network functions, DDE (app|topic!item) and external workbook references ([1]Sheet!A1)
# threaded comments (Excel 365) sit next to the legacy notes and carry their own cell references
REL_THREADED = "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment"
NS_THREADED = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
_OUTSIDE = re.compile(r"(?i)\b(?:_xlfn\.)?(?:WEBSERVICE|RTD|FILTERXML|IMAGE)\s*\(|\||\[\d+\]|\[[^\]]*\.xl")


def s(local: str) -> str:
    return "{%s}%s" % (S, local)


class XlsxError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


BUILTIN_DATE_IDS = set(range(14, 23)) | {27, 30, 36, 45, 46, 47, 50, 57}
BUILTIN_FORMATS = {0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%", 11: "0.00E+00",
                   12: "# ?/?", 13: "# ??/??", 14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
                   18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yy h:mm",
                   37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)", 39: "#,##0.00;(#,##0.00)",
                   40: "#,##0.00;[Red](#,##0.00)", 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@"}


class XlsxDoc:
    def __init__(self, data: bytes):
        self.pkg = Package(data)
        if not self.pkg.has("xl/workbook.xml"):
            raise XlsxError("invalid", "不是有效的 XLSX 工作簿")
        self.wb_part = self.pkg.main_part()
        self.wb = self.pkg.xml(self.wb_part)
        self.sheets: list[dict] = []
        for el in self.wb.iter(s("sheet")):
            rid = el.get("{%s}id" % R_NS)
            self.sheets.append({"name": el.get("name"), "part": self.pkg.rel_target(self.wb_part, rid),
                                "state": el.get("state", "visible"), "el": el})
        pr = self.wb.find(s("workbookPr"))
        self.date1904 = pr is not None and pr.get("date1904") in ("1", "true")
        self._sst_part = None
        for rel in self.pkg.rels(self.wb_part):
            if rel.type == REL_SST:
                self._sst_part = self.pkg.resolve(self.wb_part, rel.target)
        self._sst_list: list[str] | None = None
        self._styles_part = None
        for rel in self.pkg.rels(self.wb_part):
            if rel.type.endswith("/styles"):
                self._styles_part = self.pkg.resolve(self.wb_part, rel.target)
        self._numfmt_cache: dict[int, str] = {}
        self._normalized: set[str] = set()
        # sheetData element -> (sorted row numbers, {row number: row element}); rebuilt after
        # any renumbering.  Scanning every row for every written cell made a fill of N detail
        # rows O(N²).  Keyed by the element itself, which also keeps its proxy alive.
        self._rows: dict = {}
        self.touched = False
        self.warnings: list[str] = []
        self.calc_report: dict | None = None

    # ------------------------------------------------------------ lookup
    def sheet_info(self, name: str) -> dict:
        for sh in self.sheets:
            if sh["name"] == name:
                return sh
        raise XlsxError("missing_sheet", f"工作簿中没有名为「{name}」的工作表")

    def sheet_root(self, name: str):
        part = self.sheet_info(name)["part"]
        root = self.pkg.xml(part)
        if part not in self._normalized:
            self._normalize(root)
            self._normalized.add(part)
        return root

    def _sheet_data(self, root):
        sd = root.find(s("sheetData"))
        if sd is None:
            raise XlsxError("invalid", "工作表缺少 sheetData")
        return sd

    def _normalize(self, root) -> None:
        """Give every row/cell an explicit r attribute."""
        sd = self._sheet_data(root)
        rn = 0
        for row in sd.findall(s("row")):
            rn = int(row.get("r")) if row.get("r") else rn + 1
            row.set("r", str(rn))
            cn = -1
            for c in row.findall(s("c")):
                if c.get("r"):
                    m = _CELLREF.match(c.get("r"))
                    cn = col_index(m.group(1)) if m else cn + 1
                else:
                    cn += 1
                c.set("r", f"{col_letter(cn)}{rn}")

    # ------------------------------------------------------------ shared strings
    def _sst(self):
        if self._sst_part is None:
            root = etree.Element(s("sst"), nsmap={None: S})
            self._sst_part = "xl/sharedStrings.xml"
            self.pkg.add_part(self._sst_part, root, CT_SST, self.wb_part, REL_SST)
        return self.pkg.xml(self._sst_part)

    def shared_strings(self) -> list[str]:
        if self._sst_list is None:
            if self._sst_part is None or not self.pkg.has(self._sst_part):
                self._sst_list = []
            else:
                self._sst_list = ["".join(t.text or "" for t in si.iter(s("t")) if t.getparent().tag != s("rPh"))
                                  for si in self._sst().findall(s("si"))]
        return self._sst_list

    def add_string(self, text: str) -> int:
        lst = self.shared_strings()
        root = self._sst()
        si = etree.SubElement(root, s("si"))
        t = etree.SubElement(si, s("t"))
        t.text = text
        if text != text.strip() or "\n" in text:
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        lst.append(text)
        root.set("uniqueCount", str(len(lst)))
        if "count" in root.attrib:
            del root.attrib["count"]
        return len(lst) - 1

    # ------------------------------------------------------------ styles
    def numfmt_of_style(self, style_index: int) -> str:
        if style_index in self._numfmt_cache:
            return self._numfmt_cache[style_index]
        fmt = "General"
        if self._styles_part and self.pkg.has(self._styles_part):
            st = self.pkg.xml(self._styles_part)
            xfs = st.find(s("cellXfs"))
            if xfs is not None:
                lst = xfs.findall(s("xf"))
                if 0 <= style_index < len(lst):
                    fid = int(lst[style_index].get("numFmtId", "0"))
                    custom = {int(n.get("numFmtId")): n.get("formatCode") for n in st.iter(s("numFmt"))}
                    fmt = custom.get(fid) or BUILTIN_FORMATS.get(fid, "General")
                    if fid in BUILTIN_DATE_IDS and fid not in custom:
                        fmt = BUILTIN_FORMATS.get(fid, "yyyy-mm-dd")
        self._numfmt_cache[style_index] = fmt
        return fmt

    # ------------------------------------------------------------ rows & cells
    def _row_index(self, sd):
        idx = self._rows.get(sd)
        if idx is None:
            by: dict[int, object] = {}
            for row in sd.findall(s("row")):
                by.setdefault(int(row.get("r")), row)
            idx = (sorted(by), by)
            self._rows[sd] = idx
        return idx

    def _rows_changed(self) -> None:
        self._rows.clear()

    def _find_row(self, sd, r: int, create: bool):
        keys, by = self._row_index(sd)
        row = by.get(r)
        if row is not None or not create:
            return row
        new = etree.Element(s("row"))
        new.set("r", str(r))
        pos = bisect.bisect_left(keys, r)
        if pos < len(keys):
            by[keys[pos]].addprevious(new)
        else:
            sd.append(new)
        keys.insert(pos, r)
        by[r] = new
        return new

    def _find_cell(self, row, c: int, create: bool, root=None):
        r = int(row.get("r"))
        ref = f"{col_letter(c)}{r}"
        for cell in row.findall(s("c")):
            m = _CELLREF.match(cell.get("r"))
            cc = col_index(m.group(1))
            if cc == c:
                return cell
            if cc > c:
                if not create:
                    return None
                new = etree.Element(s("c"))
                new.set("r", ref)
                self._default_style(new, row, c, root)
                cell.addprevious(new)
                return new
        if not create:
            return None
        new = etree.SubElement(row, s("c"))
        new.set("r", ref)
        self._default_style(new, row, c, root)
        return new

    def _default_style(self, cell, row, c: int, root) -> None:
        if row.get("customFormat") in ("1", "true") and row.get("s"):
            cell.set("s", row.get("s"))
            return
        if root is not None:
            cols = root.find(s("cols"))
            if cols is not None:
                for col in cols.findall(s("col")):
                    if int(col.get("min")) <= c + 1 <= int(col.get("max")) and col.get("style"):
                        cell.set("s", col.get("style"))
                        return

    def get_cell(self, sheet: str, r: int, c: int) -> dict:
        root = self.sheet_root(sheet)
        row = self._find_row(self._sheet_data(root), r + 1, False)
        cell = self._find_cell(row, c, False) if row is not None else None
        if cell is None:
            return {"exists": False, "text": "", "formula": None, "style": 0}
        f = cell.find(s("f"))
        return {"exists": True, "text": self._cell_text(cell), "formula": f.text if f is not None else None,
                "style": int(cell.get("s", "0")), "t": cell.get("t")}

    def _cell_text(self, cell) -> str:
        t = cell.get("t")
        v = cell.find(s("v"))
        if t == "s" and v is not None and v.text is not None:
            idx = int(v.text)
            lst = self.shared_strings()
            return lst[idx] if 0 <= idx < len(lst) else ""
        if t == "inlineStr":
            is_ = cell.find(s("is"))
            return "".join(x.text or "" for x in is_.iter(s("t"))) if is_ is not None else ""
        return v.text if v is not None and v.text is not None else ""

    def set_cell(self, sheet: str, r: int, c: int, value: V.Value, *, mode: str = "replace",
                 data_type: str = "auto", use_display: bool = False) -> list[str]:
        """Write ``value`` into 0-based (r, c).  Returns warnings."""
        warns: list[str] = []
        root = self.sheet_root(sheet)
        sd = self._sheet_data(root)
        row = self._find_row(sd, r + 1, True)
        cell = self._find_cell(row, c, True, root)
        existing = self._cell_text(cell) if mode in ("insert_after", "insert_before") else ""
        if cell.find(s("f")) is not None:
            warns.append(f"{cell_ref(r, c)} 原有公式已被数据替换")
        for child in list(cell):
            if child.tag in (s("v"), s("f"), s("is")):
                cell.remove(child)
        if "t" in cell.attrib:
            del cell.attrib["t"]
        self.touched = True
        style_idx = int(cell.get("s", "0"))
        target_fmt = self.numfmt_of_style(style_idx)
        if mode in ("insert_after", "insert_before"):
            txt = value.text or ""
            combined = existing + txt if mode == "insert_after" else txt + existing
            self._write_string(cell, combined)
            return warns
        if value.is_empty:
            return warns
        dt = data_type
        if dt == "text" or use_display:
            self._write_string(cell, value.text if value.text is not None else str(value.v))
            return warns
        if dt == "auto":
            if value.t in V.NUMERIC_TYPES:
                dt = "text" if target_fmt == "@" else "number"
                if value.fmt and value.fmt not in ("General", "@") and target_fmt == "General" and value.text != str(value.v):
                    warns.append(f"{cell_ref(r, c)}：来源显示为「{value.text}」，目标单元格为常规格式，将按数值 {value.v} 显示；如需保留显示文本，请在映射中选择「写入显示文本」")
            elif value.t in (V.DATE, V.DATETIME, V.TIME):
                dt = "date" if is_date_format(target_fmt) else "text"
            elif value.t == V.BOOL:
                dt = "bool"
            else:
                dt = "text"
        if dt == "number":
            num = value.as_decimal()
            if num is None:
                self._write_string(cell, value.text)
                warns.append(f"{cell_ref(r, c)}：值「{value.text}」不是数字，已按文本写入")
                return warns
            v = etree.SubElement(cell, s("v"))
            v.text = str(value.v) if value.t in V.NUMERIC_TYPES else format(num, "f")
            return warns
        if dt == "date":
            d = V.to_python_date(value)
            if d is None:
                self._write_string(cell, value.text)
                return warns
            v = etree.SubElement(cell, s("v"))
            serial = datetime_to_serial(d, self.date1904)
            v.text = V.float_repr(serial)
            return warns
        if dt == "bool":
            cell.set("t", "b")
            v = etree.SubElement(cell, s("v"))
            v.text = "1" if value.v else "0"
            return warns
        self._write_string(cell, value.text)
        return warns

    def _write_string(self, cell, text: str) -> None:
        text = _XML_BAD.sub("", text or "")
        if len(text) > 32767:
            raise XlsxError("too_long", f"{cell.get('r')}：文本长度 {len(text)} 超过 Excel 单元格上限 32767")
        for child in list(cell):
            if child.tag in (s("v"), s("f"), s("is")):
                cell.remove(child)
        if text == "":
            if "t" in cell.attrib:
                del cell.attrib["t"]
            return
        idx = self.add_string(text)
        cell.set("t", "s")
        v = etree.SubElement(cell, s("v"))
        v.text = str(idx)

    def clear_cell(self, sheet: str, r: int, c: int) -> None:
        root = self.sheet_root(sheet)
        row = self._find_row(self._sheet_data(root), r + 1, False)
        if row is None:
            return
        cell = self._find_cell(row, c, False)
        if cell is None:
            return
        for child in list(cell):
            if child.tag in (s("v"), s("f"), s("is")):
                cell.remove(child)
        if "t" in cell.attrib:
            del cell.attrib["t"]
        self.touched = True

    # ------------------------------------------------------------ merges
    def merges(self, sheet: str) -> list[tuple[int, int, int, int]]:
        root = self.sheet_root(sheet)
        mc = root.find(s("mergeCells"))
        if mc is None:
            return []
        return [parse_range(m.get("ref")) for m in mc.findall(s("mergeCell"))]

    def _add_merge(self, root, ref: str, recount: bool = True) -> None:
        mc = root.find(s("mergeCells"))
        if mc is None:
            mc = etree.Element(s("mergeCells"))
            # insert after sheetData / sheetProtection / autoFilter... simplest schema-valid spot:
            anchor = None
            for tag in ("customSheetViews", "dataConsolidate", "sortState", "autoFilter", "scenarios",
                        "protectedRanges", "sheetProtection", "sheetCalcPr", "sheetData"):
                anchor = root.find(s(tag))
                if anchor is not None:
                    break
            anchor.addnext(mc)
        m = etree.SubElement(mc, s("mergeCell"))
        m.set("ref", ref)
        if recount:
            mc.set("count", str(len(mc.findall(s("mergeCell")))))

    # ------------------------------------------------------------ shared formula expansion
    def expand_shared_formulas(self) -> None:
        for sh in self.sheets:
            root = self.sheet_root(sh["name"])
            masters: dict[str, tuple[str, str]] = {}
            cells = list(root.iter(s("c")))
            for c in cells:
                f = c.find(s("f"))
                if f is not None and f.get("t") == "shared" and f.text:
                    masters[f.get("si")] = (f.text, c.get("r"))
            if not masters:
                continue
            for c in cells:
                f = c.find(s("f"))
                if f is None or f.get("t") != "shared":
                    continue
                si = f.get("si")
                if si not in masters:
                    continue
                text, origin = masters[si]
                if f.text and c.get("r") == origin:
                    new_text = text
                else:
                    try:
                        new_text = Translator("=" + text, origin=origin).translate_formula(c.get("r"))[1:]
                    except Exception:
                        continue
                for a in ("t", "si", "ref"):
                    if a in f.attrib:
                        del f.attrib[a]
                f.text = new_text

    # ------------------------------------------------------------ pivot tables, content extent
    def pivot_tables(self, sheet: str) -> list[dict]:
        """The pivot tables placed on ``sheet``: {name, ref, part, rows: (first, last)} (1-based rows)."""
        out = []
        part = self.sheet_info(sheet)["part"]
        for rel in self.pkg.rels(part):
            if rel.external or not rel.type.endswith("/pivotTable"):
                continue
            target = self.pkg.resolve(part, rel.target)
            if not self.pkg.has(target):
                continue
            loc = self.pkg.xml(target).find(s("location"))
            if loc is None or not loc.get("ref"):
                continue
            r1, _, r2, _ = parse_range(loc.get("ref"))
            out.append({"name": self.pkg.xml(target).get("name") or "", "ref": loc.get("ref"), "part": target,
                        "rows": (r1 + 1, r2 + 1)})
        return out

    def last_content_row(self, sheet: str) -> int:
        """The last row (1-based) holding a value or a formula; 0 for an empty sheet. Rows that
        only carry formatting may be pushed off the sheet by an insertion, as in Excel."""
        sd = self._sheet_data(self.sheet_root(sheet))
        last = 0
        for row in sd.findall(s("row")):
            r = int(row.get("r"))
            if r > last and any(c.find(s("v")) is not None or c.find(s("f")) is not None or c.find(s("is")) is not None
                                for c in row.findall(s("c"))):
                last = r
        return last

    def _check_pivots(self, sheet: str, shift: Shift) -> None:
        """A pivot table moves whole or not at all: rows inserted into it or deleted from it would
        split it, and its definition cannot follow. Refused before anything is changed."""
        for pv in self.pivot_tables(sheet):
            a, b = pv["rows"]
            if shift.delta > 0:
                inside = a < shift.start <= b
            else:
                lo, hi = shift.start, shift.start - shift.delta - 1
                inside = not (hi < a or lo > b)
            if inside:
                raise XlsxError("pivot_overlap", f"数据透视表「{pv['name']}」（{pv['ref']}）与插入或删除的行相交，无法随之移动：请把重复明细行移到透视表之外")

    # ------------------------------------------------------------ row shifting
    def shift_rows(self, sheet: str, shift: Shift) -> None:
        """Apply a row insertion/deletion to cells and every dependent structure."""
        self._check_pivots(sheet, shift)
        self.expand_shared_formulas()
        info = self.sheet_info(sheet)
        root = self.sheet_root(sheet)
        sd = self._sheet_data(root)
        self.touched = True
        self._rows_changed()
        # 1. rows and cells (process in reverse for insertion to avoid collisions)
        rows = sd.findall(s("row"))
        for row in rows:
            r = int(row.get("r"))
            nr = shift.row(r)
            if nr is None:
                sd.remove(row)
                continue
            if nr != r:
                row.set("r", str(nr))
                for c in row.findall(s("c")):
                    m = _CELLREF.match(c.get("r"))
                    c.set("r", f"{m.group(1)}{nr}")
                spans = row.get("spans")
                if spans is None:
                    pass
        # 2. formulas everywhere
        for sh in self.sheets:
            sroot = self.sheet_root(sh["name"])
            for f in sroot.iter(s("f")):
                if f.text:
                    f.text = shift_formula(f.text, shift, sheet, sh["name"])
                if f.get("ref") and sh["name"] == sheet:
                    nr = shift_area(f.get("ref"), shift)
                    if nr == "#REF!":
                        f.getparent().remove(f)
                    else:
                        f.set("ref", nr)
        # 3. sheet-level structures.  Merges, hyperlinks, selections etc. only move;
        #    formatting/validation/filter ranges may also grow over inserted detail rows.
        plain = Shift(shift.start, shift.delta, None)
        for tag, attr, grows in (("mergeCell", "ref", False), ("hyperlink", "ref", False), ("autoFilter", "ref", True),
                                 ("sortState", "ref", True), ("dimension", "ref", False), ("selection", "sqref", False),
                                 ("conditionalFormatting", "sqref", True), ("dataValidation", "sqref", True),
                                 ("ignoredError", "sqref", False), ("protectedRange", "sqref", False)):
            sh_ = plain
            if grows:
                sh_ = shift.variant(expand_single=(attr == "sqref"))
            for el in list(root.iter(s(tag))):
                val = el.get(attr)
                if not val:
                    continue
                new = shift_sqref(val, sh_) if attr == "sqref" or " " in val else shift_area(val, sh_)
                if new in ("", "#REF!"):
                    if tag == "mergeCell":
                        el.getparent().remove(el)
                    elif tag in ("conditionalFormatting", "dataValidation", "hyperlink", "ignoredError", "protectedRange"):
                        el.getparent().remove(el)
                    continue
                el.set(attr, new)
        for el in root.iter(s("selection")):
            ac = el.get("activeCell")
            if ac:
                el.set("activeCell", shift_area(ac, plain).replace("#REF!", "A1"))
        for el in root.iter(s("pane")):
            tl = el.get("topLeftCell")
            if tl:
                el.set("topLeftCell", shift_area(tl, plain).replace("#REF!", "A1"))
        for el in root.iter(s("formula"), s("formula1"), s("formula2")):
            if el.text:
                el.text = shift_formula(el.text, shift, sheet, sheet)
        # x14 extensions (conditional formatting / data validation stored in extLst)
        grow_sq = shift.variant(expand_single=True)
        for el in root.iter("{%s}sqref" % NS["xm"]):
            if el.text:
                el.text = shift_sqref(el.text, grow_sq) or el.text
        for el in root.iter("{%s}f" % NS["xm"]):
            if el.text:
                el.text = shift_formula(el.text, shift, sheet, sheet)
        rb = root.find(s("rowBreaks"))
        if rb is not None:
            for brk in rb.findall(s("brk")):
                nr = shift.row(int(brk.get("id", "0")))
                if nr is None:
                    rb.remove(brk)
                else:
                    brk.set("id", str(nr))
        mc = root.find(s("mergeCells"))
        if mc is not None:
            mc.set("count", str(len(mc.findall(s("mergeCell")))))
        # 4. defined names
        dn = self.wb.find(s("definedNames"))
        if dn is not None:
            for el in dn.findall(s("definedName")):
                if el.text:
                    el.text = shift_formula(el.text, shift, sheet, sheet if el.get("localSheetId") else "\0")
        # 5. related parts: tables, drawings, comments, charts, pivot caches
        part = info["part"]
        for rel in self.pkg.rels(part):
            if rel.external:
                continue
            target = self.pkg.resolve(part, rel.target)
            if not self.pkg.has(target):
                continue
            if rel.type.endswith("/table"):
                troot = self.pkg.xml(target)
                if troot.get("ref"):
                    troot.set("ref", shift_area(troot.get("ref"), shift))
                for af in troot.iter(s("autoFilter")):
                    if af.get("ref"):
                        af.set("ref", shift_area(af.get("ref"), shift))
            elif rel.type.endswith("/drawing"):
                droot = self.pkg.xml(target)
                for anchor_tag in ("from", "to"):
                    for a in droot.iter("{%s}%s" % (XDR, anchor_tag)):
                        row_el = a.find("{%s}row" % XDR)
                        if row_el is not None and row_el.text is not None:
                            nr = plain.row(int(row_el.text) + 1)
                            row_el.text = str((nr if nr is not None else (MAX_ROW if shift.delta > 0 else shift.start)) - 1)
            elif rel.type.endswith("/comments"):
                croot = self.pkg.xml(target)
                for cm in croot.iter(s("comment")):
                    nr = shift_area(cm.get("ref"), plain)
                    if nr == "#REF!":
                        cm.getparent().remove(cm)
                    else:
                        cm.set("ref", nr)
            elif rel.type.endswith("/vmlDrawing"):
                self._shift_vml(target, shift)
            elif rel.type == REL_THREADED:
                # the threaded comment and its replies share the cell's ref; a comment that stays
                # behind points at another cell than its legacy note (Excel then repairs the file)
                troot = self.pkg.xml(target)
                for tc in list(troot.iter("{%s}threadedComment" % NS_THREADED)):
                    nr = shift_area(tc.get("ref") or "", plain)
                    if nr == "#REF!":
                        tc.getparent().remove(tc)
                    elif nr:
                        tc.set("ref", nr)
            elif rel.type.endswith("/pivotTable"):
                # the pivot's cells move with the rows; its definition must say where they are now,
                # or Excel and LibreOffice lay it out again over the old rows (_check_pivots refused a split)
                loc = self.pkg.xml(target).find(s("location"))
                if loc is not None and loc.get("ref"):
                    loc.set("ref", shift_area(loc.get("ref"), plain))
        # chart series pointing at the template row (even a single cell) describe the detail data → grow with it
        chart_shift = shift.variant(expand_single=True)
        for name in self.pkg.names:
            if name.startswith("xl/charts/chart") and name.endswith(".xml"):
                croot = self.pkg.xml(name)
                for f in croot.iter("{http://schemas.openxmlformats.org/drawingml/2006/chart}f"):
                    if f.text:
                        f.text = shift_formula(f.text, chart_shift, sheet, "\0")
            elif name.startswith("xl/pivotCache/pivotCacheDefinition") and name.endswith(".xml"):
                proot = self.pkg.xml(name)
                for ws in proot.iter(s("worksheetSource")):
                    if ws.get("sheet") == sheet and ws.get("ref"):
                        ws.set("ref", shift_area(ws.get("ref"), shift))

    def _shift_vml(self, part: str, shift: Shift) -> None:
        data = self.pkg.read(part)
        try:
            root = etree.fromstring(data, etree.XMLParser(recover=True, resolve_entities=False, no_network=True))
        except etree.XMLSyntaxError:
            return
        X = "urn:schemas-microsoft-com:office:excel"
        changed = False
        for cd in root.iter("{%s}ClientData" % X):
            row_el = cd.find("{%s}Row" % X)
            if row_el is not None and row_el.text:
                nr = shift.row(int(row_el.text) + 1)
                row_el.text = str((nr if nr is not None else (MAX_ROW if shift.delta > 0 else shift.start)) - 1)
                changed = True
            anc = cd.find("{%s}Anchor" % X)
            if anc is not None and anc.text:
                parts = [p.strip() for p in anc.text.split(",")]
                if len(parts) == 8:
                    for i in (2, 6):
                        nr = shift.row(int(parts[i]) + 1)
                        parts[i] = str((nr if nr is not None else (MAX_ROW if shift.delta > 0 else shift.start)) - 1)
                    anc.text = ", ".join(parts)
                    changed = True
        if changed:
            self.pkg.write(part, etree.tostring(root, xml_declaration=True, encoding="UTF-8"))

    def insert_copies_of_row(self, sheet: str, template_row: int, count: int) -> None:
        """Insert ``count`` copies of 1-based ``template_row`` directly below it.
        Formulas in the copies are translated; horizontal merges are replicated."""
        if count <= 0:
            return
        last = max(self.last_content_row(sheet), template_row)
        if last + count > MAX_ROW:
            raise XlsxError("row_limit", f"插入 {count} 行后工作表「{sheet}」共 {last + count} 行，超过 Excel 工作表的上限 1,048,576 行")
        root = self.sheet_root(sheet)
        merges = self.merges(sheet)
        for (r1, c1, r2, c2) in merges:
            if r1 < template_row - 1 <= r2 or r1 <= template_row - 1 < r2:
                if r1 != r2:
                    raise XlsxError("vertical_merge", f"模板明细行 {template_row} 与纵向合并单元格 {cell_ref(r1, c1)}:{cell_ref(r2, c2)} 相交，无法重复该行")
        sd = self._sheet_data(root)
        tpl0 = self._find_row(sd, template_row, False)
        if tpl0 is not None:
            for f in tpl0.iter(s("f")):
                if f.get("t") == "array" and f.get("ref"):
                    a1, _, a2 = f.get("ref").partition(":")
                    if parse_cell(a1)[0] != parse_cell(a2 or a1)[0]:
                        # a multi-row array formula has one master cell; copying the row cannot
                        # make a consistent block of it
                        raise XlsxError("array_formula", f"模板明细行 {template_row} 中的数组公式跨越多行（{f.get('ref')}），无法随明细行复制")
        self.shift_rows(sheet, Shift(template_row + 1, count, expand_row=template_row))
        tpl = self._find_row(sd, template_row, True)
        anchor = tpl
        row_merges = [(c1, c2) for (r1, c1, r2, c2) in merges if r1 == r2 == template_row - 1]
        for i in range(1, count + 1):
            new_r = template_row + i
            new = copy.deepcopy(tpl)
            new.set("r", str(new_r))
            for c in new.findall(s("c")):
                m = _CELLREF.match(c.get("r"))
                old_ref = c.get("r")
                c.set("r", f"{m.group(1)}{new_r}")
                f = c.find(s("f"))
                if f is not None and f.text:
                    try:
                        f.text = Translator("=" + f.text, origin=old_ref).translate_formula(c.get("r"))[1:]
                    except Exception:
                        pass
                    if f.get("t") == "array":
                        # the copy's array range is its own cells on its own row, not the template's
                        a1, _, a2 = (f.get("ref") or old_ref).partition(":")
                        c1_, c2_ = parse_cell(a1)[1], parse_cell(a2 or a1)[1]
                        f.set("ref", f"{col_letter(c1_)}{new_r}" + (f":{col_letter(c2_)}{new_r}" if c2_ != c1_ else ""))
                    else:
                        for a in ("t", "si", "ref"):
                            if a in f.attrib:
                                del f.attrib[a]
            anchor.addnext(new)
            anchor = new
            for c1, c2 in row_merges:
                self._add_merge(root, f"{col_letter(c1)}{new_r}:{col_letter(c2)}{new_r}", recount=False)
        mc = root.find(s("mergeCells"))
        if mc is not None:
            mc.set("count", str(len(mc.findall(s("mergeCell")))))
        self._rows_changed()

    def delete_row(self, sheet: str, row: int) -> None:
        merges = self.merges(sheet)
        for (r1, c1, r2, c2) in merges:
            if r1 != r2 and r1 <= row - 1 <= r2:
                raise XlsxError("vertical_merge", f"第 {row} 行与纵向合并单元格相交，无法删除")
        self.shift_rows(sheet, Shift(row, -1))

    # ------------------------------------------------------------ finalize
    def finalize(self) -> bytes:
        """Write back the package.  When cells changed, every formula is re-evaluated by the
        built-in engine (synwevo_docs.engine.formula) and its cached value written; formulas the
        engine does not support are computed by LibreOffice when installed, otherwise their
        stale cache is removed.  ``self.calc_report`` describes what happened."""
        self.calc_report = None
        if self.touched:
            from .formula import compute_workbook
            for sh in self.sheets:
                self._update_dimension(self.sheet_root(sh["name"]))
            report = compute_workbook(self)
            self.calc_report = report
            calc = self.wb.find(s("calcPr"))
            if calc is None:
                calc = etree.Element(s("calcPr"))
                _insert_after_last(self.wb, calc, _WB_BEFORE_CALCPR)
            # Excel/LibreOffice still recalculate on open; our cached values are what other readers see
            calc.set("fullCalcOnLoad", "1")
            for rel in self.pkg.rels(self.wb_part):
                if rel.type.endswith("/calcChain"):
                    self.pkg.remove_part(self.pkg.resolve(self.wb_part, rel.target), self.wb_part)
            data = self.pkg.save()
            if report["unsupported"] and self._reaches_outside():
                # LibreOffice would evaluate WEBSERVICE / DDE / external links on this server,
                # i.e. make network requests chosen by whoever wrote the template
                report["fallback"] = "blocked"
            elif report["unsupported"]:
                from .converters import ConversionError, soffice_path
                if soffice_path():
                    from .xlsx_cache import fill_cached_values
                    # only the cells the built-in engine could not compute: the others already
                    # carry correct, correctly typed caches
                    data, info = fill_cached_values(data, {(sh_, ref) for sh_, ref, _ in report["unsupported"]})
                    report["fallback"] = "libreoffice" if not any(i["level"] == "warning" for i in info) else "failed"
                else:
                    report["fallback"] = "none"
            return data
        return self.pkg.save()

    def _reaches_outside(self) -> bool:
        """Any formula (or defined name) that fetches or links something outside the workbook."""
        texts = []
        for sh in self.sheets:
            texts.extend(f.text or "" for f in self.sheet_root(sh["name"]).iter(s("f")))
        dn = self.wb.find(s("definedNames"))
        if dn is not None:
            texts.extend(el.text or "" for el in dn.findall(s("definedName")))
        return any(_OUTSIDE.search(_STRING_LIT.sub('""', t)) for t in texts) or \
            any(rel.type.endswith("/externalLink") for rel in self.pkg.rels(self.wb_part))

    def _update_dimension(self, root) -> None:
        sd = self._sheet_data(root)
        max_r, max_c, min_r, min_c = 0, 0, None, None
        for row in sd.findall(s("row")):
            r = int(row.get("r"))
            for c in row.findall(s("c")):
                m = _CELLREF.match(c.get("r"))
                ci = col_index(m.group(1))
                max_r, max_c = max(max_r, r), max(max_c, ci)
                min_r = r if min_r is None else min(min_r, r)
                min_c = ci if min_c is None else min(min_c, ci)
        dim = root.find(s("dimension"))
        if dim is not None and max_r:
            dim.set("ref", f"{col_letter(min_c)}{min_r}:{col_letter(max_c)}{max_r}")
        # spans attributes are optional hints; remove to avoid inconsistencies after column changes
        for row in sd.findall(s("row")):
            if "spans" in row.attrib:
                del row.attrib["spans"]

    # ------------------------------------------------------------ defined names (working copy markers)
    def set_defined_name(self, name: str, sheet: str, ref: str) -> None:
        dn = self.wb.find(s("definedNames"))
        if dn is None:
            dn = etree.Element(s("definedNames"))
            _insert_after_last(self.wb, dn, _WB_BEFORE_DEFINEDNAMES)
        for el in dn.findall(s("definedName")):
            if el.get("name") == name:
                dn.remove(el)
        el = etree.SubElement(dn, s("definedName"))
        el.set("name", name)
        qs = "'" + sheet.replace("'", "''") + "'"
        r1, c1, r2, c2 = parse_range(ref)
        a = f"${col_letter(c1)}${r1 + 1}"
        b = f"${col_letter(c2)}${r2 + 1}"
        el.text = f"{qs}!{a}" + (f":{b}" if (r1, c1) != (r2, c2) else "")


# CT_Workbook is a sequence: a new child goes right after the last element that must precede
# it.  (calcPr used to land after pivotCaches and definedNames before functionGroups /
# externalReferences — orders Excel reports as needing repair.)
_WB_BEFORE_DEFINEDNAMES = ("fileVersion", "fileSharing", "workbookPr", "workbookProtection", "bookViews", "sheets",
                           "functionGroups", "externalReferences")
_WB_BEFORE_CALCPR = _WB_BEFORE_DEFINEDNAMES + ("definedNames",)


def _insert_after_last(parent, el, predecessors: tuple[str, ...]) -> None:
    wanted = {s(t) for t in predecessors}
    last = None
    for child in parent:
        if child.tag in wanted:
            last = child
    if last is not None:
        last.addnext(el)
    else:
        parent.insert(0, el)
