"""The Agent's outbound OAuth, fourth review round: the textbook mix-up (an attacker's own
authorization server that sends the browser on to an honest one), an authorization server whose
endpoints live beside its issuer, the person's own client kept to the server it belongs to, and a
manager removed while the code is being exchanged.

FIXTURES, each labelled: the MCP server, its resource metadata and every authorization server are the
FIXTURE HTTP responder of test_agent_mcp_oauth.py (`Remote`, handed to the runtime in place of its
pinned network transport); an answer may first let something else happen (a membership change), as
if it landed while that request was under way. The person's browser is not involved: the honest
server's redirect back is the callback the tests call, with the code it would carry. The people and
workspaces are the suite's fixture accounts. No network is used.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core.errors import ServiceError
from synwevo_core.store import now
from synwevo_suite.agent.mcp.oauth import _refresh_failure, client_metadata_document

from .test_agent_mcp_oauth import (AS, HTTPS_APP, MCP, PRM, _expire_and_refresh, basic, connection, fixture_flow, flows,  # noqa: F401 — `world` is a fixture
                                   oauth_connection, query, refused, world)
from .test_agent_mcp_round3_oauth import ATTACKER, as_metadata, token_posts, workspace_connection

ATTACKER_META = ATTACKER + "/.well-known/oauth-authorization-server"


def attackers_own_server(world, *, iss: bool, **extra) -> None:
    """FIXTURE: the connection's server is the attacker's, and so is its authorization server — both
    endpoints on its own origin, so nothing about the metadata is out of place. Its /authorize would
    send the browser on to an honest server that accepts Client ID Metadata Documents."""
    world.remote.challenge()                                   # no resource metadata: its own origin is the issuer
    as_metadata(world, ATTACKER_META, ATTACKER, authorization=ATTACKER + "/authorize", token=ATTACKER + "/token", iss=iss,
                client_id_metadata_document_supported=True, **extra)
    world.remote.token_endpoint(ATTACKER + "/token")


def stored_client(world, cid: str):
    return world.people.store.one("SELECT oauth_client_json FROM mcp_connections WHERE id=?", (cid,))["oauth_client_json"]


# ---------------------------------------------------------------------------------------------- the textbook mix-up
def test_this_platforms_client_document_is_never_used_with_a_server_that_does_not_say_it_sends_iss(world):
    """RFC 9700 §4.4.1: the honest server accepts this deployment's one public client id as readily
    as the attacker's does, redirects back here with its code, and only `iss` could tell whose
    callback it is. Without the server's word that it sends `iss`, the document is not used."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    attackers_own_server(world, iss=False)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_registration_unavailable", "iss_unsupported")
    assert flows(world, cid) == [] and token_posts(world) == [] and stored_client(world, cid) is None


def test_the_honest_servers_code_is_not_exchanged_at_a_server_held_to_its_iss(world):
    """A server that says it sends `iss` gets the document; the honest server's redirect back — with
    no `iss`, or its own — is refused before any code or PKCE verifier is sent anywhere."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    attackers_own_server(world, iss=True)
    for sent in (None, "https://honest-as.fixture.test"):
        started = world.oauth.start(world.alice, cid)
        assert started["registration"] == "cimd"
        with pytest.raises(ServiceError) as caught:
            world.oauth.callback(world.alice, state=query(started)["state"], code="HONEST-AS-CODE", iss=sent, error=None)
        assert caught.value.code == "mcp_oauth_issuer_mismatch"
    assert token_posts(world) == []
    started = world.oauth.start(world.alice, cid)                # its own response, with its own iss, completes
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=ATTACKER, error=None)["ok"]


def test_a_registration_endpoint_cannot_hand_out_this_platforms_client_id_as_its_own(world):
    """The same mix-up through dynamic registration: the attacker's registration endpoint answers
    with this deployment's document address as the client id it 'issued'."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    world.remote.challenge()
    as_metadata(world, ATTACKER_META, ATTACKER, authorization=ATTACKER + "/authorize", token=ATTACKER + "/token", iss=False,
                registration_endpoint=ATTACKER + "/register")
    world.remote.answer(ATTACKER + "/register", {"client_id": client_metadata_document()["client_id"]}, method="POST", status=201)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_registration_unavailable", "iss_unsupported")
    assert flows(world, cid) == [] and stored_client(world, cid) is None


