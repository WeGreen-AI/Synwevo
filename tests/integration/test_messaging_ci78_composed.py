"""CI-78 across modules: blocking reaches into the account service's export, account deletion and
mail dispatcher, and the composed `all` role serves the new routes (blocks, group avatar, unread
mentions, the organization filter) on the same origin as the account routes."""
from __future__ import annotations

import base64
import binascii
import struct
import time
import zlib

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
M = "/api/messaging"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://testserver")
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://testserver")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["owner"]["id"], "Chat Org", "Chat Team", "fixture-idem-chat-org-0001")
    oid = created["organization"]["id"]
    for key in ("ann", "ben"):
        ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (oid, users[key]["id"], users["owner"]["id"]))
    with TestClient(api) as c:
        c.api, c.ident, c.users, c.oid = api, ident, users, oid
        yield c


def login(c: TestClient, key: str) -> dict:
    c.cookies.clear()
    r = c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW})
    assert r.status_code == 200, r.text
    return {CSRF: c.cookies.get("dw_csrf")}


def _png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00" + b"\x10\x60\xd0" * 2 + b"\x00" + b"\x10\x60\xd0" * 2)) + chunk(b"IEND", b""))


def test_a_block_skips_the_due_digest_and_a_blocked_persons_group_mention_never_becomes_mail(org):
    c = org
    n, disp = c.api.state.notifier, c.api.state.notify_dispatcher
    ann, ben = c.users["ann"]["id"], c.users["ben"]["id"]
    n.set_preferences(ann, {"categories": {"conversation": {"email": "digest"}}})
    hb = login(c, "ben")
    dm = c.post(f"{M}/dm", headers=hb, json={"organization_id": c.oid, "user_id": ann}).json()
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=hb, json={"body": "屏蔽之前发的", "client_id": "b1"})
    pending = lambda: c.ident.store.all("SELECT d.notification_id, d.status, d.last_error FROM notification_deliveries d JOIN notifications x ON x.id=d.notification_id "  # noqa: E731
                                        "WHERE x.user_id=? AND x.kind IN ('conversation.dm','conversation.mention','conversation.group') ORDER BY x.created_at", (ann,))
    assert len(pending()) == 1 and pending()[0]["status"] == "pending"
    # ann blocks ben before the digest is due: the send-time re-check finds nothing unread from anyone else
    ha = login(c, "ann")
    assert c.put(f"{M}/blocks/{ben}", headers=ha).status_code == 200
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (pending()[0]["notification_id"],))
    disp.run_once()
    assert pending()[0]["status"] == "skipped" and pending()[0]["last_error"] == "nothing unread"
    assert c.ident.store.all("SELECT 1 FROM mail_outbox WHERE kind='notification'") == []
    # in a shared group ben's mention of ann creates no inbox item, so there is nothing to mail
    ho = login(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "共享群", "member_ids": [ann, ben]}).json()
    hb = login(c, "ben")
    r = c.post(f"{M}/conversations/{g['id']}/messages", headers=hb, json={"body": "@Ann 在吗", "mention_ids": [ann]})
    assert r.status_code == 201
    assert len(pending()) == 1, "no new conversation item for the blocker"
    login(c, "ann")
    one = c.get(f"{M}/conversations/{g['id']}").json()
    assert one["unread_mentions"] == 0 and one["last_message"]["body"] == "@Ann 在吗"


def test_the_export_carries_the_own_block_list_only_and_deletion_forgets_both_directions(org):
    c = org
    ann, ben, owner = c.users["ann"]["id"], c.users["ben"]["id"], c.users["owner"]["id"]
    ha = login(c, "ann")
    assert c.put(f"{M}/blocks/{ben}", headers=ha).status_code == 200
    hb = login(c, "ben")
    assert c.put(f"{M}/blocks/{ann}", headers=hb).status_code == 200
    ho = login(c, "owner")
    assert c.put(f"{M}/blocks/{ann}", headers=ho).status_code == 200
    ha = login(c, "ann")
    r = c.post("/api/auth/export", headers=ha, json={"password": PW})
    assert r.status_code == 200, r.text
    exported = r.json()["message_blocks"]
    assert [x["user_id"] for x in exported] == [ben] and set(exported[0]) == {"user_id", "created_at"}
    assert owner not in r.text, "who blocked the person is not theirs to export"
    # deletion: request, let the grace period run out, finalize — every row naming ann goes
    assert c.post("/api/auth/deletion", headers=ha, json={"password": PW}).status_code == 200
    c.ident.store.execute("UPDATE users SET deletion_due_at=? WHERE id=?", (time.time() - 1, ann))
    from synwevo_identity.profile import finalize_deletions
    assert finalize_deletions(c.ident.store) == 1
    rows = c.ident.store.all("SELECT user_id, blocked_id FROM message_blocks")
    assert rows == [], rows


def test_the_composed_origin_serves_the_new_routes(org):
    c = org
    ann, ben = c.users["ann"]["id"], c.users["ben"]["id"]
    ho = login(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "组合源", "member_ids": [ann, ben]}).json()
    r = c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": base64.b64encode(_png()).decode()})
    assert r.status_code == 200 and r.json()["avatar"]
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "@Ann", "mention_ids": [ann]})
    login(c, "ann")
    got = c.get(f"{M}/conversations/{g['id']}/avatar")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png" and got.headers["cache-control"] == "private, no-cache"
    items = c.get(f"{M}/conversations", params={"organization_id": c.oid}).json()["items"]
    assert [x["id"] for x in items] == [g["id"]] and items[0]["unread_mentions"] == 1 and items[0]["avatar"]
    assert c.get(f"{M}/conversations", params={"organization_id": "org_elsewhere"}).json()["items"] == []
    assert c.get(f"{M}/blocks").json() == {"items": []}
    # the badge keeps its pinned shape: mentions travel per conversation, not in the shared badge
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 3, "conversations": 1}, "creation, avatar change, message"


def test_the_avatar_rules_are_the_platforms_one_set():
    """A person's avatar, an organization's (account service) and a group's (Synwevo Message) are
    held to one function in the shared layer — no application imports the account service for it."""
    from synwevo_core.avatar_image import canonical_avatar
    from synwevo_identity.profile import canonical_avatar as account_rules
    assert account_rules is canonical_avatar
    assert canonical_avatar(base64.b64encode(_png()).decode()).startswith(b"\x89PNG")
