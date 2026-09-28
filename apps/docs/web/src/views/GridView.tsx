import { formatNumber } from "@synwevo/ui";
import { serverText, issueText } from "../lib/presentation";
import { errorText } from "../lib/perm";
import { t as tr, useI18n } from "@synwevo/ui";
import { ReactElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Mark } from "../lib/types";
import { cssColor, markColor } from "../lib/css";
import { cornersOf, moveCursor, type Cell } from "../lib/gridsel";
import { cellRef, colLetter, isImeKey, rangeRef, scrollBehavior } from "@synwevo/ui";

export interface GridWindow {
  sheet: string;
  offset: number;
  total_rows: number;
  total_cols: number;
  cols_shown: number;
  rows: { v: string; s?: number; f?: number; fx?: string; t?: string; nf?: string }[][];
  widths: { w: number; hidden?: boolean }[];
  heights: Record<string, { h: number; hidden?: boolean }>;
  merges: number[][];
  styles: any[];
  sheets: { name: string; rows: number; cols: number; hidden?: boolean }[];
  notes?: string[];
}

/** `rows`: whole rows picked from the row numbers (Shift+click extends them) */
export interface CellSel { sheet: string; r1: number; c1: number; r2: number; c2: number; rows?: boolean }

const ROW_H = 22;
const HEAD_H = 22;
const NUM_W = 46;
const WIN = 200;

interface Props {
  load: (sheet: string | null, offset: number, limit: number) => Promise<GridWindow>;
  reloadKey: string;
  marks: Mark[];
  selection: CellSel | null;
  onSelect: (s: CellSel | null) => void;
  onMarkClick?: (id: string) => void;
  headerRow?: { sheet: string; row: number } | null;
  focus?: { sheet: string; r: number; c: number; key: number } | null;
  sheet: string | null;
  onSheet: (s: string) => void;
  onInfo?: (info: { sheets: GridWindow["sheets"]; sheet: string; totalRows: number; totalCols: number; notes?: string[] }) => void;
  zoom?: number;
  /** False for read-only surfaces (the preview): arrow keys scroll instead of selecting. */
  selectable?: boolean;
}

