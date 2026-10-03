"""Confirmed governance of the fixed platform DeepSeek account's cost limits.

This surface neither enables a model route nor grants access to funds or business data.
The host chooses the environment; current authorization, immutable policy publication,
audit and the idempotent operation result share the same Store writer transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.billing import BillingError, Ledger
from synwevo_core.billing.catalogue import PriceUnit
from synwevo_core.billing.cost_settings import ACCOUNT_REF, PROVIDER, CostPolicySpec, CostSettings
from synwevo_core.billing.traffic import ModelTraffic
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical


class CostContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Revision = Annotated[int, Field(ge=0, le=2**63 - 1)]
ExpectedRevision = Annotated[int, Field(ge=0, lt=2**63 - 1)]
OperationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]


class CostVersion(CostContract):
    environment: Literal["test", "live"] | None
    provider: Literal["deepseek"]
    account_ref: Literal["platform-deepseek"]
    revision: Revision
    spec: CostPolicySpec | None
    actor_id: str | None
    created_at: int | None
    reason: str | None
    operation_id: str | None


class CostState(CostVersion):
    units: list[PriceUnit]


class CostHistory(CostContract):
    items: list[CostVersion]
    next_before_revision: Revision | None


class CostPreviewRequest(CostContract):
    expected_revision: ExpectedRevision
    spec: CostPolicySpec


class CostPublishRequest(CostPreviewRequest):
    preview_token: str = Field(min_length=1, max_length=8192)
    idempotency_key: OperationKey
    reason: str = Field(min_length=8, max_length=500)


class CostPreview(CostContract):
    before: CostState
    after: CostPolicySpec
    preview_token: str
    expires_at: float
    risk: Literal["high"]
    environment: Literal["test", "live"]


class TrafficUsage(CostContract):
    """Platform-wide counters only: no payer, conversation or request content."""
    environment: Literal["test", "live"] | None
    configured: bool
    as_of: int | None
    window_s: int
    requests: int
    tokens: int
    inflight: int
    waiting: int
    oldest_wait_s: int


class CostPublication(CostContract):
    operation_id: str
    status: Literal["succeeded"]
    state: CostState


class CostRoute(APIRoute):
    """Typed validation without exposing submitted values in errors or logs."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "cost_invalid", "成本策略字段无效，请检查金额、单位及调用上限") from None
            except ServiceError:
                raise
            except Exception:
                # The generic HTTP handler logs exception text. Keep unexpected persistence
                # failures inside this non-secret contract; the operation audit records a code.
                raise ServiceError(503, "cost_settings_unavailable", "成本策略暂不可用，请稍后重试") from None
        return handle


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CostOperations(SignedPreview):
    action = "admin.model_cost.publish"

    def __init__(self, store, audit, environment):
        self.store, self.audit, self.environment = store, audit, environment

    def _settings(self):
        return CostSettings(self.store, environment=self.environment)

    def _actor(self, request, *, view=False, execute=False):
        return require_platform(request, "platform.config.view" if view else "platform.config.manage",
                                store=self.store, audit=self.audit, dangerous=execute)

    def _audit(self, request, actor, *, result="ok", meta):
        self.audit(request, self.action, actor_id=actor["id"], object_kind="model_cost_policy",
                   object_id=ACCOUNT_REF, workspace_given=True, result=result,
                   meta={"billing_environment": self.environment, "provider": PROVIDER,
                         "origin": self._environment(request), **meta})

    def _failure(self, request, actor, exc):
        # No request body, preview token, exception message or unvalidated reason is logged.
        self._audit(request, actor, result=(
            "denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error"),
            meta={"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"})

    def read(self, request):
        self._actor(request, view=True)
        with self.store.tx():
            self._actor(request, view=True)
            state = self._settings().read() if self.environment else {
                "environment": None, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                "revision": 0, "spec": None, "units": [], "actor_id": None,
                "created_at": None, "reason": None, "operation_id": None}
            self._actor(request, view=True)
            return state

    def traffic(self, request):
        self._actor(request, view=True)
        empty = {"environment": self.environment, "configured": False, "as_of": None, "window_s": 60,
                 "requests": 0, "tokens": 0, "inflight": 0, "waiting": 0, "oldest_wait_s": 0}
        if not self.environment:
            return empty
        with self.store.tx():
            self._actor(request, view=True)
            ledger = Ledger(self.store, environment=self.environment, namespace="platform-cost-governance")
            try:
                usage = ModelTraffic(ledger, current_policy=self._settings().policy).snapshot()
            except BillingError as exc:
                if exc.code != "cost_not_configured":
                    raise
                return empty
            return {**usage, "environment": self.environment}

    def history(self, request, *, before_revision=None, limit=20):
        self._actor(request, view=True)
        with self.store.tx():
            self._actor(request, view=True)
            result = (self._settings().history(before_revision=before_revision, limit=limit)
                      if self.environment else {"items": [], "next_before_revision": None})
            self._actor(request, view=True)
            return result

    def preview(self, request, body: CostPreviewRequest):
        actor = self._actor(request)
        try:
            with self.store.tx():
                actor = self._actor(request)
                plan = self._settings().preview(body.spec, expected_revision=body.expected_revision)
                expires = time.time() + PREVIEW_SECONDS
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                           "target": ACCOUNT_REF, "provider": PROVIDER, "origin": self._environment(request),
                           "environment": self.environment, "expected_revision": body.expected_revision,
                           "before_digest": _digest(plan["before"]), "after": plan["after"],
                           "expires_at": expires, "nonce": new_id("plan")}
                result = {**plan, "environment": self.environment, "risk": "high",
                          "preview_token": self._sign(binding), "expires_at": expires}
                self._actor(request)
                return result
        except Exception as exc:
            self._failure(request, actor, exc)
            raise

    def publish(self, request, body: CostPublishRequest):
        actor = self._actor(request, execute=True)
        try:
            reason = body.reason.strip()
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            origin = self._environment(request)
            request_hash = _digest({"action": self.action, "target": ACCOUNT_REF, "provider": PROVIDER,
                "origin": origin, "environment": self.environment, "revision": body.expected_revision,
                "spec": body.spec.model_dump(), "token": body.preview_token, "reason": reason})
            with self.store.tx() as transaction:
                actor = self._actor(request, execute=True)
                settings = self._settings()
                previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                          (actor["id"], body.idempotency_key))
                if previous:
                    if previous["request_hash"] != request_hash or previous["action"] != self.action:
                        raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                    result = json.loads(previous["result_json"])
                    self._actor(request, execute=True)
                    return result
                signed = self._verify(body.preview_token)
                expected = {"actor": actor["id"], "action": self.action, "scope": "platform",
                            "target": ACCOUNT_REF, "provider": PROVIDER, "origin": origin,
                            "environment": self.environment, "expected_revision": body.expected_revision,
                            "after": body.spec.model_dump()}
                if any(signed.get(key) != value for key, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if signed.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                plan = settings.preview(body.spec, expected_revision=body.expected_revision)
                if signed.get("before_digest") != _digest(plan["before"]) or signed.get("after") != plan["after"]:
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                self._actor(request, execute=True)
                operation_id = new_id("ao")
                saved = settings.append(body.spec, expected_revision=body.expected_revision,
                                        actor_id=actor["id"], reason=reason, operation_id=operation_id)
                result = {"operation_id": operation_id, "status": "succeeded", "state": saved}
                self._audit(request, actor, meta={"operation_id": operation_id, "reason": reason,
                    "before_revision": plan["before"]["revision"], "revision": saved["revision"],
                    "before": plan["before"]["spec"], "after": saved["spec"]})
                transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,result_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (operation_id, actor["id"], self.action, ACCOUNT_REF,
                    body.idempotency_key, request_hash, canonical(result), time.time()))
                self._actor(request, execute=True)
                return result
        except Exception as exc:
            self._failure(request, actor, exc)
            raise


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/billing/cost-policy", tags=["admin"], route_class=CostRoute,
                       responses={400: {"description": "Invalid cost policy fields; submitted values are not echoed"},
                                  "4XX": {"description": "Current authorization, CSRF, authentication or version check refused"}})
    value = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT", "")
    environment = value if value in ("test", "live") else None
    operations = CostOperations(store, audit, environment)

    @router.get("", response_model=CostState)
    def read(request: Request):
        return operations.read(request)

    @router.get("/traffic", response_model=TrafficUsage)
    def traffic(request: Request):
        return operations.traffic(request)

    @router.get("/history", response_model=CostHistory)
    def history(request: Request, before_revision: int | None = Query(None, ge=0, lt=2**63 - 1),
                limit: int = Query(20, ge=1, le=100)):
        return operations.history(request, before_revision=before_revision, limit=limit)

    @router.post("/preview", response_model=CostPreview)
    def preview(request: Request, body: CostPreviewRequest = Body(...)):
        return operations.preview(request, body)

    @router.post("/publish", response_model=CostPublication)
    def publish(request: Request, body: CostPublishRequest = Body(...)):
        return operations.publish(request, body)

    return router
