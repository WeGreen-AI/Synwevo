import { Fragment, useEffect, useRef, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { t as tr, fmtTime, formatNumber, hasMessage, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { WORKSPACE_STATUS, errText } from "./labels";
import { ORGANIZATION_STATUS_LABELS } from "./OrganizationLifecycleDialog";
import "./messages";
import "./apps.messages";

/**
 * B1/B2: one change to the application catalogue from the administration page — an application
 * switched off or on for the whole platform, its access policy tightened or loosened, or it
 * switched off or on for one workspace (`platform.apps.manage`; synwevo_identity.admin_apps) — or,
 * for an application whose data belongs to an organization (Synwevo Message, B3), for one
 * organization (`organization_id`, `platform.apps.manage` alone).
 *
 * The organization lifecycle's confirmed-operation protocol (OrganizationLifecycleDialog.tsx):
 * the server first answers a signed preview bound to this actor, application, change, workspace
 * and their current version, with what the change does — who loses or gains access, which
 * workspaces, the running work — and a list of note codes worded here; the administrator reads
 * it, writes a reason and re-authenticates inside `ActionDialog`; the change is posted with the
 * preview token and one idempotency key per (preview, reason), so a retry after a lost answer
 * replays the same request. A preview the server no longer accepts asks for a new one, which
 * also starts a new key. Nothing here deletes business data, and nothing gives the administrator
 * access to it.
 */
export type AppChange = "disable" | "enable" | "policy_explicit" | "policy_members";
export type AppScope = "platform" | "workspace" | "organization";
export type AccessPolicy = "members" | "explicit";
/** GET /api/admin/apps: one application of the catalogue, counted, never listed. `running_jobs`
 *  is null for an application whose work this service cannot count. `scope` is how access to it
 *  is decided: per workspace (policy and per-workspace switch apply), or for every live account
 *  (`organization`, `personal`: only the platform-wide switch exists). `changes` are the
 *  platform-wide changes the server would accept now. */
export type CatalogueApp = {
  id: string; name: string; status: "active" | "disabled" | string; access_policy: AccessPolicy | string;
  launch_url: string | null; workspaces_enabled: number; members_with_access: number;
  explicit_grants: number; explicit_revocations: number; running_jobs: number | null;
  scope?: "workspace" | "organization" | "personal" | string; changes?: AppChange[];
  /** B3: how many organizations the platform switched it off for — a number only for an
   *  application switched per organization (Synwevo Message), null or absent otherwise. */
  organizations_disabled?: number | null;
};
/** Whether the access policy and the per-workspace switch exist for this application. */
export const workspaceScoped = (app: Pick<CatalogueApp, "scope">) => !app.scope || app.scope === "workspace";
/** GET /api/admin/apps/{id}/workspaces: metadata and counts of one workspace for one application. */
export type AppWorkspace = {
  id: string; name: string; kind: string; status: string; organization_id: string | null;
  enabled: boolean; enabled_at: number | null; members: number; members_with_access: number;
};
/** GET /api/admin/apps/{id}/organizations (B3): one organization and the application there —
 *  its lifecycle state, whether the platform switched it off for it (and when), its live members
 *  and its conversations that are not archived. Counts and metadata only. */
export type AppOrganization = {
  id: string; name: string; status: string; deleted_at: number | null; recover_until: number | null;
  enabled: boolean; updated_at: number | null; members: number; conversations: number;
};
/** The preview's impact for one organization (admin_apps `_organization_impact`): what switching
 *  Synwevo Message off there closes. `open_streams` is null: the streams live in the Message
 *  process's memory, which the account service cannot count. */
export type OrganizationImpact = {
  members_affected: number; members_with_conversations: number; members_left_without_messages: number;
  conversations: { groups: number; direct: number; object: number }; open_reports: number;
  pending_notifications: { email: number; push: number }; pending_cards: number; signed_in_sessions: number;
  open_streams: number | null;
};
/** `people_*`: distinct people losing (gaining) access in at least one workspace; `memberships_*`:
 *  the person × workspace pairs. Null where the server does not count (see NOT_COUNTED). */
export type AppImpact = { people_losing_access: number; people_gaining_access: number;
  memberships_losing_access?: number | null; memberships_gaining_access?: number | null;
  workspaces_affected: number | null; running_jobs: number | null; sessions_in_app: number | null;
  /** Synwevo Docs only (null elsewhere, absent from an older server): active API-platform approvals
   *  whose approving owner loses access — each governs one API key or service account, bound or not
   *  yet issued — re-checked on every use, so they stop with the approver and recover with them. */
  credentials_stopping?: number | null };
/** POST /api/admin/apps/{id}/preview */
export type AppPlan = { change: AppChange; scope: AppScope;
  target: CatalogueApp & { workspace?: AppWorkspace; organization?: AppOrganization };
  impact: AppImpact | OrganizationImpact; notes: string[]; expires_at: number; preview_token: string };
/** POST /api/admin/apps/{id}/{change} */
export type AppChangeResult = { ok: true; operation_id: string; app: CatalogueApp; workspace?: AppWorkspace; organization?: AppOrganization };

/** The change as one key: the four platform changes, and the two a workspace — or an
 *  organization (B3) — can have. */
export type ChangeKey = AppChange | "workspace_disable" | "workspace_enable" | "organization_disable" | "organization_enable";
export const changeKey = (change: AppChange, scope: AppScope): ChangeKey =>
  scope === "workspace" ? (change === "disable" ? "workspace_disable" : "workspace_enable")
    : scope === "organization" ? (change === "disable" ? "organization_disable" : "organization_enable") : change;
/** Whether the change takes access away (a danger confirmation): switching off, or tightening. */
export const takesAway = (change: AppChange) => change === "disable" || change === "policy_explicit";

export const APP_STATUS_LABELS: Record<string, string> = { active: "已启用", disabled: "全平台已停用" };
export const POLICY_LABELS: Record<string, string> = { members: "成员均可使用", explicit: "仅限明确授权" };
/** What each policy means, for a reader who has not met the word. */
export const POLICY_MEANING: Record<string, string> = {
  members: "已开通这个应用的工作空间中，每位成员都可以使用，被单独撤销访问的成员除外。",
  explicit: "只有被明确授予资格的成员可以使用；其他成员保留成员身份，但打不开这个应用。",
};
export const CHANGE_LABELS: Record<ChangeKey, string> = {
  disable: "停用应用", enable: "启用应用", policy_explicit: "改为仅限明确授权", policy_members: "改为成员均可使用",
  workspace_disable: "为这个空间停用", workspace_enable: "为这个空间启用",
  organization_disable: "为这个组织停用", organization_enable: "为这个组织启用",
};
/** What each change says before anything is counted: who is affected, that nothing is deleted,
 *  and what happens when it is undone — including what does not come back by itself (Docs webhook
 *  subscriptions switched off and integrations whose authorization ended meanwhile). */
export const CHANGE_WARNINGS: Record<ChangeKey, string> = {
  disable: "停用后，所有工作空间的成员都不能再打开这个应用，已发出的入口和链接在下一次使用时被拒绝。不会删除任何数据。重新启用后，按当时的成员关系和授权恢复使用；期间被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。",
  enable: "启用后，已开通这个应用的工作空间按当时的成员关系和授权恢复使用。之前停止的任务、被停用的 Webhook 订阅和失去授权的集成不会自动恢复：任务需要重新运行，订阅需要重新启用，集成需要重新授权。",
  policy_explicit: "改为仅限明确授权后，只有被明确授予资格的成员可以使用；其他成员在下一次请求时失去访问，成员身份保留。改回成员均可使用后他们重新可以使用，但期间被停用的 Webhook 订阅和失去授权的集成需要重新启用或重新授权。",
  policy_members: "改为成员均可使用后，已开通这个应用的工作空间中的成员都可以使用；被单独撤销访问的成员仍然不能使用。之前被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。",
  workspace_disable: "停用后，这个工作空间的成员不能再打开这个应用；其他工作空间不受影响。不会删除任何数据。重新启用后，按当时的成员关系和授权恢复使用；期间被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。",
  workspace_enable: "启用后，这个工作空间的成员按应用的访问策略和已有授权使用这个应用。之前被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。",
  organization_disable: "停用后，这个组织的成员从下一次请求起不能再读写它在 Synwevo Message 中的会话；其他组织不受影响。不会删除任何数据：会话、消息、附件和举报原样保留。重新启用后一切照旧；停用期间跳过的邮件、推送、卡片和 Agent 发送不会补发。",
  organization_enable: "启用后，这个组织的成员重新可以读写它在 Synwevo Message 中的会话，历史、成员和已读位置与停用前一致。停用期间跳过的邮件、推送、卡片和 Agent 发送不会补发。",
};
export const DONE_TEXT: Record<ChangeKey, string> = {
  disable: "已在全平台停用「{app}」。", enable: "已在全平台启用「{app}」。",
  policy_explicit: "「{app}」已改为仅限明确授权。", policy_members: "「{app}」已改为成员均可使用。",
  workspace_disable: "已为「{workspace}」停用「{app}」。", workspace_enable: "已为「{workspace}」启用「{app}」。",
  organization_disable: "已为「{organization}」停用「{app}」。", organization_enable: "已为「{organization}」启用「{app}」。",
};
export const APP_PLATFORM_NOTE = "此操作由平台执行并写入审计记录；不会给任何平台管理员带来成员身份、资格或业务文档访问。";

/** The preview's counts: the key the server sends, its label, and what the count means for a
 *  change that takes access away and for one that gives it back. The two counts of people are
 *  distinct people losing (gaining) access in at least one workspace, shown with the memberships —
 *  person × workspace pairs — they lose (gain) it in, and mean the same whichever the direction;
 *  only both at zero say nobody (`NOBODY`). The API credentials row appears only for an application
 *  whose credentials the server counts (Synwevo Docs). */
const LOSING = "从下一次请求起，在这些成员关系中不能再使用这个应用；成员身份和内容保留。在其他工作空间仍可使用的，不受影响。";
const GAINING = "从下一次请求起，在这些成员关系中可以使用这个应用，仍受各自的成员角色限制。";
export type ImpactRowKey = "people_losing_access" | "people_gaining_access" | "workspaces_affected" | "running_jobs" | "sessions_in_app" | "credentials_stopping";
export const IMPACT_ROWS: ReadonlyArray<readonly [ImpactRowKey, string, string, string]> = [
  ["people_losing_access", "将失去访问的人", LOSING, LOSING],
  ["people_gaining_access", "将获得访问的人", GAINING, GAINING],
  ["workspaces_affected", "受影响的工作空间", "这些工作空间里的这个应用随之停止可用；内容不会删除，也不会提供给管理员。", "这些工作空间里的这个应用随之可用；此操作不改变其内容。"],
  ["running_jobs", "排队或运行中的任务", "这些任务会在下一次权限检查时停止，其余部分不再生成；已生成的结果保留。", "正在运行的任务不受影响。"],
  ["sessions_in_app", "相关的在线会话", "这些会话不会被结束，但之后对这个应用的请求会被拒绝。", "现有会话不受影响。"],
  ["credentials_stopping", "将停止的 API 凭据", "由失去访问的空间拥有者批准的 API 密钥和服务账户（包括已批准、尚未签发的）在下一次使用时被拒绝；批准者重新获得访问后恢复可用，无需重新签发。", "此操作不会停止任何 API 凭据。"],
];
/** The memberships count that goes with each count of people. */
export const MEMBERSHIPS: Partial<Record<ImpactRowKey, "memberships_losing_access" | "memberships_gaining_access">> = {
  people_losing_access: "memberships_losing_access", people_gaining_access: "memberships_gaining_access",
};
/** Whether anyone is counted on a row: its value, or the memberships that go with it. */
const counted = (impact: AppImpact, key: ImpactRowKey) => {
  const pair = MEMBERSHIPS[key];
  return (impact[key] ?? 0) > 0 || (pair ? (impact[pair] ?? 0) > 0 : false);
};
/** The rows a change shows: the people who lose access only where a change can take it away (or
 *  the server counted some anyway), the people who gain it only where a change can give it. */
export function impactRows(change: AppChange, impact: AppImpact) {
  const away = takesAway(change);
  // API credentials only where the server counts them (Synwevo Docs) and something can stop
  return IMPACT_ROWS.filter(([key]) => key === "credentials_stopping" ? typeof impact.credentials_stopping === "number" && (away || counted(impact, key))
    : key === "people_losing_access" ? away || counted(impact, key)
    : key === "people_gaining_access" ? !away || counted(impact, key) : true);
}
export const NOBODY: Partial<Record<ImpactRowKey, string>> = {
  people_losing_access: "没有人因此失去访问。", people_gaining_access: "没有人因此获得访问。",
};
/** What a count the server does not take (null) means: the application's jobs are not counted
 *  here, or it is not enabled per workspace at all. */
export const NOT_COUNTED: Record<ImpactRowKey, string> = {
  people_losing_access: "这项数量暂时无法统计。", people_gaining_access: "这项数量暂时无法统计。",
  workspaces_affected: "这个应用不按工作空间开通。",
  running_jobs: "这个应用的任务不在这里统计。",
  sessions_in_app: "这个应用不按工作空间开通，在线会话不在这里统计。",
  credentials_stopping: "这个应用不签发 API 凭据。",
};
/** The count as shown: people with the memberships they are counted in, or the number alone. */
export function countText(key: ImpactRowKey, impact: AppImpact): string {
  const value = impact[key];
  if (value === null || value === undefined) return "—";
  const pair = MEMBERSHIPS[key], memberships = pair ? impact[pair] : undefined;
  return typeof memberships === "number"
    ? tr("{people} 人 · {memberships} 个成员关系", { people: formatNumber(value), memberships: formatNumber(memberships) })
    : formatNumber(value);
}
/** What one count means for this change; a count the server could not take (null) says so. */
export function impactMeaning(change: AppChange, key: ImpactRowKey, impact: AppImpact): string {
  const value = impact[key];
  if (value === null || value === undefined) return NOT_COUNTED[key];
  if (NOBODY[key] && !counted(impact, key)) return NOBODY[key]!;
  const row = IMPACT_ROWS.find(([k]) => k === key)!;
  return takesAway(change) ? row[2] : row[3];
}

/** B3: the preview's counts for one organization (Synwevo Message), each with what it means when
 *  the switch-off closes it and when the switch-on gives it back. `open_streams` is never counted
 *  (null): the sentence says so, and by when an open tab stops hearing about the organization. */
export type OrganizationRowKey = "members_affected" | "members_with_conversations" | "members_left_without_messages" | "conversations"
  | "open_reports" | "pending_notifications" | "pending_cards" | "signed_in_sessions" | "open_streams";
export const ORGANIZATION_IMPACT_ROWS: ReadonlyArray<readonly [OrganizationRowKey, string, string, string]> = [
  ["members_affected", "组织的有效成员", "从下一次请求起，他们读写这个组织的会话都会被拒绝；成员身份、历史和已读位置保留。", "从下一次请求起，他们重新可以使用这个组织的会话。"],
  ["members_with_conversations", "有会话的成员", "这些成员至少在这个组织的一个会话中；会话保留，暂时打不开。", "这些成员的会话原样回来。"],
  ["members_left_without_messages", "将没有可用消息的成员", "这些成员没有其他可以使用 Synwevo Message 的组织，消息页面只会显示停用说明。", "这些成员重新可以使用 Synwevo Message。"],
  ["conversations", "未归档的会话", "这些会话全部暂停，不会删除。", "这些会话恢复可用，内容不变。"],
  ["open_reports", "待处理的举报", "组织管理员仍然可以处理这些举报。", "举报不受影响。"],
  ["pending_notifications", "待发送的邮件和推送", "关于这些会话和举报的邮件、推送会被跳过，不会补发；站内通知保留。", "停用期间跳过的邮件和推送不会补发。"],
  ["pending_cards", "待投递的卡片", "发往这个组织讨论的 Synwevo Docs 卡片会被跳过，不会补发。", "停用期间跳过的卡片不会补发。"],
  ["signed_in_sessions", "已登录的会话", "这些会话不会被结束，但之后对这个组织会话的请求会被拒绝；这里统计成员在所有入口的登录会话。", "现有会话不受影响。"],
  ["open_streams", "打开的实时连接", "无法统计，最迟 25 秒内不再收到该组织事件。", "无法统计；已打开的页面最迟 25 秒内得知已重新启用。"],
];
/** One organization count as shown: conversations by kind, mail and push apart, the rest a number;
 *  `open_streams` (null) as a dash. */
export function organizationCountText(key: OrganizationRowKey, impact: OrganizationImpact): string {
  if (key === "conversations") {
    const c = impact.conversations || { groups: 0, direct: 0, object: 0 };
    return tr("群聊 {groups} · 私聊 {direct} · 项目讨论 {object}", { groups: formatNumber(c.groups), direct: formatNumber(c.direct), object: formatNumber(c.object) });
  }
  if (key === "pending_notifications") {
    const n = impact.pending_notifications || { email: 0, push: 0 };
    return tr("邮件 {email} · 推送 {push}", { email: formatNumber(n.email), push: formatNumber(n.push) });
  }
  const value = impact[key];
  return typeof value === "number" ? formatNumber(value) : "—";
}
export function organizationMeaning(change: AppChange, key: OrganizationRowKey): string {
  const row = ORGANIZATION_IMPACT_ROWS.find(([k]) => k === key)!;
  return takesAway(change) ? row[2] : row[3];
}

/** The notes the server attaches by code (admin_apps), each in plain language. A note's meaning
 *  depends on the direction of the change, and some on the change itself: tightening stops only
 *  what a person without a grant submitted or issued (`tighten`); API keys and service accounts
 *  are not checked against grants of their own, but each is bound to a workspace owner's approval
 *  that is re-checked on every use (Approvals.active → require_owner), so they stop with an
 *  approver who loses access and recover when the approver regains it (`approved_credentials_stop`,
 *  `tighten`, `loosen`). `jobs_continue_or_stop`: Synwevo Docs re-checks the requester's authority
 *  before a run starts, between samples and before anything is published. */
export type NoteWording = { away: string; back: string; tighten?: string; loosen?: string };
const NOT_RESTORED = "在此期间被停用的集成和 Webhook 订阅不会自动恢复：集成需要重新授权，Webhook 订阅需要重新启用。";
/** B3 and G1: what switching Synwevo Message off does to conversations — for one organization and
 *  platform-wide alike (admin_apps `MESSAGES_AWAY_NOTES` / `MESSAGES_BACK_NOTES`). */
const NOT_REPLAYED = "停用期间跳过的邮件、推送、卡片和 Agent 发送不会补发。";
const MESSAGE_NOTES: Record<string, NoteWording> = {
  conversations_paused: {
    away: "会话从成员的下一次请求起回答「Synwevo Message 已停用」；成员关系、历史、已读位置和偏好保留。",
    back: "会话从成员的下一次请求起恢复可用。" },
  streams_end_within_heartbeat: {
    away: "已打开的消息页面最迟在下一次心跳（25 秒）时得知停用，之后不再收到相关事件；连接本身不会被切断，也无法在这里统计。",
    back: "已打开的消息页面最迟在下一次心跳（25 秒）时得知已重新启用。" },
  notifications_skipped: {
    away: "关于这些会话和举报的邮件、推送会被跳过而不是推迟；站内通知保留；跳过的不会补发。",
    back: NOT_REPLAYED },
  cards_skipped: {
    away: "发往这些讨论的 Synwevo Docs 卡片会被跳过，不会积压，因此不影响其他组织的卡片；重新启用后不补发。",
    back: NOT_REPLAYED },
  agent_sends_refused: {
    away: "排队中的 Agent 或工作台发送会失败，原因是 Synwevo Message 已停用。",
    back: NOT_REPLAYED },
  moderation_continues: { away: "组织管理员仍然可以处理举报。", back: "举报不受影响。" },
  export_unaffected: { away: "个人账户导出仍包含本人在这里发出的消息。", back: "个人账户导出不受影响。" },
  platform_switch_outranks: {
    away: "Synwevo Message 目前在全平台停用：全平台的停用优先于组织的设置。",
    back: "Synwevo Message 目前在全平台停用：在全平台重新启用之前，为这个组织启用不会生效。" },
  history_unchanged_on_enable: {
    away: "重新启用后，会话、消息、成员和已读位置与停用前一致。",
    back: "会话、消息、成员和已读位置与停用前完全一致。" },
  skipped_not_replayed: { away: NOT_REPLAYED, back: NOT_REPLAYED },
};
export const NOTE_TEXT: Record<string, NoteWording> = {
  data_kept: {
    away: "不会删除任何业务数据：应用里的项目、模板和结果原样保留，只是暂时打不开。",
    back: "此操作不改变任何业务数据。" },
  links_stop: {
    away: "应用入口和已发出的链接在下一次使用时被拒绝；重新启用后，未过期的链接重新可用，每次访问仍按当时的成员关系和授权判断。",
    tighten: "没有明确授权的人，其应用入口和由他们发出的链接在下一次使用时被拒绝；获得授权或改回成员均可使用后，未过期的链接重新可用。通过 API 密钥或服务账户发出的链接按批准该凭据的空间拥有者判断：批准者仍可使用则不受影响。",
    loosen: "重新获得访问的人，其应用入口和未过期的链接重新可用；批准者重新获得访问后，由其批准的 API 密钥和服务账户发出的链接也重新可用。",
    back: "应用入口和未过期的链接重新可用，每次访问仍按当时的成员关系和授权判断。" },
  jobs_continue_or_stop: {
    away: "排队或运行中的任务会在下一次权限检查时停止（开始运行前、样本之间、发布之前），其余样本不再生成；已生成的结果保留，重新启用后需要有人重新运行。账户体系建立之前由运维提交的任务不受影响。",
    tighten: "没有明确授权的人提交的排队或运行中任务会在下一次权限检查时停止，已生成的结果保留；通过 API 密钥或服务账户提交的任务按批准该凭据的空间拥有者判断，批准者没有明确授权时同样停止。",
    back: "正在运行的任务继续运行；之前因停用而停止的任务不会自动继续，需要有人重新运行。" },
  explicit_grants_kept: {
    away: "已有的明确授权全部保留：持有授权的成员继续可以使用。",
    back: "已有的明确授权全部保留；以后再改为仅限明确授权时仍然有效。" },
  revocations_kept_on_loosen: {
    away: "被单独撤销访问的成员仍然不能使用。",
    back: "被单独撤销访问的成员仍然不能使用：改为成员均可使用不会恢复已撤销的访问。" },
  webhooks_disabled: {
    away: "创建者失去访问的 Webhook 订阅会在下一次投递时被自动停用；之后即使恢复访问也保持停用，需要有人重新启用。",
    back: "之前因创建者失去访问而停用的 Webhook 订阅不会自动恢复，需要有人重新启用。" },
  delegations_end_at_refresh: {
    away: "失去访问的人连接的集成会在下一次刷新授权时失去授权；之后即使恢复访问，也需要重新授权。",
    back: "之前已失去授权的集成不会自动恢复，需要重新授权。" },
  integrations_need_reauthorization: { away: NOT_RESTORED, back: NOT_RESTORED },
  approved_credentials_stop: {
    away: "有 API 密钥或服务账户的批准者会因此失去访问：这些凭据的调用、排队任务和发出的链接在下一次权限检查时被拒绝。批准者重新获得访问后，凭据恢复可用，无需重新签发。",
    back: "之前因批准者失去访问而被拒绝的 API 密钥和服务账户，在批准者重新获得访问后恢复可用，无需重新签发。" },
  ...MESSAGE_NOTES,
};
/** `data_kept` in Synwevo Message's own words: its data are conversations, not projects. */
export const MESSAGE_NOTE_TEXT: Record<string, NoteWording> = {
  data_kept: {
    away: "不会删除任何数据：会话、消息、附件、举报和通知原样保留，只是暂时打不开。",
    back: "此操作不改变任何数据。" },
};
export const MESSAGES_APP_ID = "messages";
export function noteText(change: AppChange, code: string, appId?: string): string {
  const note = (appId === MESSAGES_APP_ID && MESSAGE_NOTE_TEXT[code]) || NOTE_TEXT[code];
  if (note && change === "policy_explicit" && note.tighten) return tr(note.tighten);
  if (note && change === "policy_members" && note.loosen) return tr(note.loosen);
  return note ? tr(takesAway(change) ? note.away : note.back) : tr("另有一项说明（{code}），本页面无法解释，请刷新页面后重新预览。", { code });
}

/** The refusals after which the server accepts only a new preview (admin_operations). */
const PREVIEW_REFUSALS = new Set(["admin_preview_expired", "admin_preview_stale", "admin_preview_invalid"]);
/** The preview's and the change's refusals, worded for this dialog when the server sends only a
 *  code or a sentence no catalogue holds; the server's own catalogued sentence is kept as it is. */
export const APP_REFUSALS: Record<string, string> = {
  no_app: "找不到这个应用，可能已被移除；请刷新应用目录。",
  no_workspace: "找不到这个工作空间，可能已被删除；请刷新列表。",
  legacy_workspace: "这是待认领的历史数据空间，由认领流程管理，不能在这里启用或停用应用。",
  workspace_unavailable: "这个工作空间当前不可用（已冻结或已删除），不能为它启用应用。",
  no_change: "应用已经处于这个状态，不需要更改；请刷新查看最新状态。",
  bad_change: "不支持的应用操作，请刷新页面后重试。",
  portal_required: "平台本身依赖这个应用，不能停用它或改为仅限明确授权。",
  app_not_released: "当前部署没有发布这个应用，不能在这里启用；启用后下次启动也会被重新停用。",
  admin_preview_stale: "应用、工作空间或可使用的人已变化，请重新预览影响。",
  no_organization: "找不到这个组织，可能已被删除；请刷新列表。",
  organization_unavailable: "这个组织已删除且过了恢复期，不能为它启用应用。",
};
function aboutApp(e: unknown): unknown {
  if (!(e instanceof ApiError) || e.upstream || hasMessage(e.originalMessage) || !APP_REFUSALS[e.code]) return e;
  const next = new ApiError(e.status, e.code, APP_REFUSALS[e.code], e.detail, e.upstream);
  next.requestId = e.requestId; next.retryAfter = e.retryAfter;
  return next;
}
export const appErrText = (e: unknown) => errText(aboutApp(e));
const statusLabel = (status: string) => APP_STATUS_LABELS[status] ? tr(APP_STATUS_LABELS[status]) : status;
const policyLabel = (policy: string) => POLICY_LABELS[policy] ? tr(POLICY_LABELS[policy]) : policy;
export const appStatusLabel = statusLabel;
export const appPolicyLabel = policyLabel;

/** The state before and after, in words: the application's status or policy, or whether it is
 *  enabled for the workspace. */
function Transition({ change, plan }: { change: AppChange; plan: AppPlan }) {
  const target = plan.target;
  if (plan.scope === "organization") {
    const on = tr("已启用"), off = tr("已停用");
    return <p>{tr("在这个组织：{before} → {after}", { before: change === "disable" ? on : off, after: change === "disable" ? off : on })}</p>;
  }
  if (plan.scope === "workspace") {
    const on = tr("已开通"), off = tr("未开通");
    return <p>{tr("在这个空间：{before} → {after}", { before: change === "disable" ? on : off, after: change === "disable" ? off : on })}</p>;
  }
  if (change === "disable" || change === "enable") {
    return <p>{tr("状态：{before} → {after}", { before: statusLabel(target.status), after: statusLabel(change === "disable" ? "disabled" : "active") })}</p>;
  }
  return <p>{tr("访问策略：{before} → {after}", { before: policyLabel(target.access_policy), after: policyLabel(change === "policy_explicit" ? "explicit" : "members") })}</p>;
}

/** The loaded preview: the target, the change in words, every count with its meaning, and the
 *  server's notes. Pure, so it can be rendered on its own (tests/admin-apps.test.mjs). */
export function AppChangeImpact({ change, plan }: { change: AppChange; plan: AppPlan }) {
  useI18n();
  const target = plan.target, workspace = target.workspace, organization = target.organization;
  return <>
    <p><span className="muted">{tr("目标应用")}</span> <b>{target.name}</b> <span className="mono small muted">{target.id}</span></p>
    {workspace && <p><span className="muted">{tr("目标工作空间")}</span> <b>{workspace.name}</b> <span className="mono small muted">{workspace.id}</span>
      {workspace.status !== "active" && <> <span className="badge warn">{WORKSPACE_STATUS[workspace.status] || workspace.status}</span></>}</p>}
    {organization && <p><span className="muted">{tr("目标组织")}</span> <b>{organization.name}</b> <span className="mono small muted">{organization.id}</span>
      {organization.status !== "active" && <> <span className="badge warn">{ORGANIZATION_STATUS_LABELS[organization.status] ? tr(ORGANIZATION_STATUS_LABELS[organization.status]) : organization.status}</span></>}</p>}
    <Transition change={change} plan={plan} />
    {plan.scope === "organization" ? <dl className="kv" aria-label={tr("操作影响")}>
      {ORGANIZATION_IMPACT_ROWS.map(([key, label]) => <Fragment key={key}>
        <dt>{tr(label)}</dt><dd><b>{organizationCountText(key, plan.impact as OrganizationImpact)}</b> · {tr(organizationMeaning(change, key))}</dd>
      </Fragment>)}
    </dl> : <dl className="kv" aria-label={tr("操作影响")}>
      {impactRows(change, plan.impact as AppImpact).map(([key, label]) => <Fragment key={key}>
        <dt>{tr(label)}</dt><dd><b>{countText(key, plan.impact as AppImpact)}</b> · {tr(impactMeaning(change, key, plan.impact as AppImpact))}</dd>
      </Fragment>)}
    </dl>}
    {plan.notes.length > 0 && <ul aria-label={tr("这项更改的说明")}>{plan.notes.map((code) => <li key={code}>{noteText(change, code, target.id)}</li>)}</ul>}
  </>;
}

export function AppChangeDialog({ app, change, workspace, organization, onClose, onDone }: {
  app: Pick<CatalogueApp, "id" | "name">; change: AppChange; workspace?: Pick<AppWorkspace, "id" | "name">;
  organization?: Pick<AppOrganization, "id" | "name">; onClose: () => void; onDone: (result: AppChangeResult) => void;
}) {
  useI18n();
  const key = changeKey(change, organization ? "organization" : workspace ? "workspace" : "platform");
  const title = tr(CHANGE_LABELS[key]);
  const [plan, setPlan] = useState<AppPlan | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  // The server refused the preview the dialog holds; only a new preview can be confirmed.
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  // One idempotency key per (preview token, reason): a retry of the same request replays it; a
  // new preview or an edited reason is a new request with a new key (as the lifecycle dialog).
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const body = organization ? { organization_id: organization.id } : workspace ? { workspace_id: workspace.id } : {};
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setConfirmed(false); setInvalid(false); attempt.current = null;
    post<AppPlan>(`/api/admin/apps/${encodeURIComponent(app.id)}/preview`, { change, ...body })
      .then((next) => { if (live) setPlan(next); })
      .catch((e) => { if (live) setError(appErrText(e)); });
    return () => { live = false; };
  }, [app.id, change, workspace?.id, organization?.id, revision]); // eslint-disable-line react-hooks/exhaustive-deps
  const previewAgain = () => setRevision((n) => n + 1);
  return <ActionDialog<AppChangeResult> title={title} confirmLabel={title} reason="required" reauthScope="admin" danger={takesAway(change)}
    ready={!!plan && confirmed && !busy && !invalid}
    impact={<><b>{organization ? `${app.name} · ${organization.name}` : workspace ? `${app.name} · ${workspace.name}` : app.name}</b>
      <p>{tr(CHANGE_WARNINGS[key])}</p><p className="small muted">{tr(APP_PLATFORM_NOTE)}</p></>}
    onClose={onClose} onDone={onDone}
    run={async (reason) => {
      if (!plan || invalid) throw new ApiError(400, "admin_confirmation_required", "");
      const fingerprint = JSON.stringify([plan.preview_token, reason]);
      if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
      setBusy(true);
      try {
        return await post<AppChangeResult>(`/api/admin/apps/${encodeURIComponent(app.id)}/${change}`, {
          reason, preview_token: plan.preview_token, idempotency_key: attempt.current.key, ...body,
        });
      } catch (e) {
        if (e instanceof ApiError && !e.upstream && PREVIEW_REFUSALS.has(e.code)) { setInvalid(true); setConfirmed(false); }
        throw aboutApp(e);
      } finally { setBusy(false); }
    }}>
    {error && <div className="notice err col gap8" role="alert"><p>{error}</p>
      <button type="button" className="btn sm" onClick={previewAgain}>{tr("重新预览")}</button></div>}
    {!plan && !error && <p role="status">{tr("正在核对应用与操作影响…")}</p>}
    {plan && <>
      <AppChangeImpact change={change} plan={plan} />
      {invalid && <p className="notice warn" role="alert">{tr("这份预览已失效，请重新预览后再确认。")}</p>}
      <p className="muted sm">{organization ? tr("预览有效至 {time}。应用或组织变化后必须重新预览。", { time: fmtTime(plan.expires_at) })
        : tr("预览有效至 {time}。应用或工作空间变化后必须重新预览。", { time: fmtTime(plan.expires_at) })}</p>
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy || invalid}
        onChange={(e) => setConfirmed(e.target.checked)} />{tr("我已核对目标应用和上述影响。")}</label>
      <button type="button" className="btn sm" disabled={busy} onClick={previewAgain}>{tr("重新预览")}</button>
    </>}
  </ActionDialog>;
}

/** The live region and the notice inside it: present from the first render (a region inserted
 *  together with its text is not announced by many screen readers), focused when a change ends,
 *  because the button that opened the dialog may be gone or changed by then. Shared by the
 *  catalogue and its per-workspace and per-organization lists. */
export function useOutcome() {
  const [notice, setNotice] = useState("");
  const [focus, setFocus] = useState(0);
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => { if (focus) ref.current?.focus(); }, [focus]);
  const region = <div role="status" aria-live="polite">{notice && <p className="notice ok" ref={ref} tabIndex={-1}>{notice}</p>}</div>;
  return { region, say: (message: string) => { setNotice(message); setFocus((n) => n + 1); } };
}
