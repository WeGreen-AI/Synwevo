"""The Agent's outbound OAuth against hostile or unusual servers: what discovery refuses, which issuer
it records, which client secret goes where, and what an authorization overtaken by a new address,
a platform stop or a deletion leaves behind (nothing).

FIXTURES, each labelled: the MCP server, its resource metadata and every authorization server are a
FIXTURE HTTP responder (`Remote`) handed to the runtime in place of its pinned network transport
(`McpRuntime.TEST_TRANSPORT_FOR`). It answers fixed JSON by exact address and records every request
the Agent sends, so a test can say what left the process and to whom; an answer can first let
something else happen to the connection (`meanwhile`), as if it landed while that request was under
way. The person's browser is not involved: the tests call the flow's `start` and `callback` the way
the operation and the callback route do, as the signed-in owner of the connection.
"""
from __future__ import annotations

import base64
import json
import urllib.parse

import httpx2
import pytest
from fastapi.testclient import TestClient

from synwevo_core import sealed
from synwevo_core.errors import ServiceError
from synwevo_core.mcp_policy import McpPolicy
from synwevo_core.store import new_id, now
from synwevo_suite.agent.mcp.oauth import CALLBACK_PATH, OutboundOAuth
from synwevo_suite.agent.mcp.runtime import McpRuntime

from .conftest import People, WORKPLACE, make_client, sign_in
from .test_agent import op

MCP = "https://mcp.fixture.test/mcp"
PRM = "https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp"
AS = "https://as.fixture.test"
ELSEWHERE = "https://elsewhere.fixture.test/mcp"   # the server a manager points the connection at meanwhile
HTTPS_APP = "https://workplace.testserver"     # a Client ID Metadata Document needs an https client id


class Remote:
    """FIXTURE remote side: fixed answers by (method, exact URL), 404 for anything else; every request kept."""

    def __init__(self):
        self.routes: dict[tuple[str, str], tuple[int, object, dict]] = {}
        self.requests: list[httpx2.Request] = []

    def answer(self, url: str, body, *, method: str = "GET", status: int = 200, headers: dict | None = None) -> None:
        self.routes[(method, url)] = (status, body, headers or {})

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        found = self.routes.get((request.method, str(request.url)))
        if found is None:
            return httpx2.Response(404, json={"error": "not_found"})
        status, body, headers = found
        return httpx2.Response(status, json=body(request) if callable(body) else body, headers=headers)

    def hosts(self) -> set[str]:
        return {r.url.host for r in self.requests}

    def token_requests(self, endpoint: str) -> list[tuple[dict, str | None]]:
        """(form, Authorization header) of each POST to `endpoint`."""
        return [(dict(urllib.parse.parse_qsl(r.content.decode())), r.headers.get("authorization"))
                for r in self.requests if r.method == "POST" and str(r.url) == endpoint]

    # -- the fixture server's documents
    def challenge(self, *, resource_metadata: str | None = None) -> None:
        """The MCP server answers an unauthenticated request with 401, optionally naming its metadata."""
        value = "Bearer" + (f' resource_metadata="{resource_metadata}"' if resource_metadata else "")
        self.answer(MCP, {"error": "unauthorized"}, method="POST", status=401, headers={"WWW-Authenticate": value})

    def resource_metadata(self, url: str, resource: str, authorization_server: str) -> None:
        self.answer(url, {"resource": resource, "authorization_servers": [authorization_server]})

    def authorization_server(self, url: str, issuer: str, *, endpoints_at: str | None = None, pkce=("S256",),
                             iss_parameter: bool = True, **extra) -> None:
        base = (endpoints_at or issuer).rstrip("/")
        doc = {"issuer": issuer, "authorization_endpoint": base + "/authorize", "token_endpoint": base + "/token",
               "response_types_supported": ["code"], "authorization_response_iss_parameter_supported": iss_parameter, **extra}
        if pkce is not None:
            doc["code_challenge_methods_supported"] = list(pkce)
        self.answer(url, doc)

    def token_endpoint(self, url: str, access: str = "fixture-access-token") -> None:
        self.answer(url, {"access_token": access, "token_type": "Bearer", "expires_in": 3600,
                          "refresh_token": f"{access}-refresh"}, method="POST")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    remote = Remote()
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", staticmethod(lambda url: httpx2.MockTransport(remote.handler)))
    monkeypatch.setenv("SYNWEVO_AGENT_ANTHROPIC_API_KEY", "test-double-key-not-a-real-credential")
    monkeypatch.setenv("SYNWEVO_AGENT_MODEL", "test-double-model")
    api = make_client(tmp_path, monkeypatch)
    people = People(api)
    with people.store.tx():
        McpPolicy(people.store).append({**McpPolicy.default().model_dump(), "outbound_enabled": True},
                                       expected_revision=0, actor_id=people.uid("alice"), reason="fixture: open outbound MCP")
    with TestClient(api, base_url=WORKPLACE) as c:
        c.api, c.people, c.remote, c.monkeypatch = api, people, remote, monkeypatch
        c.oauth = OutboundOAuth(api.state.mcp_connections)
        c.alice = {"id": people.uid("alice")}
        sign_in(c, "alice")
        yield c


