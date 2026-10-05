"""Confirmed governance of the fixed platform DeepSeek route and credential.

Only credential commitments enter previews and request hashes. Current authorization,
sealed credential publication, successful audit and operation result commit together.
This surface never resolves a credential or invokes a provider.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from synwevo_core.billing.provider_settings import (
    ACCOUNT_REF, PROVIDER, CredentialChange, ProviderSettings, ProviderSpec,
)
from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical


class ProviderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Revision = Annotated[int, Field(ge=0, le=2**63 - 1)]
ExpectedRevision = Annotated[int, Field(ge=0, lt=2**63 - 1)]
OperationKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")]


class ProviderVersion(ProviderContract):
    environment: Literal["test", "live"] | None
    provider: Literal["deepseek"]
    account_ref: Literal["platform-deepseek"]
    revision: Revision
    spec: ProviderSpec | None
    credential_revision: Revision
    credential_configured: bool
    actor_id: str | None
    created_at: int | None
    reason: str | None
    operation_id: str | None


class ProviderClosure(ProviderContract):
    state: Literal["model_missing", "upstream_balance"]
    reason_code: str
    requested_model: str
    since: int


class ProviderCheck(ProviderContract):
    state: Literal["model_listed", "model_missing", "models_check_failed"]
    at: int


class ProviderHealth(ProviderContract):
    """Automatic closure of paid dispatch on the current revision; publishing again reopens it."""
    closure: ProviderClosure | None
    last_models_check: ProviderCheck | None


class ProviderState(ProviderVersion):
    status: Literal["unconfigured", "configured"]


class ProviderStatus(ProviderState):
    """The current version as read by administrators; previews bind the version, not its health."""
    health: ProviderHealth | None


class ProviderHistory(ProviderContract):
    items: list[ProviderVersion]
    next_before_revision: Revision | None


class CredentialCommitment(ProviderContract):
    action: Literal["keep", "replace", "revoke"]
    digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None


class ProviderPreviewRequest(ProviderContract):
    expected_revision: ExpectedRevision
    spec: ProviderSpec
    credential: CredentialChange


class ProviderPublishRequest(ProviderPreviewRequest):
    preview_token: str = Field(min_length=1, max_length=8192)
    idempotency_key: OperationKey
    reason: str = Field(min_length=8, max_length=500)


class ProviderPreview(ProviderContract):
    before: ProviderState
    after: ProviderSpec
    credential: CredentialCommitment
    credential_revision: Revision
    credential_configured: bool
    preview_token: str
    expires_at: float
    risk: Literal["high"]
    environment: Literal["test", "live"]


class ProviderPublication(ProviderContract):
    operation_id: str
    status: Literal["succeeded"]
    state: ProviderState


class ProviderRoute(APIRoute):
    """Validation and unexpected failures must not echo submitted secrets or exceptions."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def handle(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise ServiceError(400, "model_config_invalid", "模型配置字段无效，请检查路线、有效期和凭据") from None
            except ServiceError:
                raise
            except Exception:
                raise ServiceError(503, "model_config_unavailable", "模型配置暂不可用，请稍后重试") from None
        return handle


def _digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _state(version):
    return {**version, "status": "configured" if version["spec"] is not None else "unconfigured"}


