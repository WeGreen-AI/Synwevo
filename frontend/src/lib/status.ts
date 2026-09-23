/** The single place job/item status is turned into Chinese for the UI.
 *
 *  The machine API keeps its stable English enums — nothing here changes the wire format.
 *  Three divergent copies of this map used to exist (JobPage, Workspace, and none at all in
 *  Home, which printed the raw enum into a Chinese page).  They also disagreed with the
 *  backend: `interrupted` was never written by any code path, and `cancelling` — which
 *  jobs.py does write — was missing, so a job being cancelled fell through to the raw string.
 *
 *  Keep these keys in step with jobs.py (TERMINAL / ACTIVE) and job_items.status.
 */
export type Tone = "ok" | "info" | "warn" | "err" | "";

export const JOB_STATUS: Record<string, [label: string, tone: Tone]> = {
  queued: ["排队中", "info"],
  running: ["生成中", "info"],
  cancelling: ["取消中", "warn"],
  completed: ["已完成", "ok"],
  completed_with_errors: ["部分成功", "warn"],
  cancelled: ["已取消", "warn"],
  failed: ["任务失败", "err"],
};

export const ITEM_STATUS: Record<string, [label: string, tone: Tone]> = {
  pending: ["等待", ""],
  running: ["生成中", "info"],
  success: ["成功", "ok"],
  failed: ["失败", "err"],
  skipped: ["已跳过", "warn"],
  cancelled: ["已取消", "warn"],
};

/** Unknown values are shown verbatim rather than hidden, so a new backend status is visible
 *  as a gap to fix instead of silently rendering as "已完成". */
export function jobStatus(s: string | undefined | null): [string, Tone] {
  return JOB_STATUS[s || ""] || [s || "未知", "warn"];
}

export function itemStatus(s: string | undefined | null): [string, Tone] {
  return ITEM_STATUS[s || ""] || [s || "未知", "warn"];
}

/** Sample-rule modes, including per_block which the homepage was missing. */
export const RULE_MODE: Record<string, string> = {
  per_row: "每行一份",
  per_file: "每文件一份",
  per_group: "每组一份",
  per_block: "每块一份",
};

export const ruleMode = (m: string | undefined | null): string => (m ? RULE_MODE[m] || m : "未定义样本");
