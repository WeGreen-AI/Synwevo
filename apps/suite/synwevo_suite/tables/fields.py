"""Field types of Synwevo Tables: what each type accepts, how a value is stored, how it is shown,
and how a value moves from one type to another.

A cell's stored form is the same whatever wrote it (the grid, the operations API, an import, a flow
or the Agent), because every write goes through :func:`normalize` (strict, for API input) or
:func:`parse_text` (lenient, for text from a file or a type change):

=============  =====================================================================
type           stored value
=============  =====================================================================
text           str, one line, ≤ 1 000 characters, kept exactly as typed
long_text      str, ≤ 20 000 characters
number         str, an exact decimal with exactly ``decimals`` places ("12.50")
money          {"amount": "12.50", "currency": "CNY"} — the field's currency and places
date           "YYYY-MM-DD"
datetime       "YYYY-MM-DDTHH:MM:SS+HH:MM" — the offset it was given is kept
boolean        true / false
single_select  a choice id ("opt_…"); labels can be renamed without touching records
multi_select   [choice id, …]
email/url/…    str (url: http or https only — a stored link is never ``javascript:``)
link           [record id, …] of the linked table (one at most unless ``multiple``)
formula        computed (see `formula.py`), stored so it can be filtered and sorted
created_* …    not stored in cells: the record's own columns
=============  =====================================================================

Empty is ``None`` (absent from the cells document). Nothing here touches the database.
"""
from __future__ import annotations

import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any
from urllib.parse import urlsplit

FIELD_TYPES = ("text", "long_text", "number", "money", "date", "datetime", "boolean", "single_select",
               "multi_select", "email", "url", "phone", "link", "formula", "created_time", "updated_time",
               "created_by", "updated_by")
AUTO_TYPES = frozenset({"created_time", "updated_time", "created_by", "updated_by"})
COMPUTED = AUTO_TYPES | {"formula"}
TEXTLIKE = frozenset({"text", "long_text", "email", "url", "phone"})
SELECTS = frozenset({"single_select", "multi_select"})
#: Types a field may be converted between (a relation, a formula and the automatic fields are
#: replaced by a new field instead: their values are not the person's input).
CONVERTIBLE = frozenset({"text", "long_text", "number", "money", "date", "datetime", "boolean", "single_select",
                         "multi_select", "email", "url", "phone"})
UNIQUE_OK = frozenset({"text", "email", "url", "phone", "number", "date", "datetime", "single_select"})
DEFAULT_OK = frozenset({"text", "long_text", "email", "url", "phone", "number", "money", "date", "boolean",
                        "single_select", "multi_select"})
REQUIRED_OK = CONVERTIBLE | {"link"}

ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
READ_ROLES = ("viewer", "editor", "admin")

TEXT_MAX = 1000
LONG_TEXT_MAX = 20000
MAX_CHOICES = 200
MAX_LINKS = 500
MAX_INT_DIGITS = 15
FIELD_ID = re.compile(r"^fld_[a-f0-9]{12}$")
RECORD_ID = re.compile(r"^rec_[a-f0-9]{12}$")
TABLE_ID = re.compile(r"^tbl_[a-f0-9]{12}$")
CHOICE_ID = re.compile(r"^opt_[a-f0-9]{8}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}\.[^@\s.]{2,63}$")
PHONE = re.compile(r"^\+?[0-9][0-9 ()\-.]{2,30}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
DECIMAL_TEXT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$")
DATE_TEXT = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATETIME_TEXT = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,9})?)?\s*(Z|[+-]\d{2}:?\d{2})?$", re.I)
SPLIT = re.compile(r"\s*[,;、|，；\n]\s*")
TRUE_WORDS = {"true", "yes", "y", "1", "是", "对", "對", "√", "✓", "✔", "on", "t"}
FALSE_WORDS = {"false", "no", "n", "0", "否", "错", "錯", "×", "✗", "✘", "off", "f"}
CURRENCY_SYMBOLS = {"¥": "CNY", "￥": "CNY", "$": "USD", "€": "EUR", "£": "GBP"}


