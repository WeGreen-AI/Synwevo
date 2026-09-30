import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

let server, amount;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  amount = (await server.ssrLoadModule('/src/pages/account/money.ts')).walletAmount;
});
after(async () => { await server?.close(); });

test('wallet amounts preserve fractional units and integers beyond JS safe precision in all three locales', () => {
  for (const locale of ['en', 'zh-Hans', 'zh-Hant']) {
    assert.equal(amount('9007199254740993123456', 6, locale), '9,007,199,254,740,993.123456');
    assert.equal(amount('-1', 12, locale), '-0.000000000001');
    assert.equal(amount('1', 18, locale), '0.000000000000000001');
    assert.equal(amount('1200000', 6, locale, true), '+1.2');
    assert.equal(amount('0', 6, locale, true), '0');
    assert.equal(amount('-9007199254740993', 0, locale), '-9,007,199,254,740,993');
  }
});

test('invalid wallet amounts cannot be displayed as rounded or fabricated numbers', () => {
  for (const [value, scale] of [['1.5', 2], ['1e5', 6], ['+1', 0], ['01', 0], ['1', 19], ['1', -1], ['1', 1.5]]) {
    assert.throws(() => amount(value, scale, 'en'), RangeError);
  }
});
