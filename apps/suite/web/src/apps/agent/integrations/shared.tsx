/** What every page of the MCP integration centre shares: the catalogue accessor, bounded reads,
 *  one write intent at a time with a retry that reuses its idempotency key, refusals in the
 *  centre's own words (never the server's prose, which may echo a submitted secret), badges and
 *  a clipboard button. Patterned on the Agent settings page. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ApiError } from '@synwevo/auth-client';
import { useI18n, type Params } from '@synwevo/ui';
import { newKey, op, read, type RunStatus } from '../../../shared/ops';
import { Failure, RunBadge } from '../../../shared/states';
import { integrationCopy, type IntegrationText } from './integrations.messages';
import { callStatusView, errorCodeOf, formatDuration, healthView, mcpErrorKey } from './model';

export function useText() {
  const { t, locale } = useI18n();
  const m = useCallback((key: IntegrationText, params?: Params) => t(integrationCopy[key][0], params), [t]);
  const date = useCallback((stamp: number | null | undefined) => stamp == null || !Number.isFinite(stamp) ? '—'
    : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(stamp * 1000), [locale]);
  const duration = useCallback((ms: number | null | undefined) => { const d = formatDuration(ms); return d ? m(d.key, { n: d.n }) : '—'; }, [m]);
  return { m, t, locale, date, duration };
}

const GENERIC: Record<string, IntegrationText> = {
  invalid_input: 'invalidInput', idempotency_conflict: 'error', app_access_denied: 'denied', forbidden: 'denied',
  workspace_not_found: 'denied', not_a_member: 'denied', account_unavailable: 'signedOut',
  app_unavailable: 'unavailable', unknown_operation: 'unavailable', network: 'network', upstream_unavailable: 'error',
  gateway_blocked: 'error', result_access_changed: 'mcp_connection_not_found',
};

/** The sentence for a refusal, in the centre's words. */
export function errorKeyOf(error: unknown): IntegrationText {
  const code = errorCodeOf(error);
  const mcp = mcpErrorKey(code);
  if (mcp) return mcp;
  if (GENERIC[code]) return GENERIC[code];
  if (error instanceof ApiError) {
    if (error.status === 401) return 'signedOut';
    if (error.status === 403) return 'denied';
    if (error.status === 404) return 'notFound';
    if (error.status === 429) return 'tooMany';
  }
  return 'error';
}

export function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const { m } = useText();
  const code = errorCodeOf(error);
  const text = m(errorKeyOf(error));
  // Keep only safe diagnostics for the shared Failure presentation; the server's own message
  // and validation detail never reach the page (they may contain a submitted secret).
  const safe = new ApiError(error instanceof ApiError ? error.status : 0, 'agent_mcp_error', '');
  Object.defineProperty(safe, 'message', { get: () => text });
  if (error instanceof ApiError) safe.requestId = error.requestId;
  return <div className="mi-error">
    <Failure error={safe} onRetry={retry} />
    {code && <p className="muted small">{m('errorCode', { code })}</p>}
    {error instanceof ApiError && error.retryAfter != null && <p className="muted small">{m('retryAfter', { seconds: error.retryAfter })}</p>}
  </div>;
}

/** A bounded read of one operation; `input` is part of the identity (JSON). */
export function useRead<T>(operation: string, input: Record<string, unknown> = {}, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(enabled);
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify(input);
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    if (!enabled) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true); setError(null);
    void read<T>(operation, JSON.parse(key), null, controller.signal).then((value) => {
      if (!controller.signal.aborted) setData(value);
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) setError(e);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [operation, key, revision, enabled]);
  return { data, setData, error, loading, reload };
}

interface Intent { operation: string; input: Record<string, unknown>; key: string; saved: (output: unknown) => void; failed?: (error: unknown) => void }
interface WriteState { busy: boolean; pending: boolean; error: unknown; status: RunStatus | null; saved: boolean; operation: string | null;
  /** The payload of the request in flight or awaiting a retry (never a secret the page still needs). */
  input: Record<string, unknown> | null }

/** One intent at a time. A lost response keeps the exact payload and key in memory for a retry
 *  that cannot create a second record. Never persisted: a pending request may hold a token. */
