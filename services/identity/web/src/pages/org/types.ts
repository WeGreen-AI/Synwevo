export type OrganizationRole = "owner" | "admin" | "member";
export interface Organization {
  id: string; name: string; status: "active" | "frozen" | "deleted"; role: OrganizationRole;
  invitation_policy: "admins" | "owners"; version: number; created_at: number;
  updated_at?: number; has_avatar?: boolean;
  recover_until: number | null; usage?: { workspaces: number; members: number }; quota?: null;
}
export interface OrganizationWorkspace { id: string; name: string; kind: string; status: string; organization_id: string; workspace_role: string | null }
export interface OrganizationMember { user_id: string; email: string; display_name: string | null; role: OrganizationRole; account_status: string; is_self: boolean; manageable: boolean }
export interface MemberPage { items: OrganizationMember[]; total: number; offset: number; limit: number; active_owner_count: number; single_owner: boolean; caller: { user_id: string; role: OrganizationRole } }
export const roleSource = (role: string) => role === "owner" ? "所有者" : role === "admin" ? "管理员" : role === "editor" ? "编辑者" : role === "viewer" ? "查看者" : "成员";
export const statusSource = (status: string) => status === "active" ? "正常" : status === "frozen" ? "已冻结" : status === "deleted" ? "已删除" : "不可用";
export const orgPath = (id: string) => `/api/auth/organizations/${encodeURIComponent(id)}`;
