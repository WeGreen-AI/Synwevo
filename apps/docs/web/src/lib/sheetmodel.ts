/** The live table's shared content and its operations, in the browser (docs/platform/docs-sheet-live.md
 *  §2 and §9, batch B1 step ③).
 *
 *  The server owns the state (`apps/docs/synwevo_docs/collab_sheet.py`); a tab mirrors its `content`
 *  and shows its own edits before the server has answered. For that it applies each operation here
 *  with the server's own semantics — the same checks in the same order, the same refusal codes and
 *  details, the same patch and inverse — so what a tab shows is what the server will do, and when it
 *  is not (someone else got there first) the server's answer replaces it. The two implementations are
 *  held together by a corpus generated from the Python module (`apps/docs/tests/sheet_live_corpus.py`
 *  → `tests/fixtures/sheet_live_corpus.json`): operations, states and expected results, replayed by
 *  `tests/sheetmodel.test.ts`. Change one side and that test tells you.
 *
 *  What a tab cannot know stays the server's: whether someone else wrote an object after what the
 *  sender saw (first writer wins: `conflict`), the room's de-duplication and numbering, its size
 *  bounds in bytes. Ids of new rows, columns, merged ranges and sheets are proposed by the tab (the
 *  server takes a proposed id as it is); an operation that would need the server to make one up is
 *  `Unpredictable` here and waits for the server's answer.
 *
 *  Known differences, each one only in what the tab predicts (the server's answer decides):
 *  JavaScript numbers cannot tell `1` from `1.0`; worksheet names are compared ignoring case with
 *  upper-then-lower case mapping, not Python's full case folding. */

// ------------------------------------------------------------------------------------ shapes
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Kind = "cell" | "region" | "repeat";
export interface Cell { v?: string | number | boolean | null; t?: string | null; s?: Record<string, any> | null }
export interface SheetC {
  name: string | null; gone: boolean; keys: string[]; nul: string[];
  rows: string[]; cols: string[]; nr: number; nc: number;
  rh: Record<string, any>; cw: Record<string, any>;
  cells: Record<string, Cell>; merges: string[]; fields: string[];
}
export interface MergeC { sheet: string; at: string[] }
export interface FieldC { sheet: string; kind: Kind; at: string[]; raw: Record<string, any> }
export interface Content {
  keys: string[]; order: string[];
  sheets: Record<string, SheetC>; merges: Record<string, MergeC>; fields: Record<string, FieldC>;
}
export interface PatchEntry { p: string[]; v?: any; d?: 1; push?: any[]; trim?: number }
export type Accepted = { ok: true; patch: PatchEntry[]; before: PatchEntry[] };
export type Refused = { ok: false; code: string; detail: Record<string, any> };
export type Result = Accepted | Refused;
export type Rect = [number, number, number, number];

// ------------------------------------------------------------------------------------ limits (collab_sheet.py)
export const MAX_TX = 5000;
export const MAX_VALUE = 32767;
export const MAX_FORMULA = 8192;
export const MAX_APPEND = 5000;
export const MAX_ROWS = 1_048_576;
export const MAX_COLS = 16_384;
export const MAX_SHEETS = 100;
export const MAX_GRID_IDS = 200_000;
export const MAX_LIVE_CELLS = 200_000;
export const MAX_MERGE_CELLS = 100_000;
export const MAX_SHEET_NAME = 31;
export const MAX_FIELD_NAME = 100;
export const MAX_NUMBER_FORMAT = 255;
export const ROW_HEIGHT: [number, number] = [1, 1000];
export const COL_WIDTH: [number, number] = [1, 2000];
export const ECHO_MAX = 64;
export const STYLE_KEYS = ["b", "i", "u", "sz", "color", "bg", "ha", "va", "wrap", "bd", "nf"] as const;
export const SHEET_KEYS = ["name", "cols", "rows", "cells", "merges", "fields"] as const;
export const FIELD_KINDS: readonly Kind[] = ["cell", "region", "repeat"];
const POSITIONAL: Record<Kind, string[]> = { cell: ["ref"], region: ["ref"], repeat: ["row", "c1", "c2"] };
const FIELD_ATTRS = ["name", "required", "empty_policy"];
const PLACE_KEYS: Record<Kind, string[]> = { cell: ["row", "col"], region: ["range"], repeat: ["row", "c1", "c2"] };
export const RESERVED_IDS = new Set(["__proto__", "constructor", "prototype", "hasOwnProperty", "isPrototypeOf",
  "propertyIsEnumerable", "toLocaleString", "toString", "valueOf", "__defineGetter__",
  "__defineSetter__", "__lookupGetter__", "__lookupSetter__"]);
const ID = /^[0-9a-z]{8}$/;
const BAD_TITLE = /[\\/*?:[\]]/;
const BAD_TITLE_G = /[\\/*?:[\]]/g;
// what the workbook writer refuses in any text: C0 controls but tab / LF / CR, lone surrogates, U+FFFE/U+FFFF
const BAD_TEXT = /[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff￾￿]/u;
const FIELD_ID = /^[A-Za-z_][A-Za-z0-9_-]{0,99}$/;
const COLOUR = /^#?[0-9a-fA-F]{6}$/;
const EMPTY_POLICIES = ["remove_row", "blank_row", "placeholder", "remove_table", "error"];
const HA = ["left", "center", "right", "justify", "fill", "distributed", "centerContinuous", "general"];
const VA = ["top", "center", "bottom", "justify", "distributed"];

/** An operation that needs an id only the server can make up (the tab did not propose one). */
export class Unpredictable extends Error {
  constructor() { super("unpredictable"); this.name = "Unpredictable"; }
}

class RefusedErr extends Error {
  code: string;
  detail: Record<string, any>;
  constructor(code: string, detail: Record<string, any> = {}) { super(code); this.code = code; this.detail = detail; }
}
const refuse = (code: string, detail: Record<string, any> = {}): never => { throw new RefusedErr(code, detail); };

// ------------------------------------------------------------------------------------ JSON helpers
export const own = (o: any, k: string): any => (o !== null && typeof o === "object" && Object.hasOwn(o, k) ? o[k] : undefined);
export const has = (o: any, k: string): boolean => o !== null && typeof o === "object" && Object.hasOwn(o, k);
export const isDict = (x: any): x is Record<string, any> => x !== null && typeof x === "object" && !Array.isArray(x);
/** A key set on an object as its own property — also "__proto__", which assignment would not. */
export function setKey(o: Record<string, any>, k: string, v: any): void {
  if (k === "__proto__") Object.defineProperty(o, k, { value: v, writable: true, enumerable: true, configurable: true });
  else o[k] = v;
}

export function copy<T>(x: T): T {
  if (Array.isArray(x)) return x.map(copy) as any;
  if (x !== null && typeof x === "object") {
    const out: Record<string, any> = {};
    for (const k of Object.keys(x)) setKey(out, k, copy((x as any)[k]));
    return out as T;
  }
  return x;
}

/** JSON equality that tells true from 1 (as the server's does; 1 and 1.0 are one number here). */
export function eq(a: any, b: any): boolean {
  if (a === b) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((x, i) => eq(x, b[i]));
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every((k) => Object.hasOwn(b, k) && eq(a[k], b[k]));
}

/** Length in code points (Python's len of a str). */
export function cpLen(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
      const d = s.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) i++;
    }
    n++;
  }
  return n;
}
const cpSlice = (s: string, n: number): string => (cpLen(s) <= n ? s : Array.from(s).slice(0, n).join(""));
/** Code-point order (Python's str ordering; JavaScript's < compares UTF-16 units). */
export function cpCompare(a: string, b: string): number {
  const x = Array.from(a), y = Array.from(b);
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const p = x[i].codePointAt(0)!, q = y[i].codePointAt(0)!;
    if (p !== q) return p < q ? -1 : 1;
  }
  return x.length - y.length;
}
const sortedStrings = (xs: string[]): string[] => [...xs].sort(cpCompare);

