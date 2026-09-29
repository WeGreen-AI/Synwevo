import { useState } from "react";
import { get, post } from "@synwevo/auth-client";
import { fmtTime, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useLoad } from "../lib/hooks";
import { ActionDialog } from "./ActionDialog";
import { useAdminData } from "./data";
import { errText } from "./labels";
import "./messages";
import "./platform.messages";
/**
 * Delegated platform roles (synwevo_core.platform_access): the administrator roster and the
 * per-account platform-access dialog.
 *
 * The server is the authority on every step: which roles the caller could grant or revoke comes
 * back with the account's explanation, and the grant and revoke routes check it again (holding a
 * permission is not being able to grant it; nobody changes their own authority to assign; the
 * last usable super administrator stays). This screen names what will happen before it happens
 * and shows the server's refusal in the reader's language when it does not.
 */

export const ROLE_ORDER = ["super_admin", "access_admin", "support", "auditor", "api_platform_admin"] as const;
const ROLE_LABEL: Record<string, string> = {
  super_admin: "最高管理员", access_admin: "账户与访问管理员", support: "支持", auditor: "审计查看",
  api_platform_admin: "API 平台管理员",
};
const ROLE_SUMMARY: Record<string, string> = {
  super_admin: "全部平台管理能力，包括授予和撤销管理员角色。",
  access_admin: "管理账户、工作空间、组织和邮件，查看审计；不能授予管理员角色。",
  support: "查看账户与工作空间，发送找回邮件、结束会话；不能停用账户。",
  auditor: "只读：概览、账户、工作空间、组织、配置、邮件、审计与管理员名册。",
  api_platform_admin: "只使用 API 管理平台和概览；不含账户管理。",
};
const PERMISSION_LABEL: Record<string, string> = {
  "platform.overview.view": "查看平台概览", "platform.users.view": "查看账户", "platform.users.manage": "停用与恢复账户",
  "platform.users.support": "发送找回邮件、结束会话", "platform.workspaces.view": "查看工作空间（只含数量）",
  "platform.workspaces.manage": "冻结与解冻工作空间", "platform.workspaces.claim": "把历史数据指派给没有平台权限的账户",
  "platform.orgs.view": "查看组织",
  "platform.orgs.manage": "在管理后台管理自己拥有的组织", "platform.config.view": "查看注册策略与登录方式",
  "platform.config.manage": "修改注册策略", "platform.mail.view": "查看邮件投递", "platform.mail.manage": "重试失败的找回与验证邮件",
  "platform.audit.view": "查看审计记录", "platform.roles.view": "查看平台管理员与权限说明",
  "platform.roles.assign": "授予和撤销委派角色", "platform.roles.assign_super": "授予和撤销最高管理员",
  "api_platform.manage": "使用 API 管理平台",
};
const LIST = " · ";

export const platformRoleLabel = (role: string) => tr(ROLE_LABEL[role] || role);
export const permissionLabel = (permission: string) => tr(PERMISSION_LABEL[permission] || permission);
/** The account's platform roles as one short phrase, or 无平台权限. */
export const platformRolesText = (roles: string[] | undefined) =>
  roles?.length ? roles.map(platformRoleLabel).join(LIST) : tr("无平台权限");
const grantedByText = (g: Grant) => g.granted_by_email
  || tr(g.granted_by.startsWith("migration:") ? "数据迁移（原平台管理员）" : g.granted_by.startsWith("bootstrap:") ? "主机初始化"
    : g.granted_by.startsWith("reconcile:") ? "版本回滚后同步" : "系统");

/** Reasons the service writes itself — `synwevo_core.platform_access.SYSTEM_REASONS`, held to it
 *  by tests/admin-access.test.mjs. Fixed sentences, so they are shown in the reader's language; a
 *  reason an administrator typed is shown exactly as typed. */
export const SYSTEM_REASONS = [
  "平台管理员（platform_role=admin）迁移为最高管理员", "上一版本在回滚期间把该账户设为平台管理员",
  "上一版本在回滚期间撤销了该账户的平台管理员角色", "首位平台管理员（一次性引导令牌）", "命令行 bootstrap admin 创建的最高管理员",
  "账户已删除", "账户已删除（安全日志重放）", "「平台运维」角色没有任何能力，迁移为无平台权限",
];
export const systemReasonText = (reason: string) => SYSTEM_REASONS.includes(reason) ? tr(reason) : reason;
/** A grant's reason: translated when the grant came from the host or the system (its `granted_by`
 *  is a `kind:detail` source, never an account id), as typed otherwise. */
