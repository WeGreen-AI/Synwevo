"""Organization messaging on its own origin: explicit membership, history from the moment you join,
revocation that closes every door, attachments through the acceptance chain, a stream that is a
hint — and the role boundary of the application that serves it.

These were the account service's tests until CI-66 moved the API to Synwevo Message; the
ceremonies are unchanged, the prefix is `/api/messaging`, and the caller is signed in the way a
relying party signs people in (a minted session, see conftest). The moderation half (listing and
reviewing reports) stayed on the account service and is tested there."""
from __future__ import annotations

import threading
import time

M = "/api/messaging"
# The fixture's configured origins (conftest.org): this application's own and the account service's.
MESSAGES = "http://message.testserver"
ACCOUNTS = "http://accounts.testserver"


def sign_in(c, key: str) -> dict:
    """Become `key` on this origin (a minted session of this application, see conftest)."""
    return c.sign_in(key)


def test_the_application_describes_itself_and_refuses_what_is_not_its_own(org):
    """A relying party: no password box, its own id, its own address in `messages_url`; the
    account origin's former routes are refused here except the shared badge count."""
    c = org
    st = c.get("/api/auth/state").json()
    assert st["role"] == "messages" and st["password_login"] is False and st["app_id"] == "messages"
    assert st["messages_url"] == MESSAGES and st["account_url"] == ACCOUNTS + "/account"
    assert c.get(f"{M}/capabilities").status_code == 401                      # a session is required
    h = sign_in(c, "ann")
    caps = c.get(f"{M}/capabilities").json()
    assert caps["reactions"] and caps["heartbeat_s"] == 25 and caps["limits"]["messages_per_minute"] == 60
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 0, "conversations": 0}
    # the organizations a conversation can be started in come from this application (the account
    # service's /api/auth/organizations is not served here)
    orgs = c.get(f"{M}/organizations").json()
    assert [o["id"] for o in orgs["items"]] == [c.oid] and orgs["items"][0]["status"] == "active" and orgs["total"] == 1
    assert c.get("/api/auth/organizations", headers=h).status_code == 404
    for path in ("/api/auth/messaging/conversations", "/api/auth/messaging/reports?organization_id=x",
                 "/api/auth/passkeys", "/api/projects", "/api/portal/home"):
        r = c.get(path, headers=h)
        assert r.status_code == 404 and r.json()["error"] == "not_in_this_role", (path, r.text[:120])
    assert c.post("/api/auth/login", json={"email": "ann@example.test", "password": "x"}).status_code == 404


