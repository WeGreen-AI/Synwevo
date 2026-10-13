"""A Docs project's discussion while its workspace is paused (A44 review, A44-ACC-2).

A workspace the platform froze, or deleted and may still recover within seven days — or whose
organization is frozen — is *paused*, not lost: the bridge's sweep removes nobody and archives
nothing, every read and write of the discussion is refused live with `workspace_unavailable`, and
once the workspace is back the discussion is exactly as it was. A deletion past its recovery window
is lost, as before: everyone is removed and the discussion archived — and closed to everyone the
moment the window ends (`404 no_conversation`), not a sweep later (A44-PAUSE-2). A person who loses
their own access (their workspace membership) during a pause is still removed. Search leaves closed
discussions out before its limit, so they never take the place of a readable hit (A44-PAUSE-4).

The messages application alone, as in every test of this module; the Docs project is a seeded row
in the shared tables (FIXTURE), and the platform's acts go through the core services the console
calls (`workspace_lifecycle.platform_transition`, `Identity.set_workspace_frozen`,
`Organizations.platform_transition`). Regression for the review probes
`test_probe_object_conversation.py`, `test_probe_objconv_refute.py`, `test_probe_lost_window.py`
and `test_probe_search_limit.py`."""
from __future__ import annotations

from synwevo_core import workspace_lifecycle
from synwevo_core.apps import DOCS_APP_ID, Apps
from synwevo_core.messaging import Messaging
from synwevo_core.messaging_bridge import audience_fingerprint, emit_user_message, user_message_state
from synwevo_core.organizations import Organizations
from synwevo_core.store import now

M = "/api/messaging"
PID = "p_pausefix01"
REASON = "Fixture: workspace lifecycle regression"


def _seed(c):
    """The organization's workspace with ann (editor) and ben (viewer), a Docs project in it
    (FIXTURE row), ann opening its discussion and writing one message. Returns (wid, cid)."""
    store = c.ident.store
    wid = store.one("SELECT id FROM workspaces WHERE organization_id=?", (c.oid,))["id"]
    for key, role in (("ann", "editor"), ("ben", "viewer")):
        store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) "
                      "VALUES (?,?,?,1,?,?,?)", (wid, c.users[key]["id"], role, now(), now(), c.users["owner"]["id"]))
    Apps(store, c.ident).register(app_id=DOCS_APP_ID, name="Synwevo Docs", launch_url="http://docs.testserver/")
    store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES (?,?,?,?,?)",
                  (PID, "暂停夹具项目", wid, now(), now()))
    ha = c.sign_in("ann")
    conv = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ha).json()["conversation"]
    assert len(conv["members"]) == 3                                   # owner, ann, ben
    r = c.post(f"{M}/conversations/{conv['id']}/messages", json={"body": "暂停前的消息 fixture", "client_id": "pause-c1"}, headers=ha)
    assert r.status_code == 201, r.text
    c.api.state.bridge_worker.publish = lambda users, event: None
    return wid, conv["id"]


def _members(c, cid) -> set[str]:
    return {r["user_id"] for r in c.ident.store.all(
        "SELECT user_id FROM conversation_members WHERE conversation_id=? AND left_at IS NULL", (cid,))}


def _refused_while_paused(c, cid, who="ann"):
    """Every way in answers `workspace_unavailable` (or leaves the discussion out of a listing) —
    at once, not only after a sweep — and the sweep removes nobody and archives nothing."""
    before = _members(c, cid)
    h = c.sign_in(who)
    assert c.get(f"{M}/conversations/{cid}/messages").json()["error"] == "workspace_unavailable"
    assert c.post(f"{M}/conversations/{cid}/messages", json={"body": "清扫前发送", "client_id": "pause-c0"}, headers=h).status_code == 403
    assert c.api.state.bridge_worker.sweep() == 0
    assert _members(c, cid) == before
    assert c.ident.store.one("SELECT archived_at FROM conversations WHERE id=?", (cid,))["archived_at"] is None
    h = c.sign_in(who)
    for r in (c.get(f"{M}/conversations/{cid}"), c.get(f"{M}/conversations/{cid}/messages"),
              c.post(f"{M}/conversations/{cid}/messages", json={"body": "暂停中发送", "client_id": "pause-c2"}, headers=h),
              c.post(f"{M}/conversations/{cid}/read", json={"seq": 1}, headers=h),
              c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation"),
              c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=h)):
        assert r.status_code == 403 and r.json()["error"] == "workspace_unavailable", r.text
    assert cid not in [x["id"] for x in c.get(f"{M}/conversations").json()["items"]]
    assert c.get(f"{M}/search?q=fixture").json()["items"] == []
    assert cid not in [x["id"] for x in c.get(f"{M}/sync?since=0").json()["conversations"]]
    # an organization member who never had the project learns nothing new: still 404
    c.sign_in("cid")
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation").status_code == 404
    assert c.get(f"{M}/conversations/{cid}").status_code == 404


