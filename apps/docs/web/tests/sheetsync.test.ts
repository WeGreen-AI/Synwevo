/** A tab's side of a live table room (sheetsync.ts): optimistic edits and their rebase over others'
 *  patches, the numbering and `outcomes` rules of docs-sheet-live.md §9 (offline edits sent again
 *  exactly once; what a forgotten tab cannot know goes to the person), busy / rate limits, restarts,
 *  conflicts with "use my value / keep theirs", per-person undo that skips what others changed, the
 *  status line, and the sentences of every refusal in three languages.
 *
 *  TEST FIXTURE: `FixtureRoom` below answers like collab_sheet_room.py for what these tests need —
 *  it applies operations with the browser model itself (held to the server's by sheetmodel.test.ts),
 *  keeps per-client records and outcomes, and does first-writer-wins on cell values. The real room is
 *  tested in apps/docs/tests/test_sheet_live.py; the browser proof is ui_e2e_sheet_live.py. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { applyOp, copy, projectSheet, type Content } from "../src/lib/sheetmodel.ts";
import { SheetSync, labelOf, repeatable, type Attention } from "../src/lib/sheetsync.ts";
import { attentionText, closeText, refusalText, retryText, saveText, statusText, liveReasonText, skippedText, announcementText } from "../src/lib/sheettext.ts";
import "../src/lib/presentation.ts";
import { setLocale } from "@synwevo/ui/i18n-core";

const corpus = JSON.parse(readFileSync(new URL("./fixtures/sheet_live_corpus.json", import.meta.url), "utf8"));
const SEED: Content = corpus.sequences.find((s: any) => s.name === "groups_and_invalid").content;    // Sheet1, 30 × 10
const SID = SEED.order[0];
const R = (r: number) => SEED.sheets[SID].rows[r];
const C = (c: number) => SEED.sheets[SID].cols[c];
const set = (r: number, c: number, v: any) => ({ op: "set", sheet: SID, row: R(r), col: C(c), v });
const cellOf = (content: Content | null, ref: string) => content ? (projectSheet(content, SID).cells || {})[ref]?.v : undefined;
const PEOPLE: Record<string, { id: string; name: string; color: number }> = {
  u1: { id: "u1", name: "张三", color: 1 }, u2: { id: "u2", name: "李四", color: 2 }, u3: { id: "u3", name: "王五", color: 3 },
};

// ------------------------------------------------------------------ TEST FIXTURE: a room
class FixtureRoom {
  content: Content = copy(SEED);
  seq = 0;
  epoch = "ep-1";
  persisted = 0;
  who = new Map<string, string>();
  cellSeq = new Map<string, [number, string]>();
  clients = new Map<string, { hi: number; r: Map<number, any> }>();
  tabs = new Set<Tab>();
  busy = 0;                                        // answer the next N op frames `busy`
  forget = false;                                  // forget clients on reconnect (resumed: false)

  hello(tab: Tab) {
    const key = `${tab.user}/${tab.client}`;
    if (this.forget) this.clients.delete(key);
    const rec = this.clients.get(key);
    const outcomes: Record<string, any> = {};
    if (rec) for (const [n, res] of rec.r) outcomes[String(n)] = res.ok ? true : res.code;
    tab.inbox.push(["meta", { type: "hello", live: true, user: PEOPLE[tab.user], readonly: false, peers: [], epoch: this.epoch, seq: this.seq,
      client: tab.client, next: rec ? rec.hi + 1 : 1, resumed: !!rec, outcomes, persisted: this.persisted, content: copy(this.content),
      limits: { max_tx: 5000, max_frame_bytes: 2097152, ops_per_min: 600, max_cells: 50000, max_bytes: 16777216 } }]);
  }

  receive(tab: Tab, f: any) {
    if (f.type === "sync") {
      const rec = this.clients.get(`${tab.user}/${tab.client}`);
      tab.inbox.push(["sheet", { type: "full", live: true, epoch: this.epoch, seq: this.seq, next: rec ? rec.hi + 1 : 1, outcomes: {}, content: copy(this.content) }]);
      return;
    }
    const ack = (x: any) => tab.inbox.push(["sheet", { type: "ack", n: f.n, ...x }]);
    if (this.busy > 0) { this.busy--; ack({ ok: false, code: "busy", detail: { retry_after: 0.25 } }); return; }
    if (f.epoch !== this.epoch) { ack({ ok: false, code: "stale_epoch", detail: { epoch: this.epoch, seq: this.seq } }); return; }
    const key = `${tab.user}/${tab.client}`;
    const rec = this.clients.get(key) ?? { hi: 0, r: new Map() };
    this.clients.set(key, rec);
    const prior = rec.r.get(f.n);
    if (prior) { ack({ ...prior, duplicate: true }); return; }
    let res: any = this.decide(f, tab.user);
    if (!res) {
      const got = applyOp(this.content, f.op, { by: tab.user, who: this.who, maxCells: 50000 });
      if (got.ok && got.patch.length) {
        this.seq++;
        for (const e of got.patch) if (e.p[2] === "cells") this.cellSeq.set(`${e.p[1]}/${e.p[3]}`, [this.seq, tab.user]);
      }
      res = got.ok ? { ok: true, seq: this.seq, patch: got.patch, before: got.before } : { ok: false, code: got.code, detail: got.detail };
    }
    rec.r.set(f.n, res);
    rec.hi = Math.max(rec.hi, f.n);
    const people = res.ok ? undefined : Object.fromEntries([res.detail?.by].flat().filter(Boolean).map((id: string) => [id, PEOPLE[id]]));
    ack(people ? { ...res, people } : res);
    if (res.ok && res.patch.length) for (const other of this.tabs) if (other !== tab && other.online) other.inbox.push(["sheet", { type: "patch", seq: res.seq, patch: res.patch, by: PEOPLE[tab.user] }]);
  }

  /** The refusal of an operation, or null when it applies. Like collab_sheet (`_dispatch`, `_op_set`): a
   *  group is decided op by op, in order, the first refusal answering for all of it; for a cell value,
   *  "already so" first (agreeing is not a conflict), then whether the workbook can hold the value, then
   *  first-writer-wins, then a merged range covering it or the table's cell cap. */
  decide(f: any, by: string): any {
    const ops: any[] = f.op?.op === "tx" && Array.isArray(f.op.ops) ? f.op.ops : [f.op];
    const work = copy(this.content);
    const who = new Map(this.who);
    for (const op of ops) {
      let probe: any;
      try {
        probe = applyOp(copy(work), op, { by, who: new Map(who), maxCells: 50000 });
      } catch { return null; }                      // not predictable here: applying it decides
      const late = !probe.ok && (probe.code === "covered" || (probe.code === "too_large" && probe.detail?.what === "cells"));
      if (!probe.ok && !late) return { ok: false, code: probe.code, detail: probe.detail };
      if (late || probe.patch.length) {
        const conflict = this.conflict(op, f.expect, by, work);
        if (conflict) return { ok: false, code: "conflict", detail: conflict };
      }
      if (late) return { ok: false, code: probe.code, detail: probe.detail };
      applyOp(work, op, { by, who, maxCells: 50000 });
    }
    return null;
  }

  /** first writer wins on a cell value (the server does it for every object) */
  conflict(op: any, expect: number, by: string, content: Content): any {
    if (op?.op !== "set" && op?.op !== "clear") return null;
    const k = this.cellSeq.get(`${op.sheet}/${op.row}:${op.col}`);
    if (k && k[0] > expect && k[1] !== by) {
      const cell = content.sheets[op.sheet].cells[`${op.row}:${op.col}`];
      return { key: `v/${op.sheet}/${op.row}:${op.col}`, theirs: cell ? { v: cell.v } : null, by: k[1], at: k[0] };
    }
    return null;
  }

  /** the room restarts from a version (discard_live) */
  restart(epoch: string) {
    this.epoch = epoch;
    this.content = copy(SEED);
    this.seq = 0;
    this.clients.clear();
    this.cellSeq.clear();
    for (const t of this.tabs) if (t.online) t.inbox.push(["sheet", { type: "reset", epoch, previous_epoch: "x", seq: 0, next: 1, head: 3, dirty: false, reason: "restore", by: PEOPLE.u3, persisted: 0, content: copy(SEED) }]);
  }
}

