import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatDate, formatNumber, getLocale, joinMessages, messageParams, normalizeLocale, registerMessages, resolveLocale, setLocale, subscribeLocale, translate, validateMessages } from '../src/i18n-core.ts';
import { messages } from '../src/messages.ts';

test('locale selection respects explicit preference, browser order, script and region', () => {
  assert.equal(resolveLocale('en', ['zh-TW']), 'en');
  assert.equal(resolveLocale(null, ['fr-FR', 'zh-HK', 'en-US']), 'zh-Hant');
  assert.equal(resolveLocale('invalid', ['zh-CN']), 'zh-Hans');
  assert.equal(resolveLocale(null, []), 'zh-Hans');
  assert.equal(normalizeLocale('zh-Hant-CN'), 'zh-Hant');
  assert.equal(normalizeLocale('zh-MO'), 'zh-Hant');
  assert.equal(normalizeLocale('zh-Hans-HK'), 'zh-Hans');
  assert.equal(normalizeLocale('en_AU'), 'en');
  assert.equal(normalizeLocale('english'), null);
  assert.equal(resolveLocale('zh-Hant', ['zh-CN'], 'en'), 'en');
  assert.equal(resolveLocale('zh-Hant', ['en'], 'en-US'), 'zh-Hant', 'navigation accepts only the three exact tags');
});

test('catalogs require both locales and identical interpolation parameters in every plural form', () => {
  assert.deepEqual(validateMessages(messages), []);
  assert.deepEqual(messageParams('{name}: {count}, {name}'), ['count', 'name']);
  assert.equal(validateMessages({ '{count} 条': { en: { one: 'one', other: '{count} items' }, 'zh-Hant': '{count} 條' } }).length, 1);
  assert.throws(() => registerMessages({ '参数 {value}': { en: 'Parameter {different}', 'zh-Hant': '參數 {value}' } }), /parameters differ/);
  assert.throws(() => registerMessages({ broken: { en: 'Present' } } as any), /missing/);
});

test('interpolation is plain text and nonrecursive, unknown keys fall back without losing data', () => {
  registerMessages({ '测试 {name}，共 {count} 条': { en: { one: 'Test {name}: {count} item', other: 'Test {name}: {count} items' }, 'zh-Hant': '測試 {name}，共 {count} 條' } });
  const input = '<script>{count}</script>';
  assert.equal(translate('en', '测试 {name}，共 {count} 条', { name: input, count: 1 }), `Test ${input}: 1 item`);
  assert.equal(translate('en', '测试 {name}，共 {count} 条', { name: input, count: 12000 }), `Test ${input}: 12,000 items`);
  assert.equal(translate('zh-Hant', '测试 {name}，共 {count} 条', { name: '客户原文', count: 2 }), '測試 客户原文，共 2 條');
  assert.equal(translate('en', 'Unregistered source'), 'Unregistered source');
  assert.equal(translate('en', '{missing}'), '{missing}');
  assert.equal(translate('en', '{value}', { value: null }), '');
  const retained = translate('en', '测试 {name}，共 {count} 条', { name: input, count: 1 });
  assert.equal(translate('zh-Hant', retained), `測試 ${input}，共 1 條`, 'retained UI messages reuse only their known source/parameters');
});

test('formatting uses Intl for the chosen locale without changing input values', () => {
  const n = 12345.67, date = Date.UTC(2026, 8, 24, 16, 30);
  for (const locale of ['zh-Hans', 'zh-Hant', 'en'] as const) {
    assert.equal(formatNumber(n, { maximumFractionDigits: 1 }, locale), new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(n));
    assert.equal(formatDate(date, { dateStyle: 'medium', timeZone: 'UTC' }, locale), new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(date));
  }
  assert.equal(n, 12345.67);
});

test('one-part retained errors can be translated again without self-referential provenance', () => {
  registerMessages({ '验收：支付配置已变化': { en: 'Acceptance: payment settings changed', 'zh-Hant': '驗收：支付配置已變化' } });
  const rendered = translate('en', '验收：支付配置已变化');
  const error = joinMessages([rendered], ' ');
  assert.equal(translate('en', error), rendered);
  assert.equal(translate('zh-Hant', error), '驗收：支付配置已變化');
  assert.equal(translate('zh-Hans', joinMessages(['', error], '')), '验收：支付配置已变化');
  const detailed = joinMessages([error, 'Request ID: fixture'], ' ');
  assert.equal(translate('zh-Hant', detailed), '驗收：支付配置已變化 Request ID: fixture');
});

test('locale notifications work without localStorage and unsubscribe cleanly', () => {
  setLocale('zh-Hans');
  let changes = 0;
  const unsubscribe = subscribeLocale(() => changes++);
  setLocale('en');
  setLocale('en');
  assert.equal(getLocale(), 'en');
  assert.equal(changes, 1);
  unsubscribe();
  setLocale('zh-Hans');
  assert.equal(changes, 1);
});
