"""B3: the platform turns Synwevo Message off — and back on — for one organization.

While it is off for organization A, every conversation of A (groups, direct messages, A's project
discussions alike) is refused to its members with `403 messages_disabled` (detail: the
organization, the application, the scope); a non-member still gets the 404 they always got.
Listings, search, sync and the badge leave A out before their limit; narrowed to A they say why.
Nothing is deleted or rewritten, and turning it back on gives everything back as it was. Someone in
A and B keeps B. Cards for A are skipped (never held, so B's cards are not starved), a queued Suite
send fails with the code, open streams learn of the change once at a heartbeat and pass nothing
about A, mail and push about A are skipped while the inbox item stays, moderation goes on, the
account export is unaffected.

Folded in: the platform-wide switch now holds for sessions already signed in (G1, scope
`platform`); a frozen organization, or one deleted and still recoverable, closes all of its
conversations (G2, `409 organization_unavailable`; past its window: 404) — precedence
LOST > DISABLED (platform) > DISABLED (organization) > PAUSED; cards into a paused project
discussion are skipped and the live stream drops events of closed conversations (G3).

The messages application alone (conftest). FIXTURE rows: a second organization made through the
core service, Docs projects, comments and jobs seeded into the shared tables (as in
test_bridge.py), queue rows written the way Suite writes them, and a lifecycle clock moved by
writing `recover_until`. The switch goes through the core service the console calls
(`Apps.disable_for_organization` / `enable_for_organization`, `Apps.set_status`)."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from synwevo_core.apps import DOCS_APP_ID, MESSAGES_APP_ID, Apps, disabled_organizations, organization_app_disabled
from synwevo_core.errors import ServiceError
from synwevo_core.messaging import Messaging, messaging_export
from synwevo_core.messaging_bridge import (SKIPPED_MESSAGES_DISABLED, SKIPPED_PAUSED, ObjectConversations, audience_fingerprint,
                                           emit_comment, emit_user_message, user_message_state)
from synwevo_core.notifications import Notifier
from synwevo_core.organizations import Organizations
from synwevo_core.store import now

M = "/api/messaging"
REASON = "Fixture: organization switch regression"
PA, PB = "p_b3fixa001", "p_b3fixb001"


def _off(c, oid=None) -> bool:
    return Apps(c.ident.store, c.ident).disable_for_organization(oid or c.oid, MESSAGES_APP_ID, c.users["owner"]["id"])


def _on(c, oid=None) -> bool:
    return Apps(c.ident.store, c.ident).enable_for_organization(oid or c.oid, MESSAGES_APP_ID, c.users["owner"]["id"])


def _refused(r, oid, scope="organization"):
    assert r.status_code == 403, (r.request.method, str(r.request.url), r.status_code, r.text)
    body = r.json()
    assert body["error"] == "messages_disabled", body
    assert body["detail"] == {"organization_id": oid, "app_id": MESSAGES_APP_ID, "scope": scope}, body


def _world(c):
    """Organization A (the fixture's): group G (ann owner, ben), a direct message ann–ben, ann's
    message mentioning ben, ben's reply, an attachment, ben's report of ann's message. Organization
    B (FIXTURE, created by ann; ben a member): group GB (ann, ben) with one message."""
    store, U = c.ident.store, c.users
    ann, ben = U["ann"]["id"], U["ben"]["id"]
    b = Organizations(store, c.ident).create(ann, "Second Org", "Second Team", "fixture-idem-b3-second-0001")
    bid, bws = b["organization"]["id"], b["workspace"]["id"]
    store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                  (bid, ben, ann))
    ha = c.sign_in("ann")
    g = c.post(f"{M}/groups", headers=ha, json={"organization_id": c.oid, "name": "A 组", "member_ids": [ben]}).json()["id"]
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": ben}).json()["id"]
    gb = c.post(f"{M}/groups", headers=ha, json={"organization_id": bid, "name": "B 组", "member_ids": [ben]}).json()["id"]
    m1 = c.post(f"{M}/conversations/{g}/messages", headers=ha,
                json={"body": "alpha invoice in A", "client_id": "b3-m1", "mention_ids": [ben]}).json()["message"]["id"]
    up = c.post(f"{M}/conversations/{g}/attachments", headers=ha, files={"file": ("note.txt", b"fixture attachment text", "text/plain")})
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    assert c.post(f"{M}/conversations/{g}/messages", headers=ha, json={"body": "", "attachment_id": aid, "client_id": "b3-att"}).status_code == 201
    assert c.post(f"{M}/conversations/{dm}/messages", headers=ha, json={"body": "dm hello in A", "client_id": "b3-dm"}).status_code == 201
    mb = c.post(f"{M}/conversations/{gb}/messages", headers=ha, json={"body": "alpha invoice in B", "client_id": "b3-gb"}).json()["message"]["id"]
    hb = c.sign_in("ben")
    m2 = c.post(f"{M}/conversations/{g}/messages", headers=hb, json={"body": "beta reply in A", "client_id": "b3-m2"}).json()["message"]["id"]
    rep = c.post(f"{M}/messages/{m1}/report", headers=hb, json={"reason": "spam"})
    assert rep.status_code == 201, rep.text
    return SimpleNamespace(a=c.oid, b=bid, bws=bws, g=g, dm=dm, gb=gb, m1=m1, m2=m2, mb=mb, aid=aid, rid=rep.json()["id"], ann=ann, ben=ben)


def _rows(store, cids) -> dict:
    marks = ",".join("?" for _ in cids)
    return {t: store.all(f"SELECT * FROM {t} WHERE conversation_id IN ({marks}) ORDER BY {order}", tuple(cids))
            for t, order in (("conversation_members", "conversation_id, user_id"), ("messages", "conversation_id, seq"),
                             ("message_attachments", "id"), ("message_reports", "id"))} | {
        "conversations": store.all(f"SELECT * FROM conversations WHERE id IN ({marks}) ORDER BY id", tuple(cids))}


def _ids(c, path=f"{M}/conversations"):
    return [x["id"] for x in c.get(path).json()["items"]]


# ---------------------------------------------------------------- every way in, and back
def test_every_way_into_a_disabled_organizations_conversations_is_refused_and_everything_comes_back(org):
    c = org
    w = _world(c)
    store, U = c.ident.store, c.users
    svc = Messaging(store, c.ident)
    before = _rows(store, [w.g, w.dm])
    unread = svc.unread_total(w.ben)
    assert unread["conversations"] == 2                                # the direct message and B's group
    assert _off(c) is True and _off(c) is False                       # idempotent: the second changes nothing
    assert organization_app_disabled(store, w.a, MESSAGES_APP_ID) and disabled_organizations(store, MESSAGES_APP_ID) == {w.a}
    h = c.sign_in("ann")
    owner = U["owner"]["id"]
    calls = [
        ("GET", f"/conversations/{w.g}", None), ("GET", f"/conversations/{w.g}/messages", None),
        ("GET", f"/conversations/{w.g}/avatar", None), ("GET", f"/conversations/{w.dm}", None),
        ("GET", f"/conversations/{w.dm}/messages?after=0", None), ("GET", f"/conversations/{w.g}/object", None),
        ("POST", f"/conversations/{w.g}/messages", {"body": "after the switch-off", "client_id": "b3-x1"}),
        ("POST", f"/conversations/{w.dm}/messages", {"body": "after the switch-off", "client_id": "b3-x2"}),
        ("PATCH", f"/messages/{w.m1}", {"body": "edited after"}), ("POST", f"/messages/{w.m1}/recall", None),
        ("POST", f"/messages/{w.m2}/remove", None), ("DELETE", f"/messages/{w.m2}", None),
        ("POST", f"/messages/{w.m2}/unhide", None), ("PUT", f"/messages/{w.m2}/reactions", {"emoji": "👍"}),
        ("POST", f"/messages/{w.m1}/forward", {"conversation_ids": [w.gb]}),
        ("POST", f"/messages/{w.m2}/report", {"reason": "spam"}),
        ("POST", f"/conversations/{w.g}/read", {"seq": 1}), ("POST", f"/conversations/{w.g}/unread", None),
        ("PUT", f"/conversations/{w.g}/preferences", {"muted": True}), ("POST", f"/conversations/{w.g}/typing", {"state": "start"}),
        ("PATCH", f"/conversations/{w.g}", {"name": "renamed"}), ("PUT", f"/conversations/{w.g}/announcement", {"text": "notice"}),
        ("PUT", f"/conversations/{w.g}/avatar", {"image_base64": "AAAA"}), ("DELETE", f"/conversations/{w.g}/avatar", None),
        ("POST", f"/conversations/{w.g}/members", {"user_ids": [owner]}), ("DELETE", f"/conversations/{w.g}/members/{w.ben}", None),
        ("PUT", f"/conversations/{w.g}/members/{w.ben}/role", {"role": "admin"}),
        ("POST", f"/conversations/{w.g}/transfer", {"user_id": w.ben}), ("POST", f"/conversations/{w.g}/archive", {}),
        ("POST", f"/conversations/{w.g}/leave", None), ("GET", f"/attachments/{w.aid}", None),
        ("GET", f"/directory?organization_id={w.a}", None),
        ("POST", "/dm", {"organization_id": w.a, "user_id": U["cid"]["id"]}),
        ("POST", "/groups", {"organization_id": w.a, "name": "new in A", "member_ids": [w.ben]}),
        ("GET", f"/conversations?organization_id={w.a}", None), ("GET", f"/search?q=invoice&organization_id={w.a}", None),
    ]
    for method, path, body in calls:
        _refused(c.request(method, M + path, json=body, headers=h), w.a)
    _refused(c.post(f"{M}/conversations/{w.g}/attachments", headers=h, files={"file": ("x.txt", b"after", "text/plain")}), w.a)
    # A is left out of every listing — before the limit — and the list says why
    listed = c.get(f"{M}/conversations").json()
    assert [x["id"] for x in listed["items"]] == [w.gb]
    assert listed["disabled_organizations"] == [w.a] and listed["platform_disabled"] is False
    assert [x["cid"] for x in c.get(f"{M}/search?q=invoice").json()["items"]] == [w.gb]
    assert [x["cid"] for x in c.get(f"{M}/search?q=invoice&limit=1").json()["items"]] == [w.gb]
    assert [x["id"] for x in c.get(f"{M}/sync?since=0").json()["conversations"]] == [w.gb]
    assert {x["id"]: x["messages_enabled"] for x in c.get(f"{M}/organizations").json()["items"]} == {w.a: False, w.b: True}
    assert svc.unread_total(w.ben) == {"unread": 2, "conversations": 1}           # B's group only
    # the stream keeps them known: refused live, back as they were
    assert {w.g, w.dm} <= set(svc.visible_conversation_ids(w.ann))
    # a non-member learns nothing new: the same 404 as before
    c.sign_in("cid")
    for path in (f"/conversations/{w.g}", f"/conversations/{w.g}/messages", f"/attachments/{w.aid}"):
        r = c.get(M + path)
        assert r.status_code == 404, (path, r.text)
    c.sign_in("outsider")
    assert c.get(f"{M}/directory?organization_id={w.a}").status_code == 404
    assert c.get(f"{M}/conversations?organization_id={w.a}").json()["items"] == []
    # nothing was written, deleted or moved
    assert _rows(store, [w.g, w.dm]) == before
    # back on: the same rows, the same lists, search finds A again, writing works
    assert _on(c) is True and _on(c) is False
    assert _rows(store, [w.g, w.dm]) == before
    h = c.sign_in("ann")
    assert set(_ids(c)) == {w.g, w.dm, w.gb} and c.get(f"{M}/conversations").json()["disabled_organizations"] == []
    assert {x["cid"] for x in c.get(f"{M}/search?q=invoice").json()["items"]} == {w.g, w.gb}
    assert c.get(f"{M}/conversations/{w.g}/messages").status_code == 200
    assert svc.unread_total(w.ben) == unread
    assert c.post(f"{M}/conversations/{w.g}/messages", headers=h, json={"body": "after the switch-on", "client_id": "b3-x3"}).status_code == 201
    assert svc.unread_total(w.ben)["conversations"] == 3


def test_someone_in_two_organizations_keeps_the_other(org):
    c = org
    w = _world(c)
    _off(c)
    h = c.sign_in("ann")
    assert c.post(f"{M}/conversations/{w.gb}/messages", headers=h, json={"body": "still in B", "client_id": "b3-b1"}).status_code == 201
    assert c.get(f"{M}/directory?organization_id={w.b}").status_code == 200
    dm_b = c.post(f"{M}/dm", headers=h, json={"organization_id": w.b, "user_id": w.ben})
    assert dm_b.status_code == 201, dm_b.text
    g2 = c.post(f"{M}/groups", headers=h, json={"organization_id": w.b, "name": "B 新组", "member_ids": [w.ben]})
    assert g2.status_code == 201, g2.text
    assert set(_ids(c, f"{M}/conversations?organization_id={w.b}")) == {w.gb, dm_b.json()["id"], g2.json()["id"]}
    assert set(_ids(c)) == {w.gb, dm_b.json()["id"], g2.json()["id"]}
    assert [x["cid"] for x in c.get(f"{M}/search?q=still").json()["items"]] == [w.gb]
    # B going off as well: both listed; A back: only B
    _off(c, w.b)
    assert c.get(f"{M}/conversations").json()["disabled_organizations"] == sorted([w.a, w.b])
    _on(c)
    assert c.get(f"{M}/conversations").json()["disabled_organizations"] == [w.b]


def test_presence_is_not_shown_through_a_disabled_organizations_conversations(org):
    c = org
    w = _world(c)
    svc = Messaging(c.ident.store, c.ident)
    cid_user = c.users["cid"]["id"]
    h = c.sign_in("ann")
    assert c.post(f"{M}/dm", headers=h, json={"organization_id": w.a, "user_id": cid_user}).status_code == 201
    assert cid_user in svc.co_members(w.ann)
    _off(c)
    assert cid_user not in svc.co_members(w.ann) and w.ben in svc.co_members(w.ann)     # ben is in B with ann too
    _on(c)
    assert cid_user in svc.co_members(w.ann)


def test_the_switch_refuses_what_it_does_not_switch(org):
    c = org
    apps = Apps(c.ident.store, c.ident)
    for app_id in (DOCS_APP_ID, "agent", "platform"):
        with pytest.raises(ServiceError) as e:
            apps.disable_for_organization(c.oid, app_id)
        assert (e.value.status, e.value.code) == (400, "bad_change")
    with pytest.raises(ServiceError) as e:
        apps.disable_for_organization("org_nosuchfixture", MESSAGES_APP_ID)
    assert (e.value.status, e.value.code) == (404, "no_organization")
    with pytest.raises(ServiceError) as e:
        apps.enable_for_organization("org_nosuchfixture", MESSAGES_APP_ID)
    assert (e.value.status, e.value.code) == (404, "no_organization")
    assert apps.enable_for_organization(c.oid, MESSAGES_APP_ID) is False       # never switched off: on already
    assert c.ident.store.one("SELECT 1 FROM organization_apps") is None


# ---------------------------------------------------------------- the race
def test_a_write_that_passed_its_checks_before_the_switch_off_does_not_land_after_it(org):
    """The in-transaction re-check: send, forward (its source and every target, in one writer
    transaction), open_dm, create_group, edit, react, add_members, set_announcement, set_avatar and
    attach re-read the switch under the writer lock. FIXTURE: the switch-off commits between the
    call's own checks and its transaction, from a patched step of the call; every patch is put
    back with setattr(original)."""
    c = org
    w = _world(c)
    store = c.ident.store
    svc = Messaging(store, c.ident)
    # every conversation and membership row too: open_dm and create_group would write new ones
    counts = lambda: (_rows(store, [w.g, w.dm, w.gb]), store.all("SELECT * FROM message_reactions ORDER BY message_id, user_id, emoji"),  # noqa: E731
                      store.all("SELECT conversation_id, updated_at FROM conversation_avatars ORDER BY conversation_id"),
                      store.all("SELECT * FROM conversations ORDER BY id"),
                      store.all("SELECT * FROM conversation_members ORDER BY conversation_id, user_id"))

    def switching_off(name, when=lambda *args: True):
        original = getattr(Messaging, name)
        fired = []

        def patched(self, *args, **kwargs):
            out = original(self, *args, **kwargs)
            if not fired and when(*args):
                fired.append(True)
                _off(c)
            return out
        return original, patched

    cid_user = c.users["cid"]["id"]
    cases = [
        ("send", "_active_user_ids", lambda *a: True, lambda: svc.send(w.g, w.ann, body="raced", client_id="b3-race1")),
        ("forward target", "_guard_dm", lambda *a: True, lambda: svc.forward(w.mb, w.ann, [w.g], client_id="b3-race2")),
        ("forward source", "_visible_message", lambda *a: True, lambda: svc.forward(w.m1, w.ann, [w.gb], client_id="b3-race3")),
        ("open_dm", "_guard_dm", lambda *a: True, lambda: svc.open_dm(w.a, w.ann, cid_user)),
        ("create_group", "_org_member", lambda oid, uid: uid == w.ben,
         lambda: svc.create_group(w.a, w.ann, name="raced group", member_ids=[w.ben])),
        ("edit", "_guard_dm", lambda *a: True, lambda: svc.edit(w.m1, w.ann, "edited after the switch-off")),
        ("react", "_guard_dm", lambda *a: True, lambda: svc.react(w.m1, w.ann, "👍")),
        ("add_members", "_manager", lambda *a: True, lambda: svc.add_members(w.g, w.ann, [cid_user])),
        ("set_announcement", "_manager", lambda *a: True, lambda: svc.set_announcement(w.g, w.ann, "raced announcement")),
        ("set_avatar", "_manager", lambda *a: True, lambda: svc.set_avatar(w.g, w.ann, None)),
        ("attach", "_guard_dm", lambda *a: True, lambda: svc.attach(w.g, w.ann, filename="raced.txt", data=b"raced bytes", scan={})),
        # every target passed its checks (B first, then A); the switch-off lands before the one
        # writer transaction: nothing reaches B either — a refusal leaves nothing half-done
        ("forward, two targets", "_guard_dm", lambda *a, _n=[]: (_n.append(1), len(_n) == 2)[1],
         lambda: svc.forward(w.mb, w.ann, [w.gb, w.g], client_id="b3-race4")),
    ]
    for label, name, when, call in cases:
        before = counts()
        original, patched = switching_off(name, when)
        setattr(Messaging, name, patched)
        try:
            with pytest.raises(ServiceError) as e:
                call()
        finally:
            setattr(Messaging, name, original)
        assert e.value.code == "messages_disabled", (label, e.value.code)
        assert organization_app_disabled(store, w.a, MESSAGES_APP_ID), label          # the switch-off did commit first
        assert counts() == before, label
        _on(c)
    # the controls: with nothing switched off, each goes through
    assert svc.send(w.g, w.ann, body="not raced", client_id="b3-ctl1")[1] is True
    assert [x["created"] for x in svc.forward(w.m1, w.ann, [w.gb, w.g], client_id="b3-ctl2")] == [True, True]
    assert svc.react(w.m1, w.ann, "👍")["reactions"]
    # CR-050: a recall that lands between the check and the write wins — the edit does not put the
    # words back, and no reaction lands on a recalled message (409 `already_gone`, nothing written)
    original = Messaging._guard_dm

    def recalling(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        # FIXTURE: what `recall` leaves on the row, written from the patched step
        store.execute("UPDATE messages SET deleted_at=?, recalled_at=?, body='' WHERE id=? AND deleted_at IS NULL", (now(), now(), w.m1))
        return out
    for call in (lambda: svc.edit(w.m1, w.ann, "put back after the recall"), lambda: svc.react(w.m1, w.ann, "❤️")):
        store.execute("UPDATE messages SET deleted_at=NULL, recalled_at=NULL, body='alpha invoice in A' WHERE id=?", (w.m1,))  # FIXTURE: the message back
        before = counts()
        Messaging._guard_dm = recalling
        try:
            with pytest.raises(ServiceError) as e:
                call()
        finally:
            Messaging._guard_dm = original
        assert (e.value.status, e.value.code) == (409, "already_gone")
        row = store.one("SELECT body, edited_at, deleted_at FROM messages WHERE id=?", (w.m1,))
        assert row["body"] == "" and row["edited_at"] is None and row["deleted_at"] is not None
        assert counts()[1] == before[1]


# ---------------------------------------------------------------- project discussions and cards
def _projects(c, w):
    """PA in A's workspace (ann, ben members), PB in B's (ann owns it); both discussions opened by
    ann. FIXTURE project rows, as in test_bridge.py."""
    store = c.ident.store
    wid = store.one("SELECT id FROM workspaces WHERE organization_id=?", (c.oid,))["id"]
    for key, role in (("ann", "editor"), ("ben", "viewer")):
        store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,?,1,?,?,?)",
                      (wid, c.users[key]["id"], role, now(), now(), c.users["owner"]["id"]))
    Apps(store, c.ident).register(app_id=DOCS_APP_ID, name="Synwevo Docs", launch_url="http://docs.testserver/")
    store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES (?,?,?,?,?)", (PA, "A 项目", wid, now(), now()))
    store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES (?,?,?,?,?)", (PB, "B 项目", w.bws, now(), now()))
    h = c.sign_in("ann")
    ca = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation", headers=h).json()["conversation"]["id"]
    cb = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PB}/conversation", headers=h).json()["conversation"]["id"]
    c.api.state.bridge_worker.publish = lambda users, event: None
    return wid, ca, cb


def _comment(c, pid, comment_id, author):
    store = c.ident.store
    with store.tx() as t:
        t.execute("INSERT INTO project_comments (id,project_id,tenant,author_id,body,created_at) VALUES (?,?,?,?,?,?)",
                  (comment_id, pid, store.one("SELECT tenant FROM projects WHERE id=?", (pid,))["tenant"], author, "fixture comment", now()))
        emit_comment(t, comment_id=comment_id, project_id=pid, workspace_id="w", author_id=author)


def test_a_project_discussion_opens_and_enrols_nobody_while_disabled(org):
    c = org
    w = _world(c)
    wid, ca, _ = _projects(c, w)
    store = c.ident.store
    cid_user = c.users["cid"]["id"]
    store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'viewer',1,?,?,?)",
                  (wid, cid_user, now(), now(), c.users["owner"]["id"]))
    _off(c)
    h = c.sign_in("cid")                     # may open the project, not yet in its discussion
    _refused(c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation", headers=h), w.a)
    _refused(c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation"), w.a)
    assert store.one("SELECT 1 FROM conversation_members WHERE conversation_id=? AND user_id=?", (ca, cid_user)) is None
    c.sign_in("ann")
    _refused(c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation"), w.a)
    _refused(c.get(f"{M}/conversations/{ca}"), w.a)
    c.sign_in("outsider")                    # cannot open the project: the same nothing as always
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation").status_code == 404
    _on(c)
    h = c.sign_in("cid")
    out = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PA}/conversation", headers=h).json()
    assert out["conversation"]["id"] == ca and out["joined"] is True


def test_a_project_discussion_join_that_passed_its_checks_before_the_switch_off_does_not_land_after_it(org):
    """The in-transaction re-check of `ObjectConversations.open` (B3): a person who may open the
    project, and passed every check while Message was on, is not enrolled once the switch-off
    commits before the join's transaction. FIXTURE: the switch-off is written from a patched
    `_anchor_for`, put back with setattr(original)."""
    c = org
    w = _world(c)
    wid, ca, _ = _projects(c, w)
    store = c.ident.store
    cid_user = c.users["cid"]["id"]
    store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,'viewer',1,?,?,?)",
                  (wid, cid_user, now(), now(), c.users["owner"]["id"]))
    objects = ObjectConversations(store, c.ident, Messaging(store, c.ident))
    members = lambda: store.all("SELECT * FROM conversation_members WHERE conversation_id=? ORDER BY user_id", (ca,))  # noqa: E731
    before = members()
    original = ObjectConversations._anchor_for

    def switching_off(self, *args, **kwargs):
        out = original(self, *args, **kwargs)
        _off(c)
        return out
    ObjectConversations._anchor_for = switching_off
    try:
        with pytest.raises(ServiceError) as e:
            objects.open(DOCS_APP_ID, "project", PA, cid_user)
    finally:
        ObjectConversations._anchor_for = original
    assert (e.value.status, e.value.code) == (403, "messages_disabled")
    assert organization_app_disabled(store, w.a, MESSAGES_APP_ID)                    # the switch-off did commit first
    assert members() == before
    _on(c)
    assert objects.open(DOCS_APP_ID, "project", PA, cid_user)["joined"] is True        # the control


def test_cards_for_a_disabled_organization_are_skipped_and_never_starve_another(org):
    c = org
    w = _world(c)
    _, ca, cb = _projects(c, w)
    store, worker = c.ident.store, c.api.state.bridge_worker
    _off(c)
    for i in range(50):
        _comment(c, PA, f"cm_b3a{i:03d}", w.ann)
    _comment(c, PB, "cm_b3b001", w.ann)            # queued behind fifty of A's
    cards = lambda cid: store.one("SELECT COUNT(*) AS n FROM messages m JOIN message_cards k ON k.message_id=m.id WHERE m.conversation_id=?", (cid,))["n"]  # noqa: E731
    assert worker.run_once() == {"delivered": 0, "skipped": 50, "failed": 0}
    assert worker.run_once() == {"delivered": 1, "skipped": 0, "failed": 0}
    assert cards(ca) == 0 and cards(cb) == 1
    rows = store.all("SELECT last_error, delivered_at FROM messaging_outbox WHERE object_id=?", (PA,))
    assert len(rows) == 50 and all(r["last_error"] == SKIPPED_MESSAGES_DISABLED and r["delivered_at"] for r in rows)
    # back on: nothing skipped is replayed; new events are delivered as before
    _on(c)
    assert worker.run_once() == {"delivered": 0, "skipped": 0, "failed": 0}
    _comment(c, PA, "cm_b3a999", w.ann)
    assert worker.run_once()["delivered"] == 1 and cards(ca) == 1


def test_a_card_into_a_paused_project_discussion_is_skipped(org):
    """G3: the worker used to deliver into a discussion whose workspace was frozen — reaching open
    tabs while every read of it answered `workspace_unavailable`."""
    c = org
    w = _world(c)
    wid, ca, _ = _projects(c, w)
    store, worker = c.ident.store, c.api.state.bridge_worker
    c.ident.set_workspace_frozen(wid, True)
    _comment(c, PA, "cm_b3p001", w.ann)
    assert worker.run_once()["skipped"] == 1
    assert store.one("SELECT last_error FROM messaging_outbox WHERE event_id='comment:cm_b3p001'")["last_error"] == SKIPPED_PAUSED
    c.ident.set_workspace_frozen(wid, False)
    _comment(c, PA, "cm_b3p002", w.ann)
    assert worker.run_once()["delivered"] == 1


def test_a_queued_suite_send_into_a_disabled_organization_fails_with_the_code(org):
    c = org
    w = _world(c)
    wid, ca, _ = _projects(c, w)
    store = c.ident.store
    audience = audience_fingerprint(Messaging(store, c.ident).get(ca, w.ann))
    _off(c)
    with store.tx() as t:                     # FIXTURE: the queue row Suite writes
        emit_user_message(t, event_id="run_b3fix01", conversation_id=ca, workspace_id=wid, organization_id=w.a,
                          sender_id=w.ann, body="queued before the switch-off", audience=audience)
    c.api.state.bridge_worker.run_once()
    assert user_message_state(store, "run_b3fix01") == {"state": "failed", "code": "messages_disabled"}
    assert store.one("SELECT COUNT(*) AS n FROM messages WHERE body='queued before the switch-off'")["n"] == 0


# ---------------------------------------------------------------- notifications, moderation, export
def test_mail_and_push_about_a_disabled_organization_are_stale_while_the_inbox_item_stays(org):
    c = org
    w = _world(c)
    store = c.ident.store
    n = Notifier(store)
    mention = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='conversation.mention'", (w.ben,))
    report = store.one("SELECT * FROM notifications WHERE user_id=? AND kind='conversation.reported'", (c.users["owner"]["id"],))
    assert mention and report
    d = lambda row: {"notification_id": row["id"], "user_id": row["user_id"], "category": row["category"], "kind": row["kind"],  # noqa: E731
                     "severity": row["severity"], "notified_at": row["created_at"]}
    assert n.stale_reason(d(report), n.preferences(report["user_id"])) is None
    _off(c)
    assert n.stale_reason(d(mention), n.preferences(w.ben)) == "messages_disabled"
    assert n.stale_reason(d(report), n.preferences(report["user_id"])) == "messages_disabled"
    assert n.render_external(mention, "en", {"preview": "full"})["body"] == ""
    assert mention["id"] in {x["id"] for x in n.list(w.ben)["items"]}             # the inbox keeps it
    # the platform-wide switch says the same
    _on(c)
    assert n.stale_reason(d(report), n.preferences(report["user_id"])) is None
    Apps(store, c.ident).set_status(MESSAGES_APP_ID, "disabled")
    assert n.stale_reason(d(mention), n.preferences(w.ben)) == "messages_disabled"
    Apps(store, c.ident).set_status(MESSAGES_APP_ID, "active")
    # G2: a report's to-do of a frozen organization is not mailed either (its conversation item
    # already was not: `no longer a member`)
    Organizations(store, c.ident).platform_transition(w.a, c.users["owner"]["id"], "freeze", reason=REASON)
    assert n.stale_reason(d(report), n.preferences(report["user_id"])) == "organization_unavailable"
    assert n.stale_reason(d(mention), n.preferences(w.ben)) == "no longer a member"


def test_moderation_and_the_account_export_go_on_while_disabled(org):
    c = org
    w = _world(c)
    store = c.ident.store
    svc = Messaging(store, c.ident)
    _off(c)
    assert [r["id"] for r in svc.reports_for_organization(w.a)] == [w.rid]
    out = svc.review_report(w.rid, c.users["owner"]["id"], "dismissed", "fixture decision")
    assert out["status"] == "dismissed"
    sent = {m["body"] for m in messaging_export(store, w.ann)["messages_sent"]}
    assert {"alpha invoice in A", "dm hello in A", "alpha invoice in B"} <= sent


# ---------------------------------------------------------------- the stream
def _stream(c, out: list, *, cursor=None):
    """One tab's stream through the fixture's client (its event loop serves every tab, as one
    process does). The test client hands the body over only once the stream has ended (it does
    not stream), so what a tab heard is read after `event: end`; what the server did meanwhile is
    watched on the hub itself (`_wait`)."""
    url = f"{M}/stream" + (f"?last_event_id={cursor}" if cursor is not None else "")
    with c.stream("GET", url) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            out.append(line)
            if line.startswith("event: end") or len(out) > 400:
                break


def _events(lines, kind):
    return [json.loads(lines[i + 1][len("data: "):]) for i, line in enumerate(list(lines))
            if line == f"event: {kind}" and i + 1 < len(lines) and lines[i + 1].startswith("data: ")]


def _wait(pred, deadline_s=10.0) -> bool:
    """Polls until `pred()` holds, or the deadline passes: no fixed sleeps (the heartbeat is 1 s
    in these tests, and the CI container is slow and shared)."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def _announced(hub, uid, since, change) -> bool:
    """Whether a heartbeat has published the organization event `change` to `uid` since ring id
    `since` (`publish_once` puts it in the ring, where a reconnecting tab would replay it)."""
    return any(e.get("type") == "organization" and e.get("change") == change for e in (hub.replay(uid, since) or []))


def test_open_tabs_hear_the_switch_once_and_nothing_about_the_disabled_organization(org):
    c = org
    w = _world(c)
    store, hub, metrics = c.ident.store, c.api.state.message_hub, c.api.state.messaging_metrics
    import synwevo_messages.routes as mr
    c.sign_in("ann")
    tabs: list[list[str]] = [[], []]
    heard: list = []
    start = hub.current_id()
    passed = lambda: metrics()["stream_events"]  # noqa: E731

    def drained(n_tabs=2):
        """FIXTURE: a sentinel event about no conversation goes straight through each tab's live
        loop; once every tab has passed it on, everything published before it has been dealt
        with by that tab (the loop is sequential) — and the tab is past its hello."""
        before = passed()
        hub.publish([w.ann], {"type": "fixture_sentinel"}, ephemeral=True)
        return _wait(lambda: passed() >= before + n_tabs)

    def act():
        heard.append(_wait(lambda: hub.connections_of(w.ann) == 2))     # both tabs subscribed
        heard.append(drained())                                           # and said hello (their switches are read)
        _off(c)
        heard.append(_wait(lambda: _announced(hub, w.ann, start, "messages_disabled")))   # announced at a heartbeat
        # FIXTURE: events another path would publish into both organizations' conversations
        hub.publish([w.ann], {"type": "message", "conversation_id": w.g, "message": {"seq": 99, "body": "fixture A event"}})
        hub.publish([w.ann], {"type": "message", "conversation_id": w.gb, "message": {"seq": 99, "body": "fixture B event"}})
        heard.append(drained())                                           # both tabs decided on them while A was off
        _on(c)
        heard.append(_wait(lambda: _announced(hub, w.ann, start, "messages_enabled")))
        store.execute("UPDATE sessions SET revoked_at=? WHERE user_id=?", (time.time(), w.ann))   # the next heartbeat ends both
    original = mr.HEARTBEAT_S
    mr.HEARTBEAT_S = 1
    try:
        threads = [threading.Thread(target=_stream, args=(c, tab), daemon=True) for tab in tabs]
        for t in threads:
            t.start()
        actor = threading.Thread(target=act, daemon=True)
        actor.start()
        actor.join(60)
        for t in threads:
            t.join(15)
    finally:
        mr.HEARTBEAT_S = original
    assert heard == [True, True, True, True, True], heard
    for lines in tabs:
        assert "event: hello" in lines, lines
        changes = [(e["organization_id"], e["change"], e["scope"]) for e in _events(lines, "organization")]
        assert changes == [(w.a, "messages_disabled", "organization"), (w.a, "messages_enabled", "organization")], lines
        bodies = [e["message"]["body"] for e in _events(lines, "message")]
        assert "fixture B event" in bodies and "fixture A event" not in bodies
        assert "event: end" in lines


def test_a_reconnecting_tab_is_not_replayed_the_disabled_organizations_events(org):
    c = org
    w = _world(c)
    hub = c.api.state.message_hub
    cursor = hub.current_id()
    h = c.sign_in("ben")
    assert c.post(f"{M}/conversations/{w.g}/messages", headers=h, json={"body": "ring A", "client_id": "b3-r1"}).status_code == 201
    assert c.post(f"{M}/conversations/{w.gb}/messages", headers=h, json={"body": "ring B", "client_id": "b3-r2"}).status_code == 201
    _off(c)
    c.sign_in("ann")
    lines: list[str] = []
    import synwevo_messages.routes as mr
    original = mr.HEARTBEAT_S
    mr.HEARTBEAT_S = 1
    try:
        tab = threading.Thread(target=_stream, args=(c, lines), kwargs={"cursor": cursor}, daemon=True)
        tab.start()
        # the replay is written before the live loop starts; the session revoked once the tab is
        # subscribed ends the stream at its first heartbeat
        assert _wait(lambda: hub.connections_of(w.ann) == 1)
        c.ident.store.execute("UPDATE sessions SET revoked_at=? WHERE user_id=?", (time.time(), w.ann))
        tab.join(15)
    finally:
        mr.HEARTBEAT_S = original
    assert "event: end" in lines, lines
    bodies = [e["message"]["body"] for e in _events(lines, "message")]
    assert "ring B" in bodies and "ring A" not in bodies


# ---------------------------------------------------------------- G1: the platform-wide switch
def test_the_platform_wide_switch_holds_for_sessions_already_signed_in(org):
    c = org
    w = _world(c)
    store = c.ident.store
    apps = Apps(store, c.ident)
    h = c.sign_in("ann")
    assert apps.set_status(MESSAGES_APP_ID, "disabled")
    _refused(c.get(f"{M}/conversations/{w.g}"), w.a, "platform")
    _refused(c.post(f"{M}/conversations/{w.gb}/messages", headers=h, json={"body": "x", "client_id": "b3-p1"}), w.b, "platform")
    _refused(c.get(f"{M}/directory?organization_id={w.b}"), w.b, "platform")
    listed = c.get(f"{M}/conversations").json()
    assert listed["items"] == [] and listed["platform_disabled"] is True and listed["disabled_organizations"] == sorted([w.a, w.b])
    assert c.get(f"{M}/search?q=invoice").json()["items"] == [] and c.get(f"{M}/sync?since=0").json()["conversations"] == []
    assert Messaging(store, c.ident).unread_total(w.ben) == {"unread": 0, "conversations": 0}
    assert not any(x["messages_enabled"] for x in c.get(f"{M}/organizations").json()["items"])
    # the platform's switch outranks the organization's: both off, the platform is named
    _off(c)
    _refused(c.get(f"{M}/conversations/{w.g}"), w.a, "platform")
    assert apps.set_status(MESSAGES_APP_ID, "active")
    _refused(c.get(f"{M}/conversations/{w.g}"), w.a, "organization")
    assert c.get(f"{M}/conversations/{w.gb}").status_code == 200
    _on(c)
    assert set(_ids(c)) == {w.g, w.dm, w.gb}


# ---------------------------------------------------------------- G2: the organization's lifecycle
def test_a_frozen_or_recoverable_organization_pauses_all_of_its_conversations(org):
    c = org
    w = _world(c)
    store = c.ident.store
    orgs = Organizations(store, c.ident)
    actor = c.users["owner"]["id"]
    before = _rows(store, [w.g, w.dm])
    for close, reopen in (("freeze", "unfreeze"), ("delete", "recover")):
        orgs.platform_transition(w.a, actor, close, reason=REASON)
        h = c.sign_in("ann")
        for r in (c.get(f"{M}/conversations/{w.g}"), c.get(f"{M}/conversations/{w.dm}/messages"),
                  c.post(f"{M}/conversations/{w.g}/messages", headers=h, json={"body": "while paused", "client_id": f"b3-{close}"}),
                  c.get(f"{M}/directory?organization_id={w.a}"), c.get(f"{M}/conversations?organization_id={w.a}")):
            assert r.status_code == 409 and r.json()["error"] == "organization_unavailable", r.text
            assert r.json()["detail"] == {"organization_id": w.a}
        assert _ids(c) == [w.gb]
        assert c.get(f"{M}/conversations").json()["disabled_organizations"] == []    # a pause is not the switch
        # precedence: switched off as well → the switch is named
        _off(c)
        _refused(c.get(f"{M}/conversations/{w.g}"), w.a)
        _on(c)
        orgs.platform_transition(w.a, actor, reopen, reason=REASON)
        c.sign_in("ann")
        assert set(_ids(c)) == {w.g, w.dm, w.gb}
    assert _rows(store, [w.g, w.dm]) == before


def test_an_organization_past_its_recovery_window_loses_its_conversations_at_once(org):
    c = org
    w = _world(c)
    store = c.ident.store
    Organizations(store, c.ident).platform_transition(w.a, c.users["owner"]["id"], "delete", reason=REASON)
    store.execute("UPDATE organizations SET recover_until=? WHERE id=?", (now() - 1, w.a))   # FIXTURE clock
    _off(c)                                                                                 # LOST outranks the switch
    h = c.sign_in("ann")
    for r in (c.get(f"{M}/conversations/{w.g}"), c.post(f"{M}/conversations/{w.dm}/messages", headers=h,
                                                         json={"body": "after the window", "client_id": "b3-lost"})):
        assert r.status_code == 404 and r.json()["error"] == "no_conversation", r.text
    assert _ids(c) == [w.gb]
    assert w.g not in Messaging(store, c.ident).visible_conversation_ids(w.ann)              # an open tab drops it
    # LOST outranks the platform's switch too: A stays the same nothing, while B names the platform
    assert Apps(store, c.ident).set_status(MESSAGES_APP_ID, "disabled")
    for r in (c.get(f"{M}/conversations/{w.g}"), c.post(f"{M}/conversations/{w.dm}/messages", headers=h,
                                                         json={"body": "after the window, platform off", "client_id": "b3-lost-p"})):
        assert r.status_code == 404 and r.json()["error"] == "no_conversation", r.text
    _refused(c.get(f"{M}/conversations/{w.gb}"), w.b, "platform")
