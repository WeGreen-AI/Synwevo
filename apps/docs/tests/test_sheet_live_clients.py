"""Real browser sync engines against the real live table room (docs-sheet-live.md §6, §9).

Each tab is ``SheetSync`` itself (``apps/docs/web/src/lib/sheetsync.ts``, in Node); the room is
``SheetRoom`` with its operations model; the driver (``sheet_live_clients.py``) delivers every frame in
the interleaving it picks. The first part holds the findings of the review of steps ③–⑤ (H1–H10), each
a reproducer that failed on 0a6c0cd; the second drives three tabs at random — busy windows, rate
limits, dropped connections with frames on the wire, lost answers, forgotten tabs, saves, outside
versions and servers that die without their closing write — and then checks, besides convergence:

* every tab shows exactly the room's content once everything is delivered, nothing left pending;
* the room's own invariants hold, and no added row or column was applied twice;
* what a tab believes applied was applied and kept (a crash that lost it re-applied it or told the
  person) — nothing lost silently (H4);
* one tab's messages apply in the order it sent them (H1);
* undo never deleted a sheet another tab had changed since (H9).

Runs where Node and the workspace's node_modules are installed (CI's verify job runs it); skipped
elsewhere unless ``SYNWEVO_SHEET_CLIENTS=required``. ``SYNWEVO_SHEET_FUZZ_SEEDS`` sets how many seeds
each random run takes (default 12)."""
from __future__ import annotations

import copy
import json
import os
import random
import secrets
import time

import pytest

from .sheet_live_clients import Harness, Node, Tab, diff, node_missing
from .test_sheet_live import clock, machine_put, world  # noqa: F401 — fixtures

_why = node_missing()
if _why:
    if os.environ.get("SYNWEVO_SHEET_CLIENTS") == "required":
        raise RuntimeError(f"the real-room client tests must run here, but {_why}")
    pytest.skip(f"real-room client tests need Node: {_why}", allow_module_level=True)

from synwevo_core.http import RateBuckets  # noqa: E402
from synwevo_docs import collab_sheet as CS  # noqa: E402
from synwevo_docs import collab_sheet_room as R  # noqa: E402

SEEDS = int(os.environ.get("SYNWEVO_SHEET_FUZZ_SEEDS", "12"))

# TEST FIXTURE: a two-sheet table with a field
TABLE = {"sheets": [
    {"name": "结果", "cols": [{"w": 100}] * 4, "rows": [{"h": 24}] * 4,
     "cells": {"A1": {"v": "客户"}, "B1": {"v": "待填"}, "C3": {"v": "x"}}, "merges": [],
     "fields": [{"id": "customer", "kind": "cell", "name": "客户", "ref": "B1", "required": True}]},
    {"name": "明细", "cols": [{"w": 100}] * 3, "rows": [{"h": 24}] * 3, "cells": {"A1": {"v": "a"}}, "merges": [], "fields": []},
]}


@pytest.fixture()
def env(world, monkeypatch):  # noqa: F811
    """The room of a fresh table template, every operation it applies logged; drafts are not written
    (a crash is simulated by putting an earlier state back); no rate limit unless a test sets one."""
    applied: list[dict] = []
    real_apply = CS.apply

    def logged(state, op, **kw):
        res = real_apply(state, op, **kw)
        changed = bool(res["ok"] and res.get("patch") and not res.get("duplicate"))
        touched = sorted({e["p"][1] for e in (res.get("patch") or []) if e["p"][0] == "sheets" and len(e["p"]) > 1}) if changed else []
        applied.append({"client": kw.get("client"), "n": kw.get("n"), "expect": kw.get("expect"),
                        "op": json.dumps(op, sort_keys=True, ensure_ascii=False), "ok": res["ok"], "dup": bool(res.get("duplicate")),
                        "code": res.get("code"), "changed": changed, "seq": res.get("seq"), "epoch": state["epoch"], "sheets": touched})
        return res
    monkeypatch.setattr(CS, "apply", logged)
    monkeypatch.setattr(R.SheetRoom, "schedule_persist", lambda self: None)
    world["app"].state.collab.sheet_rate = RateBuckets(10 ** 7)
    t = world["co"].post("/api/templates/builtin", json={"kind": "builtin_sheet", "editor": TABLE, "name": "实时客户端"})
    assert t.status_code == 200, t.text
    world = {**world, "tid": t.json()["id"]}
    from .test_sheet_live import join
    holder = join(world, "owner")                    # a real socket keeps the room registered
    room = world["app"].state.collab.rooms[f"template:{world['tid']}"]
    node = Node()
    yield {"world": world, "room": room, "node": node, "applied": applied}
    node.close()
    holder.close()


