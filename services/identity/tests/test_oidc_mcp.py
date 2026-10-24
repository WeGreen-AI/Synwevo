"""The account service as the authorization server MCP clients use (docs/platform/mcp.md §3.3):
OAuth 2.0 Authorization Server Metadata next to OpenID Connect Discovery, `iss` on authorization
responses (RFC 9207), Client ID Metadata Documents (policy-gated), the consent page's workspace
choice for clients that cannot name one, and tokens minted for the MCP server's own audience.

FIXTURE: the metadata document an MCP client would host at its https client id is injected in place
of the network fetch (`cimd.fetch_document`); everything else is the real sign-in, consent and token
endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import cimd, oidc
from synwevo_core.identity import Identity
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store

from .oauth_consent_helper import through_consent

PW = "a-sufficiently-long-passphrase"
WORKPLACE = "http://workplace.localhost:8769"
MCP = WORKPLACE + "/api/suite/v1/mcp"
CIMD_ID = "https://client.example.test/oauth/client.json"
LOCAL_REDIRECT = "http://127.0.0.1:3000/callback"
DOCUMENT = {"client_id": CIMD_ID, "client_name": "Example MCP Client", "client_uri": "https://client.example.test",
            "redirect_uris": [LOCAL_REDIRECT, "http://localhost:3000/callback"], "grant_types": ["authorization_code"],
            "response_types": ["code"], "token_endpoint_auth_method": "none"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://accounts.localhost:8770")
    monkeypatch.setenv("SYNWEVO_APP_URL", WORKPLACE)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.com", PW, status="active")
    first = ident.workspaces_for(alice["id"])[0]["id"]
    second = ident.create_workspace("Second workspace", owner_id=alice["id"])["id"]
    oidc.Clients(store).register(client_id="mcp-pre", app_id="platform", name="Pre-registered MCP client",
                                 redirect_uris=[LOCAL_REDIRECT], scopes="openid offline_access forms.read forms.write",
                                 audiences=[oidc.mcp_resource()])
    return {"api": api, "store": store, "ident": ident, "alice": alice, "first": first, "second": second}


def allow_cimd(env, domains=()):
    with env["store"].tx():
        current = McpPolicy(env["store"]).read()
        McpPolicy(env["store"]).append({**current["spec"], "client_registration": "cimd_and_preregistered",
                                        "cimd_allowed_domains": list(domains)},
                                       expected_revision=current["revision"], actor_id=env["alice"]["id"], reason="fixture")


def login(env) -> TestClient:
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return c


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorize(c, client_id: str, scope: str, *, workspace_id: str | None = None, redirect: str = LOCAL_REDIRECT, **extra):
    verifier, challenge = pkce()
    q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": scope, "state": "s1",
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP, **extra}
    if workspace_id:
        q["workspace_id"] = workspace_id
    return c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False), verifier


def params(location: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))


def consent_request(c, parked):
    rid = urllib.parse.parse_qs(urllib.parse.urlparse(parked.headers["location"]).query)["request"][0]
    return rid, c.get(f"/api/auth/oauth-consent/{rid}").json()


def approve(c, rid: str, **body):
    return c.post(f"/api/auth/oauth-consent/{rid}/approve", json=body, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def exchange(c, code: str, verifier: str, client_id: str, redirect: str = LOCAL_REDIRECT):
    return c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                       "client_id": client_id, "code_verifier": verifier, "resource": MCP})


def test_authorization_server_metadata_is_discovery_with_the_mcp_facts(env):
    c = TestClient(env["api"])
    oauth = c.get("/.well-known/oauth-authorization-server")
    oidc_doc = c.get("/.well-known/openid-configuration").json()
    assert oauth.status_code == 200 and oauth.json() == oidc_doc
    doc = oauth.json()
    assert doc["issuer"] == "http://accounts.localhost:8770" and doc["code_challenge_methods_supported"] == ["S256"]
    assert doc["authorization_response_iss_parameter_supported"] is True
    assert doc["client_id_metadata_document_supported"] is False          # the default policy: pre-registered clients only
    assert "registration_endpoint" not in doc                              # dynamic client registration is not offered
    allow_cimd(env)
    assert c.get("/.well-known/oauth-authorization-server").json()["client_id_metadata_document_supported"] is True


def test_every_authorization_response_names_its_issuer(env):
    c = login(env)
    ok, _ = authorize(c, "mcp-pre", "openid")                              # identity only: no consent, straight back
    assert ok.status_code == 303 and params(ok.headers["location"])["iss"] == "http://accounts.localhost:8770"
    bad, _ = authorize(c, "mcp-pre", "openid", response_type="token")
    got = params(bad.headers["location"])
    assert got["error"] == "unsupported_response_type" and got["iss"] == "http://accounts.localhost:8770"


def test_a_platform_client_without_a_workspace_lets_the_person_choose_once(env):
    c = login(env)
    parked, verifier = authorize(c, "mcp-pre", "openid offline_access forms.read")
    assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent")
    rid, shown = consent_request(c, parked)
    assert shown["workspace"] is None
    assert {w["id"] for w in shown["workspace_choices"]} == {env["first"], env["second"]}
    assert all(w["consented"] is False for w in shown["workspace_choices"])
    assert shown["client"]["registration"] == "operator"
    refused = approve(c, rid)
    assert refused.status_code == 400 and refused.json()["error"] == "workspace_required"
    done = approve(c, rid, workspace_id=env["second"])
    assert done.status_code == 200, done.text
    back = params(done.json()["redirect"])
    assert back["iss"] == "http://accounts.localhost:8770" and back["state"] == "s1"
    issued = exchange(c, back["code"], verifier, "mcp-pre")
    assert issued.status_code == 200, issued.text
    row = env["store"].one("SELECT audience, workspace_id, scope FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")
    assert row["audience"] == MCP and row["workspace_id"] == env["second"]
    assert set(row["scope"].split()) == {"openid", "offline_access", "forms.read"}
    # the one consented workspace decides the next time; nothing is asked again
    again, verifier2 = authorize(c, "mcp-pre", "openid offline_access forms.read")
    assert again.status_code == 303 and "code" in params(again.headers["location"])
    issued2 = exchange(c, params(again.headers["location"])["code"], verifier2, "mcp-pre")
    assert env["store"].one("SELECT workspace_id FROM oauth_tokens WHERE access_hash=?",
                            (hashlib.sha256(issued2.json()["access_token"].encode()).hexdigest(),))["workspace_id"] == env["second"]
    # a wider scope is asked for: the page shows the choice again, with the consented workspace marked
    wider, _ = authorize(c, "mcp-pre", "openid offline_access forms.read forms.write")
    _, shown2 = consent_request(c, wider)
    assert {w["id"]: w["consented"] for w in shown2["workspace_choices"]} == {env["first"]: False, env["second"]: True}
    # a request that names the workspace offers no choice and refuses another one
    named, _ = authorize(c, "mcp-pre", "openid offline_access forms.write", workspace_id=env["first"])
    rid3, shown3 = consent_request(c, named)
    assert shown3["workspace"]["id"] == env["first"] and shown3["workspace_choices"] is None
    assert approve(c, rid3, workspace_id=env["second"]).status_code == 400
    assert approve(c, rid3).status_code == 200


def test_a_client_id_metadata_document_registers_a_public_client_only_under_policy(env, monkeypatch):
    fetched = []

    def fetch(client_id):
        fetched.append(client_id)
        return dict(DOCUMENT)
    monkeypatch.setattr(cimd, "fetch_document", fetch)
    c = login(env)
    refused, _ = authorize(c, CIMD_ID, "openid forms.read")
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_client" and fetched == []
    allow_cimd(env, domains=[".other.example"])
    refused, _ = authorize(c, CIMD_ID, "openid forms.read")
    assert refused.status_code == 400 and fetched == []                    # another domain's documents are not even fetched
    allow_cimd(env, domains=[".example.test"])
    parked, verifier = authorize(c, CIMD_ID, "openid forms.read", workspace_id=env["first"])
    assert parked.status_code == 303 and parked.headers["location"].startswith("/oauth/consent"), parked.text
    assert fetched == [CIMD_ID]
    registered = oidc.Clients(env["store"]).get(CIMD_ID)
    assert registered["registration"] == "cimd" and registered["name"] == "Example MCP Client"
    assert registered["token_endpoint_auth_method"] == "none" and registered["audiences"] == [MCP]
    assert set(registered["redirect_uris"]) == set(DOCUMENT["redirect_uris"])
    rid, shown = consent_request(c, parked)
    assert shown["client"] == {"id": CIMD_ID, "name": "Example MCP Client", "app_id": "platform", "registration": "cimd",
                               "client_uri": "https://client.example.test"}
    assert shown["scopes"] == ["forms.read"] and shown["workspace"]["id"] == env["first"]
    done = approve(c, rid)
    issued = exchange(c, params(done.json()["redirect"])["code"], verifier, CIMD_ID)
    assert issued.status_code == 200, issued.text
    assert env["store"].one("SELECT audience FROM oauth_tokens ORDER BY issued_at DESC LIMIT 1")["audience"] == MCP
    # the document is not fetched again while fresh
    again, _ = authorize(c, CIMD_ID, "openid forms.read", workspace_id=env["first"])
    assert again.status_code == 303 and fetched == [CIMD_ID]
    # a redirect uri the document did not list is refused before anything is redirected to
    elsewhere, _ = authorize(c, CIMD_ID, "openid forms.read", redirect="https://evil.example.test/cb")
    assert elsewhere.status_code == 400 and elsewhere.json()["error"] == "invalid_redirect_uri"


@pytest.mark.parametrize("broken, why", [
    ({"client_id": "https://client.example.test/other.json"}, "client_id mismatch"),
    ({"redirect_uris": ["http://attacker.example/callback"]}, "plain http to a non-local host"),
    ({"token_endpoint_auth_method": "client_secret_basic"}, "a confidential client cannot be self-registered"),
    ({"redirect_uris": []}, "no redirect uris"),
    ({"grant_types": ["client_credentials"]}, "no authorization code grant"),
])
def test_documents_that_break_the_rules_register_nothing(env, monkeypatch, broken, why):
    monkeypatch.setattr(cimd, "fetch_document", lambda client_id: {**DOCUMENT, **broken})
    allow_cimd(env)
    c = login(env)
    refused, _ = authorize(c, CIMD_ID, "openid forms.read")
    assert refused.status_code == 400 and refused.json()["error"] == "invalid_client", why
    assert oidc.Clients(env["store"]).get_any(CIMD_ID) is None


def test_the_document_fetch_goes_only_to_public_https_addresses(env):
    allow_cimd(env)
    with pytest.raises(Exception) as err:
        cimd.fetch_document("https://127.0.0.1/client.json")
    assert getattr(err.value, "code", None) == "invalid_client"
    assert not cimd.is_client_id_url("http://client.example.test/client.json")
    assert not cimd.is_client_id_url("https://client.example.test")             # a path is required
    assert not cimd.is_client_id_url("https://client.example.test/c.json?x=1")
    assert cimd.is_client_id_url(CIMD_ID)
