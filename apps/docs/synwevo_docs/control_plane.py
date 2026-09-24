"""Injected Docs adapter for the account control plane.

Requests enter the real ASGI machine gateway and original governance handlers, so tenant,
object, scope, throttling, webhook URL validation and delivery policy stay in their owners.
There is no operator principal and no administrator-cookie fallback.
"""
from __future__ import annotations

import json
import re
import asyncio

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


class DocsControlPlane:
    def __init__(self, api):
        self.api = api

    def contract(self) -> dict:
        return openapi_subset(self.api, issuer=issuer())

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
