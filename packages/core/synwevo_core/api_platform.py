"""Shared control-plane authority; never a replacement for application object checks.

Store alone executes SCHEMA_SQL. Bindings persist across key rotation via the existing
lineage, and across token rotation via client_id. Unbound pre-existing credentials retain
their compatibility contract. A managed credential cannot shed its binding.
"""
from __future__ import annotations

import json
from typing import Protocol

from .apps import Apps, DOCS_APP_ID
from .errors import ServiceError
from .identity import Identity
from .machine import SCOPE_MANAGE, SCOPE_READ, SCOPE_WRITE
from .oidc import docs_resource
from .store import Store, new_id, now

MANAGE_PERMISSION = "api_platform.manage"
SCOPES = frozenset((SCOPE_READ, SCOPE_WRITE, SCOPE_MANAGE))

# Imported and executed by Store's migration only, never by a router/service constructor.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS api_platform_approvals (
 id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, requested_by TEXT NOT NULL,
 name TEXT NOT NULL, credential_kind TEXT NOT NULL,
 requested_scopes TEXT NOT NULL, requested_resources TEXT NOT NULL,
 approved_scopes TEXT NOT NULL DEFAULT '[]', approved_resources TEXT NOT NULL DEFAULT '[]',
 status TEXT NOT NULL DEFAULT 'pending', approved_by TEXT, reason TEXT NOT NULL,
 decision_reason TEXT, created_at REAL NOT NULL, expires_at REAL NOT NULL,
 decided_at REAL, consumed_at REAL, revoked_at REAL,
 CHECK (credential_kind IN ('api_key','service_account')),
 CHECK (status IN ('pending','approved','rejected','revoked'))
);
CREATE INDEX IF NOT EXISTS api_platform_approvals_workspace
 ON api_platform_approvals(workspace_id, created_at);