def reload_from(room, draft: dict) -> None:
    """TEST FIXTURE: the process died without its closing write (every tab has dropped); the next one
    opens the room from the last draft — as a fresh load of the room does: from storage, at the
    draft's seq, under a new load id."""
    room.state = draft
    room.persisted_seq = draft["seq"]
    room.boot = f"rb_{secrets.token_hex(6)}"
    room.loaded_seq = draft["seq"]


def _key(op) -> str:
    return json.dumps(op, sort_keys=True, ensure_ascii=False)


def check(h: Harness, applied: list[dict]) -> tuple[list[str], list[dict]]:
    """Deliver everything, then every property of the module docstring; the problems found."""
    st = h.quiesce()
    problems = []
    srv = h.room.state["content"]
    for t, s in zip(h.tabs, st):
        if s["pending"]:
            problems.append(f"{t.name}: still pending {s['pending'][:3]}")
        if s["live"] and not s["closed"]:
            d = diff(s["content"], srv)
            if d:
                problems.append(f"{t.name}: diverged: " + "; ".join(d))
    inv = CS.sheet_invariants(CS.project(h.room.state))
    if inv:
        problems.append(f"room invariants: {inv[:3]}")
    kept = [a for a in applied if not a.get("lost")]
    counts: dict[str, int] = {}
    for a in kept:
        if a["changed"] and ('"append_rows"' in a["op"] or '"append_cols"' in a["op"]):
            counts[a["op"]] = counts.get(a["op"], 0) + 1
    twice = [k for k, v in counts.items() if v > 1]
    if twice:
        problems.append(f"applied twice: {twice[:2]}")
    ok_ops = {a["op"] for a in kept if a["ok"]}
    for t in h.tabs:
        fates = t.fates()
        told = {_key(f["op"]) for f in fates if ":" in str(f["fate"])}          # the person was told about it
        for f in fates:
            k = _key(f["op"])
            if f["fate"] == "applied?" and f["op"] is not None and k not in ok_ops and k not in told:
                problems.append(f"{t.name}: op {f['id']} believed applied but not in the room: {k[:160]}")
    # one tab's messages apply in the order it sent them (per epoch: numbering starts again)
    last: dict[tuple, int] = {}
    for a in kept:
        if not a["changed"]:
            continue
        k = (a["client"], a["epoch"])
        if k in last and a["n"] <= last[k]:
            problems.append(f"out of order: {a['client']} applied n={a['n']} after n={last[k]} ({a['op'][:100]})")
        last[k] = a["n"]
    # undo never deleted a sheet another tab changed since the change it undid
    for t in h.tabs:
        if not t.client_id:
            continue
        for u in t.undos():
            ops = u["op"]["ops"] if u["op"].get("op") == "tx" else [u["op"]]
            gone = {o["sheet"] for o in ops if o.get("op") == "sheet_delete"}
            if not gone:
                continue
            hit = [a for a in kept if a["changed"] and a["op"] == _key(u["op"]) and str(a["client"]).endswith(t.client_id) and a["epoch"] == u["epoch"]]
            for a in hit:
                others = [b for b in kept if b["changed"] and b["epoch"] == a["epoch"] and u["undoneSeq"] < b["seq"] < a["seq"]
                          and not str(b["client"]).endswith(t.client_id) and gone & set(b["sheets"])]
                if others:
                    problems.append(f"{t.name}: undo deleted sheet {sorted(gone)} that another tab changed since: {others[0]['op'][:100]}")
    return problems, st


