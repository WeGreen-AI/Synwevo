"""CI-81: a person's messaging data in their account export, and conversations after they go.

Real store, real identity app and the real core messaging service; nothing mocked. Deletion is
driven by the identity sweeper's `finalize_deletions`, organization departure by
`Organizations.remove_member` — the same paths production runs.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.messaging import Messaging
from synwevo_core.organizations import Organizations
from synwevo_core.store import now

PW = "harbor lights guide the evening ferry home"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://accounts.example.test")
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    store = ident.store
    users = {}
    for key in ("owner", "admin", "member", "solo"):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=key.title())
        store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u["id"]
    oid = Organizations(store, ident).create(users["owner"], "Depart Org", "Depart Team", "fixture-idem-depart-0001")["organization"]["id"]
    for key in ("admin", "member", "solo"):
        store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                      (oid, users[key], users["owner"]))
    m = Messaging(store, ident)
    group = m.create_group(oid, users["owner"], name="Launch", member_ids=[users["member"], users["admin"]])
    m.set_role(group["id"], users["owner"], users["admin"], "admin")
    alone = m.create_group(oid, users["owner"], name="Just me", member_ids=[])
    dm = m.open_dm(oid, users["owner"], users["member"])
    m.send(group["id"], users["owner"], body="owner in the group", client_id="g1")
    m.send(group["id"], users["member"], body="member in the group", client_id="g2")
    m.send(dm["id"], users["owner"], body="owner to member", client_id="d1")
    with TestClient(api) as c:
        c.api, c.store, c.ident, c.users, c.oid, c.m = api, store, ident, users, oid, m
        c.group, c.alone, c.dm = group["id"], alone["id"], dm["id"]
        yield c


def members(c, cid):
    return {r["user_id"]: r["role"] for r in c.store.all("SELECT user_id, role FROM conversation_members WHERE conversation_id=? AND left_at IS NULL", (cid,))}


def system_events(c, cid):
    import json
    return [json.loads(r["body"]) for r in c.store.all("SELECT body FROM messages WHERE conversation_id=? AND kind='system' ORDER BY seq", (cid,))]


def test_the_account_export_carries_the_persons_own_messaging_data_only(world):
    c = world
    r = c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW})
    assert r.status_code == 200
    r = c.post("/api/auth/export", json={"password": PW}, headers={"X-Synwevo-CSRF": c.cookies.get("dw_csrf")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["format"] == "synwevo-account-2"
    sent = [(m["conversation_id"], m["body"]) for m in data["messaging"]["messages_sent"]]
    assert (c.group, "owner in the group") in sent and (c.dm, "owner to member") in sent
    assert all(body != "member in the group" for _, body in sent), "another person's words are never exported"
    convs = {x["conversation_id"]: x for x in data["messaging"]["conversations"]}
    assert convs[c.group]["name"] == "Launch" and convs[c.group]["role"] == "owner"
    assert convs[c.dm]["kind"] == "dm" and convs[c.dm]["name"] is None, "the other side's name stays theirs"
    assert data["messaging"]["settings"]["read_receipts"] is False
    assert data["notification_preferences"]["categories"]["conversation"]["email"] == "off"
    assert "message_blocks" in data


def test_a_deleted_owner_hands_the_group_on_and_leaves_every_conversation(world):
    c = world
    from synwevo_identity.profile import finalize_deletions
    c.store.execute("UPDATE users SET status='pending_delete', deletion_requested_at=?, deletion_due_at=? WHERE id=?",
                    (now() - 10, now() - 1, c.users["owner"]))
    assert finalize_deletions(c.store) == 1
    owner = c.users["owner"]
    # the group passes to the earliest admin, and says so
    assert members(c, c.group) == {c.users["admin"]: "owner", c.users["member"]: "member"}
    events = system_events(c, c.group)
    assert {"event": "transferred", "to": c.users["admin"], "reason": "account_deleted", "actor": owner} in events
    assert {"event": "left", "reason": "account_deleted", "actor": owner} in events
    # a group with nobody else is archived; the direct message closes quietly for the other side to keep
    assert members(c, c.alone) == {} and c.store.one("SELECT archived_at FROM conversations WHERE id=?", (c.alone,))["archived_at"]
    assert members(c, c.dm) == {c.users["member"]: "member"}
    assert not any(e.get("reason") for e in system_events(c, c.dm)), "no system line in a direct message"
    history = c.m.history(c.dm, c.users["member"])["items"]
    assert any(x.get("body") == "owner to member" for x in history), "the other side keeps the history"
    # idempotent: a second sweep changes nothing
    before = len(system_events(c, c.group))
    finalize_deletions(c.store)
    assert len(system_events(c, c.group)) == before


def test_accounts_deleted_before_the_rule_are_swept_too(world):
    c = world
    c.store.execute("UPDATE users SET status='deleted', deleted_at=? WHERE id=?", (now(), c.users["owner"]))   # an older deletion
    from synwevo_identity.profile import depart_deleted_accounts
    assert depart_deleted_accounts(c.store) == 3
    assert members(c, c.group)[c.users["admin"]] == "owner"
    assert depart_deleted_accounts(c.store) == 0


def test_an_owner_leaving_the_organization_hands_the_group_on(world):
    """`Organizations.remove_member` calls `withdraw_from_organization` for the person leaving (the
    organization's own preconditions — workspace ownership and so on — are tested elsewhere)."""
    c = world
    # no admin this time: the earliest-joined remaining member inherits
    c.m.set_role(c.group, c.users["owner"], c.users["admin"], "member")
    assert c.m.withdraw_from_organization(c.oid, c.users["owner"]) == 3
    left = members(c, c.group)
    assert c.users["owner"] not in left and sorted(left.values()) == ["member", "owner"]
    heir = next(u for u, role in left.items() if role == "owner")
    first = c.store.one("SELECT user_id FROM conversation_members WHERE conversation_id=? AND user_id<>? ORDER BY joined_at, user_id LIMIT 1",
                        (c.group, c.users["owner"]))["user_id"]
    assert heir == first
    assert any(e.get("event") == "transferred" and e.get("reason") == "left_organization" for e in system_events(c, c.group))
