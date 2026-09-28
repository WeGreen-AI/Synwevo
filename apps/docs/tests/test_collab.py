"""Live co-editing of a built-in document template over the WebSocket room, with real sessions:
two editors converge, a viewer follows but cannot write, outsiders and other sites are refused,
the live document survives everyone leaving, a save turns it into a version, a restore replaces it
for everyone, changes are attributed, and a role change while connected takes effect."""
from __future__ import annotations

import json
import time

import pytest
from pycrdt import (Doc, XmlElement, XmlFragment, XmlText, YMessageType, YSyncMessageType, create_awareness_message,
                    create_sync_message, create_update_message, handle_sync_message, read_message, write_message)
from starlette.websockets import WebSocketDisconnect

from synwevo_docs.collab import MESSAGE_META, decode_awareness
from synwevo_docs.collab_pm import read as read_doc

PW = "a-sufficiently-long-passphrase"
DOC = {"page": {"size": "A4", "orientation": "portrait", "margins": {"top": 25.4, "bottom": 25.4, "left": 31.8, "right": 31.8}},
       "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "客户："},
                                                                            {"type": "field", "attrs": {"id": "customer", "name": "客户", "required": True}}]}]},
       "header": {"type": "doc", "content": [{"type": "paragraph"}]}, "footer": {"type": "doc", "content": [{"type": "paragraph"}]}}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """One workspace with an owner, an editor and a viewer; one built-in document template; a
    second workspace whose owner is a stranger. Sessions are real cookies."""
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

    with TestClient(app) as lifespan:  # noqa: F841 — keeps the loop alive for the sockets below
        co = client_for(owner)
        r = co.post("/api/templates/builtin", json={"kind": "builtin_doc", "editor": DOC, "name": "协同模板"})
        assert r.status_code == 200, r.text
        tid = r.json()["id"]
        yield {"app": app, "store": store, "ident": ident, "sessions": sessions, "wid": wid, "other_wid": other_wid, "tid": tid,
               "owner": owner, "editor": editor, "viewer": viewer, "stranger": stranger, "client_for": client_for, "co": co}


class Peer:
    """A browser's share of the protocol: a local document kept in sync through the socket."""

    def __init__(self, client, tid, *, origin="http://testserver", workspace=None, path="templates", doc: Doc | None = None,
                 epoch: str | None = None):
        # `epoch`: what a browser tab sends when it reconnects with the document it already holds (CI-115)
        q = [f"workspace={workspace}"] if workspace else []
        if epoch:
            q.append(f"epoch={epoch}")
        url = f"/api/collab/{path}/{tid}" + (f"?{'&'.join(q)}" if q else "")
        self.cm = client.websocket_connect(url, headers={"origin": origin})
        self.ws = self.cm.__enter__()
        self.doc = doc if doc is not None else Doc()          # a browser reconnecting brings its own document along
        self.updates: list[bytes] = []
        self.doc.observe(lambda ev: self.updates.append(ev.update))
        self.meta: list[dict] = []
        self.awareness: list[bytes] = []
        self.synced = False

    def close(self):
        self.cm.__exit__(None, None, None)

    def recv(self, *, want: str | None = None, tries: int = 12):
        """Read frames until one of ``want`` (meta/sync/update/awareness) arrives; frames of the
        other kinds are handled and kept along the way."""
        for _ in range(tries):
            data = self.ws.receive_bytes()
            kind = data[0]
            if kind == MESSAGE_META:
                payload = json.loads(read_message(data[1:]))
                self.meta.append(payload)
                if want in (None, "meta"):
                    return payload
            elif kind == YMessageType.SYNC:
                sub = data[1]
                if sub == YSyncMessageType.SYNC_STEP1:
                    reply = handle_sync_message(data[1:], self.doc)
                    self.ws.send_bytes(reply)
                    if want in (None, "sync"):
                        return "step1"
                elif sub == YSyncMessageType.SYNC_STEP2:
                    handle_sync_message(data[1:], self.doc)
                    self.synced = True
                    if want in (None, "sync"):
                        return "step2"
                else:
                    handle_sync_message(data[1:], self.doc)
                    if want in (None, "update"):
                        return "update"
            elif kind == YMessageType.AWARENESS:
                self.awareness.append(data)
                if want in (None, "awareness"):
                    return data
        raise AssertionError(f"no {want} frame within {tries} frames; meta so far: {self.meta}")

    def recv_meta(self, kind: str, tries: int = 12) -> dict:
        """The next server frame of this kind; other room frames in between are kept."""
        for _ in range(tries):
            payload = self.recv(want="meta", tries=tries)
            if payload.get("type") == kind:
                return payload
        raise AssertionError(f"no {kind} frame; meta so far: {self.meta}")

    def connect(self):
        hello = self.recv(want="meta")
        assert hello["type"] == "hello", hello
        self.recv(want="sync")                                 # the server's step 1 (answered)
        self.ws.send_bytes(create_sync_message(self.doc))     # ask for what we miss
        while not self.synced:
            self.recv(want="sync")
        return hello

    def body(self) -> XmlFragment:
        return self.doc.get("body", type=XmlFragment)

    def edit(self, fn):
        """Change the local document and send the resulting update, like a keystroke."""
        self.updates.clear()
        with self.doc.transaction():
            fn()
        assert self.updates, "the edit produced no update"
        for u in self.updates:
            self.ws.send_bytes(create_update_message(u))

    def append_text(self, text: str, block: int = 0):
        def fn():
            p = self.body().children[block]
            t = p.children[0] if len(p.children) and isinstance(p.children[0], XmlText) else p.children.insert(0, XmlText())
            t.insert(len(t), text)
        self.edit(fn)

    def append_text_offline(self, text: str, block: int = 0):
        """Typed while the socket is gone: the local document changes, nothing is sent; the next
        connection's sync step carries it (what y-websocket does after a reconnect)."""
        with self.doc.transaction():
            p = self.body().children[block]
            t = p.children[0] if len(p.children) and isinstance(p.children[0], XmlText) else p.children.insert(0, XmlText())
            t.insert(len(t), text)

    def add_paragraph(self, text: str):
        def fn():
            p = self.body().children.append(XmlElement("paragraph"))
            p.children.append(XmlText(text))
        self.edit(fn)

    def texts(self) -> list[str]:
        out = []
        for child in self.body().children:
            out.append("".join(str(c) if isinstance(c, XmlText) else "" for c in child.children))
        return out

    def send_awareness(self, client_id: int, clock: int, state: dict | None):
        from pycrdt._sync import Encoder
        enc = Encoder()
        enc.write_var_uint(1)
        enc.write_var_uint(client_id)
        enc.write_var_uint(clock)
        enc.write_var_string(json.dumps(state) if state is not None else "")
        self.ws.send_bytes(create_awareness_message(enc.to_bytes()))


