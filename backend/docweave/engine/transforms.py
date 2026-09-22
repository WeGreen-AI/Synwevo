"""Controlled value transforms.  Only the operations listed in ``OPS`` exist –
no expressions or user code are ever evaluated."""
from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import values as V
from .numfmt import format_value

DATE_PATTERNS = {
    "yyyy-mm-dd": "yyyy-mm-dd", "yyyy/m/d": "yyyy/m/d", "yyyy/mm/dd": "yyyy/mm/dd",
    "yyyy年m月d日": 'yyyy"年"m"月"d"日"', "yyyy年mm月dd日": 'yyyy"年"mm"月"dd"日"',
    "mm/dd/yyyy": "mm/dd/yyyy", "dd.mm.yyyy": "dd.mm.yyyy", "yyyymmdd": "yyyymmdd",
    "yyyy-mm-dd hh:mm": "yyyy-mm-dd hh:mm", "yyyy-mm-dd hh:mm:ss": "yyyy-mm-dd hh:mm:ss",
    "hh:mm": "hh:mm", "yyyy年m月": 'yyyy"年"m"月"',
}

OPS = {
    "trim": "去除首尾空格",
    "collapse_spaces": "合并连续空白",
    "date_format": "日期显示格式",
    "decimals": "数字小数位",
    "thousands": "千位分隔符",
    "prefix": "添加前缀",
    "suffix": "添加后缀",
    "default": "为空时使用默认值",
    "map": "值对照替换",
    "pad_left": "左侧补齐位数",
    "upper": "转大写",
    "lower": "转小写",
    "number_to_text": "按显示文本写入",
}

_DATE_PARSE = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?$"), "ymd"),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})(?: (\d{1,2}):(\d{2})(?::(\d{2}))?)?$"), "ymd"),
    (re.compile(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$"), "ymd"),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})$"), "ymd"),
]


class TransformError(ValueError):
    pass


def parse_date_text(s: str) -> _dt.date | _dt.datetime | None:
    s = s.strip()
    for rx, _ in _DATE_PARSE:
        m = rx.match(s)
        if m:
            g = m.groups()
            try:
                y, mo, d = int(g[0]), int(g[1]), int(g[2])
                if len(g) > 3 and g[3] is not None:
                    return _dt.datetime(y, mo, d, int(g[3]), int(g[4]), int(g[5] or 0))
                return _dt.date(y, mo, d)
            except ValueError:
                return None
    return None


def validate(ops: list[dict]) -> list[str]:
    errs = []
    for op in ops or []:
        name = op.get("op")
        if name not in OPS:
            errs.append(f"不支持的转换：{name}")
        if name == "date_format" and op.get("pattern") not in DATE_PATTERNS:
            got = op.get("pattern")
            errs.append((f"日期格式转换缺少 pattern" if got is None else f"不支持的日期格式：{got}")
                        + f"；可用：{'、'.join(DATE_PATTERNS)}")
        if name == "decimals":
            try:
                n = int(op.get("n", 2))
                if not 0 <= n <= 10:
                    errs.append("小数位需在 0–10 之间")
            except (TypeError, ValueError):
                errs.append("小数位必须是整数")
    return errs


def apply(value: V.Value, ops: list[dict] | None, warnings: list[str] | None = None) -> V.Value:
    warnings = warnings if warnings is not None else []
    v = V.Value(value.t, value.v, value.text, value.fmt, value.src, value.style)
    for op in ops or []:
        name = op.get("op")
        if name == "trim":
            v = _map_text(v, lambda s: s.strip())
        elif name == "collapse_spaces":
            v = _map_text(v, lambda s: re.sub(r"[ \t　]+", " ", s))
        elif name == "upper":
            v = _map_text(v, str.upper)
        elif name == "lower":
            v = _map_text(v, str.lower)
        elif name == "date_format":
            pat = DATE_PATTERNS.get(op.get("pattern", ""), "yyyy-mm-dd")
            if v.is_empty:
                continue
            d = V.to_python_date(v)
            if d is None and v.t == V.STR:
                d = parse_date_text(str(v.v))
            if d is None:
                warnings.append(f"「{v.text}」不是可识别的日期，未改变格式")
                continue
            text, _ = format_value(d, pat)
            v = V.Value(V.DATETIME if isinstance(d, _dt.datetime) else V.DATE, d.isoformat(), text, pat, v.src)
        elif name in ("decimals", "thousands"):
            if v.is_empty:
                continue
            num = v.as_decimal()
            if num is None:
                warnings.append(f"「{v.text}」不是数字，未应用数字格式")
                continue
            n = int(op.get("n", 2)) if name == "decimals" else _decimals_of(v)
            if name == "decimals":
                num = num.quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP)
            sep = bool(op.get("thousands")) or name == "thousands"
            body = f"{abs(num):,.{n}f}" if sep else f"{abs(num):.{n}f}"
            text = ("-" if num < 0 and num != 0 else "") + body
            v = V.Value(V.DEC, str(num), text, None, v.src)
        elif name == "prefix":
            if not v.is_empty:
                v = V.Value(V.STR if v.t != V.DEC else v.t, v.v, str(op.get("text", "")) + v.text, v.fmt, v.src)
        elif name == "suffix":
            if not v.is_empty:
                v = V.Value(V.STR if v.t != V.DEC else v.t, v.v, v.text + str(op.get("text", "")), v.fmt, v.src)
        elif name == "default":
            if v.is_empty or (v.t == V.STR and not str(v.v).strip()):
                t = str(op.get("text", ""))
                v = V.Value(V.STR, t, t, None, v.src)
        elif name == "map":
            table = op.get("table") or {}
            if v.text in table:
                t = str(table[v.text])
                v = V.Value(V.STR, t, t, None, v.src)
        elif name == "pad_left":
            if not v.is_empty:
                width = int(op.get("width", 0))
                ch = str(op.get("char", "0"))[:1] or "0"
                t = v.text.rjust(width, ch)
                v = V.Value(V.STR, t, t, None, v.src)
        elif name == "number_to_text":
            v = V.Value(V.STR, v.text, v.text, None, v.src)
        else:
            raise TransformError(f"不支持的转换：{name}")
    return v


