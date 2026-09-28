"""The live table room over the WebSocket, with real sessions (docs-sheet-live.md B1 step ②, behind
SYNWEVO_SHEET_LIVE): two people see each other's cells as they type; the same cell written by
someone who had not seen the other's value is a conflict with whose value it was, never a silent
overwrite; viewers follow but cannot write; frames, groups and rates are bounded; the room's state
becomes a version only when someone saves it, and what was typed during the build stays unsaved; a
version written outside the room is folded in and attributed, and whoever lost a value is told;
unsaved live edits still stop an outside save unless it says discard_live, which restarts the room
under a new epoch; the old whole-table PUT is refused while the room is live; a restart reloads the
draft and a message resent after it applies once; with the switch off nothing changes.

TEST FIXTURES: a synthetic table template (test_sheet_collab.SHEET), real accounts and sessions in a
temporary data directory, an API key minted in that directory, and a build wrapper that pauses to
make "a save is being built" observable."""
from __future__ import annotations

import json
import threading
import time
import zlib

import pytest
from pycrdt import YMessageType, read_message, write_message
from starlette.websockets import WebSocketDisconnect

from synwevo_docs import collab_sheet as cs
from synwevo_docs.collab import MESSAGE_META, MESSAGE_SHEET

from .test_collab import PW
from .test_sheet_collab import SHEET, edited


# ------------------------------------------------------------------ a table tab speaking the live frames
class SheetPeer:
    """A browser tab on a live table room: it keeps a mirror of the room's content, applies the
    patches it is sent in order, and numbers its own messages (client id + n) as the design says."""

    def __init__(self, client, tid, *, client_id: str | None = None, epoch: str | None = None, origin="http://testserver"):
        q = ([f"client={client_id}"] if client_id else []) + ([f"epoch={epoch}"] if epoch else [])
        self.cm = client.websocket_connect(f"/api/collab/templates/{tid}" + (f"?{'&'.join(q)}" if q else ""), headers={"origin": origin})
        self.ws = self.cm.__enter__()
        self.meta: list[dict] = []
        self.sheet: list[dict] = []
        self.inbox: list[tuple[str, dict]] = []              # frames read but not yet asked for
        self.hello = self.wait(lambda k, p: k == "meta" and p["type"] == "hello")
        self.content = self.hello.get("content")
        self.seq = self.hello.get("seq")
        self.next = self.hello.get("next")
        self.epoch = self.hello.get("epoch")

    def close(self):
        self.cm.__exit__(None, None, None)

    # -- receiving
    def recv(self):
        data = self.ws.receive_bytes()
        if data[0] == MESSAGE_META:
            payload = json.loads(read_message(data[1:]))
            self.meta.append(payload)
            return "meta", payload
        if data[0] == MESSAGE_SHEET:
            payload = json.loads(read_message(data[1:]))
            self.sheet.append(payload)
            self._follow(payload)
            return "sheet", payload
        return "other", data                                  # the room's (empty) document sync, awareness

    def _follow(self, p: dict):
        if p["type"] in ("patch", "ack") and p.get("ok", True) and p.get("patch") and not p.get("duplicate"):
            assert p["seq"] == self.seq + 1, f"patches arrive in order: had {self.seq}, got {p['seq']}"
            cs.apply_patch(self.content, p["patch"])
            self.seq = p["seq"]
        elif p["type"] in ("full", "reset") and p.get("live", True):
            self.content, self.seq, self.epoch = p["content"], p["seq"], p["epoch"]
            if "next" in p:
                self.next = p["next"]

    def wait(self, pred, tries: int = 40):
        """The first frame (read before or now) that ``pred`` wants; each is handed out once."""
        for i, (kind, payload) in enumerate(self.inbox):
            if pred(kind, payload):
                del self.inbox[i]
                return payload
        for _ in range(tries):
            kind, payload = self.recv()
            if kind == "other":
                continue
            if pred(kind, payload):
                return payload
            self.inbox.append((kind, payload))
        raise AssertionError(f"not received; meta {self.meta[-5:]}, sheet {self.sheet[-5:]}")

    def ack(self, n: int) -> dict:
        return self.wait(lambda k, p: k == "sheet" and p["type"] == "ack" and p["n"] == n)

    def patch(self) -> dict:
        return self.wait(lambda k, p: k == "sheet" and p["type"] == "patch")

    def meta_of(self, kind: str, tries: int = 40) -> dict:
        return self.wait(lambda k, p: k == "meta" and p["type"] == kind, tries)

    def head(self, no: int) -> dict:
        """The version frame that announces version ``no``."""
        return self.wait(lambda k, p: k == "meta" and p["type"] == "head" and p["head"] == no)

    def of(self, kind: str) -> dict:
        return self.wait(lambda k, p: k == "sheet" and p["type"] == kind)

    # -- sending
    def frame(self, payload: dict):
        self.ws.send_bytes(bytes([MESSAGE_SHEET]) + write_message(json.dumps(payload, ensure_ascii=False).encode()))

    def send(self, op=None, *, tx=None, n=None, expect="seen", epoch="mine") -> int:
        if n is None:
            n, self.next = self.next, self.next + 1
        f = {"type": "op", "epoch": self.epoch if epoch == "mine" else epoch, "n": n, "expect": self.seq if expect == "seen" else expect}
        if op is not None:
            f["op"] = op
        if tx is not None:
            f["tx"] = tx
        self.frame(f)
        return n

    def do(self, op=None, **kw) -> dict:
        return self.ack(self.send(op, **kw))

    # -- reading the mirror
    def at(self, ref: str, i: int = 0) -> dict:
        sid = self.content["order"][i]
        sh = self.content["sheets"][sid]
        r, c = cs.tabular.parse_cell(ref)
        return {"sheet": sid, "row": sh["rows"][r], "col": sh["cols"][c]}

    def model(self) -> dict:
        return cs.project({"content": self.content})

    def cells(self, i: int = 0) -> dict:
        return self.model()["sheets"][i].get("cells") or {}


def put(peer: SheetPeer, ref: str, v) -> dict:
    return {"op": "set", **peer.at(ref), "v": v}


# ------------------------------------------------------------------ the world
@pytest.fixture()
def world(tmp_path, monkeypatch):
    """One workspace with an owner, an editor and a viewer, a second workspace whose owner is a
    stranger, one table template; the live-table switch on. Sessions are real cookies."""
    from fastapi.testclient import TestClient

    from synwevo_core.apps import bootstrap_docs_app
    from synwevo_core.auth import Auth
    from synwevo_core.identity import Identity
    from synwevo_core.sessions import COOKIE_NAME, CSRF_COOKIE, CSRF_HEADER, Sessions
    from synwevo_docs.api.app import create_app

    monkeypatch.setenv("SYNWEVO_UI_AUTH", "session")
    monkeypatch.setenv("SYNWEVO_COOKIE_SECURE", "0")
    monkeypatch.setenv("SYNWEVO_COLLAB_RECHECK_S", "0.2")
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE", "1")
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
    token, _ = Auth(store).create_key("sheet-live fixture", wid, ("read", "write"))

    def client_for(user, workspace=None, the_app=None):
        raw, _ = sessions.create(user["id"], workspace_id=workspace or wid, amr="password")
        c = TestClient(the_app or app)
        c.cookies.set(COOKIE_NAME, raw)
        c.cookies.set(CSRF_COOKIE, "t")
        c.headers[CSRF_HEADER] = "t"
        return c

    with TestClient(app) as machine:
        co = client_for(owner)
        t = co.post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": SHEET, "name": "实时表格"})
        assert t.status_code == 200, t.text
        yield {"app": app, "store": store, "ident": ident, "sessions": sessions, "wid": wid, "other_wid": other_wid, "tid": t.json()["id"],
               "owner": owner, "editor": editor, "viewer": viewer, "stranger": stranger, "client_for": client_for, "co": co,
               "machine": machine, "api": {"Authorization": f"Bearer {token}"}, "tmp_path": tmp_path}


def join(world, who, **kw) -> SheetPeer:
    return SheetPeer(world["client_for"](world[who]), world["tid"], **kw)


def machine_put(world, editor, base_version, **extra):
    return world["machine"].put(f"/api/v1/templates/{world['tid']}/builtin", headers=world["api"],
                                json={"editor": editor, "base_version": base_version, **extra})


def machine_error(r) -> tuple[str, dict]:
    """The machine API's error envelope: (code, details)."""
    e = r.json()["error"]
    return e["code"], e.get("details") or {}


def head_model(world) -> dict:
    return world["machine"].get(f"/api/v1/templates/{world['tid']}/builtin", headers=world["api"]).json()