def _connect(world, who, **kw) -> Peer:
    p = Peer(world["client_for"](world[who]), world["tid"], **kw)
    p.hello = p.connect()
    return p


# ------------------------------------------------------------------ two editors converge
def test_two_editors_see_each_other_s_changes_and_who_is_there(world):
    a = _connect(world, "owner")
    assert a.hello["readonly"] is False and a.hello["head"] == 1 and a.hello["dirty"] is False and a.hello["peers"] == []
    assert a.hello["user"]["name"] == "Owner" and 1 <= a.hello["user"]["color"] <= 8
    assert a.texts() == ["客户："]                                 # seeded from the saved version
    b = _connect(world, "editor")
    assert [p["name"] for p in b.hello["peers"]] == ["Owner"]
    joined = a.recv(want="meta")
    assert joined["type"] == "joined" and joined["user"]["name"] == "Editor" and joined["online"] == 2

    a.append_text("张三")
    assert b.recv(want="update") == "update" and b.texts() == ["客户：张三"]
    b.add_paragraph("第二段")
    assert a.recv(want="update") == "update" and a.texts() == ["客户：张三", "第二段"]
    # the room told everyone it now holds unsaved changes (the frame may already have been read past)
    states = [m for m in a.meta if m["type"] == "state"] or [a.recv_meta("state")]
    assert states[-1]["dirty"] is True

    # awareness (caret / name) travels to everyone and only for the identity the connection proved
    b.send_awareness(777, 1, {"user": {"id": world["stranger"]["id"], "name": "Impostor"}, "cursor": None})
    b.send_awareness(778, 1, {"user": {"id": world["editor"]["id"], "name": "Editor"}, "cursor": {"anchor": 1}})
    frame = a.recv(want="awareness")
    entries = decode_awareness(read_message(frame[1:]))
    assert [e[0] for e in entries] == [778] and entries[0][2]["user"]["name"] == "Editor"

    b.close()
    left = a.recv(want="meta")
    assert left["type"] == "left" and left["online"] == 1
    a.close()


