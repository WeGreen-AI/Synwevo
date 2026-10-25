"""The Agent as an MCP client (docs/platform/mcp.md §4): connections to external MCP servers, what
they offer, who may use which tool, and the calls the Agent makes through them."""
from __future__ import annotations

from .connections import Connections
from .runtime import McpRuntime


def install(ctx, agent) -> Connections:
    runtime = McpRuntime(ctx.store)
    connections = Connections(ctx.store, ctx.identity, runtime)
    agent.connections = connections
    ctx.api.state.mcp_connections = connections
    from .ops import register
    register(ctx, agent, connections)
    hooks = getattr(ctx.api.state, "shutdown_hooks", None)
    if hooks is not None:
        hooks.append(runtime.close)
    return connections


__all__ = ["Connections", "McpRuntime", "install"]
