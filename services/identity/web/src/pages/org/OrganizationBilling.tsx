/** Organization payments (synwevo_identity.org_billing_routes), for holders of org.billing.manage:
 *  the organization wallet and its top-up, which members may charge it for their Agent runs and
 *  how much a month, and the organization statement. There is no per-workspace limit: no
 *  organization-paid run is attributed to a workspace yet, so such a limit would bind nothing
 *  (org_billing_routes).
 *
 *  Nothing here moves money. A top-up is priced by the server and paid in the payment provider's
 *  hosted checkout; the wallet is credited only from the provider's signed notification. Limits
 *  are rechecked by the ledger at every reservation and send, so a change here applies to the
 *  next model call. The statement carries amounts and whom a charge was for — never a
 *  conversation. Amounts stay exact: integer wallet units are formatted with BigInt.
 *
 *  The hosted checkout returns here with ?topup=<order>&result=success|cancel. The page then
 *  polls the order (GET /api/auth/wallet/topups/{id}) until the signed notice has credited it
 *  and shows the receipt the server holds; it never treats the return itself as payment. */
import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get, post, put } from "@synwevo/auth-client";
import { useI18n } from "@synwevo/ui";
import { FlowError, Loading, useAction } from "./AuthFlow";
import { isChallengeError, useChallenge } from "./Challenge";
import { orgPath, roleSource, type Organization } from "./types";
import { cnyText, fenText, parseYuan, yuanInput } from "../../governance/billing-money";
import { paymentMethodsText } from "../../lib/paymentMethods";
import { topupClosedReason, type TopupOptions } from "../account/walletModel";
import "../account/topup.messages";
import "../../org-billing.messages";

type Unit = { code: string; scale: number };
interface MemberGrant { user_id: string; email: string; display_name: string | null; role: string; revision: number; enabled: boolean; monthly_limit: string; spent_this_month: string; held: string }
interface Billing { organization_id: string; environment: string | null; currency: "CNY"; unit: Unit; month_start: number;
  wallet: { booked: string; held: string; available: string; frozen: boolean }; members: MemberGrant[] }
interface StatementItem { seq: string; id: string; kind: string; delta: string; created_at: number; member_id?: string; member_email?: string | null }
interface Statement { items: StatementItem[]; next_after_seq: string | null }
interface TopupStatus { order_id: string; status: "pending" | "paid" | "failed" | "expired"; amount_minor: number; currency: string; credited_at?: number }
interface Receipt { order_id: string; amount_minor: number; currency: string; method: string | null; paid_at: number | null;
  credited_at: number; operator_name: string | null; provider_reference: string | null }

const CHECKOUT = "https://checkout.stripe.com/";
/** The largest monthly limit the server accepts, in yuan (org_billing_routes.MAX_LIMIT_MINOR). */
const MAX_LIMIT_YUAN = 1000000;
const KIND: Record<string, string> = { paid: "充值到账", test_payment: "测试到账", sale: "已结算消费", grant: "赠送额度", chargeback: "拒付追回" };
const METHOD: Record<string, string> = { card: "银行卡", wechat_pay: "微信支付", alipay: "支付宝" };
/** Polling the order after the return: quickly at first, then slower, then stop and offer a manual check. */
const POLL_DELAYS_MS = [1500, 2000, 3000, 5000];
const POLL_FOR_MS = 120_000;
const UNAVAILABLE: Record<string, string> = { not_configured: "充值暂未开放", email_unverified: "请先验证邮箱再继续", wallet_frozen: "该钱包因支付争议已被冻结，暂不能充值" };

/** A copy of a draft map without one key (the saved row reads from the server again). */
function without<T>(key: string) { return (drafts: Record<string, T>) => { const next = { ...drafts }; delete next[key]; return next; }; }

/** Wallet units back to whole fen (scale >= 2 by configuration; limits are set in fen, so exact). */
function unitsToFen(units: string | null, scale: number): number | null {
  if (units === null || !/^(0|[1-9]\d{0,30})$/.test(units)) return null;
  return Number(BigInt(units) / 10n ** BigInt(Math.max(0, scale - 2)));
}

