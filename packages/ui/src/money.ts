/** Money display shared by every shell. A ledger amount is an integer count of a unit's smallest
 *  units, carried as a decimal string (`units` of a `{code, scale}` unit: each is 10^-scale major
 *  units); a payment amount is in minor units of a payment currency (fen for CNY). Nothing here
 *  passes through a binary float: rounding is BigInt arithmetic on the integer, and Intl is then
 *  handed an exact decimal string with exactly the digits to show.
 *
 *  The rule (docs/personal-wallet.md): an amount a person reads is money at the currency's own
 *  precision — two decimals for CNY, with the locale's grouping — rounded from the exact units.
 *  The ledger's finer digits ("¥5.11182" for 5,111,820 micro-yuan) are a storage precision, not
 *  a display one: they read as "¥5.11". A bound said as "at most" or "no lower than" rounds up so
 *  the sentence stays true. A non-zero amount below one fen reads "不到 ¥0.01" rather than a
 *  "¥0.00" that would deny the charge. `precision: 'exact'` keeps every digit the ledger holds,
 *  for configured values where the typed figure itself is the fact. A unit price per million
 *  tokens is the exact decimal of the published rate (at most the digits it was published with). */

/** This module is loaded by plain Node in the package tests (no bundler), so it imports nothing:
 *  its two phrases are a local three-locale table rather than a catalogue registration. */
type MoneyLocale = 'zh-Hans' | 'zh-Hant' | 'en';
const PHRASES: Record<'under' | 'within', Record<MoneyLocale, (amount: string) => string>> = {
  /** A positive amount below the smallest one shown. */
  under: { 'zh-Hans': (amount) => `不到 ${amount}`, 'zh-Hant': (amount) => `不到 ${amount}`, en: (amount) => `under ${amount}` },
  /** A negative amount (a deduction) whose size is below the smallest one shown. */
  within: { 'zh-Hans': (amount) => `${amount} 以内`, 'zh-Hant': (amount) => `${amount} 以內`, en: (amount) => `within ${amount}` },
};
/** The same resolution as i18n-core's normalizeLocale, for the locale tags the shells pass. */
function moneyLocale(locale: string): MoneyLocale {
  const tag = String(locale).toLowerCase().replace(/_/g, '-');
  if (/^en(?:-|$)/.test(tag)) return 'en';
  if (tag.includes('-hant') || /-(tw|hk|mo)(?:-|$)/.test(tag)) return 'zh-Hant';
  return 'zh-Hans';
}

export interface MoneyUnit { code: string; scale: number }
/** A rational price in smallest units per some number of tokens. */
export interface Rate { numerator: string; denominator: string }
export type MoneyPrecision = 'standard' | 'exact';
export type MoneyRounding = 'nearest' | 'up';
export interface MoneyOptions {
  /** A plus sign on a positive amount (statement deltas); negative amounts always carry theirs. */
  signed?: boolean;
  /** 'standard' (default): the currency's own decimals; 'exact': every digit the ledger holds. */
  precision?: MoneyPrecision;
  /** 'nearest' (default): half up, away from zero; 'up': away from zero, for an "at most" bound. */
  rounding?: MoneyRounding;
}

const UNITS = /^-?(0|[1-9][0-9]{0,63})$/;
const RATE_PART = /^(0|[1-9][0-9]{0,63})$/;

export function validUnit(unit: MoneyUnit | null | undefined): unit is MoneyUnit {
  return !!unit && typeof unit.code === 'string' && !!unit.code.trim()
    && Number.isInteger(unit.scale) && unit.scale >= 0 && unit.scale <= 18;
}
function checkUnits(units: unknown): asserts units is string {
  if (typeof units !== 'string' || !UNITS.test(units)) throw new RangeError('Invalid wallet amount');
}
function checkScale(scale: number): void {
  if (!Number.isInteger(scale) || scale < 0 || scale > 18) throw new RangeError('Invalid wallet unit');
}

const DIGITS = new Map<string, number | null>();
/** The fraction digits an ISO 4217 currency is quoted in (CNY 2, JPY 0); null for any other unit code. */
export function currencyDigits(code: string): number | null {
  if (!/^[A-Z]{3}$/.test(code)) return null;
  let digits = DIGITS.get(code);
  if (digits === undefined) {
    try { digits = new Intl.NumberFormat('en', { style: 'currency', currency: code }).resolvedOptions().maximumFractionDigits ?? 2; }
    catch { digits = null; }
    DIGITS.set(code, digits);
  }
  return digits;
}

/** Exact major-unit decimal ("12.0034") of signed smallest units, trailing zeros trimmed to at least `keep` places. */
export function majorDecimal(units: string, scale: number, keep = 0): string {
  checkUnits(units); checkScale(scale);
  const negative = units.startsWith('-');
  const digits = (negative ? units.slice(1) : units).padStart(scale + 1, '0');
  const whole = scale ? digits.slice(0, -scale) : digits;
  let fraction = scale ? digits.slice(-scale) : '';
  while (fraction.length > keep && fraction.endsWith('0')) fraction = fraction.slice(0, -1);
  const value = fraction ? `${whole}.${fraction}` : whole;
  return negative && /[1-9]/.test(value) ? `-${value}` : value;
}

/** The same amount as an integer count of 10^-digits major units: rescaled exactly when `digits`
 *  is at least `scale`, otherwise rounded on the integer (half up or up, both away from zero). */