// Python's str.isspace() set (str.strip() without arguments)
const PY_SPACE = new Set([0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
  0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000]);
export function pyStrip(s: string): string {
  const cps = Array.from(s);
  let i = 0, j = cps.length;
  while (i < j && PY_SPACE.has(cps[i].codePointAt(0)!)) i++;
  while (j > i && PY_SPACE.has(cps[j - 1].codePointAt(0)!)) j--;
  return cps.slice(i, j).join("");
}
/** Python's str.casefold, approximately: upper then lower case (ß → ss, ſ → s). */
export const casefold = (s: string): string => s.toUpperCase().toLowerCase();

const DIGIT = /\p{Nd}/u;
function digitValue(ch: string): number {
  // decimal digits come in runs of ten starting at zero: the value is the distance to the run's start, mod 10
  let cp = ch.codePointAt(0)!;
  let start = cp;
  while (start > 0 && DIGIT.test(String.fromCodePoint(start - 1)) && cp - start < 60) start--;
  return (cp - start) % 10;
}
/** Python's float() of a str: whitespace around, Unicode digits, underscores between digits,
 *  inf / infinity / nan in any case. null when Python would raise. */
export function pyFloat(s: string): number | null {
  let t = "";
  for (const ch of pyStrip(s)) {
    const cp = ch.codePointAt(0)!;
    if (PY_SPACE.has(cp)) t += " ";
    else if (cp > 127 && DIGIT.test(ch)) t += String(digitValue(ch));
    else if (cp > 127) return null;
    else t += ch;
  }
  const m = /^([+-]?)(?:(inf|infinity|nan)|((?:\d(?:_?\d)*)(?:\.(?:\d(?:_?\d)*)?)?|\.\d(?:_?\d)*)((?:[eE][+-]?\d(?:_?\d)*)?))$/i.exec(t);
  if (!m) return null;
  const sign = m[1] === "-" ? -1 : 1;
  if (m[2]) return m[2].toLowerCase() === "nan" ? NaN : sign * Infinity;
  const v = Number((m[3] + m[4]).replaceAll("_", ""));
  return Number.isNaN(v) ? null : sign * v;
}

const num = (x: any): x is number => typeof x === "number" && Number.isFinite(x);
const isInt = (x: any): x is number => typeof x === "number" && Number.isInteger(x);
const hit = (a: Rect, b: Rect): boolean => !(a[2] < b[0] || a[0] > b[2] || a[3] < b[1] || a[1] > b[3]);
const badText = (s: any): boolean => typeof s === "string" && BAD_TEXT.test(s);

/** The worksheet title the generator makes of a sheet name. */
export function sheetTitle(name: string | null | undefined): string {
  return cpSlice(pyStrip(name || "Sheet").replace(BAD_TITLE_G, "_"), 31) || "Sheet";
}

export function colLetter(c: number): string {
  let s = "";
  c += 1;
  while (c) { const rem = (c - 1) % 26; c = Math.floor((c - 1) / 26); s = String.fromCharCode(65 + rem) + s; }
  return s;
}
export const cellRef = (r: number, c: number): string => `${colLetter(c)}${r + 1}`;
export function rangeRef(r1: number, c1: number, r2: number, c2: number): string {
  const a = cellRef(r1, c1), b = cellRef(r2, c2);
  return a === b ? a : `${a}:${b}`;
}

/** Client input repeated in a refusal: short clean strings and small integers only. */
export function echo(x: any): any {
  if (x === undefined || x === null || typeof x === "boolean") return x ?? null;
  if (typeof x === "number") return Number.isInteger(x) && Math.abs(x) < 2 ** 53 ? x : null;
  if (typeof x === "string" && cpLen(x) <= ECHO_MAX && !BAD_TEXT.test(x)) return x;
  return null;
}

function limitOf(v: any, t: any): string | null {
  if (typeof v === "string") {
    const n = cpLen(v);
    if (n > MAX_VALUE) return "value";
    if (t !== "text" && v.startsWith("=") && n > MAX_FORMULA) return "formula";
  }
  return null;
}

// ------------------------------------------------------------------------------------ the editor contract (the parts ops meet)
const lastPart = (s: string): string => s.slice(s.lastIndexOf(".") + 1);

/** The editor contract's first complaint about a cell: [message, field] (editor_contract.py). */
function contractCell(cell: Record<string, any>): [string, string] | null {
  const extra = Object.keys(cell).filter((k) => !["v", "t", "s"].includes(k));
  if (extra.length) return ["不支持的编辑器选项", lastPart(sortedStrings(extra)[0])];
  const t = own(cell, "t");
  if (has(cell, "t") && t !== null && !["text", "number", "auto"].includes(t)) return ["取值只能是 text / number / auto", "t"];
  const v = own(cell, "v");
  if (v !== undefined && v !== null && !["string", "number", "boolean"].includes(typeof v)) return ["单元格值必须是文本、数字、布尔值或 null", "v"];
  if (typeof v === "number" && !Number.isFinite(v)) return ["数字必须有限", "v"];
  if (t === "number" && v !== undefined && v !== null && v !== "") {
    const numeric = typeof v === "string" ? pyFloat(v) : typeof v === "boolean" ? (v ? 1 : 0) : v;
    if (numeric === null) return ["声明为数字的单元格不能保存非数字文本", "v"];
    if (typeof v === "boolean" || !Number.isFinite(numeric)) return ["数字必须有限且不是布尔值", "v"];
  }
  const st = has(cell, "s") ? cell.s : {};
  if (!isDict(st)) return ["必须是对象", "s"];
  const sx = Object.keys(st).filter((k) => !(STYLE_KEYS as readonly string[]).includes(k));
  if (sx.length) return ["不支持的编辑器选项", lastPart(sortedStrings(sx)[0])];
  if (has(st, "ha") && st.ha !== null && !HA.includes(st.ha)) return ["取值只能是 " + HA.join(" / "), "ha"];
  if (has(st, "va") && st.va !== null && !VA.includes(st.va)) return ["取值只能是 " + VA.join(" / "), "va"];
  for (const k of ["b", "i", "u", "wrap", "bd"]) {
    if (has(st, k) && typeof st[k] !== "boolean" && !(isInt(st[k]) && (st[k] === 0 || st[k] === 1))) return ["必须是布尔值", k];
  }
  for (const k of ["color", "bg"]) {
    const x = own(st, k);
    if (x !== undefined && x !== null && (typeof x !== "string" || !COLOUR.test(x))) return ["必须是六位十六进制颜色", k];
  }
  if (has(st, "sz") && (!num(st.sz) || st.sz < 4 || st.sz > 144)) return ["必须是 4–144 范围内的有限数字", "sz"];
  return null;
}

function checkCell(cell: Record<string, any>): void {
  const bad = contractCell(cell);
  if (bad) refuse("invalid", { reason: "cell", message: bad[0], field: bad[1] });
  const lim = limitOf(own(cell, "v"), own(cell, "t"));
  if (lim) refuse("too_large", { what: lim, max: lim === "value" ? MAX_VALUE : MAX_FORMULA });
  const nf = own(isDict(own(cell, "s")) ? cell.s : {}, "nf");
  if (nf !== undefined && nf !== null && (typeof nf !== "string" || cpLen(nf) > MAX_NUMBER_FORMAT)) refuse("invalid", { reason: "number_format" });
  const what = unwritable(cell);
  if (what) refuse("invalid", { reason: "unwritable", what });
}

