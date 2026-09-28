"""CI-78: the messaging capabilities still missing from the round-9 table, on the real store and the
real routes — unread mentions per conversation, the organization scope filter, a group avatar held to
the platform's avatar rules and served to current members only, and blocking that protects the blocker
without telling the blocked person (docs/platform/messaging.md §7.15–§7.18)."""
from __future__ import annotations

import base64
import binascii
import struct
import zlib

M = "/api/messaging"


def sign_in(c, key: str) -> dict:
    return c.sign_in(key)


def say(c, who: str, cid: str, body: str, **extra) -> dict:
    h = sign_in(c, who)
    r = c.post(f"{M}/conversations/{cid}/messages", headers=h, json={"body": body, **extra})
    assert r.status_code == 201, r.text
    return r.json()["message"]


def group(c, owner: str, name: str, members: list[str]) -> dict:
    h = sign_in(c, owner)
    r = c.post(f"{M}/groups", headers=h, json={"organization_id": c.oid, "name": name, "member_ids": [c.users[k]["id"] for k in members]})
    assert r.status_code == 201, r.text
    return r.json()


def conv_of(c, who: str, cid: str) -> dict:
    sign_in(c, who)
    return c.get(f"{M}/conversations/{cid}").json()


def listed(c, who: str, **params) -> list[dict]:
    sign_in(c, who)
    r = c.get(f"{M}/conversations", params=params)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xffffffff)


def png(width: int = 2, height: int = 2, meta: bytes = b"") -> bytes:
    """A real 8-bit RGB PNG (fixture), optionally with an ancillary chunk the server must strip."""
    rows = b"".join(b"\x00" + b"\x10\x60\xd0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + meta
            + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b""))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def inbox_kinds(c, key: str) -> list[str]:
    return [r["kind"] for r in c.ident.store.all("SELECT kind FROM notifications WHERE user_id=? ORDER BY created_at", (c.users[key]["id"],))]


# ---------------------------------------------------------------------------- 1. unread mentions
def test_unread_mentions_count_what_mentions_the_reader_after_the_floor_and_clear_on_read(org):
    c = org
    U = c.users
    g = group(c, "owner", "提及群", ["ann", "ben"])
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 0
    say(c, "owner", g["id"], "@Ann 看一下", mention_ids=[U["ann"]["id"]])
    say(c, "ben", g["id"], "@Ann 还有这个", mention_ids=[U["ann"]["id"], U["owner"]["id"]])
    say(c, "ben", g["id"], "没有提及任何人")
    recalled = say(c, "ben", g["id"], "@Ann 这条会撤回", mention_ids=[U["ann"]["id"]])
    hidden = say(c, "owner", g["id"], "@Ann 这条 Ann 对自己删除", mention_ids=[U["ann"]["id"]])
    # the reader's own mention of someone else never counts for the reader
    say(c, "ann", g["id"], "@Ben 你好", mention_ids=[U["ben"]["id"]])
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/messages/{recalled['id']}/recall", headers=hb).status_code == 200
    ha = sign_in(c, "ann")
    assert c.delete(f"{M}/messages/{hidden['id']}", headers=ha).json()["hidden"] is True
    # ann's own message moved her read cursor to the end: nothing is unread, so nothing mentions her unread
    one = conv_of(c, "ann", g["id"])
    assert one["unread"] == 0 and one["unread_mentions"] == 0
    # new mentions after her read position count; the recalled and hidden ones never do
    say(c, "owner", g["id"], "@Ann 第二轮", mention_ids=[U["ann"]["id"]])
    say(c, "ben", g["id"], "@Ann @Owner 第二轮", mention_ids=[U["ann"]["id"], U["owner"]["id"]])
    r2 = say(c, "ben", g["id"], "@Ann 会撤回", mention_ids=[U["ann"]["id"]])
    h2 = say(c, "owner", g["id"], "@Ann 会被 Ann 删除", mention_ids=[U["ann"]["id"]])
    say(c, "ben", g["id"], "普通消息")
    c.post(f"{M}/messages/{r2['id']}/recall", headers=sign_in(c, "ben"))
    c.delete(f"{M}/messages/{h2['id']}", headers=sign_in(c, "ann"))
    one = conv_of(c, "ann", g["id"])
    assert one["unread_mentions"] == 2 and one["unread"] >= 2
    row = next(x for x in listed(c, "ann") if x["id"] == g["id"])
    assert row["unread_mentions"] == 2, "the list and the single read agree"
    assert conv_of(c, "owner", g["id"])["unread_mentions"] == 0, "owner read to the end by sending"
    # sync carries it too
    sign_in(c, "ann")
    synced = {x["id"]: x for x in c.get(f"{M}/sync", params={"since": 0}).json()["conversations"]}
    assert synced[g["id"]]["unread_mentions"] == 2
    # reading to the end clears it
    ha = sign_in(c, "ann")
    c.post(f"{M}/conversations/{g['id']}/read", headers=ha, json={"seq": 10 ** 6})
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 0
    # a newcomer is never shown mentions from before the join, even with the whole history shared
    say(c, "owner", g["id"], "@Ann 加人之前", mention_ids=[U["ann"]["id"]])
    ho = sign_in(c, "owner")
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["cid"]["id"]], "history_from": "all"})
    joined = conv_of(c, "cid", g["id"])
    assert joined["unread"] == 0 and joined["unread_mentions"] == 0
    say(c, "ben", g["id"], "@Cid 欢迎", mention_ids=[U["cid"]["id"]])
    assert conv_of(c, "cid", g["id"])["unread_mentions"] == 1
    # a direct message counts mentions too (the field is on every conversation)
    hb = sign_in(c, "ben")
    dm = c.post(f"{M}/dm", headers=hb, json={"organization_id": c.oid, "user_id": U["cid"]["id"]}).json()
    assert dm["unread_mentions"] == 0
    # a non-member learns nothing
    sign_in(c, "outsider")
    assert c.get(f"{M}/conversations/{g['id']}").status_code == 404


