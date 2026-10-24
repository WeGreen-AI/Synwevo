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
from urllib.parse import urlsplit

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import ProtectedResourceMetadata

from synwevo_core import env
from synwevo_core.context import set_tenant
from synwevo_core.http import MCP_METADATA_PATH, MCP_PATH, mcp_challenge
from synwevo_core.errors import ServiceError
from synwevo_core.oidc import MCP_SCOPES, issuer, mcp_resource

from .server import PRINCIPAL, build_server

log = logging.getLogger("synwevo.mcp.server")

#: Largest JSON-RPC request body accepted (the SDK answers 413 above it).
MAX_REQUEST_BODY = 4 * 1024 * 1024


def transport_security() -> TransportSecuritySettings:
    """Which `Host` values this endpoint answers (the SDK's DNS-rebinding protection): the workplace
    origin, and the loopback names when that origin is one. Everything else is 421."""
    base = (env.get("APP_URL") or "").strip()
    parts = urlsplit(base) if base else None
    hosts, origins = [], []
    if parts and parts.hostname:
        hosts += [parts.netloc, f"{parts.hostname}:*"]
        origins.append(f"{parts.scheme}://{parts.netloc}")
    if not parts or parts.hostname in ("localhost", "127.0.0.1", "::1") or (parts.hostname or "").endswith(".localhost"):
        hosts += ["localhost:*", "127.0.0.1:*", "[::1]:*", "*.localhost:*"]
        origins += ["http://localhost:*", "http://127.0.0.1:*", "http://[::1]:*"]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                     allowed_hosts=sorted(set(hosts)), allowed_origins=sorted(set(origins)))


class McpEndpoint:
    """The ASGI endpoint at `MCP_PATH`."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.server = build_server(ctx)
        self.manager = StreamableHTTPSessionManager(
            app=self.server, json_response=False, stateless=True,
            security_settings=transport_security(), max_request_body_size=MAX_REQUEST_BODY)
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

    async def __call__(self, scope, receive, send) -> None:
        state = scope.get("state") or {}
        principal = state.get("principal")
        if not principal or principal.get("kind") != "delegated" or principal.get("transport") != "mcp":
            await _refuse(send, ServiceError(401, "unauthorized", "缺少凭据（Authorization: Bearer <平台访问令牌>）",
                                             {"www_authenticate": {"error": "invalid_token"}}),
                          state.get("request_id"))
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


async def _refuse(send, err: ServiceError, request_id: str | None) -> None:
    body = json.dumps({"error": {"code": err.code, "message": err.message, "request_id": request_id}},
                      ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": err.status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"www-authenticate", mcp_challenge(err).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


def protected_resource_metadata() -> dict:
    """RFC 9728 §2: this resource, who issues its tokens, and the scopes it understands. Built with
    the SDK's model so the shape is the one clients parse."""
    # The scopes a client asks for when the challenge names none (MCP authorization, "Scope Selection
    # Strategy": the minimal set for basic use). Reading and changing the applications' records; a
    # decision taken as the person (`*.decide`) is never asked for by default — a client that needs
    # it names it explicitly, and the person sees it named on the consent page.
    doc = ProtectedResourceMetadata(resource=mcp_resource(), authorization_servers=[issuer()],
                                    scopes_supported=[s for s in MCP_SCOPES if not s.endswith(".decide")],
                                    bearer_methods_supported=["header"],
                                    resource_name="Synwevo MCP")
    return doc.model_dump(mode="json", exclude_none=True)


def install(ctx) -> McpEndpoint:
    from fastapi.responses import JSONResponse

    endpoint = McpEndpoint(ctx)
    ctx.api.add_route(MCP_PATH, endpoint, methods=["POST"], name="mcp")

    def metadata():
        return JSONResponse(protected_resource_metadata(),
                            headers={"Cache-Control": "public, max-age=300", "Access-Control-Allow-Origin": "*"})

    ctx.api.add_api_route(MCP_METADATA_PATH, metadata, methods=["GET"], include_in_schema=False, name="mcp_metadata")
    hooks = getattr(ctx.api.state, "shutdown_hooks", None)
    if hooks is not None:
        hooks.append(endpoint.close)
    ctx.api.state.mcp_server = endpoint
    return endpoint