# ------------------------------------------------------------------ viewers follow, cannot write
def test_a_viewer_follows_but_their_writes_are_dropped(world):
    v = _connect(world, "viewer")
    assert v.hello["readonly"] is True
    e = _connect(world, "editor")
    v.recv(want="meta")                                            # editor joined
    e.append_text("A")
    assert v.recv(want="update") == "update" and v.texts() == ["客户：A"]
    v.append_text("HACK")                                          # locally applied, refused by the server
    assert v.recv_meta("refused") == {"type": "refused", "reason": "read_only"}
    e.add_paragraph("B")                                           # the editor's next change still flows
    assert v.recv(want="update") == "update"
    v.close(); e.close()
    fresh = _connect(world, "owner")
    assert fresh.texts() == ["客户：A", "B"], "the viewer's text never reached the shared document"
    fresh.close()


# ------------------------------------------------------------------ refusals
def test_no_session_other_site_other_workspace_and_wrong_kind_are_refused(world):
    from fastapi.testclient import TestClient
    tid = world["tid"]
    with pytest.raises(WebSocketDisconnect) as x:
        TestClient(world["app"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as x:
        world["client_for"](world["editor"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "https://evil.example"}).__enter__()
    assert x.value.code == 4403 and x.value.reason == "origin"
    with pytest.raises(WebSocketDisconnect) as x:
        world["client_for"](world["editor"]).websocket_connect(f"/api/collab/templates/{tid}").__enter__()   # no Origin at all
    assert x.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as x:                  # a member of another workspace: the template does not exist for them
        world["client_for"](world["stranger"], world["other_wid"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4404
    with pytest.raises(WebSocketDisconnect) as x:                  # a per-tab workspace this person is not in
        world["client_for"](world["stranger"], world["other_wid"]).websocket_connect(f"/api/collab/templates/{tid}?workspace={world['wid']}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4403
    r = world["co"].post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": {"sheets": [{"name": "S", "cells": {"A1": {"v": "x"}}, "fields": []}]}})
    assert r.status_code == 200, r.text
    # a table template has a presence-only room (cells merge on save), never a shared document
    p = Peer(world["client_for"](world["editor"]), r.json()["id"])
    hello = p.connect()
    assert hello["kind"] == "template" and hello["writable"] is False
    p.close()
    # an uploaded template has no editor at all: no room
    up = world["co"].post("/api/templates/upload", files={"file": ("t.docx", b"PK\x03\x04 not really", "application/octet-stream")})
    if up.status_code == 200:
        with pytest.raises(WebSocketDisconnect) as x:
            world["client_for"](world["editor"]).websocket_connect(f"/api/collab/templates/{up.json()['id']}", headers={"origin": "http://testserver"}).__enter__()
        assert x.value.code == 4400 and x.value.reason == "not_builtin_doc"


# ------------------------------------------------------------------ persistence, save, restore
def test_live_edits_survive_everyone_leaving_and_a_save_makes_them_a_version(world):
    store, tid = world["store"], world["tid"]
    a = _connect(world, "editor")
    a.append_text("未保存")
    a.close()                                                      # the last one out persists the live document
    row = store.one("SELECT head, dirty FROM collab_docs WHERE room=?", (f"template:{tid}",))
    assert row and row["head"] == 1 and row["dirty"] == 1
    b = _connect(world, "owner")
    assert b.hello["dirty"] is True and b.texts() == ["客户：未保存"]
    # saving from the editor: the live document becomes v2 and the room is clean
    editor = {**DOC, **read_doc(b.doc)}
    c = world["client_for"](world["owner"])
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": editor, "base_version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["version"]["no"] == 2 and r.json()["version"]["slots"][0]["name"] == "客户"
    head = b.recv_meta("head")
    assert head["head"] == 2 and head["dirty"] is False and head["reason"] == "save" and head["by"] == "Owner"
    assert b.texts() == ["客户：未保存"], "a collaborator's save is a snapshot: the live document is not touched"
    # restoring v1 replaces the live document for everyone in the room
    r = c.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2})
    assert r.status_code == 200, r.text
    assert b.recv(want="update") == "update" and b.texts() == ["客户："]
    head = b.recv_meta("head")
    assert head["head"] == 3 and head["reason"] == "restore"
    b.close()
    row = store.one("SELECT head, dirty FROM collab_docs WHERE room=?", (f"template:{tid}",))
    assert row["head"] == 3 and row["dirty"] == 0
    # a save from outside the room (nobody connected) makes the next opener start from the head
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "机器写入"}]}]}}, "base_version": 3})
    assert r.status_code == 200, r.text
    assert store.one("SELECT 1 AS x FROM collab_docs WHERE room=?", (f"template:{tid}",)) is None
    d = _connect(world, "editor")
    assert d.texts() == ["机器写入"] and d.hello["head"] == 4 and d.hello["dirty"] is False
    d.close()


