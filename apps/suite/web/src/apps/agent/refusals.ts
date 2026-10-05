/** Which refusal stopped a paid send or a later model call, in words a person can act on, and the
 *  one next step.
 *
 *  Codes come from the ledger/gateway ("billing_*"), the risk controls ("risk_*": suspension,
 *  automatic brake, paid-dispatch breaker, staged-rollout allowlist), the platform cost admission
 *  ("cost_*") and the separate data-sharing consent ("provider_consent_*"). Known codes are listed
 *  exactly; the risk and consent families are also recognised by their documented stems so a new
 *  code of the same kind is never shown as a generic failure (docs/agent-runtime.md, "Refusals in
 *  the chat"). An upstream 402 is the operator's DeepSeek balance, never the person's wallet. */
import type { ChatText } from './chat.messages';

export type RefusalKind = 'funds' | 'limit' | 'no_limit' | 'brake' | 'suspended' | 'breaker' | 'allowlist'
  | 'not_configured' | 'consent' | 'price_changed';
export type NextStep = 'topup' | 'settings' | 'consent' | 'requote' | null;
export interface Refusal { kind: RefusalKind; code: string; text: ChatText; next: NextStep }

const TEXT: Record<RefusalKind, [ChatText, NextStep]> = {
  funds: ['refusedFunds', 'topup'],
  limit: ['refusedLimit', 'settings'],
  no_limit: ['refusedNoLimit', 'settings'],
  brake: ['refusedBrake', null],
  suspended: ['refusedSuspended', null],
  breaker: ['refusedBreaker', null],
  allowlist: ['refusedAllowlist', null],
  not_configured: ['refusedNotConfigured', null],
  consent: ['refusedConsent', 'consent'],
  price_changed: ['refusedPriceChanged', 'requote'],
};

const EXACT: Record<string, RefusalKind> = {
  billing_insufficient_funds: 'funds', insufficient_funds: 'funds',
  billing_payer_daily_budget_exceeded: 'limit', billing_payer_weekly_budget_exceeded: 'limit',
  billing_payer_monthly_budget_exceeded: 'limit', billing_payer_budget_disabled: 'limit',
  billing_payer_budget_not_configured: 'no_limit',
  // Upstream 402 is the operator's balance, never the person's: a breaker, not "your funds".
  provider_balance: 'breaker', billing_provider_balance: 'breaker',
  billing_not_configured: 'not_configured',
  billing_model_route_unavailable: 'not_configured',
  // The engine's own model_configuration_changed is a credential or model binding change, not a price.
  billing_consent_model_changed: 'price_changed',
  billing_model_configuration_changed: 'price_changed', billing_route_confirmation_expired: 'price_changed',
};

/** The code an error carries, unwrapping an idempotent replay of a refused request. A failure the
 *  gateway itself produced (`upstream`) has no application code. */
export function errorCode(error: unknown): string {
  if (!error || typeof error !== 'object') return '';
  const e = error as { code?: unknown; detail?: { code?: unknown } | null; upstream?: unknown };
  if (e.upstream) return '';
  const code = e.code === 'idempotent_failure' ? e.detail?.code : e.code;
  return typeof code === 'string' ? code : '';
}

export function refusalKind(code: string): RefusalKind | null {
  if (!code) return null;
  if (EXACT[code]) return EXACT[code];
  const bare = code.replace(/^billing_/, '');
  if (/^risk_/.test(bare)) {
    if (/allowlist/.test(bare)) return 'allowlist';
    if (/breaker/.test(bare)) return 'breaker';
    if (/brake/.test(bare)) return 'brake';
    if (/suspend/.test(bare)) return 'suspended';
    // Any other risk refusal (capacity, unknown payer) still stopped before dispatch.
    return 'breaker';
  }
  // Platform cost admission (switch, caps, unknown-usage limits): the service, not the person.
  if (/^cost_/.test(bare)) return 'breaker';
  // Payment consents are "billing_consent_*"; the data-sharing consent is "provider_consent_*".
  if (/^provider_consent_/.test(code)) return 'consent';
  return null;
}

export function refusalOf(error: unknown): Refusal | null {
  const code = errorCode(error);
  const kind = refusalKind(code);
  return kind ? { kind, code, text: TEXT[kind][0], next: TEXT[kind][1] } : null;
}
