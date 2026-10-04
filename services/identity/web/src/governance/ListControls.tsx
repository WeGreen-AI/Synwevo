import { t as tr } from "@synwevo/ui";
import "./messages";
import { useEffect, useRef } from "react";
import type { Selection } from "../components/selection";

/** The rows a page shows and whether a next page exists. A list that counts no further than a
 *  cap (`capped`) has more rows than `total`: the range is the rows actually on the page
 *  (`shown`), which may lie past the cap, and "is there a next page" is the server's answer
 *  (`more`), not arithmetic on a total that is not one. */
export function pageRange({ offset, total, limit = 50, shown, capped = false, more }: {
  offset: number; total: number; limit?: number; shown?: number; capped?: boolean; more?: boolean;
}): { first: number; last: number; next: boolean } {
  if (capped) return { first: offset + 1, last: offset + (shown ?? limit), next: more ?? true };
  return { first: offset + 1, last: Math.min(offset + limit, total), next: offset + limit < total };
}

export function PageControls({ offset, total, limit = 50, shown, capped, more, busy, onPage }: {
  offset: number; total: number; limit?: number; shown?: number; capped?: boolean; more?: boolean; busy?: boolean;
  onPage: (offset: number) => void;
}) {
  const range = pageRange({ offset, total, limit, shown, capped, more });
  return <nav className="pager" aria-label={tr("列表分页")}>
    <span role="status">{!total ? tr("0 项")
      : capped ? tr("{p0}–{p1} / {p2}+ 项", {p0: range.first, p1: range.last, p2: total})
      : tr("{p0}–{p1} / {p2} 项", {p0: range.first, p1: range.last, p2: total})}</span>
    <button type="button" className="btn sm" disabled={busy || offset === 0}
      onClick={() => onPage(Math.max(0, offset - limit))}>{tr("上一页")}</button>
    <button type="button" className="btn sm" disabled={busy || !range.next}
      onClick={() => onPage(offset + limit)}>{tr("下一页")}</button>
  </nav>;
}

export function SelectPage({ selection, disabled }: { selection: Selection; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = selection.some; }, [selection.some]);
  return <input ref={ref} type="checkbox" aria-label={tr("选择本页可操作的项")} checked={selection.all}
    disabled={disabled} onChange={() => selection.all ? selection.clear() : selection.selectAll()} />;
}