/** What of a contract-valid cell the workbook writer could not write (only characters can come from a browser). */
function unwritable(cell: Record<string, any>): string | null {
  const v = own(cell, "v"), t = own(cell, "t");
  if (v !== undefined && v !== null && v !== "") {
    const typed = t === "text" ? String(v) : t === "number" ? null : v;
    if (typeof typed === "string" && BAD_TEXT.test(typed)) return "characters";
  }
  if (badText(own(own(cell, "s") || {}, "nf"))) return "characters";
  return null;
}

/** The contract's first complaint about a field's attributes: [message, field]. */
function contractField(f: Record<string, any>): [string, string] | null {
  const allowed = ["id", "name", "kind", "ref", "row", "c1", "c2", "required", "empty_policy"];
  const extra = Object.keys(f).filter((k) => !allowed.includes(k));
  if (extra.length) return ["不支持的编辑器选项", lastPart(sortedStrings(extra)[0])];
  if (has(f, "kind") && f.kind !== null && !FIELD_KINDS.includes(f.kind)) return ["取值只能是 cell / region / repeat", "kind"];
  if (has(f, "empty_policy") && f.empty_policy !== null && !EMPTY_POLICIES.includes(f.empty_policy)) return ["取值只能是 " + EMPTY_POLICIES.join(" / "), "empty_policy"];
  if (has(f, "required") && typeof f.required !== "boolean") return ["必须是布尔值", "required"];
  const id = own(f, "id");
  if (id !== undefined && id !== null && (typeof id !== "string" || !FIELD_ID.test(id))) return ["字段 ID 必须有效且在整个模板中唯一", "id"];
  return null;
}

function fieldAttrs(raw: Record<string, any>): void {
  const probe: Record<string, any> = {};
  for (const k of Object.keys(raw)) if (!["ref", "row", "c1", "c2"].includes(k)) setKey(probe, k, raw[k]);
  const bad = contractField(probe);
  if (bad) refuse("invalid", { reason: "field", message: bad[0], field: bad[1] });
  const name = own(raw, "name");
  if (name !== undefined && name !== null && (typeof name !== "string" || !pyStrip(name) || cpLen(name) > MAX_FIELD_NAME || badText(name))) {
    refuse("invalid", { reason: "field_name", max: MAX_FIELD_NAME });
  }
}

// ------------------------------------------------------------------------------------ positions of merges and fields
function fieldIssue(kind: Kind, rect: Rect, merges: [Rect, any][], others: [Kind, Rect, any][]): [string, any] | null {
  const [r1, c1] = rect;
  if (kind === "cell") {
    for (const [m, ident] of merges) if (m[0] <= r1 && r1 <= m[2] && m[1] <= c1 && c1 <= m[3] && !(r1 === m[0] && c1 === m[1])) return ["merged_inner", ident];
  } else if (kind === "region") {
    for (const [m, ident] of merges) if (hit(m, rect)) return ["merged", ident];
  } else {
    for (const [m, ident] of merges) if (m[0] <= r1 && r1 <= m[2] && m[0] !== m[2]) return ["vertical_merge", ident];
    for (const [okind, orect, ident] of others) if (okind === "repeat" && orect[0] === r1) return ["duplicate_repeat", ident];
  }
  for (const [, orect, ident] of others) if (hit(orect, rect)) return ["field_overlap", ident];
  return null;
}

/** The merged ranges and fields of one sheet by position, in the order the server reports "first". */
class Geo {
  mr = new Map<string, Rect>();
  fr = new Map<string, [Kind, Rect]>();
  order = new Map<string, number>();
  k = 0;
  addMerge(mid: string, rect: Rect) { this.order.set("m" + mid, ++this.k); this.mr.set(mid, rect); }
  dropMerge(mid: string) { if (this.mr.delete(mid)) this.order.delete("m" + mid); }
  addField(fk: string, kind: Kind, rect: Rect) { this.order.set("f" + fk, ++this.k); this.fr.set(fk, [kind, rect]); }
  dropField(fk: string) { if (this.fr.delete(fk)) this.order.delete("f" + fk); }
  mergesIn(rect: Rect, skip?: Set<string>): [Rect, string][] {
    const out: [Rect, string][] = [];
    for (const [mid, m] of this.mr) if (!skip?.has(mid) && hit(m, rect)) out.push([m, mid]);
    return out.sort((a, b) => this.order.get("m" + a[1])! - this.order.get("m" + b[1])!);
  }
  private sortF<T extends [Kind, Rect, string]>(xs: T[]): T[] { return xs.sort((a, b) => this.order.get("f" + a[2])! - this.order.get("f" + b[2])!); }
  fieldsIn(rect: Rect, skip?: string): [Kind, Rect, string][] {
    const out: [Kind, Rect, string][] = [];
    for (const [fk, [kind, r]] of this.fr) if (fk !== skip && hit(r, rect)) out.push([kind, r, fk]);
    return this.sortF(out);
  }
  repeatsBetween(r1: number, r2: number, skip?: string): [Kind, Rect, string][] {
    const out: [Kind, Rect, string][] = [];
    for (const [fk, [kind, r]] of this.fr) if (fk !== skip && kind === "repeat" && r[0] >= r1 && r[0] <= r2) out.push([kind, r, fk]);
    return this.sortF(out);
  }
  fieldIssue(kind: Kind, rect: Rect, skip?: string): [string, ["merge" | "field", string]] | null {
    let merges: [Rect, string][];
    let others: [Kind, Rect, string][];
    if (kind === "repeat") {
      merges = [];
      for (const [mid, m] of this.mr) if (m[0] <= rect[0] && rect[0] <= m[2] && m[0] !== m[2]) merges.push([m, mid]);
      merges.sort((a, b) => this.order.get("m" + a[1])! - this.order.get("m" + b[1])!);
      const near = new Map<string, [Kind, Rect, string]>();
      for (const x of this.fieldsIn(rect, skip)) near.set(x[2], x);
      for (const x of this.repeatsBetween(rect[0], rect[0], skip)) near.set(x[2], x);
      others = this.sortF([...near.values()]);
    } else {
      merges = this.mergesIn(rect);
      others = this.fieldsIn(rect, skip);
    }
    return fieldIssue(kind, rect, merges.map(([m, i]) => [m, ["merge", i]]), others.map(([k, r, i]) => [k, r, ["field", i]])) as any;
  }
  mergeIssue(rect: Rect): [string, string, Rect] | null {
    const near = new Map<string, [Kind, Rect, string]>();
    for (const x of this.fieldsIn(rect)) near.set(x[2], x);
    if (rect[0] !== rect[2]) for (const x of this.repeatsBetween(rect[0], rect[2])) near.set(x[2], x);
    for (const [kind, frect, fk] of this.sortF([...near.values()])) {
      const got = fieldIssue(kind, frect, [[rect, null]], []);
      if (got) return [got[0], fk, frect];
    }
    return null;
  }
}

// ------------------------------------------------------------------------------------ transactions
const ABSENT = Symbol("absent");
const pkey = (path: string[]): string => path.join("\u0001");

function positions(sh: SheetC): [Map<string, number>, Map<string, number>] {
  return [new Map(sh.rows.map((x, i) => [x, i])), new Map(sh.cols.map((x, i) => [x, i]))];
}

/** One accepted-or-refused unit, as the server's `_Tx`: every write can be undone as a whole and
 *  becomes the patch and its inverse. `who` is who last wrote each object (the server's `kseq`, by
 *  person only) when the caller knows it — the corpus does, a tab does not. */
