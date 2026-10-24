#!/usr/bin/env python
"""Isolated one-process Synwevo (role=all) for the MCP browser proofs — FIXTURE data in the scratchpad.

What is real: the whole Synwevo stack (store, gate, account service, suite applications, the Synwevo
MCP server, the Agent engine and its MCP client), served by uvicorn on loopback; a second, real MCP
server built with the official SDK on the next port (the "external" server the Agent connects to over
HTTP); an OAuth client registration for MCP Inspector.
What is a TEST DOUBLE: the model (a rule-based provider that calls the external tool when asked about
the weather, so the chat's execution cards can be shown without a model credential). Nothing here
touches data/ or any real credential.

    .venv/bin/python mcp_ui_server.py --port 33890
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import threading
import time

SCRATCH = pathlib.Path(__file__).resolve().parent
LIVE = SCRATCH / "mcp-live"
# The fixture accounts' password: given by the operator of the proof, or generated per run and printed once.
PW = os.environ.get("SYNWEVO_PROOF_PASSWORD") or __import__("secrets").token_urlsafe(18)


def configure(port: int) -> str:
    base = f"http://127.0.0.1:{port}"
    data = LIVE / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "SYNWEVO_DATA_DIR": str(data), "SYNWEVO_TEST_INSTANCE": "1", "SYNWEVO_UI_AUTH": "session",
        "SYNWEVO_COOKIE_SECURE": "0", "SYNWEVO_MAIL_MODE": "disabled", "SYNWEVO_ROLE": "all",
        "SYNWEVO_SUITE_APPS": "forms,tables,agent", "SYNWEVO_PUBLIC_URL": base, "SYNWEVO_APP_URL": base,
        "SYNWEVO_ISSUER": base, "SYNWEVO_PORTAL_URL": base,
        "SYNWEVO_AGENT_ANTHROPIC_API_KEY": "test-double-key-not-a-real-credential",
        "SYNWEVO_AGENT_MODEL": "test-double-model",
    })
    return base


class DemoModel:
    """TEST DOUBLE for the model provider. Rule-based: a question about the weather calls the external
    forecast tool; a request to keep a note calls add_note; a tool result is summarised in one sentence."""
    name = "anthropic"

    def step(self, *, credential, system, tools, messages, effort, on_text, should_stop):
        from synwevo_suite.agent.provider import StepResult
        last = messages[-1]
        content = last.get("content")
        if last.get("role") == "user" and isinstance(content, list) and any(b.get("type") == "tool_result" for b in content):
            texts = []
            for b in content:
                if b.get("type") == "tool_result":
                    try:
                        payload = json.loads(b.get("content") or "{}")
                    except ValueError:
                        payload = {}
                    if isinstance(payload, dict) and payload.get("error"):
                        texts.append(f"操作未能完成：{payload['error'].get('code')}。")
                    elif isinstance(payload, dict) and payload.get("text"):
                        texts.append(f"外部服务返回：{payload['text'][:200]}")
                    elif isinstance(payload, dict) and payload.get("operations") is not None:
                        texts.append("已找到相关操作。")
                    else:
                        texts.append("已完成。")
            text = " ".join(texts) or "已完成。"
            on_text(text)
            return StepResult(content=[{"type": "text", "text": text}], stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})
        prompt = content if isinstance(content, str) else " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        tid = f"tu_{int(time.time() * 1000)}"
        if "天气" in prompt or "weather" in prompt.lower():
            city = "Hong Kong" if "香港" in prompt or "hong kong" in prompt.lower() else "Shanghai"
            return StepResult(content=[{"type": "tool_use", "id": tid, "name": "run_operation",
                                        "input": {"operation": "mcp:weather/forecast", "input": {"city": city}}}],
                              stop_reason="tool_use", usage={"input_tokens": 1, "output_tokens": 1})
        if "笔记" in prompt or "note" in prompt.lower():
            return StepResult(content=[{"type": "tool_use", "id": tid, "name": "run_operation",
                                        "input": {"operation": "mcp:weather/add_note", "input": {"text": prompt[:200]}}}],
                              stop_reason="tool_use", usage={"input_tokens": 1, "output_tokens": 1})
        text = "（演示替身）我可以查天气或记笔记：请问「明天香港天气如何」或「记一条笔记：…」。"
        on_text(text)
        return StepResult(content=[{"type": "text", "text": text}], stop_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})


def external_server(port: int):
    """A real MCP server (official SDK) the Agent will connect to over HTTP."""
    from mcp.server import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings
    from mcp_types import ToolAnnotations
    import uvicorn
    server = MCPServer("Weather fixture", instructions="Fixture weather and notes server for the Synwevo MCP proof.")
    notes: list[str] = []

    @server.tool(name="forecast", description="Tomorrow's forecast for a city (fixture data).",
                 annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True))
    def forecast(city: str) -> str:
        return f"Sunny in {city}, 24°C, light breeze (fixture)."

    @server.tool(name="add_note", description="Store a note (changes data).",
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
    def add_note(text: str) -> dict:
        notes.append(text)
        return {"stored": len(notes), "text": text}

    @server.resource("notes://all", name="all-notes", description="Every stored note.", mime_type="text/plain")
    def all_notes() -> str:
        return "\n".join(notes) or "(no notes yet)"

    @server.prompt(name="summarise", description="Ask for a summary of the notes.")
    def summarise(style: str = "short") -> str:
        return f"Summarise the stored notes in a {style} style."

    app = server.streamable_http_app(transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    uvicorn.Server(config).run()


def seed(base: str, port: int) -> dict:
    from synwevo_core import oidc
    from synwevo_core.identity import Identity
    from synwevo_core.mcp_policy import McpPolicy
    from synwevo_core.platform_access import SUPER_ADMIN, SYSTEM_REASONS, PlatformAccess
    from synwevo_core.store import Store
    store = Store(LIVE / "data")
    ident = Identity(store)
    people = {}
    for key, email, name in (("admin", "platform.admin@example.test", "Platform Admin"),
                             ("alice", "alice.mcp@example.test", "Alice Owner"),
                             ("bob", "bob.mcp@example.test", "Bob Editor")):
        u = ident.get_user_by_email(email) if hasattr(ident, "get_user_by_email") else None
        if u is None:
            u = ident.create_user(email, PW, display_name=name, status="active")
            store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        people[key] = u
    if not ident.platform_admins():
        with store.tx():
            PlatformAccess(store).host_grant(people["admin"]["id"], SUPER_ADMIN, source="bootstrap:cli",
                                             reason=SYSTEM_REASONS["bootstrap_cli"])
    spaces = [w for w in ident.workspaces_for(people["alice"]["id"]) if w["name"] == "Demo workspace"]
    if spaces:
        demo = spaces[0]["id"]
    else:
        demo = ident.create_workspace("Demo workspace", owner_id=people["alice"]["id"])["id"]
        ident.add_member(demo, people["bob"]["id"], "editor")
    policy = McpPolicy(store)
    current = policy.read()
    if current["revision"] == 0:
        with store.tx():
            policy.append({**McpPolicy.default().model_dump(), "outbound_enabled": True, "inbound_enabled": True,
                           "allow_http": True, "allow_private_networks": ["127.0.0.1", "localhost"],
                           "client_registration": "cimd_and_preregistered"},
                          expected_revision=0, actor_id=people["admin"]["id"], reason="fixture: open both directions on loopback")
    clients = oidc.Clients(store)
    if not clients.get_any("mcp-inspector"):
        clients.register(client_id="mcp-inspector", app_id="platform", name="MCP Inspector (local)",
                         redirect_uris=["http://localhost:6274/oauth/callback", "http://127.0.0.1:6274/oauth/callback",
                                        "http://localhost:6274/oauth/callback/debug", "http://127.0.0.1:6274/oauth/callback/debug"],
                         scopes="openid offline_access forms.read forms.write tables.read tables.write",
                         audiences=[oidc.mcp_resource()])
    # A FIXTURE delegated token for the SDK probe (as the account service mints one after consent).
    grants = oidc.Grants(store)
    out = grants.issue_tokens(client=clients.get("mcp-inspector"),
                              code_row={"user_id": people["alice"]["id"], "scope": "openid forms.read forms.write",
                                        "central_session_id": None, "workspace_id": demo},
                              with_refresh=False, audience=oidc.mcp_resource())
    token_file = LIVE / "token.txt"
    token_file.write_text(out["access_token"])
    os.chmod(token_file, 0o600)
    return {"base": base, "external_mcp": f"http://127.0.0.1:{port + 1}/mcp", "workspace": demo,
            "accounts": {k: v["email"] for k, v in people.items()}, "token_file": str(token_file),
            "mcp_endpoint": oidc.mcp_resource()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=33890)
    args = ap.parse_args()
    base = configure(args.port)
    import uvicorn
    from synwevo_server.app import create_app
    threading.Thread(target=external_server, args=(args.port + 1,), name="external-mcp-fixture", daemon=True).start()
    api = create_app(str(LIVE / "data"), role="all", start_jobs=True)
    api.state.agent.provider = DemoModel()
    info = seed(base, args.port)
    (LIVE / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2))
    print("ISOLATED MCP PROOF INSTANCE", json.dumps(info, ensure_ascii=False))
    print("password for the fixture accounts:", PW)
    uvicorn.run(api, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
