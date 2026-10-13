import { useEffect, useId, useRef, useState } from "react";
import { ApiError, get, post, useSession } from "@synwevo/auth-client";
import { fmtTime, hasMessage, t as tr, useI18n, useToast } from "@synwevo/ui";
import { useLoad } from "../lib/hooks";
import type { Held } from "./access";
import { ActionDialog } from "./ActionDialog";
import { ACCOUNT_STATUS, errText } from "./labels";
import "./second-factor.messages";

/**
 * A26: the platform-controlled reset of a person's lost second factor — for someone who lost both
 * the authenticator and every recovery code and so cannot sign in with a password any more.
 *
 * The account detail shows the factors as counts and states only (never a seed, a code or a
 * credential id), the pending reset and the recent ones. A super administrator (the only role
 * holding `platform.users.recover`) confirms the person's identity outside Synwevo, then asks for
 * the reset through the same confirmed-operation protocol as the account status dialog: the server
 * answers a signed preview of what will be removed (re-previewed when "also remove passkeys"
 * changes), the administrator names how the identity was confirmed, writes a reason and
 * re-authenticates inside `ActionDialog`; the request carries the preview token and one
 * idempotency key per preview, so a retry after a lost answer replays it. A preview the server no
 * longer accepts asks for a new one, which also starts a new key.
 *
 * Nothing is removed at once: the reset takes effect 72 hours later (the server's
 * `SECOND_FACTOR_RESET_DELAY_S`), the person is mailed at their own address with a cancel link
 * (`/account/second-factor-reset`, pages/account/SecondFactorReset.tsx), and either the person or a
 * super administrator can cancel it until then. The server re-checks every rule on every request.
 */

/** Mirrors the server's `SECOND_FACTOR_RESET_DELAY_S` (72 × 3600); the exact time comes from the preview. */
export const RESET_DELAY_HOURS = 72;
export const RESET_METHODS = ["in_person", "video_call", "verified_document", "other"] as const;
export type ResetMethod = typeof RESET_METHODS[number];
/** How the administrator confirmed the person's identity, before asking (the request's `method`). */
export const RESET_METHOD_LABELS: Record<string, string> = {
  in_person: "当面核验", video_call: "视频通话核验", verified_document: "核验了证件", other: "其他（在理由中说明）",
};
export const RESET_STATUS_LABELS: Record<string, string> = { pending: "待生效", cancelled: "已取消", completed: "已完成", void: "已作废" };
/** Why a due reset was not carried out (the sweeper's `void_reason`, admin_second_factor._void_code). */
export const VOID_REASON_LABELS: Record<string, string> = {
  account_deleted: "账户已删除", account_state_conflict: "账户已不是正常状态", target_is_platform_admin: "账户已持有平台权限",
  application_identity: "账户是应用身份", account_suspended: "期间账户被停用", email_unverified: "账户邮箱未验证",
  email_changed: "期间账户邮箱已更改", authority_changed: "申请人已不再持有重置权限",
  announcement_undelivered: "通知邮件未能送达本人", nothing_to_reset: "到期时已没有可重置的第二因素",
  factors_changed: "期间第二因素有变化或被使用过（说明本人仍持有它）",
  // a restore from a backup voids every reset that was pending (it may predate a cancellation)
  restored_invalidated: "从备份恢复后作废",
};
/** The announcement mail's state (`pending_reset.announcement_status`, the outbox row). */
export const ANNOUNCEMENT_LABELS: Record<string, string> = {
  queued: "等待发送", sending: "正在发送", sent: "已送达发信服务", failed: "发送失败", dropped: "未发送",
};

/** GET /api/admin/users/{uid}/second-factor: counts and states only. */
export type PendingReset = { id: string; created_at: number; due_at: number; method: string;
  requested_by_email: string | null; include_passkeys: boolean;
  /** The completion waits for the announcement: effective once it is sent and 72 hours have
   *  passed since then (`effective_due_at`, null until it is sent). */
  announcement_status?: string | null; announcement_sent_at?: number | null; effective_due_at?: number | null };
