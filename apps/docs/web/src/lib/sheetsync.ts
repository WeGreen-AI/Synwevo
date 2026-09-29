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
 *    columns) is handed to the person, never resent automatically. A message first sent on the
 *    connection it is answered `forgotten` on was certainly never applied: it is renumbered and sent;
 *  - every frame the tab sends is answered exactly once, in order (an `ack`, or `full` for `sync`), so
 *    an answer belongs to the oldest frame on the wire — also the `n: null` answers to frames the
 *    server refused unread, and the late `stale_epoch` of a frame sent before a restart;
 *  - `busy` / `rate_limited`: the same message again after `retry_after` (the server did not record
 *    it, nor anything this connection sent after it); `resync`: `number_ahead` renumbers from `next`,
 *    `unknown_expect` sends it again against what the tab has seen, `expect_too_old` goes to the person;
 *  - an acknowledged change stays with the tab until `persisted` covers it: a server that comes back
 *    from an older draft (same epoch, a lower `seq` than the tab had seen) lost it — it is sent again,
 *    and everything queued is sent against what that server has (`expect` lowered to its `seq`), so a
 *    newer value written after the restart is a conflict, never silently overwritten.
 *
 *  Refusals and conflicts go to "needs you" (`attention`) with what the server said and whose value
 *  it was — for a conflict, every cell of the operation someone else changed since the person acted,
 *  not only the first the server met — and the choice "use my value" (sent again against what is
 *  there now) or "keep theirs". Undo is per person, from the server's own inverse, skipping what
 *  others changed since (`sheetundo.planUndo`); pressed while the change on top is still on its way,
 *  it undoes that change — taken off the queue when not sent yet, undone once answered otherwise.
 *
 *  Pure: no socket and no clock of its own. The page wires it to the room (`sheetroom.ts`), which
 *  passes frames in and sends what it hands out. */
import { applyOp, applyPatch, cellRef, copy, eq, isDict, own, Unpredictable, type Content, type PatchEntry } from "./sheetmodel.ts";
import { at, planUndo, type Skipped } from "./sheetundo.ts";

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
  /** the connection it was first sent on (a `forgotten` answer on that same one: never applied) */
  connNo: number | null;
  /** renumbered although it may have been applied (a forgotten client resending it) */
  uncertain: boolean;
  /** its effect in what the tab shows; null while not shown (predicted refusal, or not predictable) */
  local: { patch: PatchEntry[]; before: PatchEntry[] } | null;
  /** what the person saw of the objects it writes when they did it (the prediction's inverse then) */
  saw: PatchEntry[] | null;
  predicted: string | null;
  label: OpLabel;
  undoable: boolean;
  /** undo was pressed while it was on its way: undone once answered */
  undoRequested: boolean;
  /** an undo: the entry it undoes */
  undoOf: UndoEntry | null;
  /** its place in the queue (what the person did first comes first, also for edits sent again) */
  ord: number;
}

/** A frame on the wire, waiting for its answer. `p` is null once its operation was voided (restart). */
type Wire = { kind: "op"; p: Pending | null; n: number } | { kind: "sync" };

export type AttentionKind = "conflict" | "refused" | "unknown" | "dropped" | "overwritten";
/** A cell of an operation someone else changed after the person acted (reported with a conflict). */
export interface ChangedCell { sheet: string | null; ref: string | null; theirs: any; /** who changed it (a person id), when known */ by?: string | null }
export interface Attention {
  id: number;
  kind: AttentionKind;
  code: string;
  /** the server's detail; `also`: the other cells of the operation changed since (ChangedCell[]) */
  detail: Record<string, any>;
  people: Record<string, Person>;
  label: OpLabel;
  /** "use my value": what is sent again, when it makes sense */
  retry: any | null;
  /** what the tab had seen when the person was told (epoch and sequence number): "use my value" is
   *  sent against that — a cell someone changes after the person read this is a new conflict, never
   *  silently replaced (H2) */
  seen?: { epoch: string | null; seq: number };
  /** the place of the change it is about in this tab's queue: a cell a later change of mine wrote is
   *  left to that change ("use my value" here does not overwrite it — P4) */
  ord?: number;
}

export interface UndoEntry { patch: PatchEntry[]; before: PatchEntry[]; label: OpLabel; epoch: string; seq: number }
/** What an undo did: whether operations were sent, and what was left alone (and why). `cancelled`:
 *  the change never reached the room (taken off the queue, or refused) — nothing to say. */
export interface UndoResult { sent: boolean; skipped: Skipped[]; label: OpLabel; cancelled?: boolean }
export interface Recent { by: Person; until: number }
export interface Announcement { by: string | null; where: string; count: number }

/** A change of mine the server acknowledged but has not yet written to its draft. */
interface Acked { seq: number; op: any; label: OpLabel; undoable: boolean; ord: number; /** the queued change it was (its id) */ id: number }

/** What "changed since" of an attention entry is computed from — kept, because it is computed again
 *  when a cell it left to a later change of mine becomes its own to tell (that change was taken back
 *  before it was sent, refused, dropped or undone: R3a/R3b). `deferred`: the cells ("sid|row:col") left
 *  to later changes of mine when it was last computed. */
interface AlsoSource { saw: PatchEntry[]; ord: number; except: string | null; writers: Map<string, string>; deferred: string[] }

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
  /** my changes the room acknowledged but has not written to its draft yet (lost if its process dies
   *  now and this page is gone: closing the page asks) */
  unpersisted: number;
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

const CELL_OPS = new Set(["set", "clear", "style"]);

