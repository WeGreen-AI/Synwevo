/** The money facts beside the composer: balance, the tightest limit, the most this turn can cost,
 *  and where to top up. Pure functions over server values; nothing is estimated here. */
import { formatAmount, formatRate, type Offer, type Pricing } from './budget';
import type { ChatText } from './chat.messages';

export interface BillingStatus {
  available: boolean;
  reason?: 'not_configured' | 'unavailable' | null;
  environment?: 'test' | 'live' | null;
  model?: string | null;
  unit?: { code: string; scale: number } | null;
  per_call_hold?: string | null;
  balance?: { booked: string; held: string; available: string } | null;
  limit?: { configured: boolean; enabled: boolean; usage_state: string | null; remaining: string | null;
    period: 'daily' | 'weekly' | 'monthly' | null } | null;
}

const MINOR = /^-?(0|[1-9][0-9]{0,30})$/;
const big = (value: string | null | undefined) => (typeof value === 'string' && MINOR.test(value) ? BigInt(value) : null);

/** "empty": cannot cover even one call's hold; "low": fewer than five calls' holds; else "ok".
 *  Five holds is a display threshold only; the ledger checks the real hold on every call. */
export type Funds = 'empty' | 'low' | 'ok' | 'unknown';
export function fundsState(status: BillingStatus | null | undefined): Funds {
  const available = big(status?.balance?.available), hold = big(status?.per_call_hold);
  if (available === null || hold === null) return 'unknown';
  if (available <= 0n || available < hold) return 'empty';
  if (available < hold * 5n) return 'low';
  return 'ok';
}

/** The tightest remaining UTC limit as a translatable label, or the reason none applies. */
export function limitLine(status: BillingStatus | null | undefined): { key: ChatText; amount?: string; period?: ChatText } | null {
  const limit = status?.limit;
  if (!limit) return null;
  if (!limit.configured) return { key: 'limitMissing' };
  if (!limit.enabled) return { key: 'limitPaused' };
  if (limit.remaining == null || !limit.period) return null;
  const period: ChatText = limit.period === 'daily' ? 'periodDaily' : limit.period === 'weekly' ? 'periodWeekly' : 'periodMonthly';
  return { key: 'limitRemaining', amount: limit.remaining, period };
}

/** The most this turn can cost before it is sent: the active consent's per-run budget, else the
 *  offer's per-call hold (the quote dialog then fixes the run's total before anything is sent). */
export function turnMaximum(consent: { budget: string; unit: { code: string; scale: number } } | null | undefined,
                            offer: { per_call_hold: string; unit: { code: string; scale: number } } | null | undefined):
  { key: 'turnMaximum' | 'turnMaximumCall'; amount: string; unit: { code: string; scale: number } } | null {
  if (consent && big(consent.budget) !== null) return { key: 'turnMaximum', amount: consent.budget, unit: consent.unit };
  if (offer && big(offer.per_call_hold) !== null) return { key: 'turnMaximumCall', amount: offer.per_call_hold, unit: offer.unit };
  return null;
}

/** A page of the account service, from the server-given account URL: "" is the account page
 *  itself, "wallet" the wallet page (where top-up lives). Only http(s) URLs; anything else falls
 *  back to this origin's /account. */
export function accountHref(accountUrl: string | undefined | null, appId?: string | null, origin = globalThis.location?.origin ?? 'http://localhost',
                            page: '' | 'wallet' = ''): string {
  let url: URL;
  try { url = new URL(accountUrl || '/account', origin); } catch { url = new URL('/account', origin); }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') url = new URL('/account', origin);
  const base = url.pathname.replace(/\/+$/, '');
  url.pathname = (base.endsWith('/account') ? base : '/account') + (page ? `/${page}` : '');
  url.search = '';
  url.hash = '';
  if (appId) url.searchParams.set('from', appId);
  return url.origin === origin ? url.pathname + url.search : url.href;
}

/** A page of the public site: the versioned terms of service, AI use rules and pricing (and the
 *  privacy notice) that the portal serves — zh-Hans unprefixed, the other languages under `/en`
 *  and `/zh-Hant` (apps/portal/web/src/publicRoutes.ts `publicPath`). The site's origin is the
 *  server-controlled `site_url` of the auth state (production: the public site host), never a
 *  value from the page; without one (a single-host install) the address stays on this host. */
export type PublicDocument = 'terms' | 'ai-use' | 'pricing' | 'privacy';
export function publicHref(siteUrl: string | undefined | null, page: PublicDocument, locale: string,
                           origin: string | undefined = globalThis.location?.origin): string {
  const path = `${locale === 'en' || locale === 'zh-Hant' ? `/${locale}` : ''}/${page}`;
  if (!siteUrl) return path;
  let site: URL;
  try { site = new URL(siteUrl); } catch { return path; }
  if (site.protocol !== 'https:' && site.protocol !== 'http:') return path;
  return site.origin === origin ? path : site.origin + path;
}