def test_direct_messages_are_one_conversation_with_idempotent_sends_and_own_read_positions(org):
    c = org
    ha = sign_in(c, "ann")
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
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/dm", headers=hb, json={"organization_id": c.oid, "user_id": c.users["ann"]["id"]}).json()["id"] == dm["id"]
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "hello ben", "client_id": "c1"})
    assert r.status_code == 201 and r.json()["created"] is True and r.json()["message"]["seq"] == 1
    again = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "hello ben", "client_id": "c1"}).json()
    assert again["created"] is False and again["message"]["id"] == r.json()["message"]["id"], "a retry after a lost ack is not a second message"
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "second"})
    assert c.get(f"{M}/conversations/{dm['id']}").json()["last_seq"] == 2
    # ben has two unread; reading up to seq 1 leaves one; ann's own sends are read for her
    hb = sign_in(c, "ben")
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 2, "conversations": 1}
    # ben's inbox item for the burst points at this origin's thread (`conversation.*` targets
    # name origin `messages` while the application is registered) and is unread; ann (the
    # sender) has none
    items_b = c.api.state.notifier.list(c.users["ben"]["id"])["items"]
    assert [x["kind"] for x in items_b].count("conversation.dm") == 1
    assert items_b[0]["href"] == f"{MESSAGES}/c/{dm['id']}" and items_b[0]["read_at"] is None, items_b[0]
    assert "conversation.dm" not in [x["kind"] for x in c.api.state.notifier.list(c.users["ann"]["id"])["items"]]
    half = c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 1}).json()
    assert half["unread"] == 1 and "notifications_read" not in half
    assert c.api.state.notifier.list(c.users["ben"]["id"])["items"][0]["read_at"] is None, "a partly read conversation keeps its inbox item unread"
    done = c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99}).json()
    assert done["last_read_seq"] == 2 and done["notifications_read"] == 1
    # reading the conversation to its end read the inbox item about it: the bell stops saying
    # "Ann sent you a message" once the chat has been read (an inbox read, nothing approved)
    assert c.api.state.notifier.list(c.users["ben"]["id"])["items"][0]["read_at"], "the conversation's inbox item is read with the conversation"
    assert c.api.state.notifier.unread_count(c.users["ben"]["id"]) == 0
    hist = c.get(f"{M}/conversations/{dm['id']}/messages").json()
    assert [m["body"] for m in hist["items"]] == ["hello ben", "second"] and hist["has_more"] is False
    # reactions, edit and delete: only own messages, reactions from a fixed set
    mid = hist["items"][0]["id"]
    r = c.put(f"{M}/messages/{mid}/reactions", headers=hb, json={"emoji": "👍"})
    assert r.status_code == 200 and r.json()["reactions"] == [{"emoji": "👍", "users": [c.users["ben"]["id"]], "count": 1}]
    assert c.put(f"{M}/messages/{mid}/reactions", headers=hb, json={"emoji": "🐍"}).status_code == 400
    assert c.patch(f"{M}/messages/{mid}", headers=hb, json={"body": "not mine"}).status_code == 403
    ha = sign_in(c, "ann")
    assert c.patch(f"{M}/messages/{mid}", headers=ha, json={"body": "hello ben!"}).json()["edited_at"]
    # since CI-74 DELETE is "delete for me": ann stops seeing it, ben still does; recall (the
    # sender, within the window) is what takes a message away for everyone
    out = c.request("DELETE", f"{M}/messages/{mid}", headers=ha).json()
    assert out == {"message_id": mid, "conversation_id": dm["id"], "seq": 1, "hidden": True}
    assert [m["seq"] for m in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"]] == [2]
    hb = sign_in(c, "ben")
    assert [m["seq"] for m in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"]] == [1, 2]
    ha = sign_in(c, "ann")
    out = c.post(f"{M}/messages/{mid}/recall", headers=ha).json()
    assert out["deleted"] is True and out["recalled"] is True and out["body"] == "" and out["reactions"] == [] and out["system"] == {"event": "recalled", "actor": c.users["ann"]["id"]}
    # a third person is not part of this DM and cannot see it, even by id
    hc = sign_in(c, "cid")
    assert c.get(f"{M}/conversations/{dm['id']}").status_code == 404
    assert c.get(f"{M}/conversations/{dm['id']}/messages").status_code == 404
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=hc, json={"body": "hi"}).status_code == 404


def test_groups_show_history_from_the_join_and_close_on_removal_or_organization_loss(org):
    c = org
    ho = sign_in(c, "owner")
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
    hb = sign_in(c, "ben")
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
    # members cannot manage; the owner can; removal closes every door and the inbox item points
    # at this origin's list (there is no thread to show a removed member)
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=hb, json={"user_ids": [c.users["cid"]["id"]]}).status_code == 403
    ho = sign_in(c, "owner")
    assert c.request("DELETE", f"{M}/conversations/{g['id']}/members/{c.users['ben']['id']}", headers=ho).status_code == 200
    removed = [x for x in c.api.state.notifier.list(c.users["ben"]["id"])["items"] if x["kind"] == "conversation.removed"]
    assert removed and removed[0]["href"] == MESSAGES + "/", removed
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/conversations/{g['id']}").status_code == 404
    assert [x["id"] for x in c.get(f"{M}/conversations").json()["items"]] == []
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 0, "conversations": 0}
    # added again: a new join, history from now, nothing restored
    ho = sign_in(c, "owner")
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "while ben was out"})
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [c.users["ben"]["id"]]})
    hb = sign_in(c, "ben")
    texts = [m["body"] for m in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if m["kind"] == "text"]
    assert texts == []
    # owner cannot leave without transferring; after the transfer the new owner can archive
    ho = sign_in(c, "owner")
    assert c.post(f"{M}/conversations/{g['id']}/leave", headers=ho).json()["error"] == "owner_must_transfer"
    assert c.post(f"{M}/conversations/{g['id']}/transfer", headers=ho, json={"user_id": c.users["ann"]["id"]}).json()["me"]["role"] == "admin"
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/conversations/{g['id']}/archive", headers=ha, json={}).json()["archived"] is True
    assert c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "x"}).json()["error"] == "archived"
    # losing organization membership closes every conversation of that organization at once
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["cid"]["id"]}).json()
    assert len(c.get(f"{M}/conversations").json()["items"]) == 2
    from synwevo_core.organizations import Organizations
    Organizations(c.ident.store, c.ident).remove_member(c.oid, c.users["owner"]["id"], c.users["ann"]["id"], reason="fixture removal of ann")
    ha = sign_in(c, "ann")
    assert c.get(f"{M}/conversations").json()["items"] == []
    assert c.get(f"{M}/conversations/{dm['id']}/messages").status_code == 404


