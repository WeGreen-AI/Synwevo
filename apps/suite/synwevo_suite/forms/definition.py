"""What a form is, and what an answer to it must be.

A definition is a list of fields with stable ids (``f_…``), optional steps and cross-field rules.
Everything a person or an automation submits is checked here, on the server, with the same rules
the fill-in page applies in the browser (`web/src/apps/forms/logic.ts`, kept in step by the shared
test vectors in `apps/suite/web/tests/forms-vectors.json`).

Rules that matter for data quality, stated once:

* **Hidden fields are cleared at submission.** A field whose ``visible_if`` is false when the form
  is submitted is not part of the submission, whatever was typed into it earlier. Drafts keep such
  values (so toggling an answer back restores them while filling); submissions never carry them,
  so an answer given under a branch the person later left cannot flow into business records.
* **Numbers and money are decimal strings.** ``"12.50"`` stays exactly that; no binary floating
  point touches an amount. A number with more decimals than the field allows is refused, not
  rounded.
* **Dates are ``YYYY-MM-DD``; date-times carry their UTC offset** (``2026-09-29T10:00:00+08:00``),
  so the moment is unambiguous wherever the answer is read.
* **Required checkboxes mean "must be ticked"** (an agreement); an unticked optional checkbox is
  ``false``.
* Values are not rewritten: text keeps its spacing and case. A value that is only whitespace is
  empty for the purpose of ``required``.
"""
from __future__ import annotations

import re
import secrets
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field as F, model_validator

FIELD_ID = r"^f_[a-z0-9]{4,24}$"
FieldType = Literal["text", "textarea", "number", "money", "date", "datetime", "select", "multiselect",
                    "boolean", "file", "repeat", "section"]
VALUE_TYPES = frozenset({"text", "textarea", "number", "money", "date", "datetime", "select", "multiselect",
                         "boolean", "file", "repeat"})
MAX_FIELDS = 200
MAX_SUBFIELDS = 40
MAX_ROWS = 200
MAX_TEXT = 10_000
CURRENCY = r"^[A-Z]{3}$"
#: Extensions a file field may accept (lower case, with the dot). Executables and scripts are not
#: offered at all: a form is for documents and pictures.
ACCEPTABLE = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".heic",
                        ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".txt", ".md",
                        ".odt", ".ods", ".zip"})
MAX_FILE_MB = 20
MAX_FILES_PER_FIELD = 10


def new_field_id() -> str:
    return "f_" + secrets.token_hex(4)


# ------------------------------------------------------------------------------ conditions
CondOp = Literal["eq", "neq", "in", "not_in", "filled", "empty", "gt", "gte", "lt", "lte", "contains"]