def _decimals_of(v: V.Value) -> int:
    s = v.text or str(v.v)
    m = re.search(r"\.(\d+)", s)
    return len(m.group(1)) if m else 0


def _map_text(v: V.Value, fn) -> V.Value:
    if v.t == V.STR:
        s = fn(str(v.v))
        return V.Value(V.STR, s, s, v.fmt, v.src) if s != "" else V.Value(V.EMPTY, None, "", v.fmt, v.src)
    if v.is_empty:
        return v
    return V.Value(v.t, v.v, fn(v.text), v.fmt, v.src)


# ------------------------------------------------------------------ aggregation / combination
AGG = {"unique": "必须一致（否则报冲突）", "first": "取组内第一条", "last": "取组内最后一条",
       "join": "全部拼接", "join_distinct": "去重后拼接", "sum": "求和", "count": "计数"}
COMBINE = {"concat": "拼接", "newline": "换行拼接", "sum": "求和"}


class ConflictError(ValueError):
    def __init__(self, message: str, values: list[str]):
        super().__init__(message)
        self.values = values


def aggregate(vals: list[V.Value], how: str = "unique", sep: str = "、") -> V.Value:
    non_empty = [v for v in vals if not v.is_empty]
    if how == "count":
        return V.Value(V.INT, str(len(vals)), str(len(vals)))
    if not non_empty:
        return V.empty()
    if how == "unique":
        distinct = []
        for v in non_empty:
            if v.text not in [d.text for d in distinct]:
                distinct.append(v)
        if len(distinct) > 1:
            raise ConflictError("同一组内该字段有多个不同值", [d.text for d in distinct])
        return distinct[0]
    if how == "first":
        return non_empty[0]
    if how == "last":
        return non_empty[-1]
    if how in ("join", "join_distinct"):
        texts = [v.text for v in non_empty]
        if how == "join_distinct":
            texts = list(dict.fromkeys(texts))
        t = sep.join(texts)
        return V.Value(V.STR, t, t)
    if how == "sum":
        return _sum(non_empty)
    raise TransformError(f"不支持的聚合方式：{how}")


def _sum(vals: list[V.Value]) -> V.Value:
    total = Decimal(0)
    for v in vals:
        d = v.as_decimal()
        if d is None:
            raise TransformError(f"「{v.text}」不是数字，无法求和")
        total += d
    s = format(total.normalize(), "f") if total == total.to_integral() else format(total, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".") if "E" not in s else s
    return V.Value(V.DEC, s, s)


COMBINE_OPS = ("concat", "newline", "sum")


def combine(vals: list[V.Value], op: str, sep: str = " ") -> V.Value:
    # An unknown op must not fall through to concatenation: "sum" mistyped would turn
    # 100 + 200 into "100 200" in the delivered document, with nothing reported.
    if op not in COMBINE_OPS:
        raise TransformError(f"不支持的合并方式：{op}；只能是 {' / '.join(COMBINE_OPS)}")
    if op == "sum":
        non_empty = [v for v in vals if not v.is_empty]
        return _sum(non_empty) if non_empty else V.empty()
    if op == "newline":
        sep = "\n"
    texts = [v.text for v in vals if not v.is_empty]
    if not texts:
        return V.empty()
    t = sep.join(texts)
    return V.Value(V.STR, t, t)
