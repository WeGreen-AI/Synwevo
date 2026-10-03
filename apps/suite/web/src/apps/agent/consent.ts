import { ApiError } from '@synwevo/auth-client/api';
import { boundedCount, validatePricing, type Pricing, type Quote, type QuoteParams } from './budget';

export type ConsentStatus = 'active' | 'revoked' | 'expired' | 'exhausted' | 'scope_changed' | 'model_changed' | 'conversation_unavailable';
export interface Consent extends Pricing {
  id: string; conversation_id: string; scope: string[]; max_runs: number; used_runs: number; remaining_runs: number;
  status: ConsentStatus; created_at: number; expires_at: number | null; until_revoked: boolean;
  budget: string; total_maximum: string; max_calls: number; run_seconds: number;
}
export interface ConsentInput {
  conversation_id: string; quote_id: string; max_runs: number; expires_at: number | null; until_revoked: boolean;
}
export const consentStatuses: ConsentStatus[] = ['active', 'revoked', 'expired', 'exhausted', 'scope_changed', 'model_changed', 'conversation_unavailable'];
const maxMoney = 9223372036854775807n;
export function consentTotal(budget: string, runs: number): string {
  if (!/^[1-9][0-9]{0,18}$/.test(budget) || !Number.isSafeInteger(runs) || runs < 1 || runs > 1000) throw new Error('consent_invalid_limits');
  const result = BigInt(budget) * BigInt(runs);
  if (result > maxMoney) throw new Error('consent_total_overflow');
  return result.toString();
}
export function sameScope(a: string[] | null | undefined, b: string[] | null | undefined): boolean {
  return Array.isArray(a) && Array.isArray(b) && a.length === b.length
    && [...a].sort().every((item, index) => item === [...b].sort()[index]);
}
export function consentExpiry(value: string, untilRevoked: boolean, now = Date.now()): number | null {
  if (untilRevoked) { if (value) throw new Error('consent_expiry_required'); return null; }
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (!match) throw new Error('consent_invalid_expiry');
  const [, y, mo, d, h, mi] = match.map(Number);
  const date = new Date(y, mo - 1, d, h, mi);
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d || date.getHours() !== h || date.getMinutes() !== mi
    || date.getTime() <= now || date.getTime() > now + 366 * 86400000) throw new Error('consent_invalid_expiry');
  return Math.floor(date.getTime() / 1000);
}
export function consentInput(quote: Quote, scope: string[] | null, runs: string, expiry: string, untilRevoked: boolean, now = Date.now()): ConsentInput {
  if (!Array.isArray(scope)) throw new Error('consent_explicit_scope_required');
  if (quote.status !== 'quoted' || quote.turn_id || quote.expires_at * 1000 <= now || quote.deadline * 1000 <= now) throw new Error('quote_expired_or_revoked');
  const max_runs = boundedCount(runs, 1000);
  consentTotal(quote.budget, max_runs);
  return { conversation_id: quote.conversation_id, quote_id: quote.id, max_runs,
    expires_at: consentExpiry(expiry, untilRevoked, now), until_revoked: untilRevoked };
}
function stamp(value: number) { return Number.isSafeInteger(value) && value > 0 && Number.isFinite(new Date(value * 1000).getTime()); }
export function validateConsent(value: Consent, cid: string): void {
  validatePricing(value);
  if (typeof value.id !== 'string' || !value.id || value.conversation_id !== cid
    || !Array.isArray(value.scope) || value.scope.some((v) => typeof v !== 'string' || !v) || new Set(value.scope).size !== value.scope.length
    || !consentStatuses.includes(value.status) || !stamp(value.created_at) || typeof value.until_revoked !== 'boolean'
    || (value.until_revoked ? value.expires_at !== null : value.expires_at === null || !stamp(value.expires_at) || value.expires_at <= value.created_at)
    || !Number.isSafeInteger(value.used_runs) || value.used_runs < 0 || value.used_runs > value.max_runs
    || value.remaining_runs !== value.max_runs - value.used_runs
    || value.total_maximum !== consentTotal(value.budget, value.max_runs)
    || !Number.isSafeInteger(value.max_calls) || value.max_calls < 1
    || !Number.isSafeInteger(value.run_seconds) || value.run_seconds < 1 || value.run_seconds > 3600
    || BigInt(value.budget) < BigInt(value.per_call_hold)) throw new Error('invalid_consent_response');
}
export function validateCreatedConsent(value: Consent, input: ConsentInput, quote: Quote, scope: string[], params: QuoteParams): void {
  validateConsent(value, input.conversation_id);
  if (!sameScope(value.scope, scope) || value.max_runs !== input.max_runs || value.expires_at !== input.expires_at
    || value.until_revoked !== input.until_revoked || value.budget !== quote.budget || value.max_calls !== quote.max_calls
    || value.run_seconds !== params.run_seconds
    || (['environment', 'model', 'confirmed_route', 'route_version', 'price_id', 'terms_ref', 'per_tokens', 'per_call_hold'] as const)
      .some((key) => value[key] !== quote[key])
    || value.unit.code !== quote.unit.code || value.unit.scale !== quote.unit.scale
    || (['input_uncached', 'input_cached', 'output'] as const).some((key) => value.rates[key].numerator !== quote.rates[key].numerator
      || value.rates[key].denominator !== quote.rates[key].denominator)) throw new Error('invalid_consent_response');
}
export function validateConsentList(value: { consents: Consent[] }, cid: string): void {
  if (!value || !Array.isArray(value.consents) || value.consents.length > 20) throw new Error('invalid_consent_response');
  const ids = new Set<string>();
  for (const consent of value.consents) {
    validateConsent(consent, cid);
    if (ids.has(consent.id)) throw new Error('invalid_consent_response');
    ids.add(consent.id);
  }
}
export function effectiveConsentStatus(value: Consent, scope: string[] | null, now = Date.now()): ConsentStatus {
  if (value.status !== 'active') return value.status;
  if (value.expires_at !== null && value.expires_at * 1000 <= now) return 'expired';
  if (!sameScope(value.scope, scope)) return 'scope_changed';
  return value.remaining_runs === 0 ? 'exhausted' : 'active';
}
export function consentCode(error: unknown): string {
  if (!(error instanceof ApiError) || error.upstream) return error instanceof Error ? error.message : '';
  const code = error.code === 'idempotent_failure' ? error.detail?.code : error.code;
  return typeof code === 'string' ? code.replace(/^billing_/, '') : '';
}
export function uncertainConsentError(error: unknown, retry: boolean): boolean {
  if (!(error instanceof ApiError) || error.upstream) return true;
  if (retry && error.code !== 'idempotent_failure') return true;
  if (['idempotency_conflict', 'idempotency_in_progress', 'run_in_progress', 'outcome_unknown', 'unknown',
    'run_result_unavailable', 'result_access_unverifiable'].includes(consentCode(error))) return true;
  return error.status < 400 || error.status >= 500 || error.status === 408 || error.status === 429;
}