class Tab {
  sync: SheetSync;
  inbox: [string, any][] = [];
  outbox: any[] = [];
  online = false;
  client: string;
  room: FixtureRoom;
  user: string;
  clock = { t: 1000 };
  constructor(room: FixtureRoom, user: string, client: string) {
    this.room = room;
    this.user = user;
    this.client = client;
    let k = 0;
    this.sync = new SheetSync({ send: (f) => this.outbox.push(f), now: () => this.clock.t, newId: () => `nid${user}${String(++k).padStart(8 - 3 - user.length, "0")}` });
    room.tabs.add(this);
  }
  connect() { this.online = true; this.sync.onOpen(); this.room.hello(this); }
  disconnect() { this.online = false; this.outbox = []; this.inbox = []; this.sync.onDisconnect(); }
}

/** deliver everything in flight, in order, until quiet */
function pump(room: FixtureRoom, limit = 1000) {
  for (let i = 0; i < limit; i++) {
    let moved = false;
    for (const tab of room.tabs) {
      if (!tab.online) continue;
      while (tab.outbox.length) { room.receive(tab, tab.outbox.shift()); moved = true; }
      while (tab.inbox.length) {
        const [kind, f] = tab.inbox.shift()!;
        if (kind === "meta") tab.sync.onMeta(f); else tab.sync.onSheet(f);
        moved = true;
      }
    }
    if (!moved) return;
  }
  throw new Error("fixture room never went quiet");
}

const twoTabs = () => {
  const room = new FixtureRoom();
  const a = new Tab(room, "u1", "tabAAAAA"), b = new Tab(room, "u2", "tabBBBBB");
  a.connect(); b.connect(); pump(room);
  return { room, a, b };
};

test("TEST FIXTURE: the fixture room answers a group op by op, like collab_sheet — a conflict on its first cell before an invalid value in a later one", () => {
  // before: it probed the whole group for an invalid value first and answered `invalid` where the room
  // answers `conflict` (third review's follow-up); a group test could pass here and not against the room
  const { room, a, b } = twoTabs();
  b.sync.submit(set(4, 1, "B's B5")); pump(room);                 // seq 1, by u2
  const frame = (n: number, ops: any[]) => room.receive(a, { type: "op", epoch: room.epoch, n, expect: 0, op: { op: "tx", ops } });
  frame(1, [set(4, 1, "mine"), set(5, 1, "bad\u0001char")]);
  frame(2, [set(5, 1, "bad\u0001char"), set(4, 1, "mine")]);
  frame(3, [set(4, 1, "B's B5"), set(6, 1, "fine")]);              // agreeing is not a conflict
  const answers = a.inbox.splice(0).map(([, x]) => x);            // (a's engine never sent these: not handed to it)
  assert.deepEqual(answers.map((x) => (x.ok ? "ok" : x.code)), ["conflict", "invalid", "ok"]);
  assert.equal(answers[0].detail.key, `v/${SID}/${R(4)}:${C(1)}`);
  assert.equal(cellOf(room.content, "B7"), "fine");
});

// ------------------------------------------------------------------ the status line and optimistic edits
test("a tab shows its own edit at once, says 'syncing (1)', and 'live' once the server has answered", () => {
  const room = new FixtureRoom();
  const a = new Tab(room, "u1", "tabAAAAA");
  assert.equal(a.sync.status(), "connecting");
  a.connect(); pump(room);
  assert.equal(a.sync.status(), "live");
  assert.ok(a.sync.submit(set(0, 0, "张三写的")));
  assert.equal(cellOf(a.sync.content, "A1"), "张三写的", "shown before any answer");
  assert.equal(a.sync.status(), "syncing");
  assert.equal(a.sync.snapshot().pending, 1);
  pump(room);
  assert.equal(a.sync.status(), "live");
  assert.deepEqual(a.sync.content, room.content);
  a.disconnect();
  assert.equal(a.sync.status(), "offline");
  a.sync.onClose(4410, "template_deleted");
  assert.equal(a.sync.status(), "single");
});

test("two tabs typing at once end with the server's content, each edit applied once", () => {
  const { room, a, b } = twoTabs();
  for (let i = 0; i < 5; i++) { a.sync.submit(set(i, 0, `a${i}`)); b.sync.submit(set(i, 1, `b${i}`)); }
  // B's patches reach A while A's own edits are still queued: A's stay on top (rebase)
  pump(room);
  assert.deepEqual(a.sync.content, room.content);
  assert.deepEqual(b.sync.content, room.content);
  assert.equal(room.seq, 10);
  assert.equal(cellOf(a.sync.content, "B5"), "b4");
});

test("someone else's patch arriving before my answer keeps my queued edit on top", () => {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(2, 2, "我的"));
  b.sync.submit(set(3, 3, "他的"));
  room.receive(b, b.outbox.shift());                  // B first on the server
  const [, patch] = a.inbox.shift()!;
  a.sync.onSheet(patch);                              // A hears B's patch; its own edit is still unanswered
  assert.equal(cellOf(a.sync.content, "C3"), "我的");
  assert.equal(cellOf(a.sync.content, "D4"), "他的");
  pump(room);
  assert.deepEqual(a.sync.content, room.content);
});

// ------------------------------------------------------------------ conflicts
test("the same cell: the second writer is told whose value it is, and chooses", () => {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(1, 1, "张三的值"));
  b.sync.submit(set(1, 1, "李四的值"));               // made before B heard of A's
  pump(room);
  assert.equal(cellOf(b.sync.content, "B2"), "张三的值", "B's prediction is withdrawn: the server kept A's");
  const [att] = b.sync.snapshot().attention;
  assert.equal(att.kind, "conflict");
  assert.equal(att.people.u1.name, "张三");
  setLocale("zh-Hans");
  assert.equal(attentionText(att, 1), "张三 已先修改了 B2（现在是「张三的值」）。");
  assert.ok(b.sync.useMine(att.id));                   // "use my value": against what B sees now
  pump(room);
  assert.equal(cellOf(room.content, "B2"), "李四的值");
  assert.deepEqual(a.sync.content, room.content);
  assert.equal(b.sync.snapshot().attention.length, 0);
  // "keep theirs": nothing is sent
  a.sync.submit(set(4, 4, "a"));
  b.sync.submit(set(4, 4, "b"));
  pump(room);
  const k = b.sync.snapshot().attention[0];
  b.sync.dismiss(k.id);
  pump(room);
  assert.equal(cellOf(room.content, "E5"), "a");
  assert.equal(b.sync.snapshot().attention.length, 0);
});