class Leaf(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = F(pattern=FIELD_ID)
    op: CondOp
    value: Any = None


class All(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: list["Condition"] = F(min_length=1, max_length=20)


class Any_(BaseModel):
    model_config = ConfigDict(extra="forbid")
    any: list["Condition"] = F(min_length=1, max_length=20)


class Not(BaseModel):
    model_config = ConfigDict(extra="forbid")
    not_: "Condition" = F(alias="not")


Condition = Annotated[Union[Leaf, All, Any_, Not], F(union_mode="left_to_right")]
All.model_rebuild()
Any_.model_rebuild()
Not.model_rebuild()


# ------------------------------------------------------------------------------ definition
class Option(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = F(min_length=1, max_length=100)
    label: str = F(min_length=1, max_length=200)


class Field(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = F(pattern=FIELD_ID)
    type: FieldType
    label: str = F(min_length=1, max_length=200)
    help: str = F(default="", max_length=2000)
    placeholder: str = F(default="", max_length=200)
    required: bool = False
    required_if: Condition | None = None
    visible_if: Condition | None = None
    # text / textarea
    format: Literal["plain", "email", "phone", "url", "pattern"] = "plain"
    pattern: str | None = F(default=None, max_length=100)
    pattern_hint: str = F(default="", max_length=200)
    min_length: int | None = F(default=None, ge=0, le=MAX_TEXT)
    max_length: int | None = F(default=None, ge=1, le=MAX_TEXT)
    # number / money
    min: str | None = F(default=None, max_length=40)
    max: str | None = F(default=None, max_length=40)
    decimals: int | None = F(default=None, ge=0, le=6)
    currency: str | None = F(default=None, pattern=CURRENCY)
    # select / multiselect
    options: list[Option] = F(default_factory=list, max_length=200)
    min_items: int | None = F(default=None, ge=0, le=MAX_ROWS)
    max_items: int | None = F(default=None, ge=1, le=MAX_ROWS)
    # file
    accept: list[str] = F(default_factory=list, max_length=len(ACCEPTABLE))
    max_files: int = F(default=1, ge=1, le=MAX_FILES_PER_FIELD)
    max_size_mb: int = F(default=10, ge=1, le=MAX_FILE_MB)
    # repeat
    fields: list["Field"] = F(default_factory=list, max_length=MAX_SUBFIELDS)


Field.model_rebuild()


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = F(pattern=r"^s_[a-z0-9]{1,24}$")
    title: str = F(min_length=1, max_length=200)
    fields: list[str] = F(default_factory=list, max_length=MAX_FIELDS)


class Rule(BaseModel):
    """A comparison between two answers, e.g. an end date not before the start date."""
    model_config = ConfigDict(extra="forbid")
    id: str = F(pattern=r"^r_[a-z0-9]{1,24}$")
    field: str = F(pattern=FIELD_ID)
    op: Literal["gt", "gte", "lt", "lte", "eq", "neq"]
    other: str = F(pattern=FIELD_ID)
    message: str = F(min_length=1, max_length=200)


class Definition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: list[Field] = F(default_factory=list, max_length=MAX_FIELDS)
    steps: list[Step] = F(default_factory=list, max_length=30)
    rules: list[Rule] = F(default_factory=list, max_length=50)
    confirmation: str = F(default="", max_length=2000)

    @model_validator(mode="after")
    def _ids_unique(self):
        seen: set[str] = set()
        for f in self.fields:
            for fid in [f.id, *(s.id for s in f.fields)]:
                if fid in seen:
                    raise ValueError(f"duplicate field id {fid}")
                seen.add(fid)
        return self


# ------------------------------------------------------------------------------ publishing
def problems(defn: Definition) -> list[dict]:
    """What stops this draft from being published. Empty means publishable.

    Each problem is ``{"code", "field"?, "detail"?}``; the editor translates the code.
    """
    out: list[dict] = []
    top_ids = [f.id for f in defn.fields]
    values = [f for f in defn.fields if f.type in VALUE_TYPES]
    if not values:
        out.append({"code": "no_fields"})
    order = {fid: i for i, fid in enumerate(top_ids)}

    def check_condition(cond, *, where: str, scope: dict[str, int], position: int, field: Field | None) -> None:
        for leaf in _leaves(cond):
            if leaf.field not in scope:
                out.append({"code": "condition_unknown_field", "field": where, "detail": {"refers_to": leaf.field}})
            elif scope[leaf.field] >= position:
                out.append({"code": "condition_forward_reference", "field": where, "detail": {"refers_to": leaf.field}})

    for i, f in enumerate(defn.fields):
        _field_problems(f, out)
        if f.visible_if is not None:
            check_condition(f.visible_if, where=f.id, scope=order, position=i, field=f)
        if f.required_if is not None:
            check_condition(f.required_if, where=f.id, scope=order, position=i, field=f)
        if f.type == "repeat":
            sub_order = {s.id: j for j, s in enumerate(f.fields)}
            if not f.fields:
                out.append({"code": "repeat_without_fields", "field": f.id})
            for j, s in enumerate(f.fields):
                if s.type in ("repeat", "section", "file"):
                    out.append({"code": "repeat_field_type", "field": s.id, "detail": {"type": s.type}})
                _field_problems(s, out)
                for cond in (s.visible_if, s.required_if):
                    if cond is not None:
                        check_condition(cond, where=s.id, scope=sub_order, position=j, field=s)
    if defn.steps:
        listed = [fid for s in defn.steps for fid in s.fields]
        if sorted(listed) != sorted(top_ids) or len(set(listed)) != len(listed):
            out.append({"code": "steps_must_cover_fields_once"})
        else:
            # A condition may only look back: across steps too.
            position = {fid: i for i, fid in enumerate(listed)}
            for f in defn.fields:
                for cond in (f.visible_if, f.required_if):
                    if cond is not None:
                        for leaf in _leaves(cond):
                            if leaf.field in position and position[leaf.field] >= position[f.id]:
                                out.append({"code": "condition_forward_reference", "field": f.id,
                                            "detail": {"refers_to": leaf.field}})
    by_id = {f.id: f for f in defn.fields}
    for r in defn.rules:
        a, b = by_id.get(r.field), by_id.get(r.other)
        if not a or not b:
            out.append({"code": "rule_unknown_field", "detail": {"rule": r.id}})
        elif a.type != b.type or a.type not in ("number", "money", "date", "datetime"):
            out.append({"code": "rule_incomparable", "detail": {"rule": r.id}})
    return out


def _field_problems(f: Field, out: list[dict]) -> None:
    if f.type in ("select", "multiselect"):
        if not f.options:
            out.append({"code": "options_required", "field": f.id})
        values = [o.value for o in f.options]
        if len(set(values)) != len(values):
            out.append({"code": "options_duplicate", "field": f.id})
    if f.format == "pattern":
        if not f.pattern:
            out.append({"code": "pattern_required", "field": f.id})
        elif not safe_pattern(f.pattern):
            out.append({"code": "pattern_unsafe", "field": f.id})
    if f.min_length is not None and f.max_length is not None and f.min_length > f.max_length:
        out.append({"code": "range_inverted", "field": f.id})
    if f.type in ("number", "money"):
        lo, hi = _dec(f.min), _dec(f.max)
        if (f.min is not None and lo is None) or (f.max is not None and hi is None):
            out.append({"code": "bound_not_a_number", "field": f.id})
        elif lo is not None and hi is not None and lo > hi:
            out.append({"code": "range_inverted", "field": f.id})
    if f.type == "money" and not f.currency:
        out.append({"code": "currency_required", "field": f.id})
    if f.min_items is not None and f.max_items is not None and f.min_items > f.max_items:
        out.append({"code": "range_inverted", "field": f.id})
    if f.type == "file":
        bad = [a for a in f.accept if a.lower() not in ACCEPTABLE]
        if bad:
            out.append({"code": "file_type_not_offered", "field": f.id, "detail": {"types": bad}})


_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*}][^)]*\)\s*[+*{]")


def safe_pattern(p: str) -> bool:
    """A pattern a form may use: it compiles, has no backreference and no quantified group that
    itself contains a quantifier (the shape of catastrophic backtracking)."""
    if len(p) > 100 or re.search(r"\\[1-9]|\(\?P=|\(\?<[=!]|\(\?[=!]", p) or _NESTED_QUANTIFIER.search(p):
        return False
    try:
        re.compile(p)
    except re.error:
        return False
    return True


def _leaves(cond) -> list[Leaf]:
    if cond is None:
        return []
    if isinstance(cond, Leaf):
        return [cond]
    if isinstance(cond, All):
        return [x for c in cond.all for x in _leaves(c)]
    if isinstance(cond, Any_):
        return [x for c in cond.any for x in _leaves(c)]
    if isinstance(cond, Not):
        return _leaves(cond.not_)
    return []


# ------------------------------------------------------------------------------ evaluation
def _dec(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, dict) and "amount" in v:
        v = v["amount"]
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict)):
        if isinstance(v, dict) and "amount" in v:
            return is_empty(v.get("amount"))
        return len(v) == 0
    return False