export type ResetHistoryItem = { id: string; status: string; created_at: number; due_at: number; method: string;
  completed_at?: number | null; cancelled_at?: number | null; void_reason?: string | null };
export type SecondFactorState = { totp: boolean; recovery_remaining: number; passkeys: number;
  pending_reset: PendingReset | null; history: ResetHistoryItem[] };
/** POST /api/admin/users/{uid}/second-factor-reset/preview */
export type ResetPlan = { action: string; scope: string; risk: "high"; target: { id: string; email: string; status: string };
  factors: { totp: boolean; recovery_remaining: number; passkeys: number };
  effect: { removes_totp: boolean; removes_recovery_codes: boolean; removes_passkeys: number; sessions: number; tokens: number };
  due_at: number; expires_at: number; preview_token: string };
/** POST /api/admin/users/{uid}/second-factor-reset */
export type ResetResult = { ok: true; operation_id: string; reset: { id: string; due_at: number; status: "pending" } };
/** What the account detail already says about the person that decides whether a reset is offered. */
export type ResetTarget = { id: string; email: string; status: string; platform_access: boolean; application_identity: boolean };

export const RESET_DELAY_NOTE = "重置不会立即执行：确认后 {hours} 小时才生效。期间本人会收到附取消链接的邮件，可以随时取消。";
export const RESET_PLATFORM_NOTE = "这是平台的恢复操作，会写入审计记录；全程不读取、不显示动态验证码种子或恢复码，也不以本人身份更改任何凭据。";
export const RESET_MAIL_NOTE = "确认后会立即给本人的邮箱发送通知：由平台管理员申请、何时生效，并附取消链接。本人在生效前取消，则什么都不会改变。";
export const CANCEL_IMPACT = "{email}：将于 {time} 生效的第二因素重置会被取消；动态验证码、恢复码和安全密钥保持不变。本人会收到邮件和安全通知。";
/** The reset's refusals worded for this act where the shared code map words them for another one. */
export const RESET_PLATFORM_ADMIN = "该账户持有平台权限：管理员找回访问走最高管理员交接流程，不能在这里重置第二因素";
export const RESET_STALE = "账户的第二因素或状态已变化，请重新预览影响。";

const DASH = "—";
const methodText = (method: string) => RESET_METHOD_LABELS[method] ? tr(RESET_METHOD_LABELS[method]) : method;
const statusText = (status: string) => RESET_STATUS_LABELS[status] ? tr(RESET_STATUS_LABELS[status]) : status;

export const hasResettable = (s: SecondFactorState) => s.totp || s.recovery_remaining > 0 || s.passkeys > 0;

/** Why no reset is offered for this account, said before the click (the preview's refusals), or
 *  null when one can be asked for. A pending reset is shown on its own. */
export function resetUnavailable(s: SecondFactorState, target: ResetTarget, selfId?: string): string | null {
  if (target.application_identity) return "请通过应用身份管理操作该账户。";
  if (selfId && target.id === selfId) return "不能为自己的账户申请重置第二因素。";
  if (target.platform_access) return RESET_PLATFORM_ADMIN;
  if (target.status !== "active") return "只能为状态正常的账户重置第二因素。";
  if (!hasResettable(s)) return "该账户没有动态验证码、恢复码或安全密钥，无需重置。";
  return null;
}

/** When a reset in the history took effect, ended, or will take effect. */
export function historyEnd(item: ResetHistoryItem): string {
  if (item.status === "completed") return tr("{time} 完成", { time: fmtTime(item.completed_at ?? item.due_at) });
  if (item.status === "cancelled") return item.cancelled_at ? tr("{time} 取消", { time: fmtTime(item.cancelled_at) }) : DASH;
  if (item.status === "void") {
    const reason = item.void_reason ? (VOID_REASON_LABELS[item.void_reason] ? tr(VOID_REASON_LABELS[item.void_reason]) : item.void_reason) : "";
    return reason ? tr("到期时未执行（{time}）：{reason}", { time: fmtTime(item.due_at), reason }) : tr("到期时未执行（{time}）", { time: fmtTime(item.due_at) });
  }
  return tr("{time} 生效", { time: fmtTime(item.due_at) });
}

