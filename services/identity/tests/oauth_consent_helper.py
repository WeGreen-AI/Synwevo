"""TEST HELPER (API-4): what a person's browser does when /oidc/authorize asks for consent.

Since consent became its own step, an authorization request for API scopes or offline access
answers 303 → /oauth/consent?request=… instead of → the client with a code. Tests whose subject
is something else (refresh rotation, logout, delegation) still need a code; this approves the
parked request as the signed-in person — through the real consent endpoint, with CSRF — and
returns the redirect the consent page would follow. Responses that need no consent pass through.
"""
from __future__ import annotations

import urllib.parse

import httpx

from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER


def through_consent(client, resp, *, decision: str = "approve"):
    location = resp.headers.get("location", "")
    if resp.status_code != 303 or not location.startswith("/oauth/consent"):
        return resp
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)["request"][0]
    decided = client.post(f"/api/auth/oauth-consent/{rid}/{decision}",
                          headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})
    assert decided.status_code == 200, decided.text
    return httpx.Response(303, headers={"location": decided.json()["redirect"]})
