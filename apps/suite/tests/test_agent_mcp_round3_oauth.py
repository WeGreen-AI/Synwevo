"""The Agent's outbound OAuth, third review round: mix-up through an issuer's own metadata, a callback
from someone who may no longer decide it, the issuer as published, step-up for a scope a server
challenges, what a failed refresh is, and nothing that blocks on the runtime's loop.

FIXTURES, each labelled: the MCP server, its resource metadata and every authorization server are the
FIXTURE HTTP responder of test_agent_mcp_oauth.py (`Remote`, handed to the runtime in place of its
pinned network transport); an answer may instead raise what the network would (a refused connection,
a read timeout). The people and workspaces are the suite's fixture accounts. The resolver that refuses
for want of a free slot is a FIXTURE stand-in for `resolve_checked`. No network is used.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse

import httpx2
import pytest

from synwevo_core import sealed, webhook_delivery
from synwevo_core.errors import ServiceError
from synwevo_core.store import Store, now
from synwevo_suite.agent.mcp import oauth as oauth_mod
from synwevo_suite.agent.mcp import runtime as rt_mod
from synwevo_suite.agent.mcp.connections import SERVER_FAILURES
from synwevo_suite.agent.mcp.runtime import McpRuntime, status_refusal

from .test_agent import op
from .test_agent_mcp_oauth import (AS, HTTPS_APP, MCP, PRM, _authorize, _expire_and_refresh, flows,  # noqa: F401 — `world` is a fixture
                                   oauth_connection, query, refused, world)

HONEST = "https://honest-as.fixture.test"      # an authorization server that does not send `iss`
ATTACKER = "https://mcp.fixture.test"           # the connection's own server, whose metadata steers the flow


def as_metadata(world, url: str, issuer: str, *, authorization: str, token: str, iss: bool, **extra) -> None:
    """FIXTURE: authorization server metadata at `url`, its endpoints wherever the test says."""
    world.remote.answer(url, {"issuer": issuer, "authorization_endpoint": authorization, "token_endpoint": token,
                              "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                              "authorization_response_iss_parameter_supported": iss, **extra})


def token_posts(world) -> list[str]:
    return [str(r.url) for r in world.remote.requests if r.method == "POST" and r.url.path.endswith("/token")]


# ---------------------------------------------------------------------------------------------- S3: mix-up


@pytest.mark.parametrize("path", ["2025-03-26 (no resource metadata)", "resource metadata naming the attacker's server"])
@pytest.mark.parametrize("steered", ["authorization_endpoint", "token_endpoint"])
def test_metadata_whose_endpoints_leave_the_issuers_origin_is_refused_when_it_sends_no_iss(world, path, steered):
    """An issuer's own metadata (its `issuer` right, so RFC 8414 §3.3 holds) sends the browser to an
    honest server that sends no `iss`, or keeps the token endpoint elsewhere: the honest server's code
    and the PKCE verifier would go to a token endpoint of the metadata's choosing. Refused before any
    flow exists, so there is no callback to send them from."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)       # a Client ID Metadata Document any server accepts
    cid = oauth_connection(world)
    if path.startswith("2025"):
        world.remote.challenge()
        issuer, meta_url = ATTACKER, ATTACKER + "/.well-known/oauth-authorization-server"
    else:
        issuer = "https://attacker-as.fixture.test"
        world.remote.challenge(resource_metadata=PRM)
        world.remote.resource_metadata(PRM, MCP, issuer)
        meta_url = issuer + "/.well-known/oauth-authorization-server"
    endpoints = {"authorization_endpoint": issuer + "/authorize", "token_endpoint": issuer + "/token"}
    endpoints[steered] = HONEST + ("/authorize" if steered == "authorization_endpoint" else "/token")
    as_metadata(world, meta_url, issuer, authorization=endpoints["authorization_endpoint"], token=endpoints["token_endpoint"],
                iss=False, client_id_metadata_document_supported=True)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason"), err.detail.get("endpoints")) == (
        "mcp_oauth_discovery_failed", "endpoint_origin_mismatch", [steered])
    assert flows(world, cid) == [] and token_posts(world) == []