test("a refusal the tab predicted is not shown, and the server's reason is the one the person reads", () => {
  const { room, a } = twoTabs();
  const merge = { op: "merge", sheet: SID, range: [R(0), C(0), R(0), C(2)], id: "mrg00001" };
  a.sync.submit(set(0, 1, "要被盖住的值"));
  a.sync.submit(merge);
  assert.equal(a.sync.debugPending()[1].predicted, "covers_values");
  pump(room);
  const [att] = a.sync.snapshot().attention;
  assert.equal(att.code, "covers_values");
  setLocale("en");
  assert.equal(attentionText(att, 1), "Cannot merge A1:C1: it would hide 1 cell with a value (B1). Clear them first.");
  setLocale("zh-Hans");
});

// ------------------------------------------------------------------ offline and reconnecting (§9)
test("edits made offline are sent on reconnect and applied exactly once", () => {
  const { room, a } = twoTabs();
  a.disconnect();
  for (let i = 0; i < 3; i++) a.sync.submit(set(10 + i, 0, `离线${i}`));
  assert.equal(a.sync.status(), "offline");
  setLocale("zh-Hans");
  assert.equal(statusText(a.sync.status(), a.sync.pendingCount), "离线：3 处修改待同步");
  a.connect(); pump(room);
  assert.equal(a.sync.status(), "live");
  assert.equal(room.seq, 3);
  assert.equal(cellOf(room.content, "A12"), "离线1");
  assert.deepEqual(a.sync.content, room.content);
});

test("a reconnected socket carries nothing before its hello, which decides the numbering", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(4, 0, "发出未答"));
  const inFlight = a.outbox.shift();
  room.receive(a, inFlight);                          // applied; the answer is lost with the connection
  a.disconnect();
  a.sync.submit(set(4, 1, "离线时写的"));
  a.online = true;
  a.sync.onOpen();                                    // the socket is up, the hello not here yet
  assert.equal(a.sync.status(), "offline");
  a.sync.flush();
  assert.deepEqual(a.outbox, [], "nothing on a socket that has not been greeted");
  room.hello(a);
  pump(room);
  assert.equal(room.seq, 2, "each applied once");
  assert.equal(cellOf(room.content, "A5"), "发出未答");
  assert.equal(cellOf(room.content, "B5"), "离线时写的");
  assert.deepEqual(a.sync.content, room.content);
});

test("a message applied just before the connection dropped is not sent again (outcomes say so)", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(5, 5, "一次"));
  room.receive(a, a.outbox.shift());                  // applied; the answer is lost with the connection
  a.disconnect();
  a.connect(); pump(room);
  assert.equal(room.seq, 1, "applied once");
  assert.equal(a.sync.pendingCount, 0);
  assert.equal(cellOf(a.sync.content, "F6"), "一次");
  assert.ok(a.sync.snapshot().canUndo, "and it can still be undone");
});

test("a message never received is renumbered from `next` and sent once", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(6, 6, "丢了"));
  a.disconnect();                                    // the frame never reached the server
  a.connect(); pump(room);
  assert.equal(room.seq, 1);
  assert.equal(room.clients.get("u1/tabAAAAA")!.hi, 1);
});

test("a tab the server forgot: repeatable edits go again, added rows go to the person", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(7, 7, "可重复"));
  a.sync.submit({ op: "append_rows", sheet: SID, count: 2, ids: ["apprw001", "apprw002"], h: 24 });
  room.receive(a, a.outbox.shift());
  room.receive(a, a.outbox.shift());                  // both applied, both answers lost
  a.disconnect();
  room.forget = true;
  a.connect(); pump(room);
  room.forget = false;
  assert.equal(room.content.sheets[SID].nr, 32, "the rows were not added twice");
  const att = a.sync.snapshot().attention;
  assert.equal(att.length, 1);
  assert.equal(att[0].kind, "unknown");
  assert.equal(att[0].label.kind, "append_rows");
  assert.ok(att[0].retry, "the person may do it again after looking");
  assert.equal(cellOf(room.content, "H8"), "可重复");
  assert.ok(repeatable(set(1, 1, 1)) && !repeatable({ op: "tx", ops: [set(1, 1, 1), { op: "append_cols" }] }));
});

test("busy: the same message again after the wait; the frames refused unread (n: null) are matched in order", () => {
  const { room, a } = twoTabs();
  room.busy = 2;
  a.sync.submit(set(8, 0, "等一等"));
  a.sync.submit(set(8, 1, "也等"));
  const sent = a.outbox.map((f) => f.n);
  room.receive(a, a.outbox.shift());
  room.receive(a, a.outbox.shift());
  a.inbox[1][1].n = null;                             // refused unread: the server does not read n
  while (a.inbox.length) a.sync.onSheet(a.inbox.shift()![1]);
  assert.equal(a.sync.debugWire(), 0);
  assert.ok(a.sync.debugPending().every((p) => !p.inFlight));
  assert.equal(a.sync.wakeAt(), a.clock.t + 250);
  a.sync.flush();
  assert.equal(a.outbox.length, 0, "not before the wait");
  a.clock.t += 300;
  a.sync.flush();
  assert.deepEqual(a.outbox.map((f) => f.n), sent, "the same numbers");
  pump(room);
  assert.equal(room.seq, 2);
});

test("resync number_ahead renumbers from `next`; unknown_expect goes again with what the tab has seen", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(9, 0, "编号"));
  const f = a.outbox.shift();
  a.sync.onSheet({ type: "ack", n: f.n, ok: false, code: "resync", detail: { reason: "number_ahead", applied: false, next: 40 } });
  assert.equal(a.outbox[0].n, 40);
  a.outbox.shift();
  a.sync.onSheet({ type: "ack", n: 40, ok: false, code: "resync", detail: { reason: "unknown_expect", expect: 0, seq: 0 } });
  assert.equal(a.outbox[0].n, 40, "not recorded: the same number");
  pump(room);
  assert.equal(cellOf(room.content, "A10"), "编号");
});

test("a duplicate answered in short (trimmed) asks for the whole state", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(9, 9, "短"));
  const f = a.outbox.shift();
  room.receive(a, f);
  a.inbox = [];
  a.sync.onSheet({ type: "ack", n: f.n, ok: true, seq: 1, patch: null, before: null, trimmed: true, duplicate: true });
  assert.deepEqual(a.outbox, [{ type: "sync" }]);
  pump(room);
  assert.deepEqual(a.sync.content, room.content);
});

