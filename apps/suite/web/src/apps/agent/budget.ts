import type { ContextReference, FileReference } from './types';
/** Money formatting and the governed offer's shape. Amounts and rational prices never pass
 *  through Number. (The run authorization itself is issued by the server at send, design D-1;
 *  the page only shows the offer and the bound the server reports.) */
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
/** run_seconds_min: the shortest run the server authorizes (one whole model call fits its hold);
 *  older servers did not send it. */
export interface Offer extends Pricing { max_calls_limit: number; run_seconds_limit: number; run_seconds_min?: number }
/** What one send carries to `agent.turn.start` (the payer is added beside it). */
export interface SendIntent {
  conversation_id: string;
  text?: string;
  regenerate_of?: string;
  parent_message_id?: string;
  context_refs?: ContextReference[];
  file_refs?: FileReference[];
}

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

/** The major-unit decimal of an amount, as a person would type it (no unit code). */
export function majorUnits(value: string, unit: Unit): string {
  return formatAmount(value, unit).slice(0, -(unit.code.length + 1));
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

/** Validate remote pricing before rendering it. No fallback model/prices. */
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
