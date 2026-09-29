/** The fill-in page's copy of the server's form rules (synwevo_suite/forms/definition.py), for
 *  immediate feedback only: the server checks every submission again and its answer wins. Both
 *  sides run the shared vectors in tests/forms-vectors.json, so they cannot drift apart silently. */

export type FieldType = "text" | "textarea" | "number" | "money" | "date" | "datetime" | "select" | "multiselect"
  | "boolean" | "file" | "repeat" | "section";

export type Condition =
  | { field: string; op: "eq" | "neq" | "in" | "not_in" | "filled" | "empty" | "gt" | "gte" | "lt" | "lte" | "contains"; value?: any }
  | { all: Condition[] } | { any: Condition[] } | { not: Condition };

export interface Option { value: string; label: string }

export interface FieldDef {
  id: string; type: FieldType; label: string; help?: string; placeholder?: string;
  required?: boolean; required_if?: Condition | null; visible_if?: Condition | null;
  format?: "plain" | "email" | "phone" | "url" | "pattern"; pattern?: string | null; pattern_hint?: string;
  min_length?: number | null; max_length?: number | null;
  min?: string | null; max?: string | null; decimals?: number | null; currency?: string | null;
  options?: Option[]; min_items?: number | null; max_items?: number | null;
  accept?: string[]; max_files?: number; max_size_mb?: number;
  fields?: FieldDef[];
}

export interface Step { id: string; title: string; fields: string[] }
export interface Rule { id: string; field: string; op: "gt" | "gte" | "lt" | "lte" | "eq" | "neq"; other: string; message: string }
export interface Definition { fields: FieldDef[]; steps?: Step[]; rules?: Rule[]; confirmation?: string }

export interface FieldError { field: string; code: string; parent?: string; row?: number; params?: Record<string, any> }

export const VALUE_TYPES = new Set<FieldType>(["text", "textarea", "number", "money", "date", "datetime", "select",
  "multiselect", "boolean", "file", "repeat"]);

const NUM = /^\s*[+-]?(\d+(\.\d*)?|\.\d+)\s*$/;

/** A decimal string as [sign, integer digits, fraction digits] — comparisons without floats. */
function parseDec(v: any): { neg: boolean; int: string; frac: string } | null {
  if (v === null || v === undefined || typeof v === "boolean") return null;
  if (typeof v === "object" && "amount" in v) v = v.amount;
  const s = String(v).trim();
  if (!NUM.test(s)) return null;
  const neg = s.startsWith("-");
  const body = s.replace(/^[+-]/, "");
  const [i, f = ""] = body.split(".");
  return { neg, int: (i || "0").replace(/^0+(?=\d)/, ""), frac: f.replace(/0+$/, "") };
}

function cmpDec(a: NonNullable<ReturnType<typeof parseDec>>, b: NonNullable<ReturnType<typeof parseDec>>): number {
  const zero = (d: typeof a) => /^0*$/.test(d.int) && d.frac === "";
  const sa = zero(a) ? 0 : a.neg ? -1 : 1, sb = zero(b) ? 0 : b.neg ? -1 : 1;
  if (sa !== sb) return sa < sb ? -1 : 1;
  if (sa === 0) return 0;
  let c = a.int.length !== b.int.length ? (a.int.length < b.int.length ? -1 : 1) : a.int < b.int ? -1 : a.int > b.int ? 1 : 0;
  if (c === 0) {
    const n = Math.max(a.frac.length, b.frac.length);
    const fa = a.frac.padEnd(n, "0"), fb = b.frac.padEnd(n, "0");
    c = fa < fb ? -1 : fa > fb ? 1 : 0;
  }
  return sa < 0 ? -c : c;
}

export function isEmpty(v: any): boolean {
  if (v === null || v === undefined) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return "amount" in v ? isEmpty(v.amount) : Object.keys(v).length === 0;
  return false;
}

function equal(a: any, b: any): boolean {
  if (typeof a === "boolean" || typeof b === "boolean") return a === b;
  const da = parseDec(a), db = parseDec(b);
  if (da && db && !Array.isArray(a)) return cmpDec(da, db) === 0;
  if (a && typeof a === "object" && "amount" in a) a = a.amount;
  return a === b;
}

