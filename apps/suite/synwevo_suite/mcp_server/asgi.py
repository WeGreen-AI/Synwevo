"""How the MCP server is served: the official SDK's Streamable HTTP transport behind the platform gate.

The gate has already done what a resource server must (`synwevo_core.http`): verified the bearer
token for this server's own audience, refused anything else with a `WWW-Authenticate` challenge
that names the metadata document, applied the rate gate, and recorded the call. This wrapper hands
the verified principal to the handlers (`server.PRINCIPAL`) and lets the SDK speak the protocol:
revision 2026-07-28 (one POST per message, no session, no handshake) and, for clients of the
2025-era revisions, the initialize handshake — served statelessly, so any process of the deployment
can answer any request and no state has to be shared between them.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from urllib.parse import urlsplit

import anyio
import mcp_types as types
from mcp.server.streamable_http import check_accept_headers
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp.shared.auth import ProtectedResourceMetadata
from mcp.shared.inbound import InboundLadderRejection, classify_inbound_request, find_duplicated_routing_header
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS
from starlette.requests import Request

from synwevo_core import env
from synwevo_core.context import set_tenant
from synwevo_core.http import MCP_METADATA_PATH, MCP_PATH, mcp_challenge, presents_bearer
from synwevo_core.errors import ServiceError
from synwevo_core.oidc import MCP_DEFAULT_SCOPES, canonical_resource_uri, issuer, mcp_resource

from .server import PRINCIPAL, McpTools, build_server

log = logging.getLogger("synwevo.mcp.server")

#: Largest JSON-RPC request body accepted (the SDK answers 413 above it).
MAX_REQUEST_BODY = 4 * 1024 * 1024


def transport_security() -> TransportSecuritySettings:
    """Which `Host` values this endpoint answers (the SDK's DNS-rebinding protection): the workplace
    origin and the MCP server's own URL, as a client writes them (canonical: lowercase, no default
    port, which is how `Host` and `Origin` arrive), and the loopback names when that origin is one.
    Everything else is 421."""
    base = (env.get("APP_URL") or "").strip()
    parts = urlsplit(canonical_resource_uri(base)) if base else None
    hosts, origins = [], []
    for named in (parts, urlsplit(mcp_resource())):
        if named and named.scheme in ("http", "https") and named.hostname:
            host = f"[{named.hostname}]" if ":" in named.hostname else named.hostname
            authority = f"{host}:{named.port}" if named.port else host
            hosts += [authority, f"{host}:*"]
            origins.append(f"{named.scheme}://{authority}")
    if not parts or parts.hostname in ("localhost", "127.0.0.1", "::1") or (parts.hostname or "").endswith(".localhost"):
        hosts += ["localhost:*", "127.0.0.1:*", "[::1]:*", "*.localhost:*"]
        origins += ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=sorted(set(hosts)), allowed_origins=sorted(set(origins)))


class McpEndpoint:
    """The ASGI endpoint at `MCP_PATH`."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.tools = McpTools(ctx)
        self.server = build_server(ctx, self.tools)
        security = transport_security()
        self.security = TransportSecurityMiddleware(security)
        self.manager = StreamableHTTPSessionManager(
            app=self.server, json_response=False, stateless=True,
            security_settings=security, max_request_body_size=MAX_REQUEST_BODY)
        self._lock: asyncio.Lock | None = None
        self._started = False
        self._stop: asyncio.Event | None = None
        self._task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _ensure_running(self) -> None:
        """The SDK's session manager owns the handlers' task group and must be entered once per
        process; `new_api`'s lifespan has no startup hook, so it is entered on the first request
        and left by the shutdown hook."""
        if self._started:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._started:
                return
            self._loop = asyncio.get_running_loop()
            self._stop = asyncio.Event()
            ready = asyncio.Event()

            async def serve():
                async with self.manager.run():
                    ready.set()
                    await self._stop.wait()

            self._task = asyncio.create_task(serve(), name="synwevo-mcp-server")
            await ready.wait()
            self._started = True

    def close(self) -> None:
        if self._loop is not None and self._stop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)

    async def _scope_refusal(self, scope, body: bytes | None, principal: dict) -> tuple[ServiceError, object] | None:
        """A `tools/call` this token lacks the scope for, refused here — before the SDK dispatches
        it — because only here can the answer still be HTTP 403, with the challenge a client steps
        up on where stepping up can help (`McpTools.scope_refusal`). Returns the refusal and the
        JSON-RPC id it answers, or None to let the SDK serve the request: anything it would refuse
        first (Host, Origin, Content-Type, Accept, a body too large; for revision 2026-07-28 routing
        headers that disagree with the body or name no revision it serves, which Streamable HTTP
        says MUST be a 400) and anything that is not one such call."""
        request = Request(scope)
        if body is None or await self.security.validate_request(request, is_post=True) is not None:
            return None
        accepts_json, accepts_sse = check_accept_headers(request)
        if not (accepts_json and accepts_sse):
            return None
        try:
            message = json.loads(body)
        except (ValueError, RecursionError):
            return None
        if not isinstance(message, dict) or message.get("method") != "tools/call" or message.get("id") is None:
            return None
        params = message.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(name, str):
            return None
        version = request.headers.get("mcp-protocol-version")
        # the SDK's own era routing and validation ladder for a request of the per-message revisions
        if version is not None and version not in HANDSHAKE_PROTOCOL_VERSIONS and (
                find_duplicated_routing_header(request.headers.items()) is not None
                or isinstance(classify_inbound_request(message, headers=dict(request.headers)), InboundLadderRejection)):
            return None
        arguments = params.get("arguments")
        # reads the registration (and, for synwevo.run.get, the run): off the event loop, as the handlers do
        err = await anyio.to_thread.run_sync(self.tools.scope_refusal, principal, name,
                                             arguments if isinstance(arguments, dict) else {})
        return (err, message["id"]) if err is not None else None

    async def __call__(self, scope, receive, send) -> None:
        state = scope.get("state") or {}
        principal = state.get("principal")
        if not principal or principal.get("kind") != "delegated" or principal.get("transport") != "mcp":
            await _refuse(send, ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）",
                                             {"www_authenticate": {"error": "invalid_token"}}),
                          state.get("request_id"), presented=presents_bearer(Request(scope).headers.get("authorization")))
            return
        if scope.get("method") != "POST":
            await _method_not_allowed(scope, send)
            return
        messages, body = await _read_body(receive)
        receive = _replay(messages, receive)
        refused = await self._scope_refusal(scope, body, principal)
        if refused is not None:
            err, message_id = refused
            state["error_code"] = err.code
            await _refuse_call(send, err, state.get("request_id"), message_id)
            return
        await self._ensure_running()
        token = PRINCIPAL.set(principal)
        tenant = set_tenant(principal["tenant"])
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            PRINCIPAL.reset(token)
            try:
                from synwevo_core.context import _tenant
                _tenant.reset(tenant)
            except (ValueError, LookupError):
                pass