// ------------------------------------------------------------------ restarts and leaving live mode
test("a restart voids what the tab held; a late stale_epoch answer is not mistaken for a new message", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(0, 0, "旧纪元"));
  const old = a.outbox.shift();                       // on the wire, not yet read by the server
  room.restart("ep-2");
  while (a.inbox.length) a.sync.onSheet(a.inbox.shift()![1]);
  assert.equal(a.sync.epoch, "ep-2");
  assert.equal(cellOf(a.sync.content, "A1"), undefined);
  const [dropped] = a.sync.snapshot().attention;
  assert.equal(dropped.kind, "dropped");
  setLocale("zh-Hans");
  assert.equal(attentionText(dropped, 1), "表格已从已保存的版本重新开始，A1 的修改未应用。");
  a.sync.submit(set(1, 0, "新纪元"));                 // numbered 1 again in the new epoch
  room.receive(a, old);                               // the old frame arrives: stale_epoch, n 1
  pump(room);
  assert.equal(cellOf(room.content, "A2"), "新纪元", "the new message was not taken for the old one");
  assert.deepEqual(a.sync.content, room.content);
  assert.equal(a.sync.snapshot().attention.length, 1);
});

test("a restart while the tab was away: the hello's new epoch voids the queue", () => {
  const { room, a } = twoTabs();
  a.disconnect();
  a.sync.submit(set(2, 0, "离线时写的"));
  room.restart("ep-3");
  a.connect(); pump(room);
  assert.equal(a.sync.epoch, "ep-3");
  assert.equal(room.seq, 0, "nothing of the old epoch was sent");
  assert.equal(a.sync.snapshot().attention[0].code, "reset");
});

test("the room leaves live mode: single-person editing, what was queued is listed", () => {
  const { a } = twoTabs();
  a.disconnect();
  a.sync.submit(set(3, 0, "来不及"));
  a.sync.onMeta({ type: "hello", live: false, live_reason: { code: "too_large", what: "cells", max: 50000, size: 60000 }, user: PEOPLE.u1, readonly: false });
  assert.equal(a.sync.status(), "single");
  assert.equal(a.sync.content, null);
  assert.equal(a.sync.snapshot().attention[0].code, "not_live");
  setLocale("en");
  assert.equal(liveReasonText(a.sync.liveReason), "This table has 60,000 cells, more than live collaboration allows (50,000): changes are merged when saved instead.");
  setLocale("zh-Hans");
  assert.ok(!a.sync.submit(set(0, 0, "x")), "nothing is queued in single-person mode");
});

// ------------------------------------------------------------------ undo
test("undo puts back my own value, and skips a cell someone else changed since", () => {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(0, 0, "第一版"));
  a.sync.submit({ op: "tx", ops: [set(1, 0, "甲"), set(1, 1, "乙")] });
  pump(room);
  b.sync.submit(set(1, 1, "李四改过"));
  pump(room);
  const undone = a.sync.undo() as any;
  assert.ok(undone.sent);
  assert.equal(undone.skipped.length, 1);
  setLocale("zh-Hans");
  assert.equal(skippedText(undone.skipped[0], 1), "未撤销 B2：其他成员之后改过它。");
  pump(room);
  assert.equal(cellOf(room.content, "A2"), undefined, "mine is undone");
  assert.equal(cellOf(room.content, "B2"), "李四改过", "theirs is kept");
  a.sync.undo();
  pump(room);
  assert.equal(cellOf(room.content, "A1"), undefined);
  assert.equal(a.sync.undo(), null, "nothing left to undo (an undo is not itself undone)");
});

test("undo pressed while my last change is on its way undoes that change once answered, not the one before", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(0, 5, "前一次"));
  pump(room);
  const results: any[] = [];
  a.sync.onUndone = (r) => results.push(r);
  a.sync.submit(set(0, 6, "刚输入"));                  // on its way
  assert.equal(a.sync.undo(), "deferred");
  assert.ok(a.sync.snapshot().canUndo);
  pump(room);                                         // answered, then undone
  assert.equal(results.length, 1);
  assert.ok(results[0].sent);
  assert.equal(cellOf(room.content, "G1"), undefined, "the change that was on its way is undone");
  assert.equal(cellOf(room.content, "F1"), "前一次", "the one before it is not");
  // the change an undo waits for is refused: there is nothing to undo, and nothing older is undone
  a.sync.submit({ op: "set", sheet: SID, row: "zzzzzzzz", col: C(0), v: "没有这一行" });
  assert.equal(a.sync.undo(), "deferred");
  pump(room);
  assert.deepEqual(results.slice(1).map((r) => ({ sent: r.sent, cancelled: !!r.cancelled })), [{ sent: false, cancelled: true }]);
  assert.equal(cellOf(room.content, "F1"), "前一次");
});

// ------------------------------------------------------------------ the review of steps ③–⑤ (each failed on 0a6c0cd)
test("H5: undo targets the change on top when pressed — a change never sent is taken off the queue", () => {
  const { room, a } = twoTabs();
  const A2 = set(1, 0, "要撤销的"), C2 = set(1, 2, "留下");
  a.disconnect();
  a.sync.submit(A2);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred" && r.cancelled && !r.sent, "taken back at once");
  assert.equal(cellOf(a.sync.content, "A2"), undefined);
  a.sync.submit(C2);                                  // the person carries on
  a.connect(); pump(room);
  assert.equal(cellOf(room.content, "A2"), undefined, "the undone change never reached the room");
  assert.equal(cellOf(room.content, "C2"), "留下", "the change typed after pressing undo stays");
  assert.deepEqual(a.sync.content, room.content);
});

test("H5: undo pressed while my change is on its way undoes that change, not one typed after pressing", () => {
  const { room, a } = twoTabs();
  const results: any[] = [];
  a.sync.onUndone = (r) => results.push(r);
  a.sync.submit(set(1, 0, "在路上"));                 // sent, unanswered
  assert.equal(a.sync.undo(), "deferred");
  a.sync.submit(set(1, 2, "之后输入"));
  pump(room);
  assert.equal(results.length, 1);
  assert.equal(cellOf(room.content, "A2"), undefined, "the change that was on top when undo was pressed is undone");
  assert.equal(cellOf(room.content, "C2"), "之后输入");
  assert.deepEqual(a.sync.content, room.content);
});

test("H3: undo of a merge that cleared values unmerges first, then puts the values back", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(3, 0, "左上"));
  a.sync.submit(set(3, 1, "被盖住"));
  pump(room);
  a.sync.submit({ op: "tx", ops: [{ op: "clear", sheet: SID, row: R(3), col: C(1) }, { op: "merge", sheet: SID, range: [R(3), C(0), R(3), C(1)], id: "mrgh3001" }] });
  pump(room);
  assert.ok(room.content.merges.mrgh3001);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred" && r.sent);
  pump(room);
  assert.equal(room.content.merges.mrgh3001, undefined, "unmerged");
  assert.equal(cellOf(room.content, "B4"), "被盖住", "and the value is back");
  assert.equal(a.sync.snapshot().attention.length, 0);
});

test("H3b: undo of my merge that replaced someone's unmerges mine before putting theirs back", () => {
  const { room, a, b } = twoTabs();
  b.sync.submit({ op: "merge", sheet: SID, range: [R(2), C(2), R(3), C(2)], id: "mrgbbbb1" });
  pump(room);
  a.sync.submit({ op: "merge", sheet: SID, range: [R(2), C(2), R(3), C(3)], id: "mrgaaaa1", replaces: ["mrgbbbb1"] });
  pump(room);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred" && r.sent);
  pump(room);
  assert.equal(room.content.merges.mrgaaaa1, undefined);
  assert.equal(Object.values(room.content.merges).length, 1, "the replaced range is merged again");
  assert.equal(a.sync.snapshot().attention.length, 0);
});

