import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "../lib/api";
import { fmtTime, useDialog, useToast } from "../lib/ui";
import { useSession } from "../lib/session";

interface SessionRow {
  id: string;
  current: boolean;
  created_at: number;
  last_seen_at: number;
  expires_at: number;
  amr: string;
  client: string;
  ip: string | null;
}

/** How this session was authenticated. Shown because "signed in" is not one thing — and it is
 *  read from what the server recorded at login, not inferred from what the account *has*. */
const AMR: Record<string, string> = {
  password: "口令",
  "password+totp": "口令 + 动态码",
  passkey_uv: "Passkey（含设备验证）",
  passkey: "Passkey",
};

export default function Account() {
  const { me, signOut } = useSession();
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const toast = useToast();
  const { confirm } = useDialog();

  const load = useCallback(() => {
    get<{ items: SessionRow[] }>("/api/auth/sessions")
      .then((r) => { setRows(r.items); setErr(null); })
      .catch((e) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const revoke = async (row: SessionRow) => {
    if (row.current) {
      if (!(await confirm("退出当前设备", <>这会结束你当前的登录，需要重新登录。</>, "退出", false))) return;
      await signOut();
      return;
    }
    await del(`/api/auth/sessions/${row.id}`);
    toast("该设备已退出登录", "ok");
    load();
  };

  const revokeOthers = async () => {
    const others = (rows || []).filter((r) => !r.current).length;
    if (!others) return;
    const ok = await confirm("退出其他所有设备",
      <>将结束除当前设备外的 <b>{others}</b> 个登录。这些设备下次访问时需要重新登录。<br />
        如果你怀疑账户被他人使用，建议同时修改口令。</>, "全部退出", true);
    if (!ok) return;
    const r = await post<{ revoked: number }>("/api/auth/sessions/revoke-others", {});
    toast(`已退出 ${r.revoked} 个设备`, "ok");
    load();
  };

  if (!me) return <div className="empty">加载中…</div>;

  return (
    <div className="app">
      <div className="topbar">
        <Link to="/" className="brand"><span className="logo">W</span><span className="desktop-only">DocWeave</span></Link>
        <span className="sep" />
        <Link to="/" className="btn ghost sm">← 返回项目</Link>
        <span className="grow"><b>账户与安全</b></span>
      </div>

      <div style={{ overflow: "auto", flex: 1 }}>
        <div className="home">
          {err && <div className="notice err">{err}</div>}

          <section className="card card-b col gap12">
            <h2 className="secttl">账户</h2>
            <dl className="kv">
              <dt>邮箱</dt><dd>{me.email}</dd>
              <dt>名称</dt><dd>{me.display_name || <span className="muted">未设置</span>}</dd>
              <dt>平台权限</dt>
              <dd>{me.platform_role === "admin" ? <span className="badge warn">平台管理员</span>
                : me.platform_role === "operator" ? <span className="badge">平台运维</span>
                  : <span className="muted">普通用户</span>}</dd>
            </dl>
            <p className="tiny muted">
              平台权限与工作空间角色是两回事：平台管理员管理账户与服务状态，<b>不会</b>因此获得任何
              工作空间内文档的读取权限。
            </p>
          </section>

          <section className="card card-b col gap12 mt12">
            <h2 className="secttl">工作空间</h2>
            <table className="tbl">
              <thead><tr><th>名称</th><th>类型</th><th>我的角色</th><th>可导出</th></tr></thead>
              <tbody>
                {me.workspaces.map((w) => (
                  <tr key={w.id}>
                    <td>{w.name}</td>
                    <td className="small muted">{w.kind === "personal" ? "个人" : w.kind === "org" ? "组织" : w.kind}</td>
                    <td className="small">{({ owner: "所有者", admin: "管理员", editor: "编辑", viewer: "只读" } as any)[w.role] || w.role}</td>
                    <td className="small">{w.can_export ? "是" : <span className="muted">否</span>}</td>
                  </tr>
                ))}
                {!me.workspaces.length && (
                  <tr><td colSpan={4} className="muted" style={{ textAlign: "center", padding: 16 }}>
                    你当前不属于任何工作空间，因此看不到任何项目。请联系管理员。
                  </td></tr>
                )}
              </tbody>
            </table>
          </section>

          <section className="card card-b col gap12 mt12">
            <div className="row">
              <h2 className="secttl grow">登录的设备</h2>
              {!!rows?.some((r) => !r.current) && (
                <button className="btn danger sm" onClick={revokeOthers}>退出其他所有设备</button>
              )}
            </div>
            <table className="tbl" aria-label="登录会话">
              <thead><tr><th>设备</th><th>登录方式</th><th>最近活动</th><th>到期</th><th>来源</th><th /></tr></thead>
              <tbody>
                {(rows || []).map((r) => (
                  <tr key={r.id}>
                    <td>
                      {r.client}
                      {r.current && <span className="badge ok" style={{ marginLeft: 6 }}>当前设备</span>}
                    </td>
                    <td className="small muted">{AMR[r.amr] || r.amr || "—"}</td>
                    <td className="small muted nowrap">{fmtTime(r.last_seen_at)}</td>
                    <td className="small muted nowrap">{fmtTime(r.expires_at)}</td>
                    {/* An IP, not a guessed city: a wrong location shown as fact is worse than
                        none when you are deciding whether a session is yours. */}
                    <td className="small muted mono">{r.ip || "—"}</td>
                    <td className="nowrap">
                      <button className="btn xs" onClick={() => revoke(r)}>
                        {r.current ? "退出" : "结束"}
                      </button>
                    </td>
                  </tr>
                ))}
                {rows && !rows.length && (
                  <tr><td colSpan={6} className="muted" style={{ textAlign: "center", padding: 16 }}>没有活动的登录</td></tr>
                )}
              </tbody>
            </table>
            <p className="tiny muted">
              只显示 User-Agent 能确定的设备信息，不猜测地理位置。结束某个登录后，该设备的下一次请求即失效。
            </p>
          </section>

          <section className="card card-b col gap12 mt12">
            <h2 className="secttl">Passkey 与两步验证</h2>
            {/* Stated plainly rather than shown as a disabled button that does nothing. */}
            <p className="muted">这一部分尚未上线。当前登录方式为邮箱 + 口令。</p>
          </section>
        </div>
      </div>
    </div>
  );
}