/** The sheet name, A1 reference and value an operation is about, in `content` as it is now. A group
 *  is named by the cell it writes (typing at the edge is [add rows, write the cell]). */
export function labelOf(content: Content | null, op: any): OpLabel {
  const kind = isDict(op) ? String(op.op) : "invalid";
  if (!content || !isDict(op)) return { kind, sheet: null, ref: null };
  if (kind === "tx" && Array.isArray(op.ops) && op.ops.length) {
    const cells = op.ops.filter((o: any) => isDict(o) && CELL_OPS.has(o.op));
    const main = cells.length === 1 ? cells[0] : cells.length ? cells[0] : op.ops[0];
    const first = labelOf(content, main);
    return { ...first, kind: op.ops.length > 1 ? "tx" : first.kind, value: cells.length === 1 ? first.value : cells.length ? null : first.value };
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

/** A cell by ids as the person reads it: sheet name and A1. */
function cellLabel(content: Content | null, sid: string, ck: string): { sheet: string | null; ref: string | null } {
  const sh = content ? own(content.sheets, sid) : null;
  if (!sh) return { sheet: null, ref: null };
  const i = ck.indexOf(":");
  const r = sh.rows.indexOf(ck.slice(0, i)), c = sh.cols.indexOf(ck.slice(i + 1));
  return { sheet: sh.name, ref: r >= 0 && c >= 0 ? cellRef(r, c) : null };
}

const valuePart = (cell: any): Record<string, any> | null => {
  if (!isDict(cell)) return null;
  const v: Record<string, any> = {};
  for (const k of ["v", "t"]) if (Object.hasOwn(cell, k)) v[k] = cell[k];
  return Object.keys(v).length ? v : null;
};

/** The cell values an operation (or group) writes: "sheet|row:col". */
function cellsOf(op: any): string[] {
  const ops: any[] = isDict(op) && op.op === "tx" && Array.isArray(op.ops) ? op.ops : [op];
  return ops.filter((o) => isDict(o) && (o.op === "set" || o.op === "clear")).map((o) => `${o.sheet}|${o.row}:${o.col}`);
}

/** `op` without the writes of cells `drop` says; null when nothing is left. */
function withoutCells(op: any, drop: (key: string) => boolean): any | null {
  const cell = (o: any) => isDict(o) && (o.op === "set" || o.op === "clear") && drop(`${o.sheet}|${o.row}:${o.col}`);
  if (isDict(op) && op.op === "tx" && Array.isArray(op.ops)) {
    const ops = op.ops.filter((o: any) => !cell(o));
    return ops.length === op.ops.length ? op : ops.length === 0 ? null : ops.length === 1 ? ops[0] : { ...op, ops };
  }
  return cell(op) ? null : op;
}

function loadOf(x: any): { id: string; seq: number } | null {
  return isDict(x) && typeof x.id === "string" && typeof x.seq === "number" ? { id: x.id, seq: x.seq } : null;
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
  private ordNo = 0;
  private attnNo = 0;
  private nextN = 1;
  private waitUntil = 0;
  private needFull = false;
  private connNo = 0;
  private listeners = new Set<(s: SyncSnapshot) => void>();
  private recent = new Map<string, Recent>();
  private dots = new Map<string, Set<string>>();
  /** the last sequence number at which another tab changed each sheet (undo never deletes that work) */
  private touched = new Map<string, number>();
  /** who last changed each cell, as far as this tab saw it happen (patches): names in "also changed" */
  private cellBy = new Map<string, string>();
  /** my changes in this epoch that write cell values and have left the queue still deciding them, by
   *  their place in the queue: applied by the room (`seq`: where; null: "already so"), or answered with a
   *  choice of their own about their cells (a conflict's "use my value / keep theirs", a fate unknown's
   *  "do it again": null). Together with what is still queued, the later changes of mine a cell is left to
   *  (P4). An applied one is taken back when undone; only those above the oldest change an answer can
   *  still be about are kept (`pruneApplied`). */
  private standing = new Map<number, { cells: string[]; seq: number | null }>();
  private alsoSrc = new Map<number, AlsoSource>();
  private prevOutside = new Map<string, any>();
  private undoStack: UndoEntry[] = [];
  private acked: Acked[] = [];
  /** The result of an undo pressed while my change was on its way, once it is known. */
  onUndone: ((r: UndoResult | null) => void) | null = null;
  private announceQueue: Announcement[] = [];
  private viewing: string | null = null;
  me: string | null = null;
  clientId: string | null = null;
  live = false;
  liveReason: any = null;
  stale = false;
  readonly = false;
  epoch: string | null = null;
  /** the load of the room this tab last joined (the room's `loaded.id`): a different one in the same
   *  epoch means the room was opened again from storage */
  boot: string | null = null;
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
  /** people by id (from hello, patches and refusals — the server's names): names in sentences */
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
      attention: [...this.attention],
      unpersisted: this.acked.filter((a) => a.seq > this.persisted).length,
      canUndo: this.undoStack.some((u) => u.epoch === this.epoch) || this.pending.some((p) => p.undoable && !p.undoRequested),
      closed: this.closed, limits: this.limits,
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
  wakeAt(): number | null {
    return this.waitUntil > this.now() && (this.needFull || this.pending.some((p) => !p.inFlight)) ? this.waitUntil : null;
  }
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
  submit(op: any, opts: { undoable?: boolean; label?: OpLabel; expect?: number; undoOf?: UndoEntry } = {}): boolean {
    if (!this.canWrite() || !this.epoch) return false;
    const p = this.queue(op, opts);
    this.pending.push(p);
    this.show(p);
    p.saw = p.local ? p.local.before : null;
    this.changed(true);
    this.flush();
    return true;
  }

  private queue(op: any, opts: { undoable?: boolean; label?: OpLabel; expect?: number; undoOf?: UndoEntry }): Pending {
    return { id: ++this.seqNo, op, expect: opts.expect ?? this.seq, epoch: this.epoch!, n: null, inFlight: false, connNo: null, uncertain: false,
      local: null, saw: null, predicted: null, label: opts.label ?? labelOf(this.base, op), undoable: opts.undoable ?? true,
      undoRequested: false, undoOf: opts.undoOf ?? null, ord: ++this.ordNo };
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
    if (this.needFull) this.requestFull();
    let lastInFlight = -1;
    this.pending.forEach((p, i) => { if (p.inFlight) lastInFlight = i; });
    for (let i = 0; i < this.pending.length; i++) {
      const p = this.pending[i];
      if (p.inFlight) continue;
      // a change refused for now (busy, rate limited) goes again only once nothing sent after it is
      // still on its way: the room refuses those too (it holds the tab's later messages until the
      // refused one is back), and they go again after it, in the order they were made (H1)
      if (i < lastInFlight) break;
      if (this.wire.length >= this.window) break;
      if (p.n === null) p.n = this.nextN++;
      if (p.connNo === null) p.connNo = this.connNo;
      p.inFlight = true;
      this.wire.push({ kind: "op", p, n: p.n });
      this.send({ type: "op", epoch: p.epoch, n: p.n, expect: p.expect, op: p.op });
    }
  }

  /** Ask for the whole state again (answered with `full`); later when the tab cannot ask now. */
  requestFull() {
    if (!this.connected || !this.greeted || this.waitUntil > this.now()) { this.needFull = true; return; }
    this.needFull = false;
    this.wire.push({ kind: "sync" });
    this.send({ type: "sync" });
  }

  // ------------------------------------------------------------------ frames
  onMeta(frame: any) {
    if (!isDict(frame)) return;
    switch (frame.type) {
      case "hello": this.onHello(frame); break;
      case "persisted":
        if (frame.epoch === this.epoch && typeof frame.seq === "number") {
          this.persisted = Math.max(this.persisted, frame.seq);
          this.acked = this.acked.filter((a) => a.seq > this.persisted);
          this.changed();
        }
        break;
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
    this.connNo++;
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
    this.take(h, h.resumed === true);
  }

  /** The room's whole state on (re)connecting: resolve what was sent and not answered from
   *  `outcomes`; after a server that came back from an older draft, send again what it lost. */
  private take(f: any, resumed: boolean) {
    const epoch: string = f.epoch;
    const seq: number = typeof f.seq === "number" ? f.seq : 0;
    const persisted = typeof f.persisted === "number" ? f.persisted : 0;
    // the room's load (a fresh id each time it is opened from storage), the seq it was loaded at, and
    // the loads of this epoch it remembers (a tab away across several finds its own among them)
    const loaded = loadOf(f.loaded);
    const loads: { id: string; seq: number }[] = (Array.isArray(f.loads) ? f.loads : []).map(loadOf).filter(Boolean);
    let from: number | null = null;
    if (this.epoch !== null && epoch === this.epoch) {
      if (loaded && this.boot !== null && loaded.id !== this.boot) {
        // opened again from storage since this tab last saw it (a process that died without its closing
        // write — perhaps more than once): what this tab had acknowledged above the lowest seq any later
        // load started from may be gone — also when others have filled those numbers again since (H4, P5)
        const i = loads.findIndex((l) => l.id === this.boot);
        const later = i >= 0 ? loads.slice(i + 1) : loads;
        from = Math.min(seq, loaded.seq, ...later.map((l) => l.seq));
      } else if (seq < this.seq) from = seq;
    }
    if (loaded) this.boot = loaded.id;
    let lost: Acked[] = [];
    if (this.epoch !== null && epoch !== this.epoch) {
      // restarted while away (a version put in, or a draft that could not be read): nothing of the
      // previous epoch applies — the queue and what was acknowledged but not yet kept (every entry
      // left: written ones were dropped as the room said so) go to the person
      for (const a of this.acked) this.lostNotice(a);
      this.drop("dropped", "reset", this.pending);
      this.undoStack = [];
      this.acked = [];
      this.touched.clear();
      this.cellBy.clear();
      this.standing.clear();
    } else if (from !== null) {
      // what the room had acknowledged after `from` is sent again below; what is queued, and any
      // "use my value" not answered yet, is sent against the history as it is now: a value written
      // after the restart is a conflict, not silently replaced (H10)
      lost = this.acked.filter((a) => a.seq > from!);
      this.acked = this.acked.filter((a) => a.seq <= from!);
      this.undoStack = this.undoStack.filter((u) => u.seq <= from!);
      // what the room applied of mine after `from` is gone with that history: queued again below, it
      // counts as queued (and as applied once answered again)
      for (const [o, r] of this.standing) if (r.seq !== null && r.seq > from!) this.standing.delete(o);
      for (const p of this.pending) p.expect = Math.min(p.expect, from);
      for (const a of this.attention) if (a.seen) a.seen.seq = Math.min(a.seen.seq, from);
    }
    const sameEpoch = this.epoch === epoch;
    this.epoch = epoch;
    const outcomes: Record<string, any> = isDict(f.outcomes) ? f.outcomes : {};
    const next: number = typeof f.next === "number" ? f.next : 1;
    const applied: [Pending, number][] = [], refused: [Pending, string][] = [], unknown: Pending[] = [];
    for (const p of this.pending) {
      p.inFlight = false;
      if (p.n === null) continue;
      const o = outcomes[String(p.n)];
      // applied: the seq it was answered with (a room before that answered `true`: at most this hello's)
      if (o === true || typeof o === "number") applied.push([p, typeof o === "number" ? o : seq]);
      else if (typeof o === "string" && o !== "busy" && o !== "rate_limited") refused.push([p, o]);
      else if (!resumed) {
        if (repeatable(p.op)) { p.n = null; p.uncertain = true; }  // safe to send again: "already so" or a conflict
        else unknown.push(p);
      } else p.n = null;                                            // never received: renumbered from `next`
    }
    const gone = new Set<Pending>([...applied.map(([p]) => p), ...refused.map(([p]) => p), ...unknown]);
    // what the room held as this tab last knew it, with my own changes it applied meanwhile: any sheet
    // that differs now was changed by someone else while this tab was away (undo never deletes that)
    const before = sameEpoch ? this.serverContent(applied.map(([p]) => p)) : null;
    this.pending = this.pending.filter((p) => !gone.has(p));
    this.base = copy(f.content);
    this.seq = seq;
    this.persisted = persisted;
    this.acked = this.acked.filter((a) => a.seq > this.persisted);
    this.nextN = Math.max(next, 1);
    this.waitUntil = 0;
    if (before) this.noteChangedSheets(before, this.base!, seq);
    // what the server had acknowledged and lost goes first: it came before everything queued
    const againExpect = from !== null ? from : seq;
    const again = lost.map((a) => this.queue(a.op, { label: a.label, undoable: a.undoable, expect: againExpect }));
    again.forEach((p, i) => { p.ord = lost[i].ord; });                // their own places: before everything still queued
    this.pending = [...again, ...this.pending];
    for (const p of this.pending) this.show(p);
    for (const p of again) p.saw = p.local ? p.local.before : null;
    const told = refused.filter(([p]) => !p.undoRequested)
      .map(([p, code]) => ({ p, code, kind: (code === "conflict" ? "conflict" : "refused") as AttentionKind }));
    // Every answer this hello resolved decides its cells before any of them is told (P4): a later change
    // of mine the room applied meanwhile, or refused with a choice of its own, is that change's to decide
    // also for an earlier answer resolved in this same hello, and an undo resolved here takes the change it
    // undid back. Told first, a sentence listed such a cell as "would be overwritten" and "use my value"
    // then left it alone (third review's follow-up, X3/X4).
    for (const [p] of applied) if (p.undoOf) this.forgetApplied(p.undoOf.seq);
    for (const [p, at] of applied) this.stands(p, at);
    for (const { p, kind, code } of told) this.offers(p, this.offerOf(kind, code, p));
    for (const p of unknown) this.offers(p, this.offerOf("unknown", "unknown", p, p.undoRequested ? null : undefined));
    // refused and unknown ones are told against the room as it is now (who changed what since)
    for (const [p] of refused) if (p.undoRequested) this.onUndone?.({ sent: false, skipped: [], label: p.label, cancelled: true });
    for (const { p, kind, code } of told) this.addAttention(kind, code, p.undoOf ? { undo: true } : {}, {}, p);
    for (const p of unknown) this.addAttention("unknown", "unknown", {}, {}, p, p.undoRequested ? null : undefined);
    for (const [p, at] of applied) {
      if (!p.local || !p.local.patch.length) continue;
      // resolved from `outcomes`: acknowledged like an answer — kept until a draft write covers it (P1)
      if (at > this.persisted) this.acked.push({ seq: at, op: p.op, label: p.label, undoable: p.undoable, ord: p.ord, id: p.id });
      if (p.undoRequested) this.undoFrom({ patch: p.local.patch, before: p.local.before, label: p.label, epoch, seq: at });
      else if (p.undoable) this.pushUndo(p.local.patch, p.local.before, p.label, at);
    }
    this.pruneApplied();
    this.refreshDeferred();
    this.changed(true);
    this.flush();
  }

  /** An acknowledged change of mine the room no longer has and this tab cannot send again (the room
   *  started a new epoch while it was not yet written): the person is told. */
  private lostNotice(a: Acked) {
    this.attention.push({ id: ++this.attnNo, kind: "dropped", code: "lost", detail: {}, people: {}, label: a.label, retry: null });
  }

  /** The room's content as this tab last knew it: what it shows without its own queued predictions,
   *  plus `mine` (changes of mine the room applied meanwhile). */
  private serverContent(mine: Pending[] = []): Content | null {
    if (!this.base) return null;
    const c = copy(this.base);
    for (let i = this.pending.length - 1; i >= 0; i--) {
      const l = this.pending[i].local;
      if (l) applyPatch(c, l.before);
    }
    for (const p of mine) if (p.local && this.pending.includes(p)) applyPatch(c, p.local.patch);
    return c;
  }

  /** Sheets that differ between two contents were changed by someone else at `seq` (for undo). */
  private noteChangedSheets(before: Content, now: Content, seq: number) {
    const mark = (sid: string | null | undefined) => { if (sid) this.touched.set(sid, Math.max(this.touched.get(sid) ?? 0, seq)); };
    for (const sid of new Set([...Object.keys(before.sheets), ...Object.keys(now.sheets)])) {
      if (!eq(own(before.sheets, sid) ?? null, own(now.sheets, sid) ?? null)) mark(sid);
    }
    for (const part of ["merges", "fields"] as const) {
      for (const k of new Set([...Object.keys(before[part]), ...Object.keys(now[part])])) {
        const a = own(before[part], k), b = own(now[part], k);
        if (!eq(a ?? null, b ?? null)) { mark(a?.sheet); mark(b?.sheet); }
      }
    }
  }

  private leaveLive(reason: any) {
    this.live = false;
    this.liveReason = reason;
    if (this.pending.length) this.drop("dropped", "not_live", this.pending);
    this.base = null;
    this.undoStack = [];
    this.acked = [];
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
    const before = this.serverContent();
    this.base = copy(f.content);
    if (before) this.noteChangedSheets(before, this.base!, typeof f.seq === "number" ? f.seq : this.seq);
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
    this.acked = [];
    this.touched.clear();
    this.cellBy.clear();
    this.standing.clear();
    this.epoch = f.epoch;
    const loaded = loadOf(f.loaded);
    if (loaded) this.boot = loaded.id;
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
    const seq = typeof f.seq === "number" ? f.seq : this.seq;
    this.rebase(() => {
      // which sheets another tab (another person, an outside version, or my other tab) just worked on
      for (const e of f.patch as PatchEntry[]) {
        let sid: string | null = null;
        if (e.p[0] === "sheets" && e.p.length >= 2) sid = e.p[1];
        else if ((e.p[0] === "merges" || e.p[0] === "fields") && e.p.length === 2) sid = (isDict(e.v) ? e.v.sheet : null) ?? own(own(this.base![e.p[0] as "merges" | "fields"], e.p[1]), "sheet") ?? null;
        if (sid) this.touched.set(sid, Math.max(this.touched.get(sid) ?? 0, seq));
        if (by.id && e.p[0] === "sheets" && e.p[2] === "cells" && e.p.length === 4) this.cellBy.set(`${e.p[1]}|${e.p[3]}`, by.id);
      }
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
      this.seq = seq;
    });
    if (!by.id || by.id !== this.me) this.markRecent(f.patch, by);       // my own other tab is not "someone else"
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
          const l = cellLabel(this.base, e.p[1], e.p[3]);
          if (l.ref) first = l.sheet && Object.keys(this.base!.sheets).length > 1 ? `${l.sheet}!${l.ref}` : l.ref;
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
      // a `sync` refused for now (rate limited): asked again after the wait
      const wait = isDict(a.detail) && typeof a.detail.retry_after === "number" ? a.detail.retry_after : 1;
      this.waitUntil = Math.max(this.waitUntil, this.now() + Math.ceil(wait * 1000));
      this.needFull = true;
      this.changed();
      return;
    }
    const p = w.p;
    if (!p || !this.pending.includes(p)) return;          // a frame of a voided epoch: nothing to do
    const people = this.notePeople(a.people);
    p.inFlight = false;
    if (a.ok) {
      if (p.undoOf) this.forgetApplied(p.undoOf.seq);        // the change it undid no longer decides its cells
      if (a.duplicate && !Array.isArray(a.patch)) {
        // applied before, its answer remembered only in short: keep what the tab shows and take the whole
        this.pending = this.pending.filter((x) => x !== p);
        this.noteApplied(p, null);
        if (typeof a.seq === "number" && a.seq > this.persisted) this.acked.push({ seq: a.seq, op: p.op, label: p.label, undoable: p.undoable, ord: p.ord, id: p.id });
        this.requestFull();
        if (p.undoRequested) this.onUndone?.(null);
      } else {
        const patch: PatchEntry[] = Array.isArray(a.patch) ? a.patch : [];
        this.rebase(() => {
          this.pending = this.pending.filter((x) => x !== p);
          applyPatch(this.base!, patch);
          if (typeof a.seq === "number") this.seq = a.seq;
        });
        this.noteApplied(p, patch.length ? this.seq : null);
        if (patch.length) this.acked.push({ seq: this.seq, op: p.op, label: p.label, undoable: p.undoable, ord: p.ord, id: p.id });
        const entry = patch.length && Array.isArray(a.before) ? { patch, before: a.before, label: p.label, epoch: this.epoch!, seq: this.seq } : null;
        if (p.undoRequested) {
          if (entry) this.undoFrom(entry);
          else this.onUndone?.(null);
        } else if (p.undoable && entry) {
          this.undoStack.push(entry);
          this.trimUndo();
        }
      }
      this.refreshDeferred();
      this.changed(true);
      this.flush();
      return;
    }
    const code: string = typeof a.code === "string" ? a.code : "error";
    const detail: Record<string, any> = isDict(a.detail) ? a.detail : {};
    if (code === "busy" || code === "rate_limited") {
      // not recorded (and neither is anything this connection sent after it): the same message
      // again, after the wait the server asked for
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
        // first sent on this very connection: the room kept no record of this tab since it joined,
        // so nothing it sent here was applied — certainly not this (H8)
        if (p.connNo === this.connNo && !p.uncertain) { p.n = null; this.flush(); return; }
        if (repeatable(p.op)) { p.n = null; p.uncertain = true; this.flush(); return; }
        this.removePending(p);
        this.addAttention("unknown", "unknown", detail, people, p, p.undoRequested ? null : undefined);
        this.refreshDeferred();
        this.changed(true);
        return;
      }
    }
    if (code === "superseded") { this.onClose(4409, "superseded"); return; }
    this.removePending(p);
    if (code === "read_only") this.readonly = true;
    // a refused undo is set aside, not put back on top: the person is told, and the next undo reaches
    // the change before it (P8; "use my value" / "do it again" where the refusal offers it)
    if (p.undoOf) detail.undo = true;
    if (p.undoRequested) {
      // refused: it never happened, which is what the person asked for
      this.onUndone?.({ sent: false, skipped: [], label: p.label, cancelled: true });
    } else if (code === "not_live") this.addAttention("dropped", code, detail, people, p);
    else if (code === "stale_epoch") this.addAttention("dropped", "reset", detail, people, p);
    else this.addAttention(code === "conflict" ? "conflict" : "refused", code, detail, people, p);
    // a later change of mine that did not happen leaves the cells earlier answers left to it to them (R3b)
    this.refreshDeferred();
    this.changed(true);
    this.flush();
  }

  private removePending(p: Pending) {
    if (!this.pending.includes(p)) return;
    this.rebase(() => { this.pending = this.pending.filter((x) => x !== p); });
  }

  // ------------------------------------------------------------------ later changes of mine (P4)
  /** A change of mine the room applied: while it stands, the cells it wrote are its own to decide for
   *  every earlier change of mine (P4). Undo operations are not decisions of their own, and a change
   *  undo was pressed on is being taken back. */
  private noteApplied(p: Pending, seq: number | null) {
    this.stands(p, seq);
    this.pruneApplied();
  }

  /** `noteApplied` without pruning (a hello resolves several answers before any is told). */
  private stands(p: Pending, seq: number | null) {
    if (p.undoOf || p.undoRequested) return;
    const cells = cellsOf(p.op);
    if (cells.length) this.standing.set(p.ord, { cells, seq });
  }

  /** What "use my value" / "do it again" offers for a change answered `kind` / `code` (`retry` given:
   *  that); null when there is nothing to offer. */
  private offerOf(kind: AttentionKind, code: string, p: Pending, retry?: any): any {
    return retry !== undefined ? retry : kind === "conflict" || kind === "unknown" || (kind === "refused" && code === "resync") ? p.op : null;
  }

  /** A change answered with a choice of its own about its cells ("use my value / keep theirs", "do it
   *  again"): whatever the person chooses there, earlier changes of mine leave those cells to it (P4). */
  private offers(p: Pending, offer: any) {
    if (!offer || p.undoOf) return;
    const cells = cellsOf(offer);
    if (cells.length) this.standing.set(p.ord, { cells, seq: null });
  }

  /** The change of mine applied at `seq` was undone: it decides nothing any more. */
  private forgetApplied(seq: number) {
    for (const [o, r] of this.standing) if (r.seq === seq) this.standing.delete(o);
  }

  /** Only changes above the oldest one an answer can still be about are needed: queued, acknowledged
   *  (it may be sent again after a crash) or waiting in "needs you". */
  private pruneApplied() {
    const live = new Set(this.attention.map((a) => a.id));
    for (const id of this.alsoSrc.keys()) if (!live.has(id)) this.alsoSrc.delete(id);
    let floor = Infinity;
    for (const x of this.pending) floor = Math.min(floor, x.ord);
    for (const a of this.acked) floor = Math.min(floor, a.ord);
    for (const a of this.attention) if (a.ord !== undefined) floor = Math.min(floor, a.ord);
    for (const o of this.standing.keys()) if (o <= floor) this.standing.delete(o);
  }

  /** The cells ("sid|row:col") written by changes of mine after place `ord` in the queue that still
   *  stand: queued (not taken back — an undo is not a decision of its own), applied and not undone, or
   *  answered with a choice of their own about those cells (a conflict: "use my value / keep theirs" —
   *  what the person chooses there is the cell's answer, P4). A change taken off the queue by undo before
   *  it was sent, refused for another reason (nothing to choose) or dropped decides nothing: the cell is
   *  the earlier change's to tell and to send again (R3a/R3b). */
  private laterCells(ord: number): Set<string> {
    const out = new Set<string>();
    for (const x of this.pending) if (x.ord > ord && !x.undoOf && !x.undoRequested) for (const k of cellsOf(x.op)) out.add(k);
    for (const [o, r] of this.standing) if (o > ord) for (const k of r.cells) out.add(k);
    return out;
  }

  /** Answers that left a cell to a later change of mine that no longer stands: told again, with every
   *  cell changed since as it is now. "Use my value" is still sent against what the tab had seen when the
   *  person was first told (`seen` stays): the sentence is not made again for the cell the answer is about
   *  (the room named it), nor for a cell whose write was "already so" when the person acted — a newer
   *  value of either comes back as a new conflict naming it, never replaced untold (H2; moving `seen` to
   *  now here replaced another person's newer value of the conflict's own cell — third review's
   *  follow-up, X1/X2). Whether anything was told again. */
  private refreshDeferred(only?: Attention): boolean {
    let told = false;
    this.attention = this.attention.map((a) => {
      if (only && a !== only) return a;
      const src = this.alsoSrc.get(a.id);
      // (an answer from an epoch the room has left is about ids and values of that epoch: not retold)
      if (!src || !src.deferred.length || a.ord === undefined || a.seen?.epoch !== this.epoch) return a;
      const later = this.laterCells(a.ord);
      if (src.deferred.every((k) => later.has(k))) return a;
      told = true;
      return this.retold(a, src);
    });
    return told;
  }

  private retold(a: Attention, src: AlsoSource): Attention {
    const { also, deferred } = this.alsoOf(src);
    src.deferred = deferred;
    const detail: Record<string, any> = { ...a.detail };
    if (also.length) detail.also = also;
    else delete detail.also;
    let people = a.people;
    for (const c of also) if (c.by && !people[c.by]) { const who = this.people.get(c.by); if (who) people = { ...people, [c.by]: who }; }
    return { ...a, detail, people };
  }

  /** The other cells of a change someone else changed after the person acted (`src.saw`: what the
   *  person saw of them), with what they hold in the room now and who changed them (the room's
   *  `writers`, else the patches this tab saw). Cells my own *earlier* queued edits write are left out:
   *  what I saw there was mine. A cell a later change of mine that still stands writes is that change's
   *  to decide (it has its own answer — P4): not listed, `deferred`, and "use my value" leaves it alone
   *  (useMine); a later change that was taken back, refused or dropped decides nothing (R3a/R3b). */
  private alsoOf(src: AlsoSource): { also: ChangedCell[]; deferred: string[] } {
    if (!this.base) return { also: [], deferred: [] };
    const earlier = new Set<string>();
    for (const x of this.pending) if (x.ord < src.ord && x.local) for (const e of x.local.patch) earlier.add(e.p.join("\u0001"));
    const later = this.laterCells(src.ord);
    const deferred: string[] = [];
    const out: (ChangedCell & { at: [number, number, number] })[] = [];
    for (const e of src.saw) {
      if (e.p[0] !== "sheets" || e.p[2] !== "cells" || e.p.length !== 4) continue;
      const key = e.p.join("\u0001");
      if (key === src.except || earlier.has(key)) continue;
      const ck = `${e.p[1]}|${e.p[3]}`;
      if (later.has(ck)) { deferred.push(ck); continue; }
      const saw = Object.hasOwn(e, "v") ? e.v : null;
      const now = this.roomValue(e.p, null);
      if (eq(valuePart(saw), valuePart(now))) continue;
      const by = src.writers.get(`v/${e.p[1]}/${e.p[3]}`) ?? this.cellBy.get(ck) ?? null;
      out.push({ ...cellLabel(this.base, e.p[1], e.p[3]), theirs: valuePart(now), by, at: this.position(e.p[1], e.p[3]) });
    }
    // in reading order (sheet, row, column), not the inverse patch's order
    out.sort((x, y) => x.at[0] - y.at[0] || x.at[1] - y.at[1] || x.at[2] - y.at[2]);
    return { also: out.map(({ at: _, ...c }) => c), deferred };
  }

  private position(sid: string, ck: string): [number, number, number] {
    const sh = own(this.base!.sheets, sid);
    const [rid, cid] = ck.split(":");
    return [this.base!.order.indexOf(sid), sh ? sh.rows.indexOf(rid) : -1, sh ? sh.cols.indexOf(cid) : -1];
  }

  /** A path's value in the room's content: the first of my queued edits that writes it saw it so;
   *  else what the tab shows. */
  private roomValue(path: string[], skip: Pending | null): any {
    for (const x of this.pending) {
      if (x === skip || !x.local) continue;
      const b = x.local.before.find((e) => e.p.length === path.length && e.p.every((k, i) => k === path[i]));
      if (b) return Object.hasOwn(b, "v") ? b.v : null;
    }
    return at(this.base!, path)[0] ?? null;
  }

  /** "Needs you": what happened, whose value it was, every other cell changed since, and — where it
   *  makes sense — what "use my value" / "do it again" sends. `retry` null: nothing to offer. */
  private addAttention(kind: AttentionKind, code: string, detail: Record<string, any>, people: Record<string, Person>, p: Pending, retry?: any) {
    let label = p.label;
    let except: string | null = null;
    const key = typeof detail.key === "string" ? detail.key : null;
    const m = key ? /^[vs]\/([^/]+)\/([^/]+?)(?:\/[^/]+)?$/.exec(key) : null;
    if (m) {
      // the conflict is about this cell: name it, and the value I gave it (H6)
      const [, sid, ck] = m;
      except = ["sheets", sid, "cells", ck].join("\u0001");
      const ops: any[] = isDict(p.op) && p.op.op === "tx" && Array.isArray(p.op.ops) ? p.op.ops : [p.op];
      const mine = ops.find((o) => isDict(o) && o.sheet === sid && `${o.row}:${o.col}` === ck);
      label = { ...label, ...cellLabel(this.base, sid, ck), value: mine && mine.op === "set" && mine.v != null ? String(mine.v) : null };
    }
    const offer = this.offerOf(kind, code, p, retry);
    // every cell someone changed since, with its value and who: for anything that can be sent again
    // (a conflict, a message whose fate is unknown, one refused because the room forgot what it saw — P2)
    let src: AlsoSource | null = null;
    let also: ChangedCell[] = [];
    if ((kind === "conflict" || kind === "unknown" || offer) && p.saw) {
      const writers = new Map<string, string>();
      for (const w of Array.isArray(detail.writers) ? detail.writers : []) if (isDict(w) && typeof w.key === "string" && typeof w.by === "string") writers.set(w.key, w.by);
      src = { saw: p.saw, ord: p.ord, except, writers, deferred: [] };
      const got = this.alsoOf(src);
      also = got.also;
      src.deferred = got.deferred;
    }
    for (const c of also) if (c.by && !people[c.by]) { const who = this.people.get(c.by); if (who) people = { ...people, [c.by]: who }; }
    const told: Record<string, any> = { ...detail };
    delete told.writers;
    if (also.length) told.also = also;
    if (typeof told.by === "string" && told.by === this.me) told.self = true;       // my own other tab (item 10)
    const id = ++this.attnNo;
    if (src && src.deferred.length) this.alsoSrc.set(id, src);
    this.offers(p, offer);
    this.attention.push({ id, kind, code, detail: told, people, label, retry: offer,
      seen: { epoch: this.epoch, seq: this.seq }, ord: p.ord });
  }

  /** Queued operations that can no longer be sent go to the person (a restart, leaving live mode). */
  private drop(kind: AttentionKind, code: string, list: Pending[]) {
    const gone = [...list];
    for (const p of gone) {
      if (p.undoRequested) { this.onUndone?.({ sent: false, skipped: [], label: p.label, cancelled: true }); continue; }
      this.attention.push({ id: ++this.attnNo, kind, code, detail: {}, people: {}, label: p.label, retry: null });
    }
    this.pending = this.pending.filter((p) => !gone.includes(p));
    for (const w of this.wire) if (w.kind === "op" && w.p && gone.includes(w.p)) w.p = null;
  }

  // ------------------------------------------------------------------ what the person decides
  /** "Use my value" / "do it again": send it again against what the tab had seen when the person was
   *  told — they were shown theirs and every other cell of it someone had changed; a cell changed
   *  after that comes back as a new conflict. */
  useMine(id: number): boolean {
    const a = this.attention.find((x) => x.id === id);
    if (!a || !a.retry) return false;
    // a cell the sentence left to a later change of mine that no longer stands was not told: told now
    // (every place that takes such a change back already does this; never sent without being told)
    if (this.refreshDeferred(a)) { this.changed(); return false; }
    // the cells a later change of mine that still stands writes are left to it (and to its own answer)
    const later = a.ord !== undefined ? this.laterCells(a.ord) : null;
    const retry = later ? withoutCells(a.retry, (k) => later.has(k)) : a.retry;
    const src = this.alsoSrc.get(a.id);
    this.attention = this.attention.filter((x) => x !== a);
    this.alsoSrc.delete(a.id);
    if (retry === null) { this.pruneApplied(); this.changed(); return true; }
    const expect = a.seen && a.seen.epoch === this.epoch ? Math.min(a.seen.seq, this.seq) : this.seq;
    const ok = this.submit(retry, { label: a.label, expect });
    if (!ok) {
      this.attention.push(a);
      if (src) this.alsoSrc.set(a.id, src);
    }
    this.pruneApplied();
    this.changed();
    return ok;
  }
  /** "Keep theirs" / "OK": nothing is sent. */
  dismiss(id: number) {
    this.attention = this.attention.filter((x) => x.id !== id);
    this.alsoSrc.delete(id);
    this.pruneApplied();
    this.changed();
  }

  // ------------------------------------------------------------------ undo
  private pushUndo(patch: PatchEntry[], before: PatchEntry[], label: OpLabel, seq: number) {
    if (!this.epoch) return;
    this.undoStack.push({ patch, before, label, epoch: this.epoch, seq });
    this.trimUndo();
  }
  private trimUndo() { if (this.undoStack.length > 100) this.undoStack.shift(); }

  /** Undo my last change, from the server's own inverse, where nothing changed since. The change on
   *  top when it is pressed is the one undone: taken off the queue when it has not been sent yet;
   *  "deferred" when it is on its way — undone once answered (the result then goes to `onUndone`).
   *  null when there is nothing to undo; else whether something was sent and what was skipped (why). */
  undo(): UndoResult | "deferred" | null {
    for (let i = this.pending.length - 1; i >= 0; i--) {
      const p = this.pending[i];
      if (!p.undoable || p.undoRequested) continue;
      if (p.n === null && !p.inFlight && !p.uncertain) {
        this.removePending(p);                         // never reached the room: gone from the queue, that is all
        this.refreshDeferred();                        // ... and decides no cell an earlier answer left to it (R3a)
        this.changed(true);
        return { sent: false, skipped: [], label: p.label, cancelled: true };
      }
      p.undoRequested = true;
      this.changed();
      return "deferred";
    }
    while (this.undoStack.length && this.undoStack[this.undoStack.length - 1].epoch !== this.epoch) this.undoStack.pop();
    const u = this.undoStack.pop();
    if (!u || !this.base) return null;
    return this.undoNow(u);
  }

  /** A change answered after undo was pressed on it: undone now, and the page told. */
  private undoFrom(u: UndoEntry) {
    const r = this.undoNow(u);
    this.onUndone?.(r);
  }

  private undoNow(u: UndoEntry): UndoResult {
    const plan = planUndo(this.base!, u.patch, u.before, this.newId,
      { since: u.seq, touchedSince: (sid, seq) => (this.touched.get(sid) ?? 0) > seq });
    let sent = false;
    if (plan.ops.length) {
      const op = plan.ops.length === 1 ? plan.ops[0] : { op: "tx", ops: plan.ops };
      sent = this.submit(op, { undoable: false, label: { ...u.label, kind: "undo" }, undoOf: u });
    }
    if (!plan.ops.length && u.epoch === this.epoch) {
      // nothing to send (others changed all of it since): it no longer decides any cell of mine
      this.forgetApplied(u.seq);
      this.refreshDeferred();
    }
    this.changed();
    return { sent, skipped: plan.skipped, label: u.label };
  }

  /** Tests and diagnostics: the queued operations as the tab holds them. */
  debugPending(): { id: number; n: number | null; inFlight: boolean; op: any; expect: number; shown: boolean; predicted: string | null; undoRequested: boolean }[] {
    return this.pending.map((p) => ({ id: p.id, n: p.n, inFlight: p.inFlight, op: p.op, expect: p.expect, shown: !!p.local, predicted: p.predicted, undoRequested: p.undoRequested }));
  }
  debugWire(): number { return this.wire.length; }
}
