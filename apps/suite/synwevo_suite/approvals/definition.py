"""What an approval template is, what a request's field values must be, and when a stage applies.

A template has a small typed list of request fields (text, long text, number, amount, date,
choice), an ordered list of stages and a few rules. Everything here is checked on the server:
the page's editor and request form only help a person get it right the first time.

Rules stated once:

* **Numbers and amounts are decimal strings.** ``"12.50"`` stays exact; no binary floating point
  touches an amount, and a value with more decimals than the field allows is refused, never
  rounded. Amounts carry two decimals (the field's currency is part of the template).
* **Dates are ``YYYY-MM-DD``.**
* **A stage's condition compares request fields** (``amount > 10000``); a stage whose condition
  does not hold is skipped, and the timeline records which comparison failed and with which
  value. Comparisons with an empty field are false (``neq`` / ``not_in`` are true).
* **At least one stage has no condition**, so no request can pass without anybody deciding.
* **Approvers** are named workspace members and/or "members with role ≥ X", resolved at the
  moment the stage opens. Deciding needs edit rights in the workspace, so only editors, admins
  and owners can be approvers; the requester is never one of the approvers of their own request.
* **A named person's own request can be impossible.** "All must approve" counts every named
  person, and a named person who cannot vote never lowers that count; "N votes" needs N people.
  When a stage that will run can never gather its votes once the requester is left out, the
  request is refused up front (:func:`needs_requester`) and the template lists the stage under
  ``warnings`` (:func:`requester_warnings`) — publishing is allowed, everybody else can use it.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field as F, field_validator

FIELD_ID = r"^f_[a-z0-9]{4,24}$"
STAGE_ID = r"^s_[a-z0-9]{4,24}$"
USER_ID = r"^[A-Za-z0-9_-]{1,100}$"
CURRENCY = r"^[A-Z]{3}$"
FieldType = Literal["text", "textarea", "number", "money", "date", "select"]
Mode = Literal["all", "any", "n_of_m"]
RoleFloor = Literal["editor", "admin", "owner"]
Op = Literal["eq", "neq", "gt", "gte", "lt", "lte", "in", "not_in", "filled", "empty"]

MAX_FIELDS = 50
MAX_STAGES = 20
MAX_USERS = 50
MAX_RULES = 10
MAX_OPTIONS = 100
TEXT_LIMIT = 2000
TEXTAREA_LIMIT = 10_000
MAX_DECIMALS = 6
MONEY_DECIMALS = 2
MAX_DUE_HOURS = 24 * 90
MAX_ESCALATE_HOURS = 24 * 30
#: Integer digits a number or amount may have (a far larger amount is a typing mistake).
MAX_INTEGER_DIGITS = 15

ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}
ORDERED_OPS = frozenset({"gt", "gte", "lt", "lte"})
OPS_BY_TYPE: dict[str, frozenset[str]] = {
    "text": frozenset({"eq", "neq", "in", "not_in", "filled", "empty"}),
    "textarea": frozenset({"filled", "empty"}),
    "select": frozenset({"eq", "neq", "in", "not_in", "filled", "empty"}),
    "number": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "filled", "empty"}),
    "money": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "filled", "empty"}),
    "date": frozenset({"eq", "neq", "gt", "gte", "lt", "lte", "filled", "empty"}),
}


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Option(_M):
    value: str = F(min_length=1, max_length=100)
    label: str = F(min_length=1, max_length=200)


class FieldDef(_M):
    id: str = F(pattern=FIELD_ID)
    type: FieldType
    label: str = F(min_length=1, max_length=200)
    help: str = F(default="", max_length=500)
    required: bool = False
    options: list[Option] = F(default_factory=list, max_length=MAX_OPTIONS)
    currency: str | None = F(default=None, pattern=CURRENCY)
    decimals: int | None = F(default=None, ge=0, le=MAX_DECIMALS)
    min: str | None = F(default=None, max_length=40)
    max: str | None = F(default=None, max_length=40)
    max_length: int | None = F(default=None, ge=1, le=TEXTAREA_LIMIT)


class Rule(_M):
    """One comparison of a request field with a value."""
    field: str = F(pattern=FIELD_ID)
    op: Op
    value: str | list[str] | None = None

    @field_validator("value")
    @classmethod
    def _bounded(cls, v):
        if isinstance(v, list):
            if len(v) > 50 or any(len(x) > 200 for x in v):
                raise ValueError("at most 50 values of 200 characters")
        elif isinstance(v, str) and len(v) > 200:
            raise ValueError("at most 200 characters")
        return v


class Condition(_M):
    match: Literal["all", "any"] = "all"
    rules: list[Rule] = F(min_length=1, max_length=MAX_RULES)


class Approvers(_M):
    users: list[str] = F(default_factory=list, max_length=MAX_USERS)
    min_role: RoleFloor | None = None

    @field_validator("users")
    @classmethod
    def _ids(cls, v: list[str]):
        seen = []
        for uid in v:
            if not isinstance(uid, str) or not re.match(USER_ID, uid):
                raise ValueError("not a user id")
            if uid not in seen:
                seen.append(uid)
        return seen

    def empty(self) -> bool:
        return not self.users and self.min_role is None


class Escalation(_M):
    after_hours: int = F(default=0, ge=0, le=MAX_ESCALATE_HOURS)
    action: Literal["add_approvers", "notify_requester"] = "notify_requester"
    approvers: Approvers = F(default_factory=Approvers)


class Due(_M):
    hours: int = F(ge=1, le=MAX_DUE_HOURS)
    remind: bool = True
    escalation: Escalation | None = None


class Stage(_M):
    id: str = F(pattern=STAGE_ID)
    name: str = F(min_length=1, max_length=100)
    approvers: Approvers = F(default_factory=Approvers)
    mode: Mode = "all"
    required: int | None = F(default=None, ge=1, le=MAX_USERS)
    condition: Condition | None = None
    due: Due | None = None


class Rules(_M):
    #: The same person may not decide in two stages of one revision.
    distinct_deciders: bool = False


class Definition(_M):
    fields: list[FieldDef] = F(default_factory=list, max_length=MAX_FIELDS)
    stages: list[Stage] = F(default_factory=list, max_length=MAX_STAGES)
    rules: Rules = F(default_factory=Rules)

    def field(self, fid: str) -> FieldDef | None:
        return next((f for f in self.fields if f.id == fid), None)


# ------------------------------------------------------------------------------ decimals, dates
def to_decimal(value: Any) -> Decimal | None:
    """A decimal from a string or an integer (a float only through its shortest text form).
    None when it is not a finite number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) > 60 or not re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", text):
        return None
    try:
        d = Decimal(text)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def decimals_of(d: Decimal) -> int:
    exp = d.as_tuple().exponent
    return -exp if isinstance(exp, int) and exp < 0 else 0