class CellError(Exception):
    """One value that does not fit its field: a stable code and its parameters."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


def new_choice_id() -> str:
    return "opt_" + secrets.token_hex(4)


def readable(role: str | None, read_role: str) -> bool:
    return role is not None and ROLE_RANK.get(role, -1) >= ROLE_RANK.get(read_role, 99)


def max_role(*roles: str) -> str:
    return max(roles, key=lambda r: ROLE_RANK[r]) if roles else "viewer"


def is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


# ============================================================================ configuration
def _type_config(type_: str, raw: dict | None, *, previous: dict | None = None) -> dict:
    """A type's configuration, validated and normalised (formula and link details are checked
    against the table by the service). Raises :class:`CellError` with a config code."""
    raw = dict(raw or {})
    if type_ == "number":
        return {"decimals": _int_opt(raw.get("decimals", 0), 0, 10, "decimals")}
    if type_ == "money":
        cur = str(raw.get("currency") or "CNY").upper()
        if not CURRENCY.match(cur):
            raise CellError("bad_currency", currency=cur)
        return {"currency": cur, "decimals": _int_opt(raw.get("decimals", 2), 0, 4, "decimals")}
    if type_ in SELECTS:
        choices = raw.get("choices") if "choices" in raw else (previous or {}).get("choices", [])
        if not isinstance(choices, list) or len(choices) > MAX_CHOICES:
            raise CellError("too_many_choices", limit=MAX_CHOICES)
        out, seen_ids, seen_labels = [], set(), set()
        for c in choices:
            if isinstance(c, str):
                c = {"label": c}
            if not isinstance(c, dict):
                raise CellError("bad_choice")
            label = str(c.get("label") or "").strip()
            if not 1 <= len(label) <= 100:
                raise CellError("bad_choice_label")
            cid = c.get("id") or new_choice_id()
            if not isinstance(cid, str) or not CHOICE_ID.match(cid) or cid in seen_ids:
                raise CellError("bad_choice_id", id=str(cid)[:40])
            if label.casefold() in seen_labels:
                raise CellError("duplicate_choice_label", label=label)
            seen_ids.add(cid)
            seen_labels.add(label.casefold())
            out.append({"id": cid, "label": label})
        return {"choices": out}
    if type_ == "link":
        tid = raw.get("table_id") or (previous or {}).get("table_id")
        if not isinstance(tid, str) or not TABLE_ID.match(tid):
            raise CellError("link_table_required")
        return {"table_id": tid, "multiple": bool(raw.get("multiple", (previous or {}).get("multiple", False)))}
    if type_ == "formula":
        expr = raw.get("expression")
        if not isinstance(expr, str) or not expr.strip():
            raise CellError("formula_required")
        return {"expression": expr.strip(), "decimals": _int_opt(raw.get("decimals", 2), 0, 10, "decimals")}
    return {}


def clean_config(type_: str, raw: dict | None, *, previous: dict | None = None) -> dict:
    raw = dict(raw or {})
    allowed = {"number": {"decimals", "min", "max"}, "money": {"currency", "decimals", "min", "max"},
               "date": {"min", "max"}, "text": {"max_length"}, "long_text": {"max_length"},
               "formula": {"expression", "decimals"}, "link": {"table_id", "multiple"},
               "single_select": {"choices"}, "multi_select": {"choices"}}.get(type_, set())
    if set(raw) - allowed:
        raise CellError("unknown_option")
    if "multiple" in raw and not isinstance(raw["multiple"], bool):
        raise CellError("bad_option", option="multiple")
    cfg = _type_config(type_, raw, previous=previous)
    for key in ("min", "max"):
        if raw.get(key) is not None:
            cfg[key] = _date(raw[key]) if type_ == "date" and isinstance(raw[key], str) else _decimal(raw[key], cfg["decimals"]) if type_ in ("number", "money") else None
            if cfg[key] is None:
                raise CellError("bad_option", option=key)
    if "min" in cfg and "max" in cfg:
        lo, hi = (Decimal(cfg["min"]), Decimal(cfg["max"])) if type_ in ("number", "money") else (cfg["min"], cfg["max"])
        if lo > hi:
            raise CellError("invalid_range")
    if raw.get("max_length") is not None:
        cfg["max_length"] = _int_opt(raw["max_length"], 1, LONG_TEXT_MAX if type_ == "long_text" else TEXT_MAX, "max_length")
    return cfg


def normalize(field: dict, value: Any) -> Any:
    value = _normalize(field, value)
    if value is None:
        return None
    cfg, typ = field["config"], field["type"]
    if "max_length" in cfg and len(value) > cfg["max_length"]:
        raise CellError("too_long", max=cfg["max_length"])
    if typ in ("number", "money", "date"):
        actual = Decimal(value["amount"] if typ == "money" else value) if typ != "date" else value
        for key in ("min", "max"):
            if key in cfg:
                limit = Decimal(cfg[key]) if typ != "date" else cfg[key]
                if (key == "min" and actual < limit) or (key == "max" and actual > limit):
                    raise CellError("out_of_range", bound=key)
    return value


def _int_opt(v, lo: int, hi: int, name: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise CellError("bad_option", option=name, min=lo, max=hi)
    return v


def choice_index(config: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(id → label, casefolded label → id)."""
    by_id = {c["id"]: c["label"] for c in config.get("choices", [])}
    return by_id, {label.casefold(): cid for cid, label in by_id.items()}