def _wait_idle(room, s=10.0):
    end = time.time() + s
    while room.has_work() and time.time() < end:
        time.sleep(0.01)


def run(env, clock, seed, steps=250, *, crash=False, outside=False, saves=True, churn=True, busy=True, rate=None,  # noqa: F811
        disconnects=True, kinds=None):
    """Three tabs (two people, one of them twice) at random for ``steps`` steps; then ``check``."""
    rnd = random.Random(seed)
    h = Harness(env["world"], env["room"], env["node"], clock)
    env["node"](cmd="seed", seed=seed)
    if rate:
        env["world"]["app"].state.collab.sheet_rate = RateBuckets(rate)      # fixture: a tight limit on the fake clock
    ts = [h.tab("t1", "owner"), h.tab("t2", "editor"), h.tab("t3", "owner")]
    for t in ts:
        t.connect()
        t.pump()
    crashed, ghosts, events = False, 0, []
    for step in range(steps):
        t = rnd.choice(ts)
        x = rnd.random()
        if x < 0.28:
            t.act(kinds)
        elif x < 0.55:
            if t.online and t.out:
                t.to_server()
        elif x < 0.82:
            if t.online and t.conn.inbox:
                t.to_client()
        elif x < 0.86:
            if t.online and disconnects:
                t.disconnect(server_notices=rnd.random() < 0.7, deliver_prefix=rnd.randint(0, 3))
            elif not t.online:
                t.connect()
        elif x < 0.89:
            if not t.online:
                t.connect()
            h.advance(rnd.choice([0.05, 0.3, 1.5]))
            t.flush()
        elif x < 0.91 and busy:
            # a version being folded in for a moment: frames meanwhile are answered busy
            h.room.hold()
            for _ in range(rnd.randint(0, 3)):
                u = rnd.choice(ts)
                if u.online and u.out:
                    u.to_server()
            h.room.release()
            events.append((step, "busy"))
        elif x < 0.93 and saves:
            r = h.world["co"].post(f"/api/templates/{h.world['tid']}/collab/save", json={})
            _wait_idle(h.room)
            events.append((step, "save", r.status_code))
        elif x < 0.95 and churn:
            # the owner's other tabs come and go (the room's memory of the owner's older tabs moves on)
            ghosts += 1
            g = Tab(h, f"g{ghosts}", h.world["owner"])
            g.connect()
            g.pump()
            g.act(["set"])
            g.pump()
            g.disconnect(server_notices=True)
        elif x < 0.96 and crash and not crashed and step > 20:
            # the server dies without its closing write: the next one starts from the last draft
            crashed = True
            draft = copy.deepcopy(h.room.state)
            for u in ts:
                if u.online:
                    for _ in range(rnd.randint(0, 2)):
                        if u.out:
                            u.to_server()
            for u in ts:
                if u.online:
                    u.disconnect(server_notices=True)
            for c in list(h.lingering):
                h.room.clients.discard(c)
            h.lingering.clear()
            reload_from(h.room, draft)
            for a in env["applied"]:
                if a["changed"] and a["epoch"] == draft["epoch"] and a["seq"] > draft["seq"]:
                    a["lost"] = True
            events.append((step, "crash"))
        elif x < 0.97 and outside:
            # someone saves alone (solo) or discards the live edits (a new epoch)
            ed = CS.project(h.room.state)
            ed["sheets"][0].setdefault("cells", {})["D4"] = {"v": f"outside{step}"}
            head = h.room.head
            if rnd.random() < 0.5:
                r = h.world["co"].put(f"/api/templates/{h.world['tid']}/builtin", json={"editor": ed, "base_version": head, "solo": True})
            else:
                r = machine_put(h.world, ed, head, discard_live=True)
            _wait_idle(h.room)
            events.append((step, "outside", r.status_code))
    problems, st = check(h, env["applied"])
    return problems, events