def _cmp_value(v):
    d = _dec(v)
    if d is not None:
        return d
    return v if isinstance(v, str) else None


def evaluate(cond, values: dict) -> bool:
    """Evaluate a condition against the answers visible so far (hidden fields count as empty)."""
    if cond is None:
        return True
    if isinstance(cond, All):
        return all(evaluate(c, values) for c in cond.all)
    if isinstance(cond, Any_):
        return any(evaluate(c, values) for c in cond.any)
    if isinstance(cond, Not):
        return not evaluate(cond.not_, values)
    v = values.get(cond.field)
    op = cond.op
    if op == "filled":
        return not is_empty(v)
    if op == "empty":
        return is_empty(v)
    if op in ("eq", "neq"):
        same = _equal(v, cond.value)
        return same if op == "eq" else not same
    if op in ("in", "not_in"):
        pool = cond.value if isinstance(cond.value, list) else [cond.value]
        hit = any(_equal(v, p) for p in pool)
        return hit if op == "in" else not hit
    if op == "contains":
        return isinstance(v, list) and any(_equal(x, cond.value) for x in v)
    a, b = _cmp_value(v), _cmp_value(cond.value)
    if a is None or b is None or type(a) is not type(b):
        return False
    return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]


def _equal(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b
    da, db = _dec(a), _dec(b)
    if da is not None and db is not None and not isinstance(a, list):
        return da == db
    if isinstance(a, dict) and "amount" in a:
        a = a.get("amount")
    return a == b


# ------------------------------------------------------------------------------ answers
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,63}$")
PHONE = re.compile(r"^\+?[0-9 ()\-]{5,30}$")
URL = re.compile(r"^https?://[^\s/$.?#][^\s]{0,2000}$", re.I)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _err(errors: list, field: str, code: str, *, row: int | None = None, parent: str | None = None, **params) -> None:
    e = {"field": field, "code": code}
    if parent is not None:
        e["parent"] = parent
        e["row"] = row
    if params:
        e["params"] = params
    errors.append(e)