class Tx {
  c: Content;
  by: string;
  who: Map<string, string>;
  cap: number;
  undo: any[] = [];
  first = new Map<string, any[]>();
  pushed = new Map<string, number>();
  order: string[][] = [];
  pos = new Map<string, [Map<string, number>, Map<string, number>]>();
  geos = new Map<string, Geo>();
  constructor(c: Content, by: string, who: Map<string, string>, cap: number) { this.c = c; this.by = by; this.who = who; this.cap = cap; }

  node(path: string[]): any {
    let node: any = this.c;
    for (const k of path.slice(0, -1)) node = own(node, k);
    return node;
  }
  private touch(path: string[], old: any) {
    const k = pkey(path);
    if (!this.first.has(k) && !this.pushed.has(k)) { this.first.set(k, old === ABSENT ? [] : [old]); this.order.push(path); }
  }
  put(path: string[], value: any) {
    const node = this.node(path), key = path[path.length - 1];
    const old = has(node, key) ? node[key] : ABSENT;
    this.undo.push(["put", path, old]);
    const k = pkey(path);
    if (this.pushed.has(k)) { this.first.set(k, [(old as any[]).slice(0, this.pushed.get(k))]); this.pushed.delete(k); }
    else this.touch(path, old);
    setKey(node, key, value);
  }
  delete(path: string[]) {
    const node = this.node(path), key = path[path.length - 1];
    if (!has(node, key)) return;
    const old = node[key];
    this.undo.push(["put", path, old]);
    this.touch(path, old);
    delete node[key];
  }
  push(path: string[], items: any[]) {
    const lst: any[] = own(this.node(path), path[path.length - 1]);
    const k = pkey(path);
    if (!this.first.has(k) && !this.pushed.has(k)) { this.pushed.set(k, lst.length); this.order.push(path); }
    lst.push(...items);
    this.undo.push(["push", path, items.length]);
  }
  kset(key: string) {
    this.undo.push(["k", key, this.who.has(key) ? this.who.get(key) : null]);
    this.who.set(key, this.by);
  }
  rollback() {
    for (let i = this.undo.length - 1; i >= 0; i--) {
      const [kind, a, b] = this.undo[i];
      if (kind === "put") {
        const node = this.node(a), key = a[a.length - 1];
        if (b === ABSENT) delete node[key];
        else setKey(node, key, b);
      } else if (kind === "push") {
        const lst: any[] = own(this.node(a), a[a.length - 1]);
        lst.splice(lst.length - b, b);
      } else if (b === null) this.who.delete(a);
      else this.who.set(a, b);
    }
    this.undo = [];
    this.pos.clear();
    this.geos.clear();
  }
  commit(): [PatchEntry[], PatchEntry[]] {
    const patch: PatchEntry[] = [], before: PatchEntry[] = [];
    for (const path of this.order) {
      let parentWhole = false;
      for (let k = 1; k < path.length; k++) if (this.first.has(pkey(path.slice(0, k)))) { parentWhole = true; break; }
      if (parentWhole) continue;
      const node = this.node(path), key = path[path.length - 1], k = pkey(path);
      if (this.pushed.has(k)) {
        const lst: any[] = node[key], n0 = this.pushed.get(k)!;
        if (lst.length === n0) continue;
        patch.push({ p: [...path], push: copy(lst.slice(n0)) });
        before.push({ p: [...path], trim: n0 });
      } else {
        const cur = has(node, key) ? [node[key]] : [];
        const orig = this.first.get(k)!;
        if (eq(orig, cur)) continue;
        patch.push(cur.length ? { p: [...path], v: copy(cur[0]) } : { p: [...path], d: 1 });
        before.push(orig.length ? { p: [...path], v: copy(orig[0]) } : { p: [...path], d: 1 });
      }
    }
    before.reverse();
    return [patch, before];
  }

  // -- lookups
  sheet(op: any, live = true): [string, SheetC] {
    const sid = own(op, "sheet");
    if (typeof sid !== "string") refuse("invalid", { reason: "sheet" });
    const sh: SheetC | undefined = own(this.c.sheets, sid);
    if (sh === undefined) refuse("not_found", { what: "sheet", sheet: echo(sid) });
    if (live && sh!.gone) refuse("sheet_deleted", { sheet: sid, name: sh!.name, by: this.whoOf(`x/${sid}`) });
    return [sid, sh!];
  }
  positions(sid: string) {
    let p = this.pos.get(sid);
    if (!p) { p = positions(this.c.sheets[sid]); this.pos.set(sid, p); }
    return p;
  }
  geo(sid: string): Geo {
    let g = this.geos.get(sid);
    if (!g) {
      g = new Geo();
      this.geos.set(sid, g);
      const [rp, cp] = this.positions(sid);
      const sh = this.c.sheets[sid];
      for (const mid of sh.merges) {
        const at = this.c.merges[mid].at;
        g.addMerge(mid, [rp.get(at[0])!, cp.get(at[1])!, rp.get(at[2])!, cp.get(at[3])!]);
      }
      for (const fk of sh.fields) g.addField(fk, this.c.fields[fk].kind, this.fieldRect(sid, this.c.fields[fk]));
    }
    return g;
  }
  row(sid: string, rid: any, key = "row"): number {
    const p = typeof rid === "string" ? this.positions(sid)[0].get(rid) : undefined;
    if (p === undefined) refuse("not_found", { what: "row", sheet: sid, [key]: echo(rid) });
    return p!;
  }
  col(sid: string, cid: any, key = "col"): number {
    const p = typeof cid === "string" ? this.positions(sid)[1].get(cid) : undefined;
    if (p === undefined) refuse("not_found", { what: "col", sheet: sid, [key]: echo(cid) });
    return p!;
  }
  rect(sid: string, ids: any): Rect {
    if (!Array.isArray(ids) || ids.length !== 4) refuse("invalid", { reason: "range" });
    const r1 = this.row(sid, ids[0]), r2 = this.row(sid, ids[2]);
    const c1 = this.col(sid, ids[1]), c2 = this.col(sid, ids[3]);
    return [Math.min(r1, r2), Math.min(c1, c2), Math.max(r1, r2), Math.max(c1, c2)];
  }
  idsOf(sid: string, rect: Rect): string[] {
    const sh = this.c.sheets[sid];
    return [sh.rows[rect[0]], sh.cols[rect[1]], sh.rows[rect[2]], sh.cols[rect[3]]];
  }
  whoOf(key: string): string | null { return this.who.get(key) ?? null; }
  fieldRect(sid: string, f: FieldC): Rect {
    const [rp, cp] = this.positions(sid);
    const at = f.at;
    if (f.kind === "cell") return [rp.get(at[0])!, cp.get(at[1])!, rp.get(at[0])!, cp.get(at[1])!];
    if (f.kind === "region") return [rp.get(at[0])!, cp.get(at[1])!, rp.get(at[2])!, cp.get(at[3])!];
    return [rp.get(at[0])!, cp.get(at[1])!, rp.get(at[0])!, cp.get(at[2])!];
  }
  describe(sid: string, what: "merge" | "field", ident: string): Record<string, any> {
    if (what === "merge") {
      const at = this.c.merges[ident].at;
      const [rp, cp] = this.positions(sid);
      return { merge: ident, range: rangeRef(rp.get(at[0])!, cp.get(at[1])!, rp.get(at[2])!, cp.get(at[3])!), by: this.whoOf(`m/${ident}`) };
    }
    return { field: ident, name: own(this.c.fields[ident].raw, "name") ?? null, by: this.whoOf(`f/${ident}`) };
  }
}

