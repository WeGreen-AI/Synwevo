"""A platform stop of an MCP connection holds against its destination, on the composed deployment
(`synwevo_server.app.create_app`, role `all`): the owner cannot delete the stopped connection (it is
the record of the stop, and stays in the administration's overview), cannot add the same server again
under another connection or another spelling of its address, and a second connection to it stops
working too — until a platform administrator lifts the stop.

FIXTURES, each labelled: the composed deployment, its external MCP server and its platform
administrator are those of test_mcp_admin_controls.py (the official SDK's `MCPServer` in process
through an ASGI transport; the platform role granted through `platform_fixture.make_platform_admin`).
"""
from __future__ import annotations

import json

from tests.integration.test_mcp_admin_controls import REASON, SERVER_URL, env, op, post  # noqa: F401

SPELLINGS = ("https://weather.fixture.test/mcp", "https://WEATHER.fixture.test:443/mcp/", "https://weather.fixture.test/mcp?fixture=1",
             "https://weather.fixture.test/./tools/../mcp", "https://weather.fixture.test./m%63p",
             # Written in another case, or with path parameters, on a server that routes either way.
             "https://weather.fixture.test/MCP", "https://weather.fixture.test/mcp;v=1")


def test_a_platform_stop_holds_against_deleting_recreating_and_a_second_connection_to_the_same_server(env):
    alice, root, store = env["alice"], env["root"], env["store"]
    stopped = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather", "url": SERVER_URL})["id"]
    second = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather again", "url": SERVER_URL})["id"]
    for cid in (stopped, second):
        assert op(alice, "agent.mcp.connection.test", {"connection_id": cid})["health"] == "ok"
        op(alice, "agent.mcp.binding.set", {"connection_id": cid, "enabled": True})
    assert len(op(alice, "agent.mcp.binding.list", {})["tools"]) == 2
    assert post(root, f"/api/admin/mcp/connections/{stopped}/disable", {"reason": REASON}).status_code == 200

    # The stopped connection cannot be deleted: it stays, stopped, at the head of the overview.
    assert op(alice, "agent.mcp.connection.delete", {"connection_id": stopped}, expect=409)["error"] == "mcp_platform_disabled"
    assert store.one("SELECT deleted_at FROM mcp_connections WHERE id=?", (stopped,))["deleted_at"] is None
    overview = root.get("/api/admin/mcp/overview").json()
    assert overview["items"][0]["id"] == stopped and overview["items"][0]["platform_disabled"] is True
    assert next(i for i in overview["items"] if i["id"] == second)["destination_stopped"] is True
    closed = overview["stopped_destinations"]
    assert [(d["host"], d["connection_id"], d["connection_name"], d["other_connections"]) for d in closed] == [
        ("weather.fixture.test", stopped, "Weather", 1)]
    assert "/mcp" not in json.dumps(closed), "the administration sees the host, never the address's path"

    # The same server is not added again, however its address is written.
    for url in SPELLINGS:
        refused = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather once more", "url": url}, expect=403)
        assert refused["error"] == "mcp_destination_stopped", url
    # The second connection to it no longer reaches it, and cannot be given a credential for it.
    assert op(alice, "agent.mcp.binding.list", {})["tools"] == []
    assert op(alice, "agent.mcp.connection.get", {"connection_id": second})["destination_stopped"] is True
    assert op(alice, "agent.mcp.connection.test", {"connection_id": second}, expect=403)["error"] == "mcp_destination_stopped"
    assert op(alice, "agent.mcp.connection.update", {"connection_id": second, "auth_kind": "bearer", "credential": "fixture-token-abcdefgh"},
              expect=403)["error"] == "mcp_destination_stopped"
    assert op(alice, "agent.mcp.connection.disable", {"connection_id": second})["status"] == "disabled"
    assert op(alice, "agent.mcp.connection.enable", {"connection_id": second}, expect=403)["error"] == "mcp_destination_stopped"

    # Lifting the stop opens the address again.
    assert post(root, f"/api/admin/mcp/connections/{stopped}/lift", {"reason": REASON}).status_code == 200
    assert root.get("/api/admin/mcp/overview").json()["stopped_destinations"] == []
    assert op(alice, "agent.mcp.connection.enable", {"connection_id": second})["status"] == "active"
    assert op(alice, "agent.mcp.connection.test", {"connection_id": second})["health"] == "ok"
    assert [t["tool"] for t in op(alice, "agent.mcp.binding.list", {})["tools"]] == ["forecast"]
    again = op(alice, "agent.mcp.connection.create", {"owner_kind": "user", "name": "Weather once more", "url": SPELLINGS[1]})
    assert again["platform_disabled"] is False and again["destination_stopped"] is False
    # The lifted connection is its owner's to delete again.
    assert op(alice, "agent.mcp.connection.delete", {"connection_id": stopped}) == {"deleted": stopped}