def to_date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _integer_digits(d: Decimal) -> int:
    return max(d.adjusted() + 1, 0) if d else 1


# ------------------------------------------------------------------------------ publish checks
def _p(code: str, message: str, **where) -> dict:
    return {"code": code, "message": message, **where}


def problems(defn: Definition) -> list[dict]:
    """What stops this definition from being published, each with a stable code, a zh-Hans
    sentence and where it is (``field`` / ``stage``)."""
    out: list[dict] = []
    ids = [f.id for f in defn.fields]
    for fid in {x for x in ids if ids.count(x) > 1}:
        out.append(_p("duplicate_field_id", "字段编号重复", field=fid))
    for f in defn.fields:
        if f.type == "select":
            if not f.options:
                out.append(_p("select_without_options", "选择题至少要有一个选项", field=f.id))
            values = [o.value for o in f.options]
            if len(set(values)) != len(values):
                out.append(_p("duplicate_option", "选项的值不能重复", field=f.id))
        if f.type in ("number", "money"):
            lo, hi = to_decimal(f.min) if f.min else None, to_decimal(f.max) if f.max else None
            if (f.min and lo is None) or (f.max and hi is None):
                out.append(_p("bad_bound", "最小值或最大值不是数字", field=f.id))
            elif lo is not None and hi is not None and lo > hi:
                out.append(_p("min_above_max", "最小值大于最大值", field=f.id))
        if f.type == "money" and not f.currency:
            out.append(_p("money_without_currency", "金额字段需要选择币种", field=f.id))
    if not defn.stages:
        out.append(_p("no_stages", "至少需要一个审批阶段"))
    sids = [s.id for s in defn.stages]
    for sid in {x for x in sids if sids.count(x) > 1}:
        out.append(_p("duplicate_stage_id", "阶段编号重复", stage=sid))
    if defn.stages and all(s.condition is not None for s in defn.stages):
        out.append(_p("no_unconditional_stage", "至少要有一个不带条件的阶段，任何申请都要有人审批"))
    for s in defn.stages:
        if s.approvers.empty():
            out.append(_p("stage_without_approvers", "阶段没有指定审批人", stage=s.id))
        if s.mode == "n_of_m":
            if not s.required:
                out.append(_p("required_missing", "“N 人同意”需要填写人数", stage=s.id))
            elif s.approvers.min_role is None and s.required > len(s.approvers.users):
                out.append(_p("required_above_approvers", "需要同意的人数多于指定的审批人", stage=s.id))
        if s.condition is not None:
            for r in s.condition.rules:
                out.extend(_rule_problems(defn, s, r))
        if s.due and s.due.escalation and s.due.escalation.action == "add_approvers" \
                and s.due.escalation.approvers.empty():
            out.append(_p("escalation_without_approvers", "超时升级为“增加审批人”时需要指定审批人", stage=s.id))
    return out


