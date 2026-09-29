/** A tab's side of a live table room (docs/platform/docs-sheet-live.md §3, §5 and §9; B1 step ③).
 *
 *  The server orders every operation and its answer is the truth; the tab shows its own operations at
 *  once (`sheetmodel.applyOp`, the server's own semantics) and keeps them in a queue until the server
 *  has answered. The content the tab shows is the server's as far as the tab has heard, with the
 *  queued operations applied on top: when someone else's patch arrives, the queued operations are
 *  taken off, the patch applied and the operations applied again (a rebase by re-running them — ids
 *  are stable, so an operation still means the same thing on the newer content).
 *
 *  §9's client rules, as the server speaks them:
 *  - every operation frame carries the room's epoch; a restart (`reset`, or a new epoch in `hello`)
 *    voids whatever the tab still held from the previous one: those operations are listed for the
 *    person, never sent into the new epoch;
 *  - numbering: the tab keeps its client id (in memory) and brings it back on reconnect; messages are
 *    numbered from the `next` the server gives. Whether an unanswered message was applied is decided
 *    from `outcomes` only: `true` applied, a refusal code not applied, absent (for a client the server
 *    still knows) not received. For a client the server has forgotten (`resumed: false`) the outcome
 *    is unknown: an operation that can be repeated safely (writing a value, a style, a size — the
 *    server answers "already so" or a conflict) is sent again; one that cannot (adding rows or
 *    columns) is handed to the person, never resent automatically;
 *  - every frame the tab sends is answered exactly once, in order (an `ack`, or `full` for `sync`), so
 *    an answer belongs to the oldest frame on the wire — also the `n: null` answers to frames the
 *    server refused unread, and the late `stale_epoch` of a frame sent before a restart;
 *  - `busy` / `rate_limited`: the same message again after `retry_after` (the server did not record
 *    it); `resync`: `number_ahead` renumbers from `next`, `forgotten` as for a forgotten client,
 *    `unknown_expect` sends it again against what the tab has seen, `expect_too_old` goes to the person.
 *
 *  Refusals and conflicts go to "needs you" (`attention`) with what the server said and whose value
 *  it was, and — for a conflict — the choice "use my value" (sent again against what is there now)
 *  or "keep theirs". Undo is per person, from the server's own inverse, skipping what others changed
 *  since (`sheetundo.planUndo`).
 *
 *  Pure: no socket and no clock of its own. The page wires it to the room (`sheetroom.ts`), which
 *  passes frames in and sends what it hands out. */
import { applyOp, applyPatch, cellRef, copy, isDict, own, Unpredictable, type Content, type PatchEntry } from "./sheetmodel.ts";
import { planUndo, type Skipped } from "./sheetundo.ts";

export type SyncStatus = "connecting" | "live" | "syncing" | "offline" | "single";

export interface Person { id: string | null; name: string | null; color: number | null; system?: boolean }

/** What an operation was, for the person (sheet name and A1 at the time it was made). */
export interface OpLabel { kind: string; sheet: string | null; ref: string | null; value?: string | null; name?: string | null }

export interface Limits { max_tx: number; max_frame_bytes: number; ops_per_min: number; max_cells: number; max_bytes: number }

interface Pending {
  id: number;
  op: any;
  expect: number;
  epoch: string;
  n: number | null;
  inFlight: boolean;
  /** its effect in what the tab shows; null while not shown (predicted refusal, or not predictable) */
  local: { patch: PatchEntry[]; before: PatchEntry[] } | null;
  predicted: string | null;
  label: OpLabel;
  undoable: boolean;
}

/** A frame on the wire, waiting for its answer. `p` is null once its operation was voided (restart). */
type Wire = { kind: "op"; p: Pending | null; n: number } | { kind: "sync" };

export type AttentionKind = "conflict" | "refused" | "unknown" | "dropped" | "overwritten";
export interface Attention {
  id: number;
  kind: AttentionKind;
  code: string;
  detail: Record<string, any>;
  people: Record<string, Person>;
  label: OpLabel;
  /** "use my value": what is sent again (against what the tab shows by then), when it makes sense */
  retry: any | null;
}

