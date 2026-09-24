import { useEffect, useRef } from "react";
import type { Selection } from "./selection";

export function PageControls({ offset, total, limit = 50, busy, onPage }: {
  offset: number; total: number; limit?: number; busy?: boolean; onPage: (offset: number) => void;
}) {
  return <nav className="pager" aria-label="列表分页">
    <span role="status">{total ? `${offset + 1}–${Math.min(offset + limit, total)} / ${total} 项` : "0 项"}</span>
    <button type="button" className="btn sm" disabled={busy || offset === 0}
      onClick={() => onPage(Math.max(0, offset - limit))}>上一页</button>
    <button type="button" className="btn sm" disabled={busy || offset + limit >= total}
      onClick={() => onPage(offset + limit)}>下一页</button>
  </nav>;
}

export function SelectPage({ selection, disabled }: { selection: Selection; disabled?: boolean }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = selection.some; }, [selection.some]);
  return <input ref={ref} type="checkbox" aria-label="选择本页可操作的项" checked={selection.all}
    disabled={disabled} onChange={() => selection.all ? selection.clear() : selection.selectAll()} />;
}
