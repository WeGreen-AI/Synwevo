/** What the wallet, top-up and usage pages read from the server, and the pure decisions they
 *  make about it: payer selection, the period being looked at, request addresses, money text,
 *  chart geometry and the top-up states. No request is made here and no figure is invented:
 *  every number on the pages is one of the server's, re-expressed. */

import { paymentMethods, type PaymentMethod } from '../../lib/paymentMethods';

export type PayerKind = 'user' | 'organization';
export interface Payer { kind: PayerKind; id: string }
export interface PayerOption extends Payer { name: string | null }

export interface Balance { unit: string; scale: number; booked: string; used: string; held: string; available: string; frozen?: boolean }
export interface Overview { environment: 'test' | 'live'; payer: Payer; payer_user_id: string | null; balances: Balance[]; used_through_seq: string }
export type EntryKind = 'paid' | 'test_payment' | 'sale' | 'grant' | 'chargeback';
export interface Entry { seq: string; id: string; kind: EntryKind; unit: string; scale: number; delta: string; created_at: number }
export interface Statement { environment: 'test' | 'live'; payer: Payer; items: Entry[]; next_after_seq: string | null; next_before_seq?: string | null }

export type UsageGroup = 'day' | 'model' | 'conversation' | 'member';
export interface Tokens { input_cached: number; input_uncached: number; output: number; reasoning: number }
export interface Bucket { key: string; title?: string | null; unit: string; scale: number; calls: number; charged_minor: string; held_minor: string; tokens: Tokens }
export interface Period { from: string; to: string; timezone?: string }
export interface UsageSummary { environment: 'test' | 'live'; payer: Payer; group: UsageGroup; period: Period; buckets: Bucket[]; totals: Bucket[] }
export type CallState = 'settled' | 'pending' | 'held' | 'not_charged';
export interface CallRow {
  id: string; created_at: number; model: string | null; band: 'flat' | 'peak' | 'off_peak' | null; price_version: string;
  state: CallState; unit: string; scale: number; charged_minor: string; held_minor: string; tokens: Tokens | null;
  finish_reason: string | null; dispatched_at: number | null; completed_at: number | null; duration_s: number | null;
  conversation_id: string | null; conversation_title?: string | null; member_user_id?: string | null; member_name?: string | null;
}
export interface CallsPage { environment: 'test' | 'live'; payer: Payer; period: Period; items: CallRow[]; models: string[]; next_cursor: string | null }

export interface TopupPackage { id: string; amount_minor: number; label: string }
export interface TopupOptions {
  available: boolean; reason?: string | null; currency: string; packages: TopupPackage[];
  payment_methods: PaymentMethod[];
  /** Optional: false hides the free amount even when bounds are published. */
  custom_amount?: boolean; min_amount_minor: number | null; max_amount_minor: number | null;
}
export type TopupState = 'pending' | 'paid' | 'failed' | 'expired';
/** Creation outcome, separate from payment status: GET may say resolved for an unpaid session. */
export type TopupCreationStatus = 'verifying' | 'ready' | 'blocked' | 'resolved';
export interface TopupStatus { order_id: string; status: TopupState; creation_status?: TopupCreationStatus; amount_minor: number; currency?: string; credited_at?: number; created_at?: number; payer_kind?: PayerKind; payer_id?: string;
  /** Why the provider refused to create the checkout (see TOPUP_FAILURE_REASONS); only on an order that failed at creation. */
  failure_reason?: string | null }
export interface Receipt {
  order_id: string; status: 'paid'; amount_minor: number; currency: string; method?: string | null; provider?: string;
  provider_reference?: string | null; paid_at?: number | null; credited_at?: number | null; operator_name?: string | null;
  payer_kind?: PayerKind; payer_id?: string;
}

export const WALLET_API = '/api/auth/wallet';
export const PERSONAL: Payer = { kind: 'user', id: '' };

