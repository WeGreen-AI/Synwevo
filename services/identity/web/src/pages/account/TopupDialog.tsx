import { useEffect, useId, useState } from 'react';
import { ApiError, get, post } from '@synwevo/auth-client';
import { Modal, useI18n, type Translator } from '@synwevo/ui';
import { isChallengeError, useChallenge } from '../org/Challenge';
import { ErrorNotice, errorText } from './common';
import { paymentMethodsText } from '../../lib/paymentMethods';
import { WALLET_API, minorFromText, payerKey, payerQuery, paymentText, safeCheckout, topupClosedReason, withQuery,
  type Payer, type TopupOptions } from './walletModel';
import './wallet.messages';

/** Why top-up is closed, in words; the codes are the top-up options' `reason`. */
export const UNAVAILABLE: Record<string, string> = {
  not_configured: '充值暂未开放。平台还没有接通支付渠道，此处不会创建任何订单。',
  email_unverified: '请先验证邮箱，再为钱包充值。',
  wallet_frozen: '这个钱包因一笔支付争议已被冻结，暂时不能充值。',
  not_permitted: '只有组织的付费管理者可以为组织钱包充值。',
};
const CLOSED_DEFAULT = UNAVAILABLE.not_configured;

/** The honest closed state: says why, offers the one useful next step, and no payment control. */
export function TopupClosed({ reason }: { reason: string }) {
  const { t } = useI18n();
  return <div className="notice info topup-closed" role="status">
    <p>{t(UNAVAILABLE[reason] || CLOSED_DEFAULT)}</p>
    {reason === 'email_unverified' && <p><a href="/account#email">{t('前往验证邮箱')}</a></p>}
  </div>;
}

/** An amount of one's own is offered only inside bounds the server published. */
export function customAllowed(options: TopupOptions): boolean {
  const { min_amount_minor: min, max_amount_minor: max } = options;
  return options.custom_amount !== false && Number.isSafeInteger(min) && Number.isSafeInteger(max) && min! > 0 && min! <= max!;
}

/** A refusal of the order itself, in words: too many attempts names when to try again. */
export function topupErrorText(error: unknown, t: Translator): string {
  if (error instanceof Error && error.message === 'checkout_address') return t('付款页面地址无效，未跳转。请稍后重试。');
  if (error instanceof ApiError && error.status === 429 && !error.upstream) {
    return error.retryAfter ? t('充值请求过于频繁，请在 {seconds} 秒后再试。', { seconds: Math.ceil(error.retryAfter) })
      : t('充值请求过于频繁，请稍后再试。');
  }
  return errorText(error, t);
}

/** The server-priced packages (and, when the server allows it, an amount inside its bounds). */
export function TopupChoices({ options, choice, custom, onChoice, onCustom }: {
  options: TopupOptions; choice: string; custom: string; onChoice: (id: string) => void; onCustom: (value: string) => void;
}) {
  const { t, locale } = useI18n();
  const legend = useId(), bounds = useId();
  const currency = options.currency || 'CNY';
  const ranged = customAllowed(options);
  const amount = minorFromText(custom, 2);
  const inRange = ranged && amount !== null && amount >= options.min_amount_minor! && amount <= options.max_amount_minor!;
  return <>
    <fieldset className="topup-packages" aria-labelledby={legend}>
      <legend id={legend}>{t('选择充值金额')}</legend>
      {options.packages.map(item => <label key={item.id} className={`topup-package${choice === item.id ? ' on' : ''}`}>
        <input type="radio" name="topup-package" value={item.id} checked={choice === item.id} onChange={() => onChoice(item.id)} />
        <span className="grow">{item.label}</span><b>{paymentText(item.amount_minor, currency, locale)}</b>
      </label>)}
      {ranged && <label className={`topup-package${choice === 'custom' ? ' on' : ''}`}>
        <input type="radio" name="topup-package" value="custom" checked={choice === 'custom'} onChange={() => onChoice('custom')} />
        <span className="grow">{t('其他金额')}</span>
      </label>}
    </fieldset>
    {ranged && choice === 'custom' && <label className="field">
      <span>{t('金额（{currency}）', { currency })}</span>
      <input className="input" inputMode="decimal" autoComplete="off" value={custom} onChange={e => onCustom(e.target.value)}
        aria-invalid={custom !== '' && !inRange} aria-describedby={bounds} />
      <span className="tiny muted" id={bounds}>{t('{min} 至 {max}，最多两位小数。', {
        min: paymentText(options.min_amount_minor!, currency, locale), max: paymentText(options.max_amount_minor!, currency, locale) })}</span>
    </label>}
  </>;
}

