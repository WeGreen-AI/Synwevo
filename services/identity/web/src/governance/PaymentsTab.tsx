import { useId, useRef, useState, type FormEvent } from "react";
import { ApiError, post } from "@synwevo/auth-client";
import { fmtTime, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { PaymentReadiness } from "./PaymentReadiness";
import { useAdminData } from "./data";
import { fenText, parseYuan, yuanInput } from "./billing-money";
import { PAYMENT_METHODS, PAYMENT_METHOD_LABELS, configuredPaymentMethods, paymentMethods, paymentMethodsText, type PaymentMethod } from "../lib/paymentMethods";
import "./payments.messages";
/**
 * Payment settings (synwevo_identity.admin_payments, billing.payment_settings): whether wallet
 * top-ups are open, the server-priced packages and amount bounds, the per-account velocity limits,
 * and the Stripe secret key and webhook signing secret.
 *
 * The secrets live only in their two password inputs and in the one request that publishes them;
 * they are never put in state, never shown again (the server answers only "configured"), and the
 * inputs are emptied when the confirmation closes or the publication succeeds. Publishing is a dangerous operation: the dialog asks for
 * a reason and, when the server wants one, a fresh identity confirmation.
 *
 * Publishing a version with top-ups open is checked against the payment provider by the server
 * (admin_payments.publish): a selected method the provider has not activated is refused
 * (409 payment_methods_unavailable, `detail.methods`), and the dialog names each one and offers
 * to deselect them; a key the provider rejects is refused (409 payment_credentials_rejected) and
 * must be replaced; a selection that cannot be verified (503 payment_readiness_unavailable, 409
 * payment_credentials_forbidden, 409 payment_methods_unverified) is refused unless the
 * administrator explicitly acknowledges publishing unverified methods, which is audited.
 */

type Package = { id: string; amount_minor: number; label: string | null };
type Velocity = { max_orders_per_hour: number; max_orders_per_day: number; max_pending: number; max_amount_per_day_minor: number };
type Spec = { enabled: boolean; operator_name: string; packages: Package[]; custom_amount: boolean;
  min_amount_minor: number; max_amount_minor: number; velocity: Velocity; payment_methods?: PaymentMethod[] };
type State = { environment: "test" | "live" | null; revision: number; spec: Spec | null; credential_configured: boolean;
  status: "configured" | "unconfigured"; wallet_unit: { code: string; scale: number } | null;
  webhook_url: string; webhook_events: string[]; created_at: number | null; reason: string | null;
  /** Top-up checkouts that can still be paid; revoking the keys would strand them (payments-1). */
  open_checkouts?: { count: number; until: number | null } };
type Row = { id: string; yuan: string; label: string };
type Form = { enabled: boolean; operator: string; paymentMethods: PaymentMethod[]; packages: Row[]; custom: boolean; min: string; max: string;
  perHour: string; perDay: string; pending: string; dayAmount: string; credential: "keep" | "replace" | "revoke";
  /** Revoke although checkouts are still open: an explicit, audited decision. */
  override?: boolean };

const API = "/api/admin/billing/payments";
/** The publish field that accepts publishing methods the server could not check (contract §5). */
const ACKNOWLEDGE = "acknowledge_unverified_methods";

/** What a refused publication asks of the administrator, from the server's refusal; null for any
 *  other error (the dialog words it). Only methods this page knows are named.
 *  - `methods`: Stripe has not activated these selected methods; deselect them (never acknowledgeable).
 *  - `rejected`: Stripe rejected the key; replace it (never acknowledgeable).
 *  - `unverified`: the selection could not be verified, and the server accepts an explicit, audited
 *    acknowledgement — because the check could not run (`unavailable`), the key may not read the
 *    account (`forbidden`), or Stripe's answer proves nothing for some methods (`inconclusive`). */
export type UnverifiedCause = "unavailable" | "forbidden" | "inconclusive";
export type UnverifiedRefusal = { kind: "unverified"; cause: UnverifiedCause; methods: PaymentMethod[]; chargesDisabled: boolean };
export type PublishRefusal = { kind: "methods"; methods: PaymentMethod[] } | { kind: "rejected" } | UnverifiedRefusal;
export function publishRefusal(error: unknown): PublishRefusal | null {
  if (!(error instanceof ApiError) || error.upstream) return null;
  const named = (value: unknown) => paymentMethods(Array.isArray(value) ? value : []);
  const unverified = (cause: UnverifiedCause, methods: PaymentMethod[] = [], chargesDisabled = false): UnverifiedRefusal =>
    ({ kind: "unverified", cause, methods, chargesDisabled });
  if (error.status === 409 && error.code === "payment_methods_unavailable") {
    const methods = named(error.detail?.methods);
    return methods.length ? { kind: "methods", methods } : null;
  }
  if (error.status === 409 && error.code === "payment_credentials_rejected") return { kind: "rejected" };
  if (error.status === 503 && error.code === "payment_readiness_unavailable") return unverified("unavailable");
  // The other acknowledgeable refusals say so in their detail; without it there is nothing to confirm.
  if (error.status !== 409 || error.detail?.acknowledgement !== ACKNOWLEDGE) return null;
  if (error.code === "payment_credentials_forbidden") return unverified("forbidden");
  if (error.code === "payment_methods_unverified")
    return unverified("inconclusive", named(error.detail?.methods), error.detail?.charges_enabled === false);
  return null;
}

/** Inside the publish confirmation: the methods Stripe has not activated, each named, with the way
 *  to deselect them; a rejected key, with what to do instead; or, when the selection could not be
 *  verified, why, and the explicit acknowledgement. */
export function PublishGuard({ blocked, rejected, unverified, acknowledged, onAcknowledge, onDeselect }: {
  blocked: PaymentMethod[]; rejected: boolean; unverified: UnverifiedRefusal | null; acknowledged: boolean;
  onAcknowledge: (value: boolean) => void; onDeselect: () => void;
}) {
  const { t } = useI18n();
  return <>
    {!!blocked.length && <div className="notice warn col gap8" role="note">
      <p style={{ margin: 0 }}>{t("支付服务商报告以下所选支付方式尚未开通：")}</p>
      <ul style={{ margin: 0 }}>{blocked.map(method => <li key={method}>
        {t("{method}：支付服务商报告尚未开通", { method: t(PAYMENT_METHOD_LABELS[method]) })}</li>)}</ul>
      <div><button type="button" className="btn sm" onClick={onDeselect}>{t("取消选择这些支付方式")}</button></div>
    </div>}
    {rejected && !blocked.length && <div className="notice warn col gap8" role="note">
      <p style={{ margin: 0 }}>{t("Stripe 拒绝了这个密钥，开放充值的设置不能发布，也不能通过确认跳过。请在 Stripe 核对或重新生成密钥，选择“设置或轮换密钥”填入后再发布；只关闭充值的设置不需要这项检查。")}</p>
    </div>}
    {unverified && !blocked.length && !rejected && <div className="notice warn col gap8" role="note">
      {unverified.cause === "forbidden" && <p style={{ margin: 0 }}>{t("Stripe 不允许这个密钥读取账户信息（可能是权限受限的密钥），所以无法核实所选支付方式。如确认这个密钥可以创建付款页面，可以勾选下面的确认后发布；它会写入审计记录。")}</p>}
      {unverified.cause === "inconclusive" && <>
        {!!unverified.methods.length && <>
          <p style={{ margin: 0 }}>{t("Stripe 的回答不足以确认以下所选支付方式已开通：")}</p>
          <ul style={{ margin: 0 }}>{unverified.methods.map(method => <li key={method}>
            {t("{method}：未能确认已开通", { method: t(PAYMENT_METHOD_LABELS[method]) })}</li>)}</ul>
        </>}
        {unverified.chargesDisabled && <p style={{ margin: 0 }}>{t("Stripe 账户尚未启用收款。")}</p>}
      </>}
      {unverified.cause !== "forbidden" && <p style={{ margin: 0 }}>{t("如确需在未核实的情况下现在发布，请勾选下面的确认；它会写入审计记录。")}</p>}
      <label className="row gap8"><input type="checkbox" checked={acknowledged} onChange={(e) => onAcknowledge(e.target.checked)} />
        {t(unverified.cause === "forbidden" ? "我知道这个密钥不能读取账户信息、所选支付方式未经核实，仍要发布" : "我知道所选支付方式未经核实，仍要发布")}</label>
    </div>}
  </>;
}
const DEFAULT_FORM: Form = { enabled: false, operator: "", paymentMethods: [], packages: [{ id: "", yuan: "", label: "" }], custom: false,
  min: "", max: "", perHour: "5", perDay: "20", pending: "3", dayAmount: "", credential: "replace" };

export function formOf(spec: Spec | null, configured: boolean): Form {
  if (!spec) return { ...DEFAULT_FORM, paymentMethods: [], credential: configured ? "keep" : "replace" };
  return { enabled: spec.enabled, operator: spec.operator_name, paymentMethods: configuredPaymentMethods(spec),
    packages: spec.packages.length ? spec.packages.map((p) => ({ id: p.id, yuan: yuanInput(p.amount_minor), label: p.label || "" }))
      : [{ id: "", yuan: "", label: "" }],
    custom: spec.custom_amount, min: yuanInput(spec.min_amount_minor), max: yuanInput(spec.max_amount_minor),
    perHour: String(spec.velocity.max_orders_per_hour), perDay: String(spec.velocity.max_orders_per_day),
    pending: String(spec.velocity.max_pending), dayAmount: yuanInput(spec.velocity.max_amount_per_day_minor),
    credential: configured ? "keep" : "replace" };
}

const whole = (text: string, lo: number, hi: number) => /^[1-9]\d{0,5}$/.test(text.trim()) && +text >= lo && +text <= hi ? +text : null;

/** The spec the form describes, or the first problem (a source sentence). */
export function specOf(form: Form): { spec?: Spec; problem?: string } {
  if (!form.paymentMethods.length) return { problem: "请至少选择一种支付方式。" };
  if (!form.operator.trim() || form.operator.trim().length > 120) return { problem: "请填写运营方名称（显示在收银台与收据上，最多 120 个字）。" };
  const packages: Package[] = [];
  for (const row of form.packages) {
    if (!row.id.trim() && !row.yuan.trim() && !row.label.trim()) continue;
    const amount = parseYuan(row.yuan);
    if (!/^[a-z0-9][a-z0-9-]{0,39}$/.test(row.id.trim()) || amount === null || row.label.trim().length > 60)
      return { problem: "套餐编号只能用小写字母、数字和连字符；金额须为 0.01 至 100000 元，最多两位小数。" };
    packages.push({ id: row.id.trim(), amount_minor: amount, label: row.label.trim() || null });
  }
  if (new Set(packages.map((p) => p.id)).size !== packages.length) return { problem: "套餐编号不能重复。" };
  const min = parseYuan(form.min), max = parseYuan(form.max);
  const bounds = packages.map((p) => p.amount_minor);
  const lo = min ?? (bounds.length ? Math.min(...bounds) : null), hi = max ?? (bounds.length ? Math.max(...bounds) : null);
  if (form.custom && (min === null || max === null)) return { problem: "允许自定义金额时，请填写最低与最高金额。" };
  if (lo === null || hi === null || lo > hi) return { problem: "最低金额不能高于最高金额。" };
  if (bounds.some((b) => b < lo || b > hi)) return { problem: "每个套餐金额都必须在最低与最高金额之间。" };
  if (form.enabled && !packages.length && !form.custom) return { problem: "开启充值前，至少提供一个套餐或允许自定义金额。" };
  const perHour = whole(form.perHour, 1, 100), perDay = whole(form.perDay, 1, 1000), pending = whole(form.pending, 1, 20);
  const dayAmount = parseYuan(form.dayAmount, 10_000_000);
  if (perHour === null || perDay === null || pending === null || dayAmount === null)
    return { problem: "频率限制须为正整数：每小时 1–100 单，每天 1–1000 单，未完成 1–20 单；每日金额上限须为有效金额。" };
  return { spec: { enabled: form.enabled, operator_name: form.operator.trim(), payment_methods: paymentMethods(form.paymentMethods), packages, custom_amount: form.custom,
    min_amount_minor: lo, max_amount_minor: hi,
    velocity: { max_orders_per_hour: perHour, max_orders_per_day: perDay, max_pending: pending, max_amount_per_day_minor: dayAmount } } };
}

export function PaymentMethodChoices({ selected, onChange, invalid }: {
  selected: PaymentMethod[]; onChange: (methods: PaymentMethod[]) => void; invalid: boolean;
}) {
  const { t } = useI18n();
  const id = useId();
  return <fieldset className="col gap8" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}
    aria-describedby={`${id}-hint ${id}-required`} aria-invalid={invalid || undefined}>
    <legend>{t("支付方式（至少选择一项）")}</legend>
    <p className="small muted" id={`${id}-hint`}>{t("仅选择商户已开通的支付方式，选择不代表渠道资格已验证")}</p>
    {PAYMENT_METHODS.map(method => <label key={method} className="row gap8">
      <input type="checkbox" name="payment_methods" value={method} checked={selected.includes(method)}
        onChange={e => onChange(paymentMethods(e.target.checked ? [...selected, method] : selected.filter(item => item !== method)))} />
      <span>{t(PAYMENT_METHOD_LABELS[method])}</span>
    </label>)}
    <p className="small muted" id={`${id}-required`}>{t("请至少选择一种支付方式。")}</p>
  </fieldset>;
}

