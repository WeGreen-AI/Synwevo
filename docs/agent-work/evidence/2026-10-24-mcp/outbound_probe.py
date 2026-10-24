#!/usr/bin/env python
"""Verification B (protocol and network level): the Agent in the isolated instance connects to the real
external MCP server (the official SDK's server on loopback, reached over HTTP through the pinned
transport and the policy-checked resolver, not an in-process transport), discovers it, and a turn calls
its tools — a read-only one without asking, a writing one after the person confirms.

The model is the instance's TEST DOUBLE (rule-based); the person's actions are the real HTTP operations
the page uses (/api/suite/ops/*), with the real session and CSRF. Prints JSON evidence.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

import httpx

CSRF_COOKIE = "dw_csrf"
CSRF_HEADER = "X-Synwevo-CSRF"


def main(base: str, email: str, password: str, external: str) -> dict:
    out = {"base": base, "external": external, "steps": []}
    c = httpx.Client(base_url=base, timeout=60.0, follow_redirects=False)
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    out["steps"].append({"step": "login", "status": r.status_code})

    def op(name, body, expect=200):
        resp = c.post(f"/api/suite/ops/{name}", json={"input": body}, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})
        assert resp.status_code == expect, (name, resp.status_code, resp.text)
        return resp.json()["output"] if expect == 200 else resp.json()

    policy = op("agent.mcp.policy.get", {})
    out["steps"].append({"step": "policy", "outbound_enabled": policy["outbound_enabled"], "revision": policy["revision"]})
    existing = [x for x in op("agent.mcp.connection.list", {})["items"] if x["url"] == external]
    if existing:
        created = existing[0]
    else:
        created = op("agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather", "url": external,
                                                      "description": "fixture external MCP server on loopback"})
    out["steps"].append({"step": "connection.create", "id": created["id"], "slug": created["slug"], "health": created["health"]})
    tested = op("agent.mcp.connection.test", {"connection_id": created["id"]})
    out["steps"].append({"step": "connection.test", "health": tested["health"], "protocol_version": tested["protocol_version"],
                         "server": tested["server_info"], "tools": [t["name"] for t in tested["tools"]],
                         "resources": [r["uri"] for r in tested["resources"]], "prompts": [p["name"] for p in tested["prompts"]],
                         "grants": {t["name"]: t["grant"]["mode"] for t in tested["tools"]}})
    op("agent.mcp.grant.set", {"connection_id": created["id"], "tool": "forecast", "mode": "auto"})
    binding = op("agent.mcp.binding.set", {"connection_id": created["id"], "enabled": True, "resources_enabled": True})
    out["steps"].append({"step": "binding", "alias": binding["alias"]})
    tools = op("agent.mcp.binding.list", {})["tools"]
    out["steps"].append({"step": "effective_tools", "tools": [(t["tool"], t["mode"]) for t in tools]})

    def run_turn(text):
        conv = op("agent.conversation.create", {})["id"]
        turn = op("agent.turn.start", {"conversation_id": conv, "text": text})
        return conv, turn["turn_id"]

    def wait(conv, states, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            full = op("agent.conversation.get", {"conversation_id": conv})
            turns = [m["turn"] for m in full["messages"] if m.get("turn")]
            if turns and turns[-1]["status"] in states:
                return full
            time.sleep(0.3)
        raise AssertionError(f"turn did not settle: {turns[-1] if turns else None}")

    conv, turn_id = run_turn("明天香港天气如何？")
    full = wait(conv, ("succeeded", "failed"))
    calls = [tc for m in full["messages"] for tc in m["tool_calls"]]
    out["steps"].append({"step": "turn forecast (read-only, auto)", "turn_status": full["messages"][-1]["turn"]["status"],
                         "assistant_text": full["messages"][-1]["body"],
                         "tool_calls": [{"operation": tc["operation"], "status": tc["status"], "mcp": tc["mcp"],
                                         "result_text": (tc["result"] or {}).get("text") if isinstance(tc.get("result"), dict) else None}
                                        for tc in calls]})
    conv2, turn2 = run_turn("记一条笔记：周五与设计组对齐 MCP 集成中心的交互。")
    full2 = wait(conv2, ("waiting_confirmation", "succeeded", "failed"))
    pending = [tc for m in full2["messages"] for tc in m["tool_calls"]][0]
    out["steps"].append({"step": "turn add_note (confirm)", "turn_status": full2["messages"][-1]["turn"]["status"],
                         "card": {"operation": pending["operation"], "status": pending["status"], "input": pending["input"],
                                  "needs_confirmation": pending["needs_confirmation"]}})
    op("agent.tool_call.confirm", {"tool_call_id": pending["id"], "confirmation": pending["confirmation"]})
    full3 = wait(conv2, ("succeeded", "failed"))
    done = [tc for m in full3["messages"] for tc in m["tool_calls"]][0]
    out["steps"].append({"step": "after confirmation", "turn_status": full3["messages"][-1]["turn"]["status"],
                         "tool_call": {"status": done["status"], "mcp": done["mcp"],
                                       "result_text": (done["result"] or {}).get("text") if isinstance(done.get("result"), dict) else None},
                         "assistant_text": full3["messages"][-1]["body"]})
    resource = op("agent.mcp.resource.read_page", {"connection_id": created["id"], "uri": "notes://all"})
    out["steps"].append({"step": "resource read on the page", "text": resource["text"], "bytes": resource["bytes"]})
    prompt = op("agent.mcp.prompt.render", {"connection_id": created["id"], "name": "summarise", "arguments": {"style": "two-line"}})
    out["steps"].append({"step": "prompt render on the page", "text": prompt["text"]})
    log = op("agent.mcp.calls", {"connection_id": created["id"], "limit": 20})["items"]
    out["steps"].append({"step": "call log", "items": [{"origin": i["origin"], "kind": i["kind"], "name": i["name"], "status": i["status"],
                                                        "duration_ms": i["duration_ms"]} for i in log]})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--external", required=True)
    a = ap.parse_args()
    json.dump(main(a.base, a.email, a.password, a.external), sys.stdout, ensure_ascii=False, indent=2)
    print()