def test_a_server_that_accepts_the_document_but_sends_no_iss_is_registered_with_instead(world):
    """Its own dynamic registration (on its issuer's origin) is still used: the client it issues is
    its own, not one every server accepts."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    attackers_own_server(world, iss=False, registration_endpoint=ATTACKER + "/register")
    world.remote.answer(ATTACKER + "/register", {"client_id": "issued-by-this-server"}, method="POST", status=201)
    started = world.oauth.start(world.alice, cid)
    assert started["registration"] == "dynamic" and query(started)["client_id"] == "issued-by-this-server"


# ---------------------------------------------------------------------------------------------- endpoints beside the issuer
COGNITO_ISSUER = "https://cognito-idp.fixture.test/pool_1"       # FIXTURE: an issuer on one origin ...
COGNITO_AUTH = "https://pool-domain.auth.fixture.test"            # ... and its hosted sign-in domain on another


def split_origin_server(world, **extra) -> None:
    """FIXTURE: an authorization server that keeps its endpoints on another origin than its issuer and
    says nothing of `iss` (as Amazon Cognito user pools and Google do)."""
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, COGNITO_ISSUER)
    as_metadata(world, "https://cognito-idp.fixture.test/.well-known/oauth-authorization-server/pool_1", COGNITO_ISSUER,
                authorization=COGNITO_AUTH + "/oauth2/authorize", token=COGNITO_AUTH + "/oauth2/token", iss=False, **extra)
    world.remote.token_endpoint(COGNITO_AUTH + "/oauth2/token")


def test_a_client_the_person_registered_is_used_whatever_origin_the_endpoints_are_on(world):
    cid = oauth_connection(world, {"client_id": "fixture-pool-client"})
    split_origin_server(world)
    started = world.oauth.start(world.alice, cid)
    assert started["authorization_url"].startswith(COGNITO_AUTH + "/oauth2/authorize?") and started["registration"] == "preregistered"
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=None, error=None)["ok"]
    assert token_posts(world) == [COGNITO_AUTH + "/oauth2/token"]


def test_this_platforms_client_document_is_refused_there_by_where_the_endpoints_are(world):
    """The document's client id is valid at every server, so without `iss` the endpoints' origin is
    named as what makes the answer's source unknowable — and nothing is registered or begun."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    split_origin_server(world, client_id_metadata_document_supported=True)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason"), err.detail.get("endpoints")) == (
        "mcp_oauth_discovery_failed", "endpoint_origin_mismatch", ["authorization_endpoint", "token_endpoint"])
    assert flows(world, cid) == [] and token_posts(world) == []


