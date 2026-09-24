"""Built-in Excel formula engine.

Evaluates the formulas of a workbook (after Synwevo Docs filled it) so outputs carry
correct cached values without Excel or LibreOffice.  Coverage: arithmetic,
comparison, text concatenation, cross-sheet references, whole-row/column
ranges, defined names and ~90 common functions (maths, statistics, conditional
aggregation, lookup, text, date, logic, information).

Anything outside that set raises :class:`Unsupported`; the caller then falls back
to LibreOffice (when installed) or leaves the cell for Excel to recalculate and
tells the user which function was not computed.  Nothing is guessed.
"""
from __future__ import annotations

import datetime as _dt
import math
import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Context, Decimal

from openpyxl.formula.tokenizer import Token, Tokenizer

from .numfmt import datetime_to_serial, format_value, serial_to_datetime
from .tabular import col_index, col_letter


class Unsupported(Exception):
    """The formula uses something the built-in engine does not implement."""


@dataclass(frozen=True)
class XLError:
    code: str

    def __str__(self) -> str:
        return self.code


DIV0, VALUE, REF, NAME, NA, NUM = (XLError("#DIV/0!"), XLError("#VALUE!"), XLError("#REF!"), XLError("#NAME?"),
                                   XLError("#N/A"), XLError("#NUM!"))
ERRORS = {e.code: e for e in (DIV0, VALUE, REF, NAME, NA, NUM, XLError("#NULL!"))}


class _Raise(Exception):
    def __init__(self, err: XLError):
        self.err = err


class Range:
    """A rectangular block of evaluated values."""

    def __init__(self, rows: list[list]):
        self.rows = rows

    def values(self):
        for row in self.rows:
            yield from row

    @property
    def shape(self):
        return len(self.rows), (len(self.rows[0]) if self.rows else 0)


# ====================================================================== parser
_PREC = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}
_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_COLS = re.compile(r"^\$?([A-Za-z]{1,3}):\$?([A-Za-z]{1,3})$")
_ROWS = re.compile(r"^\$?(\d+):\$?(\d+)$")


def parse(formula: str):
    text = formula if formula.startswith("=") else "=" + formula
    try:
        toks = [t for t in Tokenizer(text).items if t.type != Token.WSPACE]
    except Exception as e:  # noqa: BLE001
        raise Unsupported(f"无法解析公式：{e}") from e
    p = _Parser(toks)
    node = p.expr(0)
    if p.i != len(toks):
        raise Unsupported("公式结构不受支持")
    return node


class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expr(self, min_prec: int):
        left = self.unary()
        while True:
            tok = self.peek()
            if tok is None:
                break
            if tok.type == Token.OP_POST and tok.value == "%":
                self.next()
                left = ("pct", left)
                continue
            if tok.type != Token.OP_IN:
                break
            op = tok.value
            if op not in _PREC:
                raise Unsupported(f"不支持的运算符 {op}")
            prec = _PREC[op]
            if prec < min_prec:
                break
            self.next()
            right = self.expr(prec + 1)          # all Excel binary operators are left associative
            left = ("bin", op, left, right)
        return left

    def unary(self):
        tok = self.peek()
        if tok is not None and tok.type == Token.OP_PRE:
            self.next()
            operand = self.unary()
            return ("neg", operand) if tok.value == "-" else operand
        return self.primary()

    def primary(self):
        tok = self.next()
        if tok.type == Token.OPERAND:
            st = tok.subtype
            if st == Token.NUMBER:
                return ("num", float(tok.value))
            if st == Token.TEXT:
                return ("str", tok.value[1:-1].replace('""', '"'))
            if st == Token.LOGICAL:
                return ("bool", tok.value.upper() == "TRUE")
            if st == Token.ERROR:
                return ("err", ERRORS.get(tok.value.upper(), VALUE))
            if st == Token.RANGE:
                return ("ref", tok.value)
            raise Unsupported(f"不支持的操作数 {tok.value}")
        if tok.type == Token.FUNC and tok.subtype == Token.OPEN:
            name = tok.value[:-1].upper()
            if name.startswith("_XLFN."):
                name = name[6:]
            if name.startswith("_XLWS."):
                name = name[6:]
            args = []
            nxt = self.peek()
            if nxt is not None and nxt.type == Token.FUNC and nxt.subtype == Token.CLOSE:
                self.next()
                return ("fn", name, args)
            while True:
                nxt = self.peek()
                if nxt is not None and nxt.type == Token.SEP and nxt.subtype == Token.ARG:
                    args.append(("missing",))
                    self.next()
                    continue
                args.append(self.expr(0))
                sep = self.next()
                if sep.type == Token.FUNC and sep.subtype == Token.CLOSE:
                    break
                if not (sep.type == Token.SEP and sep.subtype == Token.ARG):
                    raise Unsupported("函数参数结构不受支持")
                nxt = self.peek()
                if nxt is not None and nxt.type == Token.FUNC and nxt.subtype == Token.CLOSE:
                    args.append(("missing",))
                    self.next()
                    break
            return ("fn", name, args)
        if tok.type == Token.PAREN and tok.subtype == Token.OPEN:
            inner = self.expr(0)
            close = self.next()
            if not (close.type == Token.PAREN and close.subtype == Token.CLOSE):
                raise Unsupported("括号不匹配")
            return inner
        if tok.type == Token.ARRAY:
            raise Unsupported("数组常量")
        raise Unsupported(f"不支持的公式元素 {tok.value}")


