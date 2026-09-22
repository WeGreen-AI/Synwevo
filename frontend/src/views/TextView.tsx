import { useEffect, useRef } from "react";
import type { Mark, Selection } from "../lib/types";
import { hexA } from "./GridView";

export default function TextView({ lines, marks, selection, onSelect, focusKey }: { lines: string[]; marks: Mark[]; selection: Selection | null; onSelect: (s: Selection | null) => void; focusKey?: string | null }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusKey) return;
    const el = ref.current?.querySelector(`[data-mark="${focusKey}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusKey]);
  const onUp = () => {
    const s = window.getSelection();
    if (!s || s.isCollapsed) return;
    const a = pt(s.anchorNode, s.anchorOffset), f = pt(s.focusNode, s.focusOffset);
    if (!a || !f || a.line !== f.line) return;
    const start = Math.min(a.off, f.off), end = Math.max(a.off, f.off);
    onSelect({ kind: "text_span", line: a.line, start, end, text: lines[a.line].slice(start, end) });
  };
  return (
    <div className="text-scroll" ref={ref} onMouseUp={onUp}>
      {lines.map((ln, i) => {
        const ms = marks.filter((m) => m.pos.kind === "text_span" && (m.pos as any).line === i);
        const sel = selection?.kind === "text_span" && selection.line === i ? selection : null;
        const cuts = new Set([0, ln.length]);
        for (const m of ms) { cuts.add((m.pos as any).start); cuts.add((m.pos as any).end); }
        if (sel) { cuts.add(sel.start); cuts.add(sel.end); }
        const b = [...cuts].filter((x) => x >= 0 && x <= ln.length).sort((x, y) => x - y);
        return (
          <div key={i} className="tline">
            <span className="ln">{i + 1}</span>
            <span className="tx" data-line={i}>
              {b.slice(0, -1).map((s0, k) => {
                const e0 = b[k + 1];
                const m = ms.find((x) => (x.pos as any).start <= s0 && (x.pos as any).end >= e0);
                const inSel = sel && sel.start <= s0 && sel.end >= e0;
                return (
                  <span key={k} data-off={s0} data-mark={m && (m.pos as any).start === s0 ? `${m.role}:${m.id}` : undefined}
                    style={{ background: inSel ? "rgba(37,99,235,.2)" : m ? hexA(m.color, 0.18) : undefined, borderBottom: m ? `2px solid ${m.color}` : undefined }}>
                    {ln.slice(s0, e0)}
                  </span>
                );
              })}
              {!ln && " "}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function pt(node: Node | null, off: number) {
  if (!node) return null;
  const el = node.nodeType === 3 ? node.parentElement : (node as HTMLElement);
  const span = el?.closest("[data-off]") as HTMLElement | null;
  const tx = el?.closest("[data-line]") as HTMLElement | null;
  if (!tx) return null;
  return { line: Number(tx.dataset.line), off: span && node.nodeType === 3 ? Number(span.dataset.off) + off : 0 };
}