def oauth_connection(c, oauth_client: dict | None = None) -> str:
    body = {"owner_kind": "user", "name": "Fixture remote", "url": MCP, "auth_kind": "oauth"}
    if oauth_client:
        body["oauth_client"] = oauth_client
    return op(c, "agent.mcp.connection.create", body)["output"]["id"]


def refused(c, cid: str) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        c.oauth.start(c.alice, cid)
    return caught.value


def query(started: dict) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(started["authorization_url"]).query))


def flows(c, cid: str) -> list[dict]:
    return c.people.store.all("SELECT issuer, client_id, resource, outcome FROM mcp_oauth_flows WHERE connection_id=? "
                              "ORDER BY created_at", (cid,))


def basic(client_id: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()


# ---------------------------------------------------------------------------------------------- resource


def test_resource_metadata_that_names_another_service_is_refused_before_its_authorization_server_is_asked(world):
    """RFC 9728 §3.3: the token the person would grant at the other service's authorization server
    would come back to this server as a bearer — a confused deputy."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    # A real-looking victim: its own resource and authorization server, both answering properly.
    world.remote.authorization_server("https://as.victim.fixture.test/.well-known/oauth-authorization-server",
                                      "https://as.victim.fixture.test")
    hint = "https://victim.fixture.test/.well-known/oauth-protected-resource/mcp"
    world.remote.challenge(resource_metadata=hint)
    world.remote.resource_metadata(hint, "https://victim.fixture.test/mcp", "https://as.victim.fixture.test")
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_discovery_failed", "resource_mismatch")
    assert "as.victim.fixture.test" not in world.remote.hosts() and flows(world, cid) == []


def test_resource_metadata_at_the_servers_own_address_must_still_name_this_server(world):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.challenge()
    own = "https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp"
    for named in ("https://victim.fixture.test/mcp",       # another origin
                  "https://mcp.fixture.test/other",         # a sibling on the same origin
                  "https://mcp.fixture.test/mcp/inner",     # something under the server's path, not the server
                  "http://mcp.fixture.test/mcp"):           # another scheme
        world.remote.resource_metadata(own, named, AS)
        err = refused(world, cid)
        assert (err.code, err.detail.get("reason")) == ("mcp_oauth_discovery_failed", "resource_mismatch"), named
    assert "as.fixture.test" not in world.remote.hosts() and flows(world, cid) == []


def test_resource_metadata_for_this_server_or_its_origin_is_used_and_sent_as_the_resource(world):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.challenge()
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp", MCP, AS)
    assert query(world.oauth.start(world.alice, cid))["resource"] == MCP
    # Metadata at the root names the origin; the connection's path lies below it (the SDK's rule).
    world.remote.routes.pop(("GET", "https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp"))
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource",
                                   "https://mcp.fixture.test", AS)
    assert query(world.oauth.start(world.alice, cid))["resource"] == "https://mcp.fixture.test"


# ---------------------------------------------------------------------------------------------- issuer


def test_without_resource_metadata_the_issuer_must_be_the_servers_own_origin(world):
    """The 2025-03-26 path: the metadata is read from the server's origin, so that origin is the issuer
    it may name. A document there claiming another issuer would steer the code, the PKCE verifier and
    any client secret to a token endpoint of its choosing while the real server's `iss` matched."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()                      # and no resource metadata anywhere (404)
    meta_url = "https://mcp.fixture.test/.well-known/oauth-authorization-server"
    world.remote.authorization_server(meta_url, "https://real-as.fixture.test", endpoints_at="https://real-as.fixture.test",
                                      token_endpoint="https://mcp.fixture.test/token")
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_discovery_failed", "issuer_mismatch")
    assert flows(world, cid) == []

    # Its own origin, with or without the root's trailing slash (both build the same well-known address):
    for issuer in ("https://mcp.fixture.test", "https://mcp.fixture.test/"):
        world.remote.authorization_server(meta_url, issuer)
        started = world.oauth.start(world.alice, cid)
        assert started["issuer"] == issuer and query(started)["resource"] == MCP
        assert flows(world, cid)[-1]["issuer"] == issuer


def test_metadata_whose_issuer_is_not_the_named_authorization_server_is_refused(world):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    prm = "https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp"
    cases = (
        (AS, AS + "/.well-known/oauth-authorization-server", "https://other.fixture.test"),
        # string for string (RFC 8414 §3.3): neither form of a path issuer stands for the other
        (AS + "/t", AS + "/.well-known/oauth-authorization-server/t", AS + "/t/"),
        (AS + "/t/", AS + "/.well-known/oauth-authorization-server/t", AS + "/t"),
    )
    for named, meta_url, claimed in cases:
        world.remote.resource_metadata(prm, MCP, named)
        world.remote.authorization_server(meta_url, claimed)
        err = refused(world, cid)
        assert (err.code, err.detail.get("reason")) == ("mcp_oauth_discovery_failed", "issuer_mismatch"), (named, claimed)
    assert flows(world, cid) == []


@pytest.mark.parametrize("issuer, meta_url", [
    ("https://tenant.fixture.test/", "https://tenant.fixture.test/.well-known/oauth-authorization-server"),
    ("https://as.fixture.test/t/", "https://as.fixture.test/.well-known/oauth-authorization-server/t"),
])
def test_an_issuer_ending_in_a_slash_is_kept_exactly_through_discovery_and_the_callback(world, issuer, meta_url):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered", "client_secret": "fixture-preregistered-secret"})
    world.remote.challenge()
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp", MCP, issuer)
    world.remote.authorization_server(meta_url, issuer)
    token = issuer.rstrip("/") + "/token"
    world.remote.token_endpoint(token)

    started = world.oauth.start(world.alice, cid)
    assert started["issuer"] == issuer and flows(world, cid)[-1]["issuer"] == issuer
    # RFC 9207 §2.4: simple string comparison — the issuer without its slash is another issuer.
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=issuer.rstrip("/"), error=None)
    assert caught.value.code == "mcp_oauth_issuer_mismatch" and world.remote.token_requests(token) == []

    started = world.oauth.start(world.alice, cid)
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=issuer,
                                error=None)["ok"] is True
    [(form, auth)] = world.remote.token_requests(token)
    assert form["client_id"] == "fixture-preregistered" and form["resource"] == MCP
    # the person's own pre-registered secret still authenticates the client
    assert auth == basic("fixture-preregistered", "fixture-preregistered-secret")
    assert flows(world, cid)[-1]["outcome"] == "ok"


