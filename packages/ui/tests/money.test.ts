/** The shared money formatter. Arithmetic fixtures only: the unit {CNY, scale 6} is the shape the
 *  production wallet is configured with (micro-yuan), the amounts are examples, not claims about
 *  any price, balance or live ledger. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { currencyDigits, formatMinor, formatMoney, formatPerMillion, majorDecimal, roundedUnits, validUnit } from '../src/money.ts';

const CNY6 = { code: 'CNY', scale: 6 };
const CNY2 = { code: 'CNY', scale: 2 };
const LOCALES = ['zh-Hans', 'zh-Hant', 'en'] as const;
/** The same figure with each locale's own currency sign ("¥" in zh-Hans, "CN¥" in zh-Hant and en). */
const yuan = (figure: string) => ({ 'zh-Hans': `¥${figure}`, 'zh-Hant': `CN¥${figure}`, en: `CN¥${figure}` });

test('a ledger amount reads at the currency\'s own precision, rounded from the integer, never with the ledger\'s sub-fen tail', () => {
  for (const locale of LOCALES) {
    // 5,111,820 micro-yuan used to read "¥5.11182"; it is ¥5.11 of money.
    const settled = formatMoney('5111820', CNY6, locale);
    assert.equal(settled, yuan('5.11')[locale], locale);
    assert.ok(!settled.includes('5.11182'), `${locale}: ${settled}`);
    assert.equal(formatMoney('988818', CNY6, locale), yuan('0.99')[locale], locale);   // formerly ¥0.988818
    assert.equal(formatMoney('1000000', CNY6, locale), yuan('1.00')[locale], locale);
    assert.equal(formatMoney('0', CNY6, locale), yuan('0.00')[locale], locale);
    assert.equal(formatMoney('5115000', CNY6, locale), yuan('5.12')[locale], `${locale}: half rounds up`);
    assert.equal(formatMoney('5114999', CNY6, locale), yuan('5.11')[locale], `${locale}: below half rounds down`);
    // A unit already at the currency's precision is shown as it is.
    assert.equal(formatMoney('1234', CNY2, locale), yuan('12.34')[locale], locale);
    assert.equal(formatMoney('-50', CNY2, locale), `-${yuan('0.50')[locale]}`, locale);
    assert.equal(formatMoney('-5111820', CNY6, locale, { signed: true }), `-${yuan('5.11')[locale]}`, locale);
    assert.equal(formatMoney('1000000', CNY6, locale, { signed: true }), `+${yuan('1.00')[locale]}`, locale);
    assert.equal(formatMoney('0', CNY6, locale, { signed: true }), yuan('0.00')[locale], locale);
  }
  // Grouping is the locale's; above 2^53 a Number would have rounded the last digit.
  assert.equal(formatMoney('9007199254740993', CNY2, 'en'), 'CN¥90,071,992,547,409.93');
  assert.equal(formatMoney('9007199254740993123456', CNY6, 'zh-Hans'), '¥9,007,199,254,740,993.12');
  // A unit that is not a currency is a number with its code, at most two decimals.
  assert.equal(formatMoney('1234567', { code: 'FIXTURE', scale: 3 }, 'en'), '1,234.57 FIXTURE');
  assert.equal(formatMoney('12', { code: 'credits', scale: 0 }, 'en'), '12 credits');
  assert.equal(formatMoney('1500', { code: 'credits', scale: 3 }, 'en'), '1.50 credits');
  // A zero-decimal currency keeps none.
  assert.equal(formatMoney('1250', { code: 'JPY', scale: 2 }, 'en'), '¥13');
});