def test_endpoints_off_the_issuers_origin_are_used_only_with_a_server_held_to_its_iss(world):
    """A server that says it sends `iss` may keep its endpoints on other origins (a hosted token
    service): its callback is then refused without `iss`, or with another server's, before any code is
    sent — and completes with its own."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    issuer, token = "https://accounts.fixture.test", "https://tokens.fixture.test/token"
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, issuer)
    as_metadata(world, issuer + "/.well-known/oauth-authorization-server", issuer,
                authorization=HONEST + "/authorize", token=token, iss=True)
    world.remote.token_endpoint(token)
    for sent in (None, HONEST):                                  # the honest server's response: no iss, or its own
        state = query(world.oauth.start(world.alice, cid))["state"]
        with pytest.raises(ServiceError) as caught:
            world.oauth.callback(world.alice, state=state, code="honest-server-code", iss=sent, error=None)
        assert caught.value.code == "mcp_oauth_issuer_mismatch"
    assert token_posts(world) == []
    started = world.oauth.start(world.alice, cid)
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=issuer, error=None)["ok"]
    assert token_posts(world) == [token]


def test_an_issuer_with_its_endpoints_on_its_own_origin_needs_no_iss(world):
    """MCP authorization, "Authorization Response Validation": a server that neither says it sends
    `iss` nor sends it is proceeded with — when its endpoints are its issuer's own."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS, iss_parameter=False)
    world.remote.token_endpoint(AS + "/token")
    started = world.oauth.start(world.alice, cid)
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=None, error=None)["ok"]
    assert token_posts(world) == [AS + "/token"]


def test_a_registration_endpoint_off_the_issuers_origin_is_never_used(world):
    """A registration's secret is sent to the issuer's token endpoint as this deployment's client
    there: a registration endpoint elsewhere would mint that client for whoever answers it."""
    cid = oauth_connection(world)                              # no client of the person's, no Client ID Metadata Document
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    as_metadata(world, AS + "/.well-known/oauth-authorization-server", AS, authorization=AS + "/authorize",
                token=AS + "/token", iss=True, registration_endpoint="https://elsewhere.fixture.test/register")
    world.remote.answer("https://elsewhere.fixture.test/register", {"client_id": "minted", "client_secret": "s"},
                        method="POST", status=201)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_registration_unavailable", "endpoint_origin_mismatch")
    assert "elsewhere.fixture.test" not in world.remote.hosts() and flows(world, cid) == []


# ---------------------------------------------------------------------------------------------- S6: who decides


def workspace_connection(world) -> tuple[str, dict]:
    """A workspace connection that Bob, made an administrator of workspace A, begins to authorize."""
    p = world.people
    p.ident.change_role(p.a, p.uid("bob"), "admin")
    cid = op(world, "agent.mcp.connection.create", {"owner_kind": "workspace", "workspace_id": p.a, "name": "Team remote",
                                                    "url": MCP, "auth_kind": "oauth",
                                                    "oauth_client": {"client_id": "fixture-preregistered"}})["output"]["id"]
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.token_endpoint(AS + "/token", access="bobs-own-external-account")
    bob = {"id": p.uid("bob")}
    return cid, {"bob": bob, "state": query(world.oauth.start(bob, cid))["state"]}


@pytest.mark.parametrize("change, code", [("removed", "mcp_connection_not_found"), ("demoted", "mcp_manage_forbidden"),
                                          ("stopped", "mcp_platform_disabled")])
def test_a_callback_decides_nothing_for_someone_who_may_no_longer_manage_the_connection(world, change, code):
    cid, began = workspace_connection(world)
    p, store = world.people, world.people.store
    if change == "removed":
        p.ident.remove_member(p.a, p.uid("bob"))
    elif change == "demoted":
        p.ident.change_role(p.a, p.uid("bob"), "editor")
    else:
        # FIXTURE: the platform stop as synwevo_identity.admin_mcp records it (the account service is not mounted here).
        store.execute("UPDATE mcp_connections SET status='disabled', platform_disabled_at=?, platform_disabled_by=? WHERE id=?",
                      (now(), p.uid("alice"), cid))
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(began["bob"], state=began["state"], code="fixture-code", iss=AS, error=None)
    assert caught.value.code == code
    assert token_posts(world) == []                          # the code was never exchanged
    assert store.one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (cid,))["credential_sealed"] is None
    assert flows(world, cid)[-1]["outcome"] == "refused"
    with pytest.raises(ServiceError) as again:                # and the flow is spent
        world.oauth.callback(began["bob"], state=began["state"], code="fixture-code", iss=AS, error=None)
    assert again.value.code == "mcp_oauth_flow_decided"


def test_a_callback_of_a_manager_still_completes(world):
    cid, began = workspace_connection(world)
    assert world.oauth.callback(began["bob"], state=began["state"], code="fixture-code", iss=AS, error=None)["ok"] is True
    assert flows(world, cid)[-1]["outcome"] == "ok"


# ---------------------------------------------------------------------------------------------- P4: the issuer as published


