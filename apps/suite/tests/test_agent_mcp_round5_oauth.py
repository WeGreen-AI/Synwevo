"""The Agent's outbound OAuth, fifth review round: this deployment's Client ID Metadata Document is
recognised by its origin, not by one spelling of its address, so a registration endpoint cannot hand it
back as "its own" client in another spelling and escape the rule that keeps the document to servers
that send `iss`; and the person's own client is authorized at its own server when the MCP server lists
several authorization servers.

FIXTURES, each labelled: the MCP server, its resource metadata and every authorization server are the
FIXTURE HTTP responder of test_agent_mcp_oauth.py (`Remote`, handed to the runtime in place of its
pinned network transport). No network is used.
"""
from __future__ import annotations

import pytest

from synwevo_suite.agent.mcp.oauth import client_metadata_document, is_deployment_client_id

from .test_agent_mcp_oauth import AS, HTTPS_APP, MCP, PRM, flows, oauth_connection, query, refused, world  # noqa: F401 — `world` is a fixture
from .test_agent_mcp_round3_oauth import ATTACKER, as_metadata
from .test_agent_mcp_round4_oauth import ATTACKER_META, OTHER_AS, authorized_with_own_client, stored_client

#: Spellings of this deployment's document address (HTTPS_APP + the metadata path) that a server
#: fetching client ids resolves to the same document, and one other path on the same origin.
VARIANTS = ("https://WORKPLACE.testserver/.well-known/mcp-client/synwevo-agent.json",
            "{base}:443/.well-known/mcp-client/synwevo-agent.json",
            "{base}/.well-known/mcp-client/synwevo-agent.json?v=1",
            "{base}/.well-known/MCP-CLIENT/synwevo-agent.json",
            "{base}/anything-else-on-this-origin")


def spell(variant: str) -> str:
    return variant.replace("{base}", HTTPS_APP)


def test_the_document_is_recognised_by_its_origin_however_it_is_spelt(world):
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    assert is_deployment_client_id(client_metadata_document()["client_id"])
    for variant in VARIANTS:
        assert is_deployment_client_id(spell(variant)), variant
    for other in ("https://other.fixture.test/.well-known/mcp-client/synwevo-agent.json", "issued-by-this-server",
                  "http" + HTTPS_APP[5:] + "/.well-known/mcp-client/synwevo-agent.json", "", None):
        assert not is_deployment_client_id(other), other


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_registration_endpoint_cannot_hand_back_the_document_in_another_spelling(world, variant):
    """The mix-up of round four through dynamic registration, with the document's address written
    another way: still this deployment's one public client id, so still refused where no `iss` is sent."""
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    world.remote.challenge()
    as_metadata(world, ATTACKER_META, ATTACKER, authorization=ATTACKER + "/authorize", token=ATTACKER + "/token", iss=False,
                registration_endpoint=ATTACKER + "/register")
    world.remote.answer(ATTACKER + "/register", {"client_id": spell(variant)}, method="POST", status=201)
    err = refused(world, cid)
    assert (err.code, err.detail.get("reason")) == ("mcp_oauth_registration_unavailable", "iss_unsupported")
    assert flows(world, cid) == [] and stored_client(world, cid) is None


def test_a_client_id_on_another_origin_is_the_servers_own(world):
    world.monkeypatch.setenv("SYNWEVO_APP_URL", HTTPS_APP)
    cid = oauth_connection(world)
    world.remote.challenge()
    as_metadata(world, ATTACKER_META, ATTACKER, authorization=ATTACKER + "/authorize", token=ATTACKER + "/token", iss=False,
                registration_endpoint=ATTACKER + "/register")
    world.remote.answer(ATTACKER + "/register", {"client_id": ATTACKER + "/clients/issued"}, method="POST", status=201)
    started = world.oauth.start(world.alice, cid)
    assert started["registration"] == "dynamic" and query(started)["client_id"] == ATTACKER + "/clients/issued"


# ---------------------------------------------------------------------------------------------- several authorization servers
def test_the_persons_client_is_used_at_its_own_server_when_the_resource_lists_several(world):
    """MCP authorization, "Authorization Server Discovery": the choice among the listed servers is the
    client's. A client the person registered at one of them is authorized there, wherever it is listed."""
    cid = authorized_with_own_client(world)
    # FIXTURE: the MCP server now lists another authorization server first, and still the one the
    # person's client belongs to.
    world.remote.answer(PRM, {"resource": MCP, "authorization_servers": [OTHER_AS, AS]})
    started = world.oauth.start(world.alice, cid)
    assert started["registration"] == "preregistered" and started["authorization_url"].startswith(AS + "/authorize?")
    assert query(started)["client_id"] == "fixture-preregistered" and started["issuer"] == AS


def test_a_resource_that_no_longer_lists_the_persons_server_is_refused_as_before(world):
    cid = authorized_with_own_client(world)
    world.remote.answer(PRM, {"resource": MCP, "authorization_servers": [OTHER_AS]})
    err = refused(world, cid)
    assert err.code == "mcp_oauth_issuer_changed" and err.detail == {"issuer": OTHER_AS, "registered_issuer": AS}
