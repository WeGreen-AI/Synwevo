/** "Copy to an organization workspace": the requester picks a target they can already write
 *  to, names the copy, chooses whether the source files (and their corrections) go along, sees
 *  the manifest that will be frozen with the request, and submits. When the requester also holds
 *  the deciding authority on the target, the same dialog can approve at once (recorded as a
 *  self-approval); otherwise the target's approvers are told and the request waits. */
import { useEffect, useState } from "react";
import { get, post, useSession, setWorkspaceContext } from "@synwevo/auth-client";
import { Modal, t as tr, useI18n } from "@synwevo/ui";
import { errorText } from "../lib/perm";
import { copyErrorText } from "../lib/refusals";

interface Target { id: string; name: string; role: string; organization_id: string; organization_name: string; can_self_approve: boolean }
interface Manifest { project: { name: string }; template: { name: string; version_no: number; pinned: boolean; slots: number } | null; mappings: { rev: number; count: number; enabled: number };
  sources: { included: boolean; count: number; total_bytes: number; items: { filename: string; size: number }[] }; corrections: { included: boolean } }
interface CopyRequest { id: string; status: string; name: string; target_workspace_id: string; created_at: number; decision_reason: string | null; error: string | null;
  result: { project_id?: string } | null; decider: { name: string } | null; self_approved: boolean }

