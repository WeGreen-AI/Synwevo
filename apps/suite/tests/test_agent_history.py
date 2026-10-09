"""Personal history pagination against real operations/Store; no model calls."""
from __future__ import annotations

from apps.suite.tests.test_agent import op


def history(suite, count=7):
    suite.sign_in("alice")
    ids = [op(suite, "agent.conversation.create", {"title": f"History {i}"})["output"]["id"]
           for i in range(count)]
    # A tied timestamp is legitimate (bulk restoration, limited clock resolution).
    suite.people.store.execute("UPDATE agent_conversations SET updated_at=1000 WHERE user_id=?",
                               (suite.people.uid("alice"),))
    return sorted(ids, reverse=True)


def test_history_equal_timestamps_are_complete_and_stable(suite):
    expected = history(suite)
    seen, cursor = [], None
    for _ in range(5):
        page = op(suite, "agent.conversation.list", {"limit": 2, **({"cursor": cursor} if cursor else {})})["output"]
        seen.extend(c["id"] for c in page["conversations"])
        cursor = page.get("next_cursor")
        if cursor is None:
            break
    assert seen == expected
    assert cursor is None


def test_history_cursor_bound_to_person_search_and_status(suite):
    history(suite, 4)
    page = op(suite, "agent.conversation.list", {"limit": 1, "q": "History"})["output"]
    cursor = page["next_cursor"]
    for change in ({"q": "other"}, {"status": "archived"}, {"cursor": cursor[:-3] + "xxx"}):
        result = op(suite, "agent.conversation.list", {"q": "History", "cursor": cursor, **change}, expect=400)
        assert result["error"] == "invalid_cursor"
    suite.sign_in("bob")
    assert op(suite, "agent.conversation.list", {"q": "History", "cursor": cursor}, expect=400)["error"] == "invalid_cursor"


def test_history_deleted_boundary_and_legacy_before(suite):
    ids = history(suite, 4)
    page = op(suite, "agent.conversation.list", {"limit": 2})["output"]
    op(suite, "agent.conversation.delete", {"conversation_id": page["conversations"][-1]["id"]})
    result = op(suite, "agent.conversation.list", {"cursor": page["next_cursor"]})["output"]
    assert [c["id"] for c in result["conversations"]] == ids[2:]
    assert op(suite, "agent.conversation.list", {"before": 1000})["output"]["conversations"] == []
    assert len(op(suite, "agent.conversation.list", {"before": 1001})["output"]["conversations"]) == 3
    assert op(suite, "agent.conversation.list", {"cursor": page["next_cursor"], "before": 1001}, expect=400)["error"] == "invalid_cursor"


def test_history_cursor_expires_with_restart_instruction(suite, monkeypatch):
    history(suite, 3)
    from synwevo_suite.agent import history as cursors
    monkeypatch.setattr(cursors, "now", lambda: 2000)
    cursor = op(suite, "agent.conversation.list", {"limit": 1})["output"]["next_cursor"]
    monkeypatch.setattr(cursors, "now", lambda: 2000 + cursors.CURSOR_TTL + 1)
    assert op(suite, "agent.conversation.list", {"cursor": cursor}, expect=410)["error"] == "cursor_expired"
