import { get } from "@synwevo/auth-client";
import { fmtTime, Modal, t as tr, useI18n } from "@synwevo/ui";
import { useLoad } from "../lib/hooks";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, errText, fmtDuration, roleLabel } from "./labels";
import { platformRoleLabel } from "./PlatformAccess";
import "./messages";
import "./platform.messages";

/**
 * One account's facts for the people who govern accounts (admin coverage A13): the metadata the
 * account's own settings page shows its owner — sign-in methods, memberships, sessions, the
 * sign-in backoff it is under, its slice of the audit log — never a password hash, TOTP seed,
 * recovery code, token or document. The server decides what the caller may see (workspace and
 * organization names follow the list permissions, the timeline the audit permission); this
 * dialog explains an omission rather than hiding the section, so a support reader knows what a
 * wider role would show. It changes nothing: every action stays in the row menu, with its own
 * reason and re-authentication.
 */
export type AccountDetail = {
  user: { id: string; email: string; display_name: string | null; status: string; platform_role: string;
    email_verified_at: number | null; created_at: number; updated_at: number };
  application_identity: boolean;
  blocked_by: string[];
  outranks_caller: boolean;
  sign_in: {
    password_set: boolean; totp: boolean; recovery_remaining: number;
    passkeys: { name: string | null; created_at: number; last_used_at: number | null; backed_up: boolean; uv_capable: boolean }[];
    external_identities: { provider: string; label: string; available: boolean; email: string | null; email_verified: boolean;
      created_at: number; last_used_at: number | null }[];
  };
  platform_roles: string[];
  platform_grants: { role: string; granted_at: number; expires_at: number | null }[];
  /** `name` is null when the caller may not list workspaces (platform.workspaces.view). */
  workspaces: { id: string; name: string | null; kind: string; status: string; role: string; can_export: boolean; joined_at: number }[];
  /** `name` is null when the caller may not list organizations (platform.orgs.view). */
  organizations: { id: string; name: string | null; status: string; role: string; joined_at: number }[];
  sessions: { created_at: number; last_seen_at: number; expires_at: number; amr: string; client: string;
    client_parts?: { browser: string | null; platform: string | null }; ip: string | null; persistent: boolean; auth_provider: string | null }[];
  backoff: { failures: number; first_failure_at: number; last_failure_at: number; retry_after: number } | null;
  audit: { permitted: boolean; limit: number; items: { id: string; at: number; action: string; actor_kind: string;
    actor_email: string | null; object_kind: string | null; object_id: string | null; result: string; reason: string | null }[] };
};

/** The authentication methods a session records (`sessions.amr`), worded as the account's own
 *  page words them (pages/Account.tsx). */
export const AMR_LABELS: Record<string, string> = {
  password: "口令", "password+totp": "口令与动态验证码", "password+recovery": "口令与恢复码",
  passkey_uv: "安全密钥（已验证身份）", passkey: "安全密钥", email: "邮箱验证码",
  "email+totp": "邮箱验证码与动态验证码", "email+recovery": "邮箱验证码与恢复码",
};
/** `idp:<provider>+totp` / `+recovery`: the external identity, then the second factor. */
export const FACTOR_SUFFIX_LABELS: Record<string, string> = { totp: "{method}与动态验证码", recovery: "{method}与恢复码" };
export const ORG_ROLE_LABELS: Record<string, string> = { owner: "所有者", admin: "管理员", member: "成员" };

export function amrText(amr: string): string {
  if (AMR_LABELS[amr]) return tr(AMR_LABELS[amr]);
  const m = /^idp:([a-z0-9_-]+)(?:\+(totp|recovery))?$/.exec(amr);
  if (!m) return amr;
  const method = tr("外部身份（{provider}）", { provider: m[1] });
  return m[2] ? tr(FACTOR_SUFFIX_LABELS[m[2]], { method }) : method;
}

/** "Chrome on macOS" in the reader's language: the server sends product names, not a sentence. */
export function deviceText(s: { client: string; client_parts?: { browser: string | null; platform: string | null } }): string {
  const parts = s.client_parts;
  if (!parts) return s.client;
  if (parts.browser && parts.platform) return tr("{platform} 上的 {browser}", { platform: parts.platform, browser: parts.browser });
  return parts.browser || parts.platform || tr("未知设备");
}

const DASH = "—";

