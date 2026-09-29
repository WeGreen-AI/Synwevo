/** Per-person undo in a live table (docs-sheet-live.md §5): an operation of mine the server accepted
 *  comes back with its patch and its inverse (`before`); undoing it sends operations that put back
 *  what was there before — but only for the objects that are still as I left them. What someone
 *  else changed since is skipped and named, never overwritten.
 *
 *  Pure: from the content as the tab shows it now and the accepted patch/inverse to the operations
 *  to send and what is skipped. Rows and columns added at the end cannot be taken away in B1 (there
 *  is no operation that removes them): they are skipped too, and said so. */
import { cellRef, eq, has, isDict, own, type Content, type FieldC, type PatchEntry } from "./sheetmodel.ts";

export interface Skipped {
  /** why: someone changed it since, or it cannot be undone in B1 */
  reason: "changed" | "append" | "idless";
  sheet: string | null;
  /** the cell (A1) or object it is about, when it has one */
  ref: string | null;
  field?: string | null;
}

export interface UndoPlan { ops: any[]; skipped: Skipped[] }

const valueOf = (cell: any): Record<string, any> | null => {
  if (!isDict(cell) || !Object.keys(cell).length) return null;
  const v: Record<string, any> = {};
  for (const k of ["v", "t"]) if (has(cell, k)) v[k] = cell[k];
  return Object.keys(v).length ? v : null;
};

/** The value of `path` in `content`: [value] or [] when absent. */
export function at(content: Content, path: string[]): any[] {
  let node: any = content;
  for (const k of path.slice(0, -1)) {
    node = isDict(node) ? own(node, k) : undefined;
    if (node === undefined || node === null) return [];
  }
  const k = path[path.length - 1];
  return isDict(node) && has(node, k) ? [node[k]] : [];
}

const nameOf = (content: Content, sid: string): string | null => own(content.sheets, sid)?.name ?? null;

function refOf(content: Content, sid: string, rid: string, cid: string): string | null {
  const sh = own(content.sheets, sid);
  if (!sh) return null;
  const r = sh.rows.indexOf(rid), c = sh.cols.indexOf(cid);
  return r < 0 || c < 0 ? null : cellRef(r, c);
}

/** What `before` put back and what the patch wrote, per path: [written] / [] and [before] / []. */
function pairs(patch: PatchEntry[], before: PatchEntry[]): Map<string, { path: string[]; wrote: any[]; was: any[]; append: boolean }> {
  const out = new Map<string, { path: string[]; wrote: any[]; was: any[]; append: boolean }>();
  for (const e of patch) {
    const k = e.p.join("\u0001");
    out.set(k, { path: e.p, wrote: has(e, "v") ? [e.v] : [], was: [], append: has(e, "push") });
  }
  for (const e of before) {
    const x = out.get(e.p.join("\u0001"));
    if (x) x.was = has(e, "v") ? [e.v] : [];
  }
  return out;
}

function fieldPlacement(f: FieldC): Record<string, any> {
  if (f.kind === "cell") return { row: f.at[0], col: f.at[1] };
  if (f.kind === "region") return { range: [...f.at] };
  return { row: f.at[0], c1: f.at[1], c2: f.at[2] };
}

/** The operations that undo an accepted patch in `content` (the tab's current view), and what is
 *  skipped. `newId` makes an id for a merged range put back (a used one is not taken again). */