def test_a_save_from_outside_the_room_replaces_the_live_document_while_people_are_in_it(world):
    tid = world["tid"]
    a = _connect(world, "editor")
    a.append_text("正在输入")
    a.recv(want="meta")                                            # dirty
    # the owner is not connected: their save (e.g. through a script) would replace what the editor
    # is typing — refused while it is unsaved (CI-113, the rule CI-49 set for restore), unless told
    outside = {"editor": {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "外部版本"}]}]}}, "base_version": 1}
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin", json=outside)
    assert r.status_code == 409 and r.json()["error"] == "live_edits" and r.json()["detail"]["online"] == ["Editor"], r.text
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin", json={**outside, "discard_live": True})
    assert r.status_code == 200, r.text
    assert a.recv(want="update") == "update" and a.texts() == ["外部版本"]
    head = a.recv_meta("head")
    assert head["head"] == 2 and head["by"] == "Owner"
    changes = world["client_for"](world["viewer"]).get(f"/api/templates/{tid}/collab/changes").json()
    assert changes["items"][0]["kind"] == "replace" and changes["items"][0]["user"]["name"] == "Owner"
    a.close()


def _changes_when(world, tid: str, ready, *, timeout_s: float = 5.0) -> list[dict]:
    """The change log once `ready(rows)` holds (or after `timeout_s`, so a wrong expectation still
    fails on the rows themselves rather than on a sleep that a loaded machine outgrows)."""
    reader = world["client_for"](world["viewer"] if "viewer" in world else world["owner"])
    deadline = time.time() + timeout_s
    rows: list[dict] = []
    while True:
        rows = reader.get(f"/api/templates/{tid}/collab/changes").json()["items"]
        if ready(rows) or time.time() > deadline:
            return rows
        time.sleep(0.05)


# ------------------------------------------------------------------ attribution
def test_changes_are_attributed_per_block_and_consecutive_typing_coalesces(world):
    tid = world["tid"]
    a = _connect(world, "owner")
    b = _connect(world, "editor")
    a.recv(want="meta")
    a.append_text("张")
    b.recv(want="update")
    a.append_text("三")
    b.recv(want="update")
    b.add_paragraph("备注")
    a.recv(want="update")
    # Change rows are recorded after each update on a worker thread; under a loaded machine the
    # second keystroke's row can land after a fixed 0.3 s, so wait for the state itself (CI-77).
    items = _changes_when(world, tid, lambda rows: len(rows) == 2 and any(r["count"] == 2 for r in rows))
    kinds = [(i["user"]["name"], i["kind"], i["part"], i["block_no"], i["excerpt"], i["count"]) for i in items]
    assert kinds[0] == ("Editor", "insert", "body", 1, "备注", 1)
    assert kinds[1] == ("Owner", "edit", "body", 0, "客户：张三【客户】", 2), "two keystrokes in one block are one entry"
    assert len(items) == 2
    a.close(); b.close()


# ------------------------------------------------------------------ revocation while connected
def test_a_role_change_and_a_removal_take_effect_on_the_open_connection(world):
    ident, wid = world["ident"], world["wid"]
    e = _connect(world, "editor")
    ident.change_role(wid, world["editor"]["id"], "viewer")
    assert e.recv_meta("role", tries=20) == {"type": "role", "readonly": True, "role": "viewer"}
    e.append_text("late")
    assert e.recv_meta("refused") == {"type": "refused", "reason": "read_only"}
    ident.remove_member(wid, world["editor"]["id"])
    assert e.recv_meta("closed", tries=20) == {"type": "closed", "reason": "membership_ended"}
    with pytest.raises(WebSocketDisconnect) as x:
        e.ws.receive_bytes()
    assert x.value.code == 4403
    e.close()


