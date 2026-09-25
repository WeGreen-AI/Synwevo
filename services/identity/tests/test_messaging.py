"""Organization messaging: explicit membership, history from the moment you join, revocation that
closes every door, attachments through the acceptance chain, and a stream that is a hint."""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member"), ("cid", "Cid Member"), ("outsider", "Oz Outsider")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["owner"]["id"], "Chat Org", "Chat Team", "fixture-idem-chat-org-0001")
    oid = created["organization"]["id"]
    for key in ("ann", "ben", "cid"):
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


M = "/api/auth/messaging"


def test_direct_messages_are_one_conversation_with_idempotent_sends_and_own_read_positions(org):
    c = org
    ha = login(c, "ann")
    # outsiders are not in the directory and cannot be messaged; the answer never says they exist
    assert c.get(f"{M}/directory?organization_id={c.oid}&q=Oz").json()["items"] == []
    assert c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["outsider"]["id"]}).status_code == 404
    people = c.get(f"{M}/directory?organization_id={c.oid}&q=ben").json()["items"]
    assert people == [{"user_id": c.users["ben"]["id"], "name": "Ben Member"}]
    r = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]})
    assert r.status_code == 201, r.text
    dm = r.json()
    assert dm["kind"] == "dm" and dm["name"] == "Ben Member" and dm["member_count"] == 2
    # opening it again (from either side) is the same conversation
    hb = login(c, "ben")
    assert c.post(f"{M}/dm", headers=hb, json={"organization_id": c.oid, "user_id": c.users["ann"]["id"]}).json()["id"] == dm["id"]
    ha = login(c, "ann")
    r = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "hello ben", "client_id": "c1"})
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["message"]["seq"] == 1
    again = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "hello ben", "client_id": "c1"}).json()
    assert again["created"] is False and again["message"]["id"] == r.json()["message"]["id"], "a retry after a lost ack is not a second message"
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "second"})
    assert c.get(f"{M}/conversations/{dm['id']}").json()["last_seq"] == 2
    # ben has two unread; reading up to seq 1 leaves one; ann's own sends are read for her
    hb = login(c, "ben")
    assert c.get(f"{M}/unread-count").json() == {"unread": 2, "conversations": 1}
    assert c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 1}).json()["unread"] == 1
    assert c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99}).json()["last_read_seq"] == 2
    hist = c.get(f"{M}/conversations/{dm['id']}/messages").json()
    assert [m["body"] for m in hist["items"]] == ["hello ben", "second"] and hist["has_more"] is False
    # ben gets exactly one inbox item for the burst; ann (the sender) none
    kinds_b = [x["kind"] for x in c.api.state.notifier.list(c.users["ben"]["id"])["items"]]
    assert kinds_b.count("conversation.dm") == 1
    assert "conversation.dm" not in [x["kind"] for x in c.api.state.notifier.list(c.users["ann"]["id"])["items"]]
    # reactions, edit and delete: only own messages, reactions from a fixed set
    mid = hist["items"][0]["id"]
    r = c.put(f"{M}/messages/{mid}/reactions", headers=hb, json={"emoji": "👍"})
    assert r.status_code == 200 and r.json()["reactions"] == [{"emoji": "👍", "users": [c.users["ben"]["id"]], "count": 1}]
    assert c.put(f"{M}/messages/{mid}/reactions", headers=hb, json={"emoji": "🐍"}).status_code == 400
    assert c.patch(f"{M}/messages/{mid}", headers=hb, json={"body": "not mine"}).status_code == 403
    ha = login(c, "ann")
    assert c.patch(f"{M}/messages/{mid}", headers=ha, json={"body": "hello ben!"}).json()["edited_at"]
    out = c.request("DELETE", f"{M}/messages/{mid}", headers=ha).json()
    assert out["deleted"] is True and out["body"] == "" and out["reactions"] == []
    # a third person is not part of this DM and cannot see it, even by id
    hc = login(c, "cid")
    assert c.get(f"{M}/conversations/{dm['id']}").status_code == 404
    assert c.get(f"{M}/conversations/{dm['id']}/messages").status_code == 404
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=hc, json={"body": "hi"}).status_code == 404


