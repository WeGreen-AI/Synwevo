import { t } from "@synwevo/ui/i18n-core";
import { issueText } from "./presentation.ts";
/** What the signed-in member may do in the current workspace, for the UI only.
 *
 *  The server is what enforces it (403 read_only_role / export_not_allowed); this decides which
 *  buttons and commands to offer, so a viewer is not handed actions that can only fail, and turns
 *  those two refusals into a sentence that says what happened and who can change it.
 *
 *  Presentation imports use the React-free shared entry point for node tests.
 */
export const WRITE_ROLES = ["owner", "admin", "editor"];

export interface MemberLike {
  workspace_id?: string | null;
  workspaces?: { id: string; role: string; can_export?: boolean }[];
}

export interface Perms { canWrite: boolean; canExport: boolean; role: string | null }

/** `disabled` = the deployment has no account system, so there are no roles to respect.
 *  An unknown workspace is not guessed at: the controls stay and the server decides. */
export function permissions(me: MemberLike | null | undefined, disabled: boolean): Perms {
  if (disabled || !me) return { canWrite: true, canExport: true, role: null };
  const list = me.workspaces || [];
  const ws = list.find((w) => w.id === me.workspace_id) || (list.length === 1 ? list[0] : undefined);
  if (!ws) return { canWrite: true, canExport: true, role: null };
  const canWrite = WRITE_ROLES.includes(ws.role);
  return { canWrite, canExport: canWrite || !!ws.can_export, role: ws.role };
}

export const READ_ONLY_TEXT = "你在当前工作空间是查看者，只能查看，不能修改。需要编辑时，请联系工作空间的所有者或管理员调整角色。";
export const NO_EXPORT_TEXT = "你在当前工作空间没有下载和导出权限。需要时，请联系工作空间的所有者或管理员开启。";

/** A clear sentence for the two permission refusals; null for anything else. */
export function permissionText(e: unknown): string | null {
  const code = (e as { code?: string } | null)?.code;
  if (code === "read_only_role") return READ_ONLY_TEXT;
  if (code === "export_not_allowed") return NO_EXPORT_TEXT;
  return null;
}

export function errorText(e: unknown): string {
  const refusal = permissionText(e);
  return refusal ? t(refusal) : issueText(typeof e === "string" ? e : e as any);
}

/** A 403 for one of these two reasons is an answer about the person, not a failed save. */
export function isPermissionError(e: unknown): boolean {
  return permissionText(e) !== null;
}
