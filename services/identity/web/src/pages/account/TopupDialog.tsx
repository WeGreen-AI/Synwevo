import { useEffect, useId, useRef, useState } from 'react';
import { ApiError, get, post, useSession } from '@synwevo/auth-client';
import { Modal, useI18n, type Translator } from '@synwevo/ui';
import { isChallengeError, useChallenge } from '../org/Challenge';
import { ErrorNotice, errorText } from './common';
import { paymentMethodsText } from '../../lib/paymentMethods';
import { WALLET_API, minorFromText, payerKey, payerQuery, paymentText, safeCheckout, topupClosedReason, withQuery,
  type Payer, type TopupOptions } from './walletModel';
import './wallet.messages';
import './topup.messages';

export interface TopupContext {
  environment: 'test' | 'live'; initiator_id: string; payer_kind: Payer['kind']; payer_id: string;
}
type RecoveryOptions = TopupOptions & TopupContext;
export interface TopupSelection { package_id?: string; amount_minor?: number; quoted_amount_minor: number }
export interface TopupIntent extends TopupContext, TopupSelection { version: 1; idempotency_key: string; order_id?: string }
export interface TopupReply {
  order_id: string; status: 'pending' | 'paid' | 'failed' | 'expired';
  creation_status: 'ready' | 'verifying' | 'blocked' | 'resolved'; checkout_url: string | null;
  payer_kind: Payer['kind']; payer_id: string; amount_minor: number;
}
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9_-]{1,200}$/.test(value);
const positive = (value: unknown): value is number => Number.isSafeInteger(value) && Number(value) > 0;

export function topupStorageKey(context: TopupContext): string {
  if (!['test', 'live'].includes(context.environment) || !identifier(context.initiator_id)
    || !['user', 'organization'].includes(context.payer_kind) || !identifier(context.payer_id)) throw new Error('topup_context');
  return `synwevo:topup:v1:${context.environment}:${context.initiator_id}:${context.payer_kind}:${context.payer_id}`;
}

/** Keep only the original request and its known order id. No checkout URL or challenge is stored. */
export function topupRequest(intent: TopupIntent) {
  return { environment: intent.environment, initiator_id: intent.initiator_id,
    payer_kind: intent.payer_kind, payer_id: intent.payer_id, idempotency_key: intent.idempotency_key,
    ...(intent.package_id !== undefined ? { package_id: intent.package_id } : { amount_minor: intent.amount_minor }) };
}
export function topupSettled(reply: TopupReply | null): boolean {
  return !!reply && ['paid', 'failed', 'expired'].includes(reply.status) && reply.creation_status === 'resolved';
}

/** A tab's recoverable submission. A failed read/write blocks dispatch: silently replacing a
 * lost intent would undo the retry guarantee. The injected sender/storage are isolated test seams. */
