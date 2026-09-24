/**
 * The words the account shell uses for the server's enums, in one place.
 *
 * Every page used to keep its own map, and the maps drifted: the session list knew three of the
 * four sign-in methods the server records, so a recovery-code sign-in showed up as the raw enum
 * `password+recovery` in an otherwise Chinese page.
 */
import { ApiError } from "@synwevo/auth-client";

export type Role = "viewer" | "editor" | "admin" | "owner";
export const ROLES: Role[] = ["viewer", "editor", "admin", "owner"];
export const ROLE_RANK: Record<Role, number> = { viewer: 0, editor: 1, admin: 2, owner: 3 };

export const ROLE_LABEL: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑",
  viewer: "只读",
};

/** Export is inherent to writing roles; can_export only adds it for viewers. */
export const canExport = (role: string, flag: boolean) => flag || ["editor", "admin", "owner"].includes(role);

/** What governing the workspace means for each role — taken from the rules the member routes
 *  enforce (services/identity/synwevo_identity/members.py), not from how the roles sound. */
export const ROLE_GOVERNS: Record<string, string> = {
  owner: "管理所有成员（包括其他所有者）、邀请任意角色、移交所有权",
  admin: "邀请成员，管理编辑与只读成员，可授予最高到管理员的角色",
  editor: "在空间内编辑，不能管理成员",
  viewer: "只能查看，不能管理成员；导出需要单独开启",
};

export const roleLabel = (r: string | null | undefined) => (r ? ROLE_LABEL[r] || r : "—");

export const ACCOUNT_STATUS: Record<string, string> = {
  active: "正常",
  suspended: "已停用",
  pending: "待激活",
  pending_delete: "待删除",
  deleted: "已删除",
};

export const WORKSPACE_STATUS: Record<string, string> = {
  active: "正常",
  frozen: "已冻结",
  pending_claim: "待认领",
};

export const WORKSPACE_KIND: Record<string, string> = {
  personal: "个人",
  org: "组织",
  team: "团队",
  legacy: "历史数据",
};

/** How a session was authenticated, as the server recorded it at sign-in. */
export const AMR_LABEL: Record<string, string> = {
  password: "口令",
  "password+totp": "口令 + 动态验证码",
  "password+recovery": "口令 + 恢复码",
  passkey_uv: "安全密钥（含设备验证）",
  passkey: "安全密钥",
};

export const INVITE_STATE: Record<string, string> = {
  pending: "待接受",
  expired: "已过期",
  revoked: "已撤销",
  accepted: "已接受",
};

export const MAIL_STATE: Record<string, string> = {
  queued: "排队发送中",
  sent: "已寄出",
  failed: "发送失败",
  unknown: "无发送记录",
};

/** A member's right to use one application, read together with the application's policy:
 *  under `members`, "no row" means allowed; under `explicit` it means not granted. */
export function entitlementLabel(ent: string, policy: string | null | undefined): { text: string; tone: "ok" | "warn" | "" } {
  if (ent === "active") return { text: "已授予资格", tone: "ok" };
  if (ent === "revoked") return { text: "已撤销", tone: "warn" };
  return policy === "members" ? { text: "随成员资格", tone: "ok" } : { text: "未授予", tone: "" };
}

/** "3 天后" / "5 小时前": when an expiry is close, the absolute time alone hides it. */
export function relativeTime(ts: number | null | undefined, now = Date.now() / 1000): string {
  if (!ts) return "";
  const d = ts - now;
  const a = Math.abs(d);
  const unit = a < 3600 ? `${Math.max(1, Math.round(a / 60))} 分钟`
    : a < 86400 ? `${Math.round(a / 3600)} 小时`
      : `${Math.round(a / 86400)} 天`;
  return d >= 0 ? `${unit}后` : `${unit}前`;
}

export function fmtDuration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} 秒`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} 分钟`;
  return `${Math.round(seconds / 3600)} 小时`;
}

/** One sentence for an error, with what the server said about when to retry and which request
 *  it was. Used by every notice on these pages so none of them drops that information. */
export function errText(e: unknown): string {
  if (e instanceof ApiError) {
    let s = e.message;
    if (e.retryAfter) s += `（请在 ${fmtDuration(e.retryAfter)}后再试）`;
    return s;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError"
  || (e instanceof Error && e.name === "AbortError");
