/** Yuan typed on the money pages (payment settings, grants, organization limits) becomes exact
 *  fen, bounded by the caller's maximum — the ceiling the server accepts is reachable and nothing
 *  beyond it is. Fixtures only; run from the package: `npm test`. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.mjs';
import { fileURLToPath } from 'node:url';

let server, money;
before(async () => {
  server = await testServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  money = await server.ssrLoadModule('/src/governance/billing-money.ts');
});
after(async () => { await server?.close(); });

test('an organization member limit reaches the server ceiling of CNY 1,000,000 and no further', () => {
  // org_billing_routes.MAX_LIMIT_MINOR = 100_000_000 fen
  assert.equal(money.parseYuan('1000000', 1000000), 100_000_000);
  assert.equal(money.parseYuan('999999.99', 1000000), 99_999_999);
  assert.equal(money.parseYuan('1000000.01', 1000000), null);
});

test('the daily top-up ceiling field reaches CNY 10,000,000', () => {
  assert.equal(money.parseYuan('10000000', 10_000_000), 1_000_000_000);
  assert.equal(money.parseYuan('10000000.01', 10_000_000), null);
});

test('only plain yuan with at most two decimals is money; zero and malformed input are not', () => {
  assert.equal(money.parseYuan('10.5'), 1050);
  assert.equal(money.parseYuan(' 0.01 '), 1);
  for (const text of ['0', '', '01', '1e3', '-1', '1.234', '1,000', '１０', 'NaN']) assert.equal(money.parseYuan(text), null, text);
  assert.equal(money.yuanInput(1050), '10.5');
  assert.equal(money.yuanInput(100_000_000), '1000000');
});

test('the money administration pages read ledger units and fen as money at fen precision, in the reader\'s locale', () => {
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    const sign = locale === 'zh-Hans' ? '¥' : 'CN¥';
    // 5,111,820 micro-yuan formerly read "¥5.11182"; a 10-yuan package read "¥10" here and "¥10.00" in the top-up dialog.
    assert.equal(money.cnyText('5111820', 6, locale), `${sign}5.11`, locale);
    assert.equal(money.cnyText(1000, 2, locale), `${sign}10.00`, locale);
    assert.equal(money.fenText(1000, locale), `${sign}10.00`, locale);
    assert.equal(money.fenText(5, locale), `${sign}0.05`, locale);
    assert.equal(money.cnyText('200', 6, locale), { 'zh-Hans': '不到 ¥0.01', 'zh-Hant': '不到 CN¥0.01', en: 'under CN¥0.01' }[locale], locale);
    // A statement credit below one fen reads "within" its sign (formerly "不到 +¥0.01"); a debit likewise.
    assert.equal(money.cnyText('200', 6, locale, { signed: true }), { 'zh-Hans': '+¥0.01 以内', 'zh-Hant': '+CN¥0.01 以內', en: 'within +CN¥0.01' }[locale], locale);
    assert.equal(money.cnyText('-5111820', 6, locale, { signed: true }), `-${sign}5.11`, locale);
    // The organization wallet's available balance rounds down and what is held rounds up, as on the composer.
    assert.equal(money.cnyText('995000', 6, locale, { rounding: 'down' }), `${sign}0.99`, locale);
    assert.equal(money.cnyText('524289', 6, locale, { rounding: 'up' }), `${sign}0.53`, locale);
  }
});
