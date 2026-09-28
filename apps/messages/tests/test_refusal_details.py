"""CI-83: a refusal that quotes a limit carries that limit in `detail`, so the shell words it in
the reader's language from the number (a changed limit never falls back to a bare code), and the
direct-message refusal names only what the application offers. The shell half — every refusal a
person can meet is catalogued or templated — is `apps/messages/web/tests/refusals.test.ts`."""
from __future__ import annotations

import pytest

M = "/api/messaging"


def test_numbered_refusals_carry_their_number(org):
    c, U = org, org.users
    ho = c.sign_in("owner")
    # a group name over the limit: the field and its length
    r = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "名" * 81, "member_ids": [U["ann"]["id"]]})
    assert r.status_code == 400 and r.json()["error"] == "invalid_field"
    assert r.json()["detail"] == {"field": "name", "limit": 80}
    # more members than a group holds: the cap (lowered here so the fixture's four people exceed it)
    import synwevo_core.messaging as messaging
    with pytest.MonkeyPatch.context() as mp:        # the fixture's own patcher holds its environment
        mp.setattr(messaging, "MAX_GROUP_MEMBERS", 2)
        r = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "三人群", "member_ids": [U["ann"]["id"], U["ben"]["id"]]})
    assert r.status_code == 400 and r.json()["error"] == "too_many_members" and r.json()["detail"]["limit"] == 2
    g = c.post(f"{M}/groups", headers=ho, json={"organization_id": c.oid, "name": "限额", "member_ids": [U["ann"]["id"]]}).json()
    # an edit emptied out: the body's length range
    m = c.post(f"{M}/conversations/{g['id']}/messages", headers=ho, json={"body": "hello"}).json()["message"]
    r = c.patch(f"{M}/messages/{m['id']}", headers=ho, json={"body": "   "})
    assert r.status_code == 400 and r.json()["detail"] == {"field": "body", "limit": 4000}
    # an attachment over the limit, whichever layer refuses it first (the route reads limit + 1 bytes)
    import synwevo_messages.routes as routes
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(messaging, "MAX_ATTACHMENT_BYTES", 1024)
        mp.setattr(routes, "MAX_ATTACHMENT_BYTES", 1024)
        r = c.post(f"{M}/conversations/{g['id']}/attachments", headers=ho, files={"file": ("a.bin", b"x" * 2048, "application/octet-stream")})
        assert r.status_code == 413 and r.json()["error"] == "file_too_large" and r.json()["detail"]["limit"] == 1024
        assert "kind" not in r.json()["detail"], "only a voice message says so"
        mp.setattr(routes, "MAX_ATTACHMENT_BYTES", 4096)          # past the route, the store's own check
        r = c.post(f"{M}/conversations/{g['id']}/attachments", headers=ho, files={"file": ("a.bin", b"x" * 2048, "application/octet-stream")})
        assert r.status_code == 413 and r.json()["detail"]["limit"] == 1024


def test_leaving_a_direct_message_names_what_exists(org):
    c, U = org, org.users
    ha = c.sign_in("ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": U["ben"]["id"]}).json()
    r = c.post(f"{M}/conversations/{dm['id']}/leave", headers=ha)
    assert r.status_code == 409 and r.json()["error"] == "not_a_group"
    # mute and block exist (CI-74, CI-78); there is no per-person "hide" to point at
    assert r.json()["message"] == "私聊不能退出；可以设为静音，或屏蔽对方"
