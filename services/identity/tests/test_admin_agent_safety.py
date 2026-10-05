"""Platform governance of the Agent's content safety, through the real account-service routes.

Isolated fixture accounts and data (`world` from test_admin_operations). No model is called.
Expectations: the rule lists are published as audited versions only by holders of
platform.config.manage; reports and suspensions need platform.users.*; an ordinary account learns
nothing (404); nothing here exposes a conversation, and audit rows carry counts, not rule bodies.
"""
from __future__ import annotations

import json

from synwevo_core import journal
from synwevo_core.agent_safety import AgentAccess, AgentReports

from .test_admin_operations import post, world  # noqa: F401 — fixture

BASE = "/api/admin/agent-safety"
RULES = [{"kind": "keyword", "pattern": "fixture-banned-term", "applies_to": "both", "category": "illegal"}]


def put(client, path, body):
    from synwevo_core.sessions import CSRF_COOKIE, CSRF_HEADER
    return client.put(path, json=body, headers={CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or ""})


def test_ordinary_accounts_cannot_see_or_change_safety_governance(world):
    assert world["ordinary"].get(BASE + "/rules").status_code == 404
    assert put(world["ordinary"], BASE + "/rules", {"rules": RULES, "reason": "nope", "expected_version": 0}).status_code == 404
    assert world["ordinary"].get(BASE + "/reports").status_code == 404
    r = post(world["ordinary"], f"{BASE}/users/{world['target']['id']}/suspend", {"reason": "nope at all"})
    assert r.status_code == 404


def test_rule_lists_are_published_as_audited_versions(world):
    admin = world["admin"]
    assert admin.get(BASE + "/rules").json()["version"] == 0
    r = put(admin, BASE + "/rules", {"rules": RULES, "reason": "first governed list", "expected_version": 0})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.json()["rules"][0]["id"] == "r1"
    stale = put(admin, BASE + "/rules", {"rules": [], "reason": "stale edit", "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["error"] == "safety_rules_changed"
    bad = put(admin, BASE + "/rules", {"rules": [{"kind": "pattern", "pattern": "(x+)+"}], "reason": "bad regex",
                                       "expected_version": 1})
    assert bad.status_code == 400 and bad.json()["error"] == "safety_rule_invalid"
    rows = world["store"].all("SELECT meta FROM audit_events WHERE action='admin.agent_safety.rules_publish'")
    assert len(rows) == 1 and "fixture-banned-term" not in rows[0]["meta"]
    assert json.loads(rows[0]["meta"])["rules"] == 1


def test_reports_are_reviewed_and_a_person_suspended_with_a_reason(world):
    store, admin, target = world["store"], world["admin"], world["target"]
    report = AgentReports(store).create(target["id"], conversation_id="conv_000000000001", message_id="msg_000000000001",
                                        category="harmful", note="fixture note", excerpt=None)
    items = admin.get(BASE + "/reports").json()["items"]
    assert [i["id"] for i in items] == [report["id"]] and items[0]["excerpt"] is None
    r = post(admin, f"{BASE}/users/{target['id']}/suspend", {"reason": "fixture: abuse confirmed", "report_id": report["id"]})
    assert r.status_code == 200, r.text
    assert AgentAccess(store).active(target["id"])["reason"] == "fixture: abuse confirmed"
    assert [e["kind"] for e in journal.read(store.root) if e["kind"] == "agent_access_suspended"] == ["agent_access_suspended"]
    decided = post(admin, f"{BASE}/reports/{report['id']}/decide", {"decision": "resolved", "resolution": "suspended"})
    assert decided.status_code == 200 and decided.json()["status"] == "resolved"
    again = post(admin, f"{BASE}/reports/{report['id']}/decide", {"decision": "dismissed"})
    assert again.status_code == 409
    assert admin.get(BASE + "/suspensions").json()["items"][0]["user_id"] == target["id"]
    lifted = post(admin, f"{BASE}/users/{target['id']}/lift", {"reason": "fixture: appeal upheld"})
    assert lifted.status_code == 200 and AgentAccess(store).active(target["id"]) is None
    actions = [r["action"] for r in store.all("SELECT action FROM audit_events WHERE action LIKE 'admin.agent_safety.%' ORDER BY seq")]
    assert actions == ["admin.agent_safety.suspend", "admin.agent_safety.report_decide", "admin.agent_safety.lift"]