def test_attachments_go_through_the_acceptance_chain_and_are_served_only_to_readers(org):
    c = org
    ha = sign_in(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = c.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("shot.png", png, "application/octet-stream")}, headers=ha)
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["content_type"] == "image/png" and att["inline"] is True and att["scan"]["status"] == "not_configured"
    # an unused attachment is only visible to its uploader; sent, it belongs to the message
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    ha = sign_in(c, "ann")
    msg = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]}).json()["message"]
    assert msg["attachment"]["id"] == att["id"] and msg["attachment"]["inline"] is True
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]}).status_code == 404, "an attachment is used once"
    hb = sign_in(c, "ben")
    r = c.get(f"{M}/attachments/{att['id']}?inline=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-content-sha256"] and r.headers["cache-control"] == "private, no-store"
    assert r.headers["content-disposition"].startswith("inline") and "sandbox" in r.headers["content-security-policy"]
    r = c.get(f"{M}/attachments/{att['id']}")
    assert r.headers["content-disposition"].startswith("attachment")
    # not a member: 404, even with the id
    hc = sign_in(c, "cid")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    # recalling the message takes the attachment with it (a delete-for-me only hides it from me)
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/messages/{msg['id']}/recall", headers=ha).json()["recalled"] is True
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    # too large is refused before storage
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (20 * 1024 * 1024 + 1)
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/conversations/{dm['id']}/attachments", files={"file": ("big.png", big, "image/png")}, headers=ha).status_code == 413


def test_the_stream_delivers_events_and_ends_when_the_session_is_revoked(org):
    c = org
    sign_in(c, "ann")
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
    import synwevo_messages.routes as mr
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