function keysOnly(op: Record<string, any>, allowed: string[]): void {
  const extra = Object.keys(op).filter((k) => k !== "op" && !allowed.includes(k));
  if (extra.length) refuse("invalid", { reason: "unknown_key", key: echo(sortedStrings(extra)[0]) });
}

function newId(tx: Tx, proposed: any, taken: (x: string) => boolean, what: string): string {
  if (proposed === undefined || proposed === null) throw new Unpredictable();
  if (typeof proposed !== "string" || !ID.test(proposed)) refuse("invalid", { reason: "id", what });
  if (taken(proposed)) refuse("id_taken", { what, id: proposed });
  return proposed;
}

const gridIds = (c: Content): number => Object.values(c.sheets).reduce((n, s) => n + s.rows.length + s.cols.length, 0);
function gridIdsOk(tx: Tx, more: number) {
  if (gridIds(tx.c) + more > MAX_GRID_IDS) refuse("too_large", { what: "grid", max: MAX_GRID_IDS });
}
const cellCount = (c: Content, liveOnly = false): number =>
  Object.values(c.sheets).reduce((n, s) => n + (liveOnly && s.gone ? 0 : Object.keys(s.cells).length), 0);

// ------------------------------------------------------------------------------------ cell ops
function valueOf(cell: any): Record<string, any> | null {
  if (!cell || !Object.keys(cell).length) return null;
  const v: Record<string, any> = {};
  for (const k of ["v", "t"]) if (has(cell, k)) v[k] = cell[k];
  return Object.keys(v).length ? v : null;
}
export const filled = (cell: any): boolean => !!cell && Object.keys(cell).length > 0 && cell.v !== undefined && cell.v !== null && cell.v !== "";

function writeCell(tx: Tx, sid: string, ck: string, cell: Record<string, any>) {
  const path = ["sheets", sid, "cells", ck];
  if (!Object.keys(cell).length) { tx.delete(path); return; }
  if (!has(tx.c.sheets[sid].cells, ck) && cellCount(tx.c) + 1 > tx.cap) refuse("too_large", { what: "cells", max: tx.cap });
  tx.put(path, cell);
}

function cellTarget(tx: Tx, op: any): [string, SheetC, string, number, number] {
  const [sid, sh] = tx.sheet(op);
  const r = tx.row(sid, own(op, "row")), c = tx.col(sid, own(op, "col"));
  return [sid, sh, `${op.row}:${op.col}`, r, c];
}

function opSet(tx: Tx, op: any, clear = false) {
  keysOnly(op, clear ? ["sheet", "row", "col"] : ["sheet", "row", "col", "v", "t"]);
  const [sid, sh, ck, r, c] = cellTarget(tx, op);
  const cur = own(sh.cells, ck);
  const neu: Record<string, any> = {};
  if (!clear) for (const k of ["v", "t"]) { const x = own(op, k); if (x !== undefined && x !== null) neu[k] = x; }
  for (const [k, v] of Object.entries(cur || {})) if (k !== "v" && k !== "t") neu[k] = v;
  if (eq(valueOf(neu), valueOf(cur))) return;
  checkCell(neu);
  const key = `v/${sid}/${ck}`;
  if (filled(neu) && sh.merges.length) {
    for (const [m, mid] of tx.geo(sid).mergesIn([r, c, r, c])) {
      if (!(r === m[0] && c === m[1])) refuse("covered", { sheet: sid, ref: cellRef(r, c), ...tx.describe(sid, "merge", mid) });
    }
  }
  writeCell(tx, sid, ck, neu);
  tx.kset(key);
}

function opStyle(tx: Tx, op: any) {
  keysOnly(op, ["sheet", "row", "col", "s"]);
  const [sid, sh, ck] = cellTarget(tx, op);
  const patch = own(op, "s");
  if (!isDict(patch) || !Object.keys(patch).length) refuse("invalid", { reason: "style" });
  const bad = Object.keys(patch).filter((k) => !(STYLE_KEYS as readonly string[]).includes(k));
  if (bad.length) refuse("invalid", { reason: "style", key: echo(sortedStrings(bad)[0]) });
  const cur: Record<string, any> = own(sh.cells, ck) || {};
  const style: Record<string, any> = { ...(own(cur, "s") || {}) };
  const changed: string[] = [];
  for (const [k, v] of Object.entries(patch)) {
    if (v === null) { if (has(style, k)) { delete style[k]; changed.push(k); } }
    else if (!has(style, k) || !eq(style[k], v)) { style[k] = v; changed.push(k); }
  }
  if (!changed.length) return;
  const neu: Record<string, any> = {};
  for (const [k, v] of Object.entries(cur)) if (k !== "s") neu[k] = v;
  if (Object.keys(style).length) neu.s = style;
  checkCell(neu);
  writeCell(tx, sid, ck, neu);
  for (const k of changed) tx.kset(`s/${sid}/${ck}/${k}`);
}

function sizeValue(op: any, key: string, bounds: [number, number]): number | null {
  const v = own(op, key);
  if (v !== undefined && v !== null && (!num(v) || v < bounds[0] || v > bounds[1])) refuse("invalid", { reason: "size", min: bounds[0], max: bounds[1] });
  return v ?? null;
}

function opSize(tx: Tx, op: any) {
  if (has(op, "row") === has(op, "col")) refuse("invalid", { reason: "size_target" });
  const part = has(op, "row") ? "row" : "col";
  keysOnly(op, ["sheet", part, part === "row" ? "h" : "w"]);
  const [sid, sh] = tx.sheet(op);
  const [key, bounds, store, count] = part === "row" ? ["h", ROW_HEIGHT, "rh", "nr"] as const : ["w", COL_WIDTH, "cw", "nc"] as const;
  if (!has(op, key)) refuse("invalid", { reason: "size" });
  const size = sizeValue(op, key, bounds);
  const ident = op[part];
  const at = part === "row" ? tx.row(sid, ident) : tx.col(sid, ident);
  if (at >= sh[count]) refuse("invalid", { reason: "not_in_grid", [part]: ident });
  const raw = own(sh[store], ident);
  let neu: Record<string, any> | null = isDict(raw) ? { ...raw } : size !== null ? {} : null;
  if (neu !== null) {
    if (size === null) delete neu[key];
    else neu[key] = size;
  }
  if (eq(neu, raw === undefined ? null : raw)) return;
  tx.put(["sheets", sid, store, ident], neu);
  tx.kset(`${key}/${sid}/${ident}`);
}

function opAppend(tx: Tx, op: any, part: "rows" | "cols") {
  const [key, bounds, store, count, cap] = part === "rows" ? ["h", ROW_HEIGHT, "rh", "nr", MAX_ROWS] as const : ["w", COL_WIDTH, "cw", "nc", MAX_COLS] as const;
  keysOnly(op, ["sheet", "count", "ids", key]);
  const [sid, sh] = tx.sheet(op);
  const n = own(op, "count");
  if (!isInt(n) || n < 1 || n > MAX_APPEND) refuse("invalid", { reason: "count", max: MAX_APPEND });
  if (sh[count] + n > cap) refuse("too_large", { what: part, max: cap });
  const size = sizeValue(op, key, bounds);
  const given = own(op, "ids");
  const proposed = pyTruthy(given) ? given : [];
  if (!Array.isArray(proposed) || proposed.length > n) refuse("invalid", { reason: "ids" });
  const [rp, cp] = tx.positions(sid);
  const ids = sh[part];
  const need = Math.max(0, sh[count] + n - ids.length);
  gridIdsOk(tx, need);
  const fresh: string[] = [];
  const made = new Set<string>();
  for (let i = 0; i < need; i++) {
    const x = newId(tx, i < proposed.length ? proposed[i] : null, (y) => rp.has(y) || cp.has(y) || made.has(y), part.slice(0, -1));
    fresh.push(x);
    made.add(x);
  }
  if (fresh.length) {
    tx.push(["sheets", sid, part], fresh);
    const target = part === "rows" ? rp : cp;
    fresh.forEach((x, j) => target.set(x, ids.length - fresh.length + j));
  }
  for (let p = sh[count]; p < sh[count] + n; p++) tx.put(["sheets", sid, store, ids[p]], size !== null ? { [key]: size } : {});
  tx.put(["sheets", sid, count], sh[count] + n);
}