STRUCT = ["set", "clear", "merge", "merge", "unmerge", "field_add", "field_add", "field_set", "field_del", "sheet_add", "sheet_rename",
          "sheet_delete", "sheet_delete", "sheet_restore", "append_rows", "grow_set", "delete_sel", "undo", "undo", "undo", "answer"]


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_with_busy_windows_saves_and_dropped_connections(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 5000 + seed)
    assert not problems, f"seed {5000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_under_a_tight_rate_limit(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 6000 + seed, rate=120)
    assert not problems, f"seed {6000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_with_outside_versions_and_restarts(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 7000 + seed, outside=True)
    assert not problems, f"seed {7000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_across_a_server_that_dies_without_its_closing_write(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 9000 + seed, crash=True)
    assert not problems, f"seed {9000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_structure_undo_and_answers(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 11000 + seed, steps=300, kinds=STRUCT, crash=seed % 2 == 1)
    assert not problems, f"seed {11000 + seed}, events {events}\n" + "\n".join(problems)


# ------------------------------------------------------------------ the review's findings (each failed on 0a6c0cd)
def _cell(content, sid, rid, cid):
    return (content["sheets"][sid]["cells"].get(f"{rid}:{cid}") or {}).get("v")


def _grid(room, sheet=0):
    st = room.state["content"]
    sid = st["order"][sheet]
    sh = st["sheets"][sid]
    return sid, sh["rows"], sh["cols"]


def _two(env):
    h = Harness(env["world"], env["room"], env["node"])
    a, b = h.tab("a", "owner"), h.tab("b", "editor")
    for t in (a, b):
        t.connect()
        t.pump()
    return h, a, b


def test_H1_a_busy_message_is_not_overtaken_by_the_next_one(env, clock):  # noqa: F811
    """busy: the tab's next message, on its way before the refusal reached it and arriving after the
    wait, was applied first — then the refused one, sent again, overwrote the later value."""
    h = Harness(env["world"], env["room"], env["node"], clock)
    t1 = h.tab("t1", "editor")
    t1.connect()
    t1.pump()
    sid, rows, cols = _grid(h.room)
    t1.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[0], "v": "first"})
    h.room.hold()
    t1.to_server()                                   # busy
    t1.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[0], "v": "second"})
    h.room.release()
    h.advance(0.3)
    t1.to_server()                                   # after the wait: held for the refused one
    t1.pump()
    for _ in range(3):
        h.advance(1.0)
        t1.flush()
        t1.pump()
    assert _cell(h.room.state["content"], sid, rows[2], cols[0]) == "second", "the value typed last wins"
    assert t1.state()["content"] == h.room.state["content"] and not t1.state()["pending"]


def test_H1b_a_rate_limited_group_is_not_overtaken_by_an_edit_in_its_new_rows(env, clock):  # noqa: F811
    h = Harness(env["world"], env["room"], env["node"], clock)
    h.world["app"].state.collab.sheet_rate = RateBuckets(60)         # fixture: one message a second
    t1 = h.tab("t1", "editor")
    t1.connect()
    t1.pump()
    sid, rows, cols = _grid(h.room)
    for i in range(60):
        t1.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[1], "v": f"x{i}"})
        t1.pump()
    new = ["zzzzzz01", "zzzzzz02"]
    t1.submit({"op": "tx", "ops": [{"op": "append_rows", "sheet": sid, "count": 2, "ids": new, "h": 24},
                                   {"op": "set", "sheet": sid, "row": rows[2], "col": cols[1], "v": "grow"}]})
    t1.submit({"op": "set", "sheet": sid, "row": new[0], "col": cols[1], "v": "typed in the new row"})
    t1.to_server()                                   # rate_limited
    h.advance(1.1)
    t1.to_server()                                   # held: not read before the group
    t1.pump()
    for _ in range(5):
        h.advance(2.0)
        t1.flush()
        t1.pump()
    st = t1.state()
    assert not st["attention"], [(x["kind"], x["code"]) for x in st["attention"]]
    assert _cell(h.room.state["content"], sid, new[0], cols[1]) == "typed in the new row"


