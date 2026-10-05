import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

// FIXTURE calendars only. The module is compiled with the installed Vite compiler, as in
// model-costs.test.mjs; the cases mirror tests/integration/test_price_bands.py so the editor's
// "band now" hint and the server's dispatch-time band cannot drift apart unnoticed.
const file = new URL('../src/governance/model-price-bands.ts', import.meta.url);
const { code } = await transformWithOxc(await readFile(file, 'utf8'), fileURLToPath(file));
const helperURL = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const { bandAt, canonicalSchedule, compareDecimal, expandDates, localInput, localTimestamp, mergeHolidays, scheduleProblems, shiftedTime, utcOffset,
  validDate, validMarkup, validTime, validateMarkup, emptyMarkupForm } = await import(helperURL);

const WORKDAYS = [1, 2, 3, 4, 5];
const SPEC = {
  peak_windows: [{ weekdays: WORKDAYS, start: '01:00', end: '04:00' }, { weekdays: WORKDAYS, start: '06:00', end: '10:00' }],
  holiday_utc_offset_minutes: 480, holidays: [{ date: '2026-10-01', name: 'Fixture holiday' }], source_ref: 'fixture-calendar:v1',
};
const utc = (text) => Date.parse(`${text}Z`) / 1000;

test('the band follows UTC windows on workdays and is off-peak on weekends and holidays', () => {
  const cases = [
    ['2026-10-05T00:59:59', 'off_peak'], ['2026-10-05T01:00:00', 'peak'], ['2026-10-05T03:59:59', 'peak'],
    ['2026-10-05T04:00:00', 'off_peak'], ['2026-10-05T05:59:59', 'off_peak'], ['2026-10-05T06:00:00', 'peak'],
    ['2026-10-05T09:59:59', 'peak'], ['2026-10-05T10:00:00', 'off_peak'], ['2026-10-09T02:00:00', 'peak'],
    ['2026-10-03T02:00:00', 'off_peak'], ['2026-10-04T07:00:00', 'off_peak'],
    ['2026-10-01T02:00:00', 'off_peak'], ['2026-10-01T07:00:00', 'off_peak'], ['2026-10-02T02:00:00', 'peak'],
  ];
  for (const [instant, band] of cases) assert.equal(bandAt(SPEC, utc(instant)), band, instant);
});

test('a holiday is a date of the calendar time zone (UTC+8), while windows are UTC', () => {
  const allDay = { ...SPEC, peak_windows: [{ weekdays: [1, 2, 3, 4, 5, 6, 7], start: '00:00', end: '24:00' }] };
  assert.equal(bandAt(allDay, utc('2026-09-30T15:59:59')), 'peak');
  assert.equal(bandAt(allDay, utc('2026-09-30T16:00:00')), 'off_peak');
  assert.equal(bandAt(allDay, utc('2026-10-01T15:59:59')), 'off_peak');
  assert.equal(bandAt(allDay, utc('2026-10-01T16:00:00')), 'peak');
  assert.equal(utcOffset(480), 'UTC+08:00');
  assert.equal(utcOffset(-210), 'UTC-03:30');
});

