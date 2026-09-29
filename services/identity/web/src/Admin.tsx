import { RegistrationTab, MailTab, OrganizationsTab } from "./governance/GovernanceTabs";
import { IdentityProvidersTab } from "./governance/IdentityProvidersTab";
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
 * for the password again. The server enforces all three; this screen is the explanation, not
 * the control.
 */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { ActionDialog } from "./governance/ActionDialog";
import { Topbar } from "./governance/Topbar";
import { RowMenu } from "./governance/RowMenu";
import { PageControls } from "./governance/ListControls";
import { useDebounced } from "./lib/hooks";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, roleLabel } from "./governance/labels";
import { ApiError, post, qs } from "@synwevo/auth-client";
import { useSession } from "@synwevo/auth-client";
import { fmtTime, useToast } from "@synwevo/ui";
import AdminStepUp from "./components/AdminStepUp";

type Overview = {
  users: number;
  suspended: number;
  workspaces: number;
  admins: number;
  unclaimed: Workspace | null;
};
type Workspace = {
  id: string; name: string; kind: string; status: string;
  members: number; projects: number; apps: string[];
};
type User = {
  id: string; email: string; display_name: string | null; status: string;
  platform_role: string; created_at: number; workspaces: number; sessions: number;
};
/** GET /api/admin/workspaces/{id}/unclaim-preview: what undoing the assignment would change.
 *  Counts, never contents — the people are named because they are the ones who lose access. */
type UnclaimPlan = {
  workspace: { id: string; name: string; status: string };
  name_after: string; name_restored: boolean;
  members: { user_id: string; email: string | null; display_name: string | null; role: string }[];
  invitations: number; tokens: number; service_accounts: number; approvals: number;
  projects: number; templates: number;
  assigned: { at: number; by_email: string | null; by_name: string | null; to_email: string | null } | null;
};
type UnclaimResult = { removed_members: { user_id: string; email: string | null }[] };
type AuditRow = {
  id: string; at: number; action: string; actor_email: string | null;
  object_kind: string | null; object_id: string | null; result: string;
  meta: Record<string, unknown>;
};

type Tab = "overview" | "users" | "workspaces" | "organizations" | "registration" | "identity" | "mail" | "audit";

export default function Admin() {
  useI18n();
  const { me } = useSession();
  const [params, setParams] = useSearchParams();
  const tab = (["overview", "users", "workspaces", "organizations", "registration", "identity", "mail", "audit"].includes(params.get("tab") || "") ? params.get("tab") : "overview") as Tab;
  const setTab = (tab: Tab) => setParams({ tab });
  const [denied, setDenied] = useState(false);

  const onDenied = useCallback(() => setDenied(true), []);
  useEffect(() => { document.title = tr("平台管理 · Synwevo Account"); }, [tr("平台管理 · Synwevo Account")]);

  // The server answers 404 for a non-administrator, deliberately — an ordinary account learns
  // nothing about what exists here. The page mirrors that rather than saying "forbidden".
  if (denied || (me && me.platform_role !== "admin")) {
    return (
      <PageState>
        <div className="big">{tr("页面不存在")}</div>
        <a href="/account">{tr("前往账户设置")}</a>
      </PageState>
    );
  }

  return (
    <div className="app">
      <Topbar title={tr("平台管理")}>

        <span className="muted sm desktop-only">{me?.email}</span>
      </Topbar>

      <main style={{ overflow: "auto", flex: 1 }}>
        <div className="acct-page">
        {me?.admin_locked && <AdminStepUp compact />}
        {!me?.admin_locked && <nav className="acct-tabs row gap4 wrap" aria-label={tr("管理后台")}>
          {(["overview", "users", "workspaces", "organizations", "registration", "identity", "mail", "audit"] as Tab[]).map((t) => (
            <button key={t} className={"btn ghost sm" + (tab === t ? " on" : "")}
                    aria-current={tab === t ? "page" : undefined}
                    onClick={() => setTab(t)}>
              {{ overview: tr("概览"), users: tr("账户"), workspaces: tr("工作空间"), organizations: tr("组织"), registration: tr("注册策略"), identity: tr("登录方式"), mail: tr("邮件投递"), audit: tr("审计") }[t]}
            </button>
          ))}
        </nav>}
          {!me?.admin_locked && tab === "overview" && <OverviewTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "users" && <UsersTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "workspaces" && <WorkspacesTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "organizations" && <OrganizationsTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "registration" && <RegistrationTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "identity" && <IdentityProvidersTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "mail" && <MailTab onDenied={onDenied} />}
          {!me?.admin_locked && tab === "audit" && <AuditTab onDenied={onDenied} />}
        </div>
      </main>
    </div>
  );
}

