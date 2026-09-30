import "../governance/settings.messages";
import "../governance/model-prices.messages";
import { TAB_PERMISSION, type Held, type Tab } from "../governance/access";
export type AdminPage = Tab | "api-platform";
export type AdminGroup = { label: string; items: { id: AdminPage; label: string }[] };
export const ADMIN_GROUPS: AdminGroup[] = [
  { label: "概览与待处理", items: [{ id: "overview", label: "概览" }] },
  { label: "账户与访问", items: [
    { id: "users", label: "账户" }, { id: "admins", label: "平台管理员" },
    { id: "organizations", label: "组织" }, { id: "workspaces", label: "工作空间" },
    { id: "identity", label: "登录方式" },
  ] },
  { label: "应用与业务", items: [{ id: "mail", label: "邮件投递" }, { id: "api-platform", label: "API 管理" }] },
  { label: "配置与治理", items: [{ id: "registration", label: "平台配置" }, { id: "model-prices", label: "模型价格" }, { id: "audit", label: "审计" }] },
];
export function adminGroups(held: Held): AdminGroup[] {
  return ADMIN_GROUPS.map(group => ({ ...group, items: group.items.filter(item => held.has(
    item.id === "api-platform" ? "api_platform.manage" : TAB_PERMISSION[item.id],
  )) })).filter(group => group.items.length > 0);
}
export function resolveAdminPage(pathname: string, asked: string | null, held: Held): AdminPage | null {
  const available = adminGroups(held).flatMap(group => group.items);
  // Direct API-console links must never fall through to a different view inside the console.
  if (pathname.replace(/\/+$/, "") === "/admin/api-platform") return held.has("api_platform.manage") ? "api-platform" : null;
  return available.find(item => item.id === asked && item.id !== "api-platform")?.id
    ?? available.find(item => item.id !== "api-platform")?.id ?? available[0]?.id ?? null;
}
/** Keep language and the real return target, not a different list's query or selection. */
export function adminHref(page: AdminPage, search = ""): string {
  const before = new URLSearchParams(search), params = new URLSearchParams();
  for (const key of ["lang", "from"]) { const value = before.get(key); if (value) params.set(key, value); }
  if (page !== "api-platform") params.set("tab", page);
  const query = params.toString();
  return (page === "api-platform" ? "/admin/api-platform" : "/admin") + (query ? `?${query}` : "");
}
