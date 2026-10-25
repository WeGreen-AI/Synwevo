/** What every page of the MCP integration centre shares: the catalogue accessor, bounded reads,
 *  one write intent at a time with a retry that reuses its idempotency key, refusals in the
 *  centre's own words (never the server's prose, which may echo a submitted secret), badges and
 *  a clipboard button. Patterned on the Agent settings page. */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
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
  gateway_blocked: 'error', result_access_changed: 'mcp_connection_not_found', run_result_unavailable: 'executed',
  output_too_large: 'tooLarge',
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

/** What a failed write says about the request it answered.
 *
 *  `refused`: definitive, nothing more will happen under this key. A refusal Synwevo itself sent
 *  is definitive whatever its status: an MCP refusal (`mcp_unreachable` 502, `mcp_circuit_open`
 *  503, `mcp_timeout` 504 …) is recorded as a failed run, and repeating its key only replays
 *  `idempotent_failure`.
 *  `unknown`: the request may have been committed. Only a lost connection, an intermediary's
 *  answer and the server's own crash codes leave the outcome open; the intent stays for a retry
 *  under the same key, whose receipt settles it.
 *  `executed`: the server carried the request out but cannot hand its result back. Either it holds
 *  a receipt for this key without a replayable result (`run_result_unavailable`: the write
 *  operations keep none), or the result was too large to record (`output_too_large`): every write
 *  this centre sends is two-phase (`transactional=False`, agent/mcp/ops.py), whose handler has
 *  committed before its result is measured. Sending the intent again, under any key, could repeat
 *  it: the page re-reads the record instead. A page read (a resource read, a rendered prompt) that
 *  is too large changed nothing a re-read could show: it is refused, with the size as its reason.
 *
 *  On a retry, a refusal that precedes the receipt lookup (sign-in, access, rate limits) cannot
 *  tell whether the first attempt committed, so it leaves the outcome open. The MCP refusals come
 *  from the operations themselves, which run on a retry only when the first attempt never reached
 *  the server. A refusal of the request's own shape or size reads only the payload, which the
 *  retry repeats unchanged: the first attempt was refused the same way or never arrived. */
export type WriteOutcome = 'refused' | 'unknown' | 'executed';
const OUTCOME_UNKNOWN = new Set(['network', 'upstream_unavailable', 'gateway_blocked', 'server_error', 'internal_error',
  'operation_failed']);
const EXECUTED = new Set(['run_result_unavailable', 'output_too_large']);
const REQUEST_REFUSED = new Set(['invalid_input', 'input_too_large']);
/** Two-phase only so that they are recorded with their call: they read and show, and change no record. */
const PAGE_READS = new Set(['agent.mcp.resource.read_page', 'agent.mcp.prompt.render']);
export function writeOutcome(error: unknown, retrying: boolean, operation?: string): WriteOutcome {
  if (!(error instanceof ApiError) || error.upstream || error.status === 0) return 'unknown';
  if (error.code === 'output_too_large' && operation && PAGE_READS.has(operation)) return 'refused';
  if (EXECUTED.has(error.code)) return 'executed';
  if (OUTCOME_UNKNOWN.has(error.code)) return 'unknown';
  if (!retrying) return 'refused';
  return error.code === 'idempotent_failure' || error.code.startsWith('mcp_') || REQUEST_REFUSED.has(error.code) ? 'refused' : 'unknown';
}

interface Intent {
  operation: string; input: Record<string, unknown>; key: string; attempts: number;
  saved: (output: unknown) => void; executed?: () => void;
}
export interface WriteState { busy: boolean; pending: boolean; error: unknown; status: RunStatus | null; saved: boolean;
  /** The server executed the request but cannot return its result; the page re-reads the record. */
  executed: boolean; operation: string | null;
  /** The payload of the request in flight or awaiting a retry (never a secret the page still needs). */
  input: Record<string, unknown> | null }
