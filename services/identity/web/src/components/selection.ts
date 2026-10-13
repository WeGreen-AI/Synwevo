/**
 * Multi-select for the member and invitation tables, and (`usePagedSelection`) for the
 * platform account list.
 *
 * The conventions people already have from file managers and mail clients:
 *   - click a checkbox (or Space on it) toggles one row and makes it the anchor;
 *   - Shift+click, or Shift+Space, selects everything between the anchor and this row;
 *   - Ctrl/⌘+click on a row toggles it without touching the rest;
 *   - ↑/↓ move between rows, Shift+↑/↓ extend the selection as they move;
 *   - Ctrl/⌘+A selects every selectable row on the page, Escape clears.
 * Rows the caller may not act on are not selectable at all, so a bulk action never starts
 * with items the page already knows will be refused.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { anyModalOpen, isImeKey } from "@synwevo/ui";

export function useSelection(selectable: string[]) {
  const [sel, setSel] = useState<Set<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const idsRef = useRef(selectable);
  idsRef.current = selectable;

  const range = useCallback((from: string, to: string) => {
    const ids = idsRef.current;
    const a = ids.indexOf(from), b = ids.indexOf(to);
    if (a < 0 || b < 0) return [to];
    const [lo, hi] = a < b ? [a, b] : [b, a];
    return ids.slice(lo, hi + 1);
  }, []);

  /** A click or keypress on one row. */
  const pick = useCallback((id: string, mods: { shift?: boolean }) => {
    if (!idsRef.current.includes(id)) return;
    if (mods.shift && anchor.current) {
      const span = range(anchor.current, id);
      setSel((prev) => { const n = new Set(prev); span.forEach((x) => n.add(x)); return n; });
      return;
    }
    setSel((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });
    anchor.current = id;
  }, [range]);

  const add = useCallback((ids: string[]) => {
    setSel((prev) => { const n = new Set(prev); ids.forEach((x) => { if (idsRef.current.includes(x)) n.add(x); }); return n; });
  }, []);
  const selectAll = useCallback(() => setSel(new Set(idsRef.current)), []);
  const clear = useCallback(() => { setSel(new Set()); anchor.current = null; }, []);
  const replace = useCallback((ids: string[]) => {
    setSel(new Set(ids.filter((x) => idsRef.current.includes(x))));
    anchor.current = null;
  }, []);

  const all = selectable.length > 0 && selectable.every((x) => sel.has(x));
  const some = !all && selectable.some((x) => sel.has(x));
  // Only what is still on the page counts: a row that left the list after a reload is gone.
  const ids = useMemo(() => selectable.filter((x) => sel.has(x)), [selectable, sel]);
  return { sel, ids, count: ids.length, all, some, pick, add, selectAll, clear, replace, anchor };
}

export type Selection = ReturnType<typeof useSelection>;

/**
 * The same conventions for a list whose selection outlives its page (the platform account list,
 * A18): rows chosen on one page stay chosen while the reader pages or searches on, up to `limit`
 * rows in all — the size of one server-side batch. `ids` is the whole selection in the order it
 * was made; `all`/`some` describe the page on screen, which the header checkbox selects or
 * clears; `full` says the cap is reached, and rows not yet chosen are then not offered.
 * Shift+click and Shift+↑/↓ still select within the page on screen.
 */
export function usePagedSelection(pageIds: string[], limit: number) {
  const [sel, setSel] = useState<Set<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const idsRef = useRef(pageIds);
  idsRef.current = pageIds;
  /** Adds in order and stops at the cap; what was chosen first stays chosen. */
  const grow = (prev: Set<string>, more: string[]) => {
    const n = new Set(prev);
    for (const x of more) { if (n.size >= limit) break; n.add(x); }
    return n;
  };

  const pick = useCallback((id: string, mods: { shift?: boolean }) => {
    const ids = idsRef.current;
    if (!ids.includes(id)) return;
    if (mods.shift && anchor.current && ids.includes(anchor.current)) {
      const a = ids.indexOf(anchor.current), b = ids.indexOf(id);
      const span = ids.slice(Math.min(a, b), Math.max(a, b) + 1);
      setSel((prev) => grow(prev, span));
      return;
    }
    setSel((prev) => { if (prev.has(id)) { const n = new Set(prev); n.delete(id); return n; } return grow(prev, [id]); });
    anchor.current = id;
  }, [limit]); // eslint-disable-line react-hooks/exhaustive-deps

  const add = useCallback((ids: string[]) => {
    setSel((prev) => grow(prev, ids.filter((x) => idsRef.current.includes(x))));
  }, [limit]); // eslint-disable-line react-hooks/exhaustive-deps
  /** "Select this page": adds the page's rows to what is already chosen elsewhere. */
  const selectAll = useCallback(() => setSel((prev) => grow(prev, idsRef.current)), [limit]); // eslint-disable-line react-hooks/exhaustive-deps
  /** Unticks the page's rows only; the rows chosen on other pages stay. */
  const clearPage = useCallback(() => {
    setSel((prev) => { const n = new Set(prev); idsRef.current.forEach((x) => n.delete(x)); return n; });
  }, []);
  const clear = useCallback(() => { setSel(new Set()); anchor.current = null; }, []);
  const replace = useCallback((ids: string[]) => { setSel(grow(new Set(), ids)); anchor.current = null; }, [limit]); // eslint-disable-line react-hooks/exhaustive-deps

  const all = pageIds.length > 0 && pageIds.every((x) => sel.has(x));
  const some = !all && pageIds.some((x) => sel.has(x));
  const ids = useMemo(() => [...sel], [sel]);
  return { sel, ids, count: sel.size, all, some, full: sel.size >= limit, pick, add, selectAll, clearPage, clear, replace, anchor };
}

export type PagedSelection = ReturnType<typeof usePagedSelection>;

/** Keyboard handling for a table whose row checkboxes carry `data-pick="<id>"`. */
export function selectionKeys(e: React.KeyboardEvent, s: Selection) {
  if (isImeKey(e) || anyModalOpen()) return;
  const t = e.target as HTMLElement;
  const box = t.closest<HTMLInputElement>("input[data-pick]");
  const mod = e.metaKey || e.ctrlKey;
  if (mod && (e.key === "a" || e.key === "A")) {
    // Inside a text field, ⌘A keeps meaning "select the text".
    if (t.matches("input[type=text], input[type=search], input[type=email], textarea")) return;
    e.preventDefault();
    s.selectAll();
    return;
  }
  if (!box) return;
  const id = box.dataset.pick!;
  if (e.key === " " && e.shiftKey) {
    e.preventDefault();
    s.pick(id, { shift: true });
    return;
  }
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    const table = box.closest("table");
    const boxes = Array.from(table?.querySelectorAll<HTMLInputElement>("input[data-pick]:not([disabled])") || []);
    const i = boxes.indexOf(box);
    const next = boxes[e.key === "ArrowDown" ? i + 1 : i - 1];
    if (!next) return;
    e.preventDefault();
    next.focus();
    if (e.shiftKey) {
      if (!s.anchor.current) s.anchor.current = id;
      s.add([id, next.dataset.pick!]);
    }
  }
}

/** Escape clears the selection wherever focus is on the page, unless a dialog or menu is
 *  open — they own Escape while they are up. */
export function escapeClears(e: React.KeyboardEvent, s: Selection, announce?: (t: string) => void) {
  if (e.key !== "Escape" || isImeKey(e) || anyModalOpen() || !s.count) return;
  if (e.defaultPrevented) return;
  e.preventDefault();
  s.clear();
  announce?.("已清除选择");
}
