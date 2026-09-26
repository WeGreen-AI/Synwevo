/** Approvals: requests to copy a personal Synwevo Docs project into one of this organization's
 *  workspaces. The approver sees the frozen manifest (names, sizes, hashes, counts) — never the
 *  personal workspace — and decides with a reason under re-authentication. Only a holder of
 *  org.approvals.decide who also administers the target workspace can decide (the server says
 *  so per request in `can_decide`). */
import { useEffect, useRef, useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading } from "./AuthFlow";
import OrganizationAction from "./OrganizationAction";
import { orgPath, type Organization } from "./types";

interface Approval {
  id: string; source_project_id: string; target_workspace_id: string; target_workspace_name: string | null; name: string; purpose: string | null;
  scope: { sources: boolean; corrections: boolean }; status: string; decided_at: number | null; decision_reason: string | null; self_approved: boolean;
  approval_expires_at: number | null; executed_at: number | null; result: { project_id?: string; sources?: number; mappings?: number; warnings?: string[] } | null;
  error: string | null; version: number; created_at: number; expires_at: number; can_decide: boolean;
  requester: { user_id: string; name: string } | null; decider: { user_id: string; name: string } | null;
  manifest: { project: { name: string; step?: string }; template: { name: string; format?: string; version_no: number; pinned: boolean; slots: number; scan?: string | null } | null;
    mappings: { rev: number; count: number; enabled: number }; sources: { included: boolean; count: number; total_bytes: number; items: { filename: string; size: number; format?: string; sha256?: string; scan?: string | null }[] };
    corrections: { included: boolean }; excluded: string[] };
}