export interface ConsentTicket {
  operation: string; input: Readonly<Record<string, unknown>>; key: string;
}
export interface ConsentWriteState {
  phase: 'idle' | 'busy' | 'uncertain' | 'succeeded' | 'refused'; ticket: ConsentTicket | null; output?: unknown; error?: unknown;
}
/** A refusal the page states itself; `code` lets the Agent error text translate it. */
export class ConsentStateError extends Error { constructor(readonly code: string) { super(code); } }
export class PendingConsentOperation extends ConsentStateError { constructor() { super('consent_pending_operation'); } }
type Sender = (operation: string, input: Record<string, unknown>, key: string) => Promise<{ status: string; output: unknown }>;
/** One manually dispatched operation per conversation; never an effect-driven retry or fallback. */
export function createConsentWriter(send: Sender, newKey: () => string) {
  let state: ConsentWriteState = { phase: 'idle', ticket: null };
  let validate: (output: any) => void = () => {};
  const listeners = new Set<() => void>();
  const publish = (next: ConsentWriteState) => { state = next; listeners.forEach((listener) => listener()); };
  async function execute(retry: boolean) {
    const ticket = state.ticket!;
    publish({ phase: 'busy', ticket });
    try {
      const result = await send(ticket.operation, { ...ticket.input }, ticket.key);
      if (result.status === 'failed' || result.status === 'cancelled') throw new ApiError(409, 'idempotent_failure', '', { code: 'consent_operation_refused' });
      if (result.status !== 'succeeded') throw new Error('consent_outcome_unknown');
      validate(result.output);
      publish({ phase: 'succeeded', ticket, output: result.output });
      return result.output;
    } catch (error) {
      publish({ phase: uncertainConsentError(error, retry) ? 'uncertain' : 'refused', ticket, error });
      throw error;
    }
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    pending: () => state.phase === 'busy' || state.phase === 'uncertain',
    start<T>(operation: string, input: Record<string, unknown>, check: (value: T) => void): Promise<T> {
      if (state.phase === 'busy' || state.phase === 'uncertain') return Promise.reject(new PendingConsentOperation());
      validate = check;
      const ticket = { operation, input: Object.freeze(structuredClone(input)), key: newKey() };
      state = { phase: 'idle', ticket };
      return execute(false) as Promise<T>;
    },
    retry<T>(): Promise<T> {
      return state.phase === 'uncertain' ? execute(true) as Promise<T> : Promise.reject(new PendingConsentOperation());
    },
    matches(operation: string, input: Record<string, unknown>) {
      return state.ticket?.operation === operation && JSON.stringify(state.ticket.input) === JSON.stringify(input);
    },
    dismiss() { if (state.phase === 'refused' || state.phase === 'succeeded') publish({ phase: 'idle', ticket: null }); },
  };
}
export type ConsentWriter = ReturnType<typeof createConsentWriter>;
