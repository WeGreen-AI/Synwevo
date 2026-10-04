/**
 * The platform's view of business-application runs (synwevo_identity.admin_runs): the shapes the
 * routes answer with, the words for their fixed identifiers, and the view state kept in the
 * address.
 *
 * Everything here is metadata. The routes never return an input, an output, an error message or a
 * name a person chose, so nothing on these pages can show one.
 */

export type Run = {
  id: string; op: string; app: string; op_version: number;
  workspace_id: string | null; workspace_name: string | null;
  user_id: string; user_email: string | null;
  via: string; principal_kind: string; client_id: string | null;
  delegation_kind: string | null; flow_run_id: string | null;
  correlation_id: string; parent_run_id: string | null; depth: number;
  status: string; error_code: string | null; object_kind: string | null; object_id: string | null;
  created_at: number; started_at: number | null; finished_at: number | null; updated_at: number;
  stalled: boolean;
};
export type RunDetail = { run: Run; related: Run[]; related_truncated: boolean; stale_after_s: number; generated_at: number };
export type RunSummary = {
  window_s: number; since: number; generated_at: number; stale_after_s: number;
  runs: { total: number; by_status: Record<string, number>; stalled: number;
    by_app: { app: string; total: number; failed: number; unknown: number }[] };
  flows: { finished: Record<string, number>; active: number; overdue: number; stalled: number; parked: Record<string, number> };
};
export type FlowRun = {
  id: string; flow_id: string; version: number; user_id: string; user_email: string | null;
  status: string; error_code: string | null; correlation_id: string; parent_run_id: string | null;
  depth: number; trigger_id: string | null; calls_used: number; deadline: number; due_at: number;
  created_at: number; updated_at: number; finished_at: number | null; leased: boolean; overdue: boolean;
  /** Due for longer than `stale_after_s` and no runner holds it: the runner is down or stuck. */
  stalled: boolean;
};
export type FlowStep = {
  position: number; status: string; attempt: number; checks: number; error_code: string | null;
  op_run_id: string | null; op: string | null; app: string | null; op_status: string | null;
  started_at: number; updated_at: number;
};
export type FlowEvent = {
  seq: number; type: string; status: string; attempt: number | null; op_run_id: string | null;
  error_code: string | null; fence: number; created_at: number;
};
export type FlowDetail = { run: FlowRun; steps: FlowStep[]; history: FlowEvent[]; history_truncated: boolean;
  stale_after_s: number; generated_at: number };

/** The states an operation run can be in (synwevo_core.capabilities.RUN_STATES), in the order the
 *  filter lists them. */
export const RUN_STATES = ["queued", "running", "waiting_input", "waiting_approval", "succeeded", "partial",
  "failed", "cancelled", "unknown"] as const;
/** A flow run's states (synwevo_core.flows.engine STATES: ACTIVE + PARKED + TERMINAL), in that
 *  order. */
export const FLOW_STATES = ["queued", "running", "waiting_input", "waiting_approval", "retrying", "paused", "blocked",
  "unknown", "succeeded", "partial", "failed", "cancelled"] as const;
/** Where the engine leaves a flow run until its owner resumes or cancels it (PARKED). */
export const FLOW_PARKED = ["paused", "blocked", "unknown"] as const;
export const VIA = ["ui", "api", "flow", "agent", "automation", "system"] as const;
export const WINDOWS_S = [3600, 86400, 604800, 2592000] as const;
export const DEFAULT_WINDOW_S = 86400;

/** Source sentences (translated where they are shown; runs.messages.ts). */
export const STATE_LABEL: Record<string, string> = {
  queued: "排队中", running: "执行中", waiting_input: "等待输入", waiting_approval: "等待审批",
  retrying: "等待重试", succeeded: "成功", partial: "部分成功", failed: "失败", cancelled: "已取消",
  unknown: "结果不明", paused: "已暂停", blocked: "受阻",
  // a flow step's own states (flow_steps.status)
  executing: "执行中", operation_wait: "等待操作结果",
};
/** What happened to a flow run (flow_history.type, written by the flow engine). */
export const HISTORY_LABEL: Record<string, string> = {
  started: "开始", advanced: "进入下一步", step_started: "步骤开始", step_completed: "步骤完成", step_failed: "步骤失败",
  waiting: "等待中", paused: "已暂停", resumed: "继续", cancelled: "已取消", policy_revoked: "授权已撤销",
};
export const VIA_LABEL: Record<string, string> = {
  ui: "界面", api: "API 客户端", flow: "流程", agent: "Agent", automation: "个人自动化", system: "系统",
};
export const WINDOW_LABEL: Record<number, string> = {
  3600: "最近 1 小时", 86400: "最近 24 小时", 604800: "最近 7 天", 2592000: "最近 30 天",
};
export const PRINCIPAL_LABEL: Record<string, string> = {
  user: "本人登录", delegated: "用户授权的客户端", service_account: "服务账户", api_key: "访问密钥",
  operator: "主机操作", unknown: "未记录",
};
export const DELEGATION_LABEL: Record<string, string> = {
  flow: "流程步骤", agent_conversation: "Agent 对话", oauth_client: "用户授权的客户端",
};
/** Product names are not translated; an application this page does not know is shown by its id.
 *  The keys are application ids (`apps.id`); the Docs one is quoted because it is that id and not
 *  a product name, which is how scripts/check_brand.py tells the two apart. */
