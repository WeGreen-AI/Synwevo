#!/usr/bin/env python3
"""A local OpenID Connect provider **test double** for exercising external sign-in in a browser.

This is not a provider and proves nothing about Google or Microsoft. It exists so the full
redirect chain — Synwevo's start route, the provider's consent page, the callback, the token
exchange, id_token verification, account matching, linking and the second factor — can be
walked through in a real browser on an isolated instance without any external credentials.

It signs real RS256 id_tokens with a key generated at start-up, serves discovery and JWKS, and
shows a consent page where the tester types the subject, e-mail and whether the e-mail counts as
verified. Nothing is persisted; codes live in memory until exchanged.

Run (isolated verification instance; never against a real data directory):

    python services/identity/scripts/idp_double.py --port 31780 \
        --client-id synwevo-local --client-secret local-secret-0123456789

Then configure the account service with
    SYNWEVO_IDP_PROVIDERS=testidp
    SYNWEVO_IDP_TESTIDP_ISSUER=http://idp.localhost:31780
    SYNWEVO_IDP_TESTIDP_CLIENT_ID=synwevo-local
    SYNWEVO_IDP_TESTIDP_CLIENT_SECRET=local-secret-0123456789
    SYNWEVO_IDP_TESTIDP_LABEL="Test IdP"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import secrets
import time
import urllib.parse

from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


def build(issuer: str, client_id: str, client_secret: str) -> FastAPI:
    key = JsonWebKey.generate_key("RSA", 2048, {"kid": "double-1", "use": "sig"}, is_private=True)
    codes: dict[str, dict] = {}
    app = FastAPI(title="OpenID provider test double")

    @app.get("/.well-known/openid-configuration")
    def discovery():
        return {"issuer": issuer, "authorization_endpoint": f"{issuer}/authorize",
                "token_endpoint": f"{issuer}/token", "jwks_uri": f"{issuer}/jwks",
                "response_types_supported": ["code"], "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "code_challenge_methods_supported": ["S256"], "scopes_supported": ["openid", "email", "profile"]}

    @app.get("/jwks")
    def jwks():
        return {"keys": [key.as_dict(is_private=False)]}

    @app.get("/authorize", response_class=HTMLResponse)
    def authorize(request: Request):
        q = request.query_params
        if q.get("client_id") != client_id or q.get("response_type") != "code" or q.get("code_challenge_method") != "S256":
            return HTMLResponse("<p>bad request</p>", status_code=400)
        hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(q.get(k) or "")}">'
                         for k in ("redirect_uri", "state", "nonce", "code_challenge"))
        return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Test IdP · consent</title>
<style>body{{font-family:sans-serif;max-width:420px;margin:60px auto;padding:0 16px}}label{{display:block;margin:12px 0 4px}}input[type=text],input[type=email]{{width:100%;padding:8px}}button{{margin-top:16px;padding:8px 16px}}</style></head>
<body><h1>Test IdP (double)</h1><p>This page stands in for an external provider's sign-in. Nothing here is real.</p>
<form method="post" action="/consent">{hidden}
<label>Subject (stable id)</label><input type="text" name="subject" value="double-user-1" required>
<label>E-mail</label><input type="email" name="email" value="double.user@example.test" required>
<label>Display name</label><input type="text" name="name" value="Double User">
<label><input type="checkbox" name="verified" checked> E-mail is verified at the provider</label>
<button type="submit">Continue to Synwevo</button> <a href="{html.escape(q.get('redirect_uri') or '/')}?error=access_denied&state={html.escape(q.get('state') or '')}">Cancel</a>
</form></body></html>"""

    @app.post("/consent")
    def consent(redirect_uri: str = Form(...), state: str = Form(...), nonce: str = Form(...), code_challenge: str = Form(...),
                subject: str = Form(...), email: str = Form(...), name: str = Form(""), verified: str | None = Form(None)):
        code = secrets.token_urlsafe(16)
        codes[code] = {"subject": subject, "email": email, "email_verified": verified is not None, "name": name,
                       "nonce": nonce, "code_challenge": code_challenge, "redirect_uri": redirect_uri, "at": time.time()}
        sep = "&" if urllib.parse.urlsplit(redirect_uri).query else "?"
        return RedirectResponse(f"{redirect_uri}{sep}{urllib.parse.urlencode({'code': code, 'state': state})}", status_code=303)

    @app.post("/token")
    def token(grant_type: str = Form(...), code: str = Form(...), redirect_uri: str = Form(...),
              client_id_: str = Form(..., alias="client_id"), client_secret_: str = Form(..., alias="client_secret"),
              code_verifier: str = Form("")):
        grant = codes.pop(code, None)
        if (not grant or grant_type != "authorization_code" or client_id_ != client_id or client_secret_ != client_secret
                or redirect_uri != grant["redirect_uri"] or time.time() - grant["at"] > 300):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        if challenge != grant["code_challenge"]:
            return JSONResponse({"error": "invalid_grant", "error_description": "pkce"}, status_code=400)
        ts = int(time.time())
        claims = {"iss": issuer, "aud": client_id, "sub": grant["subject"], "nonce": grant["nonce"], "iat": ts,
                  "exp": ts + 300, "email": grant["email"], "email_verified": grant["email_verified"], "name": grant["name"]}
        raw = jwt.encode({"alg": "RS256", "kid": "double-1"}, claims, key).decode()
        return {"access_token": secrets.token_urlsafe(8), "token_type": "Bearer", "expires_in": 300, "id_token": raw}

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=31780)
    ap.add_argument("--issuer", help="defaults to http://idp.localhost:<port>")
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    args = ap.parse_args()
    issuer = (args.issuer or f"http://idp.localhost:{args.port}").rstrip("/")
    import uvicorn
    uvicorn.run(build(issuer, args.client_id, args.client_secret), host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