def test_a_revoked_session_closes_the_connection_without_extending_it(world):
    sessions, store = world["sessions"], world["store"]
    e = _connect(world, "editor")
    sid = store.one("SELECT id FROM sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (world["editor"]["id"],))["id"]
    before = store.one("SELECT * FROM sessions WHERE id=?", (sid,))
    time.sleep(0.5)                                                # two re-checks pass without touching the session
    after = store.one("SELECT * FROM sessions WHERE id=?", (sid,))
    assert {k: v for k, v in after.items() if k != "revoked_at"} == {k: v for k, v in before.items() if k != "revoked_at"}
    sessions.revoke(sid, "test")
    assert e.recv_meta("closed", tries=20) == {"type": "closed", "reason": "session_ended"}
    with pytest.raises(WebSocketDisconnect) as x:
        e.ws.receive_bytes()
    assert x.value.code == 4401
    e.close()


# ------------------------------------------------------------------ a process shutdown writes the live document
def test_a_process_shutdown_writes_the_live_document_while_people_are_still_in_it(world):
    """uvicorn's graceful shutdown runs the lifespan's hooks (CI-48). The room's delayed persist
    lives on the event loop that is about to stop, so the hook writes synchronously what everyone
    in the room has — a restart between two saves loses nothing."""
    store, tid, app = world["store"], world["tid"], world["app"]
    a = _connect(world, "editor")
    b = _connect(world, "owner")
    a.append_text("关机前")
    assert b.recv(want="update") == "update" and b.texts() == ["客户：关机前"]      # the server has applied it
    for hook in list(app.state.shutdown_hooks):                                       # what the lifespan runs at exit
        hook()
    row = store.one("SELECT state, head, dirty FROM collab_docs WHERE room=?", (f"template:{tid}",))
    assert row and row["head"] == 1 and row["dirty"] == 1
    written = Doc()
    written.apply_update(bytes(row["state"]))
    assert "关机前" in json.dumps(read_doc(written), ensure_ascii=False)
    a.close()
    b.close()


# ------------------------------------------------------------------ version operations never discard unsaved live edits unasked
def test_restore_undo_and_redo_refuse_to_throw_away_unsaved_live_edits_unless_told(world):
    """9.13 C: restoring a version replaces the live document for everyone in the room. While it
    carries edits nobody has saved as a version, the operation is refused with 409 ``live_edits``
    naming who is there; ``discard_live`` is the explicit way through, and the room then follows."""
    store, tid = world["store"], world["tid"]
    c = world["client_for"](world["owner"])
    # a second version to have something to undo / restore
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "第二版"}]}]}}, "base_version": 1})
    assert r.status_code == 200 and r.json()["version"]["no"] == 2
    a = _connect(world, "editor")
    b = _connect(world, "owner")
    assert a.texts() == ["第二版"]
    a.append_text("（未保存）")
    assert b.recv(want="update") == "update" and b.texts() == ["第二版（未保存）"]
    # restore, undo, redo: all refused while the room is dirty; the refusal names the editor
    for path, body in (("restore", {"version": 1, "base_head": 2}), ("undo", {"head": 2}), ("redo", {"head": 2})):
        r = c.post(f"/api/templates/{tid}/{path}", json=body)
        assert r.status_code == 409 and r.json()["error"] == "live_edits", (path, r.text)
        assert "Editor" in r.json()["detail"]["online"] and "Owner" in r.json()["detail"]["online"]
        assert r.json()["detail"]["action"] == path
    assert c.get(f"/api/templates/{tid}").json()["head"] == 2, "nothing moved"
    assert store.one("SELECT head FROM templates WHERE id=?", (tid,))["head"] == 2
    # the same live state the dialog reads before asking
    live = c.get(f"/api/templates/{tid}/collab/changes?limit=1").json()
    assert live["dirty"] is True and {p["name"] for p in live["online"]} == {"Editor", "Owner"}
    # told explicitly: the restore happens, becomes v3, and replaces the live document for both
    r = c.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2, "discard_live": True})
    assert r.status_code == 200 and r.json()["head"] == 3, r.text
    assert a.recv(want="update") == "update" and a.texts() == ["客户："]
    head = a.recv_meta("head")
    assert head["reason"] == "restore" and head["head"] == 3 and head["dirty"] is False and head["by"] == "Owner"
    assert b.recv(want="update") == "update" and b.texts() == ["客户："]
    # clean again: an undo needs no flag (back to v2 as the head)
    r = c.post(f"/api/templates/{tid}/undo", json={"head": 3})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    assert a.recv(want="update") == "update" and a.texts() == ["第二版"]
    # the history says what became what (review item C4): the restore copied v1 over v2; the
    # undo moved the head from v3 back to v2 — not only the new version number
    rows = [x for x in c.get(f"/api/templates/{tid}/collab/changes?limit=20").json()["items"] if x["kind"] == "restore"]
    assert [(x["excerpt"], x["before"]) for x in rows][:2] == [("v2", "v3"), ("v1", "v2")], rows
    a.close()
    b.close()


