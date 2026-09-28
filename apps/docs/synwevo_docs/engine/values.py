"""Typed cell/field values.

Every value moving through the engine keeps its original type, a canonical
JSON-safe representation and the display text the user saw in the source.
Numbers are kept as strings in ``v`` so that long identifiers and money never
pass through a lossy float in JSON.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

EMPTY = "empty"
STR = "str"
INT = "int"
DEC = "dec"      # exact decimal written as text, e.g. "1234.50"
FLOAT = "float"  # binary float coming from a spreadsheet engine
DATE = "date"
DATETIME = "datetime"
TIME = "time"
BOOL = "bool"
ERROR = "error"

NUMERIC_TYPES = {INT, DEC, FLOAT}


@dataclass
class Value:
    t: str
    v: Any = None          # canonical: str for numbers/strings/dates, bool for BOOL
    text: str = ""         # display text as seen in the source
    fmt: str | None = None  # source number format, if any
    src: dict | None = field(default=None)  # provenance (file, sheet, cell, ...)
    style: dict | None = field(default=None)  # optional source char/cell style (only used when style copy is enabled)

    @property
    def is_empty(self) -> bool:
        return self.t == EMPTY or (self.t == STR and self.v == "")

    def to_json(self) -> dict:
        d = {"t": self.t, "v": self.v, "text": self.text}
        if self.fmt:
            d["fmt"] = self.fmt
        if self.src:
            d["src"] = self.src
        return d

    @staticmethod
    def from_json(d: dict | None) -> "Value":
        if not d:
            return Value(EMPTY, None, "")
        return Value(d.get("t", STR), d.get("v"), d.get("text", ""), d.get("fmt"), d.get("src"))

    def as_decimal(self) -> Decimal | None:
        if self.t == FLOAT:
            # the number as Excel sees it (15 significant digits), as the formula engine does:
            # 3*1.2 is 3.6 to a rounding or a comparison, not 3.5999999999999996
            try:
                f = float(self.v)
                return _finite(Decimal(str(self.v)) if f.is_integer() else Decimal(format(f, ".15g")))
            except (InvalidOperation, ValueError, TypeError):
                return None
        if self.t in NUMERIC_TYPES:
            try:
                return _finite(Decimal(str(self.v)))
            except InvalidOperation:
                return None
        if self.t == STR:
            s = str(self.v).strip().replace(",", "").replace("，", "")
            for sym in ("¥", "￥", "$", "€", "£"):
                s = s.replace(sym, "")
            try:
                return _finite(Decimal(s))
            except InvalidOperation:
                return None
        return None


def _finite(d: Decimal) -> Decimal | None:
    # "NaN", "Infinity" and "sNaN" parse as Decimals; as data they are text, not numbers:
    # summed they printed "NaN" into money fields, sorted they raised InvalidOperation
    return d if d.is_finite() else None


def empty() -> Value:
    return Value(EMPTY, None, "")


def text(s: str, src: dict | None = None) -> Value:
    if s is None:
        return Value(EMPTY, None, "", src=src)
    return Value(STR, s, s, src=src)


def exact_decimal(v: "Value") -> Decimal | None:
    """The whole double of a FLOAT value (for adding several up before one final rounding)."""
    try:
        return _finite(Decimal(str(v.v)))
    except (InvalidOperation, ValueError, TypeError):
        return None


def excel_decimal(x: float) -> Decimal:
    """A double as Excel sees it: integral values whole, others at 15 significant digits."""
    return Decimal(float_repr(x)) if x.is_integer() else Decimal(format(x, ".15g"))


def general_text(x: float) -> str:
    """A number as Excel's General format shows it: at most 15 significant digits. 3*1.2 is the
    double 3.5999999999999996, which Excel shows as 3.6 — and so must a document filled from it.
    Integral values keep every digit; other values are rounded like Excel's display, so
    999999999999999.9 shows as 1000000000000000."""
    if x != x or x in (float("inf"), float("-inf")) or x.is_integer():
        return float_repr(x)
    r = format(Decimal(format(x, ".15g")), "f")
    if "." in r:
        r = r.rstrip("0").rstrip(".")
    return r


def float_repr(x: float) -> str:
    """Shortest round-trip representation, without scientific notation for
    ordinary magnitudes and without a trailing '.0' for integral values."""
    if x != x or x in (float("inf"), float("-inf")):
        return str(x)
    if x.is_integer() and abs(x) < 1e16:
        return str(int(x))
    r = repr(x)
    if "e" in r or "E" in r:
        d = Decimal(r)
        r = format(d, "f")
        if "." in r:
            r = r.rstrip("0").rstrip(".")
    return r


def from_python(obj: Any, *, fmt: str | None = None, display: str | None = None, src: dict | None = None) -> Value:
    """Build a Value from a Python object produced by a spreadsheet reader."""
    if obj is None:
        return Value(EMPTY, None, "", fmt, src)
    if isinstance(obj, bool):
        return Value(BOOL, obj, display if display is not None else ("TRUE" if obj else "FALSE"), fmt, src)
    if isinstance(obj, int):
        return Value(INT, str(obj), display if display is not None else str(obj), fmt, src)
    if isinstance(obj, float):
        # the canonical value keeps the whole double (it is written back to spreadsheets as it is);
        # what a person sees is what Excel shows
        canon = float_repr(obj)
        t = INT if obj.is_integer() and abs(obj) < 1e16 else FLOAT
        return Value(t, canon, display if display is not None else general_text(obj), fmt, src)
    if isinstance(obj, Decimal):
        return Value(DEC, str(obj), display if display is not None else str(obj), fmt, src)
    if isinstance(obj, _dt.datetime):
        if obj.hour == 0 and obj.minute == 0 and obj.second == 0 and obj.microsecond == 0 and fmt and not _fmt_has_time(fmt):
            return Value(DATE, obj.date().isoformat(), display if display is not None else obj.date().isoformat(), fmt, src)
        return Value(DATETIME, obj.isoformat(), display if display is not None else obj.isoformat(sep=" "), fmt, src)
    if isinstance(obj, _dt.date):
        return Value(DATE, obj.isoformat(), display if display is not None else obj.isoformat(), fmt, src)
    if isinstance(obj, _dt.time):
        return Value(TIME, obj.isoformat(), display if display is not None else obj.isoformat(), fmt, src)
    if isinstance(obj, _dt.timedelta):
        secs = obj.total_seconds()
        s = display if display is not None else str(obj)
        return Value(TIME, str(secs), s, fmt, src)
    s = str(obj)
    return Value(STR, s, display if display is not None else s, fmt, src)


def _fmt_has_time(fmt: str) -> bool:
    f = fmt.lower()
    return "h" in f or "s" in f or "am/pm" in f


def to_python_date(v: Value) -> _dt.date | _dt.datetime | None:
    try:
        if v.t == DATE:
            return _dt.date.fromisoformat(v.v)
        if v.t == DATETIME:
            return _dt.datetime.fromisoformat(v.v)
    except (TypeError, ValueError):
        return None
    return None
