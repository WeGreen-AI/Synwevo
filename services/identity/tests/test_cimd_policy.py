"""A self-registered MCP client (Client ID Metadata Document, `registration='cimd'`) lives only as long
as the policy that let it register, and only as the document now describes it (docs/platform/mcp.md
§3.3):

* every authorization of a stored self-registration asks the policy again — publishing
  `preregistered_only`, or narrowing `cimd_allowed_domains`, ends the registration's use, not only new
  registrations;
* a registration older than `REFRESH_AFTER_S` is fetched again before it is used, so a callback the
  document dropped is refused;
* the tokens such a client already holds stop working with the policy: the MCP server's gate, the token
  endpoint (code exchange and refresh) and userinfo refuse them, and a parked consent request expires;
* an anonymous request makes the account service fetch and store nothing: it is sent to sign in first;
* the document's `client_uri` is a claim, kept only on the client id's own origin.

FIXTURE: the metadata document an MCP client would host at its https client id is injected in place of
the network fetch (`cimd.fetch_document`); everything else is the real sign-in, consent, token and MCP
gate code, on a data directory under tmp_path.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from synwevo_core import cimd, oidc
from synwevo_core.errors import ServiceError
from synwevo_core.identity import Identity
from synwevo_core.machine import MachineAuth
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
from synwevo_core.store import Store, now

PW = "a-sufficiently-long-passphrase"
WORKPLACE = "http://workplace.localhost:8769"
MCP = WORKPLACE + "/api/suite/v1/mcp"
CIMD_ID = "https://client.example.test/oauth/client.json"
LOCAL_REDIRECT = "http://127.0.0.1:3000/callback"
OTHER_REDIRECT = "http://localhost:3000/callback"
# FIXTURE: the document the client publishes at CIMD_ID.
DOCUMENT = {"client_id": CIMD_ID, "client_name": "Example MCP Client", "client_uri": "https://client.example.test",
            "redirect_uris": [LOCAL_REDIRECT, OTHER_REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
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
    # FIXTURE: the document server. `served` is what the client publishes now; `fetched` every fetch.
    state = {"served": dict(DOCUMENT), "fetched": []}

    def fetch(client_id):
        state["fetched"].append(client_id)
        return dict(state["served"]) if client_id == CIMD_ID else {**state["served"], "client_id": client_id}
    monkeypatch.setattr(cimd, "fetch_document", fetch)
    return {"api": api, "store": store, "ident": ident, "alice": alice, "first": first,
            "fetched": state["fetched"], "state": state}


def publish(env, **changes):
    with env["store"].tx():
        current = McpPolicy(env["store"]).read()
        McpPolicy(env["store"]).append({**current["spec"], **changes}, expected_revision=current["revision"],
                                       actor_id=env["alice"]["id"], reason="fixture")


def allow(env, domains=(".example.test",)):
    publish(env, client_registration="cimd_and_preregistered", cimd_allowed_domains=list(domains), inbound_enabled=True)


def login(env) -> TestClient:
    c = TestClient(env["api"])
    assert c.post("/api/auth/login", json={"email": "alice@example.com", "password": PW}).status_code == 200
    return c


def pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(b"verifier" * 6).rstrip(b"=").decode()
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def authorize(c, client_id=CIMD_ID, scope="openid offline_access forms.read", *, redirect=LOCAL_REDIRECT, workspace_id=None):
    verifier, challenge = pkce()
    q = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": scope, "state": "s1",
         "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP}
    if workspace_id:
        q["workspace_id"] = workspace_id
    return c.get("/oidc/authorize?" + urllib.parse.urlencode(q), follow_redirects=False), verifier


def params(location: str) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))


def parked_request(resp) -> str:
    assert resp.status_code == 303 and resp.headers["location"].startswith("/oauth/consent"), (resp.status_code, resp.text)
    return params(resp.headers["location"])["request"]


def approve(c, rid: str):
    return c.post(f"/api/auth/oauth-consent/{rid}/approve", json={}, headers={CSRF_HEADER: c.cookies.get(CSRF_COOKIE) or ""})


def exchange(c, code: str, verifier: str, redirect: str = LOCAL_REDIRECT):
    return c.post("/oidc/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                       "client_id": CIMD_ID, "code_verifier": verifier, "resource": MCP})


def code_for(env, c) -> tuple[str, str]:
    """A code for the self-registered client: approved through the real consent page the first time,
    straight back once the person's consent covers the request."""
    resp, verifier = authorize(c, workspace_id=env["first"])
    if resp.headers.get("location", "").startswith("/oauth/consent"):
        done = approve(c, parked_request(resp))
        assert done.status_code == 200, done.text
        return params(done.json()["redirect"])["code"], verifier
    assert resp.status_code == 303, resp.text
    return params(resp.headers["location"])["code"], verifier