test('only real dates and short ranges become holidays, kept ascending and distinct', () => {
  assert.equal(validDate('2026-10-01'), true);
  for (const bad of ['2026-02-30', '2026-13-01', '26-10-01', '2026-10-1', '', '2026-10-01T00:00']) assert.equal(validDate(bad), false, bad);
  assert.deepEqual(expandDates('2026-10-01', '2026-10-07'), ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07']);
  assert.deepEqual(expandDates('2026-12-31', ''), ['2026-12-31']);
  assert.deepEqual(expandDates('2026-12-31', '2027-01-01'), ['2026-12-31', '2027-01-01']);
  assert.equal(expandDates('2026-10-07', '2026-10-01'), null);
  assert.equal(expandDates('2026-01-01', '2026-02-01'), null);   // 32 days
  assert.equal(expandDates('2026-02-30', ''), null);
  const merged = mergeHolidays([{ date: '2026-10-03', name: 'Old' }, { date: '2026-01-01', name: 'New year' }],
    ['2026-10-02', '2026-10-03'], 'Renamed');
  assert.deepEqual(merged.map(h => h.date), ['2026-01-01', '2026-10-02', '2026-10-03']);
  assert.equal(merged[2].name, 'Renamed');
});

test('schedule problems are reported before a preview is requested', () => {
  assert.deepEqual(scheduleProblems(SPEC), []);
  assert.deepEqual(scheduleProblems({ ...SPEC, peak_windows: [{ weekdays: [6, 7], start: '00:00', end: '24:00' }] }), []);
  const problems = (change) => scheduleProblems({ ...SPEC, ...change });
  assert.deepEqual(problems({ peak_windows: [] }), ['windows']);
  assert.deepEqual(problems({ peak_windows: [{ weekdays: [], start: '01:00', end: '02:00' }] }), ['window_days']);
  assert.deepEqual(problems({ peak_windows: [{ weekdays: [2, 1], start: '01:00', end: '02:00' }] }), ['window_days']);
  assert.deepEqual(problems({ peak_windows: [{ weekdays: [1], start: '24:00', end: '24:00' }] }), ['window_time']);
  assert.deepEqual(problems({ peak_windows: [{ weekdays: [1], start: '1:00', end: '02:00' }] }), ['window_time']);
  assert.deepEqual(problems({ peak_windows: [{ weekdays: [1], start: '04:00', end: '01:00' }] }), ['window_order']);
  assert.deepEqual(problems({ peak_windows: [SPEC.peak_windows[0], SPEC.peak_windows[0]] }), ['window_duplicate']);
  assert.deepEqual(problems({ holidays: [{ date: '2026-10-02', name: 'A' }, { date: '2026-10-01', name: 'B' }] }), ['holiday_order']);
  assert.deepEqual(problems({ holidays: [{ date: '2026-10-01', name: ' padded' }] }), ['holiday_name']);
  assert.deepEqual(problems({ source_ref: 'https://example.test/?q=1' }), ['source']);
  assert.equal(validTime('24:00'), false);
  assert.equal(validTime('24:00', true), true);
});

test('markups are 1 to 100 with at most four decimals, so nothing is sold below cost', () => {
  for (const ok of ['1', '1.5', '2.5', '99.9999', '100', '100.0', '100.0000']) assert.equal(validMarkup(ok), true, ok);
  for (const bad of ['', '0', '0.5', '0.9999', '01', '1.', '.5', '1.23456', '100.5', '100.00001', '101', '1e2', ' 2', '2 ', '+2', '１']) {
    assert.equal(validMarkup(bad), false, bad);
  }
});

test('key order never makes an unchanged calendar look changed', () => {
  const reordered = { source_ref: SPEC.source_ref, holidays: SPEC.holidays, holiday_utc_offset_minutes: 480,
    peak_windows: SPEC.peak_windows.map(w => ({ end: w.end, start: w.start, weekdays: w.weekdays })) };
  assert.equal(canonicalSchedule(reordered), canonicalSchedule(SPEC));
  assert.notEqual(canonicalSchedule({ ...SPEC, holidays: [] }), canonicalSchedule(SPEC));
});

test('off-peak rates are compared with peak rates exactly, as decimal text', () => {
  const cases = [['0', '0', 0], ['1', '1.0', 0], ['0.5', '1', -1], ['2', '1.999999999', 1], ['10', '9.5', 1], ['0.000000001', '0', 1],
    ['123456789.123456789', '123456789.123456788', 1], ['4.5', '9', -1], ['0.15', '0.3', -1], ['100000000', '99999999.999999999', 1]];
  for (const [a, b, expected] of cases) {
    assert.equal(compareDecimal(a, b), expected, `${a} vs ${b}`);
    assert.equal(compareDecimal(b, a), expected === 0 ? 0 : -expected, `${b} vs ${a}`);
  }
});

test('peak windows show their UTC+8 times, wrapping past midnight', () => {
  assert.equal(shiftedTime('01:00', 480), '09:00');
  assert.equal(shiftedTime('10:00', 480), '18:00');
  assert.equal(shiftedTime('20:30', 480), '04:30');
  assert.equal(shiftedTime('24:00', 480), '08:00');
  assert.equal(shiftedTime('00:15', -210), '20:45');
});

test('a markup form invents nothing and asks for an off-peak markup only on a banded cost price', () => {
  const previous = process.env.TZ;
  process.env.TZ = 'Asia/Hong_Kong';
  try {
    assert.deepEqual(validateMarkup(emptyMarkupForm(), false).spec, null);
    const form = { name: 'Fixture retail', cost_price_id: 'price_fixture', markup: '1.5', off_peak_markup: '', terms_ref: 'fixture:retail',
      effective: '2026-10-05T09:00:00' };
    assert.deepEqual(validateMarkup(form, false).spec, { name: 'Fixture retail', cost_price_id: 'price_fixture', markup: '1.5',
      off_peak_markup: null, terms_ref: 'fixture:retail', effective_from: Date.parse('2026-10-05T01:00:00Z') / 1000 });
    assert.deepEqual(validateMarkup({ ...form, off_peak_markup: '1.2' }, false).problems, ['off_peak_markup']);
    assert.equal(validateMarkup({ ...form, off_peak_markup: '1.2' }, true).spec.off_peak_markup, '1.2');
    assert.deepEqual(validateMarkup({ ...form, markup: '0.9' }, true).problems, ['markup']);
    assert.deepEqual(validateMarkup({ ...form, effective: '2026-02-30T09:00:00' }, true).problems, ['effective']);
    assert.deepEqual(validateMarkup({ ...form, terms_ref: 'https://x.test/?a=1' }, true).problems, ['terms_ref']);
    assert.equal(localTimestamp(localInput(1780000007)), 1780000007);
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
});

// ------------------------------------------------------------------ SSR fixtures (no HTTP, no account)
const governance = new URL('../src/governance/', import.meta.url);
const moduleURL = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
async function compile(name) {
  const source = new URL(name, governance);
  return (await transformWithOxc(await readFile(source, 'utf8'), fileURLToPath(source), { jsx: { runtime: 'automatic' } })).code;
}
// Adapter fixtures replace HTTP and shared-context hooks only; React rendering is real.
const supportURL = moduleURL(`
export const fixture = { messages: {}, reads: [], locale: 'zh-Hans', data: {} };
export const registerMessages = messages => { for (const [k, v] of Object.entries(messages)) if (!fixture.messages[k]) fixture.messages[k] = v; };
export function useI18n() { return { locale: fixture.locale, t: (key, args = {}) =>
  (fixture.locale === 'zh-Hans' ? key : fixture.messages[key]?.[fixture.locale] || key).replace(/\\{([^}]+)\\}/g, (_, k) => String(args[k] ?? k)) }; }
export function useAdminData(path) { fixture.reads.push(path); return { data: fixture.data[path] ?? null, err: null, loading: false, reload() {} }; }
export const useUnsavedChanges = () => {};
export class ApiError extends Error {}
export const post = () => { throw new Error('HTTP prohibited in SSR fixture'); };
export const errText = () => 'fixture error';
export const ActionDialog = () => { throw new Error('Unexpected dialog in SSR fixture'); };
`);
const support = await import(supportURL);
const viaSupport = text => text.replaceAll("'@synwevo/ui'", JSON.stringify(supportURL)).replaceAll('"@synwevo/ui"', JSON.stringify(supportURL));
const catalogues = {};
for (const name of ['model-price-bands.messages.ts', 'model-prices.messages.ts']) catalogues[name] = moduleURL(viaSupport(await compile(name)));
for (const url of Object.values(catalogues)) await import(url);
const imports = { react: import.meta.resolve('react'), 'react/jsx-runtime': import.meta.resolve('react/jsx-runtime'),
  '@synwevo/auth-client': supportURL, '@synwevo/ui': supportURL, './ActionDialog': supportURL, './data': supportURL, './labels': supportURL,
  '../lib/hooks': supportURL, './model-price-bands': helperURL, './model-price-bands.messages': catalogues['model-price-bands.messages.ts'] };
const load = async name => import(moduleURL((await compile(name)).replace(/(from\s*|import\s*)(['"])([^'"]+)\2/g,
  (match, prefix, _quote, spec) => imports[spec] ? `${prefix}${JSON.stringify(imports[spec])}` : match)));
const { ModelPriceCalendar } = await load('ModelPriceCalendar.tsx');
const { ModelPriceMarkups } = await load('ModelPriceMarkups.tsx');
const DEFAULTS = { ...SPEC, holidays: [], source_ref: 'api-docs.deepseek.com/quick_start/pricing:2026-10-05' };
const unpublished = { environment: 'test', schedule_id: 'deepseek', revision: 0, spec: null, actor_id: null, created_at: null, reason: null,
  operation_id: null, defaults: DEFAULTS };
const published = { ...unpublished, revision: 2, spec: SPEC, actor_id: 'fixture-admin', created_at: 1780000000, reason: 'Fixture calendar reason',
  operation_id: 'ao_fixture' };
const renderPanel = (Component, props) => renderToStaticMarkup(React.createElement(Component, { manage: false, onDenied() {}, ...props }));
const SCHEDULE = '/api/admin/billing/prices/schedule';

test('the calendar is honest before publication and read-only viewers get no write controls', () => {
  support.fixture.reads = [];
  support.fixture.data = { [SCHEDULE]: unpublished, [`${SCHEDULE}/history?limit=20`]: { items: [], next_before_revision: null } };
  const html = renderPanel(ModelPriceCalendar);
  assert.deepEqual(support.fixture.reads, [SCHEDULE, `${SCHEDULE}/history?limit=20`]);
  assert.match(html, /尚未发布高峰时段与节假日日历。分时价格不能发布，分时调用不会派发。/);
  assert.match(html, /不能修改或发布/);
  assert.doesNotMatch(html, /<input|<button type="submit"/);
});

test('a published calendar shows UTC and UTC+8 windows, holidays, provenance and its history', () => {
  support.fixture.data = { [SCHEDULE]: published,
    [`${SCHEDULE}/history?limit=20`]: { items: [{ ...published, defaults: undefined }], next_before_revision: null } };
  const html = renderPanel(ModelPriceCalendar);
  assert.match(html, /周一至周五 UTC 01:00–04:00（UTC\+08:00 09:00–12:00）/);
  assert.match(html, /周一至周五 UTC 06:00–10:00（UTC\+08:00 14:00–18:00）/);
  assert.match(html, /2026-10-01：Fixture holiday/);
  assert.match(html, /Fixture calendar reason/);
  assert.match(html, /日历版本 2/);
  support.fixture.locale = 'en';
  try {
    const en = renderPanel(ModelPriceCalendar);
    assert.match(en, /Mon–Fri UTC 01:00–04:00 \(UTC\+08:00 09:00–12:00\)/);
    assert.doesNotMatch(en.replace(/Fixture[^<]*/g, ''), /[㐀-鿿]/);
  } finally { support.fixture.locale = 'zh-Hans'; }
});

test('a calendar manager edits windows and holidays with labelled controls; unconfigured billing disables them', () => {
  support.fixture.data = { [SCHEDULE]: published, [`${SCHEDULE}/history?limit=20`]: { items: [], next_before_revision: null } };
  // The editor mounts from an effect after the first read; SSR shows the read-only state and no stale editor.
  const html = renderPanel(ModelPriceCalendar, { manage: true });
  assert.doesNotMatch(html, /不能修改或发布/);
  support.fixture.data = { [SCHEDULE]: { ...unpublished, environment: null }, [`${SCHEDULE}/history?limit=20`]: { items: [], next_before_revision: null } };
  assert.match(renderPanel(ModelPriceCalendar, { manage: true }), /计费环境尚未配置，日历编辑与发布已禁用。/);
});

const MARKUPS = '/api/admin/billing/prices/markups';
const PRICES = '/api/admin/billing/prices?limit=100';
const costRecord = (id, banded) => ({ id: `draft_${id}`, revision: 2, status: 'published', price_id: id, created_at: 1, updated_at: 1, actor_id: 'fixture-admin',
  spec: { name: `Fixture cost ${id}`, purpose: 'cost', unit: { code: 'FIX_CNY', scale: 2 }, provider: 'deepseek', model: 'fixture-flash',
    input_uncached: '2', input_cached: '0.04', output: '8', terms_ref: 'fixture:cost', effective_from: 0, previous_price_id: null,
    off_peak: banded ? { input_uncached: '1', input_cached: '0.02', output: '4' } : null } });
const markupVersion = { price_id: 'price_sale_fixture', model: 'fixture-flash', unit: { code: 'FIX_CNY', scale: 2 },
  spec: { name: 'Fixture retail', cost_price_id: 'price_cost_banded', markup: '1.5', off_peak_markup: '1.2', terms_ref: 'fixture:retail', effective_from: 0 },
  sale_rates: { peak: { input_uncached: '3', input_cached: '0.06', output: '12' }, off_peak: { input_uncached: '1.2', input_cached: '0.024', output: '4.8' } },
  actor_id: 'fixture-admin', reason: 'Fixture markup reason', operation_id: 'ao_markup', created_at: 1780000000 };

test('published markups list their per-band sale rates; viewers cannot publish', () => {
  support.fixture.reads = [];
  support.fixture.data = { [MARKUPS]: { environment: 'test', items: [markupVersion] },
    [PRICES]: { environment: 'test', items: [costRecord('price_cost_banded', true)], next_before: null } };
  const html = renderPanel(ModelPriceMarkups);
  assert.deepEqual(support.fixture.reads, [MARKUPS, PRICES]);
  assert.match(html, /高峰倍率 ×1.5，低谷倍率 ×1.2/);
  assert.match(html, /<th scope="col">高峰<\/th><th scope="col">低谷<\/th>/);
  assert.match(html, /12 FIX_CNY/);
  assert.match(html, /4.8 FIX_CNY/);
  assert.match(html, /你可以查看加价版本，不能发布。/);
  assert.doesNotMatch(html, /<select|<button type="submit"/);
});

test('a markup manager chooses among published cost prices only, and the adoption rule is stated', () => {
  support.fixture.data = { [MARKUPS]: { environment: 'test', items: [] }, [PRICES]: { environment: 'test', next_before: null,
    items: [costRecord('price_cost_banded', true), costRecord('price_cost_flat', false),
      { ...costRecord('price_sale_x', false), spec: { ...costRecord('price_sale_x', false).spec, purpose: 'sale' } },
      { ...costRecord('draft_only', false), status: 'draft', price_id: null }] } };
  const html = renderPanel(ModelPriceMarkups, { manage: true });
  assert.match(html, /value="price_cost_banded">Fixture cost price_cost_banded · fixture-flash · 分时 · price_cost_banded/);
  assert.match(html, /value="price_cost_flat">Fixture cost price_cost_flat · fixture-flash · 统一费率/);
  assert.doesNotMatch(html, /price_sale_x|draft_only/);
  assert.match(html, /要让用户按新售价付费，请在「模型提供方」中让路线采用该售价/);
  assert.match(html, /尚未发布加价售价。/);
  support.fixture.data = { [MARKUPS]: { environment: null, items: [] }, [PRICES]: { environment: null, items: [], next_before: null } };
  assert.match(renderPanel(ModelPriceMarkups, { manage: true }), /计费环境尚未配置，加价发布已禁用。/);
});

test('every new page and validation sentence has English and Traditional Chinese with the same placeholders', async () => {
  const strings = new Set();
  for (const name of ['ModelPriceCalendar.tsx', 'ModelPriceMarkups.tsx', 'ModelPricesTab.tsx']) {
    const text = await readFile(new URL(name, governance), 'utf8');
    for (const match of text.matchAll(/'([^'\n]*[㐀-鿿、][^'\n]*)'|"([^"\n]*[㐀-鿿、][^"\n]*)"/g)) {
      const value = match[1] ?? match[2];
      if (!/[<>{]=?\s*t\(|className=/.test(value)) strings.add(value);
    }
  }
  const tokens = value => [...value.matchAll(/\{([^}]+)\}/g)].map(match => match[1]).sort();
  const missing = [];
  for (const key of strings) {
    const translations = support.fixture.messages[key];
    if (!translations) { missing.push(key); continue; }
    for (const locale of ['en', 'zh-Hant']) {
      assert.ok(translations[locale]?.trim(), `${locale}: ${key}`);
      assert.deepEqual(tokens(translations[locale]), tokens(key), `${locale}: ${key}`);
    }
    assert.doesNotMatch(translations.en, /[㐀-鿿]/, key);
  }
  assert.deepEqual(missing, []);
});