def test_H2_use_my_value_on_a_group_first_names_every_cell_it_would_replace(env):
    """A clears B2:B3 (one group) after B wrote both. The room names the first conflict; the person is
    now told about B3 too before choosing — "use my value" then replaces both, as the sentence says.
    (The review's own reproducer asserts the other remedy, resending only the reported cell; the
    decision was to report every conflicting cell — see docs-sheet-live.md §12.)"""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    B2 = {"sheet": sid, "row": rows[1], "col": cols[1]}
    B3 = {"sheet": sid, "row": rows[2], "col": cols[1]}
    a.submit({"op": "set", **B2, "v": "a2"})
    a.submit({"op": "set", **B3, "v": "a3"})
    a.pump()
    b.pump()
    b.submit({"op": "set", **B2, "v": "B's B2"})
    b.submit({"op": "set", **B3, "v": "B's B3"})
    b.pump()
    a.submit({"op": "tx", "ops": [{"op": "clear", **B2}, {"op": "clear", **B3}]})
    a.pump()
    b.pump()
    [c] = [x for x in a.state()["attention"] if x["kind"] == "conflict"]
    assert [x["ref"] for x in c["detail"].get("also", [])] == ["B3"], c["detail"]
    texts = a.texts()
    assert "B3" in texts["zh-Hans"][0] and "B's B3" in texts["zh-Hans"][0]
    assert "B3" in texts["en"][0] and "B3" in texts["zh-Hant"][0]
    a.use_mine(c["id"])
    a.pump()
    b.pump()
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    assert f"{B2['row']}:{B2['col']}" not in cells and f"{B3['row']}:{B3['col']}" not in cells, "what the person chose, having been told"


def test_H3_undo_of_a_merge_that_cleared_values(env):
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    at = lambda r, c: {"sheet": sid, "row": rows[r], "col": cols[c]}  # noqa: E731
    a.submit({"op": "set", **at(3, 0), "v": "top-left"})
    a.submit({"op": "set", **at(3, 1), "v": "hidden"})
    a.pump()
    a.submit({"op": "tx", "ops": [{"op": "clear", **at(3, 1)}, {"op": "merge", "sheet": sid, "range": [rows[3], cols[0], rows[3], cols[1]], "id": "mmmmmm01"}]})
    a.pump()
    a.undo()
    a.pump()
    assert "mmmmmm01" not in h.room.state["content"]["merges"]
    assert _cell(h.room.state["content"], sid, rows[3], cols[1]) == "hidden"
    assert not a.state()["attention"]


def test_H3b_undo_of_a_merge_that_replaced_another(env):
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    b.submit({"op": "merge", "sheet": sid, "range": [rows[2], cols[2], rows[3], cols[2]], "id": "bbbbbb01"})
    b.pump()
    a.pump()
    a.submit({"op": "merge", "sheet": sid, "range": [rows[2], cols[2], rows[3], cols[3]], "id": "aaaaaa01", "replaces": ["bbbbbb01"]})
    a.pump()
    b.pump()
    a.undo()
    a.pump()
    merges = h.room.state["content"]["merges"]
    assert "aaaaaa01" not in merges and len(merges) == 1, merges
    assert not a.state()["attention"]


def test_H4_an_acknowledged_edit_lost_by_a_crash_is_applied_again(env):
    """The tab dropped an edit from its queue on the ack; a crash before the draft write lost it and
    nobody was told. It is kept until `persisted` covers it and sent again after the restart."""
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[2], "v": "persisted"})
    a.pump()
    draft = copy.deepcopy(h.room.state)              # the last draft write
    a.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[2], "v": "typed just before the crash"})
    a.pump()
    assert a.state()["status"] == "live" and a.state()["pending"] == []
    a.disconnect(server_notices=True)
    reload_from(h.room, draft)                             # the next process loads the draft (same epoch)
    a.connect()
    a.pump()
    a.pump()
    st = a.state()
    assert _cell(h.room.state["content"], sid, rows[2], cols[2]) == "typed just before the crash"
    assert _cell(st["content"], sid, rows[2], cols[2]) == "typed just before the crash" and not st["attention"]