def row(world) -> dict | None:
    """The persisted draft, once the closing write of a room its last person just left has landed
    (it runs in a thread of its own)."""
    world["app"].state.collab.settled(f"template:{world['tid']}")
    return world["store"].one("SELECT * FROM collab_docs WHERE room=?", (f"template:{world['tid']}",))


# ------------------------------------------------------------------ two people, live
def test_two_people_see_each_others_cells_as_they_type(world):
    a = join(world, "owner")
    h = a.hello
    assert h["live"] is True and h["writable"] is False and h["seq"] == 0 and h["next"] == 1 and h["dirty"] is False
    assert h["epoch"] and h["client"] and h["resumed"] is False and h["limits"]["max_tx"] == 5000
    assert a.model() == cs.normalize(SHEET), "the room starts from the saved version, rendered exactly"
    b = join(world, "editor")
    assert b.hello["seq"] == 0 and b.hello["client"] != a.hello["client"]
    a.meta_of("joined")

    r = a.do(put(a, "A2", "张三"))
    assert r["ok"] is True and r["seq"] == 1 and r["before"], r
    p = b.patch()
    assert p["seq"] == 1 and p["by"]["name"] == "Owner" and b.cells()["A2"] == {"v": "张三"}
    r = b.do(put(b, "C1", "金额"))
    assert r["ok"] and r["seq"] == 2
    p = a.patch()
    assert p["by"]["name"] == "Editor" and p["by"]["id"] == world["editor"]["id"]
    assert a.model() == b.model() and a.cells()["C1"] == {"v": "金额"}
    assert a.meta_of("state")["dirty"] is True
    # nothing became a version: generation and the machine API still read version 1
    assert head_model(world)["version"] == 1 and "A2" not in head_model(world)["editor"]["sheets"][0]["cells"]
    # the same message delivered twice applies once, answering what it answered the first time
    b.frame({"type": "op", "epoch": b.epoch, "n": 1, "expect": 1, "op": put(b, "C1", "金额")})
    again = b.ack(1)
    assert again["duplicate"] is True and again["seq"] == 2
    a.close()
    b.close()


def test_the_same_cell_from_someone_who_had_not_seen_it_is_a_conflict(world):
    a = join(world, "owner")
    b = join(world, "editor")
    assert a.do(put(a, "B2", "甲"))["ok"]
    b.patch()
    r = b.do(put(b, "B2", "乙"), expect=0)                       # typed before the owner's value arrived
    assert r["ok"] is False and r["code"] == "conflict", r
    assert r["detail"]["theirs"] == {"v": "甲"} and r["detail"]["by"] == world["owner"]["id"] and r["detail"]["at"] == 1
    assert r["people"][world["owner"]["id"]]["name"] == "Owner"
    assert b.cells()["B2"] == {"v": "甲"}, "their value stays"
    r = b.do(put(b, "B2", "乙"))                                  # having seen it, the editor chooses theirs over it
    assert r["ok"] and a.patch()["by"]["name"] == "Editor" and a.cells()["B2"] == {"v": "乙"}
    # the model's own refusals come through with their reason: a value under someone's merged range
    assert a.do({"op": "merge", "sheet": a.at("A3")["sheet"], "range": [a.at("A3")["row"], a.at("A3")["col"], a.at("B3")["row"], a.at("B3")["col"]]})["ok"]
    b.patch()
    r = b.do(put(b, "B3", "藏在合并里"))
    assert r["ok"] is False and r["code"] == "covered" and r["people"][world["owner"]["id"]]["name"] == "Owner"
    a.close()
    b.close()


def test_a_viewer_follows_but_cannot_write_and_a_demotion_takes_effect(world):
    v = join(world, "viewer")
    assert v.hello["readonly"] is True and v.hello["live"] is True
    e = join(world, "editor")
    r = v.do(put(v, "A3", "越权"))
    assert r["ok"] is False and r["code"] == "read_only"
    assert e.do(put(e, "A3", "编辑者"))["ok"]
    assert v.patch()["seq"] == 1 and v.cells()["A3"] == {"v": "编辑者"}
    world["ident"].change_role(world["wid"], world["editor"]["id"], "viewer")
    assert e.meta_of("role")["readonly"] is True
    r = e.do(put(e, "A3", "降级后"))
    assert r["ok"] is False and r["code"] == "read_only"
    world["ident"].remove_member(world["wid"], world["editor"]["id"])
    assert e.meta_of("closed")["reason"] == "membership_ended"
    with pytest.raises(WebSocketDisconnect) as x:
        for _ in range(20):
            e.ws.receive_bytes()
    assert x.value.code == 4403
    e.close()
    v.close()


