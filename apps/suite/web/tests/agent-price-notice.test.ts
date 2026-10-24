/** The price notice (design D-7): when `agent.billing.status.price_notice` says the person's last
 *  answer ran under other pricing, the chat shows a dismissible information notice listing what
 *  changed. It never blocks a send, its dismissal is remembered per (previous, current) price in
 *  localStorage behind try/catch, and it renders even when storage throws. Fixtures only. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.ts';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

let server: any, ui: any, core: any, prices: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'notice-fixture-locale', enforce: 'pre', transform(code, id) {
    if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
  } }] });
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  prices = await server.ssrLoadModule('/src/apps/agent/PriceNotice.tsx');
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });
const LOCALES = ['zh-Hans', 'en', 'zh-Hant'] as const;
const rate = (numerator: string, denominator = '1') => ({ numerator, denominator });
const current = { environment: 'test', model: 'fixture-model', confirmed_route: 'fixture-route', route_version: 'fixture-v2', price_id: 'price_fixture_2',
  unit: { code: 'CNY', scale: 6 }, per_tokens: 1, terms_ref: 'fixture:terms', per_call_hold: '60000',
  rates: { input_uncached: rate('2'), input_cached: rate('1', '25'), output: rate('16') }, max_calls_limit: 12, run_seconds_limit: 3600 };
/** FIXTURE: agent.billing.status.price_notice (design §3). */
const notice = { previous: { ...current, route_version: 'fixture-v1', price_id: 'price_fixture_1', per_call_hold: '50000',
  rates: { ...current.rates, output: rate('8') }, off_peak_rates: null }, previous_run_at: 1_700_000_000 };
function memory() {
  const store = new Map<string, string>();
  return { store, getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
}
const broken = { getItem() { throw new Error('fixture: storage blocked'); }, setItem() { throw new Error('fixture: storage blocked'); } };
function render(locale: string, props: Record<string, unknown>) {
  ui.setLocale(locale);
  return renderToStaticMarkup(React.createElement(MemoryRouter, null, React.createElement(ui.I18nProvider ?? React.Fragment, null,
    React.createElement(prices.default, { notice, offer: current, userId: 'usr_fixture', storage: memory(), ...props }))));
}

test('the notice says prices changed, lists each changed figure and offers only a dismissal, in three languages', () => {
  const words = { 'zh-Hans': ['价格已更新，之后的回答按新价格计费。', '知道了', '输出每百万 token', '每次调用最多冻结', '价格版本'],
    en: ['Prices have changed. Answers from now on are charged at the new price.', 'Got it', 'Output per million tokens', 'Maximum hold per call', 'Price version'],
    'zh-Hant': ['價格已更新，之後的回答按新價格計費。', '知道了', '輸出每百萬 token', '每次呼叫最多凍結', '價格版本'] } as const;
  for (const locale of LOCALES) {
    const html = render(locale, {});
    for (const text of words[locale]) assert.ok(html.includes(text), `${locale}: ${text} in ${html}`);
    assert.match(html, /class="notice info ag-price-notice"[^>]*role="status"/);
    assert.ok(html.includes('<del>') && html.includes('<ins>'), html);
    assert.ok(html.includes('CN¥8.00') || html.includes('¥8.00'), html);
    assert.equal(html.match(/<button/g)?.length, 1);
    assert.ok(!/确认|確認|confirm/i.test(html), html);
  }
});

test('a dismissed notice stays hidden for the same price pair only, and storage failures never hide or break it', () => {
  const storage = memory();
  const key = prices.priceNoticeKey('usr_fixture', 'price_fixture_1', 'price_fixture_2');
  assert.equal(key, 'synwevo.agent.price_notice.usr_fixture.price_fixture_1:price_fixture_2');
  assert.equal(prices.priceNoticeDismissed(key, storage), false);
  prices.dismissPriceNotice(key, storage);
  assert.equal(prices.priceNoticeDismissed(key, storage), true);
  assert.equal(storage.store.get(key), '1');
  assert.equal(render('en', { storage }), '');
  // Another previous or current price is a new notice.
  assert.ok(render('en', { storage, notice: { ...notice, previous: { ...notice.previous, price_id: 'price_fixture_0' } } }).includes('Prices have changed'));
  assert.ok(render('en', { storage, offer: { ...current, price_id: 'price_fixture_3' } }).includes('Prices have changed'));
  // Storage that throws (private mode, blocked site data): the notice renders, and dismissing does not throw.
  assert.equal(prices.priceNoticeDismissed(key, broken), false);
  assert.doesNotThrow(() => prices.dismissPriceNotice(key, broken));
  assert.ok(render('en', { storage: broken }).includes('Prices have changed'));
  assert.ok(render('en', { storage: undefined }).includes('Prices have changed'));
  // Without a notice, or with a malformed one, nothing renders.
  assert.equal(render('en', { notice: null }), '');
  assert.equal(render('en', { notice: { previous: { price_id: 'x' } } }), '');
  // Without the current offer the sentence still shows (no list), keyed on the previous price alone.
  const bare = render('zh-Hant', { offer: null });
  assert.ok(bare.includes('價格已更新') && !bare.includes('<del>'), bare);
});
