import { formatNumber, BrandMark, PageState } from "@synwevo/ui";
import { DownloadLink } from "../components/ApiResource";
import { serverText, issueText } from "../lib/presentation";
import { fixTargetOf } from "../components/GenerateDialog";
import { t as tr, useI18n, LanguageMenu } from "@synwevo/ui";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get, post, qs, useSession } from "@synwevo/auth-client";
import { fmtSize, fmtTime, useToast } from "@synwevo/ui";

import { itemStatus, jobStatus } from "../lib/status";
import { canCancel, canRetry, haltText, planning as planningOf, progress, shouldPoll, verificationBadge } from "../lib/jobview";
import { errorText, isPermissionError, NO_EXPORT_TEXT, permissions, READ_ONLY_TEXT } from "../lib/perm";

const PAGE = 100;
const FILTERS: [string, string][] = [["", "全部"], ["success", "成功"], ["failed", "失败"], ["skipped", "跳过"], ["cancelled", "取消"]];
const newKey = () => `ui-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

export default function JobPage() {
  useI18n();
  const { jid } = useParams();
  return <Job key={jid} />;
}

function Job() {
  useI18n();
  const { pid, jid } = useParams();
  const toast = useToast();
  const session = useSession();
  const perm = permissions(session.me, session.disabled);
  const [job, setJob] = useState<any>(null);
  const [items, setItems] = useState<any[]>([]);
  const [itemsErr, setItemsErr] = useState<string | null>(null);
  const [filter, setFilter] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const [busy, setBusy] = useState<"cancel" | "retry" | "failed" | null>(null);
  const seen = useRef(false);            // read inside the recursing tick, where `job` would be stale
  const retryKey = useRef({ retry: newKey(), failed: newKey() });
  const [itemsTick, setItemsTick] = useState(0);

  /** Only the job record is polled (small, and it carries the counts). The table of items is a
   *  server-side page (status filter + offset) refreshed far less often: the page used to fetch
   *  up to 5000 items with their artifacts every 900 ms and then filter them in the browser, so a
   *  failure past item 5000 never appeared under 失败 while the badge counted it.
   *
   *  Returns the job, or "retry" when the failure says nothing about the job itself — a CDN
   *  rate-limit on this poll must not freeze the page on a job that is still running fine. */
  const loadJob = async (): Promise<any | "retry" | null> => {
    try {
      const j = await get(`/api/jobs/${jid}`);
      setJob(j);
      seen.current = true;
      setErr(null);
      return j;
    } catch (e: any) {
      const transient = e instanceof ApiError && (e.upstream || e.status === 0 || e.status >= 500);
      setErr(transient && seen.current ? null : errorText(e));   // keep showing progress rather than a blank page
      return transient ? "retry" : null;
    }
  };
  useEffect(() => {
    let alive = true;
    let t: any;
    let n = 0;
    let wasActive = false;
    const tick = async () => {
      const j = await loadJob();
      if (!alive) return;
      const active = j && j !== "retry" && shouldPoll(j);
      // refresh the visible page every ~3 s while the job moves, and once more when it stops
      if (j && j !== "retry" && (active ? n % 4 === 0 : wasActive || n === 0)) setItemsTick((x) => x + 1);
      wasActive = !!active || (j === "retry" && wasActive);
      n++;
      if (j === "retry" || active) t = setTimeout(tick, 900);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jid, pollKey]);

  useEffect(() => {
    if (!seen.current) return;
    let alive = true;
    get(`/api/jobs/${jid}/items${qs({ status: filter || undefined, offset, limit: PAGE })}`)
      .then((r) => { if (alive) { setItems(r); setItemsErr(null); } })
      .catch((e) => alive && setItemsErr(errorText(e)));
    return () => { alive = false; };
  }, [jid, filter, offset, itemsTick]);

  const act = async (what: "cancel" | "retry" | "failed") => {
    if (busy) return;
    setBusy(what);
    try {
      const r = await post(`/api/jobs/${jid}/${what === "cancel" ? "cancel" : "retry"}`, what === "cancel" ? {} : { idempotency_key: retryKey.current[what], failed_only: what === "failed" });
      if (what === "cancel" && r?.cancel && !r.cancel.accepted) {
        toast(r.cancel.reason === "already_terminal" ? tr("任务已经结束，无需取消") : tr("已经请求过取消，正在停止…"));
      } else {
        toast(what === "cancel" ? tr("已请求取消，正在停止…") : what === "failed" ? tr("已重新排队失败项") : tr("已重新排队未完成项"), "ok");
      }
      if (what !== "cancel") retryKey.current[what] = newKey();
      // Restart the one polling effect rather than starting a second loop beside it. The old
      // loop here had no unmount guard, so leaving the page left it polling and writing state
      // into a component that no longer existed — and cancelling then retrying ran two of them.
      setPollKey((k) => k + 1);
    } catch (e: any) {
      const wait = e?.code === "queue_full" && e?.detail?.retry_after ? tr("（约 {v1} 秒后再试）", { v1: e.detail.retry_after }) : "";
      toast(errorText(e) + wait, isPermissionError(e) ? "info" : "err");
      setPollKey((k) => k + 1);           // show the server's current state, whatever it is now
    } finally {
      setBusy(null);
    }
  };

  if (err) return <PageState role="alert"><div className="notice err">{serverText(err)}</div><Link to={`/p/${pid}`}>{tr("返回项目")}</Link></PageState>;
  if (!job) return <PageState>{tr("加载中…")}</PageState>;
  const c = job.counts || {};
  const { pct, done, total } = progress(job);
  const [stText, stKind] = jobStatus(job.status);
  const fmts: string[] = job.options?.formats || [];
  // Large batches are planned by the runner first; counts.planned / counts.total is its progress.
  const pp = planningOf(job);
  const planning = !!pp;
  const halt = haltText(job.halt_reason);
  const shownTotal = filter ? c[filter] || 0 : c.total || 0;
  const setF = (k: string) => { setFilter(k); setOffset(0); };

  return (
    <div className="app">
      <div className="topbar">
        <LanguageMenu />
        <Link to="/" className="brand" aria-label={tr("Synwevo Docs 首页")}><BrandMark product="Docs" hideTextOnMobile /></Link>
        <span className="sep" />
        <Link to={`/p/${pid}`} className="btn ghost sm">{tr("← 返回项目")}</Link>
        <span className="grow ellipsis"><b>{tr("生成任务")}</b> <span className="muted small mono">{job.id}</span></span>
        <span className={`badge ${stKind}`} role="status">{planning ? tr("规划中") : stText}</span>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home" style={{ paddingTop: 16 }}>
          <div className="card card-b col gap12">
            <div className="row wrap">
              <div className="grow">
                <div className="small muted">{tr("模板「")}{job.template.name}」v{job.template.version_no}{tr(" · 来源文件 ")}{formatNumber(job.source_count)}{tr(" 个 · 格式 ")}{fmts.map((f) => f.toUpperCase()).join(" + ")}{tr(" · 创建于 ")}{fmtTime(job.created_at)}</div>
                <div className="small muted">{tr("输入在任务开始时已固定（文件、模板版本、映射与样本规则的快照），之后的修改不会影响本任务。")}</div>
              </div>
              {perm.canWrite && canCancel(job) && (
                <button type="button" className="btn danger" disabled={!!busy} onClick={() => act("cancel")}>{busy === "cancel" ? tr("正在请求取消…") : tr("取消任务")}</button>
              )}
              {canRetry(job, perm.canWrite) && (
                <>
                  {canRetry(job, perm.canWrite, true) && <button type="button" className="btn" disabled={!!busy} onClick={() => act("failed")}>{busy === "failed" ? tr("正在排队…") : tr("仅重试失败项")}</button>}
                  {(c.cancelled > 0 || (job.phase === "planning" && (c.unplanned || c.total - c.planned) > 0)) && <button type="button" className="btn" disabled={!!busy} title={tr("重试失败、已取消以及尚未规划的样本；不包含跳过项")} onClick={() => act("retry")}>{busy === "retry" ? tr("正在排队…") : tr("继续未完成项")}</button>}
                </>
              )}
            </div>
            {halt && <div className="notice warn" role="alert">{halt}</div>}
            {job.status === "cancelling" && <div className="notice small">{tr("正在取消：不再开始新的样本，进行中的一份完成后停止并保留结果。")}</div>}
            {planning && (
              <div className="notice info small" role="status">{tr("正在检查样本并规划本次任务")}{pp && pp.total ? tr("：已规划 {v1} / {v2} 个样本", { v1: pp.planned, v2: pp.total }) : "…"}{tr("。规划完成后开始生成。")}</div>
            )}
            <div className={`progress${job.status === "completed" ? " ok" : ""}`} role="progressbar" aria-label={planning ? tr("规划进度") : tr("生成进度")}
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={pp ? pp.pct : pct}
              aria-valuetext={pp ? tr("已规划 {v1} / {v2}", { v1: pp.planned, v2: pp.total }) : tr("已处理 {v1} / {v2}", { v1: done, v2: total })}>
              <div style={{ width: `${pp ? pp.pct : pct}%` }} />
            </div>
            <div className="stats">
              <Stat k={tr("样本")} v={c.total || 0} />
              <Stat k={tr("成功")} v={c.success || 0} color="var(--ok)" />
              <Stat k={tr("失败")} v={c.failed || 0} color={c.failed ? "var(--err)" : undefined} />
              <Stat k={tr("跳过（生成前错误）")} v={c.skipped || 0} color={c.skipped ? "var(--warn)" : undefined} />
              <Stat k={tr("取消 / 等待")} v={(c.cancelled || 0) + (c.pending || 0) + (c.running || 0) + (c.unplanned || 0)} />
              <Stat k={tr("输出文件")} v={c.files || 0} />
            </div>
            <div className="row wrap">
              {perm.canExport ? (
                <>
                  {c.files ? <DownloadLink className="btn primary" href={`/api/jobs/${jid}/zip`}>{tr("⬇ 下载全部（ZIP）")}</DownloadLink>
                    : <button type="button" className="btn primary" disabled title={tr("还没有输出文件")}>{tr("⬇ 下载全部（ZIP）")}</button>}
                  {fmts.length > 1 && c.files > 0 && fmts.map((f) => <DownloadLink key={f} className="btn" href={`/api/jobs/${jid}/zip?fmt=${f}`}>{tr("仅 ")}{f.toUpperCase()}</DownloadLink>)}
                  <DownloadLink className="btn" href={`/api/jobs/${jid}/report.csv`}>{tr("⬇ 结果清单与失败说明（CSV）")}</DownloadLink>
                </>
              ) : <span className="small muted">{tr(NO_EXPORT_TEXT)}</span>}
              <span className="spacer" />
              <div className="seg" role="group" aria-label={tr("按状态筛选")}>
                {FILTERS.map(([k, v]) => (
                  <button type="button" key={k} className={filter === k ? "on" : ""} aria-pressed={filter === k} onClick={() => setF(k)}>{tr(v)}{k ? ` ${c[k] || 0}` : ""}</button>
                ))}
              </div>
            </div>
            {!perm.canWrite && (c.failed || c.cancelled) > 0 && <div className="tiny muted">{tr(READ_ONLY_TEXT)}</div>}
          </div>
          <div className="card mt12" style={{ overflow: "auto" }}>
            {itemsErr && <div className="notice err" role="alert">{tr("样本列表加载失败：")}{serverText(itemsErr)}</div>}
            <table className="tbl" aria-label={tr("样本与输出文件")}>
              <thead><tr><th style={{ width: 50 }}>#</th><th>{tr("样本")}</th><th style={{ width: 90 }}>{tr("状态")}</th><th>{tr("输出文件")}</th><th>{tr("说明")}</th></tr></thead>
              <tbody>
                {items.map((it) => {
                  const [t, k] = itemStatus(it.status);
                  const iss = (it.issues || []).filter((x: any) => x.level === "error" || x.level === "warning");
                  return (
                    <tr key={it.id}>
                      <td>{formatNumber(it.seq + 1)}</td>
                      <td className="small">{it.label}</td>
                      <td><span className={`badge ${k}`}>{t}</span>{it.attempts > 1 && <div className="tiny muted">{tr("第 {count} 次", { count: it.attempts })}</div>}</td>
                      <td className="small">
                        {it.artifacts.map((a: any) => {
                          const vb = verificationBadge(a.verification);
                          return (
                            <div key={a.id} className="row gap4">
                              {perm.canExport ? <DownloadLink href={`/api/artifacts/${a.id}`}>{a.filename}</DownloadLink> : <span>{a.filename}</span>}
                              <span className="tiny muted">{fmtSize(a.size)}</span>
                              {vb && <span className={`badge ${vb.tone}`} title={vb.title}>{vb.label}</span>}
                            </div>
                          );
                        })}
                      </td>
                      <td className="small">
                        {iss.slice(0, open === it.id ? 50 : 2).map((x: any, i: number) => { const fix = fixTargetOf(x); return <div key={i} className={x.level === "error" ? "" : "muted"} style={{ color: x.level === "error" ? "var(--err)" : undefined }}>{issueText(x)}
                          {fix && <Link className="btn xs ghost" style={{ marginLeft: 6 }} to={`/p/${pid}?locate=${fix.kind}:${encodeURIComponent(fix.id)}`}>{{ slot: tr("定位到字段"), mapping: tr("定位到映射"), source: tr("定位到文件") }[fix.kind]} ↗</Link>}</div>; })}
                        {iss.length > 2 && <button type="button" className="btn xs ghost" aria-expanded={open === it.id} onClick={() => setOpen(open === it.id ? null : it.id)}>{open === it.id ? tr("收起") : tr("还有 {v1} 条", { v1: iss.length - 2 })}</button>}
                      </td>
                    </tr>
                  );
                })}
                {!items.length && !itemsErr && <tr><td colSpan={5} className="muted" style={{ textAlign: "center", padding: 24 }}>{planning ? tr("规划完成后显示样本") : tr("没有记录")}</td></tr>}
              </tbody>
            </table>
          </div>
          {shownTotal > PAGE && (
            <div className="row mt8 tiny muted">
              <span className="grow">{tr("第 {start}–{end} 条，共 {total} 条", { start: offset + 1, end: Math.min(offset + items.length, shownTotal), total: shownTotal })}</span>
              <button type="button" className="btn sm" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>{tr("‹ 上一页")}</button>
              <button type="button" className="btn sm" disabled={offset + PAGE >= shownTotal} onClick={() => setOffset(offset + PAGE)}>{tr("下一页 ›")}</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ k, v, color }: { k: string; v: number; color?: string }) {
  useI18n();
  return <div className="statbox"><div className="v" style={{ color }}>{formatNumber(v)}</div><div className="k">{k}</div></div>;
}