# ---------------------------------------------------------------------------- 2. organization scope filter
def test_the_organization_filter_is_server_side_and_a_foreign_organization_is_simply_empty(org):
    c = org
    U = c.users
    from synwevo_core.organizations import Organizations
    other = Organizations(c.ident.store, c.ident).create(U["outsider"]["id"], "Second Org", "Second Team", "fixture-idem-second-org-0001")["organization"]["id"]
    c.ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                          (other, U["ann"]["id"], U["outsider"]["id"]))
    ha = sign_in(c, "ann")
    in_first = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    in_second = c.post(f"{M}/dm", headers=ha, json={"organization_id": other, "user_id": U["outsider"]["id"]}).json()
    assert in_first["organization_id"] == c.oid and in_second["organization_id"] == other
    orgs = {o["id"] for o in c.get(f"{M}/organizations").json()["items"]}
    assert orgs == {c.oid, other}
    assert {x["id"] for x in listed(c, "ann")} == {in_first["id"], in_second["id"]}
    assert [x["id"] for x in listed(c, "ann", organization_id=c.oid)] == [in_first["id"]]
    assert [x["id"] for x in listed(c, "ann", organization_id=other)] == [in_second["id"]]
    # someone who is not in the second organization gets an empty list — never 403, never a hint
    assert listed(c, "ben", organization_id=other) == []
    assert listed(c, "ben", organization_id="org_doesnotexist") == []
    assert [x["id"] for x in listed(c, "ben", organization_id=c.oid)] == [in_first["id"]]
    # leaving the organization empties the filter at once (the membership is re-checked per read)
    c.ident.store.execute("DELETE FROM organization_memberships WHERE organization_id=? AND user_id=?", (other, U["ann"]["id"]))
    assert listed(c, "ann", organization_id=other) == []
    assert [x["id"] for x in listed(c, "ann")] == [in_first["id"]]
    # an empty value is "every organization", as before
    assert [x["id"] for x in listed(c, "ann", organization_id="")] == [in_first["id"]]