export function useWrite() {
  const intent = useRef<Intent | null>(null);
  const flight = useRef(false);
  const live = useRef(true);
  const [state, setState] = useState<WriteState>({ busy: false, pending: false, error: null, status: null, saved: false, operation: null, input: null });
  useEffect(() => { live.current = true; return () => { live.current = false; intent.current = null; }; }, []);

  async function execute() {
    const request = intent.current;
    if (!request || flight.current) return;
    flight.current = true;
    setState({ busy: true, pending: true, error: null, status: null, saved: false, operation: request.operation, input: request.input });
    try {
      const result = await op<unknown>(request.operation, request.input, { key: request.key });
      if (!live.current) return;
      if (result.status === 'succeeded') {
        intent.current = null;
        setState({ busy: false, pending: false, error: null, status: null, saved: true, operation: request.operation, input: request.input });
        request.saved(result.output);
      } else {
        const pending = !['failed', 'cancelled', 'partial'].includes(result.status);
        if (!pending) intent.current = null;
        setState({ busy: false, pending, error: null, status: result.status, saved: false, operation: request.operation, input: request.input });
      }
    } catch (error) {
      if (!live.current) return;
      // A received application refusal is definitive; a network, gateway or 5xx failure may
      // follow a committed write, so the request stays for a retry under the same key.
      const pending = !(error instanceof ApiError && !error.upstream && error.status >= 400 && error.status < 500);
      if (!pending) { intent.current = null; request.failed?.(error); }
      setState({ busy: false, pending, error, status: null, saved: false, operation: request.operation, input: request.input });
    } finally {
      flight.current = false;
    }
  }
  function start<T>(operation: string, input: Record<string, unknown>, saved: (output: T) => void, failed?: (error: unknown) => void) {
    if (intent.current || flight.current) return false;
    intent.current = { operation, input: { ...input }, key: newKey(), saved: (output) => saved(output as T), failed };
    void execute();
    return true;
  }
  return { ...state, locked: state.busy || state.pending, start, retry: () => void execute(),
    dismiss: () => setState((s) => s.pending ? s : { ...s, saved: false, error: null, status: null }) };
}
export type Write = ReturnType<typeof useWrite>;

export function WriteFeedback({ write, savedText }: { write: Write; savedText?: string }) {
  const { m } = useText();
  return <div className="mi-feedback" aria-live="polite">
    {write.busy && <p role="status">{m('saving')}</p>}
    {write.error != null && <ErrorNotice error={write.error} />}
    {write.status && <p><RunBadge status={write.status} /></p>}
    {write.pending && !write.busy && <div className="notice warn col">
      <p>{m('pending')}</p>
      <div><button type="button" className="btn" onClick={write.retry}>{m('retrySame')}</button></div>
    </div>}
    {write.saved && <p className="notice ok" role="status">{savedText ?? m('saved')}</p>}
  </div>;
}

export function HealthBadge({ health }: { health: string }) {
  const { m } = useText();
  const view = healthView(health);
  return <span className={`badge ${view.tone}`}>{m(view.key)}</span>;
}
export function ConnectionStatusBadge({ status }: { status: string }) {
  const { m } = useText();
  return status === 'disabled' ? <span className="badge warn">{m('statusDisabled')}</span> : <span className="badge">{m('statusActive')}</span>;
}
export function CallStatusBadge({ status }: { status: string }) {
  const { m } = useText();
  const view = callStatusView(status);
  return <span className={`badge ${view.tone}`}>{m(view.key)}</span>;
}
export function OwnerText({ summary }: { summary: { owner_kind: string; owner_name: string | null } }) {
  const { m } = useText();
  if (summary.owner_kind !== 'workspace') return <>{m('personal')}</>;
  return <>{summary.owner_name ? m('workspaceOwner', { name: summary.owner_name }) : m('workspaceUnnamed')}</>;
}

/** Copies `text`; says what happened beside the button and to screen readers. */
export function CopyButton({ text, label, className = 'btn sm' }: { text: string; label?: string; className?: string }) {
  const { m } = useText();
  const [state, setState] = useState<'idle' | 'ok' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      if (typeof navigator === 'undefined' || !navigator.clipboard) throw new Error('clipboard_unavailable');
      await navigator.clipboard.writeText(text);
      setState('ok');
    } catch { setState('failed'); }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), 2500);
  }
  return <span className="mi-copy">
    <button type="button" className={className} onClick={() => void copy()}>{state === 'ok' ? m('copied') : label ?? m('copy')}</button>
    <span className="sr-only" role="status">{state === 'ok' ? m('copied') : ''}</span>
    {state === 'failed' && <span className="err small" role="alert">{m('copyFailed')}</span>}
  </span>;
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="mi-facts">{children}</dl>;
}
export function Fact({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <div><dt>{label}</dt><dd>{children}</dd></div>;
}
