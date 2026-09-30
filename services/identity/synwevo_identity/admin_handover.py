"""Recipient-owned, dedicated UV acceptance and a separate administrator readiness proof.

Proposals confer no permission. Acceptance grants real authority; readiness is a durable
availability check used by last-administrator protection, never a generic reauth credential.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.errors import ServiceError
from synwevo_core.http import client_ip
from synwevo_core.platform_access import ASSIGN_SUPER, SUPER_ADMIN, PlatformAccess, require_platform
from synwevo_core.store import new_id

from .admin_operations import PREVIEW_SECONDS, SignedPreview, canonical
from .passkeys import allowed_origins

OFFER_SECONDS = 86400
MAX_PENDING = 20


def offer_eligibility(store, actor_id, target_id):
    """Read-only eligibility: a proposal does not require a pre-existing UV authenticator."""
    access = PlatformAccess(store)
    if ASSIGN_SUPER not in access.effective_permissions(actor_id):
        return "platform_permission_denied"
    if actor_id == target_id:
        return "self_assignment"
    target = store.one("SELECT * FROM users WHERE id=?", (target_id,))
    if not target or target["status"] != "active" or target["deleted_at"]:
        return "target_unavailable"
    if not target["email_verified_at"]:
        return "target_unverified"
    if store.one("SELECT 1 FROM app_accounts WHERE user_id=?", (target_id,)):
        return "application_identity"
    if any(g["role"] == SUPER_ADMIN for g in access.live_grants(target_id)):
        return "already_granted"
    return None


class HandoverOperations(SignedPreview):
    action = "admin.super_admin.offer"

    def __init__(self, ident, sessions, passkeys, audit):
        self.ident, self.sessions, self.passkeys, self.audit = ident, sessions, passkeys, audit
        self.store = ident.store

    def _session(self, request):
        row = self.passkeys.current_reauth_session(getattr(request.state, "session", None))
        request.state.session = row
        return row

    def _admin(self, request, *, dangerous=False):
        return require_platform(request, ASSIGN_SUPER, store=self.store, audit=self.audit, dangerous=dangerous)

    def _origin(self, request):
        value = (request.headers.get("origin") or str(request.base_url)).rstrip("/")
        if value not in allowed_origins():
            raise ServiceError(403, "bad_origin", "当前页面不能完成身份确认，请从账户服务重新开始")
        return value

    def _limit(self, request, session, phase):
        for key, maximum in ((f"superoffer:{phase}:session:{session['id']}", 10),
                             (f"superoffer:{phase}:address:{client_ip(request)}", 30)):
            allowed, wait = self.ident.window_allows(key, limit=maximum, window_s=60)
            if not allowed:
                raise ServiceError(429, "too_many_attempts", "尝试过于频繁，请稍后再试", {"retry_after": wait})

    def _record(self, request, action, actor_id, oid, operation_id, **meta):
        self.audit(request, action, actor_id=actor_id, object_kind="super_admin_offer", object_id=oid,
                   workspace_given=True, result="ok", meta={"operation_id": operation_id, **meta})

    def failure(self, request, action, exc):
        session = getattr(request.state, "session", None)
        oid = request.path_params.get("oid")
        oid = oid if isinstance(oid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", oid) else None
        self.audit(request, action, actor_id=session.get("user_id") if session else None,
                   object_kind="super_admin_offer", object_id=oid, workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta={"code": exc.code if isinstance(exc, ServiceError) else "super_offer_failed"})

    def _input(self, body):
        uid = body.get("target_user_id")
        if not isinstance(uid, str) or not uid or len(uid) > 128:
            raise ServiceError(400, "super_offer_bad_target", "请选择一个现有账户作为接任人")
        return uid

    @staticmethod
    def _reason(body):
        value = body.get("reason")
        reason = value.strip() if isinstance(value, str) else ""
        if not 8 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        return reason

    def _idempotency(self, request, action, target, body):
        key = body.get("idempotency_key")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", key):
            raise ServiceError(400, "admin_confirmation_required", "请先预览操作影响，再确认执行")
        # Only the digest is stored. In particular no authenticator response reaches an audit.
        digest = hashlib.sha256(canonical({"action": action, "target": target,
            "origin": self._environment(request), "body": body}).encode()).hexdigest()
        return key, digest

    def _previous(self, actor_id, action, key, digest):
        row = self.store.one("SELECT * FROM admin_operations WHERE actor_id=? AND idempotency_key=?", (actor_id, key))
        if not row:
            return None
        if row["action"] != action or row["request_hash"] != digest:
            raise ServiceError(409, "idempotency_conflict", "此操作编号已用于不同的请求")
        return json.loads(row["result_json"])

    def _save(self, actor, action, target, key, digest, result):
        self.store.execute("INSERT INTO admin_operations(id,actor_id,action,target_id,idempotency_key,request_hash,"
                           "result_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (result["operation_id"], actor, action, target, key, digest, canonical(result), time.time()))
        return result

    def _get(self, oid):
        row = self.store.one("SELECT * FROM platform_super_admin_offers WHERE id=?", (oid,))
        if not row:
            raise ServiceError(404, "super_offer_not_found", "找不到这项最高管理员提议")
        return row

    def _authority(self, row):
        return any(g["id"] == row["proposer_grant_id"] and g["role"] == SUPER_ADMIN
                   for g in PlatformAccess(self.store).counted_grants(row["proposer_id"]))

    def _status(self, row):
        if row["state"] == "pending":
            if row["expires_at"] <= time.time():
                return "expired"
            if not self._authority(row):
                return "authority_changed"
            if offer_eligibility(self.store, row["proposer_id"], row["recipient_id"]):
                return "target_unavailable"
        if row["state"] in ("accepted", "ready"):
            if not self._grant_live(row):
                return "revoked"
        return row["state"]

    def _grant_live(self, row):
        return any(g["id"] == row["accepted_grant_id"] and g.get("offer_id") == row["id"] and g["role"] == SUPER_ADMIN
                   for g in PlatformAccess(self.store).counted_grants(row["recipient_id"]))

    def _view(self, row, viewer):
        def person(uid):
            user = self.store.one("SELECT id,email,display_name FROM users WHERE id=?", (uid,))
            return user or {"id": uid, "email": None, "display_name": None}
        status = self._status(row)
        uv = bool(self.passkeys.reauth_keys(row["recipient_id"]))
        unavailable = row["closed_reason"] if status == "invalidated" else None
        if status == "pending" and not uv:
            unavailable = "passkey_required"
        elif status in ("expired", "authority_changed", "target_unavailable", "revoked"):
            unavailable = status
        return {"id": row["id"], "status": status, "proposer": person(row["proposer_id"]),
                "recipient": person(row["recipient_id"]), "reason": row["reason"],
                "created_at": row["created_at"], "expires_at": row["expires_at"],
                "accepted_at": row["accepted_at"], "ready_at": row["ready_at"], "grant_id": row["accepted_grant_id"],
                "can_accept": status == "pending" and uv and viewer == row["recipient_id"],
                "required_method": "passkey_uv", "unavailable_reason": unavailable,
                "account_path": "/account#admin-handover", "security_path": "/account#security",
                "ready_path": "/admin#admin-handover-ready"}

    def _plan(self, actor, uid):
        refusal = offer_eligibility(self.store, actor["id"], uid)
        if refusal:
            raise ServiceError(409, "super_offer_bad_target", "接任人须为其他有效且已验证的人类账户，并且尚未持有最高管理员授权",
                               {"reason": refusal})
        target = self.store.one("SELECT * FROM users WHERE id=?", (uid,))
        grant = next(g for g in PlatformAccess(self.store).counted_grants(actor) if g["role"] == SUPER_ADMIN)
        version = hashlib.sha256(canonical({"target": uid, "updated_at": target["updated_at"],
            "verified": target["email_verified_at"], "grant_id": grant["id"]}).encode()).hexdigest()
        return target, grant, version

    def preview(self, request, body):
        self._admin(request)
        uid = self._input(body)
        with self.store.tx():
            actor = self._admin(request)
            target, grant, version = self._plan(actor, uid)
            expires = time.time() + PREVIEW_SECONDS
            binding = {"v": 1, "action": self.action, "scope": "platform", "actor": actor["id"],
                       "target": uid, "proposer_grant_id": grant["id"], "version": version,
                       "origin": self._environment(request), "expires_at": expires, "nonce": new_id("plan")}
            return {"action": self.action, "scope": "platform", "risk": "high",
                    "target": {"id": uid, "email": target["email"], "display_name": target["display_name"]},
                    "expires_at": expires, "offer_valid_for_s": OFFER_SECONDS, "required_method": "passkey_uv",
                    "preview_token": self._sign(binding)}

    def create(self, request, body):
        self._admin(request, dangerous=True)
        uid, reason = self._input(body), self._reason(body)
        key, digest = self._idempotency(request, self.action, uid, body)
        self._limit(request, self._session(request), "create")
        with self.store.tx():
            actor = self._admin(request, dangerous=True)
            previous = self._previous(actor["id"], self.action, key, digest)
            if previous:
                return previous
            target, grant, version = self._plan(actor, uid)
            plan = self._verify(body.get("preview_token"))
            expected = {"action": self.action, "scope": "platform", "actor": actor["id"], "target": uid,
                        "proposer_grant_id": grant["id"], "version": version, "origin": self._environment(request)}
            if any(plan.get(k) != v for k, v in expected.items()):
                raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
            ts = time.time()
            if plan.get("expires_at", 0) <= ts:
                raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
            # Bounded cleanup for this target/proposer, never an unbounded request-time sweep.
            self.store.execute("UPDATE platform_super_admin_offers SET state='expired',closed_at=?,closed_reason='expired' "
                               "WHERE state='pending' AND expires_at<=? AND (recipient_id=? OR proposer_id=?)",
                               (ts, ts, uid, actor["id"]))
            if self.store.one("SELECT 1 FROM platform_super_admin_offers WHERE recipient_id=? AND state='pending'", (uid,)):
                raise ServiceError(409, "super_offer_pending", "该账户已有待处理的最高管理员提议，请先处理原提议")
            if self.store.one("SELECT COUNT(*) n FROM platform_super_admin_offers WHERE proposer_id=? AND state='pending'",
                              (actor["id"],))["n"] >= MAX_PENDING:
                raise ServiceError(409, "super_offer_limit", "待处理提议已达上限，请先处理已有提议")
            oid, operation = new_id("so"), new_id("ao")
            self.store.execute("INSERT INTO platform_super_admin_offers(id,proposer_id,proposer_grant_id,recipient_id,reason,"
                               "created_at,expires_at) VALUES (?,?,?,?,?,?,?)", (oid, actor["id"], grant["id"], uid, reason, ts, ts + OFFER_SECONDS))
            self._record(request, self.action, actor["id"], oid, operation, reason=reason, recipient_id=uid, proposer_grant_id=grant["id"])
            return self._save(actor["id"], self.action, oid, key, digest,
                              {"operation_id": operation, "offer": self._view(self._get(oid), actor["id"])})

    def listing(self, request, *, admin, limit, before):
        viewer = self._admin(request)["id"] if admin else self._session(request)["user_id"]
        predicate, args = ("1=1", []) if admin else ("recipient_id=?", [viewer])
        if before:
            try:
                stamp, identifier = json.loads(base64.urlsafe_b64decode(before + "=" * (-len(before) % 4)))
                if type(stamp) not in (int, float) or not math.isfinite(stamp) or not isinstance(identifier, str) or len(identifier) > 128:
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise ServiceError(400, "super_offer_bad_cursor", "列表位置无效，请刷新后重试") from None
            predicate += " AND (created_at<? OR (created_at=? AND id<?))"
            args.extend((stamp, stamp, identifier))
        rows = self.store.all(f"SELECT * FROM platform_super_admin_offers WHERE {predicate} ORDER BY created_at DESC,id DESC LIMIT ?",
                              (*args, limit + 1))
        cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            cursor = base64.urlsafe_b64encode(canonical([last["created_at"], last["id"]]).encode()).decode().rstrip("=")
        return {"items": [self._view(row, viewer) for row in rows[:limit]], "next_cursor": cursor}

    def close(self, request, oid, body, *, cancel):
        actor = self._admin(request, dangerous=True)["id"] if cancel else self._session(request)["user_id"]
        action = "admin.super_admin.cancel" if cancel else "auth.super_admin.decline"
        reason = self._reason(body) if cancel else "recipient_declined"
        key, digest = self._idempotency(request, action, oid, body)
        with self.store.tx():
            actor = self._admin(request, dangerous=True)["id"] if cancel else self._session(request)["user_id"]
            row = self._get(oid)
            if not cancel and row["recipient_id"] != actor:
                raise ServiceError(404, "super_offer_not_found", "找不到这项最高管理员提议")
            previous = self._previous(actor, action, key, digest)
            if previous:
                return previous
            if row["state"] != "pending":
                raise ServiceError(409, "super_offer_state_conflict", "提议状态已变化，请刷新后重试")
            state, operation = ("cancelled" if cancel else "declined"), new_id("ao")
            self.store.execute("UPDATE platform_super_admin_offers SET state=?,closed_at=?,closed_by=?,closed_reason=? WHERE id=?",
                               (state, time.time(), actor, reason, oid))
            self.store.execute("UPDATE reauth_challenges SET used_at=? WHERE context_id=? AND used_at IS NULL", (time.time(), oid))
            self._record(request, action, actor, oid, operation, reason=reason)
            return self._save(actor, action, oid, key, digest, {"operation_id": operation, "offer": self._view(self._get(oid), actor)})

    def _recipient(self, request, oid, stage, *, allow_completed=False):
        session = self._session(request)
        if stage == "ready":
            self._admin(request)
        row = self._get(oid)
        if row["recipient_id"] != session["user_id"]:
            raise ServiceError(404, "super_offer_not_found", "找不到这项最高管理员提议")
        status = self._status(row)
        if status == "expired":
            raise ServiceError(409, "super_offer_expired", "提议已过期，请由最高管理员重新发起")
        if status == "authority_changed":
            raise ServiceError(409, "super_offer_authority_changed", "提议的原授权已失效，请由最高管理员重新发起")
        valid = ("pending",) if stage == "accept" else ("accepted",)
        if allow_completed:
            valid += ("accepted", "ready") if stage == "accept" else ("ready",)
        if status not in valid:
            raise ServiceError(409, "super_offer_state_conflict", "提议状态已变化，请刷新后重试")
        return session, row

    def readiness(self, request, oid):
        session, row = self._recipient(request, oid, "ready", allow_completed=True)
        return {"offer": self._view(row, session["user_id"]), "can_confirm": row["state"] == "accepted",
                "methods": ["passkey_uv"] if self.passkeys.reauth_keys(session["user_id"]) else []}

    def begin(self, request, oid, stage):
        if stage == "ready":
            self._admin(request)
        session = self._session(request)
        self._limit(request, session, "begin")
        with self.store.tx():
            session, row = self._recipient(request, oid, stage)
            if not self.passkeys.reauth_keys(session["user_id"]):
                raise ServiceError(409, "super_offer_passkey_required", "请先在账户安全页设置可验证身份的安全密钥")
            options = self.passkeys.begin_reauthentication(session, "admin", self._origin(request),
                context_kind="super_admin_" + stage, context_id=oid)
            return {"options": options, "offer": self._view(row, session["user_id"])}

    def finish(self, request, oid, stage, body):
        if stage == "ready":
            self._admin(request)
        session = self._session(request)
        self._limit(request, session, "finish")
        action = "auth.super_admin.accept" if stage == "accept" else "admin.super_admin.ready"
        key, digest = self._idempotency(request, action, oid, body)
        origin = self._origin(request)
        with self.store.tx():
            session, row = self._recipient(request, oid, stage, allow_completed=True)
            previous = self._previous(session["user_id"], action, key, digest)
            if previous:
                return previous
            self._recipient(request, oid, stage)
        credential = body.get("credential")
        if not isinstance(credential, dict):
            raise ServiceError(400, "bad_response", "安全密钥验证失败，请重新开始身份确认")
        context = {"context_kind": "super_admin_" + stage, "context_id": oid}
        challenge = self.passkeys.spend_reauthentication(session, "admin", credential, origin, **context)
        with self.store.tx():
            session, row = self._recipient(request, oid, stage)
            verified = self.passkeys.verify_reauthentication(session, "admin", challenge, credential, **context)
            ts, operation = time.time(), new_id("ao")
            prefix = "accepted" if stage == "accept" else "ready"
            self.store.execute(f"UPDATE platform_super_admin_offers SET {prefix}_at=?,{prefix}_session_id=?,"
                               f"{prefix}_challenge_id=?,{prefix}_passkey_id=? WHERE id=?",
                               (ts, session["id"], challenge["id"], verified["passkey_id"], oid))
            if stage == "accept":
                access = PlatformAccess(self.store, audit=lambda event, **kw: self.audit(request, event, **kw))
                grant = access._grant_accepted_offer(oid)["grant"]
                self.store.execute("UPDATE platform_super_admin_offers SET state='accepted',accepted_grant_id=? WHERE id=?",
                                   (grant["id"], oid))
            else:
                self.store.execute("UPDATE platform_super_admin_offers SET state='ready' WHERE id=?", (oid,))
                if not PlatformAccess(self.store).is_usable_super_admin(session["user_id"]):
                    raise ServiceError(409, "super_offer_unready", "当前账户尚不能作为可用的最高管理员，请检查授权和安全密钥")
            # Record only the proven factor. The dedicated assertion is NOT a generic scope
            # pass, nor a reason to extend idle/absolute expiry. Grant policy may shorten idle.
            self.sessions.add_factor(session["id"], "passkey_uv")
            row = self._get(oid)
            self._record(request, action, session["user_id"], oid, operation, grant_id=row["accepted_grant_id"],
                         proposer_id=row["proposer_id"], proposer_grant_id=row["proposer_grant_id"], method="passkey_uv")
            result = {"operation_id": operation, "offer": self._view(row, session["user_id"])}
            result.update({"grant_id": row["accepted_grant_id"], "next": "admin_ready"} if stage == "accept" else {"usable_super_admin": True})
            return self._save(session["user_id"], action, oid, key, digest, result)


def build_router(ident, sessions, passkeys, audit):
    r = APIRouter(tags=["admin"])
    ops = HandoverOperations(ident, sessions, passkeys, audit)

    def run(request, action, call):
        try:
            return call()
        except Exception as exc:
            ops.failure(request, action, exc)
            raise

    @r.get("/api/admin/super-admin-offers")
    def admin_list(request: Request, limit: int = Query(20, ge=1, le=50), before: str | None = Query(None, max_length=512)):
        return ops.listing(request, admin=True, limit=limit, before=before)

    @r.post("/api/admin/super-admin-offers/preview")
    def preview(request: Request, body: dict = Body(...)):
        return run(request, "admin.super_admin.offer", lambda: ops.preview(request, body))

    @r.post("/api/admin/super-admin-offers")
    def create(request: Request, body: dict = Body(...)):
        return run(request, "admin.super_admin.offer", lambda: ops.create(request, body))

    @r.post("/api/admin/super-admin-offers/{oid}/cancel")
    def cancel(request: Request, oid: str, body: dict = Body(...)):
        return run(request, "admin.super_admin.cancel", lambda: ops.close(request, oid, body, cancel=True))

    @r.get("/api/auth/super-admin-offers")
    def recipient_list(request: Request, limit: int = Query(20, ge=1, le=50), before: str | None = Query(None, max_length=512)):
        return ops.listing(request, admin=False, limit=limit, before=before)

    @r.post("/api/auth/super-admin-offers/{oid}/decline")
    def decline(request: Request, oid: str, body: dict = Body(...)):
        return run(request, "auth.super_admin.decline", lambda: ops.close(request, oid, body, cancel=False))

    @r.post("/api/auth/super-admin-offers/{oid}/accept/begin")
    def accept_begin(request: Request, oid: str):
        return run(request, "auth.super_admin.accept", lambda: ops.begin(request, oid, "accept"))

    @r.post("/api/auth/super-admin-offers/{oid}/accept/finish")
    def accept_finish(request: Request, oid: str, body: dict = Body(...)):
        return run(request, "auth.super_admin.accept", lambda: ops.finish(request, oid, "accept", body))

    @r.get("/api/admin/super-admin-offers/{oid}/readiness")
    def readiness(request: Request, oid: str):
        return ops.readiness(request, oid)

    @r.post("/api/admin/super-admin-offers/{oid}/ready/begin")
    def ready_begin(request: Request, oid: str):
        return run(request, "admin.super_admin.ready", lambda: ops.begin(request, oid, "ready"))

    @r.post("/api/admin/super-admin-offers/{oid}/ready/finish")
    def ready_finish(request: Request, oid: str, body: dict = Body(...)):
        return run(request, "admin.super_admin.ready", lambda: ops.finish(request, oid, "ready", body))

    return r