def test_the_handshake_refuses_as_before(world):
    from fastapi.testclient import TestClient
    tid = world["tid"]
    with pytest.raises(WebSocketDisconnect) as x:
        TestClient(world["app"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as x:
        world["client_for"](world["editor"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "https://evil.example"}).__enter__()
    assert x.value.code == 4403 and x.value.reason == "origin"
    with pytest.raises(WebSocketDisconnect) as x:
        world["client_for"](world["stranger"], world["other_wid"]).websocket_connect(f"/api/collab/templates/{tid}", headers={"origin": "http://testserver"}).__enter__()
    assert x.value.code == 4404
    # the same while the room is open and live (CI-118): an open room is no way into another workspace
    a = join(world, "owner")
    assert a.do(put(a, "A2", "本空间的未保存内容"))["ok"]
    with pytest.raises(WebSocketDisconnect) as x:
        SheetPeer(world["client_for"](world["stranger"], world["other_wid"]), tid)
    assert x.value.code == 4404
    assert not [m for m in a.meta if m["type"] == "joined"], "nobody joined"
    # and nothing about the live room leaks to another workspace over HTTP either
    stranger = world["client_for"](world["stranger"], world["other_wid"])
    assert stranger.put(f"/api/templates/{tid}/builtin", json={"editor": SHEET, "base_version": 1}).status_code == 404
    assert stranger.post(f"/api/templates/{tid}/collab/save", json={}).status_code == 404
    a.close()


# ------------------------------------------------------------------ bounds
def test_frames_groups_and_rates_are_bounded(world, monkeypatch):
    from synwevo_core.http import RateBuckets
    from synwevo_docs import collab as C
    a = join(world, "owner")
    b = join(world, "editor")
    # not the protocol: said, not applied
    b.ws.send_bytes(bytes([MESSAGE_SHEET]) + write_message(b"{not json"))
    bad = b.wait(lambda k, p: k == "sheet" and p["type"] == "ack")
    assert bad["ok"] is False and bad["code"] == "invalid" and bad["detail"] == {"reason": "frame"}
    b.frame({"type": "op", "n": b.next, "op": put(b, "A2", "x")})          # no expect
    assert b.wait(lambda k, p: k == "sheet" and p["type"] == "ack")["detail"] == {"reason": "expect"}
    # a group over 5000 operations is refused whole, before anything applies
    big = [put(b, "A2", str(i)) for i in range(cs.MAX_TX + 1)]
    r = b.do(tx=big)
    assert r["ok"] is False and r["code"] == "too_large" and r["detail"]["what"] == "tx" and r["detail"]["max"] == cs.MAX_TX
    # a frame over the limit closes only that connection (4400, not retried)
    monkeypatch.setattr(C, "MAX_FRAME_BYTES", 4096)
    b.ws.send_bytes(bytes([MESSAGE_SHEET]) + write_message(b"x" * 5000))
    with pytest.raises(WebSocketDisconnect) as x:
        for _ in range(40):
            b.ws.receive_bytes()
    assert x.value.code == 4400 and x.value.reason == "frame_too_large"
    assert a.meta_of("left")["user"]["name"] == "Editor"
    assert a.do(put(a, "C3", "其他人照常"))["ok"]
    b.close()
    a.close()


# ------------------------------------------------------------------ saving
def test_saving_writes_what_the_room_holds_and_adds_no_version_when_nothing_changed(world):
    tid, co = world["tid"], world["co"]
    a = join(world, "editor")
    a.do(put(a, "A2", "李四"))
    r = co.post(f"/api/templates/{tid}/collab/save", json={"note": "实时保存"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["head"] == 2 and out["live"] == {"seq": 1, "dirty": False}
    assert out["version"]["note"] == "实时保存" and out["version"]["editor"]["sheets"][0]["cells"]["A2"] == {"v": "李四"}
    assert [s["name"] for s in out["version"]["slots"]] == ["客户"], "the workbook was generated from the live table"
    head = a.meta_of("head")
    assert head["reason"] == "save" and head["head"] == 2 and head["dirty"] is False and head["by"] == "Owner"
    assert head_model(world)["editor"] == a.model(), "the version is exactly what the room renders"
    # nothing changed since: no version, said so
    r = co.post(f"/api/templates/{tid}/collab/save", json={})
    assert r.status_code == 200 and r.json()["unchanged"] is True and r.json()["head"] == 2
    # a viewer cannot save (route policy), a note is bounded, a template without a live room says so
    assert world["client_for"](world["viewer"]).post(f"/api/templates/{tid}/collab/save", json={}).status_code == 403
    r = co.post(f"/api/templates/{tid}/collab/save", json={"note": "长" * 201})
    assert r.status_code == 400 and r.json()["error"] == "invalid_field"
    other = co.post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": SHEET}).json()["id"]
    r = co.post(f"/api/templates/{other}/collab/save", json={})
    assert r.status_code == 409 and r.json()["error"] == "not_live"
    a.close()


def test_edits_made_while_a_save_is_being_built_stay_unsaved(world, monkeypatch):
    from synwevo_docs import builtin_service
    tid, co = world["tid"], world["co"]
    a = join(world, "owner")
    b = join(world, "editor")
    a.do(put(a, "A2", "构建前"))
    b.patch()
    started, release = threading.Event(), threading.Event()
    real = builtin_service._build

    def slow_build(kind, editor):                              # fixture: holds the build until the test lets it go
        started.set()
        assert release.wait(10)
        return real(kind, editor)
    monkeypatch.setattr(builtin_service, "_build", slow_build)
    result: dict = {}
    saver = threading.Thread(target=lambda: result.setdefault("r", co.post(f"/api/templates/{tid}/collab/save", json={})))
    saver.start()
    assert started.wait(10)
    assert b.do(put(b, "A3", "构建中"))["ok"]                    # typed while the version is being built
    release.set()
    saver.join(20)
    r = result["r"]
    assert r.status_code == 200, r.text
    cells = r.json()["version"]["editor"]["sheets"][0]["cells"]
    assert cells["A2"] == {"v": "构建前"} and "A3" not in cells
    assert r.json()["live"] == {"seq": 1, "dirty": True}
    assert b.meta_of("head")["dirty"] is True, "「构建中」 is not in version 2"
    # the unsaved edit still guards an outside restore, and the next save carries it
    r = co.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 2})
    assert r.status_code == 409 and r.json()["error"] == "live_edits" and r.json()["detail"]["last_by"] == "Editor"
    monkeypatch.setattr(builtin_service, "_build", real)
    r = co.post(f"/api/templates/{tid}/collab/save", json={})
    assert r.status_code == 200 and r.json()["version"]["editor"]["sheets"][0]["cells"]["A3"] == {"v": "构建中"} and r.json()["live"]["dirty"] is False
    a.close()
    b.close()


# ------------------------------------------------------------------ versions from outside the room
def test_an_outside_version_is_folded_in_and_attributed(world):
    tid, co = world["tid"], world["co"]
    a = join(world, "editor")
    # a single-person save from outside the room while it is clean: folded in, attributed to the saver
    r = co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A3": {"v": "外部写入"}}), "base_version": 1, "solo": True})
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    p = a.patch()
    assert p["by"]["name"] == "Owner" and p["outside"] == {"reason": "save", "head": 2}
    assert a.cells()["A3"] == {"v": "外部写入"}
    head = a.meta_of("head")
    assert head["head"] == 2 and head["by"] == "Owner" and head["dirty"] is False
    # a field renamed on the project page arrives the same way (the machine API has no person: `system`)
    r = co.patch(f"/api/templates/{tid}/slots/customer", json={"name": "客户名称"})
    assert r.status_code == 200, r.text
    p = a.patch()
    assert p["outside"]["head"] == 3 and a.model()["sheets"][0]["fields"][0]["name"] == "客户名称"
    r = machine_put(world, {**a.model(), "sheets": [{**a.model()["sheets"][0], "cells": {**a.cells(), "C3": {"v": "API"}}}]}, 3)
    assert r.status_code == 200, r.text
    p = a.patch()
    assert p["by"]["system"] is True and a.cells()["C3"] == {"v": "API"}
    # people in the room keep what the outside version did not touch; where it did, it wins and
    # whoever lost a value is told (the owner saves from inside the room, so the guard lets it pass)
    o = join(world, "owner")
    a.meta_of("joined")
    assert a.do(put(a, "A2", "编辑者的值"))["ok"] and a.do(put(a, "B3", "只有编辑者改"))["ok"]
    mine = {**a.cells(), "A2": {"v": "外部的值"}}
    mine.pop("B3")
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin",
                                                json={"editor": {"sheets": [{**a.model()["sheets"][0], "cells": mine}]}, "base_version": 4, "solo": True})
    assert r.status_code == 200, r.text
    lost = a.meta_of("overwritten")
    assert [p["name"] for p in lost["people"]] == ["Editor"] and lost["by"]["name"] == "Owner"
    assert [(c["kind"], c.get("ref")) for c in lost["conflicts"]] == [("cell", "A2")]
    assert a.cells()["A2"] == {"v": "外部的值"} and a.cells()["B3"] == {"v": "只有编辑者改"}, "the outside version wins only where both wrote"
    assert a.head(5)["dirty"] is True, "the editor's other edit is still unsaved"
    o.close()
    a.close()


def test_unsaved_live_edits_stop_an_outside_save_unless_it_discards_them(world):
    tid, co = world["tid"], world["co"]
    a = join(world, "editor")
    first = a.hello["epoch"]
    assert a.do(put(a, "A2", "未保存"))["ok"]
    outside = edited({"A2": {"v": "机器写入"}})
    r = machine_put(world, outside, 1)
    assert r.status_code == 409 and machine_error(r) == ("live_edits", {"action": "save", "online": ["Editor"], "last_by": "Editor"}), r.text
    # restore / undo / redo keep CI-49's rule: the guard answers before anything else is decided
    for path, body in (("restore", {"version": 1, "base_head": 1}), ("undo", {"head": 1}), ("redo", {"head": 1})):
        r = co.post(f"/api/templates/{tid}/{path}", json=body)
        assert r.status_code == 409 and r.json()["error"] == "live_edits" and r.json()["detail"]["action"] == path, (path, r.text)
        assert r.json()["detail"]["online"] == ["Editor"] and r.json()["detail"]["last_by"] == "Editor"
    # told explicitly: the room starts afresh from the version under a new epoch
    r = machine_put(world, outside, 1, discard_live=True)
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    reset = a.of("reset")
    assert reset["epoch"] != first and reset["previous_epoch"] == first and reset["seq"] == 0 and reset["next"] == 1
    assert a.cells()["A2"] == {"v": "机器写入"} and a.model() == cs.normalize(outside)
    assert a.meta_of("head")["dirty"] is False
    # a tab still holding the discarded epoch cannot come back with it
    with pytest.raises(WebSocketDisconnect) as x:
        join(world, "editor", epoch=first)
    assert x.value.code == 4409 and x.value.reason == "stale_document"
    # the restarted room works: numbering starts again, a restore of a clean room is folded in
    assert a.do(put(a, "C2", "重新开始"))["seq"] == 1
    assert co.post(f"/api/templates/{tid}/collab/save", json={}).json()["head"] == 3
    r = co.post(f"/api/templates/{tid}/restore", json={"version": 1, "base_head": 3})
    assert r.status_code == 200 and r.json()["head"] == 4, r.text
    p = a.patch()
    assert p["outside"]["reason"] == "restore" and a.model() == cs.normalize(SHEET)
    a.close()


def test_the_old_whole_table_put_is_refused_while_the_room_is_live(world):
    tid, co = world["tid"], world["co"]
    a = join(world, "editor")
    r = co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"C2": {"v": "旧页面"}}), "base_version": 1})
    assert r.status_code == 409 and r.json()["error"] == "live_room" and r.json()["detail"]["online"] == ["Editor"], r.text
    assert r.json()["detail"]["epoch"] == a.hello["epoch"]
    r = co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"C2": {"v": "旧页面"}}), "base_version": 1, "solo": True})
    assert r.status_code == 200, r.text
    assert a.patch()["outside"]["reason"] == "save" and a.cells()["C2"] == {"v": "旧页面"}
    a.close()
    # nobody in the room: the page's save goes through as before
    r = co.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"C2": {"v": "没人在"}}), "base_version": 2})
    assert r.status_code == 200 and r.json()["head"] == 3, r.text


