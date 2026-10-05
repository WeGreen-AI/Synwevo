"""Platform administration of the paid-model risk controls (docs/platform/model-risk.md).

Reading needs `platform.config.view`; every change needs `platform.config.manage`, a reason, a
client idempotency key and, where it lets money flow again (closing the breaker, lifting a
suspension, allowlisting a payer), a fresh re-authentication. Authorization is re-checked inside
the writer transaction that changes state, records the audit row and the durable operation
result. Views carry amounts, codes and identifiers only — never conversation content.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.billing import BillingError
from synwevo_core.billing.cost_settings import CostSettings
from synwevo_core.billing.risk import RiskControls, allowlist_required
from synwevo_core.billing.upstream import DAY, Reconciliation, UpstreamBalance
from synwevo_core.errors import ServiceError
from synwevo_core.identity import normalise_email
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import canonical

OperationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
Reason = Annotated[str, Field(min_length=8, max_length=500)]
EntryId = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class RiskContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Change(RiskContract):
    reason: Reason
    idempotency_key: OperationKey


class AllowRequest(Change):
    payer_kind: Literal["user", "organization"]
    email: str | None = Field(default=None, min_length=3, max_length=320)
    organization_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")


class TopupRequest(Change):
    amount_units: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    topped_up_at: int = Field(ge=0, le=253402300799)


class RunRequest(Change):
    kind: Literal["daily", "ledger_audit"]
    day: str | None = Field(default=None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


class RiskRoute(APIRoute):
    """Typed validation without echoing submitted values; unexpected failures stay non-secret."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "risk_invalid", "风险控制请求字段无效，请检查理由、金额和日期") from None
            except ServiceError:
                raise
            except BillingError:
                raise ServiceError(409, "risk_unavailable", "风险控制状态暂不可用，请刷新后重试") from None
            except Exception:
                raise ServiceError(503, "risk_unavailable", "风险控制状态暂不可用，请刷新后重试") from None
        return handle


# Integer amounts travel as strings: a browser number cannot hold every 64-bit amount exactly.
MONEY = ("total_units", "threshold_units", "observed_units", "amount_units", "upstream_units", "topup_units",
         "pending_units", "tolerance_units")


def _money(row):
    if row is None:
        return None
    return {key: str(value) if key in MONEY and value is not None else value for key, value in row.items()}


