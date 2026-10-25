"""Talking to external MCP servers: one event loop for the process, pinned connections, bounded calls.

The Agent's turn engine runs on worker threads; the official MCP SDK is asyncio. This module owns
the one asyncio loop (a daemon thread) every outbound exchange runs on, and hands the thread-side
callers plain results or `ServiceError`s with stable codes. Nothing here decides *whether* a call
may happen — that is the connection service's (grants, bindings, policy flags); this is *how*:

* **Where the request goes is checked and then pinned.** The destination is checked by the platform
  MCP policy (blocked/allowed hosts) and resolved by the shared resolver (`webhook_delivery.
  resolve_checked`: public addresses only, unless the operator allowed a private network); this
  deployment's own origins are refused — by name, and by the loopback address and port a name
  resolves to — unless the operator listed that host explicitly. The connection is made to the
  address that was checked — the hostname is kept for TLS (SNI and the
  certificate), so a name that answers differently a moment later cannot redirect the request.
  Redirects are not followed; no ambient proxy is consulted.
* **Nothing that blocks runs on the loop.** The policy (the database) is read on the caller's
  thread before an exchange is submitted; checking and resolving the destination (DNS answers as
  slowly as the name's server likes) happen on the runtime's worker threads (`asyncio.to_thread`).
  The loop only moves bytes, so one slow name cannot hold up anyone else's calls, deadlines or
  cancellations.
* **Every call is bounded**: a deadline from the policy; per-connection and per-user concurrency
  caps read from the policy at each admission; and the response body one exchange may receive,
  counted as it arrives (after any content coding is undone), so an answer past its bound is cut
  off rather than read into memory. Result sizes are cut (and said to be cut) at the service layer;
  a discovery's lists are followed to their end or to `MAX_LISTED` entries, and a cut is said too.
* **What the HTTP layer said is kept.** The SDK turns a non-2xx answer into a JSON-RPC error of its
  own making; the transport records how each JSON-RPC request was answered, so a 401 is
  `mcp_auth_required` and a 429 `mcp_rate_limited`, not a protocol error.
* **Cancellation propagates**: when the person stops the Agent's answer, the awaiting coroutine
  is cancelled, which closes the request's response stream — the transport's cancellation signal.
* **Credentials pass through once**: the bearer token for the server goes into the HTTP client's
  default headers for this exchange and nowhere else; never into logs, previews or results.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import REQUEST_TIMEOUT, Implementation

from synwevo_core import env
from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy, McpPolicySpec
from synwevo_core.webhook_delivery import DNS_SLOTS, UrlRejected, UrlUnresolvable, check_address_text, resolve_checked

log = logging.getLogger("synwevo.mcp.client")

CLIENT_INFO = Implementation(name="Synwevo Agent", version="1")
#: Entries of one list (tools, resources, prompts) a discovery keeps; a longer list is recorded as
#: cut (`Discovery.truncated`), never shortened silently.
MAX_LISTED = 500
#: Pages in a row that list nothing before a list that keeps handing out cursors is refused: it is
#: not a list that ends, and following it until the deadline would only fail later.
MAX_EMPTY_PAGES = 20
CONNECT_TIMEOUT_S = 10.0
#: Response body one exchange may receive (bytes, after any content coding is undone): a tool call,
#: a prompt, with its handshake. What enters the model is cut far lower (`max_result_chars`); this is
#: what the process holds before cutting.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
#: The same for a discovery: the handshake and every page of every list.
MAX_DISCOVERY_BYTES = 16 * 1024 * 1024
#: The same for one metadata document, registration or token response (`McpRuntime.http`).
MAX_DOCUMENT_BYTES = 1024 * 1024
#: Worker threads for the blocking half of opening an exchange (the destination check, the DNS
#: lookup): as many as the process's resolver slots — more would only wait for one.
PREPARE_THREADS = DNS_SLOTS
#: Settings that name this deployment's public addresses (`SYNWEVO_<name>`): this process's own
#: origin (the workplace, where the Synwevo MCP server answers), its public address, the account
#: service, the portal, the site, and an explicitly configured MCP resource.
OWN_ORIGIN_SETTINGS = ("APP_URL", "PUBLIC_URL", "ISSUER", "PORTAL_URL", "SITE_URL", "MCP_RESOURCE")
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_BEARER_ERRORS = ("invalid_request", "invalid_token", "insufficient_scope")


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
    #: The lists ("tools", "resources", "prompts") that went on past `MAX_LISTED` entries: what is
    #: listed of them is their first `MAX_LISTED`, and an entry missing from them may still exist.
    truncated: list[str] = field(default_factory=list)


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


# ---------------------------------------------------------------------- this deployment's own origins
def _url_origin(url) -> tuple[str, int | None] | None:
    """A URL's host (lower-cased, brackets and a trailing dot removed) and port, the scheme's default
    port applied; None for text that names no host. A port that cannot be read is None: any port."""
    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
    except ValueError:
        return None
    if not host or not host.rstrip("."):
        return None
    try:
        port = parts.port or _DEFAULT_PORTS.get(parts.scheme.lower())
    except ValueError:
        port = None
    return host.rstrip("."), port


def _target(target: str) -> tuple[str, int | None] | None:
    """What a caller asks about: a URL's origin, or a bare host name with no port known (None)."""
    text = (target or "").strip()
    if "://" in text:
        return _url_origin(text)
    host = text.lower().strip("[]").rstrip(".")
    return (host, None) if host else None