def test_an_issuer_is_compared_and_recorded_exactly_as_published(world):
    """RFC 8414 §3.3 and RFC 9207 §2.4 compare strings: an issuer written with an uppercase host is
    that issuer, its own `iss` matches it, and the model's rendering of it (lower-cased) does not."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    upper = "https://AS.fixture.test"
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, upper)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", upper, endpoints_at=AS)
    world.remote.token_endpoint(AS + "/token")
    started = world.oauth.start(world.alice, cid)
    assert started["issuer"] == upper
    assert world.people.store.one("SELECT issuer FROM mcp_oauth_flows WHERE connection_id=? ORDER BY created_at DESC",
                                  (cid,))["issuer"] == upper
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=AS, error=None)
    assert caught.value.code == "mcp_oauth_issuer_mismatch"
    started = world.oauth.start(world.alice, cid)
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=upper, error=None)["ok"]

    # The resource metadata names the issuer one way, the metadata another: not identical, refused.
    world.remote.resource_metadata(PRM, MCP, AS)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_discovery_failed", "issuer_mismatch")


# ---------------------------------------------------------------------------------------------- P1: step-up


def challenge_403(world, scope: str) -> None:
    """FIXTURE: the MCP server now refuses the token for scope (RFC 6750 §3.1; MCP "Runtime
    Insufficient Scope Errors")."""
    world.remote.answer(MCP, {"error": "insufficient_scope"}, method="POST", status=403, headers={"WWW-Authenticate":
                        f'Bearer error="insufficient_scope", scope="{scope}", resource_metadata="{PRM}", '
                        'error_description="needs scope=admin to write"'})


def authorized(world, *, cimd: bool = False) -> str:
    """An OAuth connection authorized for the server's basic scope (`files:read`, its scopes_supported)."""
    if cimd:
        world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world, None if cimd else {"client_id": "fixture-preregistered"})
    world.remote.challenge(resource_metadata=PRM)
    world.remote.answer(PRM, {"resource": MCP, "authorization_servers": [AS], "scopes_supported": ["files:read"]})
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS,
                                      client_id_metadata_document_supported=cimd)
    world.remote.token_endpoint(AS + "/token")
    first = _authorize(world, cid)
    assert query(first)["scope"] == "files:read"
    return cid


def discovery_refused(world, cid: str) -> ServiceError:
    with pytest.raises(ServiceError) as caught:
        world.api.state.mcp_connections.test(world.alice, cid)
    return caught.value


@pytest.mark.parametrize("cimd", [False, True], ids=["preregistered", "client-id-metadata-document"])
def test_the_scope_a_server_challenges_is_asked_for_by_the_next_authorization(world, cimd):
    """MCP authorization, "Step-Up Authorization Flow": the union of the scope set asked for before and
    the scopes of the challenge — which are authoritative for the operation it refused."""
    cid = authorized(world, cimd=cimd)
    challenge_403(world, "files:read files:write")
    err = discovery_refused(world, cid)
    assert err.code == "mcp_auth_required"
    assert err.detail["www_authenticate"]["scope"] == "files:read files:write" and err.detail["step_up"] == "requested"

    world.remote.challenge(resource_metadata=PRM)             # unauthenticated, the server still asks for files:read only
    again = _authorize(world, cid)
    assert query(again)["scope"].split() == ["files:read", "files:write"]

    # Asked for and authorized, and still refused: authorizing again would not change the answer.
    challenge_403(world, "files:write")
    assert discovery_refused(world, cid).detail["step_up"] == "already_requested"
    world.remote.challenge(resource_metadata=PRM)
    assert query(world.oauth.start(world.alice, cid))["scope"].split() == ["files:read", "files:write"]


def test_step_ups_are_capped_and_bounded(world):
    cid = authorized(world)
    for n in range(oauth_mod.MAX_STEP_UPS):
        challenge_403(world, f"extra:{n}")
        assert discovery_refused(world, cid).detail["step_up"] == "requested"
    challenge_403(world, "one:more")
    assert discovery_refused(world, cid).detail["step_up"] == "capped"
    world.remote.challenge(resource_metadata=PRM)
    asked = query(world.oauth.start(world.alice, cid))["scope"].split()
    assert asked == ["files:read"] + [f"extra:{n}" for n in range(oauth_mod.MAX_STEP_UPS)]


def test_a_new_address_forgets_the_step_up(world):
    cid = authorized(world)
    challenge_403(world, "files:write")
    assert discovery_refused(world, cid).detail["step_up"] == "requested"
    world.api.state.mcp_connections.update(world.alice, cid, url=MCP + "/v2")
    row = world.people.store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (cid,))
    assert row["oauth_client_json"] is None