# ---------------------------------------------------------------------------------------------- PKCE


@pytest.mark.parametrize("methods", [None, ("plain",), ()])
def test_an_authorization_server_that_does_not_advertise_pkce_s256_is_refused(world, methods):
    """MCP authorization, "Authorization Code Protection": no code_challenge_methods_supported means no
    PKCE, and the client MUST refuse to proceed."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp", MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS, pkce=methods)
    assert refused(world, cid).code == "mcp_oauth_unsupported"
    assert flows(world, cid) == []


# ---------------------------------------------------------------------------------------------- client secrets


def _switch_to(world, issuer: str, **extra) -> str:
    """The MCP server now names `issuer` as its authorization server; returns its token endpoint."""
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp", MCP, issuer)
    world.remote.authorization_server(issuer + "/.well-known/oauth-authorization-server", issuer, **extra)
    world.remote.token_endpoint(issuer + "/token", access=f"fixture-access-{urllib.parse.urlsplit(issuer).hostname}")
    return issuer + "/token"


def _registers(world, issuer: str, client_id: str, secret: str) -> None:
    world.remote.answer(issuer + "/register", {"client_id": client_id, "client_secret": secret}, method="POST", status=201)


def _authorize(world, cid: str) -> dict:
    started = world.oauth.start(world.alice, cid)
    world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=started["issuer"], error=None)
    return started


def _expire_and_refresh(world, cid: str) -> None:
    store = world.people.store
    conn = store.one("SELECT * FROM mcp_connections WHERE id=?", (cid,))
    tokens = json.loads(sealed.unseal(store, "mcp-credential", conn["credential_sealed"]))
    world.oauth.refresh(conn, {**tokens, "expires_at": 1})


def test_a_dynamic_registrations_secret_never_goes_to_another_authorization_servers_token_endpoint(world):
    cid = oauth_connection(world)
    world.remote.challenge()
    a = "https://as-a.fixture.test"
    token_a = _switch_to(world, a, registration_endpoint=a + "/register")
    _registers(world, a, "dyn-a", "secret-from-a")
    assert _authorize(world, cid)["registration"] == "dynamic"
    # at the server that issued it, the secret authenticates its client
    [(form, auth)] = world.remote.token_requests(token_a)
    assert form["client_id"] == "dyn-a" and auth == basic("dyn-a", "secret-from-a")

    # The server now names B, which accepts the Agent's Client ID Metadata Document.
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    b = "https://as-b.fixture.test"
    token_b = _switch_to(world, b, client_id_metadata_document_supported=True)
    started = _authorize(world, cid)
    assert started["registration"] == "cimd"
    [(form, auth)] = world.remote.token_requests(token_b)
    assert form["client_id"] == query(started)["client_id"] and auth is None
    # Refreshing B's token: B's client, still no secret of A's.
    _expire_and_refresh(world, cid)
    (form, auth) = world.remote.token_requests(token_b)[-1]
    assert (form["grant_type"], form["client_id"], auth) == ("refresh_token", query(started)["client_id"], None)
    assert all("secret-from-a" not in (r.headers.get("authorization") or "") for r in world.remote.requests
               if r.url.host != "as-a.fixture.test")


def test_refreshing_a_token_sends_only_its_own_clients_secret_after_a_registration_elsewhere(world):
    cid = oauth_connection(world)
    world.remote.challenge()
    a, b = "https://as-a.fixture.test", "https://as-b.fixture.test"
    token_a = _switch_to(world, a, registration_endpoint=a + "/register")
    _registers(world, a, "dyn-a", "secret-from-a")
    _authorize(world, cid)
    # The server names B, which only offers dynamic registration: the Agent registers there (and B's
    # record replaces A's) but the person has not finished authorizing yet — A's token is still in use.
    _switch_to(world, b, registration_endpoint=b + "/register")
    _registers(world, b, "dyn-b", "secret-from-b")
    assert world.oauth.start(world.alice, cid)["registration"] == "dynamic"
    stored = json.loads(world.people.store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (cid,))["oauth_client_json"])
    assert (stored["client_id"], stored["issuer"]) == ("dyn-b", b)

    with pytest.raises(ServiceError):
        # A's token endpoint (the fixture keeps answering) gets A's client id and no secret of B's;
        # a confidential client whose secret is gone then needs the person again, which is the outcome.
        world.remote.answer(token_a, {"error": "invalid_client"}, method="POST", status=401)
        _expire_and_refresh(world, cid)
    (form, auth) = world.remote.token_requests(token_a)[-1]
    assert (form["grant_type"], form["client_id"], auth) == ("refresh_token", "dyn-a", None)
    assert all("secret-from-b" not in (r.headers.get("authorization") or "") for r in world.remote.requests
               if r.url.host != "as-b.fixture.test")


def test_the_callback_route_still_lands_on_the_connection(world):
    """The route around `callback` is unchanged by the stricter checks: an exact issuer completes."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    world.remote.resource_metadata("https://mcp.fixture.test/.well-known/oauth-protected-resource/mcp", MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.token_endpoint(AS + "/token")
    started = world.oauth.start(world.alice, cid)
    landed = world.get(CALLBACK_PATH + "?" + urllib.parse.urlencode({"state": query(started)["state"], "code": "fixture-code",
                                                                     "iss": AS}), follow_redirects=False)
    assert landed.status_code == 303 and landed.headers["location"] == f"/agent/integrations/{cid}?auth=ok"


