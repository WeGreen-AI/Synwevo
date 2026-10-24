#!/usr/bin/env python
"""Verification A (protocol level, real HTTP): the official MCP Python SDK as an external client against
a running Synwevo (role=all) at --base, over the network — not an in-process ASGI transport.

The bearer token comes from --token-file (the isolated server writes a FIXTURE token there, minted the
way the account service mints one after consent; the OAuth browser flow itself is proven separately
with MCP Inspector / the integration tests). Prints what happened as JSON so the evidence file is the
output itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main(base: str, token: str, mode: str) -> dict:
    out = {"base": base, "mode": mode, "steps": []}
    url = base.rstrip("/") + "/api/suite/v1/mcp"
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(30.0, read=120.0)) as anon:
        meta = await anon.get(base.rstrip("/") + "/.well-known/oauth-protected-resource/api/suite/v1/mcp")
        out["steps"].append({"step": "protected_resource_metadata", "status": meta.status_code, "body": meta.json()})
        refused = await anon.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                  headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2026-07-28",
                                           "Mcp-Method": "tools/list"})
        out["steps"].append({"step": "unauthenticated_challenge", "status": refused.status_code,
                             "www_authenticate": refused.headers.get("www-authenticate")})
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(30.0, read=120.0)) as http:
        kwargs = {"mode": mode} if mode != "auto" else {}
        started = time.perf_counter()
        async with Client(streamable_http_client(url, http_client=http), **kwargs) as client:
            out["protocol_version"] = client.protocol_version
            out["server_info"] = client.server_info.model_dump(mode="json") if client.server_info else None
            tools = await client.list_tools()
            out["steps"].append({"step": "tools/list", "count": len(tools.tools), "names": [t.name for t in tools.tools]})
            who = await client.call_tool("synwevo.whoami", {})
            out["steps"].append({"step": "tools/call synwevo.whoami", "is_error": who.is_error, "result": who.structured_content})
            key = f"probe-{int(time.time())}"
            made = await client.call_tool("forms.form.create", {"title": f"MCP probe {key}", "idempotency_key": key})
            out["steps"].append({"step": "tools/call forms.form.create", "is_error": made.is_error,
                                 "status": (made.structured_content or {}).get("status"),
                                 "form_id": ((made.structured_content or {}).get("output") or {}).get("id")})
            again = await client.call_tool("forms.form.create", {"title": f"MCP probe {key}", "idempotency_key": key})
            out["steps"].append({"step": "tools/call forms.form.create (same idempotency_key)",
                                 "replayed": (again.structured_content or {}).get("replayed")})
            listed = await client.call_tool("forms.form.list", {})
            out["steps"].append({"step": "tools/call forms.form.list", "is_error": listed.is_error,
                                 "titles": [f["title"] for f in ((listed.structured_content or {}).get("output") or {}).get("forms", [])]})
            resources = await client.list_resources()
            out["steps"].append({"step": "resources/list", "uris": [str(r.uri) for r in resources.resources]})
            read = await client.read_resource("synwevo://whoami")
            out["steps"].append({"step": "resources/read synwevo://whoami", "mime": read.contents[0].mime_type})
        out["elapsed_s"] = round(time.perf_counter() - started, 2)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--mode", default="auto", choices=["auto", "legacy", "modern"])
    args = ap.parse_args()
    token = pathlib.Path(args.token_file).read_text().strip()
    result = asyncio.run(main(args.base, token, args.mode))
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()
