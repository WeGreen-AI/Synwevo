"""Project comments leave one audit row per change, written by the service in the transaction of
the change (admin coverage G26, decision D7): create, edit, resolve, reopen, delete — the comment
and project identifiers only, never the body; a failed audit undoes the deletion."""
from __future__ import annotations

import json

from tests.integration.test_project_comments import build, world  # noqa: F401  (world is a fixture)


def rows(store):
    return [dict(r) for r in store.all("SELECT * FROM audit_events WHERE action LIKE 'docs.comment.%' ORDER BY at, id")]


def test_each_comment_change_leaves_one_row_without_the_body(world):
    w = world
    ann, dan = w["c"]["ann"], w["c"]["dan"]
    pid, _tid, _slot = build(ann)
    body = "合成评论正文：这句话不能出现在审计里"
    created = ann.post(f"/api/projects/{pid}/comments", json={"body": body})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]
    assert ann.patch(f"/api/comments/{cid}", json={"body": body + "（已修改）"}).status_code == 200
    assert ann.post(f"/api/comments/{cid}/resolve").status_code == 200
    assert ann.post(f"/api/comments/{cid}/reopen").status_code == 200
    assert dan.delete(f"/api/comments/{cid}").status_code == 200
    got = rows(w["api"].state.identity.store)
    assert [r["action"] for r in got] == ["docs.comment.create", "docs.comment.edit", "docs.comment.resolve", "docs.comment.reopen", "docs.comment.delete"]
    for r in got:
        assert (r["actor_kind"], r["workspace_id"], r["object_kind"], r["object_id"]) == ("user", w["wid"], "comment", cid), r
        assert json.loads(r["meta"])["project_id"] == pid and r["request_id"]
        assert "合成评论正文" not in r["meta"] and "已修改" not in r["meta"], "the body never enters the record"
    assert [r["actor_id"] for r in got] == [w["u"]["ann"]["id"]] * 4 + [w["u"]["dan"]["id"]]
    assert json.loads(got[-1]["meta"])["by_administrator"] is True


def test_a_failed_audit_undoes_the_deletion(world, monkeypatch):
    from synwevo_core import auth
    w = world
    ann = w["c"]["ann"]
    pid, _tid, _slot = build(ann)
    cid = ann.post(f"/api/projects/{pid}/comments", json={"body": "合成评论：删除失败时应保留"}).json()["id"]

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit storage failure")
    original = auth.audit_event
    monkeypatch.setattr(auth, "audit_event", fail)
    r = ann.delete(f"/api/comments/{cid}")
    assert r.status_code >= 500 and "synthetic" not in r.text, r.text
    monkeypatch.setattr(auth, "audit_event", original)
    listed = ann.get(f"/api/projects/{pid}/comments")
    assert listed.status_code == 200, listed.text
    assert cid in {c["id"] for c in listed.json()["items"]}, listed.text[:300]
    assert not [r for r in rows(w["api"].state.identity.store) if r["action"] == "docs.comment.delete"]
