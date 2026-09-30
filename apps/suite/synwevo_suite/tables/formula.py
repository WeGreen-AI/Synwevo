"""Formulas of Synwevo Tables: a small, closed expression language over one record's fields.

A formula is text a person typed, so it is treated like any other untrusted input: it is read by a
tokenizer that knows only the characters of this language, parsed by a recursive-descent parser
into a tree of plain tuples, type-checked, and evaluated by walking that tree. Nothing reaches
``eval``/``exec``, there is no attribute access (``.`` exists only inside a number), no indexing,
and a function is one of the names in :data:`FUNCTIONS` or the formula is refused.

Grammar::

    expr    := compare
    compare := concat (("=" | "!=" | "<>" | "<" | "<=" | ">" | ">=") concat)?
    concat  := add ("&" add)*
    add     := mul (("+" | "-") mul)*
    mul     := unary (("*" | "/") unary)*
    unary   := "-" unary | primary
    primary := NUMBER | "text" | TRUE | FALSE | {field} | NAME "(" args ")" | "(" expr ")"

``{field}`` is a field of the same table, by name while typing; stored by its id (``{fld_…}``) so a
rename never breaks it. Arithmetic is exact decimal arithmetic. A blank operand makes arithmetic
blank; division by zero, an overflowing number or text that grows too long is an error value for
that record (kept with the record, shown in the grid), never an exception for the request.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_UP, localcontext

from .fields import CellError, choice_index, quantize

MAX_LENGTH = 2000
MAX_DEPTH = 40
MAX_NODES = 400
MAX_TEXT = 10000
MAX_MAGNITUDE = Decimal(10) ** 30

#: name → (minimum, maximum) number of arguments
FUNCTIONS = {"IF": (3, 3), "ROUND": (1, 2), "AND": (1, 10), "OR": (1, 10), "NOT": (1, 1), "CONCAT": (1, 20),
             "ABS": (1, 1), "ISBLANK": (1, 1)}

#: What each field type is inside a formula; types missing here cannot be used in one.
VALUE_TYPE = {"text": "text", "long_text": "text", "email": "text", "url": "text", "phone": "text",
              "single_select": "text", "date": "text", "datetime": "text", "number": "number",
              "money": "number", "boolean": "boolean"}

_TOKEN = re.compile(r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?)
  | (?P<str>"(?:[^"\\\n]|\\["\\])*")
  | (?P<field>\{[^{}\n]{1,120}\})
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|!=|<>|[-+*/&=<>(),])
""", re.X)


class FormulaError(Exception):
    """A formula that cannot be saved (at parse / check time) or a value that cannot be computed
    (at evaluation time). ``code`` is stable; ``params`` say where."""

    def __init__(self, code: str, **params):
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass
class Compiled:
    tree: tuple
    deps: list[str]              # field ids, in order of first use
    result_type: str             # number | text | boolean
    expression: str              # canonical: field references by id


# ============================================================================ tokens
def tokenize(src: str) -> list[tuple[str, str, int]]:
    if len(src) > MAX_LENGTH:
        raise FormulaError("formula_too_long", max=MAX_LENGTH)
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise FormulaError("formula_syntax", position=pos, near=src[pos:pos + 10])
        kind = m.lastgroup
        if kind != "ws":
            out.append((kind, m.group(), pos))
        pos = m.end()
    out.append(("eof", "", len(src)))
    return out


