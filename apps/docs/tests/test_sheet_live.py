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
            self.content, self.seq = p["content"], p["seq"]
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

    def send(self, op=None, *, tx=None, n=None, expect="seen") -> int:
        if n is None:
            n, self.next = self.next, self.next + 1
        f = {"type": "op", "n": n, "expect": self.seq if expect == "seen" else expect}
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
    b.frame({"type": "op", "n": 1, "expect": 1, "op": put(b, "C1", "金额")})
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
    # and nothing about the live room leaks to another workspace over HTTP either
    stranger = world["client_for"](world["stranger"], world["other_wid"])
    a = join(world, "owner")
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
    # a person over their share of the room is told when to try again; the message is not spent
    world["app"].state.collab.sheet_rate = RateBuckets(3)
    for v in ("1", "2", "3"):
        assert b.do(put(b, "A2", v))["ok"]
    n = b.send(put(b, "A2", "4"))
    r = b.ack(n)
    assert r["ok"] is False and r["code"] == "rate_limited" and r["detail"]["retry_after"] > 0
    world["app"].state.collab.sheet_rate = RateBuckets(600)
    r = b.do(put(b, "A2", "4"), n=n)
    assert r["ok"] is True and r.get("duplicate") is None, "a refused-for-rate message is applied when resent"
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
        # and a second tab of the editor asking for the id in use gets a fresh one
        a3 = SheetPeer(world["client_for"](world["editor"], the_app=app2), tid, client_id="tab-editor-1")
        assert a3.hello["client"] != "tab-editor-1"
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
