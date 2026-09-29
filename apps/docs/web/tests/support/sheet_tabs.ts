// TEST FIXTURE (not product code): hosts real SheetSync tabs (src/lib/sheetsync.ts) and speaks JSON
// lines on stdin/stdout, so apps/docs/tests/test_sheet_live_clients.py can connect them to the real
// SheetRoom (collab_sheet_room.py) and deliver every frame in whatever order it chooses. It records
// what became of each operation a tab queued ("fates"), which operations undo made, and builds random
// operations the way pages/SheetEditor.tsx builds them from the tab's own view. The approach is the
// reviewer's harness of the live-table review (steps ③–⑤), kept in CI.
import { createInterface } from "node:readline";
import { SheetSync } from "../../src/lib/sheetsync.ts";
import { liveSheetIds, filled } from "../../src/lib/sheetmodel.ts";
import { attentionText } from "../../src/lib/sheettext.ts";
import "../../src/lib/presentation.ts";
import { setLocale } from "@synwevo/ui/i18n-core";

/** seeded random numbers (mulberry32): a failing seed replays exactly */
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

interface TabRec {
  sync: SheetSync;
  out: any[];
  undone: any[];
  fates: Map<number, string>;
  ops: Map<number, any>;
  undos: { id: number; op: any; undoneSeq: number; epoch: string }[];
  /** the message number and client each of its changes the room applied had (matched against the room's log) */
  appliedN: Map<number, { n: number; client: string | null; epoch: string | null }>;
}
const tabs = new Map<string, TabRec>();
let clock = 1_000_000;
let R = rng(1);
const B36 = "0123456789abcdefghijklmnopqrstuvwxyz";
const newId = () => { let s = ""; for (let i = 0; i < 8; i++) s += B36[Math.floor(R() * 36)]; return s; };
let valNo = 0;

// What became of each queued operation: the engine's private steps are wrapped (TypeScript's
// `private` is compile-time only). A queued operation's id is its place in the tab's sequence.
const P: any = SheetSync.prototype;
const origQueue = P.queue, origAdd = P.addAttention, origDrop = P.drop, origUndo = P.undo, origSubmit = P.submit;
P.queue = function (op: any, opts: any) {
  const p = origQueue.call(this, op, opts);
  this.__rec?.ops.set(p.id, op);
  return p;
};
P.submit = function (op: any, opts: any = {}) {
  const ok = origSubmit.call(this, op, opts);
  if (ok && opts.undoOf) this.__rec?.undos.push({ id: this.seqNo, op, undoneSeq: opts.undoOf.seq, epoch: opts.undoOf.epoch });
  return ok;
};
P.addAttention = function (kind: string, code: string, detail: any, people: any, p: any, retry?: any) {
  this.__rec?.fates.set(p.id, `${kind}:${code}`);
  return origAdd.call(this, kind, code, detail, people, p, retry);
};
P.drop = function (kind: string, code: string, list: any[]) {
  for (const p of list) this.__rec?.fates.set(p.id, `${kind}:${code}`);
  return origDrop.call(this, kind, code, list);
};
P.undo = function () {
  // undo of a change never sent takes it off the queue: it never reaches the room, as asked
  const before = new Set<number>(this.debugPending().map((p: any) => p.id));
  const r = origUndo.call(this);
  if (r && r !== "deferred" && r.cancelled) for (const id of before) if (!this.debugPending().some((p: any) => p.id === id)) this.__rec?.fates.set(id, "cancelled");
  return r;
};

function mk(name: string): TabRec {
  const rec: TabRec = { sync: null as any, out: [], undone: [], fates: new Map(), ops: new Map(), undos: [], appliedN: new Map() };
  rec.sync = new SheetSync({ send: (f) => rec.out.push(f), now: () => clock, newId });
  (rec.sync as any).__rec = rec;
  rec.sync.onUndone = (r) => rec.undone.push(r);
  tabs.set(name, rec);
  return rec;
}

