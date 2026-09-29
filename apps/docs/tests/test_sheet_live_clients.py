"""Real browser sync engines against the real live table room (docs-sheet-live.md §6, §9).

Each tab is ``SheetSync`` itself (``apps/docs/web/src/lib/sheetsync.ts``, in Node); the room is
``SheetRoom`` with its operations model; the driver (``sheet_live_clients.py``) delivers every frame in
the interleaving it picks. Two parts hold the findings of the reviews of steps ③–⑤ (H1–H10 failed on
0a6c0cd, P1–P8 and the round's other items on 324adc8); the random part drives three tabs — busy
windows, rate limits, dropped connections with frames on the wire, lost answers, forgotten tabs, saves,
outside versions, the room's own draft writes (with `persisted`) every few steps, and servers that die
without their closing write, again and again, each reopened from storage under a new load id while some
tabs are away — and then checks, besides convergence:

* every tab shows exactly the room's content once everything is delivered, nothing left pending;
* the room's own invariants hold, and no added row or column was applied twice;
* what a tab believes applied was applied and kept (a crash that lost it re-applied it or told the
  person) — nothing lost silently (H4);
* one tab's messages apply in the order it sent them (H1);
* undo never deleted a sheet another tab had changed since (H9) — matched by client and message
  number in the room's log, not by the operation's text.

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

from .sheet_live_clients import Harness, Node, Tab, diff, node_missing, restart_from_storage, write_draft
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
    """The room of a fresh table template, every operation it applies logged; drafts are written when a
    test says so (``write_draft``: the room's own write), not on a timer; no rate limit unless a test
    sets one."""
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
    # undo never deleted a sheet another tab changed since the change it undid: the undo's own
    # application found in the room's log by client and message number (its text can equal a direct
    # delete of the same sheet), then every change by another client on that sheet in between
    for t in h.tabs:
        if not t.client_id:
            continue
        numbers = t.applied_numbers()
        for u in t.undos():
            ops = u["op"]["ops"] if u["op"].get("op") == "tx" else [u["op"]]
            gone = {o["sheet"] for o in ops if o.get("op") == "sheet_delete"}
            got = numbers.get(str(u["id"]))
            if not gone or not got:
                continue
            hit = [a for a in kept if a["changed"] and a["n"] == got["n"] and str(a["client"]).endswith(str(got["client"])) and a["epoch"] == u["epoch"]]
            for a in hit:
                others = [b for b in kept if b["changed"] and b["epoch"] == a["epoch"] and u["undoneSeq"] < b["seq"] < a["seq"]
                          and b["client"] != a["client"] and gone & set(b["sheets"])]
                if others:
                    problems.append(f"{t.name}: undo deleted sheet {sorted(gone)} that another tab changed since: {others[0]['op'][:100]}")
    return problems, st


def _wait_idle(room, s=10.0):
    end = time.time() + s
    while room.has_work() and time.time() < end:
        time.sleep(0.01)


def crash(h: Harness, env, rnd, ts, events, step, *, saved: int | None = None) -> None:
    """The process dies without its closing write — some frames still reached it — and the next one
    opens the room from storage (a new load of the same epoch). What it had applied after its last
    draft write is lost (marked so in the log). ``saved``: it died right after a save wrote a version
    of the room at that seq and before the next draft write — what the version holds is not lost."""
    for u in ts:
        if u.online:
            for _ in range(rnd.randint(0, 3)):
                if u.out:
                    u.to_server()
    for u in ts:
        if u.online:
            u.disconnect(server_notices=True)
    for c in list(h.lingering):
        h.room.clients.discard(c)
    h.lingering.clear()
    epoch = h.room.state["epoch"]
    loaded = restart_from_storage(h.room)
    kept = max(loaded if h.room.state["epoch"] == epoch else 0, saved or 0)
    for a in env["applied"]:
        if a["changed"] and not a.get("lost") and a["epoch"] == epoch and a["seq"] > kept:
            a["lost"] = True
    events.append((step, "crash", loaded) if saved is None else (step, "crash after save", loaded, saved))


def run(env, clock, seed, steps=300, *, crashes=0.0, outside=False, saves=True, churn=True, busy=True, rate=None,  # noqa: F811
        disconnects=True, kinds=None, persist_every=(3, 25)):
    """Three tabs (two people, one of them twice) at random for ``steps`` steps; then ``check``. The
    room writes its draft every few steps (and after every save and outside version); with ``crashes``
    it dies that often per step and is reopened from storage."""
    rnd = random.Random(seed)
    h = Harness(env["world"], env["room"], env["node"], clock)
    env["node"](cmd="seed", seed=seed)
    if rate:
        env["world"]["app"].state.collab.sheet_rate = RateBuckets(rate)      # fixture: a tight limit on the fake clock
    ts = [h.tab("t1", "owner"), h.tab("t2", "editor"), h.tab("t3", "owner")]
    for t in ts:
        t.connect()
        t.pump()
    write_draft(h.room)
    next_draft = rnd.randint(*persist_every)
    ghosts, events = 0, []
    for step in range(steps):
        if step == next_draft:
            write_draft(h.room)
            next_draft = step + rnd.randint(*persist_every)
        t = rnd.choice(ts)
        x = rnd.random()
        if x < 0.27:
            t.act(kinds)
        elif x < 0.53:
            if t.online and t.out:
                t.to_server()
        elif x < 0.80:
            if t.online and t.conn.inbox:
                t.to_client()
        elif x < 0.845:
            if t.online and disconnects:
                t.disconnect(server_notices=rnd.random() < 0.6, deliver_prefix=rnd.randint(0, 4))
            elif not t.online:
                t.connect()
        elif x < 0.875:
            if not t.online:
                t.connect()
            h.advance(rnd.choice([0.01, 0.05, 0.3, 1.5]))
            t.flush()
        elif x < 0.905 and busy:
            # a version being folded in for a moment: frames meanwhile are answered busy
            h.room.hold()
            for _ in range(rnd.randint(0, 5)):
                u = rnd.choice(ts)
                if u.online and u.out:
                    u.to_server()
            if rnd.random() < 0.5:
                h.advance(rnd.choice([0.1, 0.3]))
            h.room.release()
            events.append((step, "busy"))
        elif x < 0.92 and saves:
            r = h.world["co"].post(f"/api/templates/{h.world['tid']}/collab/save", json={})
            _wait_idle(h.room)
            events.append((step, "save", r.status_code))
            if crashes and rnd.random() < 0.25 and r.status_code == 200 and not r.json().get("unchanged"):
                # the process dies after the save wrote its version and before the next draft write:
                # the next one opens the draft and folds that version in (catch-up)
                crash(h, env, rnd, ts, events, step, saved=r.json()["live"]["seq"])
            else:
                write_draft(h.room)
        elif x < 0.945 and churn:
            # the owner's other tabs come and go (the room's memory of the owner's older tabs moves on)
            ghosts += 1
            g = Tab(h, f"g{ghosts}", h.world["owner"])
            g.connect()
            g.pump()
            g.act(["set"])
            g.pump()
            g.disconnect(server_notices=True)
        elif x < 0.945 + crashes:
            crash(h, env, rnd, ts, events, step)
        elif x < 0.955 + crashes and outside:
            # someone saves alone (solo) or discards the live edits (a new epoch)
            ed = CS.project(h.room.state)
            ed["sheets"][0].setdefault("cells", {})["D4"] = {"v": f"outside{step}"}
            head = h.room.head
            if rnd.random() < 0.6:
                r = h.world["co"].put(f"/api/templates/{h.world['tid']}/builtin", json={"editor": ed, "base_version": head, "solo": True})
            else:
                r = machine_put(h.world, ed, head, discard_live=True)
            _wait_idle(h.room)
            write_draft(h.room)
            events.append((step, "outside", r.status_code))
    if crashes:
        # the end: everything delivered and answered, then the process dies once more before its next
        # draft write — whatever every tab showed as done must come back (sent again, or told)
        h.quiesce()
        crash(h, env, rnd, ts, events, steps)
    problems, st = check(h, env["applied"])
    for t in h.tabs:
        if t.conn is not None and t.conn.held_n is not None:
            problems.append(f"{t.name}: the room still holds n={t.conn.held_n} for it after everything was delivered")
    return problems, events


STRUCT = ["set", "clear", "merge", "merge", "unmerge", "field_add", "field_set", "field_del", "sheet_add", "sheet_rename",
          "sheet_delete", "sheet_delete", "sheet_restore", "sheet_restore", "append_rows", "grow_set", "delete_sel",
          "undo", "undo", "undo", "answer", "answer"]


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_with_busy_windows_saves_and_dropped_connections(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 5000 + seed)
    assert not problems, f"seed {5000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_under_a_tight_rate_limit_and_crashes(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 6000 + seed, rate=90, crashes=0.01)
    assert not problems, f"seed {6000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_with_outside_versions_restarts_and_crashes(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 7000 + seed, outside=True, crashes=0.006)
    assert not problems, f"seed {7000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_interleavings_across_servers_that_die_again_and_again(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 9000 + seed, crashes=0.02)
    assert not problems, f"seed {9000 + seed}, events {events}\n" + "\n".join(problems)


@pytest.mark.parametrize("seed", range(SEEDS))
def test_random_structure_undo_answers_and_crashes(env, clock, seed):  # noqa: F811
    problems, events = run(env, clock, 11000 + seed, steps=400, kinds=STRUCT, crashes=0.01)
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


# ------------------------------------------------------------------ the second review of steps ③–⑤ (each failed on 324adc8)
def test_P1_an_edit_resolved_from_outcomes_is_kept_until_written(env):
    """An edit applied while its answer was lost with the socket was resolved from the next hello's
    `outcomes` but not kept as acknowledged: a crash before the next draft write lost it silently."""
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    write_draft(h.room)
    a.pump()
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "applied, answer lost"})
    a.to_server()                                    # applied; the answer is lost with the socket
    a.disconnect(server_notices=True)
    a.connect()
    a.pump()                                         # the same process: `outcomes` say it was applied
    assert a.state()["pending"] == []
    a.disconnect(server_notices=True)
    restart_from_storage(h.room)                     # the process dies before its next draft write
    a.connect()
    a.pump()
    a.pump()
    assert _cell(h.room.state["content"], sid, rows[3], cols[3]) == "applied, answer lost"
    assert not a.state()["attention"]


def test_P2_a_group_refused_after_a_save_names_every_changed_cell_with_value_and_person(env):
    """A (offline) clears B2:B3; B writes both; the table is saved and the room forgets what A saw: the
    group is refused `resync/expect_too_old`. The sentence now names both cells, their values and who
    changed them, and "do it again" is sent against what A was shown."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    B2 = {"sheet": sid, "row": rows[1], "col": cols[1]}
    B3 = {"sheet": sid, "row": rows[2], "col": cols[1]}
    a.submit({"op": "set", **B2, "v": "a2"})
    a.submit({"op": "set", **B3, "v": "a3"})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    a.submit({"op": "tx", "ops": [{"op": "clear", **B2}, {"op": "clear", **B3}]})
    b.submit({"op": "set", **B2, "v": "B's B2"})
    b.pump()
    b.submit({"op": "set", **B3, "v": "B's B3"})
    b.pump()
    b.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "B elsewhere"})
    b.pump()
    for c in list(h.room.clients):                   # the fixture's socket has seen everything too
        if not hasattr(c, "inbox"):
            c.seen = h.room.state["seq"]
    assert h.world["co"].post(f"/api/templates/{h.world['tid']}/collab/save", json={}).status_code == 200
    _wait_idle(h.room)
    b.pump()
    assert h.room.state["kfloor"] > 2
    a.connect()
    a.pump()
    a.pump()
    [att] = a.state()["attention"]
    assert att["code"] == "resync" and att["detail"]["reason"] == "expect_too_old" and att["retry"]
    assert [(c["ref"], c["theirs"]["v"], c["by"]) for c in att["detail"]["also"]] == \
        [("B2", "B's B2", h.world["editor"]["id"]), ("B3", "B's B3", h.world["editor"]["id"])]
    texts = a.texts()
    for lang in ("zh-Hans", "en", "zh-Hant"):
        assert "B's B2" in texts[lang][0] and "B's B3" in texts[lang][0] and "Editor" in texts[lang][0], texts[lang][0]
    b.submit({"op": "set", **B3, "v": "B after A was told"})
    b.pump()
    a.pump()
    a.use_mine(att["id"])                            # do it again: against what A was shown
    a.pump()
    b.pump()
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    assert _cell(h.room.state["content"], sid, rows[2], cols[1]) == "B after A was told", "B3 changed after A was told: not replaced"
    assert _cell(h.room.state["content"], sid, rows[1], cols[1]) == "B's B2", "the group is one change: refused as a whole"
    [again] = a.state()["attention"]
    assert again["code"] == "conflict" and again["label"]["ref"] == "B3"
    # told again (B2 had not changed since A last chose), A chooses once more: now both go
    assert not again["detail"].get("also")
    a.use_mine(again["id"])
    a.pump()
    b.pump()
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    assert f"{rows[1]}:{cols[1]}" not in cells and f"{rows[2]}:{cols[1]}" not in cells