export default function OrganizationBilling({ organization, mine }: { organization: Organization; mine: string[] }) {
  const { t, locale } = useI18n(); const id = useId();
  const can = mine.includes("org.billing.manage");
  const [data, setData] = useState<Billing | null>(null); const [error, setError] = useState<unknown>(null); const [revision, setRevision] = useState(0);
  const [closed, setClosed] = useState(false);
  const [params, setParams] = useSearchParams();
  const returned = params.get("topup"); const result = params.get("result");
  useEffect(() => {
    if (!can) return;
    const abort = new AbortController(); setError(null); setClosed(false);
    get<Billing>(`${orgPath(organization.id)}/billing`, abort.signal).then(setData).catch(e => {
      if (abort.signal.aborted) return;
      // Not configured on this deployment: say so plainly instead of an error box.
      if ((e as { code?: string })?.code === "org_billing_not_configured") setClosed(true); else setError(e);
    });
    return () => abort.abort();
  }, [organization.id, can, revision]);
  if (!can) return null;
  const heading = <h2 id={`${id}-h`}>{t("组织付费")}</h2>;
  if (closed) return <section className="org-section" aria-labelledby={`${id}-h`}>{heading}<p className="notice" role="note">{t("组织付费尚未开放")}</p></section>;
  const money = (units: string) => data ? cnyText(units, data.unit.scale, locale) : "";
  return <section className="org-section org-billing" id="org-billing" aria-labelledby={`${id}-h`}>{heading}
    <p className="tiny muted">{t("组织可以为成员的 Agent 使用付费。每位成员需要单独授权并设置每月额度；成员在对话中自己选择由个人还是组织付款。额度在每次模型调用前重新核对。")}</p>
    {returned && /^[A-Za-z0-9_-]{1,200}$/.test(returned) && <TopupReturn key={returned} orderId={returned} cancelled={result === "cancel"}
      onCredited={() => setRevision(v => v + 1)}
      onDismiss={() => setParams(p => { const next = new URLSearchParams(p); next.delete("topup"); next.delete("result"); return next; }, { replace: true })} />}
    <FlowError error={error} />
    {error ? <button type="button" className="btn" onClick={() => setRevision(v => v + 1)}>{t("重试")}</button> : !data ? <Loading /> : <>
      <div className="org-actions" aria-live="polite">
        <span>{t("组织钱包可用：{amount}", { amount: money(data.wallet.available) })}</span>
        {data.wallet.held !== "0" && <span className="org-count">{t("调用中冻结：{amount}", { amount: money(data.wallet.held) })}</span>}
        {data.wallet.frozen && <span className="badge err">{t("因支付争议已冻结")}</span>}
      </div>
      {organization.status === "active" && <OrganizationTopup organization={organization} />}
      <MemberGrants organization={organization} data={data} onSaved={setData} />
      <OrganizationStatement key={revision} organization={organization} unit={data.unit} />
    </>}
  </section>;
}

/** After the hosted checkout: the order's real state, then its receipt. A cancelled return is
 *  said plainly (nothing was charged) without polling; the order simply expires. */
