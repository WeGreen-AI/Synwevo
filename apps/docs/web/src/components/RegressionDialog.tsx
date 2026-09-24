import { formatNumber } from "@synwevo/ui";
import { serverText, issueText } from "../lib/presentation";
import { t as tr, useI18n } from "@synwevo/ui";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post } from "@synwevo/auth-client";
import type { Project } from "../lib/types";
import { Modal } from "@synwevo/ui";
import { errorText } from "../lib/perm";

/** Freeze the current project (files, template, rule, mappings) with its expected results. */
export default function RegressionDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  useI18n();
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
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={tr("保存为回归用例")} onClose={onClose}
      footer={done ? <><span className="spacer" /><Link className="btn" to="/regression">{tr("查看回归用例")}</Link><button type="button" className="btn primary" onClick={onClose}>{tr("完成")}</button></>
        : <>{err && <div className="notice err grow" role="alert">{serverText(err)}</div>}<span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("取消")}</button><button type="button" className="btn primary" disabled={busy || !name.trim()} onClick={save}>{busy ? tr("正在生成期望结果…") : tr("保存用例")}</button></>}>
      {done ? (
        <div className="col gap8">
          <div className="notice ok">{tr("已保存「")}{done.name}」：{formatNumber(done.samples)}{tr(" 个样本的期望结果（共 ")}{formatNumber(done.records_total)}{tr(" 个样本）。")}</div>
          {/* It used to say the case sits in regression/ "and can be committed with the code":
              the case holds this project's own files, and user files do not belong in a
              repository. Where the server keeps cases is its configuration, not the UI's claim. */}
          <div className="small muted">{tr("用例 ")}<code>{done.id}</code>{tr(" 已保存在服务器上，可以在「回归用例」页面运行和下载。其中包含本项目的文件：请只保存测试数据或已脱敏的文件，不要把它提交到代码仓库。")}</div>
        </div>
      ) : (
        <div className="col gap12">
          <div className="notice info small">{tr("保存当前项目的来源文件、模板、样本规则和映射，并记录前 ")}{formatNumber(Math.min(total, 50))}{tr(" 个样本（共 ")}{formatNumber(total)}{tr(" 个）的期望结果：每个字段的值和输出文档的文字。 以后每次升级或修改代码，都可以重新运行并逐项比较差异。")}<b>{tr("请只保存测试数据或已脱敏的文件")}</b>{tr("（可先用「生成脱敏副本」）。")}</div>
          <label className="field">{tr("用例名称")}<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
          <label className="field">{tr("说明（可选）")}<textarea className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder={tr("例如：2026 年第三季度真实订单（已脱敏），含 3 个跨页订单")} /></label>
        </div>
      )}
    </Modal>
  );
}
