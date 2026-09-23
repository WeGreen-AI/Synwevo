import { useEffect, useRef } from "react";
import type { Mark, Selection } from "../lib/types";
import { hexA } from "./GridView";

const EMU_PX = 1 / 9525;

export default function SlidesView({ data, marks, selection, onSelect, side, zoom = 1, slotNames = {}, focusKey, onMarkClick }: {
  data: any; marks: Mark[]; selection: Selection | null; onSelect: (s: Selection | null) => void; side: "src" | "tpl"; zoom?: number;
  slotNames?: Record<string, { name: string; color: string; no?: number }>; focusKey?: string | null; onMarkClick?: (id: string) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusKey) return;
    const el = ref.current?.querySelector(`[data-mark="${focusKey}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusKey]);
  const [cx, cy] = data.size;
  const scale = Math.min(1, 860 / (cx * EMU_PX)) * zoom;
  const onUp = (e: React.MouseEvent) => {
    const s = window.getSelection();
    const target = e.target as HTMLElement;
    const td = target.closest("td[data-shape]") as HTMLElement | null;
    if (s && !s.isCollapsed) {
      const a = pt(s.anchorNode, s.anchorOffset), f = pt(s.focusNode, s.focusOffset);
      if (a && f && a.key === f.key) {
        const start = Math.min(a.off, f.off), end = Math.max(a.off, f.off);
        onSelect({ kind: "pptx_text", slide: a.slide, shape: a.shape, para: a.para, start, end, text: a.text.slice(start, end) });
        return;
      }
    }
    if (td) {
      onSelect({ kind: "pptx_cell", slide: Number(td.dataset.slide), shape: td.dataset.shape!, row: Number(td.dataset.row), col: Number(td.dataset.col) });
    }
  };
  return (
    <div className="slides-scroll" ref={ref} onMouseUp={onUp}>
      {data.slides.map((s: any) => (
        <div key={s.index}>
          <div className="slide-label" style={{ width: cx * EMU_PX * scale }}>第 {s.index + 1} 张幻灯片</div>
          <div className="slide" style={{ width: cx * EMU_PX * scale, height: cy * EMU_PX * scale }}>
            {s.shapes.filter((sh: any) => sh.box).map((sh: any) => {
              const [x, y, w, h] = sh.box;
              const shapeMarks = marks.filter((m) => m.pos.kind === "pptx" && (m.pos as any).slide === s.index && String((m.pos as any).shape) === String(sh.id));
              const slotIds: string[] = sh.slots || [];
              const style: React.CSSProperties = { left: x * EMU_PX * scale, top: y * EMU_PX * scale, width: w * EMU_PX * scale, height: h * EMU_PX * scale, fontSize: 14 * scale };
              const sm = shapeMarks[0];
              if (sm) style.outline = `2px solid ${sm.color}`;
              else if (slotIds.length && slotNames[slotIds[0]]) style.outline = `2px dashed ${slotNames[slotIds[0]].color}`;
              return (
                <div key={sh.id} className="shape" style={style} title={sh.name} data-mark={sm ? `${sm.role}:${sm.id}` : undefined}>
                  {sm && sm.no != null && <span className="num" style={{ position: "absolute", right: 0, top: 0, background: sm.color }} onMouseUp={(e) => { e.stopPropagation(); onMarkClick?.(sm.id); }}>{sm.no}</span>}
                  {slotIds.map((sid) => slotNames[sid] && (
                    <span key={sid} className="fldchip" style={{ background: slotNames[sid].color, position: "absolute", left: 0, top: -1 }} onMouseUp={(e) => { e.stopPropagation(); onSelect({ kind: "slot", slotId: sid }); onMarkClick?.(sid); }}>{slotNames[sid].name}</span>
                  ))}
                  {sh.kind === "picture" && sh.img && <img src={sh.img} style={{ width: "100%", height: "100%", objectFit: "contain" }} alt={sh.name} />}
                  {sh.kind === "text" && sh.paras.map((p: any, pi: number) => (
                    <p key={pi} className="sp-p" style={{ textAlign: p.align }} data-slide={s.index} data-shape={sh.id} data-para={pi} data-text={p.text}>
                      {p.runs.map((r: any, ri: number) => (
                        <span key={ri} data-off={r.off} style={{ fontWeight: r.b ? 700 : undefined, fontStyle: r.i ? "italic" : undefined, color: r.color, fontSize: r.sz ? r.sz * scale * 1.33 : undefined }}>{r.text}</span>
                      ))}
                      {!p.runs.length && " "}
                    </p>
                  ))}
                  {sh.kind === "table" && (
                    <table><tbody>{sh.rows.map((row: any[], ri: number) => (
                      <tr key={ri}>{row.map((c: any, ci: number) => c.hmerge || c.vmerge ? null : (
                        <td key={ci} colSpan={c.span > 1 ? c.span : undefined} rowSpan={c.rspan > 1 ? c.rspan : undefined} data-slide={s.index} data-shape={sh.id} data-row={ri} data-col={ci}
                          style={{ background: selection?.kind === "pptx_cell" && selection.shape === String(sh.id) && selection.row === ri && selection.col === ci ? hexA("#2563eb", 0.15) : undefined }}>
                          {side === "tpl" && ci === 0 && <span className="btn xs" style={{ float: "left", marginRight: 2 }} title="选择整行" onMouseUp={(e) => { e.stopPropagation(); onSelect({ kind: "pptx_row", slide: s.index, shape: String(sh.id), row: ri }); }}>≡</span>}
                          {c.paras.map((p: any) => p.text).join("\n")}
                        </td>))}</tr>))}</tbody></table>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function pt(node: Node | null, off: number) {
  if (!node) return null;
  const el = node.nodeType === 3 ? node.parentElement : (node as HTMLElement);
  const p = el?.closest("p[data-shape]") as HTMLElement | null;
  const span = el?.closest("[data-off]") as HTMLElement | null;
  if (!p) return null;
  return { key: `${p.dataset.slide}/${p.dataset.shape}/${p.dataset.para}`, slide: Number(p.dataset.slide), shape: p.dataset.shape!, para: Number(p.dataset.para),
    text: p.dataset.text || "", off: span && node.nodeType === 3 ? Number(span.dataset.off) + off : 0 };
}