test('a bound said as "at most" or "no lower than" rounds up, so the sentence stays true', () => {
  // One call's hold of 524,289 micro-yuan is ¥0.524289: "at most ¥0.53", not "at most ¥0.52".
  assert.equal(formatMoney('524289', CNY6, 'zh-Hans', { rounding: 'up' }), '¥0.53');
  assert.equal(formatMoney('524289', CNY6, 'zh-Hans'), '¥0.52');
  assert.equal(formatMoney('6291468', CNY6, 'en', { rounding: 'up' }), 'CN¥6.30');     // formerly CN¥6.291468
  assert.equal(formatMoney('6290000', CNY6, 'en', { rounding: 'up' }), 'CN¥6.29', 'an exact figure is not inflated');
  assert.equal(formatMoney('200', CNY6, 'en', { rounding: 'up' }), 'CN¥0.01');
  assert.equal(roundedUnits('524289', 6, 2, 'up'), '53');
  assert.equal(roundedUnits('524289', 6, 2), '52');
  assert.equal(roundedUnits('-524289', 6, 2, 'up'), '-53');
  assert.equal(roundedUnits('-4999', 6, 2), '0', 'no negative zero');
  assert.equal(roundedUnits('1250', 2, 6), '12500000', 'a finer scale rescales exactly');
});

test('what the reader can still spend rounds down, so a balance never reads as at least a hold the ledger says it cannot cover', () => {
  // 988,818 micro-yuan available reads "¥0.98" (at least that), beside a hold of 524,289 that reads "¥0.53" (at most that).
  for (const locale of LOCALES) {
    assert.equal(formatMoney('988818', CNY6, locale, { rounding: 'down' }), yuan('0.98')[locale], locale);
    assert.equal(formatMoney('995000', CNY6, locale, { rounding: 'down' }), yuan('0.99')[locale], `${locale}: half a fen under one yuan is not one yuan`);
    assert.equal(formatMoney('5115000', CNY6, locale, { rounding: 'down' }), yuan('5.11')[locale], locale);
    assert.equal(formatMoney('1000000', CNY6, locale, { rounding: 'down' }), yuan('1.00')[locale], `${locale}: an exact figure is not reduced`);
  }
  assert.equal(formatMoney('4999', CNY6, 'zh-Hans', { rounding: 'down' }), '不到 ¥0.01', 'below one fen is still said, not shown as nothing');
  assert.equal(roundedUnits('988818', 6, 2, 'down'), '98');
  assert.equal(roundedUnits('-988818', 6, 2, 'down'), '-98', 'toward zero');
  assert.equal(roundedUnits('-4999', 6, 2, 'down'), '0', 'no negative zero');
  // The pair the composer shows: whenever the shown balance is at least the shown hold, the ledger's balance covers the hold.
  const fen = (text: string) => Number(text.replace(/[^0-9.]/g, ''));
  const sweep = [...Array.from({ length: 3400 }, (_, i) => 520000n + 3n * BigInt(i)), ...Array.from({ length: 2000 }, (_, i) => 995000n + 3n * BigInt(i))];
  for (const available of sweep) {
    for (const hold of [524289n, 525000n, 525289n, 1000000n]) {
      const balance = fen(formatMoney(available.toString(), CNY6, 'en', { rounding: 'down' }));
      const bound = fen(formatMoney(hold.toString(), CNY6, 'en', { rounding: 'up' }));
      if (balance >= bound) assert.ok(available >= hold, `${available} reads CN¥${balance} beside a hold reading CN¥${bound}`);
    }
  }
});

test('a signed amount whose size is below one fen reads "within" its sign; the sign never sits inside the "under" phrase', () => {
  const credit = { 'zh-Hans': '+¥0.01 以内', 'zh-Hant': '+CN¥0.01 以內', en: 'within +CN¥0.01' };
  const debit = { 'zh-Hans': '-¥0.01 以内', 'zh-Hant': '-CN¥0.01 以內', en: 'within -CN¥0.01' };
  for (const locale of LOCALES) {
    assert.equal(formatMoney('200', CNY6, locale, { signed: true }), credit[locale], locale);
    assert.equal(formatMoney('4999', CNY6, locale, { signed: true }), credit[locale], locale);
    assert.equal(formatMoney('-4999', CNY6, locale, { signed: true }), debit[locale], locale);
    assert.ok(!/不到|under/.test(formatMoney('200', CNY6, locale, { signed: true })), `${locale}: the "under" phrase never carries a sign`);
  }
});