@pytest.mark.parametrize("header, kept", [
    ('Bearer error="insufficient_scope", scope="files:read files:write"', "files:read files:write"),
    ('Bearer error="insufficient_scope", scope=files:write', "files:write"),
    # a parameter's value is that parameter's: the description's `scope=` is not the scope
    ('Bearer error="insufficient_scope", error_description="scope=admin", scope="files:write"', "files:write"),
    ('Bearer error="insufficient_scope", error_description="need scope=admin"', None),
    # outside RFC 6750's charset (a quote, a backslash, a non-ASCII letter), or past this client's bounds: not kept
    ('Bearer error="insufficient_scope", scope="files:write \\"x\\""', None),
    ('Bearer error="insufficient_scope", scope="files:write a\\\\b"', None),
    ('Bearer error="insufficient_scope", scope="文件:写"', None),
    ('Bearer error="insufficient_scope", scope="{one more scope token than runtime.MAX_SCOPE_TOKENS}"', None),
    # only an insufficient_scope challenge names the scope a step-up asks for
    ('Bearer error="invalid_token", scope="files:write"', None),
])
def test_a_challenges_scope_is_kept_only_as_rfc_6750_writes_it(header, kept):
    if "{one more" in header:
        header = 'Bearer error="insufficient_scope", scope="' + " ".join(f"s{i}" for i in range(rt_mod.MAX_SCOPE_TOKENS + 1)) + '"'
    # As the network delivers it: bytes (UTF-8 here), which the HTTP client decodes.
    challenge = status_refusal(403, httpx2.Headers({"www-authenticate": header.encode()})).detail["www_authenticate"]
    assert challenge.get("scope") == kept


# ---------------------------------------------------------------------------------------------- K1: a failed refresh


def raising(exc_type):
    """FIXTURE: the network failing the way `exc_type` says, instead of an answer."""
    def body(request):
        raise exc_type("fixture: the network failed", request=request)
    return body


@pytest.mark.parametrize("answer, status, code", [
    ({"error": "invalid_grant"}, 400, "mcp_auth_required"),
    ({"error": "unauthorized_client"}, 400, "mcp_auth_required"),
    ({"error": "invalid_client"}, 401, "mcp_auth_required"),
    ({}, 401, "mcp_auth_required"),
    ({"error": "server_error"}, 500, "mcp_server_error"),
    ({}, 503, "mcp_server_error"),
    ({"error": "invalid_request"}, 400, "mcp_protocol_error"),
    ({"not": "a token response"}, 200, "mcp_protocol_error"),
    (raising(httpx2.ConnectError), 200, "mcp_unreachable"),
    (raising(httpx2.ReadTimeout), 200, "mcp_timeout"),
])
def test_a_refresh_is_mcp_auth_required_only_when_the_token_endpoint_refuses_the_grant(world, answer, status, code):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.token_endpoint(AS + "/token")
    _authorize(world, cid)
    before = world.people.store.one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (cid,))["credential_sealed"]
    world.remote.answer(AS + "/token", answer, method="POST", status=status)
    with pytest.raises(ServiceError) as caught:
        _expire_and_refresh(world, cid)
    err = caught.value
    assert (err.code, err.detail.get("cause")) == (code, "token_refresh"), (err.code, err.detail)
    if code != "mcp_auth_required":
        assert err.status != 401 and "重新认证" not in err.message
    # nothing the failure said was stored over the authorization
    assert world.people.store.one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (cid,))["credential_sealed"] == before


def test_a_token_endpoints_own_words_are_not_kept(world):
    cid = oauth_connection(world, {"client_id": "fixture-preregistered"})
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS)
    world.remote.token_endpoint(AS + "/token")
    _authorize(world, cid)
    world.remote.answer(AS + "/token", {"error": "<a href=//evil>re-authorize here</a>"}, method="POST", status=400)
    with pytest.raises(ServiceError) as caught:
        _expire_and_refresh(world, cid)
    assert caught.value.detail["error"] == "other" and "evil" not in json.dumps(caught.value.detail)


# ---------------------------------------------------------------------------------------------- K3 / C2: resolver busy


