/** Money and token display for the chat. Ledger amounts are integer smallest units, carried as
 *  decimal strings; they never pass through a binary float. The formatting itself is the shared
 *  one (`@synwevo/ui/money`): money at the currency's own precision, rounded from the integer,
 *  bounds rounded up, a charge below one fen said as such. */
import { formatMoney, formatPerMillion as sharedPerMillion, majorDecimal, validUnit, type MoneyUnit } from '@synwevo/ui/money';
import type { ChatText } from './chat.messages';

export { formatMoney, majorDecimal, type MoneyUnit };
export interface TurnTokens { input_cached: number; input_uncached: number; output: number; reasoning: number }
/** The shared per-turn cost contract (synwevo_suite.agent.turn_cost). */
export interface TurnCost {
  charged_minor: string; currency: string; scale: number; tokens: TurnTokens;
  model: string | null; band: string | null; state: 'settled' | 'pending' | 'held'; held_minor: string;
  /** Who the ledger charged for this run (absent from servers before organization payment). */
  payer?: { kind: 'user' | 'organization'; id: string } | null;
}

const MINOR = /^(0|[1-9][0-9]{0,30})$/;

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
    || !(v.band === null || typeof v.band === 'string')
    || !(v.payer == null || (typeof v.payer === 'object' && (v.payer.kind === 'user' || v.payer.kind === 'organization')
      && typeof v.payer.id === 'string' && /^[A-Za-z0-9_.:-]{1,200}$/.test(v.payer.id)))) return null;
  return v;
}

export const costUnit = (cost: TurnCost): MoneyUnit => ({ code: cost.currency, scale: cost.scale });

/** The short, always-visible line beside an answer. "No charge" is the ledger's zero only: a charge
 *  below one fen is shown as such by the formatter, never as nothing. */
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

/** A price per million tokens in the person's locale: the published decimal, exactly, with at
 *  least the currency's digits ("¥2.00", "¥2.2468"); `fallback` only for a rate that is not one. */
export function formatPerMillion(pricing: { rates: Record<'input_uncached' | 'input_cached' | 'output', { numerator: string; denominator: string }>;
  per_tokens: number; unit: MoneyUnit }, key: 'input_uncached' | 'input_cached' | 'output', locale: string, fallback: string): string {
  try { return sharedPerMillion(pricing.rates[key], pricing.per_tokens, pricing.unit, locale); } catch { return fallback; }
}
