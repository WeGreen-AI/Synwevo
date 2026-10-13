import { useEffect, useRef, useState } from "react";
import { ApiError, post, useSession } from "@synwevo/auth-client";
import { t as tr, fmtTime, useI18n } from "@synwevo/ui";
import type { PagedSelection } from "../components/selection";
import { NOT_IN_ROLE, permitted, type Held } from "./access";
import { ActionDialog } from "./ActionDialog";
import { BulkResultDialog, type BulkItemResult, type BulkResponse } from "./BulkResult";
import { ACCOUNT_STATUS, errText, resultText } from "./labels";
import "./bulk-accounts.messages";

/**
 * A18: suspend several accounts, or end every session of several accounts, from the platform
 * account list.
 *
 * A bulk act is a set of single-account acts bound by one preview and one confirmation. The
 * server reads the selected accounts and answers, for each one, whether the single-account rules
 * let it be acted on now and, if not, the refusal it would get (`code`); it signs a plan that
 * binds the eligible accounts and their current versions. The administrator reads that list,
 * writes a reason and re-authenticates inside `ActionDialog`; the batch is posted with the preview
 * token and one idempotency key per preview, so a retry after a lost answer replays the stored
 * result instead of acting again. The server applies each account in its own transaction and
 * answers every item; the result lists every one, and the accounts that failed can be previewed
 * again on their own — a confirmed batch is never re-run implicitly. Closing the preview before
 * confirming is the cancellation: nothing has been done.
 */
export type BulkOperation = "suspend" | "revoke_sessions";
/** The size of one batch (the server refuses more: `too_many`). */
export const BULK_LIMIT = 100;
export const BULK_OPERATION_LABELS: Record<BulkOperation, string> = {
  suspend: "停用所选账户", revoke_sessions: "撤销所选账户的全部会话",
};
/** The permission each operation is decided by (admin_bulk on the server). */
export const BULK_OPERATION_PERMISSION: Record<BulkOperation, string> = {
  suspend: "platform.users.manage", revoke_sessions: "platform.users.support",
};
/** What the operation does, said before anything happens. */
export const BULK_OPERATION_IMPACT: Record<BulkOperation, string> = {
  suspend: "所选账户将逐个停用：每个账户按单个停用的同一规则重新核对，并撤销其会话和应用令牌；不符合条件的账户不会被处理。",
  revoke_sessions: "所选账户的全部会话和应用令牌将逐个撤销：账户本身保持可用，本人可以重新登录；不符合条件的账户不会被处理。",
};
/** Shown in place of an operation the caller's platform role does not include. */
export const BULK_OPERATION_MISSING: Record<BulkOperation, string> = {
  suspend: "你的平台角色不能停用账户。", revoke_sessions: "你的平台角色不能撤销会话。",
};
export const BULK_NOTE = "每个账户单独执行、单独写入审计记录；不会授予管理员任何文档或私信内容访问权。";
const OPERATIONS: BulkOperation[] = ["suspend", "revoke_sessions"];

/** POST /api/admin/users/bulk-preview: one row per selected account, in the order sent. */
export type BulkPlanItem = { id: string; email: string | null; status: string | null; eligible: boolean;
  code: string | null; sessions: number; tokens: number };
export type BulkPlan = { operation: BulkOperation; scope: "platform"; risk: "high"; expires_at: number; preview_token: string;
  total: number; eligible: number; impact: { sessions: number; tokens: number }; items: BulkPlanItem[] };
/** POST /api/admin/users/bulk: every account, applied or refused, with the server's sentence. */
export type BulkAccountItem = BulkItemResult & { sessions_revoked?: number; tokens_revoked?: number };
export type BulkAccountResponse = BulkResponse & { operation_id: string; operation: BulkOperation; results: BulkAccountItem[] };

/** The refusals after which the server accepts only a new preview: the plan expired or no longer
 *  matches, or this preview's key was spent on a different request (an edited reason). */
