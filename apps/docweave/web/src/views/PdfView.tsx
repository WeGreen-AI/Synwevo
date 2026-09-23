import { useEffect, useRef, useState } from "react";
import { get } from "../lib/api";
import type { Mark } from "../lib/types";
import { hexA } from "./GridView";
import { useFit } from "../lib/useFit";

const PT = 96 / 72;

interface Page { index: number; w: number; h: number; rotation: number; kind: string }

interface Props {
  pages: Page[];
  imageUrl: (page: number) => string;
  wordsUrl?: (page: number) => string;
  marks: Mark[];
  selection: { page: number; nrect: number[] } | null;
  onSelect: (s: { kind: "pdf_region"; page: number; nrect: number[] } | null) => void;
  onMarkClick?: (id: string) => void;
  zoom: number;
  focusKey?: string | null;
  reloadKey?: string;
  page: number;
  onPage: (n: number) => void;
}

export default function PdfView({ pages, imageUrl, wordsUrl, marks, selection, onSelect, onMarkClick, zoom, focusKey, reloadKey, page, onPage }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [words, setWords] = useState<Record<number, any>>({});
  const [draw, setDraw] = useState<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);
  const [showWords, setShowWords] = useState(true);
  const fit = useFit(scrollRef, Math.max(...pages.map((p) => p.w), 1) * PT, 40);

  useEffect(() => setWords({}), [reloadKey]);

  useEffect(() => {
    if (!wordsUrl) return;
    for (const p of [page - 1, page, page + 1]) {
      if (p < 0 || p >= pages.length || words[p]) continue;
      get(wordsUrl(p)).then((w) => setWords((x) => ({ ...x, [p]: w }))).catch(() => setWords((x) => ({ ...x, [p]: { words: [] } })));
    }
  }, [page, wordsUrl, pages.length, reloadKey, words]);

  // jump to page
  useEffect(() => {
    const el = scrollRef.current?.querySelector(`[data-page="${page}"]`) as HTMLElement | null;
    if (el && scrollRef.current) {
      const top = el.offsetTop - 8;
      if (Math.abs(scrollRef.current.scrollTop - top) > el.offsetHeight / 2) scrollRef.current.scrollTo({ top, behavior: "smooth" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  useEffect(() => {
    if (!focusKey || !scrollRef.current) return;
    const el = scrollRef.current.querySelector(`[data-mark="${focusKey}"]`) as HTMLElement | null;
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1900);
    }
  }, [focusKey]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const kids = Array.from(el.querySelectorAll<HTMLElement>("[data-page]"));
    const mid = el.scrollTop + el.clientHeight / 3;
    let cur = 0;
    for (const k of kids) if (k.offsetTop <= mid) cur = Number(k.dataset.page);
    if (cur !== page) onPage(cur);
  };

  const rel = (e: React.MouseEvent, box: HTMLElement) => {
    const r = box.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };

  return (
    <div className="pdf-scroll" ref={scrollRef} onScroll={onScroll}>
      {pages.map((p) => {
        const w = p.w * PT * zoom * fit, h = p.h * PT * zoom * fit;
        const pm = marks.filter((m) => m.pos.kind === "rect" && (m.pos as any).page === p.index);
        const wd = words[p.index];
        return (
          <div key={p.index} data-page={p.index} className="pdf-page" style={{ width: w, height: h }}>
            <img src={imageUrl(p.index)} loading="lazy" alt={`第 ${p.index + 1} 页`} />
            <div className="pdf-layer"
              onMouseDown={(e) => {
                if (e.button !== 0) return;
                const pt = rel(e, e.currentTarget as HTMLElement);
                setDraw({ page: p.index, x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y });
              }}
              onMouseMove={(e) => {
                if (!draw || draw.page !== p.index) return;
                const pt = rel(e, e.currentTarget as HTMLElement);
                setDraw({ ...draw, x1: pt.x, y1: pt.y });
              }}
              onMouseUp={(e) => {
                if (!draw) return;
                const d = draw;
                setDraw(null);
                const n = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
                if ((n[2] - n[0]) * w < 4 || (n[3] - n[1]) * h < 4) {
                  // click: select the word under the pointer (pad slightly)
                  const pt = rel(e, e.currentTarget as HTMLElement);
                  const hit = (wd?.words || []).find((x: any) => pt.x >= x.r[0] && pt.x <= x.r[2] && pt.y >= x.r[1] && pt.y <= x.r[3]);
                  if (hit) {
                    const px = 2 / w, py = 2 / h;
                    onSelect({ kind: "pdf_region", page: p.index, nrect: [hit.r[0] - px, hit.r[1] - py, hit.r[2] + px, hit.r[3] + py] });
                  } else onSelect(null);
                  return;
                }
                onSelect({ kind: "pdf_region", page: p.index, nrect: n });
              }}>
              {showWords && (wd?.words || []).map((x: any, i: number) => (
                <div key={i} className={`pdf-word hover${x.conf !== undefined && x.conf < 80 ? " lowconf" : ""}`} title={x.conf !== undefined ? `${x.t}（置信度 ${Math.round(x.conf)}）` : x.t}
                  style={{ left: `${x.r[0] * 100}%`, top: `${x.r[1] * 100}%`, width: `${(x.r[2] - x.r[0]) * 100}%`, height: `${(x.r[3] - x.r[1]) * 100}%` }} />
              ))}
              {pm.map((m) => (m.pos as any).grid?.map((g: any, gi: number) => (
                <div key={`${m.role}${m.id}g${gi}`} className={`pdf-grid ${g.kind}`} style={{ left: `${g.r[0] * 100}%`, top: `${g.r[1] * 100}%`,
                  width: g.kind === "col" && g.r[2] - g.r[0] < 0.002 ? 0 : `${(g.r[2] - g.r[0]) * 100}%`, height: g.kind === "row" && g.r[3] - g.r[1] < 0.002 ? 0 : `${(g.r[3] - g.r[1]) * 100}%`,
                  borderColor: m.color }} />
              )))}
              {pm.map((m) => {
                const r = (m.pos as any).nrect;
                return (
                  <div key={m.role + m.id} className="pdf-box" data-mark={`${m.role}:${m.id}`}
                    style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${(r[2] - r[0]) * 100}%`, height: `${(r[3] - r[1]) * 100}%`,
                      borderColor: m.color, background: hexA(m.color, m.active ? 0.22 : 0.1), borderStyle: m.role === "slot" ? "dashed" : "solid" }}>
                    <span className="tag num" style={{ background: m.color }} title={m.label} onMouseDown={(e) => e.stopPropagation()} onMouseUp={(e) => { e.stopPropagation(); onMarkClick?.(m.id); }}>
                      {m.no ?? "✎"}
                    </span>
                  </div>
                );
              })}
              {selection && selection.page === p.index && (
                <div className="pdf-draw" style={{ left: `${selection.nrect[0] * 100}%`, top: `${selection.nrect[1] * 100}%`, width: `${(selection.nrect[2] - selection.nrect[0]) * 100}%`, height: `${(selection.nrect[3] - selection.nrect[1]) * 100}%` }} />
              )}
              {draw && draw.page === p.index && (
                <div className="pdf-draw" style={{ left: `${Math.min(draw.x0, draw.x1) * 100}%`, top: `${Math.min(draw.y0, draw.y1) * 100}%`, width: `${Math.abs(draw.x1 - draw.x0) * 100}%`, height: `${Math.abs(draw.y1 - draw.y0) * 100}%` }} />
              )}
            </div>
            <div style={{ position: "absolute", right: 6, bottom: 4, fontSize: 11, color: "#666", background: "rgba(255,255,255,.8)", padding: "0 4px", borderRadius: 3 }}>
              {p.index + 1} / {pages.length}{p.rotation ? ` · 旋转 ${p.rotation}°` : ""}{p.kind === "scanned" ? " · 扫描页" : ""}
              <label style={{ marginLeft: 6 }}><input type="checkbox" checked={showWords} onChange={(e) => setShowWords(e.target.checked)} style={{ verticalAlign: -2 }} /> 文字层</label>
            </div>
          </div>
        );
      })}
    </div>
  );
}