export interface UndoEntry { patch: PatchEntry[]; before: PatchEntry[]; label: OpLabel; epoch: string }
export interface Recent { by: Person; until: number }
export interface Announcement { by: string | null; where: string; count: number }

export interface SyncSnapshot {
  status: SyncStatus;
  pending: number;
  live: boolean;
  liveReason: any;
  stale: boolean;
  readonly: boolean;
  epoch: string | null;
  seq: number;
  persisted: number;
  /** changes whenever the content does (for memoizing renders) */
  version: number;
  attention: Attention[];
  canUndo: boolean;
  closed: { code: number; reason: string } | null;
  limits: Limits | null;
}

export interface SyncOptions {
  /** hand a frame (101, as JSON) to the socket */
  send: (frame: Record<string, any>) => void;
  now?: () => number;
  /** makes the ids the tab proposes for new rows, columns, merged ranges and sheets */
  newId?: () => string;
  /** the most messages on the wire at once */
  window?: number;
}

const REPEATABLE_NOT = new Set(["append_rows", "append_cols"]);
/** Whether sending an operation twice is safe: the second one is "already so" or a conflict. */
export function repeatable(op: any): boolean {
  if (!isDict(op)) return true;
  if (op.op === "tx") return Array.isArray(op.ops) && op.ops.every(repeatable);
  return !REPEATABLE_NOT.has(op.op);
}

const B36 = "0123456789abcdefghijklmnopqrstuvwxyz";
export function randomId(): string {
  const bytes = new Uint8Array(8);
  globalThis.crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => B36[b % 36]).join("");
}

/** The sheet name, A1 reference and value an operation is about, in `content` as it is now. */
export function labelOf(content: Content | null, op: any): OpLabel {
  const kind = isDict(op) ? String(op.op) : "invalid";
  if (!content || !isDict(op)) return { kind, sheet: null, ref: null };
  if (kind === "tx" && Array.isArray(op.ops) && op.ops.length) {
    const first = labelOf(content, op.ops[0]);
    return { ...first, kind: op.ops.length > 1 ? "tx" : first.kind };
  }
  let sid: string | null = typeof op.sheet === "string" ? op.sheet : null;
  if (!sid && typeof op.field === "string") sid = own(content.fields, op.field)?.sheet ?? null;
  if (!sid && typeof op.id === "string") sid = own(content.merges, op.id)?.sheet ?? null;
  const sh = sid ? own(content.sheets, sid) : null;
  let ref: string | null = null;
  if (sh && typeof op.row === "string") {
    const r = sh.rows.indexOf(op.row);
    const cid = typeof op.col === "string" ? op.col : typeof op.c1 === "string" ? op.c1 : null;
    const c = cid ? sh.cols.indexOf(cid) : -1;
    if (r >= 0 && c >= 0) ref = cellRef(r, c);
  } else if (sh && Array.isArray(op.range) && op.range.length === 4) {
    const [r1, c1, r2, c2] = [sh.rows.indexOf(op.range[0]), sh.cols.indexOf(op.range[1]), sh.rows.indexOf(op.range[2]), sh.cols.indexOf(op.range[3])];
    if (r1 >= 0 && c1 >= 0 && r2 >= 0 && c2 >= 0) ref = `${cellRef(Math.min(r1, r2), Math.min(c1, c2))}:${cellRef(Math.max(r1, r2), Math.max(c1, c2))}`;
  }
  const value = kind === "set" && op.v !== undefined && op.v !== null ? String(op.v) : null;
  let name: string | null = null;
  if (kind === "field_add" && isDict(op.field)) name = op.field.name ?? null;
  else if (kind.startsWith("field") && typeof op.field === "string") name = own(own(content.fields, op.field)?.raw, "name") ?? null;
  else if (typeof op.name === "string") name = op.name;
  return { kind, sheet: sh ? sh.name : null, ref, value, name };
}

