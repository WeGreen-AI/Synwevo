"""Talking to external MCP servers: one event loop for the process, pinned connections, bounded calls.

The Agent's turn engine runs on worker threads; the official MCP SDK is asyncio. This module owns
the one asyncio loop (a daemon thread) every outbound exchange runs on, and hands the thread-side
callers plain results or `ServiceError`s with stable codes. Nothing here decides *whether* a call
may happen — that is the connection service's (grants, bindings, policy flags); this is *how*:

* **Where the request goes is checked and then pinned.** The destination is checked by the platform
  MCP policy (blocked/allowed hosts) and resolved by the shared resolver (`webhook_delivery.
  resolve_checked`: public addresses only, unless the operator allowed a private network), and the
  connection is made to the address that was checked — the hostname is kept for TLS (SNI and the
  certificate), so a name that answers differently a moment later cannot redirect the request.
  Redirects are not followed; no ambient proxy is consulted.
* **Every call is bounded**: a deadline from the policy, a per-connection and per-user concurrency
  cap, result sizes cut (and said to be cut) at the service layer.
* **Cancellation propagates**: when the person stops the Agent's answer, the awaiting coroutine
  is cancelled, which closes the request's response stream — the transport's cancellation signal.
* **Credentials pass through once**: the bearer token for the server goes into the HTTP client's
  default headers for this exchange and nowhere else; never into logs, previews or results.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT, Implementation

from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy, McpPolicySpec
from synwevo_core.webhook_delivery import UrlRejected, UrlUnresolvable, check_address_text, resolve_checked

log = logging.getLogger("synwevo.mcp.client")

CLIENT_INFO = Implementation(name="Synwevo Agent", version="1")
MAX_LISTED = 500
CONNECT_TIMEOUT_S = 10.0


class McpCancelled(Exception):
    pass


@dataclass
class Discovery:
    protocol_version: str | None
    server_info: dict | None
    capabilities: dict
    instructions: str | None
    tools: list[dict] = field(default_factory=list)
    resources: list[dict] = field(default_factory=list)
    prompts: list[dict] = field(default_factory=list)


@dataclass
class ToolOutcome:
    is_error: bool
    content: list[dict]
    structured: Any
    text: str
    duration_ms: int


@dataclass
class ResourceOutcome:
    contents: list[dict]
    text: str | None
    bytes_total: int
    duration_ms: int


@dataclass
class PromptOutcome:
    description: str | None
    messages: list[dict]
    text: str
    duration_ms: int


class PinnedTransport(httpx2.AsyncBaseTransport):
    """Connect to the checked address, speak to the named host.

    Every request's URL host is replaced by the pinned address and the original name is carried
    as the `Host` header and the TLS server name, so certificate validation is unchanged while
    no second DNS lookup can send the request anywhere else.
    """

    def __init__(self, host: str, address: str, *, timeout: float):
        self.host = host
        self.address = address
        self._inner = httpx2.AsyncHTTPTransport(retries=0, trust_env=False)
        self.timeout = timeout

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        url = request.url
        if (url.host or "").lower() != self.host.lower():
            # Only the connection's own origin is ever contacted: not a redirect target, not a
            # resource link the server handed back.
            raise httpx2.ConnectError(f"refused: {url.host} is not the connection's host", request=request)
        pinned = url.copy_with(host=self.address if ":" not in self.address else f"[{self.address}]")
        request.url = pinned
        request.headers["host"] = url.netloc.decode("ascii") if isinstance(url.netloc, bytes) else str(url.netloc)
        request.extensions["sni_hostname"] = self.host
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _error(code: str, message: str, *, retryable: bool = False, status: int = 502, detail: dict | None = None) -> ServiceError:
    d = {"retryable": retryable}
    if detail:
        d.update(detail)
    return ServiceError(status, code, message, d)


def classify(exc: BaseException) -> ServiceError:
    """One stable code per kind of failure, for the record and for the person; never the server's
    own words, which may carry anything."""
    if isinstance(exc, ServiceError):
        return exc
    if isinstance(exc, McpCancelled) or isinstance(exc, asyncio.CancelledError):
        return _error("mcp_cancelled", "调用已取消", status=409)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx2.TimeoutException)):
        return _error("mcp_timeout", "MCP 服务没有在限定时间内响应", retryable=True, status=504)
    if isinstance(exc, httpx2.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return _error("mcp_auth_required", "MCP 服务拒绝了当前凭据，请重新认证", status=401,
                          detail={"http_status": code, "www_authenticate": exc.response.headers.get("www-authenticate")})
        if code == 404:
            return _error("mcp_not_found", "该地址上没有 MCP 服务", status=502, detail={"http_status": code})
        if code == 429:
            return _error("mcp_rate_limited", "MCP 服务暂时限制了调用频率", retryable=True, status=503,
                          detail={"http_status": code})
        return _error("mcp_server_error", "MCP 服务返回了错误", retryable=code >= 500, detail={"http_status": code})
    if isinstance(exc, (httpx2.ConnectError, httpx2.NetworkError, ConnectionError, OSError)):
        return _error("mcp_unreachable", "无法连接 MCP 服务", retryable=True, status=502)
    if isinstance(exc, MCPError):
        if getattr(exc, "code", None) == REQUEST_TIMEOUT:
            # The SDK's own per-request deadline: the server did not answer in time.
            return _error("mcp_timeout", "MCP 服务没有在限定时间内响应", retryable=True, status=504)
        return _error("mcp_protocol_error", "MCP 服务返回了协议错误", status=502,
                      detail={"jsonrpc_code": getattr(exc, "code", None)})
    if isinstance(exc, ExceptionGroup):
        inner = [classify(e) for e in exc.exceptions]
        for candidate in inner:
            if candidate.code != "mcp_internal":
                return candidate
        return inner[0] if inner else _error("mcp_internal", "MCP 调用失败", status=502)
    log.warning("mcp exchange failed: %s", type(exc).__name__)
    return _error("mcp_internal", "MCP 调用失败", status=502)


def _dump(model) -> dict:
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)


def content_text(blocks: list[dict]) -> str:
    """What a model may read of a result's content: text as is, other kinds named, never bytes."""
    parts = []
    for b in blocks:
        kind = b.get("type")
        if kind == "text":
            parts.append(str(b.get("text", "")))
        elif kind == "image":
            parts.append(f"[image {b.get('mimeType', '')} omitted]")
        elif kind == "audio":
            parts.append(f"[audio {b.get('mimeType', '')} omitted]")
        elif kind == "resource_link":
            parts.append(f"[resource link {b.get('uri', '')}]")
        elif kind == "resource":
            res = b.get("resource") or {}
            parts.append(res.get("text") if isinstance(res.get("text"), str) else f"[binary resource {res.get('uri', '')} omitted]")
        else:
            parts.append(f"[{kind or 'unknown'} content omitted]")
    return "\n".join(parts)


