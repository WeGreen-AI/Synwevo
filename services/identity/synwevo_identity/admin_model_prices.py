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

from synwevo_core.billing.bands import SCHEDULE_ID, BandSchedules, ScheduleSpec, published_defaults
from synwevo_core.billing.catalogue import (MarkupSpec, PriceCatalogue, PriceMarkups, PriceSpec,
                                            PriceUnit, _key, _revision)
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


class BandAmount(PriceContract):
    exact: ExactAmount
    display_amount: str
    charge_units_if_no_prior_remainder: str


class PriceCalculation(PriceContract):
    """Top-level amount: flat, or peak for a time-banded draft; ``bands`` then holds both bands."""
    unit: PriceUnit
    exact: ExactAmount
    display_amount: str
    charge_units_if_no_prior_remainder: str
    per_tokens: Literal[1000000]
    bands: dict[Literal["peak", "off_peak"], BandAmount] | None = None


Band = Literal["flat", "peak", "off_peak"]
TokenClass = Literal["input_uncached", "input_cached", "output"]
RateTable = dict[Band, dict[TokenClass, str]]


class ScheduleVersion(PriceContract):
    environment: Literal["test", "live"] | None
    schedule_id: Literal["deepseek"]
    revision: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    spec: ScheduleSpec | None
    actor_id: str | None
    created_at: int | None
    reason: str | None
    operation_id: str | None


class ScheduleState(ScheduleVersion):
    defaults: ScheduleSpec


class ScheduleHistory(PriceContract):
    items: list[ScheduleVersion]
    next_before_revision: int | None


class SchedulePreviewRequest(PriceContract):
    expected_revision: Annotated[int, Field(ge=0, lt=2**63 - 1)]
    spec: ScheduleSpec


class SchedulePublishRequest(SchedulePreviewRequest):
    preview_token: str = Field(min_length=1, max_length=8192)
    idempotency_key: OperationKey
    reason: str = Field(min_length=8, max_length=500)


class ScheduleChanges(PriceContract):
    holidays_added: list[str]
    holidays_removed: list[str]
    holidays_renamed: list[str]
    windows_changed: bool


class SchedulePreview(PriceContract):
    before: ScheduleVersion
    after: ScheduleSpec
    changes: ScheduleChanges
    preview_token: str
    expires_at: float
    risk: Literal["high"]
    environment: Literal["test", "live"]


class SchedulePublication(PriceContract):
    operation_id: str
    status: Literal["succeeded"]
    state: ScheduleVersion
    changes: ScheduleChanges


class MarkupPreviewRequest(PriceContract):
    spec: MarkupSpec


class MarkupPublishRequest(MarkupPreviewRequest):
    preview_token: str = Field(min_length=1, max_length=8192)
    idempotency_key: OperationKey
    reason: str = Field(min_length=8, max_length=500)


class ExampleAmount(PriceContract):
    cost: str
    sale: str


class MarkupPreview(PriceContract):
    spec: MarkupSpec
    model: str
    unit: PriceUnit
    cost_rates: RateTable
    sale_rates: RateTable
    example_tokens: Literal[1000]
    examples: dict[Band, dict[TokenClass, ExampleAmount]]
    replaces_price_id: str | None
    preview_token: str
    expires_at: float
    risk: Literal["high"]
    environment: Literal["test", "live"]


class MarkupVersion(PriceContract):
    price_id: str
    model: str
    spec: MarkupSpec
    unit: PriceUnit
    sale_rates: RateTable
    actor_id: str
    reason: str
    operation_id: str
    created_at: int


class MarkupList(PriceContract):
    environment: Literal["test", "live"] | None
    items: list[MarkupVersion]


class MarkupPublication(PriceContract):
    operation_id: str
    status: Literal["succeeded"]
    markup: MarkupVersion


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


class ScheduleRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "band_schedule_invalid", "高峰时段或节假日字段无效，请检查星期、时间与日期") from None
        return handle


class MarkupRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "price_markup_invalid", "加价字段无效：倍率须为 1 至 100、最多四位小数，并填写条款引用与生效时间") from None
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


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class _Governed(SignedPreview):
    """Shared confirmed-publication flow: preview binds actor/origin/environment/before/after;
    publish rechecks authority under the writer lock and commits result, audit and data together."""
    action = object_kind = ""

    def __init__(self, store, audit, environment):
        self.store, self.audit, self.environment = store, audit, environment

    def _actor(self, request, *, view=False, execute=False):
        return require_platform(request, "platform.config.view" if view else "platform.config.manage",
                                store=self.store, audit=self.audit, dangerous=execute)

    def _audit(self, request, actor, *, target, result="ok", meta):
        self.audit(request, self.action, actor_id=actor["id"], object_kind=self.object_kind, object_id=target,
                   workspace_given=True, result=result, meta={"billing_environment": self.environment,
                       "origin": self._environment(request), **meta})

    def _failure(self, request, actor, target, exc):
        # No request body, token, exception text or unvalidated reason enters the audit.
        self._audit(request, actor, target=target, result=(
            "denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error"),
            meta={"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"})

    def _ready(self):
        if not self.environment:
            raise ServiceError(503, "billing_not_configured", "此部署尚未配置计费环境")

    def _confirm(self, request, *, body, target, binding, plan, execute):
        """Run ``execute(actor, operation_id)`` once for a signed, unexpired, unchanged preview."""
        actor = self._actor(request, execute=True)
        try:
            self._ready()
            reason = body.reason.strip()
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            origin = self._environment(request)
            request_hash = _digest({"action": self.action, "target": target, "origin": origin, "environment": self.environment,
                                    "binding": binding, "token": body.preview_token, "reason": reason})
            with self.store.tx() as transaction:
                actor = self._actor(request, execute=True)
                previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                          (actor["id"], body.idempotency_key))
                if previous:
                    if previous["request_hash"] != request_hash or previous["action"] != self.action:
                        raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                    return json.loads(previous["result_json"])
                signed = self._verify(body.preview_token)
                expected = {"actor": actor["id"], "action": self.action, "scope": "platform", "target": target,
                            "origin": origin, "environment": self.environment, "binding": binding}
                if any(signed.get(key) != value for key, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if signed.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                if signed.get("plan") != _digest(plan()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                operation_id = new_id("ao")
                result, meta = execute(actor, operation_id, reason)
                self._audit(request, actor, target=target, meta={"operation_id": operation_id, "reason": reason, **meta})
                transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,result_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (operation_id, actor["id"], self.action, target, body.idempotency_key,
                                                 request_hash, canonical(result), time.time()))
                self._actor(request, execute=True)
                return result
        except Exception as exc:
            self._failure(request, actor, target, exc)
            raise

    def _preview(self, request, *, target, binding, plan):
        actor = self._actor(request)
        try:
            self._ready()
            with self.store.tx():
                actor = self._actor(request)
                result = plan()
                expires = time.time() + PREVIEW_SECONDS
                token = self._sign({"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                                    "target": target, "origin": self._environment(request), "environment": self.environment,
                                    "binding": binding, "plan": _digest(result), "expires_at": expires, "nonce": new_id("plan")})
                return {**result, "environment": self.environment, "risk": "high", "preview_token": token, "expires_at": expires}
        except Exception as exc:
            self._failure(request, actor, target, exc)
            raise


class ScheduleOperations(_Governed):
    """Peak windows and the public-holiday calendar; a change re-bands future dispatches only."""
    action, object_kind = "admin.model_price.schedule.publish", "model_price_schedule"

    def _schedules(self):
        return BandSchedules(self.store, environment=self.environment)

    def read(self, request):
        self._actor(request, view=True)
        state = self._schedules().read() if self.environment else {
            "environment": None, "schedule_id": SCHEDULE_ID, "revision": 0, "spec": None, "actor_id": None,
            "created_at": None, "reason": None, "operation_id": None}
        return {**state, "defaults": published_defaults()}

    def history(self, request, *, before_revision=None, limit=20):
        self._actor(request, view=True)
        if not self.environment:
            return {"items": [], "next_before_revision": None}
        return self._schedules().history(before_revision=before_revision, limit=limit)

    def preview(self, request, body: SchedulePreviewRequest):
        spec = body.spec.model_dump()
        return self._preview(request, target=SCHEDULE_ID, binding={"expected_revision": body.expected_revision, "after": spec},
                             plan=lambda: self._schedules().preview(spec, expected_revision=body.expected_revision))

    def publish(self, request, body: SchedulePublishRequest):
        spec = body.spec.model_dump()

        def execute(actor, operation_id, reason):
            saved = self._schedules().append(spec, expected_revision=body.expected_revision, actor_id=actor["id"],
                                             reason=reason, operation_id=operation_id)
            changes = saved.pop("changes")
            return ({"operation_id": operation_id, "status": "succeeded", "state": saved, "changes": changes},
                    {"revision": saved["revision"], "changes": changes, "after": saved["spec"]})
        return self._confirm(request, body=body, target=SCHEDULE_ID,
                             binding={"expected_revision": body.expected_revision, "after": spec},
                             plan=lambda: self._schedules().preview(spec, expected_revision=body.expected_revision),
                             execute=execute)


class MarkupOperations(_Governed):
    """Sale price = published cost price x markup, published as a new immutable sale version."""
    action, object_kind = "admin.model_price.markup.publish", "model_price_markup"

    def _markups(self):
        return PriceMarkups(self.store, environment=self.environment)

    def listing(self, request):
        self._actor(request, view=True)
        return self._markups().list() if self.environment else {"environment": None, "items": []}

    def preview(self, request, body: MarkupPreviewRequest):
        spec = body.spec.model_dump()
        return self._preview(request, target=spec["cost_price_id"], binding={"spec": spec},
                             plan=lambda: self._markups().preview(spec))

    def publish(self, request, body: MarkupPublishRequest):
        spec = body.spec.model_dump()

        def execute(actor, operation_id, reason):
            saved = self._markups().publish(spec, actor_id=actor["id"], operation_id=operation_id, reason=reason)
            return ({"operation_id": operation_id, "status": "succeeded", "markup": saved},
                    {"price_id": saved["price_id"], "model": saved["model"], "spec": spec, "sale_rates": saved["sale_rates"]})
        return self._confirm(request, body=body, target=spec["cost_price_id"], binding={"spec": spec},
                             plan=lambda: self._markups().preview(spec), execute=execute)


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

    errors = {"4XX": {"description": "Current authorization, CSRF, authentication or version check refused"}}
    schedule = ScheduleOperations(store, audit, environment)
    bands = APIRouter(prefix="/schedule", tags=["admin"], route_class=ScheduleRoute,
                      responses={400: {"description": "Invalid schedule fields; submitted values are not echoed"}, **errors})

    @bands.get("", response_model=ScheduleState)
    def schedule_read(request: Request):
        return schedule.read(request)

    @bands.get("/history", response_model=ScheduleHistory)
    def schedule_history(request: Request, before_revision: int | None = Query(None, ge=0, lt=2**63 - 1),
                         limit: int = Query(20, ge=1, le=100)):
        return schedule.history(request, before_revision=before_revision, limit=limit)

    @bands.post("/preview", response_model=SchedulePreview)
    def schedule_preview(request: Request, body: SchedulePreviewRequest = Body(...)):
        return schedule.preview(request, body)

    @bands.post("/publish", response_model=SchedulePublication)
    def schedule_publish(request: Request, body: SchedulePublishRequest = Body(...)):
        return schedule.publish(request, body)

    markup = MarkupOperations(store, audit, environment)
    markups = APIRouter(prefix="/markups", tags=["admin"], route_class=MarkupRoute,
                        responses={400: {"description": "Invalid markup fields; submitted values are not echoed"}, **errors})

    @markups.get("", response_model=MarkupList)
    def markup_list(request: Request):
        return markup.listing(request)

    @markups.post("/preview", response_model=MarkupPreview)
    def markup_preview(request: Request, body: MarkupPreviewRequest = Body(...)):
        return markup.preview(request, body)

    @markups.post("/publish", response_model=MarkupPublication)
    def markup_publish(request: Request, body: MarkupPublishRequest = Body(...)):
        return markup.publish(request, body)

    router.include_router(bands)
    router.include_router(markups)
    return router
