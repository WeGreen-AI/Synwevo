import { useCallback, useEffect, useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { ApiError } from '@synwevo/auth-client';
import { Modal, useI18n, type Params } from '@synwevo/ui';
import { read } from '../../shared/ops';
import { Failure, Loading } from '../../shared/states';
import {
  boundedCount, formatAmount, formatRate, parseBudget, quoteExpired, sameIntent, validateOffer, validateQuote,
  type BudgetIntent, type Offer, type Quote, type QuoteParams,
} from './budget';
import { budgetCopy, type BudgetText } from './budget.messages';

export type { BudgetIntent, Offer, Quote, QuoteParams } from './budget';
export interface BudgetDialogProps {
  intent: BudgetIntent;
  /** The parent owns the fixed intent and reuses its request key for retries of these parameters. */
  onQuote: (params: QuoteParams) => Promise<Quote>;
  /** The parent must retry this exact quote with the original confirmation key. */
  onConfirm: (quote: Quote) => Promise<unknown>;
  onClose: () => void;
}
type Phase = 'loading' | 'offerError' | 'editing' | 'quoting' | 'quoteError'
  | 'review' | 'confirming' | 'confirmError' | 'confirmed';
const stack: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)', minWidth: 0, overflowWrap: 'anywhere' };
const actions: CSSProperties = { display: 'flex', flexWrap: 'wrap', gap: 'var(--sp-2)', width: '100%' };
const action: CSSProperties = { height: 'auto', minHeight: 44, whiteSpace: 'normal' };

function errorCode(error: unknown): string {
  if (!(error instanceof ApiError) || error.upstream) return '';
  const code = error.code === 'idempotent_failure' ? error.detail?.code : error.code;
  return typeof code === 'string' ? code.replace(/^billing_/, '') : '';
}
// Only explicit refusals allow replacing a quote after confirmation was attempted.
// A timeout/transport failure may hide a completed run: keep the original quote for recovery.
function refused(error: unknown): boolean {
  return ['quote_expired_or_revoked', 'quote_context_changed', 'model_configuration_changed',
    'model_route_unavailable', 'route_confirmation_expired', 'quote_conversation_unavailable'].includes(errorCode(error));
}
function errorText(error: unknown, fallback: BudgetText): BudgetText {
  const code = errorCode(error);
  if (['unknown_operation', 'app_unavailable', 'model_route_unavailable', 'model_authority_required',
    'agent_not_configured', 'not_configured'].includes(code)) return 'unavailable';
  if (code === 'insufficient_funds') return 'funds';
  if (refused(error)) return 'rejected';
  return fallback;
}

