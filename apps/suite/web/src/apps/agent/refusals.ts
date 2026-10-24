/** Which refusal stopped a paid send or a later model call, in words a person can act on, and the
 *  one next step.
 *
 *  Codes come from the ledger/gateway ("billing_*", including a frozen wallet, the organization
 *  payer's grant and monthly limits and the per-answer run authorization the server issues at
 *  send), the risk controls ("risk_*": suspension, automatic brake, paid-dispatch breaker,
 *  staged-rollout allowlist), the platform cost admission ("cost_*", including the per-call
 *  maximum cost), the one-time setup the first paid send needs (the separate data-sharing
 *  consent "provider_consent_*", the accepted terms "terms_acceptance_required", personal
 *  spending limits "billing_payer_budget_not_configured"), content safety ("content_blocked_*" /
 *  "content_filtered_*") and an administrator's suspension of the person's Agent access
 *  ("agent_access_suspended"). Known codes are listed exactly; the risk, consent and content
 *  families are also recognised by their documented stems so a new code of the same kind is
 *  never shown as a generic failure (docs/agent-runtime.md, "Refusals in the chat"). An upstream
 *  402 is the operator's DeepSeek balance, never the person's wallet. */
import type { ChatText } from './chat.messages';

export type RefusalKind = 'funds' | 'org_funds' | 'frozen' | 'org_frozen' | 'org_limit' | 'org_payer' | 'limit' | 'no_limit' | 'brake'
  | 'suspended' | 'breaker' | 'allowlist' | 'call_cap' | 'not_configured' | 'consent' | 'consent_changed' | 'terms' | 'price_changed'
  | 'content_input' | 'content_output' | 'access_suspended' | 'effort' | 'too_long' | 'quote' | 'run_calls' | 'run_budget' | 'run_cap'
  | 'run_time';
/** "personal": pay with the personal wallet instead (an organization payer refused); "wallet":
 *  open the wallet page on the account service (a frozen wallet says why there); "settings":
 *  open Agent settings (limits, the per-answer maximum); "setup": finish the one-time "Start
 *  using Synwevo Agent" step (consent, terms, limits); "retry": send again, with no further
 *  step (a changed price applies to the new send; an expired run authorization is issued
 *  afresh). A next step that is a person to ask (the administrator) is part of the sentence. */
export type NextStep = 'topup' | 'wallet' | 'personal' | 'settings' | 'setup' | 'retry' | null;
export interface Refusal { kind: RefusalKind; code: string; text: ChatText; next: NextStep }

const TEXT: Record<RefusalKind, [ChatText, NextStep]> = {
  funds: ['refusedFunds', 'topup'],
  // A member cannot top up an organization's wallet; they can pay personally or ask its billing manager.
  org_funds: ['refusedOrgFunds', 'personal'],
  frozen: ['refusedFrozen', 'wallet'],
  org_frozen: ['refusedOrgFrozen', 'personal'],
  org_limit: ['refusedOrgLimit', 'personal'],
  org_payer: ['refusedOrgPayer', 'personal'],
  limit: ['refusedLimit', 'settings'],
  // The first paid send needs the one-time setup step (limits are prefilled there).
  no_limit: ['refusedNoLimit', 'setup'],
  brake: ['refusedBrake', null],
  suspended: ['refusedSuspended', null],
  breaker: ['refusedBreaker', null],
  allowlist: ['refusedAllowlist', null],
  call_cap: ['refusedCallCap', null],
  not_configured: ['refusedNotConfigured', null],
  consent: ['refusedConsent', 'setup'],
  consent_changed: ['refusedConsentChanged', 'setup'],
  terms: ['refusedTerms', 'setup'],
  // The new price applies to the next send; nothing to confirm first.
  price_changed: ['refusedPriceChanged', 'retry'],
  // The message stays in the composer to be edited; a stopped answer can be retried below it.
  content_input: ['refusedContentInput', null],
  content_output: ['refusedContentOutput', null],
  // An administrator's decision about this person: only they can explain or lift it.
  access_suspended: ['refusedAccessSuspended', null],
  effort: ['refusedEffort', 'settings'],
  too_long: ['refusedTooLong', null],
  // A legacy run authorization that no longer opens the run: a new send issues a fresh one.
  quote: ['refusedQuote', 'retry'],
  run_calls: ['refusedRunCalls', null],
  run_budget: ['refusedRunBudget', 'settings'],
  run_cap: ['refusedRunCap', 'settings'],
  run_time: ['refusedRunTime', 'retry'],
};
/** The sentence for each kind (for tests and documentation). */
export const REFUSAL_TEXT: Record<RefusalKind, ChatText> = Object.fromEntries(
  Object.entries(TEXT).map(([kind, [text]]) => [kind, text])) as Record<RefusalKind, ChatText>;
/** The next step for each kind (for tests and documentation). */
export const REFUSAL_NEXT: Record<RefusalKind, NextStep> = Object.fromEntries(
  Object.entries(TEXT).map(([kind, [, next]]) => [kind, next])) as Record<RefusalKind, NextStep>;