def test_P3_undo_of_my_restore_leaves_a_sheet_others_typed_on_while_i_was_away(env):
    h, a, b = _two(env)
    sid2 = h.room.state["content"]["order"][1]
    a.submit({"op": "sheet_delete", "sheet": sid2})
    a.pump()
    b.pump()
    a.submit({"op": "sheet_restore", "sheet": sid2})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    sh = h.room.state["content"]["sheets"][sid2]
    b.submit({"op": "set", "sheet": sid2, "row": sh["rows"][2], "col": sh["cols"][2], "v": "B's work"})
    b.pump()
    a.connect()
    a.pump()
    r = a.undo()
    a.pump()
    b.pump()
    assert [x["reason"] for x in r["skipped"]] == ["sheet_changed"]
    assert h.room.state["content"]["sheets"][sid2]["gone"] is False


def test_P4_use_my_value_on_a_group_leaves_a_cell_my_later_edit_decides(env):
    """A (offline) clears B2:B3 and then types into B3; B had written both. Two conflicts: the group's
    (about B2) and the B3 edit's. "Use my value" on the group cleared B's B3 even when A then chose
    "keep theirs" on B3. B3 is now the later edit's to decide: the group's sentence does not list it and
    its retry leaves it alone."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    B2 = {"sheet": sid, "row": rows[1], "col": cols[1]}
    B3 = {"sheet": sid, "row": rows[2], "col": cols[1]}
    a.submit({"op": "set", **B2, "v": "a2"})
    a.submit({"op": "set", **B3, "v": "a3"})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    b.submit({"op": "set", **B2, "v": "B's B2"})
    b.submit({"op": "set", **B3, "v": "B's B3"})
    b.pump()
    a.submit({"op": "tx", "ops": [{"op": "clear", **B2}, {"op": "clear", **B3}]})
    a.submit({"op": "set", **B3, "v": "A's B3"})
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    att = a.state()["attention"]
    group = [x for x in att if x["label"]["ref"] == "B2"][0]
    b3 = [x for x in att if x["label"]["ref"] == "B3"][0]
    assert not group["detail"].get("also")
    a.use_mine(group["id"])
    a.pump()
    b.pump()
    env["node"](cmd="dismiss", tab="a", id=b3["id"])                # keep theirs on B3
    cells = h.room.state["content"]["sheets"][sid]["cells"]
    assert f"{B2['row']}:{B2['col']}" not in cells, "B2: what A chose"
    assert cells.get(f"{B3['row']}:{B3['col']}") == {"v": "B's B3"}, "B3: kept, as A chose on its own item"


def test_P5_a_tab_away_across_two_crashes_resends_what_the_first_one_lost(env):
    """A's edit acknowledged in load L1; L1 dies (draft before it); L2 loads, B refills the numbers and
    L2 writes past A's seq, then dies too. A comes back to L3: the room's load history shows L2 started
    below A's edit, so A sends it again."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    write_draft(h.room)
    a.pump()
    b.pump()
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "A's acknowledged edit"})
    a.pump()
    b.pump()
    assert a.state()["pending"] == []
    a.disconnect(server_notices=True)
    b.disconnect(server_notices=True)
    restart_from_storage(h.room)                                  # L1 dies; L2
    b.connect()
    b.pump()
    b.pump()
    for i in range(3):
        b.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[3], "v": f"B {i}"})
        b.pump()
    write_draft(h.room)                                           # L2's draft, past A's old seq
    b.pump()
    b.disconnect(server_notices=True)
    restart_from_storage(h.room)                                  # L2 dies; L3
    a.connect()
    a.pump()
    a.pump()
    assert _cell(h.room.state["content"], sid, rows[3], cols[3]) == "A's acknowledged edit"
    assert _cell(h.room.state["content"], sid, rows[1], cols[3]) == "B 2"


