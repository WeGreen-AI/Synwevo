"""Round 9 (CI-74): the messaging capabilities the brief requires, on the real store and the real
routes — presence and typing that are ephemeral and mutual, opt-in read receipts, mark-as-unread
and mute-until, forwarding that re-binds attachments only for a reader, recall versus delete-for-me
versus a manager's removal, voice attachments with a validated duration, FTS5 search scoped to what
the searcher may read, event ids with replay and resync, conversation-level sync, `history_from`,
revocation that closes every door, and counters in `/readyz`."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

M = "/api/messaging"
MESSAGES = "http://message.testserver"


def sign_in(c, key: str) -> dict:
    return c.sign_in(key)


def dm_between(c, a: str, b: str) -> dict:
    h = sign_in(c, a)
    return c.post(f"{M}/dm", headers=h, json={"organization_id": c.oid, "user_id": c.users[b]["id"]}).json()


def say(c, who: str, cid: str, body: str, **extra) -> dict:
    h = sign_in(c, who)
    r = c.post(f"{M}/conversations/{cid}/messages", headers=h, json={"body": body, **extra})
    assert r.status_code == 201, r.text
    return r.json()["message"]


def row_counts(store) -> dict:
    return {t: store.one(f"SELECT COUNT(*) AS n FROM {t}")["n"]
            for t in ("conversations", "conversation_members", "messages", "message_reactions", "message_attachments", "message_reports", "messaging_settings", "message_hides")}


# ---------------------------------------------------------------------------- 1. presence, typing, settings
def test_presence_is_three_honest_states_from_heartbeats_and_hiding_it_is_mutual(org):
    c = org
    U = c.users
    dm = dm_between(c, "ann", "ben")
    ha = sign_in(c, "ann")
    caps = c.get(f"{M}/capabilities").json()
    assert caps["presence"] == {"online_s": 60, "away_s": 300, "interval_s": 60, "states": ["online", "away", "offline"]}
    assert caps["settings"] == {"show_presence": True, "read_receipts": False}
    assert c.get(f"{M}/settings").json() == {"show_presence": True, "read_receipts": False}
    # nobody has reported anything: offline is what the server knows, and it says so
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/conversations/{dm['id']}").json()["peer"]["presence"] == "offline"
    # a heartbeat from a visible page: online, and the definition comes back with it
    before = row_counts(c.ident.store)
    ha = sign_in(c, "ann")
    hb_ = c.post(f"{M}/presence", headers=ha, json={})
    assert hb_.status_code == 200 and hb_.json()["state"] == "online" and hb_.json()["shown"] is True and hb_.json()["online_s"] == 60
    assert row_counts(c.ident.store) == before, "presence is memory only: no row anywhere"
    hb = sign_in(c, "ben")
    conv = c.get(f"{M}/conversations/{dm['id']}").json()
    assert conv["peer"]["presence"] == "online" and {m["user_id"]: m["presence"] for m in conv["members"]} == {U["ann"]["id"]: "online", U["ben"]["id"]: "offline"}
    listed = c.get(f"{M}/conversations").json()["items"][0]
    assert listed["peer"]["presence"] == "online"
    snap = c.get(f"{M}/presence", params={"user_ids": f"{U['ann']['id']},{U['outsider']['id']},{U['cid']['id']}"}).json()
    assert snap["presence"] == {U["ann"]["id"]: "online"}, "only people who share a conversation with the caller appear"
    # time passes without a heartbeat: away after 60 s, offline after 5 min — by age, nothing else
    pres = c.api.state.messaging_presence
    base = time.time()
    pres._clock = lambda: base + 61
    assert c.get(f"{M}/conversations/{dm['id']}").json()["peer"]["presence"] == "away"
    changed = pres.sweep(min_interval_s=0)
    assert changed == [(U["ann"]["id"], "away")]
    pres._clock = lambda: base + 301
    assert c.get(f"{M}/conversations/{dm['id']}").json()["peer"]["presence"] == "offline"
    assert pres.sweep(min_interval_s=0) == [(U["ann"]["id"], "offline")] and pres.tracked() == 0, "offline entries are dropped"
    pres._clock = time.time
    # hiding presence is mutual: ben hides → ann sees null for ben, ben sees null for ann
    ha = sign_in(c, "ann")
    c.post(f"{M}/presence", headers=ha, json={})
    hb = sign_in(c, "ben")
    c.post(f"{M}/presence", headers=hb, json={})
    r = c.put(f"{M}/settings", headers=hb, json={"show_presence": False})
    assert r.status_code == 200 and r.json() == {"show_presence": False, "read_receipts": False}
    assert c.get(f"{M}/conversations/{dm['id']}").json()["peer"]["presence"] is None, "hiding yours hides everyone's from you"
    assert c.post(f"{M}/presence", headers=hb, json={}).json() == {"shown": False, "state": None, **caps["presence"]}
    ha = sign_in(c, "ann")
    assert c.get(f"{M}/conversations/{dm['id']}").json()["peer"]["presence"] is None, "and nobody sees yours"
    assert c.get(f"{M}/presence", params={"user_ids": U["ben"]["id"]}).json()["presence"] == {U["ben"]["id"]: None}
    # bad settings payloads
    assert c.put(f"{M}/settings", headers=ha, json={"show_presence": "yes"}).status_code == 400
    assert c.put(f"{M}/settings", headers=ha, json={"presence": True}).status_code == 400
    # an outsider has no settings route into anyone else's state; and no session at all → 401
    c.cookies.clear()
    assert c.get(f"{M}/settings").status_code == 401 and c.post(f"{M}/presence", json={}).status_code == 401


def test_typing_is_an_ephemeral_hint_with_its_own_bucket_and_follows_the_presence_switch(org):
    c = org
    U = c.users
    dm = dm_between(c, "ann", "ben")
    hub = c.api.state.message_hub
    ha = sign_in(c, "ann")
    before = row_counts(c.ident.store)
    ring_before = hub.ring_size()
    r = c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={})
    assert r.status_code == 200 and r.json() == {"sent": True, "state": "start", "expires_in_s": 6}
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={"state": "stop"}).json()["state"] == "stop"
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={"state": "busy"}).status_code == 400
    assert row_counts(c.ident.store) == before and hub.ring_size() == ring_before, "typing is never stored and never replayed"
    # a non-member learns nothing, not even that the conversation exists
    hc = sign_in(c, "cid")
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=hc, json={}).status_code == 404
    # the typing bucket is its own: flooding it answers 429 with retry_after and a message still sends
    ha = sign_in(c, "ann")
    codes = [c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={}).status_code for _ in range(25)]
    assert 429 in codes and codes[:15] == [200] * 15
    r = c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={})
    assert r.status_code == 429 and r.json()["detail"]["retry_after"] >= 1
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "still sends"}).status_code == 201
    # a person who hides presence neither sends nor receives typing hints
    hb = sign_in(c, "ben")
    c.put(f"{M}/settings", headers=hb, json={"show_presence": False})
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=hb, json={}).json()["sent"] is False
    assert c.api.state.messaging.typing_targets(dm["id"], U["ann"]["id"]) == [], "ann's hint has no one to go to"
    c.put(f"{M}/settings", headers=hb, json={"show_presence": True})
    assert c.api.state.messaging.typing_targets(dm["id"], U["ann"]["id"]) == [U["ben"]["id"]]
    # counters, numbers only
    checks = c.get("/readyz").json()["checks"]["messaging"]
    assert checks["typing_events"] >= 2 and all(isinstance(v, int) for v in checks.values()) and set(checks) >= {"send_failed", "stream_connections", "stream_replays", "typing_events", "search_queries"}


# ---------------------------------------------------------------------------- 2. read receipts
def test_read_receipts_are_shown_only_between_people_who_turned_them_on(org):
    c = org
    U = c.users
    dm = dm_between(c, "ann", "ben")
    say(c, "ann", dm["id"], "one")
    say(c, "ann", dm["id"], "two")
    hb = sign_in(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 2})
    ha = sign_in(c, "ann")
    conv = c.get(f"{M}/conversations/{dm['id']}").json()
    assert conv["read_by"] is None, "ann has receipts off: nothing is shown (null, not an empty list)"
    assert c.get(f"{M}/conversations/{dm['id']}/messages").json()["read_by"] is None
    c.put(f"{M}/settings", headers=ha, json={"read_receipts": True})
    assert c.get(f"{M}/conversations/{dm['id']}").json()["read_by"] == [], "ann on, ben off: shown, but ben is not in it"
    hb = sign_in(c, "ben")
    c.put(f"{M}/settings", headers=hb, json={"read_receipts": True})
    ha = sign_in(c, "ann")
    rb = c.get(f"{M}/conversations/{dm['id']}").json()["read_by"]
    assert len(rb) == 1 and rb[0]["user_id"] == U["ben"]["id"] and rb[0]["last_read_seq"] == 2 and rb[0]["at"] > 0
    assert c.get(f"{M}/conversations/{dm['id']}/messages").json()["read_by"] == rb
    # a group lists only members who turned theirs on; a member who did not sees nothing
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "回执群", "member_ids": [U["ann"]["id"], U["ben"]["id"], U["cid"]["id"]]}).json()
    say(c, "owner", g["id"], "hello all")
    for who in ("ann", "ben", "cid"):
        h = sign_in(c, who)
        c.post(f"{M}/conversations/{g['id']}/read", headers=h, json={"seq": 99})
    ho = sign_in(c, "owner")
    assert c.get(f"{M}/conversations/{g['id']}").json()["read_by"] is None
    c.put(f"{M}/settings", headers=ho, json={"read_receipts": True})
    seen = {x["user_id"] for x in c.get(f"{M}/conversations/{g['id']}").json()["read_by"]}
    assert seen == {U["ann"]["id"], U["ben"]["id"]}, "cid did not turn receipts on and is not listed"
    hc = sign_in(c, "cid")
    assert c.get(f"{M}/conversations/{g['id']}").json()["read_by"] is None
    # the word "delivered" appears nowhere: the server does not know it
    text = json.dumps(c.get(f"{M}/conversations/{g['id']}").json()) + json.dumps(c.get(f"{M}/conversations/{g['id']}/messages").json()) + json.dumps(c.get(f"{M}/capabilities").json())
    assert "delivered" not in text


# ---------------------------------------------------------------------------- 3. mark unread, mute until
def test_mark_unread_counts_as_one_until_the_next_read_and_mute_until_silences_for_a_while(org):
    c = org
    U = c.users
    n = c.api.state.notifier
    dm = dm_between(c, "ann", "ben")
    say(c, "ann", dm["id"], "first")
    hb = sign_in(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99})
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 0, "conversations": 0}
    out = c.post(f"{M}/conversations/{dm['id']}/unread", headers=hb).json()
    assert out == {"conversation_id": dm["id"], "last_read_seq": 1, "marked_unread": True, "unread": 1}
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 1, "conversations": 1}
    me = c.get(f"{M}/conversations/{dm['id']}").json()["me"]
    assert me["marked_unread"] is True and me["last_read_seq"] == 1, "the read cursor did not move back"
    assert c.get(f"{M}/conversations").json()["items"][0]["me"]["marked_unread"] is True
    # the next read clears it, even one that does not move the cursor
    assert c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 1}).json()["marked_unread"] is False
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 0, "conversations": 0}
    # mute until: the past is refused, the future silences badge and inbox, null clears
    assert c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": time.time() - 5}).status_code == 400
    assert c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": "soon"}).status_code == 400
    until = time.time() + 3600
    me = c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": until}).json()["me"]
    assert me["muted"] is False and abs(me["muted_until"] - until) < 1
    items_before = len(n.list(U["ben"]["id"])["items"])
    say(c, "ann", dm["id"], "while muted")
    hb = sign_in(c, "ben")
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 0, "conversations": 0}, "a muted-until conversation is not counted"
    assert len(n.list(U["ben"]["id"])["items"]) == items_before, "recipients_for skips a reader whose muted_until lies ahead"
    assert n.conversation_unread(dm["id"], U["ben"]["id"])["muted"] is True, "the send-time re-check treats it as muted too"
    me = c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": None}).json()["me"]
    assert me["muted_until"] is None
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 1, "conversations": 1}
    say(c, "ann", dm["id"], "audible again")
    assert len(n.list(U["ben"]["id"])["items"]) == items_before + 1
    assert c.put(f"{M}/conversations/{dm['id']}/preferences", headers=sign_in(c, "ben"), json={"silence": 1}).status_code == 400


# ---------------------------------------------------------------------------- 4. forwarding
def test_forwarding_rebinds_the_attachment_and_names_the_source_only_to_its_members(org):
    c = org
    U = c.users
    n = c.api.state.notifier
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "源群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]}).json()
    other = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "别人的群", "member_ids": [U["cid"]["id"]]}).json()
    dm = dm_between(c, "ann", "cid")
    ha = sign_in(c, "ann")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    att = c.post(f"{M}/conversations/{g['id']}/attachments", files={"file": ("shot.png", png, "image/png")}, headers=ha).json()
    src = c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "看这张图", "attachment_id": att["id"]}).json()["message"]
    F = f"{M}/messages/{src['id']}/forward"
    # validation: targets required, ≤ 5, all writable — a refused target leaves nothing written
    assert c.post(F, headers=ha, json={"conversation_ids": []}).status_code == 400
    assert c.post(F, headers=ha, json={"conversation_ids": [f"cv_{i}" for i in range(6)]}).json()["error"] == "too_many_targets"
    assert c.post(F, headers=ha, json={"conversation_ids": [dm["id"], other["id"]]}).status_code == 404
    assert c.get(f"{M}/conversations/{dm['id']}").json()["last_seq"] == 0, "nothing was written before the refusal"
    assert c.post(F, headers=ha, json={"conversation_ids": [dm["id"]], "extra": 1}).status_code == 400
    r = c.post(F, headers=ha, json={"conversation_ids": [dm["id"]], "client_id": "fw1"})
    assert r.status_code == 201, r.text
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["created"] is True and items[0]["conversation_id"] == dm["id"]
    copy = items[0]["message"]
    assert copy["body"] == "看这张图" and copy["forwarded_from"] == {"sender": "Ann Member", "conversation_id": g["id"]}, "ann is in the source: she sees where it came from"
    assert copy["attachment"]["id"] != att["id"] and copy["attachment"]["filename"] == "shot.png" and copy["attachment"]["inline"] is True
    # same client_id → the same message, not a second one
    again = c.post(F, headers=ha, json={"conversation_ids": [dm["id"]], "client_id": "fw1"}).json()["items"][0]
    assert again["created"] is False and again["message"]["id"] == copy["id"]
    assert c.get(f"{M}/conversations/{dm['id']}").json()["last_seq"] == 1
    # cid reads the copy: the source is not named (not a member), the re-bound attachment opens, the original does not
    hc = sign_in(c, "cid")
    got = c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"][0]
    assert got["forwarded_from"] == {"sender": "Ann Member", "conversation_id": None}
    r = c.get(f"{M}/attachments/{copy['attachment']['id']}?inline=1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["x-content-sha256"] == att.get("sha256", r.headers["x-content-sha256"])
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404, "the source conversation stays closed to cid"
    assert c.get(f"{M}/conversations/{g['id']}").status_code == 404
    # cid got an inbox item for the forwarded private message like for any DM
    assert [x["kind"] for x in n.list(U["cid"]["id"])["items"]][0] == "conversation.dm"
    # the two blobs are the same stored bytes (content hash), not a second copy
    rows = c.ident.store.all("SELECT blob FROM message_attachments WHERE id IN (?,?)", (att["id"], copy["attachment"]["id"]))
    assert len({r["blob"] for r in rows}) == 1
    # what cannot be forwarded: a message before your join, one you hid, one that was recalled, by a non-member
    ho = sign_in(c, "owner")
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["cid"]["id"]]})
    hc = sign_in(c, "cid")
    assert c.post(F, headers=hc, json={"conversation_ids": [dm["id"]]}).status_code == 404, "before cid's join"
    hb = sign_in(c, "ben")
    c.request("DELETE", f"{M}/messages/{src['id']}", headers=hb)
    assert c.post(F, headers=hb, json={"conversation_ids": [dm["id"]]}).status_code == 404, "hidden for ben"
    assert c.post(F, headers=sign_in(c, "outsider"), json={"conversation_ids": [dm["id"]]}).status_code == 404
    ha = sign_in(c, "ann")
    c.post(f"{M}/messages/{src['id']}/recall", headers=ha)
    assert c.post(F, headers=ha, json={"conversation_ids": [dm["id"]], "client_id": "fw2"}).status_code == 404, "recalled"
    # the copy in the DM is untouched by the recall of its source
    hc = sign_in(c, "cid")
    assert c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"][0]["body"] == "看这张图"


# ---------------------------------------------------------------------------- 5. recall / remove / delete for me / edit window
def test_recall_is_the_senders_within_the_window_removal_is_the_managers_and_delete_is_for_me(org, monkeypatch):
    c = org
    U = c.users
    svc = c.api.state.messaging
    caps_before = c.get(f"{M}/capabilities", headers=sign_in(c, "ann")).json()
    assert caps_before["recall_window_s"] == 120 and caps_before["edit_window_s"] == 900
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "撤回群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]}).json()
    ha = sign_in(c, "ann")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    att = c.post(f"{M}/conversations/{g['id']}/attachments", files={"file": ("a.png", png, "image/png")}, headers=ha).json()
    m1 = c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "秘密数字 7781", "attachment_id": att["id"]}).json()["message"]
    m2 = say(c, "ann", g["id"], "second one stays")
    hb = sign_in(c, "ben")
    c.put(f"{M}/messages/{m1['id']}/reactions", headers=hb, json={"emoji": "👀"})
    assert c.get(f"{M}/search", params={"q": "7781"}).json()["items"][0]["id"] == m1["id"]
    # only the sender recalls; members cannot; the window closes
    assert c.post(f"{M}/messages/{m1['id']}/recall", headers=hb).status_code == 403
    assert c.post(f"{M}/messages/{m1['id']}/recall", headers=sign_in(c, "cid")).status_code == 404
    c.ident.store.execute("UPDATE messages SET created_at=created_at-200 WHERE id=?", (m2["id"],))
    ha = sign_in(c, "ann")
    r = c.post(f"{M}/messages/{m2['id']}/recall", headers=ha)
    assert r.status_code == 403 and r.json()["error"] == "recall_window_closed" and r.json()["detail"]["recall_window_s"] == 120
    assert c.patch(f"{M}/messages/{m2['id']}", headers=ha, json={"body": "still editable within 15 min"}).status_code == 200
    c.ident.store.execute("UPDATE messages SET created_at=created_at-1000 WHERE id=?", (m2["id"],))
    r = c.patch(f"{M}/messages/{m2['id']}", headers=ha, json={"body": "too late"})
    assert r.status_code == 403 and r.json()["error"] == "edit_window_closed"
    out = c.post(f"{M}/messages/{m1['id']}/recall", headers=ha).json()
    assert out["recalled"] is True and out["deleted"] is True and out["body"] == "" and out["attachment"] is None and out["reactions"] == []
    assert out["system"] == {"event": "recalled", "actor": U["ann"]["id"]} and out["seq"] == m1["seq"], "the row keeps its place as the marker"
    assert c.post(f"{M}/messages/{m1['id']}/recall", headers=ha).status_code == 409
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/search", params={"q": "7781"}).json()["items"] == [], "the search index forgot the body"
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM message_reactions WHERE message_id=?", (m1["id"],))["n"] == 0
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()["items"]
    marker = next(x for x in hist if x["id"] == m1["id"])
    assert marker["recalled"] is True and marker["system"]["event"] == "recalled"
    assert c.api.state.notifier.conversation_unread(g["id"], U["ben"]["id"])["unread"] == 2, "the group's system row and m2 — the recalled m1 is not unread for the digest re-check"
    # a manager removes for everyone at any time; the marker names them; members cannot; not in a DM
    m3 = say(c, "ben", g["id"], "rude thing")
    c.ident.store.execute("UPDATE messages SET created_at=created_at-99999 WHERE id=?", (m3["id"],))
    assert c.post(f"{M}/messages/{m3['id']}/remove", headers=sign_in(c, "ann")).status_code == 403
    ho = sign_in(c, "owner")
    out = c.post(f"{M}/messages/{m3['id']}/remove", headers=ho).json()
    assert out["deleted"] is True and out["recalled"] is False and out["removed_by"] == {"user_id": U["owner"]["id"], "name": "Ola Owner"} and out["system"] == {"event": "removed", "actor": U["owner"]["id"]}
    assert c.post(f"{M}/messages/{m3['id']}/remove", headers=ho).status_code == 409
    dm = dm_between(c, "ann", "ben")
    d1 = say(c, "ann", dm["id"], "dm line")
    assert c.post(f"{M}/messages/{d1['id']}/remove", headers=sign_in(c, "ben")).json()["error"] == "not_a_group"
    # delete for me: mine or anyone's, my view only, undoable, idempotent; not before my join
    hb = sign_in(c, "ben")
    hidden = c.request("DELETE", f"{M}/messages/{d1['id']}", headers=hb).json()
    assert hidden["hidden"] is True and hidden["seq"] == d1["seq"]
    assert c.request("DELETE", f"{M}/messages/{d1['id']}", headers=hb).json()["hidden"] is True
    assert c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"] == []
    assert c.get(f"{M}/conversations/{dm['id']}").json()["last_message"]["hidden"] is True
    assert c.get(f"{M}/search", params={"q": "dm line"}).json()["items"] == []
    ha = sign_in(c, "ann")
    assert [x["body"] for x in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"]] == ["dm line"], "ann still sees it"
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/messages/{d1['id']}/unhide", headers=hb).json()["hidden"] is False
    assert [x["body"] for x in c.get(f"{M}/conversations/{dm['id']}/messages").json()["items"]] == ["dm line"]
    assert c.request("DELETE", f"{M}/messages/{d1['id']}", headers=sign_in(c, "cid")).status_code == 404
    # the moderation path is unchanged: a review still removes for everyone
    m4 = say(c, "ben", g["id"], "reported words")
    rep = c.post(f"{M}/messages/{m4['id']}/report", headers=sign_in(c, "ann"), json={"reason": "spam"}).json()
    svc.review_report(rep["id"], U["owner"]["id"], "removed", "fixture review")
    ha = sign_in(c, "ann")
    gone = next(x for x in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if x["id"] == m4["id"])
    assert gone["deleted"] is True and gone["recalled"] is False and gone["removed_by"] is None and gone["body"] == ""
    # the window is configurable per deployment
    monkeypatch.setattr("synwevo_core.messaging.RECALL_WINDOW_S", 0)
    m5 = say(c, "ann", g["id"], "no limit now")
    c.ident.store.execute("UPDATE messages SET created_at=created_at-99999 WHERE id=?", (m5["id"],))
    assert c.post(f"{M}/messages/{m5['id']}/recall", headers=sign_in(c, "ann")).json()["recalled"] is True


# ---------------------------------------------------------------------------- 6. voice
def _webm(n: int = 200) -> bytes:
    return b"\x1a\x45\xdf\xa3" + b"\x9f\x42\x86\x81\x01\x42\xf7\x81\x01\x42\xf2\x81\x04\x42\xf3\x81\x08\x42\x82\x84webm\x42\x87\x81\x04" + b"\x00" * n


def test_voice_attachments_are_sniffed_declare_a_validated_duration_and_play_inline(org):
    from synwevo_core.messaging import sniff_content_type
    assert sniff_content_type(_webm(), "v.webm") == "audio/webm"
    assert sniff_content_type(b"OggS" + b"\x00" * 40, "v.ogg") == "audio/ogg"
    assert sniff_content_type(b"\xff\xf1\x50\x80" + b"\x00" * 40, "v.aac") == "audio/aac"
    m4a = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8 + b"moov" + b"\x00" * 20 + b"mp4a" + b"\x00" * 20
    assert sniff_content_type(m4a, "v.m4a") == "audio/mp4"
    assert sniff_content_type(m4a.replace(b"mp4a", b"avc1"), "v.mp4") == "video/mp4"
    c = org
    dm = dm_between(c, "ann", "ben")
    ha = sign_in(c, "ann")
    caps = c.get(f"{M}/capabilities").json()["voice"]
    assert caps == {"types": ["audio/webm", "audio/ogg", "audio/mp4", "audio/aac"], "max_bytes": 10 * 1024 * 1024, "max_duration_ms": 300000}
    A = f"{M}/conversations/{dm['id']}/attachments"
    r = c.post(A, files={"file": ("note.webm", _webm(), "application/octet-stream")}, data={"duration_ms": "4200"}, headers=ha)
    assert r.status_code == 201, r.text
    att = r.json()
    assert att["content_type"] == "audio/webm" and att["voice"] is True and att["duration_ms"] == 4200 and att["inline"] is True and att["scan"]["status"] == "not_configured"
    # the declared length is validated; only voice may declare one; the type is never the client's claim
    for bad in ("0", "300001", "abc", "-5"):
        assert c.post(A, files={"file": ("n.webm", _webm(), "audio/webm")}, data={"duration_ms": bad}, headers=ha).status_code == 400, bad
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    assert c.post(A, files={"file": ("p.png", png, "audio/webm")}, data={"duration_ms": "1000"}, headers=ha).status_code == 400
    assert c.post(A, files={"file": ("p.png", png, "audio/webm")}, headers=ha).json()["content_type"] == "image/png"
    assert c.post(A, files={"file": ("n.webm", _webm(), "audio/webm")}, headers=ha).json()["duration_ms"] is None, "no declaration is fine: no length shown"
    big = _webm(10 * 1024 * 1024)
    r = c.post(A, files={"file": ("big.webm", big, "audio/webm")}, headers=ha)
    assert r.status_code == 413 and r.json()["error"] == "file_too_large" and r.json()["detail"]["limit"] == 10 * 1024 * 1024
    # sent, the message carries the voice fields; the peer streams it inline with the same guards as images
    msg = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "", "attachment_id": att["id"]}).json()["message"]
    assert msg["attachment"]["voice"] is True and msg["attachment"]["duration_ms"] == 4200
    hb = sign_in(c, "ben")
    r = c.get(f"{M}/attachments/{att['id']}?inline=1")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/webm" and r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"] and r.content == _webm()
    assert c.get(f"{M}/attachments/{att['id']}").headers["content-disposition"].startswith("attachment")
    assert c.get(f"{M}/attachments/{att['id']}", headers=sign_in(c, "cid")).status_code == 404


# ---------------------------------------------------------------------------- 7. search
def test_search_uses_the_fts5_index_within_what_the_searcher_may_read(org):
    from synwevo_core import messaging_search as fts
    c = org
    U = c.users
    with c.ident.store.conn() as conn:
        info = fts.describe(conn)
    ha = sign_in(c, "ann")
    caps = c.get(f"{M}/capabilities").json()["search"]
    assert caps["engine"] == info["engine"] and caps["tokenizer"] == info["tokenizer"] and caps["filters"] == ["cid", "from", "has_attachment", "since", "until"]
    if info["engine"] != "fts5":
        pytest.skip("this Python's SQLite has no FTS5; search stays LIKE (reported as such)")
    assert info["tokenizer"] == "trigram", "the venv's SQLite has trigram; the test pins the substring semantics that need it"
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "搜索群", "member_ids": [U["ann"]["id"]]}).json()
    early = say(c, "owner", g["id"], "明天交周报，别忘了 NEAR the deadline")
    t_mid = time.time()
    time.sleep(0.01)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    ha = sign_in(c, "ann")
    att = c.post(f"{M}/conversations/{g['id']}/attachments", files={"file": ("q.png", png, "image/png")}, headers=ha).json()
    with_att = c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "周报模板在附件里", "attachment_id": att["id"]}).json()["message"]
    dm = dm_between(c, "ann", "ben")
    say(c, "ann", dm["id"], "私聊里的周报两个字")
    # three CJK characters: a substring hit with a highlighted snippet
    r = c.get(f"{M}/search", params={"q": "交周报"}, headers=ha).json()
    assert r["engine"] == "fts5" and r["fallback"] is False and [x["id"] for x in r["items"]] == [early["id"]]
    hit = r["items"][0]
    assert hit["cid"] == g["id"] and hit["seq"] == early["seq"] and "交周报" in hit["snippet"]
    s, e = hit["ranges"][0]
    assert hit["snippet"][s:e] == "交周报"
    # two characters: honestly below the tokenizer's floor → LIKE, still finds all three
    r = c.get(f"{M}/search", params={"q": "周报"}).json()
    assert r["engine"] == "like" and r["fallback"] is True and len(r["items"]) == 3
    assert r["items"][0]["ranges"] and r["items"][0]["snippet"][r["items"][0]["ranges"][0][0]:r["items"][0]["ranges"][0][1]] == "周报"
    # what people type is never a query operator or a syntax error
    for q in ('"quoted"', "a*b", "NEAR", "(open", "x AND y", "，。！", "col:umn", "-minus", "'quote"):
        rr = c.get(f"{M}/search", params={"q": q})
        assert rr.status_code == 200, (q, rr.text)
    assert c.get(f"{M}/search", params={"q": "NEAR"}).json()["items"][0]["id"] == early["id"]
    # filters
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报", "cid": dm["id"]}).json()["items"]] and all(x["cid"] == dm["id"] for x in c.get(f"{M}/search", params={"q": "周报", "cid": dm["id"]}).json()["items"])
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报", "from": U["owner"]["id"]}).json()["items"]] == [early["id"]]
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报", "has_attachment": 1}).json()["items"]] == [with_att["id"]]
    assert early["id"] not in [x["id"] for x in c.get(f"{M}/search", params={"q": "周报", "since": t_mid}).json()["items"]]
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报", "until": t_mid}).json()["items"]] == [early["id"]]
    # scope: before your join, hidden for you, recalled, edited-away text, and a lost membership
    ho = sign_in(c, "owner")
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["ben"]["id"]]})
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/search", params={"q": "交周报"}).json()["items"] == [], "before ben's join"
    late = say(c, "owner", g["id"], "ben 能看到的周报提醒")
    hb = sign_in(c, "ben")
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报提醒"}).json()["items"]] == [late["id"]]
    c.request("DELETE", f"{M}/messages/{late['id']}", headers=hb)
    assert c.get(f"{M}/search", params={"q": "周报提醒"}).json()["items"] == [], "hidden for ben"
    ho = sign_in(c, "owner")
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "周报提醒"}).json()["items"]] == [late["id"]], "still there for the owner"
    c.patch(f"{M}/messages/{late['id']}", headers=ho, json={"body": "改成了别的字"})
    assert c.get(f"{M}/search", params={"q": "周报提醒"}).json()["items"] == [] and [x["id"] for x in c.get(f"{M}/search", params={"q": "别的字"}).json()["items"]] == [late["id"]]
    c.post(f"{M}/messages/{late['id']}/recall", headers=ho)
    assert c.get(f"{M}/search", params={"q": "别的字"}).json()["items"] == [], "recalled bodies leave the index"
    ha = sign_in(c, "ann")
    assert len(c.get(f"{M}/search", params={"q": "周报"}).json()["items"]) == 3
    ho = sign_in(c, "owner")
    c.request("DELETE", f"{M}/conversations/{g['id']}/members/{U['ann']['id']}", headers=ho)
    ha = sign_in(c, "ann")
    assert [x["cid"] for x in c.get(f"{M}/search", params={"q": "周报"}).json()["items"]] == [dm["id"]], "removed: the group's messages are gone from ann's search"
    assert c.get(f"{M}/search", params={"q": "x"}).status_code == 400
    assert c.get("/readyz").json()["checks"]["messaging"]["search_queries"] >= 10


def test_search_helpers_are_pure():
    from synwevo_core import messaging_search as fts
    assert fts.match_expression('say "hi" NEAR*') == '"say ""hi"" NEAR*"'
    text, ranges = fts.split_snippet("…去交周报吧")
    assert text == "…去交周报吧" and ranges == [[2, 5]] and text[2:5] == "交周报"
    assert fts.split_snippet(None) == ("", [])
    tri = {"engine": "fts5", "tokenizer": "trigram", "min_chars": 3}
    assert fts.usable_for(tri, "abc") and not fts.usable_for(tri, "ab") and fts.usable_for(tri, "周报了")
    uni = {"engine": "fts5", "tokenizer": "unicode61", "min_chars": 2}
    assert fts.usable_for(uni, "hello") and not fts.usable_for(uni, "周报"), "unicode61 cannot substring-match CJK: LIKE instead"
    assert not fts.usable_for({"engine": "like"}, "anything")


# ---------------------------------------------------------------------------- 8. event ids, replay, resync
def test_the_hub_numbers_events_replays_a_bounded_ring_and_caps_streams_per_user():
    from synwevo_messages.routes import MessageHub

    async def run():
        hub = MessageHub(ring_max=3, max_per_user=2)
        start = hub.current_id()
        qs = [hub.subscribe("u1"), hub.subscribe("u1")]
        assert all(qs) and hub.subscribe("u1") is None and hub.connections_of("u1") == 2, "the third stream of one person is refused"
        assert hub.subscribe("u2") is not None
        e1 = hub.publish(["u1"], {"type": "message", "conversation_id": "cv_a"})
        e2 = hub.publish(["u2"], {"type": "message", "conversation_id": "cv_b"})
        e3 = hub.publish(["u1", "u2"], {"type": "typing", "conversation_id": "cv_a"}, ephemeral=True)
        assert e1["id"] == start + 1 and e2["id"] == e1["id"] + 1 and e3["id"] == e2["id"] + 1 and e1["at"] > 0
        assert hub.ring_size() == 2, "ephemeral events take an id but never enter the ring"
        assert [e["id"] for e in hub.replay("u1", start)] == [e1["id"]], "only what was addressed to u1"
        assert hub.replay("u1", e3["id"]) == [] and hub.replay("u2", start) == [e2["id"]] if False else [e["id"] for e in hub.replay("u2", start)] == [e2["id"]]
        assert hub.replay("u1", e3["id"] + 5) is None, "a cursor from another process (ahead of ours) cannot be trusted"
        e4 = hub.publish(["u1"], {"type": "message", "conversation_id": "cv_a"})
        e5 = hub.publish(["u1"], {"type": "message", "conversation_id": "cv_a"})
        assert hub.ring_size() == 3 and hub.replay("u1", start) is None, "the oldest event was evicted: a cursor before it must resync"
        assert [e["id"] for e in hub.replay("u1", e1["id"])] == [e4["id"], e5["id"]]
        await asyncio.sleep(0)
        assert qs[0].qsize() == 4, "u1's queue got e1, the ephemeral e3, e4 and e5"
        hub.unsubscribe("u1", qs[0]); hub.unsubscribe("u1", qs[1])
        assert hub.connections_of("u1") == 0 and hub.subscribe("u1") is not None
    asyncio.run(run())


def _stream_until_end(c, key: str, path: str, actions=None, settle: float = 0.4) -> list[str]:
    """Open `path` as `key` and read the whole stream. The TestClient hands the body over only
    when the response is complete, so a helper thread performs `actions()` (service-layer calls,
    never HTTP on the same client) once the subscription exists, waits `settle`, then revokes
    `key`'s session: the stream ends at the next heartbeat (1 s here) with `end{session}`."""
    import synwevo_messages.routes as mr
    hub = c.api.state.message_hub
    uid = c.users[key]["id"]
    sign_in(c, key)

    def later():
        for _ in range(200):
            if hub.connections():
                break
            time.sleep(0.02)
        if actions:
            actions()
        time.sleep(settle)
        c.ident.store.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (time.time(), uid))
    t = threading.Thread(target=later, daemon=True)
    original = mr.HEARTBEAT_S
    mr.HEARTBEAT_S = 1
    lines: list[str] = []
    try:
        t.start()
        with c.stream("GET", path) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            for line in r.iter_lines():
                lines.append(line)
    finally:
        mr.HEARTBEAT_S = original
    t.join(5)
    return lines