const EXACT: Record<string, RefusalKind> = {
  billing_run_call_limit_exceeded: 'run_calls',
  billing_run_budget_exceeded: 'run_budget',
  // The person's own per-answer maximum (Agent settings) is below one call's hold, or its unit
  // no longer matches the sale unit: refused at send, nothing stored.
  billing_run_cap_below_call_hold: 'run_cap', billing_run_cap_unit_changed: 'run_cap',
  // The run's time is used up before its next call (admission fixes the deadline; one send per attempt).
  billing_run_time_too_short: 'run_time',
  billing_insufficient_funds: 'funds', insufficient_funds: 'funds',
  billing_wallet_frozen: 'frozen', wallet_frozen: 'frozen',
  // Organization payment (org-payments): the member's monthly allowance (the server has no
  // per-workspace limit on a run: money-3), and a grant, membership or organization that no
  // longer lets this person charge it.
  billing_org_member_monthly_limit_exceeded: 'org_limit', billing_org_workspace_monthly_limit_exceeded: 'org_limit',
  billing_org_payer_not_granted: 'org_payer', billing_org_payer_not_member: 'org_payer',
  billing_organization_unavailable: 'org_payer', billing_organization_billing_not_configured: 'org_payer',
  billing_organization_billing_not_supported: 'org_payer', billing_payer_forbidden: 'org_payer',
  billing_invalid_payer: 'org_payer',
  billing_payer_daily_budget_exceeded: 'limit', billing_payer_weekly_budget_exceeded: 'limit',
  billing_payer_monthly_budget_exceeded: 'limit', billing_payer_budget_disabled: 'limit',
  billing_payer_budget_not_configured: 'no_limit',
  // The terms, AI use rules and pricing in force are not accepted yet (first use, or a new version).
  terms_acceptance_required: 'terms',
  // Upstream 402 is the operator's balance, never the person's: a breaker, not "your funds".
  provider_balance: 'breaker', billing_provider_balance: 'breaker',
  billing_not_configured: 'not_configured',
  billing_model_route_unavailable: 'not_configured',
  // The engine's own model_configuration_changed is a credential or model binding change, not a price.
  billing_consent_model_changed: 'price_changed',
  billing_model_configuration_changed: 'price_changed',
  // The governed route's confirmation has lapsed: the service, not the person, and not a new price.
  billing_route_confirmation_expired: 'breaker',
  // The platform's per-call maximum cost (cost policy): a policy limit on this call, not an outage.
  billing_cost_call_limit_exceeded: 'call_cap', cost_call_limit_exceeded: 'call_cap',
  provider_consent_version_changed: 'consent_changed',
  // Safety package: an administrator suspended this person's Agent access (403 on send, or the
  // check before every model call of a running answer).
  agent_access_suspended: 'access_suspended',
  // Gateway: the route does not offer the chosen reasoning effort; refused before anything is held.
  model_effort_unavailable: 'effort', billing_model_effort_unavailable: 'effort',
  // Gateway: the request is above the per-call size bound; refused before anything is held.
  billing_model_request_too_large: 'too_long',
  // A run authorization that no longer opens the run (expired, used, revoked or the conversation moved on).
  billing_quote_expired_or_revoked: 'quote', billing_quote_context_changed: 'quote', billing_quote_already_used: 'quote',
  billing_quote_not_found: 'quote', quote_not_found: 'quote', billing_quote_conversation_unavailable: 'quote',
  // The page showed a price that is no longer in force (addendum AD-1, seen_price_id /
  // seen_route_version): nothing was sent; the new price is read and shown, then one click sends.
  billing_price_changed: 'price_changed',
  // The route's price can no longer be applied: the next send runs at the current price.
  billing_price_not_applicable: 'price_changed', billing_price_route_mismatch: 'price_changed',
  billing_price_not_published: 'price_changed',
  // The organization's wallet no longer matches the payer the run named.
  billing_org_payer_inconsistent: 'org_payer',
  // The governed model configuration cannot be read right now: the service, not the person.
  billing_model_configuration_unavailable: 'breaker',
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
  // "provider_consent_not_needed" answers a consent page's request, never a send.
  if (/^provider_consent_/.test(code) && code !== 'provider_consent_not_needed') return 'consent';
  // Content safety: an input rule refuses before anything is sent; an output rule or the
  // provider's own filter stops the answer (whether that is charged is the admin's policy).
  if (/^content_(blocked|filtered)_input/.test(code)) return 'content_input';
  if (/^content_(blocked|filtered)_output/.test(code)) return 'content_output';
  return null;
}

/** A code whose kind and next step are shared but whose moment differs: refused at the send, so
 *  the sentence says the message was not sent (the kind's own sentence is the mid-run one). */
const CODE_TEXT: Record<string, ChatText> = { billing_price_changed: 'refusedPriceUpdated' };

/** `organization`: the refused run was charged to an organization (the selected payer for a
 *  fresh refusal; the answer's ledger payer for one in the transcript). */
export function refusalOf(error: unknown, context: { organization?: boolean } = {}): Refusal | null {
  const code = errorCode(error);
  let kind = refusalKind(code);
  if (context.organization && kind === 'funds') kind = 'org_funds';
  if (context.organization && kind === 'frozen') kind = 'org_frozen';
  return kind ? { kind, code, text: CODE_TEXT[code] ?? TEXT[kind][0], next: TEXT[kind][1] } : null;
}
