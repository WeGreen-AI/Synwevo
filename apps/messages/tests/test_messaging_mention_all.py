"""CI-99: @所有人 in a group (docs/platform/messaging.md §7.22), on the real store and routes.

Only the group's owner and admins may mention everyone; a member is refused (403
`mention_all_forbidden`) and a direct message has no "everyone" (400). The message counts toward
every other member's unread mentions — the same floor, recall and delete-for-me rules as a
personal @ — and reaches each of them as a `conversation.mention` inbox item under that member's
own notify mode and mute: `none` and a running mute stay quiet, and so does someone who blocked
the sender. The API shows `mention_all: true` and keeps `mentions` to user ids.
"""
from __future__ import annotations

import time

M = "/api/messaging"


def sign_in(c, key: str) -> dict:
    return c.sign_in(key)


def say(c, who: str, cid: str, body: str, **extra):
    h = sign_in(c, who)
    return c.post(f"{M}/conversations/{cid}/messages", headers=h, json={"body": body, **extra})


def group(c, owner: str, name: str, members: list[str]) -> dict:
    h = sign_in(c, owner)
    r = c.post(f"{M}/groups", headers=h, json={"organization_id": c.oid, "name": name, "member_ids": [c.users[k]["id"] for k in members]})
    assert r.status_code == 201, r.text
    return r.json()


def conv_of(c, who: str, cid: str) -> dict:
    sign_in(c, who)
    return c.get(f"{M}/conversations/{cid}").json()


def mention_items(c, key: str, cid: str) -> int:
    return c.ident.store.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id=? AND kind='conversation.mention' AND object_id=?",
                             (c.users[key]["id"], cid))["n"]