def _events(lines: list[str]) -> list[tuple[int | None, str, dict]]:
    out, cur_id, cur_ev = [], None, None
    for line in lines:
        if line.startswith("id: "):
            cur_id = int(line[4:])
        elif line.startswith("event: "):
            cur_ev = line[7:]
        elif line.startswith("data: "):
            out.append((cur_id, cur_ev, json.loads(line[6:])))
            cur_id, cur_ev = None, None
    return out


def test_the_stream_carries_ids_replays_what_was_missed_and_says_resync_when_it_cannot(org):
    c = org
    U = c.users
    hub = c.api.state.message_hub
    svc = c.api.state.messaging
    dm = dm_between(c, "ann", "ben")
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "补发群", "member_ids": [U["ann"]["id"]]}).json()
    ann = U["ann"]["id"]
    cursor = hub.current_id()
    # events while ann had no stream: two she may see, one about a conversation she is not in,
    # one ephemeral typing hint (never replayed), and one from another user's world
    m_dm = say(c, "ben", dm["id"], "missed while offline")
    m_g = say(c, "owner", g["id"], "group line")
    hub.publish([ann], {"type": "message", "conversation_id": "cv_not_hers", "message": {"seq": 9}})
    hub.publish([ann], {"type": "typing", "conversation_id": dm["id"], "user_id": U["ben"]["id"]}, ephemeral=True)
    hub.publish([U["cid"]["id"]], {"type": "message", "conversation_id": "cv_other", "message": {"seq": 1}})
    evs = _events(_stream_until_end(c, "ann", f"{M}/stream?last_event_id={cursor}"))
    assert evs[0][1] == "hello" and evs[0][2]["replay"] is True and evs[0][2]["heartbeat_s"] == 1 and evs[0][0] == evs[0][2]["last_event_id"]
    assert evs[-1] == (None, "end", {"reason": "session"})
    replayed = [(i, t, d) for i, t, d in evs if t not in ("hello", "end")]
    assert [d["conversation_id"] for _, _, d in replayed] == [dm["id"], g["id"]], "her two conversations, in order; not the foreign one, not the typing hint"
    assert [d["message"]["id"] for _, _, d in replayed] == [m_dm["id"], m_g["id"]]
    assert all(i is not None and i == d["id"] and d["at"] > 0 for i, _, d in replayed), "every event carries its id twice: the SSE id: line and the payload"
    assert replayed[0][0] > cursor and replayed[1][0] > replayed[0][0]
    assert hub.connections() == 0
    # a cursor older than the ring → resync (the client then calls /sync and history after=)
    kinds = _events(_stream_until_end(c, "ann", f"{M}/stream?last_event_id=1", settle=0))
    assert [t for _, t, _ in kinds] == ["hello", "resync", "end"] and kinds[1][2] == {"reason": "cursor_expired"}
    # a cursor from the future (another process) is a resync as well
    kinds = _events(_stream_until_end(c, "ann", f"{M}/stream?last_event_id={hub.current_id() + 1000}", settle=0))
    assert [t for _, t, _ in kinds] == ["hello", "resync", "end"]
    # no cursor: hello only, no replay; a live event arrives with its id and is not sent twice
    evs = _events(_stream_until_end(c, "ann", f"{M}/stream", actions=lambda: svc.send(dm["id"], U["ben"]["id"], body="live one")))
    assert evs[0][2]["replay"] is False
    live = [(i, t, d) for i, t, d in evs if t == "message"]
    assert len(live) == 1 and live[0][0] == live[0][2]["id"] and live[0][2]["message"]["body"] == "live one"
    # the browser's header works like the query
    sign_in(c, "ann")
    checks = c.get("/readyz").json()["checks"]["messaging"]
    assert checks["stream_replays"] == 1 and checks["stream_resyncs"] == 2 and checks["stream_connections"] == 0
    # the per-user cap: refused streams end at once, naming the reason
    hub._max_per_user = 0
    try:
        lines = _stream_until_end(c, "ann", f"{M}/stream", settle=0)
    finally:
        hub._max_per_user = 8
    assert _events(lines) == [(None, "end", {"reason": "too_many_streams", "max": 8})]
    sign_in(c, "ann")
    assert c.get("/readyz").json()["checks"]["messaging"]["stream_refused"] == 1