const pick = <T,>(xs: T[]): T => xs[Math.floor(R() * xs.length)];
const int = (lo: number, hi: number) => lo + Math.floor(R() * (hi - lo + 1));

const ALL = ["set", "set", "set", "set", "clear", "grow_set", "style", "size", "merge", "unmerge", "field_add", "field_set", "field_del",
  "sheet_add", "sheet_rename", "sheet_delete", "sheet_restore", "append_rows", "append_cols", "delete_sel", "undo", "undo", "answer"];

/** An operation as SheetEditor builds it from what the tab shows. */
function randomOp(sync: SheetSync, kinds: string[] | null): { op: any; kind: string } | null {
  const c: any = sync.content;
  if (!c) return null;
  const sids = liveSheetIds(c);
  if (!sids.length) return null;
  const sid = pick(sids);
  const sh = c.sheets[sid];
  const nr = sh.nr, nc = sh.nc;
  if (!nr || !nc) return null;
  const kind = pick(kinds ?? ALL);
  const r = int(0, nr - 1), col = int(0, nc - 1);
  const at = (rr: number, cc: number) => ({ sheet: sid, row: sh.rows[rr], col: sh.cols[cc] });
  const val = () => { valNo++; return R() < 0.2 ? String(valNo) : `v${valNo}`; };
  switch (kind) {
    case "set": { const v = val(); return { kind, op: { op: "set", ...at(r, col), v, ...(/^\d+$/.test(v) ? { t: "number" } : {}) } }; }
    case "clear": return { kind, op: { op: "clear", ...at(r, col) } };
    case "grow_set": {
      // typing near the edge: rows added and the value, in one group
      const n = int(1, 3);
      return { kind, op: { op: "tx", ops: [{ op: "append_rows", sheet: sid, count: n, ids: Array.from({ length: n }, newId), h: 24 }, { op: "set", ...at(r, col), v: val() }] } };
    }
    case "style": return { kind, op: { op: "style", ...at(r, col), s: pick([{ b: 1 }, { b: null }, { i: 1 }, { bg: "#ff0000" }, { bg: null }, { ha: "center" }]) } };
    case "size": return R() < 0.5 ? { kind, op: { op: "size", sheet: sid, row: sh.rows[r], h: int(12, 60) } } : { kind, op: { op: "size", sheet: sid, col: sh.cols[col], w: int(20, 200) } };
    case "merge": {
      const r2 = Math.min(nr - 1, r + int(0, 1)), c2 = Math.min(nc - 1, col + int(0, 1));
      if (r2 === r && c2 === col) return null;
      // SheetEditor.merge: hidden values cleared first, overlapping merges replaced
      const ops: any[] = [];
      for (let rr = r; rr <= r2; rr++) for (let cc = col; cc <= c2; cc++) {
        if (rr === r && cc === col) continue;
        if (filled(sh.cells[`${sh.rows[rr]}:${sh.cols[cc]}`])) ops.push({ op: "clear", ...at(rr, cc) });
      }
      const rp = new Map(sh.rows.map((x: string, i: number) => [x, i])), cp = new Map(sh.cols.map((x: string, i: number) => [x, i]));
      const replaces = sh.merges.filter((mid: string) => {
        const a = c.merges[mid].at; const m = [rp.get(a[0]), cp.get(a[1]), rp.get(a[2]), cp.get(a[3])] as number[];
        return !(m[2] < r || m[0] > r2 || m[3] < col || m[1] > c2);
      });
      ops.push({ op: "merge", sheet: sid, range: [sh.rows[r], sh.cols[col], sh.rows[r2], sh.cols[c2]], id: newId(), ...(replaces.length ? { replaces } : {}) });
      return { kind, op: ops.length === 1 ? ops[0] : { op: "tx", ops } };
    }
    case "unmerge": { if (!sh.merges.length) return null; return { kind, op: { op: "unmerge", id: pick(sh.merges) } }; }
    case "field_add": {
      const fk = pick(["cell", "cell", "region", "repeat"]);
      const id = "f" + newId();
      const place = fk === "cell" ? { row: sh.rows[r], col: sh.cols[col] }
        : fk === "region" ? { range: [sh.rows[r], sh.cols[col], sh.rows[Math.min(nr - 1, r + 1)], sh.cols[Math.min(nc - 1, col + 1)]] }
        : { row: sh.rows[r], c1: sh.cols[col], c2: sh.cols[Math.min(nc - 1, col + 1)] };
      return { kind, op: { op: "field_add", sheet: sid, field: { id, name: `F${++valNo}`, kind: fk, ...(fk === "repeat" ? { empty_policy: "remove_row" } : {}) }, ...place } };
    }
    case "field_set": { if (!sh.fields.length) return null; return { kind, op: { op: "field_set", field: pick(sh.fields), set: { required: R() < 0.5 } } }; }
    case "field_del": { if (!sh.fields.length) return null; return { kind, op: { op: "field_del", field: pick(sh.fields) } }; }
    case "sheet_add": {
      if (sids.length >= 4) return null;
      const n = 3;
      return { kind, op: { op: "sheet_add", id: newId(), name: `S${++valNo}`, rows: n, cols: n, row_ids: Array.from({ length: n }, newId), col_ids: Array.from({ length: n }, newId), h: 24, w: 100 } };
    }
    case "sheet_rename": return { kind, op: { op: "sheet_rename", sheet: sid, name: `N${++valNo}` } };
    case "sheet_delete": { if (sids.length < 2) return null; return { kind, op: { op: "sheet_delete", sheet: sid } }; }
    case "sheet_restore": {
      // the editor's restore path for a deleted sheet (a fresh name: the old one may be taken)
      const gone = c.order.filter((x: string) => c.sheets[x].gone);
      if (!gone.length || sids.length >= 4) return null;
      return { kind, op: { op: "sheet_restore", sheet: pick(gone), name: `R${++valNo}` } };
    }
    case "append_rows": { const n = int(1, 3); return { kind, op: { op: "append_rows", sheet: sid, count: n, ids: Array.from({ length: n }, newId), h: 24 } }; }
    case "append_cols": return { kind, op: { op: "append_cols", sheet: sid, count: 1, ids: [newId()], w: 100 } };
    case "delete_sel": {
      const ops: any[] = [];
      const r2 = Math.min(nr - 1, r + int(0, 2)), c2 = Math.min(nc - 1, col + int(0, 2));
      for (let rr = r; rr <= r2; rr++) for (let cc = col; cc <= c2; cc++) if (filled(sh.cells[`${sh.rows[rr]}:${sh.cols[cc]}`])) ops.push({ op: "clear", ...at(rr, cc) });
      if (!ops.length) return null;
      return { kind, op: ops.length === 1 ? ops[0] : { op: "tx", ops } };
    }
    case "undo": return { kind: "undo", op: null };
    case "answer": return { kind: "answer", op: null };
  }
  return null;
}

