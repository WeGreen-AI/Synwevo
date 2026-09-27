"""HTTP security headers on every response of every role (OWASP ASVS 4.0.3 V14.4).

Pinned: nosniff, a Content-Security-Policy with `frame-ancestors 'self'`, `script-src 'self'` and
`object-src 'none'`, COOP, a Permissions-Policy, Referrer-Policy and X-Frame-Options on HTML, JSON,
404 and gate-refused responses alike; HSTS only when the request came over TLS (directly or as the
gateway reports it) and never on plain HTTP; `SYNWEVO_CSP` replaces the policy and `off` removes it;
HTML carries a charset (14.4.1).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_server.app import create_app

ORIGINS = {"accounts": "http://accounts.localhost:8770", "portal": "http://portal.localhost:8769", "app": "http://docweave.localhost:8765",
           "messages": "http://message.localhost:8768", "all": "http://docweave.localhost:8765"}


@pytest.fixture()
def make(tmp_path, monkeypatch):
    def _make(role: str, **extra_env: str) -> TestClient:
        monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / role))
        monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
        monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
        monkeypatch.setenv("SYNWEVO_ISSUER", ORIGINS["accounts"])
        monkeypatch.setenv("SYNWEVO_APP_URL", ORIGINS["app"])
        monkeypatch.setenv("SYNWEVO_PORTAL_URL", ORIGINS["portal"])
        monkeypatch.delenv("SYNWEVO_CSP", raising=False)
        for k, v in extra_env.items():
            monkeypatch.setenv(k, v)
        return TestClient(create_app(str(tmp_path / role), role=role, start_jobs=False), base_url=ORIGINS[role])
    return _make


EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "SAMEORIGIN",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-site",
}


def _assert_standard(r):
    for name, value in EXPECTED.items():
        assert r.headers.get(name) == value, (name, r.headers.get(name), r.status_code)
    csp = r.headers.get("content-security-policy", "")
    for directive in ("default-src 'self'", "script-src 'self'", "frame-ancestors 'self'", "object-src 'none'", "base-uri 'self'", "form-action 'self'"):
        assert directive in csp, (directive, csp)
    assert "unsafe-eval" not in csp and "script-src 'self' 'unsafe-inline'" not in csp
    assert "camera=()" in r.headers.get("permissions-policy", "")
    assert "x-request-id" in r.headers


def test_only_the_messages_origins_may_use_the_microphone(make):
    """Voice messages are recorded on Synwevo Message (and the single-process deployment, which
    serves its shell); the account service, the portal and Docs keep the microphone closed."""
    for role, expect in (("accounts", "microphone=()"), ("portal", "microphone=()"), ("app", "microphone=()"),
                         ("messages", "microphone=(self)"), ("all", "microphone=(self)")):
        policy = make(role).get("/healthz").headers.get("permissions-policy", "")
        assert expect in policy and "camera=()" in policy, (role, policy)


@pytest.mark.parametrize("role", ["accounts", "portal", "app", "messages"])
def test_every_kind_of_response_carries_the_headers(make, role):
    c = make(role)
    seen = []
    for path in ("/api/auth/state", "/api/auth/me", "/no/such/page", "/api/nothing/here", "/healthz"):
        r = c.get(path)
        seen.append((path, r.status_code))
        _assert_standard(r)
    assert {s for _, s in seen} >= {200, 401} or {s for _, s in seen} >= {200, 404}
    # a shell page, when the front-end is built
    r = c.get("/login" if role == "accounts" else "/")
    if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
        _assert_standard(r)
        assert "charset=utf-8" in r.headers["content-type"].lower()


def test_hsts_only_when_the_request_came_over_tls(make):
    c = make("accounts")
    assert "strict-transport-security" not in c.get("/api/auth/state").headers
    forwarded = c.get("/api/auth/state", headers={"X-Forwarded-Proto": "https"})
    assert forwarded.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    assert "strict-transport-security" not in c.get("/api/auth/state", headers={"X-Forwarded-Proto": "http"}).headers
    tls = TestClient(c.app, base_url="https://accounts.localhost")
    assert tls.get("/api/auth/state").headers["strict-transport-security"].startswith("max-age=")


def test_the_operator_can_replace_or_drop_the_csp_without_a_build(make):
    custom = make("accounts", SYNWEVO_CSP="default-src 'self'; img-src *")
    assert custom.get("/api/auth/state").headers["content-security-policy"] == "default-src 'self'; img-src *"
    off = make("portal", SYNWEVO_CSP="off")
    r = off.get("/api/auth/state")
    assert "content-security-policy" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"      # the rest stays


def test_a_route_that_sets_its_own_header_keeps_it(make):
    """setdefault, not overwrite: a download that needs a different frame policy or cache rule
    is not stomped on by the gate."""
    c = make("accounts")
    r = c.get("/api/auth/state")
    assert r.headers["cache-control"] == "no-store"


def test_behind_cloudflare_only_its_own_beacon_is_allowed(make):
    """The edge injects static.cloudflareinsights.com/beacon.min.js into HTML when Web Analytics is
    on; that script (and its connect target) is allowed only when CF-Ray says the request came
    through Cloudflare, and nothing else widens."""
    c = make("portal")
    strict = c.get("/api/auth/state").headers["content-security-policy"]
    assert "cloudflareinsights" not in strict
    edge = c.get("/api/auth/state", headers={"CF-Ray": "8f1c2d3e4a5b6c7d-HKG"}).headers["content-security-policy"]
    assert "script-src 'self' https://static.cloudflareinsights.com;" in edge
    assert "connect-src 'self' https://cloudflareinsights.com;" in edge
    assert edge.replace(" https://static.cloudflareinsights.com", "").replace(" https://cloudflareinsights.com", "") == strict
    custom = make("accounts", SYNWEVO_CSP="default-src 'self'")
    assert custom.get("/api/auth/state", headers={"CF-Ray": "x"}).headers["content-security-policy"] == "default-src 'self'"
