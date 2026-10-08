"""Confirmed recovery of proven atomic email enqueue failures.

The notifier owns eligibility, pagination and the versioned transition. This surface exposes
only metadata and schedules another dispatcher pass; it never sends or retries an outbox mail.
The wire token is named ``preview_token`` in both the preview and execute contract.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from fastapi import APIRouter, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.notifications import Notifier
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical


ITEM_FIELDS = ("notification_id", "user_id", "category", "status", "attempts", "updated_at",
               "error_code", "recoverable", "blocked_reason")
RECEIPT_FIELDS = ("notification_id", "status", "attempts", "recovery_scheduled")


def _metadata(row):
    return {field: row[field] for field in ITEM_FIELDS}


def _bad_request():
    return ServiceError(400, "notification_recovery_bad_request", "通知恢复请求格式无效")


class NotificationRecoveryOperations(SignedPreview):
    action = "admin.notification.recover_enqueue"

    def __init__(self, store, audit):
        self.store, self.audit = store, audit
        self.notifier = Notifier(store)

    def _actor(self, request, *, execute=False, view=False):
        return require_platform(request, "platform.mail.view" if view else "platform.mail.manage",
                                store=self.store, audit=self.audit, dangerous=execute)

    def page(self, request, *, q="", limit=25, offset=0, snapshot=None):
        self._actor(request, view=True)
        with self.store.tx():
            self._actor(request, view=True)
            page = self.notifier.email_recovery_page(q=q, limit=limit, offset=offset, snapshot=snapshot)
            return {**{field: page[field] for field in ("total", "limit", "offset", "snapshot", "next_offset")},
                    "items": [_metadata(row) for row in page["items"]]}

    def _row(self, nid):
        return self.notifier.email_recovery_row(nid)

    @staticmethod
    def _eligible(row):
        if not row["recoverable"]:
            raise ServiceError(409, "notification_not_recoverable", "此通知当前不能恢复排队",
                               {"blocked_reason": row["blocked_reason"]})

    def preview(self, request, nid, body=None):
        actor = self._actor(request)
        try:
            if body is not None and (not isinstance(body, dict) or body):
                raise _bad_request()
            with self.store.tx():
                actor = self._actor(request)
                row = self._row(nid)
                self._eligible(row)
                expires = time.time() + PREVIEW_SECONDS
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform",
                           "notification_id": nid, "origin": self._environment(request),
                           "version": row["version"], "expires_at": expires, "nonce": new_id("plan")}
                token = self._sign(binding)
            return {"action": self.action, "scope": "platform", "risk": "high", "target": _metadata(row),
                    "impact": "恢复排队后由通知处理器按当前账户状态与通知偏好处理，不表示邮件已投递。",
                    "expires_at": expires, "preview_token": token}
        except Exception as exc:
            self._failure(request, actor, nid, exc, phase="preview")
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(500, "admin_operation_failed", "通知恢复操作未提交，请稍后重试") from None

    def execute(self, request, nid, body):
        actor = self._actor(request, execute=True)
        reason = ""
        try:
            if not isinstance(body, dict) or set(body) - {"preview_token", "reason", "idempotency_key"}:
                raise _bad_request()
            raw_reason = body.get("reason")
            reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            key, token = body.get("idempotency_key"), body.get("preview_token")
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
                raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
            origin = self._environment(request)
            request_hash = hashlib.sha256(canonical({"action": self.action, "scope": "platform",
                "notification_id": nid, "origin": origin, "reason": reason, "preview_token": token}).encode()).hexdigest()
            with self.store.tx() as transaction:
                actor = self._actor(request, execute=True)
                previous = self.store.one("SELECT action,request_hash,result_json FROM admin_operations "
                                          "WHERE actor_id=? AND idempotency_key=?", (actor["id"], key))
                if previous:
                    if previous["action"] != self.action or previous["request_hash"] != request_hash:
                        raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                    return json.loads(previous["result_json"])
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "scope": "platform",
                            "notification_id": nid, "origin": origin}
                if any(plan.get(field) != value for field, value in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if plan.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                row = self._row(nid)
                if plan.get("version") != row["version"]:
                    raise ServiceError(409, "admin_preview_stale", "通知状态已变化，请重新预览")
                self._eligible(row)
                receipt = self.notifier.recover_email_enqueue(nid, row["version"])
                operation_id = new_id("ao")
                result = {"ok": True, "operation_id": operation_id, "state": "succeeded",
                          **{field: receipt[field] for field in RECEIPT_FIELDS}}
                self.audit(request, self.action, actor_id=actor["id"], object_kind="notification", object_id=nid,
                           workspace_given=True, result="ok", meta={**result, "reason": reason,
                           "before": _metadata(row), "after": {field: receipt[field] for field in RECEIPT_FIELDS},
                           "environment": origin})
                transaction.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,"
                                    "request_hash,result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                    (operation_id, actor["id"], self.action, nid, key, request_hash,
                                     canonical(result), time.time()))
                return result
        except Exception as exc:
            self._failure(request, actor, nid, exc, phase="execute", reason=reason)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(500, "admin_operation_failed", "通知恢复操作未提交，请稍后重试") from None

    def _failure(self, request, actor, nid, exc, *, phase, reason=""):
        # After rollback: no raw errors, notification payloads, tokens or request bodies.
        meta = {"code": exc.code if isinstance(exc, ServiceError) else "admin_operation_failed",
                "phase": phase, "environment": self._environment(request)}
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        try:
            self.audit(request, self.action, actor_id=actor["id"], object_kind="notification", object_id=nid,
                       workspace_given=True,
                       result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error", meta=meta)
        except Exception:
            raise ServiceError(500, "admin_operation_failed", "通知恢复操作未提交，请稍后重试") from None


async def _body(request):
    # FastAPI's default validation response echoes invalid inputs. Keep this metadata-only
    # endpoint's errors independent of submitted payloads, including malformed JSON.
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        raise _bad_request() from None


def build_router(store, audit):
    """Registered under the parent admin router, which already supplies /api/admin."""
    router = APIRouter(prefix="/notification-recovery", tags=["admin"])
    operations = NotificationRecoveryOperations(store, audit)

    @router.get("")
    def listing(request: Request, q: str = Query("", max_length=100),
                limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**63 - 1),
                snapshot: int | None = Query(None, ge=0, le=2**63 - 1)):
        return operations.page(request, q=q, limit=limit, offset=offset, snapshot=snapshot)

    @router.post("/{nid}/preview")
    async def preview(request: Request, nid: str):
        operations._actor(request)
        return operations.preview(request, nid, await _body(request))

    @router.post("/{nid}/execute")
    async def execute(request: Request, nid: str):
        operations._actor(request, execute=True)
        return operations.execute(request, nid, await _body(request))

    return router