/** An isolated consent dialog. Only its read-only offer loads automatically. */
export default function BudgetDialog({ intent, onQuote, onConfirm, onClose }: BudgetDialogProps) {
  const { t, locale } = useI18n();
  const m = (key: BudgetText, params?: Params) => t(budgetCopy[key][0], params);
  const id = useId();
  const [fixedIntent] = useState<BudgetIntent>(() => ({ ...intent }));
  const currentIntent = useRef(intent);
  currentIntent.current = intent;
  const [offer, setOffer] = useState<Offer | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [phase, setPhase] = useState<Phase>('loading');
  const [budget, setBudget] = useState('');
  const [calls, setCalls] = useState('1');
  const [seconds, setSeconds] = useState('300');
  const [pendingParams, setPendingParams] = useState<QuoteParams | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [issue, setIssue] = useState<{ field: 'budget' | 'counts'; text: BudgetText } | null>(null);
  const [now, setNow] = useState(Date.now);
  const alive = useRef(true);
  const locked = useRef(false);
  const generation = useRef(0);
  const offerRequest = useRef<AbortController | null>(null);
  const reviewFocus = useRef<HTMLDListElement>(null);
  const changed = !sameIntent(fixedIntent, intent);
  const hasIntent = Boolean(fixedIntent.text?.trim() || fixedIntent.regenerate_of);
  const busy = phase === 'loading' || phase === 'quoting' || phase === 'confirming';

  const loadOffer = useCallback(async () => {
    offerRequest.current?.abort();
    const controller = new AbortController();
    offerRequest.current = controller;
    const ticket = ++generation.current;
    locked.current = true;
    setPhase('loading');
    setError(null);
    setOffer(null);
    try {
      const result = await read<Offer>('agent.billing.offer', {}, null, controller.signal);
      validateOffer(result);
      if (!alive.current || generation.current !== ticket) return;
      setOffer(result);
      setCalls((value) => { try { boundedCount(value, result.max_calls_limit); return value; } catch { return '1'; } });
      setSeconds((value) => { try { boundedCount(value, result.run_seconds_limit); return value; } catch { return String(Math.min(300, result.run_seconds_limit)); } });
      setPhase('editing');
    } catch (failure) {
      if (!alive.current || generation.current !== ticket) return;
      setError(failure);
      setPhase('offerError');
    } finally {
      if (generation.current === ticket) locked.current = false;
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void loadOffer();
    return () => { alive.current = false; generation.current++; offerRequest.current?.abort(); };
  }, [loadOffer]);
  useEffect(() => {
    if (!quote) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [quote]);

  useEffect(() => {
    if (phase === 'review') reviewFocus.current?.focus();
  }, [phase]);

  const close = () => {
    alive.current = false;
    generation.current++;
    offerRequest.current?.abort();
    onClose();
  };
  const renew = () => {
    if (locked.current || changed || phase === 'confirmed' || (phase === 'confirmError' && !refused(error))) return;
    setQuote(null);
    setPendingParams(null);
    setIssue(null);
    // A refreshed offer may change its unit, so never reinterpret an old amount automatically.
    setBudget('');
    void loadOffer();
  };

  const requestQuote = async () => {
    if (locked.current || !offer || !hasIntent || !sameIntent(fixedIntent, currentIntent.current)
      || !['editing', 'quoteError'].includes(phase)) return;
    let params = pendingParams;
    if (!params) {
      let amount: string;
      try { amount = parseBudget(budget, offer.unit.scale); }
      catch (failure) {
        setIssue({ field: 'budget', text: failure instanceof Error && failure.message === 'budget_precision' ? 'tooPrecise' : 'invalidAmount' });
        return;
      }
      if (BigInt(amount) < BigInt(offer.per_call_hold)) { setIssue({ field: 'budget', text: 'belowHold' }); return; }
      try {
        params = { budget: amount, max_calls: boundedCount(calls, offer.max_calls_limit), run_seconds: boundedCount(seconds, offer.run_seconds_limit) };
      } catch { setIssue({ field: 'counts', text: 'invalidCounts' }); return; }
    }
    locked.current = true;
    const ticket = ++generation.current;
    setPendingParams(params);
    setIssue(null);
    setError(null);
    setPhase('quoting');
    try {
      const result = await onQuote(params);
      validateQuote(result, fixedIntent, params);
      if (!alive.current || generation.current !== ticket) return;
      setQuote(result);
      setNow(Date.now());
      setPhase('review');
    } catch (failure) {
      if (!alive.current || generation.current !== ticket) return;
      setError(failure);
      setPhase('quoteError');
    } finally {
      if (generation.current === ticket) locked.current = false;
    }
  };

  const confirm = async () => {
    if (locked.current || !quote || !sameIntent(fixedIntent, currentIntent.current)
      || !['review', 'confirmError'].includes(phase)) return;
    const recovery = phase === 'confirmError' && !refused(error);
    if (!recovery && (quoteExpired(quote) || quote.status !== 'quoted' || quote.turn_id)) { setNow(Date.now()); return; }
    locked.current = true;
    const ticket = ++generation.current;
    setError(null);
    setPhase('confirming');
    try {
      await onConfirm(quote);
      if (alive.current && generation.current === ticket) setPhase('confirmed');
    } catch (failure) {
      if (!alive.current || generation.current !== ticket) return;
      setError(failure);
      setPhase('confirmError');
    } finally {
      if (generation.current === ticket) locked.current = false;
    }
  };

  const pricing = quote ?? offer;
  const expired = quote ? quoteExpired(quote, now) : false;
  const used = Boolean(quote && (quote.status === 'accepted' || quote.turn_id));
  const recovery = phase === 'confirmError' && !refused(error);
  const canConfirm = Boolean(quote && !changed && !busy && (recovery
    || (['review', 'confirmError'].includes(phase) && !expired && !used && quote.status === 'quoted')));
  const canRenew = !busy && !changed && !used && ['review', 'quoteError', 'confirmError'].includes(phase)
    && (phase !== 'confirmError' || refused(error));
  const date = (stamp: number) => new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  }).format(stamp * 1000);
  const detail = (label: string, value: ReactNode) => <div style={{ minWidth: 0 }}>
    <dt className="muted small">{label}</dt><dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{value}</dd>
  </div>;

  return <Modal title={m('title')} onClose={close} describedBy={`${id}-intro`} footer={<div style={actions}>
    <button type="button" className="btn" style={action} onClick={close}>{m('close')}</button>
    {canRenew && <button type="button" className="btn" style={action} onClick={renew}>{m('renew')}</button>}
    {['editing', 'quoting', 'quoteError'].includes(phase) && <button type="submit" form={`${id}-form`} className="btn primary" style={action}
      disabled={busy || changed || !hasIntent}>{m(phase === 'quoting' ? 'quoting' : phase === 'quoteError' ? 'retryQuote' : 'getQuote')}</button>}
    {quote && phase !== 'confirmed' && !used && <button type="button" className="btn primary" style={action} disabled={!canConfirm}
      onClick={() => void confirm()}>{m(phase === 'confirming' ? 'confirming' : phase === 'confirmError' ? 'retryConfirm'
        : quote.environment === 'test' ? 'confirmTest' : 'confirm')}</button>}
  </div>}>
    <div style={stack}>
      <p id={`${id}-intro`} className="notice info">{m('intro')}</p>
      {pricing && (pricing.environment === 'test' ? <div className="notice warn" role="note">
        <strong>{m('test')}</strong><p style={{ margin: 0 }}>{m('testNote')}</p>
      </div> : <p className="muted">{m('live')}</p>)}
      <section aria-labelledby={`${id}-message`} style={stack}>
        <h3 id={`${id}-message`}>{m('message')}</h3>
        <p className="muted">{m('fixed')}</p>
        {fixedIntent.text != null && <div className="notice" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '12rem', overflow: 'auto' }} tabIndex={0}>{fixedIntent.text}</div>}
        {fixedIntent.regenerate_of && <p>{m('regenerate', { id: fixedIntent.regenerate_of })}</p>}
        {fixedIntent.parent_message_id && <p className="muted small">{m('parent', { id: fixedIntent.parent_message_id })}</p>}
      </section>
      {changed && <p className="notice warn" role="alert">{m('changed')}</p>}
      {!hasIntent && <p className="notice err" role="alert">{m('missing')}</p>}
      {phase === 'loading' && <Loading label={m('load')} />}
      {['offerError', 'quoteError', 'confirmError'].includes(phase) && <div>
        <Failure error={new Error(m(errorText(error, phase === 'offerError' ? 'offerError' : phase === 'quoteError' ? 'quoteError' : 'confirmError')))}
          onRetry={phase === 'offerError' ? () => void loadOffer() : undefined} />
        {error instanceof ApiError && error.requestId && <p className="muted small">{m('requestId', { id: error.requestId })}</p>}
      </div>}
      {pricing && <>
        <dl style={{ ...stack, margin: 0 }}>
          {detail(m('wallet'), <>{m('personal')}{quote && <p className="muted small">{m('payer', { id: quote.payer_user_id })}</p>}</>)}
          {detail(m('model'), pricing.model)}
          {detail(m('actualModel'), pricing.confirmed_route)}
          {detail(m('hold'), formatAmount(pricing.per_call_hold, pricing.unit))}
        </dl>
        {!quote && offer && <form id={`${id}-form`} noValidate onSubmit={(event) => { event.preventDefault(); void requestQuote(); }} style={stack}>
          <label className="field" htmlFor={`${id}-budget`}>{m('amount', { unit: offer.unit.code })}
            <input id={`${id}-budget`} className="input" inputMode="decimal" autoComplete="off" required value={budget} maxLength={128}
              disabled={phase !== 'editing' || changed} onChange={(event) => { setBudget(event.target.value); setIssue(null); }}
              aria-invalid={issue?.field === 'budget'} aria-describedby={`${id}-precision${issue ? ` ${id}-issue` : ''}`} />
          </label>
          <p id={`${id}-precision`} className="muted small">{m('precision', { scale: offer.unit.scale, amount: formatAmount('1', offer.unit) })}</p>
          <label className="field" htmlFor={`${id}-calls`}>{m('calls')}
            <input id={`${id}-calls`} className="input" type="number" inputMode="numeric" min={1} max={offer.max_calls_limit} step={1} required value={calls}
              disabled={phase !== 'editing' || changed} onChange={(event) => { setCalls(event.target.value); setIssue(null); }}
              aria-invalid={issue?.field === 'counts'} aria-describedby={`${id}-limits${issue ? ` ${id}-issue` : ''}`} />
          </label>
          <label className="field" htmlFor={`${id}-seconds`}>{m('seconds')}
            <input id={`${id}-seconds`} className="input" type="number" inputMode="numeric" min={1} max={offer.run_seconds_limit} step={1} required value={seconds}
              disabled={phase !== 'editing' || changed} onChange={(event) => { setSeconds(event.target.value); setIssue(null); }}
              aria-invalid={issue?.field === 'counts'} aria-describedby={`${id}-limits${issue ? ` ${id}-issue` : ''}`} />
          </label>
          <p id={`${id}-limits`} className="muted small">{m('limits', { calls: offer.max_calls_limit, seconds: offer.run_seconds_limit })}</p>
          {issue && <p className="notice err" id={`${id}-issue`} role="alert">{m(issue.text)}</p>}
        </form>}
        {quote && <dl ref={reviewFocus} tabIndex={-1} aria-label={m('total')} style={{ ...stack, margin: 0 }}>
          {detail(m('total'), formatAmount(quote.budget, quote.unit))}
          {detail(m('calls'), quote.max_calls)}
          {detail(m('deadline'), date(quote.deadline))}
          {detail(m('expires'), date(quote.expires_at))}
        </dl>}
        {quote && <p className="muted small">{m('timezone')}</p>}
        <section aria-labelledby={`${id}-prices`} style={stack}>
          <h3 id={`${id}-prices`}>{m(pricing.per_tokens === 1 ? 'singlePrice' : 'prices', { count: pricing.per_tokens, unit: pricing.unit.code })}</h3>
          <dl style={{ ...stack, margin: 0 }}>
            {detail(m('uncached'), formatRate(pricing.rates.input_uncached, pricing.unit))}
            {detail(m('cached'), formatRate(pricing.rates.input_cached, pricing.unit))}
            {detail(m('output'), formatRate(pricing.rates.output, pricing.unit))}
          </dl>
          <p className="muted small">{m('exact')}</p>
        </section>
        <details><summary>{m('records')}</summary><dl style={stack}>
          {detail(m('priceId'), pricing.price_id)}
          {detail(m('version'), pricing.route_version)}
          {detail(m('terms'), pricing.terms_ref)}
          {quote && detail(m('quoteId'), quote.id)}
        </dl></details>
      </>}
      {phase === 'review' && quote && <p className={`notice ${used || expired || quote.status === 'revoked' ? 'warn' : 'info'}`} role="status" aria-live="polite">
        {m(used ? 'accepted' : expired ? 'expired' : quote.status === 'revoked' ? 'revoked' : 'ready')}
      </p>}
      {(phase === 'quoting' || phase === 'confirming') && <p role="status" aria-live="polite">{m(phase === 'quoting' ? 'quoting' : 'confirming')}</p>}
      {phase === 'confirmed' && <p className="notice ok" role="status">{m('confirmed')}</p>}
      {['quoting', 'quoteError', 'confirming', 'confirmError'].includes(phase) && <p className="muted small">{m('closePending')}</p>}
    </div>
  </Modal>;
}
