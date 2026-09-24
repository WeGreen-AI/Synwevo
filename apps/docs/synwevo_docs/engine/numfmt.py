"""A deliberately small Excel number-format renderer.

It covers the formats that appear in ordinary business spreadsheets (general,
fixed decimals, thousands separators, zero padding, percent, currency literals,
date/time patterns, Chinese date literals).  Unknown constructs fall back to the
canonical value and report ``exact=False`` so callers can surface a warning
instead of pretending the display matches Excel.
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal

_COLOR_RE = re.compile(r"\[(?:black|blue|cyan|green|magenta|red|white|yellow|color\s*\d+)\]", re.I)
_COND_RE = re.compile(r"\[[<>=][^\]]*\]")
_LOCALE_RE = re.compile(r"\[\$([^\]-]*)(?:-[0-9A-Fa-f]+)?\]")
_DBNUM_RE = re.compile(r"\[DBNum\d\]", re.I)

# Excel shows built-in id 14 in the OS short-date locale; openpyxl reports it as
# 'mm-dd-yy'.  We render it as ISO to avoid guessing the viewer's locale.
_LOCALE_DATE_ALIASES = {"mm-dd-yy": "yyyy-mm-dd", "m/d/yy": "yyyy-mm-dd", "d-mmm-yy": "yyyy-mm-dd",
                        "m/d/yy h:mm": "yyyy-mm-dd hh:mm", "mm-dd-yy h:mm": "yyyy-mm-dd hh:mm"}

_WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
              "October", "November", "December"]


def _strip_literals(fmt: str) -> str:
    out, i, n = [], 0, len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == '"':
            j = fmt.find('"', i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if ch in "_*" and i + 1 < n:
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def is_date_format(fmt: str | None) -> bool:
    if not fmt:
        return False
    f = _LOCALE_RE.sub("", _COLOR_RE.sub("", fmt))
    f = _strip_literals(f).lower()
    f = re.sub(r"\[[^\]]*\]", lambda m: "h" if m.group(0).lower() in ("[h]", "[hh]", "[m]", "[mm]", "[s]", "[ss]") else "", f)
    return any(c in f for c in "ymdhs") and "general" not in f


def _sections(fmt: str) -> list[str]:
    out, cur, i, n = [], [], 0, len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == '"':
            j = fmt.find('"', i + 1)
            j = n - 1 if j < 0 else j
            cur.append(fmt[i:j + 1])
            i = j + 1
            continue
        if ch == "\\" and i + 1 < n:
            cur.append(fmt[i:i + 2])
            i += 2
            continue
        if ch == ";":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def _tokenize(section: str):
    """Yield (kind, text) where kind is 'lit' or 'code'."""
    i, n = 0, len(section)
    while i < n:
        ch = section[i]
        if ch == '"':
            j = section.find('"', i + 1)
            j = n if j < 0 else j
            yield "lit", section[i + 1:j]
            i = j + 1
        elif ch == "\\" and i + 1 < n:
            yield "lit", section[i + 1]
            i += 2
        elif ch == "_" and i + 1 < n:
            yield "lit", " "
            i += 2
        elif ch == "*" and i + 1 < n:
            i += 2
        else:
            yield "code", ch
            i += 1


def format_value(value, fmt: str | None, date1904: bool = False) -> tuple[str, bool]:
    """Return (display_text, exact).  ``value`` is a Python object from openpyxl/xlrd.
    ``date1904``: a number under a date format is a serial of the 1904 date system."""
    if value is None:
        return "", True
    if isinstance(value, bool):
        return ("TRUE" if value else "FALSE"), True
    if isinstance(value, str):
        return value, True
    f = (fmt or "General").strip()
    f = _LOCALE_DATE_ALIASES.get(f.lower(), f)
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        if not is_date_format(f):
            f = "yyyy-mm-dd hh:mm:ss" if isinstance(value, _dt.datetime) and (value.hour or value.minute or value.second) else "yyyy-mm-dd"
            if isinstance(value, _dt.time):
                f = "hh:mm:ss"
        return _format_date(value, f)
    if isinstance(value, _dt.timedelta):
        return str(value), False
    if isinstance(value, (int, float, Decimal)):
        if is_date_format(f):
            return _format_date(serial_to_datetime(float(value), date1904), f)
        return _format_number(value, f)
    return str(value), False


def serial_to_datetime(serial: float, date1904: bool = False) -> _dt.datetime:
    base = _dt.datetime(1904, 1, 1) if date1904 else _dt.datetime(1899, 12, 30)
    return base + _dt.timedelta(days=serial)


def datetime_to_serial(value, date1904: bool = False) -> float:
    base = _dt.datetime(1904, 1, 1) if date1904 else _dt.datetime(1899, 12, 30)
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        value = _dt.datetime(value.year, value.month, value.day)
    if isinstance(value, _dt.time):
        return (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6) / 86400
    delta = value - base
    return delta.days + delta.seconds / 86400 + delta.microseconds / 86400e6


def _general(value) -> str:
    from .values import float_repr
    if isinstance(value, float):
        return float_repr(value)
    return str(value)


def _format_number(value, fmt: str) -> tuple[str, bool]:
    if fmt.lower() in ("general", "@", ""):
        return _general(value), True
    secs = _sections(fmt)
    num = Decimal(str(value)) if not isinstance(value, Decimal) else value
    sec = secs[0]
    negative_explicit = False
    if num < 0 and len(secs) >= 2 and secs[1].strip():
        sec, negative_explicit = secs[1], True
    elif num == 0 and len(secs) >= 3 and secs[2].strip():
        sec = secs[2]
    exact = True
    if _COND_RE.search(sec):
        exact = False
        sec = _COND_RE.sub("", sec)
    sec = _COLOR_RE.sub("", sec)
    sec = _DBNUM_RE.sub("", sec)
    currency = []

    def _loc(m):
        sym = m.group(1)
        currency.append(sym)
        return '"' + sym.replace('"', "") + '"'

    sec = _LOCALE_RE.sub(_loc, sec)
    if sec.lower().strip() == "general":
        return _general(value), True
    if "?" in sec and "/" in sec:
        return _general(value), False
    if re.search(r"[eE][+-]", _strip_literals(sec)):
        digits = _strip_literals(sec).split(".")
        dec = 0
        if len(digits) > 1:
            dec = len(re.match(r"[0#]*", digits[1]).group(0))
        return f"{float(num):.{dec}E}", True

    tokens = list(_tokenize(sec))
    code = "".join(t for k, t in tokens if k == "code")
    if not re.search(r"[0#?]", code):
        # a pure literal section (e.g. "-" for zero)
        return "".join(t if k == "lit" else ("" if t in "@" else t) for k, t in tokens), exact
    percent = code.count("%")
    if percent:
        num = num * (Decimal(100) ** percent)
    # trailing commas scale by thousands
    m_scale = re.search(r"[0#?](,+)(?:[^0#?]|$)", code)
    if m_scale and "." not in code[m_scale.end(1):]:
        num = num / (Decimal(1000) ** len(m_scale.group(1)))
    int_part_code, _, frac_code = code.partition(".")
    frac_digits = len(re.findall(r"[0#?]", frac_code))
    min_frac = len(re.findall(r"0", frac_code))
    int_digits_min = len(re.findall(r"0", int_part_code))
    thousands = "," in re.sub(r",+(?=[^0#?]*$)", "", int_part_code)
    abs_num = abs(num)
    q = Decimal(1).scaleb(-frac_digits) if frac_digits else Decimal(1)
    rounded = abs_num.quantize(q, rounding=ROUND_HALF_UP)
    s = format(rounded, "f")
    ip, _, fp = s.partition(".")
    if ip == "0" and int_digits_min == 0:
        ip = ""
    ip = ip.zfill(int_digits_min) if int_digits_min else ip
    if thousands and ip:
        ip = f"{int(ip):,}" if ip.isdigit() else ip
        if int_digits_min > len(ip):
            ip = ip.zfill(int_digits_min)
    if frac_digits:
        fp = fp.ljust(frac_digits, "0")
        # trim optional '#' digits
        while len(fp) > min_frac and fp.endswith("0"):
            fp = fp[:-1]
        number_text = ip + ("." + fp if fp or "." in code else "")
    else:
        number_text = ip
    if number_text == "":
        number_text = "0" if frac_digits == 0 else number_text
    # assemble: literals before first digit placeholder, number, literals after
    before, after, seen_digit, done_number = [], [], False, False
    for k, t in tokens:
        if k == "code" and (t in "0#?.," ):
            seen_digit = True
            continue
        if k == "code" and t == "%":
            (after if seen_digit else before).append("%")
            continue
        if k == "code" and t in "@":
            continue
        (after if seen_digit else before).append(t)
    sign = "-" if (num < 0 and not negative_explicit and rounded != 0) else ""
    return sign + "".join(before) + number_text + "".join(after), exact


def _format_date(value, fmt: str) -> tuple[str, bool]:
    sec = _sections(fmt)[0]
    sec = _COLOR_RE.sub("", sec)
    sec = _DBNUM_RE.sub("", sec)
    sec = _LOCALE_RE.sub(lambda m: '"' + m.group(1) + '"', sec)
    exact = True
    tokens = list(_tokenize(sec))
    # group consecutive same letters of code tokens
    parts: list[tuple[str, str]] = []
    for k, t in tokens:
        if k == "code" and parts and parts[-1][0] == "code" and parts[-1][1][-1].lower() == t.lower() and t.lower() in "ymdhs":
            parts[-1] = ("code", parts[-1][1] + t)
        else:
            parts.append((k, t))
    # merge AM/PM
    joined = "".join(t for _, t in parts)
    ampm = "am/pm" in joined.lower() or "a/p" in joined.lower()
    out = []
    dt = value
    if isinstance(dt, _dt.date) and not isinstance(dt, _dt.datetime):
        dt = _dt.datetime(dt.year, dt.month, dt.day)
    if isinstance(dt, _dt.time):
        dt = _dt.datetime(1899, 12, 30, dt.hour, dt.minute, dt.second, dt.microsecond)
    idx = 0
    codes = [p for p in parts]
    i = 0
    while i < len(codes):
        k, t = codes[i]
        if k == "lit":
            out.append(t)
            i += 1
            continue
        tl = t.lower()
        if tl.startswith("am/pm") or t == "A" or t == "a":
            # handled via sequence "AM/PM"
            pass
        if tl in ("a", "p") or t in "/":
            # detect AM/PM sequences
            rest = "".join(x[1] for x in codes[i:i + 5]).lower()
            if rest.startswith("am/pm"):
                out.append("AM" if dt.hour < 12 else "PM")
                i += 5 if len(codes[i:i + 5]) >= 5 else 1
                continue
            if rest.startswith("a/p"):
                out.append("A" if dt.hour < 12 else "P")
                i += 3
                continue
            out.append(t)
            i += 1
            continue
        c = tl[0] if tl else ""
        n = len(t)
        if c == "y":
            out.append(str(dt.year) if n > 2 else f"{dt.year % 100:02d}")
        elif c == "m":
            prev_is_h = _neighbor_code(codes, i, -1) in ("h",)
            next_is_s = _neighbor_code(codes, i, 1) in ("s",)
            if (prev_is_h or next_is_s) and n <= 2:
                out.append(f"{dt.minute:02d}" if n == 2 else str(dt.minute))
            elif n == 1:
                out.append(str(dt.month))
            elif n == 2:
                out.append(f"{dt.month:02d}")
            elif n == 3:
                out.append(_MONTHS_EN[dt.month - 1][:3])
            else:
                out.append(_MONTHS_EN[dt.month - 1])
        elif c == "d":
            if n == 1:
                out.append(str(dt.day))
            elif n == 2:
                out.append(f"{dt.day:02d}")
            elif n == 3:
                out.append(_WEEKDAYS_EN[dt.weekday()][:3])
            else:
                out.append(_WEEKDAYS_EN[dt.weekday()])
        elif c == "h":
            h = dt.hour
            if ampm:
                h = h % 12 or 12
            out.append(f"{h:02d}" if n >= 2 else str(h))
        elif c == "s":
            out.append(f"{dt.second:02d}" if n >= 2 else str(dt.second))
        elif c == "e":
            out.append(str(dt.year))
        elif t == "[":
            # elapsed-time brackets like [h]
            exact = False
            j = i
            while j < len(codes) and codes[j][1] != "]":
                j += 1
            i = j + 1
            continue
        elif t == "." and i + 1 < len(codes) and codes[i + 1][1].startswith("0"):
            digits = len(codes[i + 1][1])
            out.append("." + f"{dt.microsecond:06d}"[:digits])
            i += 2
            continue
        else:
            out.append(t)
        i += 1
    return "".join(out), exact


def _neighbor_code(codes, i, step):
    j = i + step
    while 0 <= j < len(codes):
        k, t = codes[j]
        if k == "code" and t and t[0].lower() in "ymdhs":
            return t[0].lower()
        j += step
    return None
