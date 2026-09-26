"""The project page as shared work: two people's mapping edits from the same starting point merge
mapping by mapping instead of the second one being refused; a real collision on one mapping is
refused with the ids and can be resolved by choosing a side; every change is recorded to the
person who made it and pushed to whoever has the project open; the project room carries presence
(who, on what) for members and refuses outsiders."""
from __future__ import annotations

import json
import time

import pytest
from pycrdt import YMessageType, create_awareness_message, read_message
from starlette.websockets import WebSocketDisconnect

from synwevo_docs.collab import decode_awareness
from synwevo_docs.services import App

from .test_collab import PW, Peer

DOC = {"body": {"type": "doc", "content": [{"type": "paragraph", "content": [
    {"type": "text", "text": "客户："}, {"type": "field", "attrs": {"id": "f_name", "name": "客户", "required": True}},
    {"type": "text", "text": " 金额："}, {"type": "field", "attrs": {"id": "f_amount", "name": "金额", "required": False}},
    {"type": "text", "text": " 备注："}, {"type": "field", "attrs": {"id": "f_note", "name": "备注", "required": False}}]}]}}
CSV = "姓名,金额,备注\n张三,100,a\n李四,200,b\n".encode("utf-8")


# ------------------------------------------------------------------ the merge itself, no server
def m(mid, target, header, no, **extra):
    return {"id": mid, "no": no, "type": "single", "enabled": True, "sources": [{"kind": "column", "sheet": "S", "header": header}], "target": {"binding": target}, **extra}


def test_disjoint_edits_merge_and_same_edits_agree():
    base = [m("a", "t1", "h1", 1), m("b", "t2", "h2", 2)]
    current = [m("a", "t1", "h1x", 1), m("b", "t2", "h2", 2)]              # someone changed a
    incoming = [m("a", "t1", "h1", 1), m("b", "t2", "h2y", 2)]             # the client changed b
    merged, conflicts = App.merge_mappings(base, current, incoming)
    assert conflicts == []
    assert [(x["id"], x["sources"][0]["header"]) for x in merged] == [("a", "h1x"), ("b", "h2y")]
    merged, conflicts = App.merge_mappings(base, current, current)          # the same change from both
    assert conflicts == [] and merged[0]["sources"][0]["header"] == "h1x"


def test_additions_deletions_and_collisions():
    base = [m("a", "t1", "h1", 1)]
    current = [m("a", "t1", "h1", 1), m("c", "t3", "h3", 2)]               # someone added c
    incoming = [m("a", "t1", "h1", 1), m("d", "t4", "h4", 2)]              # the client added d
    merged, conflicts = App.merge_mappings(base, current, incoming)
    assert conflicts == [] and [x["id"] for x in merged] == ["a", "c", "d"]
    # the client deleted a, nobody else touched it → gone; someone else deleted, client untouched → gone
    assert [x["id"] for x in App.merge_mappings(base, current, [m("d", "t4", "h4", 2)])[0]] == ["c", "d"]
    assert [x["id"] for x in App.merge_mappings(base, [], base)[0]] == []
    # both changed a differently → conflict; the client's version wins only when forced
    both = App.merge_mappings(base, [m("a", "t1", "h1x", 1)], [m("a", "t1", "h1y", 1)])
    assert both[1] == [{"id": "a", "no": 1, "reason": "both_changed"}]
    forced, conflicts = App.merge_mappings(base, [m("a", "t1", "h1x", 1)], [m("a", "t1", "h1y", 1)], {"a"})
    assert conflicts == [] and forced[0]["sources"][0]["header"] == "h1y"
    # deleted by one side, edited by the other → conflict either way
    assert App.merge_mappings(base, [], [m("a", "t1", "h1y", 1)])[1][0]["reason"] == "deleted_by_other"
    assert App.merge_mappings(base, [m("a", "t1", "h1x", 1)], [])[1][0]["reason"] == "deleted_by_you"
    # a saved-at timestamp is not a change
    assert App.merge_mappings(base, [dict(base[0], updated_at=5.0)], [dict(base[0], updated_at=9.0)])[1] == []