def test_a_dynamic_registration_is_that_servers_own_client_whatever_origin_its_endpoints_are_on(world):
    """Supersedes this round's first reading, which held a dynamic registration to the issuer's origin
    too. The rule is kept where it protects — the one client id every server accepts: a client a server
    issued through its own registration endpoint (on its issuer's origin) is valid there only, and for
    an attacker to have an honest server answer this callback for it, it must first register a client
    there in this deployment's name — after which its own authorization endpoint can send the browser
    on wherever the endpoints are, so the endpoints' origin says nothing (oauth._client)."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    split_origin_server(world, client_id_metadata_document_supported=True,
                        registration_endpoint="https://cognito-idp.fixture.test/register")
    world.remote.answer("https://cognito-idp.fixture.test/register", {"client_id": "issued-by-the-pool"}, method="POST",
                        status=201)
    started = world.oauth.start(world.alice, cid)
    assert started["registration"] == "dynamic" and query(started)["client_id"] == "issued-by-the-pool"
    assert started["authorization_url"].startswith(COGNITO_AUTH + "/oauth2/authorize?")
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=None, error=None)["ok"]
    assert token_posts(world) == [COGNITO_AUTH + "/oauth2/token"]


# ---------------------------------------------------------------------------------------------- the person's own client, at its own server
OTHER_AS = "https://other-as.fixture.test"                        # FIXTURE: the server the MCP server names later


def authorized_with_own_client(world) -> str:
    """FIXTURE: a connection with the person's pre-registered client (and its secret), authorized once
    at AS, which sends no `iss`; then its MCP server names another authorization server, one whose
    endpoints are on its own origin and that sends no `iss` either."""
    cid = oauth_connection(world, {"client_id": "fixture-preregistered", "client_secret": "fixture-preregistered-secret"})
    world.remote.challenge(resource_metadata=PRM)
    world.remote.resource_metadata(PRM, MCP, AS)
    world.remote.authorization_server(AS + "/.well-known/oauth-authorization-server", AS, iss_parameter=False)
    world.remote.token_endpoint(AS + "/token")
    started = world.oauth.start(world.alice, cid)
    assert world.oauth.callback(world.alice, state=query(started)["state"], code="fixture-code", iss=None, error=None)["ok"]
    world.remote.resource_metadata(PRM, MCP, OTHER_AS)
    world.remote.authorization_server(OTHER_AS + "/.well-known/oauth-authorization-server", OTHER_AS, iss_parameter=False)
    world.remote.token_endpoint(OTHER_AS + "/token")
    return cid


def test_a_client_the_person_registered_is_never_handed_to_another_authorization_server(world):
    """MCP authorization, "Authorization Server Binding": pre-registered credentials belong to the
    server that issued them, and a client surfaces an error rather than use them at another. The
    mix-up against such a client is exactly that use: the other server's authorization endpoint sends
    the browser to the one the client belongs to, and its code, the PKCE verifier and the client's
    secret come back for the other server's token endpoint."""
    cid = authorized_with_own_client(world)
    assert json.loads(stored_client(world, cid))["issuer"] == AS     # learnt from the completed authorization
    err = refused(world, cid)
    assert err.code == "mcp_oauth_issuer_changed" and err.detail == {"issuer": OTHER_AS, "registered_issuer": AS}
    assert [f["outcome"] for f in flows(world, cid)] == ["ok"] and OTHER_AS + "/token" not in token_posts(world)
    # The authorization it has still refreshes where it was given, with the client's own secret.
    _expire_and_refresh(world, cid)
    (form, auth) = world.remote.token_requests(AS + "/token")[-1]
    assert (form["grant_type"], form["client_id"], auth) == ("refresh_token", "fixture-preregistered",
                                                             basic("fixture-preregistered", "fixture-preregistered-secret"))
    assert world.oauth._auth_headers(connection(world, cid), "fixture-preregistered", OTHER_AS) == {}
    # A client the person registers at the new server is that server's.
    world.api.state.mcp_connections.update(world.alice, cid, oauth_client={"client_id": "fixture-other-client"}, expected_url=MCP)
    started = world.oauth.start(world.alice, cid)
    assert started["registration"] == "preregistered" and started["authorization_url"].startswith(OTHER_AS + "/authorize?")
    assert query(started)["client_id"] == "fixture-other-client"


def test_a_client_recorded_before_its_server_was_kept_belongs_to_the_server_it_was_last_authorized_at(world):
    cid = authorized_with_own_client(world)
    # FIXTURE: the record as it was written before the callback kept the issuer on it.
    world.people.store.execute("UPDATE mcp_connections SET oauth_client_json=? WHERE id=?",
                               (json.dumps({"kind": "preregistered", "client_id": "fixture-preregistered"}), cid))
    err = refused(world, cid)
    assert err.code == "mcp_oauth_issuer_changed" and err.detail["registered_issuer"] == AS


