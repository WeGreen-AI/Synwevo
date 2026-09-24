import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get, post, qs } from "@synwevo/auth-client";
import { fmtSize, fmtTime, useToast } from "@synwevo/ui";

import { ITEM_STATUS as IST, JOB_STATUS as ST } from "../lib/status";

export default function JobPage() {
  const { pid, jid } = useParams();
  const toast = useToast();
  const [job, setJob] = useState<any>(null);
  const [items, setItems] = useState<any[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [pollKey, setPollKey] = useState(0);
  const seen = useRef(false);            // read inside the recursing tick, where `job` would be stale

  /** Returns the job, or "retry" when the failure says nothing about the job itself.
   *
   *  This page polls every 900ms while a job runs, which is exactly the traffic a CDN rate-limit
   *  or bot rule trips on. Treating one such failure as an answer used to end the poll for good:
   *  the tick only rescheduled itself when it got a job back, so progress froze at whatever it
   *  last showed — on a job that was still running fine on the server. */
  const load = async (): Promise<any | "retry" | null> => {
    try {
      const j = await get(`/api/jobs/${jid}`);
      setJob(j);
      seen.current = true;
      setItems(await get(`/api/jobs/${jid}/items${qs({ limit: 5000 })}`));
      setErr(null);
      return j;
    } catch (e: any) {
      const transient = e instanceof ApiError && (e.upstream || e.status === 0 || e.status >= 500);
      setErr(transient && seen.current ? null : e.message);   // keep showing progress rather than a blank page
      return transient ? "retry" : null;
    }
  };
  useEffect(() => {
    let alive = true;
    let t: any;
    const tick = async () => {
      const j = await load();
      if (!alive) return;
      if (j === "retry" || (j && ["queued", "running"].includes(j.status))) t = setTimeout(tick, 900);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [jid, pollKey]);

  const c = job?.counts || {};
  const done = (c.success || 0) + (c.failed || 0) + (c.skipped || 0) + (c.cancelled || 0);
  const pct = c.total ? Math.round((done / c.total) * 100) : 0;
  const shown = useMemo(() => (filter ? items.filter((i) => i.status === filter) : items), [items, filter]);

  const act = async (what: "cancel" | "retry") => {
    try {
      await post(`/api/jobs/${jid}/${what}`, {});
      toast(what === "cancel" ? "已请求取消，正在停止…" : "已重新排队失败项", "ok");
      // Restart the one polling effect rather than starting a second loop beside it. The old
      // loop here had no unmount guard, so leaving the page left it polling and writing state
      // into a component that no longer existed — and cancelling then retrying ran two of them.
      setPollKey((k) => k + 1);
    } catch (e: any) {
      toast(e.message, "err");
    }
  };

  if (err) return <div className="home"><div className="notice err">{err}</div><Link to={`/p/${pid}`}>返回项目</Link></div>;
  if (!job) return <div className="empty">加载中…</div>;
  const [stText, stKind] = ST[job.status] || [job.status, ""];
  const fmts: string[] = job.options?.formats || [];

  return (
    <div className="app">
      <div className="topbar">
        <Link to="/" className="brand"><span className="logo">S</span><span className="desktop-only">Synwevo Docs</span></Link>
        <span className="sep" />
        <Link to={`/p/${pid}`} className="btn ghost sm">← 返回项目</Link>
        <span className="grow ellipsis"><b>生成任务</b> <span className="muted small mono">{job.id}</span></span>
        <span className={`badge ${stKind}`}>{stText}</span>
      </div>
      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home" style={{ paddingTop: 16 }}>
          <div className="card card-b col gap12">
            <div className="row wrap">
              <div className="grow">
                <div className="small muted">模板「{job.template.name}」v{job.template.version_no} · 来源文件 {job.source_count} 个 · 格式 {fmts.map((f) => f.toUpperCase()).join(" + ")} · 创建于 {fmtTime(job.created_at)}</div>
                <div className="small muted">输入在任务开始时已固定（文件、模板版本、映射与样本规则的快照），之后的修改不会影响本任务。</div>
              </div>
              {["queued", "running"].includes(job.status) && <button className="btn danger" onClick={() => act("cancel")}>取消任务</button>}
              {!["queued", "running"].includes(job.status) && (c.failed || c.cancelled) > 0 && <button className="btn" onClick={() => act("retry")}>重试失败 / 已取消项</button>}
            </div>
            <div className={`progress${job.status === "completed" ? " ok" : ""}`}><div style={{ width: `${pct}%` }} /></div>
            <div className="stats">
              <Stat k="样本" v={c.total || 0} />
              <Stat k="成功" v={c.success || 0} color="var(--ok)" />
              <Stat k="失败" v={c.failed || 0} color={c.failed ? "var(--err)" : undefined} />
              <Stat k="跳过（生成前错误）" v={c.skipped || 0} color={c.skipped ? "var(--warn)" : undefined} />
              <Stat k="取消 / 等待" v={(c.cancelled || 0) + (c.pending || 0) + (c.running || 0)} />
              <Stat k="输出文件" v={c.files || 0} />
            </div>
            <div className="row wrap">
              <a className={`btn primary${c.files ? "" : " disabled"}`} href={c.files ? `/api/jobs/${jid}/zip` : undefined} aria-disabled={!c.files}>⬇ 下载全部（ZIP）</a>
              {fmts.length > 1 && fmts.map((f) => <a key={f} className="btn" href={`/api/jobs/${jid}/zip?fmt=${f}`}>仅 {f.toUpperCase()}</a>)}
              <a className="btn" href={`/api/jobs/${jid}/report.csv`}>⬇ 结果清单与失败说明（CSV）</a>
              <span className="spacer" />
              <div className="seg">
                {[["", "全部"], ["success", "成功"], ["failed", "失败"], ["skipped", "跳过"], ["cancelled", "取消"]].map(([k, v]) => (
                  <button key={k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{v}{k ? ` ${c[k] || 0}` : ""}</button>
                ))}
              </div>
            </div>
          </div>
          <div className="card mt12" style={{ overflow: "auto" }}>
            <table className="tbl">
              <thead><tr><th style={{ width: 50 }}>#</th><th>样本</th><th style={{ width: 90 }}>状态</th><th>输出文件</th><th>说明</th></tr></thead>
              <tbody>
                {shown.map((it) => {
                  const [t, k] = IST[it.status] || [it.status, ""];
                  const iss = (it.issues || []).filter((x: any) => x.level === "error" || x.level === "warning");
                  return (
                    <tr key={it.id}>
                      <td>{it.seq + 1}</td>
                      <td className="small">{it.label}</td>
                      <td><span className={`badge ${k}`}>{t}</span>{it.attempts > 1 && <div className="tiny muted">第 {it.attempts} 次</div>}</td>
                      <td className="small">
                        {it.artifacts.map((a: any) => (
                          <div key={a.id} className="row gap4">
                            <a href={`/api/artifacts/${a.id}`}>{a.filename}</a>
                            <span className="tiny muted">{fmtSize(a.size)}</span>
                            <span className="badge ok" title={`已用 ${a.verification?.engine} 重新打开并核对 ${a.verification?.checks} 个值`}>✓ 已校验</span>
                          </div>
                        ))}
                      </td>
                      <td className="small">
                        {iss.slice(0, open === it.id ? 50 : 2).map((x: any, i: number) => <div key={i} className={x.level === "error" ? "" : "muted"} style={{ color: x.level === "error" ? "var(--err)" : undefined }}>{x.message}</div>)}
                        {iss.length > 2 && <button className="btn xs ghost" onClick={() => setOpen(open === it.id ? null : it.id)}>{open === it.id ? "收起" : `还有 ${iss.length - 2} 条`}</button>}
                      </td>
                    </tr>
                  );
                })}
                {!shown.length && <tr><td colSpan={5} className="muted" style={{ textAlign: "center", padding: 24 }}>没有记录</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

function Stat({ k, v, color }: { k: string; v: number; color?: string }) {
  return <div className="statbox"><div className="v" style={{ color }}>{v}</div><div className="k">{k}</div></div>;
}