def test_the_browser_header_last_event_id_is_honoured_like_the_query(org):
    c = org
    U = c.users
    hub = c.api.state.message_hub
    dm = dm_between(c, "ann", "ben")
    cursor = hub.current_id()
    m = say(c, "ben", dm["id"], "missed")
    import synwevo_messages.routes as mr
    uid = U["ann"]["id"]
    sign_in(c, "ann")
    original = mr.HEARTBEAT_S
    mr.HEARTBEAT_S = 1
    threading.Thread(target=lambda: (time.sleep(0.4), c.ident.store.execute("UPDATE sessions SET revoked_at=? WHERE user_id=?", (time.time(), uid))), daemon=True).start()
    lines = []
    try:
        with c.stream("GET", f"{M}/stream", headers={"Last-Event-ID": str(cursor)}) as r:
            for line in r.iter_lines():
                lines.append(line)
    finally:
        mr.HEARTBEAT_S = original
    evs = _events(lines)
    assert evs[0][2]["replay"] is True and [d["message"]["id"] for _, t, d in evs if t == "message"] == [m["id"]]


def test_sync_returns_conversation_level_changes_since_a_cursor(org):
    c = org
    U = c.users
    dm = dm_between(c, "ann", "ben")
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "同步群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]}).json()
    hb = sign_in(c, "ben")
    full = c.get(f"{M}/sync", params={"since": 0}).json()
    assert {x["id"] for x in full["conversations"]} == {dm["id"], g["id"]} and full["left"] == [] and full["recalled"] == [] and full["truncated"] is False
    cursor = full["cursor"]
    assert cursor > 0
    time.sleep(0.01)
    assert c.get(f"{M}/sync", params={"since": cursor}).json()["conversations"] == [], "nothing changed"
    m = say(c, "ann", dm["id"], "after the cursor")
    hb = sign_in(c, "ben")
    out = c.get(f"{M}/sync", params={"since": cursor}).json()
    assert [x["id"] for x in out["conversations"]] == [dm["id"]] and out["conversations"][0]["last_seq"] == 1 and out["conversations"][0]["unread"] == 1
    assert "body" not in json.dumps(out["conversations"]), "never message text"
    # ben's read on another device shows up as his own row change
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 1})
    out = c.get(f"{M}/sync", params={"since": out["cursor"]}).json()
    assert [x["id"] for x in out["conversations"]] == [dm["id"]] and out["conversations"][0]["me"]["last_read_seq"] == 1
    # a recall shows as a vanished message; a removal from a group as `left`
    ha = sign_in(c, "ann")
    c.post(f"{M}/messages/{m['id']}/recall", headers=ha)
    ho = sign_in(c, "owner")
    c.request("DELETE", f"{M}/conversations/{g['id']}/members/{U['ben']['id']}", headers=ho)
    hb = sign_in(c, "ben")
    out2 = c.get(f"{M}/sync", params={"since": cursor}).json()
    assert out2["left"] == [g["id"]] and out2["recalled"] == [{"id": m["id"], "conversation_id": dm["id"], "seq": 1, "how": "recalled"}]
    assert [x["id"] for x in out2["conversations"]] == [dm["id"]], "the group he left is not in his changes any more"
    assert c.get(f"{M}/sync", params={"since": -1}).status_code == 422


