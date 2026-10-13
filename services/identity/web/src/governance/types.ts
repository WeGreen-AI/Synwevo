import type { Invitation as ExistingInvitation, InvitationList as ExistingList } from "../pages/members/types";
export type { Member, MemberPage, WorkspaceInfo, AppInfo, Removed } from "../pages/members/types";
export { memberName } from "../pages/members/types";
export type Invitation = Omit<ExistingInvitation, "state"> & { state: ExistingInvitation["state"] | "declined";
  /** A47: "platform" when platform administration revoked it (the invite.revoke row with by: platform). */
  revoked_by?: "platform" | null };
export type InvitationList = Omit<ExistingList, "items" | "groups"> & {
  items: Invitation[];
  groups: ExistingList["groups"] & { declined?: number };
};