const RENEW = new Set(["admin_preview_expired", "admin_preview_invalid", "admin_preview_stale", "idempotency_conflict"]);

/** One idempotency key per preview: a retry of the same preview replays the batch the server
 *  stored for it; a new preview is a new request with a new key. */
export type BulkAttempt = { token: string; key: string };
export function bulkAttempt(previous: BulkAttempt | null, token: string): BulkAttempt {
  return previous?.token === token ? previous : { token, key: crypto.randomUUID() };
}

/** "Select this page" for the account list: adds the rows on screen to what is chosen on other
 *  pages, or unticks only them. Its text is read on a wide screen and shown on a phone, where the
 *  column headers give way to cards (admin/admin.css). */
export function SelectAccountsPage({ selection, disabled }: { selection: PagedSelection; disabled?: boolean }) {
  useI18n();
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = selection.some; }, [selection.some]);
  // At the cap a page with nothing chosen cannot be added to; a partly chosen page can be cleared.
  const unticks = selection.all || (selection.full && selection.some);
  return <label className="pick-page">
    <input ref={ref} type="checkbox" checked={selection.all} disabled={disabled || (selection.full && !selection.all && !selection.some)}
      onChange={() => unticks ? selection.clearPage() : selection.selectAll()} />
    <span className="pick-page-text">{tr("选择本页的账户")}</span>
  </label>;
}

/** The selection bar: how many accounts are chosen, the operations the caller's platform role
 *  includes (and a hint for the ones it does not), and the way out. */
export function BulkAccountsBar({ held, count, full, onStart, onClear }: {
  held: Held; count: number; full?: boolean; onStart: (operation: BulkOperation) => void; onClear: () => void;
}) {
  useI18n();
  const offered = OPERATIONS.filter((op) => !permitted(held, BULK_OPERATION_PERMISSION[op]).disabled);
  const missing = OPERATIONS.filter((op) => !offered.includes(op));
  return <div className="acct-bulk" role="region" aria-label={tr("批量账户操作")}>
    <span className="count" role="status">{tr("已选 {count} 个账户", { count })}</span>
    {offered.map((op) => <button key={op} type="button" className="btn danger sm" onClick={() => onStart(op)}>{tr(BULK_OPERATION_LABELS[op])}</button>)}
    <button type="button" className="btn ghost sm" onClick={onClear}>{tr("清除选择")}</button>
    {offered.length === 0 ? <span className="muted small">{tr(NOT_IN_ROLE)}</span>
      : missing.map((op) => <span key={op} className="muted small">{tr(BULK_OPERATION_MISSING[op])}</span>)}
    <span className="muted small">{tr("翻页或更换筛选时，已选账户保留。")}</span>
    {full && <span className="small" role="note">{tr("一次最多选择 {limit} 个账户；已达到上限。", { limit: BULK_LIMIT })}</span>}
  </div>;
}

/** One account's line in the preview: what would happen to it, or why nothing will. */
export function itemEligibility(item: BulkPlanItem): string {
  return item.eligible ? tr("可以执行：{sessions} 个会话，{tokens} 个应用令牌", { sessions: item.sessions, tokens: item.tokens })
    : tr("不处理：{reason}", { reason: resultText(item.code || "", "") });
}

/** The loaded preview: the totals over the eligible accounts and every selected account with its
 *  state. Pure, so it can be rendered on its own (tests/admin-bulk-accounts.test.mjs). */