/** The account detail's second-factor section. Pure, so it can be rendered on its own
 *  (tests/admin-second-factor-reset.test.mjs); the buttons appear only with `platform.users.recover`
 *  and a handler, and the reset only when the server's preview could allow it. */
export function SecondFactorSection({ state: s, target, held, selfId, onReset, onCancel }: {
  state: SecondFactorState; target: ResetTarget; held?: Held; selfId?: string;
  onReset?: () => void; onCancel?: (pending: PendingReset) => void;
}) {
  useI18n();
  const mayRecover = !!held?.has("platform.users.recover");
  const pending = s.pending_reset;
  const unavailable = resetUnavailable(s, target, selfId);
  return <section className="col gap8"><h3 className="secttl">{tr("第二因素")}</h3>
    <dl className="facts">
      <dt>{tr("动态验证码")}</dt><dd>{s.totp ? tr("已登记") : tr("未登记")}</dd>
      <dt>{tr("剩余恢复码")}</dt><dd>{s.recovery_remaining}</dd>
      <dt>{tr("已登记的安全密钥")}</dt><dd>{s.passkeys}</dd>
    </dl>
    {pending ? <div className="notice warn col gap8" role="note">
      <b>{tr("有一次第二因素重置待生效")}</b>
      <dl className="facts">
        <dt>{tr("生效时间")}</dt><dd>{pending.effective_due_at ? fmtTime(pending.effective_due_at)
          : tr("通知邮件送达 {hours} 小时后（最早 {time}）", { hours: RESET_DELAY_HOURS, time: fmtTime(pending.due_at) })}</dd>
        {pending.announcement_status && <><dt>{tr("通知邮件")}</dt><dd>{ANNOUNCEMENT_LABELS[pending.announcement_status]
          ? tr(ANNOUNCEMENT_LABELS[pending.announcement_status]) : pending.announcement_status}
          {pending.announcement_sent_at ? <span className="cell-note">{fmtTime(pending.announcement_sent_at)}</span> : null}</dd></>}
        <dt>{tr("移除范围")}</dt><dd>{pending.include_passkeys ? tr("动态验证码、恢复码和安全密钥") : tr("动态验证码和恢复码（保留安全密钥）")}</dd>
        <dt>{tr("核验方式")}</dt><dd>{methodText(pending.method)}</dd>
        <dt>{tr("申请人")}</dt><dd>{pending.requested_by_email || DASH}</dd>
        <dt>{tr("申请时间")}</dt><dd>{fmtTime(pending.created_at)}</dd>
      </dl>
      <span className="small">{tr("只有通知邮件送达本人且满 {hours} 小时后才会执行；本人可在此之前用邮件里的链接取消。期间邮箱变更、账户停用、第二因素被使用或申请人失去权限，都会使它作废。", { hours: RESET_DELAY_HOURS })}</span>
      {mayRecover && onCancel && <div><button type="button" className="btn sm" onClick={() => onCancel(pending)}>{tr("取消重置")}</button></div>}
    </div> : mayRecover && onReset ? (unavailable ? <p className="muted small">{tr(unavailable)}</p>
      : <div className="col gap8"><div><button type="button" className="btn danger sm" onClick={onReset}>{tr("重置第二因素")}</button></div>
        <span className="muted small">{tr("用于本人同时丢失了动态验证码和全部恢复码：先核验本人身份再申请，{hours} 小时后生效，期间本人可以取消。", { hours: RESET_DELAY_HOURS })}</span></div>)
      : !!held && !mayRecover && !unavailable && <p className="muted small">{tr("你的平台角色不能重置第二因素。")}</p>}
    {s.history.length ? <div className="list-wrap"><table className="gov" aria-label={tr("第二因素重置记录")}>
      <thead><tr><th scope="col">{tr("申请时间")}</th><th scope="col">{tr("状态")}</th><th scope="col">{tr("生效或结束")}</th><th scope="col">{tr("核验方式")}</th></tr></thead>
      <tbody>{s.history.map((h) => <tr key={h.id}>
        <th scope="row">{fmtTime(h.created_at)}</th><td>{statusText(h.status)}</td><td>{historyEnd(h)}</td><td>{methodText(h.method)}</td>
      </tr>)}</tbody></table></div>
      : <p className="muted small">{tr("没有第二因素重置记录。")}</p>}
  </section>;
}

