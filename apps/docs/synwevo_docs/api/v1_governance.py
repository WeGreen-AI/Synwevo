"""Machine API: governance routes. Included by `v1.build_router` under the /api/v1 prefix.

What an automation needs to know about itself and to look after its own credential, without a
person opening a product page for it (there is no such page, on purpose):

* ``GET  /usage``                      — this workspace's live job numbers and the limits that apply
* ``GET  /credentials/self``           — metadata of the credential making the call (never a secret)
* ``POST /credentials/self/rotate``    — a ``dw_`` key replaces itself; the lineage continues
* ``POST /credentials/self/revoke``    — the caller ends its own credential, immediately

Rotation and revocation are different acts and are kept apart. Rotation is hygiene: the new key
continues the old one's lineage, so queued work is unaffected, and the old key may stay valid for
a short, bounded grace so a deployment can switch over; the rotated-out key is journalled
(`api_key_superseded`) so a restored backup does not bring it back as the live key. Revocation is
a security action: it ends the lineage (or the grant family) at once and is written to the
security journal so a restored backup cannot bring it back.

What revocation does to work already queued depends on what that work is authorised against
(`synwevo_core.authority`):

* a ``dw_`` key — the lineage: its queued jobs and webhook deliveries halt;
* a delegation — the grant family: halts;
* a service account — the **account**, not a token. Revoking "self" ends every outstanding
  access token of the account, but the account itself stays registered and enabled, so work it
  already queued keeps running. Stopping that is the operator's call: disable the client in the
  account service (`python -m synwevo_identity.cli client disable <client_id>`).

Service-account secrets are not rotated here: a service account's secret is registered and
rotated by the platform operator in the account service's CLI, which is where its lifecycle lives.
"""
from __future__ import annotations

import math

from fastapi import APIRouter, Body, Request

from synwevo_core import authority
from synwevo_core.api_quotas import Policies, credential_scope
from synwevo_core.auth import MAX_ROTATION_GRACE_S, JournalWriteFailed, audit_event
from synwevo_core.errors import ServiceError

from ..engine.limits import LIMITS

JOURNAL_FAILED = ("已撤销（立即生效），但写入安全日志失败：从备份恢复数据库时，这次撤销不会被自动重放。"
                  "请通知运维检查数据目录的写权限。")


def _principal(request: Request) -> dict:
    p = getattr(request.state, "principal", None)
    if not p:
        raise ServiceError(401, "unauthorized", "缺少 API 凭据")
    return p


