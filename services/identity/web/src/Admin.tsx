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
import { ActionDialog } from "./components/ActionDialog";
import { Topbar } from "./components/Topbar";
import { RowMenu } from "./components/RowMenu";
import { PageControls } from "./components/ListControls";
import { useDebounced, useLoad } from "./lib/hooks";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, errText } from "./lib/labels";
import { ApiError, get, post, qs } from "@synwevo/auth-client";
import { useSession } from "@synwevo/auth-client";
import { useToast } from "@synwevo/ui";

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
type AuditRow = {
  id: string; at: number; action: string; actor_email: string | null;
  object_kind: string | null; object_id: string | null; result: string;
  meta: Record<string, unknown>;
};

type Tab = "overview" | "users" | "workspaces" | "audit";

export default function Admin() {
  const { me } = useSession();
  const [params, setParams] = useSearchParams();
  const tab = (["overview", "users", "workspaces", "audit"].includes(params.get("tab") || "") ? params.get("tab") : "overview") as Tab;
  const setTab = (tab: Tab) => setParams({ tab });
  const [denied, setDenied] = useState(false);

  const onDenied = useCallback(() => setDenied(true), []);
  useEffect(() => { document.title = "平台管理 · Synwevo Account"; }, []);

  // The server answers 404 for a non-administrator, deliberately — an ordinary account learns
  // nothing about what exists here. The page mirrors that rather than saying "forbidden".
  if (denied) {
    return (
      <div className="empty">
        <div className="big">页面不存在</div>
        <a href="/account">前往账户设置</a>
      </div>
    );
  }

  return (
    <div className="app">
      <Topbar title="平台管理">
        <nav className="row gap4" aria-label="管理后台">
          {(["overview", "users", "workspaces", "audit"] as Tab[]).map((t) => (
            <button key={t} className={"btn ghost sm" + (tab === t ? " on" : "")}
                    aria-current={tab === t ? "page" : undefined}
                    onClick={() => setTab(t)}>
              {{ overview: "概览", users: "账户", workspaces: "工作空间", audit: "审计" }[t]}
            </button>
          ))}
        </nav>
        <a className="btn ghost sm" href="/account">账户与安全</a>
        <span className="muted sm desktop-only">{me?.email}</span>
      </Topbar>

      <main style={{ overflow: "auto", flex: 1 }}>
        <div className="acct-page">
          {tab === "overview" && <OverviewTab onDenied={onDenied} />}
          {tab === "users" && <UsersTab onDenied={onDenied} />}
          {tab === "workspaces" && <WorkspacesTab onDenied={onDenied} />}
          {tab === "audit" && <AuditTab onDenied={onDenied} />}
        </div>
      </main>
    </div>
  );
}

/** Shared loader: 404 here means "not an administrator", which the page treats as not existing. */
function useAdminData<T>(path: string, onDenied: () => void) {
  const load = useLoad<T>(path, (signal) => get<T>(path, signal));
  useEffect(() => {
    if (load.error instanceof ApiError && !load.error.upstream && load.error.status === 404) onDenied();
  }, [load.error, onDenied]);
  return { data: load.data, err: load.error ? errText(load.error) : null, loading: load.loading, reload: load.reload };
}

type AdminPage<T> = { items: T[]; total: number; offset: number; limit: number; snapshot: number };
const ignoreDenied = () => {};

/** Keep the server's insertion boundary when changing pages. Search and explicit refresh
 * start a new listing. Rows still reflect current permissions and account state. */
function useAdminPage<T>(filterPath: string, onDenied: () => void = ignoreDenied) {
  const [page, setPage] = useState<{ key: string; offset: number; snapshot?: number }>({ key: filterPath, offset: 0 });
  useEffect(() => { setPage({ key: filterPath, offset: 0 }); }, [filterPath]);
  const current = page.key === filterPath ? page : { key: filterPath, offset: 0 };
  const path = `${filterPath}${filterPath.includes("?") ? "&" : "?"}limit=50&offset=${current.offset}${current.snapshot === undefined ? "" : `&snapshot=${current.snapshot}`}`;
  const load = useAdminData<AdminPage<T>>(path, onDenied);
  const setOffset = (offset: number) => setPage({ key: filterPath, offset, snapshot: load.data?.snapshot });
  // A removal or state filter change can empty the last page; recover to the last valid page.
  useEffect(() => {
    if (!load.loading && !load.err && load.data && current.offset >= load.data.total && current.offset > 0) {
      setPage({ key: filterPath, offset: Math.max(0, Math.floor((load.data.total - 1) / 50) * 50), snapshot: load.data.snapshot });
    }
  }, [load.loading, load.err, load.data, current.offset, filterPath]);
  return { ...load, offset: load.data?.offset ?? 0, setOffset,
    reload: () => { setPage({ key: filterPath, offset: 0 }); load.reload(); } };
}