# ------------------------------------------------------------------ over HTTP, with real sessions
@pytest.fixture()
def world(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.identity import Identity
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
    from synwevo_docs.api.app import create_app

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_COLLAB_RECHECK_S", "0.2")
    app = create_app(str(tmp_path / "data"), start_jobs=False)
    store = app.state.app.store
    ident = Identity(store)
    bootstrap_docs_app(store, ident)
    sessions = Sessions(store)

    def person(email, wid=None, role=None):
        u = ident.create_user(email, PW, status="active", display_name=email.split("@")[0].title())
        if wid and role:
            ident.add_member(wid, u["id"], role)
        return u

    owner = person("owner@example.com")
    wid = ident.workspaces_for(owner["id"])[0]["id"]
    editor = person("editor@example.com", wid, "editor")
    viewer = person("viewer@example.com", wid, "viewer")
    stranger = person("stranger@example.com")
    other_wid = ident.workspaces_for(stranger["id"])[0]["id"]

    def client_for(user, workspace=None):
        raw, _ = sessions.create(user["id"], workspace_id=workspace or wid, amr="password")
        c = TestClient(app)
        c.cookies.set(COOKIE_NAME, raw)
        c.cookies.set(CSRF_COOKIE, "t")
        c.headers[CSRF_HEADER] = "t"
        c.user = user
        return c

    with TestClient(app):
        co = client_for(owner)
        pid = co.post("/api/projects", json={"name": "协同项目"}).json()["id"]
        r = co.post(f"/api/projects/{pid}/sources", files={"files": ("客户.csv", CSV, "text/csv")})
        assert r.status_code in (200, 201), r.text
        for _ in range(100):
            if all(s["status"] == "ready" for s in co.get(f"/api/projects/{pid}/sources").json()):
                break
            time.sleep(0.1)
        t = co.post("/api/templates/builtin", json={"kind": "builtin_doc", "editor": DOC, "name": "模板", "project_id": pid})
        assert t.status_code == 200, t.text
        bind = {s["name"]: s["binding"] for s in t.json()["version"]["slots"]}
        slot_ids = {s["name"]: s["id"] for s in t.json()["version"]["slots"]}
        r = co.patch(f"/api/projects/{pid}", json={"record_rule": {"mode": "per_row", "table": {"sheet": "客户", "header_row": 0}}})
        assert r.status_code == 200, r.text
        yield {"app": app, "store": store, "ident": ident, "wid": wid, "other_wid": other_wid, "pid": pid, "tid": t.json()["id"], "bind": bind, "slot_ids": slot_ids,
               "owner": owner, "editor": editor, "viewer": viewer, "stranger": stranger, "client_for": client_for, "co": co}


def mapping(header, binding):
    return {"type": "single", "sources": [{"kind": "column", "sheet": "客户", "header": header}], "target": {"binding": binding}}


def test_two_people_s_edits_from_the_same_revision_both_land(world):
    co, pid, bind = world["co"], world["pid"], world["bind"]
    ce = world["client_for"](world["editor"])
    r = co.put(f"/api/projects/{pid}/mappings", json={"mappings": [mapping("姓名", bind["客户"]), mapping("金额", bind["金额"])], "note": "初始映射"})
    assert r.status_code == 200, r.text
    rev1, ms = r.json()["rev"], r.json()["mappings"]
    a, b = ms
    # the owner changes mapping a; the editor, still on rev1, changes mapping b and adds a third
    r = co.put(f"/api/projects/{pid}/mappings", json={"mappings": [dict(a, enabled=False), b], "note": "停用 a", "base_rev": rev1})
    assert r.status_code == 200 and r.json()["rev"] == rev1 + 1
    r = ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [a, dict(b, sources=[{"kind": "column", "sheet": "客户", "header": "备注"}]), mapping("备注", bind["备注"])],
                                                    "note": "改 b 加 c", "base_rev": rev1})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["rev"] == rev1 + 2 and out["merged_with"] == rev1 + 1 and "已与第" in out["note"]
    by_id = {x["id"]: x for x in out["mappings"]}
    assert by_id[a["id"]]["enabled"] is False, "the owner's change survived the editor's save"
    assert by_id[b["id"]]["sources"][0]["header"] == "备注", "the editor's change landed"
    assert len(out["mappings"]) == 3
    # the activity feed says who did what, mapping by mapping
    items = ce.get(f"/api/projects/{pid}/activity").json()["items"]
    assert items[0]["kind"] == "mappings.save" and items[0]["by"]["name"] == "Editor" and items[0]["detail"]["merged_with"] == rev1 + 1
    assert [c["after"]["sources"] for c in items[0]["detail"]["changed"]] == [["备注"]] and len(items[0]["detail"]["added"]) == 1
    assert items[1]["by"]["name"] == "Owner" and items[1]["detail"]["changed"][0]["after"]["enabled"] is False
    assert items[-1]["kind"] in ("project.update", "source.add", "template.save", "mappings.save")