function view(rec: TabRec) {
  const s: any = rec.sync;
  return {
    content: s.content, pending: s.debugPending(), wire: s.debugWire(), status: s.status(), attention: s.attention,
    epoch: s.epoch, seq: s.seq, clientId: s.clientId, live: s.live, closed: s.closed, readonly: s.readonly, stale: s.stale,
  };
}

function handle(m: any): any {
  if (m.cmd === "seed") { R = rng(m.seed); valNo = 0; return {}; }
  if (m.cmd === "clock") { clock = m.t; return {}; }
  if (m.cmd === "new") { mk(m.tab); return {}; }
  const rec = tabs.get(m.tab);
  if (!rec) throw new Error(`no tab ${m.tab}`);
  const s: any = rec.sync;
  // a change undo was pressed on while it was on its way leaves the queue without being applied as
  // itself when it is refused or taken back: what the person asked for
  const undoIds = new Set<number>(s.debugPending().filter((p: any) => p.undoRequested).map((p: any) => p.id));
  const numbered = new Map<number, number | null>(s.debugPending().map((p: any) => [p.id, p.n]));
  const client = s.clientId, epoch = s.epoch;
  try { return handleCmd(m, rec, s); } finally {
    for (const id of undoIds) if (!s.pending.some((p: any) => p.id === id) && !rec.fates.has(id)) rec.fates.set(id, "undone");
    // left the queue without a fate: applied, under the number it was sent with
    for (const [id, n] of numbered) {
      if (n !== null && !rec.fates.has(id) && !s.pending.some((p: any) => p.id === id)) rec.appliedN.set(id, { n, client, epoch });
    }
  }
}