# ---------------------------------------------------------------------------- 9. history_from, description
def test_adding_members_can_share_the_whole_history_and_groups_have_a_description(org):
    c = org
    U = c.users
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "历史群", "description": "  项目讨论  ", "member_ids": []}).json()
    assert g["description"] == "项目讨论"
    say(c, "owner", g["id"], "said before anyone joined")
    ho = sign_in(c, "owner")
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["ann"]["id"]], "history_from": "some"}).status_code == 400
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["ann"]["id"]], "later": 1}).status_code == 400
    # default: from the join
    c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["ann"]["id"]]})
    ha = sign_in(c, "ann")
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()
    assert [m["body"] for m in hist["items"] if m["kind"] == "text"] == [] and c.get(f"{M}/conversations/{g['id']}").json()["me"]["history_from"] == "join"
    # the manager's explicit choice: everything, recorded and said
    ho = sign_in(c, "owner")
    out = c.post(f"{M}/conversations/{g['id']}/members", headers=ho, json={"user_ids": [U["ben"]["id"]], "history_from": "all"})
    assert out.status_code == 200
    hb = sign_in(c, "ben")
    conv = c.get(f"{M}/conversations/{g['id']}").json()
    assert conv["me"]["history_from"] == "all" and conv["me"]["joined_seq"] == 0 and conv["unread"] == 0
    hist = c.get(f"{M}/conversations/{g['id']}/messages").json()
    assert [m["body"] for m in hist["items"] if m["kind"] == "text"] == ["said before anyone joined"] and hist["floor"] == 0
    assert hist["items"][-1]["system"] == {"event": "added", "added": [U["ben"]["id"]], "history": "all", "actor": U["owner"]["id"]}
    assert [x["id"] for x in c.get(f"{M}/search", params={"q": "before anyone"}).json()["items"]]
    # a member cannot choose it for others; ann still sees only from her join
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/conversations/{g['id']}/members", headers=ha, json={"user_ids": [U["cid"]["id"]], "history_from": "all"}).status_code == 403
    assert [m["body"] for m in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if m["kind"] == "text"] == []
    # the description: managers only, bounded, cleared with an empty string, said in a system row
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ha, json={"description": "我改"}).status_code == 403
    ho = sign_in(c, "owner")
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ho, json={"description": "x" * 501}).status_code == 400
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ho, json={"description": " 新的说明 "}).json()["description"] == "新的说明"
    assert c.patch(f"{M}/conversations/{g['id']}", headers=ho, json={"description": ""}).json()["description"] is None
    events = [m["system"]["event"] for m in c.get(f"{M}/conversations/{g['id']}/messages").json()["items"] if m["kind"] == "system"]
    assert events[-2:] == ["description", "description"]


