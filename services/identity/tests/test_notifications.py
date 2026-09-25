"""The platform inbox: rows land for real events, read state is the reader's, e-mail is an outbox.

The account service is the emitter for security and membership events and the only role that
mails. Everything here runs against the real store and the real mailer in file mode; nothing is
mocked except that the dispatcher is driven by hand (`run_once`) instead of by its thread.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from synwevo_core.notifications import CATEGORIES, Notifier, default_preferences

PW = "harbor lights guide the evening ferry home"


@pytest.fixture()
def acct(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "https://accounts.example.test")
    monkeypatch.setenv("SYNWEVO_ISSUER", "https://accounts.example.test")
    monkeypatch.setenv("SYNWEVO_PORTAL_URL", "https://portal.example.test")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    owner = ident.create_user("owner@example.test", PW, status="active", display_name="Ola Owner")
    member = ident.create_user("member@example.test", PW, status="active", display_name="Mia Member", locale="en")
    for u in (owner, member):
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
    with TestClient(api) as c:
        c.api = api
        c.ident = ident
        c.owner, c.member = owner, member
        yield c


def login(c: TestClient, email: str) -> dict:
    r = c.post("/api/auth/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    return {"X-Synwevo-CSRF": c.cookies.get("dw_csrf")}


def outbox(c) -> list[dict]:
    return c.ident.store.all("SELECT kind, to_addr, subject, body FROM mail_outbox ORDER BY created_at")


# ---------------------------------------------------------------- core semantics
def test_rows_render_in_the_readers_language_and_read_state_is_theirs(acct):
    n: Notifier = acct.api.state.notifier
    row = n.emit(acct.member["id"], "member.role_changed", params={"workspace": "Team Space", "role": "editor", "actor": "Ola Owner"},
                 workspace_id="w1", target={"origin": "accounts", "path": "/account#workspaces"})
    assert row and row["severity"] == "info"
    en = n.render(n.get(acct.member["id"], row["id"]), "en")
    zh = n.render(n.get(acct.member["id"], row["id"]), "zh-Hant")
    assert en["title"] == "Your role in “Team Space” is now editor" and en["href"] == "https://accounts.example.test/account#workspaces"
    assert zh["title"] == "你在「Team Space」的角色變為 編輯者" and "Ola Owner" in zh["body"]
    # another reader cannot see or mark it
    with pytest.raises(Exception):
        n.get(acct.owner["id"], row["id"])
    assert n.mark(acct.owner["id"], [row["id"]], read=True) == 0
    assert n.unread_count(acct.member["id"]) == 1
    assert n.mark(acct.member["id"], [row["id"]], read=True) == 1 and n.unread_count(acct.member["id"]) == 0
    # dedupe: the same key returns the same row
    a = n.emit(acct.member["id"], "invitation.received", params={"workspace": "T", "inviter": "O", "role": "viewer"}, dedupe_key="invite:x", actionable=True)
    b = n.emit(acct.member["id"], "invitation.received", params={"workspace": "T", "inviter": "O", "role": "viewer"}, dedupe_key="invite:x", actionable=True)
    assert a["id"] == b["id"] and n.list(acct.member["id"], filter="pending")["items"][0]["action_state"] == "pending"
    n.set_action_state("invitation", "none", "done")            # wrong object: nothing changes
    assert n.list(acct.member["id"], filter="pending")["items"]


def test_read_all_stops_at_the_cursor_the_list_was_opened_with(acct):
    n: Notifier = acct.api.state.notifier
    uid = acct.member["id"]
    for i in range(3):
        n.emit(uid, "member.left", params={"member": f"M{i}", "workspace": "W"})
    opened = n.list(uid)
    assert opened["unread_count"] == 3
    late = n.emit(uid, "member.left", params={"member": "late", "workspace": "W"})
    assert n.read_all(uid, opened["latest_seq"]) == 3
    remaining = n.list(uid, filter="unread")["items"]
    assert [x["id"] for x in remaining] == [late["id"]]


def test_preferences_drop_in_app_rows_for_a_category_but_never_security(acct):
    n: Notifier = acct.api.state.notifier
    uid = acct.member["id"]
    prefs = n.set_preferences(uid, {"categories": {"organization": {"in_app": False, "email": "off"}, "security": {"in_app": False, "email": "off"}},
                                    "quiet_start": "22:00", "quiet_end": "08:00", "timezone": "Asia/Shanghai"})
    assert prefs["categories"]["organization"] == {"in_app": False, "email": "off", "push": True}
    assert prefs["categories"]["security"] == {"in_app": True, "email": "immediate", "push": True}, "security cannot be silenced"
    assert n.emit(uid, "member.left", params={"member": "x", "workspace": "W"}) is None
    row = n.security_change(uid, "password_changed")
    assert row and n.render(row | {"seq": 1}, "en")["title"] == "Account security change: Password changed"
    d = acct.ident.store.one("SELECT status FROM notification_deliveries WHERE notification_id=?", (row["id"],))
    assert d["status"] == "pending"
    for bad in ({"categories": {"nope": {}}}, {"quiet_start": "25:00", "quiet_end": "08:00", "timezone": "Asia/Shanghai"},
                {"quiet_start": "22:00", "quiet_end": "08:00", "timezone": "Mars/Olympus"}, {"preview": "loud"}):
        with pytest.raises(Exception):
            n.set_preferences(uid, bad)
    assert default_preferences()["categories"].keys() == set(CATEGORIES)


# ---------------------------------------------------------------- the entrances
def test_security_changes_and_invitations_reach_the_inbox_through_the_routes(acct):
    c = acct
    h = login(c, "owner@example.test")
    # a workspace the owner governs
    me = c.get("/api/auth/me").json()
    wid = next(w["id"] for w in me["workspaces"])
    r = c.post(f"/api/auth/workspaces/{wid}/invitations/batch", headers=h,
               json={"invitations": [{"email": "member@example.test", "role": "editor", "can_export": True}], "expires_in_days": 7})
    assert r.status_code == 200, r.text
    assert c.ident.store.one("SELECT COUNT(*) AS n FROM invitations")["n"] == 1
    c.post("/api/auth/logout", headers=h)
    h2 = login(c, "member@example.test")
    inbox = c.get("/api/auth/notifications").json()
    assert inbox["unread_count"] == 1
    item = inbox["items"][0]
    assert item["kind"] == "invitation.received" and item["action_state"] == "pending"
    assert item["title"] == "Ola Owner invited you to “" + me["workspaces"][0]["name"] + "”", item   # the member reads English
    assert item["href"] == "https://accounts.example.test/onboarding"
    # marking read does not accept anything
    r = c.post("/api/auth/notifications/mark", headers=h2, json={"ids": [item["id"]], "read": True})
    assert r.status_code == 200 and r.json()["unread_count"] == 0
    assert c.get("/api/auth/notifications?filter=pending").json()["items"], "still a to-do"
    # a password change is a security row for the member, always with an e-mail delivery
    r = c.post("/api/auth/password", headers=h2, json={"current_password": PW, "new_password": PW + " again and again"})
    assert r.status_code == 200, r.text
    sec = [x for x in c.get("/api/auth/notifications?lang=zh-Hans").json()["items"] if x["kind"] == "security.change"]
    assert sec and sec[0]["title"] == "账户安全变更：口令已更改" and sec[0]["severity"] == "critical"


def test_the_dispatcher_mails_deliveries_once_groups_digests_and_honours_quiet_hours(acct, monkeypatch):
    c = acct
    n: Notifier = c.api.state.notifier
    disp = c.api.state.notify_dispatcher
    uid = c.member["id"]
    n.security_change(uid, "password_changed")                                     # critical: immediate
    n.emit(uid, "member.role_changed", params={"workspace": "W", "role": "admin", "actor": "O"})   # immediate by default
    before = len(outbox(c))
    out = disp.run_once()
    assert out["queued"] == 2 and len(outbox(c)) == before + 2
    subjects = [m["subject"] for m in outbox(c)[-2:]]
    assert any("Password changed" in s_ for s_ in subjects) and any("now admin" in s_ for s_ in subjects)
    assert disp.run_once()["queued"] == 0, "a delivery is mailed once"
    statuses = {d["status"] for d in c.ident.store.all("SELECT status FROM notification_deliveries")}
    assert statuses == {"queued"}
    # digest: two docs notifications become one mail after the window
    n.set_preferences(uid, {"categories": {"organization": {"in_app": True, "email": "digest"}}})
    a = n.emit(uid, "member.left", params={"member": "A", "workspace": "W"})
    b = n.emit(uid, "member.left", params={"member": "B", "workspace": "W"})
    assert disp.run_once()["queued"] == 0, "not due until the digest window passes"
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id IN (?,?)", (a["id"], b["id"]))
    out = disp.run_once()
    assert out["queued"] == 2 and out["digested"] == 1
    last = outbox(c)[-1]
    assert "2 new notifications" in last["subject"] and "A left" in last["body"] and "B left" in last["body"]
    # quiet hours defer non-critical mail, never security
    from synwevo_core import notifications as N
    n.set_preferences(uid, {"categories": {"organization": {"in_app": True, "email": "immediate"}},
                            "quiet_start": "00:00", "quiet_end": "23:59", "timezone": "UTC"})
    q = n.emit(uid, "member.left", params={"member": "Q", "workspace": "W"})
    s_ = n.security_change(uid, "email_changed")
    out = disp.run_once()
    assert out["deferred"] == 1 and out["queued"] == 1
    dq = c.ident.store.one("SELECT status, not_before FROM notification_deliveries WHERE notification_id=?", (q["id"],))
    assert dq["status"] == "pending" and dq["not_before"] > N.now()
    assert c.ident.store.one("SELECT status FROM notification_deliveries WHERE notification_id=?", (s_["id"],))["status"] == "queued"


def test_without_mail_the_inbox_still_works_and_deliveries_are_skipped_not_lost(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "disabled")
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    u = ident.create_user("solo@example.test", PW, status="active")
    n: Notifier = api.state.notifier
    row = n.security_change(u["id"], "password_changed")
    api.state.notify_dispatcher.run_once()
    d = ident.store.one("SELECT status, last_error FROM notification_deliveries WHERE notification_id=?", (row["id"],))
    assert d["status"] == "skipped" and "not configured" in d["last_error"]
    assert n.unread_count(u["id"]) == 1
