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
import { ApiError, get, post } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/ui";

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
  const [tab, setTab] = useState<Tab>("overview");
  const [denied, setDenied] = useState(false);

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
      <div className="topbar">
        <span className="brand"><span className="logo" aria-hidden="true">◫</span>
          <span className="desktop-only">平台管理</span></span>
        <span className="sep" />
        <nav className="row gap4" aria-label="管理后台">
          {(["overview", "users", "workspaces", "audit"] as Tab[]).map((t) => (
            <button key={t} className={"btn ghost sm" + (tab === t ? " on" : "")}
                    aria-current={tab === t ? "page" : undefined}
                    onClick={() => setTab(t)}>
              {{ overview: "概览", users: "账户", workspaces: "工作空间", audit: "审计" }[t]}
            </button>
          ))}
        </nav>
        <span className="grow" />
        <a className="btn ghost sm" href="/account">账户与安全</a>
        <span className="muted sm desktop-only">{me?.email}</span>
      </div>

      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          {tab === "overview" && <OverviewTab onDenied={() => setDenied(true)} />}
          {tab === "users" && <UsersTab onDenied={() => setDenied(true)} />}
          {tab === "workspaces" && <WorkspacesTab onDenied={() => setDenied(true)} />}
          {tab === "audit" && <AuditTab onDenied={() => setDenied(true)} />}
        </div>
      </div>
    </div>
  );
}

/** Shared loader: 404 here means "not an administrator", which the page treats as not existing. */
function useAdminData<T>(path: string, onDenied: () => void) {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      setData(await get<T>(path));
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return onDenied();
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [path, onDenied]);

  useEffect(() => { void load(); }, [load]);
  return { data, err, loading, reload: load };
}

function OverviewTab({ onDenied }: { onDenied: () => void }) {
  const { data, err, loading } = useAdminData<Overview>("/api/admin/overview", onDenied);
  if (loading) return <div className="empty">加载中…</div>;
  if (err) return <div className="notice err">{err}</div>;
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

      {data.unclaimed && (
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
  const toast = useToast();
  const [users, setUsers] = useState<User[] | null>(null);
  const [target, setTarget] = useState("");
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<null | { projects: number; email: string }>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    get<{ items: User[] }>("/api/admin/users").then((r) => setUsers(r.items)).catch(() => setUsers([]));
  }, []);

  const chosen = users?.find((u) => u.id === target);
  const ready = target && reason.trim().length >= 8 && password.length > 0 && !busy;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setErr(null);
    try {
      // Re-authenticate first, scoped to administration. A password proved for one purpose does
      // not unlock the others.
      await post("/api/auth/reauth", { password, scope: "admin" });
      const r = await post<{ projects: number }>(`/api/admin/workspaces/${ws.id}/claim`,
                                                 { user_id: target, reason: reason.trim() });
      // Only after the server actually succeeded.
      setDone({ projects: r.projects, email: chosen?.email || target });
      toast("历史数据已指派", "ok");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
      setPassword("");
    }
  }

  if (done) {
    return (
      <div className="notice ok">
        已把 <b>{done.projects}</b> 个历史项目所在的工作空间指派给 <b>{done.email}</b>。
        对方下次进入 DocWeave 时即可看到。此操作已写入审计记录。
      </div>
    );
  }

  return (
    <form className="col gap12" onSubmit={submit}>
      <div className="row gap12 wrap">
        <Stat label="项目" value={ws.projects} />
        <Stat label="当前成员" value={ws.members} />
      </div>

      <label className="col gap4">
        <span>指派给</span>
        <select value={target} onChange={(e) => setTarget(e.target.value)} required>
          <option value="">选择一个已有账户…</option>
          {(users || []).filter((u) => u.status === "active").map((u) => (
            <option key={u.id} value={u.id}>
              {u.display_name ? `${u.display_name}（${u.email}）` : u.email}
            </option>
          ))}
        </select>
        <span className="muted sm">只能选择已经存在的账户。这一步不会创建账户，也不会授予平台管理权。</span>
      </label>

      <label className="col gap4">
        <span>理由</span>
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
                  placeholder="为什么把这些数据交给这个账户？" required minLength={8} />
        <span className="muted sm">会原样写入审计记录。一年以后，这行字是唯一能解释这件事的东西。</span>
      </label>

      <label className="col gap4">
        <span>你的口令</span>
        <input type="password" value={password} autoComplete="current-password"
               onChange={(e) => setPassword(e.target.value)} required />
        <span className="muted sm">危险操作需要重新验证身份。</span>
      </label>

      {chosen && (
        <div className="notice">
          将把 <b>{ws.projects}</b> 个项目所在的工作空间「{ws.name}」指派给 <b>{chosen.email}</b>，
          角色为所有者。业务数据一行都不会被改写，其他工作空间不受影响。
        </div>
      )}
      {err && <div className="notice err">{err}</div>}

      <div className="row gap12">
        <button className="btn primary" type="submit" disabled={!ready}>
          {busy ? "指派中…" : "指派历史数据"}
        </button>
      </div>
    </form>
  );
}

function UsersTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const { data, err, loading, reload } = useAdminData<{ items: User[] }>(
    `/api/admin/users?q=${encodeURIComponent(q)}`, onDenied);
  const toast = useToast();
  const [acting, setActing] = useState<string | null>(null);

  async function act(u: User, kind: "status" | "revoke") {
    const what = kind === "revoke" ? "撤销全部会话"
      : u.status === "active" ? "停用账户" : "恢复账户";
    const reason = window.prompt(`${what}：${u.email}\n\n请写明理由（至少 8 个字，会写入审计记录）`);
    if (!reason || reason.trim().length < 8) return;
    const password = window.prompt("危险操作需要重新验证身份。请输入你的口令：");
    if (!password) return;
    setActing(u.id);
    try {
      await post("/api/auth/reauth", { password, scope: "admin" });
      if (kind === "revoke") {
        const r = await post<{ sessions_revoked: number }>(
          `/api/admin/users/${u.id}/revoke-sessions`, { reason: reason.trim() });
        toast(`已撤销 ${r.sessions_revoked} 个会话`, "ok");
      } else {
        await post(`/api/admin/users/${u.id}/status`,
                   { status: u.status === "active" ? "suspended" : "active", reason: reason.trim() });
        toast(what + "完成", "ok");
      }
      await reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "err");
    } finally {
      setActing(null);
    }
  }

  return (
    <section className="card card-b col gap12">
      <div className="row gap12">
        <h2 className="grow">账户</h2>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索邮箱或姓名"
               aria-label="搜索账户" />
      </div>
      {err && <div className="notice err">{err}</div>}
      {loading ? <div className="empty">加载中…</div> : (
        <table className="tbl">
          <thead>
            <tr><th>账户</th><th>状态</th><th>平台角色</th><th>空间</th><th>在线会话</th><th /></tr>
          </thead>
          <tbody>
            {(data?.items || []).map((u) => (
              <tr key={u.id}>
                <td>
                  <div>{u.display_name || "—"}</div>
                  <div className="muted sm">{u.email}</div>
                </td>
                <td>{u.status === "active" ? "正常" : u.status === "suspended" ? "已停用" : u.status}</td>
                <td>{u.platform_role === "admin" ? "平台管理员"
                  : u.platform_role === "operator" ? "平台运维" : "—"}</td>
                <td>{u.workspaces}</td>
                <td>{u.sessions}</td>
                <td className="row gap4">
                  <button className="btn ghost sm" disabled={acting === u.id}
                          onClick={() => void act(u, "status")}>
                    {u.status === "active" ? "停用" : "恢复"}
                  </button>
                  <button className="btn ghost sm" disabled={acting === u.id || u.sessions === 0}
                          onClick={() => void act(u, "revoke")}>撤销会话</button>
                </td>
              </tr>
            ))}
            {!data?.items.length && (
              <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>
                没有匹配的账户
              </td></tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}

function WorkspacesTab({ onDenied }: { onDenied: () => void }) {
  const { data, err, loading } = useAdminData<{ items: Workspace[] }>("/api/admin/workspaces", onDenied);
  return (
    <section className="card card-b col gap12">
      <h2>工作空间</h2>
      <p className="muted sm">显示数量，不显示内容。管理权限不等于读取文档的权限。</p>
      {err && <div className="notice err">{err}</div>}
      {loading ? <div className="empty">加载中…</div> : (
        <table className="tbl">
          <thead><tr><th>名称</th><th>类型</th><th>状态</th><th>成员</th><th>项目</th><th>已启用应用</th></tr></thead>
          <tbody>
            {(data?.items || []).map((w) => (
              <tr key={w.id}>
                <td>{w.name}<div className="muted sm">{w.id}</div></td>
                <td>{w.kind === "legacy" ? "历史数据" : w.kind === "personal" ? "个人" : "团队"}</td>
                <td>{w.status === "pending_claim" ? "待认领" : w.status === "active" ? "正常" : w.status}</td>
                <td>{w.members}</td>
                <td>{w.projects}</td>
                <td>{w.apps.length ? w.apps.join("、") : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function AuditTab({ onDenied }: { onDenied: () => void }) {
  const [q, setQ] = useState("");
  const { data, err, loading } = useAdminData<{ items: AuditRow[] }>(
    `/api/admin/audit?q=${encodeURIComponent(q)}`, onDenied);
  return (
    <section className="card card-b col gap12">
      <div className="row gap12">
        <h2 className="grow">审计记录</h2>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索操作者或对象"
               aria-label="搜索审计记录" />
      </div>
      <p className="muted sm">
        只读。这是单机数据库，所以这是「应用层面无法改写」，不是「不可篡改」——拿到数据库文件的人可以改它。
      </p>
      {err && <div className="notice err">{err}</div>}
      {loading ? <div className="empty">加载中…</div> : (
        <table className="tbl">
          <thead><tr><th>时间</th><th>操作</th><th>操作者</th><th>对象</th><th>结果</th><th>说明</th></tr></thead>
          <tbody>
            {(data?.items || []).map((a) => (
              <tr key={a.id}>
                <td className="muted sm">{new Date(a.at * 1000).toLocaleString("zh-CN")}</td>
                <td><code>{a.action}</code></td>
                <td>{a.actor_email || <span className="muted">—</span>}</td>
                <td className="muted sm">{a.object_kind ? `${a.object_kind} ${a.object_id}` : "—"}</td>
                <td>{a.result === "ok" ? "成功" : a.result === "denied" ? "被拒绝" : a.result}</td>
                <td className="muted sm">{String((a.meta as { reason?: string }).reason || "")}</td>
              </tr>
            ))}
            {!data?.items.length && (
              <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 24 }}>
                没有匹配的记录
              </td></tr>
            )}
          </tbody>
        </table>
      )}
    </section>
  );
}
