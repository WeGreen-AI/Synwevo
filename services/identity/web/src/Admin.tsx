import { ModelCostsTab } from "./governance/ModelCostsTab";
import { ModelProviderTab } from "./governance/ModelProviderTab";
import { ModelRiskTab } from "./governance/ModelRiskTab";
import { AgentSafetyTab } from "./governance/AgentSafetyTab";
import { ModelPricesTab } from "./governance/ModelPricesTab";
import { FinanceTab } from "./governance/FinanceTab";
import { PaymentsTab } from "./governance/PaymentsTab";
import { SettingsTab } from "./governance/SettingsTab";
import { AppRunsTab, FlowRunsTab } from "./governance/RunsTab";
import { SuperAdminOffersAdmin, SuperAdminReadiness } from "./governance/SuperAdminHandover";
import { MailTab, OrganizationsTab } from "./governance/GovernanceTabs";
import { IdentityProvidersTab } from "./governance/IdentityProvidersTab";
import { PlatformAccessDialog, PlatformAdminsTab, platformRoleLabel, platformRolesText, ROLE_ORDER, systemReasonText } from "./governance/PlatformAccess";
import { AUDIT_SCOPES, mayClaim, permitted, userOffers, type Held, type Tab } from "./governance/access";
import { useAdminData, useAdminPage } from "./governance/data";
import { t as tr, useI18n, PageState } from "@synwevo/ui";
import "./governance/messages";
/**
 * Platform administration.
 *
 * Governs accounts, workspaces and access. It shows counts, never contents — listing the names
 * of the historical projects here would hand every document title to whoever reaches this page,
 * which is the exact leak the unclaimed workspace exists to prevent.
 *
 * Every dangerous action states its object and its effect, requires a written reason, and asks
 * for scoped identity verification again. The server enforces all three; this screen is the explanation, not
 * the control.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { AccountStatusDialog } from "./governance/AccountStatusDialog";
import { ActionDialog } from "./governance/ActionDialog";
import { AdminLayout } from "./admin/AdminLayout";
import { RowMenu } from "./governance/RowMenu";
import { AccountDetailDialog } from "./governance/AccountDetail";
import { PageControls } from "./governance/ListControls";
import { useDebounced } from "./lib/hooks";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, roleLabel } from "./governance/labels";
import { ApiError, post, qs } from "@synwevo/auth-client";
import { useSession } from "@synwevo/auth-client";
import { fmtTime, useToast } from "@synwevo/ui";
import { releaseWorkspaceContext } from "./governance/workspaceContext";

type Overview = {
  users: number;
  suspended: number;
  workspaces: number;
  admins: number;
  unclaimed: Workspace | null;
  /** A20: deletion requests in their grace period, past due, or owing a withdrawal record. */
  account_deletions?: { pending: number; overdue: number; withdrawals_pending: number };
};
type Workspace = {
  id: string; name: string; kind: string; status: string;
  members: number; projects: number; apps: string[];
};
type User = {
  id: string; email: string; display_name: string | null; status: string;
  platform_role: string; platform_roles?: string[]; outranks_caller?: boolean; created_at: number; workspaces: number; sessions: number;
  /** set while status is pending_delete (A20) */
  deletion_due_at?: number | null;
};
/** GET /api/admin/workspaces/{id}/unclaim-preview: what undoing the assignment would change.
 *  Counts, never contents — the people are named because they are the ones who lose access. */
type UnclaimPlan = {
  workspace: { id: string; name: string; status: string };
  name_after: string; name_restored: boolean;
  members: { user_id: string; email: string | null; display_name: string | null; role: string }[];
  invitations: number; tokens: number; service_accounts: number; approvals: number;
  /** operator-issued dw_ keys the undo does not stop (the pending_claim rule) */
  operator_keys: number;
  /** queued or running generation jobs the runner cancels once nobody holds the data */
  jobs_stopping: number;
  projects: number; templates: number;
  assigned: { at: number; by_email: string | null; by_name: string | null; to_email: string | null } | null;
};
type UnclaimResult = { removed_members: { user_id: string; email: string | null }[] };
type AuditRow = {
  id: string; at: number; action: string; actor_kind: string; actor_email: string | null;
  object_kind: string | null; object_id: string | null; result: string;
  meta: Record<string, unknown>;
};