def test_P6_the_tab_counts_what_the_room_has_not_written_yet(env):
    """The page asks before closing while this count is not zero (sheetmode.leaveAtRisk)."""
    h = Harness(env["world"], env["room"], env["node"])
    a = h.tab("a", "owner")
    a.connect()
    a.pump()
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "answered"})
    a.pump()
    s1 = env["node"](cmd="snapshot", tab="a")["result"]
    write_draft(h.room)
    a.pump()
    s2 = env["node"](cmd="snapshot", tab="a")["result"]
    assert (s1["pending"], s1["unpersisted"], s2["unpersisted"]) == (0, 1, 0)


def test_P8_a_refused_undo_does_not_block_every_older_undo(env):
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "older change"})
    a.pump()
    b.pump()
    sid2 = h.room.state["content"]["order"][1]
    a.submit({"op": "sheet_delete", "sheet": sid2})
    a.pump()
    b.pump()
    b.submit({"op": "sheet_add", "id": "bnew0001", "name": "明细", "rows": 3, "cols": 3, "row_ids": ["br000001", "br000002", "br000003"],
              "col_ids": ["bc000001", "bc000002", "bc000003"], "h": 24, "w": 100})
    b.pump()
    a.pump()
    a.undo()                                                      # restore 明细: its name is taken — refused
    a.pump()
    assert [x["code"] for x in a.state()["attention"]] == ["sheet_name_taken"]
    assert "undo again" in a.texts()["en"][0]
    a.undo()                                                      # the change before it
    a.pump()
    assert _cell(h.room.state["content"], sid, rows[3], cols[3]) is None


