"""The personal Agent in the account export, and what account deletion erases or keeps.

Real store and identity app. FIXTURE ROWS: the Agent's tables (owned by the workplace origin) are
filled directly with the shapes the Agent writes — a conversation with a user message and an
answer carrying a private provider replay, a tool call with business data, a memory, a sealed
personal key, a payment quote, a consent and a report — so this account-service test needs no
model and no Agent route. Expectations: the export has the visible text and the person's own
records but never the replay, tool data or key; deletion erases content, keeps the pseudonymous
financial and consent records, and a restore replay erases again.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from synwevo_core import journal
from synwevo_core.agent_safety import AgentReports, PROVIDER_TERMS, ProviderConsents
from synwevo_core.store import now

PW = "harbor lights guide the evening ferry home"
SECRET_REPLAY = "fixture-private-reasoning-never-exported"
TOOL_DATA = "fixture-business-row-never-exported"
KEY_CIPHER = "fixture-sealed-key-never-exported"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for key, value in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"),
                       "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                       "PUBLIC_URL": "https://accounts.example.test", "ISSUER": "https://accounts.example.test"}.items():
        monkeypatch.setenv("SYNWEVO_" + key, value)
    from synwevo_identity.app import create_app
    api = create_app(str(tmp_path / "d"), start_jobs=False)
    ident = api.state.identity
    store = ident.store
    uid = ident.create_user("agent-person@example.test", PW, status="active", display_name="Agent Person")["id"]
    store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (uid,))
    t = now()
    store.execute("INSERT INTO agent_conversations (id,user_id,title,status,created_at,updated_at) VALUES "
                  "('conv_00000000000a',?,'Fixture chat','active',?,?)", (uid, t, t))
    store.execute("INSERT INTO agent_messages (id,conversation_id,user_id,parent_id,role,status,body,created_at,updated_at) "
                  "VALUES ('msg_00000000000a','conv_00000000000a',?,NULL,'user','done','What is due today?',?,?)", (uid, t, t))
    store.execute("INSERT INTO agent_messages (id,conversation_id,user_id,parent_id,role,status,body,api_json,created_at,updated_at) "
                  "VALUES ('msg_00000000000b','conv_00000000000a',?,'msg_00000000000a','assistant','done','Two invoices.',?,?,?)",
                  (uid, json.dumps([{"reasoning_content": SECRET_REPLAY}]), t + 1, t + 1))
    store.execute("INSERT INTO agent_turns (id,conversation_id,user_id,user_message_id,assistant_message_id,status,provider,"
                  "credential,model,created_at,updated_at) VALUES ('turn_00000000000a','conv_00000000000a',?,'msg_00000000000a',"
                  "'msg_00000000000b','succeeded','deepseek','platform','deepseek-flash',?,?)", (uid, t, t))
    store.execute("INSERT INTO agent_tool_calls (id,turn_id,conversation_id,user_id,step,tool_use_id,tool,operation,workspace_id,"
                  "input_json,input_hash,status,result_json,created_at,updated_at) VALUES ('tc_00000000000a','turn_00000000000a',"
                  "'conv_00000000000a',?,1,'tu_1','run_operation','tables.row.list','ws_fixture','{}','h','succeeded',?,?,?)",
                  (uid, json.dumps({"rows": [TOOL_DATA]}), t, t))
    store.execute("INSERT INTO agent_memories (id,user_id,body,created_at,updated_at) VALUES ('mem_00000000000a',?,"
                  "'Prefers short answers',?,?)", (uid, t, t))
    store.execute("INSERT INTO agent_settings (user_id,effort,personal_key_sealed,personal_key_hint,updated_at) "
                  "VALUES (?,'high',?,'sk-…fixt',?)", (uid, KEY_CIPHER, t))
    store.execute("INSERT INTO agent_model_quotes (id,environment,namespace,user_id,conversation_id,binding_digest,terms_json,"
                  "created_at,expires_at) VALUES ('quote_fixture','test','agent',?,'conv_00000000000a','d',?,?,?)",
                  (uid, json.dumps({"budget": 100, "max_calls": 2, "route": {"requested_model": "deepseek-flash"},
                                    "sale": {"price_id": "price_fixture", "unit": "CNY"}}), int(t), int(t) + 60))
    ProviderConsents(store).accept(uid, "deepseek", PROVIDER_TERMS["deepseek"].version)
    AgentReports(store).create(uid, conversation_id="conv_00000000000a", message_id="msg_00000000000b",
                               category="wrong", note="fixture note", excerpt="Two invoices.")
    with TestClient(api) as c:
        c.store, c.uid = store, uid
        yield c


def test_the_export_has_the_visible_agent_text_and_records_but_no_replay_tool_data_or_key(world):
    c = world
    assert c.post("/api/auth/login", json={"email": "agent-person@example.test", "password": PW}).status_code == 200
    r = c.post("/api/auth/export", json={"password": PW}, headers={"X-Synwevo-CSRF": c.cookies.get("dw_csrf")})
    assert r.status_code == 200, r.text
    agent = r.json()["agent"]
    conv = agent["conversations"][0]
    assert [(m["role"], m["body"]) for m in conv["messages"]] == [("user", "What is due today?"), ("assistant", "Two invoices.")]
    assert conv["operations"][0]["operation"] == "tables.row.list"
    assert agent["memories"][0]["body"] == "Prefers short answers"
    assert agent["settings"]["personal_key_set"] is True and agent["settings"]["effort"] == "high"
    assert [h["action"] for h in agent["provider_consent_history"]] == ["accepted"]
    assert agent["quotes"][0]["model"] == "deepseek-flash" and agent["quotes"][0]["budget"] == "100"
    assert agent["reports"][0]["note"] == "fixture note"
    assert "wallet_statement" in agent and "AI-generated" in agent["notice"]
    raw = r.text
    for secret in (SECRET_REPLAY, TOOL_DATA, KEY_CIPHER, "sk-…fixt"):
        assert secret not in raw


def _finalize(c):
    from synwevo_identity.profile import finalize_deletions
    c.store.execute("UPDATE users SET status='pending_delete', deletion_requested_at=?, deletion_due_at=? WHERE id=?",
                    (now() - 10, now() - 1, c.uid))
    assert finalize_deletions(c.store) == 1


def _content(c):
    return {t: c.store.one(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id=?", (c.uid,))["n"]
            for t in ("agent_conversations", "agent_messages", "agent_turns", "agent_tool_calls", "agent_memories", "agent_settings")}


def test_deletion_erases_agent_content_and_keeps_pseudonymous_financial_and_consent_records(world):
    c = world
    _finalize(c)
    assert set(_content(c).values()) == {0}
    assert c.store.one("SELECT COUNT(*) AS n FROM agent_model_quotes WHERE user_id=?", (c.uid,))["n"] == 1
    assert c.store.one("SELECT COUNT(*) AS n FROM agent_provider_consent_log WHERE user_id=?", (c.uid,))["n"] == 1
    report = c.store.one("SELECT category, note, excerpt FROM agent_message_reports WHERE user_id=?", (c.uid,))
    assert report == {"category": "wrong", "note": None, "excerpt": None}
    assert c.store.one("SELECT email, display_name FROM users WHERE id=?", (c.uid,)) == {"email": "", "display_name": None}


def test_a_restore_from_before_the_deletion_erases_the_agent_content_again(world):
    c = world
    before = {t: c.store.all(f"SELECT * FROM {t} WHERE user_id=?", (c.uid,)) for t in _content(c)}
    _finalize(c)
    entries = [e for e in journal.read(c.store.root) if e["kind"] == "user_deleted"]
    assert entries
    # The older backup still holds the conversations and memories.
    for table, rows in before.items():
        for row in rows:
            c.store.execute(f"INSERT INTO {table} ({','.join(row)}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
    assert _content(c)["agent_messages"] == 2
    journal.apply(c.store, entries)
    assert set(_content(c).values()) == {0}