export function BulkAccountPreview({ operation, plan, selfId }: { operation: BulkOperation; plan: BulkPlan; selfId?: string }) {
  useI18n();
  const refused = plan.total - plan.eligible;
  const self = !!selfId && operation === "revoke_sessions" && plan.items.some((i) => i.id === selfId && i.eligible);
  return <>
    <p><b>{tr("{eligible} / {total} 个账户可以执行", { eligible: plan.eligible, total: plan.total })}</b></p>
    {plan.eligible > 0 ? <ul>
      <li>{tr("符合条件的账户合计：{sessions} 个会话、{tokens} 个应用令牌将被撤销。", { sessions: plan.impact.sessions, tokens: plan.impact.tokens })}</li>
      {operation === "suspend" && <li>{tr("正在运行的任务和连接会在下一次权限检查时停止；已经生成的数据保留。")}</li>}
    </ul> : <p className="notice warn" role="note">{tr("所选账户都不符合条件，没有可以执行的账户。")}</p>}
    {plan.eligible > 0 && refused > 0 && <p>{tr("{count} 个账户不符合条件，不会被处理；原因见下方列表。", { count: refused })}</p>}
    {self && <p className="notice warn" role="note">{tr("所选账户包含你自己的账户，完成后可能需要重新登录或离开平台管理页。")}</p>}
    {/* Up to a hundred lines: the list scrolls on its own and can be scrolled from the keyboard. */}
    <ul className="bulkres bulk-preview" tabIndex={0} aria-label={tr("所选账户及其核对结果")}>
      {plan.items.map((item) => <li key={item.id} className={item.eligible ? "ok" : "err"}>
        <span className="mark" aria-hidden="true">{item.eligible ? "✓" : "✕"}</span>
        <span className="grow">
          <span className="who">{item.email || item.id}</span>
          <span className="what">{(item.status && ACCOUNT_STATUS[item.status]) || item.status || "—"} · {itemEligibility(item)}</span>
        </span>
      </li>)}
    </ul>
  </>;
}

/** The confirmation around a preview the caller holds: confirmable only once the preview is loaded,
 *  still valid and has at least one eligible account. */
export function BulkAccountConfirm({ operation, count, plan, error, invalid, busy, selfId, onPreviewAgain, run, onClose, onDone }: {
  operation: BulkOperation; count: number; plan: BulkPlan | null; error: string; invalid: boolean; busy: boolean; selfId?: string;
  onPreviewAgain: () => void; run: (reason: string) => Promise<BulkAccountResponse>;
  onClose: () => void; onDone: (response: BulkAccountResponse) => void;
}) {
  useI18n();
  const title = tr(BULK_OPERATION_LABELS[operation]);
  return <ActionDialog<BulkAccountResponse> title={title} confirmLabel={title} reason="required" reauthScope="admin" danger
    ready={!!plan && plan.eligible > 0 && !invalid && !busy}
    impact={<><b>{tr("已选 {count} 个账户", { count })}</b><p>{tr(BULK_OPERATION_IMPACT[operation])}</p><p className="small muted">{tr(BULK_NOTE)}</p></>}
    run={run} onClose={onClose} onDone={onDone}>
    {error && <div className="notice err col gap8" role="alert"><p>{error}</p>
      <div><button type="button" className="btn sm" onClick={onPreviewAgain}>{tr("重新预览")}</button></div></div>}
    {!plan && !error && <p role="status">{tr("正在核对所选账户与操作影响…")}</p>}
    {plan && <>
      <BulkAccountPreview operation={operation} plan={plan} selfId={selfId} />
      {invalid && <p className="notice warn" role="alert">{tr("这份预览已失效，请重新预览后再确认。")}</p>}
      <p className="muted sm">{tr("预览有效至 {time}。账户变化后必须重新预览。", { time: fmtTime(plan.expires_at) })}</p>
      <div><button type="button" className="btn sm" disabled={busy} onClick={onPreviewAgain}>{tr("重新预览")}</button></div>
    </>}
  </ActionDialog>;
}