export default function Admin() {
  return <AdminLayout>{({ held, active }) => <AdminViews held={held} tab={active as Tab} />}</AdminLayout>;
}

function AdminViews({ held, tab }: { held: Held; tab: Tab }) {
  const { me, refresh } = useSession();
  const [denied, setDenied] = useState(false);
  const [access, setAccess] = useState<{ id: string; email: string } | null>(null);
  const [accessRevision, setAccessRevision] = useState(0);
  const onDenied = useCallback(() => setDenied(true), []);
  if (denied) return <PageState><div className="big">{tr("页面不存在")}</div><a href="/account">{tr("前往账户设置")}</a></PageState>;
  return <>
    <SuperAdminReadiness onInspect={setAccess} onChanged={() => setAccessRevision(value => value + 1)} />
    {tab === "overview" && <OverviewTab onDenied={onDenied} held={held} />}
    {tab === "users" && <UsersTab revision={accessRevision} onDenied={onDenied} onAccess={setAccess} held={held} />}
    {tab === "admins" && <><PlatformAdminsTab key={accessRevision} onDenied={onDenied} onOpen={setAccess} />
      {held.has("platform.roles.assign_super") && <SuperAdminOffersAdmin key={`offers-${accessRevision}`} />}</> }
    {tab === "workspaces" && <WorkspacesTab onDenied={onDenied} held={held} />}
    {tab === "organizations" && <OrganizationsTab onDenied={onDenied} held={held} />}
    {tab === "model-prices" && <ModelPricesTab onDenied={onDenied} held={held} />}
    {tab === "model-costs" && <ModelCostsTab onDenied={onDenied} held={held} />}
    {tab === "model-risk" && <ModelRiskTab onDenied={onDenied} held={held} />}
    {tab === "model-provider" && <ModelProviderTab onDenied={onDenied}
      canRead={!permitted(held, "platform.config.view").disabled}
      canManage={!permitted(held, "platform.config.manage").disabled} />}
    {tab === "finance" && <FinanceTab onDenied={onDenied} canManage={!permitted(held, "platform.billing.manage").disabled} />}
    {tab === "payments" && <PaymentsTab onDenied={onDenied} canManage={!permitted(held, "platform.billing.manage").disabled} />}
    {tab === "agent-safety" && <AgentSafetyTab onDenied={onDenied} held={held} />}
    {tab === "registration" && <SettingsTab onDenied={onDenied} held={held} />}
    {tab === "identity" && <IdentityProvidersTab onDenied={onDenied} />}
    {tab === "mail" && <MailTab onDenied={onDenied} held={held} />}
    {tab === "app-runs" && <AppRunsTab onDenied={onDenied} />}
    {tab === "flow-runs" && <FlowRunsTab onDenied={onDenied} />}
    {tab === "audit" && <AuditTab onDenied={onDenied} />}
    {access && <PlatformAccessDialog user={access} onClose={() => setAccess(null)} onChanged={() => {
      setAccessRevision((n) => n + 1);
      // one's own roles changed: the tabs and links follow the server's new answer
      if (access.id === me?.id) void refresh();
    }} />}
  </>;
}

function OverviewTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const { data, err, loading, reload } = useAdminData<Overview>("/api/admin/overview", onDenied);
  // What the last assignment or undo did. Kept here, above the cards, because the card that
  // performed it is replaced by the other one when the overview reloads.
  const [outcome, setOutcome] = useState<{ text: string; n: number } | null>(null);
  const outcomeRef = useRef<HTMLDivElement>(null);
  const changed = (message: string) => { setOutcome((o) => ({ text: message, n: (o?.n || 0) + 1 })); reload(); };
  // The button that opened the confirmation is gone once the other card replaces its own, so
  // focus would drop to <body>: it goes to the outcome instead, which is then read out.
  useEffect(() => { if (outcome) outcomeRef.current?.focus(); }, [outcome?.n]);
  // The live region is on the page from the first render and stays through the reload (a region
  // inserted together with its text is not announced by many screen readers); only its text changes.
  const live = <div role="status" aria-live="polite">
    {outcome && <div className="notice ok" ref={outcomeRef} tabIndex={-1}>{tr(outcome.text)}</div>}
  </div>;
  if (loading) return <>{live}<div className="empty">{tr("加载中…")}</div></>;
  if (err) return <>{live}<div className="notice err" role="alert">{err} <button className="btn" onClick={reload}>{tr("重试")}</button></div></>;
  if (!data) return live;
  const legacy = data.unclaimed;

  return (
    <>
      {live}
      <section className="card card-b col gap12">
        <h2>{tr("平台概览")}</h2>
        <div className="row gap12 wrap">
          <Stat label={tr("账户")} value={data.users} />
          <Stat label={tr("其中已停用")} value={data.suspended} />
          <Stat label={tr("工作空间")} value={data.workspaces} />
          <Stat label={tr("平台管理员")} value={data.admins} />
        </div>
      </section>
      <AccountDeletionsCard counts={data.account_deletions} held={held} onDone={changed} />

      {legacy?.status === "pending_claim" && (
        <section className="card card-b col gap12" aria-labelledby="legacy-unclaimed">
          <h2 id="legacy-unclaimed">{tr("待认领的历史数据")}</h2>
          <p className="muted">{tr("账户体系建立之前的数据，属于一个没有成员的工作空间，目前没有人能读取（包括平台管理员）。指派给一个已有账户后才能打开。")}</p>
          {/* the claim and its account search need permissions only a super administrator holds */}
          {mayClaim(held) ? <ClaimCard ws={legacy} onClaimed={changed} />
            : <p>{tr("{count} 个项目。只有最高管理员可以把它指派给一个没有平台权限的账户。", {count: legacy.projects})}</p>}
        </section>
      )}

      {(legacy?.status === "active" || legacy?.status === "frozen") && (
        <section className="card card-b col gap12" aria-labelledby="legacy-assigned">
          <h2 id="legacy-assigned">{tr("历史数据已指派")}</h2>
          <p className="muted">{tr("账户体系建立之前的数据已经指派给下列账户。如果指派错了，可以撤销：数据回到任何账户都无法读取的待认领状态，不删除任何内容，之后可以重新指派。")}</p>
          {/* Frozen is still assigned, and is undone as it is: unfreezing first would hand the data
              back to the wrong account until the undo ran. */}
          {legacy.status === "frozen" && <div className="notice">{tr("这个空间目前被冻结。撤销指派会同时结束冻结：数据直接回到待认领状态，中间不会重新开放给这些账户。")}</div>}
          {/* the undo and its preview (which names the members) are the claim's own permission */}
          {held.has("platform.workspaces.claim") ? <AssignedCard ws={legacy} onDenied={onDenied} onRefresh={reload} onUndone={changed} />
            : <p>{tr("只有最高管理员可以查看指派详情或撤销指派。")}</p>}
        </section>
      )}
    </>
  );
}

/** A20: the deletion requests the platform is holding, and the due sweep an administrator may run
 *  now. Counts only; the people are reachable from the account list (status 待删除). */
function AccountDeletionsCard({ counts, held, onDone }: { counts?: Overview["account_deletions"]; held: Held; onDone: (message: string) => void }) {
  const [open, setOpen] = useState(false);
  if (!counts) return null;
  const stuck = counts.overdue > 0 || counts.withdrawals_pending > 0;
  const manage = permitted(held, "platform.users.manage");
  return <section className="card card-b col gap12" aria-labelledby="account-deletions">
    <h2 id="account-deletions">{tr("注销申请")}</h2>
    <div className="row gap12 wrap">
      <Stat label={tr("待删除账户")} value={counts.pending} />
      <Stat label={tr("已到期未处理")} value={counts.overdue} />
      <Stat label={tr("待写安全记录")} value={counts.withdrawals_pending} />
    </div>
    <p className="muted small">{tr("本人申请注销后账户立即停用，7 天宽限期内可用邮件里的链接取消；到期后由每小时一次的后台任务终结并写入安全记录。已到期未处理或待写安全记录不为零时，可以立即运行一次到期处理；它不会提前任何申请的到期时间。")}</p>
    {stuck && (manage.disabled ? <p className="muted small">{tr(manage.hint!)}</p>
      : <div><button type="button" className="btn" onClick={() => setOpen(true)}>{tr("立即处理到期删除")}</button></div>)}
    {open && <ActionDialog title={tr("立即处理到期删除")} confirmLabel={tr("立即处理到期删除")} reason="required" danger
      impact={tr("已过宽限期的 {count} 个注销申请将被终结：账户变为已删除，登录方式与会话全部移除，组织贡献保留；待写的安全记录会补写。宽限期内的申请不受影响。", { count: counts.overdue })}
      run={(reason) => post<{ finalized: number }>("/api/admin/account-deletions/finalize", { reason })}
      onClose={() => setOpen(false)} onDone={(r) => { setOpen(false); onDone(tr("已终结 {count} 个到期的注销申请。", { count: r.finalized })); }} />}
  </section>;
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="card" style={{ minWidth: 120, padding: "12px 16px" }}>
      <div className="muted sm">{label}</div>
      <div style={{ fontSize: 24, fontWeight: 650 }}>{value}</div>
    </div>
  );
}