# ============================================================================ parse
class _Parser:
    def __init__(self, tokens, resolve):
        self.t = tokens
        self.i = 0
        self.resolve = resolve
        self.nodes = 0
        self.refs: list[tuple[int, int, str]] = []       # (start, end, field id) for the canonical text

    def peek(self):
        return self.t[self.i]

    def take(self, kind=None, value=None):
        tok = self.t[self.i]
        if (kind and tok[0] != kind) or (value is not None and tok[1] != value):
            raise FormulaError("formula_syntax", position=tok[2], near=tok[1][:10] or "end",
                               expected=value or kind)
        self.i += 1
        return tok

    def node(self, n, depth):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FormulaError("formula_too_complex", max_nodes=MAX_NODES)
        if depth > MAX_DEPTH:
            raise FormulaError("formula_too_complex", max_depth=MAX_DEPTH)
        return n

    def expr(self, d=0):
        if d > MAX_DEPTH:
            raise FormulaError("formula_too_complex", max_depth=MAX_DEPTH)
        left = self.concat(d + 1)
        tok = self.peek()
        if tok[0] == "op" and tok[1] in ("=", "!=", "<>", "<", "<=", ">", ">="):
            self.i += 1
            op = "!=" if tok[1] == "<>" else tok[1]
            right = self.concat(d + 1)
            left = self.node(("bin", op, left, right), d)
            nxt = self.peek()
            if nxt[0] == "op" and nxt[1] in ("=", "!=", "<>", "<", "<=", ">", ">="):
                raise FormulaError("formula_syntax", position=nxt[2], near=nxt[1], expected="one comparison")
        return left

    def concat(self, d):
        left = self.add(d + 1)
        while self.peek()[:2] == ("op", "&"):
            self.i += 1
            left = self.node(("bin", "&", left, self.add(d + 1)), d)
        return left

    def add(self, d):
        left = self.mul(d + 1)
        while self.peek()[0] == "op" and self.peek()[1] in "+-" and len(self.peek()[1]) == 1:
            op = self.take()[1]
            left = self.node(("bin", op, left, self.mul(d + 1)), d)
        return left

    def mul(self, d):
        left = self.unary(d + 1)
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            left = self.node(("bin", op, left, self.unary(d + 1)), d)
        return left

    def unary(self, d):
        if d > MAX_DEPTH:
            raise FormulaError("formula_too_complex", max_depth=MAX_DEPTH)
        if self.peek()[:2] == ("op", "-"):
            self.i += 1
            return self.node(("neg", self.unary(d + 1)), d)
        return self.primary(d + 1)

    def primary(self, d):
        kind, text, pos = self.peek()
        if kind == "num":
            self.i += 1
            if len(text.replace(".", "")) > 30:
                raise FormulaError("number_too_large", position=pos)
            return self.node(("num", Decimal(text)), d)
        if kind == "str":
            self.i += 1
            value = text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            return self.node(("str", value), d)
        if kind == "field":
            self.i += 1
            fid = self.resolve(text[1:-1].strip(), pos)
            self.refs.append((pos, pos + len(text), fid))
            return self.node(("field", fid), d)
        if kind == "name":
            upper = text.upper()
            if upper in ("TRUE", "FALSE") and self.t[self.i + 1][:2] != ("op", "("):
                self.i += 1
                return self.node(("bool", upper == "TRUE"), d)
            if self.t[self.i + 1][:2] != ("op", "("):
                raise FormulaError("unknown_name", position=pos, name=text[:40])
            if upper not in FUNCTIONS:
                raise FormulaError("unknown_function", position=pos, name=text[:40])
            self.i += 2
            args = []
            if self.peek()[:2] != ("op", ")"):
                args.append(self.expr(d + 1))
                while self.peek()[:2] == ("op", ","):
                    self.i += 1
                    args.append(self.expr(d + 1))
            self.take("op", ")")
            lo, hi = FUNCTIONS[upper]
            if not lo <= len(args) <= hi:
                raise FormulaError("wrong_argument_count", position=pos, name=upper, min=lo, max=hi)
            return self.node(("call", upper, tuple(args)), d)
        if (kind, text) == ("op", "("):
            self.i += 1
            inner = self.expr(d + 1)
            self.take("op", ")")
            return inner
        raise FormulaError("formula_syntax", position=pos, near=text[:10] or "end")


def compile_formula(src: str, fields: list[dict], *, self_id: str | None = None) -> Compiled:
    """Parse and check ``src`` against the table's active ``fields``. Refuses references to missing
    fields, to fields of a type a formula cannot use, and to the formula itself; cycles through
    other formulas are checked by the caller, which knows every formula of the table."""
    by_id = {f["id"]: f for f in fields}
    by_name = {f["name"].casefold(): f for f in fields}

    def resolve(ref: str, pos: int) -> str:
        f = by_id.get(ref) or by_name.get(ref.casefold())
        if not f:
            raise FormulaError("unknown_field", position=pos, name=ref[:100])
        if f["id"] == self_id:
            raise FormulaError("formula_cycle", position=pos, fields=[f["id"]])
        if f["type"] != "formula" and f["type"] not in VALUE_TYPE:
            raise FormulaError("field_not_usable", position=pos, field=f["id"], type=f["type"])
        return f["id"]

    tokens = tokenize(src)
    p = _Parser(tokens, resolve)
    tree = p.expr()
    if p.peek()[0] != "eof":
        tok = p.peek()
        raise FormulaError("formula_syntax", position=tok[2], near=tok[1][:10])

    def type_of_field(fid: str) -> str:
        f = by_id[fid]
        if f["type"] == "formula":
            rt = f["config"].get("result_type")
            if not rt:
                raise FormulaError("field_not_usable", field=fid, type="formula")
            return rt
        return VALUE_TYPE[f["type"]]

    rtype = check(tree, type_of_field)
    canonical, last = [], 0
    for start, end, fid in p.refs:
        canonical.append(src[last:start])
        canonical.append("{" + fid + "}")
        last = end
    canonical.append(src[last:])
    deps = list(dict.fromkeys(fid for _, _, fid in p.refs))
    return Compiled(tree=tree, deps=deps, result_type=rtype, expression="".join(canonical))


def display_expression(expression: str, fields: list[dict]) -> str:
    """The stored expression with field names instead of ids (what the person edits)."""
    names = {f["id"]: f["name"] for f in fields}
    return re.sub(r"\{(fld_[a-f0-9]{12})\}", lambda m: "{" + names.get(m[1], m[1]) + "}", expression)