def _loopback(host: str) -> bool:
    """Whether `host` names this machine: a loopback name, or an address that reaches it (127.0.0.0/8,
    `::1`, the unspecified addresses, and their IPv4-mapped forms)."""
    if host in _LOOPBACK or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _listening_origin() -> tuple[str, int] | None:
    """The loopback port this process itself listens on (`SYNWEVO_PORT` on a loopback or wildcard
    `SYNWEVO_HOST`, infra/server/synwevo_server/run.py), when it is configured: a gateway or a local
    proxy may give the workplace origin another port, and both reach this process."""
    raw = (env.get("PORT") or "").strip()
    bind = (env.get("HOST") or "127.0.0.1").strip().strip("[]").lower()
    if not raw.isdigit() or not 0 < int(raw) < 65536 or not _loopback(bind):
        return None
    return "127.0.0.1", int(raw)


def own_origins(*, store=None) -> set[tuple[str, int | None]]:
    """This deployment's configured public origins as (host, port), the loopback port this process
    listens on, and — given the store — every registered application's address (Docs, Message:
    origins this process's settings do not name)."""
    urls = [env.get(key) for key in OWN_ORIGIN_SETTINGS]
    if store is not None:
        urls += [r["launch_url"] for r in store.all("SELECT launch_url FROM apps")]
    origins = {o for o in (_url_origin(u) for u in urls if u) if o}
    listening = _listening_origin()
    return origins | {listening} if listening else origins


def _own_loopback_port(port: int | None, *, store=None) -> bool:
    """Whether this machine's `port` (None: some port) is one of this deployment's. Every loopback
    name is the same machine, so only the port tells this deployment from another local server.
    With no workplace origin configured, the port this process answers on is not known, and the
    Synwevo MCP server answers every loopback name then: every port counts."""
    if _url_origin(env.get("APP_URL")) is None:
        return True
    return any(_loopback(own_host) and (port is None or own_port is None or port == own_port)
               for own_host, own_port in own_origins(store=store))


def is_own_origin(target: str, *, store=None) -> bool:
    """Whether `target` — a URL, or a bare host name — is this deployment's own: connecting the Agent
    there would hand workspace content back to it through a tool result, past the Agent processing
    and data-flow policies the native operations apply.

    A name that is not a loopback name is this deployment's on every port and scheme: what answers
    there is its gateway's to decide, and the Synwevo MCP server takes `Host: <its host>:*`
    (`mcp_server.asgi.transport_security`). Loopback names are one machine that every local server
    shares, so there the port decides: `localhost`, `127.0.0.1`, `[::1]`, `*.localhost` and the other
    loopback addresses are the same origin on the same port, and another port is another server.
    The scheme is not compared beyond its default port: one port is one listener, whatever scheme a
    URL asks of it. A bare host name, whose port is not known, is compared on every port."""
    asked = _target(target)
    if asked is None:
        return False
    host, port = asked
    if _loopback(host):
        return _own_loopback_port(port, store=store)
    return any(not _loopback(own_host) and host == own_host for own_host, _ in own_origins(store=store))