def build_router(app_, views, jobs, auth) -> APIRouter:
    r = APIRouter(tags=["v1"])
    store = app_.store
    keys = getattr(auth, "legacy", auth)          # MachineAuth wraps the dw_ key store

    def scope(request: Request, needed: str) -> dict:
        p = _principal(request)
        auth.require(p, needed)
        return p

    def audit(request: Request, p: dict, action: str, *, object_kind: str, object_id: str | None,
              result: str = "ok", meta: dict | None = None) -> None:
        audit_event(store, action, actor_kind="machine", actor_id=authority.label(authority.describe(p)),
                    workspace_id=p.get("tenant"), object_kind=object_kind, object_id=object_id,
                    result=result, meta=meta, request=request)

    # ------------------------------------------------------------ usage
    @r.get("/usage", summary="本工作空间的任务用量与上限")
    def usage(request: Request):
        p = scope(request, "read")
        if jobs is None:
            raise ServiceError(503, "jobs_unavailable", "本进程没有运行任务服务，暂时无法统计用量")
        u = jobs.usage(p["tenant"])
        limits = dict(u.get("limits") or {})
        # The numbers the gate and the queue actually enforce for this caller: the layered policy
        # (credential → workspace → platform → deployment default), shared by every process.
        eff = Policies(store).effective(p["tenant"], credential_scope(p))
        limits.update({
            "requests_per_minute": eff["credential_rpm"]["value"],
            "workspace_requests_per_minute": eff["workspace_rpm"]["value"],
            "policy_sources": {"requests_per_minute": eff["credential_rpm"]["source"],
                               "workspace_requests_per_minute": eff["workspace_rpm"]["source"],
                               "max_active_jobs": eff["active_jobs"]["source"]},
            "max_file_mb": LIMITS.max_file_bytes // (1024 * 1024),
            "max_records_per_job": LIMITS.max_records_per_job,
            "max_pages": LIMITS.max_pages,
            "max_cells": LIMITS.max_cells,
        })
        # Every counter the job service reports, as it reports it: this route adds the transport
        # limits and nothing else, so it cannot drift from what the queue actually enforces.
        counters = {k: v for k, v in u.items() if k != "limits"}
        return {"workspace": p["tenant"], **counters, "limits": limits}

    # ------------------------------------------------------------ the caller's own credential
    @r.get("/credentials/self", summary="当前凭据的元数据（不含任何密钥）")
    def credential_self(request: Request):
        # Any valid credential may read its own metadata: knowing when you expire or whether
        # you are inside a rotation grace is what lets an automation rotate in time.
        return _describe(_principal(request))

    def _describe(p: dict) -> dict:
        kind = p.get("kind")
        base = {"kind": kind, "name": p.get("name"), "workspace": p.get("tenant"),
                "scopes": sorted(p.get("scopes") or ())}
        if kind == "api_key":
            k = keys.get_key(p["key_id"]) or {}
            lineage = [{"key_id": x["id"], "created_at": x["created_at"], "expires_at": x["expires_at"],
                        "status": x["status"], "replaced_by": x["replaced_by"]}
                       for x in keys.lineage_keys(p["key_id"])]
            return {**base, "key_id": p["key_id"], "lineage": k.get("lineage") or p.get("lineage"),
                    "created_at": k.get("created_at"), "expires_at": k.get("expires_at"),
                    "last_used_at": k.get("last_used_at"), "status": k.get("status"),
                    "replaced_by": k.get("replaced_by"), "lineage_keys": lineage,
                    "rotation": {"self_service": _can_manage(p),
                                 "max_grace_minutes": MAX_ROTATION_GRACE_S // 60,
                                 "requires": "admin 或 docweave.manage 权限"}}
        if kind == "service_account":
            c = store.one("SELECT client_id, name, api_scopes, created_at, updated_at FROM oauth_clients "
                          "WHERE client_id=?", (p["client_id"],)) or {}
            return {**base, "client_id": p["client_id"], "registered_scopes": sorted((c.get("api_scopes") or "").split()),
                    "registered_at": c.get("created_at"), "token_expires_at": p.get("expires_at"),
                    "rotation": {"self_service": False,
                                 "how": "服务账户的 client_secret 由平台管理员在账户服务命令行轮换"}}
        if kind == "delegated":
            return {**base, "client_id": p["client_id"], "user_id": p["user_id"], "role": p.get("role"),
                    "can_export": bool(p.get("can_export")), "grant_family": p.get("grant_family"),
                    "token_expires_at": p.get("expires_at"),
                    "rotation": {"self_service": False, "how": "用刷新令牌换取新令牌，或让本人重新授权"}}
        return base

    @r.post("/credentials/self/rotate", status_code=201, summary="轮换当前 API 密钥（新密钥只返回这一次）")
    def rotate_self(request: Request, body: dict = Body(default={})):
        p = _principal(request)
        if p.get("kind") != "api_key":
            raise ServiceError(400, "not_rotatable",
                               "只有 dw_ API 密钥可以在这里自行轮换。服务账户的 client_secret 由平台管理员在账户服务"
                               "命令行轮换；用户委托的令牌用刷新令牌更新，或由本人重新授权。")
        if not _can_manage(p):
            raise ServiceError(403, "forbidden",
                               "自行轮换需要 admin（或 docweave.manage）权限；没有这个权限的密钥请由运维用 "
                               "`python -m synwevo_docs.cli apikey rotate <key_id>` 轮换")
        grace_min = _grace_minutes(body)
        try:
            # The holder's rotation keeps the absolute expiry: a key cannot extend itself.
            token, new, old = keys.rotate_key(p["key_id"], grace_s=grace_min * 60, keep_absolute_expiry=True)
        except ServiceError as e:
            # journal_failed is not a refusal: the rotation was rolled back, nothing changed.
            audit(request, p, "api.credential.rotate", object_kind="api_key", object_id=p["key_id"],
                  result="error" if e.status >= 500 else "denied", meta={"reason": e.code})
            raise
        audit(request, p, "api.credential.rotate", object_kind="api_key", object_id=p["key_id"],
              meta={"lineage": new["lineage"], "replaced_by": new["id"], "grace_minutes": grace_min})
        return {"key": token, "key_id": new["id"], "lineage": new["lineage"], "workspace": new["tenant"],
                "scopes": new["scopes"].split(","), "created_at": new["created_at"],
                "expires_at": new["expires_at"],
                "previous": {"key_id": old["id"], "status": old["status"],
                             "valid_until": old["expires_at"] if old["status"] == "grace" else old["revoked_at"]},
                "notice": "新密钥只返回这一次，服务器只保存哈希。"}

    @r.post("/credentials/self/revoke", summary="撤销当前凭据（立即生效，不可恢复）")
    def revoke_self(request: Request):
        # Any scope may do this. A credential that suspects it has leaked must be able to end
        # itself without first holding a stronger permission than the leak exposed.
        p = _principal(request)
        kind = p.get("kind")
        from synwevo_core import journal
        if kind == "api_key":
            lineage = p.get("lineage") or p["key_id"]
            try:
                n = keys.revoke_key(p["key_id"], reason="self_revoked")
            except JournalWriteFailed as e:
                audit(request, p, "api.credential.revoke", object_kind="api_key", object_id=p["key_id"],
                      result="error", meta={"lineage": lineage, "revoked": e.revoked, "journal": "failed"})
                raise ServiceError(500, "journal_failed", JOURNAL_FAILED, {"revoked": e.revoked}) from e
            audit(request, p, "api.credential.revoke", object_kind="api_key", object_id=p["key_id"],
                  meta={"lineage": lineage, "revoked": n})
            return {"revoked": True, "kind": kind, "key_id": p["key_id"], "lineage": lineage,
                    "keys_revoked": n, "queued_work": "halted"}

        from synwevo_core.oidc import Grants
        grants = Grants(store)
        if kind == "delegated":
            family = p.get("grant_family")
            n = grants.revoke_family(family, "self_revoked")
            entry, obj = {"family_id": family}, ("grant", family)
        elif kind == "service_account":
            # Every outstanding token of this service account. Its client_secret stays valid:
            # disabling or re-keying the account is the operator's call, in the account service.
            n = grants.revoke_for_client(p["client_id"], "self_revoked")
            entry, obj = {"client_id": p["client_id"]}, ("oauth_client", p["client_id"])
        else:
            raise ServiceError(400, "not_revocable", "这种凭据不能在这里撤销")
        try:
            journal.record(store, "tokens_revoked", **entry)
        except OSError as e:
            audit(request, p, "api.credential.revoke", object_kind=obj[0], object_id=obj[1],
                  result="error", meta={"revoked": n, "journal": "failed"})
            raise ServiceError(500, "journal_failed", JOURNAL_FAILED, {"revoked": n}) from e
        audit(request, p, "api.credential.revoke", object_kind=obj[0], object_id=obj[1], meta={"revoked": n})
        out = {"revoked": True, "kind": kind, "tokens_revoked": n}
        if kind == "service_account":
            out["queued_work"] = "continues"
            out["notice"] = ("该服务账户当前签发的访问令牌已全部撤销；client_secret 仍然有效，"
                             "它已经提交的任务与 Webhook 投递会继续执行（它们依据的是服务账户本身，而不是某个令牌）。"
                             "如怀疑 client_secret 泄露，或需要停止这些任务，请让平台管理员在账户服务停用"
                             "（client disable）或轮换该服务账户。")
        else:
            out["queued_work"] = "halted"
        return out

    def _can_manage(p: dict) -> bool:
        try:
            auth.require(p, "admin")
            return True
        except ServiceError:
            return False

    return r


def _grace_minutes(body: dict) -> float:
    raw = (body or {}).get("grace_minutes", 0)
    if raw is None:
        return 0
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ServiceError(400, "invalid", "grace_minutes 必须是数字（分钟）", {"field": "grace_minutes"})
    # Python's JSON parser accepts the non-standard NaN / Infinity literals. NaN compares false
    # to everything, so a range check written as "reject if out of range" let it through.
    if not math.isfinite(raw) or not (0 <= raw * 60 <= MAX_ROTATION_GRACE_S):
        raise ServiceError(400, "bad_grace", f"grace_minutes 必须在 0 到 {MAX_ROTATION_GRACE_S // 60} 之间",
                           {"field": "grace_minutes"})
    return raw
