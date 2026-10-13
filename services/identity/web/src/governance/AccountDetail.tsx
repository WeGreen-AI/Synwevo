import { useState, type ReactNode } from "react";
import { get, post } from "@synwevo/auth-client";
import { fmtTime, Modal, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useLoad } from "../lib/hooks";
import type { Held } from "./access";
import { ActionDialog } from "./ActionDialog";
import { ACCOUNT_STATUS, WORKSPACE_KIND, WORKSPACE_STATUS, errText, fmtDuration, roleLabel } from "./labels";
import { platformRoleLabel } from "./PlatformAccess";
import { SecondFactorPanel, type ResetTarget } from "./SecondFactorResetDialog";
import "./messages";
import "./platform.messages";

/**
 * One account's facts for the people who govern accounts (admin coverage A13): the metadata the
 * account's own settings page shows its owner — sign-in methods, memberships, sessions, the
 * sign-in backoff it is under, its slice of the audit log — never a password hash, TOTP seed,
 * recovery code, token or document. The server decides what the caller may see (workspace and
 * organization names follow the list permissions, the timeline the audit permission); this
 * dialog explains an omission rather than hiding the section, so a support reader knows what a
 * wider role would show. The per-object actions (A17: end one device, remove a passkey, unlink an
 * external identity) open the same confirmation as every dangerous action — reason, strict
 * re-authentication — and the server keeps the person's own lockout rules.
 */