// What the requester's files went through before they were accepted — the same vocabulary the
// Docs file list uses; `clean` is not shown, anything else is spelled out for the approver.
const SCAN: Record<string, string> = { pending: "等待检查", not_configured: "未配置扫描", error: "检查失败", too_large: "超出扫描大小", infected: "检出威胁" };
const STATUS: Record<string, string> = { pending: "待审批", approved: "已批准，等待复制", rejected: "已拒绝", withdrawn: "已撤回", expired: "已过期", running: "复制中", done: "已完成", failed: "失败", stale: "需重新申请" };
function fmtSize(n: number) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(1)} MB`; }

export default function OrganizationApprovals({ organization, mine }: { organization: Organization; mine: string[] }) {
  const { t, locale } = useI18n();
  const [items, setItems] = useState<Approval[] | null>(null); const [error, setError] = useState<unknown>(null); const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState<"pending" | "">("pending"); const [open, setOpen] = useState<string | null>(() => new URLSearchParams(window.location.search).get("approval"));
  const [action, setAction] = useState<{ item: Approval; kind: "approve" | "reject" } | null>(null);
  const can = mine.includes("org.approvals.decide");
  useEffect(() => {
    if (!can) return;
    const abort = new AbortController(); setError(null);
    get<{ items: Approval[] }>(`${orgPath(organization.id)}/approvals${status ? `?status=${status}` : ""}`, abort.signal).then(x => setItems(x.items)).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [organization.id, status, revision, can]);
  // a deep link to an approval that is no longer pending moves to 全部 once; after that the tabs are the reader's
  const widened = useRef(false);
  useEffect(() => { if (open && items && status === "pending" && !widened.current && !items.some(i => i.id === open)) { widened.current = true; setStatus(""); } }, [open, items, status]);
  if (!can) return null;
  const when = (ts: number | null) => ts ? new Date(ts * 1000).toLocaleString(locale) : "—";
  return <section className="org-section" id="org-approvals"><h2>{t("审批")}</h2>
    <p className="tiny muted">{t("个人空间的项目要复制到本组织的工作空间，需要一位持有审批权限并管理目标空间的成员同意。你看到的是申请人确认过的清单，不是对方的个人空间。")}</p>
    <FlowError error={error} />
    <div className="row wrap gap4" role="tablist">
      <button type="button" role="tab" aria-selected={status === "pending"} className={`btn ${status === "pending" ? "primary" : ""}`} onClick={() => setStatus("pending")}>{t("待审批")}</button>
      <button type="button" role="tab" aria-selected={status === ""} className={`btn ${status === "" ? "primary" : ""}`} onClick={() => setStatus("")}>{t("全部")}</button>
    </div>
    {!items ? <Loading /> : !items.length ? <p className="muted">{t(status === "pending" ? "没有待审批的申请。" : "还没有申请。")}</p>
      : <ul className="org-list">{items.map(a => { const m = a.manifest; const isOpen = open === a.id; return <li key={a.id} id={`approval-${a.id}`}>
        <div><strong>{a.name}</strong> <span className="muted">→ {a.target_workspace_name || a.target_workspace_id}</span>
          <div className="tiny muted">{t("申请人：{who} · {when}", { who: a.requester?.name || "—", when: when(a.created_at) })}{a.name !== m.project.name && <> · {t("源项目「{name}」", { name: m.project.name })}</>}{a.purpose && <> · {a.purpose}</>}</div>
          <div className="row wrap gap4"><span className={`badge ${a.status === "pending" ? "warn" : a.status === "done" ? "ok" : a.status === "rejected" || a.status === "failed" || a.status === "stale" ? "err" : ""}`}>{t(STATUS[a.status] || a.status)}</span>
            {a.self_approved && <span className="tiny muted">{t("申请人自行批准")}</span>}{a.decider && a.status !== "pending" && <span className="tiny muted">{t("由 {who} 决定", { who: a.decider.name })}{a.decision_reason && <>：{a.decision_reason}</>}</span>}</div>
          <button type="button" className="btn ghost sm" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : a.id)}>{t(isOpen ? "收起清单" : "查看清单")}</button>
          {isOpen && <dl className="flow-facts small">
            <dt>{t("模板")}</dt><dd>{m.template ? `${m.template.name} · v${m.template.version_no}${m.template.pinned ? t("（已固定）") : ""} · ${t("{n} 个字段", { n: m.template.slots })}` : t("未设置")}</dd>
            <dt>{t("映射")}</dt><dd>{t("修订 {rev}，{n} 条（启用 {e}）", { rev: m.mappings.rev, n: m.mappings.count, e: m.mappings.enabled })}</dd>
            <dt>{t("来源数据")}</dt><dd>{m.sources.included ? t("包含 {n} 个文件，共 {size}", { n: m.sources.count, size: fmtSize(m.sources.total_bytes) }) : t("不包含（只复制配置与模板；源项目有 {n} 个文件）", { n: m.sources.count })}
              {m.sources.included && m.sources.items.length > 0 && <ul className="tiny muted">{m.sources.items.slice(0, 20).map((s, i) => <li key={i}>{s.filename} · {fmtSize(s.size)}{s.scan && s.scan !== "clean" ? ` · ${t("安全检查：{status}", { status: t(SCAN[s.scan] || s.scan) })}` : ""}</li>)}</ul>}</dd>
            <dt>{t("修正与识别文字")}</dt><dd>{t(m.corrections.included ? "包含" : "不包含")}</dd>
            <dt>{t("不复制")}</dt><dd>{t("任务、产物、上传、授权、Webhook、API 凭据与原项目来源记录")}</dd>
            {a.result?.project_id && <><dt>{t("副本")}</dt><dd>{t("项目 {id}，{s} 个来源，{m} 条映射", { id: a.result.project_id, s: a.result.sources ?? 0, m: a.result.mappings ?? 0 })}{a.result.warnings?.length ? ` · ${a.result.warnings.join(", ")}` : ""}</dd></>}
            {a.error && <><dt>{t("原因")}</dt><dd>{a.error}</dd></>}
          </dl>}
        </div>
        {a.can_decide && <div className="org-actions">
          <button type="button" className="btn primary" onClick={() => setAction({ item: a, kind: "approve" })}>{t("批准")}</button>
          <button type="button" className="btn danger" onClick={() => setAction({ item: a, kind: "reject" })}>{t("拒绝")}</button>
        </div>}
      </li>; })}</ul>}
    {action && <OrganizationAction key={action.item.id + action.kind} spec={{
      title: t(action.kind === "approve" ? "批准复制" : "拒绝复制"),
      impact: t(action.kind === "approve"
        ? "批准后副本将按清单在「{workspace}」创建，受该空间的成员与保留策略管理；源项目之后的改动不会带入。批准 7 天内有效。"
        : "拒绝后申请人会收到你的理由，可以修改后重新申请。", { workspace: action.item.target_workspace_name || action.item.target_workspace_id }),
      danger: action.kind === "reject",
      run: reason => post(`${orgPath(organization.id)}/approvals/${action.item.id}/decide`, { action: action.kind, reason, version: action.item.version }),
    }} onClose={() => setAction(null)} onDone={() => { setAction(null); setRevision(v => v + 1); }} />}
  </section>;
}
