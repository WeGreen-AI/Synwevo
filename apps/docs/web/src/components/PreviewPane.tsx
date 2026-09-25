import { formatNumber } from "@synwevo/ui";
import { DownloadLink } from "./ApiResource";
import { serverText, issueText, diagnosticText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useCallback, useEffect, useState } from "react";
import { get, qs, useSession } from "@synwevo/auth-client";
import { isImeKey } from "@synwevo/ui";
import type { Mark, Mapping, Slot } from "../lib/types";
import { useLatest } from "../lib/latest";
import { verificationBadge } from "../lib/jobview";
import { errorText, NO_EXPORT_TEXT, permissions } from "../lib/perm";
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
  useI18n();
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
  const [indexText, setIndexText] = useState("1");
  const latest = useLatest();
  const session = useSession();
  const perm = permissions(session.me, session.disabled);

  // Only the newest request's answer is shown: stepping quickly through records used to let an
  // older, slower preview land last under the newer record's header.
  const load = useCallback((params: any) => {
    setLoading(true);
    setErr(null);
    setData(null);
    setLay(null);
    setLayView(null);
    latest(
      (signal) => get(`/api/projects/${projectId}/preview${qs(params)}`, signal),
      (d: any) => { setData(d); setSheet(null); setPage(0); setIndex(d.record.index); setIndexText(String(d.record.index + 1)); setLoading(false); },
      (e) => { setErr(errorText(e)); setLoading(false); },
    );
  }, [projectId, latest]);

  useEffect(() => {
    load({ index });
    // re-render when anything in the project changes (template / mappings / rule / sources)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, rev]);

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
      if (!l.available) return;
      const v = await get(`/api/preview-files/${l.pdf_blob}/view${qs({ fmt: "pdf" })}`);
      // checked again after the second await: the record may have changed while it ran, and
      // the previous record's pages must not appear under the new record's header
      if (alive) setLayView(v);
    }).catch((e) => { if (alive) { setLayView(null); setLay({ available: false, reason: errorText(e) }); } });
    return () => { alive = false; };
  }, [real, out?.blob]);
  const view = real && layView ? layView : data?.render;
  const issues: any[] = data?.issues || data?.plan?.issues || [];
  const loadGrid = useCallback((s: string | null, offset: number, limit: number) =>
    get(`/api/preview-files/${out?.blob}/view${qs({ fmt: out?.format, sheet: s, offset, limit })}`).then((r: any) => r.grid), [out?.blob]);
  const marks: Mark[] = [];
  const vb = verificationBadge(data?.verification);
  // The index box commits on Enter or when it loses focus — typing "120" used to render
  // previews for 1, 12 and 120 on the server.
  const commitIndex = () => {
    const n = Number(indexText);
    if (!Number.isInteger(n) || n < 1 || (data?.total && n > data.total)) { setIndexText(String(index + 1)); return; }
    if (n - 1 !== index) load({ index: n - 1 });
  };
  return (
    <div className="pane-b" style={{ display: "flex", flexDirection: "column" }}>
      <div className="toolbar">
        <b>{tr("真实样本预览")}</b>
        <button type="button" className="btn sm" disabled={loading || index <= 0} onClick={() => load({ index: index - 1 })}>{tr("‹ 上一条")}</button>
        <input className="input sm" type="number" min={1} max={data?.total || 1} value={indexText} style={{ width: 70 }} aria-label={tr("样本序号")}
          onChange={(e) => setIndexText(e.target.value)} onBlur={commitIndex}
          onKeyDown={(e) => { if (isImeKey(e)) return; if (e.key === "Enter") { e.preventDefault(); commitIndex(); } }} />
        <span className="small muted">/ {data?.total ?? "…"}</span>
        <button type="button" className="btn sm" disabled={loading || !data || index >= data.total - 1} onClick={() => load({ index: index + 1 })}>{tr("下一条 ›")}</button>
        <input className="input sm" placeholder={tr("按样本标识查找")} aria-label={tr("按样本标识查找")} value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 130 }}
          onKeyDown={(e) => {
            // Enter that confirms a pinyin candidate is not a search
            if (isImeKey(e)) return;
            if (e.key === "Enter" && search) { e.preventDefault(); load({ search }); }
          }} />
        <span className="spacer" />
        {pageable && (
          <button type="button" className={`btn sm${real ? " active" : ""}`} aria-pressed={real} onClick={() => setReal(!real)} title={tr("用排版引擎排版后的真实页面（含分页）")}>
            {real ? tr("✓ 真实分页") : tr("真实分页")}
          </button>
        )}
        {out && (perm.canExport
          ? <DownloadLink className="btn sm" href={`/api/preview-files/${out.blob}?fmt=${out.format}&name=${encodeURIComponent(tr("预览_") + (data.record.ident || data.record.index + 1))}`}>{tr("下载此预览")}</DownloadLink>
          : <span className="tiny muted" title={tr(NO_EXPORT_TEXT)}>{tr("无下载权限")}</span>)}
        <button type="button" className="btn sm" onClick={() => setZoom(Math.max(0.5, zoom - 0.1))} title={tr("缩小预览")} aria-label={tr("缩小预览")}>－</button>
        <button type="button" className="btn sm" onClick={() => setZoom(Math.min(2, zoom + 0.1))} title={tr("放大预览")} aria-label={tr("放大预览")}>＋</button>
        <button type="button" className="btn sm" onClick={onClose}>{tr("返回模板")}</button>
      </div>
      {data && (
        <div className="notice" style={{ borderRadius: 0, borderLeft: 0, borderRight: 0 }}>
          <b>{data.record.label}</b> <span className="muted small">{tr("来源：")}{data.record.file_name} · {serverText(data.record.where)}</span>
          {data.record.merged_from && <span className="badge warn" style={{ marginLeft: 6 }}>{tr("跨文件合并：")}{data.record.merged_from.join("、")}</span>}
          {vb && <span className={`badge ${vb.tone}`} style={{ marginLeft: 6 }} title={vb.title}>{vb.tone === "ok" ? tr("✓ 已校验 {v1} 个值", { v1: data.verification.checks }) : vb.label}</span>}
          <span className="tiny muted" style={{ marginLeft: 6 }}>{tr("预览与批量生成使用同一套解析、映射与格式规则")}</span>
          {real && !lay && pageable && <span className="badge" style={{ marginLeft: 6 }}>{tr("正在用排版引擎排版…")}</span>}
          {real && lay?.available && (
            <span className={`badge ${lay.pages > lay.template_pages ? "warn" : "ok"}`} style={{ marginLeft: 6 }}>{tr("模板 ")}{formatNumber(lay.template_pages)}{tr(" 页 → 本份 ")}{formatNumber(lay.pages)}{tr(" 页")}{lay.pages > lay.template_pages ? tr("（分页增加）") : ""}
            </span>
          )}
          {real && lay && !lay.available && <span className="badge warn" style={{ marginLeft: 6 }}>{diagnosticText(lay.reason)}</span>}
          {data.layout && <span className="badge info" style={{ marginLeft: 6 }}>{tr("已按「保持模板页数」处理：")}{formatNumber(data.layout.pages)}{tr(" 页")}{data.layout.factor < 1 ? tr("，字段缩小到 {v1}%", { v1: Math.round(data.layout.factor * 100) }) : ""}</span>}
        </div>
      )}
      {issues.length > 0 && (
        <div style={{ maxHeight: 130, overflow: "auto", borderBottom: "1px solid var(--line)" }}>
          {issues.map((i, k) => (
            <div key={k} className={`notice ${i.level === "error" ? "err" : i.level === "warning" ? "warn" : "info"}`} style={{ borderRadius: 0 }}>
              {i.level === "error" ? "✖" : i.level === "warning" ? "▲" : "ℹ"} {issueText(i)}
              {i.mapping && <button type="button" className="btn xs ghost" style={{ marginLeft: 6 }} onClick={() => onLocateMapping(i.mapping)}>{tr("定位映射")}</button>}
            </div>
          ))}
        </div>
      )}
      {err && <div className="empty" role="alert"><div className="notice err">{serverText(err)}</div><button className="btn" onClick={() => load({ index })}>{tr("重试当前样本")}</button></div>}
      {data?.blocked && <div className="empty"><div className="big">{tr("该样本存在错误，无法生成预览")}</div><div>{tr("请根据上方提示修正映射或数据")}</div></div>}
      <div style={{ flex: 1, position: "relative", minHeight: 0 }}>
        {loading && !view && <div className="empty" role="status">{tr("正在生成预览…")}</div>}
        {view && out && (
          <AnyView view={view} side="tpl" reloadKey={real && lay?.pdf_blob ? lay.pdf_blob : out.blob} loadGrid={loadGrid}
            pageUrl={(p) => `/api/preview-files/${real && lay?.pdf_blob ? lay.pdf_blob : out.blob}/page/${p}.png?zoom=1.5`}
            marks={marks} selection={null} onSelect={() => {}} zoom={zoom} sheet={sheet} onSheet={setSheet} page={page} onPage={setPage} selectable={false} />
        )}
      </div>
    </div>
  );
}