export function evaluate(cond: Condition | null | undefined, values: Record<string, any>): boolean {
  if (!cond) return true;
  if ("all" in cond) return cond.all.every((c) => evaluate(c, values));
  if ("any" in cond) return cond.any.some((c) => evaluate(c, values));
  if ("not" in cond) return !evaluate(cond.not, values);
  const v = values[cond.field];
  switch (cond.op) {
    case "filled": return !isEmpty(v);
    case "empty": return isEmpty(v);
    case "eq": return equal(v, cond.value);
    case "neq": return !equal(v, cond.value);
    case "in": case "not_in": {
      const pool = Array.isArray(cond.value) ? cond.value : [cond.value];
      const hit = pool.some((p: any) => equal(v, p));
      return cond.op === "in" ? hit : !hit;
    }
    case "contains": return Array.isArray(v) && v.some((x) => equal(x, cond.value));
    default: {
      const a = parseDec(v), b = parseDec(cond.value);
      if (a && b) {
        const c = cmpDec(a, b);
        return cond.op === "gt" ? c > 0 : cond.op === "gte" ? c >= 0 : cond.op === "lt" ? c < 0 : c <= 0;
      }
      if (typeof v === "string" && typeof cond.value === "string" && !parseDec(v) && !parseDec(cond.value)) {
        const c = v < cond.value ? -1 : v > cond.value ? 1 : 0;
        return cond.op === "gt" ? c > 0 : cond.op === "gte" ? c >= 0 : cond.op === "lt" ? c < 0 : c <= 0;
      }
      return false;
    }
  }
}

const EMAIL = /^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,63}$/;
const PHONE = /^\+?[0-9 ()\-]{5,30}$/;
const URL_RE = /^https?:\/\/[^\s/$.?#][^\s]{0,2000}$/i;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function validDate(s: string): boolean {
  if (!DATE.test(s)) return false;
  const [y, m, d] = s.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
}

export function visibleFields(defn: Definition, answers: Record<string, any>): Set<string> {
  const seen: Record<string, any> = {};
  const out = new Set<string>();
  for (const f of defn.fields) {
    if (f.type === "section") { if (evaluate(f.visible_if, seen)) out.add(f.id); continue; }
    if (!evaluate(f.visible_if, seen)) continue;
    out.add(f.id);
    const v = answers[f.id];
    seen[f.id] = f.type === "boolean" ? v === true : v;
  }
  return out;
}

/** Client-side check: the same codes the server returns. `files` maps an uploaded id to its
 *  field, name and size. Returns the errors only (the server normalizes). */
export function checkAnswers(defn: Definition, answers: Record<string, any>,
                             files: Record<string, { field_id: string; filename: string; size: number }> = {}): FieldError[] {
  const errors: FieldError[] = [];
  const seen: Record<string, any> = {};
  const clean: Record<string, any> = {};
  for (const f of defn.fields) {
    if (f.type === "section") continue;
    if (!evaluate(f.visible_if, seen)) continue;
    const norm = checkValue(f, answers[f.id], errors, files);
    if (norm !== undefined || f.type === "boolean") {
      seen[f.id] = norm ?? false;
      if (norm !== undefined) clean[f.id] = norm;
    }
    const required = f.required || (f.required_if ? evaluate(f.required_if, seen) : false);
    if (required && (isEmpty(norm) || (f.type === "boolean" && norm !== true)) && !errors.some((e) => e.field === f.id)) {
      errors.push({ field: f.id, code: "required" });
    }
  }
  for (const r of defn.rules ?? []) {
    if (!(r.field in clean) || !(r.other in clean)) continue;
    const a = clean[r.field], b = clean[r.other];
    let c: number | null = null;
    const da = parseDec(a), db = parseDec(b);
    if (da && db) c = cmpDec(da, db);
    else if (typeof a === "string" && typeof b === "string") {
      const ta = Date.parse(DATE.test(a) ? a + "T00:00:00Z" : a), tb = Date.parse(DATE.test(b) ? b + "T00:00:00Z" : b);
      if (!Number.isNaN(ta) && !Number.isNaN(tb)) c = ta < tb ? -1 : ta > tb ? 1 : 0;
    }
    if (c === null) continue;
    const ok = r.op === "gt" ? c > 0 : r.op === "gte" ? c >= 0 : r.op === "lt" ? c < 0 : r.op === "lte" ? c <= 0 : r.op === "eq" ? c === 0 : c !== 0;
    if (!ok) errors.push({ field: r.field, code: "rule_failed", params: { rule: r.id, message: r.message } });
  }
  return errors;
}