# ------------------------------------------------------------------ persistence and restarts
def test_the_draft_is_written_and_names_its_last_editor(world):
    tid, co = world["tid"], world["co"]
    a = join(world, "editor")
    a.do(put(a, "A2", "落盘"))
    assert a.meta_of("persisted")["seq"] == 1
    saved = row(world)
    assert saved["model"] == "sheet" and saved["dirty"] == 1 and saved["head"] == 1 and saved["epoch"] == a.hello["epoch"]
    st = json.loads(zlib.decompress(bytes(saved["state"])))
    assert cs.project(st)["sheets"][0]["cells"]["A2"] == {"v": "落盘"}
    a.close()
    # nobody in the room: the guard reads the draft and names who changed it last
    r = machine_put(world, edited({"A3": {"v": "x"}}), 1)
    assert r.status_code == 409 and machine_error(r) == ("live_edits", {"action": "save", "online": [], "last_by": "Editor"}), r.text
    b = join(world, "owner")
    assert b.hello["dirty"] is True and b.cells()["A2"] == {"v": "落盘"} and b.hello["epoch"] == a.hello["epoch"]
    b.close()


def test_a_restart_reloads_the_draft_and_a_resent_message_applies_once(world):
    from fastapi.testclient import TestClient
    from synwevo_docs.api.app import create_app
    tid, app = world["tid"], world["app"]
    a = join(world, "editor", client_id="tab-editor-1")
    assert a.hello["client"] == "tab-editor-1" and a.hello["resumed"] is False
    assert a.do(put(a, "A2", "一"))["ok"]
    op2 = put(a, "A3", "二")
    assert a.do(op2)["seq"] == 2
    epoch = a.hello["epoch"]
    for hook in list(app.state.shutdown_hooks):                               # process 1 goes down mid-session
        hook()
    a.close()
    app2 = create_app(str(world["tmp_path"] / "data"), start_jobs=False)      # process 2, same data directory
    with TestClient(app2):
        a2 = SheetPeer(world["client_for"](world["editor"], the_app=app2), tid, client_id="tab-editor-1", epoch=epoch)
        h = a2.hello
        assert h["epoch"] == epoch and h["seq"] == 2 and h["dirty"] is True and h["resumed"] is True and h["next"] == 3
        assert a2.cells()["A2"] == {"v": "一"} and a2.cells()["A3"] == {"v": "二"}
        # the tab did not see the answer to its second message before the restart: it sends it again
        r = a2.do(op2, n=2, expect=1)
        assert r["ok"] is True and r["duplicate"] is True and r["seq"] == 2
        assert a2.do(put(a2, "C3", "三"))["seq"] == 3, "nothing was applied twice"
        # the same client id from another person is theirs, not the editor's
        o = SheetPeer(world["client_for"](world["owner"], the_app=app2), tid, client_id="tab-editor-1")
        assert o.hello["client"] == "tab-editor-1" and o.hello["resumed"] is False and o.hello["next"] == 1
        # the editor's tab coming back with its id while its previous connection is still open here
        # takes the id over (review item 4); the previous connection is closed as superseded
        a3 = SheetPeer(world["client_for"](world["editor"], the_app=app2), tid, client_id="tab-editor-1")
        assert a3.hello["client"] == "tab-editor-1" and a3.hello["resumed"] is True and a3.hello["next"] == 4
        with pytest.raises(WebSocketDisconnect) as x:
            for _ in range(40):
                a2.ws.receive_bytes()
        assert x.value.code == 4409 and x.value.reason == "superseded"
        for p in (a2, o, a3):
            p.close()


def test_deleting_the_template_closes_the_room_and_leaves_no_draft(world):
    a = join(world, "editor")
    assert a.do(put(a, "A2", "删除前"))["ok"]
    r = world["co"].delete(f"/api/templates/{world['tid']}")
    assert r.status_code == 200, r.text
    with pytest.raises(WebSocketDisconnect) as x:
        for _ in range(40):
            a.ws.receive_bytes()
    assert x.value.code == 4410
    a.close()
    time.sleep(0.2)
    assert row(world) is None, "the last one out did not write the deleted template's draft back"