/** Choose a server-priced package (or an amount inside the server's bounds), then leave for the
 *  payment provider's hosted checkout. The amount is never decided here: the server prices the
 *  order and returns the checkout address; the wallet is credited only by the provider's signed
 *  confirmation, which the return page then reports. */
export default function TopupDialog({ payer, payerName, onClose }: { payer: Payer; payerName: string; onClose: () => void }) {
  const { t, locale } = useI18n();
  const [options, setOptions] = useState<TopupOptions | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [choice, setChoice] = useState<string>('');
  const [custom, setCustom] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [armed, setArmed] = useState(false);
  // Turnstile is the account service's decision (its challenge settings for "topup"); a refusal
  // asking for it arms the widget for the next attempt.
  const challenge = useChallenge('topup', { enabled: options?.available === true && !topupClosedReason(options, null), armed });
  const hint = useId();

  useEffect(() => {
    const abort = new AbortController();
    setOptions(null); setLoadError(null);
    get<TopupOptions>(withQuery(`${WALLET_API}/topup-options`, payerQuery(payer)), abort.signal)
      .then(result => { setOptions(result); setChoice(result.packages[0]?.id || (customAllowed(result) ? 'custom' : '')); })
      .catch(e => { if (!abort.signal.aborted) setLoadError(e); });
    return () => abort.abort();
  }, [payerKey(payer)]);   // eslint-disable-line react-hooks/exhaustive-deps

  const closed = topupClosedReason(options, loadError instanceof ApiError ? loadError.status ?? null : null);
  const amount = choice === 'custom' ? minorFromText(custom, 2) : null;
  const inRange = amount !== null && !!options && customAllowed(options)
    && amount >= options.min_amount_minor! && amount <= options.max_amount_minor!;
  const open = !!options?.available && !closed;
  const ready = open && (choice === 'custom' ? inRange : !!choice) && (!challenge.required || !!challenge.token);

  async function submit() {
    if (!ready) return;
    setBusy(true); setError(null);
    try {
      const body = { payer_kind: payer.kind, ...(payer.kind === 'organization' ? { payer_id: payer.id } : {}),
        ...(choice === 'custom' ? { amount_minor: amount } : { package_id: choice }), ...challenge.body() };
      const created = await post<{ order_id: string; checkout_url: string }>(`${WALLET_API}/topups`, body);
      const target = safeCheckout(created.checkout_url);
      if (!target) throw new Error('checkout_address');
      window.location.assign(target);
    } catch (e) {
      if (isChallengeError(e)) { setArmed(true); challenge.reset(); }
      setError(e); setBusy(false);
    }
  }

  const footer = open ? <>
    <button type="button" className="btn" onClick={onClose} disabled={busy}>{t('取消')}</button>
    <button type="button" className="btn primary" onClick={() => void submit()} disabled={!ready || busy} aria-describedby={hint}>
      {t(busy ? '正在前往付款…' : '前往付款')}</button>
  </> : <button type="button" className="btn" onClick={onClose}>{t('关闭')}</button>;

  return <Modal title={t('为「{name}」充值', { name: payerName })} onClose={onClose} footer={footer} size="narrow">
    <div className="col gap12 topup" aria-busy={!options && !loadError}>
      {!options && !loadError && <p role="status">{t('正在读取充值选项…')}</p>}
      {closed && <TopupClosed reason={closed} />}
      {!closed && <ErrorNotice error={loadError ? errorText(loadError, t) : null} />}
      {open && options && <>
        <TopupChoices options={options} choice={choice} custom={custom} onChoice={setChoice} onCustom={setCustom} />
        {challenge.element}
        <p className="small muted" id={hint}>{t('付款在支付服务商的安全页面完成，可用支付方式：{methods}。到账以支付服务商的确认为准；充值后不可退款，余额只能用于 Synwevo 的服务。', {
          methods: paymentMethodsText(options.payment_methods, t, locale) })}</p>
        <ErrorNotice error={error ? topupErrorText(error, t) : null} />
      </>}
    </div>
  </Modal>;
}
