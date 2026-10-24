"""Platform administration of MCP: the versioned policy (signed preview, then publish), a masked view
of every connection's health, and the revocation of a connection or a tool grant.

Reads need `platform.config.view`; publishing needs `platform.config.manage` with the dangerous
operation's strict re-authentication, through the same `SignedPreview` layer as the other platform
settings. Nothing here reads a connection's credential, a call's arguments or a result: a platform
administrator sees names, hosts, states, counts and failure codes.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from fastapi import APIRouter, Body, Query, Request

from synwevo_core.audit_change import change
from synwevo_core.auth import audit_event
from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy, McpPolicySpec
from synwevo_core.platform_access import require_platform
from synwevo_core.store import new_id, now

from .admin_operations import OPERATIONS, PREVIEW_SECONDS, Operation, SignedPreview, canonical

OPERATIONS.setdefault("admin.mcp.policy.publish", Operation("platform.config.manage", "high"))
OPERATIONS.setdefault("admin.mcp.connection.disable", Operation("platform.config.manage", "high"))


class McpPolicyOperations(SignedPreview):
    action = "admin.mcp.policy.publish"
    target = "global"

    def __init__(self, store, audit):
        self.store, self.audit = store, audit
        self.policy = McpPolicy(store)

    def _actor(self, request, *, execute=False, view=False):
        return require_platform(request, "platform.config.view" if view else OPERATIONS[self.action].permission,
                                store=self.store, audit=self.audit, dangerous=execute)

    @staticmethod
    def _input(body):
        revision = body.get("expected_revision")
        if type(revision) is not int or not 0 <= revision < (1 << 63):
            raise ServiceError(400, "mcp_policy_bad_revision", "策略版本无效，请重新读取当前策略")
        spec = body.get("spec")
        if not isinstance(spec, dict):
            raise ServiceError(400, "mcp_policy_invalid", "请提供完整的 MCP 策略")
        clean = McpPolicy.validate(spec)
        return {"expected_revision": revision, "spec": clean.model_dump()}

    def _current(self, expected):
        state = self.policy.read()
        if state["revision"] != expected:
            raise ServiceError(409, "mcp_policy_revision_conflict", "MCP 策略已变化，请重新预览", {"current": state})
        return state

    def _plan(self, inputs):
        before = self._current(inputs["expected_revision"])
        return {"before": before["spec"], "after": inputs["spec"], **change(before["spec"], inputs["spec"])}

    def preview(self, request, body):
        actor = self._actor(request)
        try:
            inputs = self._input(body)
            with self.store.tx():
                actor = self._actor(request)
                plan = self._plan(inputs)
                expires = time.time() + PREVIEW_SECONDS
                binding = {"v": 1, "actor": actor["id"], "action": self.action, "scope": "platform", "target": self.target,
                           "origin": self._environment(request), "inputs": inputs, "before": plan["before"],
                           "after": plan["after"], "expires_at": expires, "nonce": new_id("plan")}
                token = self._sign(binding)
            return {"action": self.action, "scope": "platform", "risk": OPERATIONS[self.action].risk, **plan,
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
                if any(signed.get(k) != v for k, v in expected.items()):
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                if signed.get("expires_at", 0) <= time.time():
                    raise ServiceError(409, "admin_preview_expired", "操作预览已过期，请重新预览")
                plan = self._plan(inputs)
                if signed.get("before") != plan["before"] or signed.get("after") != plan["after"]:
                    raise ServiceError(409, "admin_preview_invalid", "操作目标已变化，请重新预览")
                operation_id = new_id("ao")
                saved = self.policy.append(inputs["spec"], expected_revision=inputs["expected_revision"], actor_id=actor["id"],
                                           reason=reason, operation_id=operation_id)
                state = self.policy.read()
                result = {"operation_id": operation_id, "status": "succeeded", "revision": saved["revision"], "state": state}
                self.audit(request, self.action, actor_id=actor["id"], object_kind="mcp_policy", object_id=self.target,
                           workspace_given=True, result="ok", meta={"operation_id": operation_id, "reason": reason,
                               **change(plan["before"], plan["after"]), "environment": origin})
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
        self.audit(request, self.action, actor_id=actor["id"], object_kind="mcp_policy", object_id=self.target,
                   workspace_given=True, result="denied" if isinstance(exc, ServiceError) and exc.status < 500 else "error",
                   meta=meta)


def _masked_connection(store, row: dict) -> dict:
    from urllib.parse import urlsplit
    owner_name = None
    if row["owner_kind"] == "workspace":
        ws = store.one("SELECT name FROM workspaces WHERE id=?", (row["owner_id"],))
        owner_name = ws["name"] if ws else None
    counts = store.one("SELECT COUNT(*) AS calls, SUM(CASE WHEN status IN ('failed','timeout') THEN 1 ELSE 0 END) AS failed, "
                       "MAX(started_at) AS last_call_at FROM mcp_calls WHERE connection_id=? AND started_at>?",
                       (row["id"], now() - 7 * 86400))
    tools = store.one("SELECT COUNT(*) AS n FROM mcp_connection_tools WHERE connection_id=? AND removed_at IS NULL", (row["id"],))
    granted = store.one("SELECT COUNT(*) AS n FROM mcp_tool_grants g JOIN mcp_connection_tools t ON t.connection_id=g.connection_id "
                        "AND t.name=g.tool WHERE g.connection_id=? AND g.mode<>'off' AND t.removed_at IS NULL AND g.schema_hash=t.schema_hash",
                        (row["id"],))
    bound = store.one("SELECT COUNT(*) AS n FROM mcp_agent_bindings WHERE connection_id=? AND enabled=1", (row["id"],))
    return {"id": row["id"], "name": row["name"], "owner_kind": row["owner_kind"], "owner_name": owner_name,
            "host": urlsplit(row["url"]).hostname, "auth_kind": row["auth_kind"], "status": row["status"],
            "health": row["health"], "last_ok_at": row["last_ok_at"], "last_error_code": row["last_error_code"],
            "failure_streak": row["failure_streak"], "circuit_open_until": row["open_until"] if (row["open_until"] or 0) > now() else None,
            "protocol_version": row["protocol_version"], "tools": tools["n"], "granted": granted["n"], "agents": bound["n"],
            "calls_7d": counts["calls"] or 0, "failed_7d": counts["failed"] or 0, "last_call_at": counts["last_call_at"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def build_router(store, audit):
    router = APIRouter(prefix="/api/admin/mcp", tags=["admin"])
    operations = McpPolicyOperations(store, audit)
    policy = McpPolicy(store)

    @router.get("/policy")
    def read(request: Request):
        operations._actor(request, view=True)
        return {"state": policy.read(), "default": McpPolicy.default().model_dump(),
                "schema": McpPolicySpec.model_json_schema()}

    @router.get("/policy/history")
    def history(request: Request, limit: int = Query(default=50, ge=1, le=200), before_revision: int | None = Query(default=None, ge=1)):
        operations._actor(request, view=True)
        return {"items": policy.history(before_revision=before_revision, limit=limit)}

    @router.post("/policy/preview")
    def preview(request: Request, body: dict = Body(...)):
        return operations.preview(request, body)

    @router.post("/policy/publish")
    def publish(request: Request, body: dict = Body(...)):
        return operations.publish(request, body)

    @router.get("/overview")
    def overview(request: Request, q: str | None = Query(default=None, max_length=100),
                 health: str | None = Query(default=None, max_length=20), limit: int = Query(default=100, ge=1, le=500)):
        operations._actor(request, view=True)
        rows = store.all("SELECT * FROM mcp_connections WHERE deleted_at IS NULL ORDER BY health='failed' DESC, updated_at DESC LIMIT ?",
                         (limit,))
        items = [_masked_connection(store, r) for r in rows]
        if q:
            needle = q.lower()
            items = [i for i in items if needle in i["name"].lower() or needle in (i["host"] or "").lower()]
        if health:
            items = [i for i in items if i["health"] == health]
        totals = store.one("SELECT COUNT(*) AS connections, SUM(CASE WHEN health='failed' THEN 1 ELSE 0 END) AS failed, "
                           "SUM(CASE WHEN health='auth_required' THEN 1 ELSE 0 END) AS auth_required FROM mcp_connections WHERE deleted_at IS NULL")
        calls = store.one("SELECT COUNT(*) AS n, SUM(CASE WHEN status IN ('failed','timeout') THEN 1 ELSE 0 END) AS failed "
                          "FROM mcp_calls WHERE started_at>?", (now() - 86400,))
        inbound = store.one("SELECT COUNT(*) AS n FROM api_platform_calls WHERE route='/api/suite/v1/mcp' AND at>?",
                            (now() - 86400,))
        return {"items": items, "totals": {"connections": totals["connections"] or 0, "failed": totals["failed"] or 0,
                                           "auth_required": totals["auth_required"] or 0,
                                           "calls_24h": calls["n"] or 0, "failed_calls_24h": calls["failed"] or 0,
                                           "inbound_requests_24h": inbound["n"] or 0},
                "policy": policy.read()}

    @router.get("/connections/{cid}/calls")
    def calls(request: Request, cid: str, limit: int = Query(default=50, ge=1, le=200)):
        operations._actor(request, view=True)
        rows = store.all("SELECT id, origin, kind, name, status, error_code, started_at, finished_at, duration_ms, request_id "
                         "FROM mcp_calls WHERE connection_id=? ORDER BY started_at DESC LIMIT ?", (cid, limit))
        return {"items": rows}

    @router.post("/connections/{cid}/disable")
    def disable(request: Request, cid: str, body: dict = Body(default=None)):
        """Stop a connection platform-wide: its credential is forgotten and its Agent bindings stay but
        yield nothing. A dangerous operation: re-authentication, a written reason, audited."""
        actor = require_platform(request, OPERATIONS["admin.mcp.connection.disable"].permission, store=store, audit=audit,
                                 dangerous=True)
        reason = (body or {}).get("reason") if isinstance(body, dict) else None
        reason = reason.strip() if isinstance(reason, str) else ""
        if not 8 <= len(reason) <= 500:
            raise ServiceError(400, "missing_reason", "请写明理由（8 至 500 个字）")
        with store.tx():
            row = store.one("SELECT * FROM mcp_connections WHERE id=? AND deleted_at IS NULL", (cid,))
            if not row:
                raise ServiceError(404, "mcp_connection_not_found", "连接不存在")
            store.execute("UPDATE mcp_connections SET status='disabled', credential_sealed=NULL, credential_hint=NULL, "
                          "health=CASE WHEN auth_kind='none' THEN health ELSE 'auth_required' END, updated_at=? WHERE id=?",
                          (now(), cid))
            audit_event(store, "admin.mcp.connection.disable", actor_kind="user", actor_id=actor["id"],
                        workspace_id=row["owner_id"] if row["owner_kind"] == "workspace" else None,
                        object_kind="mcp_connection", object_id=cid, meta={"reason": reason, "name": row["name"]},
                        request=request)
        return {"id": cid, "status": "disabled"}

    return router