const STATUS: Record<string, string> = { pending: "等待审批", approved: "已批准，复制中", rejected: "已拒绝", withdrawn: "已撤回", expired: "已过期", running: "复制中", done: "已完成", failed: "失败", stale: "需重新申请" };
function fmtSize(n: number) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`; }

export default function CopyRequestDialog({ project, onClose }: { project: { id: string; name: string }; onClose: () => void }) {
  useI18n();
  const session = useSession();
  const [targets, setTargets] = useState<Target[] | null>(null); const [target, setTarget] = useState("");
  const [name, setName] = useState(project.name); const [purpose, setPurpose] = useState(""); const [sources, setSources] = useState(false); const [corrections, setCorrections] = useState(false);
  const [manifest, setManifest] = useState<Manifest | null>(null); const [requests, setRequests] = useState<CopyRequest[] | null>(null);
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false); const [selfApprove, setSelfApprove] = useState(false); const [reason, setReason] = useState("");
  const chosen = targets?.find(x => x.id === target) || null;
  const reload = () => get<{ items: CopyRequest[] }>(`/api/projects/${project.id}/copy-requests`).then(x => setRequests(x.items)).catch(e => setErr(errorText(e)));
  useEffect(() => {
    get<{ items: Target[] }>("/api/copy-targets").then(x => { setTargets(x.items); if (x.items.length === 1) setTarget(x.items[0].id); }).catch(e => setErr(errorText(e)));
    void reload();
  }, [project.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    get<{ manifest: Manifest }>(`/api/projects/${project.id}/copy-manifest?sources=${sources ? 1 : 0}&corrections=${corrections ? 1 : 0}`).then(x => setManifest(x.manifest)).catch(e => setErr(errorText(e)));
  }, [project.id, sources, corrections]);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try {
      await post(`/api/projects/${project.id}/copy-requests`, { target_workspace_id: target, name, purpose: purpose || undefined, scope: { sources, corrections: sources && corrections },
        ...(selfApprove && chosen?.can_self_approve ? { decide: "approve", reason } : {}) });
      await reload();
    } catch (e: any) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  const withdraw = async (r: CopyRequest) => { setBusy(true); try { await post(`/api/copy-requests/${r.id}/withdraw`, {}); await reload(); } catch (e: any) { setErr(errorText(e)); } finally { setBusy(false); } };
  const openCopy = async (r: CopyRequest) => {
    if (!r.result?.project_id) return;
    try { await post("/api/auth/workspace", { workspace_id: r.target_workspace_id }); setWorkspaceContext(r.target_workspace_id); window.location.assign(`/p/${r.result.project_id}`); }
    catch (e: any) { setErr(errorText(e)); }
  };
  const active = requests?.filter(r => ["pending", "approved", "running"].includes(r.status)) || [];
  return <Modal title={tr("复制到组织工作空间")} onClose={onClose} size="wide">
    <div className="col gap8">
      {err && <p className="notice err" role="alert">{err}</p>}
      <p className="tiny muted">{tr("副本在目标工作空间的管理员批准后创建，之后独立于原项目；原项目保持不变。")}</p>
      {requests && requests.length > 0 && <div className="col gap4"><h3 className="small">{tr("已有申请")}</h3>{requests.map(r => <div key={r.id} className="row wrap gap8 small" style={{ alignItems: "center" }}>
        <span className="badge">{tr(STATUS[r.status] || r.status)}</span><span>{r.name}</span><span className="muted">{new Date(r.created_at * 1000).toLocaleString(session.me?.locale || undefined)}</span>
        {r.decider && r.decision_reason && <span className="muted">{r.decider.name}：{r.decision_reason}</span>}{r.error && <span className="muted">{copyErrorText(r.error)}</span>}
        {(r.status === "pending" || r.status === "approved") && <button type="button" className="btn sm" disabled={busy} onClick={() => void withdraw(r)}>{tr("撤回")}</button>}
        {r.status === "done" && r.result?.project_id && <button type="button" className="btn sm primary" onClick={() => void openCopy(r)}>{tr("切换到目标空间并打开副本")}</button>}
      </div>)}</div>}
      {targets && !targets.length ? <p className="notice">{tr("你还不是任何组织工作空间的编辑成员。请先让组织把你加入目标空间（编辑成员或以上），再来申请复制。")}</p>
      : active.length > 0 ? <p className="notice">{tr("这个项目已有未完成的复制申请；撤回后才能再次申请。")}</p>
      : <form onSubmit={submit} className="col gap8">
        <label className="field"><span>{tr("目标工作空间")}</span><select className="input" value={target} required onChange={e => setTarget(e.target.value)} disabled={busy || !targets}>
          <option value="">{tr("请选择…")}</option>{(targets || []).map(x => <option key={x.id} value={x.id}>{x.organization_name} · {x.name}</option>)}</select></label>
        <label className="field"><span>{tr("副本名称")}</span><input className="input" value={name} maxLength={100} required onChange={e => setName(e.target.value)} disabled={busy} /></label>
        <label className="field"><span>{tr("用途说明（给审批人看，可选）")}</span><input className="input" value={purpose} maxLength={500} onChange={e => setPurpose(e.target.value)} disabled={busy} /></label>
        <fieldset className="col gap4"><legend className="small">{tr("复制内容")}</legend>
          <label className="check"><input type="checkbox" checked disabled /><span>{tr("项目配置、样本规则、映射与模板（始终复制）")}</span></label>
          <label className="check"><input type="checkbox" checked={sources} onChange={e => setSources(e.target.checked)} disabled={busy} /><span>{tr("来源数据文件（{n} 个）", { n: manifest?.sources.count ?? 0 })}</span></label>
          {sources && <label className="check"><input type="checkbox" checked={corrections} onChange={e => setCorrections(e.target.checked)} disabled={busy} /><span>{tr("来源文件的修正与识别文字")}</span></label>}
          <span className="tiny muted">{tr("不会复制：任务、产物、上传记录、授权、Webhook、API 凭据。")}</span>
        </fieldset>
        {manifest && <dl className="kv small">
          <dt>{tr("模板")}</dt><dd>{manifest.template ? `${manifest.template.name} · v${manifest.template.version_no}${manifest.template.pinned ? tr("（已固定）") : ""} · ${tr("{n} 个字段", { n: manifest.template.slots })}` : tr("未设置")}</dd>
          <dt>{tr("映射")}</dt><dd>{tr("修订 {rev}，{n} 条", { rev: manifest.mappings.rev, n: manifest.mappings.count })}</dd>
          <dt>{tr("来源")}</dt><dd>{manifest.sources.included ? tr("{n} 个文件，共 {size}", { n: manifest.sources.count, size: fmtSize(manifest.sources.total_bytes) }) : tr("不包含")}</dd>
        </dl>}
        {chosen?.can_self_approve && <div className="col gap4">
          <label className="check"><input type="checkbox" checked={selfApprove} onChange={e => setSelfApprove(e.target.checked)} disabled={busy} /><span>{tr("我也是目标空间的审批人：批准并复制（记录为自行批准）")}</span></label>
          {selfApprove && <label className="field"><span>{tr("批准理由")}</span><input className="input" value={reason} minLength={4} maxLength={500} required onChange={e => setReason(e.target.value)} disabled={busy} /></label>}
        </div>}
        <div className="row gap8"><button className="btn primary" disabled={busy || !target || !name.trim() || (selfApprove && reason.trim().length < 4)}>{tr(selfApprove && chosen?.can_self_approve ? "批准并复制" : "提交申请")}</button><button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></div>
      </form>}
    </div>
  </Modal>;
}
