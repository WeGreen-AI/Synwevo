import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "@synwevo/auth-client";
import { useDialog, useToast } from "@synwevo/ui";
import { useSession } from "@synwevo/auth-client";
import { errorText, isPermissionError, NO_EXPORT_TEXT, permissions, READ_ONLY_TEXT } from "../lib/perm";

export default function RegressionPage() {
  const [cases, setCases] = useState<any[] | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  const [running, setRunning] = useState<string | null>(null);
  const toast = useToast();
  const { confirm } = useDialog();
  const session = useSession();
  const perm = permissions(session.me, session.disabled);
  const [err, setErr] = useState<string | null>(null);
  const load = () => get("/api/regression-cases").then((c) => { setCases(c); setErr(null); }).catch((e) => setErr(errorText(e)));
  useEffect(() => { load(); }, []);
  const runOne = async (id: string) => {
    setRunning(id);
    try {
      const r = await post(`/api/regression-cases/${id}/run`);
      setResults((x) => ({ ...x, [id]: r }));
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
    } finally {
      setRunning(null);
    }
  };
  const runAll = async () => { for (const c of cases || []) await runOne(c.id); };
  const remove = async (c: any) => {
    if (!(await confirm("删除回归用例", `删除「${c.name}」？其中保存的文件和期望结果会一起删除。`, "删除", true))) return;
    try {
      await del(`/api/regression-cases/${c.id}`);
    } catch (e: any) {
      toast(errorText(e), isPermissionError(e) ? "info" : "err");
    }
    load();
  };
  return (
    <div className="page-narrow">
      <div className="row mb12">
        <Link className="btn ghost sm" to="/">← 首页</Link>
        <h1 className="grow" style={{ margin: 0 }}>回归用例</h1>
        {/* POST …/run is a write route (authz ROUTE_POLICY): a viewer would only be refused. */}
        <button type="button" className="btn primary" disabled={!perm.canWrite || !cases?.length || !!running} onClick={runAll}
          title={perm.canWrite ? undefined : READ_ONLY_TEXT}>全部运行</button>
      </div>
      <div className="notice small mb12">
        回归用例保存了项目的原始文件（不做脱敏）、模板、样本规则、映射，以及每个样本的期望结果，只在本工作空间内可见。运行时用当前代码重新解析和生成，逐个字段、逐段文字比较；
        也可以在仓库根目录运行 <code>python apps/docs/scripts/regression.py</code>。
      </div>
      {err && <div className="notice err mb12" role="alert">回归用例加载失败：{err}</div>}
      {cases && !cases.length && <div className="empty"><div className="big">还没有回归用例</div><div>在项目中点击「更多 → 保存为回归用例」即可创建</div></div>}
      <div className="col gap8">
        {cases?.map((c) => {
          const r = results[c.id];
          return (
            <div key={c.id} className="card">
              <div className="card-h">
                <b className="grow">{c.name}</b>
                {r && <span className={`badge ${r.ok ? "ok" : "err"}`}>{r.ok ? `✓ 全部一致（${r.passed}/${r.samples.length}）` : `✖ ${r.samples.length - (r.passed || 0)} 个样本有差异`}</span>}
                <button type="button" className="btn sm" disabled={!perm.canWrite || running === c.id} onClick={() => runOne(c.id)}
                  title={perm.canWrite ? undefined : READ_ONLY_TEXT}>{running === c.id ? "运行中…" : "运行"}</button>
                {perm.canExport ? <a className="btn sm" href={`/api/regression-cases/${c.id}/download`}>下载</a> : <span className="tiny muted" title={NO_EXPORT_TEXT}>无下载权限</span>}
                {perm.canWrite && <button type="button" className="btn sm danger" onClick={() => remove(c)}>删除</button>}
              </div>
              <div className="small muted" style={{ padding: "8px 14px" }}>
                {c.created_at} · 模板「{c.template}」（{String(c.format).toUpperCase()}）· 来源 {c.sources.length} 个 · 样本 {c.samples}/{c.records_total}{c.note ? ` · ${c.note}` : ""}
              </div>
              {r && !r.ok && (
                <div style={{ padding: "0 14px 12px" }}>
                  {r.errors.map((e: string, i: number) => <div key={i} className="notice err small">{e}</div>)}
                  {r.samples.filter((s: any) => !s.ok).map((s: any) => (
                    <details key={s.key} className="mt8">
                      <summary className="small"><b>{s.label}</b>：{s.diffs.length} 处差异</summary>
                      <pre className="diffbox">{s.diffs.join("\n\n")}</pre>
                    </details>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