export type AccountDetail = {
  user: { id: string; email: string; display_name: string | null; status: string; platform_role: string;
    email_verified_at: number | null; created_at: number; updated_at: number };
  application_identity: boolean;
  blocked_by: string[];
  outranks_caller: boolean;
  sign_in: {
    password_set: boolean; totp: boolean; recovery_remaining: number;
    passkeys: { id: string; name: string | null; created_at: number; last_used_at: number | null; backed_up: boolean; uv_capable: boolean }[];
    external_identities: { id: string; provider: string; label: string; available: boolean; email: string | null; email_verified: boolean;
      created_at: number; last_used_at: number | null }[];
  };
  platform_roles: string[];
  platform_grants: { role: string; granted_at: number; expires_at: number | null }[];
  /** `name` is null when the caller may not list workspaces (platform.workspaces.view). */
  workspaces: { id: string; name: string | null; kind: string; status: string; role: string; can_export: boolean; joined_at: number;
    /** the applications the workspace has switched on (ids and names), for the access explanation (A49) */
    apps?: { id: string; name: string }[] }[];
  /** `name` is null when the caller may not list organizations (platform.orgs.view). */
  organizations: { id: string; name: string | null; status: string; role: string; joined_at: number }[];
  sessions: { id: string; created_at: number; last_seen_at: number; expires_at: number; amr: string; client: string;
    client_parts?: { browser: string | null; platform: string | null }; ip: string | null; persistent: boolean; auth_provider: string | null }[];
  /** `throttle_id`: the handle the platform-configuration page releases this backoff by (G19). */
  backoff: { failures: number; first_failure_at: number; last_failure_at: number; retry_after: number; throttle_id?: string } | null;
  /** The personal Agent's content-safety suspension (admin coverage A13): the administrator's reason, never a conversation. */
  agent_access?: { suspended: boolean; suspension: { id: string; reason: string; report_id: string | null; created_at: number; created_by_email: string | null } | null };
  /** A17: browser push devices, never their endpoints; `active` means a live session still backs the device. */
  push_subscriptions?: { id: string; user_agent: string | null; created_at: number; last_sent_at: number | null; fail_count: number; active: boolean }[];
  /** A20: the deletion request, or the rules that would refuse one today (profile.deletion_state). */
  deletion?: { pending: boolean; requested_at: number | null; due_at: number | null; overdue: boolean;
    cancellation_link_valid: boolean; blockers: string[] };
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
/** The deletion request's own rules, as the detail names them (profile.deletion_blockers). */
export const DELETION_BLOCKER_LABELS: Record<string, string> = {
  last_super_admin: "最后一名可用的最高管理员", ownership_required: "共享工作空间的所有权或管理权未移交",
  organization_ownership_required: "组织的所有权或管理权未移交",
};

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

/** "Chrome · macOS" from the user agent the browser sent when it subscribed (the account page's own wording). */
export function pushDeviceText(ua: string | null | undefined): string {
  const s = ua || "";
  const browser = /Edg\//.test(s) ? "Edge" : /OPR\//.test(s) ? "Opera" : /Firefox\//.test(s) ? "Firefox" : /Chrome\//.test(s) ? "Chrome" : /Safari\//.test(s) ? "Safari" : tr("浏览器");
  const os = /iPhone|iPad/.test(s) ? "iOS" : /Android/.test(s) ? "Android" : /Mac OS X/.test(s) ? "macOS" : /Windows/.test(s) ? "Windows" : /Linux/.test(s) ? "Linux" : "";
  return os ? `${browser} · ${os}` : browser;
}

/** One of the per-object actions (A17); the dialog confirms it with a reason and re-authentication. */
export type AccountAction = { kind: "session" | "passkey" | "external" | "push" | "agent_lift" | "backoff_lift"; id: string; label: string };

/** One workspace row's "explain access" request (A49): opened as its own dialog over the detail. */
export type ExplainTarget = { workspace_id: string; workspace_name: string | null; apps: { id: string; name: string }[] };

/** What the second-factor reset (A26) needs to know about the person to decide what it offers. */
export const resetTarget = (d: AccountDetail): ResetTarget => ({ id: d.user.id, email: d.user.email, status: d.user.status,
  platform_access: d.platform_grants.length > 0 || d.platform_roles.length > 0, application_identity: d.application_identity });

/** `secondFactor`: the second-factor section (A26), which loads its own state; shown after the sign-in methods. */
export function AccountDetailBody({ detail: d, held, onAct, onExplain, secondFactor }: { detail: AccountDetail; held?: Held; onAct?: (action: AccountAction) => void;
  onExplain?: (target: ExplainTarget) => void; secondFactor?: ReactNode }) {
  useI18n();
  const u = d.user;
  const b = d.backoff;
  // The server decides again on every action; the buttons only follow what it would allow.
  const mayAct = !!onAct && !!held?.has("platform.users.manage") && !d.outranks_caller && !d.application_identity;
  // Releasing the sign-in backoff is platform configuration (the throttle card's permission), not account management.
  const mayLift = !!onAct && !!held?.has("platform.config.manage") && !d.outranks_caller;
  // Explaining access is a read (platform.roles.view); it never acts as the person.
  const mayExplain = !!onExplain && !!held?.has("platform.roles.view");
  const act = (action: AccountAction) => <button type="button" className="btn sm" onClick={() => onAct?.(action)}>{action.label}</button>;
  return <div className="col gap16">
    {d.application_identity && <p className="notice" role="note">{tr("应用身份：由它所属的应用管理，不是一个人。")}</p>}
    {d.outranks_caller && <p className="notice" role="note">{tr("该账户的平台权限高于你的：可以查看，不能对它执行管理操作。")}</p>}
    {!!held && !held.has("platform.users.manage") && <p className="muted small">{tr("你的平台角色可以查看这些信息，但不能对其执行操作。")}</p>}

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
            : platformRoleLabel(g.role)).join(tr("、"))
          : tr("无平台权限")}</dd>
      </dl></section>

    {d.agent_access && <section className="col gap8"><h3 className="secttl">{tr("Agent 使用权限")}</h3>
      {d.agent_access.suspension ? <>
        <p><span className="badge warn">{tr("已暂停 Agent")}</span> {tr("自 {time} 起暂停；理由：{reason}", { time: fmtTime(d.agent_access.suspension.created_at), reason: d.agent_access.suspension.reason })}
          {d.agent_access.suspension.created_by_email ? ` · ${tr("操作者：{email}", { email: d.agent_access.suspension.created_by_email })}` : ""}
          {d.agent_access.suspension.report_id ? ` · ${tr("关联举报 {id}", { id: d.agent_access.suspension.report_id })}` : ""}</p>
        {mayAct && <div>{act({ kind: "agent_lift", id: d.agent_access.suspension.id, label: tr("恢复 Agent 使用权限") })}</div>}
      </> : <p className="muted small">{tr("正常：没有生效中的暂停。")}</p>}
    </section>}

    {d.deletion && <section className="col gap8"><h3 className="secttl">{tr("注销申请")}</h3>
      {d.deletion.pending ? <>
        <p>{tr("本人于 {requested} 申请注销，{due} 到期删除。", { requested: d.deletion.requested_at ? fmtTime(d.deletion.requested_at) : DASH, due: d.deletion.due_at ? fmtTime(d.deletion.due_at) : DASH })}</p>
        {d.deletion.overdue ? <p className="notice" role="note">{tr("已过到期时间，等待后台任务终结；可在平台概览页立即处理。")}</p>
          : <p className="muted small">{d.deletion.cancellation_link_valid ? tr("取消注销的链接仍然有效，到期前本人可随时取消。") : tr("取消注销的链接已失效；可在账户行的菜单里重发。")}</p>}
      </> : d.deletion.blockers.length
        ? <p>{tr("没有注销申请。若本人现在申请注销，将被拒绝：{rules}。", { rules: d.deletion.blockers.map((b) => tr(DELETION_BLOCKER_LABELS[b] || b)).join(tr("、")) })}</p>
        : <p className="muted small">{tr("没有注销申请；本人现在可以申请注销。")}</p>}
    </section>}

    <section className="col gap8"><h3 className="secttl">{tr("登录方式")}</h3>
      <dl className="facts">
        <dt>{tr("口令")}</dt><dd>{d.sign_in.password_set ? tr("已设置") : tr("未设置")}</dd>
        <dt>{tr("两步验证")}</dt><dd>{d.sign_in.totp ? tr("已开启，剩余恢复码 {count} 个", { count: d.sign_in.recovery_remaining }) : tr("未开启")}</dd>
        <dt>{tr("安全密钥")}</dt><dd>{d.sign_in.passkeys.length ? <ul>{d.sign_in.passkeys.map((k) => <li key={k.id}>
          {k.name || tr("未命名")} · {tr("登记于 {time}", { time: fmtTime(k.created_at) })} · {k.last_used_at ? tr("最近使用：{time}", { time: fmtTime(k.last_used_at) }) : tr("未使用过")}{k.backed_up ? ` · ${tr("已同步到多台设备")}` : ""}
          {mayAct && <> {act({ kind: "passkey", id: k.id, label: tr("移除此安全密钥") })}</>}
        </li>)}</ul> : tr("无")}</dd>
        <dt>{tr("外部身份")}</dt><dd>{d.sign_in.external_identities.length ? <ul>{d.sign_in.external_identities.map((x) => <li key={x.id}>
          {x.label}{x.email ? ` · ${x.email}` : ""} · {x.last_used_at ? tr("最近使用：{time}", { time: fmtTime(x.last_used_at) }) : tr("未使用过")}{x.available ? "" : ` · ${tr("提供商已停用")}`}
          {mayAct && <> {act({ kind: "external", id: x.id, label: tr("解除此外部身份") })}</>}
        </li>)}</ul> : tr("无")}</dd>
      </dl></section>

    {secondFactor}

    <section className="col gap8"><h3 className="secttl">{tr("工作空间（{count} 个）", { count: d.workspaces.length })}</h3>
      {d.workspaces.length ? <div className="list-wrap"><table className="gov" aria-label={tr("工作空间")}>
        <thead><tr><th scope="col">{tr("名称")}</th><th scope="col">{tr("类型")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("角色")}</th><th scope="col">{tr("加入时间")}</th>{mayExplain && <th scope="col" className="act">{tr("访问")}</th>}</tr></thead>
        <tbody>{d.workspaces.map((w) => <tr key={w.id}>
          <th scope="row">{w.name ?? <span className="muted">{tr("名称不可见：你的角色不能查看工作空间列表")}</span>}</th>
          <td>{WORKSPACE_KIND[w.kind] || w.kind}</td><td>{WORKSPACE_STATUS[w.status] || w.status}</td>
          <td>{roleLabel(w.role)}{w.can_export ? ` · ${tr("可导出")}` : ""}</td><td>{fmtTime(w.joined_at)}</td>
          {mayExplain && <td className="act"><button type="button" className="btn sm" onClick={() => onExplain?.({ workspace_id: w.id, workspace_name: w.name, apps: w.apps || [] })}>{tr("解释访问")}</button></td>}
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
        <thead><tr><th scope="col">{tr("设备")}</th><th scope="col">{tr("登录方式")}</th><th scope="col">{tr("最近活动")}</th><th scope="col">{tr("到期")}</th><th scope="col">{tr("地址")}</th>{mayAct && <th scope="col" className="act">{tr("操作")}</th>}</tr></thead>
        <tbody>{d.sessions.map((s) => <tr key={s.id}>
          <th scope="row">{deviceText(s)}</th><td>{amrText(s.amr)}{s.persistent ? ` · ${tr("保持登录")}` : ""}</td>
          <td>{fmtTime(s.last_seen_at)}</td><td>{fmtTime(s.expires_at)}</td><td>{s.ip || DASH}</td>
          {mayAct && <td className="act">{act({ kind: "session", id: s.id, label: tr("结束此设备的登录") })}</td>}
        </tr>)}</tbody></table></div>
        : <p className="muted small">{tr("当前没有在线会话。")}</p>}
      <p className="muted small">{tr("地址是登录时记录的来源地址，不推断位置；撤销会话在账户行的菜单里。")}</p>
    </section>

    {d.push_subscriptions && <section className="col gap8"><h3 className="secttl">{tr("推送设备（{count} 个）", { count: d.push_subscriptions.length })}</h3>
      {d.push_subscriptions.length ? <>
        <div className="list-wrap"><table className="gov" aria-label={tr("推送设备")}>
          <thead><tr><th scope="col">{tr("设备")}</th><th scope="col">{tr("开启时间")}</th><th scope="col">{tr("最近推送")}</th><th scope="col">{tr("状态")}</th></tr></thead>
          <tbody>{d.push_subscriptions.map((p) => <tr key={p.id}>
            <th scope="row">{pushDeviceText(p.user_agent)}</th><td>{fmtTime(p.created_at)}</td>
            <td>{p.last_sent_at ? fmtTime(p.last_sent_at) : tr("未推送过")}{p.fail_count > 0 ? ` · ${tr("最近 {count} 次推送失败", { count: p.fail_count })}` : ""}</td>
            <td>{p.active ? tr("可接收") : tr("登录已结束，不再接收")}</td>
          </tr>)}</tbody></table></div>
        {mayAct && <div>{act({ kind: "push", id: "all", label: tr("撤销全部推送订阅") })}</div>}
      </> : <p className="muted small">{tr("没有开启推送的设备。")}</p>}
    </section>}

    <section className="col gap8"><h3 className="secttl">{tr("登录退避")}</h3>
      {b ? <p>{b.retry_after > 0
        ? tr("连续失败 {count} 次，最近一次 {time}；下次尝试需等待 {wait}。", { count: b.failures, time: fmtTime(b.last_failure_at), wait: fmtDuration(b.retry_after) })
        : tr("连续失败 {count} 次，最近一次 {time}；现在可以重试，成功登录后计数清零。", { count: b.failures, time: fmtTime(b.last_failure_at) })}</p>
        : <p className="muted small">{tr("没有失败的登录记录。")}</p>}
      {b?.throttle_id && mayLift && <div>{act({ kind: "backoff_lift", id: b.throttle_id, label: tr("解除登录退避") })}</div>}
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

const ACTION_PATH: Record<AccountAction["kind"], (uid: string, id: string) => string> = {
  session: (uid, id) => `/api/admin/users/${encodeURIComponent(uid)}/sessions/${encodeURIComponent(id)}/revoke`,
  passkey: (uid, id) => `/api/admin/users/${encodeURIComponent(uid)}/passkeys/${encodeURIComponent(id)}/remove`,
  external: (uid, id) => `/api/admin/users/${encodeURIComponent(uid)}/external-identities/${encodeURIComponent(id)}/unlink`,
  push: (uid) => `/api/admin/users/${encodeURIComponent(uid)}/push-subscriptions/revoke`,
  agent_lift: (uid) => `/api/admin/agent-safety/users/${encodeURIComponent(uid)}/lift`,
  backoff_lift: () => "/api/admin/security/throttles/lift",
};
const ACTION_IMPACT: Record<AccountAction["kind"], string> = {
  session: "{email}：该设备的登录与它签发的应用访问将立即结束；其他设备不受影响。本人会收到安全通知。",
  passkey: "{email}：该安全密钥将不能再用于登录；若它是账户唯一的登录方式，服务器会拒绝。本人会收到安全通知。",
  external: "{email}：该外部账户的下一次登录将不再进入此账户；若它是唯一的登录方式，服务器会拒绝。本人会收到安全通知。",
  push: "{email}：该账户所有浏览器的推送订阅将被撤销，这些设备不再收到系统通知；登录与登录方式不变。本人会收到安全通知，可在自己的设备上重新开启。",
  agent_lift: "{email}：该账户将可以再次使用个人 Agent；理由会记录在审计中。",
  backoff_lift: "{email}：该账户的登录退避将被解除，下一次登录尝试从零开始；口令、会话与权限不变。",
};
const ACTION_DONE: Record<AccountAction["kind"], string> = {
  session: "已结束该设备的登录。", passkey: "已移除该安全密钥。", external: "已解除该外部身份。", push: "已撤销全部推送订阅。",
  agent_lift: "已恢复该账户的 Agent 使用权限。", backoff_lift: "已解除该账户的登录退避。",
};

/** The server's answer to "why can or cannot this person open this application here" (A49). */
export type AccessExplanation = {
  user: { id: string; email: string | null; status: string };
  workspace: { id: string; name: string | null; status: string };
  app: { id: string; name: string; status: string; scope: string };
  gates: { gate: string; state: "pass" | "fail" | "skip"; detail: Record<string, unknown> }[];
  verdict: { allowed: boolean; workspace_id: string | null; reason: string | null };
  consistent: boolean;
};
export const GATE_LABELS: Record<string, string> = {
  account: "账户状态", application: "应用状态", membership: "空间成员资格", workspace: "空间状态", organization: "组织状态与组织成员",
  workspace_app: "空间已启用该应用", entitlement: "使用资格",
};
export const GATE_STATE_LABELS: Record<string, string> = { pass: "通过", fail: "未通过", skip: "不适用" };
const POLICY_LABELS: Record<string, string> = { members: "成员即可使用", granted: "需逐人授予" };
const ENTITLEMENT_LABELS: Record<string, string> = { granted: "已授予", revoked: "已撤销", none: "无单独记录" };
const ORG_REASON_LABELS: Record<string, string> = { organization_unavailable: "组织不可用", not_an_organization_member: "不是该组织的成员" };
const APP_SCOPE_LABELS: Record<string, string> = { workspace: "按工作空间", organization: "按组织", personal: "个人应用", platform: "平台门户" };
export const GATE_DETAIL_SENTENCES = [...Object.values(POLICY_LABELS), ...Object.values(ENTITLEMENT_LABELS), ...Object.values(ORG_REASON_LABELS), ...Object.values(APP_SCOPE_LABELS)];
/** The verdict's reason is the sentence `Apps.check_access` (with `_check_platform` and
 *  `check_access_for_workspace`, synwevo_core/apps.py) writes for the person who was refused. The
 *  server sends it in the source language; catalogued here, `tr(reason)` words it for the reader,
 *  and a sentence a later server adds still shows as sent rather than disappearing. */
export const ACCESS_REASON_SENTENCES = [
  "该应用不存在", "该应用已停用", "账户不可用", "你不是该工作空间的成员", "你还不属于任何工作空间，请联系管理员邀请你加入",
  "该工作空间不可用", "该组织当前不可用",
];
/** The reasons that carry the application's name in 「」: matched back to their template so the
 *  name stays a parameter and the sentence translates. */
export const ACCESS_REASON_TEMPLATES = [
  "你在该工作空间使用「{app}」的资格已被撤销，请联系工作空间管理员", "你所在的工作空间尚未启用「{app}」，或你没有使用它的资格", "该工作空间尚未启用「{app}」",
];

export function accessReasonText(reason: string): string {
  for (const template of ACCESS_REASON_TEMPLATES) {
    const [head, tail] = template.split("{app}");
    if (reason.length > head.length + tail.length && reason.startsWith(head) && reason.endsWith(tail)) {
      return tr(template, { app: reason.slice(head.length, reason.length - tail.length) });
    }
  }
  return tr(reason);
}

function gateDetail(gate: string, detail: Record<string, unknown>): string {
  const s = (v: unknown) => (v === null || v === undefined ? DASH : String(v));
  switch (gate) {
    case "account": return ACCOUNT_STATUS[s(detail.status)] || s(detail.status);
    case "application": return `${s(detail.status) === "active" ? tr("正常") : tr("已停用")} · ${tr(APP_SCOPE_LABELS[s(detail.scope)] || s(detail.scope))}${detail.access_policy ? ` · ${tr(POLICY_LABELS[s(detail.access_policy)] || s(detail.access_policy))}` : ""}`;
    case "membership": return detail.role ? `${roleLabel(s(detail.role))}${detail.can_export ? ` · ${tr("可导出")}` : ""}` : tr("不是成员");
    case "workspace": return `${WORKSPACE_STATUS[s(detail.status)] || s(detail.status)} · ${WORKSPACE_KIND[s(detail.kind)] || s(detail.kind)}`;
    case "organization": return detail.reason ? tr(ORG_REASON_LABELS[s(detail.reason)] || s(detail.reason)) : detail.organization_id ? tr("组织正常且本人是其成员") : tr("不属于组织");
    case "workspace_app": return detail.enabled_at ? tr("已启用（{time}）", { time: fmtTime(Number(detail.enabled_at)) }) : tr("未启用");
    case "entitlement": return `${tr(ENTITLEMENT_LABELS[s(detail.entitlement)] || s(detail.entitlement))}${detail.access_policy ? ` · ${tr(POLICY_LABELS[s(detail.access_policy)] || s(detail.access_policy))}` : ""}`;
    default: return detail.note === "not_workspace_scoped" ? tr("该应用不按工作空间授权") : DASH;
  }
}

export function AccessExplanationBody({ d }: { d: AccessExplanation }) {
  useI18n();
  return <div className="col gap12">
    <p className={`notice ${d.verdict.allowed ? "ok" : "warn"}`} role="status">{d.verdict.allowed
      ? tr("可以打开：{app}（{workspace}）", { app: d.app.name, workspace: d.workspace.name || d.workspace.id })
      : tr("不能打开：{reason}", { reason: d.verdict.reason ? accessReasonText(d.verdict.reason) : DASH })}</p>
    <div className="list-wrap"><table className="gov" aria-label={tr("访问闸门")}>
      <thead><tr><th scope="col">{tr("闸门")}</th><th scope="col">{tr("结果")}</th><th scope="col">{tr("说明")}</th></tr></thead>
      <tbody>{d.gates.map((g) => <tr key={g.gate}>
        <th scope="row">{tr(GATE_LABELS[g.gate] || g.gate)}</th>
        <td>{g.state === "skip" ? <span className="muted">{tr(GATE_STATE_LABELS.skip)}</span> : <span className={`badge ${g.state === "pass" ? "ok" : "warn"}`}>{tr(GATE_STATE_LABELS[g.state])}</span>}</td>
        <td>{g.state === "skip" ? gateDetail("", g.detail) : gateDetail(g.gate, g.detail)}</td>
      </tr>)}</tbody></table></div>
    {!d.consistent && <p className="notice err" role="alert">{tr("逐道闸门的解释与实际检查结果不一致：以实际结果为准，请把这个页面报告给开发者。")}</p>}
    <p className="muted small">{tr("解释与本人实际打开应用走同一条检查路径，只读取，不以该用户身份行事，也不授予任何访问。")}</p>
  </div>;
}

function AccessExplanationLoader({ userId, workspaceId, appId }: { userId: string; workspaceId: string; appId: string }) {
  const path = `/api/admin/users/${encodeURIComponent(userId)}/access?workspace_id=${encodeURIComponent(workspaceId)}&app_id=${encodeURIComponent(appId)}`;
  const load = useLoad<AccessExplanation>(path, (signal) => get<AccessExplanation>(path, signal));
  if (load.loading) return <p role="status">{tr("正在解释访问…")}</p>;
  if (load.error) return <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>;
  return load.data ? <AccessExplanationBody d={load.data} /> : null;
}

export function AccessExplanationDialog({ user, target, onClose }: { user: { id: string; email: string }; target: ExplainTarget; onClose: () => void }) {
  useI18n();
  const [appId, setAppId] = useState(target.apps[0]?.id || "");
  return <Modal title={tr("有效访问：{email} · {workspace}", { email: user.email, workspace: target.workspace_name || target.workspace_id })} onClose={onClose} size="wide"
    footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
    <div className="col gap12">
      <label className="field">{tr("应用")}
        <select className="input" value={appId} onChange={(e) => setAppId(e.target.value)} aria-label={tr("要解释的应用")}>
          {target.apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select></label>
      {target.apps.length === 0 && <p className="muted small">{tr("该工作空间没有启用任何应用。")}</p>}
      {appId && <AccessExplanationLoader key={appId} userId={user.id} workspaceId={target.workspace_id} appId={appId} />}
    </div>
  </Modal>;
}

export function AccountDetailDialog({ user, held, onClose, onChanged }: {
  user: { id: string; email: string }; held?: Held; onClose: () => void; onChanged?: () => void;
}) {
  useI18n();
  const toast = useToast();
  const path = `/api/admin/users/${encodeURIComponent(user.id)}`;
  const load = useLoad<AccountDetail>(path, (signal) => get<AccountDetail>(path, signal));
  const [action, setAction] = useState<AccountAction | null>(null);
  const [explain, setExplain] = useState<ExplainTarget | null>(null);
  // The confirmation opens on top of the detail (the modal stack keeps Escape and focus to the
  // top one); the detail reloads afterwards, so the list shows what is left.
  const confirm = action && <ActionDialog title={action.label} confirmLabel={action.label} reason="required" danger
    impact={tr(ACTION_IMPACT[action.kind], { email: user.email })}
    run={(reason) => post(ACTION_PATH[action.kind](user.id, action.id), action.kind === "backoff_lift" ? { id: action.id, reason } : { reason })}
    onClose={() => setAction(null)} onDone={() => { setAction(null); load.reload(); onChanged?.(); toast(tr(ACTION_DONE[action.kind]), "ok"); }} />;
  return <><Modal title={tr("账户详情：{email}", { email: user.email })} onClose={onClose} size="wide"
    footer={<><span className="spacer" /><button type="button" className="btn" onClick={onClose}>{tr("关闭")}</button></>}>
    {load.loading && <p role="status">{tr("正在加载账户详情…")}</p>}
    {!!load.error && <div className="notice err" role="alert">{errText(load.error)} <button className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>}
    {load.data && <AccountDetailBody detail={load.data} held={held} onAct={setAction} onExplain={setExplain}
      secondFactor={<SecondFactorPanel target={resetTarget(load.data)} held={held} onChanged={load.reload} />} />}
  </Modal>{confirm}{explain && <AccessExplanationDialog user={user} target={explain} onClose={() => setExplain(null)} />}</>;
}
export const ACCOUNT_ACTION_SENTENCES = [...Object.values(ACTION_IMPACT), ...Object.values(ACTION_DONE)];