def check_answers(defn: Definition, raw: dict, *, files: dict[str, dict] | None = None,
                  final: bool = True) -> tuple[dict, list[dict]]:
    """Normalize and check answers. Returns ``(answers, errors)``.

    ``answers`` contains only visible value fields (hidden ones are cleared — the rule in the
    module docstring). ``files`` maps each attachment id the filler may use to its record
    (``filename``, ``size``, ``field_id``); a file id not in it is refused. With ``final=False``
    (a draft), ``required`` is not enforced and nothing is cleared — only malformed values are
    reported, so a half-filled draft saves.
    """
    raw = raw if isinstance(raw, dict) else {}
    errors: list[dict] = []
    visible_values: dict = {}
    out: dict = {}
    for f in defn.fields:
        if f.type == "section":
            continue
        visible = evaluate(f.visible_if, visible_values)
        value = raw.get(f.id)
        if not final and f.id not in raw:
            continue                    # a draft or a prefill states only what it states
        if not visible:
            if not final:
                out[f.id] = value
            continue
        norm = _check_value(f, value, errors, files=files, final=final)
        if norm is not None or f.type == "boolean":
            visible_values[f.id] = norm
            if norm is not None:
                out[f.id] = norm
        required = f.required or (f.required_if is not None and evaluate(f.required_if, visible_values))
        if final and required and (is_empty(norm) or (f.type == "boolean" and norm is not True)):
            if not any(e["field"] == f.id for e in errors):
                _err(errors, f.id, "required")
    if final:
        for r in defn.rules:
            if r.field in out and r.other in out:
                a, b = _cmp_value(out[r.field]), _cmp_value(out[r.other])
                if isinstance(a, str) and isinstance(b, str):
                    a, b = _moment(a), _moment(b)
                if a is not None and b is not None and type(a) is type(b):
                    ok = {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b, "eq": a == b, "neq": a != b}[r.op]
                    if not ok:
                        _err(errors, r.field, "rule_failed", rule=r.id, message=r.message)
    return out, errors