/** The account service's wallet page (top-up lives there). */
export function walletHref(accountUrl: string | undefined | null, appId?: string | null, origin = globalThis.location?.origin ?? 'http://localhost'): string {
  return accountHref(accountUrl, appId, origin, 'wallet');
}

/** The exact price per million tokens of one rate, in the offer's unit (a reduced fraction when
 *  the decimal does not terminate). Comparing per million tokens means a different batch size
 *  is never mistaken for a price change. */
export function perMillion(pricing: Pick<Pricing, 'rates' | 'per_tokens' | 'unit'>, key: 'input_uncached' | 'input_cached' | 'output'): string {
  const rate = pricing.rates[key];
  return formatRate({ numerator: (1_000_000n * BigInt(rate.numerator)).toString(),
    denominator: (BigInt(rate.denominator) * BigInt(pricing.per_tokens)).toString() }, pricing.unit);
}

export interface PriceChange { field: ChatText; before: string; after: string }
type RateKey = 'input_uncached' | 'input_cached' | 'output';
export interface PriceDisplay { rate?: (pricing: Pricing, key: RateKey) => string; amount?: (minor: string, unit: Pricing['unit']) => string }
/** What changed between the terms a person last confirmed and the terms on offer now. Equality is
 *  decided on exact values; `display` only chooses how a changed rate or amount is written. */
export function priceChanges(before: Pricing, after: Pricing, display: PriceDisplay = {}): PriceChange[] {
  const out: PriceChange[] = [];
  const add = (field: ChatText, a: string, b: string, shown?: () => [string, string]) => {
    if (a !== b) { const [x, y] = shown ? shown() : [a, b]; out.push({ field, before: x, after: y }); }
  };
  add('changedModel', before.model, after.model);
  add('changedRoute', before.confirmed_route, after.confirmed_route);
  add('changedVersion', before.route_version, after.route_version);
  add('changedPrice', before.price_id, after.price_id);
  const sameUnit = before.unit.code === after.unit.code && before.unit.scale === after.unit.scale;
  add('changedUnit', `${before.unit.code}/${before.unit.scale}`, `${after.unit.code}/${after.unit.scale}`);
  for (const [key, field] of [['input_uncached', 'changedUncached'], ['input_cached', 'changedCached'], ['output', 'changedOutput']] as const) {
    add(field, perMillion(before, key), perMillion(after, key),
      display.rate && (() => [display.rate!(before, key), display.rate!(after, key)]));
  }
  if (sameUnit) add('changedHold', formatAmount(before.per_call_hold, before.unit), formatAmount(after.per_call_hold, after.unit),
    display.amount && (() => [display.amount!(before.per_call_hold, before.unit), display.amount!(after.per_call_hold, after.unit)]));
  return out;
}

/** Same fixed terms: a quote is accepted in one step only when it matches what was shown. */
export function samePricing(a: Pricing, b: Pricing): boolean {
  return priceChanges(a, b).length === 0 && a.environment === b.environment && a.terms_ref === b.terms_ref
    && a.per_tokens === b.per_tokens;
}

/** The limits a re-confirmation in one step reuses: the previous per-run budget, call count and
 *  time limit, but only when they still fit the new offer; otherwise null (fill them in again). */
export function renewalParams(previous: { budget: string; max_calls: number; run_seconds: number },
                              offer: Pick<Offer, 'per_call_hold' | 'max_calls_limit' | 'run_seconds_limit' | 'run_seconds_min' | 'unit'>,
                              previousUnit: { code: string; scale: number }): { budget: string; max_calls: number; run_seconds: number } | null {
  const budget = big(previous.budget), hold = big(offer.per_call_hold);
  if (budget === null || hold === null || budget <= 0n || budget < hold
    || previousUnit.code !== offer.unit.code || previousUnit.scale !== offer.unit.scale
    || !Number.isSafeInteger(previous.max_calls) || previous.max_calls < 1 || previous.max_calls > offer.max_calls_limit
    || !Number.isSafeInteger(previous.run_seconds) || previous.run_seconds < (offer.run_seconds_min ?? 1)
    || previous.run_seconds > offer.run_seconds_limit) return null;
  return { budget: previous.budget, max_calls: previous.max_calls, run_seconds: previous.run_seconds };
}

/** Enter sends; Shift+Enter is a new line; an input method's composition (Chinese, Japanese,
 *  Korean) is never interrupted: its confirming Enter arrives with isComposing, or as keyCode 229
 *  in Safari, whose compositionend fires before that keydown. */
export function shouldSubmit(event: { key: string; shiftKey: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
  isComposing?: boolean; keyCode?: number }, composing: boolean): boolean {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey) return false;
  return !composing && !event.isComposing && event.keyCode !== 229;
}
