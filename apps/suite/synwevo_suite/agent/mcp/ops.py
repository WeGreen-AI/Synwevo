"""The Agent's MCP operations (all personal, none for the model) and the two HTTP routes the OAuth
flow needs: the Agent's Client ID Metadata Document and the callback. The four carriers run only
for the engine executing a model's mcp: operation (`tools.CARRIERS`); they are not the model's to
name and not a page's or a script's to call."""
from __future__ import annotations

import json
import urllib.parse
from typing import Any, Literal

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field as F, SecretStr

from synwevo_core.capabilities import Operation
from synwevo_core.errors import ServiceError
from synwevo_core.http import MCP_CLIENT_METADATA_PATH, MCP_METADATA_PATH, mcp_metadata_url
from synwevo_core.oidc import MCP_SCOPES, issuer, mcp_resource

from .connections import Connections
from .oauth import CALLBACK_PATH, OutboundOAuth, client_metadata_document
from .tools import (AGENT_CALL, CANCEL_CHECK, REGISTERED_CALL, REGISTERED_READ, REGISTERED_RESOURCE_LIST,
                    REGISTERED_RESOURCE_READ, McpTools, result_connection)

CID = r"^mcpc_[a-f0-9]{12}$"
WS = r"^[A-Za-z0-9_-]{1,100}$"


def T(zh, hant, en):
    return {"zh-Hans": zh, "zh-Hant": hant, "en": en}


def _stored(value: Any) -> Any:
    """A JSON column of a run record as the store hands it back: some (``output``) already
    decoded, others (``input``) still text."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value) if value else None
        except ValueError:
            return None
    return value


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Empty(_In):
    pass


class ConnectionRef(_In):
    connection_id: str = F(pattern=CID)


class ListConnections(_In):
    q: str | None = F(default=None, max_length=100)
    workspace_id: str | None = F(default=None, pattern=WS)


class OAuthClientIn(_In):
    client_id: str = F(min_length=1, max_length=512)
    client_secret: SecretStr | None = None


class CreateConnection(_In):
    owner_kind: Literal["user", "workspace"] = "user"
    workspace_id: str | None = F(default=None, pattern=WS)
    name: str = F(min_length=1, max_length=80)
    url: str = F(min_length=1, max_length=2048)
    description: str = F(default="", max_length=500)
    auth_kind: Literal["none", "bearer", "oauth"] = "none"
    credential: SecretStr | None = None
    oauth_client: OAuthClientIn | None = None


class UpdateConnection(ConnectionRef):
    name: str | None = F(default=None, min_length=1, max_length=80)
    description: str | None = F(default=None, max_length=500)
    url: str | None = F(default=None, min_length=1, max_length=2048)
    auth_kind: Literal["none", "bearer", "oauth"] | None = None
    credential: SecretStr | None = None
    oauth_client: OAuthClientIn | None = None


class SetGrant(ConnectionRef):
    tool: str = F(min_length=1, max_length=128)
    mode: Literal["off", "confirm", "auto"]


class SetBinding(ConnectionRef):
    enabled: bool | None = None
    alias: str | None = F(default=None, min_length=1, max_length=24)
    resources_enabled: bool | None = None
    tool_modes: dict[str, Literal["off", "confirm"]] | None = None


class CallsIn(ConnectionRef):
    limit: int = F(default=50, ge=1, le=200)
    before: float | None = None


class ResourceReadIn(ConnectionRef):
    uri: str = F(min_length=1, max_length=2048)


class PromptRenderIn(ConnectionRef):
    name: str = F(min_length=1, max_length=128)
    arguments: dict[str, str] = F(default_factory=dict)


class ToolCallIn(_In):
    """The carrier of a model's call of an external tool (`tools.McpTools.prepare` built it)."""
    connection: str = F(min_length=1, max_length=24)
    tool: str = F(min_length=1, max_length=128)
    arguments: dict = F(default_factory=dict)


class ResourceReadAgentIn(_In):
    connection: str = F(min_length=1, max_length=24)
    uri: str = F(min_length=1, max_length=2048)