# ---------------------------------------------------------------------------- 3. group avatar
def test_a_group_avatar_follows_the_platform_rules_and_is_served_to_current_members_only(org):
    c = org
    U = c.users
    g = group(c, "owner", "头像群", ["ann", "ben"])
    assert g["avatar"] is None
    image = png(4, 3, meta=_chunk(b"tEXt", b"Comment\x00private metadata"))
    # members who are not managers, outsiders and direct messages cannot set one
    ha = sign_in(c, "ann")
    assert c.put(f"{M}/conversations/{g['id']}/avatar", headers=ha, json={"image_base64": b64(image)}).status_code == 403
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    r = c.put(f"{M}/conversations/{dm['id']}/avatar", headers=ha, json={"image_base64": b64(image)})
    assert r.status_code == 409 and r.json()["error"] == "not_a_group"
    hx = sign_in(c, "outsider")
    assert c.put(f"{M}/conversations/{g['id']}/avatar", headers=hx, json={"image_base64": "not base64"}).status_code == 404, "membership before the bytes"
    # the platform's rules: not a PNG, too large, too wide, animated → 400 bad_avatar; extra keys → 400
    ho = sign_in(c, "owner")
    for bad in (b'<svg onload="alert(1)"/>', b"\xff\xd8\xff\xe0" + b"\x00" * 64, png(513, 1), png(1, 1, meta=_chunk(b"acTL", b"animation")), png() + b"trailing"):
        r = c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": b64(bad)})
        assert r.status_code == 400 and r.json()["error"] == "bad_avatar", bad[:12]
    assert c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": b64(image), "name": "x"}).status_code == 400
    assert c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": 3}).status_code == 400
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM conversation_avatars")["n"] == 0
    # the owner sets it: the stored image is rebuilt without the metadata, and a system row says so
    r = c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": b64(image)})
    assert r.status_code == 200, r.text
    assert r.json()["avatar"] and r.json()["avatar"]["updated_at"] > 0
    stored = bytes(c.ident.store.one("SELECT image FROM conversation_avatars WHERE conversation_id=?", (g["id"],))["image"])
    assert stored.startswith(b"\x89PNG") and b"private metadata" not in stored and b"tEXt" not in stored
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()["items"]
    assert hist[-1]["kind"] == "system" and hist[-1]["system"] == {"event": "avatar", "cleared": False, "actor": U["owner"]["id"]}
    # the other members see it in the list and fetch it with private, sniff-proof headers
    row = next(x for x in listed(c, "ben") if x["id"] == g["id"])
    assert row["avatar"] == r.json()["avatar"]
    got = c.get(f"{M}/conversations/{g['id']}/avatar")
    assert got.status_code == 200 and got.content == stored and got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"] == "private, no-cache" and got.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in got.headers["content-security-policy"] and got.headers["etag"]
    again = c.get(f"{M}/conversations/{g['id']}/avatar", headers={"If-None-Match": got.headers["etag"]})
    assert again.status_code == 304 and not again.content
    # an admin (not only the owner) may change it; a DM has none to serve
    ho = sign_in(c, "owner")
    c.put(f"{M}/conversations/{g['id']}/members/{U['ann']['id']}/role", headers=ho, json={"role": "admin"})
    ha = sign_in(c, "ann")
    assert c.put(f"{M}/conversations/{g['id']}/avatar", headers=ha, json={"image_base64": b64(png(8, 8))}).status_code == 200
    assert c.get(f"{M}/conversations/{dm['id']}/avatar").status_code == 404
    # outsiders and removed members get 404 — the same answer as for a conversation that does not exist
    sign_in(c, "outsider")
    assert c.get(f"{M}/conversations/{g['id']}/avatar").status_code == 404
    ho = sign_in(c, "owner")
    c.delete(f"{M}/conversations/{g['id']}/members/{U['ben']['id']}", headers=ho)
    sign_in(c, "ben")
    assert c.get(f"{M}/conversations/{g['id']}/avatar").status_code == 404
    # removal leaves a system row; removing again is 404; an archived group is read-only
    ho = sign_in(c, "owner")
    r = c.delete(f"{M}/conversations/{g['id']}/avatar", headers=ho)
    assert r.status_code == 200 and r.json()["avatar"] is None
    assert c.get(f"{M}/conversations/{g['id']}/messages").json()["items"][-1]["system"]["event"] == "avatar"
    assert c.get(f"{M}/conversations/{g['id']}/messages").json()["items"][-1]["system"]["cleared"] is True
    assert c.delete(f"{M}/conversations/{g['id']}/avatar", headers=ho).status_code == 404
    assert c.get(f"{M}/conversations/{g['id']}/avatar").status_code == 404
    c.post(f"{M}/conversations/{g['id']}/archive", headers=ho, json={"archived": True})
    r = c.put(f"{M}/conversations/{g['id']}/avatar", headers=ho, json={"image_base64": b64(png())})
    assert r.status_code == 409 and r.json()["error"] == "archived"
    caps = c.get(f"{M}/capabilities").json()
    assert caps["group_avatar"] == {"types": ["image/png"], "max_bytes": 256 * 1024, "max_side": 512} and caps["blocks"] is True