def test_a_persisted_draft_with_nobody_in_the_room_still_guards_a_restore(world):
    """The last person out leaves a dirty draft behind; the guard reads it from the store and
    names the last editor, so a restore from the project page cannot silently drop it."""
    tid = world["tid"]
    c = world["client_for"](world["owner"])
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "第二版"}]}]}}, "base_version": 1})
    assert r.status_code == 200
    a = _connect(world, "editor")
    a.append_text("离开前")
    time.sleep(0.2)                                          # the change log row is written by the room's loop
    a.close()                                                # persists the dirty draft
    r = c.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2})
    assert r.status_code == 409 and r.json()["error"] == "live_edits", r.text
    assert r.json()["detail"]["online"] == [] and r.json()["detail"]["last_by"] == "Editor"
    r = c.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2, "discard_live": True})
    assert r.status_code == 200 and r.json()["head"] == 3
    d = _connect(world, "owner")
    assert d.texts() == ["客户："] and d.hello["dirty"] is False and d.hello["head"] == 3
    d.close()


# ------------------------------------------------------------------ a restart in the middle of editing loses nothing
def test_a_process_restart_in_the_middle_of_editing_loses_nothing(world, tmp_path):
    """9.16「进程重启…不丢已确认修改」. Process 1's shutdown hook writes the live document; process 2,
    on the same data directory, seeds its room from that draft; an editor who kept typing while the
    service was down brings those edits along on reconnect, and everyone converges on the union."""
    from fastapi.testclient import TestClient
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER
    from synwevo_docs.api.app import create_app
    store, tid, app, sessions = world["store"], world["tid"], world["app"], world["sessions"]
    a = _connect(world, "editor")
    b = _connect(world, "owner")
    a.append_text("重启前")
    assert b.recv(want="update") == "update" and b.texts() == ["客户：重启前"]
    for hook in list(app.state.shutdown_hooks):                                 # process 1 goes down mid-session
        hook()
    a.close()
    b.close()
    a.append_text_offline("（离线补充）")                                        # the editor keeps typing on their own copy
    app2 = create_app(str(tmp_path / "data"), start_jobs=False)                # process 2, same data directory

    def client2(user):
        raw, _ = sessions.create(user["id"], workspace_id=world["wid"], amr="password")
        c = TestClient(app2)
        c.cookies.set(COOKIE_NAME, raw)
        c.cookies.set(CSRF_COOKIE, "t")
        c.headers[CSRF_HEADER] = "t"
        return c

    with TestClient(app2):
        c = Peer(client2(world["owner"]), tid)
        c.hello = c.connect()
        assert c.hello["dirty"] is True and c.hello["head"] == 1 and c.texts() == ["客户：重启前"], "process 2 starts from what process 1 wrote at shutdown"
        a2 = Peer(client2(world["editor"]), tid, doc=a.doc)                    # the editor reconnects with the offline edit in hand
        a2.hello = a2.connect()
        assert c.recv(want="update") == "update" and c.texts() == ["客户：重启前（离线补充）"]
        assert a2.texts() == ["客户：重启前（离线补充）"]
        c.close()
        a2.close()                                                             # the last one out persists in process 2
        for hook in list(app2.state.shutdown_hooks):
            hook()
    row = store.one("SELECT state, dirty FROM collab_docs WHERE room=?", (f"template:{tid}",))
    kept = Doc()
    kept.apply_update(bytes(row["state"]))
    assert row["dirty"] == 1 and "重启前（离线补充）" in json.dumps(read_doc(kept), ensure_ascii=False)


def test_the_change_log_keeps_what_a_block_said_before_an_edit(world):
    """9.13 D 「文本展示插入/删除等可理解差异」: an edit row carries the block's text from before the
    first keystroke of a run and the excerpt after the last one; inserts and deletes carry none."""
    tid = world["tid"]
    a = _connect(world, "editor")
    a.append_text("第一次")
    time.sleep(0.05)
    a.append_text("第二次")                                    # coalesced into the same row
    a.add_paragraph("新段落")
    items = _changes_when(world, tid, lambda rows: any(r["kind"] == "edit" and r["count"] == 2 for r in rows) and any(r["kind"] == "insert" for r in rows))
    edit = next(it for it in items if it["kind"] == "edit")
    ins = next(it for it in items if it["kind"] == "insert")
    assert edit["before"] == "客户：【客户】" and edit["excerpt"] == "客户：第一次第二次【客户】" and edit["count"] == 2   # the field atom reads as 【name】
    assert ins["before"] is None and ins["excerpt"] == "新段落"
    a.close()


