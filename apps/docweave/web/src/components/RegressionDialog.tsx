import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "@platform/auth-client";
import type { Project } from "../lib/types";
import { Modal } from "@platform/ui";

/** Freeze the current project (files, template, rule, mappings) with its expected results. */
export default function RegressionDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const [total, setTotal] = useState(0);
  useEffect(() => { get(`/api/projects/${project.id}/records?limit=1`).then((r) => setTotal(r.total)).catch(() => setTotal(0)); }, [project.id]);
  const [name, setName] = useState(project.name);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<any>(null);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      setDone(await post(`/api/projects/${project.id}/regression-cases`, { name, note }));
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title="保存为回归用例" onClose={onClose}
      footer={done ? <><span className="spacer" /><Link className="btn" to="/regression">查看回归用例</Link><button className="btn primary" onClick={onClose}>完成</button></>
        : <>{err && <div className="notice err grow">{err}</div>}<span className="spacer" /><button className="btn" onClick={onClose}>取消</button><button className="btn primary" disabled={busy || !name.trim()} onClick={save}>{busy ? "正在生成期望结果…" : "保存用例"}</button></>}>
      {done ? (
        <div className="col gap8">
          <div className="notice ok">已保存「{done.name}」：{done.samples} 个样本的期望结果（共 {done.records_total} 个样本）。</div>
          <div className="small muted">用例保存在 <code>regression/{done.id}/</code>，可以和代码一起提交；<code>python scripts/regression.py</code> 与 pytest 都会自动运行它。</div>
        </div>
      ) : (
        <div className="col gap12">
          <div className="notice info small">
            保存当前项目的来源文件、模板、样本规则和映射，并记录前 {Math.min(total, 50)} 个样本（共 {total} 个）的期望结果：每个字段的值和输出文档的文字。
            以后每次升级或修改代码，都可以重新运行并逐项比较差异。<b>请只保存测试数据或已脱敏的文件</b>（可先用「生成脱敏副本」）。
          </div>
          <label className="field">用例名称<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field">说明（可选）<textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="例如：2026 年第三季度真实订单（已脱敏），含 3 个跨页订单" /></label>
        </div>
      )}
    </Modal>
  );
}
