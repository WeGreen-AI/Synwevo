"""Confirmed account administration, with durable results and transactional auditing.

Preview is a signed, five-minute statement of actor, origin, action and resource version.
It makes no business change. Execution rechecks current authority under the writer lock;
the result, withdrawal and audit commit together. Retrying the same key returns that result.
This is a small operation catalogue, not an arbitrary command executor.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass

from synwevo_core.auth import Auth
from synwevo_core.audit_change import change
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.oidc import Grants
from synwevo_core.platform_access import PlatformAccess, require_outranking, require_platform
from synwevo_core.sessions import Sessions
from synwevo_core.store import new_id

from .ownership import check_ownership_continuity


@dataclass(frozen=True)
class Operation:
    permission: str
    risk: str


OPERATIONS = {
    "admin.user.status": Operation("platform.users.manage", "high"),
    "admin.settings.publish": Operation("platform.config.manage", "high"),
    "admin.model_price.draft": Operation("platform.config.manage", "medium"),
    "admin.model_price.publish": Operation("platform.config.manage", "high"),
    "admin.super_admin.offer": Operation("platform.roles.assign_super", "high"),
    "admin.super_admin.cancel": Operation("platform.roles.assign_super", "high"),
    "admin.super_admin.ready": Operation("platform.roles.assign_super", "high"),
}
PREVIEW_SECONDS = 300


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SignedPreview:
    """Shared encoding for the small, explicitly registered administration operations.

    Callers bind their own action, actor, scope, origin, inputs and current resource version.
    The signature is proof of a preview, never authorization to execute it.
    """

    @staticmethod
    def _environment(request):
        # The origin comes from the host-validated request, never a submitted scope selector.
        return str(request.base_url).rstrip("/")

    def _key(self):
        return hmac.new(Auth(self.store).secret(), b"synwevo/admin-preview/v1", hashlib.sha256).digest()

    def _sign(self, value):
        data = base64.urlsafe_b64encode(canonical(value).encode()).decode().rstrip("=")
        return data + "." + hmac.new(self._key(), data.encode(), hashlib.sha256).hexdigest()

    def _verify(self, token):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            data, sig = token.split(".")
            if not hmac.compare_digest(sig, hmac.new(self._key(), data.encode(), hashlib.sha256).hexdigest()):
                raise ValueError()
            payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
            if not isinstance(payload, dict) or payload.get("v") != 1:
                raise ValueError()
            return payload
        except (ValueError, TypeError, UnicodeError):
            raise ServiceError(409, "admin_preview_invalid", "请重新预览操作影响后再确认") from None


class AccountOperations(SignedPreview):
    action = "admin.user.status"

    def __init__(self, store, audit):
        self.store, self.audit = store, audit

    def _actor(self, request, *, execute=False):
        return require_platform(request, OPERATIONS[self.action].permission, store=self.store,
                                audit=self.audit, dangerous=execute)

    def _target(self, request, actor, uid, status):
        if status not in ("active", "suspended"):
            raise ServiceError(400, "bad_status", "状态只能是 active 或 suspended")
        target = Identity(self.store).get_user(uid)
        if not target:
            raise ServiceError(404, "no_user", "找不到该账户")
        if self.store.one("SELECT 1 FROM app_accounts WHERE user_id=?", (uid,)):
            raise ServiceError(409, "application_identity", "请通过应用身份管理操作该账户")
        access = PlatformAccess(self.store)
        if status == "suspended":
            if uid == actor["id"]:
                raise ServiceError(400, "self_suspend", "不能停用自己的账户")
            if access.is_usable_super_admin(uid) and not access.usable_super_admins(excluding=uid):
                raise ServiceError(409, "last_super_admin", "不能停用最后一名可用的最高管理员")
            if access.holds_any_grant(uid):
                raise ServiceError(409, "demote_first", "请先撤销该账户的全部平台权限，再停用它")
            if target["status"] not in ("active", "pending"):
                raise ServiceError(409, "account_state_conflict", "账户状态已变化，请刷新后重试")
        else:
            if target["status"] != "suspended":
                raise ServiceError(409, "not_suspended", "只有已停用的账户可以恢复")
            require_outranking(request, actor, uid, store=self.store, audit=self.audit)
        return target

    def _snapshot(self, target, status):
        uid = target["id"]
        # Stable references enter only the opaque version digest, never the response or audit.
        # SQLite rowids can be reused after cleanup, so they cannot identify a proof version.
        tables = ("sessions", "oauth_tokens", "oauth_codes", "email_tokens", "mfa_tickets",
                  "webauthn_challenges", "account_recoveries", "reauth_challenges")
        refs = {}
        for table in tables:
            predicate = " AND revoked_at IS NULL" if table in ("sessions", "oauth_tokens") else ""
            if table in ("oauth_codes", "email_tokens", "account_recoveries", "reauth_challenges"):
                predicate = " AND used_at IS NULL"
            column = "code_hash" if table == "oauth_codes" else "id"
            refs[table] = [r["ref"] for r in self.store.all(
                f"SELECT {column} AS ref FROM {table} WHERE user_id=?{predicate} ORDER BY {column}", (uid,))]
        version = {"id": uid, "status": target["status"], "updated_at": target["updated_at"],
                   "verified": target["email_verified_at"], "refs": refs,
                   "grants": PlatformAccess(self.store).live_grants(uid)}
        if status == "suspended":
            # Both callers hold the writer lock. Recheck live successors at execution,
            # and bind even still-safe governance changes to a fresh impact preview.
            version["ownership"] = check_ownership_continuity(self.store, uid)
        counts = {"sessions": len(refs["sessions"]), "tokens": len(refs["oauth_tokens"]),
                  "pending_authentication": sum(len(refs[t]) for t in tables[2:])}
        return hashlib.sha256(canonical(version).encode()).hexdigest(), counts

    def preview(self, request, uid, status):
        actor = self._actor(request)
        try:
            with self.store.tx():
                actor = self._actor(request)
                target = self._target(request, actor, uid, status)
                version, counts = self._snapshot(target, status)
                result_status = "suspended" if status == "suspended" else "active" if target["email_verified_at"] else "pending"
                expires = time.time() + PREVIEW_SECONDS
                plan = {"v": 1, "actor": actor["id"], "action": self.action, "target": uid,
                        "status": status, "origin": self._environment(request), "version": version,
                        "expires_at": expires, "nonce": new_id("plan")}
        except Exception as exc:
            self._failure(request, actor, uid, exc, phase="preview")
            raise
        return {"action": self.action, "scope": "platform", "risk": "high",
                "target": {"id": uid, "email": target["email"], "status": target["status"]},
                "result_status": result_status, "impact": counts, "expires_at": expires,
                "preview_token": self._sign(plan)}

    def execute(self, request, uid, body):
        actor = self._actor(request, execute=True)
        status = body.get("status")
        raw_reason = body.get("reason")
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        # The old route stays, but there is no legacy path around confirmation.
        key = body.get("idempotency_key")
        token = body.get("preview_token")
        request_hash = hashlib.sha256(canonical({"status": status, "reason": reason,
                                                "preview_token": token, "target": uid,
                                                "origin": self._environment(request)}).encode()).hexdigest()
        try:
            if not 8 <= len(reason) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            with self.store.tx() as tx:
                actor = self._actor(request, execute=True)
                if isinstance(key, str):
                    previous = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?",
                                              (actor["id"], key))
                    if previous:
                        if previous["request_hash"] != request_hash:
                            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
                        return json.loads(previous["result_json"])
                target = self._target(request, actor, uid, status)
                if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
                    raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
                plan = self._verify(token)
                expected = {"actor": actor["id"], "action": self.action, "target": uid,
                            "status": status, "origin": self._environment(request)}
                if any(plan.get(k) != v for k, v in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if plan.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                version, _ = self._snapshot(target, status)
                if plan.get("version") != version:
                    raise ServiceError(409, "admin_preview_stale", "账户或访问权限已变化，请重新预览")
                operation_id = new_id("ao")
                revoked = tokens = 0
                result_status = "suspended" if status == "suspended" else "active" if target["email_verified_at"] else "pending"
                ts = time.time()
                tx.execute("UPDATE users SET status=?,updated_at=? WHERE id=?", (result_status, ts, uid))
                if status == "suspended":
                    revoked = Sessions(self.store).revoke_all_for_user(uid, "admin_suspended")
                    tokens = Grants(self.store).revoke_for_user(uid, "admin_suspended")
                    from .profile import invalidate_access
                    invalidate_access(self.store, uid, "admin_suspended")
                    tx.execute("UPDATE account_recoveries SET used_at=COALESCE(used_at,?) WHERE user_id=?", (ts, uid))
                result = {"ok": True, "operation_id": operation_id, "state": "succeeded", "status": result_status,
                          "sessions_revoked": revoked, "tokens_revoked": tokens,
                          "journaled": True if status == "suspended" else None}
                self.audit(request, self.action, actor_id=actor["id"], object_kind="user", object_id=uid,
                           workspace_given=True, result="ok", meta={**result, "reason": reason,
                           **change({"status": target["status"]}, {"status": result_status}),
                           "environment": self._environment(request)})
                tx.execute("INSERT INTO admin_operations (id,actor_id,action,target_id,idempotency_key,request_hash,"
                           "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (operation_id, actor["id"], self.action, uid, key, request_hash, canonical(result), ts))
                if status == "suspended":
                    from synwevo_core import journal
                    try:
                        journal.record_account(self.store, "user_suspended", user_id=uid, event_id=operation_id)
                    except (OSError, ValueError):
                        raise ServiceError(503, "journal_unavailable", "安全记录暂不可用，操作未提交，请稍后重试") from None
                return result
        except Exception as exc:
            self._failure(request, actor, uid, exc, phase="execute", reason=reason)
            raise

    def _failure(self, request, actor, uid, exc, *, phase, reason=""):
        # Outside the failed transaction. Invalid inputs and exception messages can contain
        # arbitrary data; audit only a stable code, phase and a validated operation reason.
        code = exc.code if isinstance(exc, ServiceError) else "admin_operation_failed"
        if code == "target_outranks_actor":
            from synwevo_core.platform_access import audit_denied
            audit_denied(self.store, self.audit, request, actor["id"], uid,
                         object_kind="user", code=code)
        meta = {"code": code, "phase": phase, "environment": self._environment(request)}
        if 8 <= len(reason) <= 500:
            meta["reason"] = reason
        self.audit(request, self.action, actor_id=actor["id"], object_kind="user", object_id=uid,
                   workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta=meta)