def test_an_unreadable_draft_is_replaced_by_the_head_version(world):
    world["store"].execute("INSERT INTO collab_docs (room, tenant, template_id, state, head, dirty, model, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                           (f"template:{world['tid']}", world["wid"], world["tid"], b"\x78\x9c not zlib", 1, 1, "sheet", 1.0))
    a = join(world, "editor")
    assert a.hello["recovered"] is True and a.hello["live"] is True and a.hello["dirty"] is False
    assert a.model() == cs.normalize(SHEET)
    a.close()


# ------------------------------------------------------------------ what stays as today
def test_a_table_the_room_cannot_hold_stays_in_the_save_and_merge_mode(world, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_CELLS", "1")
    a = join(world, "editor")
    assert a.hello["live"] is False and a.hello["live_reason"] == {"code": "too_large", "what": "cells", "max": 1, "size": 2}
    assert a.hello["epoch"] is None and "content" not in a.hello
    r = a.do({"op": "set", "sheet": "x", "row": "y", "col": "z", "v": 1}, n=1, expect=0)
    assert r["ok"] is False and r["code"] == "not_live" and r["detail"]["reason"]["code"] == "too_large"
    r = world["co"].put(f"/api/templates/{world['tid']}/builtin", json={"editor": edited({"A2": {"v": "照旧"}}), "base_version": 1})
    assert r.status_code == 200, "the page saves its whole table as before"
    assert a.meta_of("head")["head"] == 2
    a.close()
    assert row(world) is None


def test_with_the_switch_off_the_table_room_is_todays_presence_room(world, monkeypatch):
    monkeypatch.delenv("SYNWEVO_SHEET_LIVE")
    a = join(world, "editor")
    assert "live" not in a.hello and a.hello["writable"] is False and a.hello["epoch"] is None
    a.frame({"type": "op", "n": 1, "expect": 0, "op": {"op": "set"}})         # ignored: no such thing here
    a.ws.send_bytes(bytes([YMessageType.AWARENESS]) + write_message(b"\x00"))
    r = world["co"].put(f"/api/templates/{world['tid']}/builtin", json={"editor": edited({"A2": {"v": "照旧"}}), "base_version": 1})
    assert r.status_code == 200
    assert a.meta_of("head")["head"] == 2
    assert not a.sheet, "no live table frame was ever sent"
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 409 and r.json()["error"] == "not_live"
    a.close()
    assert row(world) is None, "nothing is persisted for a presence room"


# ------------------------------------------------------------------ the independent review of step ② (each failed before its fix)
@pytest.fixture()
def clock(monkeypatch):
    """Fixture: a fake monotonic clock for the rate limiter and the room (time does not pass by itself)."""
    import types

    import synwevo_core.http as H
    from synwevo_docs import collab_sheet_room as R
    now = [1000.0]
    fake = types.SimpleNamespace(monotonic=lambda: now[0], time=time.time, perf_counter=time.perf_counter, sleep=time.sleep)
    monkeypatch.setattr(H, "time", fake)
    monkeypatch.setattr(R, "time", fake, raising=False)
    return now


def _room(world):
    return world["app"].state.collab.rooms[f"template:{world['tid']}"]


def test_a_message_from_an_epoch_the_room_left_is_never_applied(world):
    """Review 2: an operation typed just before a restart crossed the `reset` on the wire. It was
    applied into the new epoch, and the sender's first message there was answered as a duplicate."""
    a = join(world, "owner")
    b = join(world, "editor")
    a.meta_of("joined")
    assert b.do(put(b, "A2", "B 的未保存修改"))["ok"]
    a.patch()
    r = machine_put(world, edited({"A2": {"v": "机器写入"}}), 1, discard_live=True)
    assert r.status_code == 200, r.text
    new_epoch = b.of("reset")["epoch"]
    old_epoch = a.epoch
    n = a.send({"op": "sheet_add", "name": "旧纪元的表"}, expect=0)        # still the old epoch: A has not read the reset
    stale = a.ack(n)
    assert stale["ok"] is False and stale["code"] == "stale_epoch" and stale["detail"]["epoch"] == new_epoch != old_epoch
    reset = a.of("reset")
    assert reset["next"] == 1, "no number of the new epoch was spent"
    r = a.do(put(a, "C2", "新纪元的第一条"))
    assert r["ok"] is True and not r.get("duplicate") and r["seq"] == 1
    assert [s["name"] for s in a.model()["sheets"]] == ["结果"] and a.cells()["C2"] == {"v": "新纪元的第一条"}
    a.close()
    b.close()


def test_a_version_written_while_the_room_opens_is_folded_in(world, monkeypatch):
    """Review 3: a save landing while the room was being opened found no room, so the room opened at
    the old version — every save from it was refused, and a reopen reseeded it and lost the typing."""
    from synwevo_docs import collab_sheet_room as R
    started, release = threading.Event(), threading.Event()
    real = R.SheetRoom.load_sheet

    def slow_load(self, template, row_, editor_of_head):       # fixture: opening the room takes a while
        started.set()
        assert release.wait(10)
        return real(self, template, row_, editor_of_head)
    monkeypatch.setattr(R.SheetRoom, "load_sheet", slow_load)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("a", join(world, "editor")))
    t.start()
    assert started.wait(10)
    r = machine_put(world, edited({"C3": {"v": "开房间时写入的版本"}}), 1)
    assert r.status_code == 200 and r.json()["head"] == 2, r.text
    release.set()
    t.join(10)
    a = box["a"]
    for _ in range(50):
        if _room(world).head == 2:
            break
        time.sleep(0.1)
    assert _room(world).head == 2, "the room follows the version written while it opened"
    if a.hello["head"] != 2:                                   # joined while the version was still being folded in
        a.head(2)
    assert a.cells()["C3"] == {"v": "开房间时写入的版本"}
    assert a.do(put(a, "A2", "开房后输入"))["ok"]
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 200 and r.json()["head"] == 3, r.text
    a.close()


def test_a_tab_back_before_its_old_socket_is_noticed_keeps_its_id_and_nothing_applies_twice(world):
    """Review 4: a tab reconnecting while the server still counted its old socket as open got a new
    client id, so the message it resent (its answer was lost with the socket) applied a second time."""
    a = join(world, "editor", client_id="tab-editor-1")
    sid = a.content["order"][0]
    rows0 = a.content["sheets"][sid]["nr"]
    op = {"op": "append_rows", "sheet": sid, "count": 3}
    n = a.send(op, expect=0)
    assert a.ack(n)["ok"]                                        # the answer the tab never saw
    a2 = join(world, "editor", client_id="tab-editor-1")
    assert a2.hello["client"] == "tab-editor-1" and a2.hello["resumed"] is True and a2.hello["next"] == n + 1
    r = a2.do(op, n=n, expect=0)
    assert r["ok"] is True and r["duplicate"] is True
    assert a2.content["sheets"][sid]["nr"] == rows0 + 3 == cs.project(_room(world).state)["sheets"][0]["rows"].__len__()
    with pytest.raises(WebSocketDisconnect) as x:
        for _ in range(40):
            a.ws.receive_bytes()
    assert x.value.code == 4409 and x.value.reason == "superseded"
    a.close()
    a2.close()


def test_a_costly_message_passes_once_the_time_it_was_told_has_passed(world, clock):
    """Review 5: refused attempts spent tokens, so a large group from someone who had typed a lot
    was refused again after every wait it was told — forever."""
    from synwevo_core.http import RateBuckets
    world["app"].state.collab.sheet_rate = RateBuckets(60)     # fixture: one message a second, on the fake clock
    b = join(world, "editor")
    for i in range(50):                                          # typing quickly: 10 left
        assert b.do(put(b, "A2", str(i)))["ok"]
    big = [put(b, "A3", f"v{i}") for i in range(cs.MAX_TX)]      # a group that costs 51
    n = b.send(tx=big)
    r = b.ack(n)
    assert r["ok"] is False and r["code"] == "rate_limited"
    wait = r["detail"]["retry_after"]
    assert 40 <= wait <= 42, wait
    b.send(tx=big, n=n)                                          # too early: refused without being read
    early = b.wait(lambda k, p: k == "sheet" and p["type"] == "ack")
    assert early["code"] == "rate_limited" and early["n"] is None and early["detail"]["retry_after"] <= wait
    clock[0] += wait
    r = b.do(tx=big, n=n)
    assert r["ok"] is True, r
    b.close()


def test_malformed_frames_and_whole_state_requests_are_metered(world, clock):
    """Review 11: a malformed frame was parsed and refused for free, and asking for the whole state
    cost the same however large it was."""
    from synwevo_core.http import RateBuckets
    world["app"].state.collab.sheet_rate = RateBuckets(5)
    v = join(world, "viewer")
    codes = []
    for _ in range(7):
        v.ws.send_bytes(bytes([MESSAGE_SHEET]) + write_message(b"{not json"))
        codes.append(v.wait(lambda k, p: k == "sheet" and p["type"] == "ack")["code"])
    assert codes == ["invalid"] * 5 + ["rate_limited"] * 2
    # a large table: the whole state costs by its size (≈1.3 MB here: 21 messages)
    world["app"].state.collab.sheet_rate = RateBuckets(600)
    o = join(world, "owner")
    sid = o.content["order"][0]
    sh = o.content["sheets"][sid]
    assert o.do({"op": "append_rows", "sheet": sid, "count": 18})["ok"]
    cells = [{"op": "set", "sheet": sid, "row": sh["rows"][r], "col": sh["cols"][c], "v": "x" * 32000}
             for r in range(1, 21) for c in (0, 2)]
    assert o.do(tx=cells[:20])["ok"] and o.do(tx=cells[20:])["ok"]
    o.meta_of("persisted")
    time.sleep(1.2)                                              # the draft write after the last change gives the exact size
    world["app"].state.collab.sheet_rate = RateBuckets(30)
    clock[0] += 1000
    v.frame({"type": "sync"})
    assert v.of("full")["live"] is True
    v.frame({"type": "sync"})
    again = v.wait(lambda k, p: k == "sheet" and p["type"] == "ack")
    assert again["code"] == "rate_limited", again
    o.close()
    v.close()


def test_an_outside_version_the_room_cannot_hold_never_drops_unsaved_edits(world):
    """Review 6: a single-person save from inside the room (the guard let it through) of a table the
    live state cannot hold dropped the room to presence mode and deleted the others' unsaved edits."""
    tid = world["tid"]
    o = join(world, "owner")
    e = join(world, "editor")
    o.meta_of("joined")
    assert e.do(put(e, "C3", "编辑者未保存的值"))["ok"]
    o.patch()
    # one far-away cell: a valid table the generator writes, beyond the live state's 200 000 row + column ids
    unholdable = edited({"A250000": {"v": "远处"}})
    with pytest.raises(cs.SeedError):
        cs.seed_state(unholdable)
    owner = world["client_for"](world["owner"])
    r = owner.put(f"/api/templates/{tid}/builtin", json={"editor": unholdable, "base_version": 1, "solo": True})
    assert r.status_code == 409 and r.json()["error"] == "live_edits" and r.json()["detail"]["reason"] == "cannot_fold", r.text
    assert e.do(put(e, "C2", "房间照常"))["ok"] and _room(world).live()
    # a table the room can hold still passes from inside the room (and is folded in)
    r = owner.put(f"/api/templates/{tid}/builtin", json={"editor": edited({"A3": {"v": "可以合入"}}), "base_version": 1, "solo": True})
    assert r.status_code == 200, r.text
    assert e.head(2)["dirty"] is True and e.cells()["C3"] == {"v": "编辑者未保存的值"}
    # confirmed: the version is written and the room, which cannot hold it, leaves live mode
    r = owner.put(f"/api/templates/{tid}/builtin", json={"editor": unholdable, "base_version": 2, "solo": True, "discard_live": True})
    assert r.status_code == 200, r.text
    assert e.meta_of("live")["live"] is False
    o.close()
    e.close()


def test_a_version_that_cannot_be_folded_in_keeps_the_edits_typed_meanwhile(world, monkeypatch):
    """Review 6 (the race): the room was clean when the guard looked, someone typed while the version
    was being built — the edits must stay, not go with the room's live mode."""
    from synwevo_docs import builtin_service
    tid = world["tid"]
    e = join(world, "editor")
    started, release = threading.Event(), threading.Event()
    real = builtin_service._build

    def slow(kind, editor):                                      # fixture: the outside version is being built
        started.set()
        assert release.wait(10)
        return real(kind, editor)
    monkeypatch.setattr(builtin_service, "_build", slow)
    far = edited({"A250000": {"v": "远处"}})                     # beyond the live state's 200 000 row + column ids
    with pytest.raises(cs.SeedError):
        cs.seed_state(far)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("r", world["co"].put(f"/api/templates/{tid}/builtin",
                                                                            json={"editor": far, "base_version": 1, "solo": True})))
    t.start()
    assert started.wait(10)
    assert e.do(put(e, "C3", "构建期间输入"))["ok"]
    release.set()
    t.join(20)
    assert box["r"].status_code == 200, box["r"].text
    kept = e.meta_of("live")
    assert kept["live"] is True and kept["stale"] is True and kept["head"] == 2
    assert _room(world).live() and cs.project(_room(world).state)["sheets"][0]["cells"]["C3"] == {"v": "构建期间输入"}
    assert e.do(put(e, "A3", "仍可输入"))["ok"]
    # the unsaved edits keep stopping outside saves until someone confirms discarding them
    monkeypatch.setattr(builtin_service, "_build", real)
    r = machine_put(world, edited({}), 2)
    assert r.status_code == 409 and machine_error(r)[0] == "live_edits"
    e.close()
    # nobody is in the room now: the draft (at version 1, with its edits) is kept, not reseeded away
    d = row(world)
    assert d["head"] == 1 and d["dirty"] == 1
    again = join(world, "editor")
    assert again.cells()["C3"] == {"v": "构建期间输入"} and again.cells()["A3"] == {"v": "仍可输入"}
    again.close()