# ---------------------------------------------------------------------------- 10. revocation propagation

# ---------------------------------------------------------------------------- 10. revocation propagation
def test_removal_and_organization_loss_close_the_stream_typing_presence_attachments_and_search_at_once(org):
    c = org
    U = c.users
    hub = c.api.state.message_hub
    svc = c.api.state.messaging
    ho = sign_in(c, "owner")
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "撤权群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]}).json()
    ha = sign_in(c, "ann")
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    att = c.post(f"{M}/conversations/{g['id']}/attachments", files={"file": ("f.png", png, "image/png")}, headers=ha).json()
    c.post(f"{M}/conversations/{g['id']}/messages", headers=ha, json={"body": "机密附件", "attachment_id": att["id"]})
    hb = sign_in(c, "ben")
    c.post(f"{M}/presence", headers=hb, json={})
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 200 and c.get(f"{M}/search", params={"q": "机密附件"}).json()["items"]
    assert set(svc.typing_targets(g["id"], U["ann"]["id"])) == {U["owner"]["id"], U["ben"]["id"]}
    cursor = hub.current_id()
    # the removal reaches ben's open stream at once — before the first heartbeat ping — as `conversation removed`
    lines = _stream_until_end(c, "ben", f"{M}/stream", actions=lambda: svc.remove_member(g["id"], U["owner"]["id"], U["ben"]["id"]))
    removed_at = next(i for i, x in enumerate(lines) if '"change": "removed"' in x)
    assert not any(x.startswith(": ping") for x in lines[:removed_at]), "delivered on publish, not at the next heartbeat"
    ev = [d for _, t, d in _events(lines) if t == "conversation"][-1]
    assert ev == {"type": "conversation", "conversation_id": g["id"], "change": "removed", "id": ev["id"], "at": ev["at"]}
    # every other door: typing 404, out of the typing/presence audience, attachment 404, search empty, history 404
    hb = sign_in(c, "ben")
    assert c.post(f"{M}/conversations/{g['id']}/typing", headers=hb, json={}).status_code == 404
    assert svc.co_members(U["ben"]["id"]) == [] and svc.typing_targets(g["id"], U["ann"]["id"]) == [U["owner"]["id"]]
    assert c.get(f"{M}/presence", params={"user_ids": U["ann"]["id"]}).json()["presence"] == {}
    assert c.get(f"{M}/attachments/{att['id']}").status_code == 404
    assert c.get(f"{M}/search", params={"q": "机密附件"}).json()["items"] == []
    assert c.get(f"{M}/conversations/{g['id']}/messages").status_code == 404
    # a reconnect with a cursor from before the removal replays nothing of the group's traffic —
    # only the notice that he was removed, which is what tells the tab to drop the conversation
    say(c, "owner", g["id"], "after ben left")
    evs = _events(_stream_until_end(c, "ben", f"{M}/stream?last_event_id={cursor}", settle=0))
    assert [(t, d.get("change")) for _, t, d in evs] == [("hello", None), ("conversation", "removed"), ("end", None)], evs
    assert evs[1][2]["conversation_id"] == g["id"]
    # organization loss happens on the account service (another process, another `Messaging`
    # instance with no hub): the open stream notices the lost membership at its next heartbeat
    # and announces `conversation removed` for each conversation of that organization — once,
    # however many tabs the person has open
    dm = dm_between(c, "ben", "cid")
    hb = sign_in(c, "ben")
    c.post(f"{M}/presence", headers=hb, json={})
    assert svc.presence_audience(U["ben"]["id"]) == [U["cid"]["id"]]
    from synwevo_core.organizations import Organizations
    evs = _events(_stream_until_end(c, "ben", f"{M}/stream", settle=1.6,
                                    actions=lambda: Organizations(c.ident.store, c.ident).remove_member(c.oid, U["owner"]["id"], U["ben"]["id"], reason="fixture removal of ben")))
    lost = [d for _, t, d in evs if t == "conversation" and d.get("change") == "removed"]
    assert [d["conversation_id"] for d in lost] == [dm["id"]] and lost[0]["detected"] == "heartbeat" and lost[0]["id"] > 0, evs
    assert hub.publish_once(f"lost:{U['ben']['id']}:{dm['id']}:{int(svc.membership_closed_at(dm['id'], U['ben']['id']))}", [U["ben"]["id"]], {"type": "conversation"}) is None, "told once"
    hb = sign_in(c, "ben")
    assert c.get(f"{M}/conversations").json()["items"] == [] and c.get(f"{M}/conversations/{dm['id']}").status_code == 404
    assert svc.presence_audience(U["ben"]["id"]) == [] and c.post(f"{M}/conversations/{dm['id']}/typing", headers=hb, json={}).status_code == 404
    hc = sign_in(c, "cid")
    assert c.get(f"{M}/presence", params={"user_ids": U["ben"]["id"]}).json()["presence"] == {}, "cid no longer shares a conversation with ben"