def _digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class RiskOperations:
    def __init__(self, store, audit, environment):
        self.store, self.audit, self.environment = store, audit, environment

    # ------------------------------------------------------------------ helpers
    def _actor(self, request, *, manage=False, dangerous=False):
        return require_platform(request, "platform.config.manage" if manage else "platform.config.view",
                                store=self.store, audit=self.audit, dangerous=dangerous)

    def _require_environment(self):
        if self.environment not in ("test", "live"):
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")

    def _controls(self):
        return RiskControls(self.store, environment=self.environment)

    def _policy(self):
        try:
            return CostSettings(self.store, environment=self.environment).policy()
        except (BillingError, ServiceError):
            return None

    def _payers(self, actor, rows, kind_key, id_key):
        """Who a row names, for this administrator: an email only with account-list rights."""
        users = [r[id_key] for r in rows if r[kind_key] == "user"]
        orgs = [r[id_key] for r in rows if r[kind_key] == "organization"]
        names = {}
        if users and "platform.users.view" in actor["platform_permissions"]:
            marks = ",".join("?" * len(users))
            names.update({r["id"]: r["email"] for r in self.store.all(
                f"SELECT id,email FROM users WHERE id IN ({marks})", tuple(users))})
        if orgs and "platform.orgs.view" in actor["platform_permissions"]:
            marks = ",".join("?" * len(orgs))
            names.update({r["id"]: r["name"] for r in self.store.all(
                f"SELECT id,name FROM organizations WHERE id IN ({marks})", tuple(orgs))})
        return [{**r, "payer_label": names.get(r[id_key]) if r[kind_key] != "route" else None} for r in rows]

    def _previous(self, actor, body, action, request_hash):
        previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                  (actor["id"], body.idempotency_key))
        if previous and (previous["request_hash"] != request_hash or previous["action"] != action):
            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
        return json.loads(previous["result_json"]) if previous else None

    def _change(self, request, action, target, body, run, *, dangerous, own_transaction=False):
        """Authorize, idempotency, state change, audit and operation result in one transaction.

        `own_transaction`: the change opens its own (the ledger audit must), so it runs between
        an authorized idempotency check and the transaction that records audit and result. Only
        append-only reconciliation results use it; repeating one is a second result, not harm."""
        # Authority first: whether billing is configured is not for callers who may not change it.
        actor = self._actor(request, manage=True, dangerous=dangerous)
        self._require_environment()
        reason = body.reason.strip()
        if not 8 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        request_hash = _digest({"action": action, "target": target, "environment": self.environment,
                                "body": body.model_dump(exclude={"idempotency_key"})})
        try:
            outcome = None
            if own_transaction:
                with self.store.tx():
                    actor = self._actor(request, manage=True, dangerous=dangerous)
                    previous = self._previous(actor, body, action, request_hash)
                if previous:
                    return previous
                outcome = run(actor, reason)
            with self.store.tx() as transaction:
                actor = self._actor(request, manage=True, dangerous=dangerous)
                previous = self._previous(actor, body, action, request_hash)
                if previous:
                    return previous
                state, meta = outcome or run(actor, reason)
                operation_id = new_id("ao")
                result = {"operation_id": operation_id, "status": "succeeded", "item": _money(state)}
                self.audit(request, action, actor_id=actor["id"], object_kind="model_risk", object_id=target,
                           workspace_given=True, result="ok",
                           meta={"billing_environment": self.environment, "operation_id": operation_id,
                                 "reason": reason, **meta})
                transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,"
                                    "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                    (operation_id, actor["id"], action, target, body.idempotency_key, request_hash,
                                     canonical(result), time.time()))
                self._actor(request, manage=True, dangerous=dangerous)
                return result
        except Exception as exc:
            code = exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"
            self.audit(request, action, actor_id=actor["id"], object_kind="model_risk", object_id=target,
                       workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                       meta={"billing_environment": self.environment, "code": code})
            raise

    # ------------------------------------------------------------------ reads
    def overview(self, request):
        self._actor(request)
        if self.environment not in ("test", "live"):
            return {"environment": None, "configured": False, "unit": None, "risk": None, "max_call_cost": None,
                    "allowlist_required": False, "allowlist_source": "default", "breaker": None, "balance": None,
                    "active_suspensions": 0, "allowlisted": 0}
        with self.store.tx():
            self._actor(request)
            policy = self._policy()
            controls = self._controls()
            readings = UpstreamBalance(self.store, environment=self.environment).readings(limit=1)
            counts = self.store.one(
                "SELECT (SELECT COUNT(*) FROM model_risk_suspensions WHERE environment=? AND lifted_at IS NULL) AS s,"
                "(SELECT COUNT(*) FROM model_risk_allowlist WHERE environment=? AND removed_at IS NULL) AS a",
                (self.environment, self.environment))
            risk = policy.risk if policy else None
            spec = CostSettings(self.store, environment=self.environment).read()["spec"] if policy else None
            return {"environment": self.environment, "configured": policy is not None,
                    "unit": {"code": policy.unit.code, "scale": policy.unit.scale} if policy else None,
                    "risk": (spec or {}).get("risk"), "max_call_cost": (spec or {}).get("max_call_cost"),
                    "allowlist_required": allowlist_required(self.environment, risk),
                    "allowlist_source": "policy" if risk is not None else "default",
                    "breaker": controls.breaker(), "balance": _money(readings[0]) if readings else None,
                    "active_suspensions": counts["s"], "allowlisted": counts["a"]}

    def suspensions(self, request, *, state, before):
        actor = self._actor(request)
        if self.environment not in ("test", "live"):
            return {"items": [], "next_before": None}
        result = self._controls().suspensions(active_only=state == "active", before=before)
        return {**result, "items": [_money(r) for r in self._payers(actor, result["items"], "scope_kind", "scope_id")]}

    def allowlist(self, request, *, before):
        actor = self._actor(request)
        if self.environment not in ("test", "live"):
            return {"items": [], "next_before": None}
        result = self._controls().allowlist(before=before)
        return {**result, "items": self._payers(actor, result["items"], "payer_kind", "payer_id")}

    def breaker_history(self, request):
        self._actor(request)
        if self.environment not in ("test", "live"):
            return {"items": []}
        return {"items": self._controls().breaker_history()}

    def balance(self, request):
        self._actor(request)
        if self.environment not in ("test", "live"):
            return {"readings": [], "topups": []}
        return {"readings": [_money(r) for r in UpstreamBalance(self.store, environment=self.environment).readings()],
                "topups": [_money(r) for r in Reconciliation(self.store, environment=self.environment).topups()]}

    def reconciliations(self, request, *, kind, before):
        self._actor(request)
        if self.environment not in ("test", "live"):
            return {"items": [], "next_before": None}
        result = Reconciliation(self.store, environment=self.environment).history(kind=kind, before=before)
        return {**result, "items": [_money(r) for r in result["items"]]}

    # ------------------------------------------------------------------ changes
    def lift(self, request, suspension_id, body: Change):
        def run(actor, reason):
            row = self._controls().lift(suspension_id, actor_id=actor["id"], reason=reason)
            return row, {"scope_kind": row["scope_kind"], "scope_id": row["scope_id"]}
        return self._change(request, "admin.model_risk.suspension_lift", suspension_id, body, run, dangerous=True)

    def open_breaker(self, request, body: Change):
        def run(actor, reason):
            before = self._controls().breaker()
            if before["state"] == "open":
                raise ServiceError(409, "risk_breaker_open", "付费调用总闸已经关闭了付费调用")
            event = self._controls().open_breaker(source="admin", reason_code="admin_opened", actor_id=actor["id"],
                                                  reason=reason)
            return event, {"state": "open", "seq": event["seq"]}
        return self._change(request, "admin.model_risk.breaker_open", "platform-breaker", body, run, dangerous=False)

    def close_breaker(self, request, body: Change):
        def run(actor, reason):
            event = self._controls().close_breaker(actor_id=actor["id"], reason=reason)
            return event, {"state": "closed", "seq": event["seq"]}
        return self._change(request, "admin.model_risk.breaker_close", "platform-breaker", body, run, dangerous=True)

    def allow(self, request, body: AllowRequest):
        def run(actor, reason):
            if body.payer_kind == "user":
                user = self.store.one("SELECT id FROM users WHERE email_key=? AND deleted_at IS NULL",
                                      (normalise_email(body.email),)) if body.email else None
                if not user:
                    raise ServiceError(404, "risk_allowlist_payer_not_found", "未找到要放行的账户或组织")
                payer = user["id"]
            else:
                if not body.organization_id:
                    raise ServiceError(404, "risk_allowlist_payer_not_found", "未找到要放行的账户或组织")
                payer = body.organization_id
            row = self._controls().allow(body.payer_kind, payer, actor_id=actor["id"], reason=reason)
            return self._payers(actor, [row], "payer_kind", "payer_id")[0], {"payer_kind": body.payer_kind, "payer_id": payer}
        return self._change(request, "admin.model_risk.allowlist_add", "allowlist", body, run, dangerous=True)

    def disallow(self, request, entry_id, body: Change):
        def run(actor, reason):
            row = self._controls().disallow(entry_id, actor_id=actor["id"], reason=reason)
            return row, {"payer_kind": row["payer_kind"], "payer_id": row["payer_id"]}
        return self._change(request, "admin.model_risk.allowlist_remove", entry_id, body, run, dangerous=False)

    def topup(self, request, body: TopupRequest):
        def run(actor, reason):
            row = Reconciliation(self.store, environment=self.environment).record_topup(
                amount_units=int(body.amount_units), topped_up_at=body.topped_up_at, actor_id=actor["id"], reason=reason)
            return row, {"amount_units": row["amount_units"], "unit": row["unit"], "topped_up_at": row["topped_up_at"]}
        return self._change(request, "admin.model_risk.upstream_topup", "upstream-topups", body, run, dangerous=False)

    def run(self, request, body: RunRequest):
        def run(actor, reason):
            reconciliation = Reconciliation(self.store, environment=self.environment)
            if body.kind == "ledger_audit":
                row = reconciliation.ledger_audit(trigger="admin", actor_id=actor["id"])
            else:
                try:
                    start = int(datetime.strptime(body.day or "", "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
                except ValueError:
                    raise ServiceError(400, "risk_invalid", "风险控制请求字段无效，请检查理由、金额和日期") from None
                row = reconciliation.daily(start - start % DAY, trigger="admin", actor_id=actor["id"])
            return row, {"kind": row["kind"], "status": row["status"], "window_start": row["window_start"]}
        return self._change(request, "admin.model_risk.reconcile", "reconciliation", body, run, dangerous=False,
                            own_transaction=True)


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/billing/risk", tags=["admin"], route_class=RiskRoute,
                       responses={400: {"description": "Invalid risk control fields; submitted values are not echoed"},
                                  "4XX": {"description": "Current authorization, CSRF, re-authentication or state check refused"}})
    value = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT", "")
    operations = RiskOperations(store, audit, value if value in ("test", "live") else None)

    @router.get("")
    def overview(request: Request):
        return operations.overview(request)

    @router.get("/suspensions")
    def suspensions(request: Request, state: Literal["active", "all"] = Query("active"),
                    before: int | None = Query(None, ge=0, le=2**63 - 1)):
        return operations.suspensions(request, state=state, before=before)

    @router.post("/suspensions/{suspension_id}/lift")
    def lift(request: Request, suspension_id: EntryId, body: Change = Body(...)):
        return operations.lift(request, suspension_id, body)

    @router.get("/breaker")
    def breaker(request: Request):
        return operations.breaker_history(request)

    @router.post("/breaker/open")
    def open_breaker(request: Request, body: Change = Body(...)):
        return operations.open_breaker(request, body)

    @router.post("/breaker/close")
    def close_breaker(request: Request, body: Change = Body(...)):
        return operations.close_breaker(request, body)

    @router.get("/allowlist")
    def allowlist(request: Request, before: int | None = Query(None, ge=0, le=2**63 - 1)):
        return operations.allowlist(request, before=before)

    @router.post("/allowlist")
    def allow(request: Request, body: AllowRequest = Body(...)):
        return operations.allow(request, body)

    @router.post("/allowlist/{entry_id}/remove")
    def disallow(request: Request, entry_id: EntryId, body: Change = Body(...)):
        return operations.disallow(request, entry_id, body)

    @router.get("/balance")
    def balance(request: Request):
        return operations.balance(request)

    @router.post("/topups")
    def topup(request: Request, body: TopupRequest = Body(...)):
        return operations.topup(request, body)

    @router.get("/reconciliations")
    def reconciliations(request: Request, kind: Literal["daily", "ledger_audit"] | None = Query(None),
                        before: int | None = Query(None, ge=0, le=2**63 - 1)):
        return operations.reconciliations(request, kind=kind, before=before)

    @router.post("/reconciliations/run")
    def run(request: Request, body: RunRequest = Body(...)):
        return operations.run(request, body)

    return router