class ResourceListAgentIn(_In):
    connection: str = F(min_length=1, max_length=24)


def _secret(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _oauth_client(value: OAuthClientIn | None) -> dict | None:
    if value is None:
        return None
    return {"client_id": value.client_id, "client_secret": _secret(value.client_secret)}


def inbound_info(store) -> dict:
    """What a person needs to connect an MCP client to Synwevo: real deployment addresses, never a
    placeholder host. The examples are derived from these the same way on the page."""
    from synwevo_core.mcp_policy import McpPolicy
    spec = McpPolicy(store).current()
    return {"enabled": spec.inbound_enabled, "endpoint": mcp_resource(), "metadata_url": mcp_metadata_url(),
            "issuer": issuer(), "authorization_server_metadata": issuer() + "/.well-known/oauth-authorization-server",
            # What the protected resource metadata advertises (asked for by default), and the decisions
            # taken as the person, which a client asks for only by naming them (mcp_server/asgi.py).
            "scopes": [s for s in MCP_SCOPES if not s.endswith(".decide")],
            "explicit_scopes": [s for s in MCP_SCOPES if s.endswith(".decide")],
            "protocol_versions": ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"],
            "client_registration": spec.client_registration, "transport": "streamable-http"}


def register(ctx, agent, connections: Connections) -> None:
    registry, store = ctx.registry, ctx.store
    oauth = OutboundOAuth(connections)

    def op(**kw):
        kw.setdefault("need", "personal")
        kw.setdefault("agent", "never")
        registry.register(Operation(app="agent", **kw))

    def guard(c, row):
        # A stored result of a carrier stays readable only while the person may still use the
        # connection it came through (the one its call record or, for a resource list, it names).
        inp = _stored(row.get("input"))
        conn = result_connection(store, c.user_id, inp.get("connection") if isinstance(inp, dict) else None,
                                 _stored(row.get("output")))
        if not conn or conn["deleted_at"] is not None or not connections.may_use(c.user_id, conn):
            raise ServiceError(409, "result_access_changed", "连接已不可用")

    def written(c, row):
        # A page's retry of adding or changing a connection with the same key gets its receipt (the
        # summary as written then; nothing is written again) while the person may still use it.
        out = _stored(row.get("output"))
        cid = out.get("id") if isinstance(out, dict) else None
        conn = store.one("SELECT * FROM mcp_connections WHERE id=? AND deleted_at IS NULL", (cid,)) if isinstance(cid, str) else None
        if not conn or not connections.may_use(c.user_id, conn):
            raise ServiceError(409, "result_access_changed", "连接已不可用")

    # ------------------------------------------------------------------ what the engine runs for the model
    def engine_call(c) -> dict:
        """The tool call of the Agent's engine this carrier runs for (`engine._execute` names it
        around `Capabilities.invoke`), checked again here rather than trusted from the caller: the
        data-flow outbound question is asked at the moment the request would leave."""
        ids = AGENT_CALL.get() or {}
        t = store.one("SELECT * FROM agent_turns WHERE id=? AND user_id=? AND conversation_id=?",
                      (ids.get("turn_id"), c.user_id, ids.get("conversation_id"))) if c.via == "agent" else None
        if t is None or ids.get("carrier") != c.op.id:
            raise ServiceError(403, "mcp_agent_only", "这个操作只在 Agent 调用外部 MCP 工具时使用，不能直接调用")
        McpTools(connections, c.user).require_outbound(agent.mcp_sources(t))
        return ids

    def agent_call(c, i, *, require_mode=None):
        ids = engine_call(c)
        return connections.call_tool(c.user, i.connection, i.tool, i.arguments, origin="agent",
                                     conversation_id=ids.get("conversation_id"), turn_id=ids.get("turn_id"),
                                     tool_call_id=ids.get("tool_call_id"), should_stop=CANCEL_CHECK.get(),
                                     require_mode=require_mode)

    def agent_read_resource(c, i):
        ids = engine_call(c)
        return connections.read_resource(c.user, i.connection, i.uri, origin="agent",
                                         **{k: ids.get(k) for k in ("conversation_id", "turn_id", "tool_call_id")})

    def agent_list_resources(c, i):
        engine_call(c)
        entry = _agent_resource_connection(c.user, i.connection)
        # The list makes no request, so no call record names its connection: the result does.
        return {"connection": i.connection, "connection_id": entry["connection_id"],
                "resources": connections.list_resources(c.user, entry["connection_id"])}

    # Each reaches an external server (or answers for one): a stored result is never rebuilt by
    # running the carrier again (rereadable=False), only checked against the connection (guard).
    carrier = dict(need="personal", agent="auto", transactional=False, result_guard=guard, rereadable=False)
    op(id=REGISTERED_CALL, title=T("调用外部 MCP 工具", "呼叫外部 MCP 工具", "Call an external MCP tool"),
       summary="Run one tool of an external MCP server the person connected and enabled for their Agent; confirmed first.",
       input=ToolCallIn, side_effect="external", **{**carrier, "agent": "confirm"}, handler=agent_call)
    op(id=REGISTERED_READ, title=T("读取外部 MCP 工具", "讀取外部 MCP 工具", "Read through an external MCP tool"),
       summary="Run one read-only tool of an external MCP server the person allowed to run without asking.",
       input=ToolCallIn, side_effect="none", **carrier,
       # Only a tool whose effective grant is auto and that the server marks read-only, checked by
       # the service when the call is made, not when the model's call was prepared.
       handler=lambda c, i: agent_call(c, i, require_mode="auto"))
    op(id=REGISTERED_RESOURCE_READ, title=T("读取外部 MCP 资源", "讀取外部 MCP 資源", "Read an external MCP resource"),
       summary="Read one resource of an external MCP server whose resources the person opened to their Agent.",
       input=ResourceReadAgentIn, side_effect="none", **carrier, handler=agent_read_resource)
    op(id=REGISTERED_RESOURCE_LIST, title=T("列出外部 MCP 资源", "列出外部 MCP 資源", "List external MCP resources"),
       summary="List the resources of an external MCP server whose resources the person opened to their Agent.",
       input=ResourceListAgentIn, side_effect="none", **carrier, handler=agent_list_resources)

    def _agent_resource_connection(user, alias):
        entry = next((e for e in connections.resource_connections(user["id"]) if e["alias"] == alias), None)
        if entry is None:
            raise ServiceError(404, "mcp_resources_unavailable", "这个连接的资源没有开放给 Agent")
        return entry

    # ------------------------------------------------------------------ the pages
    op(id="agent.mcp.policy.get", title=T("查看 MCP 开放情况", "查看 MCP 開放情況", "MCP availability"),
       summary="Whether this deployment lets the Agent connect to MCP servers and lets MCP clients call Synwevo, with the limits.",
       input=Empty, handler=lambda c, i: _policy_view(store))
    op(id="agent.mcp.inbound.info", title=T("连接你的 MCP 客户端", "連接你的 MCP 客戶端", "Connect your MCP client"),
       summary="The real addresses and scopes an MCP client needs to use Synwevo.", input=Empty,
       handler=lambda c, i: inbound_info(store))
    op(id="agent.mcp.connection.list", title=T("列出 MCP 连接", "列出 MCP 連線", "List MCP connections"),
       summary="The person's own connections and those of the workspaces they belong to.", input=ListConnections,
       handler=lambda c, i: {"items": connections.list(c.user, q=i.q or "", workspace_id=i.workspace_id)})
    op(id="agent.mcp.connection.create", title=T("添加 MCP 连接", "新增 MCP 連線", "Add an MCP connection"),
       summary="Add a connection to an external MCP server, for the person or for a workspace they administer.",
       input=CreateConnection, side_effect="write", transactional=False, result_guard=written,
       handler=lambda c, i: connections.create(c.user, owner_kind=i.owner_kind, owner_id=i.workspace_id, name=i.name, url=i.url,
                                               description=i.description, auth_kind=i.auth_kind, credential=_secret(i.credential),
                                               oauth_client=_oauth_client(i.oauth_client)))
    op(id="agent.mcp.connection.get", title=T("查看 MCP 连接", "查看 MCP 連線", "MCP connection details"),
       summary="A connection with its tools, grants, resources, prompts, health and recent calls.", input=ConnectionRef,
       handler=lambda c, i: connections.detail(c.user, i.connection_id))
    op(id="agent.mcp.connection.update", title=T("修改 MCP 连接", "修改 MCP 連線", "Update an MCP connection"),
       summary="Change a connection's name, address, description or credential.", input=UpdateConnection, side_effect="write",
       transactional=False, result_guard=written,
       handler=lambda c, i: connections.update(c.user, i.connection_id, name=i.name, description=i.description, url=i.url,
                                               auth_kind=i.auth_kind, credential=_secret(i.credential), oauth_client=_oauth_client(i.oauth_client)))
    op(id="agent.mcp.connection.delete", title=T("删除 MCP 连接", "刪除 MCP 連線", "Delete an MCP connection"),
       summary="Delete a connection and forget its credentials.", input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: connections.delete(c.user, i.connection_id))
    op(id="agent.mcp.connection.disable", title=T("停用 MCP 连接", "停用 MCP 連線", "Disable an MCP connection"),
       summary="Stop every use of a connection without deleting it.", input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: connections.set_status(c.user, i.connection_id, "disabled"))
    op(id="agent.mcp.connection.enable", title=T("启用 MCP 连接", "啟用 MCP 連線", "Enable an MCP connection"),
       summary="Resume a disabled connection.", input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: connections.set_status(c.user, i.connection_id, "active"))
    op(id="agent.mcp.connection.test", title=T("测试 MCP 连接", "測試 MCP 連線", "Test an MCP connection"),
       summary="A real protocol handshake and capability read; records health and refreshes the offered tools, resources and prompts.",
       input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: connections.test(c.user, i.connection_id, origin="test"))
    op(id="agent.mcp.connection.refresh", title=T("刷新 MCP 能力", "重新整理 MCP 能力", "Refresh MCP capabilities"),
       summary="Read the server's current tools, resources and prompts again; grants whose tool changed shape become stale.",
       input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: connections.test(c.user, i.connection_id, origin="discover"))
    op(id="agent.mcp.credential.revoke", title=T("撤销 MCP 连接凭据", "撤銷 MCP 連線憑據", "Revoke an MCP connection's credential"),
       summary="Forget the connection's token; the connection needs authentication again.", input=ConnectionRef,
       side_effect="write", transactional=False, handler=lambda c, i: connections.revoke_credential(c.user, i.connection_id))
    op(id="agent.mcp.grant.set", title=T("设置 MCP 工具授权", "設定 MCP 工具授權", "Set an MCP tool grant"),
       summary="Whether the Agent may use a tool of a connection: off, confirm (ask first) or auto (read-only tools only).",
       input=SetGrant, side_effect="write", transactional=False,
       handler=lambda c, i: connections.set_grant(c.user, i.connection_id, i.tool, i.mode))
    op(id="agent.mcp.binding.set", title=T("设置 Agent 使用的连接", "設定 Agent 使用的連線", "Use a connection in the Agent"),
       summary="Let the person's own Agent use a connection under an alias, optionally narrowing tools and opening resources.",
       input=SetBinding, side_effect="write", transactional=False,
       handler=lambda c, i: connections.bind(c.user, i.connection_id, enabled=i.enabled, alias=i.alias,
                                             resources_enabled=i.resources_enabled, tool_modes=i.tool_modes))
    op(id="agent.mcp.binding.list", title=T("Agent 使用的连接", "Agent 使用的連線", "Connections the Agent uses"),
       summary="The connections this person's Agent is set to use.", input=Empty,
       handler=lambda c, i: {"items": connections.my_bindings(c.user), "tools": connections.effective_tools(c.user["id"])})
    op(id="agent.mcp.calls", title=T("MCP 调用记录", "MCP 呼叫記錄", "MCP call log"),
       summary="Calls made through a connection: who, which tool, how long, how it ended.", input=CallsIn,
       handler=lambda c, i: {"items": connections.calls(c.user, i.connection_id, limit=i.limit, before=i.before)})
    op(id="agent.mcp.resource.browse", title=T("浏览 MCP 资源", "瀏覽 MCP 資源", "Browse MCP resources"),
       summary="The resources a connection offers, as last discovered.", input=ConnectionRef,
       handler=lambda c, i: {"items": connections.list_resources(c.user, i.connection_id)})
    op(id="agent.mcp.resource.read_page", title=T("读取 MCP 资源", "讀取 MCP 資源", "Read an MCP resource"),
       summary="Read one resource of a connection for the person to look at (bounded, text only).", input=ResourceReadIn,
       side_effect="write", transactional=False,
       handler=lambda c, i: _page_resource(c.user, i.connection_id, i.uri))
    op(id="agent.mcp.prompt.render", title=T("使用 MCP 提示词", "使用 MCP 提示詞", "Use an MCP prompt"),
       summary="Render a server prompt with arguments; the person reads it and may send it as their own message.",
       input=PromptRenderIn, side_effect="write", transactional=False,
       handler=lambda c, i: connections.render_prompt(c.user, i.connection_id, i.name, i.arguments))
    op(id="agent.mcp.oauth.start", title=T("认证 MCP 连接", "認證 MCP 連線", "Authenticate an MCP connection"),
       summary="Begin the OAuth authorization of a connection; returns the address to send the browser to.",
       input=ConnectionRef, side_effect="write", transactional=False,
       handler=lambda c, i: oauth.start(c.user, i.connection_id))

    def _page_resource(user, cid, uri):
        conn = connections._row(cid)
        connections._use(user["id"], conn)
        b = store.one("SELECT alias FROM mcp_agent_bindings WHERE user_id=? AND connection_id=?", (user["id"], cid))
        alias = b["alias"] if b else None
        if alias is None or not any(r["alias"] == alias for r in connections.resource_connections(user["id"])):
            # Reading on the page does not need the Agent binding; it needs the person's own right to use the connection.
            return connections.read_resource_direct(user, conn, uri)
        return connections.read_resource(user, alias, uri, origin="page")

    # ------------------------------------------------------------------ routes
    from ...common.web import signed_in

    @ctx.api.get(MCP_CLIENT_METADATA_PATH, include_in_schema=False, tags=["agent"])
    def agent_client_metadata():
        return JSONResponse(client_metadata_document(), headers={"Cache-Control": "public, max-age=3600"})

    @ctx.api.get(CALLBACK_PATH, include_in_schema=False, tags=["agent"])
    def oauth_callback(request: Request, state: str | None = None, code: str | None = None, iss: str | None = None,
                       error: str | None = None, error_description: str | None = None):
        user = signed_in(request)
        row = store.one("SELECT connection_id FROM mcp_oauth_flows WHERE state=?", (state,)) if state else None
        target = f"/agent/integrations/{row['connection_id']}" if row else "/agent/integrations"
        try:
            out = oauth.callback(user, state=state, code=code, iss=iss, error=error, error_description=error_description)
            query = {"auth": "ok"}
            target = f"/agent/integrations/{out['connection_id']}"
        except ServiceError as err:
            query = {"auth": "error", "code": err.code}
        return RedirectResponse(f"{target}?{urllib.parse.urlencode(query)}", status_code=303)


def _policy_view(store) -> dict:
    from synwevo_core.mcp_policy import McpPolicy
    state = McpPolicy(store).read()
    spec = state["spec"]
    return {"revision": state["revision"], "outbound_enabled": spec["outbound_enabled"], "inbound_enabled": spec["inbound_enabled"],
            "allow_http": spec["allow_http"], "allowed_hosts": spec["allowed_hosts"], "blocked_hosts": spec["blocked_hosts"],
            "limits": {k: spec[k] for k in ("max_connections_per_user", "max_connections_per_workspace",
                                             "max_concurrent_calls_per_connection", "max_concurrent_calls_per_user",
                                             "call_timeout_s", "discover_timeout_s", "max_result_chars", "max_resource_bytes")},
            "client_registration": spec["client_registration"]}