export const grantReasonText = (g: { granted_by: string; reason: string }) =>
  g.granted_by.includes(":") ? systemReasonText(g.reason) : g.reason;

/** Why the caller cannot grant or revoke a role on this account, from the code the server gives
 *  (admin_routes `_assign_refusal`): shown as visible text, not a tooltip on a disabled button. */
const REVOKE_REFUSAL: Record<string, string> = {
  platform_permission_denied: "你的平台角色不能授予或撤销平台角色。",
  role_exceeds_own: "这个角色包含你没有的权限，只有持有这些权限的管理员能撤销它。",
  self_assignment: "不能撤销自己带授权能力的角色，请由另一位最高管理员操作。",
};
const ASSIGN_REFUSAL: Record<string, string> = {
  platform_permission_denied: "你的平台角色可以查看平台权限，但不能授予或撤销平台角色。",
  role_exceeds_own: "没有你可以授予这个账户的角色：只能授予由你自己持有的权限组成的角色。",
  self_assignment: "带授权能力的角色不能授予自己，请由另一位最高管理员操作。",
};
export const revokeRefusalText = (code: string | null | undefined) => code ? tr(REVOKE_REFUSAL[code] || "你的平台角色不能撤销这项授权") : "";
export const assignRefusalText = (code: string | null | undefined) => code ? tr(ASSIGN_REFUSAL[code] || "你的平台角色不包含这项操作") : "";

export type Grant = {
  id: string; role: string; granted_by: string; granted_by_email: string | null; granted_at: number;
  expires_at: number | null; reason: string; counted: boolean; not_counted?: string; revocable?: boolean;
  /** Why the caller could not revoke it (null when they can). */
  revoke_refusal?: string | null;
};
type StepUp = { capable: boolean; password: boolean; passkey_uv: boolean; totp: boolean; reauth: boolean };
type Explained = {
  user_id: string; email: string; status: string; email_verified: boolean; blocked_by: string[]; step_up: StepUp;
  roles: string[]; permissions: { permission: string; sources: { role: string }[] }[]; grants: Grant[];
  assignable_roles: string[];
  /** Why nothing can be granted, when `assignable_roles` is empty and the account lacks a role. */
  assign_refusal?: string | null;
  /** The server's catalogue: each built-in role and its permissions. */
  catalogue: { id: string; permissions: string[] }[];
};
type Roster = {
  roles: { id: string; permissions: string[]; assigning: boolean }[];
  roster: { user: { id: string; email: string; display_name: string | null; status: string }; roles: string[]; grants: Grant[];
    blocked_by: string[]; step_up: StepUp; super_admin_usable: boolean; last_sign_in_at: number | null }[];
  super_admins: number; recommended_super_admins: number;
};

function StepUpText({ step }: { step: StepUp }) {
  return <>{step.capable ? <span className="badge ok">{tr("能完成身份确认")}</span> : <span className="badge err">{tr("不能完成身份确认")}</span>}
    {step.capable && !step.reauth && <span className="cell-note">{tr("没有口令：危险操作要求的重新验证暂时无法完成")}</span>}</>;
}