function ClaimCard({ ws, onClaimed }: { ws: Workspace; onClaimed: (message: string) => void }) {
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<User | null>(null);
  const [confirming, setConfirming] = useState(false);
  const query = useDebounced(q.trim());
  // Only accounts without any platform grant: the server refuses the others (target_is_platform_admin).
  const users = useAdminPage<User>(`/api/admin/users${qs({ q: query, status: "active", role: "none" })}`);
  const searching = query !== q.trim();
  return <div className="col gap12">
    <p>{tr("{projects}，{members}。请选择接收这个工作空间的已有账户。", { projects: tr("{count} 个项目", { count: ws.projects }), members: tr("{count} 位成员", { count: ws.members }) })}</p>
    <p className="small muted">{tr("只列出没有平台权限的账户：平台管理与业务数据须由不同账户持有，也不能指派给你自己。")}</p>
    <label className="field">{tr("搜索接收账户")}<input className="input" type="search" maxLength={100} value={q}
      onChange={(e) => { setQ(e.target.value); setChosen(null); }} /></label>
    {(users.loading || searching) && <p role="status">{tr("正在查找账户…")}</p>}
    {!!users.err && <div className="notice err" role="alert">{users.err} <button className="btn sm" onClick={users.reload}>{tr("重试")}</button></div>}
    <div className="picker" role="group" aria-label={tr("接收账户")}>
      {!users.loading && !searching && !users.err && users.data?.items.map((u) => <label key={u.id}>
        <input type="radio" name="claim-account" checked={chosen?.id === u.id} onChange={() => setChosen(u)} />
        <span>{u.display_name || u.email}<span className="dim"> {u.email}</span></span></label>)}
    </div>
    {!users.loading && !searching && !users.err && !users.data?.items.length && <p role="status">{tr("没有可选的正常账户。")}</p>}
    <PageControls offset={users.offset} total={users.data?.total || 0} busy={users.loading || searching || !!users.err}
      onPage={(offset) => { setChosen(null); users.setOffset(offset); }} />
    <div><button className="btn primary" disabled={!chosen || !users.data?.items.some((u) => u.id === chosen.id) || users.loading || searching || !!users.err} onClick={() => setConfirming(true)}>{tr("指派历史数据")}</button></div>
    {confirming && chosen && <ActionDialog title={tr("指派历史数据")} confirmLabel={tr("指派历史数据")} reason="required" danger
      impact={<>{tr("将把「{name}」及其中 {projects}的访问权交给{email}，角色为所有者。不会授予平台管理权限。", { name: ws.name, projects: tr("{count} 个项目", { count: ws.projects }), email: chosen.email })}</>}
      run={(reason) => post<{ projects: number }>(`/api/admin/workspaces/${encodeURIComponent(ws.id)}/claim`, { user_id: chosen.id, reason })}
      onClose={() => setConfirming(false)} onDone={(r) => { setConfirming(false); onClaimed(tr("已把包含 {projects}的工作空间指派给 {email}。", { projects: tr("{count} 个项目", { count: r.projects }), email: chosen.email })); }} />}
  </div>;
}

function memberLabel(m: UnclaimPlan["members"][number]) {
  return m.display_name || m.email || m.user_id;
}

/** The assigned historical data, and the way back: who has it, since when and from whom, and an
 *  undo whose confirmation names every person who loses access (the server refuses if that list
 *  changed after it was shown — `expected_members`). */