def test_a_resolver_pool_that_refuses_is_a_local_refusal(tmp_path, monkeypatch):
    """`webhook_delivery.ResolverBusy` is this process's own refusal (no resolver free in time), not the
    name's: it must not be `mcp_unresolvable`, which marks the connection failed and counts toward its
    circuit breaker for everyone."""
    monkeypatch.setattr(McpRuntime, "TEST_TRANSPORT_FOR", None)
    store = Store(tmp_path / "d")
    rt = McpRuntime(store).start()
    try:
        def busy(*a, **kw):                                  # FIXTURE resolver: no slot free for this owner
            raise webhook_delivery.ResolverBusy("fixture: busy")

        def unresolvable(*a, **kw):                          # FIXTURE resolver: the name does not resolve
            raise webhook_delivery.UrlUnresolvable("fixture: no such name")
        conn = {"id": "mcpc_fixture", "url": "https://shared.fixture.test/mcp", "owner_id": "ws-fixture"}
        results = {}
        for name, resolver in (("busy", busy), ("unresolvable", unresolvable)):
            monkeypatch.setattr(rt_mod, "resolve_checked", resolver)
            with pytest.raises(ServiceError) as caught:
                rt.discover(conn, None, user_id="u-fixture", timeout=5)
            results[name] = caught.value
    finally:
        rt.close()
    busy_err = results["busy"]
    assert (busy_err.status, busy_err.code) == (429, "mcp_resolver_busy")
    assert busy_err.detail["retryable"] is True and busy_err.detail["retry_after"] > 0
    assert busy_err.code not in SERVER_FAILURES
    assert results["unresolvable"].code == "mcp_unresolvable"   # a name that does not resolve is still the server's


# ---------------------------------------------------------------------------------------------- C3: off the loop


def test_registration_sealing_and_unsealing_never_run_on_the_runtime_loop(world):
    """The runtime loop carries every outbound MCP exchange of the process: a database write there
    (the process-wide write lock, `BEGIN IMMEDIATE`) or a seal would hold up everyone's calls,
    deadlines and cancellations behind another writer."""
    cid = oauth_connection(world)
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS, registration_endpoint=AS + "/register")
    world.remote.answer(AS + "/register", {"client_id": "dyn-fixture", "client_secret": "dyn-fixture-secret"}, method="POST",
                        status=201)
    world.remote.token_endpoint(AS + "/token")
    on_loop: list[str] = []
    store = world.people.store

    def watched(fn, name):
        def inner(*a, **kw):
            if threading.current_thread().name == "synwevo-mcp-client":
                on_loop.append(name)
            return fn(*a, **kw)
        return inner
    world.monkeypatch.setattr(store, "execute", watched(store.execute, "execute"))
    world.monkeypatch.setattr(store, "one", watched(store.one, "one"))
    world.monkeypatch.setattr(sealed, "seal", watched(sealed.seal, "seal"))
    world.monkeypatch.setattr(sealed, "unseal", watched(sealed.unseal, "unseal"))
    started = _authorize(world, cid)                          # registers, then exchanges with the registration's secret
    assert started["registration"] == "dynamic"
    _expire_and_refresh(world, cid)
    (form, auth) = world.remote.token_requests(AS + "/token")[-1]
    assert form["grant_type"] == "refresh_token" and auth                 # the secret was sent, opened off the loop
    assert on_loop == []


def test_a_registration_waiting_for_the_database_holds_up_no_other_exchange(world):
    cid = oauth_connection(world)
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS, registration_endpoint=AS + "/register")
    store, rt = world.people.store, world.oauth.runtime
    held, registered = threading.Event(), threading.Event()
    world.remote.answer(AS + "/register", lambda request: (registered.set(), {"client_id": "dyn-fixture"})[1], method="POST",
                        status=201)

    def writer():                                            # FIXTURE: another writer of the process, in a transaction
        with store.tx():
            store.execute("UPDATE mcp_connections SET updated_at=updated_at WHERE id=?", (cid,))
            held.set()
            registered.wait(10)
            threading.Event().wait(1.5)
    w = threading.Thread(target=writer)
    w.start()
    assert held.wait(5)
    out: list = []
    s = threading.Thread(target=lambda: out.append(world.oauth.start(world.alice, cid)))
    s.start()
    assert registered.wait(10)
    threading.Event().wait(0.3)                              # the registration's answer has arrived; its write waits

    async def beat(submitted):
        return time.monotonic() - submitted
    latency = rt.run(beat(time.monotonic()), timeout=10)
    w.join(10)
    s.join(15)
    assert latency < 0.5, f"an unrelated exchange waited {latency:.2f}s behind a database write on the loop"
    assert out and out[0]["registration"] == "dynamic"
    stored = json.loads(store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (cid,))["oauth_client_json"])
    assert stored["client_id"] == "dyn-fixture" and urllib.parse.urlsplit(stored["registration_endpoint"]).hostname == "as.fixture.test"
