import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';

// Compile this pure module with the installed Vite compiler. No server, shared
// optimizer cache, experimental Node TS stripping or additional dependency is needed.
const file = new URL('../src/governance/model-costs.ts', import.meta.url);
const { code } = await transformWithOxc(await readFile(file, 'utf8'), fileURLToPath(file));
const { amountToUnits, unitsToAmount, callLimit, costForm, emptyCostForm, validateCostForm, validCostSpec } =
  await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`);

test('major-unit budgets preserve cents and values above the JavaScript safe integer limit', () => {
  assert.equal(amountToUnits('90071992547409.93', 2), '9007199254740993');
  assert.equal(amountToUnits('9223372036854.775807', 6), '9223372036854775807');
  assert.equal(amountToUnits('9223372036854.775808', 6), null);
  assert.equal(amountToUnits('9223372036854775807', 0), '9223372036854775807');
  assert.equal(amountToUnits('9223372036854775808', 0), null);
  assert.equal(amountToUnits('0.000000000001', 12), '1');
  assert.equal(amountToUnits('0.00', 2), '0');
});

test('ambiguous, rounded, negative and oversized monetary inputs are refused', () => {
  for (const value of ['', ' 1', '1 ', '+1', '-0', '-1', '1e3', '1,000', '01', '.5', '1.', 'NaN', 'Infinity', '１', '0x10', '1.001', '1.000', '9'.repeat(1000)]) {
    assert.equal(amountToUnits(value, 2), null, value);
  }
  for (const scale of [-1, 13, 1.5, NaN]) assert.equal(amountToUnits('1', scale), null);
});

test('published budget values can be edited and republished without changing a single unit', () => {
  for (const scale of [0, 2, 6, 12]) {
    for (const value of ['0', '1', '100', '9007199254740993', '9223372036854775807']) {
      assert.equal(amountToUnits(unitsToAmount(value, scale), scale), value);
    }
  }
  for (const value of ['01', '-1', '1.1', '1e3', '9223372036854775808']) assert.throws(() => unitsToAmount(value, 2), RangeError);
});

test('call slots have independent inclusive limits and reject decimals, signs and coercion', () => {
  assert.equal(callLimit('1'), 1);
  assert.equal(callLimit('10000'), 10000);
  for (const value of ['0', '10001', '-1', '01', '1.0', '1e3', ' 1', '', '+1', 'true']) assert.equal(callLimit(value), null);
});

test('a blank policy invents no budgets, units or enablement; reading or selecting a unit does not create a policy', () => {
  const blank = emptyCostForm();
  assert.ok(Object.values(blank).every(value => value === ''));
  const units = [{ code: 'TEST_COST', scale: 6 }];
  assert.equal(validateCostForm(blank, units).spec, null);
  assert.equal(validateCostForm({ ...blank, unit: 'TEST_COST:6' }, units).spec, null);
  assert.deepEqual(costForm(null), blank);
});

test('only exact units supplied by the server are accepted, including when copying a historical version', () => {
  const spec = { enabled: false, unit: { code: 'TEST_COST', scale: 6 }, daily_budget: '9007199254740993', weekly_budget: '0',
    monthly_budget: '9223372036854775807', unknown_budget: '1', max_inflight: 1, max_unknown: 10000 };
  const form = costForm(spec);
  assert.deepEqual(validateCostForm(form, [spec.unit]).spec, spec);
  assert.equal(validateCostForm(form, []).spec, null);
  assert.equal(validateCostForm(form, [{ code: 'TEST_COST', scale: 2 }]).spec, null);
  assert.equal(validateCostForm({ ...form, enabled: '' }, [spec.unit]).spec, null);
  assert.equal(validateCostForm({ ...form, max_unknown: '10001' }, [spec.unit]).spec, null);
  assert.ok(validCostSpec(spec));
  for (const change of [{ daily_budget: 1 }, { daily_budget: '01' }, { unknown_budget: '9223372036854775808' }, { enabled: 'false' }, { max_unknown: true }, { unit: null }]) {
    assert.equal(validCostSpec({ ...spec, ...change }), false);
  }
});
