import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatAmount, formatRate, majorUnits, parseBudget } from '../src/apps/agent/budget.ts';

// Explicit arithmetic fixtures, not claims about a configured currency, price or live model.
const unit = { code: 'TEST', scale: 2 };

test('major-unit budgets and amount displays preserve integers above 2^53', () => {
  assert.equal(parseBudget('90071992547409.93', 2), '9007199254740993');
  assert.equal(formatAmount('9007199254740993', unit), '90071992547409.93 TEST');
  assert.equal(parseBudget('92233720368547758.07', 2), '9223372036854775807');
  assert.equal(formatAmount('9223372036854775807', { code: 'TEST', scale: 0 }), '9223372036854775807 TEST');
});

test('18 decimal places retain the smallest unit and the exact wallet maximum', () => {
  assert.equal(parseBudget('0.000000000000000001', 18), '1');
  assert.equal(parseBudget('1.000000000000000001', 18), '1000000000000000001');
  assert.equal(parseBudget('9.223372036854775807', 18), '9223372036854775807');
  assert.equal(formatAmount('1000000000000000001', { code: 'TEST', scale: 18 }), '1.000000000000000001 TEST');
  assert.equal(formatAmount('1', { code: 'TEST', scale: 18 }), '0.000000000000000001 TEST');
  assert.equal(parseBudget('0001.20', 2), '120');
  assert.equal(parseBudget('12', 0), '12');
});

test('invalid budgets never round, truncate, parse an exponent or overflow silently', () => {
  for (const value of ['', ' ', ' 1', '1 ', '0', '0.00', '-1', '+1', '1e3', '1E3', 'NaN', 'Infinity',
    '1,000', '1,25', '.1', '1.', '1..0', '0x10', '１２', '1_000', '1\n', '1.001', '1.000', '92233720368547758.08']) {
    assert.throws(() => parseBudget(value, 2), undefined, value);
  }
  assert.throws(() => parseBudget('0.0000000000000000001', 18));
  assert.throws(() => parseBudget('1.0', 0));
  for (const scale of [-1, 19, 1.5, NaN, Infinity]) assert.throws(() => parseBudget('1', scale));
});

test('per-batch rational rates display exact decimals or reduced fractions in the stated unit', () => {
  assert.equal(formatRate({ numerator: '125', denominator: '2' }, unit), '0.625 TEST');
  assert.equal(formatRate({ numerator: '1', denominator: '3' }, unit), '1/300 TEST');
  assert.equal(formatRate({ numerator: '200', denominator: '6' }, unit), '1/3 TEST');
  assert.equal(formatRate({ numerator: '9007199254740993', denominator: '2' }, unit), '45035996273704.965 TEST');
  assert.equal(formatRate({ numerator: '1', denominator: '8' }, { code: 'TEST', scale: 18 }), '0.000000000000000000125 TEST');
  assert.equal(formatRate({ numerator: '0', denominator: '7' }, unit), '0 TEST');
  assert.equal(formatRate({ numerator: '1000', denominator: '1' }, unit), '10 TEST');
  assert.throws(() => formatRate({ numerator: '1', denominator: '0' }, unit));
  assert.throws(() => formatRate({ numerator: '1e3', denominator: '1' }, unit));
  assert.throws(() => formatRate({ numerator: '-1', denominator: '1' }, unit));
});

test('an amount is written back as the major-unit decimal a person would type, without the unit code', () => {
  // The setup step prefills the suggested spending limits this way (CNY 10 = '1000' at scale 2).
  assert.equal(majorUnits('1000', { code: 'CNY', scale: 2 }), '10.00');
  assert.equal(majorUnits('25050', { code: 'CNY', scale: 2 }), '250.50');
  assert.equal(majorUnits('1', { code: 'TEST', scale: 18 }), '0.000000000000000001');
  assert.equal(majorUnits('12', { code: 'X', scale: 0 }), '12');
  assert.equal(parseBudget(majorUnits('9007199254740993', unit), 2), '9007199254740993');
});
