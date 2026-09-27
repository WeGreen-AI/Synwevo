"""Private messages are never mailed one by one (CI-68), checked where both halves live: the
conversation API is Synwevo Message's (`/api/messaging`, apps/messages) and the mail dispatcher is
the account service's, so the one process that has both is the single-process composition.

Moved here from the account service's messaging tests when the routes moved (CI-66); the rule and
every assertion are unchanged. Off by default; once the reader turns the unread digest on, what is
still unread at send time goes out as one mail without message text, at most one per half hour;
read, recalled or muted conversations are not mailed."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF = "X-Synwevo-CSRF"
M = "/api/messaging"


@pytest.fixture()
def org(tmp_path, monkeypatch):
    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("SYNWEVO_MAIL_MODE", "file")
    monkeypatch.setenv("SYNWEVO_MAIL_DIR", str(tmp_path / "mail"))
    monkeypatch.setenv("SYNWEVO_MAIL_FROM", "synwevo@example.test")
    monkeypatch.setenv("SYNWEVO_PUBLIC_URL", "http://testserver")
    monkeypatch.setenv("SYNWEVO_ISSUER", "http://testserver")
    monkeypatch.setenv("SYNWEVO_APP_URL", "http://testserver")
    monkeypatch.delenv("SYNWEVO_ROLE", raising=False)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("owner", "Ola Owner"), ("ann", "Ann Member"), ("ben", "Ben Member")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["owner"]["id"], "Chat Org", "Chat Team", "fixture-idem-chat-org-0001")
    oid = created["organization"]["id"]
    for key in ("ann", "ben"):
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


def test_private_message_mail_is_off_by_default_and_an_opt_in_digest_of_what_is_still_unread(org):
    c = org
    from synwevo_core import notifications as N
    n = c.api.state.notifier
    disp = c.api.state.notify_dispatcher
    ben = c.users["ben"]["id"]
    ha = login(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    outbox = lambda: c.ident.store.all("SELECT subject, body FROM mail_outbox WHERE kind='notification' ORDER BY created_at")  # noqa: E731
    # ---- default: inbox row yes, mail no (the delivery is skipped with the reason, not lost)
    assert n.preferences(ben)["categories"]["conversation"]["email"] == "off"
    assert n.preferences(ben)["preview"] == "minimal"
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "秘密的第一句", "client_id": "s1"})
    disp.run_once()
    row = c.ident.store.one("SELECT d.status, d.last_error FROM notification_deliveries d JOIN notifications x ON x.id=d.notification_id WHERE x.user_id=? AND x.kind='conversation.dm'", (ben,))
    assert row["status"] == "skipped" and row["last_error"] == "channel off" and outbox() == []
    # per-message mail cannot even be chosen for conversations; other categories still can
    with pytest.raises(Exception) as ex:
        n.set_preferences(ben, {"categories": {"conversation": {"email": "immediate"}}})
    assert "不逐条" in str(ex.value)
    n.set_preferences(ben, {"categories": {"organization": {"email": "immediate"}}})
    # ---- opt in: the digest, and what it says is decided when it is sent
    n.set_preferences(ben, {"categories": {"conversation": {"email": "digest"}}})
    hb = login(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99})     # start clean: the first message is read
    ha = login(c, "ann")
    first = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "会被撤回的话", "client_id": "s2"}).json()["message"]
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "留下来的第二句 with the secret word", "client_id": "s3"})
    pend = c.ident.store.all("SELECT d.notification_id, d.status, d.not_before FROM notification_deliveries d JOIN notifications x ON x.id=d.notification_id "
                             "WHERE x.user_id=? AND x.kind='conversation.dm' AND d.status='pending'", (ben,))
    assert len(pend) == 1 and pend[0]["not_before"] > N.now() + 25 * 60, "one pending digest item per unread span, due after the window"
    assert c.post(f"{M}/messages/{first['id']}/recall", headers=ha).json()["recalled"] is True   # the message that created the item is recalled (CI-74: DELETE is delete-for-me)
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (pend[0]["notification_id"],))
    out = disp.run_once()
    assert out["queued"] == 1 and len(outbox()) == 1
    mail = outbox()[-1]
    assert "1 条未读消息" in mail["body"] and "Ann Member" in mail["body"], mail["body"]
    assert "会被撤回的话" not in mail["body"] and "secret word" not in mail["body"], "no message text unless the reader asked for it"
    # ---- one item per unread span: a span read before it is mailed is dropped, the next span is
    #      held until half an hour after the previous digest instead of mailed at once
    hb = login(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99})     # span closed → the next message opens a new item
    ha = login(c, "ann")
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "第三句", "client_id": "s4"})
    hb = login(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99})     # …and is read in the app before the digest is due
    ha = login(c, "ann")
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "第四句", "client_id": "s5"})
    ids = [r["notification_id"] for r in c.ident.store.all("SELECT d.notification_id FROM notification_deliveries d JOIN notifications x ON x.id=d.notification_id "
                                                             "WHERE x.user_id=? AND x.kind='conversation.dm' AND d.status='pending'", (ben,))]
    assert len(ids) == 2
    c.ident.store.execute(f"UPDATE notification_deliveries SET not_before=0 WHERE notification_id IN ({','.join('?' * len(ids))})", tuple(ids))
    out = disp.run_once()
    assert out["queued"] == 0 and out["skipped"] >= 1 and out["deferred"] == 1, out
    states = {r["notification_id"]: (r["status"], r["last_error"], r["not_before"]) for r in
              c.ident.store.all(f"SELECT notification_id, status, last_error, not_before FROM notification_deliveries WHERE notification_id IN ({','.join('?' * len(ids))})", tuple(ids))}
    kinds = sorted(v[0] for v in states.values())
    assert kinds == ["pending", "skipped"], states
    held = next(v for v in states.values() if v[0] == "pending")
    assert held[2] > N.now() + 25 * 60, "held until half an hour after the previous digest"
    skipped = next(v for v in states.values() if v[0] == "skipped")
    assert skipped[1] in ("read in app", "nothing unread", "superseded")
    # ---- with `preview` = full the digest carries the latest unread line; a muted conversation is never mailed
    n.set_preferences(ben, {"preview": "full"})
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0, updated_at=0 WHERE status='queued'")   # forget the interval
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE status='pending'")
    out = disp.run_once()
    assert out["queued"] == 1 and "第四句" in outbox()[-1]["body"]
    hb = login(c, "ben")
    c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted": True})
    assert c.get(f"{M}/conversations/{dm['id']}").json()["me"]["muted"] is True
    ha = login(c, "ann")
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "静音后的话", "client_id": "s6"})
    assert disp.run_once()["queued"] == 0 and len(outbox()) == 2
    # ---- reading the conversation to its end reads the inbox items about it (CI-66 fix): the
    #      bell no longer keeps "Ann sent you a message" after the chat has been read
    assert n.unread_count(ben) > 0
    hb = login(c, "ben")
    c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted": False})
    assert c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99}).json()["notifications_read"] >= 1
    assert all(x["read_at"] for x in n.list(ben)["items"] if x["kind"].startswith("conversation.") and x["object_id"] == dm["id"])
