"""Govern the two real conversation digest settings through signed, scoped previews."""
from __future__ import annotations

import hashlib
import json
import re
import time

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.platform_access import require_platform
from synwevo_core.platform_settings import PlatformSettings
from synwevo_core.store import new_id

from .admin_operations import OPERATIONS, PREVIEW_SECONDS, SignedPreview, canonical


class SettingsOperations(SignedPreview):
    action = "admin.settings.publish"
    target = "global"

    def __init__(self, store, audit):
        self.store, self.audit = store, audit
        self.settings = PlatformSettings(store)

    def _actor(self, request, *, execute=False, view=False):
        return require_platform(request, "platform.config.view" if view else OPERATIONS[self.action].permission,
                                store=self.store, audit=self.audit, dangerous=execute)

    @staticmethod
    def _input(body):
        revision = body.get("expected_revision")
        if type(revision) is not int or not 0 <= revision < (1 << 63):
            raise ServiceError(400, "settings_bad_revision", "配置版本无效，请重新读取当前配置")
        if ("changes" in body) == ("rollback_revision" in body):
            raise ServiceError(400, "settings_bad_changes", "请提供配置变更或选择一个历史版本")
        if "changes" in body:
            return {"expected_revision": revision, "changes": PlatformSettings.validate(body["changes"])}
        rollback = body["rollback_revision"]
        if type(rollback) is not int or not 0 <= rollback < (1 << 63):
            raise ServiceError(400, "settings_bad_revision", "配置版本无效，请重新读取当前配置")
        return {"expected_revision": revision, "rollback_revision": rollback}

    def _current(self, expected):
        state = self.settings.read()
        if state["revision"] != expected:
            raise ServiceError(409, "settings_revision_conflict", "配置已变化，请重新预览", {"current": state})
        return state

    def _plan(self, inputs):
        self._current(inputs["expected_revision"])
        return self.settings.preview(**{key: value for key, value in inputs.items() if key != "expected_revision"})

    def preview(self, request, body):
        actor = self._actor(request)
        try:
            inputs = self._input(body)
            with self.store.tx():
                actor = self._actor(request)
                plan = self._plan(inputs)
                expires = time.time() + PREVIEW_SECONDS
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                           "target": self.target, "origin": self._environment(request), "inputs": inputs,
                           "before": plan["before"], "after": plan["after"],
                           "expires_at": expires, "nonce": new_id("plan")}
                token = self._sign(binding)
            return {"action": self.action, "scope": "platform", "risk": OPERATIONS[self.action].risk, **plan,
                    "changes": inputs.get("changes", plan["changes"]),
                    "expires_at": expires, "preview_token": token}
        except Exception as exc:
            self._failure(request, actor, exc, phase="preview")
            raise

    def publish(self, request, body):
        actor = self._actor(request, execute=True)
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        try:
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            inputs = self._input(body)
            key, token = body.get("idempotency_key"), body.get("preview_token")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
                raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
            origin = self._environment(request)
            request_hash = hashlib.sha256(canonical({"action": self.action, "scope": "platform", "target": self.target,
                "origin": origin, "inputs": inputs, "reason": reason, "preview_token": token}).encode()).hexdigest()
            with self.store.tx() as transaction:
                actor = self._actor(request, execute=True)
                previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                          (actor["id"], key))
                if previous:
                    if previous["request_hash"] != request_hash or previous["action"] != self.action:
                        raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                    return json.loads(previous["result_json"])
                signed = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "scope": "platform", "target": self.target,
                            "origin": origin, "inputs": inputs}
                if any(signed.get(key) != value for key, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if signed.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                plan = self._plan(inputs)
                if signed.get("before") != plan["before"] or signed.get("after") != plan["after"]:
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                operation_id = new_id("ao")
                saved = self.settings.append(**inputs, actor_id=actor["id"], reason=reason, operation_id=operation_id)
                state = self.settings.read()
                result = {"operation_id": operation_id, "status": "succeeded", "revision": saved["revision"], "state": state}
                self.audit(request, self.action, actor_id=actor["id"], object_kind="platform_settings", object_id=self.target,
                           workspace_given=True, result="ok", meta={"operation_id": operation_id, "reason": reason,
                               "before": plan["before"], "after": state, "changes": plan["changes"],
                               "rollback_revision": plan["rollback_revision"], "environment": origin})
                transaction.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                                    "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                    (operation_id, actor["id"], self.action, self.target, key, request_hash, canonical(result), time.time()))
                return result
        except Exception as exc:
            self._failure(request, actor, exc, phase="publish", reason=reason)
            raise

    def _failure(self, request, actor, exc, *, phase, reason=""):
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "environment": self._environment(request)}
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        self.audit(request, self.action, actor_id=actor["id"], object_kind="platform_settings", object_id=self.target,
                   workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta=meta)


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/settings", tags=["admin"])
    operations = SettingsOperations(store, audit)

    @router.get("")
    def read(request: Request):
        operations._actor(request, view=True)
        return {"catalogue": operations.settings.catalogue(), "state": operations.settings.read()}

    @router.get("/history")
    def history(request: Request, before_revision: int | None = Query(None), limit: int = Query(20)):
        operations._actor(request, view=True)
        return operations.settings.history(before_revision=before_revision, limit=limit)

    @router.post("/preview")
    def preview(request: Request, body: dict = Body(...)):
        return operations.preview(request, body)

    @router.post("/publish")
    def publish(request: Request, body: dict = Body(...)):
        return operations.publish(request, body)

    return router