def test_the_live_table_is_bounded_in_cells(world, monkeypatch):
    """Review 7: only the opening was bounded; the live table could then grow without limit."""
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_CELLS", "3")
    a = join(world, "editor")                                    # two cells: admitted
    assert a.do(put(a, "A2", "第三格"))["ok"]
    r = a.do(put(a, "A3", "第四格"))
    assert r["ok"] is False and r["code"] == "too_large" and r["detail"] == {"what": "cells", "max": 3}
    assert a.do(put(a, "A2", "改已有的格子照常"))["ok"]
    a.close()


def test_the_live_table_is_bounded_in_bytes_and_can_still_shrink(world, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_BYTES", str(64 * 1024))
    a = join(world, "editor")
    refused = None
    for ref in ("A2", "A3", "B2", "B3", "C2", "C3", "C1", "A1"):
        r = a.do(put(a, ref, "x" * 6000))
        if not r["ok"]:
            refused = r
            break
    assert refused is not None and refused["code"] == "too_large" and refused["detail"]["what"] == "table"
    assert refused["detail"]["max"] == 64 * 1024 and refused["detail"]["size"] > 64 * 1024
    exact = len(json.dumps(_room(world).state, ensure_ascii=False, separators=(",", ":")).encode())
    assert exact <= 64 * 1024 + 3 * 7000, "at most one message past the bound"
    assert a.do({"op": "clear", **a.at("A2")})["ok"], "clearing is always allowed"
    a.close()


def test_edits_wait_while_an_outside_version_is_folded_in(world, monkeypatch):
    """Review 7: folding a version in (0.6 s at 50 000 cells) ran on the event loop; it runs in a
    worker thread now, and the room answers `busy` until it is done."""
    started, release = threading.Event(), threading.Event()
    real = cs.reconcile

    def slow(state, editor, *, by):                             # fixture: a large table takes time to fold in
        started.set()
        assert release.wait(10)
        return real(state, editor, by=by)
    monkeypatch.setattr(cs, "reconcile", slow)
    a = join(world, "editor")
    threading.Timer(3, release.set).start()                      # (the old code blocked the loop instead)
    r = machine_put(world, edited({"C3": {"v": "外部"}}), 1)
    assert r.status_code == 200
    assert started.wait(10)
    n = a.send(put(a, "A2", "折叠期间"))
    r = a.ack(n)
    assert r["ok"] is False and r["code"] == "busy" and 0 < r["detail"]["retry_after"] < 1
    release.set()
    assert a.patch()["outside"]["head"] == 2
    time.sleep(r["detail"]["retry_after"])
    r = a.do(put(a, "A2", "折叠期间"), n=n)
    assert r["ok"] is True and a.cells()["C3"] == {"v": "外部"} and a.cells()["A2"] == {"v": "折叠期间"}
    a.close()


def test_a_draft_write_finishing_after_a_save_does_not_mark_the_room_unsaved_again(world, monkeypatch):
    """Review 8: a draft write that started before a save finished after it and put back the dirty
    flag it had computed for the state before the save."""
    from synwevo_docs import collab_sheet_room as R
    tid, co = world["tid"], world["co"]
    writing, release = threading.Event(), threading.Event()
    real = R.SheetRoom._write
    first: list = []

    def slow_write(self, snap):                                  # fixture: the first draft write is slow
        if not first:
            first.append(snap)
            writing.set()
            assert release.wait(10)
        return real(self, snap)
    monkeypatch.setattr(R.SheetRoom, "_write", slow_write)
    a = join(world, "editor")
    assert a.do(put(a, "A2", "值"))["ok"]
    assert writing.wait(5)
    r = co.post(f"/api/templates/{tid}/collab/save", json={})
    assert r.status_code == 200 and r.json()["live"] == {"seq": 1, "dirty": False}
    release.set()
    time.sleep(0.5)
    assert _room(world).dirty is False and world["app"].state.collab.live_state(tid)["dirty"] is False
    r = co.post(f"/api/templates/{tid}/undo", json={"head": 2})
    assert r.status_code == 200, r.text
    a.close()


def test_a_join_racing_the_last_leave_opens_the_room_again(world, monkeypatch):
    """Review 9: a tab whose handshake got the room just before its last person left joined that
    closed room — alone, unreachable, its edits in no draft anyone would open."""
    import asyncio

    from synwevo_docs.collab import CollabHub
    hub, key = world["app"].state.collab, f"template:{world['tid']}"
    a = join(world, "editor")
    first = hub.rooms[key]
    real = CollabHub._room
    calls = {"n": 0}

    async def racing_room(self, tid, tenant):                   # fixture: the old tab leaves between the lookup and the join
        room = await real(self, tid, tenant)
        calls["n"] += 1
        if calls["n"] == 1:
            for _ in range(200):
                if self.rooms.get(key) is not room:
                    break
                await asyncio.sleep(0.01)
        return room
    monkeypatch.setattr(CollabHub, "_room", racing_room)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("b", join(world, "editor")))
    t.start()
    time.sleep(0.2)
    a.close()
    t.join(10)
    b = box["b"]
    assert hub.rooms[key] is not first and any(c for c in hub.rooms[key].clients), "the tab is in the open room"
    assert b.do(put(b, "A2", "同一个房间"))["ok"]
    assert cs.project(hub.rooms[key].state)["sheets"][0]["cells"]["A2"] == {"v": "同一个房间"}
    c = join(world, "owner")
    assert c.cells()["A2"] == {"v": "同一个房间"}
    b.close()
    c.close()


def test_a_fault_inside_an_operation_leaves_the_table_as_it_was(world, monkeypatch):
    """Review 10: an unexpected exception half-way through a group left its first writes in the state."""
    real = cs._write_cell

    def faulty(tx, sid, ck, cell):                              # fixture: a fault after a write
        real(tx, sid, ck, cell)
        if (cell or {}).get("v") == "故障":
            raise RuntimeError("fixture fault")
    monkeypatch.setattr(cs, "_write_cell", faulty)
    a = join(world, "editor")
    r = a.do(tx=[put(a, "A2", "先写的"), put(a, "A3", "故障")])
    assert r["ok"] is False and r["code"] == "error"
    held = cs.project(_room(world).state)["sheets"][0]["cells"]
    assert "A2" not in held and "A3" not in held and _room(world).state["seq"] == 0
    assert a.do(put(a, "C3", "之后照常"))["ok"] and a.model() == cs.project(_room(world).state)
    a.close()


def test_a_save_whose_room_loop_is_slow_to_answer_still_answers(world, monkeypatch):
    """Review 10: when the room's loop did not answer within the wait, the save marked itself on the
    worker thread, whose frames could not be sent (RuntimeError, a 500 after the version was written)."""
    from synwevo_docs import collab_sheet_room as R
    monkeypatch.setattr(R, "SAVE_WAIT_S", 0.2, raising=False)
    a = join(world, "editor")
    assert a.do(put(a, "A2", "慢"))["ok"]
    loop = next(iter(_room(world).clients)).loop
    loop.call_soon_threadsafe(time.sleep, 1.0)                  # fixture: the room's loop is busy for a second
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 200 and r.json()["head"] == 2 and r.json()["live"] == {"seq": 1, "pending": True}, r.text
    assert a.head(2)["dirty"] is False, "marked once the loop got to it"
    a.close()


def test_a_live_table_draft_is_not_reported_while_the_switch_is_off(world, monkeypatch):
    """Review 11: the change list reported a table draft's unsaved edits with the switch off."""
    a = join(world, "editor")
    assert a.do(put(a, "A2", "草稿"))["ok"]
    a.close()
    assert row(world)["dirty"] == 1
    monkeypatch.delenv("SYNWEVO_SHEET_LIVE")
    assert world["co"].get(f"/api/templates/{world['tid']}/collab/changes").json()["dirty"] is False
    r = machine_put(world, edited({"A3": {"v": "开关关闭"}}), 1)
    assert r.status_code == 200, r.text


