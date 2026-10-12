import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { post } from "@synwevo/auth-client";
import { fmtTime, t as tr, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { useAdminData } from "./data";
import { permitted, type Held } from "./access";
import { WORKSPACE_STATUS, roleLabel } from "./labels";
import "./messages";
import "./ownership.messages";

/**
 * A37: the workspaces and organizations that have no usable owner, and the platform-controlled
 * way out — naming one of their *existing* members as owner.
 *
 * An owner counts only while the account is active and not deleted (and, for a workspace that
 * belongs to an organization, while the account is still a member of that organization). The
 * suspend/delete routes refuse to strand an object (D11), but orphans still arise from data that
 * predates that rule and from membership paths outside it. This panel lists them with the reason
 * (metadata only, never content) and offers the handover to a caller whose role manages the
 * object: an existing member, chosen here, becomes owner in one server transaction, with a reason,
 * a renewed identity proof and an audit record. Nothing is granted to the administrator — no
 * membership, no entitlement, no token — and the successor must be able to pass the second-factor
 * step-up that owner-only acts demand, which is why members without one are listed but cannot be
 * chosen. The server checks all of it again inside the write.
 */
export type OwnershipKind = "workspace" | "organization";

/** One owner row that does not count (`owners[]` of GET /api/admin/ownership/orphans): why the
 *  object is orphaned. `organization_member` is sent for workspaces only. */
export type UnusableOwner = { user_id: string; email: string; status: string; deleted: boolean; organization_member?: boolean };
/** Every current member, with the reasons it could not be named (`blockers`), so the picker can
 *  say who cannot be chosen rather than hide them. */
export type Candidate = {
  user_id: string; email: string; display_name: string | null; role: string; status: string;
  second_factor: boolean; organization_member: boolean; platform_admin: boolean; eligible: boolean; blockers: string[];
};
type WorkspaceOrphan = {
  id: string; name: string; kind: string; status: string; organization_id: string | null; created_at: number;
  members: number; projects: number; owners: UnusableOwner[]; candidates: Candidate[]; candidates_total: number;
};
type OrganizationOrphan = {
  id: string; name: string; status: string; created_at: number; recover_until: number | null;
  members: number; workspaces: number; owners: UnusableOwner[]; candidates: Candidate[]; candidates_total: number;
};
type Orphan = WorkspaceOrphan | OrganizationOrphan;
type Orphans = {
  as_of: number; limit: number;
  workspaces: { total: number; items: WorkspaceOrphan[] };
  organizations: { total: number; items: OrganizationOrphan[] };
};
/** POST …/recover-owner: the member that became owner. */
type Recovered = { ok: true; member: { user_id: string; email: string; role: "owner"; previous_role: string } };

const PATH = "/api/admin/ownership/orphans?limit=50";
/** The server's blocker codes, worded. */
const BLOCKER: Record<string, string> = {
  account_unavailable: "账户不可用", second_factor_required: "未启用第二因素",
  not_organization_member: "已不在组织", platform_admin: "持有平台权限",
};
const ORG_STATUS: Record<string, string> = { active: "正常", frozen: "已冻结", deleted: "已删除" };
const ORG_ROLE: Record<string, string> = { owner: "所有者", admin: "管理员", member: "成员" };

/** Why an owner row does not count, in the order the server decides it: the account first, then —
 *  for a workspace attached to an organization — the organization membership. Source text; null for
 *  a row that gives no reason (the server does not send such rows). */
export function ownerReason(owner: UnusableOwner): string | null {
  if (owner.deleted || owner.status === "deleted") return "账户已删除";
  if (owner.status === "suspended") return "账户已停用";
  if (owner.status === "pending_delete") return "账户待删除";
  if (owner.status === "pending") return "账户待激活";
  if (owner.organization_member === false) return "已不在组织";
  return null;
}
const roleText = (kind: OwnershipKind, role: string) => kind === "workspace" ? roleLabel(role) : tr(ORG_ROLE[role] || role);

/** One option's text: who, their current role, and whether they can be named (or why not). */
export function candidateText(kind: OwnershipKind, c: Candidate): string {
  const standing = c.eligible ? tr("已启用第二因素") : c.blockers.map((code) => tr(BLOCKER[code] || code)).join(" · ");
  return [c.display_name, c.email, roleText(kind, c.role), standing].filter(Boolean).join(" · ");
}

/** The successor picker inside the confirmation: every member is listed; only an eligible one can
 *  be chosen, the others say why they cannot. */
export function CandidatePicker({ kind, candidates, total, value, onChange }: {
  kind: OwnershipKind; candidates: Candidate[]; total?: number; value: string; onChange: (userId: string) => void;
}) {
  useI18n();
  const helpId = useId();
  return <>
    <label className="field">{tr("新拥有者")}
      <select className="input" value={value} aria-describedby={helpId} onChange={(e) => onChange(e.target.value)}>
        <option value="">{tr("请选择成员")}</option>
        {candidates.map((c) => <option key={c.user_id} value={c.user_id} disabled={!c.eligible}>{candidateText(kind, c)}</option>)}
      </select></label>
    <p id={helpId} className="small muted">{tr("只能选择账户正常、已启用第二因素且没有平台权限的现有成员；不符合条件的成员会标明原因。")}
      {total !== undefined && total > candidates.length && <> {tr("共 {total} 位成员，这里只列出前 {shown} 位（符合条件者优先）。", { total, shown: candidates.length })}</>}</p>
    {!candidates.length ? <p className="notice" role="note">{kind === "workspace" ? tr("这个工作空间没有可指定的成员。") : tr("这个组织没有可指定的成员。")}</p>
      : !candidates.some((c) => c.eligible) && <p className="notice" role="note">{tr("当前没有符合条件的成员：请先让其中一位启用第二因素，或恢复其账户。")}</p>}
  </>;
}

type Props = { kind: OwnershipKind; held: Held; onDenied: () => void; onChanged: () => void };

/** The panel for one kind, below that kind's own list. `onChanged` runs after a handover so the
 *  list above (whose rows show the object) re-reads. */
export function OwnershipRecovery({ kind, held, onDenied, onChanged }: Props) {
  // The list is read with both view permissions (every built-in role that holds one holds both).
  // A caller without them is not offered a request the server would refuse and record.
  if (!held.has("platform.workspaces.view") || !held.has("platform.orgs.view")) return null;
  return <OrphanPanel kind={kind} held={held} onDenied={onDenied} onChanged={onChanged} />;
}

function statusCell(kind: OwnershipKind, item: Orphan): ReactNode {
  if (kind === "workspace") return WORKSPACE_STATUS[item.status] || item.status;
  const org = item as OrganizationOrphan;
  return <>{tr(ORG_STATUS[org.status] || org.status)}
    {org.recover_until && <span className="cell-note">{tr("恢复期限：{time}", { time: fmtTime(org.recover_until) })}</span>}</>;
}

function OrphanPanel({ kind, held, onDenied, onChanged }: Props) {
  useI18n();
  const headingId = useId();
  const manage = permitted(held, kind === "workspace" ? "platform.workspaces.manage" : "platform.orgs.manage");
  const { data, err, loading, reload } = useAdminData<Orphans>(PATH, onDenied);
  const [action, setAction] = useState<Orphan | null>(null);
  const [chosen, setChosen] = useState("");
  // What the last handover did. The row that offered it is gone once the list re-reads, so focus
  // would drop to <body>: it goes to the outcome instead, which is then read out. The live region
  // is on the page from the first render; only its text changes (a region inserted together with
  // its text is not announced by many screen readers).
  const [outcome, setOutcome] = useState<{ text: string; n: number } | null>(null);
  const outcomeRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (outcome) outcomeRef.current?.focus(); }, [outcome?.n]);
  const page = data ? (kind === "workspace" ? data.workspaces : data.organizations) : null;
  const items: Orphan[] = page?.items ?? [];
  const columns = manage.disabled ? 4 : 5;
  const title = kind === "workspace" ? tr("没有有效拥有者的工作空间") : tr("没有有效拥有者的组织");
  const candidate = action?.candidates.find((c) => c.user_id === chosen) ?? null;
  const open = (item: Orphan) => { setChosen(""); setAction(item); };
  return <section className="card card-b col gap12" aria-labelledby={headingId}>
    <div className="row gap12 wrap"><h2 id={headingId} className="grow">{title}</h2>
      <button type="button" className="btn sm" disabled={loading} onClick={reload}>{tr("刷新")}</button></div>
    <p className="muted small">{kind === "workspace"
      ? tr("这些工作空间没有任何一位可用的拥有者：原拥有者的账户已停用、已删除，或已不在所属组织。")
      : tr("这些组织没有任何一位可用的拥有者：原拥有者的账户已停用或已删除。")}
      {" "}{tr("平台管理可以把其中一位现有成员指定为拥有者：不新增成员，不授予任何平台管理员内容访问；接任的成员必须已启用第二因素。")}</p>
    {manage.hint && <p className="notice" role="note">{tr("你的平台角色可以查看这份清单，但不能指定拥有者。")}</p>}
    <div role="status" aria-live="polite">
      {outcome && <div className="notice ok" ref={outcomeRef} tabIndex={-1}>{outcome.text}</div>}
    </div>
    {err && <div className="notice err" role="alert">{err} <button type="button" className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在读取拥有者情况…")}</p>}
    <div className="list-wrap admin-cards-wrap"><table className="gov admin-cards-table" aria-label={title} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("成员")}</th><th scope="col">{tr("原因")}</th>
        {!manage.disabled && <th scope="col" className="act">{tr("操作")}</th>}</tr></thead>
      <tbody>{!loading && !err && items.map((item) => <tr key={item.id}>
        <th scope="row">{item.name}<div className="who2 mono">{item.id}</div></th>
        <td data-label={tr("状态")}>{statusCell(kind, item)}</td>
        <td data-label={tr("成员")}>{item.members}<span className="cell-note">{"projects" in item
          ? tr("{count} 个项目", { count: item.projects }) : tr("{count} 个工作空间", { count: item.workspaces })}</span></td>
        <td data-label={tr("原因")}>{item.owners.length
          ? item.owners.map((o) => { const why = ownerReason(o); return <div key={o.user_id}>{o.email}<span className="cell-note">{why ? tr(why) : o.status}</span></div>; })
          : tr("没有拥有者成员")}</td>
        {!manage.disabled && <td className="act" data-label={tr("操作")}>
          <button type="button" className="btn sm" disabled={loading || !!err} onClick={() => open(item)}>{tr("指定拥有者")}</button></td>}
      </tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={columns}>
        {kind === "workspace" ? tr("当前没有缺少有效拥有者的工作空间。") : tr("当前没有缺少有效拥有者的组织。")}</td></tr>}</tbody>
    </table></div>
    {page && page.total > items.length && <p className="muted small">{tr("共 {total} 个，这里只显示前 {shown} 个。", { total: page.total, shown: items.length })}</p>}
    {data && <p className="tiny muted">{tr("读取于 {time}", { time: fmtTime(data.as_of) })}</p>}
    {action && <ActionDialog title={tr("指定拥有者")} confirmLabel={tr("指定拥有者")} danger reason="required" reauthScope="admin" ready={!!candidate?.eligible}
      impact={<>
        <p>{kind === "workspace" ? tr("工作空间「{name}」目前没有有效拥有者。", { name: action.name }) : tr("组织「{name}」目前没有有效拥有者。", { name: action.name })}</p>
        <p>{candidate
          ? tr("{email} 将成为拥有者（当前角色：{role}）；其他成员的角色和权限不变。", { email: candidate.email, role: roleText(kind, candidate.role) })
          : tr("请先选择一位现有成员；其他成员的角色和权限不变。")}</p>
        <p className="small muted">{tr("这不会给任何平台管理员带来成员身份或内容访问；理由会原样写入审计记录。")}</p>
      </>}
      run={(reason) => post<Recovered>(`/api/admin/${kind === "workspace" ? "workspaces" : "organizations"}/${encodeURIComponent(action.id)}/recover-owner`, { user_id: chosen, reason })}
      onClose={() => setAction(null)}
      onDone={(r) => {
        setAction(null); reload(); onChanged();
        setOutcome((o) => ({ text: tr("已指定 {email} 为拥有者。", { email: r.member.email }), n: (o?.n || 0) + 1 }));
      }}>
      <CandidatePicker kind={kind} candidates={action.candidates} total={action.candidates_total} value={chosen} onChange={setChosen} />
    </ActionDialog>}
  </section>;
}