/** The loaded preview: the target, when the reset takes effect and everything it will do. Pure. */
export function SecondFactorResetImpact({ plan }: { plan: ResetPlan }) {
  useI18n();
  const e = plan.effect, f = plan.factors;
  return <>
    <p><span className="muted">{tr("目标账户")}</span> <b>{plan.target.email}</b> · {ACCOUNT_STATUS[plan.target.status] || plan.target.status}</p>
    <p><b>{tr("生效时间：{time}", { time: fmtTime(plan.due_at) })}</b></p>
    <ul aria-label={tr("生效时将发生")}>
      {e.removes_totp && <li>{tr("删除动态验证码：身份验证器应用里的条目不再有效。")}</li>}
      {e.removes_recovery_codes && f.recovery_remaining > 0 && <li>{tr("删除剩余的 {count} 个恢复码。", { count: f.recovery_remaining })}</li>}
      {e.removes_passkeys > 0 ? <li>{tr("移除 {count} 个安全密钥。", { count: e.removes_passkeys })}</li>
        : f.passkeys > 0 && <li>{tr("保留 {count} 个安全密钥：本人仍可用它们登录。", { count: f.passkeys })}</li>}
      <li>{tr("结束该账户的全部会话并撤销应用令牌（当前 {sessions} 个会话、{tokens} 个应用令牌）。", { sessions: e.sessions, tokens: e.tokens })}</li>
      <li>{tr("口令、资料、成员关系和数据不变；之后本人用口令登录，再重新设置第二因素。")}</li>
    </ul>
    <p>{tr(RESET_MAIL_NOTE)}</p>
  </>;
}

/** The confirmation around the preview the caller holds, every field controlled by the caller:
 *  confirmable only once the preview is loaded and still valid, a method is chosen and the
 *  administrator has ticked that they checked it. */
export function SecondFactorResetConfirm(p: {
  target: { id: string; email: string }; plan: ResetPlan | null; error: string; invalid: boolean; busy: boolean;
  passkeys: number; includePasskeys: boolean; onIncludePasskeys: (include: boolean) => void;
  method: string; onMethod: (method: string) => void; confirmed: boolean; onConfirmed: (confirmed: boolean) => void;
  onPreviewAgain: () => void; run: (reason: string) => Promise<ResetResult>; onClose: () => void; onDone: (result: ResetResult) => void;
}) {
  useI18n();
  const methodId = useId();
  const title = tr("重置第二因素");
  return <ActionDialog<ResetResult> title={title} confirmLabel={title} reason="required" reauthScope="admin" danger size="wide"
    ready={!!p.plan && !p.invalid && !p.busy && !!p.method && p.confirmed}
    reasonPlaceholder={tr("写明如何核验了本人身份，以及为什么需要重置")}
    impact={<><b>{p.target.email}</b><p>{tr(RESET_DELAY_NOTE, { hours: RESET_DELAY_HOURS })}</p><p className="small muted">{tr(RESET_PLATFORM_NOTE)}</p></>}
    run={p.run} onClose={p.onClose} onDone={p.onDone}>
    {p.passkeys > 0 && <label className="check"><input type="checkbox" checked={p.includePasskeys} disabled={p.busy}
      onChange={(e) => p.onIncludePasskeys(e.target.checked)} />{tr("同时移除该账户的安全密钥（{count} 个）", { count: p.passkeys })}</label>}
    {p.error && <div className="notice err col gap8" role="alert"><p>{p.error}</p>
      <div><button type="button" className="btn sm" onClick={p.onPreviewAgain}>{tr("重新预览")}</button></div></div>}
    {!p.plan && !p.error && <p role="status">{tr("正在核对账户与重置影响…")}</p>}
    {p.plan && <>
      <SecondFactorResetImpact plan={p.plan} />
      {p.invalid && <p className="notice warn" role="alert">{tr("这份预览已失效，请重新预览后再确认。")}</p>}
      <p className="muted sm">{tr("预览有效至 {time}。账户变化后必须重新预览。", { time: fmtTime(p.plan.expires_at) })}</p>
    </>}
    <div className="field">
      <label htmlFor={methodId}>{tr("如何核验了本人身份")}<span className="req">{tr("（必选）")}</span></label>
      <select id={methodId} className="input" value={p.method} required onChange={(e) => p.onMethod(e.target.value)}>
        <option value="">{tr("请选择核验方式…")}</option>
        {RESET_METHODS.map((m) => <option key={m} value={m}>{tr(RESET_METHOD_LABELS[m])}</option>)}
      </select>
    </div>
    {p.plan && <label className="check"><input type="checkbox" checked={p.confirmed} disabled={p.busy || p.invalid}
      onChange={(e) => p.onConfirmed(e.target.checked)} />{tr("我已核验本人身份，并核对了目标账户和上述影响。")}</label>}
    {p.plan && <div><button type="button" className="btn sm" disabled={p.busy} onClick={p.onPreviewAgain}>{tr("重新预览")}</button></div>}
  </ActionDialog>;
}

