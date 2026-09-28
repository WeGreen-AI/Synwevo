"""Controlled value transforms.  Only the operations listed in ``OPS`` exist –
no expressions or user code are ever evaluated."""
from __future__ import annotations

import datetime as _dt
import re
from decimal import ROUND_HALF_UP, Context, Decimal, InvalidOperation

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


# Parameters each operation takes.  Anything else is refused: an option nobody reads is an
# option the caller believes was applied.
_PARAMS = {
    "trim": (), "collapse_spaces": (), "upper": (), "lower": (), "number_to_text": (), "thousands": (),
    "date_format": ("pattern",), "decimals": ("n", "thousands"), "prefix": ("text",), "suffix": ("text",),
    "default": ("text",), "map": ("table",), "pad_left": ("width", "char"),
}
MAX_AFFIX = 200                 # prefix / suffix characters
MAX_DEFAULT = 1000
MAX_PAD_WIDTH = 64              # pad_left used to take any width: 10**9 allocated gigabytes per record
MAX_MAP_ENTRIES = 5000
MAX_MAP_TEXT = 1000


def _int_in(op: dict, key: str, default: int, lo: int, hi: int, label: str, errs: list[str]) -> None:
    v = op.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        errs.append(f"{label}必须是整数")
        return
    try:
        n = int(v)
        if float(v) != n:
            raise ValueError
    except (TypeError, ValueError):
        errs.append(f"{label}必须是整数")
        return
    if not lo <= n <= hi:
        errs.append(f"{label}需在 {lo}–{hi} 之间")


def _text_param(op: dict, key: str, cap: int, label: str, errs: list[str]) -> None:
    v = op.get(key, "")
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        errs.append(f"{label}必须是文字")
    elif len(str(v)) > cap:
        errs.append(f"{label}最多 {cap} 个字符")


def validate(ops: list[dict]) -> list[str]:
    errs: list[str] = []
    if ops is None:
        return errs
    if not isinstance(ops, list):
        return ["转换列表格式无效"]
    for op in ops:
        if not isinstance(op, dict):
            errs.append("转换格式无效")
            continue
        name = op.get("op")
        if name not in OPS:
            errs.append(f"不支持的转换：{name}")
            continue
        extra = sorted(k for k in op if k != "op" and k not in _PARAMS[name])
        if extra:
            errs.append(f"转换「{OPS[name]}」不接受参数：{'、'.join(map(str, extra))}")
        if name == "date_format" and op.get("pattern") not in DATE_PATTERNS:
            got = op.get("pattern")
            errs.append((f"日期格式转换缺少 pattern" if got is None else f"不支持的日期格式：{got}")
                        + f"；可用：{'、'.join(DATE_PATTERNS)}")
        elif name == "decimals":
            _int_in(op, "n", 2, 0, 10, "小数位", errs)
            if "thousands" in op and not isinstance(op["thousands"], bool):
                errs.append("千位分隔设置必须是 true / false")
        elif name in ("prefix", "suffix"):
            _text_param(op, "text", MAX_AFFIX, "前后缀", errs)
        elif name == "default":
            _text_param(op, "text", MAX_DEFAULT, "默认值", errs)
        elif name == "pad_left":
            _int_in(op, "width", 0, 0, MAX_PAD_WIDTH, "补齐宽度", errs)
            ch = op.get("char", "0")
            if not isinstance(ch, str) or len(ch) > 1:
                errs.append("补齐字符必须是单个字符")
        elif name == "map":
            table = op.get("table", {})
            if not isinstance(table, dict):
                errs.append("对照表必须是「原值 → 新值」的对应关系")
            elif len(table) > MAX_MAP_ENTRIES:
                errs.append(f"对照表最多 {MAX_MAP_ENTRIES} 条")
            elif any(not isinstance(k, str) or isinstance(v, (dict, list)) or v is None
                     or len(k) > MAX_MAP_TEXT or len(str(v)) > MAX_MAP_TEXT for k, v in table.items()):
                errs.append(f"对照表的原值和新值必须是文字（每项最多 {MAX_MAP_TEXT} 个字符）")
    return errs


def apply(value: V.Value, ops: list[dict] | None, warnings: list[str] | None = None) -> V.Value:
    warnings = warnings if warnings is not None else []
    errs = validate(ops)
    if errs:
        # never run an unchecked parameter (a slot format that skipped validation, an import)
        raise TransformError("；".join(errs))
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
                # enough precision for any digit count: the default context (28 digits) raised
                # InvalidOperation on long identifiers-as-numbers
                ctx = Context(prec=max(28, len(num.as_tuple().digits) + n + 2))
                num = num.quantize(Decimal(1).scaleb(-n), rounding=ROUND_HALF_UP, context=ctx)
            sep = bool(op.get("thousands")) or name == "thousands"
            body = f"{abs(num):,.{n}f}" if sep else f"{abs(num):.{n}f}"
            text = ("-" if num < 0 and num != 0 else "") + body
            v = V.Value(V.DEC, str(num), text, None, v.src)
        elif name in ("prefix", "suffix"):
            if not v.is_empty:
                t = str(op.get("text", "")) + v.text if name == "prefix" else v.text + str(op.get("text", ""))
                # a decimal keeps its number (the affix is display); anything else becomes that
                # text in both fields, so a later trim / upper / lower keeps the affix
                v = V.Value(V.DEC, v.v, t, v.fmt, v.src) if v.t == V.DEC else V.Value(V.STR, t, t, v.fmt, v.src)
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
    raise TransformError(f"不支持的组内取值方式：{how}；只能是必须一致、取第一条、取最后一条、全部拼接、去重后拼接、求和或计数")


def _sum(vals: list[V.Value]) -> V.Value:
    total = Decimal(0)
    doubles = False
    for v in vals:
        # a double is added whole and only the total is shown as Excel shows it (15 significant
        # digits): 100/3 three times is 100, not 99.9999999999999 (rounding each first) nor
        # 100.000000000000008 (showing the binary sum)
        d = V.exact_decimal(v) if v.t == V.FLOAT else v.as_decimal()
        if d is None:
            raise TransformError(f"「{v.text}」不是数字，无法求和")
        doubles = doubles or v.t == V.FLOAT
        total += d
    if doubles:
        total = Context(prec=15, rounding=ROUND_HALF_UP).plus(total)
    s = format(total.normalize(), "f") if total == total.to_integral() else format(total, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".") if "E" not in s else s
    return V.Value(V.DEC, s, s)


COMBINE_OPS = ("concat", "newline", "sum")


def combine(vals: list[V.Value], op: str, sep: str = " ") -> V.Value:
    # An unknown op must not fall through to concatenation: "sum" mistyped would turn
    # 100 + 200 into "100 200" in the delivered document, with nothing reported.
    if op not in COMBINE_OPS:
        raise TransformError(f"不支持的合并方式：{op}；只能是拼接、换行拼接或求和")
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
