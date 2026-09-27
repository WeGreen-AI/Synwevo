"""Round 9 (CI-74) across modules: what the composed deployment has to agree on.

The conversation API is Synwevo Message's and the mail dispatcher is the account service's, so the
send-time re-check that a recalled message or a running `muted_until` stops a private-message digest
can only be proven where both halves run in one process — the single-process composition. The same
composition shows the new routes under `/api/messaging` on the `all` role, the account origin's
legacy prefix answering 410 for them too once the application registers, and schema v24 as the
store applies it (additive columns, the FTS5 index and its triggers, an idempotent reopen)."""
from __future__ import annotations

import time

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


def test_a_running_mute_and_a_recall_stop_the_private_message_digest_before_it_is_mailed(org):
    """CI-68's rule with CI-74's facts: the dispatcher re-checks at send time, and `muted_until`
    counts as muted while a recalled message counts as gone."""
    c = org
    n, disp = c.api.state.notifier, c.api.state.notify_dispatcher
    ben = c.users["ben"]["id"]
    n.set_preferences(ben, {"categories": {"conversation": {"email": "digest"}}})
    ha = login(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": ben}).json()
    outbox = lambda: c.ident.store.all("SELECT subject FROM mail_outbox WHERE kind='notification' ORDER BY created_at")  # noqa: E731
    pending = lambda: c.ident.store.all("SELECT d.notification_id, d.status, d.last_error FROM notification_deliveries d JOIN notifications x ON x.id=d.notification_id "  # noqa: E731
                                        "WHERE x.user_id=? AND x.kind='conversation.dm' ORDER BY x.created_at", (ben,))
    # ---- a message arrives, then the reader mutes the conversation for an hour: the due digest is skipped as muted
    first = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "第一句", "client_id": "r1"}).json()["message"]
    assert len(pending()) == 1 and pending()[0]["status"] == "pending"
    hb = login(c, "ben")
    me = c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": time.time() + 3600}).json()["me"]
    assert me["muted_until"] and me["muted"] is False
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (pending()[0]["notification_id"],))
    disp.run_once()
    assert pending()[0]["status"] == "skipped" and pending()[0]["last_error"] == "conversation muted" and outbox() == []
    # while muted, a new message creates no inbox item at all (recipients_for skips the reader)
    c.post(f"{M}/conversations/{dm['id']}/messages", headers=login(c, "ann"), json={"body": "静音期间", "client_id": "r2"})
    assert len(pending()) == 1
    # ---- the mute is cleared; the next message opens an item; the sender recalls it → nothing unread → skipped
    hb = login(c, "ben")
    c.put(f"{M}/conversations/{dm['id']}/preferences", headers=hb, json={"muted_until": None})
    c.post(f"{M}/conversations/{dm['id']}/read", headers=hb, json={"seq": 99})
    ha = login(c, "ann")
    third = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "会被撤回", "client_id": "r3"}).json()["message"]
    assert len(pending()) == 2 and pending()[1]["status"] == "pending"
    assert c.post(f"{M}/messages/{third['id']}/recall", headers=ha).json()["recalled"] is True
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (pending()[1]["notification_id"],))
    disp.run_once()
    assert pending()[1]["status"] == "skipped" and pending()[1]["last_error"] == "nothing unread" and outbox() == []
    # ---- the control: an unread, unmuted, unrecalled message is mailed as a digest without its text
    #      (ben reads first, so the next message opens a new unread span and a new item)
    c.post(f"{M}/conversations/{dm['id']}/read", headers=login(c, "ben"), json={"seq": 99})
    ha = login(c, "ann")
    fourth = c.post(f"{M}/conversations/{dm['id']}/messages", headers=ha, json={"body": "真的要寄的那句 secret", "client_id": "r4"}).json()["message"]
    c.ident.store.execute("UPDATE notification_deliveries SET not_before=0 WHERE notification_id=?", (pending()[2]["notification_id"],))
    disp.run_once()
    assert pending()[2]["status"] == "queued" and len(outbox()) == 1
    mail = c.ident.store.one("SELECT body FROM mail_outbox WHERE kind='notification'")
    assert "secret" not in mail["body"] and "1 条未读消息" in mail["body"]
    assert first["id"] != fourth["id"]