def test_item6_a_new_epoch_reports_every_acknowledged_edit_it_dropped(env, clock):  # noqa: F811
    """The "not kept" notice after a restart from a version was computed against the new epoch's
    `persisted` — an edit acknowledged at a lower seq went unreported."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[3], "col": cols[3], "v": "acknowledged, not written"})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    ed = CS.project(h.room.state)
    r = machine_put(h.world, ed, h.room.head, discard_live=True)            # a new epoch, the live edits discarded
    assert r.status_code == 200, r.text
    _wait_idle(h.room)
    b.pump()
    sid, rows, cols = _grid(h.room)                               # the new epoch's ids
    for i in range(4):
        b.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[3], "v": f"B {i}"})
        b.pump()
    assert h.room.state["seq"] == 4
    write_draft(h.room)                                           # the new epoch's draft, past A's old seq
    b.pump()
    a.connect()
    a.pump()
    lost = [x for x in a.state()["attention"] if x["code"] == "lost"]
    assert len(lost) == 1 and lost[0]["label"]["ref"] == "D4", a.state()["attention"]


def test_item10_undo_does_not_delete_what_my_other_tab_typed_on_a_new_sheet(env):
    """One person, two tabs: the first adds a sheet, the second types on it; the first's undo, pressed
    before that edit reached it, deleted the sheet (the room did not check within one person). The
    room now checks by tab: refused, and said as "you, in another tab"."""
    h = Harness(env["world"], env["room"], env["node"])
    a1, a2 = h.tab("a1", "owner"), h.tab("a2", "owner")
    for t in (a1, a2):
        t.connect()
        t.pump()
    a1.submit({"op": "sheet_add", "id": "nsheet01", "name": "新表", "rows": 3, "cols": 3, "row_ids": ["nr000001", "nr000002", "nr000003"],
               "col_ids": ["nc000001", "nc000002", "nc000003"], "h": 24, "w": 100})
    a1.pump()
    a2.pump()
    a2.submit({"op": "set", "sheet": "nsheet01", "row": "nr000001", "col": "nc000001", "v": "typed in my other tab"})
    a2.to_server()                                               # applied; its patch has not reached a1 yet
    a1.undo()
    a1.pump()
    a2.pump()
    assert h.room.state["content"]["sheets"]["nsheet01"]["gone"] is False
    [att] = a1.state()["attention"]
    assert att["code"] == "conflict" and "another tab" in a1.texts()["en"][0]


# ------------------------------------------------------------------ the third review (each failed on bddbfa6)
def _group_then_later(env, b_writes):
    """A wrote B2 and B3; offline, B writes ``b_writes`` of them and A clears B2:B3 as one group."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    B2 = {"sheet": sid, "row": rows[1], "col": cols[1]}
    B3 = {"sheet": sid, "row": rows[2], "col": cols[1]}
    a.submit({"op": "set", **B2, "v": "a2"})
    a.submit({"op": "set", **B3, "v": "a3"})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    for cell, v in b_writes:
        b.submit({"op": "set", **(B2 if cell == "B2" else B3), "v": v})
        b.pump()
    a.submit({"op": "tx", "ops": [{"op": "clear", **B2}, {"op": "clear", **B3}]})
    return h, a, b, sid, rows, cols, B3