function AssignedCard({ ws, onDenied, onRefresh, onUndone }: {
  ws: Workspace; onDenied: () => void; onRefresh: () => void; onUndone: (message: string) => void;
}) {
  const path = `/api/admin/workspaces/${encodeURIComponent(ws.id)}/unclaim-preview`;
  const { data: plan, err, loading, reload } = useAdminData<UnclaimPlan>(path, onDenied);
  const [confirming, setConfirming] = useState(false);
  // A re-read keeps the plan on screen (and the confirmation open) until the new one arrives.
  if (loading && !plan) return <p role="status">{tr("正在读取指派情况…")}</p>;
  if (err || !plan) return <div className="notice err" role="alert">{err} <button className="btn sm" onClick={onRefresh}>{tr("刷新概览")}</button></div>;
  const owners = plan.members.filter((m) => m.role === "owner");
  return <div className="col gap12">
    <dl className="facts">
      <dt>{tr("名称")}</dt><dd>{plan.workspace.name}</dd>
      <dt>{tr("所有者账户")}</dt>
      <dd>{owners.length ? owners.map((m) => <div key={m.user_id}>{m.email || memberLabel(m)}</div>) : tr("没有所有者")}</dd>
      <dt>{tr("成员人数")}</dt><dd>{tr("共 {count} 位", { count: plan.members.length })}</dd>
      <dt>{tr("指派时间")}</dt><dd>{plan.assigned ? fmtTime(plan.assigned.at) : tr("没有找到指派记录")}</dd>
      <dt>{tr("指派人")}</dt><dd>{plan.assigned?.by_email || "—"}</dd>
      <dt>{tr("项目")}</dt><dd>{tr("{count} 个项目", { count: plan.projects })}</dd>
    </dl>
    <div><button className="btn danger" onClick={() => setConfirming(true)}>{tr("撤销指派")}</button></div>
    {/* The members changed after the plan was read: the server refuses (`expected_members`), and
        the list in the open confirmation is re-read in place, so the next confirm is of what is
        there now. Closing re-reads it too — any refusal may have left it stale. */}
    {confirming && <ActionDialog title={tr("撤销指派")} confirmLabel={tr("撤销指派")} reason="required" danger
      reasonPlaceholder={tr("例如：指派给了错误的账户")}
      impact={<UnclaimImpact plan={plan} />}
      run={async (reason) => {
        try {
          return await post<UnclaimResult>(`/api/admin/workspaces/${encodeURIComponent(ws.id)}/unclaim`,
            { reason, expected_members: plan.members.map((m) => m.user_id) });
        } catch (e) {
          if (e instanceof ApiError && e.code === "assignment_changed") reload();
          throw e;
        }
      }}
      onClose={() => { setConfirming(false); reload(); }}
      onDone={(r) => {
        setConfirming(false);
        releaseWorkspaceContext(ws.id);
        onUndone(tr("已撤销指派：{count} 位成员失去访问，数据全部保留。", { count: r.removed_members.length }));
      }} />}
  </div>;
}

function UnclaimImpact({ plan }: { plan: UnclaimPlan }) {
  return <>
    <p>{tr("「{name}」将回到待认领状态。", { name: plan.workspace.name })}</p>
    {plan.workspace.status === "frozen" && <p>{tr("它目前被冻结；冻结随撤销一并结束，不需要先解冻。")}</p>}
    {plan.members.length ? <>
      <p>{tr("以下 {count} 位成员将失去访问：", { count: plan.members.length })}</p>
      <ul className="names" tabIndex={0} aria-label={tr("将失去访问的成员")}>
        {plan.members.map((m) => <li key={m.user_id}><b>{memberLabel(m)}</b>{m.display_name && m.email && <span className="muted"> {m.email}</span>} · {roleLabel(m.role)}</li>)}
      </ul>
    </> : <p>{tr("这个空间目前没有成员。")}</p>}
    <ul>
      <li>{tr("{count} 个尚未接受的邀请将被撤回。", { count: plan.invitations })}</li>
      <li>{tr("{count} 个仍可使用的应用登录凭据将被撤销。", { count: plan.tokens })}</li>
      {(plan.service_accounts > 0 || plan.approvals > 0) &&
        <li>{tr("这个空间的服务账户（{p0} 个）和经批准的 API 凭据（{p1} 项）将停止工作，但不会被删除。", { p0: plan.service_accounts, p1: plan.approvals })}
          {" "}{tr("再次指派之后：经批准的凭据只有在数据回到当初批准它们的账户时才重新生效；运维直接登记的服务账户随空间一起恢复。")}</li>}
      {plan.jobs_stopping > 0 &&
        <li>{tr("{count} 个排队或运行中的生成任务会停止：剩余样本被取消，已生成的结果保留；再次指派之后需要重新运行。", { count: plan.jobs_stopping })}</li>}
      <li>{plan.name_restored ? tr("名称改回「{name}」。", { name: plan.name_after }) : tr("名称「{name}」保持不变。", { name: plan.name_after })}</li>
    </ul>
    <p><b>{tr("不会删除任何数据：{projects}和 {templates}原样保留。", { projects: tr("{count} 个项目", { count: plan.projects }), templates: tr("{count} 个模板", { count: plan.templates }) })}</b></p>
    <p>{tr("在再次指派之前，任何账户都不能打开它，包括平台管理员。")}</p>
    {/* The pending_claim rule for operator-issued dw_ keys (MachineAuth._legacy_key): not stopped
        by the undo, as they were not before the claim. Said here rather than discovered later. */}
    {plan.operator_keys > 0 &&
      <p>{tr("例外：{count} 个由运维签发的 dw_ 访问密钥不受撤销影响，和指派之前一样可以读写这些数据，直到运维撤销它们。", { count: plan.operator_keys })}</p>}
  </>;
}