export class SheetSync {
  private send: (frame: Record<string, any>) => void;
  private now: () => number;
  private newId: () => string;
  private window: number;
  private base: Content | null = null;        // the content the tab shows (server + its own queued operations)
  private pending: Pending[] = [];
  private wire: Wire[] = [];
  private seqNo = 0;
  private attnNo = 0;
  private nextN = 1;
  private waitUntil = 0;
  private listeners = new Set<(s: SyncSnapshot) => void>();
  private recent = new Map<string, Recent>();
  private dots = new Map<string, Set<string>>();
  private prevOutside = new Map<string, any>();
  private undoStack: UndoEntry[] = [];
  private announceQueue: Announcement[] = [];
  private viewing: string | null = null;
  me: string | null = null;
  clientId: string | null = null;
  live = false;
  liveReason: any = null;
  stale = false;
  readonly = false;
  epoch: string | null = null;
  seq = 0;
  persisted = 0;
  connected = false;
  /** this connection's hello has arrived: before it, nothing is sent (it says what became of the queue) */
  greeted = false;
  helloSeen = false;
  closed: { code: number; reason: string } | null = null;
  limits: Limits | null = null;
  attention: Attention[] = [];
  version = 0;
  /** people by id (from hello, patches and refusals): names in sentences */
  people = new Map<string, Person>();

  constructor(opts: SyncOptions) {
    this.send = opts.send;
    this.now = opts.now ?? (() => Date.now());
    this.newId = opts.newId ?? randomId;
    this.window = opts.window ?? 16;
  }

  // ------------------------------------------------------------------ reading
  get content(): Content | null { return this.base; }
  get pendingCount(): number { return this.pending.length; }

  status(): SyncStatus {
    if (this.closed || (this.helloSeen && !this.live)) return "single";
    if (!this.helloSeen) return "connecting";
    if (!this.connected || !this.greeted) return "offline";
    return this.pending.length ? "syncing" : "live";
  }

  snapshot(): SyncSnapshot {
    return {
      status: this.status(), pending: this.pending.length, live: this.live, liveReason: this.liveReason, stale: this.stale,
      readonly: this.readonly, epoch: this.epoch, seq: this.seq, persisted: this.persisted, version: this.version,
      attention: [...this.attention], canUndo: this.undoStack.some((u) => u.epoch === this.epoch), closed: this.closed, limits: this.limits,
    };
  }

  subscribe(fn: (s: SyncSnapshot) => void): () => void {
    this.listeners.add(fn);
    fn(this.snapshot());
    return () => { this.listeners.delete(fn); };
  }

  private changed(content = false) {
    if (content) this.version++;
    const s = this.snapshot();
    this.listeners.forEach((fn) => fn(s));
  }

  /** Someone else changed this cell less than ~3 s ago: who (for the tint). */
  recentAt(sid: string, rid: string, cid: string): Recent | null {
    const k = `${sid}|${rid}:${cid}`;
    const r = this.recent.get(k);
    if (!r) return null;
    if (r.until <= this.now()) { this.recent.delete(k); return null; }
    return r;
  }
  /** Sheets others changed since this tab last showed them, with who (a dot on the tab). */
  changedSheets(): ReadonlyMap<string, Set<string>> { return this.dots; }
  /** The sheet this tab shows now (its dot goes away). */
  view(sid: string | null) { this.viewing = sid; if (sid && this.dots.delete(sid)) this.changed(); }
  /** Others' changes since the last call, for screen readers (the page throttles them). */
  takeAnnouncements(): Announcement[] { const out = this.announceQueue; this.announceQueue = []; return out; }
  /** When `flush()` should run again (the wait the server asked for), or null. */
  wakeAt(): number | null { return this.waitUntil > this.now() && this.pending.some((p) => !p.inFlight) ? this.waitUntil : null; }
  whoIs(id: string | null | undefined): Person | null { return id ? this.people.get(id) ?? null : null; }

  // ------------------------------------------------------------------ connection
  /** A socket is up. Nothing is sent on it before its hello, which says what became of the queue
   *  (outcomes, next): sent earlier, a message could be renumbered by that hello and go twice. */
  onOpen() { this.connected = true; this.greeted = false; this.wire = []; this.changed(); }

