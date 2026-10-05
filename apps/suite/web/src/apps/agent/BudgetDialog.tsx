import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type CSSProperties, type ReactNode } from 'react';
import { ApiError } from '@synwevo/auth-client';
import { Modal, useI18n, type Params } from '@synwevo/ui';
import { read } from '../../shared/ops';
import { Failure, Loading } from '../../shared/states';
import {
  boundedCount, formatAmount, formatRate, parseBudget, quoteExpired, sameIntent, validateOffer, validateQuote,
  type BudgetIntent, type Offer, type Pricing, type Quote, type QuoteParams,
} from './budget';
import { PriceChangeList, useChatText } from './ChatParts';
import { renewalParams, samePricing } from './composer';
import { majorDecimal } from './cost';
import { refusalOf } from './refusals';
import { budgetCopy, type BudgetText } from './budget.messages';
import { consentTotal, consentInput, type Consent, type ConsentInput, type ConsentWriteState, type ConsentWriter } from './consent';
import { ConsentRecovery, ConsentScope, ConsentTerms, consentErrorKey, useConsentText } from './ConsentControls';
import type { ConsentText } from './consent.messages';
import type { Workspace } from './types';

export type { BudgetIntent, Offer, Quote, QuoteParams } from './budget';
export interface BudgetDialogProps {
  intent: BudgetIntent;
  /** The parent owns the fixed intent and reuses its request key for retries of these parameters. */
  onQuote: (params: QuoteParams) => Promise<Quote>;
  /** The parent must retry this exact quote with the original confirmation key. */
  onConfirm: (quote: Quote) => Promise<unknown>;
  onClose: () => void;
  /** Offered only when this conversation has no active continuing consent. */
  consent?: ConsentOption;
  /** Who pays when it is not the person's own wallet (an organization that granted payment). */
  payerLabel?: string;
  /** The terms and limits the person last confirmed, when they no longer apply (a consent whose
   *  model or price changed): the dialog says what changed and offers to re-confirm in one step. */
  previous?: Previous;
}
export interface Previous { terms: Pricing; budget: string; max_calls: number; run_seconds: number }
export interface ConsentOption {
  /** The conversation scope as shown now; null is global discovery and cannot back a consent. */
  scope: string[] | null;
  spaces: Workspace[];
  payer: string;
  writer: ConsentWriter;
  /** `retry` checks the same uncertain request with its original key; it never creates another. */
  create: (quote: Quote, input: ConsentInput, params: QuoteParams, retry: boolean) => Promise<Consent>;
  send: (consent: Consent, retry: boolean) => Promise<unknown>;
}
const idle: ConsentWriteState = { phase: 'idle', ticket: null };
const quiet = () => () => {};
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
// The terms themselves changed (not merely an expired or used quote): say what changed on renewal.
function priceChanged(error: unknown): boolean {
  return ['model_configuration_changed', 'route_confirmation_expired', 'consent_model_changed'].includes(errorCode(error));
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
export default function BudgetDialog({ intent, onQuote, onConfirm, onClose, consent: offered, payerLabel, previous }: BudgetDialogProps) {
  const { t, locale } = useI18n();
  const { c: ct } = useChatText();
  // Fixed when the dialog opens, and replaced only by a quote this dialog saw refused for a change.
  const [prior, setPrior] = useState<Previous | null>(() => previous ?? null);
  const [differs, setDiffers] = useState(false);
  const m = (key: BudgetText, params?: Params) => t(budgetCopy[key][0], params);
  const { m: c } = useConsentText();
  const id = useId();
  const [fixedIntent] = useState<BudgetIntent>(() => ({ ...intent }));
  // Fixed when the dialog opens: saving a consent refreshes the page's list, which must not
  // withdraw this dialog's next step or bring back the retired single-run quote.
  const [consent] = useState(offered);
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
  const priorRef = useRef(prior);
  priorRef.current = prior;
  const locked = useRef(false);
  const generation = useRef(0);
  const offerRequest = useRef<AbortController | null>(null);
  const reviewFocus = useRef<HTMLDListElement>(null);
  const changed = !sameIntent(fixedIntent, intent);
  const hasIntent = Boolean(fixedIntent.text?.trim() || fixedIntent.regenerate_of);
  const busy = phase === 'loading' || phase === 'quoting' || phase === 'confirming';
  const [optIn, setOptIn] = useState(false);
  const [runs, setRuns] = useState('');
  const [lifetime, setLifetime] = useState<'' | 'expires' | 'until'>('');
  const [expiry, setExpiry] = useState('');
  const [consentIssue, setConsentIssue] = useState<ConsentText | null>(null);
  const [consentReview, setConsentReview] = useState<ConsentInput | null>(null);
  const [created, setCreated] = useState<Consent | null>(null);
  const writer = useSyncExternalStore(consent?.writer.subscribe ?? quiet, consent?.writer.getSnapshot ?? (() => idle), consent?.writer.getSnapshot ?? (() => idle));
  const consentPending = writer.phase === 'busy' || writer.phase === 'uncertain';

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
      const reuse = priorRef.current && renewalParams(priorRef.current, result, priorRef.current.terms.unit);
      if (reuse) {
        setBudget(majorDecimal(reuse.budget, result.unit.scale));
        setCalls(String(reuse.max_calls));
        setSeconds(String(reuse.run_seconds));
      }
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
    if (locked.current || changed || phase === 'confirmed' || (phase === 'confirmError' && !refused(error))
      || created || consentPending) return;
    consent?.writer.dismiss();
    if (quote && pendingParams && priceChanged(error)) {
      const next = { terms: quote, budget: quote.budget, max_calls: quote.max_calls, run_seconds: pendingParams.run_seconds };
      priorRef.current = next;
      setPrior(next);
    }
    setDiffers(false);
    setConsentReview(null);
    setConsentIssue(null);
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

  /** Re-confirm in one step: quote the previous limits at the new terms, and send only when the
   *  quote matches exactly what is shown; otherwise stop at the review with the difference. */
  const renewAndSend = async () => {
    if (locked.current || !offer || !prior || !hasIntent || !sameIntent(fixedIntent, currentIntent.current) || phase !== 'editing') return;
    const params = renewalParams(prior, offer, prior.terms.unit);
    if (!params) return;
    locked.current = true;
    const ticket = ++generation.current;
    setPendingParams(params);
    setIssue(null);
    setError(null);
    setDiffers(false);
    setPhase('quoting');
    try {
      let result: Quote;
      try {
        result = await onQuote(params);
        validateQuote(result, fixedIntent, params);
      } catch (failure) {
        if (alive.current && generation.current === ticket) { setError(failure); setPhase('quoteError'); }
        return;
      }
      if (!alive.current || generation.current !== ticket) return;
      setQuote(result);
      setNow(Date.now());
      if (!samePricing(result, offer) || quoteExpired(result) || result.status !== 'quoted' || result.turn_id) {
        setDiffers(true);
        setPhase('review');
        return;
      }
      setPhase('confirming');
      try {
        await onConfirm(result);
        if (alive.current && generation.current === ticket) setPhase('confirmed');
      } catch (failure) {
        if (alive.current && generation.current === ticket) { setError(failure); setPhase('confirmError'); }
      }
    } finally {
      if (generation.current === ticket) locked.current = false;
    }
  };

  const reviewConsent = () => {
    if (!consent || !quote || !pendingParams || changed || consentPending) return;
    try {
      setConsentReview(consentInput(quote, consent.scope, runs, lifetime === 'expires' ? expiry : '', lifetime === 'until'));
      setConsentIssue(null);
    } catch (failure) { setConsentIssue(consentErrorKey(failure, 'invalidRuns')); }
  };
  // The writer keeps the outcome (busy, uncertain or refused) on screen; nothing is retried automatically.
  const createConsent = async (retry = false) => {
    if (!consent || !quote || !consentReview || !pendingParams || changed || created) return;
    try {
      const value = await consent.create(quote, consentReview, pendingParams, retry);
      if (alive.current) setCreated(value);
    } catch { /* shown by ConsentRecovery */ }
  };
  const sendOriginal = async (retry = false) => {
    if (!consent || !created || changed) return;
    try {
      await consent.send(created, retry);
      if (alive.current) setPhase('confirmed');
    } catch { /* shown by ConsentRecovery */ }
  };
  const back = () => {
    if (consentPending) return;
    consent?.writer.dismiss();
    setConsentReview(null);
  };

  /** A payment refusal the chat explains in its own words (organization payer, frozen wallet,
   *  risk controls, consent); price changes and expired quotes keep the dialog's own wording. */
  const paymentRefusal = (failure: unknown): string | null => {
    const known = refusalOf(failure, { organization: payerLabel !== undefined });
    return known && !['price_changed', 'funds'].includes(known.kind) ? ct(known.text) : null;
  };
  const pricing = quote ?? offer;
  const reuse = prior && offer ? renewalParams(prior, offer, prior.terms.unit) : null;
  const renewal = Boolean(prior && offer && !quote && ['editing', 'quoting', 'quoteError'].includes(phase));
  const expired = quote ? quoteExpired(quote, now) : false;
  const used = Boolean(quote && (quote.status === 'accepted' || quote.turn_id));
  const recovery = phase === 'confirmError' && !refused(error);
  const canConfirm = Boolean(quote && !changed && !busy && (recovery
    || (['review', 'confirmError'].includes(phase) && !expired && !used && quote.status === 'quoted')));
  const canRenew = !busy && !changed && !used && ['review', 'quoteError', 'confirmError'].includes(phase)
    && (phase !== 'confirmError' || refused(error)) && !created && !consentPending;
  const consentStage = !consent || !quote || phase !== 'review' ? null
    : created ? 'created' : consentReview ? 'review' : optIn ? 'edit' : 'off';
  const single = consentStage === null || consentStage === 'off';
  const date = (stamp: number) => new Intl.DateTimeFormat(locale, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  }).format(stamp * 1000);
  const detail = (label: string, value: ReactNode) => <div style={{ minWidth: 0 }}>
    <dt className="muted small">{label}</dt><dd style={{ margin: 0, overflowWrap: 'anywhere' }}>{value}</dd>
  </div>;

  return <Modal title={m('title')} onClose={close} describedBy={`${id}-intro`} footer={<div style={actions}>
    <button type="button" className="btn" style={action} onClick={close}>{m('close')}</button>
    {canRenew && <button type="button" className="btn" style={action} onClick={renew}>{m('renew')}</button>}
    {['editing', 'quoting', 'quoteError'].includes(phase) && <button type="submit" form={`${id}-form`} className={`btn${renewal && reuse ? '' : ' primary'}`} style={action}
      disabled={busy || changed || !hasIntent}>{m(phase === 'quoting' ? 'quoting' : phase === 'quoteError' ? 'retryQuote' : 'getQuote')}</button>}
    {renewal && reuse && phase === 'editing' && <button type="button" className="btn primary" style={action}
      disabled={busy || changed || !hasIntent} onClick={() => void renewAndSend()}>{ct('acceptNew')}</button>}
    {quote && phase !== 'confirmed' && !used && single && <button type="button" className="btn primary" style={action} disabled={!canConfirm}
      onClick={() => void confirm()}>{m(phase === 'confirming' ? 'confirming' : phase === 'confirmError' ? 'retryConfirm'
        : quote.environment === 'test' ? 'confirmTest' : 'confirm')}</button>}
    {consentStage === 'edit' && <button type="submit" form={`${id}-consent-form`} className="btn primary" style={action}
      disabled={changed || expired || consentPending}>{c('review')}</button>}
    {consentStage === 'review' && <>
      <button type="button" className="btn" style={action} disabled={consentPending} onClick={back}>{c('back')}</button>
      <button type="button" className="btn primary" style={action} disabled={changed || expired || consentPending}
        onClick={() => void createConsent()}>{c('create')}</button>
    </>}
    {consentStage === 'created' && <button type="button" className="btn primary" style={action} disabled={changed || consentPending}
      onClick={() => void sendOriginal()}>{c('sendOriginal')}</button>}
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
      {renewal && prior && offer && <div className="notice info" style={stack}>
        <PriceChangeList before={prior.terms} after={offer} />
        <p className="small" style={{ margin: 0 }}>{reuse ? ct('acceptNewNote', { budget: formatAmount(reuse.budget, offer.unit) }) : ct('acceptNewBlocked')}</p>
      </div>}
      {differs && phase === 'review' && <p className="notice warn" role="alert">{ct('quoteDiffers')}</p>}
      {['offerError', 'quoteError', 'confirmError'].includes(phase) && <div>
        <Failure error={new Error(paymentRefusal(error) ?? m(errorText(error, phase === 'offerError' ? 'offerError' : phase === 'quoteError' ? 'quoteError' : 'confirmError')))}
          onRetry={phase === 'offerError' ? () => void loadOffer() : undefined} />
        {error instanceof ApiError && error.requestId && <p className="muted small">{m('requestId', { id: error.requestId })}</p>}
      </div>}
      {pricing && <>
        <dl style={{ ...stack, margin: 0 }}>
          {detail(m('wallet'), <>{payerLabel ?? m('personal')}{quote && <p className="muted small">{m('payer', { id: quote.payer_user_id })}</p>}</>)}
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
      {phase === 'review' && quote && single && <p className={`notice ${used || expired || quote.status === 'revoked' ? 'warn' : 'info'}`} role="status" aria-live="polite">
        {m(used ? 'accepted' : expired ? 'expired' : quote.status === 'revoked' ? 'revoked' : 'ready')}
      </p>}
      {consent && consentStage && quote && <section aria-labelledby={`${id}-consent`} style={stack}>
        <h3 id={`${id}-consent`}>{c('title')}</h3>
        {!Array.isArray(consent.scope) ? <p className="notice info">{c('explicitScope')}</p>
          : consentStage === 'created' && created ? <>
            <p className="notice ok" role="status">{c('created')}</p>
            <ConsentTerms consent={created} payer={consent.payer} spaces={consent.spaces} />
          </> : consentStage === 'review' && consentReview && pendingParams ? <>
            <p className="notice info">{c('intro')}</p>
            <p>{c('payer', { name: consent.payer })}</p>
            <ConsentScope scope={consent.scope} spaces={consent.spaces} />
            <dl style={{ ...stack, margin: 0 }}>
              {detail(m('model'), quote.model)}{detail(m('actualModel'), quote.confirmed_route)}
              {detail(c('perRun'), formatAmount(quote.budget, quote.unit))}{detail(c('perRunCalls'), quote.max_calls)}
              {detail(c('runs'), consentReview.max_runs)}
              {detail(c('maximum'), formatAmount(consentTotal(quote.budget, consentReview.max_runs), quote.unit))}
              {detail(c('expiry'), consentReview.expires_at === null ? c('until') : date(consentReview.expires_at))}
            </dl>
            <p>{c('perRunSeconds', { seconds: pendingParams.run_seconds })}</p>
            <p>{c('equation', { amount: formatAmount(quote.budget, quote.unit), runs: consentReview.max_runs,
              total: formatAmount(consentTotal(quote.budget, consentReview.max_runs), quote.unit) })}</p>
            <p>{c('accounting')}</p><p>{c('fixed')}</p><p className="muted small">{c('costsRemain')}</p>
          </> : <>
            <label className="check" style={{ minHeight: 44 }}><input type="checkbox" checked={optIn} disabled={consentPending || expired || used}
              onChange={(event) => { setOptIn(event.target.checked); setConsentIssue(null); }} />{c('optIn')}</label>
            {optIn && <form id={`${id}-consent-form`} noValidate style={stack}
              onSubmit={(event) => { event.preventDefault(); reviewConsent(); }}>
              <p className="muted">{c('intro')}</p>
              <label className="field" htmlFor={`${id}-runs`}>{c('runs')}
                <input id={`${id}-runs`} className="input" type="number" inputMode="numeric" min={1} max={1000} step={1} required value={runs}
                  aria-invalid={consentIssue === 'invalidRuns' || consentIssue === 'totalOverflow'}
                  onChange={(event) => { setRuns(event.target.value); setConsentIssue(null); }} />
              </label>
              <fieldset style={{ ...stack, border: 0, padding: 0, margin: 0 }} aria-describedby={`${id}-expiry-help`}>
                <legend>{c('expiry')}</legend>
                <label className="check" style={{ minHeight: 44 }}><input type="radio" name={`${id}-lifetime`} checked={lifetime === 'expires'}
                  onChange={() => { setLifetime('expires'); setConsentIssue(null); }} />{c('expiresChoice')}</label>
                {lifetime === 'expires' && <label className="field" htmlFor={`${id}-expiry`}>{c('expiry')}
                  <input id={`${id}-expiry`} className="input" type="datetime-local" required value={expiry}
                    aria-invalid={consentIssue === 'invalidExpiry'} onChange={(event) => { setExpiry(event.target.value); setConsentIssue(null); }} />
                </label>}
                <label className="check" style={{ minHeight: 44 }}><input type="radio" name={`${id}-lifetime`} checked={lifetime === 'until'}
                  onChange={() => { setLifetime('until'); setExpiry(''); setConsentIssue(null); }} />{c('untilChoice')}</label>
                <p id={`${id}-expiry-help`} className="muted small">{c('expiryHelp')}</p>
              </fieldset>
              {consentIssue && <p className="notice err" role="alert">{c(consentIssue)}</p>}
            </form>}
          </>}
        <ConsentRecovery writer={consent.writer} retry={() => created ? sendOriginal(true) : createConsent(true)} />
      </section>}
      {(phase === 'quoting' || phase === 'confirming') && <p role="status" aria-live="polite">{m(phase === 'quoting' ? 'quoting' : 'confirming')}</p>}
      {phase === 'confirmed' && <p className="notice ok" role="status">{m('confirmed')}</p>}
      {['quoting', 'quoteError', 'confirming', 'confirmError'].includes(phase) && <p className="muted small">{m('closePending')}</p>}
    </div>
  </Modal>;
}