def test_one_persons_many_tabs_cannot_push_out_anothers_resume(world, monkeypatch):
    """Review 11: one writer opening many client ids evicted everyone else's remembered messages."""
    monkeypatch.setattr(cs, "MAX_CLIENTS", 10)                  # fixture: a small room memory
    e = join(world, "editor", client_id="editor-tab-1")
    op = put(e, "A2", "编辑者")
    n = e.send(op)
    assert e.ack(n)["ok"]
    for i in range(11):
        o = join(world, "owner", client_id=f"owner-tab-{i:02d}")
        assert o.do(put(o, "C3", str(i)))["ok"]
        o.close()
    owners = [k for k in _room(world).state["clients"] if k.startswith(world["owner"]["id"] + "/")]
    assert len(owners) <= 8
    r = e.do(op, n=n, expect=0)                                  # its answer was lost: still recognised
    assert r["ok"] is True and r["duplicate"] is True
    e.close()


# ------------------------------------------------------------------ the second review of step ② (each failed before its fix)
def _work_loop(monkeypatch):
    """Fixture: one event loop of its own for the room's queued work (production has one loop for
    everything; here the test decides when that work runs)."""
    import asyncio

    from synwevo_docs import collab_sheet_room as R
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    monkeypatch.setattr(R.SheetRoom, "work_loop", lambda self: loop)
    return loop


def test_a_fold_finishing_after_its_room_closed_does_not_write_over_the_next_room(world, monkeypatch):
    """Second review, 1: a version folded in on a worker thread finished after the room's last person
    left and a newer room had opened, taken edits and closed; the old room then wrote its state over
    the newer draft a second later — the newer room's unsaved edit was gone."""
    loop = _work_loop(monkeypatch)
    started, release = threading.Event(), threading.Event()
    real = cs.reconcile
    calls: list = []

    def slow(state, editor, *, by):                             # fixture: the first fold is slow
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(20)
        return real(state, editor, by=by)
    monkeypatch.setattr(cs, "reconcile", slow)
    e = join(world, "editor")
    assert machine_put(world, edited({"C3": {"v": "外部"}}), 1).status_code == 200
    assert started.wait(10)
    e.close()                                                    # the last one out, while the fold runs
    e2 = join(world, "editor")                                   # a newer room, from version 2
    assert e2.hello["head"] == 2
    assert e2.do(put(e2, "A2", "新房间里的修改"))["ok"]
    e2.close()
    d = row(world)
    assert d["dirty"] == 1 and d["epoch"] == e2.hello["epoch"]
    release.set()
    time.sleep(2.0)                                              # the closed room's fold ends; it may not write
    d = row(world)
    assert d["epoch"] == e2.hello["epoch"] and d["dirty"] == 1
    e3 = join(world, "editor")
    assert e3.cells()["A2"] == {"v": "新房间里的修改"}
    e3.close()
    loop.call_soon_threadsafe(loop.stop)


def test_a_save_marked_after_its_room_closed_does_not_write_over_the_next_room(world, monkeypatch):
    """Second review, 1: a save whose room loop answered late (``live.pending``) was marked after the
    room had closed and a newer one had taken edits; its write replaced the newer draft."""
    from synwevo_docs import collab_sheet_room as R
    loop = _work_loop(monkeypatch)
    monkeypatch.setattr(R, "SAVE_WAIT_S", 0.3)
    e = join(world, "editor")
    assert e.do(put(e, "A2", "保存前"))["ok"]
    gate = threading.Event()
    loop.call_soon_threadsafe(gate.wait, 20)                    # fixture: the room's loop is busy
    r = world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={})
    assert r.status_code == 200 and r.json()["live"]["pending"] is True, r.text
    e.close()
    e2 = join(world, "editor")
    assert e2.do(put(e2, "A3", "新房间里的修改"))["ok"]
    e2.close()
    assert "A3" in cs.project(json.loads(zlib.decompress(bytes(row(world)["state"]))))["sheets"][0]["cells"]
    gate.set()                                                   # the old room marks its save now
    time.sleep(2.0)
    kept = cs.project(json.loads(zlib.decompress(bytes(row(world)["state"]))))["sheets"][0]["cells"]
    assert kept["A3"] == {"v": "新房间里的修改"} and row(world)["dirty"] == 1
    loop.call_soon_threadsafe(loop.stop)


def test_the_closing_write_runs_in_a_thread_outside_the_room_lock(world, monkeypatch):
    """Second review, 7: the last one out serialised, rendered and compressed the whole table on the
    event loop while holding the lock every join and leave needs (≈0.5 s at 17 MiB)."""
    from synwevo_docs import collab_sheet_room as R
    hub = world["app"].state.collab
    seen: list = []
    real = R.SheetRoom._write

    def spy(self, snap, **kw):
        seen.append((threading.current_thread().name, hub._rooms_mu._is_owned()))
        return real(self, snap, **kw)
    monkeypatch.setattr(R.SheetRoom, "_write", spy)
    a = join(world, "editor")
    assert a.do(put(a, "A2", "最后离开"))["ok"]
    a.close()
    assert row(world)["dirty"] == 1
    name, locked = seen[-1]
    assert name.startswith("collab-close-") and locked is False, seen


def test_a_restart_serialises_the_table_once_for_everyone(world, monkeypatch):
    """Second review, 7: a restart serialised the whole content once per connected tab."""
    from synwevo_docs import collab_sheet_room as R
    count = {"in": False, "n": 0}
    real_dumps, real_restarted = R._dumps, R.SheetRoom._restarted

    def dumps(x):
        if count["in"] and isinstance(x, dict) and ("content" in x or {"order", "sheets"} <= set(x)):
            count["n"] += 1
        return real_dumps(x)

    def restarted(self, *a, **kw):
        count["in"] = True
        try:
            return real_restarted(self, *a, **kw)
        finally:
            count["in"] = False
    monkeypatch.setattr(R, "_dumps", dumps)
    monkeypatch.setattr(R.SheetRoom, "_restarted", restarted)
    peers = [join(world, who) for who in ("owner", "editor", "viewer")]
    assert peers[1].do(put(peers[1], "A2", "未保存"))["ok"]
    assert machine_put(world, edited({"A3": {"v": "新"}}), 1, discard_live=True).status_code == 200
    resets = [p.of("reset") for p in peers]
    assert len({r["epoch"] for r in resets}) == 1 and all(p.cells()["A3"] == {"v": "新"} for p in peers)
    assert count["n"] == 1, count
    for p in peers:
        p.close()


def test_an_outside_version_over_the_room_s_cell_cap_takes_a_clean_room_out_of_live_mode(world, monkeypatch):
    """Second review, 2: a version folded in, and a draft loaded, were held live beyond the room's own
    cell cap (SYNWEVO_SHEET_LIVE_MAX_CELLS) — also after reopening."""
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_CELLS", "3")
    a = join(world, "editor")
    assert a.hello["live"] is True
    big = edited({f"C{i}": {"v": f"x{i}"} for i in range(1, 4)} | {"A2": {"v": "a"}, "A3": {"v": "b"}, "B2": {"v": "c"}})
    assert machine_put(world, big, 1).status_code == 200
    left = a.meta_of("live")
    assert left["live"] is False and left["reason"]["what"] == "cells" and left["reason"]["max"] == 3
    assert not _room(world).live() and row(world) is None
    a.close()
    b = join(world, "editor")
    assert b.hello["live"] is False and b.hello["live_reason"]["what"] == "cells"
    b.close()


def test_an_outside_version_over_the_room_s_byte_cap_takes_a_clean_room_out_of_live_mode(world, monkeypatch):
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_BYTES", str(8 * 1024))
    a = join(world, "editor")
    assert a.hello["live"] is True
    assert machine_put(world, edited({"A2": {"v": "x" * 20000}}), 1).status_code == 200
    left = a.meta_of("live")
    assert left["live"] is False and left["reason"]["what"] == "bytes"
    r = a.do(put(a, "A1", "改一个已有的格子"), n=a.next, expect=0)
    assert r["ok"] is False and r["code"] == "not_live"
    a.close()


