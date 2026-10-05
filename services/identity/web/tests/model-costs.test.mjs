import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';

// Compile this pure module with the installed Vite compiler. No server, shared
// optimizer cache, experimental Node TS stripping or additional dependency is needed.
const file = new URL('../src/governance/model-costs.ts', import.meta.url);
const { code } = await transformWithOxc(await readFile(file, 'utf8'), fileURLToPath(file));
const { amountToUnits, unitsToAmount, callLimit, costForm, emptyCostForm, validateCostForm, validCostSpec, sameCostSpec, trafficNumber } =
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

const trafficSpec = { enabled: true, unit: { code: 'TEST_COST', scale: 2 }, daily_budget: '100', weekly_budget: '100',
  monthly_budget: '100', unknown_budget: '100', max_inflight: 8, max_unknown: 8,
  traffic: { platform_rpm: 600, platform_tpm: 1000000000000, payer_rpm: 60, payer_tpm: 200000, payer_inflight: 2,
    wait_seconds: 300, max_waiting: 500, max_waiting_per_payer: 20 } };

test('traffic limits round-trip exactly and a policy edited in the form never silently loses them', () => {
  const form = costForm(trafficSpec);
  assert.equal(form.traffic, 'yes');
  assert.deepEqual(validateCostForm(form, [trafficSpec.unit]).spec, trafficSpec);
  // Editing an unrelated field keeps the traffic section; turning it off is an explicit choice.
  const edited = validateCostForm({ ...form, daily_budget: '2' }, [trafficSpec.unit]).spec;
  assert.deepEqual(edited.traffic, trafficSpec.traffic);
  const off = validateCostForm({ ...form, traffic: 'no' }, [trafficSpec.unit]).spec;
  assert.equal(off.traffic, undefined);
  assert.equal(sameCostSpec(off, trafficSpec), false);
  assert.equal(sameCostSpec(edited, { ...trafficSpec, daily_budget: '200' }), true);
  // Older policies without traffic are shown as an explicit "not set", never blank.
  const legacy = { ...trafficSpec }; delete legacy.traffic;
  assert.equal(costForm(legacy).traffic, 'no');
  assert.equal(validateCostForm({ ...costForm(legacy), traffic: '' }, [trafficSpec.unit]).spec, null);
});

test('traffic inputs are bounded integers and payers never exceed the platform', () => {
  assert.equal(trafficNumber('wait_seconds', '0'), 0);
  assert.equal(trafficNumber('wait_seconds', '900'), 900);
  assert.equal(trafficNumber('platform_tpm', '1000000000000'), 1000000000000);
  for (const [key, value] of [['wait_seconds', '901'], ['platform_rpm', '0'], ['platform_tpm', '1000000000001'],
    ['payer_inflight', '10001'], ['max_waiting', '1.5'], ['payer_rpm', '01'], ['payer_rpm', '-1'], ['payer_rpm', '1e3']]) {
    assert.equal(trafficNumber(key, value), null, `${key}=${value}`);
  }
  const form = costForm(trafficSpec);
  for (const [change, field] of [[{ payer_rpm: '601' }, 'payer_rpm'], [{ payer_tpm: '1000000000000', platform_tpm: '999' }, 'payer_tpm'],
    [{ max_waiting_per_payer: '501' }, 'max_waiting_per_payer'], [{ payer_inflight: '9' }, 'payer_inflight']]) {
    const result = validateCostForm({ ...form, ...change }, [trafficSpec.unit]);
    assert.equal(result.spec, null);
    assert.ok(result.errors[field], field);
  }
  assert.ok(validCostSpec(trafficSpec));
  for (const traffic of [null, {}, { ...trafficSpec.traffic, wait_seconds: 901 }, { ...trafficSpec.traffic, payer_rpm: '60' }]) {
    assert.equal(validCostSpec({ ...trafficSpec, traffic }), false);
  }
});

const riskSpec = { ...trafficSpec, max_call_cost: '150', risk: { allowlist_only: true, payer_window_s: 3600, payer_window_cost: '5000',
  route_window_s: 600, route_window_cost: null, balance_warn: '20000', balance_close: '5000', balance_poll_s: 300,
  reconcile_tolerance: '100', pending_alert: null } };

test('the per-call cap and risk section round-trip exactly; switched-off checks stay explicit nulls', () => {
  const form = costForm(riskSpec);
  assert.equal(form.risk, 'yes');
  assert.equal(form.route_window_cost, '');
  assert.deepEqual(validateCostForm(form, [riskSpec.unit]).spec, riskSpec);
  assert.ok(validCostSpec(riskSpec));
  // An older policy without them shows an explicit "not set" and publishes without them.
  const legacy = { ...trafficSpec };
  assert.equal(costForm(legacy).risk, 'no');
  assert.equal(costForm(legacy).max_call_cost, '');
  assert.deepEqual(validateCostForm(costForm(legacy), [trafficSpec.unit]).spec, legacy);
  assert.equal(sameCostSpec(legacy, riskSpec), false);
  assert.equal(sameCostSpec({ ...riskSpec, risk: { ...riskSpec.risk, payer_window_cost: null } }, riskSpec), false);
});

test('risk thresholds are validated before preview', () => {
  const form = costForm(riskSpec);
  for (const [change, field] of [[{ payer_window_s: '59' }, 'payer_window_s'], [{ balance_poll_s: '86401' }, 'balance_poll_s'],
    [{ balance_close: '200.01' }, 'balance_close'], [{ reconcile_tolerance: '' }, 'reconcile_tolerance'],
    [{ max_call_cost: '1.001' }, 'max_call_cost'], [{ allowlist_only: '' }, 'allowlist_only'], [{ risk: '' }, 'risk']]) {
    const result = validateCostForm({ ...form, ...change }, [riskSpec.unit]);
    assert.equal(result.spec, null, field);
    assert.ok(result.errors[field], field);
  }
  for (const risk of [{ ...riskSpec.risk, payer_window_s: '3600' }, { ...riskSpec.risk, balance_close: '30000' },
    { ...riskSpec.risk, allowlist_only: 'true' }, { ...riskSpec.risk, pending_alert: 5 }]) {
    assert.equal(validCostSpec({ ...riskSpec, risk }), false);
  }
  assert.equal(validCostSpec({ ...riskSpec, max_call_cost: 150 }), false);
});
