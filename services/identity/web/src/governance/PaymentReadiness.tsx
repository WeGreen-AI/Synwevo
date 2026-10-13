import { useEffect, useId, useRef, useState } from "react";
import { get } from "@synwevo/auth-client";
import { fmtTime, useI18n } from "@synwevo/ui";
import { errText, isAbort } from "./labels";
import { PAYMENT_METHODS, PAYMENT_METHOD_LABELS, paymentMethodsText, type PaymentMethod } from "../lib/paymentMethods";
import "./payments.messages";

type Readiness = {
  revision: number;
  credential_revision: number;
  checked_at: number;
  provider: "stripe";
  environment: "test" | "live";
  charges_enabled: boolean;
  methods: Record<PaymentMethod, boolean | null>;
  configuration_status: "available" | "missing" | "ambiguous" | "incomplete" | "inactive";
  /** Whether Stripe has activated each method for the account (its capability, or availability in the
   *  default configuration); null when its answer proves neither. The publish check refuses `false`. */
  activated?: Record<PaymentMethod, boolean | null>;
  /** The Stripe API version Stripe applied to the check, and the one this platform pins on every request. */
  api_version?: string | null;
  api_version_requested?: string;
};
type Props = { revision: number; savedMethods: PaymentMethod[]; hasDraft: boolean };
type Check = { phase: "idle" | "checking" | "stale" } | { phase: "ready"; result: Readiness } | { phase: "error"; error: unknown };

const API_VERSION = /^\d{4}-\d{2}-\d{2}(\.[a-z]{1,20})?$/;
const version = (value: unknown) => (typeof value === "string" && API_VERSION.test(value) ? value : null);

const CONFIGURATION_NOTICES: Record<Exclude<Readiness["configuration_status"], "available">, string> = {
  missing: "未找到 Stripe 默认支付配置，渠道状态未能核实。",
  ambiguous: "无法确定唯一的 Stripe 默认支付配置，渠道状态未能核实。",
  incomplete: "Stripe 默认支付配置返回的信息不完整，部分渠道状态未能核实。",
  inactive: "Stripe 默认支付配置未启用，请到 Stripe 检查。",
};

/** Mounted only within the billing-view permission gate. A saved revision owns both the
 * request and its result; changing it unmounts the old check before any result can be shown. */
export function PaymentReadiness(props: Props) {
  return <SavedPaymentReadiness key={props.revision} {...props} />;
}