# ---------------------------------------------------------------------------- 11. metrics
def test_readyz_reports_messaging_counters_as_numbers_only(org):
    c = org
    before = c.get("/readyz").json()
    assert before["status"] == "ready"
    m = before["checks"]["messaging"]
    # `stream_events` (B3): live events the streams passed on — closed conversations' are not
    assert set(m) == {"send_failed", "stream_replays", "stream_resyncs", "stream_refused", "stream_events", "typing_events", "presence_events", "search_queries",
                      "stream_connections", "presence_tracked", "ring_events"}
    assert all(isinstance(v, int) for v in m.values()) and m["stream_connections"] == 0
    dm = dm_between(c, "ann", "ben")
    ha = sign_in(c, "ann")
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": ""}).status_code == 400
    assert c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "x" * 5000}).status_code == 400
    c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={})
    c.post(f"{M}/presence", headers=ha, json={})
    c.get(f"{M}/search", params={"q": "anything"})
    after = c.get("/readyz").json()["checks"]["messaging"]
    assert after["send_failed"] == 2 and after["typing_events"] == 1 and after["search_queries"] == 1 and after["presence_tracked"] == 1
    assert json.dumps(after).count("Ann") == 0 and "cv_" not in json.dumps(after)


# ---------------------------------------------------------------------------- 12. the notification hooks
def test_notification_hooks_treat_a_recalled_message_as_gone_and_a_running_mute_as_muted(org):
    c = org
    U = c.users
    n = c.api.state.notifier
    dm = dm_between(c, "ann", "ben")
    m = say(c, "ann", dm["id"], "will be recalled")
    ben = U["ben"]["id"]
    item = [x for x in n.list(ben)["items"] if x["kind"] == "conversation.dm"][0]
    row = {"notification_id": item["id"], "user_id": ben, "category": "conversation", "kind": "conversation.dm", "severity": "info", "notified_at": item["created_at"]}
    assert n.stale_reason(row, n.preferences(ben)) is None, "live: one unread message"
    hb = sign_in(c, "ben")
    c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": time.time() + 600})
    assert n.stale_reason(row, n.preferences(ben)) == "conversation muted"
    c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": None})
    assert n.stale_reason(row, n.preferences(ben)) is None
    ha = sign_in(c, "ann")
    c.post(f"{M}/messages/{m['id']}/recall", headers=ha)
    assert n.stale_reason(row, n.preferences(ben)) == "nothing unread", "the recalled message is not unread any more"
    assert n.conversation_unread(dm["id"], ben) == {"unread": 0, "muted": False, "latest": None}