  /** The socket is gone: nothing is on the wire any more; the next hello says (outcomes) what became of it. */
  onDisconnect() {
    this.connected = false;
    this.greeted = false;
    this.wire = [];
    for (const p of this.pending) p.inFlight = false;
    this.changed();
  }

  /** The server closed for good (44xx): working alone from here; what was not sent is listed. */
  onClose(code: number, reason: string) {
    this.closed = { code, reason };
    this.connected = false;
    this.wire = [];
    this.drop("dropped", "closed", this.pending);
    this.changed(true);
  }

  // ------------------------------------------------------------------ writing
  canWrite(): boolean { return this.live && !this.readonly && !this.closed && !!this.base; }

  /** Show `op` now and send it (when connected). Returns false when it cannot be written here. */
  submit(op: any, opts: { undoable?: boolean; label?: OpLabel } = {}): boolean {
    if (!this.canWrite() || !this.epoch) return false;
    const p: Pending = { id: ++this.seqNo, op, expect: this.seq, epoch: this.epoch, n: null, inFlight: false, local: null, predicted: null,
      label: opts.label ?? labelOf(this.base, op), undoable: opts.undoable ?? true };
    this.show(p);
    this.pending.push(p);
    this.changed(true);
    this.flush();
    return true;
  }

  /** The tab's prediction of an operation, applied to what it shows. */
  private show(p: Pending) {
    p.local = null;
    p.predicted = null;
    try {
      const res = applyOp(this.base!, p.op, { by: this.me || "me", maxCells: this.limits?.max_cells ?? null });
      if (res.ok) p.local = { patch: res.patch, before: res.before };
      else p.predicted = res.code;
    } catch (e) {
      if (!(e instanceof Unpredictable)) throw e;
    }
  }

  /** Take the queued operations off, change the server's content (and the queue), put them on again. */
  private rebase(change: () => void) {
    for (let i = this.pending.length - 1; i >= 0; i--) {
      const l = this.pending[i].local;
      if (l) applyPatch(this.base!, l.before);
    }
    change();
    for (const p of this.pending) this.show(p);
  }

  /** Send what is queued, in order, while connected, not told to wait, and within the window. */
  flush() {
    if (!this.connected || !this.greeted || !this.live || this.closed || !this.epoch) return;
    if (this.waitUntil > this.now()) return;
    for (const p of this.pending) {
      if (p.inFlight) continue;
      if (this.wire.length >= this.window) break;
      if (p.n === null) p.n = this.nextN++;
      p.inFlight = true;
      this.wire.push({ kind: "op", p, n: p.n });
      this.send({ type: "op", epoch: p.epoch, n: p.n, expect: p.expect, op: p.op });
    }
  }

  /** Ask for the whole state again (answered with `full`). */
  requestFull() {
    if (!this.connected || !this.greeted) return;
    this.wire.push({ kind: "sync" });
    this.send({ type: "sync" });
  }

  // ------------------------------------------------------------------ frames
  onMeta(frame: any) {
    if (!isDict(frame)) return;
    switch (frame.type) {
      case "hello": this.onHello(frame); break;
      case "persisted": if (frame.epoch === this.epoch && typeof frame.seq === "number") { this.persisted = Math.max(this.persisted, frame.seq); this.changed(); } break;
      case "live": this.onLive(frame); break;
      case "overwritten": this.onOverwritten(frame); break;
      case "role": if (typeof frame.readonly === "boolean") { this.readonly = frame.readonly; this.changed(); } break;
      case "joined": if (isDict(frame.user) && frame.user.id) this.people.set(frame.user.id, { id: frame.user.id, name: frame.user.name ?? null, color: frame.user.color ?? null }); break;
      default: break;
    }
  }

  onSheet(frame: any) {
    if (!isDict(frame)) return;
    switch (frame.type) {
      case "ack": this.onAck(frame); break;
      case "patch": this.onPatch(frame); break;
      case "full": this.onFull(frame); break;
      case "reset": this.onReset(frame); break;
      default: break;
    }
  }

