import type { ContextReference, FileReference } from './types';
/** Personal run consent contracts. Amounts and rational prices never pass through Number. */
export interface Unit { code: string; scale: number }
export interface Rate { numerator: string; denominator: string }
export interface Pricing {
  environment: 'test' | 'live';
  model: string;
  confirmed_route: string;
  route_version: string;
  price_id: string;
  unit: Unit;
  rates: { input_uncached: Rate; input_cached: Rate; output: Rate };
  per_tokens: number;
  terms_ref: string;
  per_call_hold: string;
}
/** run_seconds_min: the shortest run the server quotes (one whole model call fits its hold);
 *  older servers did not send it, so 1. */
export interface Offer extends Pricing { max_calls_limit: number; run_seconds_limit: number; run_seconds_min?: number }
export function runSecondsMin(offer: Pick<Offer, 'run_seconds_min'>): number { return offer.run_seconds_min ?? 1; }
// Issued quotes contain the fixed limits, rather than the offer's editable upper limits.
export interface Quote extends Pricing {
  id: string;
  payer_user_id: string;
  conversation_id: string;
  budget: string;
  max_calls: number;
  deadline: number;
  expires_at: number;
  turn_id: string | null;
  status: 'quoted' | 'accepted' | 'expired' | 'revoked';
}
export interface BudgetIntent {
  conversation_id: string;
  text?: string;
  regenerate_of?: string;
  parent_message_id?: string;
  context_refs?: ContextReference[];
  file_refs?: FileReference[];
}
export interface QuoteParams { budget: string; max_calls: number; run_seconds: number }

const MAX_BUDGET = 9223372036854775807n;

function validScale(scale: number): void {
  if (!Number.isInteger(scale) || scale < 0 || scale > 18) throw new Error('invalid_unit');
}
function unsigned(value: string): bigint {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,127})$/.test(value)) throw new Error('invalid_amount');
  return BigInt(value);
}
function decimal(value: bigint, scale: number): string {
  if (!scale) return value.toString();
  const digits = value.toString().padStart(scale + 1, '0');
  return `${digits.slice(0, -scale)}.${digits.slice(-scale)}`;
}

/** Strict major-unit decimal input; no exponent, sign, grouping, rounding or silent truncation. */
export function parseBudget(value: string, scale: number): string {
  validScale(scale);
  if (typeof value !== 'string' || value.length > 128 || !/^[0-9]+(?:\.[0-9]+)?$/.test(value)) {
    throw new Error('invalid_budget');
  }
  const [whole, fraction = ''] = value.split('.');
  if (fraction.length > scale) throw new Error('budget_precision');
  const units = BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, '0') || '0');
  if (units <= 0n || units > MAX_BUDGET) throw new Error('invalid_budget');
  return units.toString();
}

/** Keep the full unit precision visible, including the smallest unit at scale 18. */
export function formatAmount(value: string, unit: Unit): string {
  validScale(unit.scale);
  return `${decimal(unsigned(value), unit.scale)} ${unit.code}`;
}

function gcd(a: bigint, b: bigint): bigint {
  while (b) [a, b] = [b, a % b];
  return a;
}

/** Exact major-unit price for the existing per_tokens batch, never a rounded token price. */
export function formatRate(rate: Rate, unit: Unit): string {
  validScale(unit.scale);
  let numerator = unsigned(rate.numerator);
  let denominator = unsigned(rate.denominator) * 10n ** BigInt(unit.scale);
  if (!denominator) throw new Error('invalid_rate');
  const divisor = gcd(numerator, denominator);
  numerator /= divisor;
  denominator /= divisor;
  let rest = denominator;
  let twos = 0, fives = 0;
  while (rest % 2n === 0n) { rest /= 2n; twos++; }
  while (rest % 5n === 0n) { rest /= 5n; fives++; }
  if (rest !== 1n) return `${numerator}/${denominator} ${unit.code}`;
  const places = Math.max(twos, fives);
  const exact = decimal(numerator * (10n ** BigInt(places) / denominator), places);
  return `${places ? exact.replace(/\.?0+$/, '') : exact} ${unit.code}`;
}

export function boundedCount(value: string, maximum: number, minimum = 1): number {
  if (!/^[1-9][0-9]*$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) > maximum
    || Number(value) < minimum) {
    throw new Error('invalid_count');
  }
  return Number(value);
}

/** Validate remote pricing before rendering it or enabling consent. No fallback model/prices. */
export function validatePricing(value: Pricing): void {
  if (!value || !['test', 'live'].includes(value.environment)
    || [value.model, value.confirmed_route, value.route_version, value.price_id, value.terms_ref, value.unit?.code]
      .some((item) => typeof item !== 'string' || !item.trim())
    || !Number.isSafeInteger(value.per_tokens) || value.per_tokens <= 0) throw new Error('invalid_offer');
  formatAmount(value.per_call_hold, value.unit);
  for (const key of ['input_uncached', 'input_cached', 'output'] as const) formatRate(value.rates[key], value.unit);
}
export function validateOffer(value: Offer): void {
  validatePricing(value);
  if (!Number.isSafeInteger(value.max_calls_limit) || value.max_calls_limit < 1
    || !Number.isInteger(value.run_seconds_limit) || value.run_seconds_limit < 1 || value.run_seconds_limit > 3600
    || (value.run_seconds_min !== undefined && (!Number.isInteger(value.run_seconds_min) || value.run_seconds_min < 1
      || value.run_seconds_min > 3600))) {
    throw new Error('invalid_offer');
  }
}
export function validateQuote(value: Quote, intent: BudgetIntent, params: QuoteParams): void {
  validatePricing(value);
  if (!value.id || !value.payer_user_id || value.conversation_id !== intent.conversation_id
    || value.budget !== params.budget || value.max_calls !== params.max_calls
    || unsigned(value.budget) < unsigned(value.per_call_hold)
    || !Number.isSafeInteger(value.deadline) || value.deadline <= 0 || !Number.isFinite(new Date(value.deadline * 1000).getTime())
    || !Number.isSafeInteger(value.expires_at) || value.expires_at <= 0 || value.expires_at > value.deadline
    || !['quoted', 'accepted', 'expired', 'revoked'].includes(value.status)
    || !(value.turn_id === null || typeof value.turn_id === 'string')) throw new Error('invalid_quote');
}
export function sameIntent(a: BudgetIntent, b: BudgetIntent): boolean {
  return a.conversation_id === b.conversation_id && a.text === b.text
    && a.regenerate_of === b.regenerate_of && a.parent_message_id === b.parent_message_id
    && (a.file_refs?.length ?? 0) === (b.file_refs?.length ?? 0)
    && (a.file_refs ?? []).every((ref, i) => {
      const other = b.file_refs?.[i];
      return other && ref.file_id === other.file_id && ref.snapshot === other.snapshot;
    })
    && (a.context_refs?.length ?? 0) === (b.context_refs?.length ?? 0)
    && (a.context_refs ?? []).every((ref, i) => {
      const other = b.context_refs?.[i];
      return other && ref.kind === other.kind && ref.workspace_id === other.workspace_id
        && ref.resource_id === other.resource_id && ref.snapshot === other.snapshot;
    });
}
export function quoteExpired(quote: Quote, now = Date.now()): boolean {
  return quote.status === 'expired' || Math.min(quote.expires_at, quote.deadline) * 1000 <= now;
}