def _as_before(c, cid, who="ann"):
    """Back: the same members, the history, sending, and the object's entry as before."""
    h = c.sign_in(who)
    g = c.get(f"{M}/conversations/{cid}")
    assert g.status_code == 200 and g.json()["archived"] is False, g.text
    bodies = [m["body"] for m in c.get(f"{M}/conversations/{cid}/messages").json()["items"] if m["kind"] == "text"]
    assert "暂停前的消息 fixture" in bodies
    assert c.post(f"{M}/conversations/{cid}/messages", json={"body": "恢复后的消息", "client_id": "pause-c3"}, headers=h).status_code == 201
    out = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=h).json()
    assert out["conversation"]["id"] == cid and out["created"] is False and out["joined"] is False
    assert cid in [x["id"] for x in c.get(f"{M}/conversations").json()["items"]]
    assert [x["cid"] for x in c.get(f"{M}/search?q=fixture").json()["items"]] == [cid]


def test_a_frozen_workspace_pauses_the_project_discussion_and_unfreezing_gives_it_back(org):
    c = org
    wid, cid = _seed(c)
    members = _members(c, cid)
    unread_before = Messaging(c.ident.store, c.ident).unread_total(c.users["ben"]["id"])
    assert unread_before["unread"] >= 1
    ann = c.users["ann"]["id"]
    audience = audience_fingerprint(Messaging(c.ident.store, c.ident).get(cid, ann))
    c.ident.set_workspace_frozen(wid, True)
    _refused_while_paused(c, cid)
    # the badge does not count what cannot be read
    assert Messaging(c.ident.store, c.ident).unread_total(c.users["ben"]["id"])["unread"] == 0
    # a message queued by another process before the freeze (FIXTURE: the queue row Suite writes)
    # is refused at delivery with the pause's own code, and nothing is stored
    with c.ident.store.tx() as t:
        emit_user_message(t, event_id="run_pausefix01", conversation_id=cid, workspace_id=wid, organization_id=c.oid,
                          sender_id=ann, body="排队中的消息", audience=audience)
    c.api.state.bridge_worker.run_once()
    assert user_message_state(c.ident.store, "run_pausefix01") == {"state": "failed", "code": "workspace_unavailable"}
    c.ident.set_workspace_frozen(wid, False)
    assert c.api.state.bridge_worker.sweep() == 0
    assert _members(c, cid) == members
    _as_before(c, cid)
    assert Messaging(c.ident.store, c.ident).unread_total(c.users["ben"]["id"])["unread"] >= unread_before["unread"]


def test_a_deleted_workspace_pauses_the_discussion_until_it_is_recovered(org):
    c = org
    wid, cid = _seed(c)
    members = _members(c, cid)
    actor = c.users["owner"]["id"]
    workspace_lifecycle.platform_transition(c.ident.store, wid, actor, "delete", reason=REASON)
    _refused_while_paused(c, cid)
    workspace_lifecycle.platform_transition(c.ident.store, wid, actor, "recover", reason=REASON)
    assert c.ident.get_workspace(wid)["status"] == "active"
    assert c.api.state.bridge_worker.sweep() == 0
    assert _members(c, cid) == members
    _as_before(c, cid)


def test_a_deletion_past_its_recovery_window_loses_the_discussion_as_before(org):
    c = org
    wid, cid = _seed(c)
    workspace_lifecycle.platform_transition(c.ident.store, wid, c.users["owner"]["id"], "delete", reason=REASON)
    assert c.api.state.bridge_worker.sweep() == 0
    # FIXTURE clock: the seven days are over (the row as it is once recover_until has passed)
    c.ident.store.execute("UPDATE workspaces SET recover_until=? WHERE id=?", (now() - 1, wid))
    assert c.api.state.bridge_worker.sweep() == 3
    assert _members(c, cid) == set()
    assert c.ident.store.one("SELECT archived_at FROM conversations WHERE id=?", (cid,))["archived_at"]
    c.sign_in("ann")
    assert c.get(f"{M}/conversations/{cid}").status_code == 404
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation").status_code == 404


