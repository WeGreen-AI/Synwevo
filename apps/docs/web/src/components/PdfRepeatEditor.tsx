import { useEffect, useMemo, useRef, useState } from "react";
import { post } from "@synwevo/auth-client";

export interface RepeatCol { name: string; x0: number; x1: number; align: "left" | "center" | "right"; sample?: string }
export interface RepeatLayout { trect: number[]; cols: RepeatCol[]; row_height: number }

interface Detected {
  trot: number;
  trect: number[];
  cols: RepeatCol[];
  row_height: number;
  font_size: number;
  rows_fit: number;
  source: { cols: string; rows: string };
  visual_aligned: boolean;
  page_w: number;
  page_h: number;
  nrect: number[];
}

const COL_SRC: Record<string, string> = { rules: "表格竖线", header: "表头文字位置", sample: "样例行文字", single: "未找到分列依据（单列）" };
const ROW_SRC: Record<string, string> = { rules: "表格横线", text: "样例行距", header: "表头与样例行间距", default: "默认行高" };
const HEADER_BAND = 34; // points of context shown above the area

/** Interactive column / row-height editor for a PDF repeat area.
 *  Dividers can be dragged, double-clicked to merge; the first row line sets the row height.
 *  Everything the pointer does has a control too (WCAG 2.5.7 / 2.1.1): the numeric boundaries
 *  are always shown, 合并 sits next to each column, and the row height is a number field. */