  private notePeople(people: any): Record<string, Person> {
    const out: Record<string, Person> = {};
    if (!isDict(people)) return out;
    for (const [id, w] of Object.entries(people)) {
      if (!isDict(w)) continue;
      out[id] = { id, name: w.name ?? null, color: w.color ?? null, system: !!w.system };
      this.people.set(id, out[id]);
    }
    return out;
  }

  private onHello(h: any) {
    this.helloSeen = true;
    this.connected = true;
    this.greeted = true;
    this.wire = [];
    if (isDict(h.user) && h.user.id) { this.me = h.user.id; this.people.set(h.user.id, { id: h.user.id, name: h.user.name ?? null, color: h.user.color ?? null }); }
    for (const p of Array.isArray(h.peers) ? h.peers : []) if (isDict(p) && p.id) this.people.set(p.id, { id: p.id, name: p.name ?? null, color: p.color ?? null });
    this.readonly = !!h.readonly;
    if (!h.live) { this.leaveLive(h.live_reason ?? null); return; }
    this.live = true;
    this.liveReason = null;
    this.closed = null;
    this.stale = h.stale_head != null;
    if (typeof h.client === "string") this.clientId = h.client;
    if (isDict(h.limits)) this.limits = h.limits as Limits;
    this.persisted = typeof h.persisted === "number" ? h.persisted : 0;
    this.take(h, h.resumed === true);
  }

  /** A whole state from the server (hello or full): resolve what was sent and not answered from `outcomes`. */
  private take(f: any, resumed: boolean) {
    const epoch: string = f.epoch;
    if (this.epoch !== null && epoch !== this.epoch) {
      this.drop("dropped", "reset", this.pending);          // restarted while away: nothing of it applies
      this.undoStack = [];
    }
    this.epoch = epoch;
    const outcomes: Record<string, any> = isDict(f.outcomes) ? f.outcomes : {};
    const next: number = typeof f.next === "number" ? f.next : 1;
    const applied: Pending[] = [], refused: [Pending, string][] = [], unknown: Pending[] = [];
    for (const p of this.pending) {
      p.inFlight = false;
      if (p.n === null) continue;
      const o = outcomes[String(p.n)];
      if (o === true) applied.push(p);
      else if (typeof o === "string" && o !== "busy" && o !== "rate_limited") refused.push([p, o]);
      else if (!resumed) {
        if (repeatable(p.op)) p.n = null;          // safe to send again: "already so" or a conflict
        else unknown.push(p);
      } else p.n = null;                            // never received: renumbered from `next`
    }
    for (const p of applied) if (p.undoable && p.local && p.local.patch.length) this.pushUndo(p.local.patch, p.local.before, p.label);
    const gone = new Set<Pending>([...applied, ...refused.map(([p]) => p), ...unknown]);
    for (const [p, code] of refused) this.addAttention(code === "conflict" ? "conflict" : "refused", code, {}, {}, p);
    for (const p of unknown) this.addAttention("unknown", "unknown", {}, {}, p);
    this.pending = this.pending.filter((p) => !gone.has(p));
    this.base = copy(f.content);
    this.seq = typeof f.seq === "number" ? f.seq : 0;
    this.nextN = Math.max(next, 1);
    this.waitUntil = 0;
    for (const p of this.pending) this.show(p);
    this.changed(true);
    this.flush();
  }

  private leaveLive(reason: any) {
    this.live = false;
    this.liveReason = reason;
    if (this.pending.length) this.drop("dropped", "not_live", this.pending);
    this.base = null;
    this.undoStack = [];
    this.changed(true);
  }

  private onLive(f: any) {
    if (f.live === false) { this.leaveLive(f.reason ?? null); return; }
    if (f.stale) { this.stale = true; this.changed(); }
  }