def test_a_person_who_loses_their_own_access_during_a_pause_is_still_removed(org):
    c = org
    wid, cid = _seed(c)
    ben = c.users["ben"]["id"]
    c.ident.set_workspace_frozen(wid, True)
    c.ident.remove_member(wid, ben)
    assert c.ident.membership(wid, ben) is None
    assert c.api.state.bridge_worker.sweep() == 1
    assert ben not in _members(c, cid) and len(_members(c, cid)) == 2
    assert c.ident.store.one("SELECT archived_at FROM conversations WHERE id=?", (cid,))["archived_at"] is None
    c.ident.set_workspace_frozen(wid, False)
    _as_before(c, cid)
    c.sign_in("ben")
    assert c.get(f"{M}/conversations/{cid}").status_code == 404


def test_a_frozen_organization_pauses_the_discussion_too(org):
    c = org
    wid, cid = _seed(c)
    members = _members(c, cid)
    orgs = Organizations(c.ident.store, c.ident)
    orgs.platform_transition(c.oid, c.users["owner"]["id"], "freeze", reason=REASON)
    _refused_while_paused(c, cid)
    orgs.platform_transition(c.oid, c.users["owner"]["id"], "unfreeze", reason=REASON)
    assert c.api.state.bridge_worker.sweep() == 0
    assert _members(c, cid) == members
    _as_before(c, cid)


def test_workspace_availability_tells_a_pause_from_a_loss(org):
    """The one rule the bridge and the Suite's approvals sweep share (FIXTURE rows: the lifecycle
    columns written directly, as each act leaves them)."""
    from synwevo_core.workspace_availability import AVAILABLE, LOST, PAUSED, availability
    c = org
    store = c.ident.store
    wid = store.one("SELECT id FROM workspaces WHERE organization_id=?", (c.oid,))["id"]
    ws = lambda **v: store.execute("UPDATE workspaces SET " + ",".join(f"{k}=?" for k in v) + " WHERE id=?", (*v.values(), wid))  # noqa: E731
    og = lambda **v: store.execute("UPDATE organizations SET " + ",".join(f"{k}=?" for k in v) + " WHERE id=?", (*v.values(), c.oid))  # noqa: E731
    t = now()
    assert availability(store, wid) == AVAILABLE
    assert availability(store, "w_nosuchfixture") == LOST and availability(store, None) == LOST
    ws(status="frozen")
    assert availability(store, wid) == PAUSED
    ws(status="deleted", deleted_at=t, recover_until=t + 60)
    assert availability(store, wid) == PAUSED and availability(store, wid, t + 61) == LOST
    ws(recover_until=None)
    assert availability(store, wid) == LOST                       # a deletion without a window does not come back
    ws(status="pending_claim", deleted_at=None)
    assert availability(store, wid) == LOST                       # the unclaimed historical workspace is not a pause
    ws(status="active")
    og(status="frozen")
    assert availability(store, wid) == PAUSED
    og(status="deleted", deleted_at=t, recover_until=t + 60)
    assert availability(store, wid) == PAUSED and availability(store, wid, t + 61) == LOST
    ws(status="frozen")
    assert availability(store, wid, t + 61) == LOST               # the worse of the two
    og(status="active", deleted_at=None, recover_until=None)
    ws(status="active")
    assert availability(store, wid) == AVAILABLE