def test_R3a_a_later_edit_taken_back_before_it_was_sent_leaves_its_cell_to_the_group(env):
    """Offline: Delete over B2:B3, type B3, undo (the typing never leaves the tab); B had changed both.
    The group's conflict named only B2 and "use my value" left B3 — the delete of B3 silently not done.
    Now B3 is the group's: told with B's value, and cleared on "use my value"."""
    h, a, b, sid, rows, cols, B3 = _group_then_later(env, [("B2", "B's B2"), ("B3", "B's B3")])
    a.submit({"op": "set", **B3, "v": "typo"})
    assert a.undo()["cancelled"] is True
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    [g] = [x for x in a.state()["attention"] if x["kind"] == "conflict"]
    assert [(c["ref"], c["theirs"]["v"]) for c in g["detail"].get("also", [])] == [("B3", "B's B3")], g["detail"]
    assert "B's B3" in a.texts()["en"][0]
    a.use_mine(g["id"])
    a.pump()
    b.pump()
    assert _cell(h.room.state["content"], sid, rows[2], cols[1]) is None, "A's delete of B3, as the sentence said"
    assert _cell(h.room.state["content"], sid, rows[1], cols[1]) is None


def test_R3b_a_later_edit_refused_for_another_reason_leaves_its_cell_to_the_group(env):
    h, a, b, sid, rows, cols, B3 = _group_then_later(env, [("B2", "B's B2")])
    a.submit({"op": "set", **B3, "v": "bad\x01char"})          # a character Excel cannot hold: refused
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    att = a.state()["attention"]
    assert [(x["kind"], x["code"]) for x in att] == [("conflict", "conflict"), ("refused", "invalid")]
    a.use_mine(att[0]["id"])
    a.pump()
    b.pump()
    assert _cell(h.room.state["content"], sid, rows[2], cols[1]) is None, "A's delete of B3 (the group) is done"