# ------------------------------------------------------------------ bounded frames, corrupt drafts
def test_an_oversized_frame_closes_only_that_connection(world, monkeypatch):
    """9.13 E 「恶意大更新」: a writer's frame over the limit closes their connection with a code
    the client does not retry; the room and the other people in it are untouched."""
    a = _connect(world, "editor")
    b = _connect(world, "owner")
    from synwevo_docs import collab as C
    monkeypatch.setattr(C, "MAX_FRAME_BYTES", 4096)
    a.ws.send_bytes(bytes([YMessageType.SYNC, YSyncMessageType.SYNC_UPDATE]) + b"\x00" * 5000)
    with pytest.raises(WebSocketDisconnect) as e:
        for _ in range(20):
            a.ws.receive_bytes()
    assert e.value.code == 4400 and e.value.reason == "frame_too_large"
    assert b.recv_meta("left")["user"]["name"] == "Editor"          # gone, cleanly
    b.append_text("其他人照常")
    assert b.texts() == ["客户：其他人照常"]
    b.close()


def test_a_corrupt_persisted_draft_does_not_keep_the_room_from_opening(world):
    """9.13 C.5 「损坏检测」: an unreadable draft is logged and the room reseeds from the head
    version, saying so in hello; the alternative was a room nobody could ever open again."""
    store, tid = world["store"], world["tid"]
    store.execute("INSERT INTO collab_docs (room, tenant, template_id, state, head, dirty, updated_at) VALUES (?,?,?,?,?,?,?) "
                  "ON CONFLICT(room) DO UPDATE SET state=excluded.state, head=excluded.head, dirty=excluded.dirty",
                  (f"template:{tid}", world["wid"], tid, b"\xff\xfe not a yjs update \x00", 1, 1, 1.0))
    a = _connect(world, "editor")
    assert a.hello["recovered"] is True and a.hello["head"] == 1 and a.hello["dirty"] is False
    assert a.texts() == ["客户："]                                    # the head version, not garbage
    a.append_text("修好了")
    a.close()                                                        # the next persist replaces the corrupt row
    row = store.one("SELECT state FROM collab_docs WHERE room=?", (f"template:{tid}",))
    fixed = Doc()
    fixed.apply_update(bytes(row["state"]))
    assert "修好了" in json.dumps(read_doc(fixed), ensure_ascii=False)


def test_a_save_from_outside_the_room_does_not_drop_a_persisted_live_draft(world):
    """CI-113: with nobody in the room, a save made elsewhere (another page, the machine API, a
    field change on the project page) deleted the persisted draft — edits someone typed and left
    unsaved were gone without a word. It is refused like a restore now, unless told."""
    tid = world["tid"]
    c = world["client_for"](world["owner"])
    a = _connect(world, "editor")
    a.append_text("离开前")
    time.sleep(0.2)
    a.close()                                                # persists the dirty draft
    other = {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "外部保存"}]}]}}
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": other, "base_version": 1})
    assert r.status_code == 409 and r.json()["error"] == "live_edits" and r.json()["detail"]["last_by"] == "Editor", r.text
    slot = c.get(f"/api/templates/{tid}").json()["version"]["slots"][0]
    r = c.patch(f"/api/templates/{tid}/slots/{slot['id']}", json={"required": False})
    assert r.status_code == 409 and r.json()["error"] == "live_edits" and "先在编辑器里保存" in r.json()["message"]
    d = _connect(world, "owner")
    assert d.texts() == ["客户：离开前"] and d.hello["dirty"] is True, "the draft is still there"
    d.close()
    time.sleep(0.2)
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": other, "base_version": 1, "discard_live": True})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text


def test_a_save_from_inside_the_room_saves_the_live_document(world):
    tid = world["tid"]
    c = world["client_for"](world["editor"])
    a = _connect(world, "editor")
    a.append_text("在房间里")
    time.sleep(0.2)
    live = {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "客户：在房间里"}]}]}}
    r = c.put(f"/api/templates/{tid}/builtin", json={"editor": live, "base_version": 1})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    a.close()


def test_a_save_from_the_room_leaves_newer_typing_unsaved(world):
    """CI-114: a collaborator's save is what their page held when they pressed it. Someone who
    typed after that had their text marked as saved though it was not in the version, so a later
    restore replaced it without asking. The room is clean only when it equals the saved version."""
    tid = world["tid"]
    ce = world["client_for"](world["editor"])
    a = _connect(world, "editor")
    b = _connect(world, "owner")
    a.append_text("甲")
    assert b.recv(want="update") == "update"
    b.append_text("乙")                                     # typed after the editor's page serialized its save
    assert a.recv(want="update") == "update"
    time.sleep(0.2)
    field = DOC["body"]["content"][0]["content"][1]
    body = lambda text: {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}, field]}]}}  # noqa: E731
    pressed = body("客户：甲")
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": pressed, "base_version": 1})
    assert r.status_code == 200 and r.json()["head"] == 2
    head = a.recv_meta("head")
    assert head["head"] == 2 and head["dirty"] is True, "「乙」is not in version 2"
    r = world["client_for"](world["owner"]).post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2})
    assert r.status_code == 409 and r.json()["error"] == "live_edits"
    # saving what the room holds makes it clean
    r = ce.put(f"/api/templates/{tid}/builtin", json={"editor": body("客户：甲乙"), "base_version": 2})
    assert r.status_code == 200 and a.recv_meta("head")["dirty"] is False
    a.close()
    b.close()