def test_groups_show_history_from_the_join_and_close_on_removal_or_organization_loss(org):
    c = org
    ho = login(c, "owner")
    r = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "Launch", "member_ids": [c.users["ann"]["id"]]})
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["kind"] == "group" and g["me"]["role"] == "owner" and g["member_count"] == 2
    assert [x["kind"] for x in c.api.state.notifier.list(c.users["ann"]["id"])["items"]] == ["conversation.added"]
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "before ben joins"})
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "still before"})
    # ben is added later: his history starts now
    r = c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [c.users["ben"]["id"]]})
    assert r.status_code == 200 and r.json()["member_count"] == 3
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "after ben joined", "mention_ids": [c.users["ben"]["id"]]})
    hb = login(c, "ben")
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()
    assert [m["body"] for m in hist["items"] if m["kind"] == "text"] == ["after ben joined"], hist
    assert hist["floor"] > 0 and hist["has_more"] is False
    assert c.get(f"{M}/search?q=before").json()["items"] == [], "search cannot reach messages from before the join"
    # a mention reaches a group member with the default 'mentions' mode; plain group messages do not
    kinds_b = [x["kind"] for x in c.api.state.notifier.list(c.users["ben"]["id"])["items"]]
    assert kinds_b.count("conversation.mention") == 1 and "conversation.group" not in kinds_b
    # ann (also 'mentions' by default) got no item for the two ordinary messages
    kinds_a = [x["kind"] for x in c.api.state.notifier.list(c.users["ann"]["id"])["items"]]
    assert kinds_a == ["conversation.added"]
    # members cannot manage; the owner can; removal closes every door
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=hb, json={"user_ids": [c.users["cid"]["id"]]}).status_code == 403
    ho = login(c, "owner")
    assert c.request("DELETE", f"{M}/conversations/{g['id']}/members/{c.users['ben']['id']}", headers=ho).status_code == 200
    hb = login(c, "ben")
    assert c.get(f"{M}/conversations/{g['id']}").status_code == 404
    assert [x["id"] for x in c.get(f"{M}/conversations").json()["items"]] == []
    assert c.get(f"{M}/unread-count").json() == {"unread": 0, "conversations": 0}
    # added again: a new join, history from now, nothing restored
    ho = login(c, "owner")
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "while ben was out"})
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [c.users["ben"]["id"]]})
    hb = login(c, "ben")
    texts = [m["body"] for m in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if m["kind"] == "text"]
    assert texts == []
    # owner cannot leave without transferring; after the transfer the new owner can archive
    ho = login(c, "owner")
    assert c.post(f"{M}/conversations/{g['id']}/leave", headers=ho).json()["error"] == "owner_must_transfer"
    assert c.post(f"{M}/conversations/{g['id']}/transfer", headers=ho, json={"user_id": c.users["ann"]["id"]}).json()["me"]["role"] == "admin"
    ha = login(c, "ann")
    assert c.post(f"{M}/conversations/{g['id']}/archive", headers=ha, json={}).json()["archived"] is True
    assert c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "x"}).json()["error"] == "archived"
    # losing organization membership closes every conversation of that organization at once
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["cid"]["id"]}).json()
    assert len(c.get(f"{M}/conversations").json()["items"]) == 2
    from synwevo_core.organizations import Organizations
    Organizations(c.ident.store, c.ident).remove_member(c.oid, c.users["owner"]["id"], c.users["ann"]["id"], reason="fixture removal of ann")
    ha = login(c, "ann")
    assert c.get(f"{M}/conversations").json()["items"] == []
    assert c.get(f"{M}/conversations/{dm['id']}/messages").status_code == 404


def test_attachments_go_through_the_acceptance_chain_and_are_served_only_to_readers(org):
    c = org
    ha = login(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = c.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("shot.png", png, "application/octet-stream")}, headers=ha)
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["content_type"] == "image/png" and att["inline"] is True and att["scan"]["status"] == "not_configured"
    # an unused attachment is only visible to its uploader; sent, it belongs to the message
    hb = login(c, "ben")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    ha = login(c, "ann")
    msg = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]}).json()["message"]
    assert msg["attachment"]["id"] == att["id"] and msg["attachment"]["inline"] is True
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]}).status_code == 404, "an attachment is used once"
    hb = login(c, "ben")
    r = c.get(f"{M}/attachments/{att['id']}?inline=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-content-sha256"] and r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-disposition"].startswith("inline") and "sandbox" in r.headers["content-security-policy"]
    r = c.get(f"{M}/attachments/{att['id']}")
    assert r.headers["content-disposition"].startswith("attachment")
    # not a member: 404, even with the id
    hc = login(c, "cid")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    # deleting the message takes the attachment with it
    ha = login(c, "ann")
    c.request("DELETE", f"{M}/messages/{msg['id']}", headers=ha)
    hb = login(c, "ben")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    # too large is refused before storage
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (20 * 1024 * 1024 + 1)
    ha = login(c, "ann")
    assert c.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("big.png", big, "image/png")}, headers=ha).status_code == 413