def is_own_address(address: str, port: int, *, store=None) -> bool:
    """Whether a connection to `address:port` (what a name resolved to) reaches this deployment on
    this machine: any name that resolves to a loopback address — `/etc/hosts`, `127.1`, a public
    name pointed at 127.0.0.1 — on one of this deployment's loopback ports."""
    return _loopback(address.lower().strip("[]")) and _own_loopback_port(port, store=store)


def listed_explicitly(target: str, spec: McpPolicySpec) -> bool:
    """Whether the policy's `allowed_hosts` names the host of `target` (a URL or a bare host name)
    itself: an exact entry, not a suffix rule. Connecting the Agent back to this deployment is an
    operator's deliberate decision (a private deployment trying itself out), never a side effect of
    allowing `.example.com`."""
    asked = _target(target)
    if asked is None:
        return False
    return any(not e.startswith(".") and e.lower().rstrip(".") == asked[0] for e in (spec.allowed_hosts or []))


# ---------------------------------------------------------------------- refusals
def _error(code: str, message: str, *, retryable: bool = False, status: int = 502, detail: dict | None = None) -> ServiceError:
    d = {"retryable": retryable}
    if detail:
        d.update(detail)
    return ServiceError(status, code, message, d)


def _is_self(host: str) -> ServiceError:
    return ServiceError(403, "mcp_destination_is_self",
                        "不能把 Agent 连接到本平台自己的地址（确有需要时由平台管理员在 MCP 策略的允许主机中明确列出）",
                        {"host": host})


def _challenge(headers) -> dict | None:
    """What a `WWW-Authenticate` challenge says, in fixed words only: its scheme, its RFC 6750 error
    code, and whether it points at protected resource metadata (RFC 9728). Never the header text,
    which is the server's and may carry anything."""
    raw = (headers.get("www-authenticate") if headers is not None else None) or ""
    raw = raw.strip()[:4096]
    if not raw:
        return None
    scheme = raw.split(None, 1)[0].lower()
    out = {"scheme": scheme if scheme in ("bearer", "dpop", "basic") else "other",
           "resource_metadata": bool(re.search(r"\bresource_metadata\s*=", raw, re.I))}
    m = re.search(r'\berror\s*=\s*"?([A-Za-z_]+)', raw)
    if m and m.group(1).lower() in _BEARER_ERRORS:
        out["error"] = m.group(1).lower()
    return out


def _retry_after(headers) -> int | None:
    raw = ((headers.get("retry-after") if headers is not None else None) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) <= 3600 else None


def status_refusal(status: int, headers=None, *, jsonrpc_code: int | None = None) -> ServiceError:
    """The refusal for an HTTP status the server answered with (docs/platform/mcp.md §4.4)."""
    detail: dict = {"http_status": status}
    if status in (401, 403):
        detail["www_authenticate"] = _challenge(headers)
        return _error("mcp_auth_required", "MCP 服务拒绝了当前凭据，请重新认证", status=401, detail=detail)
    if status == 429:
        after = _retry_after(headers)
        if after is not None:
            detail["retry_after"] = after
        return _error("mcp_rate_limited", "MCP 服务暂时限制了调用频率", retryable=True, status=503, detail=detail)
    if jsonrpc_code is not None and 400 <= status < 500:
        # A spec-correct server answers a JSON-RPC error with a matching status (400 for invalid
        # params, 404 for an unknown method): the protocol said no, not the address.
        return _error("mcp_protocol_error", "MCP 服务返回了协议错误", detail={**detail, "jsonrpc_code": jsonrpc_code})
    if status == 404:
        return _error("mcp_not_found", "该地址上没有 MCP 服务", detail=detail)
    return _error("mcp_server_error", "MCP 服务返回了错误", retryable=status >= 500, detail=detail)