def refused_client(resp) -> bool:
    return resp.status_code == 400 and resp.json()["error"] == "invalid_client"


# ------------------------------------------------------------------ the policy, asked at every authorization
def test_a_withdrawn_mechanism_or_domain_ends_a_stored_self_registration(env):
    allow(env)
    c = login(env)
    parked_request(authorize(c, workspace_id=env["first"])[0])
    assert oidc.Clients(env["store"]).get(CIMD_ID)["registration"] == "cimd" and env["fetched"] == [CIMD_ID]

    publish(env, client_registration="preregistered_only")
    assert refused_client(authorize(c, workspace_id=env["first"])[0])
    allow(env, domains=[".other.example"])
    assert refused_client(authorize(c, workspace_id=env["first"])[0])
    # Allowed again: the stored registration is fresh, so it is used without another fetch.
    allow(env)
    parked_request(authorize(c, workspace_id=env["first"])[0])
    assert env["fetched"] == [CIMD_ID]


def test_a_stale_registration_is_fetched_again_and_a_dropped_callback_is_refused(env):
    allow(env)
    c = login(env)
    parked_request(authorize(c, workspace_id=env["first"])[0])
    before = oidc.Clients(env["store"]).get(CIMD_ID)
    # A day later the client's document no longer lists the loopback callback.
    env["state"]["served"] = {**DOCUMENT, "client_name": "Example MCP Client 2", "redirect_uris": [OTHER_REDIRECT]}
    env["store"].execute("UPDATE oauth_clients SET metadata_fetched_at=? WHERE client_id=?",
                         (now() - cimd.REFRESH_AFTER_S - 60, CIMD_ID))
    dropped, _ = authorize(c, workspace_id=env["first"])
    assert dropped.status_code == 400 and dropped.json()["error"] == "invalid_redirect_uri"
    assert env["fetched"] == [CIMD_ID, CIMD_ID]
    after = oidc.Clients(env["store"]).get(CIMD_ID)
    assert after["redirect_uris"] == [OTHER_REDIRECT] and after["name"] == "Example MCP Client 2"
    assert after["metadata_fetched_at"] > before["metadata_fetched_at"]
    # The callback it still lists works, and the refreshed registration is not fetched again while fresh.
    parked_request(authorize(c, redirect=OTHER_REDIRECT, workspace_id=env["first"])[0])
    assert env["fetched"] == [CIMD_ID, CIMD_ID]


def test_a_refresh_that_fails_leaves_nothing_usable_and_the_next_authorization_tries_again(env):
    allow(env)
    c = login(env)
    parked_request(authorize(c, workspace_id=env["first"])[0])
    env["state"]["served"] = {**DOCUMENT, "token_endpoint_auth_method": "client_secret_basic"}
    env["store"].execute("UPDATE oauth_clients SET metadata_fetched_at=? WHERE client_id=?",
                         (now() - cimd.REFRESH_AFTER_S - 60, CIMD_ID))
    assert refused_client(authorize(c, workspace_id=env["first"])[0])
    assert refused_client(authorize(c, workspace_id=env["first"])[0])
    assert env["fetched"] == [CIMD_ID, CIMD_ID, CIMD_ID]


# ------------------------------------------------------------------ nothing fetched for an anonymous request
def test_an_anonymous_request_fetches_and_stores_nothing(env):
    allow(env, domains=())
    anonymous = TestClient(env["api"])
    for i in range(3):
        client_id = f"https://client{i}.example.test/oauth/client.json"
        resp, _ = authorize(anonymous, client_id)
        assert resp.status_code == 303 and resp.headers["location"].startswith("/login?next="), resp.text
        # the return target is this very request, built from the server's own URL
        assert urllib.parse.unquote(params(resp.headers["location"])["next"]).startswith("/oidc/authorize?")
    assert env["fetched"] == []
    assert env["store"].one("SELECT COUNT(*) AS n FROM oauth_clients WHERE registration='cimd'")["n"] == 0
    # The policy is still asked first: a refused mechanism answers at once, not with a sign-in.
    publish(env, client_registration="preregistered_only")
    assert refused_client(authorize(anonymous, CIMD_ID)[0]) and env["fetched"] == []
    # Signed in, the same request fetches and registers.
    allow(env)
    c = login(env)
    parked_request(authorize(c, workspace_id=env["first"])[0])
    assert env["fetched"] == [CIMD_ID]
    # The fetch is recorded with the person whose request caused it.
    recorded = env["store"].all("SELECT meta FROM audit_events WHERE action='oidc.client_metadata_document'")
    assert len(recorded) == 1 and json.loads(recorded[0]["meta"])["requested_by"] == env["alice"]["id"]
    # A stored registration that is stale is not refreshed for an anonymous request either.
    env["store"].execute("UPDATE oauth_clients SET metadata_fetched_at=? WHERE client_id=?",
                         (now() - cimd.REFRESH_AFTER_S - 60, CIMD_ID))
    resp, _ = authorize(anonymous)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/login?next=")
    assert env["fetched"] == [CIMD_ID]


