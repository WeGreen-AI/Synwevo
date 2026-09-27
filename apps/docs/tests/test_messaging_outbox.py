"""Synwevo Docs writes the messaging outbox (CI-75) in the same transaction as the business change
it reports: a comment stored, a run finished. Docs alone, no messages application: what is checked
is the row — its deterministic `event_id`, the object it names (the project), that nothing about
the object is copied into it — and that a repeated write is one row. Delivery is the messages
process's job and is tested there (`apps/messages/tests/test_bridge.py`) and end to end
(`tests/integration/test_messaging_bridge.py`)."""
from __future__ import annotations

import json

from synwevo_core.context import as_tenant
from synwevo_core.identity import Identity

from .test_api_v1 import _ok, _prepared, keys  # noqa: F401


def test_a_stored_comment_queues_exactly_one_event_naming_its_project(api):
    from synwevo_docs.comments import Comments
    store = api.c.app_state.app.store
    pid = api.project("外发项目")
    author = Identity(store).create_user("ann@example.test", None, display_name="Ann", status="active", with_personal_workspace=False)["id"]
    comments = Comments(api.c.app_state.app, Identity(store), None)
    with as_tenant("default"):
        cm = comments.create(pid, author, "这个字段取哪一列？", mentions=None)
        reply = comments.create(pid, author, "回复", parent_id=cm["id"])
    rows = store.all("SELECT * FROM messaging_outbox ORDER BY created_at")
    assert [r["event_id"] for r in rows] == [f"comment:{cm['id']}", f"comment:{reply['id']}"]
    row = rows[0]
    assert row["kind"] == "docs.comment" and row["object_kind"] == "project" and row["object_id"] == pid and row["actor_id"] == author
    assert row["workspace_id"] == "default" and row["delivered_at"] is None and row["attempts"] == 0
    payload = json.loads(row["payload"])
    assert payload == {"comment_id": cm["id"], "project_id": pid, "mentions": [], "parent_id": None}
    assert "取哪一列" not in row["payload"] and "外发项目" not in row["payload"], "the body and the title are read at delivery, never copied"
    assert json.loads(rows[1]["payload"])["parent_id"] == cm["id"]
    # the same comment id emitted again (a retried request) is still one row
    from synwevo_core.messaging_bridge import emit_comment
    with store.tx() as t:
        assert emit_comment(t, comment_id=cm["id"], project_id=pid, workspace_id="default", author_id=author) is False
    assert store.one("SELECT COUNT(*) AS n FROM messaging_outbox")["n"] == 2
    # the comment itself is stored (the outbox rode on its transaction, not the other way round)
    assert store.one("SELECT body FROM project_comments WHERE id=?", (cm["id"],))["body"] == "这个字段取哪一列？"


def test_a_finished_run_queues_one_event_per_run(api, keys, fixtures):
    """A real job through the real runner: the terminal status and the outbox row land together,
    keyed by run, so a second run is a second event and a re-finalised run is none."""
    store = api.c.app_state.app.store
    pid = _prepared(api, keys, fixtures)
    job = api.job(pid, formats=["docx"])
    assert job["status"] == "completed", job
    rows = store.all("SELECT * FROM messaging_outbox WHERE kind='docs.job_finished'")
    assert len(rows) == 1
    row = rows[0]
    assert row["event_id"] == f"job:{job['id']}:1" and row["object_kind"] == "project" and row["object_id"] == pid
    assert json.loads(row["payload"]) == {"job_id": job["id"], "project_id": pid, "run_no": 1, "status": "completed"}
    assert row["actor_id"] is None, "a machine principal's run names nobody, and is still news to the project's conversation"
    assert row["workspace_id"] == "default"
    from synwevo_core.messaging_bridge import emit_job_finished
    with store.tx() as t:
        assert emit_job_finished(t, job_id=job["id"], project_id=pid, workspace_id="default", run_no=1, status="completed") is False
    assert store.one("SELECT COUNT(*) AS n FROM messaging_outbox WHERE kind='docs.job_finished'")["n"] == 1