def test_the_discussion_closes_the_moment_the_recovery_window_ends_not_a_sweep_later(org):
    """A44-PAUSE-2 (probe `test_probe_lost_window.py`): paused → lost before the sweep runs is
    answered as removed at once — no read, no write, no listing, no badge, no queued send — and
    the sweep then does the removal and archiving as before."""
    c = org
    wid, cid = _seed(c)
    ann, ben = c.users["ann"]["id"], c.users["ben"]["id"]
    svc = Messaging(c.ident.store, c.ident)
    audience = audience_fingerprint(svc.get(cid, ann))
    workspace_lifecycle.platform_transition(c.ident.store, wid, c.users["owner"]["id"], "delete", reason=REASON)
    c.sign_in("ann")
    assert c.get(f"{M}/conversations/{cid}/messages").json()["error"] == "workspace_unavailable"
    # FIXTURE clock: the seven days are over (the row as it is once recover_until has passed); the
    # bridge's sweep has not run yet
    c.ident.store.execute("UPDATE workspaces SET recover_until=? WHERE id=?", (now() - 1, wid))
    assert _members(c, cid) == {c.users[k]["id"] for k in ("owner", "ann", "ben")}
    h = c.sign_in("ann")
    for r in (c.get(f"{M}/conversations/{cid}"), c.get(f"{M}/conversations/{cid}/messages"),
              c.post(f"{M}/conversations/{cid}/messages", json={"body": "窗口结束后发送", "client_id": "lost-c1"}, headers=h),
              c.post(f"{M}/conversations/{cid}/read", json={"seq": 1}, headers=h)):
        assert r.status_code == 404 and r.json()["error"] == "no_conversation", r.text
    for r in (c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation"),
              c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=h)):
        assert r.status_code == 404 and r.json()["error"] == "no_object", r.text
    assert cid not in [x["id"] for x in c.get(f"{M}/conversations").json()["items"]]
    assert c.get(f"{M}/search?q=fixture").json()["items"] == []
    assert cid not in [x["id"] for x in c.get(f"{M}/sync?since=0").json()["conversations"]]
    assert svc.unread_total(ben) == {"unread": 0, "conversations": 0}
    # an open tab is told at its next heartbeat that the discussion is gone (`_lost_memberships`)
    assert cid not in svc.visible_conversation_ids(ann)
    # a message queued by another process before the window ended is refused, not delivered
    with c.ident.store.tx() as t:
        emit_user_message(t, event_id="run_lostwin01", conversation_id=cid, workspace_id=wid, organization_id=c.oid,
                          sender_id=ann, body="排队中的消息", audience=audience)
    c.api.state.bridge_worker.run_once()
    assert user_message_state(c.ident.store, "run_lostwin01") == {"state": "failed", "code": "no_conversation"}
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=? AND body=?", (cid, "窗口结束后发送"))["n"] == 0
    # the sweep does the bookkeeping: everyone removed, the discussion archived, still 404
    assert c.api.state.bridge_worker.sweep() == 3
    assert _members(c, cid) == set()
    assert c.ident.store.one("SELECT archived_at FROM conversations WHERE id=?", (cid,))["archived_at"]
    c.sign_in("ann")
    assert c.get(f"{M}/conversations/{cid}/messages").status_code == 404


def test_a_pause_keeps_the_open_tab_and_the_paused_refusal(org):
    """Only a loss drops the discussion from an open tab; a paused one stays known (it is refused
    live and comes back as it was), and readers outside the project are unaffected."""
    c = org
    wid, cid = _seed(c)
    svc = Messaging(c.ident.store, c.ident)
    c.ident.set_workspace_frozen(wid, True)
    assert cid in svc.visible_conversation_ids(c.users["ann"]["id"])
    c.sign_in("ann")
    assert c.get(f"{M}/conversations/{cid}").json()["error"] == "workspace_unavailable"


def test_search_leaves_closed_discussions_out_before_its_limit(org):
    """A44-PAUSE-4 (probe `test_probe_search_limit.py`): newer hits in a paused discussion never
    use up the page — the readable hit elsewhere is returned whatever the limit, and the paused
    hits are back once the workspace is."""
    c = org
    wid, cid = _seed(c)
    ha = c.sign_in("ann")
    g = c.post(f"{M}/groups", json={"organization_id": c.oid, "name": "普通群", "member_ids": [c.users["ben"]["id"]]}, headers=ha)
    assert g.status_code == 201, g.text
    gid = g.json()["id"]
    assert c.post(f"{M}/conversations/{gid}/messages", json={"body": "invoice 2026 plain group", "client_id": "sl-g"}, headers=ha).status_code == 201
    for i in range(3):
        r = c.post(f"{M}/conversations/{cid}/messages", json={"body": f"invoice 2026 project {i}", "client_id": f"sl-{i}"}, headers=ha)
        assert r.status_code == 201, r.text
    hits = lambda limit: [x["cid"] for x in c.get(f"{M}/search?q=invoice&limit={limit}").json()["items"]]  # noqa: E731
    assert hits(3) == [cid, cid, cid]                                   # newest first while available
    c.ident.set_workspace_frozen(wid, True)
    for who in ("ann", "ben"):
        c.sign_in(who)
        assert hits(1) == [gid] and hits(3) == [gid] and hits(50) == [gid]
        assert [x["cid"] for x in c.get(f"{M}/search?q=invoice&limit=1&cid={cid}").json()["items"]] == []
    c.ident.set_workspace_frozen(wid, False)
    c.sign_in("ann")
    assert hits(3) == [cid, cid, cid] and hits(50) == [cid, cid, cid, gid]

