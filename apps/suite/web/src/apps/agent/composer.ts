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
  /** The sale price id and route version in force (addendum AD-1): what a send now would be bound to. */
  price_id?: string | null; route_version?: string | null;
  /** A stable digest of what the person pays (addendum-2 S2: price, rates, hold, model and route);
   *  when present it is what the page repeats as `seen_route_version`. */
  price_key?: string | null;
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
/** How a changed rate or amount is written, given both values at once: a pair that would read alike
 *  after rounding (two holds a micro-yuan apart) can then be shown exactly instead. */
export interface PriceDisplay {
  rate?: (before: Pricing, after: Pricing, key: RateKey) => [string, string];
  amount?: (before: string, after: string, unit: Pricing['unit']) => [string, string];
}
const SEEN = /^[\x21-\x7e]{1,200}$/;
/** The price the composer showed, repeated on every paid send (addendum AD-1: `seen_price_id`,
 *  `seen_route_version`), so the server refuses rather than runs at a price the page did not show
 *  (409 `billing_price_changed`). The second field carries the run's `price_key` when the server
 *  reports one (addendum-2 S2: what the person pays, so a plain route renewal refuses nothing),
 *  else its route version — exactly the value the server gave. Both or neither: `{}` means no price
 *  is shown, and the page then holds the send (W3) rather than send it unchecked. */
export function seenPrice(status: Pick<BillingStatus, 'run'> | null | undefined): { seen_price_id: string; seen_route_version: string } | Record<string, never> {
  const run = status?.run;
  const price = run?.price_id, route = run?.price_key != null ? run.price_key : run?.route_version;
  return typeof price === 'string' && SEEN.test(price) && typeof route === 'string' && SEEN.test(route)
    ? { seen_price_id: price, seen_route_version: route } : {};
}

/** What the composer says beside Send when billing applies: the most this answer can cost, as the
 *  server reports it with the price a send repeats; "reading" while the status loads, or, honestly,
 *  that it cannot be shown (addendum-2 W3: a paid send is held in both). When the run bound is not
 *  usable (W8) it says why instead of a maximum no send can run at: the person's cap is below one
 *  call's hold (with that hold) or cannot be used — both a matter for Agent settings — or else the
 *  service cannot take a send right now (the route's validity is nearly over). */
export type MaximumShown = { key: 'turnMaximum' | 'turnMaximumCall' | 'turnMaximumCapLow'; amount: string; unit: MoneyUnit }
  | { key: 'turnMaximumLoading' | 'turnMaximumUnavailable' | 'turnMaximumCapUnusable' | 'turnMaximumPaused' };
export function composerMaximum(status: BillingStatus | null | undefined): MaximumShown {
  if (status === undefined) return { key: 'turnMaximumLoading' };
  const shown = turnMaximum(status);
  if (!shown || shown.key !== 'turnMaximum' || !('seen_price_id' in seenPrice(status))) return { key: 'turnMaximumUnavailable' };
  const run = status!.run!;   // turnMaximum gave the run's maximum, so both are there
  if (run.usable) return shown;
  if (run.source === 'personal_cap') {
    const cap = big(run.cap), hold = big(run.per_call_hold), max = big(run.maximum), all = big(run.default);
    // quotes.run_bound: an honoured cap binds min(cap, default) and is at least one call's hold.
    const honoured = cap !== null && hold !== null && max !== null && all !== null && cap >= hold && max === (cap < all ? cap : all);
    if (!honoured) return cap !== null && hold !== null && cap < hold
      ? { key: 'turnMaximumCapLow', amount: run.per_call_hold, unit: shown.unit } : { key: 'turnMaximumCapUnusable' };
  }
  return { key: 'turnMaximumPaused' };
}

/** What changed between the pricing of the person's last answer and the pricing on offer now.
 *  Equality is decided on exact values; `display` only chooses how a changed rate or amount is
 *  written — and a listed change never reads the same on both sides: should the display round two
 *  different values alike, the exact values it was decided on are shown instead. */
export function priceChanges(before: Pricing, after: Pricing, display: PriceDisplay = {}): PriceChange[] {
  const out: PriceChange[] = [];
  const add = (field: ChatText, a: string, b: string, shown?: () => [string, string]) => {
    if (a === b) return;
    const [x, y] = shown ? shown() : [a, b];
    out.push(x === y ? { field, before: a, after: b } : { field, before: x, after: y });
  };
  add('changedModel', before.model, after.model);
  add('changedRoute', before.confirmed_route, after.confirmed_route);
  add('changedVersion', before.route_version, after.route_version);
  add('changedPrice', before.price_id, after.price_id);
  const sameUnit = before.unit.code === after.unit.code && before.unit.scale === after.unit.scale;
  add('changedUnit', `${before.unit.code}/${before.unit.scale}`, `${after.unit.code}/${after.unit.scale}`);
  for (const [key, field] of [['input_uncached', 'changedUncached'], ['input_cached', 'changedCached'], ['output', 'changedOutput']] as const) {
    add(field, perMillion(before, key), perMillion(after, key), display.rate && (() => display.rate!(before, after, key)));
  }
  if (sameUnit) add('changedHold', formatAmount(before.per_call_hold, before.unit), formatAmount(after.per_call_hold, after.unit),
    display.amount && (() => display.amount!(before.per_call_hold, after.per_call_hold, before.unit)));
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