  private onFull(f: any) {
    const i = this.wire.findIndex((w) => w.kind === "sync");
    if (i >= 0) this.wire.splice(i, 1);
    if (f.live === false) { this.leaveLive(f.reason ?? null); return; }
    if (this.epoch !== null && f.epoch !== this.epoch) { this.onReset({ ...f, by: null }); return; }
    // the answer to this tab's `sync`: everything answered before it is in this content; what is still
    // on the wire is answered after it (and keeps its number: nothing new may reuse one)
    this.base = copy(f.content);
    if (typeof f.seq === "number") this.seq = f.seq;
    if (typeof f.next === "number") this.nextN = Math.max(this.nextN, f.next);
    if (typeof f.persisted === "number") this.persisted = f.persisted;
    for (const p of this.pending) this.show(p);
    this.changed(true);
  }

  private onReset(f: any) {
    // the room started afresh from a version: nothing of the previous epoch applies any more (the
    // frames of it still on the wire stay there, answered `stale_epoch` or already applied)
    for (const w of this.wire) if (w.kind === "op") w.p = null;
    this.drop("dropped", "reset", this.pending);
    this.undoStack = [];
    this.epoch = f.epoch;
    this.base = copy(f.content);
    this.seq = typeof f.seq === "number" ? f.seq : 0;
    this.nextN = typeof f.next === "number" ? f.next : 1;
    this.persisted = typeof f.persisted === "number" ? f.persisted : 0;
    this.stale = false;
    if (isDict(f.by) && f.by.id) this.notePeople({ [f.by.id]: f.by });
    this.changed(true);
  }

  private onPatch(f: any) {
    if (!this.base || !Array.isArray(f.patch)) return;
    const by: Person = isDict(f.by) ? { id: f.by.id ?? null, name: f.by.name ?? null, color: f.by.color ?? null, system: !!f.by.system } : { id: null, name: null, color: null };
    if (by.id) this.people.set(by.id, by);
    const outside = isDict(f.outside);
    this.rebase(() => {
      if (outside) {
        // an outside version may replace someone's values: keep what they were ("use my value")
        this.prevOutside.clear();
        for (const e of f.patch as PatchEntry[]) {
          if (e.p[0] === "sheets" && e.p[2] === "cells" && e.p.length === 4) {
            const cur = own(own(own(this.base!.sheets, e.p[1]), "cells"), e.p[3]);
            if (cur !== undefined) this.prevOutside.set(`${e.p[1]}|${e.p[3]}`, copy(cur));
          }
        }
      }
      applyPatch(this.base!, f.patch);
      if (typeof f.seq === "number") this.seq = f.seq;
    });
    this.markRecent(f.patch, by);
    this.changed(true);
  }

  private markRecent(patch: PatchEntry[], by: Person) {
    const until = this.now() + 3000;
    const sheets = new Set<string>();
    let cells = 0;
    let first: string | null = null;
    for (const e of patch) {
      if (e.p[0] !== "sheets" || e.p.length < 3) continue;
      sheets.add(e.p[1]);
      if (e.p[2] === "cells" && e.p.length === 4) {
        this.recent.set(`${e.p[1]}|${e.p[3]}`, { by, until });
        cells++;
        if (!first) {
          const sh = own(this.base!.sheets, e.p[1]);
          const i = e.p[3].indexOf(":");
          if (sh) {
            const r = sh.rows.indexOf(e.p[3].slice(0, i)), c = sh.cols.indexOf(e.p[3].slice(i + 1));
            if (r >= 0 && c >= 0) first = sh.name && Object.keys(this.base!.sheets).length > 1 ? `${sh.name}!${cellRef(r, c)}` : cellRef(r, c);
          }
        }
      }
    }
    const key = by.id || "system";
    for (const sid of sheets) if (sid !== this.viewing) { const s = this.dots.get(sid) ?? new Set<string>(); s.add(key); this.dots.set(sid, s); }
    if (cells && first) this.announceQueue.push({ by: by.name, where: first, count: cells });
  }