def test_a_callback_for_the_persons_client_at_another_server_exchanges_nothing(world):
    """However a flow for another server came to be (here a FIXTURE flow, as start would have recorded
    it before the client was kept to its server), its code is not exchanged and nothing is sealed."""
    cid = authorized_with_own_client(world)
    sealed_before = connection(world, cid)["credential_sealed"]
    fid = fixture_flow(world, cid, "https://elsewhere.fixture.test/mcp")
    state = world.people.store.one("SELECT state FROM mcp_oauth_flows WHERE id=?", (fid,))["state"]
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(world.alice, state=state, code="fixture-code", iss=None, error=None)
    assert caught.value.code == "mcp_oauth_issuer_changed"
    assert "elsewhere.fixture.test" not in world.remote.hosts() and connection(world, cid)["credential_sealed"] == sealed_before
    assert world.people.store.one("SELECT outcome FROM mcp_oauth_flows WHERE id=?", (fid,))["outcome"] == "refused"


def test_a_callback_for_this_platforms_client_document_needs_iss_whatever_its_flow_recorded(world):
    """A flow for the document recorded before the document needed `iss` (FIXTURE: iss_supported 0)
    still has its callback refused without one, before any code is sent."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    fid = fixture_flow(world, cid, "https://elsewhere.fixture.test/mcp")
    world.people.store.execute("UPDATE mcp_oauth_flows SET client_id=? WHERE id=?", (client_metadata_document()["client_id"], fid))
    world.remote.token_endpoint("https://elsewhere.fixture.test/token")
    state = world.people.store.one("SELECT state FROM mcp_oauth_flows WHERE id=?", (fid,))["state"]
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(world.alice, state=state, code="HONEST-AS-CODE", iss=None, error=None)
    assert caught.value.code == "mcp_oauth_issuer_mismatch" and token_posts(world) == []
    assert world.people.store.one("SELECT outcome FROM mcp_oauth_flows WHERE id=?", (fid,))["outcome"] == "issuer_missing"


def test_a_refresh_answer_too_large_to_read_is_the_authorization_servers_not_the_mcp_servers():
    """The pinned client cuts any answer at the document bound (runtime.MAX_DOCUMENT_BYTES, which the
    FIXTURE transport does not apply, so the refusal it raises is given here as the runtime words it):
    during a refresh it is the authorization server that answered too much, not the MCP server."""
    err = _refresh_failure(ServiceError(413, "mcp_response_too_large", "MCP 服务的响应超过允许的大小",
                                        {"max_bytes": 1024 * 1024, "retryable": False}))
    assert (err.status, err.code, err.detail) == (413, "mcp_response_too_large",
                                                  {"max_bytes": 1024 * 1024, "retryable": False, "cause": "token_refresh"})
    assert "授权服务器" in err.message and "MCP 服务" not in err.message


# ---------------------------------------------------------------------------------------------- who decides, to the end
@pytest.mark.parametrize("change, code", [("removed", "mcp_connection_not_found"), ("demoted", "mcp_manage_forbidden"),
                                          ("stopped", "mcp_platform_disabled")])
def test_a_manager_removed_while_the_code_is_exchanged_has_nothing_sealed(world, change, code):
    cid, began = workspace_connection(world)
    p, store = world.people, world.people.store

    def token(request):
        # FIXTURE: the change lands while the token endpoint is answering the exchange.
        if change == "removed":
            p.ident.remove_member(p.a, p.uid("bob"))
        elif change == "demoted":
            p.ident.change_role(p.a, p.uid("bob"), "editor")
        else:
            store.execute("UPDATE mcp_connections SET status='disabled', platform_disabled_at=?, platform_disabled_by=? WHERE id=?",
                          (now(), p.uid("alice"), cid))
        return {"access_token": "bobs-own-external-account", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "r"}
    world.remote.answer(AS + "/token", token, method="POST")
    with pytest.raises(ServiceError) as caught:
        world.oauth.callback(began["bob"], state=began["state"], code="fixture-code", iss=AS, error=None)
    assert caught.value.code == code
    assert token_posts(world) == [AS + "/token"]                 # the exchange happened, and its answer is dropped
    assert store.one("SELECT credential_sealed FROM mcp_connections WHERE id=?", (cid,))["credential_sealed"] is None
    assert flows(world, cid)[-1]["outcome"] == "refused"