test("P8: an undo the room refuses is set aside and said — the next undo reaches the change before it", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(0, 2, "更早的"));
  pump(room);
  a.sync.submit(set(0, 3, "我的"));
  pump(room);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred" && r.sent);
  const f = a.outbox.shift();
  a.sync.onSheet({ type: "ack", n: f.n, ok: false, code: "sheet_name_taken", detail: { name: "明细" } });
  const [att] = a.sync.snapshot().attention;
  setLocale("zh-Hans");
  assert.equal(attentionText(att, 1), "已有名为「明细」的工作表（名称不区分大小写）。 这一步撤销没有完成；再按撤销会撤销更早的修改。");
  setLocale("en");
  assert.ok(/undo again/.test(attentionText(att, 1)));
  setLocale("zh-Hans");
  assert.ok((a.sync.undo() as any).sent, "not the same refusal again: the older change");
  pump(room);
  assert.equal(cellOf(room.content, "C1"), undefined, "the change before the refused one is undone");
  assert.equal(cellOf(room.content, "D1"), "我的", "the refused one is left as it is");
});

test("H6 / H7 / H2: a conflict in a group names the cell it is about, offers my value, and lists what else changed", () => {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(1, 1, "a2")); a.sync.submit(set(2, 1, "a3"));
  pump(room);
  b.sync.submit(set(2, 1, "李四的B3"));
  room.receive(b, b.outbox.shift());                  // on the server first; A has not heard of it
  a.sync.submit({ op: "tx", ops: [{ op: "clear", sheet: SID, row: R(1), col: C(1) }, { op: "clear", sheet: SID, row: R(2), col: C(1) }] });
  pump(room);
  const [att] = a.sync.snapshot().attention;
  assert.equal(att.label.ref, "B3", "H6: the cell the refusal is about");
  // H7: typing at the edge is a group [append_rows, set]: the cell and my value, not just the sheet
  b.sync.submit(set(3, 0, "李四的A4"));
  room.receive(b, b.outbox.shift());
  a.sync.submit({ op: "tx", ops: [{ op: "append_rows", sheet: SID, count: 2, ids: ["edgerow1", "edgerow2"], h: 24 }, set(3, 0, "我在边上输入")] });
  pump(room);
  const edge = a.sync.snapshot().attention.at(-1)!;
  assert.equal(edge.label.ref, "A4");
  assert.equal(edge.label.value, "我在边上输入");
  // H2: B changed both cells of A's group; the room names the first, the sentence names the other too
  b.sync.submit(set(5, 1, "李四的B6")); b.sync.submit(set(6, 1, "李四的B7"));
  room.receive(b, b.outbox.shift()); room.receive(b, b.outbox.shift());
  a.sync.submit({ op: "tx", ops: [set(5, 1, "我的B6"), set(6, 1, "我的B7")] });
  pump(room);
  const both = a.sync.snapshot().attention.at(-1)!;
  assert.equal(both.label.ref, "B6");
  assert.deepEqual(both.detail.also.map((c: any) => [c.ref, c.theirs?.v]), [["B7", "李四的B7"]]);
  setLocale("zh-Hans");
  assert.equal(attentionText(both, 1), "李四 已先修改了 B6（现在是「李四的B6」）。 同一操作里 B7（李四 改为「李四的B7」） 在你之后也被改过；再发送你的修改会覆盖它们。");
  setLocale("en");
  assert.ok(/B7/.test(attentionText(both, 1)) && !/[一-鿿]/.test(attentionText(both, 1).replace(/李四\S*/g, "")));
  setLocale("zh-Hans");
});

test("H1: a change refused for now goes again only after everything sent behind it is answered, then all in order", () => {
  const { a } = twoTabs();
  a.sync.submit(set(2, 0, "先"));
  a.sync.submit(set(2, 1, "后"));
  const [f1, f2] = a.outbox.splice(0);
  a.sync.onSheet({ type: "ack", n: f1.n, ok: false, code: "busy", detail: { retry_after: 0.25 } });
  a.clock.t += 300;
  a.sync.submit(set(2, 2, "更后"));                   // typed meanwhile
  a.sync.flush();
  assert.deepEqual(a.outbox, [], "nothing while the frame sent after the refused one is on its way (the room refuses it too)");
  a.sync.onSheet({ type: "ack", n: f2.n, ok: false, code: "busy", detail: { retry_after: 0.05, held: f1.n } });
  a.clock.t += 100;
  a.sync.flush();
  assert.deepEqual(a.outbox.map((f) => f.op.v), ["先", "后", "更后"], "then every one, in the order typed");
  assert.deepEqual(a.outbox.map((f) => f.n).slice(0, 2), [f1.n, f2.n], "with the same numbers");
});

test("H2: 'use my value' is sent against what the person was shown — a cell changed after that is a new conflict", () => {
  const { room, a, b } = twoTabs();
  b.sync.submit(set(8, 1, "李四的B9"));
  room.receive(b, b.outbox.shift());
  a.sync.submit({ op: "tx", ops: [set(8, 1, "我的B9"), set(9, 1, "我的B10")] });
  pump(room);
  const [shown] = a.sync.snapshot().attention;
  assert.equal(shown.label.ref, "B9");
  assert.equal(shown.detail.also, undefined, "B10 had not changed when the person was told");
  b.sync.submit(set(9, 1, "李四之后写的B10"));        // after the person read the sentence
  pump(room);
  assert.ok(a.sync.useMine(shown.id));
  pump(room);
  assert.equal(cellOf(room.content, "B10"), "李四之后写的B10", "not replaced without telling");
  const [again] = a.sync.snapshot().attention;
  assert.equal(again.kind, "conflict");
  assert.equal(again.label.ref, "B10");
});

// ------------------------------------------------------------------ review round 3: P4 and later changes that did not happen
const clear = (r: number, c: number) => ({ op: "clear", sheet: SID, row: R(r), col: C(c) });

/** A wrote B2 and B3; offline, B changes `bChanges` of them and A clears B2:B3 as one group, then `later`. */
function groupThenLater(bChanges: [number, string][], later: () => void) {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(1, 1, "a2")); a.sync.submit(set(2, 1, "a3")); pump(room);
  a.disconnect();
  for (const [r, v] of bChanges) b.sync.submit(set(r, 1, v));
  pump(room);
  a.sync.submit({ op: "tx", ops: [clear(1, 1), clear(2, 1)] });
  later();
  return { room, a, b };
}

test("R3a: a later edit of mine taken off the queue by undo before it was sent leaves the cell to the group — told, and done", () => {
  let undone: any = null;
  const { room, a } = groupThenLater([[1, "B's B2"], [2, "B's B3"]], () => {});
  a.sync.submit(set(2, 1, "typo"));
  undone = a.sync.undo();                                   // never sent: taken off the queue
  assert.ok(undone && undone !== "deferred" && undone.cancelled);
  a.connect(); pump(room);
  const [g] = a.sync.snapshot().attention.filter((x) => x.kind === "conflict");
  assert.equal(g.label.ref, "B2");
  assert.deepEqual((g.detail.also ?? []).map((c: any) => [c.ref, c.theirs?.v]), [["B3", "B's B3"]], "B3 is the group's again: told with B's value");
  for (const lang of ["zh-Hans", "en", "zh-Hant"]) { setLocale(lang as any); assert.ok(attentionText(g, 1).includes("B's B3"), attentionText(g, 1)); }
  setLocale("zh-Hans");
  assert.ok(a.sync.useMine(g.id));
  pump(room);
  assert.equal(cellOf(room.content, "B2"), undefined);
  assert.equal(cellOf(room.content, "B3"), undefined, "the person's delete of B3 is done, as the sentence said");
});

