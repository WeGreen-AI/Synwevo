import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "@synwevo/auth-client";
import { useDialog, useToast } from "@synwevo/ui";

export default function RegressionPage() {
  const [cases, setCases] = useState<any[] | null>(null);
  const [results, setResults] = useState<Record<string, any>>({});
  const [running, setRunning] = useState<string | null>(null);
  const toast = useToast();
  const { confirm } = useDialog();
  const load = () => get("/api/regression-cases").then(setCases).catch((e) => toast(e.message, "err"));
  useEffect(() => { load(); }, []);
  const runOne = async (id: string) => {
    setRunning(id);
    try {
      const r = await post(`/api/regression-cases/${id}/run`);
      setResults((x) => ({ ...x, [id]: r }));
    } catch (e: any) {
      toast(e.message, "err");
    } finally {
      setRunning(null);
    }
  };
  const runAll = async () => { for (const c of cases || []) await runOne(c.id); };
  const remove = async (c: any) => {
    if (!(await confirm("删除回归用例", `删除「${c.name}」？其中保存的文件和期望结果会一起删除。`, "删除", true))) return;
    await del(`/api/regression-cases/${c.id}`);
    load();
  };
  return (
    <div className="page-narrow">
      <div className="row mb12">
        <Link className="btn ghost sm" to="/">← 首页</Link>
        <h1 className="grow" style={{ margin: 0 }}>回归用例</h1>
        <button className="btn primary" disabled={!cases?.length || !!running} onClick={runAll}>全部运行</button>
      </div>
      <div className="notice small mb12">
        回归用例保存了真实（已脱敏）文件、模板、样本规则、映射，以及每个样本的期望结果。运行时用当前代码重新解析和生成，逐个字段、逐段文字比较；
        也可以在命令行运行 <code>python scripts/regression.py</code>，pytest 会自动运行 <code>regression/</code> 下的所有用例。
      </div>
      {cases && !cases.length && <div className="empty"><div className="big">还没有回归用例</div><div>在项目中点击「更多 → 保存为回归用例」即可创建</div></div>}
      <div className="col gap8">
        {cases?.map((c) => {
          const r = results[c.id];
          return (
            <div key={c.id} className="card">
              <div className="card-h">
                <b className="grow">{c.name}</b>
                {r && <span className={`badge ${r.ok ? "ok" : "err"}`}>{r.ok ? `✓ 全部一致（${r.passed}/${r.samples.length}）` : `✖ ${r.samples.length - (r.passed || 0)} 个样本有差异`}</span>}
                <button className="btn sm" disabled={running === c.id} onClick={() => runOne(c.id)}>{running === c.id ? "运行中…" : "运行"}</button>
                <a className="btn sm" href={`/api/regression-cases/${c.id}/download`}>下载</a>
                <button className="btn sm danger" onClick={() => remove(c)}>删除</button>
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