export class TopupAttempt {
  readonly storageKey: string;
  intent: TopupIntent | null = null;
  reply: TopupReply | null = null;
  private inflight: Promise<TopupReply> | null = null;
  constructor(readonly context: TopupContext, private storage: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> = sessionStorage) {
    this.storageKey = topupStorageKey(context);
    try {
      const raw = storage.getItem(this.storageKey);
      if (raw === null) return;
      const saved = JSON.parse(raw);
      const allowed = ['version', 'idempotency_key', 'environment', 'initiator_id', 'payer_kind', 'payer_id',
        'package_id', 'amount_minor', 'quoted_amount_minor', 'order_id'];
      if (!saved || Object.keys(saved).some(key => !allowed.includes(key)) || saved.version !== 1
        || topupStorageKey(saved) !== this.storageKey || !identifier(saved.idempotency_key)
        || !positive(saved.quoted_amount_minor) || (saved.order_id !== undefined && !identifier(saved.order_id))
        || ((saved.package_id !== undefined) === (saved.amount_minor !== undefined))
        || (saved.package_id !== undefined && (typeof saved.package_id !== 'string' || !saved.package_id || saved.package_id.length > 40))
        || (saved.amount_minor !== undefined && !positive(saved.amount_minor))) throw new Error('invalid');
      this.intent = saved;
    } catch { throw new Error('topup_storage'); }
  }
  private save(intent: TopupIntent) {
    try { this.storage.setItem(this.storageKey, JSON.stringify(intent)); }
    catch { throw new Error('topup_storage'); }
    this.intent = intent;
  }
  startNew() {
    if (this.inflight || !topupSettled(this.reply)) throw new Error('topup_unresolved');
    try { this.storage.removeItem(this.storageKey); } catch { throw new Error('topup_storage'); }
    this.intent = null; this.reply = null;
  }
  submit(selection?: TopupSelection, challenge: Record<string, string> = {},
    send: (path: string, body: unknown) => Promise<unknown> = post): Promise<TopupReply> {
    if (this.inflight) return this.inflight;
    this.inflight = this.perform(selection, challenge, send).finally(() => { this.inflight = null; });
    return this.inflight;
  }
  private async perform(selection: TopupSelection | undefined, challenge: Record<string, string>,
    send: (path: string, body: unknown) => Promise<unknown>): Promise<TopupReply> {
    const first = this.intent === null;
    if (first) {
      if (!selection || !positive(selection.quoted_amount_minor)
        || ((selection.package_id !== undefined) === (selection.amount_minor !== undefined))) throw new Error('topup_context');
      this.save({ ...this.context, version: 1, idempotency_key: crypto.randomUUID(),
        quoted_amount_minor: selection.quoted_amount_minor,
        ...(selection.package_id !== undefined ? { package_id: selection.package_id } : { amount_minor: selection.amount_minor }) });
    }
    const intent = this.intent!;
    if (selection && (selection.package_id !== intent.package_id || selection.amount_minor !== intent.amount_minor))
      throw new Error('topup_unresolved');
    try {
      const raw = await send(`${WALLET_API}/topups`, { ...topupRequest(intent),
        ...(challenge.challenge_token ? { challenge_token: challenge.challenge_token } : {}) });
      const reply = raw as TopupReply;
      if (!reply || !identifier(reply.order_id) || (intent.order_id && reply.order_id !== intent.order_id)
        || reply.payer_kind !== intent.payer_kind || reply.payer_id !== intent.payer_id || !positive(reply.amount_minor)
        || !['pending', 'paid', 'failed', 'expired'].includes(reply.status)
        || !['ready', 'verifying', 'blocked', 'resolved'].includes(reply.creation_status)
        || ((reply.status !== 'pending') !== (reply.creation_status === 'resolved'))) throw new Error('topup_response_unknown');
      this.save({ ...intent, order_id: reply.order_id });
      this.reply = reply;
      return reply;
    } catch (error) {
      // Only a first, positively refused admission can be discarded. An error on a restored
      // intent may follow an earlier accepted request, so it never unlocks a new order.
      const beforeAdmission = ['challenge_required', 'challenge_failed', 'topup_amount_invalid', 'topup_idempotency_invalid',
        'topup_rate_limited', 'topup_pending_limit', 'topup_daily_amount', 'topup_not_configured', 'topup_context_changed'];
      // In particular, authority/email refusals may happen AFTER provider dispatch, when
      // _creation_result rechecks the account. They must retain the original intent too.
      if (first && error instanceof ApiError && !error.upstream && beforeAdmission.includes(error.code)) {
        try { this.storage.removeItem(this.storageKey); this.intent = null; } catch { /* retain it if removal failed */ }
      }
      throw error;
    }
  }
}

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
  if (error instanceof Error && error.message === 'topup_storage') return t('无法读取或保存原充值请求。请保留此页面并核实原订单，暂不能发起新充值。');
  if (error instanceof Error && error.message === 'topup_context') return t('充值账户或环境已变化，请返回原账户核实订单');
  if (error instanceof Error && ['topup_response_unknown', 'topup_unresolved'].includes(error.message))
    return t('正在核实原充值请求。重试会使用原请求；核实完成前不能更改金额或创建另一笔充值。');
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
export function topupNotice(reply: TopupReply | null): string {
  if (reply?.status === 'paid') return '原充值订单已到账，可查看收据。';
  if (reply?.status === 'failed') return '原充值订单已失败，可以重新选择金额。';
  if (reply?.status === 'expired') return '原充值订单已过期，可以重新选择金额。';
  if (reply?.creation_status === 'blocked') return '支付配置已变化，原订单暂不能继续付款。请核实原订单，不要重复创建。';
  return '正在核实原充值请求。重试会使用原请求；核实完成前不能更改金额或创建另一笔充值。';
}

