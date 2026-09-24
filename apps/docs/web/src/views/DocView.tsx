import { ReactElement, useEffect, useRef } from "react";
import type { Mark, Selection } from "../lib/types";
import { cssColor, highlightColor, markColor } from "../lib/css";
import { hexA } from "./GridView";
import { scrollBehavior, useFit } from "@synwevo/ui";

const PT = 96 / 72;

interface Props {
  doc: any;
  marks: Mark[];
  selection: Selection | null;
  onSelect: (s: Selection | null) => void;
  onMarkClick?: (id: string) => void;
  side: "src" | "tpl";
  zoom?: number;
  slotNames?: Record<string, { name: string; color: string; no?: number; cols?: Record<string, string> }>;
  focusKey?: string | null;
  selectable?: boolean;
}

export default function DocView({ doc, marks, selection, onSelect, onMarkClick, side, zoom = 1, slotNames = {}, focusKey, selectable = true }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const fit = useFit(ref, ((doc.page?.w || 595) - (doc.page?.margin?.left || 90) - (doc.page?.margin?.right || 90) + Math.min(doc.page?.margin?.left || 90, 40) + Math.min(doc.page?.margin?.right || 90, 40)) * PT, 36);

  useEffect(() => {
    if (!focusKey || !ref.current) return;
    const el = ref.current.querySelector(`[data-mark="${cssEsc(focusKey)}"]`) as HTMLElement | null;
    if (el) {
      el.scrollIntoView({ block: "center", behavior: scrollBehavior() });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 1900);
    }
  }, [focusKey]);

  const onMouseUp = (e: React.MouseEvent) => {
    const s = window.getSelection();
    if (!selectable || !s || !ref.current) return;
    if (!s.isCollapsed && (!ref.current.contains(s.anchorNode) || !ref.current.contains(s.focusNode))) return;
    const target = e.target as HTMLElement;
    if (target.closest("[data-skip-select]")) return;
    const a = boundary(s.anchorNode, s.anchorOffset);
    const f = boundary(s.focusNode, s.focusOffset);
    if (a && f && a.p === f.p && (a.off !== f.off || side === "tpl")) {
      const p = a.p;
      const start = Math.min(a.off, f.off), end = Math.max(a.off, f.off);
      const cellEl = p.closest("td[data-tbl]") as HTMLElement | null;
      if (start === end && cellEl && side === "src") {
        onSelect(cellSel(cellEl, e.shiftKey ? selection : null));
        return;
      }
      if (start === end && cellEl && side === "tpl" && !e.altKey) {
        // a plain click inside a template table cell selects the cell; Alt+click places an insertion caret
        onSelect(cellSel(cellEl, e.shiftKey ? selection : null));
        return;
      }
      const text = p.dataset.text || "";
      onSelect({ kind: "docx_text", part: p.dataset.part!, path: p.dataset.path!, start, end, text: text.slice(start, end), blk: p.dataset.blk || undefined });
      return;
    }
    if (a && f && a.p !== f.p) {
      // several paragraphs of the same container on the template side: a candidate repeat content block
      const pa = a.p.dataset.path!, pf = f.p.dataset.path!;
      const pre = (x: string) => x.split(":").slice(0, -1).join(":");
      const last = (x: string) => Number(x.split(":").pop());
      if (side === "tpl" && a.p.dataset.part === f.p.dataset.part && pre(pa) === pre(pf)) {
        const [p1, p2] = last(pa) <= last(pf) ? [a.p, f.p] : [f.p, a.p];
        const all = Array.from(ref.current.querySelectorAll<HTMLElement>("p.doc-p")).filter((x) => x.dataset.part === p1.dataset.part && pre(x.dataset.path!) === pre(pa)
          && last(x.dataset.path!) >= last(p1.dataset.path!) && last(x.dataset.path!) <= last(p2.dataset.path!));
        onSelect({ kind: "docx_blocks", part: p1.dataset.part!, first: p1.dataset.path!, last: p2.dataset.path!, count: last(p2.dataset.path!) - last(p1.dataset.path!) + 1,
          text: all.map((x) => x.dataset.text || "").join(" / ") });
        return;
      }
      onSelect(null);
      return;
    }
    const td = target.closest("td[data-tbl]") as HTMLElement | null;
    if (td) onSelect(cellSel(td, e.shiftKey ? selection : null));
  };

  const markFor = (part: string, path: string) => marks.filter((m) => m.pos.kind === "docx_text" && (m.pos as any).path === path && ((m.pos as any).part || part) === part);

  const renderPara = (b: any, key: string): ReactElement => {
    const pm = markFor(b.part, b.path);
    const selHere = selection?.kind === "docx_text" && selection.path === b.path && selection.part === b.part ? selection : null;
    const cuts = new Set<number>();
    for (const r of b.runs) {
      cuts.add(r.off);
      cuts.add(r.off + r.text.length);
    }
    for (const m of pm) {
      cuts.add((m.pos as any).start);
      cuts.add((m.pos as any).end);
    }
    if (selHere) {
      cuts.add(selHere.start);
      cuts.add(selHere.end);
    }
    const st = b.style || {};
    const pstyle: React.CSSProperties = {
      textAlign: (st.align as any) || undefined,
      marginLeft: st.ind_left ? st.ind_left * PT : undefined,
      textIndent: st.ind_first ? st.ind_first * PT : st.ind_first_chars ? `${st.ind_first_chars}em` : st.ind_hanging ? -st.ind_hanging * PT : undefined,
      marginTop: st.sp_before ? st.sp_before * PT : undefined,
      marginBottom: st.sp_after ? Math.min(st.sp_after, 24) * PT : undefined,
      lineHeight: st.line ? Math.max(1.1, st.line * 1.25) : undefined,
      // Colours come from the uploaded file: only plain colour syntax reaches the style
      // attribute, never a url() the file's author chose (see lib/css.ts).
      background: cssColor(st.shd),
    };
    const base = b.rbase || {};
    if (base.sz) pstyle.fontSize = base.sz * PT;
    if (base.b) pstyle.fontWeight = 700;
    const children: ReactElement[] = [];
    if (b.marker) children.push(<span key="lst" className="lst" data-skip-select>{b.marker}</span>);
    const seenField = new Set<string>();
    b.runs.forEach((r: any, ri: number) => {
      const baseField = r.field ? String(r.field).split(/[#@]/)[0] : null;
      const slotInfo = baseField ? slotNames[baseField] : undefined;
      const fieldMarks = baseField ? marks.filter((m) => m.pos.kind === "field" && (m.pos as any).field === baseField) : [];
      if (r.field && slotInfo && !seenField.has(r.field)) {
        seenField.add(r.field);
        const colKey = String(r.field).includes("#") ? String(r.field).split("#")[1] : "";
        const colName = colKey ? slotInfo.cols?.[colKey] || colKey : "";
        children.push(
          <button type="button" key={`chip${ri}`} className="fldchip" data-skip-select style={{ background: cssColor(slotInfo.color) }} data-mark={fieldMarks[0] ? `${fieldMarks[0].role}:${fieldMarks[0].id}` : `slot:${baseField}`}
            title={colName ? `明细「${slotInfo.name}」的列「${colName}」` : `字段：${slotInfo.name}`} onClick={(e) => { e.stopPropagation(); onMarkClick?.(baseField!); onSelect({ kind: "slot", slotId: baseField! }); }}>
            {slotInfo.no != null ? `${slotInfo.no} ` : ""}{colName || slotInfo.name}
          </button>,
        );
      }
      if (r.obj) {
        const img = r.img;
        const src = img && doc.images?.[img.key];
        children.push(
          src ? <img key={ri} data-off={r.off} data-len={1} src={src} style={{ width: img.w || undefined, height: img.h || undefined, maxWidth: "100%", verticalAlign: "bottom" }} alt="图片" />
            : <span key={ri} data-off={r.off} data-len={1} className="badge" title="嵌入对象">{r.obj === "math" ? "公式" : "对象"}</span>,
        );
        return;
      }
      if (r.empty) {
        children.push(<span key={ri} className="fld-empty" data-off={r.off} data-len={0} style={{ borderColor: markColor(slotInfo?.color, "#999") }} title={slotInfo ? `插入位置：${slotInfo.name}` : "空字段"} />);
        return;
      }
      // split run at cut points
      const pts = [...cuts].filter((x) => x > r.off && x < r.off + r.text.length).sort((x, y) => x - y);
      const bounds = [r.off, ...pts, r.off + r.text.length];
      for (let i = 0; i < bounds.length - 1; i++) {
        const s0 = bounds[i], e0 = bounds[i + 1];
        if (e0 <= s0) continue;
        const style: React.CSSProperties = {};
        if (r.b) style.fontWeight = 700;
        if (r.i) style.fontStyle = "italic";
        const deco = [r.u ? "underline" : "", r.s ? "line-through" : ""].filter(Boolean).join(" ");
        if (deco) style.textDecoration = deco;
        if (r.color) style.color = cssColor(r.color);
        if (r.sz) style.fontSize = r.sz * PT;
        if (r.font) style.fontFamily = `"${r.font}", var(--font)`;
        if (r.hl) style.background = highlightColor(r.hl);
        if (r.shd) style.background = cssColor(r.shd) ?? style.background;
        if (r.va === "superscript") style.verticalAlign = "super";
        if (r.va === "subscript") style.verticalAlign = "sub";
        if (r.hidden) style.opacity = 0.4;
        let cls = "";
        let dataMark: string | undefined;
        if (slotInfo) {
          cls = "fld";
          style.background = hexA(markColor(slotInfo.color), fieldMarks.some((m) => m.active) ? 0.32 : 0.16);
          style.boxShadow = `inset 0 -2px 0 ${markColor(slotInfo.color)}`;
        }
        const covering = pm.filter((m) => (m.pos as any).start <= s0 && (m.pos as any).end >= e0 && (m.pos as any).end > (m.pos as any).start);
        if (covering.length) {
          const m = covering[covering.length - 1];
          cls += " mkspan";
          style.borderBottomColor = markColor(m.color);
          style.background = hexA(markColor(m.color), m.active ? 0.3 : 0.15);
          if ((m.pos as any).start === s0) dataMark = `${m.role}:${m.id}`;
        }
        if (selHere && selHere.start <= s0 && selHere.end >= e0 && selHere.end > selHere.start) {
          style.outline = "1px solid #2563eb";
          style.background = "rgba(37,99,235,.18)";
        }
        children.push(
          <span key={`${ri}-${i}`} data-off={s0} className={cls || undefined} style={style} data-mark={dataMark}
            onClick={slotInfo ? (e) => { if (window.getSelection()?.isCollapsed) { e.stopPropagation(); onMarkClick?.(baseField!); onSelect({ kind: "slot", slotId: baseField! }); } } : undefined}>
            {r.text.slice(s0 - r.off, e0 - r.off)}
          </span>,
        );
        for (const m of covering) {
          if ((m.pos as any).end === e0 && m.no != null) {
            children.push(<span key={`tag${ri}-${i}-${m.id}`} className="num" data-skip-select style={{ background: markColor(m.color), marginLeft: 2, cursor: "pointer" }} title={m.label}
              onMouseUp={(e) => { e.stopPropagation(); onMarkClick?.(m.id); }}>{m.no}</span>);
          }
        }
      }
    });
    if (selHere && selHere.start === selHere.end) {
      // caret marker for insertion
    }
    const inBlockSel = selection?.kind === "docx_blocks" && selection.part === b.part && inRange(b.path, selection.first, selection.last);
    return (
      <p key={key} className={`doc-p${inBlockSel ? " blksel" : ""}`} style={pstyle} data-path={b.path} data-part={b.part} data-text={b.text} data-blk={b.blk || undefined}
        data-mark={pm.find((m) => (m.pos as any).start === 0 && (m.pos as any).end === 0) ? undefined : undefined}>
        {children.length ? children : <br />}
      </p>
    );
  };

  /** blocks in order; consecutive members of one repeat content block are framed together */
  const renderSeq = (blocks: any[], kp: string): ReactElement[] => {
    const out: ReactElement[] = [];
    for (let i = 0; i < blocks.length; ) {
      const b = blocks[i];
      if (b.blk) {
        let j = i;
        while (j < blocks.length && blocks[j].blk === b.blk) j++;
        const info = slotNames[b.blk];
        const blkMarks = marks.filter((m) => m.pos.kind === "field" && (m.pos as any).field === b.blk);
        const active = blkMarks.some((m) => m.active);
        const color = markColor(info?.color, "#7c3aed");
        out.push(
          <div key={`${kp}blk${i}`} className={`doc-blk${active ? " on" : ""}`} style={{ borderColor: color, background: hexA(color, active ? 0.08 : 0.035) }}
            data-mark={blkMarks[0] ? `${blkMarks[0].role}:${blkMarks[0].id}` : `slot:${b.blk}`}>
            <button type="button" className="doc-blk-tag" data-skip-select style={{ background: color }} title="重复内容块：每条明细复制一次这些内容"
              onClick={(e) => { e.stopPropagation(); onMarkClick?.(b.blk); onSelect({ kind: "slot", slotId: b.blk }); }}>
              ⟳ {info?.no != null ? `${info.no} ` : ""}{info?.name || "重复内容块"}
            </button>
            {blocks.slice(i, j).map((x: any, k: number) => (x.t === "p" ? renderPara(x, `${kp}${i + k}`) : renderTable(x, `${kp}${i + k}`)))}
          </div>,
        );
        i = j;
      } else {
        out.push(b.t === "p" ? renderPara(b, `${kp}${i}`) : renderTable(b, `${kp}${i}`));
        i++;
      }
    }
    return out;
  };

  const renderTable = (t: any, key: string): ReactElement => {
    const colMarks = marks.filter((m) => (m.pos.kind === "docx_cell" || m.pos.kind === "docx_row" || m.pos.kind === "docx_table") && (m.pos as any).table_path === t.path);
    // compute rowspans from vMerge
    const grid: { gc: number; cell: any; ci: number }[][] = t.rows.map((row: any) => {
      let gc = 0;
      return row.cells.map((cell: any, ci: number) => {
        const o = { gc, cell, ci };
        gc += cell.span || 1;
        return o;
      });
    });
    const rowspan = new Map<string, number>();
    grid.forEach((row, ri) => {
      row.forEach(({ gc, cell }) => {
        if (cell.vmerge === "restart") {
          let n = 1;
          for (let k = ri + 1; k < grid.length; k++) {
            const c2 = grid[k].find((x) => x.gc === gc);
            if (c2 && c2.cell.vmerge === "continue") n++;
            else break;
          }
          rowspan.set(`${ri},${gc}`, n);
        }
      });
    });
    const tableMark = colMarks.find((m) => m.pos.kind === "docx_table");
    const selRow = selection?.kind === "docx_row" && selection.table_path === t.path ? selection.row : -1;
    const tableSel = selection?.kind === "docx_table" && selection.table_path === t.path ? selection : null;
    return (
      <div key={key} style={{ position: "relative", margin: "4px 0", outline: tableMark ? `2px solid ${markColor(tableMark.color)}` : undefined }} data-mark={tableMark ? `${tableMark.role}:${tableMark.id}` : undefined}>
        {/* onClick, not onMouseUp: Enter and Space on a focused button fire click only, so the
            old handler made this control mouse-only. */}
        {selectable && <button type="button" className="btn xs" data-skip-select style={{ position: "absolute", left: -28, top: -2 }} title="选择整个表格（作为明细表）"
          aria-label="选择整个表格" onClick={(e) => { e.stopPropagation(); onSelect({ kind: "docx_table", part: t.part, table_path: t.path, header_rows: 1 }); }}>▦</button>}
        {tableMark && <span className="num" style={{ position: "absolute", right: -8, top: -9, background: markColor(tableMark.color) }}>{tableMark.no}</span>}
        <table className={`doc-tbl${t.borders ? " b" : ""}`} style={{ width: t.grid?.length ? t.grid.reduce((a: number, b: number) => a + b, 0) * PT : "100%", maxWidth: "100%" }}>
          {t.grid?.length > 0 && <colgroup>{t.grid.map((g: number, i: number) => <col key={i} style={{ width: g * PT }} />)}</colgroup>}
          <tbody>
            {grid.map((row, ri) => {
              const rowMark = colMarks.find((m) => m.pos.kind === "docx_row" && (m.pos as any).row === ri);
              return (
                <tr key={ri} className={selRow === ri ? "rowsel" : undefined} style={rowMark ? { outline: `2px dashed ${markColor(rowMark.color)}`, outlineOffset: -2 } : undefined}
                  data-mark={rowMark ? `${rowMark.role}:${rowMark.id}` : undefined}>
                  {row.map(({ gc, cell, ci }, k) => {
                    if (cell.vmerge === "continue") return null;
                    const cm = colMarks.filter((m) => m.pos.kind === "docx_cell" && (m.pos as any).row === ri && (m.pos as any).col === ci);
                    const inSel = (selection?.kind === "docx_cell" && selection.table_path === t.path && selection.row === ri && selection.col === ci) ||
                      (tableSel && tableSel.r1 !== undefined && ri >= tableSel.r1! && ri <= tableSel.r2! && ci >= tableSel.c1! && ci <= tableSel.c2!);
                    return (
                      <td key={k} colSpan={cell.span > 1 ? cell.span : undefined} rowSpan={rowspan.get(`${ri},${gc}`)} data-tbl={t.path} data-part={t.part} data-row={ri} data-col={ci}
                        className={inSel ? "cellsel" : undefined}
                        style={{ background: cm[0] ? hexA(markColor(cm[0].color), 0.15) : cssColor(cell.shd), boxShadow: cm[0] ? `inset 0 0 0 2px ${markColor(cm[0].color)}` : undefined }}
                        data-mark={cm[0] ? `${cm[0].role}:${cm[0].id}` : undefined}>
                        {selectable && k === 0 && side === "tpl" && (
                          <button type="button" className="rowhandle" data-skip-select title="选择整行（可设为重复明细行）" aria-label={`选择表格第 ${ri + 1} 行`}
                            onClick={(e) => { e.stopPropagation(); onSelect({ kind: "docx_row", part: t.part, table_path: t.path, row: ri }); }}>≡</button>
                        )}
                        {selectable && k === 0 && side === "src" && (
                          <button type="button" className="rowhandle" data-skip-select title="从此行开始作为明细表（此行为表头）" aria-label={`以第 ${ri + 1} 行为表头选择明细表`}
                            onClick={(e) => { e.stopPropagation(); onSelect({ kind: "docx_table", part: t.part, table_path: t.path, header_rows: ri + 1 }); }}>≡</button>
                        )}
                        {cm[0] && cm[0].no != null && <span className="num" style={{ position: "absolute", right: 1, top: 1, background: markColor(cm[0].color) }}>{cm[0].no}</span>}
                        {renderSeq(cell.blocks, `c${ri}-${ci}-`)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  const page = doc.page || { w: 595, h: 842, margin: { top: 72, bottom: 72, left: 90, right: 90 } };
  // on screen the page keeps its text width but uses compact margins (paper margins are kept in the file itself)
  const ml = Math.min(page.margin.left, 40), mr = Math.min(page.margin.right, 40);
  const widthPx = (page.w - page.margin.left - page.margin.right + ml + mr) * PT;
  const eff = zoom * fit;
  const pad = `${Math.min(page.margin.top, 40) * PT}px ${mr * PT}px ${Math.min(page.margin.bottom, 40) * PT}px ${ml * PT}px`;
  const body = doc.parts.find((p: any) => p.kind === "body");
  const headers = doc.parts.filter((p: any) => p.kind === "header" && p.blocks.some((b: any) => b.t === "tbl" || b.text));
  const footers = doc.parts.filter((p: any) => p.kind === "footer" && p.blocks.some((b: any) => b.t === "tbl" || b.text));
  const renderBlocks = (blocks: any[]) => renderSeq(blocks, "b");

  return (
    <div className="doc-scroll" ref={ref} onMouseUp={onMouseUp}>
      <div className="doc-page" style={{ width: widthPx, minHeight: page.h * PT * 0.6, padding: pad, zoom: eff }}>
        {headers.map((h: any) => (
          <div key={h.part} className="doc-hf">
            <span className="doc-part-label" data-skip-select>{h.label}</span>
            {renderBlocks(h.blocks)}
          </div>
        ))}
        {body && renderBlocks(body.blocks)}
        {footers.map((h: any) => (
          <div key={h.part} className="doc-hf" style={{ marginTop: 16 }}>
            <span className="doc-part-label" data-skip-select>{h.label}</span>
            {renderBlocks(h.blocks)}
          </div>
        ))}
      </div>
    </div>
  );
}

function cellSel(td: HTMLElement, prev: Selection | null): Selection {
  const t = td.dataset.tbl!, part = td.dataset.part!, r = Number(td.dataset.row), c = Number(td.dataset.col);
  if (prev && (prev.kind === "docx_cell" || prev.kind === "docx_table") && prev.table_path === t) {
    const pr = prev.kind === "docx_cell" ? prev.row : prev.r1 ?? r;
    const pc = prev.kind === "docx_cell" ? prev.col : prev.c1 ?? c;
    return { kind: "docx_table", part, table_path: t, r1: Math.min(pr, r), c1: Math.min(pc, c), r2: Math.max(pr, r), c2: Math.max(pc, c), header_rows: 0 };
  }
  return { kind: "docx_cell", part, table_path: t, row: r, col: c, text: td.innerText };
}

/** map a DOM boundary point to (paragraph element, text offset) */
function boundary(node: Node | null, offset: number): { p: HTMLElement; off: number } | null {
  if (!node) return null;
  const el = (node.nodeType === 3 ? node.parentElement : (node as HTMLElement)) as HTMLElement | null;
  if (!el) return null;
  const p = el.closest("p.doc-p") as HTMLElement | null;
  if (!p) return null;
  if (node.nodeType === 3) {
    const atom = el.closest("[data-off]") as HTMLElement | null;
    if (!atom || !p.contains(atom)) return null;
    if (atom.closest("[data-skip-select]")) return null;
    return { p, off: Number(atom.dataset.off) + offset };
  }
  // element boundary: look at child nodes
  const kids = Array.from(node.childNodes);
  const next = kids[offset] as HTMLElement | undefined;
  const prevEl = kids[offset - 1] as HTMLElement | undefined;
  const atomOf = (x?: Node) => (x && x.nodeType === 1 ? ((x as HTMLElement).matches("[data-off]") ? (x as HTMLElement) : (x as HTMLElement).querySelector("[data-off]")) : null);
  if (next) {
    const a = atomOf(next);
    if (a) return { p, off: Number((a as HTMLElement).dataset.off) };
  }
  if (prevEl) {
    const all = prevEl.nodeType === 1 ? [prevEl, ...Array.from((prevEl as HTMLElement).querySelectorAll("[data-off]"))] : [];
    const a = all.filter((x) => (x as HTMLElement).dataset?.off !== undefined).pop() as HTMLElement | undefined;
    if (a) return { p, off: Number(a.dataset.off) + (a.dataset.len !== undefined ? Number(a.dataset.len) : (a.textContent || "").length) };
  }
  return { p, off: 0 };
}

function inRange(path: string, first: string, last: string) {
  const pre = (x: string) => x.split(":").slice(0, -1).join(":");
  const n = (x: string) => Number(x.split(":").pop());
  return pre(path) === pre(first) && n(path) >= n(first) && n(path) <= n(last);
}

function cssEsc(s: string) {
  return s.replace(/["\\]/g, "\\$&");
}