# ---------------------------------------------------------------------------------------------- overtaken by a new address


def meanwhile(effect, answer):
    """A FIXTURE answer that first lets `effect` happen to the connection — as if it landed while this
    request was under way — and then answers `answer`."""
    def body(request):
        effect()
        return answer
    return body


def repoint(world, cid: str):
    """A manager points the connection at another server (the connection service's own update)."""
    return lambda: world.api.state.mcp_connections.update(world.alice, cid, url=ELSEWHERE)


def fixture_flow(world, cid: str, url: str) -> str:
    """FIXTURE: an authorization of the connection in progress, as oauth.start records it."""
    host, fid, t = urllib.parse.urlsplit(url).hostname, new_id("mcpflow"), now()
    world.people.store.execute(
        "INSERT INTO mcp_oauth_flows (id, connection_id, user_id, state, code_verifier_sealed, issuer, authorization_endpoint, "
        "token_endpoint, client_id, scope, resource, redirect_uri, iss_supported, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (fid, cid, world.alice["id"], new_id("fixturestate"), "", f"https://{host}", f"https://{host}/authorize",
         f"https://{host}/token", "fixture-preregistered", None, url, "https://workplace.testserver" + CALLBACK_PATH, 0, t, t + 600))
    return fid


def connection(world, cid: str) -> dict:
    return world.people.store.one("SELECT * FROM mcp_connections WHERE id=?", (cid,))