# ---------------------------------------------------------------------------- 4. blocking
def test_blocking_is_only_between_organization_members_and_is_the_blockers_own_list(org):
    c = org
    U = c.users
    ha = sign_in(c, "ann")
    r = c.put(f"{M}/blocks/{U['ann']['id']}", headers=ha)
    assert r.status_code == 400 and r.json()["error"] == "invalid_field"
    from synwevo_core.apps import DOCS_APP_ID
    app_uid = c.ident.store.one("SELECT user_id FROM app_accounts WHERE app=?", (DOCS_APP_ID,))["user_id"]
    r = c.put(f"{M}/blocks/{app_uid}", headers=ha)
    assert r.status_code == 400 and r.json()["error"] == "invalid_field"
    for stranger in (U["outsider"]["id"], "u_nobody"):
        r = c.put(f"{M}/blocks/{stranger}", headers=ha)
        assert r.status_code == 404 and r.json()["error"] == "no_user"
    r = c.put(f"{M}/blocks/{U['ben']['id']}", headers=ha)
    assert r.status_code == 200 and r.json()["blocked"] is True and r.json()["name"] == "Ben Member"
    assert c.put(f"{M}/blocks/{U['ben']['id']}", headers=ha).status_code == 200, "idempotent"
    assert [x["user_id"] for x in c.get(f"{M}/blocks").json()["items"]] == [U["ben"]["id"]]
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM message_blocks")["n"] == 1
    # the blocked person's own list is theirs only: it does not show who blocked them
    sign_in(c, "ben")
    assert c.get(f"{M}/blocks").json()["items"] == []
    # unblocking is idempotent too
    ha = sign_in(c, "ann")
    assert c.delete(f"{M}/blocks/{U['ben']['id']}", headers=ha).json() == {"user_id": U["ben"]["id"], "blocked": False}
    assert c.delete(f"{M}/blocks/{U['ben']['id']}", headers=ha).status_code == 200
    assert c.get(f"{M}/blocks").json()["items"] == []
    c.cookies.clear()
    assert c.get(f"{M}/blocks").status_code == 401


