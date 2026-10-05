/** Money and token display for the chat. Ledger amounts are integer smallest units, carried as
 *  decimal strings; they never pass through a binary float before Intl formats them. */
import type { ChatText } from './chat.messages';

export interface MoneyUnit { code: string; scale: number }
export interface TurnTokens { input_cached: number; input_uncached: number; output: number; reasoning: number }
/** The shared per-turn cost contract (synwevo_suite.agent.turn_cost). */
export interface TurnCost {
  charged_minor: string; currency: string; scale: number; tokens: TurnTokens;
  model: string | null; band: string | null; state: 'settled' | 'pending' | 'held'; held_minor: string;
}

const MINOR = /^(0|[1-9][0-9]{0,30})$/;
const SIGNED = /^-?(0|[1-9][0-9]{0,30})$/;

function validUnit(unit: MoneyUnit): boolean {
  return !!unit && typeof unit.code === 'string' && !!unit.code.trim() && Number.isInteger(unit.scale) && unit.scale >= 0 && unit.scale <= 18;
}

/** Exact major-unit decimal ("12.0034") of a signed minor amount, trailing zeros trimmed to at least `keep` places. */
export function majorDecimal(minor: string, scale: number, keep = 0): string {
  if (typeof minor !== 'string' || !SIGNED.test(minor) || !Number.isInteger(scale) || scale < 0 || scale > 18) throw new Error('invalid_amount');
  const negative = minor.startsWith('-');
  const digits = (negative ? minor.slice(1) : minor).padStart(scale + 1, '0');
  const whole = scale ? digits.slice(0, -scale) : digits;
  let fraction = scale ? digits.slice(-scale) : '';
  while (fraction.length > keep && fraction.endsWith('0')) fraction = fraction.slice(0, -1);
  const value = fraction ? `${whole}.${fraction}` : whole;
  return negative && /[1-9]/.test(value) ? `-${value}` : value;
}

function isCurrency(code: string): boolean {
  if (!/^[A-Z]{3}$/.test(code)) return false;
  try { new Intl.NumberFormat('en', { style: 'currency', currency: code }); return true; } catch { return false; }
}

/** A ledger amount in the person's locale. ISO currencies use Intl's currency style with every
 *  significant fraction digit kept; other units (fixtures, credits) show the exact decimal and code. */
export function formatMoney(minor: string, unit: MoneyUnit, locale: string): string {
  if (!validUnit(unit)) throw new Error('invalid_unit');
  if (isCurrency(unit.code)) {
    const standard = new Intl.NumberFormat('en', { style: 'currency', currency: unit.code }).resolvedOptions().minimumFractionDigits ?? 2;
    const keep = Math.min(standard, unit.scale);
    const exact = majorDecimal(minor, unit.scale, keep);
    const places = exact.includes('.') ? exact.split('.')[1].length : 0;
    // Intl formats a decimal string exactly (no float conversion) in current engines.
    return new Intl.NumberFormat(locale, { style: 'currency', currency: unit.code,
      minimumFractionDigits: Math.min(keep, places), maximumFractionDigits: Math.max(places, Math.min(keep, places)) })
      .format(exact as unknown as number);
  }
  const exact = majorDecimal(minor, unit.scale);
  const [whole, fraction] = exact.replace(/^-/, '').split('.');
  const grouped = new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(BigInt(whole));
  const decimal = new Intl.NumberFormat(locale).formatToParts(1.1).find((p) => p.type === 'decimal')?.value || '.';
  return `${exact.startsWith('-') ? '-' : ''}${grouped}${fraction ? decimal + fraction : ''} ${unit.code}`;
}

export function formatTokens(count: number, locale: string): string {
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(count);
}

const count = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