def test_docs_link_cards_follow_the_readers_own_access(org):
    """A link to a Docs project or job in a message gets a card only for readers who can open
    that project; the same conversation is not access. Cards come from the registered Docs
    address, and a link to anything else is just a link."""
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.store import now
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
    sign_in(org, "owner")
    C = f"{M}/link-card"
    card = org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).json()
    assert card["kind"] == "project" and card["title"] == "团队合同" and card["workspace"] == "Chat Team" and card["path"] == "/p/p_team1"
    job = org.get(C, params={"url": "http://docs.testserver/p/p_team1/jobs/j_team1"}).json()
    assert job["kind"] == "job" and job["status"] == "completed_with_errors" and job["done"] == 8 and job["failed"] == 2
    assert org.get(C, params={"url": "http://docs.testserver/p/p_mine1"}).json()["title"] == "我的草稿"
    # ann is in the team workspace: the team project yes, the owner's personal draft no
    sign_in(org, "ann")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 200
    assert org.get(C, params={"url": "http://docs.testserver/p/p_mine1"}).status_code == 404
    # ben is in the organization but not in the team workspace; an outsider sees nothing at all
    sign_in(org, "ben")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 404
    sign_in(org, "outsider")
    assert org.get(C, params={"url": "http://docs.testserver/p/p_team1"}).status_code == 404
    # not a Docs link, a foreign host with the same path, an unknown project, a job of another project
    sign_in(org, "owner")
    for url in ("https://example.com/p/p_team1", "http://docs.testserver/about", "http://docs.testserver/p/p_nope", "http://docs.testserver/p/p_mine1/jobs/j_team1"):
        assert org.get(C, params={"url": url}).status_code == 404, url


def test_a_docs_link_card_closes_while_the_projects_organization_is_frozen_or_deleted(org):
    """A44-44c-3: the card asked only the workspace row, so while the *organization* was frozen, or
    deleted inside its recovery window, a member still got the project's title, its workspace's
    name and a job's status and counts — while the project's discussion answered 403
    `workspace_unavailable`. The card now asks `workspace_availability` (the workspace and its
    organization) and answers the same 404 `no_card` it gives a frozen workspace; once the
    organization is back, the card is back."""
    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.organizations import Organizations
    from synwevo_core.store import now
    st = org.ident.store
    bootstrap_docs_app(st, org.ident, launch_url="http://docs.testserver/")
    team = st.one("SELECT id FROM workspaces WHERE organization_id=? AND kind='org'", (org.oid,))["id"]
    t = now()
    st.execute("INSERT INTO projects (id,name,created_at,updated_at,tenant,record_rule,output,mapping_rev,rev,step) VALUES (?,?,?,?,?,'{}','{}',0,1,'sources')", ("p_team2", "团队合同", t, t, team))
    st.execute("INSERT INTO jobs (id,project_id,status,created_at,counts,tenant,run_no) VALUES (?,?,?,?,?,?,1)", ("j_team2", "p_team2", "completed", t, '{"success": 3}', team))
    orgs, owner = Organizations(st, org.ident), org.users["owner"]["id"]
    C, links = f"{M}/link-card", ("http://docs.testserver/p/p_team2", "http://docs.testserver/p/p_team2/jobs/j_team2")

    def answers() -> list[tuple]:
        sign_in(org, "owner")               # a fresh session each time: a lifecycle act revokes the old ones
        out = []
        for url in links:
            r = org.get(C, params={"url": url})
            out.append((r.status_code, r.json().get("error") or r.json().get("title")))
        return out

    open_card, closed = [(200, "团队合同")] * 2, [(404, "no_card")] * 2
    assert answers() == open_card
    for close, reopen in (("freeze", "unfreeze"), ("delete", "recover")):
        orgs.platform_transition(org.oid, owner, close, reason="Fixture: organization lifecycle for link cards")
        assert st.one("SELECT status FROM workspaces WHERE id=?", (team,))["status"] == "active", "only the organization changed"
        assert answers() == closed, close
        orgs.platform_transition(org.oid, owner, reopen, reason="Fixture: organization lifecycle for link cards")
        assert answers() == open_card, reopen
    # the workspace itself frozen: the same answer as before
    org.ident.set_workspace_frozen(team, True)
    assert answers() == closed