export const APP_NAME: Record<string, string> = {
  forms: "Synwevo Forms", tables: "Synwevo Tables", dataprep: "Synwevo DataPrep", approvals: "Synwevo Approvals",
  extract: "Synwevo Extract", agent: "Synwevo Agent", "docweave": "Synwevo Docs", messages: "Synwevo Message",
};
export const appName = (app: string | null | undefined) => (app && APP_NAME[app]) || app || "—";

/** How a state is drawn: finished well, needs a look, still moving, or set aside by its owner
 *  (cancelled, paused). A blocked run waits for its owner after a refusal, so it needs a look. */
export function stateTone(status: string, flagged = false): "ok" | "err" | "warn" | "info" | "" {
  if (status === "failed") return "err";
  if (flagged || status === "unknown" || status === "partial" || status === "blocked") return "warn";
  if (status === "succeeded") return "ok";
  if (status === "cancelled" || status === "paused") return "";
  return "info";
}

/** A correlation id the caller chose is answered as a digest (admin_runs `correlation_token`):
 *  the page says so next to it, because it is not the text the caller knows it by. */
export const isCorrelationDigest = (id: string) => /^ext-[0-9a-f]{24}$/.test(id);

/** After a run's dialog closes, focus is back on the button that opened it (the shared Modal
 *  does that) — unless there was none (the run was opened by its link) or the list was read
 *  again meanwhile and the row is gone (a filter chosen inside the dialog). Then nothing holds
 *  focus, and the page puts it on its heading. `before` and `now` are the open run's id. */
export const closedWithoutFocus = (before: string, now: string, focusHeld: boolean): boolean =>
  !!before && !now && !focusHeld;

/** Whole seconds between two server times, as "3 秒" / "4 分钟" style input for the page's
 *  duration words; null while the run has not finished. */
export function elapsedSeconds(from: number | null | undefined, to: number | null | undefined): number | null {
  if (typeof from !== "number" || typeof to !== "number" || to < from) return null;
  return to - from;
}

// ---------------------------------------------------------------- view state in the address
/** The filters and the open run live in the address, so a reload, the Back button and a link
 *  from another page all show the same list. `tab`, `lang` and `from` belong to the shell. */
export const RUN_VIEW_KEYS = ["status", "app", "via", "workspace", "user", "q", "window", "run"] as const;
export const FLOW_VIEW_KEYS = ["status", "user", "flow", "q", "run"] as const;

export function readView<K extends string>(search: string, keys: readonly K[]): Record<K, string> {
  const params = new URLSearchParams(search);
  return Object.fromEntries(keys.map((key) => [key, params.get(key) || ""])) as Record<K, string>;
}

/** `search` with `changes` applied: an empty value removes the key, everything else the address
 *  already carries (the shell's `tab`, `lang`, `from`) stays. */
export function withView(search: string, changes: Record<string, string>): string {
  const params = new URLSearchParams(search);
  for (const [key, value] of Object.entries(changes)) {
    if (value) params.set(key, value); else params.delete(key);
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** A link to one of the two run pages with a view of its own: the shell's language and origin
 *  are kept, the other page's filters are not carried over. */
export function runsHref(tab: "app-runs" | "flow-runs", search: string, view: Record<string, string>): string {
  const before = new URLSearchParams(search), params = new URLSearchParams();
  for (const key of ["lang", "from"]) { const value = before.get(key); if (value) params.set(key, value); }
  params.set("tab", tab);
  for (const [key, value] of Object.entries(view)) if (value) params.set(key, value);
  return `/admin?${params.toString()}`;
}

export const windowOf = (value: string): number =>
  (WINDOWS_S as readonly number[]).includes(Number(value)) ? Number(value) : DEFAULT_WINDOW_S;
