"""Injected Docs adapter for the account control plane.

Requests enter the real ASGI machine gateway and original governance handlers, so tenant,
object, scope, throttling, webhook URL validation and delivery policy stay in their owners.
There is no operator principal and no administrator-cookie fallback.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse

import httpx

from synwevo_core.errors import ServiceError
from synwevo_core.api_platform import enforce_principal
from synwevo_core.machine import MachineAuth
from synwevo_core.oidc import issuer

from .api.v1 import openapi_subset

IDENTIFIER = r"[A-Za-z0-9_-]{1,100}"
READ_ROUTES = (
    r"/api/v1/(whoami|capabilities|usage|projects|templates|events|credentials/self)",
    rf"/api/v1/projects/{IDENTIFIER}(/(files|mappings|definition))?",
    rf"/api/v1/templates/{IDENTIFIER}(/fields)?",
    rf"/api/v1/jobs/{IDENTIFIER}(/(items|runs|events|artifacts))?",
    rf"/api/v1/files/{IDENTIFIER}",
    rf"/api/v1/webhooks/subscriptions(/{IDENTIFIER}(/deliveries(/{IDENTIFIER})?)?)?",
)
WEBHOOK_ROUTES = {
    "POST": (r"/api/v1/webhooks/subscriptions",
             rf"/api/v1/webhooks/subscriptions/{IDENTIFIER}/(pause|resume|rotate-secret)",
             rf"/api/v1/webhooks/subscriptions/{IDENTIFIER}/deliveries/{IDENTIFIER}/redeliver"),
    "PATCH": (rf"/api/v1/webhooks/subscriptions/{IDENTIFIER}",),
    "DELETE": (rf"/api/v1/webhooks/subscriptions/{IDENTIFIER}",),
}
QUERY_FIELDS = frozenset(("limit", "offset", "cursor", "status", "types", "q", "sort", "order", "archived"))
MAX_RESPONSE_BYTES = 1024 * 1024
DISPATCH_TIMEOUT_SECONDS = 15


def _origin(url: str) -> str:
    """scheme://host[:port] of a receiver URL. Paths and queries are withheld from the platform view:
    a receiver's path can carry its own token, and an administrator does not need it to see health."""
    parts = urllib.parse.urlsplit(url or "")
    return f"{parts.scheme}://{parts.hostname or ''}{f':{parts.port}' if parts.port else ''}" if parts.scheme else ""