function SavedPaymentReadiness({ revision, savedMethods, hasDraft }: Props) {
  const { t, locale } = useI18n();
  const id = useId();
  const [check, setCheck] = useState<Check>({ phase: "idle" });
  const flight = useRef<AbortController | null>(null);
  const checkButton = useRef<HTMLButtonElement>(null);
  useEffect(() => () => { flight.current?.abort(); flight.current = null; }, []);

  async function inspect() {
    if (flight.current) return;
    const request = new AbortController();
    flight.current = request;
    const current = () => flight.current === request && !request.signal.aborted;
    setCheck({ phase: "checking" });
    try {
      const result = await get<Readiness>("/api/admin/billing/payments/readiness", request.signal);
      if (!current()) return;
      // Another administrator may have published while this page still has an older revision.
      // Never adopt the response's revision or imply that it checked this page's saved choices.
      setCheck(result.revision === revision ? { phase: "ready", result } : { phase: "stale" });
    } catch (error) {
      if (current()) setCheck(isAbort(error) ? { phase: "idle" } : { phase: "error", error });
    } finally {
      if (current()) flight.current = null;
    }
  }

  function cancel() {
    flight.current?.abort();
    flight.current = null;
    setCheck({ phase: "idle" });
    checkButton.current?.focus();
  }

  const result = check.phase === "ready" ? check.result : null;
  // Not activated is what makes Stripe refuse a checkout; an older answer without that evidence
  // falls back to the default configuration's flag.
  const unavailable = result ? savedMethods.filter(method => (result.activated ? result.activated[method] : result.methods[method]) === false) : [];
  const applied = version(result?.api_version), pinned = version(result?.api_version_requested);
  return <section className="col gap12" aria-labelledby={`${id}-title`} style={{ minWidth: 0, overflowWrap: "anywhere" }}>
    <h3 id={`${id}-title`}>{t("已保存的支付渠道检查")}</h3>
    <p className="small muted" id={`${id}-scope`}>{t("仅检查当前已保存版本的凭据与渠道；未发布的草稿和新密钥不在检查范围内。充值关闭时也可检查已保存凭据。")}</p>
    {hasDraft && <p className="notice warn" role="note">{t("你正在编辑支付设置或密钥。检查结果仅针对当前保存版本，不代表草稿或新密钥已经验证；发布后请重新检查。")}</p>}
    <div className="row gap8 wrap">
      <button ref={checkButton} type="button" className="btn" aria-disabled={check.phase === "checking"} aria-describedby={`${id}-scope`}
        style={{ whiteSpace: "normal", height: "auto", paddingBlock: "var(--sp-1)", maxWidth: "100%" }}
        onClick={() => void inspect()}>{t("检查已保存的支付渠道")}</button>
      {check.phase === "checking" && <button type="button" className="btn ghost" onClick={cancel}>{t("取消检查")}</button>}
    </div>
    {check.phase === "checking" && <p className="small muted" role="status">{t("正在检查已保存的支付渠道…")}</p>}
    {check.phase === "error" && <div className="notice err" role="alert">{errText(check.error)}</div>}
    {check.phase === "stale" && <p className="notice warn" role="status">{t("已保存的支付设置已变化，本次结果已丢弃。请刷新支付设置后重新检查。")}</p>}
    {result && <div className="col gap12" aria-live="polite">
      <p className="small muted">{t("检查范围：已保存第 {n} 版 · {env}；检查时间：{time}。", {
        n: revision, env: t(result.environment === "live" ? "正式环境" : "测试环境"), time: fmtTime(result.checked_at),
      })}</p>
      {applied && <p className="small muted">{t("Stripe API 版本：{version}", { version: applied })}</p>}
      {applied && pinned && applied !== pinned && <p className="notice warn" role="note">{t("Stripe 应用的 API 版本（{applied}）与平台固定的版本（{pinned}）不一致，请检查后再开放充值。", { applied, pinned })}</p>}
      {!result.charges_enabled && <div className="notice err" role="alert"><strong>{t("Stripe 账户尚未启用收款。即使默认配置显示渠道可用，也不能据此认定可以收款；请到 Stripe 检查账户状态。")}</strong></div>}
      {result.configuration_status !== "available" && <p className="notice warn" role="note">{t(CONFIGURATION_NOTICES[result.configuration_status])}</p>}
      <dl className="col gap8" style={{ margin: 0 }}>
        {PAYMENT_METHODS.map(method => <div key={method} className="row gap8 wrap">
          <dt>{t(PAYMENT_METHOD_LABELS[method])}</dt>
          <dd style={{ margin: 0, color: result.methods[method] === false ? "var(--warn)" : "var(--text-2)" }}>
            {t(result.methods[method] === true ? "默认配置可用" : result.methods[method] === false ? "默认配置不可用" : "未能核实")}
          </dd>
          {typeof result.activated?.[method] === "boolean" && <dd style={{ margin: 0 }} className="small muted">
            {t(result.activated[method] ? "Stripe 账户已开通此方式" : "Stripe 账户未开通此方式")}</dd>}
        </div>)}
      </dl>
      {!!unavailable.length && <p className="notice warn" role="note">{t("已保存的选择中包含 Stripe 报告尚未开通的渠道：{methods}。支付服务商会拒绝为包含这些渠道的充值创建付款页面。请到 Stripe 开通，或通过“检查并发布”取消选择它们。", {
        methods: paymentMethodsText(unavailable, t, locale),
      })}</p>}
    </div>}
    <p className="small muted">{t("Stripe 默认配置的可用状态不代表最终渠道资格、币种支持或订单成功。收银台显式指定的支付方式可能与默认配置展示不同；此检查不会更改所选渠道。发布开放充值的设置时，服务器会再做一次同样的检查：拒绝发布未开通的渠道，无法核实时需要明确确认。")}</p>
  </section>;
}