const counted = (v: unknown) => typeof v === "number" && Number.isFinite(v);
/** What a success did to one account (sessions and tokens revoked), or that it was already done. */
export function bulkItemDetail(operation: BulkOperation, r: BulkAccountItem): string | undefined {
  if (r.code === "already_applied") return tr("此前已完成，本次未重复执行。");
  if (!counted(r.sessions_revoked) || !counted(r.tokens_revoked)) return undefined;
  const counts = { sessions: r.sessions_revoked!, tokens: r.tokens_revoked! };
  return operation === "suspend" ? tr("已停用，撤销 {sessions} 个会话和 {tokens} 个应用令牌", counts)
    : tr("已撤销 {sessions} 个会话和 {tokens} 个应用令牌", counts);
}

/** Every account of the batch, failures first, and a new preview of only the failed ones. */
export function BulkAccountResult({ operation, response, labelOf, onClose, onRetryFailed }: {
  operation: BulkOperation; response: BulkAccountResponse; labelOf: (id: string) => string;
  onClose: () => void; onRetryFailed: (ids: string[]) => void;
}) {
  useI18n();
  return <BulkResultDialog title={BULK_OPERATION_LABELS[operation]} response={response} labelOf={labelOf}
    detailOf={(r) => bulkItemDetail(operation, r as BulkAccountItem)} onClose={onClose}
    retryFailed={{ label: tr("重新预览失败的账户"), run: onRetryFailed }} />;
}

/** The whole act: preview on open, confirm, result; and a new preview of the failed accounts from
 *  the result. `onDone` is called after every completed batch (the list reloads), `onClose` when
 *  the reader leaves. */
export function BulkAccountDialog({ operation, userIds, onClose, onDone }: {
  operation: BulkOperation; userIds: string[]; onClose: () => void; onDone: (response: BulkAccountResponse) => void;
}) {
  useI18n();
  const { me } = useSession();
  // A round is one preview-and-confirm over a set of accounts; a retry of the failed ones is the next.
  const [round, setRound] = useState({ ids: userIds, n: 0 });
  const [plan, setPlan] = useState<BulkPlan | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  // The server refused the preview the dialog holds; only a new preview can be confirmed.
  const [invalid, setInvalid] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ response: BulkAccountResponse; names: Record<string, string> } | null>(null);
  const attempt = useRef<BulkAttempt | null>(null);
  useEffect(() => {
    let live = true;
    setPlan(null); setError(""); setInvalid(false); attempt.current = null;
    post<BulkPlan>("/api/admin/users/bulk-preview", { operation, user_ids: round.ids })
      .then((next) => { if (live) setPlan(next); })
      .catch((e) => { if (live) setError(errText(e)); });
    return () => { live = false; };
  }, [operation, round, revision]);

  if (outcome) return <BulkAccountResult operation={operation} response={outcome.response}
    labelOf={(id) => outcome.names[id] || id} onClose={onClose}
    onRetryFailed={(ids) => { setOutcome(null); setRound((r) => ({ ids, n: r.n + 1 })); }} />;

  // Keyed by round: the failed accounts' preview is a new act, with its own reason.
  return <BulkAccountConfirm key={round.n} operation={operation} count={round.ids.length} plan={plan} error={error}
    invalid={invalid} busy={busy} selfId={me?.id} onPreviewAgain={() => setRevision((n) => n + 1)} onClose={onClose}
    run={async (reason) => {
      if (!plan || invalid || plan.eligible === 0) throw new ApiError(400, "admin_confirmation_required", "");
      attempt.current = bulkAttempt(attempt.current, plan.preview_token);
      setBusy(true);
      try {
        return await post<BulkAccountResponse>("/api/admin/users/bulk", {
          operation, preview_token: plan.preview_token, idempotency_key: attempt.current.key, reason,
        });
      } catch (e) {
        if (e instanceof ApiError && !e.upstream && RENEW.has(e.code)) setInvalid(true);
        throw e;
      } finally { setBusy(false); }
    }}
    onDone={(response) => {
      setOutcome({ response, names: Object.fromEntries((plan?.items || []).map((i) => [i.id, i.email || i.id])) });
      onDone(response);
    }} />;
}