def _moment(s: str):
    try:
        if DATE.match(s):
            return datetime.fromisoformat(s + "T00:00:00+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _check_value(f: Field, value, errors: list, *, files, final: bool, parent: str | None = None, row: int | None = None):
    kw = {"parent": parent, "row": row} if parent else {}
    if f.type == "repeat":
        if value is None:
            value = []
        if not isinstance(value, list):
            _err(errors, f.id, "bad_value", **kw)
            return None
        if len(value) > (f.max_items or MAX_ROWS):
            _err(errors, f.id, "too_many", max=f.max_items or MAX_ROWS)
            return None
        rows = []
        for i, item in enumerate(value):
            item = item if isinstance(item, dict) else {}
            seen: dict = {}
            row_out: dict = {}
            for s in f.fields:
                if not evaluate(s.visible_if, seen):
                    continue
                norm = _check_value(s, item.get(s.id), errors, files=files, final=final, parent=f.id, row=i)
                if norm is not None or s.type == "boolean":
                    seen[s.id] = norm
                    if norm is not None:
                        row_out[s.id] = norm
                req = s.required or (s.required_if is not None and evaluate(s.required_if, seen))
                if final and req and (is_empty(norm) or (s.type == "boolean" and norm is not True)):
                    if not any(e["field"] == s.id and e.get("row") == i for e in errors):
                        _err(errors, s.id, "required", parent=f.id, row=i)
            rows.append(row_out)
        if final and f.min_items and len(rows) < f.min_items:
            _err(errors, f.id, "too_few", min=f.min_items)
        return rows
    if value is None or (isinstance(value, str) and value == ""):
        return False if f.type == "boolean" else None
    if f.type in ("text", "textarea"):
        if not isinstance(value, str):
            _err(errors, f.id, "bad_value", **kw)
            return None
        n = len(value)
        cap = f.max_length or (MAX_TEXT if f.type == "textarea" else 500)
        if n > cap:
            _err(errors, f.id, "too_long", max=cap, **kw)
            return None
        if value.strip() == "":
            return value
        if f.min_length and n < f.min_length:
            _err(errors, f.id, "too_short", min=f.min_length, **kw)
        s = value.strip()
        if f.format == "email" and not EMAIL.match(s):
            _err(errors, f.id, "bad_email", **kw)
        elif f.format == "phone" and not PHONE.match(s):
            _err(errors, f.id, "bad_phone", **kw)
        elif f.format == "url" and not URL.match(s):
            _err(errors, f.id, "bad_url", **kw)
        elif f.format == "pattern" and f.pattern and safe_pattern(f.pattern) and len(s) <= 500:
            if not re.fullmatch(f.pattern, s):
                _err(errors, f.id, "pattern_mismatch", hint=f.pattern_hint, **kw)
        return value
    if f.type in ("number", "money"):
        amount = value.get("amount") if isinstance(value, dict) else value
        if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
            _err(errors, f.id, "not_a_number", **kw)
            return None
        if isinstance(amount, float):
            amount = repr(amount)
        d = _dec(amount)
        if d is None:
            _err(errors, f.id, "not_a_number", **kw)
            return None
        places = f.decimals if f.decimals is not None else (2 if f.type == "money" else 6)
        exp = d.normalize().as_tuple().exponent
        if isinstance(exp, int) and exp < 0 and -exp > places:
            _err(errors, f.id, "too_many_decimals", max=places, **kw)
            return None
        lo, hi = _dec(f.min), _dec(f.max)
        if lo is not None and d < lo:
            _err(errors, f.id, "below_min", min=f.min, **kw)
        if hi is not None and d > hi:
            _err(errors, f.id, "above_max", max=f.max, **kw)
        text = format(d, "f")
        if f.type == "money":
            # Money is written with exactly the field's decimals ("12.5" → "12.50"): the same
            # amount, in the one representation a ledger and a template both expect.
            text = format(d.quantize(Decimal(1).scaleb(-places)), "f")
            if isinstance(value, dict) and value.get("currency") not in (None, f.currency):
                _err(errors, f.id, "wrong_currency", currency=f.currency, **kw)
                return None
            return {"amount": text, "currency": f.currency}
        return text
    if f.type == "date":
        if not isinstance(value, str) or not DATE.match(value):
            _err(errors, f.id, "bad_date", **kw)
            return None
        try:
            date.fromisoformat(value)
        except ValueError:
            _err(errors, f.id, "bad_date", **kw)
            return None
        return value
    if f.type == "datetime":
        if not isinstance(value, str):
            _err(errors, f.id, "bad_datetime", **kw)
            return None
        try:
            moment = datetime.fromisoformat(value)
        except ValueError:
            _err(errors, f.id, "bad_datetime", **kw)
            return None
        if moment.tzinfo is None:
            _err(errors, f.id, "datetime_without_offset", **kw)
            return None
        return moment.isoformat(timespec="minutes" if moment.second == 0 else "seconds")
    if f.type == "select":
        if not isinstance(value, str) or value not in {o.value for o in f.options}:
            _err(errors, f.id, "not_an_option", **kw)
            return None
        return value
    if f.type == "multiselect":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            _err(errors, f.id, "not_an_option", **kw)
            return None
        allowed = {o.value for o in f.options}
        picked = [o.value for o in f.options if o.value in set(value)]
        if set(value) - allowed:
            _err(errors, f.id, "not_an_option", **kw)
            return None
        if f.max_items and len(picked) > f.max_items:
            _err(errors, f.id, "too_many", max=f.max_items, **kw)
        if final and f.min_items and picked and len(picked) < f.min_items:
            _err(errors, f.id, "too_few", min=f.min_items, **kw)
        return picked
    if f.type == "boolean":
        if not isinstance(value, bool):
            _err(errors, f.id, "bad_value", **kw)
            return None
        return value
    if f.type == "file":
        ids = value if isinstance(value, list) else [value]
        if not all(isinstance(i, str) for i in ids):
            _err(errors, f.id, "bad_value", **kw)
            return None
        if len(ids) > f.max_files:
            _err(errors, f.id, "too_many", max=f.max_files, **kw)
            return None
        out = []
        for fid in ids:
            rec = (files or {}).get(fid)
            if not rec or rec.get("field_id") != f.id:
                _err(errors, f.id, "file_missing", **kw)
                return None
            ext = "." + rec["filename"].rsplit(".", 1)[-1].lower() if "." in rec["filename"] else ""
            if f.accept and ext not in {a.lower() for a in f.accept}:
                _err(errors, f.id, "file_type", accept=",".join(f.accept), **kw)
                return None
            if rec["size"] > f.max_size_mb * 1024 * 1024:
                _err(errors, f.id, "file_too_large", max_mb=f.max_size_mb, **kw)
                return None
            out.append(fid)
        return out
    _err(errors, f.id, "bad_value", **kw)
    return None


def field_index(defn: Definition) -> dict[str, Field]:
    out: dict[str, Field] = {}
    for f in defn.fields:
        out[f.id] = f
        for s in f.fields:
            out[s.id] = s
    return out