export function planUndo(content: Content, patch: PatchEntry[], before: PatchEntry[], newId: () => string): UndoPlan {
  const ops: any[] = [];
  const skipped: Skipped[] = [];
  const all = pairs(patch, before);
  const appendedSheets = new Set<string>();
  for (const { path } of all.values()) if (path.length === 3 && path[0] === "sheets" && (path[2] === "nr" || path[2] === "nc")) appendedSheets.add(path[1]);
  for (const sid of appendedSheets) skipped.push({ reason: "append", sheet: nameOf(content, sid), ref: null });

  for (const { path, wrote, was, append } of all.values()) {
    if (append) continue;
    const cur = at(content, path);
    // ---- cells: the value and each style attribute this operation changed
    if (path[0] === "sheets" && path[2] === "cells" && path.length === 4) {
      const [, sid, , ck] = path;
      const i = ck.indexOf(":");
      const rid = ck.slice(0, i), cid = ck.slice(i + 1);
      const w = wrote[0] ?? null, b = was[0] ?? null, c = cur[0] ?? null;
      const ref = refOf(content, sid, rid, cid);
      if (!own(content.sheets, sid) || own(content.sheets, sid).gone) { skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref }); continue; }
      if (!eq(valueOf(w), valueOf(b))) {
        if (!eq(valueOf(c), valueOf(w))) skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref });
        else {
          const bv = valueOf(b);
          if (bv && bv.v !== undefined && bv.v !== null) ops.push({ op: "set", sheet: sid, row: rid, col: cid, v: bv.v, ...(bv.t != null ? { t: bv.t } : {}) });
          else ops.push({ op: "clear", sheet: sid, row: rid, col: cid });
        }
      }
      const ws = (w && w.s) || {}, bs = (b && b.s) || {}, cs = (c && c.s) || {};
      const keys = [...new Set([...Object.keys(ws), ...Object.keys(bs)])].filter((k) => !eq(own(ws, k) ?? null, own(bs, k) ?? null));
      if (keys.length) {
        if (keys.some((k) => !eq(own(cs, k) ?? null, own(ws, k) ?? null))) {
          if (!skipped.some((s) => s.ref === ref && s.sheet === nameOf(content, sid))) skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref });
        } else {
          const s: Record<string, any> = {};
          for (const k of keys) s[k] = own(bs, k) ?? null;
          ops.push({ op: "style", sheet: sid, row: rid, col: cid, s });
        }
      }
      continue;
    }
    // ---- row heights and column widths
    if (path[0] === "sheets" && (path[2] === "rh" || path[2] === "cw") && path.length === 4) {
      const [, sid, store, ident] = path;
      if (appendedSheets.has(sid) && !was.length) continue;                   // a row / column this operation added
      if (!eq(cur, wrote)) { skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref: null }); continue; }
      const key = store === "rh" ? "h" : "w";
      const size = isDict(was[0]) && has(was[0], key) ? was[0][key] : null;
      ops.push({ op: "size", sheet: sid, [store === "rh" ? "row" : "col"]: ident, [key]: size });
      continue;
    }
    // ---- merged ranges
    if (path[0] === "merges" && path.length === 2) {
      const mid = path[1];
      if (wrote.length && !was.length) {
        if (!eq(cur, wrote)) skipped.push({ reason: "changed", sheet: nameOf(content, wrote[0].sheet), ref: null });
        else ops.push({ op: "unmerge", id: mid });
      } else if (!wrote.length && was.length) {
        const m = was[0];
        if (cur.length || !own(content.sheets, m.sheet) || own(content.sheets, m.sheet).gone) skipped.push({ reason: "changed", sheet: nameOf(content, m.sheet), ref: null });
        else ops.push({ op: "merge", sheet: m.sheet, range: [...m.at], id: newId() });
      }
      continue;
    }
    // ---- fields
    if (path[0] === "fields" && path.length === 2) {
      const fk = path[1];
      const w: FieldC | null = wrote[0] ?? null, b: FieldC | null = was[0] ?? null;
      const label = (b || w)?.raw?.name ?? null;
      if (!eq(cur, wrote)) { skipped.push({ reason: "changed", sheet: nameOf(content, (b || w)!.sheet), ref: null, field: label }); continue; }
      if (w && !b) { ops.push({ op: "field_del", field: fk }); continue; }
      if (!w && b) {
        if (typeof own(b.raw, "id") !== "string" || fk.startsWith("~")) { skipped.push({ reason: "idless", sheet: nameOf(content, b.sheet), ref: null, field: label }); continue; }
        const f: Record<string, any> = { id: fk, name: b.raw.name, kind: b.kind };
        for (const k of ["required", "empty_policy"]) if (has(b.raw, k) && b.raw[k] !== null) f[k] = b.raw[k];
        ops.push({ op: "field_add", sheet: b.sheet, field: f, ...fieldPlacement(b) });
        continue;
      }
      if (w && b) {
        if (w.kind !== b.kind || !eq(w.at, b.at)) ops.push({ op: "field_move", field: fk, kind: b.kind, ...fieldPlacement(b) });
        const set: Record<string, any> = {};
        for (const k of ["name", "required", "empty_policy"]) if (!eq(own(w.raw, k) ?? null, own(b.raw, k) ?? null)) set[k] = own(b.raw, k) ?? null;
        if (Object.keys(set).length) ops.push({ op: "field_set", field: fk, set });
      }
      continue;
    }
    // ---- sheets: a new sheet, a name, deleted or restored
    if (path[0] === "sheets" && path.length === 2) {
      const sid = path[1];
      if (wrote.length && !was.length) {
        if (!cur.length || cur[0].gone) continue;
        ops.push({ op: "sheet_delete", sheet: sid });
      }
      continue;
    }
    if (path[0] === "sheets" && path.length === 3 && path[2] === "name") {
      const sid = path[1];
      if (!eq(cur, wrote)) skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref: null });
      else ops.push({ op: "sheet_rename", sheet: sid, name: was[0] });
      continue;
    }
    if (path[0] === "sheets" && path.length === 3 && path[2] === "gone") {
      const sid = path[1];
      if (!eq(cur, wrote)) skipped.push({ reason: "changed", sheet: nameOf(content, sid), ref: null });
      else ops.push(was[0] ? { op: "sheet_delete", sheet: sid } : { op: "sheet_restore", sheet: sid });
      continue;
    }
    // order, a sheet's lists of merges / fields, nr / nc: carried by the operations above
  }
  return { ops, skipped };
}