class DocsControlPlane:
    def __init__(self, api):
        self.api = api

    def contract(self) -> dict:
        return openapi_subset(self.api, issuer=issuer())

    # ------------------------------------------------------------------ webhook operations (API-6)
    # A platform view of delivery health across workspaces: metadata only — no event payloads, no
    # receiver paths, no signing secrets — and one protective action (pause). Resuming, redelivering
    # and editing stay with the workspace's own credential, as before.
    def webhook_overview(self, *, workspace_id: str = "", status: str = "", failing: bool = False,
                         limit: int = 25, offset: int = 0) -> dict:
        store = self.api.state.app.store
        since = time.time() - 7 * 86400
        where, args = ["s.deleted_at IS NULL"], []
        if workspace_id:
            where.append("s.tenant=?"); args.append(workspace_id)
        if status:
            where.append("s.status=?"); args.append(status)
        if failing:
            where.append("s.consecutive_failures>0")
        sql_where = " AND ".join(where)
        total = store.one(f"SELECT COUNT(*) AS n FROM webhook_subscriptions s WHERE {sql_where}", tuple(args))["n"]
        rows = store.all(
            "SELECT s.id, s.tenant, s.url, s.events, s.status, s.disabled_reason, s.consecutive_failures, s.last_success_at, "
            "s.last_failure_at, s.created_at, s.updated_at, s.version, "
            "(SELECT COUNT(*) FROM webhook_deliveries d WHERE d.subscription_id=s.id AND d.status='pending') AS pending, "
            "(SELECT COUNT(*) FROM webhook_deliveries d WHERE d.subscription_id=s.id AND d.status='failed' AND d.created_at>=?) AS failed_7d, "
            "(SELECT COUNT(*) FROM webhook_deliveries d WHERE d.subscription_id=s.id AND d.status='delivered' AND d.created_at>=?) AS delivered_7d, "
            "(SELECT d.last_error FROM webhook_deliveries d WHERE d.subscription_id=s.id AND d.last_error IS NOT NULL "
            " ORDER BY d.created_at DESC LIMIT 1) AS last_error "
            f"FROM webhook_subscriptions s WHERE {sql_where} ORDER BY s.consecutive_failures DESC, s.updated_at DESC LIMIT ? OFFSET ?",
            (since, since, *args, limit, offset))
        items = []
        for r in rows:
            try:
                events = json.loads(r["events"] or "[]")
            except ValueError:
                events = []
            items.append({**{k: r[k] for k in ("id", "tenant", "status", "disabled_reason", "consecutive_failures",
                                               "last_success_at", "last_failure_at", "created_at", "updated_at", "version",
                                               "pending", "failed_7d", "delivered_7d")},
                          "receiver": _origin(r["url"]), "events": events,
                          "last_error": (r["last_error"] or "")[:200] or None})
        totals = store.one(
            "SELECT (SELECT COUNT(*) FROM webhook_deliveries WHERE status='pending') AS pending, "
            "(SELECT COUNT(*) FROM webhook_subscriptions WHERE deleted_at IS NULL AND consecutive_failures>0) AS failing, "
            "(SELECT COUNT(*) FROM webhook_subscriptions WHERE deleted_at IS NULL AND status='disabled') AS disabled, "
            "(SELECT MIN(next_attempt_at) FROM webhook_deliveries WHERE status='pending') AS next_attempt_at")
        return {"items": items, "total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None,
                "totals": totals, "window_days": 7, "measured_at": time.time()}

    def webhook_pause(self, sid: str) -> dict:
        """Pause one subscription for the platform (a failing or abusive receiver). Reversible by the
        workspace (resume); queued deliveries are dropped exactly as the owner's own pause does."""
        from .webhooks import Subscriptions
        subs = Subscriptions(self.api.state.app.store, getattr(self.api.state, "webhooks", None))
        out = subs.pause(None, sid)
        return {"id": out["id"], "tenant": out.get("tenant"), "status": out["status"]}

    def webhook_tenant(self, sid: str) -> str | None:
        row = self.api.state.app.store.one("SELECT tenant FROM webhook_subscriptions WHERE id=? AND deleted_at IS NULL", (sid,))
        return row["tenant"] if row else None

    async def request(self, credential: str, method: str, path: str,
                      query: dict, body: dict | None) -> dict:
        if not isinstance(credential, str) or not 10 <= len(credential) <= 4096 or any(
                c.isspace() for c in credential):
            raise ServiceError(400, "credential_required", "请输入独立的服务访问令牌或访问密钥")
        if not isinstance(path, str) or not isinstance(method, str):
            raise ServiceError(400, "request_not_allowed", "请选择允许的机器接口")
        patterns = READ_ROUTES if method == "GET" else WEBHOOK_ROUTES.get(method, ())
        if not any(re.fullmatch(pattern, path) for pattern in patterns):
            raise ServiceError(400, "request_not_allowed", "该接口或方法不在调试允许范围内")
        if not isinstance(query, dict) or set(query) - QUERY_FIELDS or len(query) > 8 or any(
                not isinstance(v, (str, int)) or len(str(v)) > 500 for v in query.values()):
            raise ServiceError(400, "invalid_query", "查询参数无效")
        if method == "GET" and body:
            raise ServiceError(400, "invalid_body", "只读调试不接受请求体")
        if body is not None:
            try:
                valid_body = isinstance(body, dict) and len(json.dumps(body, allow_nan=False)) <= 16384
            except (TypeError, ValueError):
                valid_body = False
            if not valid_body:
                raise ServiceError(400, "invalid_body", "请求体过大或格式无效")
        machine = MachineAuth(self.api.state.app.store, self.api.state.identity, self.api.state.auth)
        principal = enforce_principal(self.api.state.app.store, machine.verify(f"Bearer {credential}"))
        if principal.get("kind") not in ("api_key", "service_account"):
            raise ServiceError(403, "service_credential_required", "调试需要独立的服务凭据")
        if "limit" in query:
            try:
                if not 1 <= int(query["limit"]) <= 100:
                    raise ValueError
            except (TypeError, ValueError):
                raise ServiceError(400, "invalid_query", "每页最多 100 条记录") from None
        # ASGITransport never opens a socket; base_url is an internal scope value only.
        # Each call constructs an empty cookie jar and sends ONLY the explicit bearer.
        transport = httpx.ASGITransport(app=self.api, raise_app_exceptions=False)
        try:
            # httpx transport timeouts do not put a deadline on an in-process ASGI app.
            # Do not hold a Store transaction across this await.
            async with asyncio.timeout(DISPATCH_TIMEOUT_SECONDS):
                async with httpx.AsyncClient(transport=transport, base_url="http://control-plane.internal",
                                            follow_redirects=False, timeout=15) as client:
                    async with client.stream(method, path, params=query, json=body,
                                             headers={"Authorization": f"Bearer {credential}"}) as response:
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE_BYTES:
                                raise ServiceError(413, "debug_response_too_large", "响应过大，请缩小查询范围")
                            chunks.append(chunk)
                        try:
                            data = json.loads(b"".join(chunks))
                        except (ValueError, UnicodeError):
                            raise ServiceError(502, "invalid_api_response", "机器接口没有返回 JSON 响应") from None
                        # Recheck after the real handler completed: revocation or narrowing while
                        # awaiting it must prevent delivery of data obtained under old authority.
                        current = machine.verify(f"Bearer {credential}")
                        if (current.get("tenant") != principal.get("tenant") or
                                current.get("kind") != principal.get("kind") or
                                not set(principal.get("scopes") or ()) <= set(current.get("scopes") or ())):
                            raise ServiceError(403, "credential_authority_changed", "服务凭据权限已改变，请重新请求")
                        return {"status": response.status_code, "data": data,
                                "request_id": response.headers.get("X-Request-ID"),
                                "headers": {k: v for k, v in response.headers.items()
                                            if k.lower() in ("etag", "retry-after", "x-request-id")}}
        except TimeoutError:
            raise ServiceError(504, "debug_request_timeout", "机器接口请求超时，请稍后重试") from None