test("R3b: a later edit of mine refused for another reason leaves the cell to the group", () => {
  // B changed only B2: the group's "use my value" clears B3 as well (the refused edit decides nothing)
  const { room, a } = groupThenLater([[1, "B's B2"]], () => {});
  a.sync.submit(set(2, 1, "bad\u0001char"));                // a character Excel cannot hold: refused
  a.connect(); pump(room);
  const att = a.sync.snapshot().attention;
  assert.deepEqual(att.map((x) => [x.kind, x.code]), [["conflict", "conflict"], ["refused", "invalid"]]);
  assert.ok(a.sync.useMine(att[0].id));
  pump(room);
  assert.equal(cellOf(room.content, "B2"), undefined);
  assert.equal(cellOf(room.content, "B3"), undefined, "A's delete of B3 (the group) is done");
});

test("R3b: refused after the group's sentence was made, the cell it had left out is told then — never replaced untold", () => {
  const { room, a } = groupThenLater([[1, "B's B2"], [2, "B's B3"]], () => {});
  a.sync.submit(set(2, 1, "bad\u0001char"));
  a.connect();
  // deliver the hello, the frames, and the group's answer — the later edit is still on its way
  a.sync.onMeta(a.inbox.shift()![1]);
  while (a.outbox.length) room.receive(a, a.outbox.shift());
  const [kind, groupAck] = a.inbox.shift()!;
  assert.equal(kind, "sheet");
  a.sync.onSheet(groupAck);
  let [g] = a.sync.snapshot().attention;
  assert.equal(g.detail.also, undefined, "B3 is left to the later edit while it may still apply (P4)");
  pump(room);                                               // the later edit is refused
  [g] = a.sync.snapshot().attention.filter((x) => x.kind === "conflict");
  assert.deepEqual((g.detail.also ?? []).map((c: any) => [c.ref, c.theirs?.v]), [["B3", "B's B3"]], "now told");
  assert.ok(a.sync.useMine(g.id));
  pump(room);
  assert.equal(cellOf(room.content, "B3"), undefined, "what the person chose, having been told");
});

test("P4 still: a later edit refused as a conflict has its own answer for its cell — whichever is answered first", () => {
  for (const order of ["keep theirs first", "group first"]) {
    const { room, a } = groupThenLater([[1, "B's B2"], [2, "B's B3"]], () => {});
    a.sync.submit(set(2, 1, "A's later B3"));
    a.connect(); pump(room);
    const later = a.sync.snapshot().attention.find((x) => x.label.ref === "B3")!;
    let g = a.sync.snapshot().attention.find((x) => x.label.ref === "B2")!;
    assert.equal(g.detail.also, undefined, "B3 is the later edit's to answer, not the group's");
    if (order === "keep theirs first") {
      a.sync.dismiss(later.id);                              // keep B's B3: that is B3's answer
      g = a.sync.snapshot().attention.find((x) => x.id === g.id)!;
      assert.equal(g.detail.also, undefined, "choosing on the later edit is not a later edit that went away");
      assert.ok(a.sync.useMine(g.id));
    } else {
      assert.ok(a.sync.useMine(g.id));
      pump(room);
      a.sync.dismiss(later.id);
    }
    pump(room);
    assert.equal(cellOf(room.content, "B2"), undefined, order);
    assert.equal(cellOf(room.content, "B3"), "B's B3", `${order}: B3 is what the person chose on its own item`);
  }
});

test("P4 still: a later edit of mine that applied decides its cell — the group's retry leaves it alone", () => {
  const { room, a } = groupThenLater([[1, "B's B2"], [2, "B's B3"]], () => {});
  a.connect(); pump(room);
  a.sync.submit(set(2, 1, "A's newest B3"));
  pump(room);
  assert.equal(cellOf(room.content, "B3"), "A's newest B3");
  const g = a.sync.snapshot().attention.find((x) => x.label.ref === "B2")!;
  assert.ok(a.sync.useMine(g.id));
  pump(room);
  assert.equal(cellOf(room.content, "B2"), undefined);
  assert.equal(cellOf(room.content, "B3"), "A's newest B3", "my own newer value is not overwritten by the older group (P4)");
});

test("P4: a later edit of mine undone after it applied leaves its cell to the earlier group again", () => {
  const { room, a } = groupThenLater([[1, "B's B2"], [2, "B's B3"]], () => {});
  a.connect(); pump(room);                                   // the group conflicts; nothing later yet
  a.sync.submit(set(2, 1, "typed after"));
  pump(room);
  let g = a.sync.snapshot().attention.find((x) => x.kind === "conflict")!;
  assert.equal(cellOf(room.content, "B3"), "typed after");
  const u = a.sync.undo();                                   // takes "typed after" back: B3 is B's again
  assert.ok(u && u !== "deferred" && u.sent);
  pump(room);
  assert.equal(cellOf(room.content, "B3"), "B's B3");
  g = a.sync.snapshot().attention.find((x) => x.id === g.id)!;
  assert.ok(a.sync.useMine(g.id));
  pump(room);
  assert.equal(cellOf(room.content, "B3"), undefined, "the group's delete of B3 is done: the undone edit decides nothing");
});

// ------------------------------------------------------------------ the third review's follow-up (each failed on f2332c4)
for (const how of ["X1: later edit refused", "X2: later edit undone after it applied"]) test(`${how}: told again, the group's 'use my value' still meets a newer value of the conflict's own cell as a new conflict`, () => {
  const { room, a, b } = groupThenLater([[1, "B's B2"]], () => {});
  a.sync.submit(set(2, 1, how.startsWith("X1") ? "bad\u0001char" : "later"));
  a.connect();
  a.sync.onMeta(a.inbox.shift()![1]);                      // the hello
  room.receive(a, a.outbox.shift());                      // the group; the later edit is still on its way
  a.sync.onSheet(a.inbox.shift()![1]);                    // the group's answer: a conflict on B2
  const g0 = a.sync.snapshot().attention.find((x) => x.kind === "conflict")!;
  assert.equal(g0.detail.theirs?.v, "B's B2", how);
  assert.equal(g0.detail.also, undefined, `${how}: B3 is left to the later edit while it may still apply`);
  b.sync.submit(set(1, 1, "B's newer B2"));               // after A was told ...
  room.receive(b, b.outbox.shift());                      // ... in the room before the later edit's answer
  pump(room);                                             // (refused: the group is told again now)
  if (how.startsWith("X2")) {
    const u = a.sync.undo();                              // "later" applied: undone — the group is told again
    assert.ok(u && u !== "deferred" && u.sent, how);
    pump(room);
  }
  const g = a.sync.snapshot().attention.find((x) => x.id === g0.id)!;
  assert.equal(g.detail.theirs?.v, "B's B2", `${how}: the sentence still names the value A was shown`);
  assert.ok(a.sync.useMine(g.id), how);
  pump(room);
  assert.equal(cellOf(room.content, "B2"), "B's newer B2", `${how}: B's newer B2 is not replaced untold (H2)`);
  const again = a.sync.snapshot().attention.find((x) => x.kind === "conflict")!;
  assert.equal(again.label.ref, "B2", how);
  assert.equal(again.detail.theirs?.v, "B's newer B2", `${how}: a new conflict names the newer value`);
  assert.ok(a.sync.useMine(again.id), how);
  pump(room);
  assert.equal(cellOf(room.content, "B2"), undefined, `${how}: told, the person's choice is done`);
  assert.equal(cellOf(room.content, "B3"), undefined, `${how}: B3 is the group's (the later edit does not stand)`);
});