def needs_requester(stage: Stage, requester: str) -> bool:
    """Whether ``stage``, when it runs, can never gather its required votes on a request that
    ``requester`` filed — the requester never approves their own request, and nobody else the
    template names can take that vote.

    Only the certain case counts. A role selector may gain members and an escalation may add
    people, so a stage that could still fill up that way is left to the stall-and-repair rules.
    """
    named = set(stage.approvers.users)
    if requester not in named:
        return False
    extra = Approvers()
    if stage.due and stage.due.escalation and stage.due.escalation.action == "add_approvers":
        extra = stage.due.escalation.approvers
    if stage.mode == "all":
        # Every named person counts towards the quorum, the requester too; their vote can only
        # be made up by somebody an escalation adds beyond the named people.
        return extra.min_role is None and not set(extra.users) - named - {requester}
    if stage.approvers.min_role or extra.min_role:
        return False
    required = (stage.required or 1) if stage.mode == "n_of_m" else 1
    return len((named | set(extra.users)) - {requester}) < required


def requester_warnings(defn: Definition) -> list[dict]:
    """What does not stop publishing but refuses some requests: each stage whose named approvers'
    own requests :func:`needs_requester` refuses, with those people under ``users``."""
    out: list[dict] = []
    for s in defn.stages:
        users = [uid for uid in s.approvers.users if needs_requester(s, uid)]
        if users:
            out.append(_p("stage_needs_requester", "被指定的审批人自己发起申请时，这个阶段凑不齐所需票数，申请会被拒绝",
                          stage=s.id, users=users))
    return out


def _rule_problems(defn: Definition, s: Stage, r: Rule) -> list[dict]:
    f = defn.field(r.field)
    if f is None:
        return [_p("condition_unknown_field", "条件引用了不存在的字段", stage=s.id, field=r.field)]
    if r.op not in OPS_BY_TYPE[f.type]:
        return [_p("condition_bad_op", "这种字段不能这样比较", stage=s.id, field=r.field)]
    if r.op in ("filled", "empty"):
        return []
    values = r.value if isinstance(r.value, list) else [r.value]
    if r.op in ("in", "not_in"):
        if not isinstance(r.value, list) or not r.value:
            return [_p("condition_bad_value", "条件的比较值无效", stage=s.id, field=r.field)]
    elif not isinstance(r.value, str) or not r.value.strip():
        return [_p("condition_bad_value", "条件的比较值无效", stage=s.id, field=r.field)]
    for v in values:
        ok = True
        if f.type in ("number", "money"):
            ok = to_decimal(v) is not None
        elif f.type == "date":
            ok = to_date(v) is not None
        elif f.type == "select":
            ok = v in {o.value for o in f.options}
        if not ok:
            return [_p("condition_bad_value", "条件的比较值无效", stage=s.id, field=r.field)]
    return []