def test_a_tab_holding_a_replaced_document_is_not_merged_back(world):
    """CI-115: an editor typed, left, and the owner restored an older version (discarding that edit
    explicitly). The editor's tab still held its document; reconnecting, it synced it into the
    freshly seeded room and put the discarded text back next to the restored one. The room now
    starts a new epoch whenever it seeds from a version, and a tab from a previous one is refused."""
    tid = world["tid"]
    c = world["client_for"](world["owner"])
    v2 = {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "第二版"}]}]}}
    assert c.put(f"/api/templates/{tid}/builtin", json={"editor": v2, "base_version": 1}).status_code == 200
    a = _connect(world, "editor")
    first = a.hello["epoch"]
    assert first and a.texts() == ["第二版"]
    a.append_text("（被丢弃的修改）")
    time.sleep(0.2)
    a.close()
    r = c.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2, "discard_live": True})
    assert r.status_code == 200 and r.json()["head"] == 3
    with pytest.raises(WebSocketDisconnect) as e:
        Peer(world["client_for"](world["editor"]), tid, doc=a.doc, epoch=first).connect()
    assert e.value.code == 4409 and e.value.reason == "stale_document"
    fresh = _connect(world, "owner")
    assert fresh.texts() == ["客户："] and fresh.hello["epoch"] != first, "nothing of the replaced document came back"
    fresh.close()


def test_a_tab_reconnecting_to_the_same_document_keeps_its_offline_edits(world):
    """The epoch only changes when the room seeds from a version: a reconnect to the same live
    document (a network drop, a restart from the persisted draft) still merges what was typed offline."""
    tid = world["tid"]
    a = _connect(world, "editor")
    epoch = a.hello["epoch"]
    b = _connect(world, "owner")                             # keeps the room open while the editor is away
    a.close()
    a.append_text_offline("（离线）")
    a2 = Peer(world["client_for"](world["editor"]), tid, doc=a.doc, epoch=epoch)
    a2.hello = a2.connect()
    assert a2.hello["epoch"] == epoch
    assert b.recv(want="update") == "update" and b.texts() == ["客户：（离线）"]
    a2.close()
    b.close()


def test_an_open_room_is_no_way_into_another_workspace(world):
    """CI-118: once someone in the template's workspace had its room open, a person signed into
    another workspace could join by the template id — receive the live document and write to it —
    because an open room was handed out without comparing its workspace. Closed rooms already
    answered 404; open ones do now too, for template and project rooms."""
    tid = world["tid"]
    a = _connect(world, "owner")                             # the room is open
    stranger = world["client_for"](world["stranger"], world["other_wid"])
    with pytest.raises(WebSocketDisconnect) as x:
        stranger.websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4404
    assert a.texts() == ["客户："], "nothing reached the room"
    a.close()
    # a project room of this workspace, open, is refused the same way
    pid = world["co"].post("/api/projects", json={"name": "同空间项目"}).json()["id"]
    p = Peer(world["client_for"](world["owner"]), pid, path="projects")
    p.hello = p.connect()
    with pytest.raises(WebSocketDisconnect) as x:
        stranger.websocket_connect(f"/api/collab/projects/{pid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4404
    p.close()


def test_a_version_written_while_the_room_opens_is_not_missed(world, monkeypatch):
    """Review of sheet-live step ②, item 3 (the same race in document rooms): a save landing while
    the room was being opened found no room and removed the draft; the room then opened with the
    version it had read before — the older one — and every save from it was refused as stale."""
    import threading

    from synwevo_docs import collab as C
    tid = world["tid"]
    started, release = threading.Event(), threading.Event()
    real = C.Room.load

    def slow_load(self, template, row, editor_of_head):         # fixture: opening the room takes a while
        started.set()
        assert release.wait(10)
        return real(self, template, row, editor_of_head)
    monkeypatch.setattr(C.Room, "load", slow_load)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("a", _connect(world, "editor")))
    t.start()
    assert started.wait(10)
    newer = {**DOC, "body": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "开房间时保存"}]}]}}
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin", json={"editor": newer, "base_version": 1})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    release.set()
    t.join(10)
    a = box["a"]
    assert a.hello["head"] == 2 and a.texts() == ["开房间时保存"]
    a.close()
