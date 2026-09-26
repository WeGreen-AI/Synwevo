"""Comments and mentions on a Docs project, on the composed server: who may comment, who is
told (mentioned members and earlier participants, never the author, never someone who cannot
open the project), anchors that go stale when the field or mapping they point at is gone,
resolve/reopen rights, and edit/delete rights."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

PW = "harbor lights guide the evening ferry home"
CSRF, WS = "X-Synwevo-CSRF", "X-Synwevo-Workspace"


def _docx() -> bytes:
    import docx
    d = docx.Document(); d.add_paragraph("尊敬的示例客户名称：您好。")
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()


def _walk(blocks):
    for b in blocks:
        if b["t"] == "p":
            yield b
        else:
            for row in b["rows"]:
                for cell in row["cells"]:
                    yield from _walk(cell["blocks"])


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for k, v in {"UI_AUTH": "session", "COOKIE_SECURE": "0", "DATA_DIR": str(tmp_path / "d"), "MAIL_MODE": "file", "MAIL_DIR": str(tmp_path / "mail"),
                 "MAIL_FROM": "synwevo@example.test", "PUBLIC_URL": "http://testserver", "ROLE": "all", "APP_URL": "http://docs.testserver"}.items():
        monkeypatch.setenv("SYNWEVO_" + k, v)
    from synwevo_server.app import create_app
    api = create_app(str(tmp_path / "d"), role="all", start_jobs=False)
    ident = api.state.identity
    users = {}
    for key, name in (("ann", "Ann Editor"), ("bob", "Bob Viewer"), ("cid", "Cid Editor"), ("dan", "Dan Admin"), ("eve", "Eve Outside")):
        u = ident.create_user(f"{key}@example.test", PW, status="active", display_name=name, with_personal_workspace=True)
        ident.store.execute("UPDATE users SET email_verified_at=1 WHERE id=?", (u["id"],))
        users[key] = u
    from synwevo_core.organizations import Organizations
    created = Organizations(ident.store, ident).create(users["dan"]["id"], "Comment Org", "Team", "fixture-idem-comment-org-0001")
    wid = created["workspace"]["id"]
    for key, role in (("ann", "editor"), ("bob", "viewer"), ("cid", "editor")):
        ident.store.execute("INSERT INTO organization_memberships (organization_id,user_id,role,created_at,updated_at,created_by) VALUES (?,?,'member',1,1,?)",
                            (created["organization"]["id"], users[key]["id"], users["dan"]["id"]))
        ident.store.execute("INSERT INTO memberships (workspace_id,user_id,role,can_export,created_at,updated_at,created_by) VALUES (?,?,?,1,1,1,?)",
                            (wid, users[key]["id"], role, users["dan"]["id"]))
    clients = {}
    for key in users:
        c = TestClient(api)
        assert c.post("/api/auth/login", json={"email": f"{key}@example.test", "password": PW}).status_code == 200
        c.headers.update({CSRF: c.cookies.get("dw_csrf"), WS: wid})
        clients[key] = c
    try:
        yield {"api": api, "c": clients, "u": users, "wid": wid}
    finally:
        for c in clients.values():
            c.close()


def build(c) -> tuple[str, str, str]:
    pid = c.post("/api/projects", json={"name": "评论项目"}).json()["id"]
    t = c.post("/api/templates/upload", files={"file": ("模板.docx", _docx())}, data={"project_id": pid}).json()
    view = c.get(f"/api/templates/{t['id']}/view").json()
    block = next(b for part in view["doc"]["parts"] for b in _walk(part["blocks"]) if "示例客户名称" in b["text"])
    s = block["text"].index("示例客户名称")
    slot = c.post(f"/api/templates/{t['id']}/slots", json={"kind": "text", "part": block["part"], "path": block["path"], "start": s, "end": s + 6, "name": "客户名称"}).json()["created_slot"]
    return pid, t["id"], slot["id"]


def test_comments_mentions_anchors_and_rights(world):
    C, U, n = world["c"], world["u"], world["api"].state.notifier
    ann, bob, cid, dan, eve = (C[k] for k in ("ann", "bob", "cid", "dan", "eve"))
    pid, tid, slot_id = build(ann)
    members = ann.get(f"/api/projects/{pid}/comment-members").json()["items"]
    assert {m["name"] for m in members} == {"Ann Editor", "Bob Viewer", "Cid Editor", "Dan Admin"} and all(set(m) == {"user_id", "name"} for m in members)
    # ann comments on the field and mentions bob (a viewer) and eve (not a member: dropped silently)
    r = ann.post(f"/api/projects/{pid}/comments", json={"body": "@Bob Viewer 这个字段取哪一列？", "anchor": {"kind": "slot", "id": slot_id},
                                                        "mentions": [U["bob"]["id"], U["eve"]["id"], U["ann"]["id"]]})
    assert r.status_code == 201, r.text
    c1 = r.json()
    assert c1["author"]["name"] == "Ann Editor" and c1["mentions"] == [U["bob"]["id"]] and c1["anchor"] == {"kind": "slot", "id": slot_id, "label": "客户名称", "template_id": tid, "template_version": c1["anchor"]["template_version"], "valid": True, "reason": None}
    assert [x["kind"] for x in n.list(U["bob"]["id"], locale="en")["items"]] == ["docs.mention"]
    item = n.list(U["bob"]["id"], locale="en")["items"][0]
    assert item["title"] == "Ann Editor mentioned you in a comment on “评论项目”" and item["href"] == f"http://docs.testserver/p/{pid}?comment={c1['id']}"
    assert n.list(U["eve"]["id"])["items"] == [] and n.list(U["ann"]["id"])["items"] == []
    # a viewer may reply; the earlier participant (ann) is told, bob himself is not
    r = bob.post(f"/api/projects/{pid}/comments", json={"body": "取「姓名」列", "parent_id": c1["id"]})
    assert r.status_code == 201, r.text
    assert [x["kind"] for x in n.list(U["ann"]["id"])["items"]] == ["docs.comment"]
    assert len(n.list(U["bob"]["id"])["items"]) == 1
    # an outsider is stopped at the workspace gate (403 for a workspace she is not in); a member
    # of another workspace naming this project would get the project's 404
    assert eve.get(f"/api/projects/{pid}/comments").status_code == 403
    assert eve.post(f"/api/projects/{pid}/comments", json={"body": "hi"}).status_code == 403
    eve.headers.update({WS: world["api"].state.identity.ensure_personal_workspace(U["eve"]["id"])["id"]})
    assert eve.get(f"/api/projects/{pid}/comments").status_code == 404
    assert eve.post(f"/api/projects/{pid}/comments", json={"body": "hi"}).status_code == 404
    # rights: bob cannot edit ann's comment; ann can; a viewer cannot resolve someone else's thread, an editor can
    assert bob.patch(f"/api/comments/{c1['id']}", json={"body": "x"}).status_code == 403
    assert ann.patch(f"/api/comments/{c1['id']}", json={"body": "@Bob Viewer 这个字段取哪一列？（改）", "mentions": [U["bob"]["id"]]}).json()["edited_at"]
    assert len([x for x in n.list(U["bob"]["id"])["items"] if x["kind"] == "docs.mention"]) == 1, "editing does not re-mention"
    assert bob.post(f"/api/comments/{c1['id']}/resolve").status_code == 403
    assert cid.post(f"/api/comments/{c1['id']}/resolve").json()["resolved_by"]["name"] == "Cid Editor"
    assert ann.get(f"/api/projects/{pid}/comments").json()["items"] == []
    assert len(ann.get(f"/api/projects/{pid}/comments?include_resolved=1").json()["items"]) == 2
    assert cid.post(f"/api/comments/{c1['id']}/resolve").status_code == 409
    assert ann.post(f"/api/comments/{c1['id']}/reopen").status_code == 200
    # anchors are judged at read time: delete the field → the comment says so and keeps its label
    assert ann.delete(f"/api/templates/{tid}/slots/{slot_id}").status_code == 200
    got = ann.get(f"/api/projects/{pid}/comments").json()["items"][0]
    assert got["anchor"]["valid"] is False and got["anchor"]["reason"] == "slot_gone" and got["anchor"]["label"] == "客户名称"
    # bad anchors and empty bodies are refused; a reply to a reply is refused
    assert ann.post(f"/api/projects/{pid}/comments", json={"body": "x", "anchor": {"kind": "slot", "id": "s_nope"}}).status_code == 400
    assert ann.post(f"/api/projects/{pid}/comments", json={"body": "   \x00 "}).status_code == 400
    reply_id = ann.get(f"/api/projects/{pid}/comments").json()["items"][1]["id"]
    assert ann.post(f"/api/projects/{pid}/comments", json={"body": "x", "parent_id": reply_id}).status_code == 400
    # delete: bob cannot delete ann's; dan (workspace owner) can; replies go with the thread
    assert bob.delete(f"/api/comments/{c1['id']}").status_code == 403
    assert dan.delete(f"/api/comments/{c1['id']}").json()["ok"]
    assert ann.get(f"/api/projects/{pid}/comments?include_resolved=1").json()["items"] == []