def test_the_composed_all_role_serves_the_new_routes_and_keeps_its_readiness(org):
    """The `all` role mounts the real prefix only; the account origin's legacy prefix is one
    router with a 410 dependency, so every new route inherits the switch `test_messages_origin.py`
    pins — nothing to mount twice here."""
    c = org
    ha = login(c, "ann")
    dm = c.post(f"{M}/dm", headers=ha, json={"organization_id": c.oid, "user_id": c.users["ben"]["id"]}).json()
    # the round-9 surfaces answer on the composed origin
    assert c.get(f"{M}/settings").json() == {"show_presence": True, "read_receipts": False}
    assert c.post(f"{M}/presence", headers=ha, json={}).json()["state"] == "online"
    assert c.post(f"{M}/conversations/{dm['id']}/typing", headers=ha, json={}).json()["sent"] is True
    assert c.get(f"{M}/sync", params={"since": 0}).json()["conversations"][0]["id"] == dm["id"]
    caps = c.get(f"{M}/capabilities").json()
    assert caps["recall_window_s"] == 120 and caps["voice"]["max_duration_ms"] == 300000 and caps["search"]["engine"] in ("fts5", "like")
    # the shared badge route counts a marked-unread conversation on this origin too
    hb = login(c, "ben")
    c.post(f"{M}/conversations/{dm['id']}/unread", headers=hb)
    assert c.get("/api/auth/messaging/unread-count").json() == {"unread": 1, "conversations": 1}
    # the old prefix is not mounted on `all` (it is the account origin's fallback only)
    assert c.get("/api/auth/messaging/settings").status_code == 404
    # the process that serves conversations reports its counters, numbers only; readiness itself is unchanged
    ready = c.get("/readyz").json()
    assert ready["status"] == "ready" and all(isinstance(v, int) for v in ready["checks"]["messaging"].values())
    assert ready["checks"]["messaging"]["typing_events"] == 1 and ready["checks"]["messaging"]["stream_connections"] == 0


def test_schema_v24_is_additive_indexed_and_idempotent(tmp_path, monkeypatch):
    from synwevo_core import messaging_search as fts
    from synwevo_core.store import SCHEMA_VERSION, Store
    monkeypatch.setenv("SYNWEVO_DATA_DIR", str(tmp_path / "d"))
    st = Store(str(tmp_path / "d"))
    assert SCHEMA_VERSION == 24 and st.schema_version() == 24
    cols = lambda t: {r["name"] for r in st.all(f"PRAGMA table_info({t})")}  # noqa: E731
    assert {"marked_unread", "muted_until", "history_from", "last_read_at", "updated_at"} <= cols("conversation_members")
    assert {"recalled_at", "removed_by", "forwarded_from"} <= cols("messages") and "duration_ms" in cols("message_attachments")
    assert {"user_id", "show_presence", "read_receipts", "updated_at"} == cols("messaging_settings") and {"message_id", "user_id", "created_at"} == cols("message_hides")
    # the CHECK sets of the existing columns are untouched
    ddl = st.one("SELECT sql FROM sqlite_master WHERE name='messages'")["sql"]
    assert "CHECK (kind IN ('text','system'))" in ddl
    with st.conn() as c:
        info = fts.describe(c)
    triggers = {r["name"] for r in st.all("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'messages_fts_%'")}
    if info["engine"] == "fts5":
        assert triggers == {"messages_fts_ai", "messages_fts_ad", "messages_fts_au"}
        assert st.one("SELECT sql FROM sqlite_master WHERE name='messages_fts'")["sql"].startswith("CREATE VIRTUAL TABLE messages_fts USING fts5(body, content='messages'")
    else:
        assert triggers == set(), "no FTS5 in this build: nothing was created, search stays LIKE"
    # a second open changes nothing (no duplicate triggers, same version)
    again = Store(str(tmp_path / "d"))
    assert again.schema_version() == 24
    assert {r["name"] for r in again.all("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'messages_fts_%'")} == triggers