/** The refusals after which the server accepts only a new preview: the plan expired or no longer
 *  matches, or this preview's key was spent on a different request (an edited reason or method). */
const RENEW = new Set(["admin_preview_expired", "admin_preview_invalid", "admin_preview_stale", "idempotency_conflict"]);

/** One idempotency key per preview: a retry of the same preview replays the request the server
 *  stored for it; a new preview is a new request with a new key. */
export type ResetAttempt = { token: string; key: string };
export function resetAttempt(previous: ResetAttempt | null, token: string): ResetAttempt {
  return previous?.token === token ? previous : { token, key: crypto.randomUUID() };
}

/** `target_is_platform_admin` and `admin_preview_stale` sent with only a code, or with a sentence no
 *  catalogue holds, are worded by labels.ts for other acts; here they are about this reset. The
 *  server's own catalogued sentence is kept as it is, and so are the code, status, detail and request id. */
export function aboutReset(e: unknown): unknown {
  if (!(e instanceof ApiError) || e.upstream || hasMessage(e.originalMessage)) return e;
  const sentence = e.code === "target_is_platform_admin" ? RESET_PLATFORM_ADMIN : e.code === "admin_preview_stale" ? RESET_STALE : null;
  if (!sentence) return e;
  const next = new ApiError(e.status, e.code, sentence, e.detail, e.upstream);
  next.requestId = e.requestId; next.retryAfter = e.retryAfter;
  return next;
}

/** The whole act: preview on open (again when "also remove passkeys" changes), confirm, done. */
export function SecondFactorResetDialog({ target, passkeys, onClose, onDone }: {
  target: { id: string; email: string }; passkeys: number; onClose: () => void; onDone: (result: ResetResult) => void;
}) {
  useI18n();
  const [includePasskeys, setIncludePasskeys] = useState(false);
  // The preview and the choice it was made for: the request must name what was previewed.
  const [previewed, setPreviewed] = useState<{ include: boolean; plan: ResetPlan } | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  // The server refused the preview the dialog holds; only a new preview can be confirmed.
  const [invalid, setInvalid] = useState(false);
  const [method, setMethod] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const attempt = useRef<ResetAttempt | null>(null);
  const base = `/api/admin/users/${encodeURIComponent(target.id)}/second-factor-reset`;
  useEffect(() => {
    let live = true;
    setPreviewed(null); setError(""); setInvalid(false); setConfirmed(false); attempt.current = null;
    post<ResetPlan>(`${base}/preview`, { include_passkeys: includePasskeys })
      .then((plan) => { if (live) setPreviewed({ include: includePasskeys, plan }); })
      .catch((e) => { if (live) setError(errText(aboutReset(e))); });
    return () => { live = false; };
  }, [base, includePasskeys, revision]);
  const plan = previewed?.plan ?? null;
  return <SecondFactorResetConfirm target={target} plan={plan} error={error} invalid={invalid} busy={busy}
    passkeys={plan ? plan.factors.passkeys : passkeys} includePasskeys={includePasskeys} onIncludePasskeys={setIncludePasskeys}
    method={method} onMethod={setMethod} confirmed={confirmed} onConfirmed={setConfirmed}
    onPreviewAgain={() => setRevision((n) => n + 1)} onClose={onClose} onDone={onDone}
    run={async (reason) => {
      if (!previewed || invalid || !method) throw new ApiError(400, "admin_confirmation_required", "");
      attempt.current = resetAttempt(attempt.current, previewed.plan.preview_token);
      setBusy(true);
      try {
        return await post<ResetResult>(base, { method, reason, include_passkeys: previewed.include,
          preview_token: previewed.plan.preview_token, idempotency_key: attempt.current.key });
      } catch (e) {
        if (e instanceof ApiError && !e.upstream && RENEW.has(e.code)) { setInvalid(true); setConfirmed(false); }
        throw aboutReset(e);
      } finally { setBusy(false); }
    }} />;
}