/** The roster: who holds which platform role, whether they can use it, and the catalogue. */
export function PlatformAdminsTab({ onDenied, onOpen }: { onDenied: () => void; onOpen: (user: { id: string; email: string }) => void }) {
  useI18n();
  const load = useAdminData<Roster>("/api/admin/platform/roles", onDenied);
  const data = load.data;
  return <section className="card card-b col gap12">
    <div className="row gap12 wrap"><h2 className="grow">{tr("平台管理员名册")}</h2>
      <button className="btn sm" disabled={load.loading} onClick={load.reload}>{tr("刷新")}</button></div>
    <p className="muted small">{tr("持有平台角色的账户。角色由最高管理员授予；撤销在对方的下一个请求生效。平台角色不授予任何工作空间或文档的访问。")}</p>
    {data && data.super_admins < data.recommended_super_admins && <div className="notice warn" role="status">
      {data.super_admins === 0 ? tr("没有可用的最高管理员。需要由能访问服务器的人用命令行初始化一名。")
        : tr("只有 {count} 名可用的最高管理员。建议至少 2 名，由不同的人持有：唯一的最高管理员无法登录时，没有人能管理平台。", { count: data.super_admins })}</div>}
    {load.err && <div className="notice err" role="alert">{load.err} <button className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>}
    {load.loading && <p role="status">{tr("加载中…")}</p>}
    <div className="list-wrap"><table className="gov" aria-label={tr("平台管理员名册")} aria-busy={load.loading}>
      <thead><tr><th scope="col">{tr("账户")}</th><th scope="col">{tr("角色")}</th><th scope="col">{tr("身份确认")}</th><th scope="col">{tr("最近登录")}</th><th scope="col">{tr("操作")}</th></tr></thead>
      <tbody>{!load.loading && !load.err && data?.roster.map((row) => <tr key={row.user.id}>
        <th scope="row">{row.user.display_name || "—"}<div className="who2">{row.user.email}</div>
          {row.blocked_by.length > 0 && <span className="cell-note err">{tr("该账户当前不可用，这些授权不生效。")}</span>}</th>
        <td>{row.grants.map((g) => <div key={g.id}>{platformRoleLabel(g.role)}
          <span className="cell-note">{g.expires_at ? tr("到期") + " " + fmtTime(g.expires_at) : tr("长期")}{!g.counted && !row.blocked_by.length ? LIST + tr("未计入：邮箱未验证") : ""}</span></div>)}</td>
        <td><StepUpText step={row.step_up} />
          {row.roles.includes("super_admin") && !row.super_admin_usable && <span className="cell-note">{tr("不计为可用的最高管理员")}</span>}</td>
        <td>{row.last_sign_in_at ? fmtTime(row.last_sign_in_at) : "—"}</td>
        <td className="act"><button className="btn sm" onClick={() => onOpen(row.user)}>{tr("查看平台权限")}</button></td>
      </tr>)}
      {!load.loading && !load.err && !data?.roster.length && <tr className="empty-row"><td colSpan={5}>{tr("没有持有平台角色的账户。")}</td></tr>}</tbody>
    </table></div>
    {data && <details><summary>{tr("内置角色与权限")}</summary>
      <dl className="col gap8">{data.roles.map((role) => <div key={role.id}>
        <dt><b>{platformRoleLabel(role.id)}</b> <span className="muted small">{tr(ROLE_SUMMARY[role.id] || "")}</span></dt>
        <dd className="small">{role.permissions.map(permissionLabel).join(LIST)}</dd></div>)}</dl>
    </details>}
  </section>;
}

const EXPIRY_DAYS = [0, 1, 7, 30, 90];

/** One account's platform access: its grants and effective permissions, and — for a caller who
 *  may — granting a built-in role or revoking a grant, each confirmed with a reason. */