export default function TopupDialog({ payer, payerName, onClose }: { payer: Payer; payerName: string; onClose: () => void }) {
  const { t, locale } = useI18n();
  const { me } = useSession();
  const [options, setOptions] = useState<RecoveryOptions | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [choice, setChoice] = useState<string>('');
  const [custom, setCustom] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [armed, setArmed] = useState(false);
  const [attempt, setAttempt] = useState<TopupAttempt | null>(null);
  const [reply, setReply] = useState<TopupReply | null>(null);
  const [reload, setReload] = useState(0);
  const active = useRef<TopupAttempt | null>(null);
  const submitting = useRef(false);
  const intent = attempt?.intent;
  const challenge = useChallenge('topup', { enabled: options?.available === true && !topupClosedReason(options, null)
    && (!intent || armed), armed });
  const hint = useId();

  useEffect(() => {
    const abort = new AbortController();
    setOptions(null); setLoadError(null); setAttempt(null); setReply(null); setError(null); setArmed(false);
    setBusy(false); submitting.current = false;
    get<RecoveryOptions>(withQuery(`${WALLET_API}/topup-options`, payerQuery(payer)), abort.signal)
      .then(result => {
        if (abort.signal.aborted) return;
        if (result.initiator_id !== me?.id || result.payer_kind !== payer.kind
          || result.payer_id !== (payer.kind === 'user' ? me?.id : payer.id)) throw new Error('topup_context');
        const restored = new TopupAttempt({ environment: result.environment, initiator_id: result.initiator_id,
          payer_kind: result.payer_kind, payer_id: result.payer_id });
        active.current = restored; setAttempt(restored); setOptions(result);
        setChoice(result.packages[0]?.id || (customAllowed(result) ? 'custom' : ''));
      })
      .catch(e => { if (!abort.signal.aborted) setLoadError(e); });
    return () => { abort.abort(); active.current = null; };
  }, [payerKey(payer), me?.id, reload]);   // eslint-disable-line react-hooks/exhaustive-deps

  const closed = topupClosedReason(options, loadError instanceof ApiError ? loadError.status ?? null : null);
  const amount = choice === 'custom' ? minorFromText(custom, 2) : null;
  const inRange = amount !== null && !!options && customAllowed(options)
    && amount >= options.min_amount_minor! && amount <= options.max_amount_minor!;
  const open = !!options?.available && !closed;
  const sameActor = !!options && options.initiator_id === me?.id && options.payer_kind === payer.kind
    && options.payer_id === (payer.kind === 'user' ? me?.id : payer.id);
  const settled = topupSettled(reply);
  const ready = !!attempt && sameActor && (intent ? !settled : open && (choice === 'custom' ? inRange : !!choice))
    && (!challenge.required || !!challenge.token);

  async function submit() {
    if (!ready || !attempt || submitting.current) return;
    const owner = attempt;
    submitting.current = true; setBusy(true); setError(null);
    try {
      const selected = owner.intent ? undefined : { ...(choice === 'custom' ? { amount_minor: amount! } : { package_id: choice }),
        quoted_amount_minor: choice === 'custom' ? amount! : options!.packages.find(item => item.id === choice)!.amount_minor };
      const created = await owner.submit(selected, challenge.body());
      if (active.current !== owner) return;
      setReply(created); setArmed(false);
      if (created.status === 'pending' && created.creation_status === 'ready') {
        const target = safeCheckout(created.checkout_url);
        if (!target || new URL(target).origin !== 'https://checkout.stripe.com') throw new Error('checkout_address');
        window.location.assign(target);
      }
    } catch (e) {
      if (active.current !== owner) return;
      if (isChallengeError(e)) { setArmed(true); challenge.reset(); }
      setError(e);
    } finally {
      if (active.current === owner) { submitting.current = false; setBusy(false); }
    }
  }

  function startNew() {
    try {
      attempt!.startNew(); setReply(null); setError(null); setReload(value => value + 1);
    } catch (e) { setError(e); }
  }
  const footer = <>
    <button type="button" className="btn" onClick={onClose} disabled={busy}>{t('关闭')}</button>
    {settled && <button type="button" className="btn" onClick={startNew} disabled={busy}>{t('重新选择金额')}</button>}
    {!settled && (open || intent) && <button type="button" className="btn primary" onClick={() => void submit()}
      disabled={!ready || busy} aria-describedby={hint}>
      {t(busy ? '正在核实充值…' : intent ? '核实并继续原订单' : '前往付款')}</button>}
  </>;
  const orderPage = payer.kind === 'organization' ? `/organizations/${encodeURIComponent(payer.id)}` : '/account/wallet';
  return <Modal title={t('为「{name}」充值', { name: payerName })} onClose={onClose} footer={footer} size="narrow">
    <div className="col gap12 topup" aria-busy={busy || (!options && !loadError)}>
      {!options && !loadError && <p role="status">{t('正在读取充值选项…')}</p>}
      {closed && <TopupClosed reason={closed} />}
      <ErrorNotice error={loadError ? topupErrorText(loadError, t) : null} />
      {intent && <div className="notice info" role="status">
        <p>{t(topupNotice(reply))}</p>
        <p>{t('原请求金额：{amount}', { amount: paymentText(reply?.amount_minor ?? intent.quoted_amount_minor, 'CNY', locale) })}</p>
        {intent.order_id && <a href={`${orderPage}?topup=${encodeURIComponent(intent.order_id)}`}>{t('查看原订单')}</a>}
      </div>}
      {open && options && !intent && <TopupChoices options={options} choice={choice} custom={custom} onChoice={setChoice} onCustom={setCustom} />}
      {challenge.element}
      {open && options && <p className="small muted" id={hint}>{t('付款在支付服务商的安全页面完成，可用支付方式：{methods}。到账以支付服务商的确认为准；充值后不可退款，余额只能用于 Synwevo 的服务。', {
        methods: paymentMethodsText(options.payment_methods, t, locale) })}</p>}
      <ErrorNotice error={error ? topupErrorText(error, t) : null} />
    </div>
  </Modal>;
}