async def _refuse(send, err: ServiceError, request_id: str | None, *, presented: bool = True) -> None:
    body = json.dumps({"error": {"code": err.code, "message": err.message, "request_id": request_id}},
                      ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": err.status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"www-authenticate", mcp_challenge(err, presented=presented).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


async def _refuse_call(send, err: ServiceError, request_id: str | None, message_id) -> None:
    """403 `insufficient_scope` for one `tools/call`: the challenge for the client's authorization
    layer (with `error="insufficient_scope"` only where a step-up can grant the scope; `mcp_challenge`),
    and the JSON-RPC error for the request it answers (a client reads a JSON-RPC error body at a
    non-2xx status as that request's error, and the SDK's does)."""
    detail = {k: v for k, v in (err.detail or {}).items() if k != "www_authenticate"}
    data = {"code": err.code, **detail, **({"request_id": request_id} if request_id else {})}
    body = json.dumps({"jsonrpc": "2.0", "id": message_id,
                       "error": {"code": types.INVALID_PARAMS, "message": err.message, "data": data}},
                      ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": err.status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"www-authenticate", mcp_challenge(err).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


async def _method_not_allowed(scope, send) -> None:
    """Streamable HTTP: a server that offers no GET stream answers GET with 405, and this one is
    stateless, so there is no session for a DELETE to end either. Only POST carries messages."""
    request_id = (scope.get("state") or {}).get("request_id")
    body = json.dumps({"error": {"code": "method_not_allowed", "message": "MCP 端点只接受 POST",
                                 "request_id": request_id}}, ensure_ascii=False).encode()
    if isinstance(scope.get("state"), dict):
        scope["state"]["error_code"] = "method_not_allowed"
    await send({"type": "http.response.start", "status": 405, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"allow", b"POST"), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": b"" if scope.get("method") == "HEAD" else body})


class _OtherMethods:
    """Every method but POST at `MCP_PATH`. A route of its own, registered for all methods after the
    POST route and before the shells' `GET /{full_path:path}` catch-all, which would otherwise
    claim a GET here and answer 404 — read by a 2025-era client opening its listening stream as
    "this session is gone" rather than "no stream here"."""

    async def __call__(self, scope, receive, send) -> None:
        await _method_not_allowed(scope, send)


async def _read_body(receive) -> tuple[list[dict], bytes | None]:
    """Read the request body ahead of the SDK, keeping the ASGI messages that carried it for replay.
    The body is None when it is larger than the SDK accepts (it answers 413) or the client left."""
    messages, chunks, size = [], [], 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            return messages, None
        chunk = message.get("body", b"")
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_REQUEST_BODY:
            return messages, None
        if not message.get("more_body", False):
            return messages, b"".join(chunks)


def _replay(messages: list[dict], receive):
    pending = deque(messages)

    async def replay():
        if pending:
            return pending.popleft()
        return await receive()
    return replay


def protected_resource_metadata() -> dict:
    """RFC 9728 §2: this resource, who issues its tokens, and the scopes it understands. Built with
    the SDK's model so the shape is the one clients parse."""
    # The scopes a client asks for when the challenge names none (MCP authorization, "Scope Selection
    # Strategy": the minimal set for basic use; `MCP_DEFAULT_SCOPES` says why *.decide is not among them).
    doc = ProtectedResourceMetadata(resource=mcp_resource(), authorization_servers=[issuer()],
                                    scopes_supported=list(MCP_DEFAULT_SCOPES),
                                    bearer_methods_supported=["header"],
                                    resource_name="Synwevo MCP")
    return doc.model_dump(mode="json", exclude_none=True)


def install(ctx) -> McpEndpoint:
    from fastapi.responses import JSONResponse

    endpoint = McpEndpoint(ctx)
    ctx.api.add_route(MCP_PATH, endpoint, methods=["POST"], name="mcp")
    # No `methods`: an ASGI route then matches every method, so whatever the POST route leaves is
    # answered 405 here, ahead of any catch-all registered later (`_OtherMethods`).
    ctx.api.add_route(MCP_PATH, _OtherMethods(), name="mcp_other_methods")

    def metadata():
        return JSONResponse(protected_resource_metadata(),
                            headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})

    ctx.api.add_api_route(MCP_METADATA_PATH, metadata, methods=["GET"], include_in_schema=False, name="mcp_metadata")
    hooks = getattr(ctx.api.state, "shutdown_hooks", None)
    if hooks is not None:
        hooks.append(endpoint.close)
    ctx.api.state.mcp_server = endpoint
    return endpoint