  private onOverwritten(f: any) {
    const ids = (Array.isArray(f.people) ? f.people : []).map((x: any) => (isDict(x) ? x.id : x));
    if (!this.me || !ids.includes(this.me)) return;
    const people: Record<string, Person> = {};
    for (const x of Array.isArray(f.people) ? f.people : []) if (isDict(x) && x.id) people[x.id] = { id: x.id, name: x.name ?? null, color: x.color ?? null };
    if (isDict(f.by) && f.by.id) people[f.by.id] = { id: f.by.id, name: f.by.name ?? null, color: f.by.color ?? null, system: !!f.by.system };
    for (const c of Array.isArray(f.conflicts) ? f.conflicts : []) {
      if (!isDict(c) || !Array.isArray(c.by) || !c.by.includes(this.me)) continue;
      let retry: any = null;
      if (c.kind === "cell" && typeof c.sheet_id === "string" && typeof c.ref === "string" && this.base) {
        const sh = own(this.base.sheets, c.sheet_id);
        const m = /^([A-Z]+)(\d+)$/.exec(c.ref);
        if (sh && m) {
          let col = 0;
          for (const ch of m[1]) col = col * 26 + ch.charCodeAt(0) - 64;
          const rid = sh.rows[Number(m[2]) - 1], cid = sh.cols[col - 1];
          const was = rid && cid ? this.prevOutside.get(`${c.sheet_id}|${rid}:${cid}`) : undefined;
          if (isDict(was) && was.v !== undefined && was.v !== null) retry = { op: "set", sheet: c.sheet_id, row: rid, col: cid, v: was.v, ...(was.t != null ? { t: was.t } : {}) };
        }
      }
      this.attention.push({ id: ++this.attnNo, kind: "overwritten", code: "overwritten", detail: { ...c, by: isDict(f.by) ? f.by.id ?? null : null, head: f.head ?? null },
        people, label: { kind: c.kind ?? "cell", sheet: c.sheet_name ?? null, ref: c.ref ?? null, name: c.name ?? null }, retry });
    }
    this.changed();
  }

  /** The answer to the oldest frame on the wire. */
  private onAck(a: any) {
    const w = this.wire.shift();
    if (!w) return;
    if (w.kind === "sync") {
      // a `sync` refused for now (rate limited): ask again after the wait
      const wait = isDict(a.detail) && typeof a.detail.retry_after === "number" ? a.detail.retry_after : 1;
      this.waitUntil = Math.max(this.waitUntil, this.now() + Math.ceil(wait * 1000));
      return;
    }
    const p = w.p;
    if (!p || !this.pending.includes(p)) return;          // a frame of a voided epoch: nothing to do
    const people = this.notePeople(a.people);
    p.inFlight = false;
    if (a.ok) {
      if (a.duplicate && !Array.isArray(a.patch)) {
        // applied before, its answer remembered only in short: keep what the tab shows and take the whole
        this.pending = this.pending.filter((x) => x !== p);
        this.requestFull();
      } else {
        const patch: PatchEntry[] = Array.isArray(a.patch) ? a.patch : [];
        this.rebase(() => {
          this.pending = this.pending.filter((x) => x !== p);
          applyPatch(this.base!, patch);
          if (typeof a.seq === "number") this.seq = a.seq;
        });
        if (p.undoable && patch.length && Array.isArray(a.before)) this.pushUndo(patch, a.before, p.label);
      }
      this.changed(true);
      this.flush();
      return;
    }
    const code: string = typeof a.code === "string" ? a.code : "error";
    const detail: Record<string, any> = isDict(a.detail) ? a.detail : {};
    if (code === "busy" || code === "rate_limited") {
      // not recorded: the same message again, after the wait the server asked for
      const wait = typeof detail.retry_after === "number" ? detail.retry_after : 0.25;
      this.waitUntil = Math.max(this.waitUntil, this.now() + Math.ceil(wait * 1000));
      this.changed();
      return;
    }
    if (code === "resync") {
      const reason = detail.reason;
      if (reason === "number_ahead" && typeof detail.next === "number") {
        this.nextN = detail.next;
        for (const x of this.pending) if (!x.inFlight) x.n = null;
        this.flush();
        return;
      }
      if (reason === "unknown_expect") {
        p.expect = Math.min(p.expect, this.seq);
        this.flush();
        return;
      }
      if (reason === "forgotten") {
        if (typeof detail.next === "number") this.nextN = Math.max(this.nextN, detail.next);
        if (repeatable(p.op)) { p.n = null; this.flush(); return; }
        this.removePending(p);
        this.addAttention("unknown", "unknown", detail, people, p);
        this.changed(true);
        return;
      }
    }
    if (code === "superseded") { this.onClose(4409, "superseded"); return; }
    this.removePending(p);
    if (code === "read_only") this.readonly = true;
    if (code === "not_live") this.addAttention("dropped", code, detail, people, p);
    else if (code === "stale_epoch") this.addAttention("dropped", "reset", detail, people, p);
    else this.addAttention(code === "conflict" ? "conflict" : "refused", code, detail, people, p);
    this.changed(true);
    this.flush();
  }

