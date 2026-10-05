/**
 * What the caller's platform permissions let the administration pages offer
 * (synwevo_core.platform_access).
 *
 * The server decides every request on its own; this only keeps the pages from offering what it
 * would refuse. An offer that always ends in a refusal is a dead end for the reader, and it writes
 * an `admin.access.denied` record for an attempt nobody meant to make, which buries the records
 * that exist to show real probing. A control the caller's role does not reach is shown disabled
 * with its reason (or not at all, where the whole section is someone else's work).
 */

export type Held = ReadonlySet<string>;
export const heldPermissions = (me?: { platform_permissions?: string[] } | null): Held =>
  new Set(me?.platform_permissions || []);

export type Tab = "agent-safety" | "model-provider" | "model-costs" | "model-risk" | "model-prices" | "overview" | "users" | "admins" | "workspaces" | "organizations" | "registration" | "identity" | "mail" | "app-runs" | "flow-runs" | "audit" | "payments" | "finance";
/** The permission each view reads with. Only the views the caller's grants open are drawn. */
export const TAB_PERMISSION: Record<Tab, string> = {
  overview: "platform.overview.view", users: "platform.users.view", admins: "platform.roles.view",
  workspaces: "platform.workspaces.view", organizations: "platform.orgs.view", registration: "platform.config.view",
  "model-provider": "platform.config.view", "model-costs": "platform.config.view", "model-risk": "platform.config.view", "model-prices": "platform.config.view", identity: "platform.config.view", mail: "platform.mail.view",
  "app-runs": "platform.apps.view", "flow-runs": "platform.apps.view", audit: "platform.audit.view",
  payments: "platform.billing.view", finance: "platform.billing.view",
  // Reports, stops and suspensions are account governance; the rule lists inside need platform.config.*.
  "agent-safety": "platform.users.view",
};
export const visibleTabs = (held: Held): Tab[] =>
  (Object.keys(TAB_PERMISSION) as Tab[]).filter((t) => held.has(TAB_PERMISSION[t]));

/** Assigning the historical data: the claim itself, and the account search its picker runs. */
export const mayClaim = (held: Held) => held.has("platform.workspaces.claim") && held.has("platform.users.view");

/** Source sentences (translated where they are shown). */
export const NOT_IN_ROLE = "你的平台角色不包含这项操作";
export const OUTRANKS_YOU = "该账户持有你没有的平台权限";

export type Offer = { disabled: boolean; hint?: string };
const offer = (hint?: string): Offer => ({ disabled: !!hint, hint });

/** A control that needs one permission and nothing else. */
export const permitted = (held: Held, permission: string): Offer =>
  offer(held.has(permission) ? undefined : NOT_IN_ROLE);

export type UserRow = { id: string; status: string; platform_roles?: string[]; outranks_caller?: boolean };
/** The account row's menu: suspend or restore, recovery mail, end sessions, platform access —
 *  each as the server would answer it (admin_routes: permission, rank, the account's state). */
export function userOffers(held: Held, user: UserRow, meId: string | undefined) {
  const outranks = !!user.outranks_caller && user.id !== meId;
  const status = !held.has("platform.users.manage") ? offer(NOT_IN_ROLE)
    : ["active", "pending"].includes(user.status)
      ? offer(user.id === meId ? "不能停用自己的账户" : user.platform_roles?.length ? "请先撤销该账户的全部平台权限" : undefined)
      : offer(user.status !== "suspended" ? "只有已停用的账户可以恢复" : outranks ? OUTRANKS_YOU : undefined);
  const support = (extra?: string) => offer(!held.has("platform.users.support") ? NOT_IN_ROLE : outranks ? OUTRANKS_YOU : extra);
  return {
    status,
    recovery: support(["active", "pending"].includes(user.status) ? undefined : "该账户当前不能接收找回邮件"),
    revoke: support(),
    access: permitted(held, "platform.roles.view"),
  };
}

/** The audit log's action scopes: a prefix and its label. `admin.platform.` and `admin.access.`
 *  are the platform-access records (grants, revocations, denied attempts). */
export const AUDIT_SCOPES: [string, string][] = [
  ["", "全部操作"], ["admin.user.", "账户管理"], ["admin.workspace.", "工作空间管理"],
  ["admin.platform.", "平台权限"], ["admin.access.", "被拒绝的管理请求"],
  ["auth.", "登录与账户安全"], ["member.", "成员管理"], ["invite.", "邀请"], ["mfa.", "两步验证"],
];