test('a non-zero amount below one fen says so in the reader\'s language, instead of a "¥0.00" that denies it', () => {
  const under = { 'zh-Hans': '不到 ¥0.01', 'zh-Hant': '不到 CN¥0.01', en: 'under CN¥0.01' };
  const within = { 'zh-Hans': '-¥0.01 以内', 'zh-Hant': '-CN¥0.01 以內', en: 'within -CN¥0.01' };
  for (const locale of LOCALES) {
    assert.equal(formatMoney('200', CNY6, locale), under[locale], locale);          // formerly ¥0.0002
    assert.equal(formatMoney('4999', CNY6, locale), under[locale], locale);
    assert.equal(formatMoney('-200', CNY6, locale, { signed: true }), within[locale], locale);
    assert.equal(formatMoney('-200', CNY6, locale), within[locale], locale);
    assert.equal(formatMoney('5000', CNY6, locale), formatMoney('10000', CNY6, locale), `${locale}: half a fen rounds to one`);
  }
  assert.equal(formatMoney('0', CNY6, 'en'), 'CN¥0.00', 'nothing is nothing');
  assert.equal(formatMoney('1', { code: 'FIXTURE', scale: 3 }, 'en'), 'under 0.01 FIXTURE');
});

test('exact precision keeps every digit the ledger holds, for configured values that are facts as typed', () => {
  assert.equal(formatMoney('123', CNY6, 'zh-Hans', { precision: 'exact' }), '¥0.000123');
  assert.equal(formatMoney('120000', CNY6, 'zh-Hans', { precision: 'exact' }), '¥0.12');
  assert.equal(formatMoney('10000000', CNY6, 'en', { precision: 'exact' }), 'CN¥10.00');
  assert.equal(formatMoney('524289', CNY6, 'en', { precision: 'exact' }), 'CN¥0.524289');
  assert.equal(formatMoney('-45', { code: 'CNY', scale: 4 }, 'en', { precision: 'exact', signed: true }), '-CN¥0.0045');
  assert.equal(formatMoney('1234567', { code: 'FIXTURE', scale: 3 }, 'en', { precision: 'exact' }), '1,234.567 FIXTURE');
  assert.equal(formatMoney('1', { code: 'TEST', scale: 18 }, 'en', { precision: 'exact' }), '0.000000000000000001 TEST');
  assert.equal(majorDecimal('5', 6), '0.000005');
  assert.equal(majorDecimal('1200', 2, 2), '12.00');
  assert.equal(majorDecimal('-0', 2), '0');
  assert.equal(majorDecimal('-45', 4), '-0.0045');
});

test('a payment amount in minor units of its currency is money at that currency\'s digits', () => {
  for (const locale of LOCALES) {
    assert.equal(formatMinor(1000, 'CNY', locale), yuan('10.00')[locale], locale);   // the same package read "¥10" on one page and "¥10.00" on another
    assert.equal(formatMinor('5000', 'CNY', locale), yuan('50.00')[locale], locale);
  }
  assert.equal(formatMinor(1250, 'JPY', 'en'), '¥1,250');
  assert.equal(currencyDigits('CNY'), 2);
  assert.equal(currencyDigits('JPY'), 0);
  assert.equal(currencyDigits('FIXTURE'), null);
  assert.equal(currencyDigits('credits'), null);
  assert.throws(() => formatMinor(1.5, 'CNY', 'en'), RangeError);
  assert.throws(() => formatMinor(10, 'credits', 'en'), RangeError);
});