def test_no_flow_is_recorded_for_the_server_a_connection_was_pointed_away_from_during_discovery(world):
    """A new address forgets the connection's flows. A flow for the old server recorded after that would
    let the old server's tokens be sealed on the connection and sent to the new one."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    world.remote.resource_metadata(PRM, MCP, AS)
    meta_url = AS + "/.well-known/oauth-authorization-server"
    world.remote.authorization_server(meta_url, AS)
    world.remote.answer(meta_url, meanwhile(repoint(world, cid), world.remote.routes[("GET", meta_url)][1]))
    assert refused(world, cid).code == "mcp_connection_changed"
    assert connection(world, cid)["url"] == ELSEWHERE and flows(world, cid) == []


@pytest.mark.parametrize("change, code", [("stop", "mcp_platform_disabled"), ("delete", "mcp_connection_changed")])
def test_no_flow_is_recorded_for_a_connection_stopped_or_deleted_during_discovery(world, change, code):
    """A stop or a deletion that lands while the authorization server is being discovered wins: no flow
    (nothing for a callback to seal tokens with), no record of an authorization begun, and a stop is
    named as such."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    world.remote.resource_metadata(PRM, MCP, AS)
    meta_url = AS + "/.well-known/oauth-authorization-server"
    world.remote.authorization_server(meta_url, AS)
    store = world.people.store
    effects = {
        # FIXTURE: the platform stop as synwevo_identity.admin_mcp records it (the account service is not mounted here).
        "stop": lambda: store.execute("UPDATE mcp_connections SET status='disabled', platform_disabled_at=?, platform_disabled_by=? "
                                      "WHERE id=?", (now(), world.alice["id"], cid)),
        "delete": lambda: world.api.state.mcp_connections.delete(world.alice, cid),
    }
    world.remote.answer(meta_url, meanwhile(effects[change], world.remote.routes[("GET", meta_url)][1]))
    assert refused(world, cid).code == code
    assert flows(world, cid) == []
    assert store.all("SELECT id FROM audit_events WHERE action='suite.agent.mcp.oauth.start' AND object_id=?", (cid,)) == []