/** Also accepts historical settings: only a missing field has the legacy meaning. */
export function PaymentMethodSummary({ spec }: { spec: Spec | null }) {
  const { t, locale } = useI18n();
  return <div><dt>{t("已选支付方式")}</dt><dd>{paymentMethodsText(configuredPaymentMethods(spec), t, locale) || t("未配置")}</dd></div>;
}

export function PaymentsTab({ onDenied, canManage }: { onDenied: () => void; canManage: boolean }) {
  const { t, locale } = useI18n();
  const id = useId();
  const load = useAdminData<State>(API, onDenied);
  const state = load.data;
  const [form, setForm] = useState<Form | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<{ spec: Spec; expected_revision: number; idempotency_key: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  // The server's answer to the last publication attempt in the open confirmation (see PublishGuard).
  const [blocked, setBlocked] = useState<PaymentMethod[]>([]);
  const [rejected, setRejected] = useState(false);
  const [unverified, setUnverified] = useState<UnverifiedRefusal | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [round, setRound] = useState(0);
  const secretKey = useRef<HTMLInputElement>(null);
  const webhookSecret = useRef<HTMLInputElement>(null);
  const current = form ?? (state ? formOf(state.spec, state.credential_configured) : null);
  const set = (patch: Partial<Form>) => { setForm({ ...(current as Form), ...patch }); setDone(null); };
  const clearSecrets = () => { if (secretKey.current) secretKey.current.value = ""; if (webhookSecret.current) webhookSecret.current.value = ""; };
  const clearGuard = () => { setBlocked([]); setRejected(false); setUnverified(null); setAcknowledged(false); };
  const newKey = () => "pay_" + crypto.randomUUID().replace(/-/g, "");

  /** Deselect the refused methods and confirm the changed spec afresh (a new request and a
   *  fresh dialog); with no method left, back to the form, which says what is missing. */
  function deselect() {
    if (!confirming || !current) return;
    const next = { ...current, paymentMethods: current.paymentMethods.filter(method => !blocked.includes(method)) };
    setForm(next); clearGuard();
    const { spec, problem: why } = specOf(next);
    if (!spec) { setConfirming(null); clearSecrets(); setProblem(why || null); return; }
    setConfirming({ spec, expected_revision: confirming.expected_revision, idempotency_key: newKey() });
    setRound(n => n + 1);
  }

  function review(e: FormEvent) {
    e.preventDefault();
    if (!current) return;
    const { spec, problem: why } = specOf(current);
    if (why) { setProblem(why); return; }
    if (current.credential === "replace" && (!secretKey.current?.value || !webhookSecret.current?.value)) {
      setProblem("请同时填写 Stripe 密钥与 Webhook 签名密钥。");
      return;
    }
    setProblem(why || null);
    clearGuard();
    if (spec && state) setConfirming({ spec, expected_revision: state.revision, idempotency_key: newKey() });
  }

  if (load.loading) return <p role="status">{t("加载中…")}</p>;
  if (load.err) return <div className="notice err" role="alert">{load.err} <button className="btn sm" onClick={load.reload}>{t("重试")}</button></div>;
  if (!state || !current) return null;
  const live = state.environment === "live";
  return <section className="card card-b col gap16" aria-labelledby={`${id}-h`}>
    <div className="row gap12 wrap"><h2 id={`${id}-h`} className="grow">{t("支付设置")}</h2>
      <button className="btn sm" onClick={() => { setForm(null); load.reload(); }}>{t("刷新")}</button></div>
    <p className="muted small">{t("钱包充值通过 Stripe 托管收银台以人民币收款，使用此处选择的支付方式。套餐与金额只由这里决定，浏览器不能指定金额；资金只在签名校验通过的支付通知到达后入账。充值后不可退款。")}</p>
    {!state.environment && <div className="notice warn" role="note">{t("此部署尚未配置计费环境，不能开启充值。")}</div>}
    {state.environment && !state.wallet_unit && <div className="notice warn" role="note">{t("钱包单位中没有 CNY（精度至少到分），付款无法入账；请先在部署配置中加入 CNY 钱包单位。")}</div>}
    <dl className="col gap8 small" style={{ margin: 0 }}>
      <div><dt>{t("计费环境")}</dt><dd>{state.environment ? t(live ? "正式（live）" : "测试（test）") : t("未配置")}</dd></div>
      <div><dt>{t("状态")}</dt><dd>{t(state.spec?.enabled ? "充值已开放" : "充值未开放")}</dd></div>
      <PaymentMethodSummary spec={state.spec} />
      <div><dt>{t("支付密钥")}</dt><dd>{t(state.credential_configured ? "已配置（不会再次显示）" : "未配置")}</dd></div>
      <div><dt>{t("Webhook 地址")}</dt><dd><code style={{ overflowWrap: "anywhere" }}>{state.webhook_url}</code></dd></div>
      <div><dt>{t("需要订阅的事件")}</dt><dd><code style={{ overflowWrap: "anywhere" }}>{state.webhook_events.join(", ")}</code></dd></div>
      {state.created_at && <div><dt>{t("当前版本")}</dt><dd>{t("第 {n} 版", { n: state.revision })}</dd></div>}
    </dl>
    {!canManage && <p className="notice" role="note">{t("你可以查看支付设置，不能修改。")}</p>}
    <PaymentReadiness revision={state.revision} savedMethods={configuredPaymentMethods(state.spec)}
      hasDraft={canManage && (form !== null || current.credential !== "keep")} />
    {canManage && state.environment && <form className="col gap12" onSubmit={review} noValidate aria-describedby={problem ? `${id}-err` : undefined}>
      <label className="row gap8"><input type="checkbox" checked={current.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        {t("开放钱包充值")}</label>
      <PaymentMethodChoices selected={current.paymentMethods} onChange={paymentMethods => set({ paymentMethods })}
        invalid={!!problem && !current.paymentMethods.length} />
      <label className="col gap4">{t("运营方名称（显示在收银台与收据上）")}
        <input className="input" value={current.operator} maxLength={120} onChange={(e) => set({ operator: e.target.value })} /></label>
      <fieldset className="col gap8" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend>{t("充值套餐（元）")}</legend>
        {current.packages.map((row, index) => <div className="row gap8 wrap" key={index}>
          <input className="input" aria-label={t("套餐编号")} placeholder={t("套餐编号")} value={row.id} style={{ flex: "1 1 120px", minWidth: 0 }}
            onChange={(e) => set({ packages: current.packages.map((r, i) => i === index ? { ...r, id: e.target.value } : r) })} />
          <input className="input" aria-label={t("金额（元）")} placeholder={t("金额（元）")} inputMode="decimal" value={row.yuan} style={{ flex: "1 1 100px", minWidth: 0 }}
            onChange={(e) => set({ packages: current.packages.map((r, i) => i === index ? { ...r, yuan: e.target.value } : r) })} />
          <input className="input" aria-label={t("显示名称（可选）")} placeholder={t("显示名称（可选）")} value={row.label} maxLength={60} style={{ flex: "2 1 160px", minWidth: 0 }}
            onChange={(e) => set({ packages: current.packages.map((r, i) => i === index ? { ...r, label: e.target.value } : r) })} />
          <button type="button" className="btn sm" aria-label={t("删除这个套餐")} disabled={current.packages.length === 1}
            onClick={() => set({ packages: current.packages.filter((_, i) => i !== index) })}>{t("删除")}</button>
        </div>)}
        <div><button type="button" className="btn sm" disabled={current.packages.length >= 12}
          onClick={() => set({ packages: [...current.packages, { id: "", yuan: "", label: "" }] })}>{t("添加套餐")}</button></div>
      </fieldset>
      <label className="row gap8"><input type="checkbox" checked={current.custom} onChange={(e) => set({ custom: e.target.checked })} />
        {t("允许在上下限内自定义金额")}</label>
      <div className="row gap12 wrap">
        <label className="col gap4" style={{ flex: "1 1 160px" }}>{t("最低金额（元）")}
          <input className="input" inputMode="decimal" value={current.min} onChange={(e) => set({ min: e.target.value })} /></label>
        <label className="col gap4" style={{ flex: "1 1 160px" }}>{t("最高金额（元）")}
          <input className="input" inputMode="decimal" value={current.max} onChange={(e) => set({ max: e.target.value })} /></label>
      </div>
      <fieldset className="row gap12 wrap" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend>{t("每个账户的频率与金额限制")}</legend>
        <label className="col gap4" style={{ flex: "1 1 140px" }}>{t("每小时订单数")}
          <input className="input" inputMode="numeric" value={current.perHour} onChange={(e) => set({ perHour: e.target.value })} /></label>
        <label className="col gap4" style={{ flex: "1 1 140px" }}>{t("每天订单数")}
          <input className="input" inputMode="numeric" value={current.perDay} onChange={(e) => set({ perDay: e.target.value })} /></label>
        <label className="col gap4" style={{ flex: "1 1 140px" }}>{t("同时未完成的订单")}
          <input className="input" inputMode="numeric" value={current.pending} onChange={(e) => set({ pending: e.target.value })} /></label>
        <label className="col gap4" style={{ flex: "1 1 140px" }}>{t("每天金额上限（元）")}
          <input className="input" inputMode="decimal" value={current.dayAmount} onChange={(e) => set({ dayAmount: e.target.value })} /></label>
      </fieldset>
      <fieldset className="col gap8" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend>{t("支付密钥")}</legend>
        {(["keep", "replace", "revoke"] as const).map((action) => <label className="row gap8" key={action}>
          <input type="radio" name={`${id}-cred`} checked={current.credential === action}
            disabled={action !== "replace" && !state.credential_configured} onChange={() => set({ credential: action })} />
          {t(action === "keep" ? "保留当前密钥" : action === "replace" ? "设置或轮换密钥" : "撤销密钥（充值随即关闭）")}</label>)}
        {current.credential === "revoke" && !!state.open_checkouts?.count && <div className="notice warn col gap8" role="note" id={`${id}-open`}>
          <p style={{ margin: 0 }}>{t("仍有 {n} 个未到期的充值收银台（最晚 {time} 到期）。撤销密钥会删除 Webhook 签名密钥，此后在这些收银台完成的付款无法验证、无法自动入账。建议先关闭充值并保留密钥，等它们到期后再撤销。",
            { n: state.open_checkouts.count, time: state.open_checkouts.until ? fmtTime(state.open_checkouts.until) : "—" })}</p>
          <label className="row gap8"><input type="checkbox" checked={!!current.override} aria-describedby={`${id}-open`}
            onChange={(e) => set({ override: e.target.checked })} />
            {t("仍要立即撤销（例如签名密钥已泄露）；这些付款需要人工核对")}</label>
        </div>}
        {current.credential === "replace" && <>
          <p className="muted small">{t(live ? "正式环境只接受正式模式密钥（sk_live_ 或 rk_live_）。" : "测试环境只接受测试模式密钥（sk_test_ 或 rk_test_）。")}</p>
          <label className="col gap4">{t("Stripe 密钥")}
            <input className="input" type="password" ref={secretKey} autoComplete="off" spellCheck={false} /></label>
          <label className="col gap4">{t("Webhook 签名密钥（whsec_ 开头）")}
            <input className="input" type="password" ref={webhookSecret} autoComplete="off" spellCheck={false} /></label>
        </>}
      </fieldset>
      {problem && <div id={`${id}-err`} className="notice err" role="alert">{t(problem)}</div>}
      {done && <div className="notice ok" role="status">{t(done)}</div>}
      <div><button className="btn primary" type="submit">{t("检查并发布")}</button></div>
    </form>}
    {state.spec && <section className="col gap8" aria-label={t("当前套餐")}>
      <h3>{t("当前套餐")}</h3>
      {state.spec.packages.length ? <ul>{state.spec.packages.map((p) => <li key={p.id}>
        {p.label ? `${p.label} · ` : ""}{fenText(p.amount_minor, locale)} <span className="muted small">({p.id})</span></li>)}</ul>
        : <p className="muted">{t("没有套餐。")}</p>}
      {state.spec.custom_amount && <p className="small">{t("自定义金额：{min} 至 {max}", {
        min: fenText(state.spec.min_amount_minor, locale), max: fenText(state.spec.max_amount_minor, locale) })}</p>}
    </section>}
    {confirming && <ActionDialog key={round} title={t("发布支付设置")} confirmLabel={t("发布")} danger={live} reason="required"
      impact={<>{current.credential === "revoke" && current.override && !!state.open_checkouts?.count
        ? <p className="notice warn">{t("撤销后，{n} 个未到期收银台中完成的付款无法自动入账，需要人工核对。", { n: state.open_checkouts.count })}</p>
        : <p>{t(confirming.spec.enabled && current.credential !== "revoke" ? "发布后，用户可以按这些套餐和限制向钱包充值。" : "发布后，钱包充值关闭；已发起的付款仍会在支付通知到达后入账。")}</p>}
        {confirming.spec.enabled && <p className="small muted">{t("发布前，服务器会向支付服务商核实所选支付方式；密钥被拒绝或所选支付方式尚未开通时不能发布。")}</p>}
        <dl className="col gap8 small"><PaymentMethodSummary spec={confirming.spec} /></dl></>}
      run={async (reason) => {
        const credential = current.credential === "replace"
          ? { action: "replace", secret_key: secretKey.current?.value || "", webhook_secret: webhookSecret.current?.value || "" }
          : { action: current.credential, ...(current.credential === "revoke" && current.override ? { override_open_checkouts: true } : {}) };
        // A retry after the identity confirmation sends the same inputs; they are emptied when
        // the dialog closes or the publication succeeds.
        try {
          return await post<{ methods_check?: string }>(`${API}/publish`, { ...confirming, credential, reason,
            ...(unverified && acknowledged ? { [ACKNOWLEDGE]: true } : {}) });
        } catch (error) {
          const refusal = publishRefusal(error);
          if (refusal?.kind === "methods") { clearGuard(); setBlocked(refusal.methods); }
          if (refusal?.kind === "rejected") { clearGuard(); setRejected(true); }
          if (refusal?.kind === "unverified") {
            // An acknowledgement covers the reason it was given for, not a different one.
            if (unverified?.cause !== refusal.cause) setAcknowledged(false);
            setBlocked([]); setRejected(false); setUnverified(refusal);
          }
          throw error;
        }
      }}
      onDone={(result) => { clearSecrets(); clearGuard(); setConfirming(null); setForm(null); load.reload();
        setDone(result?.methods_check === "unverified_acknowledged" ? "支付设置已发布；所选支付方式未经支付服务商核实。" : "支付设置已发布。"); }}
      onClose={() => { setConfirming(null); clearSecrets(); clearGuard(); }}>
      <PublishGuard blocked={blocked} rejected={rejected} unverified={unverified} acknowledged={acknowledged}
        onAcknowledge={setAcknowledged} onDeselect={deselect} />
    </ActionDialog>}
  </section>;
}