# ============================================================================ strict input
def _normalize(field: dict, value: Any) -> Any:
    """A value from the operations API, as it will be stored. Raises :class:`CellError`."""
    t = field["type"]
    cfg = field["config"]
    if t in COMPUTED:
        raise CellError("field_read_only")
    if t == "boolean":
        if value is None:
            return None
        if not isinstance(value, bool):
            raise CellError("not_boolean")
        return value
    if is_empty(value):
        return None
    if t in ("text", "long_text", "email", "url", "phone"):
        if not isinstance(value, str):
            raise CellError("not_text")
        return _text(t, value)
    if t == "number":
        return _decimal(value, cfg["decimals"])
    if t == "money":
        amount, cur = value, None
        if isinstance(value, dict):
            if set(value) - {"amount", "currency"}:
                raise CellError("bad_money")
            amount, cur = value.get("amount"), value.get("currency")
        if cur is not None and cur != cfg["currency"]:
            raise CellError("currency_mismatch", currency=str(cur)[:10], expected=cfg["currency"])
        if is_empty(amount):
            return None
        return {"amount": _decimal(amount, cfg["decimals"]), "currency": cfg["currency"]}
    if t == "date":
        if not isinstance(value, str):
            raise CellError("bad_date")
        return _date(value)
    if t == "datetime":
        if not isinstance(value, str):
            raise CellError("bad_datetime")
        return _datetime(value, None)
    if t == "single_select":
        if not isinstance(value, str):
            raise CellError("unknown_choice")
        return _choice(cfg, value)
    if t == "multi_select":
        items = value if isinstance(value, list) else [value]
        if len(items) > MAX_CHOICES or not all(isinstance(x, str) for x in items):
            raise CellError("bad_choices")
        out: list[str] = []
        for x in items:
            cid = _choice(cfg, x)
            if cid not in out:
                out.append(cid)
        return out or None
    if t == "link":
        items = value if isinstance(value, list) else [value]
        if not all(isinstance(x, str) and RECORD_ID.match(x) for x in items):
            raise CellError("bad_link")
        out = list(dict.fromkeys(items))
        if not cfg.get("multiple") and len(out) > 1:
            raise CellError("single_link_only")
        if len(out) > MAX_LINKS:
            raise CellError("too_many_links", limit=MAX_LINKS)
        return out or None
    raise CellError("unknown_type")


def _text(t: str, value: str) -> str:
    if t == "text":
        if "\n" in value or "\r" in value:
            raise CellError("single_line")
        if len(value) > TEXT_MAX:
            raise CellError("too_long", max=TEXT_MAX)
        return value
    if t == "long_text":
        if len(value) > LONG_TEXT_MAX:
            raise CellError("too_long", max=LONG_TEXT_MAX)
        return value
    v = value.strip()
    if t == "email":
        if len(v) > 254 or not EMAIL.match(v):
            raise CellError("bad_email")
        return v
    if t == "url":
        if len(v) > 2000:
            raise CellError("too_long", max=2000)
        try:
            parts = urlsplit(v)
        except ValueError:
            raise CellError("bad_url") from None
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc or any(ch.isspace() for ch in v):
            raise CellError("bad_url")
        return v
    if t == "phone":
        if not PHONE.match(v) or sum(ch.isdigit() for ch in v) < 3:
            raise CellError("bad_phone")
        return v
    raise CellError("unknown_type")