function OverviewTab({ onDenied }: { onDenied: () => void }) {
  const { data, err, loading, reload } = useAdminData<Overview>("/api/admin/overview", onDenied);
  // What the last assignment or undo did. Kept here, above the cards, because the card that
  // performed it is replaced by the other one when the overview reloads.
  const [outcome, setOutcome] = useState<string | null>(null);
  const changed = (message: string) => { setOutcome(message); reload(); };
  if (loading) return <div className="empty">{tr("加载中…")}</div>;
  if (err) return <div className="notice err" role="alert">{err} <button className="btn" onClick={reload}>{tr("重试")}</button></div>;
  if (!data) return null;
  const legacy = data.unclaimed;

  return (
    <>
      <section className="card card-b col gap12">
        <h2>{tr("平台概览")}</h2>
        <div className="row gap12 wrap">
          <Stat label={tr("账户")} value={data.users} />
          <Stat label={tr("其中已停用")} value={data.suspended} />
          <Stat label={tr("工作空间")} value={data.workspaces} />
          <Stat label={tr("平台管理员")} value={data.admins} />
        </div>
      </section>

      {outcome && <div className="notice ok" role="status">{tr(outcome)}</div>}

      {legacy?.status === "pending_claim" && (
        <section className="card card-b col gap12" aria-labelledby="legacy-unclaimed">
          <h2 id="legacy-unclaimed">{tr("待认领的历史数据")}</h2>
          <p className="muted">{tr("账户体系建立之前的数据，属于一个没有成员的工作空间，目前没有人能读取（包括平台管理员）。指派给一个已有账户后才能打开。")}</p>
          <ClaimCard ws={legacy} onClaimed={changed} />
        </section>
      )}

      {(legacy?.status === "active" || legacy?.status === "frozen") && (
        <section className="card card-b col gap12" aria-labelledby="legacy-assigned">
          <h2 id="legacy-assigned">{tr("历史数据已指派")}</h2>
          <p className="muted">{tr("账户体系建立之前的数据已经指派给下列账户。如果指派错了，可以撤销：数据回到无人可读的待认领状态，不删除任何内容，之后可以重新指派。")}</p>
          {legacy.status === "frozen"
            ? <div className="notice" role="status">{tr("这个空间已被冻结。要撤销指派，请先在「工作空间」中解冻它。")}</div>
            : <AssignedCard ws={legacy} onDenied={onDenied} onRefresh={reload} onUndone={changed} />}
        </section>
      )}
    </>
  );
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
  const users = useAdminPage<User>(`/api/admin/users${qs({ q: query, status: "active" })}`);
  const searching = query !== q.trim();
  return <div className="col gap12">
    <p>{tr("{p0} 个项目，{p1} 位成员。请选择接收这个工作空间的已有账户。", {p0: ws.projects, p1: ws.members})}</p>
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
      impact={<>{tr("将把「{p0}」及其中{p1}个项目的访问权交给{p2}，角色为所有者。不会授予平台管理权限。", {p0: ws.name, p1: ws.projects, p2: chosen.email})}</>}
      run={(reason) => post<{ projects: number }>(`/api/admin/workspaces/${encodeURIComponent(ws.id)}/claim`, { user_id: chosen.id, reason })}
      onClose={() => setConfirming(false)} onDone={(r) => { setConfirming(false); onClaimed(tr("已把包含 {p0} 个项目的工作空间指派给 {p1}。", {p0: r.projects, p1: chosen.email})); }} />}
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
      onDone={(r) => { setConfirming(false); onUndone(tr("已撤销指派：{count} 位成员失去访问，数据全部保留。", { count: r.removed_members.length })); }} />}
  </div>;
}

function UnclaimImpact({ plan }: { plan: UnclaimPlan }) {
  return <>
    <p>{tr("「{name}」将回到待认领状态。", { name: plan.workspace.name })}</p>
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
        <li>{tr("这个空间的服务账户（{p0} 个）和经批准的 API 凭据（{p1} 项）将停止工作，但不会被删除。", { p0: plan.service_accounts, p1: plan.approvals })}</li>}
      <li>{plan.name_restored ? tr("名称改回「{name}」。", { name: plan.name_after }) : tr("名称「{name}」保持不变。", { name: plan.name_after })}</li>
    </ul>
    <p><b>{tr("不会删除任何数据：{p0} 个项目和 {p1} 个模板原样保留。", { p0: plan.projects, p1: plan.templates })}</b></p>
    <p>{tr("在再次指派之前，没有人能打开它，包括平台管理员。")}</p>
  </>;
}

function UsersTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [status, setStatus] = useState("");
  const [filterRole, setFilterRole] = useState("");
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<User>(`/api/admin/users${qs({ q: query, status, role: filterRole })}`, onDenied);
  const { me, refresh } = useSession();
  const toast = useToast();
  const [action, setAction] = useState<{ user: User; kind: "status" | "revoke" | "role" | "recovery" } | null>(null);
  const [role, setRole] = useState("none");
  const items = loading || err ? [] : data?.items || [];
  const u = action?.user;
  const title = action?.kind === "recovery" ? tr("发送找回邮件") : action?.kind === "revoke" ? tr("撤销全部会话") : action?.kind === "role" ? tr("修改平台角色") : u?.status === "active" ? tr("停用账户") : tr("恢复账户");
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("账户")}</h2>
      <input type="search" className="input" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder={tr("搜索邮箱或姓名")} aria-label={tr("搜索账户")} />
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新账户")}</button></div>
    <div className="row gap12 wrap">
      <label className="field">{tr("账户状态")}<select className="input" aria-label={tr("筛选账户状态")} value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">{tr("全部状态")}</option>{["active", "suspended", "pending", "pending_delete"].map((s) => <option key={s} value={s}>{ACCOUNT_STATUS[s] || s}</option>)}
      </select></label>
      <label className="field">{tr("平台角色")}<select className="input" aria-label={tr("筛选平台角色")} value={filterRole} onChange={(e) => setFilterRole(e.target.value)}>
        <option value="">{tr("全部角色")}</option>{["none", "operator", "admin"].map((r) => <option key={r} value={r}>{platformRole(r)}</option>)}
      </select></label>
    </div>
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载账户…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("平台账户")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("账户")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("平台角色")}</th><th scope="col">{tr("工作空间")}</th><th scope="col">{tr("在线会话")}</th><th scope="col">{tr("操作")}</th></tr></thead>
      <tbody>{items.map((user) => <tr key={user.id}>
        <th scope="row">{user.display_name || "—"}<div className="who2">{user.email}</div></th>
        <td>{ACCOUNT_STATUS[user.status] || user.status}</td><td>{platformRole(user.platform_role)}</td><td>{user.workspaces}</td><td>{user.sessions}</td>
        <td className="act"><RowMenu label={tr("{p0} 的账户操作", {p0: user.email})} items={[
          { label: user.status === "active" ? tr("停用账户") : tr("恢复账户"), danger: user.status === "active",
            disabled: loading || !!err || (user.status === "active" && (user.id === me?.id || user.platform_role !== "none")),
            hint: user.id === me?.id ? tr("不能停用自己的账户") : tr("请先将平台角色改为普通用户"), onSelect: () => setAction({ user, kind: "status" }) },
          { label: tr("发送找回邮件"), disabled: loading || !!err || !["active", "pending"].includes(user.status), onSelect: () => setAction({ user, kind: "recovery" }) },
          { label: tr("撤销全部会话"), danger: true, disabled: loading || !!err, onSelect: () => setAction({ user, kind: "revoke" }) },
          { label: tr("修改平台角色"), disabled: loading || !!err, onSelect: () => { setRole(user.platform_role); setAction({ user, kind: "role" }); } },
        ]} /></td></tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的账户。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
    {action && u && <ActionDialog title={title} confirmLabel={title} reason="required" danger ready={action.kind !== "role" || role !== u.platform_role}
      impact={<><b>{u.email}</b>：{action.kind === "recovery" ? tr("找回链接将发送到该账户当前邮箱。旧找回链接失效；管理员不会看到链接或设置新口令。") : action.kind === "revoke" ? tr("全部设备将退出，已签发的应用登录凭据会失效。")
        : action.kind === "role" ? tr("平台角色将从「{p0}」改为「{p1}」。这不授予文档读取权限。", {p0: platformRole(u.platform_role), p1: platformRole(role)})
          : u.status === "active" ? tr("账户将被停用，全部设备退出，应用访问立即停止。") : tr("账户将恢复正常；已经结束的登录不会恢复。")}
        {u.id === me?.id && <p>{tr("这是你自己的账户，完成后可能需要重新登录或离开平台管理页。")}</p>}</>}
      run={(reason) => post<{ sessions_revoked?: number }>(`/api/admin/users/${encodeURIComponent(u.id)}/${action.kind === "recovery" ? "recovery" : action.kind === "revoke" ? "revoke-sessions" : action.kind === "role" ? "platform-role" : "status"}`,
        { reason, ...(action.kind === "role" ? { role } : action.kind === "status" ? { status: u.status === "active" ? "suspended" : "active" } : {}) })}
      onClose={() => setAction(null)} onDone={(r) => {
        setAction(null); reload(); toast(action.kind === "recovery" ? tr("找回邮件已排队，请在邮件投递中查看状态。") : tr("{action}完成，撤销 {count} 个会话。", { action: title, count: r.sessions_revoked || 0 }), "ok");
        if (u.id === me?.id) void refresh();
      }}>
      {action.kind === "role" && <label className="field">{tr("新平台角色")}<select aria-label={tr("新平台角色")} className="input" value={role} onChange={(e) => setRole(e.target.value)}>
        {["none", "operator", "admin"].map((r) => <option key={r} value={r}>{platformRole(r)}</option>)}</select></label>}
    </ActionDialog>}
  </section>;
}

function platformRole(role: string) { return ({ none: tr("普通用户"), admin: tr("平台管理员"), operator: tr("平台运维") } as Record<string, string>)[role] || role; }

function WorkspacesTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<Workspace>(`/api/admin/workspaces${qs({ q: query })}`, onDenied);
  const [action, setAction] = useState<Workspace | null>(null);
  const [notice, setNotice] = useState("");
  const { me } = useSession();
  const items = loading || err ? [] : data?.items || [];
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("工作空间")}</h2><input className="input" type="search" aria-label={tr("搜索工作空间")} value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
      <button className="btn sm" disabled={loading} onClick={reload}>{tr("刷新工作空间")}</button></div>
    <p className="muted small">{tr("只显示数量，不显示内容；平台管理权限不包含读取文档或管理工作空间成员。")}</p>
    {notice && <div className="notice" role="status">{tr(notice)}</div>}
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载工作空间…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("平台工作空间")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("成员")}</th><th scope="col">{tr("项目")}</th><th scope="col">{tr("操作")}</th></tr></thead>
      <tbody>{items.map((w) => <tr key={w.id}>
        <th scope="row">{w.name}</th><td>{WORKSPACE_KIND[w.kind] || w.kind}</td><td>{WORKSPACE_STATUS[w.status] || w.status}</td><td>{w.members}</td><td>{w.projects}</td>
        <td><div className="row gap4 wrap">{(w.status === "active" || w.status === "frozen") && <button className="btn sm" disabled={loading || !!err} onClick={() => setAction(w)}>{w.status === "active" ? tr("冻结工作空间") : tr("解冻工作空间")}</button>}
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
      <option value="">{tr("全部操作")}</option><option value="admin.user.">{tr("账户管理")}</option><option value="admin.workspace.">{tr("工作空间管理")}</option>
      <option value="auth.">{tr("登录与账户安全")}</option><option value="member.">{tr("成员管理")}</option><option value="invite.">{tr("邀请")}</option><option value="mfa.">{tr("两步验证")}</option>
    </select></label>
    <p className="muted small">{tr("翻页时保留本次查询的范围；刷新后包含新记录。")}</p>
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>{tr("重试")}</button></div>}
    {loading && <p role="status">{tr("正在加载审计记录…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("审计记录")} aria-busy={loading}>
      <thead><tr><th scope="col">{tr("时间")}</th><th scope="col">{tr("操作")}</th><th scope="col">{tr("操作者")}</th><th scope="col">{tr("对象")}</th><th scope="col">{tr("结果")}</th><th scope="col">{tr("理由")}</th></tr></thead>
      <tbody>{items.map((a) => <tr key={a.id}>
        <td>{fmtTime(a.at)}</td><td>{a.action}</td><td>{a.actor_email || "—"}</td>
        <td>{a.object_id || "—"}</td><td>{a.result === "ok" ? tr("成功") : a.result === "denied" ? tr("被拒绝") : a.result}</td><td>{String(a.meta.reason || "")}</td>
      </tr>)}{!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>{tr("没有匹配的记录。")}</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
  </section>;
}
