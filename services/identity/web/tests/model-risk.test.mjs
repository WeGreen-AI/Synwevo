import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';

// Pure helpers of the paid-model risk tab, compiled with the installed Vite compiler (no server).
const file = new URL('../src/governance/model-risk.ts', import.meta.url);
const { code } = await transformWithOxc(await readFile(file, 'utf8'), fileURLToPath(file));
const { roundedUnits, utcDay, lastEndedUtcDay, validDay, topupUnits, codeLabel } =
  await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('rational ledger amounts round half away from zero and say when they were rounded', () => {
  assert.deepEqual(roundedUnits('3000000', '1'), { units: '3000000', exact: true });
  assert.deepEqual(roundedUnits('7', '2'), { units: '4', exact: false });
  assert.deepEqual(roundedUnits('-7', '2'), { units: '-4', exact: false });
  assert.deepEqual(roundedUnits('1', '3'), { units: '0', exact: false });
  assert.deepEqual(roundedUnits('-1', '3'), { units: '0', exact: false });
  // Beyond the JavaScript safe integer range, still exact.
  assert.deepEqual(roundedUnits('18446744073709551615', '1'), { units: '18446744073709551615', exact: true });
  for (const [n, d] of [[null, '1'], ['1', '0'], ['1.5', '1'], ['1', '-2'], ['01', '1'], ['1', null]]) assert.equal(roundedUnits(n, d), null);
});

test('reconciliation days are UTC calendar days and only finished days are offered', () => {
  const now = Date.parse('2033-05-19T00:30:00Z');
  assert.equal(utcDay(now / 1000), '2033-05-19');
  assert.equal(lastEndedUtcDay(now), '2033-05-18');
  assert.equal(validDay('2033-05-18', now), true);
  for (const day of ['2033-05-19', '2033-02-30', '2033-5-18', '', '2033-05-18T00:00']) assert.equal(validDay(day, now), false, day);
});

test('upstream top-ups are positive exact amounts within the unit precision', () => {
  assert.equal(topupUnits('100', 6), '100000000');
  assert.equal(topupUnits('0.000001', 6), '1');
  for (const value of ['0', '0.0', '-1', '1.0000001', '1,000', ' 1', '1e3', '']) assert.equal(topupUnits(value, 6), null, value);
  assert.equal(topupUnits('9223372036854.775807', 6), '9223372036854775807');
  assert.equal(topupUnits('9223372036854.775808', 6), null);
});

test('server codes become fixed wording; unknown codes stay visible as codes', () => {
  assert.deepEqual(codeLabel('upstream_http_401'), { text: '上游返回 HTTP {status}', params: { status: '401' } });
  assert.deepEqual(codeLabel('payer_velocity'), { text: '付款方成本速率超过阈值' });
  assert.deepEqual(codeLabel('something_new'), { text: '代码：{code}', params: { code: 'something_new' } });
  assert.deepEqual(codeLabel(null), { text: '—' });
});
