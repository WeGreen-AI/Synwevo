"""Local desensitization: make shareable copies of real files for regression tests.

Everything runs on this server.  Replacements are *consistent* within one run – the
same original value becomes the same pseudonym in every file – so relationships
(order numbers shared by detail rows, a customer's name in two documents) survive and
the copies still exercise the same mapping logic as the originals.

* Spreadsheets / CSV: per-column strategies (pseudonym, keep first/last characters,
  replace digits keeping length, scale numbers, shift dates, clear) plus the text rules.
* DOCX: text rules applied inside paragraphs, table cells, headers and footers; the
  replaced text keeps the formatting of the text it replaces.
* Text rules: built-in patterns (mobile / landline numbers, ID-card numbers, bank card
  numbers, e-mail addresses) and a word list (names, company names...).

Formats whose text cannot be rewritten faithfully here (PDF, images, slides) are
refused with the reason instead of being passed through unchanged.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
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
        self.patterns = [p for p in (patterns or list(PATTERNS)) if p in PATTERNS]
        self.map: dict[tuple[str, str], str] = {}
        self.counts: dict[str, int] = {}
        self.factor = round(self.rng.uniform(0.6, 1.4), 3)
        self.days = self.rng.randint(-200, 200) or 17

    def _count(self, rule: str):
        self.counts[rule] = self.counts.get(rule, 0) + 1

    def _digits(self, s: str) -> str:
        r = random.Random(self.seed + s)
        return "".join(str(r.randint(0, 9)) if ch.isdigit() else ch for ch in s)

    def pseudonym(self, kind: str, value: str) -> str:
        key = (kind, value)
        if key not in self.map:
            n = sum(1 for k in self.map if k[0] == kind) + 1
            self.map[key] = f"{kind}{n:04d}"
        return self.map[key]

    def spans(self, text: str) -> list[tuple[int, int, str]]:
        """Non-overlapping (start, end, replacement) for the rules, found on the ORIGINAL text
        (a replacement is never matched again).  Word list first, then patterns in order."""
        if not text:
            return []
        cands: list[tuple[int, int, int, str, str]] = []      # start, end, priority, kind, value
        for w in self.words:
            for m in re.finditer(re.escape(w), text):
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
            return self._digits(str(v)) if isinstance(v, str) else type(v)(self._digits(str(v))) if isinstance(v, int) else v
        if strategy == "scale" and isinstance(v, (int, float)) and not isinstance(v, bool):
            self._count(f"列「{header}」")
            nv = v * self.factor
            return round(nv) if isinstance(v, int) else round(nv, max(2, len(str(v).split(".")[-1]) if "." in str(v) else 2))
        if strategy == "date_shift" and isinstance(v, (dt.date, dt.datetime)):
            self._count(f"列「{header}」")
            return v + dt.timedelta(days=self.days)
        if isinstance(v, str):
            return self.replace_text(v)
        return v


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
        return _docx(data, sess), "docx"
    if fmt in ("txt", "md"):
        from .sources import text_content
        return sess.replace_text(text_content(data, fmt)).encode("utf-8"), fmt
    raise DesensitizeError("unsupported", f"{fmt.upper()} 暂不能在本工具中脱敏（PDF / 图片 / 幻灯片中的文字无法在不破坏版式的前提下可靠替换）。"
                                          "建议：在原软件中导出为 DOCX / XLSX 后再脱敏，或使用原软件的「编辑/涂黑」功能")


def _xlsx(data: bytes, sess: Session, columns: dict[str, str]) -> bytes:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data))
    for ws in wb.worksheets:
        header_row, heads = None, {}
        for r in range(1, min(ws.max_row, 30) + 1):
            vals = {c.column: str(c.value).strip() for c in ws[r] if c.value is not None and str(c.value).strip()}
            if any(h in columns for h in vals.values()):
                header_row, heads = r, {col: name for col, name in vals.items() if name in columns}
                break
        for row in ws.iter_rows():
            for c in row:
                if c.value is None or getattr(c, "data_type", None) == "f" or (isinstance(c.value, str) and c.value.startswith("=")):
                    continue
                if header_row and c.row > header_row and c.column in heads:
                    c.value = sess.column_value(columns[heads[c.column]], heads[c.column], c.value)
                elif isinstance(c.value, str):
                    c.value = sess.replace_text(c.value)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _csv(data: bytes, sess: Session, columns: dict[str, str]) -> bytes:
    import csv
    from .tabular import detect_encoding, detect_delimiter
    enc, _bom = detect_encoding(data)
    text = data.decode(enc, errors="replace").lstrip("\ufeff")
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


def _docx(data: bytes, sess: Session) -> bytes:
    from . import docx_core as dc
    from .ooxml import Package
    pkg = Package(data)
    for info in dc.list_parts(pkg):
        root = pkg.xml(info["part"])
        for p in list(root.iter(dc.w("p"))):
            text = dc.para_text(p)
            if not text:
                continue
            # each match separately, right to left: the new text keeps the formatting of what it replaces
            for a, b, rep in reversed(sess.spans(text)):
                try:
                    sdt = dc.wrap_range(p, a, b, "dw:__desens__", "脱敏")
                except dc.DocxError:
                    continue
                dc.fill_sdt(sdt, rep, False)
    return pkg.save()