def test_R3b_refused_after_the_group_was_told_the_cell_it_left_out_is_told_before_anything_is_replaced(env):
    h, a, b, sid, rows, cols, B3 = _group_then_later(env, [("B2", "B's B2"), ("B3", "B's B3")])
    a.submit({"op": "set", **B3, "v": "bad\x01char"})
    a.connect()
    a.to_client()                                               # the hello
    a.to_server()                                               # the group
    a.to_client()                                               # its answer: a conflict — the later edit may still apply
    [g] = a.state()["attention"]
    assert not g["detail"].get("also"), "B3 is left to the later edit while it may still apply (P4)"
    a.pump()                                                    # the later edit is refused
    [g] = [x for x in a.state()["attention"] if x["kind"] == "conflict"]
    assert [(c["ref"], c["theirs"]["v"], c["by"]) for c in g["detail"]["also"]] == [("B3", "B's B3", h.world["editor"]["id"])]
    a.use_mine(g["id"])
    a.pump()
    b.pump()
    assert _cell(h.room.state["content"], sid, rows[2], cols[1]) is None


@pytest.mark.parametrize("deleter_sorts_higher", [True, False])
def test_R3c2_a_stale_delete_names_the_person_who_wrote_whichever_way_the_ids_sort(env, deleter_sorts_higher):
    """The per-tab check added every other tab's write on the sheet as the sender's own: when the
    deleter's id sorted higher, the sentence said "you changed it first in another tab" about someone
    else's work."""
    h, a, b = _two(env)
    owner_higher = h.world["owner"]["id"] > h.world["editor"]["id"]
    deleter, writer = (a, b) if owner_higher == deleter_sorts_higher else (b, a)
    writer_id = h.world["editor" if writer is b else "owner"]["id"]
    deleter.submit({"op": "sheet_add", "id": "delhi001", "name": "表X", "rows": 2, "cols": 2, "row_ids": ["rhi00001", "rhi00002"],
                    "col_ids": ["chi00001", "chi00002"], "h": 24, "w": 100})
    deleter.pump()
    writer.pump()
    deleter.disconnect(server_notices=True)
    deleter.submit({"op": "sheet_delete", "sheet": "delhi001"})
    writer.submit({"op": "set", "sheet": "delhi001", "row": "rhi00001", "col": "chi00001", "v": "their work"})
    writer.pump()
    deleter.connect()
    deleter.pump()
    deleter.pump()
    writer.pump()
    [att] = [x for x in deleter.state()["attention"] if x["kind"] == "conflict"]
    assert att["detail"]["by"] == writer_id and not att["detail"].get("self"), att["detail"]
    assert "another tab" not in deleter.texts()["en"][0]
    assert h.room.state["content"]["sheets"]["delhi001"]["gone"] is False