def test_H4_an_edit_lost_by_a_crash_is_applied_again_even_after_others_refilled_its_numbers(env):
    """Found by the browser proof of this round: after a crash, whoever reconnects first re-sends its
    own lost edits, which take the sequence numbers again; a tab reconnecting later saw no lower `seq`
    than its own and kept believing in its lost edit. The room now says it was loaded from storage
    (`loaded`: a fresh id and the seq it was loaded at): everything a tab had acknowledged above that
    is sent again, whatever the room's seq is by then."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    draft = copy.deepcopy(h.room.state)                          # the last draft write
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "A before the crash"})
    a.pump()
    b.pump()
    b.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[3], "v": "B before the crash"})
    b.pump()
    a.pump()
    a.disconnect(server_notices=True)
    b.disconnect(server_notices=True)
    reload_from(h.room, draft)
    b.connect()
    b.pump()
    b.pump()                                                     # B is back first: its lost edit takes the next number
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    assert cells.get(f"{rows[3]}:{cols[3]}") == {"v": "A before the crash"}, "A's acknowledged edit came back"
    assert cells.get(f"{rows[2]}:{cols[3]}") == {"v": "B before the crash"}
    assert a.state()["content"] == h.room.state["content"] == b.state()["content"]


def test_H5_undo_pressed_before_typing_on_undoes_the_edit_that_was_on_top(env):
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    A2 = {"sheet": sid, "row": rows[1], "col": cols[0]}
    C2 = {"sheet": sid, "row": rows[1], "col": cols[2]}
    a.disconnect()
    a.submit({"op": "set", **A2, "v": "to be undone"})
    a.undo()
    a.submit({"op": "set", **C2, "v": "keep me"})
    a.connect()
    a.pump()
    a.pump()
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    got = {k: cells.get(f"{x['row']}:{x['col']}", {}).get("v") for k, x in (("A2", A2), ("C2", C2))}
    assert got == {"A2": None, "C2": "keep me"}, got


def test_H5_undo_pressed_while_the_edit_is_on_its_way(env):
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[0], "v": "on its way"})
    assert a.undo() == "deferred"
    a.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[2], "v": "keep me"})
    a.pump()
    a.pump()
    assert _cell(h.room.state["content"], sid, rows[1], cols[0]) is None
    assert _cell(h.room.state["content"], sid, rows[1], cols[2]) == "keep me"


def test_H6_a_conflict_in_a_group_names_the_cell_it_is_about(env):
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    B2 = {"sheet": sid, "row": rows[1], "col": cols[1]}
    B3 = {"sheet": sid, "row": rows[2], "col": cols[1]}
    a.submit({"op": "set", **B2, "v": "a2"})
    a.submit({"op": "set", **B3, "v": "a3"})
    a.pump()
    b.pump()
    b.submit({"op": "set", **B3, "v": "B's B3"})
    b.pump()
    a.submit({"op": "tx", "ops": [{"op": "clear", **B2}, {"op": "clear", **B3}]})
    a.pump()
    b.pump()
    att = [x for x in a.state()["attention"] if x["kind"] == "conflict"][0]
    assert att["label"]["ref"] == "B3"
    assert "B3" in a.texts()["en"][0]


def test_H7_typing_at_the_edge_names_the_cell_and_offers_my_value(env):
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    A4 = {"sheet": sid, "row": rows[3], "col": cols[0]}
    b.submit({"op": "set", **A4, "v": "B's"})
    b.pump()
    a.submit({"op": "tx", "ops": [{"op": "append_rows", "sheet": sid, "count": 5, "ids": [f"gggggg0{i}" for i in range(5)], "h": 24},
                                  {"op": "set", **A4, "v": "mine, typed in the last row"}]})
    a.pump()
    att = a.state()["attention"][0]
    assert att["label"]["ref"] == "A4" and att["label"]["value"] == "mine, typed in the last row"


def test_H8_an_open_tab_that_never_wrote_is_not_told_forgotten(env):
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    for i in range(9):                               # the owner's other tabs come, type and go
        g = h.tab(f"g{i}", "owner")
        g.connect()
        g.pump()
        g.act(["set"])
        g.pump()
        g.disconnect(server_notices=True)
    sid = h.room.state["content"]["order"][0]
    nr = h.room.state["content"]["sheets"][sid]["nr"]
    a.submit({"op": "append_rows", "sheet": sid, "count": 10, "ids": [f"hhhhhh{i:02d}" for i in range(10)], "h": 24})
    a.pump()
    a.pump()
    assert not a.state()["attention"], [(x["kind"], x["code"]) for x in a.state()["attention"]]
    assert h.room.state["content"]["sheets"][sid]["nr"] == nr + 10


def test_H9_undoing_my_new_sheet_leaves_it_when_someone_typed_on_it(env):
    h, a, b = _two(env)
    a.submit({"op": "sheet_add", "id": "nsheet01", "name": "新表", "rows": 3, "cols": 3, "row_ids": ["nr000001", "nr000002", "nr000003"],
              "col_ids": ["nc000001", "nc000002", "nc000003"], "h": 24, "w": 100})
    a.pump()
    b.pump()
    b.submit({"op": "set", "sheet": "nsheet01", "row": "nr000001", "col": "nc000001", "v": "B's work"})
    b.pump()
    a.pump()
    r = a.undo()
    a.pump()
    b.pump()
    assert [s["reason"] for s in r["skipped"]] == ["sheet_changed"] and not r["sent"]
    assert h.room.state["content"]["sheets"]["nsheet01"]["gone"] is False


def test_H9b_a_sheet_deleted_by_mistake_can_be_restored_until_a_save(env):
    """A deleted sheet stays in the room (gone) until the table is saved as a version: anyone can
    restore it (the editor lists them); the save purges only sheets still deleted then."""
    h, a, b = _two(env)
    a.submit({"op": "sheet_add", "id": "nsheet01", "name": "新表", "rows": 3, "cols": 3, "row_ids": ["nr000001", "nr000002", "nr000003"],
              "col_ids": ["nc000001", "nc000002", "nc000003"], "h": 24, "w": 100})
    a.pump()
    b.pump()
    b.submit({"op": "set", "sheet": "nsheet01", "row": "nr000001", "col": "nc000001", "v": "B's work"})
    b.pump()
    a.pump()
    a.submit({"op": "sheet_delete", "sheet": "nsheet01"})
    a.pump()
    b.pump()
    b.submit({"op": "sheet_restore", "sheet": "nsheet01"})          # B restores it (SheetEditor's restore list)
    b.pump()
    a.pump()
    r = h.world["co"].post(f"/api/templates/{h.world['tid']}/collab/save", json={})
    assert r.status_code == 200, r.text
    _wait_idle(h.room)
    sh = h.room.state["content"]["sheets"]["nsheet01"]
    assert sh["gone"] is False and _cell(h.room.state["content"], "nsheet01", "nr000001", "nc000001") == "B's work"


def test_H10_after_a_crash_a_queued_edit_does_not_silently_replace_a_newer_value(env):
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    at = lambda r, c: {"sheet": sid, "row": rows[r], "col": cols[c]}  # noqa: E731
    for i in range(3):
        b.submit({"op": "set", **at(0, 3), "v": f"b{i}"})
        b.pump()
        a.pump()
    draft = copy.deepcopy(h.room.state)
    for i in range(3):
        b.submit({"op": "set", **at(1, 3), "v": f"lost{i}"})
        b.pump()
        a.pump()
    a.disconnect()
    a.submit({"op": "set", **at(2, 2), "v": "A typed offline"})
    b.disconnect()
    reload_from(h.room, draft)                             # the next process loads the draft
    b.connect()
    b.pump()
    b.submit({"op": "set", **at(2, 2), "v": "B's newer value"})
    b.pump()
    for i in range(4):
        b.submit({"op": "set", **at(3, 3), "v": f"more{i}"})
        b.pump()
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    assert _cell(h.room.state["content"], sid, rows[2], cols[2]) == "B's newer value"
    assert [(x["kind"], x["label"]["ref"]) for x in a.state()["attention"]] == [("conflict", "C3")]
