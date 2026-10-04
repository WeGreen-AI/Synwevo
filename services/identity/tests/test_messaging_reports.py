"""Moderation of reported messages stays on the account service after the conversations moved to
Synwevo Message (CI-66): listing needs `org.messaging.manage`, reviewing needs a strict fresh
re-authentication on the account session, one decision settles every open report of the message,
and the reporter is told. The conversation, the message and the report are made through the shared
service layer, the way the messages application makes them; the HTTP under test is the account
service's own."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
R = "/api/auth/messaging/reports"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://testserver")          # inbox links to the account origin resolve against it
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member"), ("cid", "Cid Member")):
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


def test_a_review_needs_the_permission_and_a_fresh_reauth_settles_the_message_and_tells_the_reporter(org):
    c = org
    U, n = c.users, c.api.state.notifier
    from synwevo_core.messaging import Messaging
    svc = Messaging(c.ident.store, c.ident)
    g = svc.create_group(c.oid, U["owner"]["id"], name="公告群", member_ids=[U["ann"]["id"], U["ben"]["id"]])
    msg, _ = svc.send(g["id"], U["ann"]["id"], body="把你的账号密码发我")
    rep = svc.report(msg["id"], U["ben"]["id"], "privacy", "索要密码")
    # the sender edits the message afterwards: the snapshot still shows what was reported
    svc.edit(msg["id"], U["ann"]["id"], "开玩笑的")
    # the conversations themselves are not served here: refused by role, not by a 404 page
    ho = login(c, "owner")
    assert c.get("/api/auth/messaging/conversations").status_code == 404
    # ---- who may read and review: a member cannot; the owner can, but only after a fresh reauth
    ha = login(c, "ann")
    assert c.get(f"{R}?organization_id={c.oid}").status_code == 403
    ho = login(c, "owner")
    listed = c.get(f"{R}?organization_id={c.oid}").json()
    assert [x["id"] for x in listed["items"]] == [rep["id"]] and listed["items"][0]["body_snapshot"] == "把你的账号密码发我"
    assert listed["reasons"] == ["harassment", "spam", "privacy", "other"]
    assert c.post(f"{R}/{rep['id']}/review", headers=ho, json={"decision": "removed"}).json()["error"] == "reauth_required"
    assert c.post("/api/auth/reauth", headers=ho, json={"password": PW, "scope": "admin"}).status_code == 200
    assert c.post(f"{R}/{rep['id']}/review", headers=ho, json={"decision": "maybe"}).status_code == 400
    # a second reporter of the same message, settled by the same decision
    svc.add_members(g["id"], U["owner"]["id"], [U["cid"]["id"]])
    with pytest.raises(Exception):                       # cid joined after the message: he cannot see it, so he cannot report it
        svc.report(msg["id"], U["cid"]["id"], "spam")
    done = c.post(f"{R}/{rep['id']}/review", headers=ho, json={"decision": "removed", "note": "已删除，并提醒发送者"}).json()
    assert done["status"] == "removed" and done["reviewed_by"]["name"] == "Ola Owner" and done["decision_note"] == "已删除，并提醒发送者"
    assert c.post(f"{R}/{rep['id']}/review", headers=ho, json={"decision": "dismissed"}).status_code == 409
    assert c.get(f"{R}?organization_id={c.oid}").json()["items"] == []
    assert c.get(f"{R}?organization_id={c.oid}&status=all").json()["items"][0]["status"] == "removed"
    # the message is gone for everyone; the reporter was told the outcome, with a link to the
    # conversation — on the account origin's old path while no messages application is
    # registered (this database has none), so an existing deployment keeps working links
    row = c.ident.store.one("SELECT deleted_at, body FROM messages WHERE id=?", (msg["id"],))
    assert row["deleted_at"] and row["body"] == ""
    mine = n.list(U["ben"]["id"], locale="en")["items"]
    assert mine[0]["kind"] == "conversation.report_removed" and mine[0]["title"] == "Your report was reviewed: the message was removed" and mine[0]["body"] == "已删除，并提醒发送者"
    assert mine[0]["href"] == f"http://testserver/messages/{g['id']}", mine[0]["href"]


def test_two_moderators_deciding_at_once_settle_the_report_exactly_once(org):
    """Admin defect D13: the review read the report, checked it was open, removed the message and
    only then wrote the decision — so a removal that had read the report before a concurrent
    dismissal still deleted the message and overwrote the dismissal. The losing decision must now
    be refused with nothing changed."""
    c = org
    U = c.users
    from synwevo_core.errors import ServiceError
    from synwevo_core.messaging import Messaging
    svc = Messaging(c.ident.store, c.ident)
    g = svc.create_group(c.oid, U["owner"]["id"], name="审核竞争", member_ids=[U["ann"]["id"], U["ben"]["id"]])
    msg, _ = svc.send(g["id"], U["ann"]["id"], body="有争议但合规的消息")
    rep = svc.report(msg["id"], U["ben"]["id"], "other")
    # The race, made deterministic: the second moderator's review has already looked at the report
    # (still open) when the first one's dismissal commits.
    stale = dict(c.ident.store.one("SELECT * FROM message_reports WHERE id=?", (rep["id"],)))
    assert stale["status"] == "open"
    assert svc.review_report(rep["id"], U["owner"]["id"], "dismissed", "保留")["status"] == "dismissed"
    real_one = svc.store.one
    svc.store.one = lambda sql, params=(): stale if "FROM message_reports WHERE id=?" in sql else real_one(sql, params)
    try:
        with pytest.raises(ServiceError) as lost:
            svc.review_report(rep["id"], U["owner"]["id"], "removed", "删除")
    finally:
        svc.store.one = real_one
    assert (lost.value.status, lost.value.code) == (409, "already_reviewed")
    row = c.ident.store.one("SELECT deleted_at, body FROM messages WHERE id=?", (msg["id"],))
    assert row["deleted_at"] is None and row["body"] == "有争议但合规的消息"
    settled = c.ident.store.one("SELECT status, reviewed_by, decision_note FROM message_reports WHERE id=?", (rep["id"],))
    assert (settled["status"], settled["reviewed_by"], settled["decision_note"]) == ("dismissed", U["owner"]["id"], "保留")


def test_the_outcome_points_at_the_messages_origin_once_the_application_is_registered(org):
    """The same review, on a deployment where Synwevo Message has registered itself: the
    reporter's item links to that origin's thread, resolved through the registry at read time."""
    c = org
    U, n = c.users, c.api.state.notifier
    from synwevo_core.apps import bootstrap_messages_app
    from synwevo_core.messaging import Messaging
    bootstrap_messages_app(c.ident.store, c.ident, launch_url="http://message.testserver/")
    svc = Messaging(c.ident.store, c.ident)
    dm = svc.open_dm(c.oid, U["ann"]["id"], U["ben"]["id"])
    msg, _ = svc.send(dm["id"], U["ann"]["id"], body="垃圾信息")
    rep = svc.report(msg["id"], U["ben"]["id"], "spam")
    ho = login(c, "owner")
    assert c.post("/api/auth/reauth", headers=ho, json={"password": PW, "scope": "admin"}).status_code == 200
    assert c.post(f"{R}/{rep['id']}/review", headers=ho, json={"decision": "dismissed"}).json()["status"] == "dismissed"
    mine = n.list(U["ben"]["id"], locale="en")["items"]
    assert mine[0]["kind"] == "conversation.report_dismissed" and mine[0]["href"] == f"http://message.testserver/c/{dm['id']}"