def test_the_stream_delivers_events_and_ends_when_the_session_is_revoked(org):
    c = org
    ha = login(c, "ann")
    uid = c.users["ann"]["id"]
    hub = c.api.state.message_hub
    lines = []

    def later():
        for _ in range(50):                     # wait for the subscription, then publish from another thread
            if hub.connections():
                break
            time.sleep(0.05)
        hub.publish([uid], {"type": "message", "conversation_id": "cv_test", "message": {"seq": 1}})
        time.sleep(0.2)
        c.ident.store.execute("UPDATE sessions SET revoked_at=? WHERE user_id=?", (time.time(), uid))
        hub.publish([uid], {"type": "read", "conversation_id": "cv_test", "last_read_seq": 1})
    threading.Thread(target=later, daemon=True).start()
    import synwevo_identity.messaging_routes as mr
    original = mr.HEARTBEAT_S
    mr.HEARTBEAT_S = 1                          # the revocation is noticed at the next heartbeat
    try:
        with c.stream("GET", f"{M}/stream") as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                lines.append(line)
                if line.startswith("event: end") or len(lines) > 40:
                    break
    finally:
        mr.HEARTBEAT_S = original
    text = "\n".join(lines)
    assert "event: hello" in text and '"conversation_id": "cv_test"' in text and "event: read" in text
    assert "event: end" in text, text
    assert hub.connections() == 0


def test_docs_link_cards_follow_the_readers_own_access(org, monkeypatch):
    """A link to a Docs project or job in a message gets a card only for readers who can open
    that project; the same conversation is not access. Cards come from the registered Docs
    address, and a link to anything else is just a link."""
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.store import new_id, now
    st = org.ident.store
    bootstrap_docs_app(st, org.ident, launch_url="http://docs.testserver/")
    team = st.one("SELECT id FROM workspaces WHERE organization_id=? AND kind='org'", (org.oid,))["id"]
    st.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'editor',1,1,1,?)",
               (team, org.users["ann"]["id"], org.users["owner"]["id"]))
    personal = org.ident.ensure_personal_workspace(org.users["owner"]["id"])["id"]
    t = now()
    st.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant,record_rule,output,mapping_rev,rev,step) VALUES (?,?,?,?,?,'{}','{}',0,1,'sources')", ("p_team1", "团队合同", t, t, team))
    st.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant,record_rule,output,mapping_rev,rev,step) VALUES (?,?,?,?,?,'{}','{}',0,1,'sources')", ("p_mine1", "我的草稿", t, t, personal))
    st.execute("INSERT INTO jobs (id,project_id,status,created_at,counts,tenant,run_no) VALUES (?,?,?,?,?,?,1)", ("j_team1", "p_team1", "completed_with_errors", t, '{"success": 8, "failed": 2}', team))
    login(org, "owner")
    C = "/api/auth/messaging/link-card"
    card = org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).json()
    assert card["kind"] == "project" and card["title"] == "团队合同" and card["workspace"] == "Chat Team" and card["path"] == "/p/p_team1"
    job = org.get(C, params={"url": "http://docs.testserver/p/p_team1/jobs/j_team1"}).json()
    assert job["kind"] == "job" and job["status"] == "completed_with_errors" and job["done"] == 8 and job["failed"] == 2
    assert org.get(C, params={"url": "http://docs.testserver/p/p_mine1"}).json()["title"] == "我的草稿"
    # ann is in the team workspace: the team project yes, the owner's personal draft no
    login(org, "ann")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 200
    assert org.get(C, params={"url": "http://docs.testserver/p/p_mine1"}).status_code == 404
    # ben is in the organization but not in the team workspace; an outsider sees nothing at all
    login(org, "ben")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 404
    login(org, "outsider")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 404
    # not a Docs link, a foreign host with the same path, an unknown project, a job of another project
    login(org, "owner")
    for url in ("https://example.com/p/p_team1", "http://docs.testserver/about", "http://docs.testserver/p/p_nope", "http://docs.testserver/p/p_mine1/jobs/j_team1"):
        assert org.get(C, params={"url": url}).status_code == 404, url