def classify(exc: BaseException) -> ServiceError:
    """One stable code per kind of failure, for the record and for the person; never the server's
    own words, which may carry anything. The HTTP status of an MCP request is not visible here —
    the SDK has already turned it into a JSON-RPC error — so `_Exchange.verdict` applies what the
    transport recorded on top of this."""
    if isinstance(exc, ServiceError):
        return exc
    if isinstance(exc, McpCancelled) or isinstance(exc, asyncio.CancelledError):
        return _error("mcp_cancelled", "调用已取消", status=409)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx2.TimeoutException)):
        return _error("mcp_timeout", "MCP 服务没有在限定时间内响应", retryable=True, status=504)
    if isinstance(exc, httpx2.HTTPStatusError):
        return status_refusal(exc.response.status_code, exc.response.headers)
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


# ---------------------------------------------------------------------- the wire
class _Exchange:
    """What one exchange's HTTP traffic showed, kept where it is still known: the response body
    received against the exchange's bound, and how the server answered the last JSON-RPC request.

    The SDK makes a JSON-RPC error of its own out of a non-2xx answer, and a closed connection out
    of a stream that was cut off; the exchange's failure is decided from what is recorded here.
    Touched only on the runtime loop."""

    def __init__(self, limit: int, refusal: Callable[[], ServiceError] | None = None):
        self.limit = limit
        self._refusal = refusal
        self.received = 0
        self.over = False
        self.status: int | None = None
        self.headers: httpx2.Headers | None = None
        self.jsonrpc_code: int | None = None

    def refusal(self) -> ServiceError:
        if self._refusal is not None:
            return self._refusal()
        return ServiceError(413, "mcp_response_too_large", "MCP 服务的响应超过允许的大小",
                            {"max_bytes": self.limit, "retryable": False})

    def room(self) -> int:
        return max(0, self.limit - self.received)

    def take(self, n: int) -> None:
        self.received += n
        if self.received > self.limit:
            self.over = True
            raise self.refusal()

    def answered(self, response: httpx2.Response) -> None:
        self.status, self.headers, self.jsonrpc_code = response.status_code, response.headers, None

    def verdict(self, exc: BaseException) -> ServiceError:
        if self.over:
            return self.refusal()
        err = classify(exc)
        if err.code in ("mcp_protocol_error", "mcp_internal") and self.status is not None and not 200 <= self.status < 300:
            return status_refusal(self.status, self.headers, jsonrpc_code=self.jsonrpc_code)
        return err


class _BoundedStream(httpx2.AsyncByteStream):
    """A response body counted against its exchange as it arrives. A body the server compressed
    anyway is inflated here, a bounded piece at a time, so what is counted is what would be held."""

    def __init__(self, inner: httpx2.AsyncByteStream, exchange: _Exchange, *, inflate: bool):
        self._inner = inner
        self._exchange = exchange
        self._z = zlib.decompressobj(32 + zlib.MAX_WBITS) if inflate else None   # gzip or zlib header

    async def __aiter__(self):
        async for chunk in self._inner:
            if self._z is None:
                self._exchange.take(len(chunk))
                yield chunk
                continue
            data = chunk
            while data:
                if self._z.eof:
                    # Bytes after the end of the compressed body would pile up uncounted (`unused_data`).
                    raise _error("mcp_protocol_error", "MCP 服务返回了无法解码的内容")
                try:
                    out = self._z.decompress(data, self._exchange.room() + 1)
                except zlib.error:
                    raise _error("mcp_protocol_error", "MCP 服务返回了无法解码的内容") from None
                self._exchange.take(len(out))
                if out:
                    yield out
                data = self._z.unconsumed_tail
        if self._z is not None:
            out = self._z.flush()
            self._exchange.take(len(out))
            if out:
                yield out

    async def aclose(self) -> None:
        await self._inner.aclose()


def _rpc_request(request: httpx2.Request) -> bool:
    """Whether `request` carries a JSON-RPC request (an `id` and a `method`); how it is answered is
    what an MCP failure is classified by. Notifications and responses are not."""
    if request.method != "POST":
        return False
    try:
        message = json.loads(request.content)
    except (ValueError, TypeError, httpx2.RequestNotRead):
        return False
    return isinstance(message, dict) and "id" in message and "method" in message