# ------------------------------------------------------------------ tokens end with the policy
def test_the_tokens_of_a_withdrawn_self_registered_client_stop_working(env):
    allow(env)
    c = login(env)
    code, verifier = code_for(env, c)
    issued = exchange(c, code, verifier)
    assert issued.status_code == 200, issued.text
    tokens = issued.json()
    gate = MachineAuth(env["store"], env["ident"], None)
    assert gate.verify_mcp(f"Bearer {tokens['access_token']}")["client_id"] == CIMD_ID
    assert c.get("/oidc/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 200
    # A second code and a parked request, both from before the policy changed.
    code2, verifier2 = code_for(env, c)
    rid = parked_request(authorize(c, scope="openid forms.read forms.write", workspace_id=env["first"])[0])

    publish(env, client_registration="preregistered_only")
    with pytest.raises(ServiceError) as err:
        gate.verify_mcp(f"Bearer {tokens['access_token']}")
    assert err.value.status == 401 and err.value.code == "client_unavailable"
    assert err.value.detail["www_authenticate"] == {"error": "invalid_token"}
    refreshed = c.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                            "client_id": CIMD_ID, "resource": MCP})
    assert refreshed.status_code == 401 and refreshed.json()["error"] == "invalid_client"
    late = exchange(c, code2, verifier2)
    assert late.status_code == 401 and late.json()["error"] == "invalid_client"
    assert c.get("/oidc/userinfo", headers={"Authorization": f"Bearer {tokens['access_token']}"}).status_code == 401
    shown = c.get(f"/api/auth/oauth-consent/{rid}")
    assert shown.status_code == 410 and shown.json()["error"] == "consent_expired"
    assert approve(c, rid).status_code == 410
    # Narrowing the domains has the same effect as turning the mechanism off.
    allow(env, domains=[".other.example"])
    with pytest.raises(ServiceError):
        gate.verify_mcp(f"Bearer {tokens['access_token']}")
    # The refusal follows the policy; nothing was revoked, so allowing it again restores the grant.
    allow(env)
    assert gate.verify_mcp(f"Bearer {tokens['access_token']}")["client_id"] == CIMD_ID
    again = c.post("/oidc/token", data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                                        "client_id": CIMD_ID, "resource": MCP})
    assert again.status_code == 200, again.text


def test_an_operator_registration_of_a_url_id_is_not_subject_to_the_document_policy(env):
    """An operator who registered this exact id by hand decides it; the document mechanism being off
    does not touch it, and nothing is fetched for it."""
    oidc.Clients(env["store"]).register(client_id=CIMD_ID, app_id="platform", name="Operator registration",
                                        redirect_uris=[LOCAL_REDIRECT], scopes="openid offline_access forms.read",
                                        audiences=[oidc.mcp_resource()])
    publish(env, inbound_enabled=True)
    c = login(env)
    code, verifier = code_for(env, c)
    issued = exchange(c, code, verifier)
    assert issued.status_code == 200, issued.text
    assert MachineAuth(env["store"], env["ident"], None).verify_mcp(f"Bearer {issued.json()['access_token']}")
    assert env["fetched"] == []


# ------------------------------------------------------------------ client_uri is a claim
@pytest.mark.parametrize("claimed, kept", [
    ("https://client.example.test", "https://client.example.test"),
    ("https://client.example.test/about", "https://client.example.test/about"),
    ("https://claude.ai", None),                                     # another origin: nobody checked it
    ("https://client.example.test.evil.example/", None),
    ("https://client.example.test:8443/", None),
    ("https://user@client.example.test/", None),
    ("http://client.example.test/", None),
    (42, None),
])
def test_a_client_uri_is_kept_only_on_the_client_ids_own_origin(claimed, kept):
    clean = cimd.validate_document(CIMD_ID, {**DOCUMENT, "client_uri": claimed})
    assert clean["client_uri"] == kept


def test_the_consent_page_is_not_told_a_foreign_home_page(env):
    env["state"]["served"] = {**DOCUMENT, "client_name": "Claude", "client_uri": "https://claude.ai"}
    allow(env)
    c = login(env)
    rid = parked_request(authorize(c, workspace_id=env["first"])[0])
    shown = c.get(f"/api/auth/oauth-consent/{rid}").json()
    assert shown["client"]["id"] == CIMD_ID and shown["client"]["registration"] == "cimd"
    assert shown["client"]["client_uri"] is None