function TopupReturn({ orderId, cancelled, onCredited, onDismiss }: { orderId: string; cancelled: boolean; onCredited: () => void; onDismiss: () => void }) {
  const { t, locale } = useI18n(); const id = useId();
  const [status, setStatus] = useState<TopupStatus | null>(null); const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<unknown>(null); const [waiting, setWaiting] = useState(!cancelled); const [round, setRound] = useState(0);
  const credited = useRef(onCredited); credited.current = onCredited;
  useEffect(() => {
    if (cancelled && round === 0) return;
    const abort = new AbortController(); const started = Date.now(); let timer: number | undefined; let n = 0;
    setWaiting(true); setError(null);
    const poll = async () => {
      try {
        const now = await get<TopupStatus>(`/api/auth/wallet/topups/${encodeURIComponent(orderId)}`, abort.signal);
        if (abort.signal.aborted) return;
        setStatus(now);
        if (now.status === "paid") {
          setWaiting(false); credited.current();
          setReceipt(await get<Receipt>(`/api/auth/wallet/receipts/${encodeURIComponent(orderId)}`, abort.signal));
          return;
        }
        if (now.status !== "pending" || Date.now() - started > POLL_FOR_MS) { setWaiting(false); return; }
        timer = window.setTimeout(poll, POLL_DELAYS_MS[Math.min(n++, POLL_DELAYS_MS.length - 1)]);
      } catch (e) { if (!abort.signal.aborted) { setError(e); setWaiting(false); } }
    };
    void poll();
    return () => { abort.abort(); window.clearTimeout(timer); };
  }, [orderId, cancelled, round]);
  const state = status?.status;
  const message = cancelled && !status ? "你已取消支付，没有扣款。订单会在收银台过期后关闭。"
    : state === "paid" ? "充值已到账。"
    : state === "failed" ? "支付没有完成，没有扣款。可以重新发起充值。"
    : state === "expired" ? "收银台已过期，没有扣款。可以重新发起充值。"
    : waiting ? "正在等待支付服务确认到账…"
    : "支付服务尚未确认到账。如果你已经付款，稍后再查看；确认前不会入账。";
  return <div className={`notice${state === "paid" ? " ok" : ""}`} role="status" aria-live="polite" aria-labelledby={`${id}-h`}>
    <h3 id={`${id}-h`}>{t("充值订单 {order}", { order: orderId })}</h3>
    <p>{t(message)}</p>
    <FlowError error={error} />
    {receipt && <dl className="org-receipt">
      <dt>{t("金额")}</dt><dd>{receipt.currency === "CNY" ? fenText(receipt.amount_minor, locale) : `${receipt.amount_minor} ${receipt.currency}`}</dd>
      <dt>{t("付款方式")}</dt><dd>{receipt.method ? t(METHOD[receipt.method] || receipt.method) : t("支付服务未报告")}</dd>
      <dt>{t("到账时间")}</dt><dd>{new Date(receipt.credited_at * 1000).toLocaleString(locale)}</dd>
      {receipt.operator_name && <><dt>{t("收款方")}</dt><dd>{receipt.operator_name}</dd></>}
      {receipt.provider_reference && <><dt>{t("支付单号")}</dt><dd className="org-billing-wrap">{receipt.provider_reference}</dd></>}
    </dl>}
    <div className="org-actions">
      {!waiting && state !== "paid" && state !== "failed" && state !== "expired" && <button type="button" className="btn" onClick={() => setRound(v => v + 1)}>{t("再次查看")}</button>}
      {receipt && <button type="button" className="btn" onClick={() => window.print()}>{t("打印收据")}</button>}
      <button type="button" className="btn" onClick={onDismiss}>{t("关闭")}</button>
    </div>
  </div>;
}