for (const later of ["applied", "conflict"]) test(`X${later === "applied" ? 3 : 4}: a group and a later edit (${later}) resolved in one hello — the cell the later edit decides is not listed as 'would be overwritten'`, () => {
  const { room, a, b } = twoTabs();
  a.sync.submit(set(1, 1, "a2")); a.sync.submit(set(2, 1, "a3")); pump(room);
  b.sync.submit(set(1, 1, "B's B2"));
  if (later === "conflict") b.sync.submit(set(2, 1, "B's B3"));
  while (b.outbox.length) room.receive(b, b.outbox.shift());      // in the room; A has not read the patches
  a.sync.submit({ op: "tx", ops: [clear(1, 1), clear(2, 1)] });
  a.sync.submit(set(2, 1, "A's later B3"));
  while (a.outbox.length) room.receive(a, a.outbox.shift());      // both reach the room ...
  a.disconnect();                                                  // ... and their answers are lost
  a.connect(); pump(room);
  const att = a.sync.snapshot().attention;
  const g = att.find((x) => x.label.ref === "B2")!;
  assert.equal(g.kind, "conflict", later);
  // (resolved from `outcomes`, the answer carries no detail: every changed cell of the group is listed — B2 —
  // but not B3)
  assert.deepEqual((g.detail.also ?? []).map((c: any) => c.ref), ["B2"], `${later}: B3 is the later edit's to decide — not listed`);
  if (later === "conflict") assert.ok(att.some((x) => x.label.ref === "B3" && x.kind === "conflict"), "B3 has its own answer");
  const before = cellOf(room.content, "B3");
  assert.ok(a.sync.useMine(g.id));
  pump(room);
  assert.equal(cellOf(room.content, "B2"), undefined, later);
  assert.equal(cellOf(room.content, "B3"), before, `${later}: left alone, as the sentence said`);
});

test("H9: undo never deletes a sheet someone else worked on since — it says so", () => {
  const { room, a, b } = twoTabs();
  const add = { op: "sheet_add", id: "shth9001", name: "新表", rows: 3, cols: 3, row_ids: ["h9row001", "h9row002", "h9row003"], col_ids: ["h9col001", "h9col002", "h9col003"], h: 24, w: 100 };
  a.sync.submit(add);
  pump(room);
  b.sync.submit({ op: "set", sheet: "shth9001", row: "h9row001", col: "h9col001", v: "李四的工作" });
  pump(room);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred" && !r.sent);
  assert.deepEqual(r.skipped.map((s) => s.reason), ["sheet_changed"]);
  setLocale("zh-Hans");
  assert.equal(skippedText(r.skipped[0], 2), "工作表「新表」之后有其他人的修改（或不再是空表），撤销不会删除它；确实要删除，请用「删除当前表」。");
  pump(room);
  assert.equal(room.content.sheets.shth9001.gone, false);
  // a sheet nobody touched since is still taken back by undo
  a.sync.submit({ ...add, id: "shth9002", name: "空表", row_ids: ["h9row011", "h9row012", "h9row013"], col_ids: ["h9col011", "h9col012", "h9col013"] });
  pump(room);
  assert.ok((a.sync.undo() as any).sent);
  pump(room);
  assert.equal(room.content.sheets.shth9002.gone, true);
});

test("H9b: undo of my restore of a sheet leaves it when someone worked on it since", () => {
  const { room, a, b } = twoTabs();
  a.sync.submit({ op: "sheet_add", id: "shth9b01", name: "恢复表", rows: 2, cols: 2, row_ids: ["h9brow01", "h9brow02"], col_ids: ["h9bcol01", "h9bcol02"] });
  pump(room);
  b.sync.submit({ op: "sheet_delete", sheet: "shth9b01" });
  pump(room);
  a.sync.submit({ op: "sheet_restore", sheet: "shth9b01" });
  pump(room);
  b.sync.submit({ op: "set", sheet: "shth9b01", row: "h9brow01", col: "h9bcol01", v: "恢复后写的" });
  pump(room);
  const r = a.sync.undo();
  assert.ok(r && r !== "deferred");
  assert.deepEqual(r.skipped.map((s) => s.reason), ["sheet_changed"]);
  pump(room);
  assert.equal(room.content.sheets.shth9b01.gone, false);
});

test("my own other tab's edits are not tinted or announced as someone else's", () => {
  const room = new FixtureRoom();
  const a = new Tab(room, "u1", "tabAAAAA"), a2 = new Tab(room, "u1", "tabA2222"), b = new Tab(room, "u2", "tabBBBBB");
  a.connect(); a2.connect(); b.connect(); pump(room);
  a2.sync.submit(set(0, 0, "另一个标签页"));
  b.sync.submit(set(0, 1, "李四"));
  pump(room);
  assert.equal(a.sync.recentAt(SID, R(0), C(0)), null, "mine, from my other tab");
  assert.equal(a.sync.recentAt(SID, R(0), C(1))?.by.id, "u2");
  assert.deepEqual(a.sync.takeAnnouncements().map((x) => x.by), ["李四"]);
});

test("a whole-state request refused by the rate limit is asked again after the wait", () => {
  const { room, a } = twoTabs();
  a.sync.submit(set(9, 8, "短"));
  const f = a.outbox.shift();
  room.receive(a, f);
  a.inbox = [];
  a.sync.onSheet({ type: "ack", n: f.n, ok: true, seq: 1, patch: null, before: null, trimmed: true, duplicate: true });
  assert.deepEqual(a.outbox.splice(0), [{ type: "sync" }]);
  a.sync.onSheet({ type: "ack", n: null, ok: false, code: "rate_limited", detail: { retry_after: 2 } });
  assert.equal(a.sync.wakeAt(), a.clock.t + 2000);
  a.clock.t += 2100;
  a.sync.flush();
  assert.deepEqual(a.outbox, [{ type: "sync" }], "asked again");
  pump(room);
  assert.deepEqual(a.sync.content, room.content);
});

