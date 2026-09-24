import { formatNumber } from "@synwevo/ui";
import { ApiImage } from "../components/ApiResource";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useRef, useState } from "react";
import { get } from "@synwevo/auth-client";
import type { Mark } from "../lib/types";
import { markColor } from "../lib/css";
import { hexA } from "./GridView";
import { ScrollEcho } from "../lib/scrollecho";
import { isImeKey, scrollBehavior, useFit } from "@synwevo/ui";

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
  /** False for read-only surfaces (the preview): no region drawing, no numeric region form. */
  selectable?: boolean;
}

export default function PdfView({ pages, imageUrl, wordsUrl, marks, selection, onSelect, onMarkClick, zoom, focusKey, reloadKey, page, onPage, selectable = true }: Props) {
  useI18n();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [words, setWords] = useState<Record<number, any>>({});
  const [draw, setDrawState] = useState<{ page: number; x0: number; y0: number; x1: number; y1: number } | null>(null);
  const drawRef = useRef(draw);
  const setDraw = (d: typeof draw) => { drawRef.current = d; setDrawState(d); };
  const [showWords, setShowWords] = useState(true);
  const [numeric, setNumeric] = useState(false);
  const fit = useFit(scrollRef, Math.max(...pages.map((p) => p.w), 1) * PT, 40);
  // The document the word layers belong to. A words request for the previous file or preview
  // record that answers after the switch is dropped instead of merged into the new document.
  const docGen = useRef(0);
  // The page number this view last reported from its own scrolling, recognised once when the
  // parent echoes it back (see lib/scrollecho.ts).
  const echo = useRef(new ScrollEcho()).current;

  useEffect(() => { docGen.current++; setWords({}); }, [reloadKey]);

  useEffect(() => {
    if (!wordsUrl) return;
    const g = docGen.current;
    for (const p of [page - 1, page, page + 1]) {
      if (p < 0 || p >= pages.length || words[p]) continue;
      get(wordsUrl(p))
        .then((w) => { if (g === docGen.current) setWords((x) => ({ ...x, [p]: w })); })
        .catch(() => { if (g === docGen.current) setWords((x) => ({ ...x, [p]: { words: [] } })); });
    }
  }, [page, wordsUrl, pages.length, reloadKey, words]);

  // jump to page — only for page changes that did not come from this view's own scrolling
  useEffect(() => {
    if (!echo.shouldJump(page)) return;
    const el = scrollRef.current?.querySelector(`[data-page="${page}"]`) as HTMLElement | null;
    if (el && scrollRef.current) {
      const top = el.offsetTop - 8;
      if (Math.abs(scrollRef.current.scrollTop - top) > el.offsetHeight / 2) scrollRef.current.scrollTo({ top, behavior: scrollBehavior() });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  useEffect(() => {
    if (!focusKey || !scrollRef.current) return;
    const el = scrollRef.current.querySelector(`[data-mark="${focusKey}"]`) as HTMLElement | null;
    if (el) {
      el.scrollIntoView({ block: "center", behavior: scrollBehavior() });
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
    if (cur !== page) {
      echo.reported(cur);
      onPage(cur);
    }
  };

  /** End a drag-drawn region on the page where it started, with the last position seen there.
   *  Releasing over another page used to create the region on that page with the first page's
   *  coordinates; releasing outside every page left the drawing attached to the pointer. */
  const finish = (clickHit?: { page: number; nrect: number[] } | null) => {
    const d = drawRef.current;
    if (!d) return;
    setDraw(null);
    if (clickHit !== undefined) { onSelect(clickHit ? { kind: "pdf_region", ...clickHit } : null); return; }
    const pg = pages.find((x) => x.index === d.page);
    const w = (pg?.w || 1) * PT * zoom * fit, h = (pg?.h || 1) * PT * zoom * fit;
    const n = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
    if ((n[2] - n[0]) * w < 4 || (n[3] - n[1]) * h < 4) return;
    onSelect({ kind: "pdf_region", page: d.page, nrect: n });
  };
  useEffect(() => {
    if (!draw) return;
    const up = () => finish();
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [!!draw]);

  const rel = (e: React.MouseEvent, box: HTMLElement) => {
    const r = box.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };

  return (
    <div className="pdf-scroll" ref={scrollRef} onScroll={onScroll}>
      {/* Keyboard and single-pointer alternative to drawing a box with the mouse (WCAG 2.1.1,
          2.5.7): the region is typed in as percentages of the page. */}
      {selectable && <div className="pdf-numeric">
        <button type="button" className="btn xs" aria-expanded={numeric} onClick={() => setNumeric(!numeric)}>
          {numeric ? tr("收起数值框选") : tr("用数值框选区域…")}
        </button>
        {numeric && <NumericRegion pages={pages.length} page={selection?.page ?? page} nrect={selection?.nrect}
          onApply={(pg, n) => { onPage(pg); onSelect({ kind: "pdf_region", page: pg, nrect: n }); }} />}
      </div>}
      {pages.map((p) => {
        const w = p.w * PT * zoom * fit, h = p.h * PT * zoom * fit;
        const pm = marks.filter((m) => m.pos.kind === "rect" && (m.pos as any).page === p.index);
        const wd = words[p.index];
        return (
          <div key={p.index} data-page={p.index} className="pdf-page" style={{ width: w, height: h }}>
            <ApiImage src={imageUrl(p.index)} loading="lazy" alt={tr("第 {v1} 页", { v1: p.index + 1 })} />
            <div className="pdf-layer"
              onMouseDown={(e) => {
                if (e.button !== 0 || !selectable) return;
                const pt = rel(e, e.currentTarget as HTMLElement);
                setDraw({ page: p.index, x0: pt.x, y0: pt.y, x1: pt.x, y1: pt.y });
              }}
              onMouseMove={(e) => {
                if (!draw || draw.page !== p.index) return;
                const pt = rel(e, e.currentTarget as HTMLElement);
                setDraw({ ...draw, x1: pt.x, y1: pt.y });
              }}
              onMouseUp={(e) => {
                const d = drawRef.current;
                if (!d) return;
                if (d.page !== p.index) { finish(); return; }       // released over another page
                const n = [Math.min(d.x0, d.x1), Math.min(d.y0, d.y1), Math.max(d.x0, d.x1), Math.max(d.y0, d.y1)];
                if ((n[2] - n[0]) * w < 4 || (n[3] - n[1]) * h < 4) {
                  // click: select the word under the pointer (pad slightly)
                  const pt = rel(e, e.currentTarget as HTMLElement);
                  const hit = (wd?.words || []).find((x: any) => pt.x >= x.r[0] && pt.x <= x.r[2] && pt.y >= x.r[1] && pt.y <= x.r[3]);
                  const px = 2 / w, py = 2 / h;
                  finish(hit ? { page: p.index, nrect: [hit.r[0] - px, hit.r[1] - py, hit.r[2] + px, hit.r[3] + py] } : null);
                  return;
                }
                finish();
              }}>
              {showWords && (wd?.words || []).map((x: any, i: number) => (
                <div key={i} className={`pdf-word hover${x.conf !== undefined && x.conf < 80 ? " lowconf" : ""}`} title={x.conf !== undefined ? tr("{v1}（置信度 {v2}）", { v1: x.t, v2: Math.round(x.conf) }) : x.t}
                  style={{ left: `${x.r[0] * 100}%`, top: `${x.r[1] * 100}%`, width: `${(x.r[2] - x.r[0]) * 100}%`, height: `${(x.r[3] - x.r[1]) * 100}%` }} />
              ))}
              {pm.map((m) => (m.pos as any).grid?.map((g: any, gi: number) => (
                <div key={`${m.role}${m.id}g${gi}`} className={`pdf-grid ${g.kind}`} style={{ left: `${g.r[0] * 100}%`, top: `${g.r[1] * 100}%`,
                  width: g.kind === "col" && g.r[2] - g.r[0] < 0.002 ? 0 : `${(g.r[2] - g.r[0]) * 100}%`, height: g.kind === "row" && g.r[3] - g.r[1] < 0.002 ? 0 : `${(g.r[3] - g.r[1]) * 100}%`,
                  borderColor: markColor(m.color) }} />
              )))}
              {pm.map((m) => {
                const r = (m.pos as any).nrect;
                return (
                  <div key={m.role + m.id} className="pdf-box" data-mark={`${m.role}:${m.id}`}
                    style={{ left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${(r[2] - r[0]) * 100}%`, height: `${(r[3] - r[1]) * 100}%`,
                      borderColor: markColor(m.color), background: hexA(markColor(m.color), m.active ? 0.22 : 0.1), borderStyle: m.role === "slot" ? "dashed" : "solid" }}>
                    <span className="tag num" style={{ background: markColor(m.color) }} title={m.label} onMouseDown={(e) => e.stopPropagation()} onMouseUp={(e) => { e.stopPropagation(); onMarkClick?.(m.id); }}>
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
              {formatNumber(p.index + 1)} / {formatNumber(pages.length)}{p.rotation ? tr(" · 旋转 {v1}°", { v1: p.rotation }) : ""}{p.kind === "scanned" ? tr(" · 扫描页") : ""}
              <label style={{ marginLeft: 6 }}><input type="checkbox" checked={showWords} onChange={(e) => setShowWords(e.target.checked)} style={{ verticalAlign: -2 }} />{tr(" 文字层")}</label>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** Type a region instead of drawing it: page, then left / top / right / bottom in % of the page. */
function NumericRegion({ pages, page, nrect, onApply }: { pages: number; page: number; nrect?: number[]; onApply: (page: number, n: number[]) => void }) {
  useI18n();
  const pct = (x: number) => Math.round(x * 1000) / 10;
  const [pg, setPg] = useState(page + 1);
  const [v, setV] = useState<number[]>(nrect ? nrect.map(pct) : [10, 10, 50, 20]);
  useEffect(() => { setPg(page + 1); if (nrect) setV(nrect.map(pct)); }, [page, nrect?.join(",")]);
  const ok = pg >= 1 && pg <= pages && v.every((x) => x >= 0 && x <= 100) && v[2] > v[0] && v[3] > v[1];
  const apply = () => { if (ok) onApply(pg - 1, v.map((x) => x / 100)); };
  const names = [tr("左"), tr("上"), tr("右"), tr("下")];
  return (
    <div className="row gap4 wrap small" role="group" aria-label={tr("用数值框选区域")}
      onKeyDown={(e) => { if (!isImeKey(e) && e.key === "Enter") { e.preventDefault(); apply(); } }}>
      <label className="row gap4">{tr("页")}<input className="input sm w64" type="number" min={1} max={pages} value={pg} onChange={(e) => setPg(Number(e.target.value))} /></label>
      {names.map((n, i) => (
        <label key={n} className="row gap4">{n}%<input className="input sm w64" type="number" min={0} max={100} step={0.5} value={v[i]}
          onChange={(e) => setV(v.map((x, k) => (k === i ? Number(e.target.value) : x)))} /></label>
      ))}
      <button type="button" className="btn xs primary" disabled={!ok} onClick={apply}>{tr("选中此区域")}</button>
      {!ok && <span className="tiny" style={{ color: "var(--err)" }}>{tr("右须大于左、下须大于上，数值 0–100")}</span>}
    </div>
  );
}
