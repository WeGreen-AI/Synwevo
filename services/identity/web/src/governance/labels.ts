import { ApiError } from "@synwevo/auth-client";
import { t, joinMessages } from "@synwevo/ui";
import * as base from "../lib/labels";
import { refusalText } from "../lib/refusals";
import "./messages";
import "./platform.messages";
import "./account-operations.messages";
export { ROLES, ROLE_RANK, canExport, isAbort } from "../lib/labels";
export type { Role } from "../lib/labels";
// Read translations at render time; imported enum labels must not freeze the initial locale.
const translated = (values: Record<string, string>) => new Proxy(values, {
  get(target, key: string) { return target[key] === undefined ? undefined : t(target[key]); },
});
export const ACCOUNT_STATUS = translated(base.ACCOUNT_STATUS);
export const WORKSPACE_STATUS = translated(base.WORKSPACE_STATUS);
export const WORKSPACE_KIND = translated(base.WORKSPACE_KIND);
export const INVITE_STATE = translated({ ...base.INVITE_STATE, declined: "已拒绝" });
export const MAIL_STATE = translated(base.MAIL_STATE);
export const ROLE_GOVERNS = translated(base.ROLE_GOVERNS);
export const roleLabel = (role: string | null | undefined) => t(base.roleLabel(role));
export function entitlementLabel(ent: string, policy: string | null | undefined) {
  const result = base.entitlementLabel(ent, policy);
  return { ...result, text: t(result.text) };
}
export function fmtDuration(seconds: number) {
  if (seconds < 90) return t("{n} 秒", { n: Math.max(1, Math.ceil(seconds)) });
  if (seconds < 5400) return t("{n} 分钟", { n: Math.ceil(seconds / 60) });
  return t("{n} 小时", { n: Math.ceil(seconds / 3600) });
}
const errors: Record<string, string> = {
  admin_preview_invalid: "操作预览无效，请重新预览影响。",
  admin_preview_expired: "操作预览已过期，请重新预览影响。",
  admin_preview_stale: "账户或访问权限已变化，请重新预览影响。",
  admin_confirmation_required: "请先预览操作影响，再确认执行。",
  account_state_conflict: "账户状态已变化，请刷新后重试。",
  no_session: "找不到该账户的这个在线会话，可能已经结束。",
  no_external_identity: "找不到该账户的这个外部身份，可能已经解除。",
  no_passkey: "找不到该账户的这个安全密钥，可能已经移除。",
  last_sign_in_method: "这是该账户唯一的登录方式，不能解除关联；需要停止访问请停用账户。",
  last_factor: "这是该账户唯一的登录方式，不能移除；需要停止访问请停用账户。",
  application_identity: "请通过应用身份管理操作该账户。",
  idempotency_conflict: "操作编号已用于其他请求，请重新预览影响。",
  journal_unavailable: "安全记录暂不可用，操作未提交，请稍后重试。",
  mail_config_invalid: "邮件服务配置无效，请联系部署管理员。",
  role_too_high: "不能管理同级或更高角色的成员",
  self_role: "不能修改自己的权限，请使用退出工作空间或移交所有权",
  self_entitlement: "不能修改自己的权限，请使用退出工作空间或移交所有权",
  no_workspace: "找不到这个工作空间", not_owner: "当前角色无权执行此操作。",
  invite_expired: "邀请已过期，请让邀请人重新发送。",
  invite_declined: "邀请已被拒绝，请让邀请人重新发送。",
  missing_reason: "请写明理由（8 至 500 个字）", bad_credentials: "口令不正确，请重试。",
  unauthorized: "登录已失效，请重新登录。", reauth_required: "这一步需要重新验证身份。",
  forbidden: "当前角色无权执行此操作。", not_found: "页面不存在", no_organization: "找不到该组织或你不是其成员。",
  bad_target: "目标账户不存在或不可用。", no_user: "找不到该账户。", last_super_admin: "必须保留至少一位可用的最高管理员。",
  target_outranks_actor: "该账户持有你没有的平台权限", not_suspended: "只有已停用的账户可以恢复",
  platform_permission_denied: "你的平台角色不包含这项操作",
  last_owner: "必须保留至少一位正常的所有者。", sole_owner: "请先移交所有权，再退出工作空间。",
  demote_first: "请先撤销该账户的全部平台权限", self_suspend: "不能停用自己的账户",
  policy_changed: "策略已更改，请刷新后重试。",
  not_pending_delete: "该账户没有待处理的注销申请。",
  bad_throttle: "请选择要解除的限流记录。",
  bad_access_query: "请指定要解释的工作空间与应用。",
  no_app: "找不到该应用。",
  no_throttle: "找不到该限流记录，可能已经过期或被解除。",
  finalize_failed: "到期删除未能完成，已记录失败；请查看服务日志后重试。",
  no_second_factor_admin: "至少需要一名可用的最高管理员已登记安全密钥或动态验证码，才能要求管理员使用第二因素。",
  bad_second_factor_mode: "请选择有效的第二因素策略。", mail_rate_limited: "发送过于频繁，请稍后重试。",
  mail_not_configured: "邮件服务尚未配置。", public_url_missing: "邮件链接地址尚未配置，请联系部署管理员。",
  smtp_not_configured: "邮件服务尚未配置。", smtp_config_invalid: "邮件服务配置无效，请联系部署管理员。",
  mail_not_retryable: "该邮件不能安全重试。", no_mail: "找不到该邮件。", already_verified: "该邮箱已经验证。",
  organization_state_conflict: "组织当前状态不允许此操作，请刷新。",
  organization_recovery_expired: "组织的七天恢复期限已过。",
  workspace_frozen: "工作空间已冻结，不能增加权限。", already_member: "该账户已经是工作空间成员。",
  invitation_rate_limited: "邀请过于频繁，请稍后重试。", invite_rate_limited: "邀请过于频繁，请稍后重试。",
  already_invited: "已有待接受的邀请。", invite_not_pending: "邀请已接受或已撤销，请刷新。",
  self_change: "不能修改自己的权限，请使用退出工作空间或移交所有权",
  cannot_manage: "不能管理同级或更高角色的成员", not_claimable: "该工作空间已被认领或不能认领，请刷新。",
};
/** One item of a batch the server refused: its sentence when catalogued, else this page's wording
 *  for its code (lib/refusals.ts) — a mixed batch answers 200, so the item carries no status. */
export const resultText = (code: string, message: string, detail?: unknown) => refusalText({ code, originalMessage: message, detail }, errors);
export function errText(error: unknown): string {
  if (!(error instanceof ApiError)) return t("无法连接服务，请检查连接后重试。");
  const parts = [error.upstream ? t("网关未能完成请求，请稍后重试。") : refusalText(error, errors)];
  if (error.retryAfter) parts.push(t("请在 {duration} 后重试。", { duration: fmtDuration(error.retryAfter) }));
  if (error.requestId) parts.push(t("请求编号：{id}", { id: error.requestId }));
  return joinMessages(parts, " ");
}