test('a unit price per million tokens is the published decimal, exact, with at least the currency\'s digits and no float tail', () => {
  // Rates are smallest units per `perTokens` tokens, as the server quotes them (catalogue.snapshot).
  const million = 1_000_000;
  assert.equal(formatPerMillion({ numerator: '2000000', denominator: '1' }, million, CNY6, 'zh-Hans'), '¥2.00');
  assert.equal(formatPerMillion({ numerator: '2000000', denominator: '1' }, million, CNY6, 'en'), 'CN¥2.00');
  // A markup of ×1.1234 on ¥2 per million is ¥2.2468 per million: published with four decimals, shown with four.
  assert.equal(formatPerMillion({ numerator: '22468000', denominator: '10' }, million, CNY6, 'zh-Hans'), '¥2.2468');
  // The same price per a smaller batch is the same price.
  assert.equal(formatPerMillion({ numerator: '2000', denominator: '1' }, 1000, CNY6, 'zh-Hant'), 'CN¥2.00');
  assert.equal(formatPerMillion({ numerator: '2', denominator: '1' }, 1, CNY6, 'zh-Hans'), '¥2.00');
  assert.equal(formatPerMillion({ numerator: '1', denominator: '25' }, 1, CNY6, 'zh-Hans'), '¥0.04');
  assert.equal(formatPerMillion({ numerator: '0', denominator: '7' }, 1000, CNY2, 'en'), 'CN¥0.00');
  // A rate whose decimal does not terminate is rounded to the unit's own decimals and said to be about that (the test below).
  assert.equal(formatPerMillion({ numerator: '1', denominator: '3' }, 1, CNY6, 'en'), 'about CN¥0.333333');
  assert.equal(formatPerMillion({ numerator: '1', denominator: '3' }, 1, { code: 'FIXTURE', scale: 2 }, 'en'), 'about 3,333.33 FIXTURE');
  // 62.5 smallest units of a two-decimal unit per million: the published price has three decimals, and keeps them.
  assert.equal(formatPerMillion({ numerator: '125', denominator: '2' }, million, { code: 'TEST', scale: 2 }, 'en'), '0.625 TEST');
  for (const bad of [{ numerator: '1', denominator: '0' }, { numerator: '1e3', denominator: '1' }, { numerator: '-1', denominator: '1' }, null]) {
    assert.throws(() => formatPerMillion(bad as any, million, CNY6, 'en'), RangeError);
  }
  assert.throws(() => formatPerMillion({ numerator: '1', denominator: '1' }, 0, CNY6, 'en'), RangeError);
});

test('a unit price whose decimal does not terminate is said to be about its rounding; below the smallest figure shown, under it', () => {
  const million = 1_000_000;
  const about = { 'zh-Hans': '约 ¥0.333333', 'zh-Hant': '約 CN¥0.333333', en: 'about CN¥0.333333' };
  for (const locale of LOCALES) assert.equal(formatPerMillion({ numerator: '1', denominator: '3' }, 1, CNY6, locale), about[locale], locale);
  assert.equal(formatPerMillion({ numerator: '2', denominator: '3' }, 1, CNY6, 'en'), 'about CN¥0.666667', 'rounded half up, and said to be rounded');
  // A third of a micro-yuan per million tokens is not nothing, and never "¥0.00".
  assert.equal(formatPerMillion({ numerator: '1', denominator: '3' }, million, CNY6, 'en'), 'under CN¥0.000001');
  assert.equal(formatPerMillion({ numerator: '1', denominator: '3000000' }, 1, CNY6, 'zh-Hans'), '不到 ¥0.000001');
  // A decimal that terminates only beyond 18 places is rounded too, and says so.
  assert.equal(formatPerMillion({ numerator: '1', denominator: '33554432' }, 1, { code: 'CNY', scale: 0 }, 'en'), 'about CN¥0.03');
  // An exact price carries no such word; the ledger's zero is a plain zero.
  assert.equal(formatPerMillion({ numerator: '2000000', denominator: '1' }, million, CNY6, 'en'), 'CN¥2.00');
  assert.equal(formatPerMillion({ numerator: '0', denominator: '3' }, 1, CNY6, 'en'), 'CN¥0.00');
});

test('malformed amounts and units are refused, never rounded into a plausible figure', () => {
  for (const bad of ['1.5', '1e3', '', ' 1', '0x10', '01', '+1', 5, null]) {
    assert.throws(() => formatMoney(bad as any, CNY2, 'en'), RangeError, String(bad));
  }
  for (const unit of [{ code: 'CNY', scale: 19 }, { code: 'CNY', scale: -1 }, { code: 'CNY', scale: 1.5 }, { code: '', scale: 2 }, { code: ' ', scale: 2 }, null]) {
    assert.equal(validUnit(unit as any), false);
    assert.throws(() => formatMoney('1', unit as any, 'en'), RangeError);
  }
  assert.equal(validUnit(CNY6), true);
  assert.throws(() => majorDecimal('1', 19), RangeError);
  assert.throws(() => roundedUnits('1', 2, 19), RangeError);
});
