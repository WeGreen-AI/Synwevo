"""Govern versioned model prices without granting model or wallet access.

The host fixes the billing environment at startup. Every write checks current platform
permission inside Store.tx(); publishing also requires fresh admin authentication and a
signed preview. Price, provenance, audit and the idempotent result commit together.
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

from synwevo_core.billing.catalogue import PriceCatalogue, PriceSpec, PriceUnit, _key, _revision
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical


class PriceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Revision = Annotated[int, Field(ge=1, lt=2**63 - 1)]
OperationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
TokenCount = Annotated[int, Field(ge=0, le=10**9)]


class PriceCreate(PriceContract):
    spec: PriceSpec
    idempotency_key: OperationKey


class PriceRevision(PriceContract):
    expected_revision: Revision


class PriceUpdate(PriceRevision):
    spec: PriceSpec


class PricePublish(PriceRevision):
    preview_token: str = Field(min_length=1, max_length=8192)
    idempotency_key: OperationKey
    reason: str = Field(min_length=8, max_length=500)


class PriceCalculate(PriceRevision):
    input_uncached: TokenCount
    input_cached: TokenCount
    output: TokenCount


class PriceDraft(PriceContract):
    id: str
    revision: Revision
    status: Literal["draft", "published"]
    price_id: str | None
    actor_id: str
    created_at: int
    updated_at: int
    spec: PriceSpec


class PriceList(PriceContract):
    environment: Literal["test", "live"] | None
    items: list[PriceDraft]
    next_before: str | None


class PricePreview(PriceContract):
    draft: PriceDraft
    environment: Literal["test", "live"]
    risk: Literal["high"]
    preview_token: str
    expires_at: float


class PricePublication(PriceContract):
    operation_id: str
    status: Literal["succeeded"]
    draft: PriceDraft


class ExactAmount(PriceContract):
    numerator: str
    denominator: str


class PriceCalculation(PriceContract):
    unit: PriceUnit
    exact: ExactAmount
    display_amount: str
    charge_units_if_no_prior_remainder: str
    per_tokens: Literal[1000000]


class PriceRoute(APIRoute):
    """Keep typed OpenAPI validation from reflecting submitted credentials in errors."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "price_invalid", "价格字段无效，请检查单位、费率和生效时间") from None
        return handle


def _only(body, required, optional=()):
    if not isinstance(body, dict) or not set(required) <= body.keys() or set(body) - set(required) - set(optional):
        raise ServiceError(400, "price_invalid", "价格字段无效，请检查单位、费率和生效时间")


