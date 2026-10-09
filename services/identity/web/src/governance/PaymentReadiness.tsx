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
};
type Props = { revision: number; savedMethods: PaymentMethod[]; hasDraft: boolean };
type Check = { phase: "idle" | "checking" | "stale" } | { phase: "ready"; result: Readiness } | { phase: "error"; error: unknown };

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
  const unavailable = result ? savedMethods.filter(method => result.methods[method] === false) : [];
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
      {!result.charges_enabled && <div className="notice err" role="alert"><strong>{t("Stripe 账户尚未启用收款。即使默认配置显示渠道可用，也不能据此认定可以收款；请到 Stripe 检查账户状态。")}</strong></div>}
      {result.configuration_status !== "available" && <p className="notice warn" role="note">{t(CONFIGURATION_NOTICES[result.configuration_status])}</p>}
      <dl className="col gap8" style={{ margin: 0 }}>
        {PAYMENT_METHODS.map(method => <div key={method} className="row gap8 wrap">
          <dt>{t(PAYMENT_METHOD_LABELS[method])}</dt>
          <dd style={{ margin: 0, color: result.methods[method] === false ? "var(--warn)" : "var(--text-2)" }}>
            {t(result.methods[method] === true ? "默认配置可用" : result.methods[method] === false ? "默认配置不可用" : "未能核实")}
          </dd>
        </div>)}
      </dl>
      {!!unavailable.length && <p className="notice warn" role="note">{t("已保存的选择中包含默认配置不可用的渠道：{methods}。请到 Stripe 检查；如需调整，请通过原有“检查并发布”流程修改。", {
        methods: paymentMethodsText(unavailable, t, locale),
      })}</p>}
    </div>}
    <p className="small muted">{t("Stripe 默认配置的可用状态不代表最终渠道资格、币种支持或订单成功。收银台显式指定的支付方式可能与默认配置展示不同；此检查不会更改所选渠道，也不会阻止发布。")}</p>
  </section>;
}