export function OrganizationTopup({ organization }: { organization: Organization }) {
  const { t, locale } = useI18n(); const id = useId(); const action = useAction();
  const [options, setOptions] = useState<TopupOptions | null>(null); const [loadError, setLoadError] = useState<unknown>(null);
  const [choice, setChoice] = useState<string>(""); const [custom, setCustom] = useState("");
  const challenge = useChallenge("topup", { enabled: !!options?.available && !topupClosedReason(options, null) });
  useEffect(() => {
    const abort = new AbortController();
    get<TopupOptions>(`/api/auth/wallet/topup-options?payer_kind=organization&payer_id=${encodeURIComponent(organization.id)}`, abort.signal)
      .then(o => { setOptions(o); setChoice(o.packages[0]?.id ?? (o.custom_amount ? "custom" : "")); })
      .catch(e => { if (!abort.signal.aborted) setLoadError(e); });
    return () => abort.abort();
  }, [organization.id]);
  if (loadError) return <FlowError error={loadError} />;
  if (!options) return <Loading />;
  const closed = topupClosedReason(options, null);
  if (closed) return <p className="notice" role="note">{t(UNAVAILABLE[closed] || "充值暂未开放")}</p>;
  const customFen = choice === "custom" ? parseYuan(custom, (options.max_amount_minor ?? 0) / 100) : null;
  const customOk = customFen !== null && customFen >= (options.min_amount_minor ?? 1) && customFen <= (options.max_amount_minor ?? 0);
  const ready = choice !== "" && (choice !== "custom" || customOk) && (!challenge.required || !!challenge.token);
  function pay(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || !options) return;
    void action.run(async () => {
      try {
        const out = await post<{ order_id: string; checkout_url: string }>("/api/auth/wallet/topups", {
          payer_kind: "organization", payer_id: organization.id,
          ...(choice === "custom" ? { amount_minor: customFen } : { package_id: choice }), ...challenge.body() });
        // The server only ever returns the provider's hosted checkout; anything else is not followed.
        if (!out.checkout_url.startsWith(CHECKOUT)) throw new Error("支付服务暂时无法创建收银台，未扣款，请稍后再试");
        window.location.assign(out.checkout_url);
      } catch (err) { if (isChallengeError(err)) challenge.reset(); throw err; }
    });
  }
  return <form className="flow-stack org-create" onSubmit={pay} aria-busy={action.busy} aria-labelledby={`${id}-h`}>
    <h3 id={`${id}-h`}>{t("为组织钱包充值")}</h3>
    <fieldset className="flow-stack org-fieldset">
      <legend className="tiny muted">{t("选择充值金额（人民币）。充值后不可退款。")}</legend>
      {options.packages.map(p => <label key={p.id} className="row gap8"><input type="radio" name={`${id}-amount`} value={p.id} checked={choice === p.id} onChange={() => setChoice(p.id)} disabled={action.busy} />
        <span>{fenText(p.amount_minor, locale)}{p.label && <span className="tiny muted"> · {p.label}</span>}</span></label>)}
      {options.custom_amount && <label className="row gap8"><input type="radio" name={`${id}-amount`} value="custom" checked={choice === "custom"} onChange={() => setChoice("custom")} disabled={action.busy} /><span>{t("其他金额")}</span></label>}
      {choice === "custom" && <div className="field"><label htmlFor={`${id}-custom`}>{t("金额（元），可充值 {min} 至 {max}", { min: fenText(options.min_amount_minor ?? 0, locale), max: fenText(options.max_amount_minor ?? 0, locale) })}</label>
        <input id={`${id}-custom`} className="input" inputMode="decimal" autoComplete="off" value={custom} aria-invalid={!!custom && !customOk} disabled={action.busy} onChange={e => setCustom(e.target.value)} /></div>}
    </fieldset>
    {challenge.element}
    <FlowError error={action.error} />
    <p className="tiny muted">{t("付款在支付服务的收银台完成，可用支付方式：{methods}。只有支付服务的签名通知确认到账后，金额才会计入组织钱包并出现在账单中。", {
      methods: paymentMethodsText(options.payment_methods, t, locale) })}</p>
    <div className="org-actions"><button className="btn primary" disabled={!ready || action.busy}>{t(action.busy ? "正在打开收银台…" : "前往支付")}</button></div>
  </form>;
}

