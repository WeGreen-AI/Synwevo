/** Shapes of the workspace governance API (services/identity/synwevo_identity/members.py). */

export interface Member {
  user_id: string;
  role: string;
  can_export: boolean;
  joined_at: number;
  added_by: string | null;
  email: string;
  display_name: string | null;
  account_status: string;
  entitlement: "active" | "revoked" | "none";
  entitlement_granted_at: number | null;
  entitlement_revoked_at: number | null;
  is_self: boolean;
  manageable: boolean;
}

export interface WorkspaceInfo { id: string; name: string; kind: string; status: string }
export interface AppInfo { id: string; name: string; status: string; access_policy: string }

export interface MemberPage {
  items: Member[];
  total: number;
  offset: number;
  limit: number;
  next_offset: number | null;
  owner_count: number;
  active_owner_count: number;
  single_owner: boolean;
  workspace: WorkspaceInfo;
  caller: { user_id: string; role: string };
  app: AppInfo | null;
}

export interface Invitation {
  id: string;
  email_key: string;
  role: string;
  can_export: boolean;
  invited_by: string | null;
  invited_by_email: string | null;
  created_at: number;
  expires_at: number;
  accepted_at: number | null;
  revoked_at: number | null;
  state: "pending" | "expired" | "revoked" | "accepted";
  mail: string;
  mail_error: string | null;
}

export interface InvitationList {
  items: Invitation[];
  groups: { pending: number; expired: number; revoked: number; accepted: number; mail_failed: number };
}

export interface Removed {
  role: string;
  can_export: boolean;
  entitlements_revoked: number;
  tokens_revoked: number;
  sessions_moved: number;
  invitations_cancelled: number;
}

export const memberName = (m: Pick<Member, "display_name" | "email">) => m.display_name || m.email;