def _jsonrpc_error_code(body: bytes) -> int | None:
    try:
        message = json.loads(body)
    except ValueError:
        return None
    error = message.get("error") if isinstance(message, dict) and message.get("jsonrpc") == "2.0" else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if type(code) is int else None


class BoundedTransport(httpx2.AsyncBaseTransport):
    """Every response through it counted against its exchange, and every JSON-RPC request's answer
    recorded on it. Asks for no content coding; gzip/deflate sent anyway is undone under the same
    bound, any other coding is refused."""

    def __init__(self, inner: httpx2.AsyncBaseTransport, exchange: _Exchange):
        self._inner = inner
        self.exchange = exchange

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.exchange.over:
            raise self.exchange.refusal()
        request.headers["accept-encoding"] = "identity"
        rpc = _rpc_request(request)
        response = await self._inner.handle_async_request(request)
        coding = response.headers.get("content-encoding", "").strip().lower()
        if coding not in ("", "identity", "gzip", "x-gzip", "deflate"):
            await response.aclose()
            raise _error("mcp_protocol_error", "MCP 服务返回了无法解码的内容")
        inflate = coding not in ("", "identity")
        if inflate:
            # Inflated below: the client must not inflate it again, and the length is no longer the body's.
            del response.headers["content-encoding"]
            response.headers.pop("content-length", None)
        response.stream = _BoundedStream(response.stream, self.exchange, inflate=inflate)
        if rpc:
            self.exchange.answered(response)
            if response.status_code >= 400 and response.headers.get("content-type", "").lower().startswith("application/json"):
                # Whether a failure's body is a JSON-RPC error decides what the failure was; it is
                # read here, under the bound, and handed on unchanged.
                stream = response.stream
                try:
                    body = b"".join([chunk async for chunk in stream])
                finally:
                    await stream.aclose()
                self.exchange.jsonrpc_code = _jsonrpc_error_code(body)
                response.stream = httpx2.ByteStream(body)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


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