test("undo of styles, sizes, merges, fields and sheets; added rows cannot be taken away", () => {
  const { room, a } = twoTabs();
  const ops = [
    { op: "style", sheet: SID, row: R(0), col: C(0), s: { b: 1, color: "#ff0000" } },
    { op: "size", sheet: SID, col: C(2), w: 180 },
    { op: "merge", sheet: SID, range: [R(5), C(0), R(5), C(1)], id: "mrgundo1" },
    { op: "field_add", sheet: SID, field: { id: "fUndo", name: "撤销字段", kind: "cell" }, row: R(6), col: C(0) },
    { op: "sheet_add", id: "shtundo1", name: "新工作表", rows: 1, cols: 1, row_ids: ["rrrundo1"], col_ids: ["cccundo1"] },
    { op: "sheet_rename", sheet: SID, name: "改名了" },
  ];
  const start = copy(room.content);
  for (const op of ops) { a.sync.submit(op); pump(room); }
  for (let i = 0; i < ops.length; i++) { assert.ok((a.sync.undo() as any).sent); pump(room); }
  assert.deepEqual(projectSheet(room.content, SID), projectSheet(start, SID));
  assert.ok(room.content.sheets.shtundo1.gone, "the new sheet is deleted (restorable until a save)");
  a.sync.submit({ op: "append_rows", sheet: SID, count: 1, ids: ["apprw009"] });
  pump(room);
  const u = a.sync.undo() as any;
  assert.equal(u.sent, false);
  assert.equal(u.skipped[0].reason, "append");
  setLocale("en");
  assert.equal(skippedText(u.skipped[0]), "Rows and columns that were added cannot be undone.");
  setLocale("zh-Hans");
});

// ------------------------------------------------------------------ who changed what: tint, dots, announcements
test("cells others just changed are tinted for ~3 s; other sheets get a dot; screen readers hear it", () => {
  const { room, a, b } = twoTabs();
  a.sync.view(SID);
  b.sync.submit(set(0, 3, "刚改"));
  b.sync.submit({ op: "sheet_add", id: "shtdot01", name: "第二张", rows: 1, cols: 1, row_ids: ["rrrdot01"], col_ids: ["cccdot01"] });
  pump(room);
  b.sync.submit({ op: "set", sheet: "shtdot01", row: "rrrdot01", col: "cccdot01", v: 1 });
  pump(room);
  const tint = a.sync.recentAt(SID, R(0), C(3))!;
  assert.equal(tint.by.name, "李四");
  a.clock.t += 3100;
  assert.equal(a.sync.recentAt(SID, R(0), C(3)), null, "gone after ~3 s");
  assert.deepEqual([...a.sync.changedSheets().keys()], ["shtdot01"], "a dot on the other sheet, not on this one");
  a.sync.view("shtdot01");
  assert.equal(a.sync.changedSheets().size, 0);
  setLocale("zh-Hans");
  const said = a.sync.takeAnnouncements().map(announcementText);
  assert.deepEqual(said, ["李四 修改了 D1", "李四 修改了 第二张!A1"], "the sheet is named once there is more than one");
  assert.deepEqual(a.sync.takeAnnouncements(), [], "each once");
});

// ------------------------------------------------------------------ the sentences, in three languages
const CJK = /[㐀-鿿]/;
const LABEL = { kind: "set", sheet: "Sheet1", ref: "B2", value: "x" };
const REFUSALS: [string, Record<string, any>][] = [
  ["conflict", { theirs: { v: "theirs" }, by: "u2" }], ["covered", { ref: "B1", range: "A1:C1", by: "u2" }],
  ["merge_overlap", { want: "A1:B2", range: "B2:C3", by: "u2" }], ["covers_values", { want: "A1:B2", cells: ["B2"], count: 1, by: [] }],
  ["merged_inner", { ref: "B1", range: "A1:C1", merge: "m", by: null }], ["merged_inner", { want: "A1:C1", field: "f", name: "Customer", ref: "B1" }],
  ["merged", { ref: "B5:C6", range: "B5:C5", merge: "m" }], ["merged", { want: "B5:C5", field: "f", name: "Area", ref: "B5:C6" }],
  ["vertical_merge", { ref: "A4:C4", range: "A3:A5", merge: "m" }], ["vertical_merge", { want: "A3:A5", field: "f", name: "Items", ref: "A4:C4" }],
  ["field_overlap", { ref: "B2", field: "f", name: "Customer" }], ["duplicate_repeat", { ref: "A4:C4", field: "f", name: "Items" }],
  ["id_taken", { what: "field", id: "f", name: "Customer" }], ["id_taken", { what: "merge", id: "x" }], ["sheet_name_taken", { name: "Sheet2" }],
  ["sheet_deleted", { name: "Sheet2", by: "u2" }], ["last_sheet", {}], ["not_found", { what: "row" }], ["not_found", { what: "field" }],
  ...["value", "formula", "cells", "grid", "rows", "cols", "merge", "tx", "sheets", "table", "other"].map((what) => ["too_large", { what, max: 5 }] as [string, Record<string, any>]),
  ...["cell", "unwritable", "number_format", "style", "size", "not_in_grid", "single_cell_merge", "sheet_name", "field_name", "field_id", "field", "count", "future"]
    .map((reason) => ["invalid", { reason, field: "v", min: 1, max: 1000 }] as [string, Record<string, any>]),
  ["resync", { reason: "expect_too_old" }], ["read_only", {}], ["not_live", {}], ["reset", {}], ["closed", {}], ["unknown", {}], ["error", {}], ["future_code", {}],
];

test("every refusal and every status reads as a sentence in each language, with no Chinese outside Chinese", () => {
  const people = { u2: { id: "u2", name: "Li Si", color: 2 } };
  for (const locale of ["en", "zh-Hant", "zh-Hans"] as const) {
    setLocale(locale);
    const texts = [
      ...REFUSALS.map(([code, d]) => refusalText(code, d, people, LABEL)),
      ...(["live", "syncing", "offline", "single", "connecting"] as const).flatMap((s) => [statusText(s, 3), statusText(s, 0)]),
      ...[[4409, "superseded"], [4409, "stale_document"], [4410, "template_deleted"], [4429, "too_many_tabs"], [4429, "room_full"], [4401, "session"], [4403, "role"], [4404, "not_found"], [4400, "frame"]]
        .map(([c, r]) => closeText(c as number, r as string)),
      retryText(4503), retryText(1011), retryText(1006),
      liveReasonText({ code: "too_large", what: "bytes" }), liveReasonText({ code: "no_model" }),
      skippedText({ reason: "idless", sheet: "S", ref: null, field: "f" }), skippedText({ reason: "changed", sheet: "S", ref: "A1", field: "FieldA" }),
      announcementText({ by: null, where: "A1", count: 3 }),
      saveText({ unchanged: true, version: { no: 3 } })[0], saveText({ live: { pending: true }, head: 4 })[0], saveText({ head: 5 })[0],
      attentionText({ id: 1, kind: "overwritten", code: "overwritten", detail: { by: "u2" }, people, label: LABEL, retry: null } as Attention),
    ];
    for (const s of texts) {
      assert.ok(s && !s.includes("{"), `${locale}: ${s}`);
      if (locale === "en") assert.ok(!CJK.test(s), `untranslated: ${s}`);
    }
  }
  setLocale("zh-Hans");
});

test("labels name the sheet, cell and value an operation was about", () => {
  assert.deepEqual(labelOf(SEED, set(1, 1, "值")), { kind: "set", sheet: "Sheet1", ref: "B2", value: "值", name: null });
  assert.deepEqual(labelOf(SEED, { op: "merge", sheet: SID, range: [R(2), C(3), R(0), C(1)] }).ref, "B1:D3");
  assert.equal(labelOf(SEED, { op: "tx", ops: [set(0, 0, 1), set(0, 1, 2)] }).kind, "tx");
});
