import { Fragment, useEffect, useRef, useState } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { t as tr, fmtTime, hasMessage, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { errText } from "./labels";
import "./organization-lifecycle.messages";

/**
 * A35: the platform-scope organization lifecycle (freeze, unfreeze, soft-delete, recover) from
 * the administration page, for a platform administrator holding `platform.orgs.manage` — member
 * of the organization or not.
 *
 * Same confirmed-operation protocol as the account status dialog (A14): the server first answers
 * a signed preview bound to this actor, action, organization and the organization's current
 * version; the administrator reads the impact, writes a reason and re-authenticates inside
 * `ActionDialog`; the transition is posted with the preview token and one idempotency key per
 * attempt, so a retry after a lost answer replays the same request instead of making a second one.
 * A preview the server no longer accepts (expired, stale, invalid) asks for a new preview, which
 * also starts a new key.
 */
export type LifecycleAction = "freeze" | "unfreeze" | "delete" | "recover";
export type LifecycleTarget = { id: string; name: string; status: string; deleted_at: number | null;
  recover_until: number | null; previous_status: string | null; version: number };
export type LifecycleImpactCounts = { members: number; workspaces: number; sessions: number; tokens: number;
  invitations: number; approvals: number; running_jobs: number };
/** POST /api/admin/organizations/{id}/preview */
export type LifecyclePlan = { action: LifecycleAction; scope: "platform"; risk: "high"; target: LifecycleTarget;
  result_status: string; impact: LifecycleImpactCounts; expires_at: number; preview_token: string };
/** POST /api/admin/organizations/{id}/{action}; the revoked counts are 0 or absent for unfreeze and recover. */
export type LifecycleResult = { ok: true; operation_id: string; state: "succeeded"; transition: LifecycleAction;
  organization: LifecycleTarget; sessions_revoked?: number; tokens_revoked?: number; invitations_revoked?: number; approvals_revoked?: number };

export const LIFECYCLE_ACTION_LABELS: Record<LifecycleAction, string> = { freeze: "冻结组织", unfreeze: "解冻组织", delete: "删除组织", recover: "恢复组织" };
export const ORGANIZATION_STATUS_LABELS: Record<string, string> = { active: "正常", frozen: "已冻结", deleted: "已删除" };
/** What each action says before anything happens: nothing is deleted at once, freeze and delete
 *  take access away on the next request, delete opens a seven-day window, unfreeze and recover
 *  revoke nothing and bring nothing revoked back. */
export const LIFECYCLE_WARNINGS: Record<LifecycleAction, string> = {
  freeze: "冻结不会删除任何内容：组织及其工作空间停止应用访问，成员在下一次请求时失去访问；解冻后按原有成员关系恢复。",
  delete: "删除不会立即清除任何内容：组织进入七天恢复期，期内可以恢复到删除前的状态；成员在下一次请求时失去访问。",
  unfreeze: "解冻不撤销任何东西：组织按原有成员关系恢复可用；之前撤销的会话、令牌和邀请不会恢复。",
  recover: "恢复不撤销任何东西：组织回到删除前的状态；之前撤销的会话、令牌和邀请不会恢复。",
};
export const PLATFORM_NOTE = "此操作由平台执行并写入审计记录；不会给任何平台管理员带来成员身份、资格或业务文档访问。";
/** The transitions the platform path offers from each status. `recover` is offered for a
 *  deleted organization and disabled once its window has closed (the server refuses it too).
 *  Shared by the organizations list and the organization detail (OrganizationDetail.tsx). */
export function lifecycleActions(status: string): LifecycleAction[] {
  return status === "deleted" ? ["recover"] : [status === "active" ? "freeze" : "unfreeze", "delete"];
}
export const recoverable = (org: { recover_until: number | null }, now = Date.now()) => !!org.recover_until && org.recover_until * 1000 > now;
/** Freeze and delete revoke the organization's authority (sessions, tokens, invitations, approvals);
 *  unfreeze and recover revoke nothing. */
export const revokes = (action: LifecycleAction) => action === "freeze" || action === "delete";
/** The preview's counts: the key the server sends, its label, what the count means when the
 *  action revokes, and what it means when the action restores. */
export const IMPACT_ROWS: ReadonlyArray<readonly [keyof LifecycleImpactCounts, string, string, string]> = [
  ["members", "成员", "成员在下一次请求时失去访问；成员关系保留。", "成员按原有成员关系恢复访问。"],
  ["workspaces", "工作空间", "所属工作空间停止应用访问；内容不会删除，也不会提供给管理员。", "所属工作空间恢复可用；此操作不改变其内容。"],
  ["sessions", "会话", "这些会话将被撤销。", "现有会话不受影响；之前撤销的会话不会恢复。"],
  ["tokens", "应用令牌", "这些应用令牌将被撤销。", "现有应用令牌不受影响；之前撤销的令牌不会恢复。"],
  ["invitations", "待接受的邀请", "这些尚未接受的邀请将被撤回。", "现有邀请不受影响；之前撤回的邀请不会恢复。"],
  ["approvals", "API 凭据批准", "这些 API 凭据批准将被撤销；凭据本身不会删除。", "现有 API 凭据批准不受影响；之前撤销的批准不会恢复。"],
  ["running_jobs", "运行中的任务", "正在运行的任务会在下一次权限检查时停止；已生成的数据保留。", "正在运行的任务不受影响。"],
];
/** A recovery returns the organization to its status before deletion: one deleted while frozen
 *  comes back frozen, so its members and workspaces stay without access until it is unfrozen. */
export const FROZEN_AFTER_RECOVERY: Partial<Record<keyof LifecycleImpactCounts, string>> = {
  members: "组织恢复后仍为冻结状态：成员在解冻前仍无法访问；成员关系保留。",
  workspaces: "所属工作空间在解冻前仍停止应用访问；此操作不改变其内容。",
};
export const FROZEN_RECOVERY_NOTE = "恢复后组织仍为冻结状态，需要另行解冻才能恢复访问。";
/** What one count means for this act, judged by where the organization ends up, not only by the
 *  act's name: freeze and delete revoke; a recovery that ends frozen restores nothing to members. */
export function impactMeaning(action: LifecycleAction, key: keyof LifecycleImpactCounts, resultStatus: string,
                              stop: string, restore: string): string {
  if (revokes(action)) return stop;
  if (action === "recover" && resultStatus === "frozen") return FROZEN_AFTER_RECOVERY[key] ?? restore;
  return restore;
}
/** The refusals after which the server accepts only a new preview (admin_operations). */
const PREVIEW_REFUSALS = new Set(["admin_preview_expired", "admin_preview_stale", "admin_preview_invalid"]);
const statusLabel = (status: string) => ORGANIZATION_STATUS_LABELS[status] ? tr(ORGANIZATION_STATUS_LABELS[status]) : status;

/** `admin_preview_stale` sent with only a code, or with a sentence no catalogue holds, would be
 *  worded by labels.ts for an account; here the object that changed is the organization. The
 *  server's own catalogued sentence is kept as it is; so are the code, status, detail and request id. */
function aboutOrganization(e: ApiError): ApiError {
  if (hasMessage(e.originalMessage)) return e;
  const next = new ApiError(e.status, e.code, "组织的状态、成员或工作空间已变化，请重新预览影响。", e.detail, e.upstream);
  next.requestId = e.requestId; next.retryAfter = e.retryAfter;
  return next;
}

/** The loaded preview: the target, the status change, and every count with its meaning. Pure, so
 *  it can be rendered on its own (tests/admin-organization-lifecycle.test.mjs). */
export function OrganizationLifecycleImpact({ action, plan }: { action: LifecycleAction; plan: LifecyclePlan }) {
  useI18n();
  const target = plan.target;
  const deleted = target.status === "deleted" ? [
    target.deleted_at ? tr("删除时间：{time}", { time: fmtTime(target.deleted_at) }) : "",
    target.recover_until ? tr("恢复期限：{time}", { time: fmtTime(target.recover_until) }) : "",
    target.previous_status ? tr("删除前状态：{status}", { status: statusLabel(target.previous_status) }) : "",
  ].filter(Boolean) : [];
  return <>
    <p><span className="muted">{tr("目标组织")}</span> <b>{target.name}</b> <span className="mono small muted">{target.id}</span></p>
    <p>{tr("状态：{before} → {after}", { before: statusLabel(target.status), after: statusLabel(plan.result_status) })}</p>
    {deleted.length > 0 && <p className="small muted">{deleted.join(" · ")}</p>}
    {action === "recover" && plan.result_status === "frozen" && <p className="notice" role="note">{tr(FROZEN_RECOVERY_NOTE)}</p>}
    <dl className="kv" aria-label={tr("操作影响")}>
      {IMPACT_ROWS.map(([key, label, stop, restore]) => <Fragment key={key}>
        <dt>{tr(label)}</dt><dd><b>{plan.impact[key]}</b> · {tr(impactMeaning(action, key, plan.result_status, stop, restore))}</dd>
      </Fragment>)}
    </dl>
  </>;
}

export function OrganizationLifecycleDialog({ org, action, onClose, onDone }: {
  org: { id: string; name: string; status: string };
  action: LifecycleAction; onClose: () => void; onDone: (result: LifecycleResult) => void;
}) {
  useI18n();
  const title = tr(LIFECYCLE_ACTION_LABELS[action]);
  const [plan, setPlan] = useState<LifecyclePlan | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [confirmed, setConfirmed] = useState(false);
  // The server refused the preview the dialog holds; only a new preview can be confirmed.
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  // One idempotency key per (preview token, reason): a retry of the same request replays it; a
  // new preview or an edited reason is a new request with a new key (as AccountStatusDialog).
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null);
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setConfirmed(false); setInvalid(false); attempt.current = null;
    post<LifecyclePlan>(`/api/admin/organizations/${encodeURIComponent(org.id)}/preview`, { action })
      .then((next) => { if (live) setPlan(next); })
      .catch((e) => { if (live) setError(errText(e)); });
    return () => { live = false; };
  }, [org.id, action, revision]);
  const previewAgain = () => setRevision((n) => n + 1);
  return <ActionDialog<LifecycleResult> title={title} confirmLabel={title} reason="required" reauthScope="admin" danger={revokes(action)}
    ready={!!plan && confirmed && !busy && !invalid}
    impact={<><b>{org.name}</b><p>{tr(LIFECYCLE_WARNINGS[action])}</p><p className="small muted">{tr(PLATFORM_NOTE)}</p></>}
    onClose={onClose} onDone={onDone}
    run={async (reason) => {
      if (!plan || invalid) throw new ApiError(400, "admin_confirmation_required", "");
      const fingerprint = JSON.stringify([plan.preview_token, reason]);
      if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() };
      setBusy(true);
      try {
        return await post<LifecycleResult>(`/api/admin/organizations/${encodeURIComponent(org.id)}/${action}`, {
          reason, preview_token: plan.preview_token, idempotency_key: attempt.current.key,
        });
      } catch (e) {
        if (e instanceof ApiError && !e.upstream && PREVIEW_REFUSALS.has(e.code)) {
          setInvalid(true); setConfirmed(false);
          if (e.code === "admin_preview_stale") throw aboutOrganization(e);
        }
        throw e;
      } finally { setBusy(false); }
    }}>
    {error && <div className="notice err col gap8" role="alert"><p>{error}</p>
      <button type="button" className="btn sm" onClick={previewAgain}>{tr("重新预览")}</button></div>}
    {!plan && !error && <p role="status">{tr("正在核对组织与操作影响…")}</p>}
    {plan && <>
      <OrganizationLifecycleImpact action={action} plan={plan} />
      {invalid && <p className="notice warn" role="alert">{tr("这份预览已失效，请重新预览后再确认。")}</p>}
      <p className="muted sm">{tr("预览有效至 {time}。组织变化后必须重新预览。", { time: fmtTime(plan.expires_at) })}</p>
      <label className="check"><input type="checkbox" checked={confirmed} disabled={busy || invalid}
        onChange={(e) => setConfirmed(e.target.checked)} />{tr("我已核对目标组织和上述影响。")}</label>
      <button type="button" className="btn sm" disabled={busy} onClick={previewAgain}>{tr("重新预览")}</button>
    </>}
  </ActionDialog>;
}
