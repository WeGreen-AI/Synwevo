/** What the job page may show and offer, decided from the server's own job record.
 *
 *  Kept free of React and of imports so the rules can be tested with `node --test`: which states
 *  keep the page polling, when 重试 is something the server would accept, and when an output may
 *  honestly be called 已校验.
 *
 *  Keep ACTIVE in step with jobs.py (ACTIVE = queued, running, cancelling).
 */
export const ACTIVE_STATUSES = ["queued", "running", "cancelling"];
export const TERMINAL_STATUSES = ["completed", "completed_with_errors", "failed", "cancelled"];

export interface JobLike {
  status?: string | null;
  phase?: string | null;
  halt_reason?: string | null;
  cancel_requested?: boolean | null;
  counts?: Record<string, number> | null;
}

export const isActive = (j: JobLike | null | undefined) => !!j && ACTIVE_STATUSES.includes(j.status || "");

/** A job keeps changing while it is queued, running, being cancelled, or still being planned.
 *  `cancelling` used to end the poll, so the page froze at 取消中 with stale counts. A job that
 *  ended during planning (cancelled, halted) keeps phase=planning but no longer changes. */
export function shouldPoll(j: JobLike | null | undefined): boolean {
  if (!j) return false;
  return isActive(j) || (j.phase === "planning" && !TERMINAL_STATUSES.includes(j.status || ""));
}

/** Planning progress of a large batch, from counts.planned / counts.total (jobs.py). */
export function planning(j: JobLike | null | undefined): { planned: number; total: number; pct: number } | null {
  if (!j || j.phase !== "planning" || !shouldPoll(j)) return null;
  const c = j.counts || {};
  const total = c.total || 0, planned = c.planned || 0;
  return { planned, total, pct: total ? Math.min(100, Math.round((planned / total) * 100)) : 0 };
}

export function canCancel(j: JobLike | null | undefined): boolean {
  return !!j && (j.status === "queued" || j.status === "running") && !j.cancel_requested;
}

/** The server answers 409 job_active while the job is active and 409 nothing_to_retry when no
 *  item is failed or cancelled (and, for a job stopped while planning, nothing is left to plan),
 *  and 403 for a read-only member; offer the button only when none of those would happen. */
export function canRetry(j: JobLike | null | undefined, canWrite = true, failedOnly = false): boolean {
  if (!j || !canWrite || shouldPoll(j)) return false;
  const c = j.counts || {};
  if (failedOnly) return (c.failed || 0) > 0;
  const unplanned = j.phase === "planning" ? c.unplanned || Math.max(0, (c.total || 0) - (c.planned ?? c.total ?? 0)) : 0;
  return (c.failed || 0) + (c.cancelled || 0) + unplanned > 0;
}

export function progress(j: JobLike | null | undefined): { done: number; total: number; pct: number } {
  const c = j?.counts || {};
  const total = c.total || 0;
  const done = (c.success || 0) + (c.failed || 0) + (c.skipped || 0) + (c.cancelled || 0);
  return { done, total, pct: total ? Math.min(100, Math.round((done / total) * 100)) : 0 };
}

const HALT: Record<string, string> = {
  credential_revoked: "提交任务所用的凭据已被吊销或已过期",
  scope_withdrawn: "提交者已不再拥有写入权限",
  role_insufficient: "提交者在该工作空间的角色已不允许生成",
  not_a_member: "提交者已不是该工作空间的成员",
  account_unavailable: "提交者的账户已被停用或删除",
  entitlement_revoked: "提交者对 Synwevo Docs 的使用资格已被收回",
  no_entitlement: "提交者没有 Synwevo Docs 的使用资格",
  grant_revoked: "提交任务所用的授权已被撤销",
  wrong_audience: "提交任务所用的授权不是为 Synwevo Docs 签发的",
  workspace_mismatch: "提交者的凭据不属于该工作空间",
  workspace_unavailable: "工作空间当前不可用",
  workspace_frozen: "工作空间已被冻结",
  workspace_suspended: "工作空间已被暂停",
  app_disabled: "Synwevo Docs 已被平台停用",
  app_not_enabled_for_workspace: "该工作空间已不再启用 Synwevo Docs",
  unknown_principal: "无法确认提交者的身份",
};

/** Why a job stopped without finishing: its submitter lost access, or the data was restored
 *  from a backup while it was queued or running (restore cancels such jobs rather than resuming
 *  them, and sends no job event). Unknown reasons are shown verbatim. */
export function haltText(reason: string | null | undefined): string | null {
  if (!reason) return null;
  if (reason === "restored_from_backup") {
    return "任务已停止：数据从备份恢复时，这个任务还没有完成；恢复后不会自动继续，未完成的部分可以用「重试」重新生成。";
  }
  const prefix = "authorization_revoked:";
  if (reason.startsWith(prefix)) {
    const why = reason.slice(prefix.length);
    return `任务已停止：${HALT[why] || why || "提交者已失去权限"}。已生成的文件保留，不会再生成新的文件。`;
  }
  return `任务已停止：${reason}`;
}

export interface Verification { reopened?: boolean; checks?: number; missing?: unknown[]; engine?: string | null; error?: string; note?: string }

/** 已校验 only when values were actually compared. A PDF export of a DOCX template and every
 *  image output are reopened but not compared value by value (checks = 0); calling those
 *  「已校验」claimed a check that did not happen. */
export function verificationBadge(v: Verification | null | undefined): { label: string; tone: "ok" | "info" | "warn" | "err"; title: string } | null {
  if (!v) return null;
  if (v.error) return { label: "无法重新打开", tone: "err", title: v.error };
  const engine = v.engine || "对应程序";
  const missing = Array.isArray(v.missing) ? v.missing.length : 0;
  if (missing) return { label: `${missing} 个值未找到`, tone: "warn", title: `已用 ${engine} 重新打开，核对 ${v.checks || 0} 个值，其中 ${missing} 个未在文件中找到` };
  if ((v.checks || 0) > 0) return { label: "✓ 已校验", tone: "ok", title: `已用 ${engine} 重新打开并核对 ${v.checks} 个值` };
  if (v.reopened) return { label: "已重新打开", tone: "info", title: v.note || `已用 ${engine} 重新打开；此格式没有逐值核对` };
  return null;
}