@pytest.mark.parametrize("part", ["rows", "cols"])
def test_R3h_a_stale_delete_does_not_hide_rows_or_columns_someone_just_added(env, part):
    h, a, b = _two(env)
    sid2 = h.room.state["content"]["order"][1]
    a.disconnect(server_notices=True)
    a.submit({"op": "sheet_delete", "sheet": sid2})             # typed before B's change arrived
    size = {"h": 24} if part == "rows" else {"w": 100}
    b.submit({"op": f"append_{part}", "sheet": sid2, "count": 5, "ids": [f"bbnew{part[0]}0{i}" for i in range(5)], **size})
    b.pump()
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    assert h.room.state["content"]["sheets"][sid2]["gone"] is False, "B's new rows did not go with a delete typed before they existed"
    [att] = a.state()["attention"]
    assert att["kind"] == "conflict" and att["detail"]["by"] == h.world["editor"]["id"]


def test_R3h_a_lost_delete_sent_again_after_a_crash_is_checked_against_rows_added_since(env):
    """A's acknowledged delete is lost with a crash; B, back first, adds rows to the sheet; A sends the
    delete again (against the history as it is now) — a conflict, not a silent removal."""
    h, a, b = _two(env)
    sid2 = h.room.state["content"]["order"][1]
    write_draft(h.room)
    a.pump()
    b.pump()
    a.submit({"op": "sheet_delete", "sheet": sid2})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    b.disconnect(server_notices=True)
    restart_from_storage(h.room)                                  # the delete was not in the draft
    b.connect()
    b.pump()
    b.submit({"op": "append_rows", "sheet": sid2, "count": 3, "ids": ["bbrow101", "bbrow102", "bbrow103"], "h": 24})
    b.pump()
    a.connect()
    a.pump()
    a.pump()
    b.pump()
    assert h.room.state["content"]["sheets"][sid2]["gone"] is False
    assert [(x["kind"], x["detail"].get("by")) for x in a.state()["attention"]] == [("conflict", h.world["editor"]["id"])]


