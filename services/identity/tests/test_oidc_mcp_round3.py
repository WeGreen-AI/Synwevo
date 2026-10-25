"""The account service as the MCP server's authorization server, on the two strings clients compare:

* the issuer: the metadata's `issuer` and the `iss` of every authorization response are one string
  (RFC 8414 §3.3, RFC 9207 §2.4: simple string comparison), and the service does not start with an
  issuer a client's URL parser would render differently from how it is written;
* the resource: a token request that names the MCP server with an uppercase scheme or host, a
  default port or a trailing slash names the same server (MCP authorization, "Canonical Server
  URI": SHOULD be accepted), and the token's audience is the canonical URI, the one the MCP server
  checks (RFC 8707); anything else stays `invalid_target`.

FIXTURE: one operator-registered MCP client; everything else is the real sign-in, consent and token
endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter

from synwevo_core import oidc
from synwevo_core.identity import Identity
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
ISSUER = "https://accounts.synwevo.test"
WORKPLACE = "https://workplace.synwevo.test"
MCP = WORKPLACE + "/api/suite/v1/mcp"
LOCAL_REDIRECT = "http://127.0.0.1:3000/callback"
# How a metadata client renders an issuer (the MCP SDK's OAuthMetadata: AnyHttpUrl, empty path kept)
_AS_A_CLIENT_READS_IT = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True))


def start(tmp_path, monkeypatch, issuer: str = ISSUER):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", issuer)
    monkeypatch.setenv("SYNWEVO_APP_URL", WORKPLACE)
    monkeypatch.delenv("SYNWEVO_MCP_RESOURCE", raising=False)
    from synwevo_identity.app import create_app
    return create_app(str(tmp_path / "d"), start_jobs=False)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    api = start(tmp_path, monkeypatch)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", PW, status="active")
    workspace = ident.workspaces_for(alice["id"])[0]["id"]
    oidc.Clients(store).register(client_id="mcp-pre", app_id="platform", name="Pre-registered MCP client",   # FIXTURE
                                 redirect_uris=[LOCAL_REDIRECT], scopes="openid offline_access forms.read",
                                 audiences=[oidc.mcp_resource()])
    return {"api": api, "store": store, "alice": alice, "workspace": workspace}


def login(env) -> TestClient:
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return c


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def params(location: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))


def code_for(env, c, scope: str = "openid offline_access forms.read") -> tuple[str, str, dict]:
    """A code through the real authorize and consent endpoints; also every `iss` seen on the way."""
    verifier, challenge = pkce()
    q = {"client_id": "mcp-pre", "redirect_uri": LOCAL_REDIRECT, "response_type": "code", "scope": scope, "state": "s1",
         "code_challenge": challenge, "code_challenge_method": "S256", "workspace_id": env["workspace"]}
    parked = c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False)
    assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent"), parked.headers
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
    done = c.post(f"/api/auth/oauth-consent/{rid}/approve", json={},
                  headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})
    assert done.status_code == 200, done.text
    back = params(done.json()["redirect"])
    return back["code"], verifier, back


def exchange(c, code: str, verifier: str, resource: str | list[str]):
    return c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": LOCAL_REDIRECT,
                                       "client_id": "mcp-pre", "code_verifier": verifier, "resource": resource})


def audience_of(env, access_token: str) -> str:
    return env["store"].one("SELECT audience FROM oauth_tokens WHERE access_hash=?",
                            (hashlib.sha256(access_token.encode()).hexdigest(),))["audience"]


# ---------------------------------------------------------------------- the issuer (P2)
def test_the_metadata_issuer_and_every_authorization_response_iss_are_one_string(env):
    c = login(env)
    doc = c.get("/.well-known/oauth-authorization-server").json()
    assert doc["issuer"] == c.get("/.well-known/openid-configuration").json()["issuer"] == oidc.issuer() == ISSUER
    # what a metadata client makes of it is the string itself, so its RFC 9207 comparison can hold
    assert str(_AS_A_CLIENT_READS_IT.validate_python(doc["issuer"])) == doc["issuer"]
    refused = c.get("/oidc/authorize?" + urllib.parse.urlencode(
        {"client_id": "mcp-pre", "redirect_uri": LOCAL_REDIRECT, "response_type": "token", "state": "s1"}),
        follow_redirects=False)
    assert params(refused.headers["location"])["iss"] == doc["issuer"]
    _, _, back = code_for(env, c)
    assert back["iss"] == doc["issuer"]


@pytest.mark.parametrize("written, canonical", [
    ("https://Accounts.Synwevo.test", "https://accounts.synwevo.test"),
    ("https://accounts.synwevo.test:443", "https://accounts.synwevo.test"),
    ("HTTPS://ACCOUNTS.SYNWEVO.TEST", "https://accounts.synwevo.test"),
    ("http://accounts.localhost:80", "http://accounts.localhost"),
    ("https://accounts.synwevo.test#top", "https://accounts.synwevo.test"),
])
def test_the_account_service_does_not_start_with_an_issuer_clients_would_rewrite(tmp_path, monkeypatch, written, canonical):
    """Refused with the form to write instead: rewriting it silently would change the `iss` that
    tokens already issued and every relying party's own setting carry."""
    with pytest.raises(RuntimeError) as refused:
        start(tmp_path, monkeypatch, issuer=written)
    message = str(refused.value)
    assert "SYNWEVO_ISSUER" in message and repr(canonical) in message


