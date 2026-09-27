"""The Docs ↔ Message bridge on the messages origin (CI-75): object conversations that follow the
object's own access, cards delivered from the outbox once and only once, rendered per reader,
never producing inbox items — and the application identity that sends them and cannot sign in.

Docs is not mounted here (the messages application alone, as in every test of this module): the
Docs rows a card is about (`projects`, `jobs`, `project_comments`, `project_copies`) are seeded
into the shared tables, and the outbox is written the way Docs writes it — through
`synwevo_core.messaging_bridge.emit_*` on a transaction. The whole chain with the real Docs
routes runs in `tests/integration/test_messaging_bridge.py`."""
from __future__ import annotations

import json

import pytest

from synwevo_core.apps import Apps, DOCS_APP_ID
from synwevo_core.messaging_bridge import (NEUTRAL_TITLE, SKIPPED_NO_CONVERSATION, emit_comment, emit_copy,
                                           emit_job_finished, ensure_app_account, is_app_account)
from synwevo_core.store import now

M = "/api/messaging"
DOCS = "http://docs.testserver"
ACCOUNTS = "http://accounts.testserver"
PID = "p_fixture0001"


def _seed(c):
    """The organization's workspace with ann (editor) and ben (viewer) as members — cid stays an
    organization member without workspace membership — a Docs project in it, and Synwevo Docs
    registered so links can be built from the registry."""
    store = c.ident.store
    wid = store.one("SELECT id FROM workspaces WHERE organization_id=?", (c.oid,))["id"]
    for key, role in (("ann", "editor"), ("ben", "viewer")):
        store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,?,1,?,?,?)",
                      (wid, c.users[key]["id"], role, now(), now(), c.users["owner"]["id"]))
    Apps(store, c.ident).register(app_id=DOCS_APP_ID, name="Synwevo Docs", launch_url=DOCS + "/")
    store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES (?,?,?,?,?)", (PID, "合同项目", wid, now(), now()))
    return wid


def _comment(c, cid_: str, author: str, body: str) -> str:
    """What Docs does when a comment is stored: the row and the outbox event, one transaction."""
    store = c.ident.store
    with store.tx() as t:
        t.execute("INSERT INTO project_comments (id,project_id,tenant,author_id,body,created_at) VALUES (?,?,?,?,?,?)",
                  (cid_, PID, store.one("SELECT tenant FROM projects WHERE id=?", (PID,))["tenant"], author, body, now()))
        emit_comment(t, comment_id=cid_, project_id=PID, workspace_id="w", author_id=author)
    return cid_


def _job(c, jid: str, status: str = "completed", counts=None, run_no: int = 1) -> str:
    store = c.ident.store
    with store.tx() as t:
        t.execute("INSERT OR REPLACE INTO jobs (id,project_id,status,created_at,finished_at,counts,tenant,run_no) VALUES (?,?,?,?,?,?,?,?)",
                  (jid, PID, status, now(), now(), json.dumps(counts or {"total": 2, "success": 2, "failed": 0}), "w", run_no))
        emit_job_finished(t, job_id=jid, project_id=PID, workspace_id="w", run_no=run_no, status=status)
    return jid