def test_only_the_owner_and_admins_mention_everyone_and_a_dm_has_no_everyone(org):
    c = org
    g = group(c, "owner", "全员群", ["ann", "ben", "cid"])
    r = say(c, "ann", g["id"], "@所有人 开会了", mention_all=True)
    assert r.status_code == 403 and r.json()["error"] == "mention_all_forbidden" and r.json()["detail"]["role"] == "member"
    assert conv_of(c, "ben", g["id"])["unread_mentions"] == 0, "a refused message is not stored"
    # an admin may
    ho = sign_in(c, "owner")
    assert c.put(f"{M}/conversations/{g['id']}/members/{c.users['ann']['id']}/role", headers=ho, json={"role": "admin"}).status_code == 200
    r = say(c, "ann", g["id"], "@所有人 开会了", mention_all=True)
    assert r.status_code == 201, r.text
    msg = r.json()["message"]
    assert msg["mention_all"] is True and msg["mentions"] == []
    # a direct message has no "everyone"; a non-boolean flag is a shape error
    dm = c.post(f"{M}/dm", headers=sign_in(c, "owner"), json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    r = say(c, "owner", dm["id"], "@所有人", mention_all=True)
    assert r.status_code == 400 and r.json()["detail"]["field"] == "mention_all"
    r = say(c, "owner", g["id"], "x", mention_all="yes")
    assert r.status_code == 400 and r.json()["detail"]["field"] == "mention_all"
    # history renders it the same way to everyone
    sign_in(c, "cid")
    item = next(m for m in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if m["id"] == msg["id"])
    assert item["mention_all"] is True and item["mentions"] == []


def test_everyone_counts_as_a_mention_of_each_member_under_their_own_settings(org):
    c = org
    U = c.users
    g = group(c, "owner", "通知群", ["ann", "ben", "cid"])
    # ben turned notifications off for this group; cid muted it for an hour; ann keeps the default
    assert c.put(f"{M}/conversations/{g['id']}/preferences", headers=sign_in(c, "ben"), json={"notify": "none"}).status_code == 200
    assert c.put(f"{M}/conversations/{g['id']}/preferences", headers=sign_in(c, "cid"),
                 json={"muted_until": time.time() + 3600}).status_code == 200
    r = say(c, "owner", g["id"], "@所有人 明天九点开会", mention_all=True, mention_ids=[U["ann"]["id"]])
    assert r.status_code == 201, r.text
    msg = r.json()["message"]
    assert msg["mention_all"] is True and msg["mentions"] == [U["ann"]["id"]]
    # every other member has one unread mention — the mark does not depend on notify or mute
    for key in ("ann", "ben", "cid"):
        assert conv_of(c, key, g["id"])["unread_mentions"] == 1, key
    assert conv_of(c, "owner", g["id"])["unread_mentions"] == 0, "the sender is not mentioned by their own message"
    # the inbox follows each member's own choice: ann is told, ben (none) and cid (muted) are not
    assert mention_items(c, "ann", g["id"]) == 1
    assert mention_items(c, "ben", g["id"]) == 0 and mention_items(c, "cid", g["id"]) == 0
    assert mention_items(c, "owner", g["id"]) == 0
    # reading clears it; a recalled @所有人 never counts
    sign_in(c, "ann")
    c.post(f"{M}/conversations/{g['id']}/read", headers=sign_in(c, "ann"), json={"seq": msg["seq"]})
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 0
    r2 = say(c, "owner", g["id"], "@所有人 这条撤回", mention_all=True).json()["message"]
    assert c.post(f"{M}/messages/{r2['id']}/recall", headers=sign_in(c, "owner")).status_code == 200
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 0


def test_someone_who_blocked_the_sender_gets_no_mark_and_no_item(org):
    c = org
    g = group(c, "owner", "屏蔽群", ["ann", "ben"])
    assert c.put(f"{M}/blocks/{c.users['owner']['id']}", headers=sign_in(c, "ben"), json={}).status_code in (200, 201)
    assert say(c, "owner", g["id"], "@所有人", mention_all=True).status_code == 201
    assert conv_of(c, "ben", g["id"])["unread_mentions"] == 0 and mention_items(c, "ben", g["id"]) == 0
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 1 and mention_items(c, "ann", g["id"]) == 1


def test_the_actor_of_a_system_line_is_not_told_about_their_own_action(org):
    """Found during the CI-99 browser proof: a new group's creator saw 1 unread for their own
    「创建了群聊」, and renaming a group did the same. The line counts as read for its actor when
    they were caught up; the other members still see it as new."""
    c = org
    g = group(c, "owner", "自己的动作", ["ann"])
    assert conv_of(c, "owner", g["id"])["unread"] == 0
    assert conv_of(c, "ann", g["id"])["unread"] == 1
    assert c.patch(f"{M}/conversations/{g['id']}", headers=sign_in(c, "owner"), json={"name": "改过的名字"}).status_code == 200
    assert conv_of(c, "owner", g["id"])["unread"] == 0
    assert conv_of(c, "ann", g["id"])["unread"] == 2
    # someone behind on reading stays behind: their own line does not mark the others' messages read
    assert say(c, "ann", g["id"], "ann 说了一句").status_code == 201
    assert c.patch(f"{M}/conversations/{g['id']}", headers=sign_in(c, "owner"), json={"description": "说明"}).status_code == 200
    assert conv_of(c, "owner", g["id"])["unread"] == 2


def test_an_object_discussion_has_no_everyone_and_the_shell_is_told_where_it_may_offer_it(org):
    """Review of CI-99: in an application object's discussion (CI-75) the members are everyone who
    may open the object and the owner is whoever opened it first — no @所有人 there. The
    conversation says, per reader, whether the composer may offer it."""
    c = org
    g = group(c, "owner", "普通群", ["ann"])
    obj = group(c, "owner", "对象讨论", ["ann"])
    # TEST FIXTURE: the bridge's link row (messaging_bridge.object_conversations) for the second group
    c.ident.store.execute("CREATE TABLE IF NOT EXISTS object_conversations (app TEXT, kind TEXT, object_id TEXT, conversation_id TEXT, "
                          "workspace_id TEXT, organization_id TEXT, created_by TEXT, created_at REAL)")
    c.ident.store.execute("INSERT INTO object_conversations (app,kind,object_id,conversation_id,organization_id,created_by,created_at) "
                          "VALUES ('docweave','project','p_fixture',?,?,?,1)", (obj["id"], c.oid, c.users["owner"]["id"]))
    assert conv_of(c, "owner", g["id"])["can_mention_all"] is True
    assert conv_of(c, "owner", obj["id"])["can_mention_all"] is False
    assert conv_of(c, "ann", g["id"])["can_mention_all"] is False
    r = say(c, "owner", obj["id"], "@所有人", mention_all=True)
    assert r.status_code == 403 and r.json()["error"] == "mention_all_forbidden" and r.json()["detail"]["object"] is True


def test_a_retry_after_a_demotion_gets_the_stored_message_back(org):
    """Review of CI-99: the duplicate check comes before the permission check, so a retry of a
    message stored while the sender was an admin is answered with that message, not refused."""
    c = org
    g = group(c, "owner", "重试群", ["ann", "ben"])
    ho = sign_in(c, "owner")
    assert c.put(f"{M}/conversations/{g['id']}/members/{c.users['ann']['id']}/role", headers=ho, json={"role": "admin"}).status_code == 200
    first = say(c, "ann", g["id"], "@所有人 集合", mention_all=True, client_id="retry-fixture-01")
    assert first.status_code == 201
    ho = sign_in(c, "owner")
    assert c.put(f"{M}/conversations/{g['id']}/members/{c.users['ann']['id']}/role", headers=ho, json={"role": "member"}).status_code == 200
    again = say(c, "ann", g["id"], "@所有人 集合", mention_all=True, client_id="retry-fixture-01")
    assert again.status_code == 201 and again.json()["created"] is False
    assert again.json()["message"]["id"] == first.json()["message"]["id"]
    assert say(c, "ann", g["id"], "@所有人 新的", mention_all=True, client_id="retry-fixture-02").status_code == 403


def test_a_system_line_is_read_for_its_actor_by_the_read_floor_and_clears_marked_unread(org):
    """Review of CI-99: the actor's floor is max(last_read_seq, joined_seq), as for unread counts;
    and their own line clears a marked-unread flag the way sending does."""
    c = org
    g = group(c, "owner", "标记群", ["ann"])
    store = c.ident.store
    # TEST FIXTURE: ann's membership as a rejoin through an object leaves it — read position one
    # behind the join floor (messaging_bridge sets last_read_seq=last, joined_seq=last+1)
    last = store.one("SELECT last_seq FROM conversations WHERE id=?", (g["id"],))["last_seq"]
    store.execute("UPDATE conversation_members SET last_read_seq=?, joined_seq=? WHERE conversation_id=? AND user_id=?",
                  (last, last + 1, g["id"], c.users["ann"]["id"]))
    ho = sign_in(c, "owner")
    assert c.put(f"{M}/conversations/{g['id']}/members/{c.users['ann']['id']}/role", headers=ho, json={"role": "admin"}).status_code == 200
    ha = sign_in(c, "ann")
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ha, json={"description": "ann 改的"}).status_code == 200
    assert conv_of(c, "ann", g["id"])["unread"] == 0
    # marked unread, then her own action: the mark goes, as when she sends
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/conversations/{g['id']}/unread", headers=ha).status_code == 200
    assert conv_of(c, "ann", g["id"])["me"]["marked_unread"] is True
    ha = sign_in(c, "ann")
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ha, json={"description": "再改一次"}).status_code == 200
    assert conv_of(c, "ann", g["id"])["me"]["marked_unread"] is False