export function roundedUnits(units: string, scale: number, digits: number, rounding: MoneyRounding = 'nearest'): string {
  checkUnits(units); checkScale(scale); checkScale(digits);
  const negative = units.startsWith('-');
  let value = BigInt(negative ? units.slice(1) : units);
  if (digits >= scale) value *= 10n ** BigInt(digits - scale);
  else {
    const divisor = 10n ** BigInt(scale - digits);
    const rest = value % divisor;
    value /= divisor;
    if (rest && (rounding === 'up' || rest * 2n >= divisor)) value += 1n;
  }
  return (negative && value ? '-' : '') + value.toString();
}

const places = (decimal: string): number => (decimal.includes('.') ? decimal.split('.')[1].length : 0);
/** Intl.NumberFormat v3 formats a decimal string exactly, without a float on the way; TypeScript's
 *  library still declares number | bigint only. The string always carries exactly the digits to show. */
function intlText(exact: string, locale: string, options: Intl.NumberFormatOptions): string {
  const shown = places(exact);
  return new Intl.NumberFormat(locale, { ...options, minimumFractionDigits: shown, maximumFractionDigits: shown })
    .format(exact as unknown as number);
}
function moneyText(exact: string, unit: MoneyUnit, locale: string, signed: boolean): string {
  const signDisplay = signed ? 'exceptZero' : 'auto';
  return currencyDigits(unit.code) !== null
    ? intlText(exact, locale, { style: 'currency', currency: unit.code, signDisplay })
    : `${intlText(exact, locale, { signDisplay })} ${unit.code}`;
}
/** The decimals an amount of `unit` is read at: the currency's own, else at most two. */
const shownDigits = (unit: MoneyUnit): number => currencyDigits(unit.code) ?? Math.min(2, unit.scale);

/** A ledger amount in the reader's locale: "¥5.11" / "CN¥5.11" for CNY, "1,234.57 FIXTURE" for a
 *  unit that is not a currency. See the module note for the precision, rounding and sub-fen rules. */
export function formatMoney(units: string, unit: MoneyUnit, locale: string, options: MoneyOptions = {}): string {
  checkUnits(units);
  if (!validUnit(unit)) throw new RangeError('Invalid wallet unit');
  const { signed = false, precision = 'standard', rounding = 'nearest' } = options;
  const digits = shownDigits(unit);
  if (precision === 'exact') return moneyText(majorDecimal(units, unit.scale, Math.min(digits, unit.scale)), unit, locale, signed);
  const rounded = roundedUnits(units, unit.scale, digits, rounding);
  if (rounded === '0' && /[1-9]/.test(units)) {
    // Below the smallest amount shown, yet not nothing: say so instead of a "¥0.00" that denies it.
    const negative = units.startsWith('-');
    const smallest = moneyText(majorDecimal(negative ? '-1' : '1', digits, digits), unit, locale, signed);
    return PHRASES[negative ? 'within' : 'under'][moneyLocale(locale)](smallest);
  }
  return moneyText(majorDecimal(rounded, digits, digits), unit, locale, signed);
}

/** A payment amount in minor units of its currency (fen for CNY): 1000 → "¥10.00". */
export function formatMinor(minor: number | string, currency: string, locale: string, options: MoneyOptions = {}): string {
  const digits = currencyDigits(currency);
  if (digits === null) throw new RangeError('Invalid payment currency');
  if (typeof minor === 'number' && !Number.isSafeInteger(minor)) throw new RangeError('Invalid wallet amount');
  return formatMoney(String(minor), { code: currency, scale: digits }, locale, options);
}

function gcd(a: bigint, b: bigint): bigint {
  while (b) [a, b] = [b, a % b];
  return a;
}

/** The price per million tokens of a rate quoted in smallest units per `perTokens` tokens, in the
 *  reader's locale: exact, with at least the currency's digits and at most the digits the published
 *  decimal has ("¥2.00", "¥2.2468"), never a trailing float tail. A rate whose decimal does not
 *  terminate (a batch size that is not a divisor of a power of ten) is rounded half up to the
 *  unit's own decimals; equality between prices is decided elsewhere on the exact rationals. */
export function formatPerMillion(rate: Rate, perTokens: number, unit: MoneyUnit, locale: string): string {
  if (!rate || typeof rate !== 'object' || !RATE_PART.test(rate.numerator ?? '') || !RATE_PART.test(rate.denominator ?? '')
    || rate.denominator === '0' || !Number.isSafeInteger(perTokens) || perTokens <= 0 || !validUnit(unit)) {
    throw new RangeError('Invalid rate');
  }
  const digits = shownDigits(unit);
  // Major units per million tokens = numerator × 10^6 / (denominator × perTokens × 10^scale).
  let numerator = BigInt(rate.numerator) * 1_000_000n;
  let denominator = BigInt(rate.denominator) * BigInt(perTokens) * 10n ** BigInt(unit.scale);
  const divisor = gcd(numerator, denominator);
  numerator /= divisor; denominator /= divisor;
  let rest = denominator, twos = 0, fives = 0;
  while (rest % 2n === 0n) { rest /= 2n; twos++; }
  while (rest % 5n === 0n) { rest /= 5n; fives++; }
  let shown = Math.max(twos, fives, digits), value: bigint;
  if (rest === 1n && shown <= 18) value = (numerator * 10n ** BigInt(shown)) / denominator;
  else {
    shown = Math.max(unit.scale, digits);
    value = (2n * numerator * 10n ** BigInt(shown) + denominator) / (2n * denominator);
  }
  return moneyText(majorDecimal(value.toString(), shown, digits), unit, locale, false);
}
