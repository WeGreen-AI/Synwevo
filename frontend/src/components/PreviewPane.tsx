import { useCallback, useEffect, useState } from "react";
import { get, qs } from "../lib/api";
import type { Mark, Mapping, Slot } from "../lib/types";
import AnyView from "./AnyView";

interface Props {
  projectId: string;
  rev: number;
  mappings: Mapping[];
  slots: Slot[];
  onLocateMapping: (id: string) => void;
  onClose: () => void;
}

export default function PreviewPane({ projectId, rev, mappings, slots, onLocateMapping, onClose }: Props) {
  const [index, setIndex] = useState(0);
  const [search, setSearch] = useState("");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [sheet, setSheet] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [real, setReal] = useState(false);
  const [lay, setLay] = useState<any>(null);
  const [layView, setLayView] = useState<any>(null);

  const load = useCallback(async (params: any) => {
    setLoading(true);
    setErr(null);
    try {
      const d = await get(`/api/projects/${projectId}/preview${qs(params)}`);
      setData(d);
      setIndex(d.record.index);
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load({ index });
    // re-render when anything in the project changes (template / mappings / rule / sources)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rev]);

  const out = data?.output;
  const pageable = out && ["docx", "xlsx", "pptx"].includes(out.format);
  useEffect(() => {
    setLay(null);
    setLayView(null);
    if (!real || !out || !pageable) return;
    let alive = true;
    get(`/api/projects/${projectId}/preview-layout${qs({ blob: out.blob })}`).then(async (l) => {
      if (!alive) return;
      setLay(l);
      if (l.available) setLayView(await get(`/api/preview-files/${l.pdf_blob}/view${qs({ fmt: "pdf" })}`));
    }).catch((e) => alive && setLay({ available: false, reason: e.message }));
    return () => { alive = false; };
  }, [real, out?.blob]);
  const view = real && layView ? layView : data?.render;
  const issues: any[] = data?.issues || data?.plan?.issues || [];
  const loadGrid = useCallback((s: string | null, offset: number, limit: number) =>
    get(`/api/preview-files/${out?.blob}/view${qs({ fmt: out?.format, sheet: s, offset, limit })}`).then((r: any) => r.grid), [out?.blob]);
  const marks: Mark[] = [];
  return (
    <div className="pane-b" style={{ display: "flex", flexDirection: "column" }}>
      <div className="toolbar">
        <b>真实样本预览</b>
        <button className="btn sm" disabled={loading || index <= 0} onClick={() => load({ index: index - 1 })}>‹ 上一条</button>
        <input className="input sm" type="number" min={1} max={data?.total || 1} value={index + 1} style={{ width: 70 }} onChange={(e) => load({ index: Number(e.target.value) - 1 })} />
        <span className="small muted">/ {data?.total ?? "…"}</span>
        <button className="btn sm" disabled={loading || !data || index >= data.total - 1} onClick={() => load({ index: index + 1 })}>下一条 ›</button>
        <input className="input sm" placeholder="按样本标识查找" value={search} onChange={(e) => setSearch(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search && load({ search })} style={{ width: 130 }} />
        <span className="spacer" />
        {pageable && (
          <button className={`btn sm${real ? " active" : ""}`} onClick={() => setReal(!real)} title="用 LibreOffice 排版后的真实页面（含分页）">
            {real ? "✓ 真实分页" : "真实分页"}
          </button>
        )}
        {out && <a className="btn sm" href={`/api/preview-files/${out.blob}?fmt=${out.format}&name=${encodeURIComponent("预览_" + (data.record.ident || data.record.index + 1))}`}>下载此预览</a>}
        <button className="btn sm" onClick={() => setZoom(Math.max(0.5, zoom - 0.1))}>－</button>
        <button className="btn sm" onClick={() => setZoom(Math.min(2, zoom + 0.1))}>＋</button>
        <button className="btn sm" onClick={onClose}>返回模板</button>
      </div>
      {data && (
        <div className="notice" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0 }}>
          <b>{data.record.label}</b> <span className="muted small">来源：{data.record.file_name} · {data.record.where}</span>
          {data.record.merged_from && <span className="badge warn" style={{ marginLeft: 6 }}>跨文件合并：{data.record.merged_from.join("、")}</span>}
          {data.verification && <span className="badge ok" style={{ marginLeft: 6 }} title="预览文件已重新打开并核对填入的值">✓ 已校验 {data.verification.checks} 个值</span>}
          <span className="tiny muted" style={{ marginLeft: 6 }}>预览与批量生成使用同一套解析、映射与格式规则</span>
          {real && !lay && pageable && <span className="badge" style={{ marginLeft: 6 }}>正在用 LibreOffice 排版…</span>}
          {real && lay?.available && (
            <span className={`badge ${lay.pages > lay.template_pages ? "warn" : "ok"}`} style={{ marginLeft: 6 }}>
              模板 {lay.template_pages} 页 → 本份 {lay.pages} 页{lay.pages > lay.template_pages ? "（分页增加）" : ""}
            </span>
          )}
          {real && lay && !lay.available && <span className="badge warn" style={{ marginLeft: 6 }}>{lay.reason}</span>}
          {data.layout && <span className="badge info" style={{ marginLeft: 6 }}>已按「保持模板页数」处理：{data.layout.pages} 页{data.layout.factor < 1 ? `，字段缩小到 ${Math.round(data.layout.factor * 100)}%` : ""}</span>}
        </div>
      )}
      {issues.length > 0 && (
        <div style={{ maxHeight: 130, overflow: "auto", borderBottom: "1px solid var(--line)" }}>
          {issues.map((i, k) => (
            <div key={k} className={`notice ${i.level === "error" ? "err" : i.level === "warning" ? "warn" : "info"}`} style={{ borderRadius: 0, cursor: i.mapping ? "pointer" : undefined }}
              onClick={() => i.mapping && onLocateMapping(i.mapping)}>
              {i.level === "error" ? "✖" : i.level === "warning" ? "▲" : "ℹ"} {i.message}{i.mapping && <span className="tiny"> （点击定位映射）</span>}
            </div>
          ))}
        </div>
      )}
      {err && <div className="empty"><div className="notice err">{err}</div></div>}
      {data?.blocked && <div className="empty"><div className="big">该样本存在错误，无法生成预览</div><div>请根据上方提示修正映射或数据</div></div>}
      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        {loading && !view && <div className="empty">正在生成预览…</div>}
        {view && out && (
          <AnyView view={view} side="tpl" reloadKey={real && lay?.pdf_blob ? lay.pdf_blob : out.blob} loadGrid={loadGrid}
            pageUrl={(p) => `/api/preview-files/${real && lay?.pdf_blob ? lay.pdf_blob : out.blob}/page/${p}.png?zoom=1.5`}
            marks={marks} selection={null} onSelect={() => {}} zoom={zoom} sheet={sheet} onSheet={setSheet} page={page} onPage={setPage} />
        )}
      </div>
    </div>
  );
}