/** Accept only a well-formed ledger cost; anything else renders nothing rather than a guess. */
export function validTurnCost(value: unknown): TurnCost | null {
  if (!value || typeof value !== 'object') return null;
  const v = value as TurnCost;
  const t = v.tokens as TurnTokens | undefined;
  if (typeof v.charged_minor !== 'string' || !MINOR.test(v.charged_minor)
    || typeof v.held_minor !== 'string' || !MINOR.test(v.held_minor)
    || !validUnit({ code: v.currency, scale: v.scale })
    || !['settled', 'pending', 'held'].includes(v.state)
    || !t || !count(t.input_cached) || !count(t.input_uncached) || !count(t.output) || !count(t.reasoning) || t.reasoning > t.output
    || !(v.model === null || (typeof v.model === 'string' && !!v.model))
    || !(v.band === null || typeof v.band === 'string')) return null;
  return v;
}

export const costUnit = (cost: TurnCost): MoneyUnit => ({ code: cost.currency, scale: cost.scale });

/** The short, always-visible line beside an answer. */
export function costSummary(cost: TurnCost): { key: ChatText; amount?: string } {
  if (cost.state === 'pending') return { key: 'costPending' };
  if (cost.state === 'held') return { key: 'costHeld', amount: cost.held_minor };
  if (cost.charged_minor === '0') return { key: 'costFree' };
  return { key: 'costSettled', amount: cost.charged_minor };
}

export function bandText(band: string | null): ChatText | null {
  if (band === 'peak') return 'bandPeak';
  if (band === 'off_peak' || band === 'off-peak' || band === 'offpeak') return 'bandOffPeak';
  if (band === 'mixed') return 'bandMixed';
  if (band === 'flat') return 'bandFlat';
  return null;
}

function gcd(a: bigint, b: bigint): bigint {
  while (b) [a, b] = [b, a % b];
  return a;
}

/** A rational per-batch rate as an exact amount per `tokens` tokens, in minor units of the
 *  smallest scale that represents it exactly; null when the decimal does not terminate. */
export function rateAmount(rate: { numerator: string; denominator: string }, perTokens: number, scale: number,
                           tokens = 1_000_000n): { minor: string; scale: number } | null {
  if (!/^(0|[1-9][0-9]{0,40})$/.test(rate?.numerator ?? '') || !/^[1-9][0-9]{0,40}$/.test(rate?.denominator ?? '')
    || !Number.isSafeInteger(perTokens) || perTokens <= 0 || !Number.isInteger(scale) || scale < 0 || scale > 18) return null;
  let numerator = BigInt(rate.numerator) * tokens;
  let denominator = BigInt(rate.denominator) * BigInt(perTokens) * 10n ** BigInt(scale);
  const divisor = gcd(numerator, denominator) || 1n;
  numerator /= divisor; denominator /= divisor;
  let rest = denominator, twos = 0, fives = 0;
  while (rest % 2n === 0n) { rest /= 2n; twos++; }
  while (rest % 5n === 0n) { rest /= 5n; fives++; }
  const places = Math.max(twos, fives);
  if (rest !== 1n || places > 18) return null;
  return { minor: (numerator * (10n ** BigInt(places) / denominator)).toString(), scale: places };
}

/** A price per million tokens in the person's locale, exactly; a fraction when it does not terminate. */
export function formatPerMillion(pricing: { rates: Record<'input_uncached' | 'input_cached' | 'output', { numerator: string; denominator: string }>;
  per_tokens: number; unit: MoneyUnit }, key: 'input_uncached' | 'input_cached' | 'output', locale: string, fallback: string): string {
  const exact = rateAmount(pricing.rates[key], pricing.per_tokens, pricing.unit.scale);
  if (!exact) return fallback;
  // Widen to at least four places so a whole price still shows the currency's usual decimals
  // ("¥2.00", "¥2" for a currency without them); formatMoney trims back to those.
  const scale = Math.max(exact.scale, 4);
  return formatMoney((BigInt(exact.minor) * 10n ** BigInt(scale - exact.scale)).toString(), { code: pricing.unit.code, scale }, locale);
}