def test_a_block_stops_direct_messages_both_ways_with_the_right_words_and_unblocking_restores_them(org):
    c = org
    U = c.users
    ha = sign_in(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    before = say(c, "ben", dm["id"], "屏蔽之前的一句")
    mine = say(c, "ann", dm["id"], "Ann 自己的一句")
    hub = c.api.state.message_hub
    ring_before = hub.current_id()
    ha = sign_in(c, "ann")
    assert c.put(f"{M}/blocks/{U['ben']['id']}", headers=ha).status_code == 200
    # the blocker's own devices hear about the DM; the blocked person hears nothing
    after = [(set(users), ev) for eid, users, ev in list(hub._ring) if eid > ring_before]
    assert any(U["ann"]["id"] in users and ev.get("conversation_id") == dm["id"] for users, ev in after)
    assert not any(U["ben"]["id"] in users for users, _ in after)
    # the blocker is told plainly, with the way back
    r = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "发不出去"})
    assert r.status_code == 403 and r.json()["error"] == "blocked" and r.json()["message"] == "你已屏蔽对方，解除后才能发送"
    assert r.json()["detail"] == {"by_me": True}
    assert conv_of(c, "ann", dm["id"])["peer"]["blocked_by_me"] is True
    # the blocked person gets the neutral sentence and nothing that names the block
    hb = sign_in(c, "ben")
    r = c.post(f"{M}/conversations/{dm['id']}/messages", headers=hb, json={"body": "也发不出去"})
    assert r.status_code == 403 and r.json()["error"] == "blocked" and r.json()["message"] == "暂时无法向对方发送消息"
    assert not r.json().get("detail")
    seen_by_ben = conv_of(c, "ben", dm["id"])
    assert seen_by_ben["peer"]["blocked_by_me"] is False and "blocked" not in {k for k in seen_by_ben if k != "peer"}
    # the history stays readable for both; nothing was added
    assert [m["id"] for m in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"]] == [before["id"], mine["id"]]
    # every other way of putting something in front of the other side is closed too
    hb = sign_in(c, "ben")
    assert c.put(f"{M}/messages/{mine['id']}/reactions", headers=hb, json={"emoji": "👍"}).status_code == 403
    assert c.patch(f"{M}/messages/{before['id']}", headers=hb, json={"body": "改一下"}).status_code == 403
    up = c.post(f"{M}/conversations/{dm['id']}/attachments", headers=hb, files={"file": ("a.txt", b"hello", "text/plain")})
    assert up.status_code == 403 and up.json()["error"] == "blocked"
    g = group(c, "ben", "Ben 的群", ["cid"])
    groupmsg = say(c, "ben", g["id"], "群里的一句")
    hb = sign_in(c, "ben")
    r = c.post(f"{M}/messages/{groupmsg['id']}/forward", headers=hb, json={"conversation_ids": [dm["id"]]})
    assert r.status_code == 403 and r.json()["message"] == "暂时无法向对方发送消息"
    # neither side can open a new direct message in another organization they share, either
    from synwevo_core.organizations import Organizations
    other = Organizations(c.ident.store, c.ident).create(U["cid"]["id"], "Third Org", "T", "fixture-idem-third-org-0001")["organization"]["id"]
    for k in ("ann", "ben"):
        c.ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                              (other, U[k]["id"], U["cid"]["id"]))
    hb = sign_in(c, "ben")
    r = c.post(f"{M}/dm", headers=hb, json={"organization_id": other, "user_id": U["ann"]["id"]})
    assert r.status_code == 403 and r.json()["message"] == "暂时无法向对方发送消息"
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/dm", headers=ha, json={"organization_id": other, "user_id": U["ben"]["id"]})
    assert r.status_code == 403 and r.json()["message"] == "你已屏蔽对方，解除后才能发送"
    # the existing one still opens (its history is theirs to read)
    assert c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()["id"] == dm["id"]
    # taking one's own reaction back and one's own recall are not contact
    assert c.post(f"{M}/messages/{mine['id']}/recall", headers=ha).status_code == 200
    # unblock: both directions work again, and the new DM can be opened
    assert c.delete(f"{M}/blocks/{U['ben']['id']}", headers=ha).status_code == 200
    assert conv_of(c, "ann", dm["id"])["peer"]["blocked_by_me"] is False
    say(c, "ann", dm["id"], "解除后可以发")
    say(c, "ben", dm["id"], "我也可以")
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/dm", headers=hb, json={"organization_id": other, "user_id": U["ann"]["id"]}).status_code == 201
    # a block by the other side works the same way round
    hb = sign_in(c, "ben")
    c.put(f"{M}/blocks/{U['ann']['id']}", headers=hb)
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "被屏蔽了"})
    assert r.status_code == 403 and r.json()["message"] == "暂时无法向对方发送消息"