def test_an_object_conversation_follows_the_objects_own_access(org):
    c = org
    wid = _seed(c)
    ha = c.sign_in("ann")
    # first call creates: a named group in the organization, workspace-bound, marked as an object
    # conversation, with everyone who may open the project as members — cid (organization member,
    # not a workspace member) is not one of them
    r = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ha)
    assert r.status_code == 200, r.text
    out = r.json()
    conv = out["conversation"]
    assert out["created"] is True and out["joined"] is False
    assert conv["kind"] == "group" and conv["name"] == "合同项目" and conv["workspace_id"] == wid and conv["organization_id"] == c.oid
    assert conv["me"]["role"] == "owner"
    assert {m["user_id"] for m in conv["members"]} == {c.users[k]["id"] for k in ("owner", "ann", "ben")}
    assert out["object"] == {"app": DOCS_APP_ID, "app_name": "Synwevo Docs", "kind": "project", "id": PID, "title": "合同项目",
                             "href": f"{DOCS}/p/{PID}", "workspace_id": wid}
    assert c.ident.store.one("SELECT purpose FROM conversations WHERE id=?", (conv["id"],))["purpose"] == "object"
    # the second call is the same conversation; GET answers it too; a job of the project resolves to it
    again = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ha).json()
    assert again["conversation"]["id"] == conv["id"] and again["created"] is False and again["joined"] is False
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation").json()["conversation"]["id"] == conv["id"]
    c.ident.store.execute("INSERT INTO jobs (id,project_id,status,created_at,tenant,run_no) VALUES ('j_fixture01',?, 'completed',?,?,1)", (PID, now(), wid))
    assert c.post(f"{M}/objects/{DOCS_APP_ID}/job/j_fixture01/conversation", headers=ha).json()["conversation"]["id"] == conv["id"]
    # what the conversation is about, for a member
    assert c.get(f"{M}/conversations/{conv['id']}/object").json()["href"] == f"{DOCS}/p/{PID}"
    # ben (viewer) is already a member and may read it; the object summary is his too
    hb = c.sign_in("ben")
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation").json()["conversation"]["me"]["role"] == "member"
    # cid may not open the project: 404, never 403, and never the title — for GET and POST alike,
    # and the conversation itself is not reachable by id
    hc = c.sign_in("cid")
    for method in ("GET", "POST"):
        r = c.request(method, f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=hc)
        assert r.status_code == 404 and "合同" not in r.text, (method, r.text)
    assert c.get(f"{M}/conversations/{conv['id']}").status_code == 404
    # someone outside the organization, an unknown application, an unknown kind: the same nothing
    ho = c.sign_in("outsider")
    assert c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ho).status_code == 404
    ha = c.sign_in("ann")
    assert c.post(f"{M}/objects/other-app/project/{PID}/conversation", headers=ha).status_code == 404
    assert c.post(f"{M}/objects/{DOCS_APP_ID}/template/{PID}/conversation", headers=ha).status_code == 404
    assert c.post(f"{M}/objects/{DOCS_APP_ID}/project/p_nope/conversation", headers=ha).status_code == 404
    # a project in a personal workspace has no organization to hold a conversation: 409, not 404
    personal = c.ident.ensure_personal_workspace(c.users["ann"]["id"])["id"]
    c.ident.store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES ('p_personal01','私人',?,?,?)", (personal, now(), now()))
    r = c.post(f"{M}/objects/{DOCS_APP_ID}/project/p_personal01/conversation", headers=ha)
    assert r.status_code == 409 and r.json()["error"] == "no_organization"
    # an ordinary group is not about anything
    g = c.post(f"{M}/groups", headers=ha, json={"organization_id": c.oid, "name": "普通群", "member_ids": [c.users["ben"]["id"]]}).json()
    assert c.get(f"{M}/conversations/{g['id']}/object").status_code == 404


