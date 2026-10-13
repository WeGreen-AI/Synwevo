import { useId, useState } from "react";
import { post } from "@synwevo/auth-client";
import { fmtTime, useI18n } from "@synwevo/ui";
import { ActionDialog } from "./ActionDialog";
import { useAdminData } from "./data";
import { cnyText, fenText, parseYuan } from "./billing-money";
import "./payments.messages";
/**
 * The finance view (synwevo_identity.admin_payments GET /api/admin/billing/finance): top-ups,
 * grants, chargebacks and the unused wallet balances on the base dates (March 31 and
 * September 30, Japan time), as amounts and metadata only — never a conversation or a card.
 * Granting credit and lifting a chargeback freeze are dangerous operations with a reason.
 */

type Unit = { code: string; scale: number };
type Payment = { order_id: string; payer_kind: string; payer_id: string; payer_email: string | null; organization_name: string | null;
  initiator_email: string; amount_minor: number; status: string; method: string | null; created_at: number; credited_at: number | null;
  /** Why an order failed or is unresolved (stored code), its sanitized provider diagnosis and the person's reason code. */
  failure_code?: string | null; failure_detail?: string | null; failure_reason?: string | null };
type GrantRow = { id: string; kind: "welcome" | "admin"; payer_kind: string; payer_email: string | null; organization_name: string | null;
  amount: number; reason: string; actor_email: string | null; created_at: number };
type Chargeback = { id: string; dispute_id: string; amount_minor: number; disputed_units: number; recovered_units: number;
  outstanding_units: number; created_at: number; payer_kind: string; payer_email: string | null; organization_name: string | null;
  freeze_id: string | null; frozen: boolean; lift_reason: string | null };
type Unused = { cutoff: number; unused_units: number; wallets_with_history: number; by_kind: Record<string, number>; date?: string };
type Finance = { environment: string | null; configured: boolean; unit: Unit | null;
  totals: { paid_minor: number; paid_count: number; welcome_units: number; admin_grant_units: number; recovered_units: number;
    outstanding_units: number; chargeback_count: number; webhook_failures: number; frozen_wallets: number } | null;
  payments: Payment[]; grants: GrantRow[]; chargebacks: Chargeback[]; current?: Unused; base_dates: Unused[] };

const STATUS: Record<string, string> = { pending: "等待付款", paid: "已入账", failed: "失败", expired: "已过期", creating: "创建中" };
const METHOD: Record<string, string> = { card: "银行卡", wechat_pay: "微信支付", alipay: "支付宝" };
/** The stored failure codes (payments.py), in words; an unknown code is shown as itself. */
const FAILURE: Record<string, string> = {
  provider_refused: "支付服务拒绝创建付款页面",
  provider_unknown: "未能确认付款页面是否已创建",
  provider_unavailable: "支付服务暂时不可用",
  order_refused: "账本未能登记订单",
  dispatch_refused: "发起前付款方权限或支付配置已变化",
  expired: "付款页面已过期（支付服务通知）",
  async_payment_failed: "延后确认的付款失败（支付服务通知）",
};
/** The reason code the person was shown (walletModel.TOPUP_FAILURE_REASONS), for an administrator. */
const REASON: Record<string, string> = {
  payment_method_not_enabled: "所选支付方式未在支付服务商处开通",
  provider_configuration: "支付密钥、权限或账户配置有误",
  provider_rejected: "支付服务拒绝了请求",
};
/** The sanitized diagnosis is shown only in its documented alphabet (a-z, 0-9, _ . ; =). */
const detailText = (value: unknown) => (typeof value === "string" && /^[a-z0-9_.;=]{1,200}$/.test(value) ? value : null);

/** Why one top-up failed or is still unresolved: the stored code in words, the reason the person
 *  saw, and the provider's sanitized diagnosis (never its message, a key or an account). */
export function PaymentFailure({ payment }: { payment: Pick<Payment, "failure_code" | "failure_detail" | "failure_reason"> }) {
  const { t } = useI18n();
  const code = payment.failure_code, detail = detailText(payment.failure_detail);
  const reason = payment.failure_reason && Object.hasOwn(REASON, payment.failure_reason) ? REASON[payment.failure_reason] : null;
  if (!code && !detail) return null;
  return <div className="cell-note">
    {code && (Object.hasOwn(FAILURE, code) ? t(FAILURE[code]) : <code>{code}</code>)}{reason && <>{" · "}{t(reason)}</>}
    {detail && <div><code style={{ overflowWrap: "anywhere" }}>{detail}</code></div>}
  </div>;
}
const payer = (row: { payer_kind: string; payer_email: string | null; organization_name: string | null },
  t: (s: string, p?: Record<string, string>) => string) =>
  row.payer_kind === "organization" ? t("组织：{name}", { name: row.organization_name || "—" }) : row.payer_email || "—";