export default function PdfRepeatEditor({ templateId, page, nrect, onChange }: {
  templateId: string; page: number; nrect: number[]; onChange: (l: RepeatLayout | null, info: { capacity: number; error?: string }) => void;
}) {
  const [det, setDet] = useState<Detected | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cols, setCols] = useState<RepeatCol[]>([]);
  const [rowH, setRowH] = useState(0);
  const [drag, setDrag] = useState<null | { kind: "col"; i: number } | { kind: "row" }>(null);
  const boxRef = useRef<HTMLDivElement>(null);     // the drawing surface (pointer coordinates)
  const wrapRef = useRef<HTMLDivElement>(null);    // full available width (measured)
  const [boxW, setBoxW] = useState(560);

  useEffect(() => {
    let alive = true;
    setDet(null);
    setErr(null);
    post<Detected>(`/api/templates/${templateId}/pdf-repeat-detect`, { page, nrect })
      .then((d) => {
        if (!alive) return;
        setDet(d);
        setCols(d.cols.map((c) => ({ ...c })));
        setRowH(d.row_height);
      })
      .catch((e) => alive && setErr(e.message));
    return () => { alive = false; };
  }, [templateId, page, nrect]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBoxW(el.clientWidth || 560));
    ro.observe(el);
    return () => ro.disconnect();
  }, [det]);

  const capacity = det ? Math.max(0, Math.floor((det.trect[3] - det.trect[1]) / (rowH || 1) + 1e-6)) : 0;
  const problem = useMemo(() => {
    if (!det) return undefined;
    if (!(rowH >= 6)) return "行高至少 6 点";
    if (rowH > det.trect[3] - det.trect[1] + 0.01) return "行高超过区域高度";
    const narrow = cols.find((c) => c.x1 - c.x0 < 6);
    if (narrow) return `列「${narrow.name}」太窄`;
    if (cols.some((c) => !c.name.trim())) return "每一列都需要名称";
    return undefined;
  }, [det, cols, rowH]);

  useEffect(() => {
    if (!det) return onChange(null, { capacity: 0, error: err || undefined });
    onChange({ trect: det.trect, cols, row_height: rowH }, { capacity, error: problem });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [det, cols, rowH, capacity, problem, err]);

  if (err) return <div className="notice err">无法识别明细区域：{err}</div>;
  if (!det) return <div className="rp-loading"><span className="spinner" /> 正在识别表格的列和行…</div>;

  const [tx0, ty0, tx1, ty1] = det.trect;
  const areaW = tx1 - tx0;
  const band = Math.min(HEADER_BAND, ty0);
  const scale = Math.min(boxW / areaW, 340 / (ty1 - ty0 + band));   // px per point; keep the preview compact
  const viewH = (ty1 - ty0 + band) * scale;
  const px = (x: number) => (x - tx0) * scale;
  const py = (y: number) => (y - ty0 + band) * scale;
  const imgW = det.page_w * scale;
  // table space → visual image offset (pure translation when the table reads upright on screen)
  const imgLeft = -det.nrect[0] * imgW;
  const imgTop = -(det.nrect[1] * det.page_h - band) * scale;

  const onMove = (e: React.PointerEvent) => {
    if (!drag || !boxRef.current) return;
    const r = boxRef.current.getBoundingClientRect();
    if (drag.kind === "col") {
      const x = tx0 + (e.clientX - r.left) / scale;
      const i = drag.i;
      const lo = cols[i].x0 + 6, hi = cols[i + 1].x1 - 6;
      const v = Math.round(Math.max(lo, Math.min(hi, x)) * 4) / 4;
      setCols((cs) => cs.map((c, k) => (k === i ? { ...c, x1: v } : k === i + 1 ? { ...c, x0: v } : c)));
    } else {
      const y = ty0 - band + (e.clientY - r.top) / scale;
      setRowH(Math.round(Math.max(6, Math.min(ty1 - ty0, y - ty0)) * 4) / 4);
    }
  };
  const merge = (i: number) => setCols((cs) => {
    if (cs.length < 2) return cs;
    const a = cs[i], b = cs[i + 1];
    const name = [a.name, b.name].filter((n) => n && !/^第\d+列$/.test(n)).join("") || a.name;
    return [...cs.slice(0, i), { ...a, x1: b.x1, name }, ...cs.slice(i + 2)];
  });
  const split = (i: number) => setCols((cs) => {
    const c = cs[i];
    if (c.x1 - c.x0 < 14) return cs;
    const m = Math.round(((c.x0 + c.x1) / 2) * 4) / 4;
    return [...cs.slice(0, i), { ...c, x1: m }, { name: `第${cs.length + 1}列`, x0: m, x1: c.x1, align: "left" }, ...cs.slice(i + 1)];
  });

  return (
    <div className="col gap8 rp">
      <div className="row small gap8 wrap">
        <span className="badge info">可容纳 {capacity} 行</span>
        <span className="muted">列依据：{COL_SRC[det.source.cols] || det.source.cols}</span>
        <span className="muted">行高依据：{ROW_SRC[det.source.rows] || det.source.rows}</span>
      </div>
      {det.visual_aligned ? (
        <div ref={wrapRef} className="rp-wrap">
        <div className={`rp-view${drag ? " dragging" : ""}`} ref={boxRef} style={{ height: viewH, width: areaW * scale }}
          onPointerMove={onMove} onPointerUp={() => setDrag(null)} onPointerLeave={() => setDrag(null)}>
          <img alt="模板页面" draggable={false} src={`/api/templates/${templateId}/page/${page}.png?zoom=2&which=original`}
            style={{ width: imgW, left: imgLeft, top: imgTop }} />
          <div className="rp-band" style={{ height: band * scale }} />
          <div className="rp-area" style={{ top: py(ty0), height: (ty1 - ty0) * scale }} />
          {Array.from({ length: capacity }, (_, k) => (
            <div key={k} className={`rp-row${k === 0 ? " first" : ""}`} style={{ top: py(ty0 + (k + 1) * rowH) - 1 }}
              onPointerDown={k === 0 ? (e) => { (e.target as HTMLElement).setPointerCapture?.(e.pointerId); setDrag({ kind: "row" }); } : undefined}
              title={k === 0 ? "拖动调整行高" : undefined} />
          ))}
          {cols.slice(0, -1).map((c, i) => (
            <div key={i} className="rp-div" style={{ left: px(c.x1) - 5, top: py(ty0), height: (ty1 - ty0) * scale }}
              onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture?.(e.pointerId); setDrag({ kind: "col", i }); }}
              onDoubleClick={() => merge(i)} title="拖动调整列宽；双击合并相邻两列"><i /></div>
          ))}
          {cols.map((c, i) => (
            <div key={`t${i}`} className="rp-colname" style={{ left: px(c.x0) + 2, width: px(c.x1) - px(c.x0) - 4, top: 3 }}>{c.name}</div>
          ))}
        </div>
        </div>
      ) : (
        <div className="notice">该区域的文字方向与页面显示方向不同，请用下方数值调整列边界（单位：点）。</div>
      )}
      <div className="rp-cols">
        {cols.map((c, i) => (
          <div key={i} className="rp-col">
            <span className="badge">{i + 1}</span>
            <input className="input sm grow" value={c.name} aria-label={`第${i + 1}列名称`}
              onChange={(e) => setCols((cs) => cs.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))} />
            <input className="input sm w64" type="number" step={0.5} value={c.x0} aria-label={`第${i + 1}列左边界（点）`} title="左边界（点）" disabled={i === 0}
              onChange={(e) => { const v = Number(e.target.value); setCols((cs) => cs.map((x, k) => (k === i ? { ...x, x0: v } : k === i - 1 ? { ...x, x1: v } : x))); }} />
            <input className="input sm w64" type="number" step={0.5} value={c.x1} aria-label={`第${i + 1}列右边界（点）`} title="右边界（点）" disabled={i === cols.length - 1}
              onChange={(e) => { const v = Number(e.target.value); setCols((cs) => cs.map((x, k) => (k === i ? { ...x, x1: v } : k === i + 1 ? { ...x, x0: v } : x))); }} />
            <div className="seg sm" role="group" aria-label="对齐">
              {(["left", "center", "right"] as const).map((a) => (
                <button type="button" key={a} aria-pressed={c.align === a} className={c.align === a ? "on" : ""} onClick={() => setCols((cs) => cs.map((x, k) => (k === i ? { ...x, align: a } : x)))}>
                  {a === "left" ? "左" : a === "center" ? "中" : "右"}
                </button>
              ))}
            </div>
            <button type="button" className="btn xs" onClick={() => split(i)} disabled={c.x1 - c.x0 < 14} title="从中间拆成两列">拆分</button>
            {i < cols.length - 1 && (
              <button type="button" className="btn xs" onClick={() => merge(i)} title={`与第${i + 2}列合并（同双击分隔线）`} aria-label={`第${i + 1}列与第${i + 2}列合并`}>合并右列</button>
            )}
            {c.sample && <span className="tiny muted ellipsis" style={{ maxWidth: 110 }} title={c.sample}>样例：{c.sample}</span>}
          </div>
        ))}
      </div>
      <div className="row gap8 small">
        <label className="row gap4">行高（点）
          <input className="input sm w64" type="number" step={0.5} min={6} value={rowH} onChange={(e) => setRowH(Number(e.target.value))} />
        </label>
        <button type="button" className="btn xs" onClick={() => { setCols(det.cols.map((c) => ({ ...c }))); setRowH(det.row_height); }}>恢复自动识别</button>
        {problem && <span className="badge err">{problem}</span>}
      </div>
    </div>
  );
}