# ====================================================================== workbook model
class Book:
    """Cell values/formulas for all sheets.  ``cells[sheet][(r, c)]`` holds either
    ('v', value) or ('f', formula_text); r/c are 0-based."""

    def __init__(self, cells: dict[str, dict[tuple[int, int], tuple]], names: dict[str, str] | None = None,
                 date1904: bool = False):
        self.cells = cells
        self.lower = {k.lower(): k for k in cells}
        self.names = {k.upper(): v for k, v in (names or {}).items()}
        self.date1904 = date1904
        self.cache: dict[tuple, object] = {}
        self.stack: set[tuple] = set()
        self.dims = {s: (max((r for r, _ in d), default=-1), max((c for _, c in d), default=-1)) for s, d in cells.items()}
        self._parsed: dict[str, object] = {}

    def sheet(self, name: str) -> str:
        if name in self.cells:
            return name
        k = self.lower.get(name.lower())
        if k is None:
            raise _Raise(REF)
        return k

    def value(self, sheet: str, r: int, c: int):
        key = (sheet, r, c)
        if key in self.cache:
            return self.cache[key]
        cell = self.cells.get(sheet, {}).get((r, c))
        if cell is None:
            return None
        if cell[0] == "v":
            return cell[1]
        if cell[0] == "f!":
            raise Unsupported("数组公式")
        if key in self.stack:
            raise Unsupported("循环引用")
        self.stack.add(key)
        try:
            v = self.eval_formula(sheet, cell[1])
        finally:
            self.stack.discard(key)
        if isinstance(v, Range):
            v = v.rows[0][0] if v.rows and v.rows[0] else None
        v = _finite_result(v)
        self.cache[key] = v
        return v

    def eval_formula(self, sheet: str, formula: str):
        node = self._parsed.get(formula)
        if node is None:
            node = parse(formula)
            self._parsed[formula] = node
        try:
            return _Eval(self, sheet).ev(node)
        except _Raise as e:
            return e.err
        # Numeric edge cases are Excel error values, not a failed document: POWER(0,-1) is
        # #DIV/0!, DATE(10000,1,1) and an overflowing product are #NUM!.  Dependent cells then
        # see the error value exactly as they would in Excel.
        except ZeroDivisionError:
            return DIV0
        except (ArithmeticError, ValueError):         # overflow, decimal InvalidOperation, domain errors
            return NUM

    # ---------------------------------------------------------------- references
    def resolve_ref(self, host: str, text: str):
        """Return a scalar (single cell) or Range."""
        sheet = host
        area = text
        if "!" in text:
            idx = text.rfind("!")
            sname = text[:idx]
            area = text[idx + 1:]
            if sname.startswith("'") and sname.endswith("'"):
                sname = sname[1:-1].replace("''", "'")
            if sname.startswith("[") or ":" in sname:
                raise Unsupported("外部工作簿或三维引用")
            sheet = self.sheet(sname)
        if area.upper() in self.names or (":" not in area and not _CELL.match(area)):
            name = self.names.get(area.upper())
            if name is None:
                if "[" in area:
                    raise Unsupported("结构化引用（表格列）")
                raise _Raise(NAME)
            return self.resolve_ref(host, name.lstrip("="))
        if area.upper() == "#REF!":
            raise _Raise(REF)
        m = _CELL.match(area)
        if m:
            return self.value(sheet, int(m.group(2)) - 1, col_index(m.group(1)))
        if ":" in area:
            a, b = area.split(":", 1)
            ma, mb = _CELL.match(a), _CELL.match(b)
            if ma and mb:
                r1, c1 = int(ma.group(2)) - 1, col_index(ma.group(1))
                r2, c2 = int(mb.group(2)) - 1, col_index(mb.group(1))
            else:
                mc, mr = _COLS.match(area), _ROWS.match(area)
                maxr, maxc = self.dims.get(sheet, (-1, -1))
                if mc:
                    c1, c2 = col_index(mc.group(1)), col_index(mc.group(2))
                    r1, r2 = 0, max(maxr, 0)
                elif mr:
                    r1, r2 = int(mr.group(1)) - 1, int(mr.group(2)) - 1
                    c1, c2 = 0, max(maxc, 0)
                else:
                    raise Unsupported(f"不支持的引用 {area}")
            r1, r2 = min(r1, r2), max(r1, r2)
            c1, c2 = min(c1, c2), max(c1, c2)
            if (r2 - r1 + 1) * (c2 - c1 + 1) > 2_000_000:
                raise Unsupported("引用区域过大")
            return Range([[self.value(sheet, r, c) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)])
        raise Unsupported(f"不支持的引用 {area}")


# ====================================================================== coercion
def _finite_result(v):
    """inf / nan / complex never reach a cell: Excel shows #NUM! for all of them."""
    if isinstance(v, complex):
        return NUM
    if isinstance(v, float) and not math.isfinite(v):
        return NUM
    return v


def _num(v):
    if isinstance(v, XLError):
        raise _Raise(v)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "")
        if s.endswith("%"):
            try:
                return float(s[:-1]) / 100
            except ValueError:
                raise _Raise(VALUE)
        try:
            return float(s)
        except ValueError:
            d = _parse_date(s)
            if d is not None:
                return d
            raise _Raise(VALUE)
    if isinstance(v, Range):
        if v.shape == (1, 1):
            return _num(v.rows[0][0])
        raise _Raise(VALUE)
    raise _Raise(VALUE)


