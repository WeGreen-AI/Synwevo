/** Paging, search and sort for the project list, with a fallback for an older server.
 *
 *  The server pages the list when it gets any of q / sort / order / offset / limit / archived and
 *  answers {items, total, offset, limit, next_offset}. A server from before that change ignores
 *  the parameters and returns the whole array; `toPage` then applies the same search, order and
 *  window on the client, so the page works against either without a version check.
 *
 *  No imports: tested with `node --test`.
 */
export type ProjectSort = "updated" | "created" | "name";
export type Order = "asc" | "desc";

export interface ListParams { q: string; sort: ProjectSort; order: Order; offset: number; limit: number; archived: boolean }
export interface Page<T> { items: T[]; total: number; offset: number; limit: number; next_offset: number | null }

export const PAGE_SIZE = 50;

export function listQuery(p: ListParams): Record<string, string | number> {
  const out: Record<string, string | number> = { sort: p.sort, order: p.order, offset: Math.max(0, p.offset), limit: p.limit, archived: p.archived ? 1 : 0 };
  const q = p.q.trim();
  if (q) out.q = q;
  return out;
}

/** The natural direction for each column: newest first, names A→Z. */
export const defaultOrder = (s: ProjectSort): Order => (s === "name" ? "asc" : "desc");

interface Row { name?: string; template_name?: string | null; updated_at?: number | null; created_at?: number | null; archived_at?: number | null }

export function toPage<T extends Row>(resp: unknown, p: ListParams): Page<T> {
  if (Array.isArray(resp)) {
    const needle = p.q.trim().toLowerCase();
    let rows = (resp as T[]).filter((r) => p.archived || !r.archived_at);
    if (needle) rows = rows.filter((r) => [r.name, r.template_name].some((s) => String(s || "").toLowerCase().includes(needle)));
    const dir = p.order === "asc" ? 1 : -1;
    const by: Record<ProjectSort, (a: T, b: T) => number> = {
      updated: (a, b) => ((a.updated_at || 0) - (b.updated_at || 0)) * dir,
      created: (a, b) => ((a.created_at || 0) - (b.created_at || 0)) * dir,
      name: (a, b) => String(a.name || "").localeCompare(String(b.name || ""), "zh") * dir,
    };
    rows = [...rows].sort(by[p.sort]);
    const offset = Math.max(0, p.offset);
    const items = rows.slice(offset, offset + p.limit);
    const next = offset + p.limit < rows.length ? offset + p.limit : null;
    return { items, total: rows.length, offset, limit: p.limit, next_offset: next };
  }
  const r = (resp || {}) as Partial<Page<T>>;
  const items = Array.isArray(r.items) ? r.items : [];
  const offset = typeof r.offset === "number" ? r.offset : p.offset;
  const limit = typeof r.limit === "number" ? r.limit : p.limit;
  const total = typeof r.total === "number" ? r.total : offset + items.length;
  const next = r.next_offset === undefined ? (offset + items.length < total ? offset + items.length : null) : r.next_offset;
  return { items, total, offset, limit, next_offset: next ?? null };
}

/** Keep a keyboard cursor inside the list after rows disappear (delete, archive, a new page). */
export const clampCursor = (i: number, n: number) => (n <= 0 ? 0 : Math.max(0, Math.min(n - 1, i)));