# ------------------------------------------------------------------------------ field values
def check_values(defn: Definition, values: dict) -> tuple[dict, list[dict]]:
    """The values a request may carry: known fields only, typed and bounded. Returns the clean
    values (numbers and amounts as normalized decimal strings) and the errors, each
    ``{"field", "code"}`` (plus ``limit`` where a number explains it)."""
    errors: list[dict] = []
    clean: dict = {}
    if not isinstance(values, dict):
        return {}, [{"field": None, "code": "not_an_object"}]
    known = {f.id for f in defn.fields}
    for k in values:
        if k not in known:
            errors.append({"field": k, "code": "unknown_field"})
    for f in defn.fields:
        raw = values.get(f.id)
        blank = raw is None or (isinstance(raw, str) and not raw.strip())
        if blank:
            if f.required:
                errors.append({"field": f.id, "code": "required"})
            continue
        if f.type in ("text", "textarea"):
            if not isinstance(raw, str):
                errors.append({"field": f.id, "code": "not_text"})
                continue
            limit = f.max_length or (TEXT_LIMIT if f.type == "text" else TEXTAREA_LIMIT)
            limit = min(limit, TEXT_LIMIT if f.type == "text" else TEXTAREA_LIMIT)
            if len(raw) > limit:
                errors.append({"field": f.id, "code": "too_long", "limit": limit})
                continue
            clean[f.id] = raw
        elif f.type in ("number", "money"):
            d = to_decimal(raw)
            if d is None:
                errors.append({"field": f.id, "code": "not_number"})
                continue
            allowed = MONEY_DECIMALS if f.type == "money" else (f.decimals if f.decimals is not None else MAX_DECIMALS)
            if decimals_of(d) > allowed:
                errors.append({"field": f.id, "code": "too_many_decimals", "limit": allowed})
                continue
            if _integer_digits(d) > MAX_INTEGER_DIGITS:
                errors.append({"field": f.id, "code": "too_large"})
                continue
            lo, hi = to_decimal(f.min) if f.min else None, to_decimal(f.max) if f.max else None
            if lo is not None and d < lo:
                errors.append({"field": f.id, "code": "below_min", "limit": f.min})
                continue
            if hi is not None and d > hi:
                errors.append({"field": f.id, "code": "above_max", "limit": f.max})
                continue
            if f.type == "money":
                clean[f.id] = format(d.quantize(Decimal("0.01")), "f")
            else:
                clean[f.id] = format(d, "f")
        elif f.type == "date":
            if to_date(raw) is None:
                errors.append({"field": f.id, "code": "not_date"})
                continue
            clean[f.id] = raw
        elif f.type == "select":
            if not isinstance(raw, str) or raw not in {o.value for o in f.options}:
                errors.append({"field": f.id, "code": "not_option"})
                continue
            clean[f.id] = raw
    return clean, errors


# ------------------------------------------------------------------------------ conditions
def evaluate(cond: Condition, defn: Definition, values: dict) -> tuple[bool, list[dict]]:
    """Whether a stage's condition holds for these values, and each comparison as it was made
    (field, operator, expected, actual, result) — what the timeline shows when a stage is
    skipped."""
    checks = []
    for r in cond.rules:
        f = defn.field(r.field)
        actual = values.get(r.field)
        ok = _compare(f, r, actual) if f is not None else False
        checks.append({"field": r.field, "label": f.label if f else r.field, "op": r.op,
                       "value": r.value, "actual": actual, "result": ok})
    results = [c["result"] for c in checks]
    held = all(results) if cond.match == "all" else any(results)
    return held, checks


def _compare(f: FieldDef, r: Rule, actual: Any) -> bool:
    empty = actual is None or (isinstance(actual, str) and not actual.strip())
    if r.op == "filled":
        return not empty
    if r.op == "empty":
        return empty
    if empty:
        return r.op in ("neq", "not_in")
    if f.type in ("number", "money"):
        a = to_decimal(actual)
        targets = [to_decimal(v) for v in (r.value if isinstance(r.value, list) else [r.value])]
        if a is None or any(t is None for t in targets):
            return False
    elif f.type == "date":
        a = to_date(actual)
        targets = [to_date(v) for v in (r.value if isinstance(r.value, list) else [r.value])]
        if a is None or any(t is None for t in targets):
            return False
    else:
        a = actual
        targets = r.value if isinstance(r.value, list) else [r.value]
    t = targets[0] if targets else None
    if r.op == "eq":
        return a == t
    if r.op == "neq":
        return a != t
    if r.op == "in":
        return a in targets
    if r.op == "not_in":
        return a not in targets
    if r.op in ORDERED_OPS and f.type in ("number", "money", "date"):
        return {"gt": a > t, "gte": a >= t, "lt": a < t, "lte": a <= t}[r.op]
    return False
