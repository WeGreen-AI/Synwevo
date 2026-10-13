import { Fragment, useEffect, useId, useRef, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { Modal, t as tr, fmtTime, formatNumber, hasMessage, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { WORKSPACE_KIND, WORKSPACE_STATUS, errText } from "./labels";
import "./messages";
import "./workspace-lifecycle.messages";

/**
 * A44: the platform-scope workspace lifecycle — rename, delete (soft, with a seven-day recovery
 * window) and recover — from the administration page's workspace list, for a platform
 * administrator holding `platform.workspaces.manage` (synwevo_identity.admin_workspaces).
 *
 * The organization lifecycle's confirmed-operation protocol (OrganizationLifecycleDialog.tsx): the
 * server first answers a signed preview bound to this actor, action, workspace (and the new name)
 * and the workspace's current version, with counts and a list of note codes worded here; the
 * administrator reads it, writes a reason and re-authenticates inside `ActionDialog`; the action
 * is posted with the preview token and one idempotency key per (preview, reason, name), so a retry
 * after a lost answer replays the same request. A preview the server no longer accepts (expired,
 * stale, invalid, a key already used, a state that changed) asks for a new preview, which also
 * starts a new key. The answer is shown as a result: what was withdrawn and what a later backup
 * restore would undo.
 *
 * Which action a row offers follows the server's rules (`workspaceLifecycleOffers`): a personal
 * workspace can only be renamed (deleting it would make sign-in create a second one), the
 * historical data (`legacy`, `pending_claim`) belongs to the claim flow, and a deleted workspace
 * can only be recovered, until its window closes.
 */
export type WorkspaceLifecycleAction = "rename" | "delete" | "recover";
export type WorkspaceOrganization = { id: string; name: string; status: string };
/** A workspace as the administration list and the lifecycle routes answer it: metadata and
 *  counts, never content. The lifecycle fields are absent from an older server. */
export type WorkspaceRow = {
  id: string; name: string; kind: string; status: string; members: number;
  organization_id?: string | null; organization?: WorkspaceOrganization | null; created_at?: number;
  deleted_at?: number | null; recover_until?: number | null; previous_status?: string | null;
};
/** The preview's counts; null where the deployment has no such table (an identity-only host). */
export type WorkspaceImpact = {
  members: number; usable_owners: number; sessions: number; tokens: number; invitations: number; approvals: number;
  api_keys: number | null; service_accounts: number | null; running_jobs: number | null; webhooks: number | null;
  form_links: number | null; projects: number | null; org_templates: number | null;
};
/** POST /api/admin/workspaces/{id}/lifecycle/preview */
export type WorkspacePlan = {
  action: WorkspaceLifecycleAction; transition: { from: string; to: string }; target: WorkspaceRow;
  /** the normalised new name for a rename, else null */
  name: string | null; impact: WorkspaceImpact; notes: string[]; risk: "high" | "medium";
  expires_at: number; preview_token: string;
};
/** What a deletion took away in its transaction (null for rename and recover). */
export type WorkspaceWithdrawn = { sessions: number; tokens: number; codes: number; invitations: number; approvals: number };
/** POST /api/admin/workspaces/{id}/lifecycle/{action} */
export type WorkspaceLifecycleResult = {
  ok: true; operation_id: string; state: "succeeded"; action: WorkspaceLifecycleAction; workspace: WorkspaceRow;
  withdrawn: WorkspaceWithdrawn | null; restore_note: string | null;
};

export const WORKSPACE_ACTION_LABELS: Record<WorkspaceLifecycleAction, string> = {
  rename: "重命名工作空间", delete: "删除工作空间", recover: "恢复工作空间",
};
/** What each action says before anything is counted. */
export const WORKSPACE_ACTION_WARNINGS: Record<WorkspaceLifecycleAction, string> = {
  rename: "重命名只改变名称：成员、内容和访问都不变。",
  // Nothing is ever purged (A44): after the window the workspace stays deleted with its data stored.
  delete: "删除不会清除任何数据，之后也不会自动清除：工作空间进入七天恢复期，期内可以恢复到删除前的状态；期满后它保持已删除状态，数据仍然保存，但不能再从控制台恢复。删除后任何人都不能打开它（包括平台管理员），它也不再出现在工作空间切换器和列表中。有些撤销是永久的，恢复也不会带回来，请看下方的影响。",
  // Said before anything is counted, so it promises no access: the state it returns to, the
  // organization and each member's own account decide that (the counts below say which applies).
  recover: "恢复让工作空间回到删除前的状态，成员关系保留；成员能否重新访问取决于恢复后的状态、所属组织和各自的账户。删除时撤销或停止的东西并不都会回来，请看下方的影响。",
};
export const WORKSPACE_PLATFORM_NOTE = "此操作由平台执行并写入审计记录；不会给任何平台管理员带来成员身份、资格或业务文档访问。";
export const WORKSPACE_DONE_TITLES: Record<WorkspaceLifecycleAction, string> = {
  rename: "工作空间已重命名", delete: "工作空间已删除", recover: "工作空间已恢复",
};
export const RECOVERY_EXPIRED = "恢复期限已过";
/** A deleted row whose window has closed: nothing is purged, it only cannot be recovered any more. */
export const RECOVERY_EXPIRED_KEPT = "恢复期限已过：数据仍然保存，但不能再从控制台恢复。";
export const FROZEN_RECOVERY_NOTE = "恢复后工作空间仍为冻结状态，需要另行解冻才能恢复访问。";

// ------------------------------------------------------------------------------ the row's offers
export const workspaceRecoverable = (ws: { recover_until?: number | null }, now = Date.now()) =>
  !!ws.recover_until && ws.recover_until * 1000 > now;
export type LifecycleOffer = { action: WorkspaceLifecycleAction; disabled: boolean; hint?: string };
/** The lifecycle actions a row offers, as the server would answer them: none for the historical
 *  data; rename only for a personal workspace; rename and delete for an organization workspace
 *  that is active or frozen; recover for a deleted one, disabled once its window has closed. */
export function workspaceLifecycleOffers(ws: Pick<WorkspaceRow, "kind" | "status" | "recover_until">, now = Date.now()): LifecycleOffer[] {
  if (ws.kind === "legacy" || ws.status === "pending_claim") return [];
  if (ws.status === "deleted") {
    if (ws.kind === "personal") return [];
    const open = workspaceRecoverable(ws, now);
    return [{ action: "recover", disabled: !open, hint: open ? undefined : RECOVERY_EXPIRED }];
  }
  if (ws.status !== "active" && ws.status !== "frozen") return [];
  return ws.kind === "personal" ? [{ action: "rename", disabled: false }]
    : [{ action: "rename", disabled: false }, { action: "delete", disabled: false }];
}
export type RowAction = "freeze" | "unfreeze" | WorkspaceLifecycleAction;
export type RowOffer = { action: RowAction; disabled: boolean; hint?: string; danger: boolean };
/** Everything the row's menu holds for a caller with `platform.workspaces.manage` (nothing
 *  without it), in this order: rename, freeze or unfreeze (A43, any kind that is active or
 *  frozen, as before), delete; or recover for a deleted workspace. */
export function workspaceRowActions(ws: Pick<WorkspaceRow, "kind" | "status" | "recover_until">, manage: boolean, now = Date.now()): RowOffer[] {
  if (!manage) return [];
  const offers = workspaceLifecycleOffers(ws, now).map((o): RowOffer => ({ ...o, danger: o.action === "delete" }));
  const freeze: RowOffer[] = ws.status === "active" ? [{ action: "freeze", disabled: false, danger: true }]
    : ws.status === "frozen" ? [{ action: "unfreeze", disabled: false, danger: false }] : [];
  return [...offers.filter((o) => o.action === "rename"), ...freeze, ...offers.filter((o) => o.action !== "rename")];
}

// ------------------------------------------------------------------------------ the list after an act
/** The list re-reads after an act; a row that leaves it (a state the filter no longer shows, a
 *  deletion) takes the menu that opened the dialog with it, and focus drops to the page's start.
 *  The outcome notice takes it then (A44-WEB-6) — once the list has re-read past the page the act
 *  was taken on (`stale`), or failed to: the row is gone only then. */
export type NoticeFocusWait = { stale: unknown };
export function noticeFocusDue(wait: NoticeFocusWait | null, page: { loading: boolean; data: unknown; err: unknown }): boolean {
  return !!wait && !page.loading && (page.data !== wait.stale || !!page.err);
}
/** Whether nothing holds focus: the focused element was removed, or nothing was focused. */
export const focusLost = (doc: { activeElement: unknown; body: unknown }) => !doc.activeElement || doc.activeElement === doc.body;

// ------------------------------------------------------------------------------ the new name
export const NAME_MAX = 100;
const CONTROL = /\p{C}/u;
/** The local check of a new name, by the server's rule (organizations._name: 1..100 characters
 *  after trimming, no control characters) plus "unchanged": the source sentence and its values. */
export function nameProblem(value: string, current: string): { source: string; params?: Record<string, number> } | null {
  const name = value.trim();
  if (!name) return { source: "请填写新名称。" };
  const length = Array.from(name).length;
  if (length > NAME_MAX) return { source: "名称最多 {max} 个字符（当前 {count} 个）。", params: { max: NAME_MAX, count: length } };
  if (CONTROL.test(name)) return { source: "名称不能包含换行、制表符等控制字符。" };
  if (name === current) return { source: "新名称与当前名称相同，无需重命名。" };
  return null;
}

// ------------------------------------------------------------------------------ the counts
export type ImpactKey = keyof WorkspaceImpact;
/** The preview's counts: the key the server sends, its label, what the count means for a
 *  deletion, and what it means for a recovery. A rename shows only the members. */
export const IMPACT_ROWS: ReadonlyArray<readonly [ImpactKey, string, string, string]> = [
  ["members", "成员", "成员关系保留；成员从下一次请求起不能再打开这个工作空间。", "成员关系保留；账户和组织成员身份仍然有效的成员恢复后可以重新访问。"],
  ["usable_owners", "可用的拥有者", "拥有者的角色保留；恢复后由他们继续管理。", "恢复后由这些拥有者继续管理。"],
  ["sessions", "会话", "这些会话会移出这个工作空间，不会退出登录；恢复后成员需要重新切换进来。", "现有会话不受影响；删除时移出的会话不会自动回来，成员需要重新切换进来。"],
  ["tokens", "应用令牌", "这些应用令牌立即撤销；恢复后不会恢复，应用需要重新登录。", "现有应用令牌不受影响；之前撤销的令牌不会恢复。"],
  ["invitations", "待接受的邀请", "这些邀请立即撤回；恢复后不会恢复，需要重新邀请。", "现有邀请不受影响；之前撤回的邀请不会恢复。"],
  // A credential issued through an API-platform approval ends with that approval, for good: it is
  // counted here, under the approvals. The two rows after it count only the credentials no
  // approval binds, which a deletion pauses and a recovery gives back (A44-WEB-1).
  ["approvals", "API 凭据批准", "这些批准被永久撤销：通过它们签发的 API 访问密钥和服务账户从此停止工作，恢复后也不会重新生效。",
    "现有批准不受影响；删除时撤销的批准不会恢复，通过它们签发的 API 访问密钥和服务账户仍然无效，需要重新申请批准并签发。"],
  ["api_keys", "不经批准签发的 API 访问密钥", "这些密钥暂停使用，不会删除；恢复后重新可用。经 API 凭据批准签发的密钥不在此列，它们随批准永久停止。",
    "这些密钥随恢复重新可用；经 API 凭据批准签发的密钥不在此列，仍然无效。"],
  ["service_accounts", "不经批准签发的服务账户", "这些服务账户暂停使用，不会删除；恢复后重新可用。经 API 凭据批准签发的服务账户不在此列，它们随批准永久停止。",
    "这些服务账户随恢复重新可用；经 API 凭据批准签发的服务账户不在此列，仍然无效。"],
  // A recovery's counts are what is still queued or still active: they carry on only while their
  // submitter's or creator's own authority holds, and what the deletion stopped stays stopped (A44-WEB-2).
  ["running_jobs", "排队或运行中的任务", "这些任务会在下一次检查时永久停止；已生成的结果保留，恢复后需要有人重新运行。",
    "这些任务尚未停止：恢复后仍按提交者当前的授权核对，授权已在删除时撤销的任务会停止。删除期间已停止的任务保持停止，需要有人重新运行。"],
  ["webhooks", "Webhook 订阅", "Docs 的订阅在删除期间遇到下一次投递时被停用，业务应用的订阅随应用授权撤销而停用；已停用的订阅恢复后仍保持停用，需要有人重新启用。",
    "这些订阅尚未被停用：恢复后每次投递仍按创建者当前的授权核对，授权已在删除时撤销的会被停用。删除期间已被停用的订阅保持停用，需要有人重新启用。"],
  ["form_links", "表单公开链接", "这些链接暂时打不开；恢复后未过期的链接重新可用。", "这些链接随恢复重新可用（已过期的除外）。"],
  ["projects", "项目", "项目原样保留，不会删除，也不会提供给管理员。", "项目原样保留。"],
  ["org_templates", "发布到组织的模板", "这些模板属于组织，保留不变。", "这些模板属于组织，不受影响。"],
];
export const RENAME_MEMBERS = "这些成员会在工作空间切换器、成员列表和邀请邮件中看到新名称。";
export const NOT_COUNTED = "此部署没有这些数据，不统计：{names}";
export const NO_OWNER_NOW = "当前没有可用的拥有者；恢复后仍需要为它指定拥有者。";
export const NO_OWNER_AFTER_RECOVERY = "恢复后没有可用的拥有者：请在本页「没有有效拥有者的工作空间」中为它指定拥有者。";
export const MEMBERS_STILL_FROZEN = "恢复后仍为冻结状态：成员在解冻前仍无法访问；成员关系保留。";
export const MEMBERS_BLOCKED_BY_ORGANIZATION = "所属组织当前不可用：恢复后成员仍无法访问，直到组织恢复正常；成员关系保留。";
export const MEMBERS_WITHOUT_OWNER = "成员关系保留；恢复后没有可用的拥有者：成员能否访问取决于各自的账户和组织成员身份，也没有人能管理成员和设置。";

/** Whether the workspace's organization keeps its members out (A44-WEB-4): it belongs to one that
 *  is not active (frozen, deleted) or no longer exists. Read from the row itself, so the result of
 *  a recovery — which carries no notes — says it too. */
export const organizationBlocks = (ws: Pick<WorkspaceRow, "organization_id" | "organization">) =>
  !!ws.organization_id && (!ws.organization || ws.organization.status !== "active");

const counted = (value: number | null | undefined): value is number => typeof value === "number";
/** The rows a preview shows: members alone for a rename; for a deletion or a recovery every count
 *  the deployment takes (a null count is a table this deployment does not have, named once). */
export function impactRows(action: WorkspaceLifecycleAction, impact: Partial<WorkspaceImpact>) {
  if (action === "rename") return IMPACT_ROWS.filter(([key]) => key === "members");
  return IMPACT_ROWS.filter(([key]) => counted(impact[key]));
}
export const uncountedRows = (action: WorkspaceLifecycleAction, impact: Partial<WorkspaceImpact>) =>
  action === "rename" ? [] : IMPACT_ROWS.filter(([key]) => !counted(impact[key]));
/** What one count means for this action, judged by where the workspace ends up: a recovery into a
 *  frozen state or an organization that is not active gives members nothing back yet, and without
 *  a usable owner the members' access is not promised either — the ownership recovery section is. */
export function impactMeaning(action: WorkspaceLifecycleAction, key: ImpactKey,
                              plan: Pick<WorkspacePlan, "impact" | "notes" | "transition"> & { target?: Pick<WorkspaceRow, "organization_id" | "organization"> }): string {
  if (action === "rename") return RENAME_MEMBERS;
  const row = IMPACT_ROWS.find(([k]) => k === key)!;
  if (key === "usable_owners" && plan.impact.usable_owners === 0) return action === "recover" ? NO_OWNER_AFTER_RECOVERY : NO_OWNER_NOW;
  if (action === "recover" && key === "members") {
    if (plan.notes.includes("recover_blocked_by_organization") || (plan.target && organizationBlocks(plan.target))) return MEMBERS_BLOCKED_BY_ORGANIZATION;
    if (plan.transition.to === "frozen") return MEMBERS_STILL_FROZEN;
    if (plan.impact.usable_owners === 0) return MEMBERS_WITHOUT_OWNER;
  }
  return action === "delete" ? row[2] : row[3];
}

// ------------------------------------------------------------------------------ the notes
/** The notes the server attaches by code, worded for the action they come with: what is kept,
 *  what ends now, what never comes back, what comes back on recovery, the window, and what a
 *  later backup restore undoes (rename and recovery are not in the security journal). Only the
 *  codes the server sends are shown: it sends a recovery's codes only when they are true for the
 *  workspace (approvals the deletion revoked, jobs it halted, subscriptions it disabled, the
 *  paused credentials and Forms links, the organization's templates). */
export const NOTE_TEXT: Record<string, Partial<Record<WorkspaceLifecycleAction, string>>> = {
  data_kept: {
    delete: "不会删除任何数据，七天后也不会自动清除：项目、模板和结果原样保留，只是在删除期间打不开；平台管理员也看不到其内容。",
    recover: "删除期间数据原样保留；成员恢复访问后即可照常使用。",
    rename: "重命名不改变任何数据、成员或访问。" },
  sessions_moved: {
    delete: "成员当前在这个工作空间的登录会话会移出它，不会退出登录；恢复后成员需要重新切换进来。" },
  tokens_revoked: {
    delete: "发给这个工作空间的应用令牌和授权码立即作废；恢复后不会恢复，连接的应用需要重新登录或重新授权。" },
  invitations_revoked: {
    delete: "尚未接受的邀请立即撤回；恢复后不会恢复，需要重新邀请。" },
  approvals_revoked_for_good: {
    delete: "API 凭据批准（待处理的和已批准的）被永久撤销：通过它们签发的 API 访问密钥和服务账户从此停止工作，恢复后也不会重新生效，需要重新申请批准并签发新的凭据。",
    recover: "删除时撤销的 API 凭据批准不会恢复：通过它们签发的 API 访问密钥和服务账户仍然无效，需要重新申请批准并签发新的凭据。" },
  jobs_halt_for_good: {
    delete: "排队或运行中的 Docs 任务会在下一次检查时永久停止；已生成的结果保留，恢复后不会自动继续，需要有人重新运行。",
    recover: "删除期间停止的 Docs 任务保持停止，提交者授权已随删除结束的排队或运行中任务也会在下一次检查时停止；它们不会随恢复继续，已生成的结果保留，需要有人重新运行。" },
  webhooks_disabled_for_good: {
    delete: "Docs 的 Webhook 订阅会在下一次投递时被停用，业务应用的 Webhook 订阅随应用授权撤销而停用；恢复不会重新启用它们，需要有人逐个重新启用。",
    recover: "删除期间被停用的 Webhook 订阅（Docs 和业务应用的）保持停用，创建者授权已随删除结束的订阅也会在下一次投递时被停用；恢复不会重新启用它们，需要有人逐个重新启用。" },
  credentials_paused: {
    delete: "不经 API 凭据批准签发的 API 访问密钥和服务账户暂停使用，不会删除；恢复后它们重新可用，无需重新签发。经批准签发的密钥和服务账户不在此列，随批准永久停止。",
    recover: "暂停的、不经 API 凭据批准签发的 API 访问密钥和服务账户随恢复重新可用，无需重新签发；经批准签发的仍然无效。" },
  forms_links_paused: {
    delete: "表单的公开填写链接暂时失效，填写者会看到链接已失效；恢复后未过期的链接重新可用。",
    recover: "表单的公开填写链接随恢复重新可用（已过期的除外）。" },
  org_templates_kept: {
    delete: "这个工作空间发布到组织的模板属于组织，保留不变，组织成员仍可使用。",
    recover: "这个工作空间发布到组织的模板一直保留在组织中，不受恢复影响。" },
  recover_within_7_days: {
    delete: "删除后有七天恢复期：期内平台管理员可以把它恢复到删除前的状态；期满后它保持已删除状态，数据仍然保存，但不能再从控制台恢复。",
    recover: "恢复只能在删除后七天内进行；期满后数据仍然保存，但不能再从控制台恢复。" },
  recover_not_journalled: {
    delete: "删除会写入安全日志，之后的恢复不会：恢复之后如果从更早的备份还原，它会再次处于已删除状态。",
    recover: "恢复不会写入安全日志：如果之后从早于此刻的备份还原，这个工作空间会按原来的删除时间再次处于已删除状态，届时可能已超出恢复期限。" },
  recover_blocked_by_organization: {
    recover: "所属组织当前已冻结或已删除：恢复后这个工作空间仍然无法访问，直到组织恢复正常。" },
  recover_without_usable_owner: {
    recover: "恢复后没有可用的拥有者：没有人能管理成员和设置。请在本页「没有有效拥有者的工作空间」中为它指定拥有者。" },
  rename_visible_everywhere: {
    rename: "新名称立即对所有成员可见：工作空间切换器、成员列表、邀请邮件和各个应用都会显示新名称。" },
  rename_not_journalled: {
    rename: "重命名不会写入安全日志：如果之后从早于此刻的备份还原，会显示原来的名称，需要再次重命名。" },
};
export const UNKNOWN_NOTE = "另有一项说明（{code}），本页面无法解释，请刷新页面后重新预览。";
/** One note in the reader's language: its wording for this action, or for the action it was
 *  written for; a code this page does not know says so with the code. */
export function workspaceNoteText(action: WorkspaceLifecycleAction, code: string): string {
  const entry = NOTE_TEXT[code];
  const source = entry ? entry[action] ?? Object.values(entry)[0] : undefined;
  return source ? tr(source) : tr(UNKNOWN_NOTE, { code });
}
/** The result's restore note: a code worded like a note, or a sentence the catalogue holds. */
export function restoreNoteText(action: WorkspaceLifecycleAction, note: string | null | undefined): string {
  if (!note) return "";
  return hasMessage(note) ? tr(note) : workspaceNoteText(action, note);
}

// ------------------------------------------------------------------------------ refusals
/** After these the server accepts only a new preview (admin_operations), or the workspace is no
 *  longer in the state the preview showed: the dialog asks for a new preview, with a new key. A
 *  deletion whose journal entry failed (`journal_unavailable`) may still take effect on a later
 *  restore, and the server asks for the workspace's state to be checked again before a retry. */
const NEEDS_NEW_PREVIEW = new Set(["admin_preview_expired", "admin_preview_stale", "admin_preview_invalid", "idempotency_conflict",
  "workspace_state_conflict", "workspace_recovery_expired", "workspace_name_unchanged", "no_workspace", "journal_unavailable"]);
/** The preview's and the action's refusals worded for this dialog when the server sends only a
 *  code or a sentence no catalogue holds; the server's own catalogued sentence is kept as it is.
 *  The rest are worded page-wide (labels.ts). */
export const WORKSPACE_REFUSALS: Record<string, string> = {
  bad_action: "不支持的工作空间操作，请刷新页面后重试。",
  invalid_field: "新名称无效：须为 1 至 100 个字符，且不能包含换行、制表符等控制字符。",
  no_workspace: "找不到这个工作空间，请刷新列表。",
  admin_preview_stale: "工作空间的状态、名称或成员已变化，请重新预览影响。",
  // the page-wide 「操作未提交」 is not true of a deletion: its journal entry may already be written
  journal_unavailable: "安全记录暂不可用，删除未完成；如果之后从备份还原，这次删除仍可能生效。请重新预览并核实工作空间状态后再试。",
};
function aboutWorkspace(e: unknown): unknown {
  if (!(e instanceof ApiError) || e.upstream || hasMessage(e.originalMessage) || !WORKSPACE_REFUSALS[e.code]) return e;
  const next = new ApiError(e.status, e.code, WORKSPACE_REFUSALS[e.code], e.detail, e.upstream);
  next.requestId = e.requestId; next.retryAfter = e.retryAfter;
  return next;
}
export const workspaceErrText = (e: unknown) => errText(aboutWorkspace(e));
/** Whether the refusal leaves the held preview unusable: the dialog then asks for a new one. */
export const needsNewPreview = (e: unknown) => e instanceof ApiError && !e.upstream && NEEDS_NEW_PREVIEW.has(e.code);

// ------------------------------------------------------------------------------ the requests
export type Attempt = { fingerprint: string; key: string };
/** One idempotency key per (preview token, reason, name): the same request again (a retry after a
 *  lost answer, a gateway's 503) reuses the key and is replayed by the server; a new preview, an
 *  edited reason or another name is a new request with a new key. */
export function attemptFor(current: Attempt | null, previewToken: string, reason: string, name: string | null,
                           newKey: () => string = () => crypto.randomUUID()): Attempt {
  const fingerprint = JSON.stringify([previewToken, reason, name]);
  return current?.fingerprint === fingerprint ? current : { fingerprint, key: newKey() };
}
/** The preview's body: the action, and for a rename the name asked for. */
export const previewBody = (action: WorkspaceLifecycleAction, name: string | null) =>
  action === "rename" ? { action, name } : { action };
/** The action's body: the reason, the preview it confirms, the attempt's key, and for a rename the
 *  name the preview normalised. */
export const actionBody = (action: WorkspaceLifecycleAction, previewToken: string, reason: string, key: string, name: string | null) =>
  ({ reason, preview_token: previewToken, idempotency_key: key, ...(action === "rename" ? { name: name ?? "" } : {}) });

const stateLabel = (status: string) => WORKSPACE_STATUS[status] || status;
export const RISK_LABELS: Record<string, string> = { high: "高风险", medium: "中等风险" };

/** The loaded preview: the target, the change in words, every count with its meaning, and the
 *  server's notes. Pure, so it can be rendered on its own (tests/admin-workspace-lifecycle.test.mjs). */
export function WorkspaceLifecycleImpact({ action, plan }: { action: WorkspaceLifecycleAction; plan: WorkspacePlan }) {
  useI18n();
  const target = plan.target, org = target.organization;
  const facts = [
    WORKSPACE_KIND[target.kind] ? tr("类型：{kind}", { kind: WORKSPACE_KIND[target.kind] }) : "",
    org ? tr("所属组织：{name}", { name: org.name }) + (org.status !== "active" ? ` · ${stateLabel(org.status)}` : "") : "",
  ].filter(Boolean);
  const deleted = target.status === "deleted" ? [
    target.deleted_at ? tr("删除时间：{time}", { time: fmtTime(target.deleted_at) }) : "",
    target.recover_until ? tr("恢复期限：{time}", { time: fmtTime(target.recover_until) }) : "",
    target.previous_status ? tr("删除前状态：{status}", { status: stateLabel(target.previous_status) }) : "",
  ].filter(Boolean) : [];
  const absent = uncountedRows(action, plan.impact);
  return <>
    <p><span className="muted">{tr("目标工作空间")}</span> <b>{target.name}</b> <span className="mono small muted">{target.id}</span>
      {RISK_LABELS[plan.risk] && <> <span className={`badge ${plan.risk === "high" ? "err" : "warn"}`}>{tr(RISK_LABELS[plan.risk])}</span></>}</p>
    {facts.length > 0 && <p className="small muted">{facts.join(" · ")}</p>}
    {action === "rename"
      ? <p>{tr("名称：{before} → {after}", { before: target.name, after: plan.name ?? "" })}</p>
      : <p>{tr("状态：{before} → {after}", { before: stateLabel(plan.transition.from), after: stateLabel(plan.transition.to) })}</p>}
    {deleted.length > 0 && <p className="small muted">{deleted.join(" · ")}</p>}
    {action === "recover" && plan.transition.to === "frozen" && <p className="notice" role="note">{tr(FROZEN_RECOVERY_NOTE)}</p>}
    <dl className="kv ws-impact" aria-label={tr("操作影响")}>
      {impactRows(action, plan.impact).map(([key, label]) => <Fragment key={key}>
        <dt>{tr(label)}</dt><dd><b>{formatNumber(plan.impact[key] as number)}</b> · {tr(impactMeaning(action, key, plan))}</dd>
      </Fragment>)}
    </dl>
    {absent.length > 0 && <p className="small muted">{tr(NOT_COUNTED, { names: absent.map(([, label]) => tr(label)).join(" · ") })}</p>}
    {plan.notes.length > 0 && <ul className="ws-lifecycle-notes" aria-label={tr("这项操作的说明")}>
      {plan.notes.map((code) => <li key={code}>{workspaceNoteText(action, code)}</li>)}</ul>}
  </>;
}

/** What a deletion withdrew, by the result's keys. */
export const WITHDRAWN_ROWS: ReadonlyArray<readonly [keyof WorkspaceWithdrawn, string]> = [
  ["sessions", "移出这个工作空间的会话"], ["tokens", "撤销的应用令牌"], ["codes", "作废的授权码"],
  ["invitations", "撤回的邀请"], ["approvals", "永久撤销的 API 凭据批准"],
];
/** One sentence for the result, shown in the result dialog and in the list's notice. A recovery
 *  into an organization that keeps members out says so: the workspace is back, its members are not. */
export function workspaceOutcomeText(result: Pick<WorkspaceLifecycleResult, "action" | "workspace">): string {
  const ws = result.workspace;
  if (result.action === "rename") return tr("工作空间已重命名为「{name}」。", { name: ws.name });
  if (result.action === "delete") return ws.recover_until
    ? tr("「{name}」已删除，可在 {time} 前恢复。", { name: ws.name, time: fmtTime(ws.recover_until) })
    : tr("「{name}」已删除。", { name: ws.name });
  if (organizationBlocks(ws)) return tr(RECOVERED_BLOCKED_BY_ORGANIZATION, { name: ws.name, status: stateLabel(ws.status) });
  return tr("「{name}」已恢复，当前状态：{status}。", { name: ws.name, status: stateLabel(ws.status) });
}
export const RECOVERED_BLOCKED_BY_ORGANIZATION = "「{name}」已恢复，当前状态：{status}。所属组织当前已冻结或已删除：在组织恢复正常之前，成员仍无法访问这个工作空间。";

/** The answer: the operation, what the deletion withdrew, and what a later backup restore undoes. */
export function WorkspaceLifecycleOutcome({ result, onClose }: { result: WorkspaceLifecycleResult; onClose: () => void }) {
  useI18n();
  const bodyId = useId();
  const note = restoreNoteText(result.action, result.restore_note);
  return <Modal title={tr(WORKSPACE_DONE_TITLES[result.action])} onClose={onClose} describedBy={bodyId}
    footer={<><span className="spacer" /><button type="button" className="btn primary" data-autofocus onClick={onClose}>{tr("关闭")}</button></>}>
    <div className="ws-lifecycle col gap12">
      <div id={bodyId} className="col gap8">
        <p>{workspaceOutcomeText(result)}</p>
        {result.action === "recover" && result.workspace.status === "frozen" && <p>{tr(FROZEN_RECOVERY_NOTE)}</p>}
        {note && <p className="notice warn" role="note">{note}</p>}
      </div>
      {result.withdrawn && <>
        <dl className="kv" aria-label={tr("已撤回的访问")}>
          {WITHDRAWN_ROWS.map(([key, label]) => <Fragment key={key}>
            <dt>{tr(label)}</dt><dd><b>{formatNumber(result.withdrawn![key] ?? 0)}</b></dd>
          </Fragment>)}
        </dl>
        <p className="small muted">{tr("这些在删除时撤回的访问不会随恢复回来。")}</p>
      </>}
      <p className="small muted">{tr("操作编号：{id}", { id: result.operation_id })}</p>
    </div>
  </Modal>;
}

export function WorkspaceLifecycleDialog({ workspace, action, onClose, onDone }: {
  workspace: Pick<WorkspaceRow, "id" | "name">; action: WorkspaceLifecycleAction;
  onClose: () => void; onDone: (result: WorkspaceLifecycleResult) => void;
}) {
  useI18n();
  const title = tr(WORKSPACE_ACTION_LABELS[action]);
  const rename = action === "rename";
  const [plan, setPlan] = useState<WorkspacePlan | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  // The server refused the preview the dialog holds; only a new preview can be confirmed.
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WorkspaceLifecycleResult | null>(null);
  // A rename is previewed for the name typed: `asked` is the name the preview is for, null until
  // one is asked for and again as soon as the name is edited.
  const [name, setName] = useState(workspace.name);
  const [asked, setAsked] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const nameId = useId();
  // The current attempt's idempotency key (attemptFor); a new preview starts over.
  const attempt = useRef<Attempt | null>(null);
  const problem = rename ? nameProblem(name, workspace.name) : null;
  const waiting = rename && asked === null;
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setConfirmed(false); setInvalid(false); attempt.current = null;
    if (rename && asked === null) return;
    post<WorkspacePlan>(`/api/admin/workspaces/${encodeURIComponent(workspace.id)}/lifecycle/preview`, previewBody(action, asked))
      .then((next) => { if (live) setPlan(next); })
      .catch((e) => { if (live) setError(workspaceErrText(e)); });
    return () => { live = false; };
  }, [workspace.id, action, revision, asked]); // eslint-disable-line react-hooks/exhaustive-deps
  const previewAgain = () => setRevision((n) => n + 1);
  const previewName = () => {
    setTried(true);
    if (problem) { nameRef.current?.focus(); return; }
    setAsked(name.trim()); previewAgain();
  };
  if (result) return <WorkspaceLifecycleOutcome result={result} onClose={onClose} />;
  return <ActionDialog<WorkspaceLifecycleResult> title={title} confirmLabel={title} reason="required" reauthScope="admin"
    danger={action === "delete"}
    ready={!!plan && confirmed && !busy && !invalid && !waiting}
    impact={<><b className="ws-lifecycle-name">{workspace.name}</b><p>{tr(WORKSPACE_ACTION_WARNINGS[action])}</p>
      <p className="small muted">{tr(WORKSPACE_PLATFORM_NOTE)}</p></>}
    onClose={onClose} onDone={(out) => { setResult(out); onDone(out); }}
    run={async (reason) => {
      if (!plan || invalid || waiting) throw new ApiError(400, "admin_confirmation_required", "");
      const named = rename ? plan.name ?? asked : null;
      const current = attempt.current = attemptFor(attempt.current, plan.preview_token, reason, named);
      setBusy(true);
      try {
        return await post<WorkspaceLifecycleResult>(`/api/admin/workspaces/${encodeURIComponent(workspace.id)}/lifecycle/${action}`,
          actionBody(action, plan.preview_token, reason, current.key, named));
      } catch (e) {
        if (needsNewPreview(e)) { setInvalid(true); setConfirmed(false); }
        throw aboutWorkspace(e);
      } finally { setBusy(false); }
    }}>
    <div className="ws-lifecycle col gap12">
      {rename && <div className="field">
        <label htmlFor={nameId}>{tr("新名称")}</label>
        <div className="row gap8 wrap">
          <input id={nameId} ref={nameRef} className="input grow" value={name} autoComplete="off" spellCheck={false}
            aria-invalid={tried && problem ? true : undefined} aria-describedby={`${nameId}-help`}
            onChange={(e) => { setName(e.target.value); if (asked !== null) setAsked(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); previewName(); } }} />
          <button type="button" className="btn sm" onClick={previewName}>{tr("预览影响")}</button>
        </div>
        {tried && problem
          ? <span id={`${nameId}-help`} className="small ws-name-error" role="alert">{tr(problem.source, problem.params)}</span>
          : <span id={`${nameId}-help`} className="tiny muted">{tr("1 至 100 个字符，不能包含控制字符；首尾空格会被去掉。")}</span>}
      </div>}
      {error && <div className="notice err col gap8" role="alert"><p>{error}</p>
        <button type="button" className="btn sm" onClick={previewAgain}>{tr("重新预览")}</button></div>}
      {waiting && !error && <p className="small muted">{tr("填写新名称后预览影响；名称改动后需要重新预览。")}</p>}
      {!plan && !error && !waiting && <p role="status">{tr("正在核对工作空间与操作影响…")}</p>}
      {plan && <>
        <WorkspaceLifecycleImpact action={action} plan={plan} />
        {invalid && <p className="notice warn" role="alert">{tr("这份预览已失效，请重新预览后再确认。")}</p>}
        <p className="muted small">{tr("预览有效至 {time}。工作空间变化后必须重新预览。", { time: fmtTime(plan.expires_at) })}</p>
        <label className="check"><input type="checkbox" checked={confirmed} disabled={busy || invalid}
          onChange={(e) => setConfirmed(e.target.checked)} />{tr("我已核对目标工作空间和上述影响。")}</label>
        <div><button type="button" className="btn sm" disabled={busy} onClick={previewAgain}>{tr("重新预览")}</button></div>
      </>}
    </div>
  </ActionDialog>;
}