class PriceOperations(SignedPreview):
    action = "admin.model_price.publish"

    def __init__(self, store, audit, environment):
        self.store, self.audit, self.environment = store, audit, environment

    def _catalogue(self):
        return PriceCatalogue(self.store, environment=self.environment)

    def _actor(self, request, *, view=False, execute=False):
        return require_platform(request, "platform.config.view" if view else "platform.config.manage",
                                store=self.store, audit=self.audit, dangerous=execute)

    def _audit(self, request, actor, action, *, target, result="ok", meta):
        self.audit(request, action, actor_id=actor["id"], object_kind="model_price", object_id=target,
                   workspace_given=True, result=result, meta={"billing_environment": self.environment,
                       "origin": self._environment(request), **meta})

    def _failure(self, request, actor, action, target, exc):
        self._audit(request, actor, action, target=target, result=(
            "denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error"),
            meta={"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"})

    def create(self, request, body):
        actor, action = self._actor(request), "admin.model_price.draft"
        try:
            _only(body, ("spec", "idempotency_key"))
            with self.store.tx():
                actor = self._actor(request)
                item, changed = self._catalogue().create(spec=body["spec"], actor_id=actor["id"], idempotency_key=body["idempotency_key"])
                if changed:
                    self._audit(request, actor, action, target=item["id"], meta={"revision": item["revision"], "after": item["spec"]})
                return item
        except Exception as exc:
            self._failure(request, actor, action, None, exc)
            raise

    def update(self, request, draft_id, body):
        actor, action = self._actor(request), "admin.model_price.draft"
        try:
            _only(body, ("spec", "expected_revision"))
            with self.store.tx():
                actor = self._actor(request)
                catalog = self._catalogue()
                before = catalog.get(draft_id)
                item, changed = catalog.update(draft_id, **body, actor_id=actor["id"])
                if changed:
                    self._audit(request, actor, action, target=draft_id,
                                meta={"revision": item["revision"], "before": before["spec"], "after": item["spec"]})
                return item
        except Exception as exc:
            self._failure(request, actor, action, draft_id, exc)
            raise

    def preview(self, request, draft_id, body):
        actor = self._actor(request)
        try:
            _only(body, ("expected_revision",))
            with self.store.tx():
                actor = self._actor(request)
                draft = self._catalogue().validate_publication(draft_id, body["expected_revision"])
                expires = time.time() + PREVIEW_SECONDS
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                           "target": draft_id, "origin": self._environment(request), "environment": self.environment,
                           "draft": draft, "expires_at": expires, "nonce": new_id("plan")}
                return {"draft": draft, "environment": self.environment, "risk": "high",
                        "preview_token": self._sign(binding), "expires_at": expires}
        except Exception as exc:
            self._failure(request, actor, self.action, draft_id, exc)
            raise

    def publish(self, request, draft_id, body):
        actor = self._actor(request, execute=True)
        try:
            _only(body, ("expected_revision", "preview_token", "idempotency_key", "reason"))
            revision, key = _revision(body["expected_revision"]), _key(body["idempotency_key"])
            token, raw_reason = body["preview_token"], body["reason"]
            reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            origin = self._environment(request)
            request_hash = hashlib.sha256(canonical({"action": self.action, "target": draft_id, "origin": origin,
                "environment": self.environment, "revision": revision, "token": token, "reason": reason}).encode()).hexdigest()
            with self.store.tx() as transaction:
                actor = self._actor(request, execute=True)
                catalog = self._catalogue()
                previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
                if previous:
                    if previous["request_hash"] != request_hash or previous["action"] != self.action:
                        raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                    return json.loads(previous["result_json"])
                signed = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "target": draft_id, "origin": origin,
                            "scope": "platform", "environment": self.environment}
                if any(signed.get(k) != v for k, v in expected.items()) or signed.get("draft", {}).get("revision") != revision:
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if signed.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                draft = catalog.validate_publication(draft_id, revision)
                if signed.get("draft") != draft:
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                operation_id = new_id("ao")
                saved = catalog.publish(draft_id, expected_revision=revision, actor_id=actor["id"],
                                        operation_id=operation_id, reason=reason)
                result = {"operation_id": operation_id, "status": "succeeded", "draft": saved}
                self._audit(request, actor, self.action, target=draft_id, meta={"operation_id": operation_id,
                    "reason": reason, "price_id": saved["price_id"], "draft_revision": revision, "snapshot": saved["spec"]})
                transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,result_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (operation_id, actor["id"], self.action, draft_id, key, request_hash, canonical(result), time.time()))
                return result
        except Exception as exc:
            self._failure(request, actor, self.action, draft_id, exc)
            raise


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/billing/prices", tags=["admin"], route_class=PriceRoute,
                       responses={400: {"description": "Invalid price fields; submitted values are not echoed"},
                                  "4XX": {"description": "Current authorization, CSRF, authentication or version check refused"}})
    # Captured from the trusted host, never a browser parameter. Missing/invalid means closed.
    value = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT", "")
    environment = value if value in ("test", "live") else None
    operations = PriceOperations(store, audit, environment)

    @router.get("", response_model=PriceList)
    def listing(request: Request, before: str | None = Query(None), limit: int = Query(20)):
        operations._actor(request, view=True)
        return (operations._catalogue().list(before=before, limit=limit) if environment else
                {"environment": None, "items": [], "next_before": None})

    @router.get("/drafts/{draft_id}", response_model=PriceDraft)
    def detail(request: Request, draft_id: str):
        operations._actor(request, view=True)
        return operations._catalogue().get(draft_id)

    @router.post("/drafts", response_model=PriceDraft)
    def create(request: Request, body: PriceCreate = Body(...)):
        return operations.create(request, body.model_dump())

    @router.patch("/drafts/{draft_id}", response_model=PriceDraft)
    def update(request: Request, draft_id: str, body: PriceUpdate = Body(...)):
        return operations.update(request, draft_id, body.model_dump())

    @router.post("/drafts/{draft_id}/preview", response_model=PricePreview)
    def preview(request: Request, draft_id: str, body: PriceRevision = Body(...)):
        return operations.preview(request, draft_id, body.model_dump())

    @router.post("/drafts/{draft_id}/publish", response_model=PricePublication)
    def publish(request: Request, draft_id: str, body: PricePublish = Body(...)):
        return operations.publish(request, draft_id, body.model_dump())

    @router.post("/drafts/{draft_id}/calculate", response_model=PriceCalculation)
    def calculate(request: Request, draft_id: str, body: PriceCalculate = Body(...)):
        operations._actor(request, view=True)
        return operations._catalogue().calculate(draft_id, **body.model_dump())

    return router