// ------------------------------------------------------------------------------------ merges
function valuesUnder(tx: Tx, sid: string, sh: SheetC, rect: Rect): [number, number, string][] {
  const [r1, c1, r2, c2] = rect;
  const [rp, cp] = tx.positions(sid);
  const held: [number, number, string][] = [];
  for (const [ck, cell] of Object.entries(sh.cells)) {
    if (!filled(cell)) continue;
    const i = ck.indexOf(":");
    const r = rp.get(ck.slice(0, i))!, c = cp.get(ck.slice(i + 1))!;
    if (r1 <= r && r <= r2 && c1 <= c && c <= c2 && !(r === r1 && c === c1)) held.push([r, c, ck]);
  }
  return held.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

function opMerge(tx: Tx, op: any) {
  keysOnly(op, ["sheet", "id", "range", "replaces"]);
  const [sid, sh] = tx.sheet(op);
  const rect = tx.rect(sid, own(op, "range"));
  const want = rangeRef(...rect);
  const area = (rect[2] - rect[0] + 1) * (rect[3] - rect[1] + 1);
  if (area < 2) refuse("invalid", { reason: "single_cell_merge" });
  if (area > MAX_MERGE_CELLS) refuse("too_large", { what: "merge", max: MAX_MERGE_CELLS });
  const rep = own(op, "replaces");
  const replaces = pyTruthy(rep) ? rep : [];
  if (!Array.isArray(replaces) || !replaces.every((m) => typeof m === "string")) refuse("invalid", { reason: "replaces" });
  const gone: string[] = [];
  for (const m of replaces as string[]) {
    const cur = own(tx.c.merges, m);
    if (cur === undefined) continue;
    if (cur.sheet !== sid) refuse("invalid", { reason: "replaces", merge: echo(m) });
    gone.push(m);
  }
  const g = tx.geo(sid);
  for (const [, other] of g.mergesIn(rect, new Set(gone))) refuse("merge_overlap", { sheet: sid, want, ...tx.describe(sid, "merge", other) });
  const got = g.mergeIssue(rect);
  if (got) {
    const [code, fk, frect] = got;
    refuse(code, { sheet: sid, want, ref: rangeRef(...frect), ...tx.describe(sid, "field", fk) });
  }
  const held = valuesUnder(tx, sid, sh, rect);
  if (held.length) {
    const writers = sortedStrings([...new Set(held.map(([, , ck]) => tx.whoOf(`v/${sid}/${ck}`)).filter((w): w is string => !!w))]);
    refuse("covers_values", { sheet: sid, want, cells: held.slice(0, 10).map(([r, c]) => cellRef(r, c)), count: held.length, by: writers.slice(0, 5) });
  }
  const mid = newId(tx, own(op, "id"), (x) => has(tx.c.merges, x) || tx.who.has(`m/${x}`), "merge");
  for (const m of gone) {
    tx.delete(["merges", m]);
    tx.kset(`m/${m}`);
    g.dropMerge(m);
  }
  tx.put(["merges", mid], { sheet: sid, at: tx.idsOf(sid, rect) });
  if (gone.length) tx.put(["sheets", sid, "merges"], [...sh.merges.filter((m) => !gone.includes(m)), mid]);
  else tx.push(["sheets", sid, "merges"], [mid]);
  tx.kset(`m/${mid}`);
  g.addMerge(mid, rect);
}

/** Python truthiness of a JSON value (`x or []`). */
function pyTruthy(x: any): boolean {
  if (x === undefined || x === null || x === false || x === 0 || x === "") return false;
  if (Array.isArray(x)) return x.length > 0;
  if (isDict(x)) return Object.keys(x).length > 0;
  return true;
}

function opUnmerge(tx: Tx, op: any) {
  keysOnly(op, ["sheet", "id"]);
  const mid = own(op, "id");
  if (typeof mid !== "string") refuse("invalid", { reason: "id" });
  const cur: MergeC | undefined = own(tx.c.merges, mid);
  if (cur === undefined) refuse("not_found", { what: "merge", id: echo(mid) });
  const [sid, sh] = tx.sheet({ sheet: has(op, "sheet") ? op.sheet : cur!.sheet });
  if (sid !== cur!.sheet) refuse("invalid", { reason: "sheet" });
  tx.delete(["merges", mid]);
  tx.put(["sheets", sid, "merges"], sh.merges.filter((m) => m !== mid));
  tx.kset(`m/${mid}`);
  tx.geos.get(sid)?.dropMerge(mid);
}

// ------------------------------------------------------------------------------------ fields
function fieldPlace(tx: Tx, sid: string, op: any, kind: Kind): [string[], Rect] {
  if (kind === "cell") {
    const r = tx.row(sid, own(op, "row")), c = tx.col(sid, own(op, "col"));
    return [[op.row, op.col], [r, c, r, c]];
  }
  if (kind === "region") {
    const rect = tx.rect(sid, own(op, "range"));
    return [tx.idsOf(sid, rect), rect];
  }
  const r = tx.row(sid, own(op, "row"));
  const a = tx.col(sid, own(op, "c1"), "c1"), b = tx.col(sid, own(op, "c2"), "c2");
  const lo = Math.min(a, b), hi = Math.max(a, b);
  const sh = tx.c.sheets[sid];
  return [[op.row, sh.cols[lo], sh.cols[hi]], [r, lo, r, hi]];
}

function fieldFits(tx: Tx, sid: string, kind: Kind, rect: Rect, skip?: string) {
  const got = tx.geo(sid).fieldIssue(kind, rect, skip);
  if (got) {
    const [code, [what, ident]] = got;
    refuse(code, { sheet: sid, ref: rangeRef(...rect), ...tx.describe(sid, what, ident) });
  }
}

function opFieldAdd(tx: Tx, op: any) {
  const f = own(op, "field");
  if (!isDict(f) || Object.keys(f).some((k) => !["id", "name", "kind", "required", "empty_policy"].includes(k))) refuse("invalid", { reason: "field" });
  const kind = own(f, "kind");
  if (!FIELD_KINDS.includes(kind)) refuse("invalid", { reason: "field_kind" });
  keysOnly(op, ["sheet", "field", ...PLACE_KEYS[kind as Kind]]);
  const [sid] = tx.sheet(op);
  const fid = own(f, "id");
  if (typeof fid !== "string" || RESERVED_IDS.has(fid)) refuse("invalid", { reason: "field_id" });
  if (typeof own(f, "name") !== "string") refuse("invalid", { reason: "field_name", max: MAX_FIELD_NAME });
  const raw: Record<string, any> = { id: fid, name: f.name, kind };
  for (const k of POSITIONAL[kind as Kind]) raw[k] = null;
  for (const k of ["required", "empty_policy"]) if (has(f, k)) raw[k] = f[k];
  fieldAttrs(raw);
  const cur: FieldC | undefined = own(tx.c.fields, fid);
  if (cur !== undefined) refuse("id_taken", { what: "field", id: fid, ...tx.describe(cur.sheet, "field", fid) });
  const [at, rect] = fieldPlace(tx, sid, op, kind);
  fieldFits(tx, sid, kind, rect);
  tx.put(["fields", fid], { sheet: sid, kind, at, raw });
  tx.push(["sheets", sid, "fields"], [fid]);
  tx.kset(`f/${fid}`);
  tx.geo(sid).addField(fid, kind, rect);
}

function fieldTarget(tx: Tx, op: any): [string, FieldC, string, SheetC] {
  const fk = own(op, "field");
  if (typeof fk !== "string") refuse("invalid", { reason: "field" });
  const cur: FieldC | undefined = own(tx.c.fields, fk);
  if (cur === undefined) refuse("not_found", { what: "field", field: echo(fk) });
  const [sid, sh] = tx.sheet({ sheet: cur!.sheet });
  return [fk, cur!, sid, sh];
}

function opFieldSet(tx: Tx, op: any) {
  keysOnly(op, ["field", "set"]);
  const changes = own(op, "set");
  if (!isDict(changes) || !Object.keys(changes).length || Object.keys(changes).some((k) => !FIELD_ATTRS.includes(k))) refuse("invalid", { reason: "set" });
  const [fk, cur, sid] = fieldTarget(tx, op);
  const raw: Record<string, any> = { ...cur.raw };
  for (const [k, v] of Object.entries(changes)) {
    if (v === null) delete raw[k];
    else raw[k] = v;
  }
  if (eq(raw, cur.raw)) return;
  fieldAttrs(raw);
  tx.put(["fields", fk], { ...cur, raw });
  tx.kset(`f/${fk}`);
  void sid;
}

function opFieldMove(tx: Tx, op: any) {
  const fk0 = own(op, "field");
  if (typeof fk0 !== "string") refuse("invalid", { reason: "field" });
  const kind = has(op, "kind") ? op.kind : (own(own(tx.c.fields, fk0) || {}, "kind") ?? "cell");
  if (!FIELD_KINDS.includes(kind)) refuse("invalid", { reason: "field_kind" });
  keysOnly(op, ["field", "kind", ...PLACE_KEYS[kind as Kind]]);
  const [fk, cur, sid] = fieldTarget(tx, op);
  const [at, rect] = fieldPlace(tx, sid, op, kind);
  if (kind === cur.kind && eq(at, cur.at)) return;
  fieldFits(tx, sid, kind, rect, fk);
  const raw: Record<string, any> = {};
  for (const [k, v] of Object.entries(cur.raw)) if (!["ref", "row", "c1", "c2"].includes(k)) raw[k] = v;
  if (kind !== cur.kind || has(raw, "kind")) raw.kind = kind;
  for (const k of POSITIONAL[kind as Kind]) raw[k] = null;
  tx.put(["fields", fk], { sheet: sid, kind, at, raw });
  tx.kset(`f/${fk}`);
  const g = tx.geo(sid);
  g.dropField(fk);
  g.addField(fk, kind, rect);
}

function opFieldDel(tx: Tx, op: any) {
  keysOnly(op, ["field"]);
  const [fk, , sid, sh] = fieldTarget(tx, op);
  tx.delete(["fields", fk]);
  tx.put(["sheets", sid, "fields"], sh.fields.filter((x) => x !== fk));
  tx.kset(`f/${fk}`);
  tx.geos.get(sid)?.dropField(fk);
}

// ------------------------------------------------------------------------------------ sheets
function nameFree(tx: Tx, title: string, skip?: string) {
  for (const sid of tx.c.order) {
    const sh = tx.c.sheets[sid];
    if (sid !== skip && !sh.gone && casefold(sheetTitle(sh.name)) === casefold(title)) refuse("sheet_name_taken", { name: title, sheet: sid, by: tx.whoOf(`n/${sid}`) });
  }
}

function sheetName(tx: Tx, name: any, skip?: string): string {
  if (typeof name !== "string" || !name || name !== pyStrip(name) || cpLen(name) > MAX_SHEET_NAME || BAD_TITLE.test(name)
      || name.startsWith("'") || name.endsWith("'") || badText(name) || casefold(name) === "history") {
    refuse("invalid", { reason: "sheet_name", max: MAX_SHEET_NAME });
  }
  nameFree(tx, name, skip);
  return name;
}

const liveSheets = (tx: Tx): number => Object.values(tx.c.sheets).filter((s) => !s.gone).length;

function opSheetAdd(tx: Tx, op: any) {
  keysOnly(op, ["id", "name", "rows", "cols", "row_ids", "col_ids", "h", "w"]);
  const name = sheetName(tx, own(op, "name"));
  if (liveSheets(tx) >= MAX_SHEETS || Object.keys(tx.c.sheets).length >= 2 * MAX_SHEETS) refuse("too_large", { what: "sheets", max: MAX_SHEETS });
  const sid = newId(tx, own(op, "id"), (x) => has(tx.c.sheets, x) || tx.who.has(`x/${x}`), "sheet");
  const counts: Record<string, number> = {};
  for (const part of ["rows", "cols"]) {
    const n = has(op, part) ? op[part] : 0;
    if (!isInt(n) || n < 0 || n > MAX_APPEND) refuse("invalid", { reason: "count", what: part, max: MAX_APPEND });
    counts[part] = n;
  }
  gridIdsOk(tx, counts.rows + counts.cols);
  const h = sizeValue(op, "h", ROW_HEIGHT), w = sizeValue(op, "w", COL_WIDTH);
  const taken = new Set<string>();
  const ids: Record<string, string[]> = {};
  for (const part of ["rows", "cols"]) {
    const p = own(op, part.slice(0, -1) + "_ids");
    const proposed = pyTruthy(p) ? p : [];
    if (!Array.isArray(proposed) || proposed.length > counts[part]) refuse("invalid", { reason: "ids", what: part });
    ids[part] = [];
    for (let i = 0; i < counts[part]; i++) {
      const x = newId(tx, i < proposed.length ? proposed[i] : null, (y) => taken.has(y), part.slice(0, -1));
      taken.add(x);
      ids[part].push(x);
    }
  }
  const rh: Record<string, any> = {}, cw: Record<string, any> = {};
  for (const r of ids.rows) rh[r] = h !== null ? { h } : {};
  for (const c of ids.cols) cw[c] = w !== null ? { w } : {};
  const sheet: SheetC = { name, gone: false, keys: [...SHEET_KEYS], nul: [], rows: ids.rows, cols: ids.cols, nr: counts.rows, nc: counts.cols, rh, cw, cells: {}, merges: [], fields: [] };
  tx.put(["sheets", sid], sheet);
  tx.put(["order"], [...tx.c.order, sid]);
  tx.kset(`x/${sid}`);
  tx.kset(`n/${sid}`);
}

function opSheetRename(tx: Tx, op: any) {
  keysOnly(op, ["sheet", "name"]);
  const [sid, sh] = tx.sheet(op);
  const name = sheetName(tx, own(op, "name"), sid);
  if (name === sh.name) return;
  tx.put(["sheets", sid, "name"], name);
  tx.kset(`n/${sid}`);
}

function opSheetDelete(tx: Tx, op: any) {
  keysOnly(op, ["sheet"]);
  const [sid, sh] = tx.sheet(op, false);
  if (sh.gone) return;
  if (liveSheets(tx) <= 1) refuse("last_sheet", { sheet: sid });
  tx.put(["sheets", sid, "gone"], true);
  tx.kset(`x/${sid}`);
}

function opSheetRestore(tx: Tx, op: any) {
  keysOnly(op, ["sheet", "name"]);
  const [sid, sh] = tx.sheet(op, false);
  if (!sh.gone) return;
  let name: string;
  if (has(op, "name")) name = sheetName(tx, op.name, sid);
  else { name = sh.name as string; nameFree(tx, sheetTitle(name), sid); }
  if (liveSheets(tx) >= MAX_SHEETS) refuse("too_large", { what: "sheets", max: MAX_SHEETS });
  if (cellCount(tx.c, true) + Object.keys(sh.cells).length > tx.cap) refuse("too_large", { what: "cells", max: tx.cap });
  tx.put(["sheets", sid, "gone"], false);
  tx.kset(`x/${sid}`);
  if (name !== sh.name) { tx.put(["sheets", sid, "name"], name); tx.kset(`n/${sid}`); }
}

const OPS: Record<string, (tx: Tx, op: any) => void> = {
  set: (tx, op) => opSet(tx, op), clear: (tx, op) => opSet(tx, op, true), style: opStyle, size: opSize,
  append_rows: (tx, op) => opAppend(tx, op, "rows"), append_cols: (tx, op) => opAppend(tx, op, "cols"),
  merge: opMerge, unmerge: opUnmerge,
  field_add: opFieldAdd, field_set: opFieldSet, field_move: opFieldMove, field_del: opFieldDel,
  sheet_add: opSheetAdd, sheet_rename: opSheetRename, sheet_delete: opSheetDelete, sheet_restore: opSheetRestore,
};
export const OP_NAMES = Object.keys(OPS);

function dispatch(tx: Tx, op: any, top: boolean) {
  if (!isDict(op)) refuse("invalid", { reason: "op" });
  const kind = own(op, "op");
  if (kind === "tx") {
    if (!top) refuse("invalid", { reason: "nested_tx" });
    keysOnly(op, ["ops"]);
    const ops = own(op, "ops");
    if (!Array.isArray(ops) || !ops.length) refuse("invalid", { reason: "ops" });
    if (ops.length > MAX_TX) refuse("too_large", { what: "tx", max: MAX_TX });
    ops.forEach((sub: any, i: number) => {
      try { dispatch(tx, sub, false); }
      catch (e) {
        if (e instanceof RefusedErr && !has(e.detail, "index")) e.detail.index = i;
        throw e;
      }
    });
    return;
  }
  const fn = typeof kind === "string" && Object.hasOwn(OPS, kind) ? OPS[kind] : undefined;
  if (!fn) refuse("invalid", { reason: "op", op: echo(kind) });
  fn!(tx, op);
}

export interface ApplyOptions {
  /** The person the operation is from (the server stamps it; a tab uses its own id). */
  by: string;
  /** Who last wrote each object, when known (names in refusals; ids already used). */
  who?: Map<string, string>;
  /** The room's own bound on cells (hello.limits.max_cells). */
  maxCells?: number | null;
}

/** Apply one operation (or a `tx` group) to `content` in place, as the server would with nobody
 *  else's writes in between: the patch and its inverse, or the refusal (content untouched). Throws
 *  `Unpredictable` when the server would have to make up an id. */
export function applyOp(content: Content, op: any, opts: ApplyOptions): Result {
  const cap = Math.min(MAX_LIVE_CELLS, opts.maxCells ?? Infinity);
  const tx = new Tx(content, opts.by, opts.who ?? new Map(), cap);
  try {
    dispatch(tx, op, true);
  } catch (e) {
    tx.rollback();
    if (e instanceof RefusedErr) return { ok: false, code: e.code, detail: e.detail };
    throw e;
  }
  const [patch, before] = tx.commit();
  if (!patch.length) tx.rollback();
  return { ok: true, patch, before };
}

/** Apply a patch (or a `before` list) in place — what a tab does with the server's patches. Entries
 *  under a parent that no longer exists are skipped. */
export function applyPatch(content: Content, patch: PatchEntry[]): Content {
  for (const e of patch) {
    let node: any = content;
    for (const k of e.p.slice(0, -1)) {
      node = isDict(node) ? own(node, k) : undefined;
      if (node === undefined || node === null) break;
    }
    if (node === undefined || node === null) continue;
    const key = e.p[e.p.length - 1];
    if (has(e, "v")) setKey(node, key, copy(e.v));
    else if (has(e, "d")) delete node[key];
    else if (has(e, "push")) node[key].push(...copy(e.push!));
    else if (has(e, "trim")) node[key].splice(e.trim!);
  }
  return content;
}

// ------------------------------------------------------------------------------------ rendering (project)
export interface FieldOut { id?: string | null; name?: string; kind?: Kind; ref?: string; row?: number; c1?: number; c2?: number; required?: boolean; empty_policy?: string; [k: string]: any }
export interface SheetOut { name?: string | null; cols?: any[] | null; rows?: any[] | null; cells?: Record<string, Cell>; merges?: string[] | null; fields?: FieldOut[] }

function fieldOut(f: FieldC, rp: Map<string, number>, cp: Map<string, number>): FieldOut {
  const d: Record<string, any> = copy(f.raw);
  const at = f.at;
  if (f.kind === "cell") d.ref = cellRef(rp.get(at[0])!, cp.get(at[1])!);
  else if (f.kind === "region") d.ref = rangeRef(rp.get(at[0])!, cp.get(at[1])!, rp.get(at[2])!, cp.get(at[3])!);
  else for (const [k, v] of [["row", rp.get(at[0])!], ["c1", cp.get(at[1])!], ["c2", cp.get(at[2])!]] as const) if (has(d, k)) d[k] = v;
  return d;
}

/** One sheet of the editor model, as the server renders it (collab_sheet._sheet_out). */
export function projectSheet(content: Content, sid: string): SheetOut {
  const sh = content.sheets[sid];
  const [rp, cp] = positions(sh);
  const value = (k: string): any => {
    if (k === "name") return sh.name;
    if (k === "cols") return sh.cols.slice(0, sh.nc).map((c) => copy(own(sh.cw, c) ?? null));
    if (k === "rows") return sh.rows.slice(0, sh.nr).map((r) => copy(own(sh.rh, r) ?? null));
    if (k === "cells") {
      const out: Record<string, Cell> = {};
      for (const [ck, cell] of Object.entries(sh.cells)) {
        const i = ck.indexOf(":");
        out[cellRef(rp.get(ck.slice(0, i))!, cp.get(ck.slice(i + 1))!)] = copy(cell);
      }
      return out;
    }
    if (k === "merges") return sh.merges.map((mid) => { const at = content.merges[mid].at; return rangeRef(rp.get(at[0])!, cp.get(at[1])!, rp.get(at[2])!, cp.get(at[3])!); });
    return sh.fields.map((fk) => fieldOut(content.fields[fk], rp, cp));
  };
  const present: Record<string, boolean> = { name: sh.name !== null, cols: sh.nc > 0, rows: sh.nr > 0, cells: Object.keys(sh.cells).length > 0, merges: sh.merges.length > 0, fields: sh.fields.length > 0 };
  const nul = sh.nul || [];
  const keys = [...sh.keys, ...SHEET_KEYS.filter((k) => !sh.keys.includes(k) && present[k])];
  const out: Record<string, any> = {};
  for (const k of keys) out[k] = nul.includes(k) && !present[k] ? null : value(k);
  return out;
}

/** The editor model of the content (what a save writes). */
export function project(content: Content): { sheets?: SheetOut[] } {
  const sheets = content.order.filter((sid) => !content.sheets[sid].gone).map((sid) => projectSheet(content, sid));
  return content.keys.includes("sheets") || sheets.length ? { sheets } : {};
}

/** Live sheets in tab order. */
export const liveSheetIds = (content: Content): string[] => content.order.filter((sid) => !content.sheets[sid].gone);