def test_E_a_crash_between_a_save_and_the_next_draft_write_adds_nothing_twice(env):
    """The crash model's new case: the process dies after a save wrote its version and before the next
    draft write. The next one opened the older draft and folded the version in; the tabs sent again
    everything they had acknowledged after that draft — rows added twice, values written twice. A save
    now writes the draft of its snapshot first: nothing below the version is sent again."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    write_draft(h.room)
    a.pump()
    b.pump()
    nr = h.room.state["content"]["sheets"][sid]["nr"]
    a.submit({"op": "tx", "ops": [{"op": "append_rows", "sheet": sid, "count": 1, "ids": ["edgerow1"], "h": 24},
                                  {"op": "set", "sheet": sid, "row": "edgerow1", "col": cols[0], "v": "typed at the edge"}]})
    a.pump()
    b.pump()
    b.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[3], "v": "B's saved value"})
    b.pump()
    a.pump()
    r = h.world["co"].post(f"/api/templates/{h.world['tid']}/collab/save", json={})
    assert r.status_code == 200 and not r.json().get("unchanged"), r.text
    _wait_idle(h.room)
    a.pump()
    b.pump()
    saved = CS.project(h.room.state)
    a.disconnect(server_notices=True)
    b.disconnect(server_notices=True)
    restart_from_storage(h.room)                                  # no draft write after the save
    for t in (a, b):
        t.connect()
        t.pump()
        t.pump()
    a.pump()
    b.pump()
    sh = h.room.state["content"]["sheets"][sid]
    assert sh["nr"] == nr + 1, f"the row was added once, not {sh['nr'] - nr} times"
    values = [c.get("v") for c in sh["cells"].values()]
    assert values.count("typed at the edge") == 1 and values.count("B's saved value") == 1
    assert CS.project(h.room.state) == saved
    assert not a.state()["attention"] and not b.state()["attention"], "nothing to tell: nothing was lost"
    assert a.state()["content"] == b.state()["content"] == h.room.state["content"]


def test_E_a_lost_notice_is_recorded_for_its_operation_even_when_the_person_dismisses_it(env):
    """The sidecar recorded what became of each queued operation, but not the "not kept" notice a tab
    gives when the room restarted in a new epoch after a crash lost its acknowledged edit: a later
    random "answer" dismissed the notice and the check saw a silent loss that had been told."""
    h, a, b = _two(env)
    sid, rows, cols = _grid(h.room)
    a.submit({"op": "set", "sheet": sid, "row": rows[1], "col": cols[3], "v": "unsaved, written"})
    a.pump()
    b.pump()
    write_draft(h.room)
    a.pump()
    b.pump()
    a.submit({"op": "set", "sheet": sid, "row": rows[2], "col": cols[3], "v": "acknowledged, then lost"})
    a.pump()
    b.pump()
    a.disconnect(server_notices=True)
    b.disconnect(server_notices=True)
    crash(h, env, random.Random(0), [], [], 0)                   # A's second edit is lost with the process
    b.connect()
    b.pump()
    r = machine_put(h.world, CS.project(h.room.state), h.room.head, discard_live=True)    # a new epoch before A is back
    assert r.status_code == 200, r.text
    _wait_idle(h.room)
    b.pump()
    a.connect()
    a.pump()
    [lost] = [x for x in a.state()["attention"] if x["code"] == "lost"]
    assert lost["label"]["ref"] == "D3"
    env["node"](cmd="dismiss", tab="a", id=lost["id"])          # the person read it and dismissed it
    assert "dropped:lost" in {f["fate"] for f in a.fates()}
    problems, _st = check(h, env["applied"])
    assert not problems, problems