/** The section as the account detail shows it: loads the state, opens the reset and the cancel. */
export function SecondFactorPanel({ target, held, onChanged }: { target: ResetTarget; held?: Held; onChanged?: () => void }) {
  useI18n();
  const toast = useToast();
  const { me } = useSession();
  const path = `/api/admin/users/${encodeURIComponent(target.id)}/second-factor`;
  const load = useLoad<SecondFactorState>(path, (signal) => get<SecondFactorState>(path, signal));
  const [resetting, setResetting] = useState(false);
  const [cancelling, setCancelling] = useState<PendingReset | null>(null);
  const changed = () => { load.reload(); onChanged?.(); };
  if (!load.data) return <section className="col gap8"><h3 className="secttl">{tr("第二因素")}</h3>
    {load.error ? <div className="notice err" role="alert">{errText(load.error)} <button type="button" className="btn sm" onClick={load.reload}>{tr("重试")}</button></div>
      : <p role="status">{tr("正在读取第二因素状态…")}</p>}</section>;
  return <>
    <SecondFactorSection state={load.data} target={target} held={held} selfId={me?.id}
      onReset={() => setResetting(true)} onCancel={setCancelling} />
    {resetting && <SecondFactorResetDialog target={target} passkeys={load.data.passkeys} onClose={() => setResetting(false)}
      onDone={(result) => {
        setResetting(false); changed();
        toast(tr("已申请重置第二因素，将于 {time} 生效；已给本人发送附取消链接的邮件。", { time: fmtTime(result.reset.due_at) }), "ok");
      }} />}
    {cancelling && <ActionDialog title={tr("取消重置")} confirmLabel={tr("取消重置")} reason="required" reauthScope="admin"
      impact={tr(CANCEL_IMPACT, { email: target.email, time: fmtTime(cancelling.due_at) })}
      run={(reason) => post(`/api/admin/users/${encodeURIComponent(target.id)}/second-factor-reset/${encodeURIComponent(cancelling.id)}/cancel`, { reason })}
      onClose={() => setCancelling(null)}
      onDone={() => { setCancelling(null); changed(); toast(tr("已取消第二因素重置。"), "ok"); }} />}
  </>;
}

/** Every fixed sentence of this module that is not written inline at its call (the tests read the inline ones). */
export const SECOND_FACTOR_SENTENCES = [
  ...Object.values(RESET_METHOD_LABELS), ...Object.values(RESET_STATUS_LABELS), ...Object.values(VOID_REASON_LABELS),
  RESET_DELAY_NOTE, RESET_PLATFORM_NOTE, RESET_MAIL_NOTE, CANCEL_IMPACT, RESET_PLATFORM_ADMIN, RESET_STALE,
  "请通过应用身份管理操作该账户。", "不能为自己的账户申请重置第二因素。", "只能为状态正常的账户重置第二因素。",
  "该账户没有动态验证码、恢复码或安全密钥，无需重置。",
];