def test_cards_are_delivered_once_rendered_per_reader_and_never_notify(org):
    c = org
    wid = _seed(c)
    ha = c.sign_in("ann")
    conv = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ha).json()["conversation"]
    cid = conv["id"]
    worker = c.api.state.bridge_worker
    sent: list[tuple[list[str], dict]] = []
    worker.publish = lambda users, event: sent.append((users, event))
    # Docs' side of the transaction: a comment and a finished run for this project, and a
    # comment on a project nobody discusses
    _comment(c, "cm_fixture01", c.users["ben"]["id"], "这个字段取哪一列？")
    _job(c, "j_fixture01")
    c.ident.store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES ('p_other0001','别的项目',?,?,?)", (wid, now(), now()))
    with c.ident.store.tx() as t:
        t.execute("INSERT INTO project_comments (id,project_id,tenant,author_id,body,created_at) VALUES ('cm_other01','p_other0001',?,?,'x',?)", (wid, c.users["ann"]["id"], now()))
        emit_comment(t, comment_id="cm_other01", project_id="p_other0001", workspace_id=wid, author_id=c.users["ann"]["id"])
    assert worker.run_once() == {"delivered": 2, "skipped": 1, "failed": 0}
    rows = {r["event_id"]: r for r in c.ident.store.all("SELECT * FROM messaging_outbox")}
    assert rows["comment:cm_other01"]["delivered_at"] and rows["comment:cm_other01"]["last_error"] == SKIPPED_NO_CONVERSATION, "an event never creates a conversation"
    assert rows["comment:cm_fixture01"]["last_error"] is None and rows["job:j_fixture01:1"]["attempts"] == 1
    # the cards, as ann (zh-Hans) reads them: sent by the application, links built from the registry
    hist = c.get(f"{M}/conversations/{cid}/messages").json()["items"]
    cards = [m for m in hist if m.get("card")]
    assert len(cards) == 2 and all(m["sender_kind"] == "app" and m["sender"] == "Synwevo Docs" and m["kind"] == "text" and m["mentions"] == [] for m in cards)
    comment, job = cards
    assert comment["body"] == "Synwevo Docs · 新评论" and "合同" not in comment["body"]
    assert comment["card"] == {"app": DOCS_APP_ID, "app_name": "Synwevo Docs", "title": "合同项目", "subtitle": "Ben Member: 这个字段取哪一列？", "state": "open",
                               "object": {"kind": "comment", "id": "cm_fixture01"},
                               "actions": [{"label": "查看评论", "href": f"{DOCS}/p/{PID}?comment=cm_fixture01"}]}
    assert job["body"] == "Synwevo Docs · 生成任务已完成"
    assert job["card"] == {"app": DOCS_APP_ID, "app_name": "Synwevo Docs", "title": "合同项目", "subtitle": "2 份成功，0 份失败", "state": "completed",
                           "object": {"kind": "job", "id": "j_fixture01"},
                           "actions": [{"label": "查看任务", "href": f"{DOCS}/p/{PID}/jobs/j_fixture01"}]}
    # the same cards in ben's language
    c.ident.store.execute("UPDATE users SET locale='en' WHERE id=?", (c.users["ben"]["id"],))
    hb = c.sign_in("ben")
    cards_b = [m["card"] for m in c.get(f"{M}/conversations/{cid}/messages").json()["items"] if m.get("card")]
    assert cards_b[1]["subtitle"] == "2 succeeded, 0 failed" and cards_b[1]["actions"][0]["label"] == "View the job"
    assert cards_b[0]["actions"][0]["label"] == "View the comment"
    # fanned out per reader (one event per active member, each with that reader's card), never to the application itself
    readers = sorted(u for users, ev in sent if ev["type"] == "message" for u in users)
    assert readers == sorted([c.users[k]["id"] for k in ("owner", "ann", "ben")] * 2)
    assert all(ev["message"]["card"]["title"] == "合同项目" and ev["message"]["sender_kind"] == "app" for _, ev in sent if ev["type"] == "message")
    # cards count as unread like any message (system row + 2 cards for ben), but produce no inbox item
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 3, "conversations": 1}
    for key in ("owner", "ann", "ben"):
        kinds = [x["kind"] for x in c.api.state.notifier.list(c.users[key]["id"])["items"]]
        assert not any(k.startswith("conversation.") for k in kinds), (key, kinds)
    # idempotent: nothing left to deliver; the same events emitted again are ignored; a row whose
    # mark was lost after the insert (a crash in between) does not become a second card
    assert worker.run_once() == {"delivered": 0, "skipped": 0, "failed": 0}
    with c.ident.store.tx() as t:
        assert emit_job_finished(t, job_id="j_fixture01", project_id=PID, workspace_id=wid, run_no=1, status="completed") is False
        assert emit_comment(t, comment_id="cm_fixture01", project_id=PID, workspace_id=wid, author_id=c.users["ben"]["id"]) is False
    c.ident.store.execute("UPDATE messaging_outbox SET delivered_at=NULL WHERE event_id='job:j_fixture01:1'")
    assert worker.run_once()["delivered"] == 1
    assert len([m for m in c.get(f"{M}/conversations/{cid}/messages").json()["items"] if m.get("card")]) == 2
    # a second run of the same job is a second event and a second card, built from the job's state now
    _job(c, "j_fixture01", status="completed_with_errors", counts={"total": 3, "success": 2, "failed": 1}, run_no=2)
    worker.run_once()
    last = c.get(f"{M}/conversations/{cid}/messages").json()["items"][-1]
    assert last["card"]["state"] == "completed_with_errors" and last["card"]["subtitle"] == "2 succeeded, 1 failed" and last["body"] == "Synwevo Docs · 生成任务部分失败"
    # a member's reaction on a card works like on any message; the answer is rendered for the
    # reactor (their own card, since CI-74 every response has a viewer) while the event fanned out
    # to everyone carries none (each reader keeps the one rendered for them and re-reads history)
    r = c.put(f"{M}/messages/{last['id']}/reactions", headers=hb, json={"emoji": "👍"})
    assert r.status_code == 200 and r.json()["sender_kind"] == "app"
    assert r.json().get("card", {}).get("state") in (None, "completed_with_errors")
    # the card's plain text is searchable, the object's title is not in it
    assert [m["id"] for m in c.get(f"{M}/search?q=生成任务").json()["items"]] and not c.get(f"{M}/search?q=合同项目").json()["items"]
    # a deleted comment leaves nothing to say: skipped, not failed
    _comment(c, "cm_fixture02", c.users["ben"]["id"], "马上删掉")
    c.ident.store.execute("UPDATE project_comments SET deleted_at=? WHERE id='cm_fixture02'", (now(),))
    assert worker.run_once() == {"delivered": 0, "skipped": 1, "failed": 0}