def _parse_date(s: str):
    m = re.match(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$", s)
    if not m:
        return None
    try:
        return datetime_to_serial(_dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    except ValueError:
        return None


def general(x: float) -> str:
    if x != x:
        return "#NUM!"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    s = format(x, ".15g")
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}E{'+' if not exp.startswith('-') else '-'}{abs(int(exp)):02d}"
    return s


def _text(v):
    if isinstance(v, XLError):
        raise _Raise(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return general(float(v))
    if isinstance(v, Range):
        if v.shape == (1, 1):
            return _text(v.rows[0][0])
        raise _Raise(VALUE)
    return str(v)


def _bool(v):
    if isinstance(v, XLError):
        raise _Raise(v)
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        if v.upper() in ("TRUE", "FALSE"):
            return v.upper() == "TRUE"
        raise _Raise(VALUE)
    if isinstance(v, Range) and v.shape == (1, 1):
        return _bool(v.rows[0][0])
    raise _Raise(VALUE)


def _scalar(v):
    if isinstance(v, Range):
        if v.shape == (1, 1):
            return v.rows[0][0]
        raise _Raise(VALUE)
    return v


def _nums_for_agg(args, *, count_text_as_zero=False):
    """Numbers for SUM/AVERAGE/MIN/MAX: in ranges only numbers count; direct
    arguments are coerced (text that is not a number is an error)."""
    out = []
    for a in args:
        if isinstance(a, Range):
            for v in a.values():
                if isinstance(v, XLError):
                    raise _Raise(v)
                if isinstance(v, bool) or v is None or isinstance(v, str):
                    if count_text_as_zero and (isinstance(v, str) or isinstance(v, bool)):
                        out.append(0.0 if isinstance(v, str) else (1.0 if v else 0.0))
                    continue
                out.append(float(v))
        else:
            if a is None:
                continue
            out.append(_num(a))
    return out


# ====================================================================== evaluator
class _Eval:
    def __init__(self, book: Book, sheet: str):
        self.b = book
        self.sheet = sheet

    def ev(self, n):
        k = n[0]
        if k == "num":
            return n[1]
        if k == "str":
            return n[1]
        if k == "bool":
            return n[1]
        if k == "err":
            return n[1]
        if k == "missing":
            return None
        if k == "ref":
            return self.b.resolve_ref(self.sheet, n[1])
        if k == "neg":
            return -_num(self.ev(n[1]))
        if k == "pct":
            return _num(self.ev(n[1])) / 100
        if k == "bin":
            return self.binop(n[1], n[2], n[3])
        if k == "fn":
            return self.call(n[1], n[2])
        raise Unsupported(k)

    def binop(self, op, a, b):
        x, y = _scalar(self.ev(a)), _scalar(self.ev(b))
        if op == "&":
            return _text(x) + _text(y)
        if op in ("+", "-", "*", "/", "^"):
            xn, yn = _num(x), _num(y)
            if op == "+":
                return xn + yn
            if op == "-":
                return xn - yn
            if op == "*":
                return xn * yn
            if op == "/":
                if yn == 0:
                    raise _Raise(DIV0)
                return xn / yn
            try:
                r = xn ** yn
            except (OverflowError, ZeroDivisionError):
                raise _Raise(NUM)
            if isinstance(r, complex):
                raise _Raise(NUM)
            return r
        return _compare(op, x, y)

    def call(self, name, args):
        lazy = _LAZY.get(name)
        if lazy is not None:
            return lazy(self, args)
        fn = _FUNCS.get(name)
        if fn is None:
            raise Unsupported(f"函数 {name}")
        vals = [self.ev(a) for a in args]
        return fn(self.b, vals)


def _cmp_key(v):
    # Excel ordering: numbers < text < logicals; empty compares as 0 / ""
    if isinstance(v, bool):
        return (2, v)
    if isinstance(v, (int, float)):
        return (0, float(v))
    if isinstance(v, str):
        return (1, v.lower())
    return (0, 0.0)


def _compare(op, x, y):
    if isinstance(x, XLError):
        raise _Raise(x)
    if isinstance(y, XLError):
        raise _Raise(y)
    if x is None:
        x = "" if isinstance(y, str) else (False if isinstance(y, bool) else 0.0)
    if y is None:
        y = "" if isinstance(x, str) else (False if isinstance(x, bool) else 0.0)
    a, b = _cmp_key(x), _cmp_key(y)
    return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


# ---------------------------------------------------------------- lazy functions
def _if(e: _Eval, args):
    if not args:
        raise _Raise(VALUE)
    cond = _bool(_scalar(e.ev(args[0])))
    if cond:
        return e.ev(args[1]) if len(args) > 1 and args[1][0] != "missing" else (True if len(args) < 2 else 0.0)
    if len(args) > 2:
        return e.ev(args[2]) if args[2][0] != "missing" else 0.0
    return False


def _iferror(e: _Eval, args):
    try:
        v = e.ev(args[0])
        v = _scalar(v) if isinstance(v, Range) and v.shape == (1, 1) else v
    except _Raise:
        return e.ev(args[1])
    if isinstance(v, XLError):
        return e.ev(args[1])
    return v


def _ifna(e: _Eval, args):
    try:
        v = e.ev(args[0])
    except _Raise as r:
        if r.err == NA:
            return e.ev(args[1])
        raise
    if v == NA:
        return e.ev(args[1])
    return v


def _ifs(e: _Eval, args):
    for i in range(0, len(args) - 1, 2):
        if _bool(_scalar(e.ev(args[i]))):
            return e.ev(args[i + 1])
    raise _Raise(NA)


def _choose(e: _Eval, args):
    i = int(_num(_scalar(e.ev(args[0]))))
    if i < 1 or i >= len(args):
        raise _Raise(VALUE)
    return e.ev(args[i])


def _switch(e: _Eval, args):
    v = _scalar(e.ev(args[0]))
    rest = args[1:]
    for i in range(0, len(rest) - 1, 2):
        if _compare("=", v, _scalar(e.ev(rest[i]))):
            return e.ev(rest[i + 1])
    if len(rest) % 2 == 1:
        return e.ev(rest[-1])
    raise _Raise(NA)


def _and_or(kind):
    def f(e: _Eval, args):
        vals = []
        for a in args:
            v = e.ev(a)
            if isinstance(v, Range):
                for x in v.values():
                    if isinstance(x, XLError):
                        raise _Raise(x)
                    if isinstance(x, (bool, int, float)):
                        vals.append(bool(x))
            elif v is not None:
                vals.append(_bool(v))
        if not vals:
            raise _Raise(VALUE)
        if kind == "and":
            return all(vals)
        if kind == "or":
            return any(vals)
        return sum(vals) % 2 == 1
    return f


def _is_lazy(kind):
    def f(e: _Eval, args):
        if len(args) != 1:
            raise _Raise(VALUE)
        try:
            v = e.ev(args[0])
        except _Raise as r:
            v = r.err
        return f_is(kind)(e.b, [v])
    return f


_LAZY = {"IF": _if, "IFERROR": _iferror, "IFNA": _ifna, "IFS": _ifs, "CHOOSE": _choose, "SWITCH": _switch,
         "AND": _and_or("and"), "OR": _and_or("or"), "XOR": _and_or("xor"),
         "ISBLANK": _is_lazy("blank"), "ISNUMBER": _is_lazy("number"), "ISTEXT": _is_lazy("text"), "ISERROR": _is_lazy("error"),
         "ISERR": _is_lazy("err"), "ISNA": _is_lazy("na"), "ISLOGICAL": _is_lazy("logical")}


# ---------------------------------------------------------------- eager functions
def _dec15(x: float) -> Decimal:
    """The number as Excel sees it: 15 significant digits.  Rounding the binary float (with a
    fudge term) got ROUND(0.4999999999, 0) = 1 and TRUNC(1.15, 2) = 1.14."""
    return Decimal(format(x, ".15g"))


def _round_dec(x: float, n: int, mode) -> float:
    n = max(-400, min(400, n))
    q = Decimal(1).scaleb(-n)
    return float(_dec15(x).quantize(q, rounding=mode, context=Context(prec=1000)))


def _round_half_away(x: float, n: int) -> float:
    return _round_dec(x, n, ROUND_HALF_UP)


def _rng_list(a):
    if isinstance(a, Range):
        return list(a.values())
    return [a]


def _criteria(c):
    """Excel criteria → predicate."""
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        target = float(c)
        return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == target
    if isinstance(c, bool):
        return lambda v: v is c
    s = _text(c) if c is not None else ""
    m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", s, re.S)
    op, rhs = m.group(1) or "=", m.group(2)
    try:
        rnum = float(rhs.replace(",", ""))
        is_num = rhs.strip() != ""
    except ValueError:
        rnum, is_num = None, False
    if is_num:
        def pred(v):
            if isinstance(v, bool) or v is None or isinstance(v, XLError):
                return op == "<>" and not isinstance(v, XLError)
            if isinstance(v, str):
                try:
                    vv = float(v)
                except ValueError:
                    return op == "<>"
            else:
                vv = float(v)
            return {"=": vv == rnum, "<>": vv != rnum, "<": vv < rnum, ">": vv > rnum, "<=": vv <= rnum, ">=": vv >= rnum}[op]
        return pred
    if op in ("=", "<>"):
        if rhs == "":
            return (lambda v: v is None or v == "") if op == "=" else (lambda v: not (v is None or v == ""))
        pat = re.compile("^" + "".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in _unescape_tilde(rhs)) + "$", re.I | re.S)
        if op == "=":
            return lambda v: isinstance(v, str) and bool(pat.match(v))
        return lambda v: not (isinstance(v, str) and bool(pat.match(v)))
    low = rhs.lower()
    return lambda v: isinstance(v, str) and {"<": v.lower() < low, ">": v.lower() > low, "<=": v.lower() <= low, ">=": v.lower() >= low}[op]


def _unescape_tilde(s: str) -> str:
    return s.replace("~*", "\0a").replace("~?", "\0b").replace("\0a", "*").replace("\0b", "?")


def _flat_same(ranges):
    lists = [_rng_list(r) for r in ranges]
    n = len(lists[0])
    if any(len(x) != n for x in lists):
        raise _Raise(VALUE)
    return lists


def f_sum(b, a):
    return float(sum(_nums_for_agg(a)))


def f_average(b, a):
    xs = _nums_for_agg(a)
    if not xs:
        raise _Raise(DIV0)
    return sum(xs) / len(xs)


def f_count(b, a):
    n = 0
    for x in a:
        for v in _rng_list(x):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                n += 1
            elif not isinstance(x, Range) and v is not None and not isinstance(v, XLError):
                try:
                    _num(v)
                    n += 1
                except _Raise:
                    pass
    return float(n)


def f_counta(b, a):
    return float(sum(1 for x in a for v in _rng_list(x) if v is not None and v != ""))


def f_countblank(b, a):
    return float(sum(1 for x in a for v in _rng_list(x) if v is None or v == ""))


def f_minmax(fn):
    def f(b, a):
        xs = _nums_for_agg(a)
        return float(fn(xs)) if xs else 0.0
    return f


def f_product(b, a):
    xs = _nums_for_agg(a)
    return float(math.prod(xs)) if xs else 0.0


def f_sumproduct(b, a):
    lists = _flat_same(a)
    tot = 0.0
    for vals in zip(*lists):
        prod = 1.0
        for v in vals:
            if isinstance(v, XLError):
                raise _Raise(v)
            prod *= float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        tot += prod
    return tot


def f_sumif(b, a):
    rng, crit = a[0], _scalar(a[1])
    sumr = a[2] if len(a) > 2 and a[2] is not None else rng
    p = _criteria(crit)
    cells, sums = _rng_list(rng), _rng_list(sumr)
    return float(sum(float(s) for v, s in zip(cells, sums) if p(v) and isinstance(s, (int, float)) and not isinstance(s, bool)))


def _same_size(first: list, preds: list) -> None:
    # every range of a *IFS function must have the size of the first: #VALUE! otherwise
    if any(len(r) != len(first) for r, _ in preds):
        raise _Raise(VALUE)


def f_sumifs(b, a):
    sumr = _rng_list(a[0])
    preds = [(_rng_list(a[i]), _criteria(_scalar(a[i + 1]))) for i in range(1, len(a), 2)]
    _same_size(sumr, preds)
    tot = 0.0
    for idx, s in enumerate(sumr):
        if all(p(r[idx]) for r, p in preds) and isinstance(s, (int, float)) and not isinstance(s, bool):
            tot += float(s)
    return tot


def f_countif(b, a):
    p = _criteria(_scalar(a[1]))
    return float(sum(1 for v in _rng_list(a[0]) if p(v)))


def f_countifs(b, a):
    preds = [(_rng_list(a[i]), _criteria(_scalar(a[i + 1]))) for i in range(0, len(a), 2)]
    _same_size(preds[0][0], preds)
    n = len(preds[0][0])
    return float(sum(1 for idx in range(n) if all(p(r[idx]) for r, p in preds)))


def f_averageif(b, a):
    rng, p = a[0], _criteria(_scalar(a[1]))
    avg = a[2] if len(a) > 2 else rng
    xs = [float(s) for v, s in zip(_rng_list(rng), _rng_list(avg)) if p(v) and isinstance(s, (int, float)) and not isinstance(s, bool)]
    if not xs:
        raise _Raise(DIV0)
    return sum(xs) / len(xs)


def f_averageifs(b, a):
    avg = _rng_list(a[0])
    preds = [(_rng_list(a[i]), _criteria(_scalar(a[i + 1]))) for i in range(1, len(a), 2)]
    _same_size(avg, preds)
    xs = [float(s) for idx, s in enumerate(avg) if all(p(r[idx]) for r, p in preds) and isinstance(s, (int, float)) and not isinstance(s, bool)]
    if not xs:
        raise _Raise(DIV0)
    return sum(xs) / len(xs)


def f_round(kind):
    def f(b, a):
        x = _num(_scalar(a[0]))
        n = int(_num(_scalar(a[1]))) if len(a) > 1 else 0
        if kind == "round":
            return _round_half_away(x, n)
        return _round_dec(x, n, ROUND_UP if kind == "up" else ROUND_DOWN)     # away from / towards zero
    return f


def f_int(b, a):
    return float(math.floor(_num(_scalar(a[0]))))


def f_trunc(b, a):
    x = _num(_scalar(a[0]))
    n = int(_num(_scalar(a[1]))) if len(a) > 1 else 0
    return _round_dec(x, n, ROUND_DOWN)


def f_mod(b, a):
    x, y = _num(_scalar(a[0])), _num(_scalar(a[1]))
    if y == 0:
        raise _Raise(DIV0)
    return x - y * math.floor(x / y)


def f_sqrt(b, a):
    x = _num(_scalar(a[0]))
    if x < 0:
        raise _Raise(NUM)
    return math.sqrt(x)


def f_power(b, a):
    x, y = _num(_scalar(a[0])), _num(_scalar(a[1]))
    if x == 0 and y < 0:
        raise _Raise(DIV0)
    try:
        r = x ** y
    except OverflowError:
        raise _Raise(NUM)
    if isinstance(r, complex):          # a fractional power of a negative number
        raise _Raise(NUM)
    return r


def f_ceiling(b, a):
    x = _num(_scalar(a[0]))
    s = _num(_scalar(a[1])) if len(a) > 1 else 1.0
    if s == 0:
        return 0.0
    return math.ceil(x / s - 1e-12) * s


def f_floor(b, a):
    x = _num(_scalar(a[0]))
    s = _num(_scalar(a[1])) if len(a) > 1 else 1.0
    if s == 0:
        raise _Raise(DIV0)
    return math.floor(x / s + 1e-12) * s


def f_abs(b, a):
    return abs(_num(_scalar(a[0])))


def f_sign(b, a):
    x = _num(_scalar(a[0]))
    return float((x > 0) - (x < 0))


def f_pi(b, a):
    return math.pi


def f_median(b, a):
    xs = sorted(_nums_for_agg(a))
    if not xs:
        raise _Raise(NUM)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def f_large_small(kind):
    def f(b, a):
        xs = sorted(_nums_for_agg([a[0]]), reverse=(kind == "large"))
        k = int(_num(_scalar(a[1])))
        if k < 1 or k > len(xs):
            raise _Raise(NUM)
        return xs[k - 1]
    return f


def _exact(key):
    """Exact-match predicate of VLOOKUP / HLOOKUP / MATCH(…,0): type-strict equality (text
    "5" is not the number 5), case-insensitive for text, with Excel's * ? ~ wildcards — but
    no comparison operators: a key "">1"" is looked up as the text >1, not as a condition."""
    if isinstance(key, XLError):
        raise _Raise(key)
    if isinstance(key, str):
        if any(ch in key for ch in "*?~"):
            pat = _wildcard(key)
            return lambda v: isinstance(v, str) and bool(pat.match(v))
        low = key.lower()
        return lambda v: isinstance(v, str) and v.lower() == low
    if isinstance(key, bool):
        return lambda v: isinstance(v, bool) and v is key
    if key is None:
        key = 0.0
    k = float(key)
    return lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and float(v) == k


def _wildcard(text: str):
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch == "~" and i + 1 < len(text) and text[i + 1] in "*?~":
            out.append(re.escape(text[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.I | re.S)


def _as_rows(a) -> list[list]:
    if isinstance(a, Range):
        return a.rows
    return [[a]]


def f_vlookup(b, a, horizontal=False):
    key = _scalar(a[0])
    table = _as_rows(a[1])
    if horizontal:
        table = [list(col) for col in zip(*table)]
    idx = int(_num(_scalar(a[2])))
    approx = True if len(a) < 4 or a[3] is None else _bool(_scalar(a[3]))
    if idx < 1 or (table and idx > len(table[0])):
        raise _Raise(REF)
    if not approx:
        p = _exact(key)
        for row in table:
            if row and p(row[0]):
                return row[idx - 1]
        raise _Raise(NA)
    best = None
    for row in table:
        v = row[0]
        if v is None:
            continue
        try:
            if _compare("<=", v, key):
                best = row
            else:
                break
        except _Raise:
            continue
    if best is None:
        raise _Raise(NA)
    return best[idx - 1]


def f_match(b, a):
    key = _scalar(a[0])
    vals = _rng_list(a[1])
    mtype = int(_num(_scalar(a[2]))) if len(a) > 2 and a[2] is not None else 1
    if mtype == 0:
        p = _exact(key)
        for i, v in enumerate(vals):
            if p(v):
                return float(i + 1)
        raise _Raise(NA)
    best = None
    for i, v in enumerate(vals):
        if v is None:
            continue
        ok = _compare("<=", v, key) if mtype == 1 else _compare(">=", v, key)
        if ok:
            best = i
        else:
            break
    if best is None:
        raise _Raise(NA)
    return float(best + 1)


def f_index(b, a):
    rows = _as_rows(a[0])
    r = int(_num(_scalar(a[1]))) if len(a) > 1 and a[1] is not None else 0
    c = int(_num(_scalar(a[2]))) if len(a) > 2 and a[2] is not None else 0
    if r < 0 or c < 0:
        raise _Raise(VALUE)            # a negative index used to pick a row from the end
    if len(rows) == 1 and c == 0 and len(a) == 2:
        c, r = r, 1
    if r > len(rows) or (rows and c > len(rows[0])):
        raise _Raise(REF)
    if r == 0 or c == 0:
        if r == 0 and c == 0:
            raise Unsupported("INDEX 返回整块区域")
        if r == 0:
            return Range([[row[c - 1]] for row in rows])
        return Range([rows[r - 1]])
    try:
        return rows[r - 1][c - 1]
    except IndexError:
        raise _Raise(REF)


def f_xlookup(b, a):
    key = _scalar(a[0])
    look = _rng_list(a[1])
    ret = _as_rows(a[2])
    flat_ret = [row[0] for row in ret] if len(ret) == len(look) else (ret[0] if ret and len(ret[0]) == len(look) else None)
    if flat_ret is None:
        raise _Raise(VALUE)
    match_mode = int(_num(_scalar(a[4]))) if len(a) > 4 and a[4] is not None else 0
    search_mode = int(_num(_scalar(a[5]))) if len(a) > 5 and a[5] is not None else 1
    if match_mode not in (0, -1, 1, 2) or search_mode not in (1, -1, 2, -2):
        raise _Raise(VALUE)
    if search_mode in (2, -2):
        raise Unsupported("XLOOKUP 二分查找模式")     # its result on unsorted data is not guessed
    order = range(len(look)) if search_mode == 1 else range(len(look) - 1, -1, -1)
    if match_mode == 2:
        p = _exact(key)                              # wildcards only in match_mode 2
    else:
        k = key
        if isinstance(k, str):
            low = k.lower()
            p = lambda v: isinstance(v, str) and v.lower() == low  # noqa: E731
        else:
            p = _exact(k)
    for i in order:
        if p(look[i]):
            return flat_ret[i]
    if match_mode in (-1, 1):
        # exact or the next smaller (-1) / larger (1) item, compared in Excel's order
        best = None
        for i in order:
            v = look[i]
            if v is None or isinstance(v, XLError) or _cmp_key(v)[0] != _cmp_key(key)[0]:
                continue
            if (match_mode == -1 and _compare("<", v, key)) or (match_mode == 1 and _compare(">", v, key)):
                if best is None or (match_mode == -1 and _compare(">", v, look[best])) or \
                        (match_mode == 1 and _compare("<", v, look[best])):
                    best = i
        if best is not None:
            return flat_ret[best]
    if len(a) > 3 and a[3] is not None:
        return a[3]
    raise _Raise(NA)


def f_concat(b, a):
    return "".join(_text(v) for x in a for v in _rng_list(x))


def f_textjoin(b, a):
    sep = _text(_scalar(a[0]))
    ignore = _bool(_scalar(a[1]))
    parts = [_text(v) for x in a[2:] for v in _rng_list(x)]
    if ignore:
        parts = [p for p in parts if p != ""]
    return sep.join(parts)


def f_left(b, a):
    n = int(_num(_scalar(a[1]))) if len(a) > 1 else 1
    if n < 0:
        raise _Raise(VALUE)
    return _text(_scalar(a[0]))[:n]


def f_right(b, a):
    n = int(_num(_scalar(a[1]))) if len(a) > 1 else 1
    if n < 0:
        raise _Raise(VALUE)
    s = _text(_scalar(a[0]))
    return s[max(0, len(s) - n):] if n > 0 else ""        # RIGHT("abc", 5) is "abc", not "bc"


def f_mid(b, a):
    s = _text(_scalar(a[0]))
    start, n = int(_num(_scalar(a[1]))), int(_num(_scalar(a[2])))
    if start < 1 or n < 0:
        raise _Raise(VALUE)
    return s[start - 1:start - 1 + n]


def f_len(b, a):
    return float(len(_text(_scalar(a[0]))))


def f_trim(b, a):
    return re.sub(r" +", " ", _text(_scalar(a[0])).strip(" "))


def f_substitute(b, a):
    s, old, new = _text(_scalar(a[0])), _text(_scalar(a[1])), _text(_scalar(a[2]))
    if len(a) > 3:
        n = int(_num(_scalar(a[3])))
        if n < 1:
            raise _Raise(VALUE)
        idx = -1
        for _ in range(n):
            idx = s.find(old, idx + 1)
            if idx < 0:
                return s
        return s[:idx] + new + s[idx + len(old):]
    return s.replace(old, new) if old else s


def f_replace(b, a):
    s = _text(_scalar(a[0]))
    start, n, new = int(_num(_scalar(a[1]))), int(_num(_scalar(a[2]))), _text(_scalar(a[3]))
    if start < 1 or n < 0:
        raise _Raise(VALUE)
    return s[:start - 1] + new + s[start - 1 + n:]


def f_find(case: bool):
    def f(b, a):
        needle, hay = _text(_scalar(a[0])), _text(_scalar(a[1]))
        start = int(_num(_scalar(a[2]))) if len(a) > 2 else 1
        if case:
            i = hay.find(needle, start - 1)
        else:
            pat = re.compile("".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in needle), re.I)
            m = pat.search(hay, start - 1)
            i = m.start() if m else -1
        if i < 0:
            raise _Raise(VALUE)
        return float(i + 1)
    return f


def f_rept(b, a):
    return _text(_scalar(a[0])) * max(0, int(_num(_scalar(a[1]))))


def f_text(b, a):
    v, fmt = _scalar(a[0]), _text(_scalar(a[1]))
    if isinstance(v, str):
        try:
            v = float(v)
        except ValueError:
            return v
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    x = _num(v)
    text, _exact_fmt = format_value(x, fmt, b.date1904)      # a 1904 workbook counts days from 1904
    return text


def f_value(b, a):
    return _num(_scalar(a[0]))


def f_upper(b, a):
    return _text(_scalar(a[0])).upper()


def f_lower(b, a):
    return _text(_scalar(a[0])).lower()


def f_proper(b, a):
    return _text(_scalar(a[0])).title()


def f_exact(b, a):
    return _text(_scalar(a[0])) == _text(_scalar(a[1]))


def _date_of(b: Book, v) -> _dt.datetime:
    x = _num(_scalar(v))
    return serial_to_datetime(x, b.date1904)


def f_date(b, a):
    y, m, d = (int(_num(_scalar(x))) for x in a[:3])
    if y < 1900:
        y += 1900
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    base = _dt.date(y, m, 1) + _dt.timedelta(days=d - 1)
    return datetime_to_serial(base, b.date1904)


def f_part(kind):
    def f(b, a):
        d = _date_of(b, a[0])
        return float(getattr(d, kind))
    return f


def f_weekday(b, a):
    d = _date_of(b, a[0])
    t = int(_num(_scalar(a[1]))) if len(a) > 1 else 1
    wd = d.isoweekday()  # Mon=1..Sun=7
    if t == 1:
        return float(wd % 7 + 1)
    if t == 2:
        return float(wd)
    if t == 3:
        return float(wd - 1)
    raise _Raise(NUM)


def f_today(b, a):
    return datetime_to_serial(_dt.date.today(), b.date1904)


def f_now(b, a):
    return datetime_to_serial(_dt.datetime.now(), b.date1904)


def f_edate(b, a):
    d = _date_of(b, a[0]).date()
    n = int(_num(_scalar(a[1])))
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return datetime_to_serial(_dt.date(y, m, day), b.date1904)


def f_eomonth(b, a):
    import calendar
    d = _date_of(b, a[0]).date()
    n = int(_num(_scalar(a[1])))
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return datetime_to_serial(_dt.date(y, m, calendar.monthrange(y, m)[1]), b.date1904)


def f_datedif(b, a):
    d1, d2 = _date_of(b, a[0]).date(), _date_of(b, a[1]).date()
    unit = _text(_scalar(a[2])).upper()
    if d1 > d2:
        raise _Raise(NUM)
    if unit == "D":
        return float((d2 - d1).days)
    months = (d2.year - d1.year) * 12 + d2.month - d1.month - (1 if d2.day < d1.day else 0)
    if unit == "M":
        return float(months)
    if unit == "Y":
        return float(months // 12)
    raise Unsupported(f"DATEDIF 单位 {unit}")


def f_days(b, a):
    return _num(_scalar(a[0])) - _num(_scalar(a[1]))


def f_is(kind):
    def f(b, a):
        v = _scalar(a[0]) if not isinstance(a[0], Range) or a[0].shape == (1, 1) else a[0]
        if kind == "blank":
            return v is None
        if kind == "number":
            return isinstance(v, (int, float)) and not isinstance(v, bool)
        if kind == "text":
            return isinstance(v, str)
        if kind == "error":
            return isinstance(v, XLError)
        if kind == "err":
            return isinstance(v, XLError) and v != NA
        if kind == "na":
            return v == NA
        if kind == "logical":
            return isinstance(v, bool)
        if kind == "even":
            return int(_num(v)) % 2 == 0
        if kind == "odd":
            return int(_num(v)) % 2 == 1
        return False
    return f


def f_not(b, a):
    return not _bool(_scalar(a[0]))


def f_n(b, a):
    v = _scalar(a[0])
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    return float(v) if isinstance(v, (int, float)) else 0.0


def f_t(b, a):
    v = _scalar(a[0])
    return v if isinstance(v, str) else ""


def f_na(b, a):
    return NA


def f_rows(b, a):
    return float(len(_as_rows(a[0])))


def f_columns(b, a):
    rows = _as_rows(a[0])
    return float(len(rows[0]) if rows else 0)


_FUNCS = {
    "SUM": f_sum, "AVERAGE": f_average, "COUNT": f_count, "COUNTA": f_counta, "COUNTBLANK": f_countblank,
    "MIN": f_minmax(min), "MAX": f_minmax(max), "PRODUCT": f_product, "SUMPRODUCT": f_sumproduct,
    "SUMIF": f_sumif, "SUMIFS": f_sumifs, "COUNTIF": f_countif, "COUNTIFS": f_countifs,
    "AVERAGEIF": f_averageif, "AVERAGEIFS": f_averageifs, "MEDIAN": f_median, "LARGE": f_large_small("large"),
    "SMALL": f_large_small("small"),
    "ROUND": f_round("round"), "ROUNDUP": f_round("up"), "ROUNDDOWN": f_round("down"), "INT": f_int, "TRUNC": f_trunc,
    "MOD": f_mod, "SQRT": f_sqrt, "POWER": f_power, "CEILING": f_ceiling, "FLOOR": f_floor, "CEILING.MATH": f_ceiling,
    "FLOOR.MATH": f_floor, "ABS": f_abs, "SIGN": f_sign, "PI": f_pi,
    "VLOOKUP": f_vlookup, "HLOOKUP": lambda b, a: f_vlookup(b, a, horizontal=True), "MATCH": f_match, "INDEX": f_index,
    "XLOOKUP": f_xlookup,
    "CONCATENATE": f_concat, "CONCAT": f_concat, "TEXTJOIN": f_textjoin, "LEFT": f_left, "RIGHT": f_right, "MID": f_mid,
    "LEN": f_len, "TRIM": f_trim, "SUBSTITUTE": f_substitute, "REPLACE": f_replace, "FIND": f_find(True),
    "SEARCH": f_find(False), "REPT": f_rept, "TEXT": f_text, "VALUE": f_value, "UPPER": f_upper, "LOWER": f_lower,
    "PROPER": f_proper, "EXACT": f_exact,
    "DATE": f_date, "YEAR": f_part("year"), "MONTH": f_part("month"), "DAY": f_part("day"), "HOUR": f_part("hour"),
    "MINUTE": f_part("minute"), "SECOND": f_part("second"), "WEEKDAY": f_weekday, "TODAY": f_today, "NOW": f_now,
    "EDATE": f_edate, "EOMONTH": f_eomonth, "DATEDIF": f_datedif, "DAYS": f_days,
    "ISEVEN": f_is("even"), "ISODD": f_is("odd"),
    "NOT": f_not, "N": f_n, "T": f_t, "NA": f_na, "TRUE": lambda b, a: True, "FALSE": lambda b, a: False,
    "ROWS": f_rows, "COLUMNS": f_columns,
}

SUPPORTED_FUNCTIONS = sorted(set(_FUNCS) | set(_LAZY))


# ====================================================================== workbook integration
def book_from_xlsx(x) -> Book:
    """Build a :class:`Book` from a synwevo_docs.engine.xlsx_doc.XlsxDoc (after edits)."""
    from .xlsx_doc import _CELLREF, s as S
    x.expand_shared_formulas()
    cells: dict[str, dict[tuple[int, int], tuple]] = {}
    sst = x.shared_strings()
    for sh in x.sheets:
        root = x.sheet_root(sh["name"])
        d: dict[tuple[int, int], tuple] = {}
        for c in root.iter(S("c")):
            m = _CELLREF.match(c.get("r") or "")
            if not m:
                continue
            key = (int(m.group(2)) - 1, col_index(m.group(1)))
            f = c.find(S("f"))
            if f is not None and f.get("t") == "array":
                d[key] = ("f!", f.text or "")
                continue
            if f is not None and f.text:
                d[key] = ("f", f.text)
                continue
            t = c.get("t")
            v = c.find(S("v"))
            if t == "s" and v is not None and v.text is not None:
                i = int(v.text)
                d[key] = ("v", sst[i] if 0 <= i < len(sst) else "")
            elif t == "inlineStr":
                d[key] = ("v", "".join(x_.text or "" for x_ in c.iter(S("t"))))
            elif t in ("str",):
                d[key] = ("v", v.text if v is not None else "")
            elif t == "b":
                d[key] = ("v", v is not None and v.text == "1")
            elif t == "e":
                d[key] = ("v", ERRORS.get((v.text or "").upper(), VALUE) if v is not None else VALUE)
            elif v is not None and v.text not in (None, ""):
                try:
                    d[key] = ("v", float(v.text))
                except ValueError:
                    d[key] = ("v", v.text)
        cells[sh["name"]] = d
    names = {}
    dn = x.wb.find(S("definedNames"))
    if dn is not None:
        for el in dn.findall(S("definedName")):
            nm = el.get("name") or ""
            if nm.startswith("_xlnm.") or nm.startswith("_dw_") or el.get("localSheetId") is not None:
                continue
            names[nm] = el.text or ""
    return Book(cells, names, x.date1904)


def compute_workbook(x) -> dict:
    """Evaluate every formula of XlsxDoc ``x`` and write cached values into the XML.

    Returns {'computed': n, 'unsupported': [(sheet, ref, reason)], 'errors': [(sheet, ref, code)]}."""
    from lxml import etree

    from .xlsx_doc import _CELLREF, s as S
    from .values import float_repr
    book = book_from_xlsx(x)
    report = {"computed": 0, "unsupported": [], "errors": []}
    for sh in x.sheets:
        root = x.sheet_root(sh["name"])
        for c in root.iter(S("c")):
            f = c.find(S("f"))
            if f is None:
                continue
            ref = c.get("r")
            m = _CELLREF.match(ref or "")
            key = (int(m.group(2)) - 1, col_index(m.group(1)))
            for old in c.findall(S("v")):
                c.remove(old)
            if f.get("t") == "array" or book.cells[sh["name"]].get(key, ("",))[0] == "f!":
                report["unsupported"].append((sh["name"], ref, "数组公式"))
                if "t" in c.attrib and c.get("t") != "s":
                    del c.attrib["t"]
                continue
            try:
                val = book.value(sh["name"], key[0], key[1])
            except Unsupported as e:
                report["unsupported"].append((sh["name"], ref, str(e)))
                if "t" in c.attrib:
                    del c.attrib["t"]
                continue
            except Exception as e:  # noqa: BLE001 - one cell the engine cannot evaluate is not a failed document
                report["unsupported"].append((sh["name"], ref, f"内置引擎无法计算（{type(e).__name__}）"))
                if "t" in c.attrib:
                    del c.attrib["t"]
                continue
            v = etree.SubElement(c, S("v"))
            if isinstance(val, XLError):
                c.set("t", "e")
                v.text = val.code
                report["errors"].append((sh["name"], ref, val.code))
            elif isinstance(val, bool):
                c.set("t", "b")
                v.text = "1" if val else "0"
            elif isinstance(val, (int, float)):
                if "t" in c.attrib:
                    del c.attrib["t"]
                v.text = float_repr(float(val))
            elif val is None:
                if "t" in c.attrib:
                    del c.attrib["t"]
                v.text = "0"
            else:
                c.set("t", "str")
                v.text = str(val)
            report["computed"] += 1
    return report


def evaluate_cells(data: bytes, wanted: list[tuple[str, str]]) -> dict[tuple[str, str], object]:
    """Evaluate specific formula cells of an XLSX (used for source files saved without cached values)."""
    from .xlsx_doc import XlsxDoc
    from .tabular import parse_cell
    x = XlsxDoc(data)
    book = book_from_xlsx(x)
    out: dict[tuple[str, str], object] = {}
    for sheet, ref in wanted:
        r, c = parse_cell(ref)
        try:
            out[(sheet, ref)] = book.value(sheet, r, c)
        except Unsupported as e:
            out[(sheet, ref)] = Unsupported(str(e))
    return out