def test_a_block_hides_presence_typing_and_receipts_between_the_two_and_only_them(org):
    c = org
    U = c.users
    g = group(c, "owner", "三人群", ["ann", "ben"])
    ha = sign_in(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    for k in ("ann", "ben", "owner"):
        h = sign_in(c, k)
        c.put(f"{M}/settings", headers=h, json={"read_receipts": True})
        c.post(f"{M}/presence", headers=h, json={})
    say(c, "owner", g["id"], "大家好")
    for k in ("ann", "ben"):
        c.post(f"{M}/conversations/{g['id']}/read", headers=sign_in(c, k), json={"seq": 10 ** 6})
    pres = lambda who, cid: {m["user_id"]: m["presence"] for m in conv_of(c, who, cid)["members"]}  # noqa: E731
    assert pres("ann", g["id"])[U["ben"]["id"]] == "online"
    # capture the ephemeral presence events the block produces
    hub = c.api.state.message_hub
    events: list = []
    original = hub.publish

    def spy(users, event, **kw):
        events.append((list(users), event, kw))
        return original(users, event, **kw)
    hub.publish = spy
    try:
        ha = sign_in(c, "ann")
        c.put(f"{M}/blocks/{U['ben']['id']}", headers=ha)
    finally:
        hub.publish = original
    nulls = [(u, e["user_id"]) for u, e, kw in events if e["type"] == "presence" and e["state"] is None and kw.get("ephemeral")]
    assert ([U["ann"]["id"]], U["ben"]["id"]) in nulls and ([U["ben"]["id"]], U["ann"]["id"]) in nulls
    # presence: neither sees the other, anywhere; a third member still sees both
    assert pres("ann", g["id"])[U["ben"]["id"]] is None and conv_of(c, "ann", dm["id"])["peer"]["presence"] is None
    assert pres("ben", g["id"])[U["ann"]["id"]] is None and conv_of(c, "ben", dm["id"])["peer"]["presence"] is None
    assert pres("owner", g["id"])[U["ann"]["id"]] == "online" and pres("owner", g["id"])[U["ben"]["id"]] == "online"
    sign_in(c, "ben")
    assert c.get(f"{M}/presence", params={"user_ids": U["ann"]["id"]}).json()["presence"] == {U["ann"]["id"]: None}
    # typing: the group hint goes to the owner only; the DM hint goes to nobody
    svc = c.api.state.messaging
    assert svc.typing_targets(g["id"], U["ann"]["id"]) == [U["owner"]["id"]]
    assert svc.typing_targets(g["id"], U["ben"]["id"]) == [U["owner"]["id"]]
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=hb, json={}).json()["sent"] is False
    # receipts: the two are left out of each other's read_by; the owner still sees both
    rb = lambda who, cid: {x["user_id"] for x in (conv_of(c, who, cid)["read_by"] or [])}  # noqa: E731
    assert rb("ann", g["id"]) == {U["owner"]["id"]} and rb("ben", g["id"]) == {U["owner"]["id"]}
    assert rb("owner", g["id"]) == {U["ann"]["id"], U["ben"]["id"]}
    sign_in(c, "ann")
    assert {x["user_id"] for x in c.get(f"{M}/conversations/{g['id']}/messages").json()["read_by"]} == {U["owner"]["id"]}
    # unblocking brings them back
    ha = sign_in(c, "ann")
    c.delete(f"{M}/blocks/{U['ben']['id']}", headers=ha)
    assert pres("ann", g["id"])[U["ben"]["id"]] == "online" and rb("ben", g["id"]) == {U["owner"]["id"], U["ann"]["id"]}


