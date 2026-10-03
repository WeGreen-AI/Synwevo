import { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { ApiError } from '@synwevo/auth-client';
import { Modal, useI18n, type Params } from '@synwevo/ui';
import { read } from '../../shared/ops';
import { Failure, Loading } from '../../shared/states';
import { budgetCopy } from './budget.messages';
import { formatAmount, formatRate } from './budget';
import { consentCode, effectiveConsentStatus, validateConsentList, type Consent, type ConsentWriter } from './consent';
import { consentCopy, type ConsentText } from './consent.messages';
import type { Workspace } from './types';

export const consentStack: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)', minWidth: 0, overflowWrap: 'anywhere' };
export const consentButton: CSSProperties = { height: 'auto', minHeight: 44, maxWidth: '100%', whiteSpace: 'normal' };
export function useConsentText() {
  const { t, locale } = useI18n();
  return { m: (key: ConsentText, params?: Params) => t(consentCopy[key][0], params), t, locale };
}
export function consentErrorKey(error: unknown, fallback: ConsentText = 'refused'): ConsentText {
  const code = consentCode(error);
  const map: Record<string, ConsentText> = {
    consent_explicit_scope_required: 'explicitScope', consent_already_exists: 'alreadyExists', consent_limit_exceeded: 'limitExceeded',
    consent_invalid_expiry: 'invalidExpiry', consent_expiry_required: 'invalidExpiry', invalid_count: 'invalidRuns',
    consent_total_overflow: 'totalOverflow', consent_total_too_large: 'totalOverflow', consent_state_unavailable: 'loadError', invalid_integer: 'totalOverflow', consent_invalid_limits: 'invalidRuns',
    consent_pending_operation: 'pending', consent_outcome_unknown: 'unknown', consent_not_found: 'notFound',
    consent_revoked: 'revoked', consent_expired: 'expired', consent_exhausted: 'exhausted', consent_scope_changed: 'scope_changed',
    consent_model_changed: 'model_changed', consent_conversation_unavailable: 'conversation_unavailable',
    quote_expired_or_revoked: 'quoteChanged', quote_context_changed: 'quoteChanged', model_configuration_changed: 'model_changed',
    route_confirmation_expired: 'model_changed', model_route_unavailable: 'model_changed',
  };
  return map[code] ?? fallback;
}
export function ConsentFailure({ error, fallback = 'refused', retry }: { error: unknown; fallback?: ConsentText; retry?: () => void }) {
  const { m, t } = useConsentText();
  return <div><Failure error={new Error(m(consentErrorKey(error, fallback)))} onRetry={retry} />
    {error instanceof ApiError && error.requestId && <p className="muted small">{t(budgetCopy.requestId[0], { id: error.requestId })}</p>}
  </div>;
}
export function ConsentScope({ scope, spaces }: { scope: string[]; spaces: Workspace[] }) {
  const { m } = useConsentText();
  return <div style={consentStack}><strong>{m('scope')}</strong>{scope.length === 0 ? <p>{m('personal')}</p>
    : <ul style={{ margin: 0, paddingInlineStart: 'var(--sp-5)' }}>{scope.map((id) => <li key={id}>
      {spaces.find((space) => space.workspace_id === id)?.name ?? id} <code>{id}</code>
    </li>)}</ul>}</div>;
}
export function ConsentTerms({ consent, payer, spaces }: { consent: Consent; payer: string; spaces: Workspace[] }) {
  const { m, t, locale } = useConsentText();
  const b = (key: keyof typeof budgetCopy, params?: Params) => t(budgetCopy[key][0], params);
  const date = (value: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long' }).format(value * 1000);
  const detail = (label: string, value: string | number) => <div><dt className="muted small">{label}</dt><dd style={{ margin: 0 }}>{value}</dd></div>;
  return <div style={consentStack}>
    <p className={`notice ${consent.environment === 'test' ? 'warn' : 'info'}`}>{b(consent.environment)}{consent.environment === 'test' && <> · {b('testNote')}</>}</p>
    <p>{m('payer', { name: payer })}</p><p>{m('intro')}</p>
    <ConsentScope scope={consent.scope} spaces={spaces} />
    <dl style={{ ...consentStack, margin: 0 }}>
      {detail(b('model'), consent.model)}{detail(b('actualModel'), consent.confirmed_route)}
      {detail(m('perRun'), formatAmount(consent.budget, consent.unit))}
      {detail(m('perRunCalls'), consent.max_calls)}
      {detail(m('maximum'), formatAmount(consent.total_maximum, consent.unit))}
      {detail(m('runs'), consent.max_runs)}{detail(m('createdAt'), date(consent.created_at))}
      {detail(m('expiry'), consent.expires_at === null ? m('until') : date(consent.expires_at))}
      {detail(b('hold'), formatAmount(consent.per_call_hold, consent.unit))}
      {detail(b('prices', { count: consent.per_tokens, unit: consent.unit.code }), '')}
      {detail(b('uncached'), formatRate(consent.rates.input_uncached, consent.unit))}
      {detail(b('cached'), formatRate(consent.rates.input_cached, consent.unit))}
      {detail(b('output'), formatRate(consent.rates.output, consent.unit))}
      {detail(b('priceId'), consent.price_id)}{detail(b('version'), consent.route_version)}{detail(b('terms'), consent.terms_ref)}
    </dl>
    <p>{m('perRunSeconds', { seconds: consent.run_seconds })}</p>
    <p>{m('equation', { amount: formatAmount(consent.budget, consent.unit), runs: consent.max_runs, total: formatAmount(consent.total_maximum, consent.unit) })}</p>
    <p>{m('remaining', { remaining: consent.remaining_runs, maximum: consent.max_runs })} · {m('used', { used: consent.used_runs })}</p>
    <p>{m('accounting')}</p><p>{m('fixed')}</p><p>{m('costsRemain')}</p>
    <p className="muted small">{m('reference', { id: consent.id })}</p>
  </div>;
}

export function useConversationConsents(cid: string | undefined, scope: string[] | null | undefined, connection: string, enabled: boolean) {
  const [state, setState] = useState<{ cid?: string; data?: Consent[]; error?: unknown; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick((value) => value + 1), []);
  const scopeKey = JSON.stringify(scope);
  useEffect(() => {
    if (!cid || !enabled) { setState({ cid, data: [], loading: false }); return; }
    const abort = new AbortController();
    let busy = false;
    const load = async () => {
      if (busy || abort.signal.aborted) return;
      busy = true;
      setState((old) => ({ ...old, cid, data: old.cid === cid ? old.data : undefined, loading: true, error: undefined }));
      try {
        const value = await read<{ consents: Consent[] }>('agent.consent.list', { conversation_id: cid }, null, abort.signal);
        validateConsentList(value, cid);
        if (!abort.signal.aborted) setState({ cid, data: value.consents, loading: false });
      } catch (error) { if (!abort.signal.aborted) setState({ cid, error, loading: false }); }
      finally { busy = false; }
    };
    void load();
    const visible = () => { if (document.visibilityState === 'visible') void load(); };
    const timer = window.setInterval(visible, 15000);
    document.addEventListener('visibilitychange', visible);
    return () => { abort.abort(); window.clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [cid, scopeKey, connection, enabled, tick]);
  return { ...(state.cid === cid ? state : { loading: true }), refresh };
}

export function ConsentRecovery({ writer, retry }: { writer: ConsentWriter; retry: () => Promise<unknown> }) {
  const { m } = useConsentText();
  const state = useSyncExternalStore(writer.subscribe, writer.getSnapshot, writer.getSnapshot);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (writer.pending()) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [writer]);
  if (state.phase === 'idle' || state.phase === 'succeeded') return null;
  return <div style={{ ...consentStack, padding: 'var(--sp-3)', flexShrink: 0 }} aria-live="polite">
    {state.phase === 'busy' ? <p role="status">{m('busy')}</p>
      : state.phase === 'uncertain' ? <div className="notice warn" style={consentStack}>
        <p role="alert">{m('unknown')}</p>
        <div><button type="button" className="btn" style={consentButton} onClick={() => void retry().catch(() => {})}>{m('retry')}</button></div>
      </div> : <ConsentFailure error={state.error} />}
  </div>;
}

export function ConsentPanel({ consents, loading, error, refresh, scope, spaces, payer, writer, single, setSingle, revoke }: {
  consents?: Consent[]; loading: boolean; error?: unknown; refresh: () => void; scope: string[] | null; spaces: Workspace[]; payer: string;
  writer: ConsentWriter; single: boolean; setSingle: (value: boolean) => void; revoke: (consent: Consent) => Promise<unknown>;
}) {
  const { m } = useConsentText();
  const id = useId();
  const state = useSyncExternalStore(writer.subscribe, writer.getSnapshot, writer.getSnapshot);
  const [detail, setDetail] = useState<{ consent: Consent; revoke: boolean } | null>(null);
  const [localError, setLocalError] = useState<unknown>();
  const [, setNow] = useState(0);
  useEffect(() => { const timer = window.setInterval(() => setNow((n) => n + 1), 1000); return () => window.clearInterval(timer); }, []);
  const selected = consents?.find((c) => effectiveConsentStatus(c, scope) === 'active') ?? consents?.[0];
  const pending = state.phase === 'busy' || state.phase === 'uncertain';
  return <section style={{ ...consentStack, padding: 'var(--sp-3) var(--sp-4)', flexShrink: 0, maxHeight: '30dvh', overflow: 'auto', borderBottom: '1px solid var(--line)' }} aria-labelledby={`${id}-title`}>
    <div className="row wrap"><h2 id={`${id}-title`} style={{ fontSize: 'var(--fs-3)', margin: 0 }}>{m('title')}</h2>
      <button className="btn ghost" style={consentButton} type="button" disabled={loading} onClick={refresh}>{m('refresh')}</button></div>
    {error != null ? <ConsentFailure error={error} fallback="loadError" /> : !consents ? <Loading label={m('loading')} /> : <>
      {selected ? <>
        <div className="row wrap"><strong>{m(effectiveConsentStatus(selected, scope))}</strong><span>{m('payer', { name: payer })}</span>
          <span>{m('model', { model: selected.model })}</span><span>{m('perRun')} · {formatAmount(selected.budget, selected.unit)}</span>
          <span>{m('remaining', { remaining: selected.remaining_runs, maximum: selected.max_runs })}</span>
          <button className="btn" style={consentButton} type="button" onClick={() => { setLocalError(undefined); setDetail({ consent: selected, revoke: false }); }}>{m('details')}</button>
          {selected.status !== 'revoked' && <button className="btn" style={consentButton} type="button" disabled={pending}
            onClick={() => { setLocalError(undefined); setDetail({ consent: selected, revoke: true }); }}>{m('revoke')}</button>}
        </div>
        <details><summary style={consentButton}>{m('history')}</summary><ul>{consents.map((consent) => <li key={consent.id}>
          <button className="btn ghost" type="button" style={consentButton} onClick={() => { setLocalError(undefined); setDetail({ consent, revoke: false }); }}>
            {m(effectiveConsentStatus(consent, scope))} · {consent.id}
          </button></li>)}</ul></details>
        {effectiveConsentStatus(selected, scope) === 'active' && <>
          <label className="check" style={{ minHeight: 44 }}><input type="checkbox" checked={single} disabled={pending} onChange={(event) => setSingle(event.target.checked)} />{m('single')}</label>
          {single && <p className="muted small">{m('singleNote')}</p>}
        </>}
      </> : <p className="muted small">{m('none')}</p>}
      <p className="muted small">{m('costsRemain')}</p>
    </>}
    {detail && <Modal title={m(detail.revoke ? 'revoke' : 'details')} onClose={() => setDetail(null)} footer={<div className="row wrap">
      <button className="btn" style={consentButton} type="button" onClick={() => setDetail(null)}>{m('close')}</button>
      {detail.consent.status !== 'revoked' && <button className="btn danger" style={consentButton} type="button" disabled={pending}
        onClick={() => { if (!detail.revoke) { setDetail({ ...detail, revoke: true }); return; }
          void revoke(detail.consent).then(() => setDetail(null)).catch(setLocalError); }}>{m('revoke')}</button>}
    </div>}>
      {detail.revoke && <p className="notice warn">{m('revokeReview')}</p>}
      <p>{m(effectiveConsentStatus(detail.consent, scope))}</p>
      <ConsentTerms consent={detail.consent} payer={payer} spaces={spaces} />
      {localError != null && <ConsentFailure error={localError} />}
      {pending && <ConsentRecovery writer={writer} retry={async () => { await writer.retry(); refresh(); setDetail(null); }} />}
    </Modal>}
  </section>;
}