def _decimal(value: Any, places: int) -> str:
    if isinstance(value, bool):
        raise CellError("not_number")
    if isinstance(value, int):
        d = Decimal(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CellError("not_number")
        d = Decimal(repr(value))
    elif isinstance(value, str):
        s = value.strip()
        if not DECIMAL_TEXT.match(s):
            raise CellError("not_number")
        d = Decimal(s)
    else:
        raise CellError("not_number")
    return quantize(d, places, strict=True)


def quantize(d: Decimal, places: int, *, strict: bool) -> str:
    """Exactly ``places`` decimals. ``strict``: more places than declared is an error, never a
    silent rounding (conversions round and say so)."""
    if not d.is_finite() or d.adjusted() >= MAX_INT_DIGITS:
        raise CellError("number_too_large", max_digits=MAX_INT_DIGITS)
    try:
        with localcontext() as ctx:
            ctx.prec = MAX_INT_DIGITS + 12
            q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise CellError("number_too_large", max_digits=MAX_INT_DIGITS) from None
    if strict and d != q:
        raise CellError("too_many_decimals", max=places)
    if q.adjusted() >= MAX_INT_DIGITS:
        raise CellError("number_too_large", max_digits=MAX_INT_DIGITS)
    if q == 0:
        q = abs(q)                                   # no "-0.00"
    return f"{q:f}"


def _date(value: str) -> str:
    m = DATE_TEXT.match(value.strip())
    if not m:
        raise CellError("bad_date")
    try:
        return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError:
        raise CellError("bad_date") from None


def _datetime(value: str, default_offset: str | None) -> str:
    if re.search(r":\d{2}\.\d*[1-9]", value):
        raise CellError("datetime_subseconds_not_supported")
    m = DATETIME_TEXT.match(value.strip())
    if not m:
        raise CellError("bad_datetime")
    y, mo, d, h, mi, s, off = m.groups()
    off = off or default_offset
    if not off:
        raise CellError("datetime_offset_required")
    tz = parse_offset(off)
    if tz is None:
        raise CellError("bad_datetime")
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0), tzinfo=tz)
    except ValueError:
        raise CellError("bad_datetime") from None
    return dt.isoformat(timespec="seconds")


def parse_offset(off: str) -> timezone | None:
    if off.upper() == "Z":
        return timezone.utc
    m = re.match(r"^([+-])(\d{2}):?(\d{2})$", off)
    if not m:
        return None
    hours, minutes = int(m[2]), int(m[3])
    if hours > 14 or minutes >= 60 or (hours == 14 and minutes):
        return None
    delta = timedelta(hours=hours, minutes=minutes)
    return timezone(delta if m[1] == "+" else -delta)


def _choice(cfg: dict, value: str) -> str:
    by_id, by_label = choice_index(cfg)
    if value in by_id:
        return value
    cid = by_label.get(value.strip().casefold())
    if cid:
        return cid
    raise CellError("unknown_choice", value=value[:100])


