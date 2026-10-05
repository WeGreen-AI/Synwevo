"""Wallet projections, usage reads and the person's own wallet settings; mount before the SPA.

The positional dependencies match account_routes.build_router. The host must additionally
inject its environment-bound Ledger (using the same Store) and explicitly configured
Units. Missing configuration returns 503; this module invents no unit, price or environment.
Only the admitted central session determines who is asking. No admin or workspace override.

Payer selection (shared contract ``{"kind": "user"|"organization", "id"}``): ``payer_kind`` and
``payer_id`` choose whose wallet a read is about. ``user`` (the default) is only ever the
signed-in person; ``organization`` requires a current billing manager of that active
organization — its owner or a holder of ``org.billing.manage``, the rule the top-up routes use
(synwevo_core.billing.usage decides, from current state, on every read). A platform
administrator, a workspace role, an organization admin without that permission or another
member gains nothing, and an unknown organization is refused the same way.

GET /api/auth/wallet                 balance (personal: Ledger.overview_for_user's atomic snapshot)
GET /api/auth/wallet/payers          the payers this person may read (themself, organizations)
GET /api/auth/wallet/statement       posted entries: oldest first after ``after_seq``, or newest
                                     first before ``before_seq`` (limit 1..100)
GET /api/auth/wallet/usage           totals per day / model / conversation (organization: member)
GET /api/auth/wallet/calls           per-call rows, newest first, opaque decimal ``cursor``
GET /api/auth/wallet/calls.csv       the same rows as CSV, every cell formula-neutralized
GET|PUT /api/auth/wallet/limits      the person's daily/weekly/monthly spending limits (PayerBudgets)
GET|PUT /api/auth/wallet/reminder    the person's low-balance reminder threshold

Amounts and sequence cursors are decimal strings; scale means decimal places in the unit. Usage
figures come from ledger and metering facts, never from engine counters; platform cost, provider
request ids and evidence are never returned. Reads create no money facts; the only writes are the
person's own limit and reminder settings, audited without amounts.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
import json
import os
from typing import Literal

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from synwevo_core.billing import BillingError, Ledger, Unit
from synwevo_core.billing.models import MAX_INTEGER
from synwevo_core.billing.payer_budgets import PayerBudgets
from synwevo_core.billing.reminders import MAX_THRESHOLD, LowBalanceReminders
from synwevo_core.billing.usage import Payer, PayerUsage, period as usage_period
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.sessions import Sessions
from synwevo_core.store import Store

from .mail import Mailer


class WalletModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class WalletUnit(WalletModel):
    unit: str
    scale: int = Field(ge=0, le=18)

    @field_validator("scale")
    @classmethod
    def valid_scale(cls, value: int) -> int:
        # Use the real Unit contract, including its upper bound, without a second limit.
        return Unit("wallet-projection", value).scale


class UnitBalance(WalletUnit):
    booked: str
    used: str
    held: str
    available: str


class PayerOut(WalletModel):
    kind: Literal["user", "organization"]
    id: str


class WalletBalance(WalletModel):
    environment: Literal["test", "live"]
    payer: PayerOut
    #: The signed-in person for a personal wallet; null for an organization's.
    payer_user_id: str | None
    balances: list[UnitBalance]
    used_through_seq: str


class WalletEntry(WalletUnit):
    seq: str
    id: str
    kind: Literal["paid", "test_payment", "sale"]
    delta: str
    created_at: int


class WalletStatement(WalletModel):
    environment: Literal["test", "live"]
    payer: PayerOut
    payer_user_id: str | None
    items: list[WalletEntry]
    next_after_seq: str | None
    #: Set only for a newest-first page (``before_seq``): the cursor of the next, older page.
    next_before_seq: str | None = None


class LimitsChange(WalletModel):
    unit_code: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0, le=2**63 - 2)
    enabled: bool
    daily_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    weekly_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")
    monthly_budget: str = Field(pattern=r"^(0|[1-9][0-9]{0,18})$")


class ReminderChange(WalletModel):
    unit: str = Field(min_length=1, max_length=200)
    expected_revision: int = Field(ge=0, le=2**63 - 2)
    enabled: bool
    threshold: str = Field(pattern=r"^(0|[1-9][0-9]{0,15})$")


def _unavailable() -> ServiceError:
    return ServiceError(503, "wallet_unavailable", "个人钱包暂不可用，请稍后再试")


def from_host(store: Store) -> tuple[Ledger | None, tuple[Unit, ...]]:
    """Capture explicit host settings at installation; missing/invalid means unavailable.

    SYNWEVO_BILLING_WALLET_UNITS is a JSON array of 1..16 {code, scale} objects. This
    registers/funds nothing. The composition root can instead inject its governed Ledger
    and units into build_router; no caller can override either via the HTTP request.
    """
    mode = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT")
    raw = os.environ.get("SYNWEVO_BILLING_WALLET_UNITS")
    if mode not in ("test", "live") or not raw or len(raw) > 16384:
        return None, ()

    def unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_field")
            result[key] = value
        return result

    try:
        records = json.loads(raw, object_pairs_hook=unique_fields)
        if not isinstance(records, list) or not 1 <= len(records) <= 16:
            return None, ()
        if any(not isinstance(record, dict) or set(record) != {"code", "scale"} for record in records):
            return None, ()
        units = tuple(Unit(record["code"], record["scale"]) for record in records)
        if len({unit.code for unit in units}) != len(units):
            return None, ()
        return Ledger(store, environment=mode, namespace="personal-agent"), units
    except (ValueError, TypeError, RecursionError):
        # Do not echo or log raw host configuration, including parser exception messages.
        return None, ()


# Each refusal is a literal sentence (services/identity/web/tests/refusals.test.mjs reads them).
_REFUSALS: dict[str, Callable[[], ServiceError]] = {
    "account_unavailable": lambda: ServiceError(401, "unauthorized", "请先登录可用账户"),
    "wallet_forbidden": lambda: ServiceError(403, "wallet_forbidden", "无权查看此钱包"),
    "invalid_payer": lambda: ServiceError(400, "bad_payer", "付款方参数无效"),
    "invalid_period": lambda: ServiceError(400, "bad_period", "时间范围无效：起止日期最多相隔 366 天"),
    "invalid_timezone": lambda: ServiceError(400, "bad_timezone", "时区无效"),
    "invalid_group": lambda: ServiceError(400, "bad_group", "分组方式无效"),
    "invalid_state": lambda: ServiceError(400, "bad_filter", "筛选条件无效"),
    "invalid_identifier": lambda: ServiceError(400, "bad_filter", "筛选条件无效"),
    "usage_period_too_large": lambda: ServiceError(422, "period_too_large", "所选时间段内的记录过多，请缩短时间段后再试"),
    "payer_budget_conflict": lambda: ServiceError(409, "limits_conflict", "支出上限已在别处修改，请刷新后再试"),
    "unknown_sale_unit": lambda: ServiceError(409, "limits_unavailable", "模型价格尚未发布，暂时不能设置支出上限"),
    "reminder_conflict": lambda: ServiceError(409, "reminder_conflict", "余额提醒已在别处修改，请刷新后再试"),
    "invalid_reminder": lambda: ServiceError(400, "bad_fields", "请求参数无效"),
}


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except ServiceError:
        raise
    except BillingError as exc:
        refusal = _REFUSALS.get(exc.code)
        if refusal is not None:
            raise refusal() from None
        raise _unavailable() from None
    except Exception:
        # Storage/configuration/projection failures must not reach the shared ValueError
        # handler (which returns str(exc)), or log private ledger fields via a traceback.
        raise _unavailable() from None


def _query_fields(request: Request, allowed: set[str]) -> None:
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(key not in allowed for key in keys) or len(keys) != len(set(keys)):
        raise ServiceError(400, "bad_fields", "请求参数无效")


def _decimal(value: str, *, minimum: int, maximum: int) -> int:
    # Bound length before int(); reject whitespace, signs, unicode digits, floats,
    # exponents, empty strings and non-canonical leading zeroes without echoing input.
    if (not value or len(value) > len(str(maximum)) or not value.isascii()
            or not value.isdecimal() or (len(value) > 1 and value[0] == "0")):
        raise ServiceError(400, "bad_pagination", "分页参数无效")
    result = int(value)
    if not minimum <= result <= maximum:
        raise ServiceError(400, "bad_pagination", "分页参数无效")
    return result


PAYER_FIELDS = {"payer_kind", "payer_id"}
PERIOD_FIELDS = {"from", "to", "tz"}
FILTER_FIELDS = {"model", "state"}


def build_router(ident: Identity, sessions: Sessions, mailer: Mailer,
                 audit: Callable[..., None], *, ledger: Ledger | None = None,
                 units: Sequence[Unit] = ()) -> APIRouter:
    """Use the account factory's existing dependencies; never authenticate a new way.

    mailer is retained for the account factory contract; reads send no mail and do not put
    financial details in audit logs.
    """
    router = APIRouter(prefix="/api/auth/wallet", tags=["account"])
    environment = ledger.environment if ledger is not None else None
    wallet_units = tuple(units)

    def current_user(request: Request) -> dict:
        live = sessions.current(getattr(request.state, "session", None))
        user = ident.get_user(live["user_id"]) if live else None
        if not user or user["status"] != "active" or user.get("deleted_at") is not None:
            raise ServiceError(401, "unauthorized", "请先登录可用账户")
        return user

    def current(request: Request) -> str:
        return current_user(request)["id"]

    def configured() -> tuple[Ledger, Literal["test", "live"]]:
        if (ledger is None or not 1 <= len(wallet_units) <= 16 or environment not in ("test", "live")
                or ledger.environment != environment or ledger.store is not ident.store
                or sessions.store is not ident.store
                or any(not isinstance(unit, Unit) for unit in wallet_units)
                or len({unit.code for unit in wallet_units}) != len(wallet_units)):
            raise _unavailable()
        return ledger, environment

    def payer_of(uid: str, kind: str | None, pid: str | None) -> Payer:
        """``user`` (the default) names the signed-in person; any other user id is refused."""
        kind = kind or "user"
        if kind == "user":
            pid = pid or uid
        elif kind != "organization" or not pid:
            raise ServiceError(400, "bad_payer", "付款方参数无效")
        try:
            return Payer(kind, pid)
        except BillingError:
            raise ServiceError(400, "bad_payer", "付款方参数无效") from None

    def payer_out(payer: Payer) -> PayerOut:
        return PayerOut(kind=payer.kind, id=payer.id)

    def window(user: dict, first: str | None, last: str | None, tz: str | None):
        # Calendar days in the reader's own time zone unless the page names one.
        return usage_period(first or None, last or None, tz or user.get("timezone") or "UTC")

    def usage_service(mode: str) -> PayerUsage:
        return PayerUsage(ident.store, environment=mode)

    def private(response: Response) -> None:
        response.headers["Cache-Control"] = "private, no-store"

    @router.get("", response_model=WalletBalance)
    def balance(request: Request, response: Response, payer_kind: str | None = Query(None),
                payer_id: str | None = Query(None)) -> WalletBalance:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, PAYER_FIELDS)
            payer = payer_of(uid, payer_kind, payer_id)
            service, mode = configured()
            if payer.kind == "user":
                if payer.id != uid:
                    raise BillingError("wallet_forbidden")
                overview = service.overview_for_user(requester_user_id=uid, payer_user_id=uid,
                                                     units=wallet_units)
            else:
                overview = usage_service(mode).overview(uid, payer, wallet_units)
            balances = [UnitBalance(unit=row["unit"], scale=row["scale"],
                                    booked=str(row["booked"]), used=str(row["used"]),
                                    held=str(row["held"]), available=str(row["available"]))
                        for row in overview["balances"]]
            result = WalletBalance(environment=mode, payer=payer_out(payer),
                                   payer_user_id=uid if payer.kind == "user" else None,
                                   balances=balances, used_through_seq=str(overview["used_through_seq"]))
            current(request)  # A revoked session must not receive a completed projection.
            private(response)
            return result

    @router.get("/payers")
    def payers(request: Request, response: Response) -> dict:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            _, mode = configured()
            items = usage_service(mode).payers(uid)
            current(request)
            private(response)
            return {"environment": mode, "items": items}

    @router.get("/statement", response_model=WalletStatement)
    def statement(request: Request, response: Response,
                  after_seq: str = Query("0", description="Exclusive decimal sequence, 0..2^63-1"),
                  before_seq: str | None = Query(None, description="Newest first below this exclusive sequence"),
                  limit: str = Query("50", description="Decimal page size, 1..100"),
                  payer_kind: str | None = Query(None), payer_id: str | None = Query(None)) -> WalletStatement:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, {"after_seq", "before_seq", "limit"} | PAYER_FIELDS)
            if before_seq is not None and "after_seq" in request.query_params:
                raise ServiceError(400, "bad_pagination", "分页参数无效")
            cursor = _decimal(after_seq, minimum=0, maximum=MAX_INTEGER)
            below = _decimal(before_seq, minimum=1, maximum=MAX_INTEGER) if before_seq is not None else None
            size = _decimal(limit, minimum=1, maximum=100)
            payer = payer_of(uid, payer_kind, payer_id)
            service, mode = configured()
            if payer.kind == "user" and below is None:
                if payer.id != uid:
                    raise BillingError("wallet_forbidden")
                rows = service.statement_for_user(requester_user_id=uid, payer_user_id=uid,
                                                  after_seq=cursor, limit=size + 1)
            else:
                rows = usage_service(mode).statement(uid, payer, after_seq=cursor, before_seq=below,
                                                     limit=size + 1)
            # Explicit output allowlist even if the service later grows internal fields.
            items = [WalletEntry(seq=str(row["seq"]), id=row["id"], kind=row["kind"],
                                 unit=row["unit"], scale=row["scale"], delta=str(row["delta"]),
                                 created_at=row["created_at"]) for row in rows[:size]]
            more = items[-1].seq if len(rows) > size else None
            result = WalletStatement(environment=mode, payer=payer_out(payer),
                                     payer_user_id=uid if payer.kind == "user" else None, items=items,
                                     next_after_seq=more if below is None else None,
                                     next_before_seq=more if below is not None else None)
            current(request)
            private(response)
            return result

    @router.get("/usage")
    def usage(request: Request, response: Response, group: str = Query("day"),
              payer_kind: str | None = Query(None), payer_id: str | None = Query(None),
              first: str | None = Query(None, alias="from"), last: str | None = Query(None, alias="to"),
              tz: str | None = Query(None)) -> dict:
        with _safe_errors():
            user = current_user(request)
            _query_fields(request, {"group"} | PAYER_FIELDS | PERIOD_FIELDS)
            payer = payer_of(user["id"], payer_kind, payer_id)
            _, mode = configured()
            result = usage_service(mode).summary(user["id"], payer, window(user, first, last, tz), group=group)
            current(request)
            private(response)
            return {"environment": mode, "payer": payer_out(payer).model_dump(), **result}

    @router.get("/calls")
    def calls(request: Request, response: Response, payer_kind: str | None = Query(None),
              payer_id: str | None = Query(None), first: str | None = Query(None, alias="from"),
              last: str | None = Query(None, alias="to"), tz: str | None = Query(None),
              model: str | None = Query(None), state: str | None = Query(None),
              cursor: str | None = Query(None), limit: str = Query("50")) -> dict:
        with _safe_errors():
            user = current_user(request)
            _query_fields(request, {"cursor", "limit"} | FILTER_FIELDS | PAYER_FIELDS | PERIOD_FIELDS)
            before = _decimal(cursor, minimum=1, maximum=MAX_INTEGER) if cursor is not None else None
            size = _decimal(limit, minimum=1, maximum=100)
            payer = payer_of(user["id"], payer_kind, payer_id)
            _, mode = configured()
            span = window(user, first, last, tz)
            result = usage_service(mode).calls(user["id"], payer, span, model=model or None,
                                               state=state or None, before=before, limit=size)
            current(request)
            private(response)
            return {"environment": mode, "payer": payer_out(payer).model_dump(), "period": span.as_dict(), **result}

    @router.get("/calls.csv")
    def calls_csv(request: Request, payer_kind: str | None = Query(None), payer_id: str | None = Query(None),
                  first: str | None = Query(None, alias="from"), last: str | None = Query(None, alias="to"),
                  tz: str | None = Query(None), model: str | None = Query(None),
                  state: str | None = Query(None)) -> Response:
        with _safe_errors():
            user = current_user(request)
            _query_fields(request, FILTER_FIELDS | PAYER_FIELDS | PERIOD_FIELDS)
            payer = payer_of(user["id"], payer_kind, payer_id)
            _, mode = configured()
            span = window(user, first, last, tz)
            text = usage_service(mode).csv_text(user["id"], payer, span, model=model or None, state=state or None)
            current(request)
            name = f"synwevo-usage-{span.first.isoformat()}-{span.last.isoformat()}.csv"
            # The BOM lets spreadsheet programs read the UTF-8 titles; the name is ours, not input.
            return Response(content=("﻿" + text).encode("utf-8"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}"',
                                     "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @router.get("/limits")
    def limits(request: Request, response: Response) -> dict:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            service, _ = configured()
            result = PayerBudgets(service).read(uid)
            current(request)
            private(response)
            return result

    @router.put("/limits")
    def set_limits(request: Request, response: Response, body: LimitsChange) -> dict:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            service, _ = configured()
            budgets = PayerBudgets(service)
            with ident.store.tx():
                result = budgets.set(uid, unit_code=body.unit_code, expected_revision=body.expected_revision,
                                     enabled=body.enabled, daily_budget=int(body.daily_budget),
                                     weekly_budget=int(body.weekly_budget),
                                     monthly_budget=int(body.monthly_budget))
                # Who changed which unit's limits and to which revision; never the amounts.
                audit(request, "wallet.limits.changed", actor_id=uid, object_kind="wallet", object_id=uid,
                      meta={"unit": body.unit_code, "revision": result["revision"], "enabled": body.enabled},
                      workspace_given=True)
            private(response)
            return result

    def reminder_unit(code: str) -> Unit:
        for unit in wallet_units:
            if unit.code == code:
                return unit
        raise ServiceError(400, "bad_fields", "请求参数无效")

    @router.get("/reminder")
    def reminder(request: Request, response: Response) -> dict:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            _, mode = configured()
            rows = LowBalanceReminders(ident.store, environment=mode).read(uid, wallet_units)
            current(request)
            private(response)
            return {"environment": mode, "reminders": rows}

    @router.put("/reminder")
    def set_reminder(request: Request, response: Response, body: ReminderChange) -> dict:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            _, mode = configured()
            unit = reminder_unit(body.unit)
            threshold = int(body.threshold)
            if threshold > MAX_THRESHOLD:
                raise ServiceError(400, "bad_fields", "请求参数无效")
            with ident.store.tx():
                result = LowBalanceReminders(ident.store, environment=mode).set(
                    uid, unit, expected_revision=body.expected_revision, enabled=body.enabled, threshold=threshold)
                audit(request, "wallet.reminder.changed", actor_id=uid, object_kind="wallet", object_id=uid,
                      meta={"unit": unit.code, "revision": result["revision"], "enabled": body.enabled},
                      workspace_given=True)
            private(response)
            return result

    return router