def test_announcements_and_reports_are_accepted_here_and_reviewed_on_the_account_service(org):
    """A group's announcement is one pinned text set by its managers and told once as a digest;
    a report of a message reaches the organization's messaging managers with an actionable item
    pointing at the organization page on the account origin. Reviewing it is the account
    service's (services/identity/tests/test_messaging_reports.py); asked here it is refused by role."""
    c = org
    U, n = c.users, c.api.state.notifier
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "公告群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]}).json()
    # ---- announcement: managers only; members get a digest item once; clearing tells nobody
    ha = sign_in(c, "ann")
    assert c.put(f"{M}/conversations/{g['id']}/announcement", headers=ha, json={"text": "我来定"}).status_code == 403
    ho = sign_in(c, "owner")
    assert c.put(f"{M}/conversations/{g['id']}/announcement", headers=ho, json={"text": "  下周一交周报 ", "extra": 1}).status_code == 400
    out = c.put(f"{M}/conversations/{g['id']}/announcement", headers=ho, json={"text": "  下周一交周报 "}).json()
    assert out["announcement"] == "下周一交周报" and out["announcement_at"]
    assert [x["kind"] for x in n.list(U["ann"]["id"], locale="en")["items"]] == ["conversation.announcement", "conversation.added"]
    assert n.list(U["ann"]["id"], locale="en")["items"][0]["title"] == "Ola Owner updated the announcement in “公告群”"
    assert n.list(U["ann"]["id"], locale="en")["items"][0]["href"] == f"{MESSAGES}/c/{g['id']}"
    assert n.list(U["owner"]["id"])["items"] == []
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()["items"]
    assert hist[-1]["kind"] == "system" and hist[-1]["system"]["event"] == "announcement" and hist[-1]["system"]["cleared"] is False
    assert c.put(f"{M}/conversations/{g['id']}/announcement", headers=ho, json={"text": None}).json()["announcement"] is None
    assert len(n.list(U["ann"]["id"])["items"]) == 2, "clearing an announcement tells nobody"
    # ---- a report: not your own message, one open report per message and reporter, unknown reason refused
    ha = sign_in(c, "ann")
    mid = c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "把你的账号密码发我"}).json()["message"]["id"]
    assert c.post(f"{M}/messages/{mid}/report", headers=ha, json={"reason": "spam"}).status_code == 400
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/messages/{mid}/report", headers=hb, json={"reason": "rude"}).status_code == 400
    r = c.post(f"{M}/messages/{mid}/report", headers=hb, json={"reason": "privacy", "note": "索要密码"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["status"] == "open" and rep["body_snapshot"] == "把你的账号密码发我" and rep["sender"]["name"] == "Ann Member" and rep["reporter"]["name"] == "Ben Member"
    assert rep["conversation"] == {"kind": "group", "name": "公告群"}
    assert c.post(f"{M}/messages/{mid}/report", headers=hb, json={"reason": "spam"}).status_code == 409
    # the owner (holds org.messaging.manage) is told with an actionable item pointing at the
    # organization page — on the account origin, where the review happens; the sender is not
    items = n.list(U["owner"]["id"], locale="en")["items"]
    assert items[0]["kind"] == "conversation.reported" and items[0]["title"] == "Ben Member reported a message in “公告群”"
    assert items[0]["href"] == f"{ACCOUNTS}/organizations/{c.oid}?report={rep['id']}", items[0]["href"]
    assert all(x["kind"] != "conversation.reported" for x in n.list(U["ann"]["id"])["items"])
    # someone who joined after the message cannot see it, so cannot report it
    ho = sign_in(c, "owner")
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["cid"]["id"]]}).status_code == 200
    hc = sign_in(c, "cid")
    assert c.post(f"{M}/messages/{mid}/report", headers=hc, json={"reason": "spam"}).status_code == 404
    # reviewing is not this application's: refused by role, whoever asks
    ho = sign_in(c, "owner")
    for method, path in (("GET", f"/api/auth/messaging/reports?organization_id={c.oid}"), ("POST", f"/api/auth/messaging/reports/{rep['id']}/review")):
        r = c.request(method, path, headers=ho, json={"decision": "removed"} if method == "POST" else None)
        assert r.status_code == 404 and r.json()["error"] == "not_in_this_role", (path, r.text[:120])
    assert c.ident.store.one("SELECT status FROM message_reports WHERE id=?", (rep["id"],))["status"] == "open"