  private removePending(p: Pending) {
    if (!this.pending.includes(p)) return;
    this.rebase(() => { this.pending = this.pending.filter((x) => x !== p); });
  }

  private addAttention(kind: AttentionKind, code: string, detail: Record<string, any>, people: Record<string, Person>, p: Pending) {
    // a conflict can be decided: "use my value" sends the operation again against what the tab shows then;
    // an operation whose outcome is unknown can be done again once the person has looked
    const retry = kind === "conflict" || kind === "unknown" || (kind === "refused" && code === "resync") ? p.op : null;
    this.attention.push({ id: ++this.attnNo, kind, code, detail, people, label: p.label, retry });
  }

  /** Queued operations that can no longer be sent go to the person (a restart, leaving live mode). */
  private drop(kind: AttentionKind, code: string, list: Pending[]) {
    const gone = [...list];
    for (const p of gone) this.attention.push({ id: ++this.attnNo, kind, code, detail: {}, people: {}, label: p.label, retry: null });
    this.pending = this.pending.filter((p) => !gone.includes(p));
    for (const w of this.wire) if (w.kind === "op" && w.p && gone.includes(w.p)) w.p = null;
  }

  // ------------------------------------------------------------------ what the person decides
  /** "Use my value": send it again against what the tab shows now (the person has seen theirs). */
  useMine(id: number): boolean {
    const a = this.attention.find((x) => x.id === id);
    if (!a || !a.retry) return false;
    this.attention = this.attention.filter((x) => x !== a);
    const ok = this.submit(a.retry, { label: a.label });
    if (!ok) this.attention.push(a);
    this.changed();
    return ok;
  }
  /** "Keep theirs" / "OK": nothing is sent. */
  dismiss(id: number) {
    this.attention = this.attention.filter((x) => x.id !== id);
    this.changed();
  }

  // ------------------------------------------------------------------ undo
  private pushUndo(patch: PatchEntry[], before: PatchEntry[], label: OpLabel) {
    if (!this.epoch) return;
    this.undoStack.push({ patch, before, label, epoch: this.epoch });
    if (this.undoStack.length > 100) this.undoStack.shift();
  }

  /** Undo my last accepted operation, from the server's own inverse, where nothing changed since.
   *  null when there is nothing to undo; else whether something was sent and what was skipped (why). */
  undo(): { sent: boolean; skipped: Skipped[]; label: OpLabel } | null {
    while (this.undoStack.length && this.undoStack[this.undoStack.length - 1].epoch !== this.epoch) this.undoStack.pop();
    const u = this.undoStack.pop();
    if (!u || !this.base) return null;
    const plan = planUndo(this.base, u.patch, u.before, this.newId);
    let sent = false;
    if (plan.ops.length) {
      const op = plan.ops.length === 1 ? plan.ops[0] : { op: "tx", ops: plan.ops };
      sent = this.submit(op, { undoable: false, label: { ...u.label, kind: "undo" } });
    }
    this.changed();
    return { sent, skipped: plan.skipped, label: u.label };
  }

  /** Tests and diagnostics: the queued operations as the tab holds them. */
  debugPending(): { n: number | null; inFlight: boolean; op: any; expect: number; shown: boolean; predicted: string | null }[] {
    return this.pending.map((p) => ({ n: p.n, inFlight: p.inFlight, op: p.op, expect: p.expect, shown: !!p.local, predicted: p.predicted }));
  }
  debugWire(): number { return this.wire.length; }
}