function UsersTab({ onDenied, onAccess, revision, held }: { onDenied: () => void; onAccess: (user: User) => void; revision: number; held: Held }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [status, setStatus] = useState("");
  const [filterRole, setFilterRole] = useState("");
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<User>(`/api/admin/users${qs({ q: query, status, role: filterRole })}`, onDenied);
  const { me, refresh } = useSession();
  const toast = useToast();
  const [resultNotice, setResultNotice] = useState("");
  const [action, setAction] = useState<{ user: User; kind: "status" | "revoke" | "recovery" | "cancellation" } | null>(null);
  // Read-only: the account's facts (A13). Every change stays in the items below, with its own reason.
  const [detail, setDetail] = useState<User | null>(null);
  // A grant or revocation in the platform-access dialog changes this list's platform column.
  useEffect(() => { if (revision) reload(); }, [revision]); // eslint-disable-line react-hooks/exhaustive-deps
  const items = loading || err ? [] : data?.items || [];
  const u = action?.user;
  const title = action?.kind === "cancellation" ? tr("重发取消注销链接") : action?.kind === "recovery" ? tr("发送找回邮件") : action?.kind === "revoke" ? tr("撤销全部会话") : u?.status !== "suspended" ? tr("停用账户") : tr("恢复账户");
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("账户")}</h2>
      <input type="search" className="input" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder={tr("搜索邮箱或姓名")} aria-label={tr("搜索账户")} />
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新账户")}</button></div>
    <div className="row gap12 wrap">
      <label className="field">{tr("账户状态")}<select className="input" aria-label={tr("筛选账户状态")} value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">{tr("全部状态")}</option>{["active", "suspended", "pending", "pending_delete"].map((s) => <option key={s} value={s}>{ACCOUNT_STATUS[s] || s}</option>)}
      </select></label>
      <label className="field">{tr("平台权限")}<select className="input" aria-label={tr("筛选平台角色")} value={filterRole} onChange={(e) => setFilterRole(e.target.value)}>
        <option value="">{tr("全部角色")}</option><option value="none">{tr("无平台权限")}</option><option value="any">{tr("任一平台角色")}</option>
        {ROLE_ORDER.map((r) => <option key={r} value={r}>{platformRoleLabel(r)}</option>)}
      </select></label>
    </div>
    {resultNotice && <p className="notice" role="status">{resultNotice}</p>}
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载账户…")}</p>}
    <div className="list-wrap admin-users-wrap"><table className="gov admin-users-table" aria-label={tr("平台账户")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("账户")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("平台权限")}</th><th scope="col">{tr("工作空间")}</th><th scope="col">{tr("在线会话")}</th><th scope="col" className="act">{tr("操作")}</th></tr></thead>
      <tbody>{items.map((user) => { const o = userOffers(held, user, me?.id); const hint = (h?: string) => h && tr(h);
        return <tr key={user.id}>
        <th scope="row">{user.display_name || "—"}<div className="who2">{user.email}</div></th>
        <td data-label={tr("状态")}>{ACCOUNT_STATUS[user.status] || user.status}{user.status === "pending_delete" && user.deletion_due_at ? <div className="who2">{tr("到期：{time}", { time: fmtTime(user.deletion_due_at) })}</div> : null}</td><td data-label={tr("平台权限")}>{platformRolesText(user.platform_roles)}</td><td data-label={tr("工作空间")}>{user.workspaces}</td><td data-label={tr("在线会话")}>{user.sessions}</td>
        <td className="act" data-label={tr("操作")}><RowMenu label={tr("{p0} 的账户操作", {p0: user.email})} items={[
          { label: tr("查看详情"), disabled: loading || !!err, onSelect: () => setDetail(user) },
          { label: user.status !== "suspended" ? tr("停用账户") : tr("恢复账户"), danger: user.status !== "suspended",
            disabled: loading || !!err || o.status.disabled, hint: hint(o.status.hint), onSelect: () => setAction({ user, kind: "status" }) },
          { label: tr("发送找回邮件"), disabled: loading || !!err || o.recovery.disabled, hint: hint(o.recovery.hint), onSelect: () => setAction({ user, kind: "recovery" }) },
          ...(user.status === "pending_delete" ? [{ label: tr("重发取消注销链接"), disabled: loading || !!err || o.cancellationMail.disabled, hint: hint(o.cancellationMail.hint), onSelect: () => setAction({ user, kind: "cancellation" }) }] : []),
          { label: tr("撤销全部会话"), danger: true, disabled: loading || !!err || o.revoke.disabled, hint: hint(o.revoke.hint), onSelect: () => setAction({ user, kind: "revoke" }) },
          { label: tr("平台权限"), disabled: loading || !!err || o.access.disabled, hint: hint(o.access.hint), onSelect: () => onAccess(user) },
        ]} /></td></tr>; })}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的账户。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
    {detail && <AccountDetailDialog user={detail} held={held} onClose={() => setDetail(null)} onChanged={reload} />}
    {action?.kind === "status" && u && <AccountStatusDialog user={u} onClose={() => setAction(null)} onDone={(out) => {
      setAction(null); reload(); setResultNotice(tr("操作 {id} 已完成，账户状态为「{status}」。", {
        id: out.operation_id, status: ACCOUNT_STATUS[out.status] || out.status,
      }));
    }} />}
    {action && action.kind !== "status" && u && <ActionDialog title={title} confirmLabel={title} reason="required" danger
      impact={<><b>{u.email}</b>：{action.kind === "cancellation" ? tr("新的取消注销链接将发送到该账户当前邮箱，到期时间不变；之前的链接失效。管理员不会看到链接，也不能代为取消注销。") : action.kind === "recovery" ? tr("找回链接将发送到该账户当前邮箱。旧找回链接失效；管理员不会看到链接或设置新口令。") : action.kind === "revoke" ? tr("全部设备将退出，已签发的应用登录凭据会失效。")
          : u.status === "active" ? tr("账户将被停用，全部设备退出，应用访问立即停止。") : tr("账户将恢复正常；已经结束的登录不会恢复。")}
        {u.id === me?.id && <p>{tr("这是你自己的账户，完成后可能需要重新登录或离开平台管理页。")}</p>}</>}
      run={(reason) => post<{ sessions_revoked?: number }>(`/api/admin/users/${encodeURIComponent(u.id)}/${action.kind === "cancellation" ? "deletion/cancellation-mail" : action.kind === "recovery" ? "recovery" : action.kind === "revoke" ? "revoke-sessions" : "status"}`,
        { reason })}
      onClose={() => setAction(null)} onDone={(r) => {
        setAction(null); reload(); toast(action.kind === "cancellation" ? tr("取消注销链接已排队，请在邮件投递中查看状态。") : action.kind === "recovery" ? tr("找回邮件已排队，请在邮件投递中查看状态。") : tr("{action}完成，撤销 {count} 个会话。", { action: title, count: r.sessions_revoked || 0 }), "ok");
        if (u.id === me?.id) void refresh();
      }} />}
  </section>;
}

