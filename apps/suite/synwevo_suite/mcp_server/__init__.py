"""Synwevo as an MCP server (docs/platform/mcp.md §3): the business applications' operations for an
MCP client a person authorised, on the workplace origin at ``/api/suite/v1/mcp``."""
from __future__ import annotations

from .asgi import McpEndpoint, install, protected_resource_metadata
from .server import PRINCIPAL, build_server

__all__ = ["McpEndpoint", "PRINCIPAL", "build_server", "install", "protected_resource_metadata"]