def test_in_a_shared_group_a_blocked_persons_messages_stay_visible_but_never_reach_the_blockers_inbox(org):
    c = org
    U = c.users
    g = group(c, "owner", "共享群", ["ann", "ben", "cid"])
    ha = sign_in(c, "ann")
    c.put(f"{M}/conversations/{g['id']}/preferences", headers=ha, json={"notify": "all"})
    c.put(f"{M}/blocks/{U['ben']['id']}", headers=ha)
    c.post(f"{M}/conversations/{g['id']}/read", headers=ha, json={"seq": 10 ** 6})      # the creation line is read
    before_ann, before_cid = inbox_kinds(c, "ann"), inbox_kinds(c, "cid")
    m = say(c, "ben", g["id"], "@Ann @Cid 请看", mention_ids=[U["ann"]["id"], U["cid"]["id"]])
    # visible: in the history, with the mention recorded as sent
    sign_in(c, "ann")
    items = c.get(f"{M}/conversations/{g['id']}/messages").json()["items"]
    assert items[-1]["id"] == m["id"] and items[-1]["body"] == "@Ann @Cid 请看" and U["ann"]["id"] in items[-1]["mentions"]
    one = conv_of(c, "ann", g["id"])
    assert one["unread"] >= 1 and one["unread_mentions"] == 0, "visible and unread, but no @ mark"
    # no inbox item for the blocker; the other person mentioned gets theirs as always
    assert inbox_kinds(c, "ann") == before_ann
    assert inbox_kinds(c, "cid")[len(before_cid):] == ["conversation.mention"]
    # the blocked person adding the blocker to a group, or writing its announcement, does not reach the inbox either
    hb = sign_in(c, "ben")
    g2 = c.post(f"{M}/groups", headers=hb, json={"organization_id": c.oid, "name": "Ben 建的群", "member_ids": [U["ann"]["id"], U["cid"]["id"]]}).json()
    c.put(f"{M}/conversations/{g2['id']}/announcement", headers=hb, json={"text": "公告"})
    assert inbox_kinds(c, "ann") == before_ann
    assert "conversation.added" in inbox_kinds(c, "cid") and "conversation.announcement" in inbox_kinds(c, "cid")
    assert any(x["id"] == g2["id"] for x in listed(c, "ann")), "the group is there all the same"
    # moderation is unaffected: the blocker can still report the message
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/messages/{m['id']}/report", headers=ha, json={"reason": "harassment"})
    assert r.status_code == 201, r.text
    # the send-time re-check for a digest does not count the blocked person's messages
    n = c.api.state.notifier
    state = n.conversation_unread(g["id"], U["ann"]["id"])
    assert state["unread"] == 0 and state["latest"] is None
    say(c, "cid", g["id"], "Cid 的消息")
    state = n.conversation_unread(g["id"], U["ann"]["id"])
    assert state["unread"] == 1 and state["latest"]["body"] == "Cid 的消息"
    # after unblocking, the blocked person's mentions count and notify again
    ha = sign_in(c, "ann")
    c.delete(f"{M}/blocks/{U['ben']['id']}", headers=ha)
    assert conv_of(c, "ann", g["id"])["unread_mentions"] == 1
    say(c, "ben", g["id"], "@Ann 再看", mention_ids=[U["ann"]["id"]])
    assert inbox_kinds(c, "ann")[-1] == "conversation.mention"


def test_a_group_made_from_a_direct_message_starts_with_its_own_history(org):
    """DM → new group (shell only): a direct message never takes members, and the new group shows
    nothing of the private conversation — `history_from` stays the join."""
    c = org
    U = c.users
    ha = sign_in(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    say(c, "ann", dm["id"], "私聊里的话")
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/conversations/{dm['id']}/members", headers=ha, json={"user_ids": [U["cid"]["id"]]})
    assert r.status_code == 409 and r.json()["error"] == "not_a_group"
    g = c.post(f"{M}/groups", headers=ha, json={"organization_id": c.oid, "name": "由私聊建的群", "member_ids": [U["ben"]["id"], U["cid"]["id"]]}).json()
    for k in ("ann", "ben", "cid"):
        sign_in(c, k)
        items = c.get(f"{M}/conversations/{g['id']}/messages").json()["items"]
        assert [m["kind"] for m in items] == ["system"] and items[0]["system"]["event"] == "created"
        assert not any("私聊里的话" in (m["body"] or "") for m in items)
    assert conv_of(c, "cid", g["id"])["me"]["history_from"] == "join"
    sign_in(c, "cid")
    assert c.get(f"{M}/conversations/{dm['id']}/messages").status_code == 404