def test_a_version_over_the_caps_keeps_unsaved_edits_and_the_guard_says_why(world, monkeypatch):
    """Second review, 2: over the caps behaves like a table the room cannot hold — kept (`stale`) when
    edits are unsaved, and refused up front for a saver inside the room."""
    from synwevo_docs import builtin_service
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_CELLS", "4")
    tid = world["tid"]
    o = join(world, "owner")
    e = join(world, "editor")
    o.meta_of("joined")
    assert e.do(put(e, "C3", "未保存"))["ok"]
    o.patch()
    five = edited({"A2": {"v": 1}, "A3": {"v": 2}, "B2": {"v": 3}})
    r = world["client_for"](world["owner"]).put(f"/api/templates/{tid}/builtin", json={"editor": five, "base_version": 1, "solo": True})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "cannot_fold", r.text
    o.close()
    # the race: clean when the guard looked, typed during the build
    assert world["co"].post(f"/api/templates/{tid}/collab/save", json={}).json()["head"] == 2
    started, release = threading.Event(), threading.Event()
    real = builtin_service._build

    def slow(kind, editor):                                      # fixture: the outside version is being built
        started.set()
        assert release.wait(10)
        return real(kind, editor)
    monkeypatch.setattr(builtin_service, "_build", slow)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("r", machine_put(world, five, 2)))
    t.start()
    assert started.wait(10)
    assert e.do(put(e, "A1", "构建期间"))["ok"]
    release.set()
    t.join(20)
    assert box["r"].status_code == 200
    kept = e.meta_of("live")
    assert kept["stale"] is True and kept["reason"]["what"] == "cells"
    assert _room(world).live() and e.cells()["A1"] == {"v": "构建期间"}
    e.close()


def test_a_draft_over_the_caps_is_kept_while_it_holds_edits_and_not_held_live_after(world, monkeypatch):
    """Second review, 2: a loaded draft beyond the caps stayed live (lowered caps, or folded in before
    they were checked)."""
    a = join(world, "editor")
    for ref in ("A2", "A3", "B2"):
        assert a.do(put(a, ref, ref))["ok"]
    a.close()
    monkeypatch.setenv("SYNWEVO_SHEET_LIVE_MAX_CELLS", "3")
    b = join(world, "editor")
    assert b.hello["live"] is True and b.hello["dirty"] is True and b.cells()["B2"] == {"v": "B2"}, "unsaved edits are kept"
    r = b.do(put(b, "C3", "再多一格"))
    assert r["ok"] is False and r["code"] == "too_large"
    assert world["co"].post(f"/api/templates/{world['tid']}/collab/save", json={}).status_code == 200
    b.close()
    c = join(world, "editor")
    assert c.hello["live"] is False and c.hello["live_reason"]["what"] == "cells"
    c.close()


def test_one_persons_tabs_opened_first_and_written_later_keep_their_share(world, monkeypatch):
    """Second review, 4: the per-person bound was checked only when a tab joined — tabs that all joined
    first and wrote afterwards pushed everyone else's remembered messages out."""
    monkeypatch.setattr(cs, "MAX_CLIENTS", 10)                  # fixture: a small room memory
    e = join(world, "editor", client_id="editor-tab-1")
    op = put(e, "A2", "编辑者")
    n = e.send(op)
    assert e.ack(n)["ok"]
    tabs = [join(world, "owner", client_id=f"owner-tab-{i:02d}") for i in range(11)]
    for i, o in enumerate(tabs):
        r = o.do(put(o, "C3", str(i)), expect=_room(world).state["seq"])
        if not r["ok"] and r["code"] == "resync":                # the person's own oldest tab was forgotten: renumber
            r = o.do(put(o, "C3", str(i)), n=r["detail"]["next"], expect=_room(world).state["seq"])
        assert r["ok"], r
    owners = [k for k in _room(world).state["clients"] if k.startswith(world["owner"]["id"] + "/")]
    assert len(owners) == cs.MAX_CLIENTS_PER_SCOPE
    r = e.do(op, n=n, expect=0)
    assert r["ok"] is True and r["duplicate"] is True, "the editor's lost answer is still recognised"
    assert join(world, "viewer").hello["next"] == 1, "nobody else's numbers moved"
    for o in tabs:
        o.close()
    e.close()


def test_a_message_number_far_ahead_cannot_push_everyone_s_numbers_past_a_browser(world):
    """Second review, 3: n = 2**60 was accepted; once that tab was forgotten, every new tab was told a
    `next` JavaScript cannot represent, in a loop of resyncs, persisted in the draft."""
    o = join(world, "owner", client_id="owner-tab-big")
    r = o.do(put(o, "A2", "大号"), n=2 ** 60)
    assert r["ok"] is False and r["code"] == "resync" and r["detail"] == {"reason": "number_ahead", "next": 1}
    for i in range(cs.MAX_CLIENTS_PER_SCOPE + 1):
        t = join(world, "owner", client_id=f"owner-tab-x{i}")
        assert t.do(put(t, "C3", str(i)), expect=_room(world).state["seq"])["ok"]
        t.close()
    fresh = join(world, "editor")
    assert fresh.hello["next"] == 1
    assert max(rec["hi"] for rec in _room(world).state["clients"].values()) < 2 ** 53
    o.close()
    fresh.close()


def test_the_catch_up_of_an_old_draft_runs_before_newer_versions(world, monkeypatch):
    """Second review, 5: a dirty draft left at an older version folded that version in after the
    newer ones that arrived while the room opened — the room ended at the older version."""
    from synwevo_docs import builtin_service
    from synwevo_docs import collab_sheet_room as R
    tid = world["tid"]
    e = join(world, "editor")
    started, release = threading.Event(), threading.Event()
    real_build = builtin_service._build

    def slow(kind, editor):                                      # fixture: v2 is built while someone types
        started.set()
        assert release.wait(10)
        return real_build(kind, editor)
    monkeypatch.setattr(builtin_service, "_build", slow)
    far = edited({"A250000": {"v": "远处"}})
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("r", world["co"].put(f"/api/templates/{tid}/builtin",
                                                                            json={"editor": far, "base_version": 1, "solo": True})))
    t.start()
    assert started.wait(10)
    assert e.do(put(e, "C3", "构建期间输入"))["ok"]
    release.set()
    t.join(20)
    assert box["r"].status_code == 200 and e.meta_of("live")["stale"] is True
    e.close()
    monkeypatch.setattr(builtin_service, "_build", real_build)
    assert row(world)["head"] == 1 and row(world)["dirty"] == 1
    s2, r2 = threading.Event(), threading.Event()
    real_ls = R.SheetRoom.load_sheet

    def slow_ls(self, tmpl, row_, eoh):                          # fixture: the room is being opened
        s2.set()
        assert r2.wait(10)
        return real_ls(self, tmpl, row_, eoh)
    monkeypatch.setattr(R.SheetRoom, "load_sheet", slow_ls)
    box2: dict = {}
    th = threading.Thread(target=lambda: box2.setdefault("a", join(world, "editor")))
    th.start()
    assert s2.wait(10)
    r = machine_put(world, edited({"C2": {"v": "第三版"}}), 2, discard_live=True)
    assert r.status_code == 200 and r.json()["head"] == 3, r.text
    r2.set()
    th.join(10)
    a = box2["a"]
    for _ in range(50):
        if _room(world).head == 3 and not _room(world).holding():
            break
        time.sleep(0.1)
    room = _room(world)
    assert room.head == 3 and room.live() and cs.project(room.state)["sheets"][0]["cells"]["C2"] == {"v": "第三版"}
    a.close()


def test_a_template_deleted_while_its_room_opens_leaves_no_room_behind(world, monkeypatch):
    """Second review, 6: a template deleted after its room read it but before anyone joined left a
    `gone` room with nobody in it registered for ever (holding its whole table)."""
    from synwevo_docs import collab_sheet_room as R
    hub, key = world["app"].state.collab, f"template:{world['tid']}"
    started, release = threading.Event(), threading.Event()
    real = R.SheetRoom.load_sheet

    def slow(self, tmpl, row_, eoh):                             # fixture: opening the room takes a while
        real(self, tmpl, row_, eoh)
        started.set()
        assert release.wait(10)
    monkeypatch.setattr(R.SheetRoom, "load_sheet", slow)
    box: dict = {}

    def opener():
        try:
            join(world, "editor")
        except WebSocketDisconnect as x:
            box["code"] = x.code
    t = threading.Thread(target=opener)
    t.start()
    assert started.wait(10)
    assert world["co"].delete(f"/api/templates/{world['tid']}").status_code == 200
    release.set()
    t.join(10)
    assert box.get("code") == 4404
    assert key not in hub.rooms
    with pytest.raises(WebSocketDisconnect) as x:
        join(world, "owner")
    assert x.value.code == 4404 and key not in hub.rooms