export function PlatformAccessDialog({ user, onClose, onChanged }: {
  user: { id: string; email: string }; onClose: () => void; onChanged: () => void;
}) {
  useI18n();
  const toast = useToast();
  const path = `/api/admin/users/${encodeURIComponent(user.id)}/platform-access`;
  const load = useLoad<Explained>(path, (signal) => get<Explained>(path, signal));
  const [step, setStep] = useState<{ kind: "grant" } | { kind: "revoke"; grant: Grant } | null>(null);
  const [role, setRole] = useState("");
  const [days, setDays] = useState(0);
  const data = load.data;
  const assignable = data?.assignable_roles || [];
  const chosen = role && assignable.includes(role) ? role : assignable[0] || "";
  const done = (message: string) => { setStep(null); load.reload(); onChanged(); toast(message, "ok"); };

  // The confirmation opens on top of this dialog (the modal stack keeps Escape and focus to the
  // top one); closing it returns focus to the button that opened it, and the grants stay visible
  // behind it.
  const confirm = step?.kind === "grant" && chosen ? (
    <ActionDialog title={tr("授予平台角色")} confirmLabel={tr("授予角色")} reason="required" danger={chosen === "super_admin"}
      impact={tr("{email} 将获得「{role}」：{permissions}。这不授予任何工作空间或文档的访问。对方现有的登录会按管理员规则缩短闲置时限，且不再保持登录。",
        { email: user.email, role: platformRoleLabel(chosen), permissions: (data?.catalogue.find((r) => r.id === chosen)?.permissions || []).map(permissionLabel).join(LIST) })}
      run={(reason) => post(`/api/admin/users/${encodeURIComponent(user.id)}/platform-grants`,
        { role: chosen, reason, ...(chosen !== "super_admin" && days ? { expires_at: Math.floor(Date.now() / 1000) + days * 86400 } : {}) })}
      onClose={() => setStep(null)} onDone={() => done(tr("已授予「{role}」。", { role: platformRoleLabel(chosen) }))}>
      <label className="field">{tr("新角色")}<select className="input" aria-label={tr("新角色")} value={chosen} onChange={(e) => setRole(e.target.value)}>
        {assignable.map((r) => <option key={r} value={r}>{platformRoleLabel(r)}</option>)}</select></label>
      <p className="small muted">{tr(ROLE_SUMMARY[chosen] || "")}</p>
      {chosen === "super_admin" ? <p className="small muted">{tr("最高管理员不设期限：到期会在无人操作时让平台失去最高管理员。")}</p>
        : <label className="field">{tr("期限")}<select className="input" aria-label={tr("期限")} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {EXPIRY_DAYS.map((n) => <option key={n} value={n}>{n ? tr("{count} 天", { count: n }) : tr("不设期限")}</option>)}</select></label>}
    </ActionDialog>
  ) : step?.kind === "revoke" ? (
    <ActionDialog title={tr("撤销平台角色")} confirmLabel={tr("撤销")} reason="required" danger
      impact={tr("{email} 将失去「{role}」。撤销在对方的下一个请求生效，此前完成的操作不受影响。", { email: user.email, role: platformRoleLabel(step.grant.role) })}
      run={(reason) => post(`/api/admin/platform-grants/${encodeURIComponent(step.grant.id)}/revoke`, { reason })}
      onClose={() => setStep(null)} onDone={() => done(tr("已撤销「{role}」。", { role: platformRoleLabel(step.grant.role) }))} />
  ) : null;

  return <><Modal title={tr("平台权限：{email}", { email: user.email })} onClose={onClose} size="wide"
    footer={<><span className="spacer" />
      <button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button>
      <button type="button" className="btn primary" disabled={!data || !assignable.length} onClick={() => { setRole(chosen); setDays(0); setStep({ kind: "grant" }); }}>{tr("授予平台角色")}</button></>}>
    <div className="col gap12">
      {load.loading && <p role="status">{tr("加载中…")}</p>}
      {!!load.error && <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>}
      {data && <>
        {data.blocked_by.length > 0 && <p className="notice warn">{tr("该账户当前不可用，这些授权不生效。")}</p>}
        <div className="row gap8 wrap">{tr("身份确认")}<StepUpText step={data.step_up} /></div>
        <div className="list-wrap"><table className="gov" aria-label={tr("平台权限")}>
          <thead><tr><th scope="col">{tr("角色")}</th><th scope="col">{tr("授予者")}</th><th scope="col">{tr("授予时间")}</th><th scope="col">{tr("到期")}</th><th scope="col">{tr("理由")}</th><th scope="col">{tr("操作")}</th></tr></thead>
          <tbody>{data.grants.map((g) => <tr key={g.id}>
            <th scope="row">{platformRoleLabel(g.role)}{!g.counted && !data.blocked_by.length && <span className="cell-note">{tr("未计入：邮箱未验证")}</span>}</th>
            <td>{grantedByText(g)}</td><td>{fmtTime(g.granted_at)}</td><td>{g.expires_at ? fmtTime(g.expires_at) : tr("长期")}</td>
            <td className="small">{grantReasonText(g)}</td>
            <td className="act"><button className="btn sm danger" disabled={!g.revocable} aria-describedby={g.revocable ? undefined : `revoke-why-${g.id}`}
              onClick={() => setStep({ kind: "revoke", grant: g })}>{tr("撤销")}</button>
              {!g.revocable && <span className="cell-note" id={`revoke-why-${g.id}`}>{revokeRefusalText(g.revoke_refusal || "platform_permission_denied")}</span>}</td>
          </tr>)}
          {!data.grants.length && <tr className="empty-row"><td colSpan={6}>{tr("这个账户没有平台角色。")}</td></tr>}</tbody>
        </table></div>
        {!assignable.length && data.assign_refusal && <p className="notice" role="note">{assignRefusalText(data.assign_refusal)}</p>}
        {data.permissions.length > 0 && <details><summary>{tr("有效权限")}</summary>
          <ul className="small">{data.permissions.map((p) => <li key={p.permission}>{permissionLabel(p.permission)}
            <span className="muted"> — {Array.from(new Set(p.sources.map((s) => s.role))).map(platformRoleLabel).join(LIST)}</span></li>)}</ul>
        </details>}
      </>}
    </div>
  </Modal>{confirm}</>;
}