# ============================================================================ types
def check(node: tuple, type_of_field) -> str:
    kind = node[0]
    if kind == "num":
        return "number"
    if kind == "str":
        return "text"
    if kind == "bool":
        return "boolean"
    if kind == "field":
        return type_of_field(node[1])
    if kind == "neg":
        _want(check(node[1], type_of_field), "number", "-")
        return "number"
    if kind == "bin":
        op, a, b = node[1], check(node[2], type_of_field), check(node[3], type_of_field)
        if op in "+-*/":
            _want(a, "number", op)
            _want(b, "number", op)
            return "number"
        if op == "&":
            return "text"
        if op in ("=", "!="):
            if a != b:
                raise FormulaError("type_mismatch", operator=op, left=a, right=b)
            return "boolean"
        if a != b or a == "boolean":
            raise FormulaError("type_mismatch", operator=op, left=a, right=b)
        return "boolean"
    name, args = node[1], [check(x, type_of_field) for x in node[2]]
    if name == "IF":
        _want(args[0], "boolean", "IF")
        if args[1] != args[2]:
            raise FormulaError("type_mismatch", operator="IF", left=args[1], right=args[2])
        return args[1]
    if name == "ROUND":
        _want(args[0], "number", "ROUND")
        if len(args) == 2:
            places = node[2][1]
            if places[0] != "num" or places[1] != places[1].to_integral_value() or not 0 <= places[1] <= 10:
                raise FormulaError("bad_round_places", min=0, max=10)
        return "number"
    if name in ("AND", "OR", "NOT"):
        for a in args:
            _want(a, "boolean", name)
        return "boolean"
    if name == "ABS":
        _want(args[0], "number", "ABS")
        return "number"
    if name == "ISBLANK":
        return "boolean"
    return "text"                                            # CONCAT


def _want(got: str, want: str, where: str) -> None:
    if got != want:
        raise FormulaError("type_mismatch", operator=where, left=got, right=want)


# ============================================================================ evaluate
def formula_input(field: dict, value):
    """A stored cell as a formula sees it."""
    if value is None:
        return None
    t = field["type"]
    if t == "number":
        return Decimal(value)
    if t == "money":
        return Decimal(value["amount"])
    if t == "single_select":
        return choice_index(field["config"])[0].get(value)
    if t == "formula":
        rt = field["config"].get("result_type")
        return Decimal(value) if rt == "number" else value
    return value


def evaluate(compiled_tree: tuple, values: dict):
    """The value of a formula for one record; ``values`` maps field id → formula input."""
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        ctx.traps[Overflow] = True
        try:
            return _eval(compiled_tree, values)
        except (DivisionByZero, ZeroDivisionError):
            raise FormulaError("division_by_zero") from None
        except (InvalidOperation, Overflow):
            raise FormulaError("number_too_large") from None


def _num(v: Decimal) -> Decimal:
    if abs(v) >= MAX_MAGNITUDE:
        raise FormulaError("number_too_large")
    return v


def _text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v != 0 else "0"
    return str(v)


def _eval(node: tuple, values: dict):
    kind = node[0]
    if kind in ("num", "str", "bool"):
        return node[1]
    if kind == "field":
        return values.get(node[1])
    if kind == "neg":
        v = _eval(node[1], values)
        return None if v is None else _num(-v)
    if kind == "bin":
        op = node[1]
        a, b = _eval(node[2], values), _eval(node[3], values)
        if op == "&":
            out = _text(a) + _text(b)
            if len(out) > MAX_TEXT:
                raise FormulaError("text_too_long", max=MAX_TEXT)
            return out
        if op in "+-*/":
            if a is None or b is None:
                return None
            if op == "+":
                return _num(a + b)
            if op == "-":
                return _num(a - b)
            if op == "*":
                return _num(a * b)
            if b == 0:
                raise FormulaError("division_by_zero")
            return _num(a / b)
        if op == "=":
            return a == b
        if op == "!=":
            return a != b
        if a is None or b is None:
            return None
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    name, args = node[1], node[2]
    if name == "IF":
        cond = _eval(args[0], values)
        return _eval(args[1] if cond is True else args[2], values)
    if name == "AND":
        return all(_eval(a, values) is True for a in args)
    if name == "OR":
        return any(_eval(a, values) is True for a in args)
    if name == "NOT":
        v = _eval(args[0], values)
        return None if v is None else not v
    if name == "ISBLANK":
        v = _eval(args[0], values)
        return v is None or v == ""
    if name == "ABS":
        v = _eval(args[0], values)
        return None if v is None else abs(v)
    if name == "ROUND":
        v = _eval(args[0], values)
        places = int(args[1][1]) if len(args) == 2 else 0
        return None if v is None else v.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    out = "".join(_text(_eval(a, values)) for a in args)       # CONCAT
    if len(out) > MAX_TEXT:
        raise FormulaError("text_too_long", max=MAX_TEXT)
    return out


def store_result(result_type: str, decimals: int, value):
    """A computed value in its stored form (a number as an exact decimal string)."""
    if value is None:
        return None
    if result_type == "number":
        try:
            return quantize(value, decimals, strict=False)
        except CellError:
            raise FormulaError("number_too_large") from None
    if result_type == "boolean":
        return bool(value)
    return value if value != "" else None