/** The address form of a payer: `user` for the person, `organization:<id>` for an organization. */
export function payerKey(payer: Payer): string {
  return payer.kind === 'organization' ? `organization:${payer.id}` : 'user';
}
export function parsePayer(value: string | null | undefined): Payer {
  const m = /^organization:([^\s/?#]{1,200})$/.exec(value || '');
  return m ? { kind: 'organization', id: m[1] } : PERSONAL;
}
/** Query parameters naming the payer. The person's own wallet needs none (the server's default). */
export function payerQuery(payer: Payer): Record<string, string> {
  return payer.kind === 'organization' ? { payer_kind: 'organization', payer_id: payer.id } : {};
}
export function withQuery(path: string, params: Record<string, string | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== null && value !== undefined && value !== '') query.set(key, value);
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

// ------------------------------------------------------------------ periods
const ISO = /^\d{4}-\d{2}-\d{2}$/;
export const MAX_SPAN_DAYS = 366;
export type Preset = '7d' | '30d' | '90d' | 'month' | 'last_month';
export const PRESETS: Preset[] = ['7d', '30d', '90d', 'month', 'last_month'];

function toDate(value: string): Date | null {
  if (!ISO.test(value)) return null;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isNaN(date.getTime()) || date.toISOString().slice(0, 10) !== value ? null : date;
}
function iso(date: Date): string { return date.toISOString().slice(0, 10); }
function addDays(value: string, days: number): string {
  const date = toDate(value)!;
  date.setUTCDate(date.getUTCDate() + days);
  return iso(date);
}
/** Today's calendar date where the person is (their account time zone), as YYYY-MM-DD. */
export function localToday(timezone: string, at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(at);
    const pick = (type: string) => parts.find(part => part.type === type)?.value || '';
    return `${pick('year')}-${pick('month')}-${pick('day')}`;
  } catch {
    return iso(at);
  }
}
export function presetPeriod(preset: Preset, today: string): Period {
  if (!toDate(today)) throw new RangeError('Invalid date');
  if (preset === 'month') return { from: today.slice(0, 8) + '01', to: today };
  if (preset === 'last_month') {
    const first = today.slice(0, 8) + '01';
    const end = addDays(first, -1);
    return { from: end.slice(0, 8) + '01', to: end };
  }
  const days = preset === '7d' ? 7 : preset === '90d' ? 90 : 30;
  return { from: addDays(today, 1 - days), to: today };
}
/** Days in an inclusive period, or null when it is not one the server accepts. */
export function periodDays(period: Period): number | null {
  const from = toDate(period.from), to = toDate(period.to);
  if (!from || !to || from > to) return null;
  const days = Math.round((to.getTime() - from.getTime()) / 86_400_000) + 1;
  return days <= MAX_SPAN_DAYS ? days : null;
}
export function daysOf(period: Period): string[] {
  const count = periodDays(period);
  if (count === null) return [];
  return Array.from({ length: count }, (_, index) => addDays(period.from, index));
}

// ------------------------------------------------------------------ addresses
export function usagePath(payer: Payer, period: Period, group: UsageGroup, timezone: string): string {
  return withQuery(`${WALLET_API}/usage`, { ...payerQuery(payer), group, from: period.from, to: period.to, tz: timezone });
}
export interface CallFilters { model?: string; state?: CallState | '' }
export function callsPath(payer: Payer, period: Period, filters: CallFilters, timezone: string, cursor?: string | null): string {
  return withQuery(`${WALLET_API}/calls`, { ...payerQuery(payer), from: period.from, to: period.to, tz: timezone,
    model: filters.model, state: filters.state, cursor: cursor || undefined, limit: '50' });
}
/** The CSV export of exactly what the table shows (the same payer, period and filters). */
export function csvPath(payer: Payer, period: Period, filters: CallFilters, timezone: string): string {
  return withQuery(`${WALLET_API}/calls.csv`, { ...payerQuery(payer), from: period.from, to: period.to, tz: timezone,
    model: filters.model, state: filters.state });
}

// ------------------------------------------------------------------ money
const UNITS = /^-?(0|[1-9]\d{0,30})$/;
/** Exact decimal text of integer smallest units ("-12.05"), never through a binary float. */
export function decimalText(units: string, scale: number): string {
  if (!UNITS.test(units) || !Number.isInteger(scale) || scale < 0 || scale > 18) throw new RangeError('Invalid wallet amount');
  const negative = units.startsWith('-');
  const digits = (negative ? units.slice(1) : units).padStart(scale + 1, '0');
  const whole = scale ? digits.slice(0, -scale) : digits;
  const fraction = scale ? digits.slice(-scale).replace(/0+$/, '') : '';
  return (negative && units !== '-0' ? '-' : '') + whole + (fraction ? '.' + fraction : '');
}
/** Exact decimal strings are formatted without a float on the way (Intl.NumberFormat v3 accepts
 *  a numeric string; the TypeScript library still declares number | bigint only). */
function exactFormat(format: Intl.NumberFormat, exact: string): string {
  return (format.format as unknown as (value: string) => string)(exact);
}
function isCurrency(code: string): boolean {
  if (!/^[A-Z]{3}$/.test(code)) return false;
  try { new Intl.NumberFormat('en', { style: 'currency', currency: code }); return true; } catch { return false; }
}
/** A wallet amount in the reader's locale. A currency unit (CNY) reads as money ("¥12.50", "CN¥0.0045")
 *  with every fractional digit the ledger holds; any other unit is a number followed by its code. */
export function moneyText(units: string, scale: number, unit: string, locale: string, signed = false): string {
  const exact = decimalText(units, scale);
  if (isCurrency(unit)) {
    const format = new Intl.NumberFormat(locale, { style: 'currency', currency: unit, minimumFractionDigits: Math.min(2, scale),
      maximumFractionDigits: Math.max(Math.min(2, scale), scale), signDisplay: signed ? 'exceptZero' : 'auto' });
    return exactFormat(format, exact);
  }
  const format = new Intl.NumberFormat(locale, { maximumFractionDigits: scale, signDisplay: signed ? 'exceptZero' : 'auto' });
  return `${exactFormat(format, exact)} ${unit}`;
}
/** Top-up amounts are minor units of the payment currency (fen for CNY). */
export function paymentText(amountMinor: number, currency: string, locale: string): string {
  if (!Number.isSafeInteger(amountMinor) || amountMinor < 0) throw new RangeError('Invalid payment amount');
  const digits = new Intl.NumberFormat('en', { style: 'currency', currency }).resolvedOptions().maximumFractionDigits ?? 2;
  return moneyText(String(amountMinor), digits, currency, locale);
}
/** A typed major-unit amount ("12.5") as minor units (1250), or null when it is not one exactly. */
export function minorFromText(value: string, digits: number): number | null {
  const m = /^(0|[1-9]\d{0,9})(?:\.(\d+))?$/.exec(value.trim());
  if (!m || (m[2] && m[2].length > digits)) return null;
  const minor = Number(m[1]) * 10 ** digits + Number((m[2] || '').padEnd(digits, '0') || '0');
  return Number.isSafeInteger(minor) ? minor : null;
}

// ------------------------------------------------------------------ chart
export interface Bar { key: string; label: string; value: string; ratio: number; calls: number }
/** Bars for one unit. By day every date of the period is drawn — a day the server did not list had
 *  no calls, so it is a real zero, not a gap; other groupings show what the server listed, in its
 *  order (most spent first), capped at `limit` with the remainder summed into one honest bar. */
export function chartBars(summary: Pick<UsageSummary, 'group' | 'period' | 'buckets'>, unit: string,
                          names: { other: string; untitled: string }, limit = 12): Bar[] {
  const buckets = summary.buckets.filter(bucket => bucket.unit === unit);
  let rows: { key: string; label: string; value: bigint; calls: number }[];
  if (summary.group === 'day') {
    const byDay = new Map(buckets.map(bucket => [bucket.key, bucket]));
    rows = daysOf(summary.period).map(day => ({ key: day, label: day.slice(5), value: BigInt(byDay.get(day)?.charged_minor || '0'),
      calls: byDay.get(day)?.calls || 0 }));
  } else {
    rows = buckets.map(bucket => ({ key: bucket.key, label: bucket.title || bucket.key || names.untitled,
      value: BigInt(bucket.charged_minor), calls: bucket.calls }));
    if (rows.length > limit) {
      const rest = rows.slice(limit - 1);
      rows = [...rows.slice(0, limit - 1), { key: '__other__', label: names.other, value: rest.reduce((sum, row) => sum + row.value, 0n),
        calls: rest.reduce((sum, row) => sum + row.calls, 0) }];
    }
  }
  const max = rows.reduce((top, row) => (row.value > top ? row.value : top), 0n);
  // Ratios in thousandths from integers: a bar's length never depends on float rounding of money.
  return rows.map(row => ({ key: row.key, label: row.label, value: row.value.toString(), calls: row.calls,
    ratio: max === 0n ? 0 : Number((row.value * 1000n) / max) / 1000 }));
}

// ------------------------------------------------------------------ wallet entries
/** Catalogue keys of the posted entry kinds. A kind this page does not know yet (a later ledger
 *  release) is named by its direction only, never shown as a raw code or hidden. */
export const ENTRY_KINDS: Record<string, string> = {
  paid: '充值到账', test_payment: '测试到账', sale: '已结算消费', grant: '赠送额度', chargeback: '支付争议扣回',
};
export function entryKindLabel(kind: string, delta: string): string {
  return ENTRY_KINDS[kind] || (delta.startsWith('-') ? '其他扣减' : '其他入账');
}

// ------------------------------------------------------------------ top-up
/** Why top-up is closed, or null when the server offers it. An explicit `available: false` gives
 *  its own reason; a top-up service that does not exist on this deployment (404) is the same
 *  honest "not yet available" — never an invented checkout. Other failures are errors to show. */
export function topupClosedReason(options: TopupOptions | null, failureStatus: number | null): string | null {
  if (failureStatus === 404) return 'not_configured';
  if (!options) return null;
  if (!options.available) return options.reason || 'not_configured';
  return paymentMethods(options.payment_methods).length ? null : 'not_configured';
}
/** Why the payment provider refused to create a checkout, by the server's stable `failure_reason`
 *  code. Each sentence says that nothing was charged: the order failed before any payment page
 *  existed. A code this page does not know yet gets no sentence of its own (the caller falls back
 *  to the plain failed state) rather than a guess. */
export const TOPUP_FAILURE_REASONS: Record<string, string> = {
  payment_method_not_enabled: '支付服务拒绝创建付款页面：所选支付方式尚未在支付服务商处开通。没有扣款。请稍后再试或联系平台管理员。',
  provider_configuration: '支付服务拒绝创建付款页面：平台的支付服务配置有误。没有扣款。请稍后再试或联系平台管理员。',
  provider_rejected: '支付服务拒绝创建付款页面。没有扣款。请稍后再试或联系平台管理员。',
};
export function topupFailureText(reason: unknown): string | null {
  return typeof reason === 'string' && Object.hasOwn(TOPUP_FAILURE_REASONS, reason) ? TOPUP_FAILURE_REASONS[reason] : null;
}
export type TopupView = TopupState | 'cancelled' | 'slow' | 'verifying';
/** A terminal payment state wins. An unknown creation outcome stays distinct even after the
 *  polling deadline or a cancel query parameter; neither proves that a checkout ever opened. */
export function topupView(status: TopupState | undefined, slow: boolean, cancelled: boolean,
                          creationStatus?: TopupCreationStatus): TopupView {
  if (status && status !== 'pending') return status;
  if (creationStatus === 'verifying') return 'verifying';
  return slow ? 'slow' : cancelled ? 'cancelled' : 'pending';
}
/** Whether the status page should keep asking: only while the order is still pending. */
export function topupFinal(status: TopupState | undefined): boolean {
  return status === 'paid' || status === 'failed' || status === 'expired';
}
/** Polling interval: quick at first (the webhook usually lands within seconds), then slower. */
export function pollDelay(attempt: number): number {
  return Math.min(10_000, 1500 * 2 ** Math.min(Math.max(attempt, 0), 3));
}
/** Stop asking after this long; the page then offers a manual refresh instead of spinning forever. */
export const POLL_LIMIT_MS = 3 * 60_000;
/** Only a secure, credential-free address may be followed to the hosted checkout. */
export function safeCheckout(url: unknown): string | null {
  if (typeof url !== 'string') return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : null;
  } catch { return null; }
}
/** A plain-text receipt for saving: the server's fields, labelled; nothing computed here. */
export function receiptText(title: string, lines: { label: string; value: string }[]): string {
  return [title, '', ...lines.filter(line => line.value).map(line => `${line.label}: ${line.value}`), ''].join('\r\n');
}