class McpRuntime:
    """The process's outbound MCP runtime. `transport_for(url)` is a test seam: a FIXTURE transport
    (an ASGI application in process, a loopback server) in place of the pinned network transport."""

    #: TEST SEAM (FIXTURE): a transport factory that stands in for the pinned network transport in
    #: tests (an ASGI application in process). Never set by the product.
    TEST_TRANSPORT_FOR: Callable[[str], httpx2.AsyncBaseTransport] | None = None

    def __init__(self, store, *, transport_for: Callable[[str], httpx2.AsyncBaseTransport] | None = None):
        self.store = store
        self.policy = McpPolicy(store)
        self._transport_for = transport_for or McpRuntime.TEST_TRANSPORT_FOR
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._conn_slots: dict[str, asyncio.Semaphore] = {}
        self._user_slots: dict[str, asyncio.Semaphore] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "McpRuntime":
        with self._lock:
            if self._thread is not None:
                return self
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._run_loop, name="synwevo-mcp-client", daemon=True)
            self._thread.start()
        self._started.wait(5)
        return self

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(self._started.set)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._thread = None
        if loop is not None and thread is not None:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)

    def _submit(self, coro, *, timeout: float, should_stop: Callable[[], bool] | None = None):
        """Run `coro` on the runtime loop from a worker thread, bounded by `timeout` and stopped
        early when `should_stop` says so (cancelling the coroutine closes the response stream)."""
        if self._thread is None:
            self.start()
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        deadline = time.monotonic() + timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    fut.cancel()
                    raise _error("mcp_timeout", "MCP 服务没有在限定时间内响应", retryable=True, status=504)
                try:
                    return fut.result(timeout=min(0.5, remaining))
                except TimeoutError:
                    if should_stop is not None and should_stop():
                        fut.cancel()
                        raise McpCancelled()
        except BaseException as exc:  # noqa: BLE001 — every failure becomes a classified refusal
            raise classify(exc) from None

    # ------------------------------------------------------------------ connections
    def _destination(self, url: str, owner: str, spec: McpPolicySpec) -> tuple[str, str, int]:
        try:
            host, port = check_address_text(url, allow_http=spec.allow_http, http_refusal="MCP 服务地址默认要求 https")
        except UrlRejected as e:
            raise ServiceError(400, "mcp_url_invalid", f"MCP 服务地址不可用：{e}", {"field": "url"}) from None
        refusal = self.policy.destination_refusal(host)
        if refusal:
            raise ServiceError(403, refusal, "平台策略不允许连接这个地址", {"host": host})
        if self._transport_for is not None:
            return host, "fixture", port
        try:
            addresses = resolve_checked(url, owner=owner, allow_http=spec.allow_http,
                                        allow_private=spec.allow_private_networks, timeout=CONNECT_TIMEOUT_S,
                                        http_refusal="MCP 服务地址默认要求 https")
        except UrlUnresolvable as e:
            raise ServiceError(502, "mcp_unresolvable", "无法解析 MCP 服务地址", {"host": host, "retryable": True}) from None
        except UrlRejected as e:
            raise ServiceError(403, "mcp_host_not_allowed", "MCP 服务地址不被允许（内网地址需由管理员配置允许列表）",
                               {"host": host}) from None
        return host, addresses[0], port

    def _http(self, url: str, owner: str, credential: str | None, *, read_timeout: float) -> httpx2.AsyncClient:
        spec = self.policy.current()
        host, address, port = self._destination(url, owner, spec)
        if self._transport_for is not None:
            transport = self._transport_for(url)
        else:
            transport = PinnedTransport(host, address, timeout=read_timeout)
        headers = {"User-Agent": "Synwevo-Agent/1 (+mcp)"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return httpx2.AsyncClient(transport=transport, headers=headers, follow_redirects=False, trust_env=False,
                                  timeout=httpx2.Timeout(CONNECT_TIMEOUT_S, read=read_timeout))

    def http(self, url: str, owner: str, credential: str | None = None, *, read_timeout: float = 30.0) -> httpx2.AsyncClient:
        """A pinned HTTP client for `url` (any URL the policy allows: an MCP server, its metadata, its
        authorization server) — the one way out of this process for the OAuth exchanges too."""
        return self._http(url, owner, credential, read_timeout=read_timeout)

    def run(self, coro, *, timeout: float):
        return self._submit(coro, timeout=timeout)

    def _slots(self, connection_id: str, user_id: str | None, spec: McpPolicySpec):
        conn = self._conn_slots.get(connection_id)
        if conn is None:
            conn = self._conn_slots[connection_id] = asyncio.Semaphore(spec.max_concurrent_calls_per_connection)
        user = None
        if user_id:
            user = self._user_slots.get(user_id)
            if user is None:
                user = self._user_slots[user_id] = asyncio.Semaphore(spec.max_concurrent_calls_per_user)
        return conn, user

    async def _with_client(self, conn: dict, credential: str | None, user_id: str | None, timeout: float, fn):
        spec = self.policy.current()
        conn_slot, user_slot = self._slots(conn["id"], user_id, spec)
        if conn_slot.locked():  # refuse rather than queue behind the cap
            raise ServiceError(429, "mcp_connection_busy", "这个连接的并发调用已达上限，请稍后再试",
                               {"retry_after": 2, "retryable": True})
        if user_slot is not None and user_slot.locked():
            raise ServiceError(429, "mcp_user_busy", "你的 MCP 并发调用已达上限，请稍后再试",
                               {"retry_after": 2, "retryable": True})
        async with conn_slot:
            if user_slot is not None:
                await user_slot.acquire()
            try:
                async with self._http(conn["url"], conn["owner_id"], credential, read_timeout=timeout) as http:
                    async with Client(streamable_http_client(conn["url"], http_client=http), client_info=CLIENT_INFO,
                                      read_timeout_seconds=timeout) as client:
                        return await fn(client)
            finally:
                if user_slot is not None:
                    user_slot.release()

    # ------------------------------------------------------------------ exchanges
    def discover(self, conn: dict, credential: str | None, *, user_id: str | None, timeout: float) -> Discovery:
        async def go(client: Client) -> Discovery:
            caps = _dump(client.server_capabilities) if client.server_capabilities else {}
            out = Discovery(protocol_version=client.protocol_version,
                            server_info=_dump(client.server_info) if client.server_info else None,
                            capabilities=caps, instructions=client.instructions)
            if caps.get("tools") is not None:
                out.tools = await _page(client.list_tools, "tools")
            if caps.get("resources") is not None:
                out.resources = await _page(client.list_resources, "resources")
            if caps.get("prompts") is not None:
                out.prompts = await _page(client.list_prompts, "prompts")
            return out
        return self._submit(self._with_client(conn, credential, user_id, timeout, go), timeout=timeout + 5)

    def call_tool(self, conn: dict, credential: str | None, name: str, arguments: dict, *, user_id: str | None,
                  timeout: float, should_stop: Callable[[], bool] | None = None) -> ToolOutcome:
        async def go(client: Client) -> ToolOutcome:
            started = time.perf_counter()
            result = await client.call_tool(name, arguments, read_timeout_seconds=timeout)
            blocks = [_dump(b) for b in (result.content or [])]
            structured = result.structured_content
            return ToolOutcome(is_error=bool(result.is_error), content=blocks, structured=structured,
                               text=content_text(blocks), duration_ms=int((time.perf_counter() - started) * 1000))
        return self._submit(self._with_client(conn, credential, user_id, timeout, go), timeout=timeout + 5,
                            should_stop=should_stop)

    def read_resource(self, conn: dict, credential: str | None, uri: str, *, user_id: str | None, timeout: float,
                      max_bytes: int) -> ResourceOutcome:
        async def go(client: Client) -> ResourceOutcome:
            started = time.perf_counter()
            result = await client.read_resource(uri)
            contents, total, texts = [], 0, []
            for item in result.contents or []:
                d = _dump(item)
                if "text" in d and isinstance(d["text"], str):
                    total += len(d["text"].encode("utf-8"))
                    texts.append(d["text"])
                elif "blob" in d:
                    total += len(d["blob"]) * 3 // 4
                    d["blob"] = None
                    d["blob_omitted"] = True
                if total > max_bytes:
                    raise ServiceError(413, "mcp_resource_too_large", "资源超过允许读取的大小", {"max_bytes": max_bytes})
                contents.append(d)
            return ResourceOutcome(contents=contents, text="\n".join(texts) if texts else None, bytes_total=total,
                                   duration_ms=int((time.perf_counter() - started) * 1000))
        return self._submit(self._with_client(conn, credential, user_id, timeout, go), timeout=timeout + 5)

    def get_prompt(self, conn: dict, credential: str | None, name: str, arguments: dict[str, str], *,
                   user_id: str | None, timeout: float) -> PromptOutcome:
        async def go(client: Client) -> PromptOutcome:
            started = time.perf_counter()
            result = await client.get_prompt(name, arguments or None)
            messages = [_dump(m) for m in (result.messages or [])]
            text = "\n\n".join(f"[{m.get('role', 'user')}] " + content_text([m.get("content")]) if isinstance(m.get("content"), dict)
                               else f"[{m.get('role', 'user')}] " + content_text(m.get("content") or []) for m in messages)
            return PromptOutcome(description=result.description, messages=messages, text=text,
                                 duration_ms=int((time.perf_counter() - started) * 1000))
        return self._submit(self._with_client(conn, credential, user_id, timeout, go), timeout=timeout + 5)


async def _page(method, attr: str) -> list[dict]:
    items, cursor = [], None
    while True:
        result = await method(cursor=cursor)
        items.extend(_dump(x) for x in getattr(result, attr) or [])
        cursor = result.next_cursor
        if not cursor or len(items) >= MAX_LISTED:
            break
    return items[:MAX_LISTED]