def test_a_reader_who_lost_the_object_sees_a_neutral_card_and_then_loses_the_conversation(org):
    c = org
    wid = _seed(c)
    ha = c.sign_in("ann")
    cid = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=ha).json()["conversation"]["id"]
    worker = c.api.state.bridge_worker
    worker.publish = lambda users, event: None
    _comment(c, "cm_fixture01", c.users["ann"]["id"], "请看第三列")
    worker.run_once()
    ben = c.users["ben"]["id"]
    # ben leaves the workspace but stays in the organization: still a member of the conversation
    # for the moment, and what he reads is the card's application and state, not the project
    c.ident.remove_member(wid, ben)
    assert c.ident.membership(wid, ben) is None and c.ident.store.one("SELECT 1 AS x FROM organization_memberships WHERE organization_id=? AND user_id=?", (c.oid, ben))
    hb = c.sign_in("ben")
    hist = c.get(f"{M}/conversations/{cid}/messages").json()["items"]
    card = [m for m in hist if m.get("card")][0]["card"]
    assert card == {"app": DOCS_APP_ID, "app_name": "Synwevo Docs", "state": "open", "title": NEUTRAL_TITLE["zh-Hans"], "object": {"kind": "comment"}, "degraded": True}
    # neither the comment (the subtitle) nor the object's title is anywhere in his rendering of the
    # cards; the group's *name* — set from the title when the group was made, a group name like any
    # other from then on — stays what every member sees until the sweep removes him
    assert "第三列" not in json.dumps(hist, ensure_ascii=False)
    assert all("合同" not in json.dumps(m["card"], ensure_ascii=False) for m in hist if m.get("card"))
    obj = c.get(f"{M}/conversations/{cid}/object").json()
    assert obj["degraded"] is True and obj["title"] == NEUTRAL_TITLE["zh-Hans"] and "href" not in obj and "id" not in obj
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation").status_code == 404
    # the worker's sweep removes him — the object's access decides the conversation's membership
    assert worker.sweep() == 1
    assert c.get(f"{M}/conversations/{cid}").status_code == 404
    assert c.get(f"{M}/conversations/{cid}/messages").status_code == 404
    assert c.get(f"{M}/conversations").json()["items"] == []
    ha = c.sign_in("ann")
    rows = [m["system"] for m in c.get(f"{M}/conversations/{cid}/messages").json()["items"] if m["kind"] == "system"]
    removed = [s for s in rows if s["event"] == "removed"][-1]
    app_uid = c.ident.store.one("SELECT user_id FROM app_accounts WHERE app=?", (DOCS_APP_ID,))["user_id"]
    assert removed == {"event": "removed", "removed": [ben], "reason": "object_access_lost", "actor": app_uid}
    assert worker.sweep() == 0
    # back in the workspace, ben may open the project again and joins — history from now
    c.ident.add_member(wid, ben, "viewer")
    hb = c.sign_in("ben")
    out = c.post(f"{M}/objects/{DOCS_APP_ID}/project/{PID}/conversation", headers=hb).json()
    assert out["joined"] is True and out["created"] is False and out["conversation"]["id"] == cid
    assert [m for m in c.get(f"{M}/conversations/{cid}/messages").json()["items"] if m.get("card")] == []
    # the owner losing access hands the group to the earliest remaining member
    c.ident.remove_member(wid, c.users["ann"]["id"])
    assert worker.sweep() == 1
    hb = c.sign_in("ben")
    conv = c.get(f"{M}/conversations/{cid}").json()
    assert {m["user_id"]: m["role"] for m in conv["members"]} == {c.users["owner"]["id"]: "owner", ben: "member"}