function checkValue(f: FieldDef, value: any, errors: FieldError[], files: Record<string, { field_id: string; filename: string; size: number }>,
                    parent?: string, row?: number): any {
  const err = (code: string, params?: Record<string, any>) => {
    const e: FieldError = { field: f.id, code };
    if (parent !== undefined) { e.parent = parent; e.row = row; }
    if (params) e.params = params;
    errors.push(e);
  };
  if (f.type === "repeat") {
    const rows = Array.isArray(value) ? value : value == null ? [] : null;
    if (rows === null) { err("bad_value"); return undefined; }
    const max = f.max_items ?? 200;
    if (rows.length > max) { err("too_many", { max }); return undefined; }
    const out: any[] = [];
    rows.forEach((item, i) => {
      const rowSeen: Record<string, any> = {};
      const rowOut: Record<string, any> = {};
      for (const s of f.fields ?? []) {
        if (!evaluate(s.visible_if, rowSeen)) continue;
        const norm = checkValue(s, (item ?? {})[s.id], errors, files, f.id, i);
        if (norm !== undefined || s.type === "boolean") { rowSeen[s.id] = norm ?? false; if (norm !== undefined) rowOut[s.id] = norm; }
        const req = s.required || (s.required_if ? evaluate(s.required_if, rowSeen) : false);
        if (req && (isEmpty(norm) || (s.type === "boolean" && norm !== true))
            && !errors.some((e) => e.field === s.id && e.row === i)) errors.push({ field: s.id, code: "required", parent: f.id, row: i });
      }
      out.push(rowOut);
    });
    if (f.min_items && out.length < f.min_items) err("too_few", { min: f.min_items });
    return out;
  }
  if (value === null || value === undefined || value === "") return f.type === "boolean" ? false : undefined;
  switch (f.type) {
    case "text": case "textarea": {
      if (typeof value !== "string") { err("bad_value"); return undefined; }
      const cap = f.max_length ?? (f.type === "textarea" ? 10000 : 500);
      if ([...value].length > cap) { err("too_long", { max: cap }); return undefined; }
      if (value.trim() === "") return value;
      if (f.min_length && [...value].length < f.min_length) err("too_short", { min: f.min_length });
      const s = value.trim();
      if (f.format === "email" && !EMAIL.test(s)) err("bad_email");
      else if (f.format === "phone" && !PHONE.test(s)) err("bad_phone");
      else if (f.format === "url" && !URL_RE.test(s)) err("bad_url");
      else if (f.format === "pattern" && f.pattern && s.length <= 500) {
        let re: RegExp | null = null;
        try { re = new RegExp(`^(?:${f.pattern})$`); } catch { re = null; }
        if (re && !re.test(s)) err("pattern_mismatch", { hint: f.pattern_hint ?? "" });
      }
      return value;
    }
    case "number": case "money": {
      const amount = typeof value === "object" && value !== null ? value.amount : value;
      if (typeof amount === "boolean" || (typeof amount !== "string" && typeof amount !== "number")) { err("not_a_number"); return undefined; }
      const d = parseDec(amount);
      if (!d) { err("not_a_number"); return undefined; }
      const places = f.decimals ?? (f.type === "money" ? 2 : 6);
      if (d.frac.length > places) { err("too_many_decimals", { max: places }); return undefined; }
      const lo = parseDec(f.min), hi = parseDec(f.max);
      if (lo && cmpDec(d, lo) < 0) err("below_min", { min: f.min });
      if (hi && cmpDec(d, hi) > 0) err("above_max", { max: f.max });
      if (f.type === "money") {
        const frac = d.frac.padEnd(places, "0");
        return { amount: `${d.neg && !(/^0*$/.test(d.int) && d.frac === "") ? "-" : ""}${d.int}${places ? "." + frac : ""}`, currency: f.currency };
      }
      return String(amount).trim();
    }
    case "date":
      if (typeof value !== "string" || !validDate(value)) { err("bad_date"); return undefined; }
      return value;
    case "datetime": {
      if (typeof value !== "string" || Number.isNaN(Date.parse(value))) { err("bad_datetime"); return undefined; }
      if (!/(Z|[+-]\d{2}:\d{2})$/.test(value)) { err("datetime_without_offset"); return undefined; }
      return value;
    }
    case "select":
      if (typeof value !== "string" || !(f.options ?? []).some((o) => o.value === value)) { err("not_an_option"); return undefined; }
      return value;
    case "multiselect": {
      if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) { err("not_an_option"); return undefined; }
      const allowed = new Set((f.options ?? []).map((o) => o.value));
      if (value.some((v) => !allowed.has(v))) { err("not_an_option"); return undefined; }
      const picked = (f.options ?? []).map((o) => o.value).filter((v) => value.includes(v));
      if (f.max_items && picked.length > f.max_items) err("too_many", { max: f.max_items });
      if (f.min_items && picked.length && picked.length < f.min_items) err("too_few", { min: f.min_items });
      return picked;
    }
    case "boolean":
      if (typeof value !== "boolean") { err("bad_value"); return undefined; }
      return value;
    case "file": {
      const ids = Array.isArray(value) ? value : [value];
      if (!ids.every((i) => typeof i === "string")) { err("bad_value"); return undefined; }
      if (ids.length > (f.max_files ?? 1)) { err("too_many", { max: f.max_files ?? 1 }); return undefined; }
      for (const id of ids) {
        const rec = files[id];
        if (!rec || rec.field_id !== f.id) { err("file_missing"); return undefined; }
        const ext = rec.filename.includes(".") ? "." + rec.filename.split(".").pop()!.toLowerCase() : "";
        if (f.accept?.length && !f.accept.map((a) => a.toLowerCase()).includes(ext)) { err("file_type", { accept: f.accept.join(",") }); return undefined; }
        if (rec.size > (f.max_size_mb ?? 10) * 1024 * 1024) { err("file_too_large", { max_mb: f.max_size_mb ?? 10 }); return undefined; }
      }
      return ids;
    }
  }
  err("bad_value");
  return undefined;
}

export function newFieldId(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  return "f_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