class ProviderOperations(SignedPreview):
    action = "admin.model_provider.publish"

    def __init__(self, store, audit, environment):
        self.store, self.audit, self.environment = store, audit, environment

    def _settings(self):
        return ProviderSettings(self.store, environment=self.environment)

    def _actor(self, request, *, view=False, execute=False):
        return require_platform(request, "platform.config.view" if view else "platform.config.manage",
                                store=self.store, audit=self.audit, dangerous=execute)

    def _audit(self, request, actor, *, result="ok", meta):
        self.audit(request, self.action, actor_id=actor["id"], object_kind="model_provider",
                   object_id=ACCOUNT_REF, workspace_given=True, result=result,
                   meta={"billing_environment": self.environment, "provider": PROVIDER,
                         "origin": self._environment(request), **meta})

    def _failure(self, request, actor, exc):
        # No submitted body, token, key, ciphertext or exception message is recorded.
        self._audit(request, actor, result=(
            "denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error"),
            meta={"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"})

    def read(self, request):
        self._actor(request, view=True)
        with self.store.tx():
            self._actor(request, view=True)
            version = self._settings().status() if self.environment else {
                "environment": None, "provider": PROVIDER, "account_ref": ACCOUNT_REF,
                "revision": 0, "spec": None, "credential_revision": 0, "credential_configured": False,
                "actor_id": None, "created_at": None, "reason": None, "operation_id": None, "health": None}
            self._actor(request, view=True)
            return _state(version)

    def history(self, request, *, before_revision=None, limit=20):
        self._actor(request, view=True)
        with self.store.tx():
            self._actor(request, view=True)
            result = (self._settings().history(before_revision=before_revision, limit=limit)
                      if self.environment else {"items": [], "next_before_revision": None})
            self._actor(request, view=True)
            return result

    def preview(self, request, body: ProviderPreviewRequest):
        actor = self._actor(request)
        try:
            with self.store.tx():
                actor = self._actor(request)
                plan = self._settings().preview(body.spec, body.credential, expected_revision=body.expected_revision)
                plan = {**plan, "before": _state(plan["before"])}
                expires = time.time() + PREVIEW_SECONDS
                # Never dump the request/CredentialChange: SecretStr is an input boundary,
                # not a substitute for an explicit, non-secret signed contract.
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                           "target": ACCOUNT_REF, "provider": PROVIDER, "origin": self._environment(request),
                           "environment": self.environment, "expected_revision": body.expected_revision,
                           "before_digest": _digest(plan["before"]), "after": plan["after"],
                           "credential": plan["credential"], "credential_revision": plan["credential_revision"],
                           "credential_configured": plan["credential_configured"],
                           "expires_at": expires, "nonce": new_id("plan")}
                result = {**plan, "environment": self.environment, "risk": "high",
                          "preview_token": self._sign(binding), "expires_at": expires}
                self._actor(request)
                return result
        except Exception as exc:
            self._failure(request, actor, exc)
            raise

    def publish(self, request, body: ProviderPublishRequest):
        actor = self._actor(request, execute=True)
        try:
            reason = body.reason.strip()
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            origin = self._environment(request)
            commitment = body.credential.commitment()
            request_hash = _digest({"action": self.action, "target": ACCOUNT_REF, "provider": PROVIDER,
                "origin": origin, "environment": self.environment, "revision": body.expected_revision,
                "spec": body.spec.model_dump(), "credential": commitment,
                "token": body.preview_token, "reason": reason})
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
                            "after": body.spec.model_dump(), "credential": commitment}
                if any(signed.get(key) != value for key, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                expiry = signed.get("expires_at")
                if type(expiry) not in (float, int) or not math.isfinite(expiry):
                    raise ServiceError(409, "admin_preview_invalid", "请重新预览操作影响后再确认")
                if expiry <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                plan = settings.preview(body.spec, body.credential, expected_revision=body.expected_revision)
                if (signed.get("before_digest") != _digest(_state(plan["before"])) or
                        any(signed.get(key) != plan[key] for key in
                            ("after", "credential", "credential_revision", "credential_configured"))):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                self._actor(request, execute=True)
                operation_id = new_id("ao")
                saved = settings.append(body.spec, body.credential, expected_revision=body.expected_revision,
                                        actor_id=actor["id"], reason=reason, operation_id=operation_id)
                result = {"operation_id": operation_id, "status": "succeeded", "state": _state(saved)}
                self._audit(request, actor, meta={"operation_id": operation_id, "reason": reason,
                    "before_revision": plan["before"]["revision"], "revision": saved["revision"],
                    "credential_action": commitment["action"],
                    "before_credential_revision": plan["before"]["credential_revision"],
                    "credential_revision": saved["credential_revision"]})
                transaction.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,result_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)", (operation_id, actor["id"], self.action, ACCOUNT_REF,
                    body.idempotency_key, request_hash, canonical(result), time.time()))
                self._actor(request, execute=True)
                return result
        except Exception as exc:
            self._failure(request, actor, exc)
            raise


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/billing/model-provider", tags=["admin"], route_class=ProviderRoute,
                       responses={400: {"description": "Invalid model configuration; submitted values are not echoed"},
                                  "4XX": {"description": "Current authorization, CSRF, authentication or version check refused"}})
    value = os.environ.get("SYNWEVO_BILLING_ENVIRONMENT", "")
    environment = value if value in ("test", "live") else None
    operations = ProviderOperations(store, audit, environment)

    @router.get("", response_model=ProviderStatus)
    def read(request: Request):
        return operations.read(request)

    @router.get("/history", response_model=ProviderHistory)
    def history(request: Request, before_revision: int | None = Query(None, ge=0, lt=2**63 - 1),
                limit: int = Query(20, ge=1, le=100)):
        return operations.history(request, before_revision=before_revision, limit=limit)

    @router.post("/preview", response_model=ProviderPreview)
    def preview(request: Request, body: ProviderPreviewRequest = Body(...)):
        return operations.preview(request, body)

    @router.post("/publish", response_model=ProviderPublication)
    def publish(request: Request, body: ProviderPublishRequest = Body(...)):
        return operations.publish(request, body)

    return router