export function AccountDetailBody({ detail: d }: { detail: AccountDetail }) {
  useI18n();
  const u = d.user;
  const b = d.backoff;
  return <div className="col gap16">
    {d.application_identity && <p className="notice" role="note">{tr("应用身份：由它所属的应用管理，不是一个人。")}</p>}
    {d.outranks_caller && <p className="notice" role="note">{tr("该账户的平台权限高于你的：可以查看，不能对它执行管理操作。")}</p>}

    <section className="col gap8"><h3 className="secttl">{tr("资料")}</h3>
      <dl className="facts">
        <dt>{tr("邮箱")}</dt><dd>{u.email}</dd>
        <dt>{tr("姓名")}</dt><dd>{u.display_name || DASH}</dd>
        <dt>{tr("状态")}</dt><dd>{ACCOUNT_STATUS[u.status] || u.status}</dd>
        <dt>{tr("邮箱验证")}</dt><dd>{u.email_verified_at ? tr("已验证（{time}）", { time: fmtTime(u.email_verified_at) }) : tr("未验证")}</dd>
        <dt>{tr("创建时间")}</dt><dd>{fmtTime(u.created_at)}</dd>
        <dt>{tr("最近更新")}</dt><dd>{fmtTime(u.updated_at)}</dd>
        <dt>{tr("平台角色")}</dt><dd>{d.platform_grants.length
          ? d.platform_grants.map((g) => g.expires_at
            ? tr("{role}（到期：{time}）", { role: platformRoleLabel(g.role), time: fmtTime(g.expires_at) })
            : platformRoleLabel(g.role)).join("、")
          : tr("无平台权限")}</dd>
      </dl></section>

    <section className="col gap8"><h3 className="secttl">{tr("登录方式")}</h3>
      <dl className="facts">
        <dt>{tr("口令")}</dt><dd>{d.sign_in.password_set ? tr("已设置") : tr("未设置")}</dd>
        <dt>{tr("两步验证")}</dt><dd>{d.sign_in.totp ? tr("已开启，剩余恢复码 {count} 个", { count: d.sign_in.recovery_remaining }) : tr("未开启")}</dd>
        <dt>{tr("安全密钥")}</dt><dd>{d.sign_in.passkeys.length ? <ul>{d.sign_in.passkeys.map((k, i) => <li key={i}>
          {k.name || tr("未命名")} · {tr("登记于 {time}", { time: fmtTime(k.created_at) })} · {k.last_used_at ? tr("最近使用：{time}", { time: fmtTime(k.last_used_at) }) : tr("未使用过")}{k.backed_up ? ` · ${tr("已同步到多台设备")}` : ""}
        </li>)}</ul> : tr("无")}</dd>
        <dt>{tr("外部身份")}</dt><dd>{d.sign_in.external_identities.length ? <ul>{d.sign_in.external_identities.map((x, i) => <li key={i}>
          {x.label}{x.email ? ` · ${x.email}` : ""} · {x.last_used_at ? tr("最近使用：{time}", { time: fmtTime(x.last_used_at) }) : tr("未使用过")}{x.available ? "" : ` · ${tr("提供商已停用")}`}
        </li>)}</ul> : tr("无")}</dd>
      </dl></section>

    <section className="col gap8"><h3 className="secttl">{tr("工作空间（{count} 个）", { count: d.workspaces.length })}</h3>
      {d.workspaces.length ? <div className="list-wrap"><table className="gov" aria-label={tr("工作空间")}>
        <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("角色")}</th><th scope="col">{tr("加入时间")}</th></tr></thead>
        <tbody>{d.workspaces.map((w) => <tr key={w.id}>
          <th scope="row">{w.name ?? <span className="muted">{tr("名称不可见：你的角色不能查看工作空间列表")}</span>}</th>
          <td>{WORKSPACE_KIND[w.kind] || w.kind}</td><td>{WORKSPACE_STATUS[w.status] || w.status}</td>
          <td>{roleLabel(w.role)}{w.can_export ? ` · ${tr("可导出")}` : ""}</td><td>{fmtTime(w.joined_at)}</td>
        </tr>)}</tbody></table></div>
        : <p className="muted small">{tr("不是任何工作空间的成员。")}</p>}
    </section>

    <section className="col gap8"><h3 className="secttl">{tr("组织（{count} 个）", { count: d.organizations.length })}</h3>
      {d.organizations.length ? <div className="list-wrap"><table className="gov" aria-label={tr("组织")}>
        <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("角色")}</th><th scope="col">{tr("加入时间")}</th></tr></thead>
        <tbody>{d.organizations.map((o) => <tr key={o.id}>
          <th scope="row">{o.name ?? <span className="muted">{tr("名称不可见：你的角色不能查看组织列表")}</span>}</th>
          <td>{WORKSPACE_STATUS[o.status] || o.status}</td><td>{tr(ORG_ROLE_LABELS[o.role] || o.role)}</td><td>{fmtTime(o.joined_at)}</td>
        </tr>)}</tbody></table></div>
        : <p className="muted small">{tr("不是任何组织的成员。")}</p>}
    </section>

    <section className="col gap8"><h3 className="secttl">{tr("在线会话（{count} 个）", { count: d.sessions.length })}</h3>
      {d.sessions.length ? <div className="list-wrap"><table className="gov" aria-label={tr("在线会话")}>
        <thead><tr><th scope="col">{tr("设备")}</th><th scope="col">{tr("登录方式")}</th><th scope="col">{tr("最近活动")}</th><th scope="col">{tr("到期")}</th><th scope="col">{tr("地址")}</th></tr></thead>
        <tbody>{d.sessions.map((s, i) => <tr key={i}>
          <th scope="row">{deviceText(s)}</th><td>{amrText(s.amr)}{s.persistent ? ` · ${tr("保持登录")}` : ""}</td>
          <td>{fmtTime(s.last_seen_at)}</td><td>{fmtTime(s.expires_at)}</td><td>{s.ip || DASH}</td>
        </tr>)}</tbody></table></div>
        : <p className="muted small">{tr("当前没有在线会话。")}</p>}
      <p className="muted small">{tr("地址是登录时记录的来源地址，不推断位置；撤销会话在账户行的菜单里。")}</p>
    </section>

    <section className="col gap8"><h3 className="secttl">{tr("登录退避")}</h3>
      {b ? <p>{b.retry_after > 0
        ? tr("连续失败 {count} 次，最近一次 {time}；下次尝试需等待 {wait}。", { count: b.failures, time: fmtTime(b.last_failure_at), wait: fmtDuration(b.retry_after) })
        : tr("连续失败 {count} 次，最近一次 {time}；现在可以重试，成功登录后计数清零。", { count: b.failures, time: fmtTime(b.last_failure_at) })}</p>
        : <p className="muted small">{tr("没有失败的登录记录。")}</p>}
    </section>

    <section className="col gap8"><h3 className="secttl">{tr("安全时间线")}</h3>
      {!d.audit.permitted ? <p className="notice" role="note">{tr("需要审计查看权限才能显示时间线；完整记录在审计页。")}</p> : <>
        <p className="muted small">{tr("最近 {count} 条与该账户有关的登录、安全与管理事件；完整记录在审计页。", { count: d.audit.limit })}</p>
        {d.audit.items.length ? <div className="list-wrap"><table className="gov" aria-label={tr("安全时间线")}>
          <thead><tr><th scope="col">{tr("时间")}</th><th scope="col">{tr("操作")}</th><th scope="col">{tr("操作者")}</th><th scope="col">{tr("对象")}</th><th scope="col">{tr("结果")}</th><th scope="col">{tr("理由")}</th></tr></thead>
          <tbody>{d.audit.items.map((a) => <tr key={a.id}>
            <td>{fmtTime(a.at)}</td><td>{a.action}</td><td>{a.actor_email || (a.actor_kind === "user" ? DASH : a.actor_kind)}</td>
            <td>{a.object_id || DASH}</td><td>{a.result === "ok" ? tr("成功") : a.result === "denied" ? tr("被拒绝") : a.result}</td><td>{a.reason || DASH}</td>
          </tr>)}</tbody></table></div>
          : <p className="muted small">{tr("没有相关事件。")}</p>}
      </>}
    </section>
  </div>;
}

export function AccountDetailDialog({ user, onClose }: { user: { id: string; email: string }; onClose: () => void }) {
  useI18n();
  const path = `/api/admin/users/${encodeURIComponent(user.id)}`;
  const load = useLoad<AccountDetail>(path, (signal) => get<AccountDetail>(path, signal));
  return <Modal title={tr("账户详情：{email}", { email: user.email })} onClose={onClose} size="wide"
    footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
    {load.loading && <p role="status">{tr("正在加载账户详情…")}</p>}
    {!!load.error && <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>}
    {load.data && <AccountDetailBody detail={load.data} />}
  </Modal>;
}