const IDLE: WriteState = { busy: false, pending: false, error: null, status: null, saved: false, executed: false, operation: null, input: null };
type Send = (operation: string, input: Record<string, unknown>, key: string) => Promise<{ status: RunStatus; output: unknown }>;

/** One intent at a time, without React so its decisions can be exercised directly. A lost
 *  response keeps the exact payload and key in memory for a retry that cannot create a second
 *  record. Never persisted: a pending request may hold a token. */
export function createWriter(send: Send, newIntentKey: () => string = newKey) {
  let intent: Intent | null = null;
  let flight = false;
  let open = true;
  let state = IDLE;
  const listeners = new Set<() => void>();
  const publish = (next: WriteState) => { state = next; listeners.forEach((listener) => listener()); };

  async function execute() {
    const request = intent;
    if (!request || flight || !open) return;
    flight = true;
    const retrying = request.attempts > 0;
    request.attempts += 1;
    const base = { operation: request.operation, input: request.input };
    publish({ ...IDLE, ...base, busy: true, pending: true });
    try {
      const result = await send(request.operation, request.input, request.key);
      if (!open) return;
      if (result.status === 'succeeded') {
        intent = null;
        publish({ ...IDLE, ...base, saved: true });
        request.saved(result.output);
      } else {
        const pending = !['failed', 'cancelled', 'partial'].includes(result.status);
        if (!pending) intent = null;
        publish({ ...IDLE, ...base, pending, status: result.status });
      }
    } catch (error) {
      if (!open) return;
      const outcome = writeOutcome(error, retrying, request.operation);
      if (outcome !== 'unknown') intent = null;
      publish({ ...IDLE, ...base, pending: outcome === 'unknown', executed: outcome === 'executed', error });
      if (outcome === 'executed') request.executed?.();
    } finally {
      flight = false;
    }
  }
  return {
    getState: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    /** Starts an intent unless one is in flight or awaiting its retry. `executed` runs when the
     *  server executed it but cannot return the result. */
    start<T>(operation: string, input: Record<string, unknown>, saved: (output: T) => void, executed?: () => void) {
      if (intent || flight || !open) return false;
      intent = { operation, input: { ...input }, key: newIntentKey(), attempts: 0, saved: (output) => saved(output as T), executed };
      void execute();
      return true;
    },
    retry: () => execute(),
    dismiss() { if (!state.pending) publish({ ...state, saved: false, error: null, status: null, executed: false }); },
    /** The page mounted (again, under StrictMode) or left: a closed writer forgets its intent. */
    open() { open = true; },
    close() { open = false; intent = null; },
  };
}
export type Writer = ReturnType<typeof createWriter>;

export function useWrite() {
  const [writer] = useState(() => createWriter((operation, input, key) => op<unknown>(operation, input, { key })));
  const state = useSyncExternalStore(writer.subscribe, writer.getState, writer.getState);
  useEffect(() => { writer.open(); return () => writer.close(); }, [writer]);
  return { ...state, locked: state.busy || state.pending, start: writer.start, retry: () => void writer.retry(), dismiss: writer.dismiss };
}
export type Write = ReturnType<typeof useWrite>;

export function WriteFeedback({ write, savedText }: { write: Write; savedText?: string }) {
  const { m } = useText();
  return <div className="mi-feedback" aria-live="polite">
    {write.busy && <p role="status">{m('saving')}</p>}
    {write.error != null && !write.executed && <ErrorNotice error={write.error} />}
    {write.executed && <p className="notice warn" role="status">{m(errorCodeOf(write.error) === 'output_too_large' ? 'executedTooLarge' : 'executed')}</p>}
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
/** Enabled or disabled; a platform stop (which also disables) is named as such, since only the
 *  platform can lift it. */
export function ConnectionStatusBadge({ status, stopped = false }: { status: string; stopped?: boolean }) {
  const { m } = useText();
  if (stopped) return <span className="badge err">{m('statusPlatformStopped')}</span>;
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