def test_a_collision_on_one_mapping_is_refused_with_the_ids_and_resolved_by_choosing(world):
    co, pid, bind = world["co"], world["pid"], world["bind"]
    ce = world["client_for"](world["editor"])
    r = co.put(f"/api/projects/{pid}/mappings", json={"mappings": [mapping("姓名", bind["客户"])]})
    rev1, a = r.json()["rev"], r.json()["mappings"][0]
    assert co.put(f"/api/projects/{pid}/mappings", json={"mappings": [dict(a, sources=[{"kind": "column", "sheet": "客户", "header": "金额"}])], "base_rev": rev1}).status_code == 200
    r = ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [dict(a, sources=[{"kind": "column", "sheet": "客户", "header": "备注"}])], "base_rev": rev1})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "stale" and body["detail"]["rev"] == rev1 + 1 and body["detail"]["conflicts"] == [{"id": a["id"], "no": 1, "reason": "both_changed"}]
    assert co.get(f"/api/projects/{pid}/mappings").json()["mappings"][0]["sources"][0]["header"] == "金额", "nothing was written"
    # choosing their own version for that mapping
    r = ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [dict(a, sources=[{"kind": "column", "sheet": "客户", "header": "备注"}])], "base_rev": rev1, "force_ids": [a["id"]]})
    assert r.status_code == 200 and r.json()["mappings"][0]["sources"][0]["header"] == "备注"
    # a base revision that never existed is still simply stale
    r = ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [a], "base_rev": 999})
    assert r.status_code == 409 and "conflicts" not in (r.json()["detail"] or {})
    assert ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [a], "force_ids": "x"}).status_code == 400


def test_the_project_room_carries_presence_and_live_change_frames(world):
    pid, tid = world["pid"], world["tid"]
    a = Peer(world["client_for"](world["owner"]), pid, origin="http://testserver", path="projects")
    a.hello = a.connect()
    assert a.hello["kind"] == "project" and a.hello["readonly"] is False
    v = Peer(world["client_for"](world["viewer"]), pid, origin="http://testserver", path="projects")
    v.hello = v.connect()
    assert v.hello["kind"] == "project" and v.hello["readonly"] is True and [p["name"] for p in v.hello["peers"]] == ["Owner"]
    assert a.recv_meta("joined")["user"]["name"] == "Viewer"
    # what the owner is on travels as awareness
    a.send_awareness(501, 1, {"user": {"id": world["owner"]["id"], "name": "Owner"}, "focus": {"kind": "mapping", "id": "m1"}})
    frame = v.recv(want="awareness")
    entries = decode_awareness(read_message(frame[1:]))
    assert entries[0][2]["focus"] == {"kind": "mapping", "id": "m1"}
    # a save by the owner over HTTP reaches the viewer's open page as a change frame, attributed
    ce = world["client_for"](world["owner"])
    r = ce.put(f"/api/projects/{pid}/mappings", json={"mappings": [mapping("姓名", world["bind"]["客户"])], "note": "第一条"})
    assert r.status_code == 200
    change = v.recv_meta("change")
    assert change["kind"] == "mappings.save" and change["by"]["name"] == "Owner" and change["detail"]["rev"] == r.json()["rev"] and len(change["detail"]["added"]) == 1
    # a template field change reaches the project room too
    r = ce.patch(f"/api/templates/{tid}/slots/{world['slot_ids']['客户']}", json={"required": False})
    assert r.status_code == 200, r.text
    change = v.recv_meta("change")
    assert change["kind"] == "template.save" and change["by"]["name"] == "Owner" and change["detail"]["template_id"] == tid
    # a source added over HTTP is announced as well
    r = ce.post(f"/api/projects/{pid}/sources", files={"files": ("更多.csv", CSV, "text/csv")})
    assert r.status_code in (200, 201), r.text
    change = v.recv_meta("change", tries=30)
    assert change["kind"] == "source.add" and change["detail"]["name"] == "更多.csv"
    # document frames mean nothing here and are ignored without a refusal notice
    a.ws.send_bytes(bytes([YMessageType.SYNC, 2]) + b"\x02\x00\x00")
    a.send_awareness(501, 2, None)
    assert v.recv(want="awareness") is not None
    assert not any(x.get("type") == "refused" for x in a.meta)
    a.close(); v.close()


def test_outsiders_cannot_join_a_project_room(world):
    pid = world["pid"]
    with pytest.raises(WebSocketDisconnect) as x:
        world["client_for"](world["stranger"], world["other_wid"]).websocket_connect(f"/api/collab/projects/{pid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4404
    from fastapi.testclient import TestClient
    with pytest.raises(WebSocketDisconnect) as x:
        TestClient(world["app"]).websocket_connect(f"/api/collab/projects/{pid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4401
    assert world["client_for"](world["stranger"], world["other_wid"]).get(f"/api/projects/{pid}/activity").status_code == 404