# ============================================================================ lenient text input
def parse_text(field: dict, raw: Any, *, utc_offset: str | None = None) -> Any:
    """A value read from text (a file cell, or another type's text in a conversion). More forgiving
    than :func:`normalize` about formats people type, never about meaning: a currency symbol that
    is not the field's currency, a date that does not exist or an unknown choice is an error.
    Links are resolved by the service (they need the linked table)."""
    t = field["type"]
    cfg = field["config"]
    if isinstance(raw, bool):
        raw = "true" if raw else "false"
    s = "" if raw is None else str(raw)
    if t == "boolean":
        w = unicodedata.normalize("NFKC", s).strip().casefold()
        if w == "":
            return None
        if w in TRUE_WORDS:
            return True
        if w in FALSE_WORDS:
            return False
        raise CellError("not_boolean")
    if s.strip() == "":
        return None
    if t in ("text", "long_text"):
        return _text(t, s)
    if t in ("email", "url", "phone"):
        return _text(t, unicodedata.normalize("NFKC", s))
    if t == "number":
        return _decimal(_plain_number(s), cfg["decimals"])
    if t == "money":
        n = unicodedata.normalize("NFKC", s).strip()
        cur = None
        m = re.match(r"^([A-Za-z]{3})\s*(.+)$", n) or re.match(r"^(.+?)\s*([A-Za-z]{3})$", n)
        if m:
            a, b = m.groups()
            cur, n = (a.upper(), b) if len(a) == 3 and a.isalpha() else (b.upper(), a)
        if n[:1] in CURRENCY_SYMBOLS or n[:1] in "¥￥":
            sym_cur = CURRENCY_SYMBOLS.get(n[:1], "CNY")
            cur = cur or sym_cur
            if sym_cur != cur:
                raise CellError("currency_mismatch", currency=sym_cur, expected=cfg["currency"])
            n = n[1:]
        if cur and cur != cfg["currency"]:
            raise CellError("currency_mismatch", currency=cur, expected=cfg["currency"])
        return {"amount": _decimal(_plain_number(n), cfg["decimals"]), "currency": cfg["currency"]}
    if t == "date":
        n = unicodedata.normalize("NFKC", s).strip()
        m = re.match(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(?:[T ]00:00(?::00)?)?$", n)
        if not m:
            raise CellError("bad_date")
        return _date(f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}")
    if t == "datetime":
        n = unicodedata.normalize("NFKC", s).strip()
        if re.search(r":\d{2}\.\d*[1-9]", n):
            raise CellError("datetime_subseconds_not_supported")
        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s*(Z|[+-]\d{2}:?\d{2})?$", n, re.I)
        if not m:
            raise CellError("bad_datetime")
        y, mo, d, h, mi, sec, off = m.groups()
        return _datetime(f"{y}-{int(mo):02d}-{int(d):02d}T{int(h):02d}:{mi}:{sec or '00'}{off or ''}", utc_offset)
    if t == "single_select":
        return _choice(cfg, s.strip())
    if t == "multi_select":
        out: list[str] = []
        for part in SPLIT.split(s.strip()):
            if part:
                cid = _choice(cfg, part)
                if cid not in out:
                    out.append(cid)
        return out or None
    raise CellError("field_read_only" if t in COMPUTED else "unknown_type")


def _plain_number(s: str) -> str:
    n = unicodedata.normalize("NFKC", s).strip().replace(" ", "")
    if re.match(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$", n):
        n = n.replace(",", "")
    return n


# ============================================================================ text form
def as_text(field: dict, value: Any, *, titles: dict[str, str] | None = None,
            users: dict[str, str] | None = None) -> str:
    """How a value reads as text: exports, search, conversions between types."""
    t = field["type"]
    if value is None:
        return ""
    if t == "boolean":
        return "true" if value else "false"
    if t == "money":
        return f"{value['amount']} {value['currency']}" if isinstance(value, dict) else str(value)
    if t == "single_select":
        return choice_index(field["config"])[0].get(value, "")
    if t == "multi_select":
        labels = choice_index(field["config"])[0]
        return ", ".join(labels[c] for c in value if c in labels)
    if t == "link":
        return ", ".join((titles or {}).get(r, r) for r in value)
    if t in ("created_time", "updated_time"):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat(timespec="seconds")
    if t in ("created_by", "updated_by"):
        return (users or {}).get(value, value)
    if t == "formula" and isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ============================================================================ uniqueness
def unique_key(field: dict, value: Any) -> str | None:
    if value is None:
        return None
    t = field["type"]
    if t in ("text", "email", "url"):
        return unicodedata.normalize("NFKC", value).strip().casefold()
    if t == "phone":
        return re.sub(r"[^\d+]", "", value)
    if t == "number":
        return format(Decimal(value).normalize(), "f") if Decimal(value) != 0 else "0"
    if t == "datetime":
        return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    if t in ("date", "single_select"):
        return value
    return None


# ============================================================================ conversion
def convert(src: dict, dst: dict, value: Any, *, utc_offset: str = "+00:00") -> tuple[Any, str]:
    """Move one stored value from field definition ``src`` to ``dst`` (a type and/or configuration
    change). Returns ``(new_value, outcome)``, outcome one of ``unchanged`` (same stored value),
    ``ok`` (converted, nothing lost), ``lossy`` (converted, something lost — the original is kept
    by the caller), ``failed`` (cannot be represented — cleared, the original kept).

    "Nothing lost" is tested, not assumed: converting the result back must give the original."""
    if value is None or (src["type"] == "boolean" and value is None):
        return None, "unchanged"
    try:
        new = _convert_once(src, dst, value, utc_offset)
    except CellError:
        return None, "failed"
    if new == value and src["type"] == dst["type"]:
        return new, "unchanged"
    if src["type"] == "boolean" and dst["type"] == "boolean":
        return new, "unchanged" if new == value else "ok"
    try:
        back = _convert_once(dst, src, new, utc_offset)
    except CellError:
        back = object()
    return new, ("ok" if back == value else "lossy")


def _convert_once(src: dict, dst: dict, value: Any, utc_offset: str) -> Any:
    st, dt = src["type"], dst["type"]
    if value is None:
        return None
    if st == dt:
        if st == "number":
            return quantize(Decimal(value), dst["config"]["decimals"], strict=False)
        if st == "money":
            return {"amount": quantize(Decimal(value["amount"]), dst["config"]["decimals"], strict=False),
                    "currency": dst["config"]["currency"]}
        if st in SELECTS:
            ids = {c["id"] for c in dst["config"]["choices"]}
            items = value if isinstance(value, list) else [value]
            kept = [c for c in items if c in ids]
            if not kept:
                raise CellError("unknown_choice")
            return kept if st == "multi_select" else kept[0]
        if st == "link":
            if not dst["config"].get("multiple") and len(value) > 1:
                return value[:1]
            return value
        return value
    # between the two select types the choice ids carry over
    if st in SELECTS and dt in SELECTS:
        ids = {c["id"] for c in dst["config"]["choices"]}
        items = [c for c in (value if isinstance(value, list) else [value]) if c in ids]
        if not items:
            raise CellError("unknown_choice")
        return items if dt == "multi_select" else items[0]
    if st == "money" and dt == "number":
        return quantize(Decimal(value["amount"]), dst["config"]["decimals"], strict=False)
    if st == "number" and dt == "money":
        return {"amount": quantize(Decimal(value), dst["config"]["decimals"], strict=False),
                "currency": dst["config"]["currency"]}
    if st == "datetime" and dt == "date":
        return value[:10]
    if st == "date" and dt == "datetime":
        return _datetime(value + "T00:00:00", utc_offset)
    if st == "multi_select" and dt not in SELECTS:
        labels = choice_index(src["config"])[0]
        text = ", ".join(labels[c] for c in value if c in labels)
        return parse_text(dst, text, utc_offset=utc_offset)
    if dt == "long_text" and st == "text":
        return value
    if st == "long_text" and dt == "text":
        flat = re.sub(r"\s*[\r\n]+\s*", " ", value)
        return _text("text", flat)
    text = as_text(src, value)
    if dt in ("number", "money"):
        # a number with more places than the field keeps is rounded here — and flagged lossy by
        # the round trip, so the original is kept
        n = _plain_number(text if st != "money" else value["amount"])
        if not DECIMAL_TEXT.match(n):
            raise CellError("not_number")
        q = quantize(Decimal(n), dst["config"]["decimals"], strict=False)
        return q if dt == "number" else {"amount": q, "currency": dst["config"]["currency"]}
    return parse_text(dst, text, utc_offset=utc_offset)


def with_added_choices(field_from: dict, dst_type: str, dst_config: dict, values: list[Any]) -> tuple[dict, list[str]]:
    """Converting to a select type: labels found in the values that the target does not have yet
    become new choices (up to the limit), so a text column of statuses converts without losing a
    value. Returns the config to use and the labels added."""
    if field_from["type"] in SELECTS:
        return dst_config, []
    _, by_label = choice_index(dst_config)
    choices = list(dst_config.get("choices", []))
    added: list[str] = []
    for v in values:
        if v is None:
            continue
        text = as_text(field_from, v).strip()
        if not text:
            continue
        parts = SPLIT.split(text) if dst_type == "multi_select" else [text]
        for p in parts:
            p = p.strip()
            if not p or len(p) > 100 or p.casefold() in by_label:
                continue
            if len(choices) >= MAX_CHOICES:
                return {"choices": choices}, added
            cid = new_choice_id()
            choices.append({"id": cid, "label": p})
            by_label[p.casefold()] = cid
            added.append(p)
    return {"choices": choices}, added