function handleCmd(m: any, rec: TabRec, s: any): any {
  let result: any = null;
  switch (m.cmd) {
    case "open": s.onOpen(); break;
    case "meta": s.onMeta(m.frame); break;
    case "sheet": s.onSheet(m.frame); break;
    case "disconnect": s.onDisconnect(); break;
    case "close": s.onClose(m.code, m.reason); break;
    case "flush": s.flush(); break;
    case "submit": {
      const ok = s.submit(m.op, m.undoable === undefined ? {} : { undoable: m.undoable });
      result = { ok, id: ok ? s.seqNo : null };
      break;
    }
    case "act": {
      let got: any = null;
      for (let i = 0; i < 8 && !got; i++) got = randomOp(s, m.kinds ?? null);
      if (!got) { result = { kind: null }; break; }
      if (got.kind === "undo") {
        const r = s.undo();
        result = { kind: "undo", undo: r === "deferred" ? "deferred" : r ? { sent: r.sent, skipped: r.skipped, cancelled: !!r.cancelled } : null };
        break;
      }
      if (got.kind === "answer") {
        // the person answers something in "needs you": use my value / do it again, or dismiss
        const att = s.attention;
        if (!att.length) { result = { kind: "answer", none: true }; break; }
        const a = pick(att) as any;
        if (a.retry && R() < 0.6) result = { kind: "answer", useMine: s.useMine(a.id) };
        else { s.dismiss(a.id); result = { kind: "answer", dismissed: true }; }
        break;
      }
      const ok = s.submit(got.op);
      result = { kind: got.kind, op: got.op, ok, id: ok ? s.seqNo : null };
      break;
    }
    case "undo": { const r = s.undo(); result = r === "deferred" ? "deferred" : r ? { sent: r.sent, skipped: r.skipped, cancelled: !!r.cancelled } : null; break; }
    case "useMine": result = s.useMine(m.id); break;
    case "dismiss": s.dismiss(m.id); break;
    case "state": result = view(rec); break;
    case "snapshot": { const x = s.snapshot(); result = { pending: x.pending, unpersisted: x.unpersisted, canUndo: x.canUndo, status: x.status }; break; }
    case "fates": {
      const pendingIds = new Set(s.pending.map((p: any) => p.id));
      const out: any[] = [];
      for (const [id, op] of rec.ops) out.push({ id, op, fate: rec.fates.get(id) ?? (pendingIds.has(id) ? "pending" : "applied?") });
      result = out;
      break;
    }
    case "undos": result = rec.undos; break;
    case "appliedN": result = Object.fromEntries(rec.appliedN); break;
    case "undone": result = rec.undone.splice(0); break;
    case "texts": {
      const out: any = {};
      for (const lang of ["zh-Hans", "en", "zh-Hant"]) { setLocale(lang as any); out[lang] = s.attention.map((a: any) => attentionText(a, 2)); }
      setLocale("zh-Hans" as any);
      result = out;
      break;
    }
    default: throw new Error(`bad cmd ${m.cmd}`);
  }
  return { out: rec.out.splice(0), result };
}

const rl = createInterface({ input: process.stdin });
rl.on("line", (line) => {
  let reply: any;
  try { reply = handle(JSON.parse(line)); }
  catch (e: any) { reply = { error: String((e && e.stack) || e) }; }
  process.stdout.write(JSON.stringify(reply) + "\n");
});
