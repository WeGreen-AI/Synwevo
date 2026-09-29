"""Platform-admin API governance with separate workspace-owner consent.

Management grants no data authority. Issuance consumes a live owner approval, and the
injected adapter accepts an explicit machine credential for every business-data request.
The identity service imports no application implementation.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math

from fastapi import APIRouter, Body, Query, Request, Response

from synwevo_core import journal
from synwevo_core.api_platform import (Approvals, ControlPlaneAdapter, MANAGE_PERMISSION, SCOPES,
                                      require_manage)
from synwevo_core.api_quotas import (CREDENTIAL_KINDS, FIELDS as QUOTA_FIELDS, LAYER_FIELDS, RANGES,
                                    WINDOW_S, Policies, RateGate, deployment_defaults)
from synwevo_core.apps import DOCS_APP_ID
from synwevo_core.auth import Auth, JournalWriteFailed, MAX_ROTATION_GRACE_S
from synwevo_core.errors import ServiceError
from synwevo_core.sessions import admin_capable
from synwevo_core.machine import SCOPE_MANAGE, SCOPE_READ, SCOPE_WRITE
from synwevo_core.oidc import Clients, Grants, MAX_SECRET_GRACE_S, docs_resource
from synwevo_core.sessions import COOKIE_NAME, Sessions
from synwevo_core.store import Store, new_id, now

PREFIX = "/api/admin/api-platform"
CONSENT = "/api/auth/api-approvals"


def build_router(store: Store, ident, sessions, audit,
                 adapter: ControlPlaneAdapter | None = None) -> APIRouter:
    r = APIRouter(tags=["api-platform"])
    approvals, keys, clients = Approvals(store), Auth(store), Clients(store)

    def actor(request: Request, *, admin: bool = True) -> dict:
        admitted = getattr(request.state, "session", None)
        session = sessions.load(request.cookies.get(COOKIE_NAME), touch=False)
        if not admitted or not session or session["id"] != admitted["id"] or session["user_id"] != admitted["user_id"]:
            raise ServiceError(401, "unauthorized", "请先登录")
        request.state.session = session
        if admin:
            user = require_manage(ident, session["user_id"])
            if not admin_capable(session.get("amr") or ""):
                raise ServiceError(403, "step_up_required", "当前登录只验证了一个因素：请在账户页输入口令确认身份后再使用 API 管理",
                                   {"scope": "admin"})
            return user
        user = ident.get_user(session["user_id"])
        if not user or user["status"] != "active":
            raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
        return user

    def mutation_actor(request, *, admin=True):
        user = actor(request, admin=admin)
        if not Sessions.reauth_fresh(request.state.session, "admin", strict=True):
            raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": "admin"})
        return user

    @contextmanager
    def mutation(request, body, action, object_id=None, *, admin=True, workspace=None, outcome=None):
        user = actor(request, admin=admin)
        reason = body.get("reason")
        meta = {"permission": MANAGE_PERMISSION if admin else "workspace.owner.consent"}
        try:
            if not Sessions.reauth_fresh(request.state.session, "admin", strict=True):
                raise ServiceError(403, "reauth_required", "这一步需要重新验证身份", {"scope": "admin"})
            if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 500:
                raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
            meta["reason"] = reason.strip()
            yield user, reason.strip()
        except ServiceError as e:
            audit(request, f"api_platform.{action}", actor_id=user["id"], workspace_id=workspace,
                  object_kind="api_platform", object_id=object_id,
                  result="denied" if e.status < 500 else "error", meta={**meta, "code": e.code})
            raise
        except (OSError, JournalWriteFailed) as e:
            audit(request, f"api_platform.{action}", actor_id=user["id"], workspace_id=workspace,
                  object_kind="api_platform", object_id=object_id, result="error",
                  meta={**meta, "code": "journal_unavailable"})
            raise ServiceError(503, "journal_unavailable", "无法持久记录安全操作，请检查安全日志") from e
        else:
            status = (outcome or {}).get("status", 200)
            audit(request, f"api_platform.{action}", actor_id=user["id"], workspace_id=workspace,
                  object_kind="api_platform", object_id=object_id,
                  result="error" if status >= 500 else "denied" if status >= 400 else "ok",
                  meta={**meta, "status": status})

    def page(source: str, columns: str, args: tuple, *, limit: int, offset: int,
             order: str) -> dict:
        with store.tx():
            total = store.one(f"SELECT COUNT(*) AS n FROM {source}", args)["n"]
            items = store.all(f"SELECT {columns} FROM {source} ORDER BY {order} LIMIT ? OFFSET ?",
                              (*args, limit, offset))
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None}

    def require_adapter() -> ControlPlaneAdapter:
        if adapter is None:
            raise ServiceError(503, "docs_adapter_unavailable", "本账户进程未连接 Docs 控制适配器")
        return adapter

    @r.get(PREFIX + "/overview")
    def overview(request: Request):
        actor(request)
        counts = store.one("SELECT COUNT(*) AS calls, SUM(CASE WHEN status>=400 THEN 1 ELSE 0 END) AS errors,"
                           "AVG(duration_ms) AS mean_duration_ms, MIN(at) AS first_at, MAX(at) AS last_at "
                           "FROM api_platform_calls WHERE at>=?", (now() - 30 * 86400,))
        # Event backlog for the overview (API-6): pending deliveries and failing subscriptions, so an
        # administrator sees a stuck receiver without opening every workspace.
        webhooks = None
        if adapter is not None:
            try:
                webhooks = adapter.webhook_overview(limit=1)["totals"]
            except Exception:  # noqa: BLE001 - the overview must render even if Docs' tables are unreadable
                webhooks = None
        return {"permission": MANAGE_PERMISSION, "adapter_available": adapter is not None, "webhooks": webhooks,
                "resource": docs_resource(), "scopes": sorted(SCOPES), "retention_days": 30,
                "calls": {**counts, "errors": counts["errors"] or 0},
                "pending_approvals": store.one("SELECT COUNT(*) AS n FROM api_platform_approvals "
                                               "WHERE status='pending' AND expires_at>?", (now(),))["n"],
                "sdk_links": [
                    {"name": "Python SDK", "url": "https://github.com/WeGreen-AI/Synwevo/tree/main/apps/docs/sdk/python"},
                    {"name": "TypeScript SDK", "url": "https://github.com/WeGreen-AI/Synwevo/tree/main/apps/docs/sdk/typescript"},
                    {"name": "API", "url": "https://github.com/WeGreen-AI/Synwevo/blob/main/docs/integration/api.md"}],
                "resource_boundary": "workspace_and_audience"}

    @r.get(PREFIX + "/approvals")
    def list_approvals(request: Request, status: str = Query("", pattern="^(|pending|approved|rejected|revoked)$"),
                       limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        result = page("api_platform_approvals WHERE (?='' OR status=?)", "*", (status, status),
                      limit=limit, offset=offset, order="created_at DESC,id DESC")
        result["items"] = [approvals.get(x["id"]) for x in result["items"]]
        return result

    @r.post(PREFIX + "/approvals", status_code=201)
    def request_approval(request: Request, body: dict = Body(...)):
        with mutation(request, body, "approval.request") as (user, reason):
            with store.tx():
                user = mutation_actor(request)
                return approvals.create(user["id"], body, reason)

    @r.get(CONSENT)
    def owner_approvals(request: Request, limit: int = Query(25, ge=1, le=100),
                        offset: int = Query(0, ge=0, le=2**31)):
        user = actor(request, admin=False)
        result = page("api_platform_approvals a JOIN memberships m ON m.workspace_id=a.workspace_id "
                      "WHERE m.user_id=? AND m.role='owner'", "a.*", (user["id"],),
                      limit=limit, offset=offset, order="a.created_at DESC,a.id DESC")
        result["items"] = [approvals.get(x["id"]) for x in result["items"]]
        return result

    @r.post(CONSENT + "/{aid}/{action}")
    def owner_decision(request: Request, aid: str, action: str, body: dict = Body(...)):
        with mutation(request, body, "approval." + action, aid, admin=False) as (user, reason):
            with store.tx():
                user = mutation_actor(request, admin=False)
                row = approvals.get(aid)
                approvals.require_owner(row["workspace_id"], user["id"])
                if action == "revoke":
                    return revoke_approval(aid, reason)
                return approvals.decide(aid, user["id"], action, body, reason)

    def revoke_approval(aid, reason):
        with store.tx():
            approvals.get(aid)
            # Journal first, including unconsumed approvals: restore must not resurrect one.
            journal.record(store, "api_platform_approval_revoked", approval_id=aid)
            store.execute("UPDATE api_platform_approvals SET status='revoked',revoked_at=?,decision_reason=? "
                          "WHERE id=?", (now(), reason, aid))
            binding = store.one("SELECT * FROM api_platform_bindings WHERE approval_id=?", (aid,))
            if binding:
                if binding["kind"] == "api_key":
                    keys.revoke_key(binding["credential_id"], reason="approval_revoked")
                else:
                    disable_client(binding["credential_id"])
            return approvals.get(aid)

    @r.post(PREFIX + "/approvals/{aid}/revoke")
    def admin_revoke(request: Request, aid: str, body: dict = Body(...)):
        with mutation(request, body, "approval.revoke", aid):
            with store.tx():
                mutation_actor(request)
                return revoke_approval(aid, body["reason"].strip())

    @r.post(PREFIX + "/approvals/{aid}/issue", status_code=201)
    def issue(request: Request, response: Response, aid: str, body: dict = Body(...)):
        with mutation(request, body, "credential.issue", aid) as (user, _):
            with store.tx():
                # Admission to the request is not authority to finish it after a demotion.
                # Serialize this check with owner authority, creation and approval binding.
                mutation_actor(request)
                row = approvals.active(aid)
                if row["consumed_at"]:
                    raise ServiceError(409, "approval_consumed", "该审批已签发凭据")
                # The issuer cannot enlarge the signed-off request via this endpoint.
                if set(body) - {"reason"}:
                    raise ServiceError(400, "unknown_field", "签发时不可修改已批准范围")
                scopes = row["effective_scopes"]
                if not scopes:
                    raise ServiceError(403, "empty_approval", "审批没有可签发的权限")
                if row["credential_kind"] == "api_key":
                    mapped = {SCOPE_READ: "read", SCOPE_WRITE: "write", SCOPE_MANAGE: "admin"}
                    token, key = keys.create_key(row["name"], row["workspace_id"],
                        tuple(mapped[s] for s in scopes), expires_in_days=(row["expires_at"] - now()) / 86400)
                    cid = key["lineage"]
                    out = {"key": token, "credential": key}
                else:
                    cid = new_id("svc")
                    client, secret = clients.register(client_id=cid, app_id=DOCS_APP_ID,
                        name=row["name"], redirect_uris=[], confidential=True, kind="service", scopes="",
                        workspace_id=row["workspace_id"], api_scopes=" ".join(scopes),
                        audiences=row["approved_resources"])
                    out = {"client_id": cid, "client_secret": secret}
                approvals.bind(aid, row["credential_kind"], cid)
            response.headers["Cache-Control"] = "no-store"
            return {**out, "approval_id": aid, "kind": row["credential_kind"], "shown_once": True}

    @r.get(PREFIX + "/credentials")
    def credentials(request: Request, workspace_id: str = Query("", max_length=100),
                    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        # last_used_at: a key's own (written at most once a minute); a service account's last recorded
        # call (30-day call window). Enough to see whether an old credential is still being called.
        source = """(SELECT 'api_key' AS kind,id,name,tenant AS workspace_id,scopes,created_at,
          expires_at,revoked_at AS disabled_at,COALESCE(lineage,id) AS binding_id,last_used_at FROM api_keys
          UNION ALL SELECT 'service_account',client_id,name,workspace_id,api_scopes,created_at,
          NULL,disabled_at,client_id,(SELECT MAX(at) FROM api_platform_calls k WHERE k.client_id=o.client_id)
          FROM oauth_clients o WHERE kind='service' AND app_id=?) c
          LEFT JOIN api_platform_bindings b ON b.kind=c.kind AND b.credential_id=c.binding_id
          WHERE (?='' OR c.workspace_id=?)"""
        result = page(source, "c.*,b.approval_id", (DOCS_APP_ID, workspace_id, workspace_id),
                      limit=limit, offset=offset, order="c.created_at DESC,c.id DESC")
        for row in result["items"]:
            row["status"] = (keys.get_key(row["id"]) or {}).get("status") if row["kind"] == "api_key" else (
                "disabled" if row["disabled_at"] else "active")
            if row["approval_id"]:
                try:
                    approved = approvals.active(row["approval_id"], tenant=row["workspace_id"])
                    row["approval_expires_at"] = approved["expires_at"]
                except ServiceError as e:
                    row["approval_error"] = e.code
                    if row["status"] == "active":
                        row["status"] = "approval_inactive"
        return result

    def disable_client(cid):
        journal.record(store, "client_disabled", client_id=cid)
        clients.disable(cid)
        return Grants(store).revoke_for_client(cid, "api_platform_disabled")

    @r.post(PREFIX + "/credentials/{kind}/{cid}/{action}")
    def credential_action(request: Request, response: Response, kind: str, cid: str,
                          action: str, body: dict = Body(...)):
        with mutation(request, body, "credential." + action, cid):
            with store.tx():
                mutation_actor(request)
                if kind not in ("api_key", "service_account") or action not in ("rotate", "disable", "enable", "revoke-tokens"):
                    raise ServiceError(400, "invalid_action", "不支持的凭据操作")
                row = keys.get_key(cid) if kind == "api_key" else clients.get_any(cid)
                if not row or (kind == "service_account" and (row["kind"] != "service" or row["app_id"] != DOCS_APP_ID)):
                    raise ServiceError(404, "not_found", "凭据不存在")
                binding = approvals.binding(kind, (row.get("lineage") or cid) if kind == "api_key" else cid)
                if action in ("rotate", "enable"):
                    if not binding:
                        raise ServiceError(403, "approval_required", "历史凭据不能通过管理权限取得密钥；请申请新的所有者审批")
                    approvals.active(binding["approval_id"], tenant=row.get("tenant") or row.get("workspace_id"))
                if action == "rotate":
                    grace = body.get("grace_seconds", 0)
                    cap = MAX_ROTATION_GRACE_S if kind == "api_key" else MAX_SECRET_GRACE_S
                    if type(grace) not in (int, float) or not math.isfinite(grace) or not 0 <= grace <= cap:
                        raise ServiceError(400, "bad_grace", "密钥重叠有效期超出允许范围")
                    with store.tx():
                        # Recheck under the same write lock as rotation, including owner changes.
                        approvals.active(binding["approval_id"])
                        if kind == "api_key":
                            token, key, _ = keys.rotate_key(cid, grace_s=grace, keep_absolute_expiry=True)
                            out = {"key": token, "credential": key}
                        else:
                            rotated = clients.rotate_secret(cid, grace_s=grace)
                            journal.record(store, "client_secret_rotated", client_id=cid, secret_hash=rotated["secret_hash"])
                            if not grace:
                                Grants(store).revoke_for_client(cid, "secret_rotated")
                            out = {"client_id": cid, "client_secret": rotated["secret"], "grace_until": rotated["grace_until"]}
                    response.headers["Cache-Control"] = "no-store"
                    return {**out, "shown_once": True}
                if action == "disable":
                    if kind == "api_key":
                        return {"keys_revoked": keys.revoke_key(cid, "api_platform_revoked")}
                    with store.tx():
                        return {"tokens_revoked": disable_client(cid), "disabled": True}
                if kind == "api_key":
                    raise ServiceError(400, "invalid_action", "访问密钥撤销后不可恢复")
                if action == "enable":
                    with store.tx():
                        approvals.active(binding["approval_id"])
                        return {"enabled": clients.enable(cid)}
                with store.tx():
                    journal.record(store, "tokens_revoked", client_id=cid)
                    return {"tokens_revoked": Grants(store).revoke_for_client(cid, "api_platform_revoked")}

    @r.get(PREFIX + "/calls")
    def calls(request: Request, workspace_id: str = Query("", max_length=100),
              errors_only: bool = False, request_id: str = Query("", max_length=128),
              credential_id: str = Query("", max_length=200), route: str = Query("", max_length=200),
              status: str = Query("", pattern="^(|2xx|3xx|4xx|5xx|429)$"),
              error_code: str = Query("", max_length=64),
              limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        # Diagnosis by request ID, caller, route, status class or refusal code. Every filter is an
        # exact match on stored metadata (never a LIKE over user input), and the record holds no
        # token, body, query or document content to find.
        actor(request)
        lo, hi = {"2xx": (200, 299), "3xx": (300, 399), "4xx": (400, 499), "5xx": (500, 599),
                  "429": (429, 429)}.get(status, (0, 999))
        return page("api_platform_calls WHERE at>=? AND (?='' OR workspace_id=?) AND (?=0 OR status>=400) "
                    "AND (?='' OR request_id=?) AND (?='' OR credential_id=? OR client_id=?) AND (?='' OR route=?) "
                    "AND status BETWEEN ? AND ? AND (?='' OR error_code=?)",
                    "*", (now() - 30 * 86400, workspace_id, workspace_id, int(errors_only),
                          request_id, request_id, credential_id, credential_id, credential_id, route, route,
                          lo, hi, error_code, error_code),
                    limit=limit, offset=offset, order="at DESC,id DESC")

    # ------------------------------------------------------------------ traffic and capacity
    policies, rate_gate = Policies(store), RateGate(store)

    def quota_target(scope_kind: str, scope_id: str) -> dict:
        Policies.validate_target(scope_kind, scope_id)
        target = policies.target_exists(scope_kind, scope_id)
        if target is None:
            raise ServiceError(404, "not_found", "策略目标不存在")
        return target

    @r.get(PREFIX + "/quotas")
    def quotas(request: Request, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        return {"window_s": WINDOW_S, "fields": list(QUOTA_FIELDS),
                "layer_fields": {k: list(v) for k, v in LAYER_FIELDS.items()},
                "ranges": {k: {"min": lo, "max": hi} for k, (lo, hi) in RANGES.items()},
                "deployment_defaults": deployment_defaults(),
                "platform": {"policy": policies.get("platform", ""), "effective": policies.effective(None)},
                "overrides": policies.overrides(limit=limit, offset=offset),
                "enforcement": {"shared": True, "applies": "next_request"}}

    @r.get(PREFIX + "/quotas/effective")
    def quota_effective(request: Request, workspace_id: str = Query("", max_length=100),
                        credential: str = Query("", max_length=200)):
        # What a given workspace (and optionally one of its credentials) is held to right now, which
        # layer decided each value, and how much of each budget is in use at this moment.
        actor(request)
        if not workspace_id and not credential:
            raise ServiceError(400, "target_required", "请指定工作空间或凭据")
        if credential:
            target = quota_target("credential", credential)
            if workspace_id and target["workspace_id"] != workspace_id:
                raise ServiceError(400, "credential_not_in_workspace", "该凭据不属于所选工作空间")
            workspace_id = target["workspace_id"]
        else:
            quota_target("workspace", workspace_id)
        active = store.one("SELECT COUNT(*) AS n FROM jobs WHERE status IN ('queued','running','cancelling') "
                           "AND tenant=?", (workspace_id,))["n"]
        layers = policies.layers(workspace_id, credential or None)
        return {"workspace_id": workspace_id, "credential": credential or None,
                "policies": {k: v for k, v in layers.items()},
                "effective": policies.effective(workspace_id, credential or None),
                "usage": {**rate_gate.usage(workspace_id, credential or None),
                          "active_jobs": {"now": active, "measured_at": now()}}}

    def _quota_scope(scope: str) -> tuple[str, str]:
        if scope == "platform":
            return "platform", ""
        kind, _, rest = scope.partition("/")
        if kind == "workspace" and rest:
            return "workspace", rest
        if kind == "credential" and rest:
            return "credential", rest
        raise ServiceError(400, "invalid_scope", "不支持的策略范围")

    @r.post(PREFIX + "/quotas/preview/{scope:path}")
    def quota_preview(request: Request, scope: str, body: dict = Body(...)):
        # A dry run: the effective values before and after and what happens to work in flight.
        # Nothing is written, so no reason or re-authentication is asked for.
        actor(request)
        kind, sid = _quota_scope(scope)
        quota_target(kind, sid)
        return policies.change(kind, sid, {k: v for k, v in body.items() if k != "reason"},
                               actor_id="", reason="", dry_run=True)

    @r.put(PREFIX + "/quotas/{scope:path}")
    def quota_change(request: Request, scope: str, body: dict = Body(...)):
        # The workspace is looked up only to attribute the audit row; every refusal (unknown target,
        # stale version, out-of-range value) happens inside `mutation` and is audited as denied.
        kind, sid = _quota_scope(scope)
        try:
            found = policies.target_exists(kind, sid) if kind == "platform" or sid else None
        except (ServiceError, ValueError):
            found = None
        ws = sid if kind == "workspace" else (found or {}).get("workspace_id")
        with mutation(request, body, "quota.change", f"{kind}:{sid}" if sid else kind, workspace=ws) as (user, reason):
            with store.tx():
                mutation_actor(request)
                quota_target(kind, sid)
                return policies.change(kind, sid, {k: v for k, v in body.items() if k != "reason"},
                                       actor_id=user["id"], reason=reason)

    @r.get(PREFIX + "/quotas/history/{scope:path}")
    def quota_history(request: Request, scope: str, limit: int = Query(25, ge=1, le=100),
                      offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        kind, sid = _quota_scope(scope)
        Policies.validate_target(kind, sid)
        return policies.history(kind, sid, limit=limit, offset=offset)

    # ------------------------------------------------------------------ webhook operations (API-6)
    @r.get(PREFIX + "/webhooks")
    def webhook_overview(request: Request, workspace_id: str = Query("", max_length=100),
                         status: str = Query("", pattern="^(|active|paused|disabled)$"), failing: bool = False,
                         limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        return require_adapter().webhook_overview(workspace_id=workspace_id, status=status, failing=failing,
                                                  limit=limit, offset=offset)

    @r.post(PREFIX + "/webhooks/{sid}/pause")
    def webhook_pause(request: Request, sid: str, body: dict = Body(...)):
        adapter_ = require_adapter()
        with mutation(request, body, "webhook.pause", sid, workspace=adapter_.webhook_tenant(sid)):
            mutation_actor(request)
            if adapter_.webhook_tenant(sid) is None:
                raise ServiceError(404, "not_found", "订阅不存在")
            return adapter_.webhook_pause(sid)

    @r.get(PREFIX + "/openapi")
    def openapi(request: Request):
        actor(request)
        return require_adapter().contract()

    @r.get(PREFIX + "/operations")
    def operations(request: Request, q: str = Query("", max_length=100),
                   limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0, le=2**31)):
        actor(request)
        paths = require_adapter().contract()["paths"]
        items = [{"path": path, "method": method.upper(), **op} for path, methods in paths.items()
                 for method, op in methods.items() if method in ("get", "post", "put", "patch", "delete")]
        items = [x for x in items if q.casefold() in json.dumps(x, ensure_ascii=False).casefold()]
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < len(items) else None}

    @r.post(PREFIX + "/request")
    async def controlled_request(request: Request, response: Response, body: dict = Body(...)):
        # Read-only debugging can disclose document contents, so it also requires an explicit
        # confirmation and reason. Audit records no bearer, response, body, URL or query.
        outcome = {}
        with mutation(request, body, "request", outcome=outcome):
            allowed = {"reason", "credential", "method", "path", "query", "body"}
            if set(body) - allowed:
                raise ServiceError(400, "unknown_field", "不支持额外的请求字段")
            result = await require_adapter().request(body.get("credential"), body.get("method", "GET"),
                body.get("path"), body.get("query", {}), body.get("body"))
            # Awaiting the data-plane request can outlive logout, demotion or reauthentication.
            # Never deliver its business response under the admission-time browser authority.
            mutation_actor(request)
            outcome["status"] = result["status"]
            response.headers["Cache-Control"] = "no-store"
            return result

    return r