def test_a_registration_overtaken_by_a_new_address_leaves_no_client_or_secret_behind(world):
    cid = oauth_connection(world)                       # no client of the person's: dynamic registration
    world.remote.challenge()
    a = "https://as-a.fixture.test"
    _switch_to(world, a, registration_endpoint=a + "/register")
    world.remote.answer(a + "/register", meanwhile(repoint(world, cid), {"client_id": "dyn-a", "client_secret": "secret-from-a"}),
                        method="POST", status=201)
    assert refused(world, cid).code == "mcp_connection_changed"
    row = connection(world, cid)
    assert (row["url"], row["oauth_client_json"], row["oauth_client_secret_sealed"]) == (ELSEWHERE, None, None)
    assert flows(world, cid) == []


def test_a_late_callback_for_a_connection_pointed_elsewhere_meanwhile_stores_nothing(world):
    """The old server's code is exchanged while a manager re-points the connection and the new server's
    authorization begins: that newer flow does not vouch for the old server's tokens, and nothing the
    old server issued reaches the new one."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    started = world.oauth.start(world.alice, cid)
    newer = []

    def elsewhere():
        repoint(world, cid)()
        newer.append(fixture_flow(world, cid, ELSEWHERE))
    world.remote.answer(AS + "/token", meanwhile(elsewhere, {"access_token": "old-server-access", "token_type": "Bearer",
                                                             "expires_in": 3600, "refresh_token": "old-server-refresh"}),
                        method="POST")
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=AS, error=None)
    assert caught.value.code == "mcp_connection_changed"
    row = connection(world, cid)
    assert (row["url"], row["credential_sealed"]) == (ELSEWHERE, None)
    assert "elsewhere.fixture.test" not in world.remote.hosts()
    # The new server's authorization is untouched: still open, for its own callback to decide.
    assert world.people.store.all("SELECT id, outcome FROM mcp_oauth_flows WHERE connection_id=?", (cid,)) == [
        {"id": newer[0], "outcome": None}]


def test_a_refresh_whose_authorization_a_new_address_voided_stores_nothing(world):
    """The refresh is tied to the authorization its refresh token came from. (The connection service's
    own refresh also holds a lease that such a change clears; this is the refresh's own guarantee.)"""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge()
    token = _switch_to(world, AS)
    _authorize(world, cid)

    def elsewhere():
        repoint(world, cid)()
        fixture_flow(world, cid, ELSEWHERE)
    world.remote.answer(token, meanwhile(elsewhere, {"access_token": "old-server-access-2", "token_type": "Bearer",
                                                     "expires_in": 3600, "refresh_token": "old-server-refresh-2"}),
                        method="POST")
    with pytest.raises(ServiceError) as caught:
        _expire_and_refresh(world, cid)
    assert caught.value.code == "mcp_connection_changed"
    assert (connection(world, cid)["url"], connection(world, cid)["credential_sealed"]) == (ELSEWHERE, None)
    assert "elsewhere.fixture.test" not in world.remote.hosts()