function WorkspacesTab({ onDenied, held }: { onDenied: () => void; held: Held }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<Workspace>(`/api/admin/workspaces${qs({ q: query })}`, onDenied);
  const [action, setAction] = useState<Workspace | null>(null);
  const [notice, setNotice] = useState("");
  const { me } = useSession();
  const manage = permitted(held, "platform.workspaces.manage");
  const items = loading || err ? [] : data?.items || [];
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("工作空间")}</h2><input className="input" type="search" aria-label={tr("搜索工作空间")} value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新工作空间")}</button></div>
    <p className="muted small">{tr("只显示数量，不显示内容；平台管理权限不包含读取文档或管理工作空间成员。")}</p>
    {manage.hint && <p className="notice" role="note">{tr("你的平台角色可以查看工作空间，但不能冻结或解冻。")}</p>}
    {notice && <div className="notice" role="status">{tr(notice)}</div>}
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载工作空间…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("平台工作空间")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("成员")}</th><th scope="col">{tr("项目")}</th><th scope="col" className="act">{tr("操作")}</th></tr></thead>
      <tbody>{items.map((w) => <tr key={w.id}>
        <th scope="row">{w.name}</th><td>{WORKSPACE_KIND[w.kind] || w.kind}</td><td>{WORKSPACE_STATUS[w.status] || w.status}</td><td>{w.members}</td><td>{w.projects}</td>
        <td className="act"><div className="row gap4 wrap">{(w.status === "active" || w.status === "frozen") && <button className="btn sm" disabled={loading || !!err || manage.disabled} onClick={() => setAction(w)}>{w.status === "active" ? tr("冻结工作空间") : tr("解冻工作空间")}</button>}
          {me?.workspaces.some((m) => m.id === w.id && (m.role === "owner" || m.role === "admin")) && <a className="btn sm" href={`/account/workspaces/${encodeURIComponent(w.id)}/members`}>{tr("成员与邀请")}</a>}</div></td>
      </tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的工作空间。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
    {action && <ActionDialog title={action.status === "active" ? tr("冻结工作空间") : tr("解冻工作空间")} confirmLabel={action.status === "active" ? tr("冻结工作空间") : tr("解冻工作空间")} danger reason="required"
      impact={<>「{action.name}」：{action.status === "active" ? tr("成员将立即失去应用访问，当前会话将移出这个工作空间。成员关系和内容保留。") : tr("工作空间将恢复可用，成员仍按原有资格访问。此前结束的登录不会恢复。")}</>}
      run={(reason) => post<{ restore_note?: string; status: string }>(`/api/admin/workspaces/${encodeURIComponent(action.id)}/${action.status === "active" ? "freeze" : "unfreeze"}`, { reason })}
      onClose={() => setAction(null)} onDone={(r) => { setAction(null); reload(); setNotice(r.status === "frozen" ? tr("工作空间已冻结。") : tr("工作空间已解冻。若之后恢复更早的备份，冻结可能重新生效，需要再次解冻。")); }} />}
  </section>;
}

function AuditTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [action, setAction] = useState("");
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<AuditRow>(`/api/admin/audit${qs({ q: query, action })}`, onDenied);
  const items = loading || err ? [] : data?.items || [];
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("审计记录")}</h2><input className="input" type="search" maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} placeholder={tr("操作者邮箱或对象")} aria-label={tr("搜索审计记录")} />
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新审计")}</button></div>
    <label className="field">{tr("操作范围")}<select className="input" aria-label={tr("筛选审计操作")} value={action} onChange={(e) => setAction(e.target.value)}>
      {AUDIT_SCOPES.map(([prefix, label]) => <option key={prefix} value={prefix}>{tr(label)}</option>)}
    </select></label>
    <p className="muted small">{tr("翻页时保留本次查询的范围；刷新后包含新记录。")}</p>
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载审计记录…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("审计记录")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("时间")}</th><th scope="col" className="act">{tr("操作")}</th><th scope="col">{tr("操作者")}</th><th scope="col">{tr("对象")}</th><th scope="col">{tr("结果")}</th><th scope="col">{tr("理由")}</th></tr></thead>
      <tbody>{items.map((a) => <tr key={a.id}>
        <td>{fmtTime(a.at)}</td><td>{a.action}</td><td>{a.actor_email || "—"}</td>
        <td>{a.object_id || "—"}</td><td>{a.result === "ok" ? tr("成功") : a.result === "denied" ? tr("被拒绝") : a.result}</td>
        <td>{a.actor_kind === "system" ? systemReasonText(String(a.meta.reason || "")) : String(a.meta.reason || "")}</td>
      </tr>)}{!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的记录。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
  </section>;
}
