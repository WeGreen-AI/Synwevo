"""Personal wallet projections; mount before the account application's SPA catch-all.

The positional dependencies match account_routes.build_router. The host must additionally
inject its environment-bound Ledger (using the same Store) and explicitly configured
Units. Missing configuration returns 503; this module invents no unit, price or environment.
Only the admitted central session determines the payer. No admin or workspace override.

GET /api/auth/wallet serializes Ledger.overview_for_user's single atomic projection of
booked/used/held/available per unit and its used_through_seq watermark. The route neither
scans statements nor computes money totals, and never reads platform costs.
GET /api/auth/wallet/statement uses ascending, exclusive after_seq pagination (limit 1..100).
Amounts and sequence cursors are decimal strings; scale means decimal places in the unit.

There are no money mutations here; expiry and all balance semantics belong to Ledger.
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


class WalletBalance(WalletModel):
    environment: Literal["test", "live"]
    payer_user_id: str
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
    payer_user_id: str
    items: list[WalletEntry]
    next_after_seq: str | None


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


@contextmanager
def _safe_errors() -> Iterator[None]:
    try:
        yield
    except ServiceError:
        raise
    except BillingError as exc:
        if exc.code == "account_unavailable":
            raise ServiceError(401, "unauthorized", "请先登录可用账户") from None
        if exc.code == "wallet_forbidden":
            raise ServiceError(403, "wallet_forbidden", "无权查看此钱包") from None
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


def build_router(ident: Identity, sessions: Sessions, mailer: Mailer,
                 audit: Callable[..., None], *, ledger: Ledger | None = None,
                 units: Sequence[Unit] = ()) -> APIRouter:
    """Use the account factory's existing dependencies; never authenticate a new way.

    mailer/audit are retained for the account factory contract; read-only projections
    send no mail and do not put financial details in audit logs.
    """
    router = APIRouter(prefix="/api/auth/wallet", tags=["account"])
    environment = ledger.environment if ledger is not None else None
    wallet_units = tuple(units)

    def current(request: Request) -> str:
        live = sessions.current(getattr(request.state, "session", None))
        user = ident.get_user(live["user_id"]) if live else None
        if not user or user["status"] != "active" or user.get("deleted_at") is not None:
            raise ServiceError(401, "unauthorized", "请先登录可用账户")
        return user["id"]

    def configured() -> tuple[Ledger, Literal["test", "live"]]:
        if (ledger is None or not 1 <= len(wallet_units) <= 16 or environment not in ("test", "live")
                or ledger.environment != environment or ledger.store is not ident.store
                or sessions.store is not ident.store
                or any(not isinstance(unit, Unit) for unit in wallet_units)
                or len({unit.code for unit in wallet_units}) != len(wallet_units)):
            raise _unavailable()
        return ledger, environment

    @router.get("", response_model=WalletBalance)
    def balance(request: Request, response: Response) -> WalletBalance:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, set())
            service, mode = configured()
            overview = service.overview_for_user(requester_user_id=uid, payer_user_id=uid,
                                                 units=wallet_units)
            balances = [UnitBalance(unit=row["unit"], scale=row["scale"],
                                    booked=str(row["booked"]), used=str(row["used"]),
                                    held=str(row["held"]), available=str(row["available"]))
                        for row in overview["balances"]]
            result = WalletBalance(environment=mode, payer_user_id=uid, balances=balances,
                                   used_through_seq=str(overview["used_through_seq"]))
            current(request)  # A revoked session must not receive a completed projection.
            response.headers["Cache-Control"] = "private, no-store"
            return result

    @router.get("/statement", response_model=WalletStatement)
    def statement(request: Request, response: Response,
                  after_seq: str = Query("0", description="Exclusive decimal sequence, 0..2^63-1"),
                  limit: str = Query("50", description="Decimal page size, 1..100")) -> WalletStatement:
        with _safe_errors():
            uid = current(request)
            _query_fields(request, {"after_seq", "limit"})
            cursor = _decimal(after_seq, minimum=0, maximum=MAX_INTEGER)
            size = _decimal(limit, minimum=1, maximum=100)
            service, mode = configured()
            rows = service.statement_for_user(requester_user_id=uid, payer_user_id=uid,
                                              after_seq=cursor, limit=size + 1)
            # Explicit output allowlist even if the service later grows internal fields.
            items = [WalletEntry(seq=str(row["seq"]), id=row["id"], kind=row["kind"],
                                 unit=row["unit"], scale=row["scale"], delta=str(row["delta"]),
                                 created_at=row["created_at"]) for row in rows[:size]]
            result = WalletStatement(environment=mode, payer_user_id=uid, items=items,
                                     next_after_seq=items[-1].seq if len(rows) > size else None)
            current(request)
            response.headers["Cache-Control"] = "private, no-store"
            return result

    return router
