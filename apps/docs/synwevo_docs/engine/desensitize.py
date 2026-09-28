"""Local desensitization: make shareable copies of real files for regression tests.

Everything runs on this server.  Replacements are *consistent* within one run – the
same original value becomes the same pseudonym in every file – so relationships
(order numbers shared by detail rows, a customer's name in two documents) survive and
the copies can exercise the same mapping logic as the originals.

* Spreadsheets / CSV: per-column strategies (pseudonym, keep first/last characters,
  replace digits keeping length, scale numbers, shift dates, clear) plus the text rules,
  applied to text *and* numbers (a phone number typed into Excel is a number), cell
  comments, headers / footers, hyperlinks, sheet names, data-validation lists and the
  string literals inside formulas.  Workbook author fields are cleared; pivot caches
  (a full copy of the source data) are dropped.  The copy is re-saved by openpyxl:
  formulas lose their cached results (the reader recomputes them) and objects openpyxl
  does not keep (some charts, shapes) are not in the copy.
* DOCX: text rules applied to every paragraph of every part — body, headers, footers,
  footnotes, endnotes, comments, text boxes — including deleted text and field codes, to
  chart and diagram text, external link targets (``mailto:``), document variables and
  custom XML; revision / comment authors are pseudonymised and document properties
  scrubbed.  The replaced text keeps the formatting of the text it replaces.  Embedded
  spreadsheets and documents are desensitized too; embedded objects of other kinds are
  refused, never passed through.  Macro projects are removed.
* Text rules: built-in patterns (mobile / landline numbers, ID-card numbers, bank card
  numbers, e-mail addresses) and a word list (names, company names...).

A match is either replaced or the file is refused: nothing is counted as replaced that
is still in the copy.  Formats whose text cannot be rewritten faithfully here (PDF,
images, slides) are refused with the reason instead of being passed through unchanged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import posixpath
import random
import re

PATTERNS = {
    "mobile": ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    "idcard": ("身份证号", re.compile(r"(?<![\dA-Za-z])\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dA-Za-z])")),
    "bankcard": ("银行卡号", re.compile(r"(?<![\d-])\d{4}(?:[ -]?\d{4}){3}(?:[ -]?\d{1,3})?(?![\d-])")),
    "email": ("电子邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    "landline": ("固定电话", re.compile(r"(?<!\d)0\d{2,3}-\d{7,8}(?!\d)")),
}
STRATEGIES = ("pseudonym", "mask", "digits", "scale", "date_shift", "clear", "keep")


class DesensitizeError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Session:
    """Consistent replacements for one desensitization run (shared by all its files)."""

    def __init__(self, seed: str | None = None, words: list[str] | None = None, patterns: list[str] | None = None):
        self.seed = seed or hashlib.sha256(os.urandom(32)).hexdigest()     # secret, never stored
        self.rng = random.Random(self.seed)
        self.words = sorted({w.strip() for w in (words or []) if w and w.strip()}, key=len, reverse=True)
        # one alternation, longest word first: the leftmost-longest match at each position,
        # found in a single pass instead of one full scan of the text per word
        self._words_rx = re.compile("|".join(re.escape(w) for w in self.words)) if self.words else None
        self.patterns = [p for p in (patterns or list(PATTERNS)) if p in PATTERNS]
        self.map: dict[tuple[str, str], str] = {}
        self._per_kind: dict[str, int] = {}
        self.counts: dict[str, int] = {}
        # A factor within 5 % of 1 — exactly 1.000 once in about 800 sessions before CI-84 — copied
        # the real amounts into the desensitized file. Draw the distance from 1, then the side.
        step = self.rng.uniform(0.05, 0.4)
        self.factor = round(1 + step if self.rng.random() < 0.5 else 1 - step, 3)
        self.days = self.rng.randint(-200, 200) or 17

    def _count(self, rule: str):
        self.counts[rule] = self.counts.get(rule, 0) + 1

    def _digits(self, s: str) -> str:
        r = random.Random(self.seed + s)
        digits = [str(r.randint(0, 9)) if ch.isdigit() else ch for ch in s]
        # A leading zero disappears when a numeric cell is written back. Apply the same
        # correction to text too, so IDs shared by text and numeric sources stay identical.
        first = next((i for i, ch in enumerate(s) if ch.isdigit()), None)
        if first is not None and digits[first] == "0":
            digits[first] = str(r.randint(1, 9))
        return "".join(digits)

    def pseudonym(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.map:
            n = self._per_kind.get(kind, 0) + 1          # a counter per kind: counting the map was quadratic
            self._per_kind[kind] = n
            self.map[key] = f"{kind}{n:04d}"
        return self.map[key]

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        """Non-overlapping (start, end, replacement) for the rules, found on the ORIGINAL text
        (a replacement is never matched again).  Word list first, then patterns in order."""
        if not text:
            return []
        cands: list[tuple[int, int, int, str, str]] = []      # start, end, priority, kind, value
        if self._words_rx is not None:
            for m in self._words_rx.finditer(text):
                cands.append((m.start(), m.end(), 0, "word", m.group(0)))
        for prio, p in enumerate(self.patterns, start=1):
            for m in PATTERNS[p][1].finditer(text):
                cands.append((m.start(), m.end(), prio, p, m.group(0)))
        cands.sort(key=lambda c: (c[2], c[0], -(c[1] - c[0])))
        taken: list[tuple[int, int, int, str, str]] = []
        for c in cands:
            if all(c[1] <= t[0] or c[0] >= t[1] for t in taken):
                taken.append(c)
        out = []
        for a, b, _prio, kind, v in sorted(taken):
            if kind == "word":
                self._count("词表")
                out.append((a, b, self.pseudonym("脱敏词", v)))
                continue
            self._count(PATTERNS[kind][0])
            if kind == "email":
                rep = self.pseudonym("user", v).lower() + "@example.com"
            elif kind == "idcard":
                d = self._digits(v[:-1])
                rep = d[:6] + v[6:14] + d[14:17] + "X"          # birth date kept: date logic stays testable
            else:
                rep = self._digits(v)
            out.append((a, b, rep))
        return out

    def replace_text(self, text: str) -> str:
        out, last = [], 0
        for a, b, rep in self.spans(text):
            out.append(text[last:a])
            out.append(rep)
            last = b
        out.append(text[last:])
        return "".join(out)

    # ---------------------------------------------------------- column strategies
    def column_value(self, strategy: str, header: str, v):
        if v is None or v == "":
            return v
        if strategy == "keep":
            return v
        if strategy == "clear":
            self._count(f"列「{header}」")
            return None
        if strategy == "pseudonym":
            self._count(f"列「{header}」")
            return self.pseudonym(header or "值", str(v))
        if strategy == "mask":
            s = str(v)
            self._count(f"列「{header}」")
            return s if len(s) <= 1 else (s[0] + "*" * max(1, len(s) - 2) + (s[-1] if len(s) > 2 else ""))
        if strategy == "digits":
            self._count(f"列「{header}」")
            if isinstance(v, bool):
                return v
            if isinstance(v, int):
                return int(self._digits(str(v)))
            if isinstance(v, float):
                # every digit of the written number, same type; a float used to come back unchanged
                return float(self._digits(_num_text(v)))
            return self._digits(str(v))
        if strategy == "scale" and isinstance(v, (int, float)) and not isinstance(v, bool):
            self._count(f"列「{header}」")
            places = 0 if isinstance(v, int) else max(2, len(str(v).split(".")[-1]) if "." in str(v) else 2)
            out = round(v * self.factor, places) if places else round(v * self.factor)
            if out == v and v != 0:
                # a small value rounds back to itself: move it one unit in the factor's direction,
                # so no non-zero amount survives a "scaled" copy unchanged (CI-84)
                away = (1 if self.factor > 1 else -1) * (1 if v > 0 else -1)      # grow or shrink the magnitude
                out = v + away if not places else round(v + away * 10 ** -places, places)
            return out
        if strategy == "date_shift" and isinstance(v, (dt.date, dt.datetime)):
            self._count(f"列「{header}」")
            return v + dt.timedelta(days=self.days)
        return self.replace_value(v)

    def replace_value(self, v):
        """Text rules on a cell value of any type.  Numbers are checked as the text they are
        written as (a mobile number stored as a number is still a mobile number) and come back
        as numbers of the same type when something was replaced."""
        if isinstance(v, str):
            return self.replace_text(v)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return v
        text = _num_text(v)
        new = self.replace_text(text)
        if new == text:
            return v
        try:
            return int(new) if isinstance(v, int) else float(new)
        except ValueError:
            return new


def _num_text(v) -> str:
    if isinstance(v, float):
        if v.is_integer() and abs(v) < 1e16:
            return str(int(v))
        return repr(v)
    return str(v)


# ------------------------------------------------------------------ formats
def desensitize(data: bytes, filename: str, sess: Session, columns: dict[str, str] | None = None) -> tuple[bytes, str]:
    from .detect import detect
    info = detect(data, filename)
    fmt = info["format"]
    columns = {k: v for k, v in (columns or {}).items() if v in STRATEGIES}
    if fmt in ("xlsx", "xlsm"):
        return _xlsx(data, sess, columns), "xlsx"
    if fmt in ("csv", "tsv"):
        return _csv(data, sess, columns), fmt
    if fmt in ("docx", "docm"):
        # the copy is a plain .docx: a macro-enabled main part (and the VBA project, whose
        # source can hold the same data) inside a .docx is a file Word refuses to open
        return _docx(data, sess), "docx"
    if fmt in ("txt", "md"):
        from .sources import text_content
        return sess.replace_text(text_content(data, fmt)).encode("utf-8"), fmt
    raise DesensitizeError("unsupported", f"{fmt.upper()} 暂不能在本工具中脱敏（PDF / 图片 / 幻灯片中的文字无法在不破坏版式的前提下可靠替换）。"
                                          "建议：在原软件中导出为 DOCX / XLSX 后再脱敏，或使用原软件的「编辑/涂黑」功能")


# ------------------------------------------------------------------ XLSX
_FORMULA_STRING = re.compile(r'"(?:[^"]|"")*"')


def _formula_literals(sess: Session, formula: str) -> str:
    """The text rules applied to the string literals of a formula only (never to references)."""
    def one(m):
        inner = m.group(0)[1:-1].replace('""', '"')
        return '"' + sess.replace_text(inner).replace('"', '""') + '"'
    return _FORMULA_STRING.sub(one, formula)


def _xlsx(data: bytes, sess: Session, columns: dict[str, str]) -> bytes:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))           # keep_vba=False: the copy has no VBA project
    # sheet names first: formulas and defined names that point at a renamed sheet follow it
    renames = {}
    for ws in wb.worksheets:
        new = sess.replace_text(ws.title)[:31]
        if new != ws.title:
            renames[ws.title] = new
    for ws in wb.worksheets:
        if ws.title in renames:
            ws.title = renames[ws.title]
    for ws in wb.worksheets:
        header_row, heads = None, {}
        for r in range(1, min(ws.max_row, 30) + 1):
            vals = {c.column: str(c.value).strip() for c in ws[r] if c.value is not None and str(c.value).strip()}
            if any(h in columns for h in vals.values()):
                header_row, heads = r, {col: name for col, name in vals.items() if name in columns}
                break
        for row in ws.iter_rows():
            for c in row:
                if getattr(c, "comment", None) is not None:
                    c.comment.text = sess.replace_text(c.comment.text or "")
                    if c.comment.author:
                        c.comment.author = sess.pseudonym("作者", c.comment.author)
                if getattr(c, "hyperlink", None) is not None:
                    hl = c.hyperlink
                    for attr in ("target", "display", "tooltip", "location"):
                        if getattr(hl, attr, None):
                            setattr(hl, attr, sess.replace_text(getattr(hl, attr)))
                if c.value is None:
                    continue
                if getattr(c, "data_type", None) == "f" or (isinstance(c.value, str) and c.value.startswith("=")):
                    if isinstance(c.value, str):
                        c.value = _rename_refs(_formula_literals(sess, c.value), renames)
                    continue
                if header_row and c.row > header_row and c.column in heads:
                    c.value = sess.column_value(columns[heads[c.column]], heads[c.column], c.value)
                else:
                    c.value = sess.replace_value(c.value)
        for hf in (ws.oddHeader, ws.oddFooter, ws.evenHeader, ws.evenFooter, ws.firstHeader, ws.firstFooter):
            for part in (hf.left, hf.center, hf.right):
                if part.text:
                    part.text = sess.replace_text(part.text)
        for dv in getattr(ws.data_validations, "dataValidation", []) or []:
            for attr in ("formula1", "formula2", "prompt", "error", "promptTitle", "errorTitle"):
                val = getattr(dv, attr, None)
                if val:
                    setattr(dv, attr, _formula_literals(sess, val) if attr.startswith("formula") else sess.replace_text(val))
        if getattr(ws, "_pivots", None):
            # a pivot cache is a full copy of the source rows: it cannot be partly scrubbed
            sess._count("数据透视表（已移除）")
            ws._pivots = []
    for name, dn in list(_defined_names(wb)):
        if dn.attr_text:
            dn.attr_text = _rename_refs(_formula_literals(sess, dn.attr_text), renames)
    props = wb.properties
    for attr in ("creator", "lastModifiedBy"):
        if getattr(props, attr, None):
            setattr(props, attr, "")
    for attr in ("title", "subject", "description", "keywords", "category"):
        if getattr(props, attr, None):
            setattr(props, attr, sess.replace_text(getattr(props, attr)))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _defined_names(wb):
    dn = getattr(wb, "defined_names", None)
    if dn is None:
        return []
    items = dn.items() if hasattr(dn, "items") else [(d.name, d) for d in dn.definedName]
    out = list(items)
    for ws in wb.worksheets:
        local = getattr(ws, "defined_names", None)
        if local is not None and hasattr(local, "items"):
            out.extend(local.items())
    return out


def _rename_refs(formula: str, renames: dict[str, str]) -> str:
    for old, new in renames.items():
        q_old, q_new = "'" + old.replace("'", "''") + "'!", "'" + new.replace("'", "''") + "'!"
        formula = formula.replace(q_old, q_new)
        if re.fullmatch(r"[A-Za-z_\u0080-￿][\w.\u0080-￿]*", old):
            formula = re.sub(r"(?<![\w.'])" + re.escape(old) + r"!", lambda m: q_new, formula)
    return formula


def _csv(data: bytes, sess: Session, columns: dict[str, str]) -> bytes:
    import csv
    from .tabular import detect_encoding, detect_delimiter
    enc, _bom = detect_encoding(data)
    text = data.decode(enc, errors="replace").lstrip("﻿")
    delim = detect_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if rows:
        heads = [h.strip() for h in rows[0]]
        for r in rows[1:]:
            for i, v in enumerate(r):
                h = heads[i] if i < len(heads) else ""
                r[i] = sess.column_value(columns[h], h, v) if h in columns else sess.replace_text(v)
                r[i] = "" if r[i] is None else str(r[i])
        rows[0] = [sess.replace_text(h) if h not in columns else h for h in rows[0]]
    out = io.StringIO()
    csv.writer(out, delimiter=delim, lineterminator="\r\n").writerows(rows)
    s = out.getvalue()
    return ("﻿" + s).encode("utf-8") if enc.lower().replace("-", "").startswith("utf8") else s.encode(enc, errors="replace")


# ------------------------------------------------------------------ DOCX
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

# text-bearing elements of a paragraph, per stream: the visible text (inserted text is
# visible), deleted text and field codes are matched separately so they never run together
_STREAMS = (
    ({"{%s}t" % _W}, True),
    ({"{%s}delText" % _W}, True),
    ({"{%s}instrText" % _W}, False),
)
_BREAKS = {"{%s}%s" % (_W, t) for t in ("tab", "br", "cr")} | {"{%s}br" % _A}
_NESTED = {"{%s}txbxContent" % _W, "{%s}p" % _W, "{%s}p" % _A}
_AUTHOR_ATTRS = ("{%s}author" % _W, "{%s}author" % _W15, "{%s}author" % _W14)
_EMBED_OK = (".docx", ".docm", ".xlsx", ".xlsm")


def _replace_nodes(sess: Session, nodes: list, separators_between: list[bool] | None = None) -> None:
    """Apply the text rules to text spread over ``nodes`` (elements whose ``.text`` holds the
    characters, in reading order).  A replacement is written into the node where the match
    starts — so it keeps that run's formatting — and the rest of the match is removed from
    the nodes it continues into, whatever lies between them (other runs, hyperlinks, content
    controls, revision marks).  Nothing is skipped: a match is always replaced."""
    if not nodes:
        return
    texts = [n.text or "" for n in nodes]
    starts, pos, joined = [], 0, []
    for i, t in enumerate(texts):
        if i and separators_between and separators_between[i]:
            joined.append("\n")
            pos += 1
        starts.append(pos)
        joined.append(t)
        pos += len(t)
    full = "".join(joined)
    spans = sess.spans(full)
    if not spans:
        return
    for a, b, rep in reversed(spans):
        first = True
        for i, t in enumerate(texts):
            s0, s1 = starts[i], starts[i] + len(t)
            if s1 <= a or s0 >= b or s0 == s1:
                continue
            lo, hi = max(a, s0) - s0, min(b, s1) - s0
            if first:
                texts[i] = t[:lo] + rep + t[hi:]
                first = False
                # later spans lie to the left: offsets of this node's start stay valid
            else:
                texts[i] = t[:lo] + t[hi:]
    for n, t in zip(nodes, texts):
        n.text = t
        if n.tag.endswith("}t") or n.tag.endswith("}delText") or n.tag.endswith("}instrText"):
            if t != t.strip() or "  " in t:
                n.set(_XML_SPACE, "preserve")


def _paragraph_streams(p):
    """{stream index: ([nodes], [separator before node?])} of a w:p / a:p, without the
    paragraphs nested in it (text boxes are paragraphs of their own)."""
    out: dict[int, tuple[list, list]] = {}
    sep = [False] * len(_STREAMS)

    def walk(el):
        for child in el:
            tag = child.tag
            if not isinstance(tag, str):
                continue
            if tag in _NESTED:
                continue
            if tag in _BREAKS:
                sep[:] = [True] * len(_STREAMS)
                continue
            hit = False
            for k, (tags, _) in enumerate(_STREAMS):
                if tag in tags:
                    nodes, seps = out.setdefault(k, ([], []))
                    nodes.append(child)
                    seps.append(sep[k])
                    sep[k] = False
                    hit = True
                    break
            if tag == "{%s}t" % _A:
                nodes, seps = out.setdefault(0, ([], []))
                nodes.append(child)
                seps.append(sep[0])
                sep[0] = False
                hit = True
            if not hit:
                walk(child)
    walk(p)
    return out


def _scrub_wordml(root, sess: Session) -> None:
    for p in list(root.iter("{%s}p" % _W, "{%s}p" % _A)):
        for nodes, seps in _paragraph_streams(p).values():
            _replace_nodes(sess, nodes, seps)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in _AUTHOR_ATTRS:
            v = el.get(attr)
            if v:
                el.set(attr, sess.pseudonym("作者", v))
        if el.get("{%s}initials" % _W):
            el.set("{%s}initials" % _W, "")
        instr = el.get("{%s}instr" % _W)
        if instr:
            el.set("{%s}instr" % _W, sess.replace_text(instr))
        if el.tag == "{%s}docVar" % _W and el.get("{%s}val" % _W):
            el.set("{%s}val" % _W, sess.replace_text(el.get("{%s}val" % _W)))
        if el.tag in ("{%s}v" % _C, "{%s}f" % _C) and el.text:
            el.text = sess.replace_text(el.text)
        if el.tag.endswith("}presenceInfo") and el.get("{%s}userId" % _W15):
            el.set("{%s}userId" % _W15, sess.pseudonym("用户", el.get("{%s}userId" % _W15)))


def _scrub_all_text(root, sess: Session) -> None:
    """Custom XML data and custom properties: every text node and attribute value."""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.text and el.text.strip():
            el.text = sess.replace_text(el.text)
        for k, v in list(el.attrib.items()):
            if v and not k.startswith("{http://www.w3.org/2000/xmlns/}"):
                el.set(k, sess.replace_text(v))


def _scrub_props(name: str, root, sess: Session) -> None:
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        local = el.tag.rsplit("}", 1)[-1]
        if name.endswith("core.xml") and local in ("creator", "lastModifiedBy"):
            el.text = ""
        elif name.endswith("app.xml") and local in ("Company", "Manager"):
            el.text = ""
        elif el.text and el.text.strip() and len(el) == 0:
            el.text = sess.replace_text(el.text)


def _docx(data: bytes, sess: Session) -> bytes:
    from .ooxml import Package, strip_vba
    pkg = Package(strip_vba(data))
    for name in list(pkg.names):
        low = name.lower()
        if low.endswith(".rels"):
            root = pkg.xml(name)
            for rel in root.iter("{%s}Relationship" % _REL_NS):
                if rel.get("TargetMode") == "External" and rel.get("Target"):
                    rel.set("Target", sess.replace_text(rel.get("Target")))
            continue
        if low.startswith("word/embeddings/") or "/embeddings/" in low:
            if low.endswith((".docx", ".docm")):
                pkg.write(name, _docx(pkg.read(name), sess))
            elif low.endswith((".xlsx", ".xlsm")):
                pkg.write(name, _xlsx(pkg.read(name), sess, {}))
            else:
                raise DesensitizeError("embedded_object", f"文档包含无法脱敏的嵌入对象（{posixpath.basename(name)}）。"
                                                          "请在原软件中删除该对象，或把它转换为图片后再脱敏")
            continue
        if not low.endswith(".xml") or name == "[Content_Types].xml":
            continue
        if low.startswith("docprops/"):
            root = pkg.xml(name)
            if low.endswith("custom.xml"):
                _scrub_all_text(root, sess)
            else:
                _scrub_props(low, root, sess)
        elif low.startswith("customxml/") and not posixpath.basename(low).startswith("itemprops"):
            _scrub_all_text(pkg.xml(name), sess)
        else:
            _scrub_wordml(pkg.xml(name), sess)
    return pkg.save()
