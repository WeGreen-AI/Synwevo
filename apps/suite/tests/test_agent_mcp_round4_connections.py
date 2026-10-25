"""The connection service's guarantees, fourth review round: a credential is given only together with
the address it is for, a platform stop's destination is the server's however its path is spelled,
and a failure at the authorization server is recorded as such, not as the MCP server's.

TEST DOUBLES, each labelled: the external MCP server is the FIXTURE server of test_agent_mcp.py
behind the FIXTURE recording transport and token endpoints of test_agent_mcp_connections.py; a
refresh that fails is a FIXTURE stand-in for `OutboundOAuth.refresh`'s token request, raising what the
runtime raises when the authorization server cannot be reached.
"""
from __future__ import annotations

import json

import pytest

from synwevo_core.errors import ServiceError
from synwevo_core.store import now
from synwevo_core.suite_schema.mcp import destination_key
from synwevo_suite.agent.mcp.oauth import OutboundOAuth

from .test_agent import op
from .test_agent_mcp import FIXTURE_URL, connect
from .test_agent_mcp_connections import fixture_oauth_connection, row, user, world  # noqa: F401 — `world` is a fixture
from .test_agent_mcp_round3_connections import bearer_workspace_connection


# ---------------------------------------------------------------- a credential names its address
def test_a_credential_without_the_address_it_is_for_is_refused_and_sends_nothing(world):
    """Any caller, not only the page: a machine credential, the CLI or a script giving a token (or an
    OAuth client) for a connection says which address it is for — the one it set, or the one it was
    shown — or another manager's new address could be the one to receive it."""
    cid = bearer_workspace_connection(world)
    before = row(world, cid)["credential_sealed"]
    for given in ({"credential": "fixture-token-server-1-BBBB"}, {"auth_kind": "bearer", "credential": "fixture-token-server-1-BBBB"}):
        refused = op(world, "agent.mcp.connection.update", {"connection_id": cid, **given}, expect=422)
        assert refused["error"] == "invalid_input" and refused["detail"]["field"] == "expected_url", refused
        assert "BBBB" not in json.dumps(refused)
    assert row(world, cid)["credential_sealed"] == before
    oauth = fixture_oauth_connection(world)
    refused = op(world, "agent.mcp.connection.update", {"connection_id": oauth, "oauth_client": {"client_id": "another"}}, expect=422)
    assert refused["detail"]["field"] == "expected_url"
    # With the address — shown, or set in the same change — it is written.
    saved = op(world, "agent.mcp.connection.update", {"connection_id": cid, "credential": "fixture-token-server-1-CCCC",
                                                      "expected_url": FIXTURE_URL})["output"]
    assert saved["credential_hint"] == "…CCCC"
    saved = op(world, "agent.mcp.connection.update", {"connection_id": cid, "credential": "fixture-token-server-1-DDDD",
                                                      "url": FIXTURE_URL})["output"]
    assert saved["credential_hint"] == "…DDDD"
    # Changes without a secret need no address.
    assert op(world, "agent.mcp.connection.update", {"connection_id": cid, "name": "Renamed"})["output"]["name"] == "Renamed"


# ---------------------------------------------------------------- one destination, however written
@pytest.mark.parametrize("spelling", ["https://weather.fixture.test/MCP", "https://weather.fixture.test/Mcp/",
                                      "https://weather.fixture.test/mcp;v=1", "https://weather.fixture.test/mcp;jsessionid=x/",
                                      "https://weather.fixture.test/%4dCP", "https://weather.fixture.test/mcp%3Bv=1"])
def test_a_servers_address_in_another_case_or_with_path_parameters_is_one_destination(spelling):
    assert destination_key(spelling) == destination_key("https://weather.fixture.test/mcp")


@pytest.mark.parametrize("other", ["https://weather.fixture.test/mcp2", "https://weather.fixture.test/v2/mcp",
                                   "https://weather.fixture.test:8443/mcp", "https://other.fixture.test/mcp"])
def test_another_path_port_or_host_is_another_destination(other):
    assert destination_key(other) != destination_key("https://weather.fixture.test/mcp")


def test_a_stopped_address_written_in_another_case_or_with_path_parameters_gets_no_connection(world):
    """Through the service, not only the key: once the platform has stopped the fixture server's
    address, no new connection and no re-pointing reaches it under another spelling of its path."""
    stopped = connect(world)["id"]
    # FIXTURE: the platform stop as synwevo_identity.admin_mcp writes it.
    world.store.execute("UPDATE mcp_connections SET status='disabled', platform_disabled_at=?, platform_disabled_by=? WHERE id=?",
                        (now(), "fixture-platform-admin", stopped))
    for spelling in ("https://fixture-notes.test/MCP", "https://fixture-notes.test/mcp;v=1",
                     "https://FIXTURE-notes.test/Mcp;jsessionid=x/"):
        refused = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Again", "url": spelling}, expect=403)
        assert refused["error"] == "mcp_destination_stopped", spelling
    elsewhere = op(world, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Elsewhere",
                                                          "url": "https://fixture-notes.test/mcp2"})["output"]
    refused = op(world, "agent.mcp.connection.update", {"connection_id": elsewhere["id"], "url": "https://fixture-notes.test/MCP;v=2"},
                 expect=403)
    assert refused["error"] == "mcp_destination_stopped"
    assert world.store.one("SELECT url FROM mcp_connections WHERE id=?", (elsewhere["id"],))["url"] == "https://fixture-notes.test/mcp2"


# ---------------------------------------------------------------- where a failure happened
def test_a_refresh_that_could_not_reach_the_authorization_server_is_recorded_as_its_failure(world, monkeypatch):
    """The MCP server was never asked: the connection's last error says the authorization server
    failed, refreshing the token, so its page does not send the person to check the server's address."""
    cid = fixture_oauth_connection(world, expires_in=5)

    def refresh(self, conn, tokens):
        raise ServiceError(502, "mcp_unreachable", "刷新令牌时无法连接授权服务器", {"retryable": True, "cause": "token_refresh"})
    monkeypatch.setattr(OutboundOAuth, "refresh", refresh)
    with pytest.raises(ServiceError):
        world.connections.test(user(world, "alice"), cid, origin="discover")
    shown = op(world, "agent.mcp.connection.get", {"connection_id": cid})["output"]
    assert shown["health"] == "failed" and shown["last_error"]["code"] == "mcp_unreachable"
    assert shown["last_error"]["cause"] == "token_refresh"
    # The server's own failure says nothing of a cause, and a success forgets it.
    monkeypatch.setattr(OutboundOAuth, "refresh", lambda self, conn, tokens: (_ for _ in ()).throw(
        ServiceError(502, "mcp_unreachable", "fixture: unreachable", {"retryable": True})))
    with pytest.raises(ServiceError):
        world.connections.test(user(world, "alice"), cid, origin="discover")
    assert op(world, "agent.mcp.connection.get", {"connection_id": cid})["output"]["last_error"]["cause"] is None
    world.connections._health(cid, "ok")
    assert row(world, cid)["last_error_cause"] is None