def test_copy_requests_are_discussed_by_the_requester_and_the_deciders(org):
    c = org
    wid = _seed(c)
    store = c.ident.store
    ann, owner = c.users["ann"]["id"], c.users["owner"]["id"]
    personal = c.ident.ensure_personal_workspace(ann)["id"]
    store.execute("INSERT INTO projects (id,name,tenant,created_at,updated_at) VALUES ('p_personal01','私人合同',?,?,?)", (personal, now(), now()))
    from synwevo_core.project_copies import ProjectCopies
    copies = ProjectCopies(store, c.ident)
    req = copies.create(requester_id=ann, source_workspace_id=personal, source_project_id="p_personal01", target_workspace_id=wid,
                        name="合同（组织副本）", purpose=None, scope={}, manifest={"project": {"name": "私人合同"}}, digest="d")
    assert copies.deciders(c.oid, wid) == [owner]
    # the requester opens the discussion: requester + deciders; a workspace member who cannot decide is not in it and cannot open it
    ha = c.sign_in("ann")
    out = c.post(f"{M}/objects/{DOCS_APP_ID}/copy_request/{req['id']}/conversation", headers=ha).json()
    assert out["created"] is True and out["object"]["kind"] == "copy_request" and out["object"]["title"] == "合同（组织副本）"
    assert out["object"]["href"] == f"{ACCOUNTS}/organizations/{c.oid}?approval={req['id']}"
    assert {m["user_id"] for m in out["conversation"]["members"]} == {ann, owner} and out["conversation"]["workspace_id"] is None
    hb = c.sign_in("ben")
    assert c.get(f"{M}/objects/{DOCS_APP_ID}/copy_request/{req['id']}/conversation").status_code == 404
    # the decision (the account service calls the same service method) queues its card on the same transaction
    copies.decide(req["id"], owner, "approve", reason="内容合适，批准复制")
    row = store.one("SELECT * FROM messaging_outbox WHERE event_id=?", (f"copy:{req['id']}:approved",))
    assert row and row["kind"] == "copy.decided" and row["object_kind"] == "copy_request" and row["actor_id"] == owner
    worker = c.api.state.bridge_worker
    worker.publish = lambda users, event: None
    assert worker.run_once()["delivered"] == 1
    ha = c.sign_in("ann")
    card = [m for m in c.get(f"{M}/conversations/{out['conversation']['id']}/messages").json()["items"] if m.get("card")][-1]
    assert card["body"] == "Synwevo Docs · 复制申请已批准"
    assert card["card"] == {"app": DOCS_APP_ID, "app_name": "Synwevo Docs", "title": "合同（组织副本）", "state": "approved", "subtitle": "Ola Owner: 内容合适，批准复制",
                            "object": {"kind": "copy_request", "id": req["id"]},
                            "actions": [{"label": "查看审批", "href": f"{ACCOUNTS}/organizations/{c.oid}?approval={req['id']}"}]}
    # the outcome (Docs calls `finish` after copying) adds a card with the copy's own link first
    copies.claim_approved(req["id"])
    copies.finish(req["id"], "done", result={"project_id": "p_copy00001"})
    assert worker.run_once()["delivered"] == 1
    card = [m for m in c.get(f"{M}/conversations/{out['conversation']['id']}/messages").json()["items"] if m.get("card")][-1]["card"]
    assert card["state"] == "done" and [a["href"] for a in card["actions"]] == [f"{DOCS}/p/p_copy00001", f"{ACCOUNTS}/organizations/{c.oid}?approval={req['id']}"]
    assert card["actions"][0]["label"] == "在 Synwevo Docs 中打开"


def test_the_application_identity_cannot_sign_in_and_is_in_no_directory(org):
    c = org
    store = c.ident.store
    uid = store.one("SELECT user_id FROM app_accounts WHERE app=?", (DOCS_APP_ID,))["user_id"]
    u = c.ident.get_user(uid)
    assert u["status"] == "active" and u["display_name"] == "Synwevo Docs" and is_app_account(store, uid)
    assert store.one("SELECT 1 AS x FROM user_passwords WHERE user_id=?", (uid,)) is None
    assert store.one("SELECT 1 AS x FROM memberships WHERE user_id=?", (uid,)) is None
    assert store.one("SELECT 1 AS x FROM organization_memberships WHERE user_id=?", (uid,)) is None
    assert ensure_app_account(store, c.ident, DOCS_APP_ID) == uid, "idempotent across starts"
    # no session can be minted for it — whatever route asked
    from synwevo_core.errors import ServiceError
    from synwevo_core.sessions import Sessions
    with pytest.raises(ServiceError) as ex:
        Sessions(store).create(uid, workspace_id=None, amr="password")
    assert ex.value.code == "app_account"
    # not a person anyone can message or find
    ha = c.sign_in("ann")
    assert c.get(f"{M}/directory?organization_id={c.oid}&q=Synwevo").json()["items"] == []
    assert c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": uid}).status_code == 404
    # an ordinary message is from a person
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    msg = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "hi"}).json()["message"]
    assert msg["sender_kind"] == "user" and "card" not in msg
    # emitting for an unknown application or kind is a programming error, refused at once
    from synwevo_core.messaging_bridge import emit
    with store.tx() as t:
        for bad in ({"app": "other-app"}, {"kind": "docs.saved"}, {"object_kind": "template"}):
            args = {"app": DOCS_APP_ID, "event_id": "x:1", "kind": "docs.comment", "object_kind": "project", "object_id": PID, **bad}
            with pytest.raises(ValueError):
                emit(t, **args)
    assert store.one("SELECT 1 AS x FROM messaging_outbox WHERE event_id='x:1'") is None