function MemberGrants({ organization, data, onSaved }: { organization: Organization; data: Billing; onSaved: (b: Billing) => void }) {
  const { t, locale } = useI18n(); const id = useId(); const action = useAction();
  const [drafts, setDrafts] = useState<Record<string, { enabled: boolean; yuan: string }>>({});
  const [saved, setSaved] = useState<string | null>(null);
  const scale = data.unit.scale; const money = (units: string) => cnyText(units, scale, locale);
  const draftOf = (m: MemberGrant) => drafts[m.user_id] ?? { enabled: m.enabled, yuan: m.enabled || m.monthly_limit !== "0" ? yuanInput(unitsToFen(m.monthly_limit, scale) ?? 0) : "" };
  const editable = organization.status === "active";
  return <div className="flow-stack" role="group" aria-labelledby={`${id}-h`}>
    <h3 id={`${id}-h`}>{t("成员额度")}</h3>
    <p className="tiny muted">{t("本月（自 {date} 起，UTC）已结算与调用中冻结的金额合计不能超过成员的每月额度。", { date: new Date(data.month_start * 1000).toLocaleDateString(locale, { timeZone: "UTC" }) })}</p>
    <FlowError error={action.error} />
    {saved && <p className="notice ok" role="status">{t("已保存「{name}」的付费设置。", { name: saved })}</p>}
    {!data.members.length ? <p className="muted">{t("这个组织还没有成员。")}</p> : <ul className="org-list">{data.members.map(m => {
      const d = draftOf(m); const fen = d.yuan.trim() ? parseYuan(d.yuan, MAX_LIMIT_YUAN) : null;
      const valid = d.enabled ? fen !== null : !d.yuan.trim() || fen !== null;
      const changed = d.enabled !== m.enabled || (fen ?? 0) !== (unitsToFen(m.monthly_limit, scale) ?? 0);
      const name = m.display_name || m.email;
      return <li key={m.user_id}>
        <div className="org-billing-who"><strong>{name}</strong>
          <div className="tiny muted org-billing-wrap">{m.display_name ? `${m.email} · ` : ""}{t(roleSource(m.role))}</div>
          <div className="tiny muted">{t("本月已用 {spent} · 冻结 {held}", { spent: money(m.spent_this_month), held: money(m.held) })}</div></div>
        <form className="org-actions" onSubmit={e => { e.preventDefault(); if (!valid || !changed) return; void action.run(async () => {
          setSaved(null);
          const next = await put<Billing>(`${orgPath(organization.id)}/billing/members/${encodeURIComponent(m.user_id)}`,
            { enabled: d.enabled, monthly_limit_minor: fen ?? 0, expected_revision: m.revision });
          setDrafts(without(m.user_id)); onSaved(next); setSaved(name);
        }); }}>
          <label className="row gap8"><input type="checkbox" checked={d.enabled} disabled={!editable || action.busy} onChange={e => setDrafts(v => ({ ...v, [m.user_id]: { ...d, enabled: e.target.checked } }))} />{t("可用组织付费")}</label>
          <label className="row gap8">{t("每月额度（元）")}<input className="input org-billing-amount" inputMode="decimal" autoComplete="off" value={d.yuan} aria-invalid={!valid}
            disabled={!editable || action.busy} onChange={e => setDrafts(v => ({ ...v, [m.user_id]: { ...d, yuan: e.target.value } }))} /></label>
          <button className="btn" disabled={!editable || !valid || !changed || action.busy} aria-label={t("保存「{name}」的付费设置", { name })}>{t("保存")}</button>
        </form>
      </li>; })}</ul>}
  </div>;
}

function OrganizationStatement({ organization, unit }: { organization: Organization; unit: Unit }) {
  const { t, locale } = useI18n(); const id = useId(); const action = useAction();
  const [items, setItems] = useState<StatementItem[] | null>(null); const [next, setNext] = useState<string | null>(null);
  async function load(after: string) {
    const page = await get<Statement>(`${orgPath(organization.id)}/billing/statement?limit=50&after_seq=${encodeURIComponent(after)}`);
    setItems(v => after === "0" ? page.items : [...(v || []), ...page.items]); setNext(page.next_after_seq);
  }
  useEffect(() => { setItems(null); void action.run(() => load("0")); }, [organization.id]);   // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="flow-stack" role="group" aria-labelledby={`${id}-h`}>
    <h3 id={`${id}-h`}>{t("组织账单")}</h3>
    <p className="tiny muted">{t("组织钱包的每一笔入账与扣费；消费注明成员，不包含任何对话内容。")}</p>
    <FlowError error={action.error} />
    {!items ? (action.error ? null : <Loading />) : !items.length ? <p className="muted">{t("还没有账单记录。")}</p> : <ul className="org-list">{items.map(r =>
      <li key={r.id}><div className="org-billing-who"><strong>{t(KIND[r.kind] || r.kind)}</strong>
        <div className="tiny muted org-billing-wrap">{new Date(r.created_at * 1000).toLocaleString(locale)}{r.member_email ? ` · ${r.member_email}` : ""}</div></div>
        <span className="org-billing-money">{r.delta.startsWith("-") ? "-" : "+"}{cnyText(r.delta.replace(/^-/, ""), unit.scale, locale)}</span></li>)}</ul>}
    {next && <button type="button" className="btn" disabled={action.busy} onClick={() => void action.run(() => load(next))}>{t("加载更多")}</button>}
  </div>;
}
