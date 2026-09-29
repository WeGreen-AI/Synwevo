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
    let res: any;
    const conflict = this.conflict(f, tab.user);
    if (conflict) res = { ok: false, code: "conflict", detail: conflict };
    else {
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

  /** first writer wins on a cell value (the server does it for every object) */
  conflict(f: any, by: string): any {
    const ops = f.op?.op === "tx" ? f.op.ops : [f.op];
    for (const op of ops) {
      if (op?.op !== "set" && op?.op !== "clear") continue;
      const k = this.cellSeq.get(`${op.sheet}/${op.row}:${op.col}`);
      if (k && k[0] > f.expect && k[1] !== by) {
        const cell = this.content.sheets[op.sheet].cells[`${op.row}:${op.col}`];
        return { key: `v/${op.sheet}/${op.row}:${op.col}`, theirs: cell ? { v: cell.v } : null, by: k[1], at: k[0] };
      }
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
  const undone = a.sync.undo()!;
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
  for (let i = 0; i < ops.length; i++) { assert.ok(a.sync.undo()!.sent); pump(room); }
  assert.deepEqual(projectSheet(room.content, SID), projectSheet(start, SID));
  assert.ok(room.content.sheets.shtundo1.gone, "the new sheet is deleted (restorable until a save)");
  a.sync.submit({ op: "append_rows", sheet: SID, count: 1, ids: ["apprw009"] });
  pump(room);
  const u = a.sync.undo()!;
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