export function FinanceTab({ onDenied, canManage }: { onDenied: () => void; canManage: boolean }) {
  const { t, locale } = useI18n();
  const id = useId();
  const load = useAdminData<Finance>("/api/admin/billing/finance", onDenied);
  const [lifting, setLifting] = useState<Chargeback | null>(null);
  const [granting, setGranting] = useState(false);
  const [grant, setGrant] = useState({ kind: "user", target: "", yuan: "" });
  const [done, setDone] = useState<string | null>(null);
  if (load.loading) return <p role="status">{t("加载中…")}</p>;
  if (load.err) return <div className="notice err" role="alert">{load.err} <button className="btn sm" onClick={load.reload}>{t("重试")}</button></div>;
  const data = load.data;
  if (!data) return null;
  if (!data.configured || !data.unit || !data.totals)
    return <p className="notice" role="note">{t("此部署尚未配置计费环境或 CNY 钱包单位，没有资金记录可以显示。")}</p>;
  const unit = data.unit, totals = data.totals;
  const units = (n: number) => cnyText(n, unit.scale, locale);
  const grantFen = parseYuan(grant.yuan, 10000);
  const cards: [string, string][] = [
    ["已入账充值", `${fenText(totals.paid_minor, locale)} · ${t("{n} 笔", { n: totals.paid_count })}`],
    ["欢迎额度", units(totals.welcome_units)], ["管理员赠送", units(totals.admin_grant_units)],
    ["拒付追回", units(totals.recovered_units)], ["拒付欠款", units(totals.outstanding_units)],
    ["冻结的钱包", String(totals.frozen_wallets)], ["支付通知失败", String(totals.webhook_failures)],
  ];
  return <section className="col gap16" aria-labelledby={`${id}-h`}>
    <div className="row gap12 wrap"><h2 id={`${id}-h`} className="grow">{t("财务")}</h2>
      {canManage && <button className="btn sm" onClick={() => setGranting(true)}>{t("赠送额度")}</button>}
      <button className="btn sm" onClick={load.reload}>{t("刷新")}</button></div>
    <p className="muted small">{t("只显示金额与元数据（付款方、时间、方式、理由），不包含任何对话或文档内容。环境：{env}。", { env: data.environment || "—" })}</p>
    {done && <div className="notice ok" role="status">{t(done)}</div>}
    <ul className="row gap12 wrap" style={{ listStyle: "none", padding: 0, margin: 0 }}>
      {cards.map(([label, value]) => <li key={label} className="card card-b col gap4" style={{ flex: "1 1 160px", minWidth: 0 }}>
        <span className="muted small">{t(label)}</span><strong style={{ overflowWrap: "anywhere" }}>{value}</strong></li>)}
    </ul>

    <section className="card card-b col gap8" aria-labelledby={`${id}-unused`}>
      <h3 id={`${id}-unused`}>{t("未使用余额（基准日）")}</h3>
      <p className="muted small">{t("每年 3 月 31 日与 9 月 30 日日终（日本时间）时所有个人与组织钱包的余额合计，含赠送额度，供预付式支付手段申报参考。")}</p>
      <div className="list-wrap"><table className="gov">
        <thead><tr><th scope="col">{t("基准日")}</th><th scope="col">{t("未使用余额")}</th><th scope="col">{t("其中赠送")}</th><th scope="col">{t("有记录的钱包")}</th></tr></thead>
        <tbody>{[...(data.current ? [{ ...data.current, date: t("当前") }] : []), ...data.base_dates].map((row) => <tr key={row.cutoff}>
          <th scope="row">{row.date}</th><td>{units(row.unused_units)}</td><td>{units(row.by_kind.grant || 0)}</td><td>{row.wallets_with_history}</td></tr>)}</tbody>
      </table></div>
    </section>

    <section className="card card-b col gap8" aria-labelledby={`${id}-pay`}>
      <h3 id={`${id}-pay`}>{t("最近的充值")}</h3>
      {!data.payments.length ? <p className="muted">{t("还没有充值订单。")}</p> : <div className="list-wrap"><table className="gov">
        <thead><tr><th scope="col">{t("时间")}</th><th scope="col">{t("付款方")}</th><th scope="col">{t("金额")}</th><th scope="col">{t("方式")}</th><th scope="col">{t("状态")}</th></tr></thead>
        <tbody>{data.payments.map((p) => <tr key={p.order_id}>
          <th scope="row">{fmtTime(p.created_at)}<div className="cell-note">{p.order_id}</div></th>
          <td>{payer(p, t)}{p.payer_kind === "organization" && <div className="cell-note">{t("发起人：{who}", { who: p.initiator_email })}</div>}</td>
          <td>{fenText(p.amount_minor, locale)}</td><td>{p.method ? t(METHOD[p.method] || p.method) : "—"}</td>
          <td>{t(STATUS[p.status] || p.status)}{p.credited_at && <div className="cell-note">{fmtTime(p.credited_at)}</div>}
            {p.status !== "paid" && <PaymentFailure payment={p} />}</td></tr>)}</tbody>
      </table></div>}
    </section>

    <section className="card card-b col gap8" aria-labelledby={`${id}-grants`}>
      <h3 id={`${id}-grants`}>{t("赠送记录")}</h3>
      {!data.grants.length ? <p className="muted">{t("还没有赠送。")}</p> : <div className="list-wrap"><table className="gov">
        <thead><tr><th scope="col">{t("时间")}</th><th scope="col">{t("类型")}</th><th scope="col">{t("接收方")}</th><th scope="col">{t("金额")}</th><th scope="col">{t("理由与操作人")}</th></tr></thead>
        <tbody>{data.grants.map((g) => <tr key={g.id}>
          <th scope="row">{fmtTime(g.created_at)}</th><td>{t(g.kind === "welcome" ? "欢迎额度" : "管理员赠送")}</td>
          <td>{payer(g, t)}</td><td>{units(g.amount)}</td>
          <td>{g.kind === "welcome" ? t("每个账户一次的欢迎额度") : g.reason}<div className="cell-note">{g.actor_email || t("系统")}</div></td></tr>)}</tbody>
      </table></div>}
    </section>

    <section className="card card-b col gap8" aria-labelledby={`${id}-cb`}>
      <h3 id={`${id}-cb`}>{t("拒付与冻结")}</h3>
      <p className="muted small">{t("拒付发生时，系统追回钱包中仍可用的部分，其余记为欠款，并冻结该钱包（不能再发起付费调用或充值）。钱包余额永远不会为负。")}</p>
      {!data.chargebacks.length ? <p className="muted">{t("没有拒付。")}</p> : <div className="list-wrap"><table className="gov">
        <thead><tr><th scope="col">{t("时间")}</th><th scope="col">{t("付款方")}</th><th scope="col">{t("争议金额")}</th><th scope="col">{t("已追回 / 欠款")}</th><th scope="col" className="act">{t("冻结")}</th></tr></thead>
        <tbody>{data.chargebacks.map((c) => <tr key={c.id}>
          <th scope="row">{fmtTime(c.created_at)}<div className="cell-note">{c.dispute_id}</div></th><td>{payer(c, t)}</td>
          <td>{fenText(c.amount_minor, locale)}</td><td>{units(c.recovered_units)} / {units(c.outstanding_units)}</td>
          <td className="act">{c.frozen ? <>{t("已冻结")}{canManage && c.freeze_id && <> <button className="btn sm" onClick={() => setLifting(c)}>{t("解除冻结")}</button></>}</>
            : <span>{t("已解除")}{c.lift_reason && <span className="cell-note">{c.lift_reason}</span>}</span>}</td></tr>)}</tbody>
      </table></div>}
    </section>

    {lifting && <ActionDialog title={t("解除冻结")} confirmLabel={t("解除冻结")} reason="required"
      impact={<p>{t("解除后，{who} 的钱包可以再次发起付费调用与充值。拒付记录与欠款保留不变。", { who: payer(lifting, t) })}</p>}
      run={(reason) => post(`/api/admin/billing/freezes/${encodeURIComponent(lifting.freeze_id || "")}/lift`, { reason })}
      onDone={() => { setLifting(null); setDone("已解除冻结。"); load.reload(); }} onClose={() => setLifting(null)} />}
    {granting && <ActionDialog title={t("赠送额度")} confirmLabel={t("赠送")} reason="required"
      ready={!!grant.target.trim() && grantFen !== null}
      impact={<p>{t("赠送记为“赠送”分录（不是付款），写明理由与操作人，不能撤回。")}</p>}
      run={(reason) => post("/api/admin/billing/grants", {
        payer_kind: grant.kind, ...(grant.kind === "user" ? { email: grant.target.trim() } : { payer_id: grant.target.trim() }),
        amount_minor: grantFen, reason, idempotency_key: "grant_" + crypto.randomUUID().replace(/-/g, "") })}
      onDone={() => { setGranting(false); setGrant({ kind: "user", target: "", yuan: "" }); setDone("已赠送。"); load.reload(); }}
      onClose={() => setGranting(false)}>
      <fieldset className="col gap8" style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
        <legend>{t("接收方")}</legend>
        <label className="row gap8"><input type="radio" name={`${id}-kind`} checked={grant.kind === "user"} onChange={() => setGrant({ ...grant, kind: "user" })} />{t("个人账户（邮箱）")}</label>
        <label className="row gap8"><input type="radio" name={`${id}-kind`} checked={grant.kind === "organization"} onChange={() => setGrant({ ...grant, kind: "organization" })} />{t("组织（组织 ID）")}</label>
        <input className="input" aria-label={t(grant.kind === "user" ? "邮箱" : "组织 ID")} value={grant.target}
          onChange={(e) => setGrant({ ...grant, target: e.target.value })} />
        <label className="col gap4">{t("金额（元，最多 10000）")}
          <input className="input" inputMode="decimal" value={grant.yuan} aria-invalid={!!grant.yuan && grantFen === null}
            onChange={(e) => setGrant({ ...grant, yuan: e.target.value })} /></label>
      </fieldset>
    </ActionDialog>}
  </section>;
}
