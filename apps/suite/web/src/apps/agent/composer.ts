/** The money facts beside the composer: balance, the tightest limit, the most this answer can
 *  cost, and where to top up. Pure functions over server values; nothing is estimated here. */
import { formatAmount, formatRate, type Pricing } from './budget';
import type { ChatText } from './chat.messages';

export interface MoneyUnit { code: string; scale: number }
/** The per-answer run bound the server will issue at the next send (`agent.billing.status.run`,
 *  design §3): every `<units>` is a decimal string of integer sale units. `maximum` is the
 *  default (one call's hold × `max_calls`) or the person's lower cap; `usable` says whether a
 *  send can be admitted with it (false: the cap is below one call's hold). */
export interface RunBound {
  maximum: string; default: string; cap: string | null; source: 'default' | 'personal_cap';
  per_call_hold: string; max_calls: number; run_seconds: number; usable: boolean;
}
/** The pricing the person's last answer ran under, when it differs from the offer now
 *  (`agent.billing.status.price_notice`, design D-7): information, never a gate. */
export interface PriceNoticeFacts { previous: Pricing & { off_peak_rates?: unknown }; previous_run_at: number }
export interface BillingStatus {
  available: boolean;
  reason?: 'not_configured' | 'unavailable' | null;
  environment?: 'test' | 'live' | null;
  model?: string | null;
  unit?: MoneyUnit | null;
  per_call_hold?: string | null;
  balance?: { booked: string; held: string; available: string } | null;
  limit?: { configured: boolean; enabled: boolean; usage_state: string | null; remaining: string | null;
    period: 'daily' | 'weekly' | 'monthly' | null } | null;
  run?: RunBound | null;
  price_notice?: PriceNoticeFacts | null;
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

/** The most the next answer can cost, before sending: the run bound the server reports
 *  (`status.run.maximum`, in the status unit). A server without it (older) still reports the
 *  per-call hold, which is then shown as such; nothing is multiplied in the page. */
export function turnMaximum(status: Pick<BillingStatus, 'run' | 'unit' | 'per_call_hold'> | null | undefined):
  { key: 'turnMaximum' | 'turnMaximumCall'; amount: string; unit: MoneyUnit } | null {
  const unit = status?.unit;
  if (!unit) return null;
  const run = status?.run;
  if (run && big(run.maximum) !== null) return { key: 'turnMaximum', amount: run.maximum, unit };
  if (big(status?.per_call_hold) !== null) return { key: 'turnMaximumCall', amount: status!.per_call_hold!, unit };
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
/** What changed between the pricing of the person's last answer and the pricing on offer now.
 *  Equality is decided on exact values; `display` only chooses how a changed rate or amount is
 *  written. */
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

/** Enter sends; Shift+Enter is a new line; an input method's composition (Chinese, Japanese,
 *  Korean) is never interrupted: its confirming Enter arrives with isComposing, or as keyCode 229
 *  in Safari, whose compositionend fires before that keydown. */
export function shouldSubmit(event: { key: string; shiftKey: boolean; altKey?: boolean; ctrlKey?: boolean; metaKey?: boolean;
  isComposing?: boolean; keyCode?: number }, composing: boolean): boolean {
  if (event.key !== 'Enter' || event.shiftKey || event.altKey) return false;
  return !composing && !event.isComposing && event.keyCode !== 229;
}
