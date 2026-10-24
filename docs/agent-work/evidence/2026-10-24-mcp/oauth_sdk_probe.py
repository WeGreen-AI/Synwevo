#!/usr/bin/env python
"""Verification A, full OAuth: the official SDK's OAuthClientProvider (mcp 2.3.0) against a running Synwevo.

The SDK does everything a real MCP client does — the 401 challenge, RFC 9728 protected resource
metadata, RFC 8414 authorization server metadata, PKCE S256, the RFC 8707 `resource`, the RFC 9207
`iss` check, the code exchange, then MCP calls with the token. The only stand-in is the person's
browser: `open_browser` performs what a browser would (sign in with the fixture account's password,
approve the consent page, choose the workspace) with plain HTTP against the same server, and hands the
redirect back to the SDK. The client is the instance's pre-registered `mcp-inspector`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.parse

import httpx
import httpx2
from mcp import Client
from mcp.client.auth import AuthorizationCodeResult, OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata

REDIRECT = "http://127.0.0.1:6274/oauth/callback"


class MemoryTokens:
    def __init__(self, client):
        self.tokens = None
        self.client = client

    async def get_tokens(self): return self.tokens
    async def set_tokens(self, tokens): self.tokens = tokens
    async def get_client_info(self): return self.client
    async def set_client_info(self, info): self.client = info


def main(base: str, email: str, password: str, workspace: str) -> dict:
    evidence: dict = {"base": base, "steps": []}
    pending: dict = {}

    async def open_browser(url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        q = dict(urllib.parse.parse_qsl(parsed.query))
        evidence["steps"].append({"step": "authorization request built by the SDK",
                                  "endpoint": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
                                  "params": {k: (v if k not in ("code_challenge", "state") else "<present>") for k, v in q.items()}})
        browser = httpx.Client(base_url=base, follow_redirects=False, timeout=30.0)
        r = browser.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        parked = browser.get(url.replace(base, ""))
        assert parked.status_code == 303, parked.text
        location = parked.headers["location"]
        if location.startswith("/oauth/consent"):
            rid = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["request"][0]
            shown = browser.get(f"/api/auth/oauth-consent/{rid}").json()
            evidence["steps"].append({"step": "consent page", "client": shown["client"], "scopes": shown["scopes"],
                                      "workspace": shown["workspace"],
                                      "workspace_choices": [w["id"] for w in shown["workspace_choices"]] if shown["workspace_choices"] else None})
            body = {"workspace_id": workspace} if shown["workspace_choices"] else {}
            decided = browser.post(f"/api/auth/oauth-consent/{rid}/approve", json=body,
                                   headers={"X-Synwevo-CSRF": browser.cookies.get("dw_csrf") or ""})
            assert decided.status_code == 200, decided.text
            location = decided.json()["redirect"]
        back = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
        if "code" not in back:
            print("REDIRECT WITHOUT CODE:", location, file=sys.stderr)
        evidence["steps"].append({"step": "redirect back to the client", "has_code": "code" in back, "iss": back.get("iss"),
                                  "state_matches": back.get("state") == q.get("state")})
        pending.update(back)

    async def wait_for_callback() -> AuthorizationCodeResult:
        return AuthorizationCodeResult(code=pending["code"], state=pending["state"], iss=pending.get("iss"))

    async def run() -> None:
        endpoint = base.rstrip("/") + "/api/suite/v1/mcp"
        auth = OAuthClientProvider(
            server_url=endpoint,
            client_metadata=OAuthClientMetadata(client_name="SDK probe", redirect_uris=[REDIRECT],
                                                grant_types=["authorization_code", "refresh_token"], response_types=["code"],
                                                token_endpoint_auth_method="none", scope="forms.read forms.write offline_access"),
            storage=MemoryTokens(OAuthClientInformationFull(client_id="mcp-inspector", redirect_uris=[REDIRECT],
                                                           token_endpoint_auth_method="none")),
            redirect_handler=open_browser, callback_handler=wait_for_callback)
        async with httpx2.AsyncClient(auth=auth, timeout=httpx2.Timeout(30.0, read=300.0)) as http:
            async with Client(streamable_http_client(endpoint, http_client=http)) as client:
                evidence["protocol_version"] = client.protocol_version
                who = await client.call_tool("synwevo.whoami", {})
                evidence["steps"].append({"step": "tools/call synwevo.whoami with the SDK-obtained token", "is_error": who.is_error,
                                          "workspace_id": (who.structured_content or {}).get("workspace_id"),
                                          "client_id": (who.structured_content or {}).get("client_id"),
                                          "scopes": (who.structured_content or {}).get("scopes")})
                made = await client.call_tool("forms.form.create", {"title": "Created through the SDK OAuth client", "idempotency_key": "sdk-oauth-1"})
                evidence["steps"].append({"step": "tools/call forms.form.create", "is_error": made.is_error,
                                          "status": (made.structured_content or {}).get("status")})
        stored = await auth.context.storage.get_tokens()
        evidence["token"] = {"scope": stored.scope if stored else None, "has_refresh_token": bool(stored and stored.refresh_token)}

    asyncio.run(run())
    return evidence


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--workspace", required=True)
    a = ap.parse_args()
    json.dump(main(a.base, a.email, a.password, a.workspace), sys.stdout, ensure_ascii=False, indent=2)
    print()