export default function GridView({ load, reloadKey, marks, selection, onSelect, onMarkClick, headerRow, focus, sheet, onSheet, onInfo, zoom = 1, selectable = true }: Props) {
  useI18n();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [wins, setWins] = useState<Record<number, GridWindow>>({});
  const [meta, setMeta] = useState<GridWindow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [scroll, setScroll] = useState({ top: 0, left: 0, w: 800, h: 600 });
  const loading = useRef<Set<number>>(new Set());
  const drag = useRef<{ r: number; c: number } | null>(null);
  const [live, setLiveState] = useState<CellSel | null>(null);
  const liveRef = useRef<CellSel | null>(null);
  const setLive = (v: CellSel | null) => {
    liveRef.current = v;
    setLiveState(v);
  };
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  // Which file / sheet / record the windows in `wins` belong to. Every window request carries the
  // generation it was made for; an answer for an earlier one is dropped. The grid is not remounted
  // when the workspace switches files or the preview switches records, so without this a slow
  // window of the previous document merged into the new one — rows 200+ showing the old values,
  // never refetched, and mapped as if they were the new file's.
  const gen = useRef(0);
  // keyboard selection: the fixed corner and the moving one (see lib/gridsel.ts)
  const kb = useRef<{ anchor: Cell; active: Cell; key: string } | null>(null);

  // reset on file / sheet change.  The first load is made with sheet=null and the
  // server answers which sheet that was; telling the parent must not re-fetch the
  // window we already hold (a 20k×30 workbook paid ~300 ms and 133 KB for that).
  const loadedRef = useRef("");
  useEffect(() => {
    if (sheet && loadedRef.current === `${reloadKey}::${sheet}`) return;
    const g = ++gen.current;
    setWins({});
    setMeta(null);
    setErr(null);
    loading.current = new Set();
    kb.current = null;
    let alive = true;
    load(sheet, 0, WIN)
      .then((w) => {
        if (!alive || g !== gen.current) return;
        loadedRef.current = `${reloadKey}::${w.sheet}`;
        setMeta(w);
        setWins({ 0: w });
        onInfo?.({ sheets: w.sheets, sheet: w.sheet, totalRows: w.total_rows, totalCols: w.total_cols, notes: w.notes });
        if (!sheet) onSheet(w.sheet);
      })
      .catch((e) => alive && setErr(errorText(e)));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey, sheet]);

  const colW = useMemo(() => {
    if (!meta) return [] as number[];
    const n = meta.cols_shown;
    if (meta.widths && meta.widths.length) return meta.widths.map((w) => (w.hidden ? 0 : Math.max(24, w.w)));
    const base = wins[0] || meta;
    const out: number[] = [];
    for (let c = 0; c < n; c++) {
      let mx = 4;
      for (let r = 0; r < Math.min(base.rows.length, 60); r++) {
        const v = base.rows[r]?.[c]?.v || "";
        let len = 0;
        for (const ch of v.slice(0, 40)) len += ch.charCodeAt(0) > 255 ? 2 : 1;
        mx = Math.max(mx, len);
      }
      out.push(Math.min(280, Math.max(56, mx * 7 + 14)));
    }
    return out;
  }, [meta, wins[0]]);

  const colX = useMemo(() => {
    const xs = [0];
    for (const w of colW) xs.push(xs[xs.length - 1] + w);
    return xs;
  }, [colW]);

  const customH = useMemo(() => {
    const m: Record<number, number> = {};
    for (const w of Object.values(wins)) for (const [k, v] of Object.entries(w.heights || {})) m[Number(k)] = v.hidden ? 0 : Math.max(12, v.h);
    return m;
  }, [wins]);
  const customKeys = useMemo(() => Object.keys(customH).map(Number).sort((a, b) => a - b), [customH]);
  const rowTop = useCallback(
    (r: number) => {
      let y = r * ROW_H;
      for (const k of customKeys) {
        if (k >= r) break;
        y += customH[k] - ROW_H;
      }
      return y;
    },
    [customKeys, customH],
  );
  const rowH = useCallback((r: number) => (r in customH ? customH[r] : ROW_H), [customH]);
  const totalRows = meta?.total_rows || 0;
  const totalH = rowTop(totalRows);
  const totalW = colX[colX.length - 1] || 0;

  const rowAt = useCallback(
    (y: number) => {
      // binary search on rowTop
      let lo = 0,
        hi = Math.max(0, totalRows - 1);
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (rowTop(mid) <= y) lo = mid;
        else hi = mid - 1;
      }
      return lo;
    },
    [rowTop, totalRows],
  );
  const colAt = useCallback(
    (x: number) => {
      let c = 0;
      while (c < colW.length - 1 && colX[c + 1] <= x) c++;
      return c;
    },
    [colX, colW],
  );

  const first = rowAt(Math.max(0, scroll.top / zoom - HEAD_H));
  const last = Math.min(totalRows - 1, rowAt((scroll.top + scroll.h) / zoom) + 2);

  // lazy window loading
  useEffect(() => {
    if (!meta) return;
    const need = new Set<number>();
    for (let r = first; r <= last; r += WIN) need.add(Math.floor(r / WIN) * WIN);
    need.add(Math.floor(last / WIN) * WIN);
    const g = gen.current;
    const pending = loading.current;       // the set of this generation; a reset replaces it
    for (const off of need) {
      if (wins[off] || pending.has(off) || off >= totalRows) continue;
      pending.add(off);
      load(meta.sheet, off, WIN)
        .then((w) => {
          if (g !== gen.current || (w.sheet && w.sheet !== meta.sheet)) return;
          setWins((x) => ({ ...x, [off]: w }));
        })
        .catch((e) => { if (g === gen.current) setErr(errorText(e)); })
        .finally(() => pending.delete(off));
    }
  }, [first, last, meta, wins, load, totalRows]);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const upd = () => setScroll({ top: el.scrollTop, left: el.scrollLeft, w: el.clientWidth, h: el.clientHeight });
    upd();
    const ro = new ResizeObserver(upd);
    ro.observe(el);
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(upd);
    };
    el.addEventListener("scroll", onScroll);
    return () => {
      ro.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, [meta]);

  // focus requests (定位)
  useEffect(() => {
    if (!focus || !meta || focus.sheet !== meta.sheet) return;
    const el = scrollRef.current;
    if (!el) return;
    const y = rowTop(focus.r) * zoom;
    const x = (colX[focus.c] || 0) * zoom;
    el.scrollTo({ top: Math.max(0, y - el.clientHeight / 3), left: Math.max(0, x - 80), behavior: scrollBehavior() });
  }, [focus?.key, meta?.sheet]);

  const cellValue = (r: number, c: number) => {
    const off = Math.floor(r / WIN) * WIN;
    const w = wins[off];
    if (!w) return undefined;
    return w.rows[r - off]?.[c];
  };

  const merges = meta?.merges || [];
  const mergeAnchor = useMemo(() => {
    const cover = new Map<string, number[]>();
    for (const m of merges) for (let r = m[0]; r <= m[2]; r++) for (let c = m[1]; c <= m[3]; c++) cover.set(`${r},${c}`, m);
    return cover;
  }, [merges]);

  const visibleCols: number[] = [];
  if (colW.length) {
    const c0 = colAt(Math.max(0, scroll.left / zoom - NUM_W));
    const c1 = colAt((scroll.left + scroll.w) / zoom);
    for (let c = c0; c <= Math.min(colW.length - 1, c1 + 1); c++) visibleCols.push(c);
  }

  const posFromEvent = (e: React.MouseEvent) => {
    const el = scrollRef.current!;
    const rect = el.getBoundingClientRect();
    const x = (e.clientX - rect.left + el.scrollLeft) / zoom - NUM_W;
    const y = (e.clientY - rect.top + el.scrollTop) / zoom - HEAD_H;
    return { r: Math.max(0, Math.min(totalRows - 1, rowAt(Math.max(0, y)))), c: Math.max(0, colAt(Math.max(0, x))) };
  };

  const onDown = (e: React.MouseEvent) => {
    if (!selectable || !meta || e.button !== 0) return;
    scrollRef.current?.focus({ preventScroll: true });
    const p = posFromEvent(e);
    if (e.shiftKey && selection && selection.sheet === meta.sheet) {
      const s = norm({ sheet: meta.sheet, r1: selection.r1, c1: selection.c1, r2: p.r, c2: p.c });
      onSelect(s);
      return;
    }
    drag.current = p;
    setLive(expandMerge(norm({ sheet: meta.sheet, r1: p.r, c1: p.c, r2: p.r, c2: p.c }), merges));
  };
  const onMove = (e: React.MouseEvent) => {
    if (!selectable || !drag.current || !meta) return;
    const p = posFromEvent(e);
    setLive(expandMerge(norm({ sheet: meta.sheet, r1: drag.current.r, c1: drag.current.c, r2: p.r, c2: p.c }), merges));
  };
  useEffect(() => {
    const up = () => {
      if (!drag.current) return;
      const v = liveRef.current;
      drag.current = null;
      setLive(null);
      if (v) onSelectRef.current(v);
    };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, []);

  if (err) return <div className="empty"><div className="big">{tr("无法显示表格")}</div><div>{serverText(err)}</div></div>;
  if (!meta) return <div className="empty">{tr("加载中…")}</div>;

  const sel = live || (selection && selection.sheet === meta.sheet ? selection : null);
  const box = (r1: number, c1: number, r2: number, c2: number) => ({
    left: NUM_W + colX[c1],
    top: HEAD_H + rowTop(r1),
    width: colX[Math.min(c2 + 1, colX.length - 1)] - colX[c1],
    height: rowTop(r2 + 1) - rowTop(r1),
  });

  const cells: ReactElement[] = [];
  const drawn = new Set<string>();
  for (let r = first; r <= last; r++) {
    const h = rowH(r);
    if (h === 0) continue;
    for (const c of visibleCols) {
      if (colW[c] === 0) continue;
      const m = mergeAnchor.get(`${r},${c}`);
      let rr = r,
        cc = c,
        r2 = r,
        c2 = c;
      if (m) {
        rr = m[0];
        cc = m[1];
        r2 = m[2];
        c2 = m[3];
        const k = `${rr},${cc}`;
        if (drawn.has(k)) continue;
        drawn.add(k);
      }
      const v = cellValue(rr, cc);
      const st = v?.s !== undefined ? winsStyle(wins, rr, v.s) : null;
      const b = box(rr, cc, r2, c2);
      const style: React.CSSProperties = { ...b };
      let cls = "gcell";
      if (st) {
        if (st.b) style.fontWeight = 700;
        if (st.i) style.fontStyle = "italic";
        if (st.u) style.textDecoration = "underline";
        // cell colours come from the uploaded workbook: plain colours only (lib/css.ts)
        if (st.color) style.color = cssColor(st.color);
        if (st.bg) style.background = cssColor(st.bg);
        if (st.sz) style.fontSize = Math.max(9, Math.min(22, st.sz * 1.1));
        if (st.ha === "center" || st.ha === "centerContinuous") style.textAlign = "center";
        if (st.ha === "right") style.textAlign = "right";
        if (st.wrap) cls += " wrap";
        if (st.bd) {
          if (st.bd.l) style.borderLeft = "1px solid #444";
          if (st.bd.r) style.borderRight = "1px solid #444";
          if (st.bd.t) style.borderTop = "1px solid #444";
          if (st.bd.b) style.borderBottom = "1px solid #444";
        }
        if (st.va === "center") {
          style.display = "flex";
          style.alignItems = "center";
          style.justifyContent = style.textAlign === "center" ? "center" : style.textAlign === "right" ? "flex-end" : "flex-start";
        }
      }
      if (v?.f) cls += " formula";
      else if (v?.t && ["int", "dec", "float"].includes(v.t) && !st?.ha) cls += " gnum";
      cells.push(
        <div key={`${rr}:${cc}`} className={cls} style={style} title={v?.fx ? tr("公式 {v1}", { v1: v.fx }) : v?.v && v.v.length > 12 ? v.v : undefined}>
          {v?.fx && !v.f && <span className="fxdot" aria-hidden="true" />}
          {v === undefined && Math.floor(rr / WIN) * WIN in wins === false ? "" : v?.v}
        </div>,
      );
    }
  }

  // headers
  const heads: ReactElement[] = [];
  const topFix = scroll.top / zoom;
  const leftFix = scroll.left / zoom;
  for (const c of visibleCols) {
    if (colW[c] === 0) continue;
    const on = sel && c >= sel.c1 && c <= sel.c2;
    heads.push(
      <div key={`h${c}`} className={`ghead${on ? " on" : ""}`} style={{ left: NUM_W + colX[c], top: topFix, width: colW[c], height: HEAD_H, cursor: "s-resize" }}
        onMouseDown={(e) => { e.stopPropagation(); if (!selectable) return; scrollRef.current?.focus({ preventScroll: true }); onSelect({ sheet: meta.sheet, r1: 0, c1: c, r2: Math.max(0, totalRows - 1), c2: c }); }}>
        {colLetter(c)}
      </div>,
    );
  }
  for (let r = first; r <= last; r++) {
    const h = rowH(r);
    if (!h) continue;
    const on = sel && r >= sel.r1 && r <= sel.r2;
    heads.push(
      <div key={`r${r}`} className={`ghead${on ? " on" : ""}`} style={{ left: leftFix, top: HEAD_H + rowTop(r), width: NUM_W, height: h, lineHeight: `${h}px`, cursor: "e-resize" }}
        onMouseDown={(e) => {
          e.stopPropagation();
          if (!selectable) return;
          scrollRef.current?.focus({ preventScroll: true });
          const from = e.shiftKey && selection && selection.sheet === meta.sheet ? selection.r1 : r;
          onSelect({ sheet: meta.sheet, r1: Math.min(from, r), c1: 0, r2: Math.max(from, r), c2: Math.max(0, colW.length - 1), rows: true });
        }}>
        {formatNumber(r + 1)}
      </div>,
    );
  }

  const markEls = marks
    .filter((m) => m.pos.kind === "cells" && m.pos.sheet === meta.sheet)
    .map((m) => {
      const p = m.pos as any;
      const b = box(p.r1, p.c1, Math.min(p.r2, totalRows - 1), Math.min(p.c2, colW.length - 1));
      return (
        <div key={m.role + m.id} className={`gmark${m.active ? " active" : ""}${m.dim ? " dim" : ""}`} data-mark={`${m.role}:${m.id}`}
          style={{ ...b, borderColor: markColor(m.color), background: hexA(markColor(m.color), m.role === "slot" ? 0.1 : 0.08), borderStyle: m.role === "slot" ? "dashed" : "solid" }}>
          <button type="button" className="tag num" style={{ background: markColor(m.color), border: 0 }} title={m.label} aria-label={m.label || tr("定位映射")} onMouseDown={(e) => e.stopPropagation()} onClick={(e) => { e.stopPropagation(); onMarkClick?.(m.id); }}>
            {m.no ?? "✎"}
          </button>
        </div>
      );
    });

  /** Keep the active cell on screen after a keyboard move (it used to move off-screen). */
  const reveal = (c: Cell) => {
    const el = scrollRef.current;
    if (!el) return;
    const top = (HEAD_H + rowTop(c.r)) * zoom, bottom = (HEAD_H + rowTop(c.r + 1)) * zoom;
    const left = (NUM_W + (colX[c.c] || 0)) * zoom, right = (NUM_W + (colX[c.c + 1] || 0)) * zoom;
    let y = el.scrollTop, x = el.scrollLeft;
    if (top - HEAD_H * zoom < y) y = top - HEAD_H * zoom;
    else if (bottom > y + el.clientHeight) y = bottom - el.clientHeight;
    if (left - NUM_W * zoom < x) x = left - NUM_W * zoom;
    else if (right > x + el.clientWidth) x = right - el.clientWidth;
    if (y !== el.scrollTop || x !== el.scrollLeft) el.scrollTo({ top: Math.max(0, y), left: Math.max(0, x) });
  };
  const onGridKey = (e: React.KeyboardEvent) => {
    if (isImeKey(e) || !selectable) return;
    const d: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    const k = d[e.key];
    if (!k) return;
    e.preventDefault();
    const cur = selection && selection.sheet === meta.sheet ? selection : null;
    const key = cur ? `${cur.r1},${cur.c1},${cur.r2},${cur.c2}` : "";
    let anchor: Cell, active: Cell;
    if (kb.current && kb.current.key === key) ({ anchor, active } = kb.current);
    else if (cur) ({ anchor, active } = cornersOf(cur));
    else {
      // Nothing selected yet: start on the first visible cell, so the grid can be used without
      // ever touching the mouse. The first arrow press only places the cursor.
      const start = { r: first, c: visibleCols[0] ?? 0 };
      kb.current = { anchor: start, active: start, key: `${start.r},${start.c},${start.r},${start.c}` };
      onSelect({ sheet: meta.sheet, r1: start.r, c1: start.c, r2: start.r, c2: start.c });
      return;
    }
    const m = moveCursor(anchor, active, k[0], k[1], e.shiftKey, totalRows, colW.length);
    const next = { sheet: meta.sheet, ...m.rect };
    kb.current = { anchor: m.anchor, active: m.active, key: `${next.r1},${next.c1},${next.r2},${next.c2}` };
    onSelect(next);
    reveal(m.active);
  };
  const done = selection && selection.sheet === meta.sheet ? selection : null;    // not the live drag
  const selText = done ? tr("已选 {v1}!{v2}", { v1: meta.sheet, v2: rangeRef(done.r1, done.c1, done.r2, done.c2) }) : "";

  return (
    <div className="grid-wrap">
      <div className="sr-only" role="status" aria-live="polite">{selText}</div>
      <div className="grid-scroll" ref={scrollRef} onMouseDown={onDown} onMouseMove={onMove} tabIndex={0}
        aria-label={tr("工作表「{v1}」，{v2} 行 × {v3} 列{v4}", { v1: meta.sheet, v2: totalRows, v3: colW.length, v4: selectable ? tr("。方向键移动选区，Shift+方向键扩展选区") : "" })}
        onKeyDown={onGridKey}>
        <div className="grid-canvas" style={{ width: (NUM_W + totalW) * zoom, height: (HEAD_H + totalH) * zoom }}>
          <div style={{ transform: `scale(${zoom})`, transformOrigin: "0 0", position: "absolute", inset: 0 }}>
            {headerRow && headerRow.sheet === meta.sheet && headerRow.row >= 0 && (
              <div className="gheader-row" style={{ left: NUM_W, top: HEAD_H + rowTop(headerRow.row), width: totalW, height: rowH(headerRow.row) }} title={tr("表头行")} />
            )}
            {cells}
            {markEls}
            {sel && <div className="gsel" style={box(sel.r1, sel.c1, sel.r2, sel.c2)} />}
            {heads}
            <div className="gcorner" style={{ left: leftFix, top: topFix, width: NUM_W, height: HEAD_H }} />
          </div>
        </div>
      </div>
      {meta.sheets.length > 1 && (
        <div className="sheettabs" role="tablist" aria-label={tr("工作表")}>
          {meta.sheets.map((s) => (
            <button type="button" key={s.name} role="tab" aria-selected={s.name === meta.sheet} className={`sheettab${s.name === meta.sheet ? " on" : ""}`} onClick={() => onSheet(s.name)} title={tr("{v1} 行 × {v2} 列", { v1: s.rows, v2: s.cols })}>
              {s.name}{s.hidden ? tr("（隐藏）") : ""}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function winsStyle(wins: Record<number, GridWindow>, r: number, s: number) {
  const off = Math.floor(r / WIN) * WIN;
  return wins[off]?.styles?.[s];
}

function norm(s: CellSel): CellSel {
  return { sheet: s.sheet, r1: Math.min(s.r1, s.r2), c1: Math.min(s.c1, s.c2), r2: Math.max(s.r1, s.r2), c2: Math.max(s.c1, s.c2) };
}

function expandMerge(s: CellSel, merges: number[][]): CellSel {
  let { r1, c1, r2, c2 } = s;
  let changed = true;
  while (changed) {
    changed = false;
    for (const m of merges) {
      if (m[0] <= r2 && m[2] >= r1 && m[1] <= c2 && m[3] >= c1) {
        const n = [Math.min(r1, m[0]), Math.min(c1, m[1]), Math.max(r2, m[2]), Math.max(c2, m[3])];
        if (n[0] !== r1 || n[1] !== c1 || n[2] !== r2 || n[3] !== c2) {
          [r1, c1, r2, c2] = n;
          changed = true;
        }
      }
    }
  }
  return { sheet: s.sheet, r1, c1, r2, c2 };
}

export function hexA(hex: string, a: number) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((x) => x + x).join("") : h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

export function describeCells(s: CellSel) {
  const n = (s.r2 - s.r1 + 1) * (s.c2 - s.c1 + 1);
  return `${s.sheet}!${rangeRef(s.r1, s.c1, s.r2, s.c2)}` + (n > 1 ? tr("（{v1} 行 × {v2} 列）", { v1: s.r2 - s.r1 + 1, v2: s.c2 - s.c1 + 1 }) : "");
}
export { cellRef };
