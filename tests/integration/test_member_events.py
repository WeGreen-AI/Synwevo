"""Membership and invitation changes as events (API-5): a directory integration hears member.added /
changed / removed and invitation.created / accepted / revoked from the workspace's event feed and
webhook subscriptions, each written in the transaction that changed the row
(synwevo_core.member_events). Composed app: the identity service writes the rows, Docs serves the
feed and the subscriptions, one Store carries both. Synthetic accounts only."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core.auth import Auth
from synwevo_core.identity import Identity
from synwevo_core.member_events import MEMBER_EVENT_TYPES
from synwevo_core.store import Store

PW = "a-sufficiently-long-passphrase"
TYPES = ",".join(MEMBER_EVENT_TYPES)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_API_RATE_PER_MIN", "10000")
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE", "127.0.0.1")   # a loopback receiver address only
    monkeypatch.setenv("SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP", "1")
    from synwevo_server.app import create_app
    from synwevo_identity.invites import Invites
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    store = Store(tmp_path / "d")
    ident = Identity(store)
    alice = ident.create_user("alice@example.test", PW, status="active")
    bob = ident.create_user("bob@example.test", PW, status="active", with_personal_workspace=False)
    for u in (alice, bob):
        store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
    ws = ident.create_workspace("Workspace W", owner_id=alice["id"])["id"]
    other = ident.create_workspace("Workspace other", owner_id=bob["id"])["id"]
    key, _ = Auth(store).create_key("events fixture", ws, ("read", "write", "admin"))
    other_key, _ = Auth(store).create_key("other fixture", other, ("read",))
    with TestClient(api) as c:
        yield {"api": api, "c": c, "store": store, "ident": ident, "invites": Invites(store, ident),
               "alice": alice, "bob": bob, "ws": ws, "other": other,
               "h": {"Authorization": f"Bearer {key}"}, "other_h": {"Authorization": f"Bearer {other_key}"}}


def feed(w, headers=None):
    r = w["c"].get(f"/api/v1/events?types={TYPES}", headers=headers or w["h"])
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_membership_and_invitation_changes_are_announced_with_ids_only(world):
    w, ident, ws, bob = world, world["ident"], world["ws"], world["bob"]
    # The workspace's owner membership was the first event of this tenant.
    first = feed(w)
    assert [e["type"] for e in first] == ["member.added"] and first[0]["data"]["role"] == "owner"
    invitation, token = w["invites"].create(workspace_id=ws, email="bob@example.test", role="editor", actor_id=w["alice"]["id"])
    accepted = w["invites"].accept(token, password=None, display_name=None, as_user_id=bob["id"])
    assert accepted["workspace_id"] == ws
    ident.change_role(ws, bob["id"], "viewer", can_export=True)
    ident.remove_member(ws, bob["id"])
    # TEST FIXTURE: the inviter's 60 s cooldown is backdated so a second invitation can follow at once.
    w["store"].execute("UPDATE invitations SET created_at=created_at-120 WHERE workspace_id=?", (ws,))
    second, _ = w["invites"].create(workspace_id=ws, email="bob@example.test", role="viewer", actor_id=w["alice"]["id"])
    assert w["invites"].revoke(second["id"], w["alice"]["id"]) is True
    events = feed(w)[1:]
    assert [e["type"] for e in events] == ["invitation.created", "invitation.accepted", "member.added", "member.changed",
                                           "member.removed", "invitation.created", "invitation.revoked"]
    created, accept, added, changed, removed, _, revoked = events
    # An editor's invitation carries the export right by default (data_flow EXPORT_ROLES).
    assert created["data"] == {"workspace_id": ws, "invitation_id": invitation["id"], "role": "editor", "can_export": True,
                               "expires_at": pytest.approx(created["data"]["expires_at"])}
    assert accept["data"] == {"workspace_id": ws, "invitation_id": invitation["id"], "user_id": bob["id"], "role": "editor"}
    assert added["data"] == {"workspace_id": ws, "user_id": bob["id"], "role": "editor", "can_export": True}
    assert changed["data"] == {"workspace_id": ws, "user_id": bob["id"], "role": "viewer", "can_export": True,
                               "previous_role": "editor", "previous_can_export": True}
    assert removed["data"] == {"workspace_id": ws, "user_id": bob["id"], "role": "viewer"}
    assert revoked["data"] == {"workspace_id": ws, "invitation_id": second["id"]}
    assert all(e["job_id"] is None for e in events)
    # Identifiers only: no address, no name, no invitation token, in the feed or in the outbox.
    everything = json.dumps(events) + json.dumps(w["store"].all("SELECT data FROM job_events WHERE tenant=?", (ws,)), default=str)
    for secret in ("bob@example.test", "alice@example.test", token, "Workspace W"):
        assert secret not in everything
    # Another workspace's credential sees none of it; its own owner event only.
    assert [e["data"]["workspace_id"] for e in feed(w, w["other_h"])] == [w["other"]]


def test_a_subscription_may_filter_on_member_and_invitation_events(world):
    w = world
    r = w["c"].post("/api/v1/webhooks/subscriptions", headers=w["h"],
                    json={"url": "http://127.0.0.1:9/hooks/directory", "events": ["member.*", "invitation.accepted"]})
    assert r.status_code == 201, r.text
    assert r.json()["events"] == ["member.*", "invitation.accepted"]
    bad = w["c"].post("/api/v1/webhooks/subscriptions", headers=w["h"], json={"url": "http://127.0.0.1:9/hooks/x", "events": ["member.joined"]})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_events"
    assert "member.*" in bad.json()["error"]["details"]["allowed"] and "invitation.revoked" in bad.json()["error"]["details"]["allowed"]
