export type OrganizationRole = "owner" | "admin" | "member";
export interface Organization {
  id: string; name: string; status: "active" | "frozen" | "deleted"; role: OrganizationRole;
  invitation_policy: "admins" | "owners"; version: number; created_at: number;
  updated_at?: number; has_avatar?: boolean;
  recover_until: number | null; usage?: { workspaces: number; members: number }; quota?: null;
}
export interface OrganizationWorkspace { id: string; name: string; kind: string; status: string; organization_id: string; workspace_role: string | null }
export interface CustomRole { id: string; name: string; description: string | null; permissions: string[]; version: number; assigned_count?: number | null }
export interface PermissionInfo { id: string; group: string; owner_only: boolean; label: string; description: string }
export interface EffectivePermissions { base_role: OrganizationRole | null; roles: CustomRole[]; permissions: string[]; sources: Record<string, string[]> }
export interface OrganizationMember { custom_roles?: { id: string; name: string }[]; user_id: string; email: string; display_name: string | null; role: OrganizationRole; account_status: string; is_self: boolean; manageable: boolean }
export interface MemberPage { items: OrganizationMember[]; total: number; offset: number; limit: number; active_owner_count: number; single_owner: boolean; caller: { user_id: string; role: OrganizationRole } }
export const roleSource = (role: string) => role === "owner" ? "所有者" : role === "admin" ? "管理员" : role === "editor" ? "编辑者" : role === "viewer" ? "查看者" : "成员";
export const statusSource = (status: string) => status === "active" ? "正常" : status === "frozen" ? "已冻结" : status === "deleted" ? "已删除" : "不可用";
export const orgPath = (id: string) => `/api/auth/organizations/${encodeURIComponent(id)}`;

export type ImportAction = "invite" | "update" | "unchanged" | "skip" | "blocked";
export interface ImportTable { name: string; source: Record<string, unknown>; truncated: boolean; rows: number; preview: string[][] }
export interface ImportMapping { mode?: "table" | "lines"; table?: number | null; header_row?: boolean; columns?: Partial<Record<"email" | "name" | "workspace_role" | "org_roles", number>>;
  defaults?: { workspace_role?: string; org_role_ids?: string[] }; workspace_role_names?: Record<string, string>; org_role_names?: Record<string, string> }
export interface MemberImport { id: string; organization_id: string; workspace_id: string | null; filename: string; size: number; format: string | null; family: string | null;
  status: "extracted" | "planned" | "running" | "finished" | "partial" | "failed" | "cancelled"; mapping: ImportMapping | null; error: string | null; version: number;
  cancel_requested: boolean; created_at: number; finished_at: number | null; scan: { status: string } | null;
  summary: { rows: number; counts: Record<ImportAction, number>; reasons: Record<string, number>; unmapped_workspace_roles: string[]; unmapped_org_roles: string[];
    workspace?: { id: string; name: string }; over_limit?: number; outcome?: { done: number; failed: number; skipped: number; pending: number; invited: number; updated: number } } | null;
  extraction: { engine: string; format: string; family: string; status: string; error: string | null; notes: string[]; pages: number | null; needs_engine?: boolean;
    ocr: { available: boolean; applied: boolean; engine: string | null }; tables: ImportTable[]; lines: number; line_preview: string[] } }
export interface MemberImportRow { id: string; seq: number; source: Record<string, unknown>; fields: { email: string; name: string; workspace_role: string; org_roles: string[]; existing?: Record<string, unknown> };
  action: ImportAction; reason: string | null; diff: { workspace_role?: { from: string; to: string }; org_roles?: { add: string[] } } | null;
  status: "planned" | "pending" | "running" | "done" | "failed" | "skipped"; result: Record<string, any> | null; invitation_id: string | null }
export interface ImportCapabilities { ingest: { engine: string; formats: string[]; ocr: boolean; converter?: boolean; scanner?: { status: string } }; limits: { rows: number; file_bytes: number }; workspace_roles: string[] }