def _resource_bound(max_bytes: int) -> int:
    """The response body a resource read may receive: a text resource arrives JSON-escaped and a
    binary one base64-encoded, so twice the resource's own bound and room for the envelope hold
    any resource within it."""
    return max(MAX_RESPONSE_BYTES, 2 * max_bytes + 1024 * 1024)


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
        # Calls in flight per connection and per person; changed only on the loop.
        self._conn_calls: dict[str, int] = {}
        self._user_calls: dict[str, int] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> "McpRuntime":
        with self._lock:
            if self._thread is not None:
                return self
            self._loop = asyncio.new_event_loop()
            # `asyncio.to_thread` from the loop lands here: the destination checks and DNS lookups.
            self._loop.set_default_executor(ThreadPoolExecutor(PREPARE_THREADS, thread_name_prefix="synwevo-mcp-prepare"))
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
        """Check and resolve where `url` goes. Blocking (the policy, DNS): never on the loop."""
        try:
            host, port = check_address_text(url, allow_http=spec.allow_http, http_refusal="MCP 服务地址默认要求 https")
        except UrlRejected as e:
            raise ServiceError(400, "mcp_url_invalid", f"MCP 服务地址不可用：{e}", {"field": "url"}) from None
        refusal = self.policy.destination_refusal(host)
        if refusal:
            raise ServiceError(403, refusal, "平台策略不允许连接这个地址", {"host": host})
        listed = listed_explicitly(host, spec)
        if is_own_origin(url, store=self.store) and not listed:
            raise _is_self(host)
        if self._transport_for is not None:
            return host, "fixture", port
        try:
            addresses = resolve_checked(url, owner=owner, allow_http=spec.allow_http,
                                        allow_private=spec.allow_private_networks, timeout=CONNECT_TIMEOUT_S,
                                        http_refusal="MCP 服务地址默认要求 https")
        except UrlUnresolvable:
            raise ServiceError(502, "mcp_unresolvable", "无法解析 MCP 服务地址", {"host": host, "retryable": True}) from None
        except UrlRejected:
            raise ServiceError(403, "mcp_host_not_allowed", "MCP 服务地址不被允许（内网地址需由管理员配置允许列表）",
                               {"host": host}) from None
        if not listed and any(is_own_address(a, port, store=self.store) for a in addresses):
            # A name of its own that leads back to this machine's own port is this deployment too.
            raise _is_self(host)
        return host, addresses[0], port

    def _prepare(self, url: str, owner: str, exchange: _Exchange, read_timeout: float,
                 spec: McpPolicySpec | None = None) -> httpx2.AsyncBaseTransport:
        """The blocking half of opening an exchange, on a worker thread: the policy, the checked and
        resolved destination, and the pinned transport (its TLS context reads the trust store)."""
        spec = spec or self.policy.current()
        host, address, _port = self._destination(url, owner, spec)
        if self._transport_for is not None:
            inner = self._transport_for(url)
        else:
            inner = PinnedTransport(host, address, timeout=read_timeout)
        return BoundedTransport(inner, exchange)

    @staticmethod
    def _client(transport: httpx2.AsyncBaseTransport, credential: str | None, read_timeout: float) -> httpx2.AsyncClient:
        headers = {"User-Agent": "Synwevo-Agent/1 (+mcp)", "Accept-Encoding": "identity"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        return httpx2.AsyncClient(transport=transport, headers=headers, follow_redirects=False, trust_env=False,
                                  timeout=httpx2.Timeout(CONNECT_TIMEOUT_S, read=read_timeout))

    @asynccontextmanager
    async def http(self, url: str, owner: str, credential: str | None = None, *, read_timeout: float = 30.0):
        """A pinned HTTP client for `url` (any URL the policy allows: an MCP server, its metadata, its
        authorization server) — the one way out of this process for the OAuth exchanges too. Used as
        `async with runtime.http(...) as client` on the runtime loop: opening it (policy, DNS) runs
        off the loop, and what it receives is bounded by `MAX_DOCUMENT_BYTES`."""
        transport = await asyncio.to_thread(self._prepare, url, owner, _Exchange(MAX_DOCUMENT_BYTES), read_timeout)
        async with self._client(transport, credential, read_timeout) as client:
            yield client

    def run(self, coro, *, timeout: float):
        return self._submit(coro, timeout=timeout)

    def _admit(self, connection_id: str, user_id: str | None, spec: McpPolicySpec) -> None:
        """Take a place among the connection's and the person's calls in flight, against the caps
        the policy sets now — a published change applies to the next call; refuse rather than queue."""
        if self._conn_calls.get(connection_id, 0) >= spec.max_concurrent_calls_per_connection:
            raise ServiceError(429, "mcp_connection_busy", "这个连接的并发调用已达上限，请稍后再试",
                               {"retry_after": 2, "retryable": True})
        if user_id and self._user_calls.get(user_id, 0) >= spec.max_concurrent_calls_per_user:
            raise ServiceError(429, "mcp_user_busy", "你的 MCP 并发调用已达上限，请稍后再试",
                               {"retry_after": 2, "retryable": True})
        self._conn_calls[connection_id] = self._conn_calls.get(connection_id, 0) + 1
        if user_id:
            self._user_calls[user_id] = self._user_calls.get(user_id, 0) + 1

    def _release(self, connection_id: str, user_id: str | None) -> None:
        for counts, key in ((self._conn_calls, connection_id), (self._user_calls, user_id)):
            if not key:
                continue
            left = counts.get(key, 0) - 1
            if left > 0:
                counts[key] = left
            else:
                counts.pop(key, None)

    def _exchange(self, conn: dict, credential: str | None, user_id: str | None, timeout: float, fn,
                  exchange: _Exchange, *, should_stop: Callable[[], bool] | None = None):
        """Run one exchange from a worker thread. The policy is read here, on the caller's thread, so
        admission on the loop never waits for a database read or behind someone else's DNS lookup."""
        spec = self.policy.current()
        return self._submit(self._with_client(conn, credential, user_id, timeout, fn, exchange, spec),
                            timeout=timeout + 5, should_stop=should_stop)

    async def _with_client(self, conn: dict, credential: str | None, user_id: str | None, timeout: float, fn,
                           exchange: _Exchange, spec: McpPolicySpec):
        self._admit(conn["id"], user_id, spec)
        try:
            transport = await asyncio.to_thread(self._prepare, conn["url"], conn["owner_id"], exchange, timeout, spec)
            try:
                async with self._client(transport, credential, timeout) as http:
                    async with Client(streamable_http_client(conn["url"], http_client=http), client_info=CLIENT_INFO,
                                      read_timeout_seconds=timeout) as client:
                        return await fn(client)
            except Exception as exc:  # noqa: BLE001 — what the wire showed decides what the failure was
                raise exchange.verdict(exc) from None
        finally:
            self._release(conn["id"], user_id)

    # ------------------------------------------------------------------ exchanges
    def discover(self, conn: dict, credential: str | None, *, user_id: str | None, timeout: float) -> Discovery:
        async def go(client: Client) -> Discovery:
            caps = _dump(client.server_capabilities) if client.server_capabilities else {}
            out = Discovery(protocol_version=client.protocol_version,
                            server_info=_dump(client.server_info) if client.server_info else None,
                            capabilities=caps, instructions=client.instructions)
            for attr, method in (("tools", client.list_tools), ("resources", client.list_resources),
                                 ("prompts", client.list_prompts)):
                if caps.get(attr) is not None:
                    items, cut = await _page(method, attr)
                    setattr(out, attr, items)
                    if cut:
                        out.truncated.append(attr)
                        log.warning("mcp discovery of %s kept the first %d %s of a longer list", conn["id"], MAX_LISTED, attr)
            return out
        return self._exchange(conn, credential, user_id, timeout, go, _Exchange(MAX_DISCOVERY_BYTES))

    def call_tool(self, conn: dict, credential: str | None, name: str, arguments: dict, *, user_id: str | None,
                  timeout: float, should_stop: Callable[[], bool] | None = None) -> ToolOutcome:
        async def go(client: Client) -> ToolOutcome:
            started = time.perf_counter()
            result = await client.call_tool(name, arguments, read_timeout_seconds=timeout)
            blocks = [_dump(b) for b in (result.content or [])]
            structured = result.structured_content
            return ToolOutcome(is_error=bool(result.is_error), content=blocks, structured=structured,
                               text=content_text(blocks), duration_ms=int((time.perf_counter() - started) * 1000))
        return self._exchange(conn, credential, user_id, timeout, go, _Exchange(MAX_RESPONSE_BYTES), should_stop=should_stop)

    def read_resource(self, conn: dict, credential: str | None, uri: str, *, user_id: str | None, timeout: float,
                      max_bytes: int) -> ResourceOutcome:
        def too_large() -> ServiceError:
            return ServiceError(413, "mcp_resource_too_large", "资源超过允许读取的大小", {"max_bytes": max_bytes})

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
                    raise too_large()
                contents.append(d)
            return ResourceOutcome(contents=contents, text="\n".join(texts) if texts else None, bytes_total=total,
                                   duration_ms=int((time.perf_counter() - started) * 1000))
        return self._exchange(conn, credential, user_id, timeout, go, _Exchange(_resource_bound(max_bytes), too_large))

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
        return self._exchange(conn, credential, user_id, timeout, go, _Exchange(MAX_RESPONSE_BYTES))


async def _page(method, attr: str) -> tuple[list[dict], bool]:
    """One list's entries, following its cursors to its end or to `MAX_LISTED` entries, and whether
    it went on past them (said, not dropped). A list that hands out a cursor it has already handed
    out, or `MAX_EMPTY_PAGES` pages in a row with nothing on them, does not end: refused. Any other
    list that will not end is stopped by the exchange's byte bound or the discovery's deadline —
    a failure with its own code, never a shorter list passed off as the whole one."""
    items: list[dict] = []
    cursor, seen, empty = None, set(), 0
    while True:
        result = await method(cursor=cursor)
        page = getattr(result, attr) or []
        room = MAX_LISTED - len(items)
        items.extend(_dump(x) for x in page[:room])
        cursor = result.next_cursor
        if len(page) > room or (cursor and len(items) >= MAX_LISTED):
            return items, True
        if not cursor:
            return items, False
        empty = 0 if page else empty + 1
        if cursor in seen or empty >= MAX_EMPTY_PAGES:
            raise _error("mcp_protocol_error", "MCP 服务的列表分页没有结束", detail={"list": attr})
        seen.add(cursor)
