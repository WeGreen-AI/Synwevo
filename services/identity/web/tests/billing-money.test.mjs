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