function OverviewTab({ onDenied }: { onDenied: () => void }) {
  const { data, err, loading, reload } = useAdminData<Overview>("/api/admin/overview", onDenied);
  if (loading) return <div className="empty">加载中…</div>;
  if (err) return <div className="notice err" role="alert">{err} <button className="btn" onClick={reload}>重试</button></div>;
  if (!data) return null;

  return (
    <>
      <section className="card card-b col gap12">
        <h2>平台概览</h2>
        <div className="row gap12 wrap">
          <Stat label="账户" value={data.users} />
          <Stat label="其中已停用" value={data.suspended} />
          <Stat label="工作空间" value={data.workspaces} />
          <Stat label="平台管理员" value={data.admins} />
        </div>
      </section>

      {data.unclaimed?.status === "pending_claim" && (
        <section className="card card-b col gap12">
          <h2>待认领的历史数据</h2>
          <p className="muted">
            这些数据是账户体系建立之前产生的。它现在属于一个 <b>没有任何成员</b> 的工作空间，
            因此没有人能读取它——包括平台管理员。指派给一个已有账户之后，才会有人能打开。
          </p>
          <ClaimCard ws={data.unclaimed} />
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

function ClaimCard({ ws }: { ws: Workspace }) {
  const [q, setQ] = useState("");
  const [chosen, setChosen] = useState<User | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const query = useDebounced(q.trim());
  const users = useAdminPage<User>(`/api/admin/users${qs({ q: query, status: "active" })}`);
  const searching = query !== q.trim();
  if (done) return <div className="notice ok" role="status">{done}</div>;
  return <div className="col gap12">
    <p>{ws.projects} 个项目，{ws.members} 位成员。请选择接收此空间的已有账户。</p>
    <label className="field">搜索接收账户<input className="input" type="search" maxLength={100} value={q}
      onChange={(e) => { setQ(e.target.value); setChosen(null); }} /></label>
    {(users.loading || searching) && <p role="status">正在查找账户…</p>}
    {!!users.err && <div className="notice err" role="alert">{errText(users.err)} <button className="btn sm" onClick={users.reload}>重试</button></div>}
    <div className="picker" role="group" aria-label="接收账户">
      {!users.loading && !searching && !users.err && users.data?.items.map((u) => <label key={u.id}>
        <input type="radio" name="claim-account" checked={chosen?.id === u.id} onChange={() => setChosen(u)} />
        <span>{u.display_name || u.email}<span className="dim"> {u.email}</span></span></label>)}
    </div>
    {!users.loading && !searching && !users.err && !users.data?.items.length && <p role="status">没有可选的正常账户。</p>}
    <PageControls offset={users.offset} total={users.data?.total || 0} busy={users.loading || searching || !!users.err}
      onPage={(offset) => { setChosen(null); users.setOffset(offset); }} />
    <div><button className="btn primary" disabled={!chosen || !users.data?.items.some((u) => u.id === chosen.id) || users.loading || searching || !!users.err} onClick={() => setConfirming(true)}>指派历史数据</button></div>
    {confirming && chosen && <ActionDialog title="指派历史数据" confirmLabel="指派历史数据" reason="required" danger
      impact={<>将把「{ws.name}」及其中 {ws.projects} 个项目的访问权交给 <b>{chosen.email}</b>，角色为所有者。不会授予平台管理权限。</>}
      run={(reason) => post<{ projects: number }>(`/api/admin/workspaces/${encodeURIComponent(ws.id)}/claim`, { user_id: chosen.id, reason })}
      onClose={() => setConfirming(false)} onDone={(r) => { setConfirming(false); setDone(`已把包含 ${r.projects} 个项目的空间指派给 ${chosen.email}。`); }} />}
  </div>;
}

function UsersTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [status, setStatus] = useState("");
  const [filterRole, setFilterRole] = useState("");
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<User>(`/api/admin/users${qs({ q: query, status, role: filterRole })}`, onDenied);
  const { me, refresh } = useSession();
  const toast = useToast();
  const [action, setAction] = useState<{ user: User; kind: "status" | "revoke" | "role" } | null>(null);
  const [role, setRole] = useState("none");
  const items = loading || err ? [] : data?.items || [];
  const u = action?.user;
  const title = action?.kind === "revoke" ? "撤销全部会话" : action?.kind === "role" ? "修改平台角色" : u?.status === "active" ? "停用账户" : "恢复账户";
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">账户</h2>
      <input type="search" className="input" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder="搜索邮箱或姓名" aria-label="搜索账户" />
      <button className="btn sm" disabled={loading} onClick={reload}>刷新账户</button></div>
    <div className="row gap12 wrap">
      <label className="field">账户状态<select className="input" aria-label="筛选账户状态" value={status} onChange={(e) => setStatus(e.target.value)}>
        <option value="">全部状态</option>{["active", "suspended", "pending", "pending_delete"].map((s) => <option key={s} value={s}>{ACCOUNT_STATUS[s] || s}</option>)}
      </select></label>
      <label className="field">平台角色<select className="input" aria-label="筛选平台角色" value={filterRole} onChange={(e) => setFilterRole(e.target.value)}>
        <option value="">全部角色</option>{["none", "operator", "admin"].map((r) => <option key={r} value={r}>{platformRole(r)}</option>)}
      </select></label>
    </div>
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>重试</button></div>}
    {loading && <p role="status">正在加载账户…</p>}
    <div className="list-wrap"><table className="gov" aria-label="平台账户" aria-busy={loading}>
      <thead><tr><th scope="col">账户</th><th scope="col">状态</th><th scope="col">平台角色</th><th scope="col">空间</th><th scope="col">在线会话</th><th scope="col">操作</th></tr></thead>
      <tbody>{items.map((user) => <tr key={user.id}>
        <th scope="row">{user.display_name || "—"}<div className="who2">{user.email}</div></th>
        <td>{ACCOUNT_STATUS[user.status] || user.status}</td><td>{platformRole(user.platform_role)}</td><td>{user.workspaces}</td><td>{user.sessions}</td>
        <td className="act"><RowMenu label={`${user.email} 的账户操作`} items={[
          { label: user.status === "active" ? "停用账户" : "恢复账户", danger: user.status === "active",
            disabled: loading || !!err || (user.status === "active" && (user.id === me?.id || user.platform_role !== "none")),
            hint: user.id === me?.id ? "不能停用自己的账户" : "请先将平台角色改为普通用户", onSelect: () => setAction({ user, kind: "status" }) },
          { label: "撤销全部会话", danger: true, disabled: loading || !!err, onSelect: () => setAction({ user, kind: "revoke" }) },
          { label: "修改平台角色", disabled: loading || !!err, onSelect: () => { setRole(user.platform_role); setAction({ user, kind: "role" }); } },
        ]} /></td></tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>没有匹配的账户。</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
    {action && u && <ActionDialog title={title} confirmLabel={title} reason="required" danger ready={action.kind !== "role" || role !== u.platform_role}
      impact={<><b>{u.email}</b>：{action.kind === "revoke" ? "全部设备将退出，已签发的应用登录凭据会失效。"
        : action.kind === "role" ? `平台角色将从「${platformRole(u.platform_role)}」改为「${platformRole(role)}」。这不授予文档读取权限。`
          : u.status === "active" ? "账户将被停用，全部设备退出，应用访问立即停止。" : "账户将恢复正常；已经结束的登录不会恢复。"}
        {u.id === me?.id && <p>这是你自己的账户，完成后可能需要重新登录或离开平台管理页。</p>}</>}
      run={(reason) => post<{ sessions_revoked?: number }>(`/api/admin/users/${encodeURIComponent(u.id)}/${action.kind === "revoke" ? "revoke-sessions" : action.kind === "role" ? "platform-role" : "status"}`,
        { reason, ...(action.kind === "role" ? { role } : action.kind === "status" ? { status: u.status === "active" ? "suspended" : "active" } : {}) })}
      onClose={() => setAction(null)} onDone={(r) => {
        setAction(null); reload(); toast(`${title}完成${r.sessions_revoked !== undefined ? `，撤销 ${r.sessions_revoked} 个会话` : ""}`, "ok");
        if (u.id === me?.id) void refresh();
      }}>
      {action.kind === "role" && <label className="field">新平台角色<select aria-label="新平台角色" className="input" value={role} onChange={(e) => setRole(e.target.value)}>
        {["none", "operator", "admin"].map((r) => <option key={r} value={r}>{platformRole(r)}</option>)}</select></label>}
    </ActionDialog>}
  </section>;
}

function platformRole(role: string) { return ({ none: "普通用户", admin: "平台管理员", operator: "平台运维" } as Record<string, string>)[role] || role; }

function WorkspacesTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<Workspace>(`/api/admin/workspaces${qs({ q: query })}`, onDenied);
  const [action, setAction] = useState<Workspace | null>(null);
  const [notice, setNotice] = useState("");
  const { me } = useSession();
  const items = loading || err ? [] : data?.items || [];
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">工作空间</h2><input className="input" type="search" aria-label="搜索工作空间" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} />
      <button className="btn sm" disabled={loading} onClick={reload}>刷新空间</button></div>
    <p className="muted small">显示数量，不显示内容。平台管理权限不等于读取文档或管理空间成员的权限。</p>
    {notice && <div className="notice" role="status">{notice}</div>}
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>重试</button></div>}
    {loading && <p role="status">正在加载工作空间…</p>}
    <div className="list-wrap"><table className="gov" aria-label="平台工作空间" aria-busy={loading}>
      <thead><tr><th scope="col">名称</th><th scope="col">类型</th><th scope="col">状态</th><th scope="col">成员</th><th scope="col">项目</th><th scope="col">操作</th></tr></thead>
      <tbody>{items.map((w) => <tr key={w.id}>
        <th scope="row">{w.name}</th><td>{WORKSPACE_KIND[w.kind] || w.kind}</td><td>{WORKSPACE_STATUS[w.status] || w.status}</td><td>{w.members}</td><td>{w.projects}</td>
        <td><div className="row gap4 wrap">{(w.status === "active" || w.status === "frozen") && <button className="btn sm" disabled={loading || !!err} onClick={() => setAction(w)}>{w.status === "active" ? "冻结空间" : "解冻空间"}</button>}
          {me?.workspaces.some((m) => m.id === w.id && (m.role === "owner" || m.role === "admin")) && <a className="btn sm" href={`/account/workspaces/${encodeURIComponent(w.id)}/members`}>成员与邀请</a>}</div></td>
      </tr>)}
      {!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>没有匹配的工作空间。</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
    {action && <ActionDialog title={action.status === "active" ? "冻结空间" : "解冻空间"} confirmLabel={action.status === "active" ? "冻结空间" : "解冻空间"} danger reason="required"
      impact={<>「{action.name}」：{action.status === "active" ? "成员将立即失去应用访问，当前会话将移出空间。已有成员关系和内容保留。" : "空间将恢复可用，成员仍按原有资格访问。此前结束的登录不会恢复。"}</>}
      run={(reason) => post<{ restore_note?: string; status: string }>(`/api/admin/workspaces/${encodeURIComponent(action.id)}/${action.status === "active" ? "freeze" : "unfreeze"}`, { reason })}
      onClose={() => setAction(null)} onDone={(r) => { setAction(null); reload(); setNotice(`空间已${r.status === "frozen" ? "冻结" : "解冻"}。${r.restore_note || ""}`); }} />}
  </section>;
}

function AuditTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [action, setAction] = useState("");
  const { data, err, loading, reload, offset, setOffset } = useAdminPage<AuditRow>(`/api/admin/audit${qs({ q: query, action })}`, onDenied);
  const items = loading || err ? [] : data?.items || [];
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">审计记录</h2><input className="input" type="search" maxLength={100} value={q} onChange={(e) => setQ(e.target.value)} placeholder="操作者邮箱或对象" aria-label="搜索审计记录" />
      <button className="btn sm" disabled={loading} onClick={reload}>刷新审计</button></div>
    <label className="field">操作范围<select className="input" aria-label="筛选审计操作" value={action} onChange={(e) => setAction(e.target.value)}>
      <option value="">全部操作</option><option value="admin.user.">账户管理</option><option value="admin.workspace.">空间管理</option>
      <option value="auth.">登录与账户安全</option><option value="member.">成员管理</option><option value="invite.">邀请</option><option value="mfa.">两步验证</option>
    </select></label>
    <p className="muted small">只读。翻页时保留本次查询的记录范围；点击刷新查看新记录。</p>
    {err && <div className="notice err" role="alert">{err} <button className="btn sm" onClick={reload}>重试</button></div>}
    {loading && <p role="status">正在加载审计记录…</p>}
    <div className="list-wrap"><table className="gov" aria-label="审计记录" aria-busy={loading}>
      <thead><tr><th scope="col">时间</th><th scope="col">操作</th><th scope="col">操作者</th><th scope="col">对象</th><th scope="col">结果</th><th scope="col">理由</th></tr></thead>
      <tbody>{items.map((a) => <tr key={a.id}>
        <td>{new Date(a.at * 1000).toLocaleString("zh-CN")}</td><td>{a.action}</td><td>{a.actor_email || "—"}</td>
        <td>{a.object_id || "—"}</td><td>{a.result === "ok" ? "成功" : a.result === "denied" ? "被拒绝" : a.result}</td><td>{String(a.meta.reason || "")}</td>
      </tr>)}{!loading && !err && !items.length && <tr className="empty-row"><td colSpan={6}>没有匹配的记录。</td></tr>}</tbody>
    </table></div>
    <PageControls offset={offset} total={data?.total || 0} busy={loading || !!err} onPage={setOffset} />
  </section>;
}