def test_an_issuer_that_is_not_an_address_is_refused_by_name(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError) as refused:
        start(tmp_path, monkeypatch, issuer="accounts.synwevo.test")
    assert "SYNWEVO_ISSUER" in str(refused.value) and "http(s)" in str(refused.value)


@pytest.mark.parametrize("written", ["https://accounts.synwevo.test/", "http://accounts.localhost:8770",
                                     "https://accounts.synwevo.test/tenant"])
def test_a_canonical_issuer_starts_as_written(tmp_path, monkeypatch, written):
    start(tmp_path, monkeypatch, issuer=written)
    assert oidc.discovery()["issuer"] == written.rstrip("/")


# ---------------------------------------------------------------------- the resource (P6)
@pytest.mark.parametrize("resource", [
    "https://Workplace.Synwevo.test/api/suite/v1/mcp",
    "HTTPS://WORKPLACE.SYNWEVO.TEST/api/suite/v1/mcp",
    "https://workplace.synwevo.test:443/api/suite/v1/mcp",
    "https://workplace.synwevo.test/api/suite/v1/mcp/",
    MCP,
])
def test_the_mcp_server_named_in_another_spelling_gets_a_token_for_its_canonical_uri(env, resource):
    c = login(env)
    code, verifier, _ = code_for(env, c)
    issued = exchange(c, code, verifier, resource)
    assert issued.status_code == 200, issued.text
    assert audience_of(env, issued.json()["access_token"]) == oidc.mcp_resource() == MCP
    # the refresh grant reads the resource the same way, and the audience stays canonical
    again = c.post("/oidc/token", data={"grant_type": "refresh_token", "client_id": "mcp-pre",
                                        "refresh_token": issued.json()["refresh_token"], "resource": resource})
    assert again.status_code == 200, again.text
    assert audience_of(env, again.json()["access_token"]) == MCP


def test_two_spellings_of_the_mcp_server_are_one_resource(env):
    c = login(env)
    code, verifier, _ = code_for(env, c)
    issued = exchange(c, code, verifier, [MCP, "https://Workplace.Synwevo.test:443/api/suite/v1/mcp/"])
    assert issued.status_code == 200, issued.text
    assert audience_of(env, issued.json()["access_token"]) == MCP


@pytest.mark.parametrize("resource", [
    "https://workplace.synwevo.test/API/suite/v1/mcp",          # the path is case-sensitive: another resource
    "https://workplace.synwevo.test/api/suite/v1/mcp/tools",
    "https://workplace.synwevo.test:8443/api/suite/v1/mcp",
    "https://other.synwevo.test/api/suite/v1/mcp",
    MCP + "#tools",                                             # a fragment: no resource indicator (RFC 8707 §2)
    MCP + "?x=1",
    "urn:synwevo:platform:suite",                               # not registered for this client
])
def test_any_other_resource_is_still_an_invalid_target(env, resource):
    c = login(env)
    code, verifier, _ = code_for(env, c)
    refused = exchange(c, code, verifier, resource)
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_target", refused.text
