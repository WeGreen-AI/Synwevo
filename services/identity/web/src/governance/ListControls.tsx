import { t as tr } from "@synwevo/ui";
import "./messages";
import { useEffect, useRef } from "react";
import type { Selection } from "../components/selection";

export function PageControls({ offset, total, limit = 50, busy, onPage }: {
  offset: number; total: number; limit?: number; busy?: boolean; onPage: (offset: number) => void;
}) {
  return <nav className="pager" aria-label={tr("列表分页")}>
    <span role="status">{total ? tr("{p0}–{p1} / {p2} 项", {p0: offset + 1, p1: Math.min(offset + limit, total), p2: total}) : tr("0 项")}</span>
    <button type="button" className="btn sm" disabled={busy || offset === 0}
      onClick={() => onPage(Math.max(0, offset - limit))}>{tr("上一页")}</button>
    <button type="button" className="btn sm" disabled={busy || offset + limit >= total}
      onClick={() => onPage(offset + limit)}>{tr("下一页")}</button>
  </nav>;
}

export function SelectPage({ selection, disabled }: { selection: Selection; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = selection.some; }, [selection.some]);
  return <input ref={ref} type="checkbox" aria-label={tr("选择本页可操作的项")} checked={selection.all}
    disabled={disabled} onChange={() => selection.all ? selection.clear() : selection.selectAll()} />;
}