CREATE TABLE IF NOT EXISTS api_platform_bindings (
 kind TEXT NOT NULL, credential_id TEXT NOT NULL,
 approval_id TEXT NOT NULL UNIQUE REFERENCES api_platform_approvals(id),
 created_at REAL NOT NULL, PRIMARY KEY(kind,credential_id),
 CHECK (kind IN ('api_key','service_account'))
);
CREATE TABLE IF NOT EXISTS api_platform_calls (
 id TEXT PRIMARY KEY, at REAL NOT NULL, workspace_id TEXT, credential_kind TEXT,
 credential_id TEXT, method TEXT NOT NULL, route TEXT NOT NULL,
 status INTEGER NOT NULL, duration_ms REAL NOT NULL, request_id TEXT
);
CREATE INDEX IF NOT EXISTS api_platform_calls_at ON api_platform_calls(at);
CREATE INDEX IF NOT EXISTS api_platform_calls_workspace ON api_platform_calls(workspace_id,at);
CREATE INDEX IF NOT EXISTS api_platform_calls_request ON api_platform_calls(request_id);
"""
# Added to api_platform_calls after it first shipped (API-1): the refusal's error code, so a diagnosis
# by request ID says *why* ("rate_limited", "queue_full"), and the OAuth client behind a delegated
# call, which has no credential binding of its own. Rollback: older code ignores both columns.
CALL_COLUMNS = [("api_platform_calls", "error_code", "TEXT"), ("api_platform_calls", "client_id", "TEXT")]


class ControlPlaneAdapter(Protocol):
    def contract(self) -> dict: ...
    async def request(self, credential: str, method: str, path: str,
                      query: dict, body: dict | None) -> dict: ...
    def webhook_overview(self, *, workspace_id: str = "", status: str = "", failing: bool = False,
                         limit: int = 25, offset: int = 0) -> dict: ...
    def webhook_pause(self, sid: str) -> dict: ...
    def webhook_tenant(self, sid: str) -> str | None: ...
    def jobs_overview(self, *, workspace_id: str = "", status: str = "", origin: str = "",
                      limit: int = 25, offset: int = 0) -> dict: ...
    def job_tenant(self, jid: str) -> str | None: ...
    def job_cancel(self, jid: str) -> dict: ...


def require_manage(ident: Identity, user_id: str) -> dict:
    """The caller, if a platform grant gives them `api_platform.manage` (platform_access.py).

    Read afresh on every call, so a revocation between admission and issuance is seen by the
    re-check inside the operation. An account with no platform permission at all gets the 404 of
    a page that does not exist; one that holds other platform permissions gets 403
    `platform_permission_denied`. Workspace and organization roles never reach this permission.
    """
    from .platform_access import PlatformAccess
    user = ident.get_user(user_id)
    if not user or user["status"] != "active":
        raise ServiceError(401, "unauthorized", "账户不可用，请重新登录")
    held = PlatformAccess(ident.store).effective_permissions(user)
    if not held:
        raise ServiceError(404, "not_found", "页面不存在")
    if MANAGE_PERMISSION not in held:
        raise ServiceError(403, "platform_permission_denied", "你的平台角色不包含这项操作所需的权限",
                           {"permission": MANAGE_PERMISSION})
    return user


def _strings(value, *, field: str, allowed: set | frozenset | None = None) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 10 or any(
            not isinstance(x, str) or not x or len(x) > 500 for x in value):
        raise ServiceError(400, "invalid_field", "请选择有效的权限与资源", {"field": field})
    result = sorted(set(value))
    if allowed is not None and not set(result) <= allowed:
        raise ServiceError(400, "invalid_field", "包含不支持的权限或资源", {"field": field})
    return result


def _decoded(row: dict) -> dict:
    return {**row, **{k: json.loads(row[k]) if isinstance(row[k], str) else row[k]
                     for k in ("requested_scopes", "requested_resources", "approved_scopes", "approved_resources")}}


class Approvals:
    def __init__(self, store: Store):
        self.store = store
        self.ident = Identity(store)

    def get(self, aid: str) -> dict:
        row = self.store.one("SELECT * FROM api_platform_approvals WHERE id=?", (aid,))
        if not row:
            raise ServiceError(404, "not_found", "审批不存在")
        return _decoded(row)

    def require_owner(self, workspace_id: str, uid: str) -> None:
        user = self.ident.get_user(uid)
        member = self.ident.membership(workspace_id, uid)
        if not user or user["status"] != "active" or not member or member["role"] != "owner":
            raise ServiceError(403, "owner_required", "需要该工作空间当前所有者批准")
        ok, wid, _ = Apps(self.store, self.ident).check_access(uid, DOCS_APP_ID, workspace_id)
        if not ok or wid != workspace_id:
            raise ServiceError(403, "approver_unavailable", "审批人当前没有此应用的使用资格")

    def create(self, actor_id: str, body: dict, reason: str) -> dict:
        require_manage(self.ident, actor_id)
        workspace = body.get("workspace_id")
        ws = self.ident.get_workspace(workspace) if isinstance(workspace, str) else None
        if not ws or ws["status"] != "active":
            raise ServiceError(400, "workspace_unavailable", "请选择有效工作空间")
        name = body.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100:
            raise ServiceError(400, "invalid_name", "凭据名称必须为 1 至 100 个字")
        kind = body.get("credential_kind")
        if kind not in ("api_key", "service_account"):
            raise ServiceError(400, "invalid_kind", "不支持的凭据类型")
        scopes = _strings(body.get("scopes"), field="scopes", allowed=SCOPES)
        resources = _strings(body.get("resources"), field="resources", allowed={docs_resource()})
        days = body.get("expires_in_days", 30)
        if type(days) is not int or not 1 <= days <= 365:
            raise ServiceError(400, "invalid_expiry", "审批有效期必须为 1 至 365 天")
        if kind == "api_key" and SCOPE_MANAGE in scopes and set(scopes) != SCOPES:
            raise ServiceError(400, "legacy_admin_scope", "访问密钥的管理权限包含读写权限，请明确选择全部权限")
        aid, ts = new_id("apr"), now()
        self.store.insert("api_platform_approvals", {
            "id": aid, "workspace_id": workspace, "requested_by": actor_id, "name": name.strip(),
            "credential_kind": kind, "requested_scopes": json.dumps(scopes),
            "requested_resources": json.dumps(resources), "reason": reason,
            "created_at": ts, "expires_at": ts + days * 86400})
        return self.get(aid)

    def decide(self, aid: str, uid: str, action: str, body: dict, reason: str) -> dict:
        with self.store.tx():
            row = self.get(aid)
            self.require_owner(row["workspace_id"], uid)
            if row["status"] != "pending" or row["expires_at"] <= now():
                raise ServiceError(409, "approval_not_pending", "审批已处理或已过期")
            if action not in ("approve", "reject"):
                raise ServiceError(400, "invalid_action", "不支持的操作")
            scopes, resources = [], []
            if action == "approve":
                wanted = _strings(body.get("scopes"), field="scopes", allowed=SCOPES)
                targets = _strings(body.get("resources"), field="resources", allowed={docs_resource()})
                scopes = sorted(set(wanted) & set(row["requested_scopes"]))
                resources = sorted(set(targets) & set(row["requested_resources"]))
                if not scopes or not resources:
                    raise ServiceError(400, "empty_approval", "审批范围与申请没有交集")
                if row["credential_kind"] == "api_key" and SCOPE_MANAGE in scopes and set(scopes) != SCOPES:
                    raise ServiceError(400, "legacy_admin_scope", "访问密钥的管理权限必须同时批准读写权限")
            self.store.execute("UPDATE api_platform_approvals SET status=?,approved_by=?,approved_scopes=?,"
                               "approved_resources=?,decision_reason=?,decided_at=? WHERE id=?",
                               ("approved" if action == "approve" else "rejected", uid, json.dumps(scopes),
                                json.dumps(resources), reason, now(), aid))
            return self.get(aid)

    def active(self, aid: str, *, tenant: str | None = None) -> dict:
        row = self.get(aid)
        if row["status"] != "approved" or row["revoked_at"] or row["expires_at"] <= now():
            raise ServiceError(403, "approval_inactive", "工作空间审批无效、已撤回或已过期")
        if tenant is not None and tenant != row["workspace_id"]:
            raise ServiceError(403, "approval_resource_mismatch", "凭据超出已批准的工作空间")
        self.require_owner(row["workspace_id"], row["approved_by"])
        if docs_resource() not in set(row["approved_resources"]) & set(row["requested_resources"]):
            raise ServiceError(403, "approval_resource_mismatch", "凭据超出已批准的 API 资源")
        row["effective_scopes"] = sorted(set(row["approved_scopes"]) & set(row["requested_scopes"]) & SCOPES)
        return row

    def bind(self, aid: str, kind: str, credential_id: str) -> None:
        # Caller holds the transaction that created the credential, so a failed binding
        # cannot leave an unbound credential usable outside the approval checks.
        row = self.active(aid)
        if row["consumed_at"] or row["credential_kind"] != kind:
            raise ServiceError(409, "approval_consumed", "审批已使用或凭据类型不匹配")
        self.store.insert("api_platform_bindings", {"kind": kind, "credential_id": credential_id,
                                                   "approval_id": aid, "created_at": now()})
        self.store.execute("UPDATE api_platform_approvals SET consumed_at=? WHERE id=?", (now(), aid))

    def binding(self, kind: str, credential_id: str) -> dict | None:
        return self.store.one("SELECT * FROM api_platform_bindings WHERE kind=? AND credential_id=?",
                              (kind, credential_id))


def _binding_id(p: dict) -> str | None:
    if p.get("kind") == "api_key":
        return p.get("lineage") or p.get("key_id")
    if p.get("kind") == "service_account":
        return p.get("client_id")
    return None


def enforce_principal(store: Store, principal: dict) -> dict:
    """MachineAuth.verify hook; checks live approval, then intersects permissions."""
    ident = _binding_id(principal)
    if ident is None:
        return principal
    approvals = Approvals(store)
    binding = approvals.binding(principal["kind"], ident)
    if not binding:
        return principal
    row = approvals.active(binding["approval_id"], tenant=principal.get("tenant"))
    allowed = set(row["effective_scopes"])
    original = set(principal.get("scopes") or ())
    if principal["kind"] == "api_key":
        mapping = {"read": SCOPE_READ, "write": SCOPE_WRITE, "admin": SCOPE_MANAGE}
        if "admin" in original:
            original.update(("read", "write"))
        # admin is a legacy wildcard, so preserve it only when ALL permissions survive.
        scopes = {s for s in original if mapping.get(s) in allowed and (s != "admin" or SCOPES <= allowed)}
    else:
        scopes = original & allowed
    if not scopes:
        raise ServiceError(403, "approval_scope_withdrawn", "凭据已没有获批权限")
    return {**principal, "scopes": tuple(sorted(scopes)), "approval_id": row["id"]}


def approved_service_scopes(store: Store, client: dict, scope: str) -> str:
    p = enforce_principal(store, {"kind": "service_account", "client_id": client["client_id"],
                                 "tenant": client.get("workspace_id"), "scopes": scope.split()})
    return " ".join(p["scopes"])


def approval_failure(store: Store, desc: dict, *, need: str = "write") -> str | None:
    """Additional authority.check hook for queued work and signed-link redemption."""
    required = SCOPE_WRITE if need == "write" else SCOPE_READ
    cid = _binding_id(desc)
    if cid is None:
        return None
    approvals = Approvals(store)
    binding = approvals.binding(desc["kind"], cid)
    if not binding:
        return None
    try:
        row = approvals.active(binding["approval_id"], tenant=desc.get("tenant"))
        return None if required in row["effective_scopes"] else "approval_scope_withdrawn"
    except ServiceError as e:
        return e.code


def record_call(store: Store, principal: dict | None, method: str, route: str, status: int,
                duration_ms: float, request_id: str | None, error_code: str | None = None) -> None:
    """Persist metadata only. Caller passes the matched template, NEVER the request URL.

    Unmatched paths are collapsed, so user-controlled paths cannot store document names,
    credentials or local paths. Raw headers, body, query and response are never accepted.
    """
    p = principal or {}
    if not route.startswith("/api/v1/") or "?" in route or len(route) > 200:
        route = "/api/v1/<unmatched>"
    with store.tx():
        store.insert("api_platform_calls", {"id": new_id("call"), "at": now(),
            "workspace_id": p.get("tenant"), "credential_kind": p.get("kind"),
            "credential_id": _binding_id(p), "client_id": p.get("client_id"),
            "method": method[:10], "route": route,
            "status": status, "duration_ms": max(0, round(duration_ms, 3)),
            "request_id": request_id[:128] if request_id else None,
            # A stable machine code from the error contract, never a message or a payload.
            "error_code": error_code[:64] if isinstance(error_code, str) and status >= 400 else None})
        # Bounded retention; aggregate statistics explicitly describe this retained window.
        store.execute("DELETE FROM api_platform_calls WHERE at<?", (now() - 30 * 86400,))
