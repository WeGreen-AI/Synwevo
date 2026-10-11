/** Wallet, top-up, receipt and usage pages: their pure decisions and their rendered markup.
 *  Isolated UI fixtures: no HTTP server, no account, no ledger; every figure below is a fixture
 *  value standing for what the server would send, marked as such. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { testServer } from './_vite.mjs';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

const WEB = fileURLToPath(new URL('../', import.meta.url));
const storage = new Map();
globalThis.window ??= { location: new URL('http://127.0.0.1/'), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage ??= { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };
let server, ui, model, wallet, topup, status, usage, chart, receipt, topbar, gate;
before(async () => {
  server = await testServer({ root: WEB, configFile: false, logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ui = await server.ssrLoadModule('@synwevo/ui');
  model = await server.ssrLoadModule('/src/pages/account/walletModel.ts');
  wallet = await server.ssrLoadModule('/src/pages/account/Wallet.tsx');
  topup = await server.ssrLoadModule('/src/pages/account/TopupDialog.tsx');
  status = await server.ssrLoadModule('/src/pages/account/TopupStatus.tsx');
  usage = await server.ssrLoadModule('/src/pages/account/Usage.tsx');
  chart = await server.ssrLoadModule('/src/pages/account/UsageChart.tsx');
  receipt = await server.ssrLoadModule('/src/pages/account/Receipt.tsx');
  topbar = await server.ssrLoadModule('/src/components/Topbar.tsx');
  gate = await server.ssrLoadModule('/src/pages/account/WalletGate.tsx');
  await server.ssrLoadModule('/src/components/Topbar.messages.ts');
  await server.ssrLoadModule('/src/pages/account/WalletSettings.tsx');
});
after(async () => { await server?.close(); });

const render = element => renderToStaticMarkup(React.createElement(MemoryRouter, null, element));
const h = React.createElement;

// ------------------------------------------------------------------ payer and addresses
test('a payer is the person by default; only a well-formed organization id selects an organization', () => {
  assert.deepEqual(model.parsePayer(null), { kind: 'user', id: '' });
  assert.deepEqual(model.parsePayer('organization:org_1'), { kind: 'organization', id: 'org_1' });
  for (const raw of ['organization:', 'organization:a/b', 'organization:a b', 'user:someone-else', 'org_1', 'organization:x?y'])
    assert.deepEqual(model.parsePayer(raw), { kind: 'user', id: '' }, raw);
  assert.equal(model.payerKey(model.parsePayer('organization:org_1')), 'organization:org_1');
  // The person's own wallet never names a user id: the server takes it from the session.
  assert.deepEqual(model.payerQuery({ kind: 'user', id: 'u_fixture' }), {});
  assert.deepEqual(model.payerQuery({ kind: 'organization', id: 'org_1' }), { payer_kind: 'organization', payer_id: 'org_1' });
});

test('the CSV export asks for exactly what the table shows', () => {
  const payer = { kind: 'organization', id: 'org_1' };
  const period = { from: '2026-09-01', to: '2026-09-30' };
  const filters = { model: 'deepseek-flash', state: 'settled' };
  const table = new URL(model.callsPath(payer, period, filters, 'Asia/Shanghai', '42'), 'http://x');
  const csv = new URL(model.csvPath(payer, period, filters, 'Asia/Shanghai'), 'http://x');
  assert.equal(csv.pathname, '/api/auth/wallet/calls.csv');
  for (const key of ['payer_kind', 'payer_id', 'from', 'to', 'tz', 'model', 'state']) assert.equal(csv.searchParams.get(key), table.searchParams.get(key), key);
  assert.equal(table.searchParams.get('cursor'), '42');
  assert.equal(csv.searchParams.has('cursor'), false);
  assert.equal(new URL(model.callsPath({ kind: 'user', id: '' }, period, {}, 'UTC'), 'http://x').searchParams.has('model'), false);
});

// ------------------------------------------------------------------ periods
test('periods are local calendar days and never exceed what the server accepts', () => {
  assert.deepEqual(model.presetPeriod('7d', '2026-03-03'), { from: '2026-02-25', to: '2026-03-03' });
  assert.deepEqual(model.presetPeriod('30d', '2026-10-05'), { from: '2026-09-06', to: '2026-10-05' });
  assert.deepEqual(model.presetPeriod('month', '2026-10-05'), { from: '2026-10-01', to: '2026-10-05' });
  assert.deepEqual(model.presetPeriod('last_month', '2026-03-31'), { from: '2026-02-01', to: '2026-02-28' });
  assert.deepEqual(model.presetPeriod('last_month', '2026-01-15'), { from: '2025-12-01', to: '2025-12-31' });
  assert.equal(model.periodDays({ from: '2024-01-01', to: '2024-12-31' }), 366);
  assert.equal(model.periodDays({ from: '2025-01-01', to: '2026-01-02' }), null);
  assert.equal(model.periodDays({ from: '2026-10-05', to: '2026-10-04' }), null);
  assert.equal(model.periodDays({ from: '2026-02-30', to: '2026-03-01' }), null);
  assert.equal(model.daysOf({ from: '2026-02-27', to: '2026-03-01' }).join(','), '2026-02-27,2026-02-28,2026-03-01');
  // "Today" is where the person is: 2026-10-04T20:00Z is already the 5th in Shanghai.
  assert.equal(model.localToday('Asia/Shanghai', new Date('2026-10-04T20:00:00Z')), '2026-10-05');
  assert.equal(model.localToday('America/Los_Angeles', new Date('2026-10-04T20:00:00Z')), '2026-10-04');
});

// ------------------------------------------------------------------ money
test('wallet amounts are exact decimals in every locale, as money for CNY and as a number for other units', () => {
  assert.equal(model.decimalText('9007199254740993123456', 6), '9007199254740993.123456');
  assert.equal(model.decimalText('-45', 4), '-0.0045');
  assert.equal(model.decimalText('1250', 2), '12.5');
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    const big = model.moneyText('9007199254740993123456', 6, 'CNY', locale);
    assert.ok(big.includes('9,007,199,254,740,993.123456'), `${locale}: ${big}`);
    assert.ok(/¥/.test(model.moneyText('1250', 2, 'CNY', locale)), locale);
    assert.ok(model.moneyText('1250', 2, 'CNY', locale).endsWith('12.50'), locale);
    assert.ok(model.moneyText('45', 4, 'CNY', locale).endsWith('0.0045'), locale);
    assert.ok(model.moneyText('-45', 4, 'CNY', locale, true).includes('-'), locale);
    assert.ok(model.moneyText('45', 4, 'CNY', locale, true).includes('+'), locale);
    assert.equal(model.moneyText('1500', 3, 'credits', locale), '1.5 credits');
  }
  for (const [units, scale] of [['1.5', 2], ['1e5', 2], ['01', 0], ['1', 19], ['1', -1]]) assert.throws(() => model.decimalText(units, scale), RangeError);
  assert.ok(model.paymentText(5000, 'CNY', 'en').endsWith('50.00'));
  assert.throws(() => model.paymentText(-1, 'CNY', 'en'), RangeError);
  assert.throws(() => model.paymentText(1.5, 'CNY', 'en'), RangeError);
  assert.equal(model.minorFromText('12.5', 2), 1250);
  assert.equal(model.minorFromText('12', 2), 1200);
  for (const text of ['12.555', '-1', '1e3', '', '01', ' 12,5']) assert.equal(model.minorFromText(text, 2), null, text);
});

// ------------------------------------------------------------------ chart geometry
test('daily bars draw every day of the period, a missing day as a real zero, lengths from integers', () => {
  const summary = { group: 'day', period: { from: '2026-10-01', to: '2026-10-04' }, buckets: [
    { key: '2026-10-02', unit: 'CNY', scale: 6, calls: 2, charged_minor: '300', held_minor: '0', tokens: {} },
    { key: '2026-10-04', unit: 'CNY', scale: 6, calls: 1, charged_minor: '900', held_minor: '0', tokens: {} },
    { key: '2026-10-04', unit: 'credits', scale: 0, calls: 1, charged_minor: '7', held_minor: '0', tokens: {} },
  ] };
  const bars = model.chartBars(summary, 'CNY', { other: 'other', untitled: 'untitled' });
  assert.deepEqual(bars.map(bar => [bar.key, bar.value, bar.ratio, bar.calls]),
    [['2026-10-01', '0', 0, 0], ['2026-10-02', '300', 0.333, 2], ['2026-10-03', '0', 0, 0], ['2026-10-04', '900', 1, 1]]);
});

test('model, conversation and member bars keep the server order and sum the rest into one honest bar', () => {
  const buckets = Array.from({ length: 14 }, (_, i) => ({ key: `m${i}`, title: i === 1 ? null : `Name ${i}`, unit: 'CNY', scale: 2,
    calls: 1, charged_minor: String(100 - i), held_minor: '0', tokens: {} }));
  const bars = model.chartBars({ group: 'model', period: { from: '2026-10-01', to: '2026-10-01' }, buckets }, 'CNY', { other: 'Other', untitled: 'Untitled' });
  assert.equal(bars.length, 12);
  assert.equal(bars[0].label, 'Name 0');
  assert.equal(bars[1].label, 'm1');
  const rest = buckets.slice(11).reduce((sum, b) => sum + Number(b.charged_minor), 0);
  assert.deepEqual([bars[11].key, bars[11].label, bars[11].value, bars[11].calls], ['__other__', 'Other', String(rest), 3]);
});

// ------------------------------------------------------------------ top-up decisions
test('top-up is offered only when the server says so; a missing top-up service is the same honest closed state', () => {
  const open = { available: true, currency: 'CNY', payment_methods: ['card'], packages: [{ id: 'p1', amount_minor: 5000, label: '50' }], min_amount_minor: 100, max_amount_minor: 100000 };
  assert.equal(model.topupClosedReason(open, null), null);
  for (const payment_methods of [[], undefined, null, ['unknown']])
    assert.equal(model.topupClosedReason({ ...open, payment_methods }, null), 'not_configured');
  assert.equal(model.topupClosedReason({ ...open, available: false, reason: 'not_configured', packages: [] }, null), 'not_configured');
  assert.equal(model.topupClosedReason({ ...open, available: false, reason: null }, null), 'not_configured');
  assert.equal(model.topupClosedReason({ ...open, available: false, reason: 'email_unverified' }, null), 'email_unverified');
  assert.equal(model.topupClosedReason(null, 404), 'not_configured');
  assert.equal(model.topupClosedReason(null, 503), null, 'any other failure is shown as an error, not as "closed"');
  assert.equal(model.topupClosedReason(null, null), null, 'still loading');
  assert.equal(topup.customAllowed(open), true);
  assert.equal(topup.customAllowed({ ...open, custom_amount: false }), false);
  assert.equal(topup.customAllowed({ ...open, min_amount_minor: null }), false);
  assert.equal(topup.customAllowed({ ...open, min_amount_minor: 500, max_amount_minor: 100 }), false);
});

test('the checkout address must be https without credentials; anything else is never followed', () => {
  assert.equal(model.safeCheckout('https://checkout.stripe.com/c/pay/cs_test_fixture'), 'https://checkout.stripe.com/c/pay/cs_test_fixture');
  for (const url of ['http://checkout.example/x', 'javascript:alert(1)', 'data:text/html,x', 'https://user:pw@checkout.example/', '/relative', '', null, 42])
    assert.equal(model.safeCheckout(url), null, String(url));
});

test('the return page shows the provider’s final answer first, then a slow confirmation, then a left checkout', () => {
  assert.equal(model.topupView(undefined, false, false), 'pending');
  assert.equal(model.topupView('pending', false, true), 'cancelled');
  assert.equal(model.topupView('pending', true, true), 'slow');
  assert.equal(model.topupView('paid', true, true), 'paid');
  assert.equal(model.topupView('expired', false, true), 'expired');
  assert.equal(model.topupFinal('pending'), false);
  for (const final of ['paid', 'failed', 'expired']) assert.equal(model.topupFinal(final), true);
  const delays = Array.from({ length: 8 }, (_, i) => model.pollDelay(i));
  assert.ok(delays.every((d, i) => d >= 1500 && d <= 10_000 && (i === 0 || d >= delays[i - 1])), delays.join(','));
  assert.ok(model.POLL_LIMIT_MS > 0 && model.POLL_LIMIT_MS <= 5 * 60_000);
});

test('an unknown create result stays distinct after polling stops or a cancelled return, until an actual terminal state', () => {
  for (const slow of [false, true]) for (const cancelled of [false, true]) {
    assert.equal(model.topupView('pending', slow, cancelled, 'verifying'), 'verifying');
    assert.equal(model.topupFinal('pending'), false);
  }
  // GET status uses resolved for an existing session even while payment is still pending.
  assert.equal(model.topupView('pending', false, false, 'resolved'), 'pending');
  assert.equal(model.topupView('pending', true, false, 'resolved'), 'slow');
  for (const final of ['paid', 'failed', 'expired'])
    assert.equal(model.topupView(final, true, true, 'resolved'), final);
});

test('an unknown ledger entry kind is named by its direction, never shown as a raw code', () => {
  assert.equal(model.entryKindLabel('sale', '-5'), '已结算消费');
  assert.equal(model.entryKindLabel('future_kind', '5'), '其他入账');
  assert.equal(model.entryKindLabel('future_kind', '-5'), '其他扣减');
});

// ------------------------------------------------------------------ rendering
test('closed top-up says why and offers no way to pay', () => {
  const html = render(h(topup.TopupClosed, { reason: 'not_configured' }));
  assert.ok(html.includes('充值暂未开放'), html);
  assert.ok(html.includes('role="status"'));
  assert.ok(!/<button|<form|<input|checkout/i.test(html), html);
  const unknown = render(h(topup.TopupClosed, { reason: 'something_new' }));
  assert.ok(unknown.includes('充值暂未开放'), 'an unknown reason falls back to the honest closed sentence');
  assert.ok(render(h(topup.TopupClosed, { reason: 'email_unverified' })).includes('href="/account#email"'));
});

test('top-up packages are the server’s, as labelled radio choices; an own amount only inside published bounds', () => {
  const options = { available: true, currency: 'CNY', packages: [{ id: 'p10', amount_minor: 1000, label: 'Fixture 10' }, { id: 'p50', amount_minor: 5000, label: 'Fixture 50' }],
    min_amount_minor: 100, max_amount_minor: 200000 };
  const html = render(h(topup.TopupChoices, { options, choice: 'p50', custom: '', onChoice() {}, onCustom() {} }));
  assert.ok(html.includes('<fieldset') && html.includes('<legend'), html);
  assert.equal((html.match(/type="radio"/g) || []).length, 3);
  assert.ok(html.includes('Fixture 10') && html.includes('10.00') && html.includes('50.00'));
  assert.ok(/value="p50"[^>]*checked=""|checked=""[^>]*value="p50"/.test(html), html);
  const fixed = render(h(topup.TopupChoices, { options: { ...options, min_amount_minor: null, max_amount_minor: null }, choice: 'p10', custom: '', onChoice() {}, onCustom() {} }));
  assert.equal((fixed.match(/type="radio"/g) || []).length, 2, 'no free amount without server bounds');
  const custom = render(h(topup.TopupChoices, { options, choice: 'custom', custom: '0.5', onChoice() {}, onCustom() {} }));
  assert.ok(custom.includes('aria-invalid="true"'), 'an amount below the server minimum is marked invalid');
  assert.ok(custom.includes('1.00') && custom.includes('2,000.00'), custom);
});

test('a too-frequent top-up refusal says when to try again', async () => {
  const { ApiError } = await server.ssrLoadModule('@synwevo/auth-client');
  const t = (s, p) => ui.translate('en', s, p);
  const limited = new ApiError(429, 'too_many_topups', 'fixture', undefined);
  limited.retryAfter = 30;
  assert.equal(topup.topupErrorText(limited, t), 'Too many top-up attempts. Try again in 30 seconds.');
  limited.retryAfter = undefined;
  assert.equal(topup.topupErrorText(limited, t), 'Too many top-up attempts. Try again later.');
  assert.equal(topup.topupErrorText(new Error('checkout_address'), t), 'The payment page address was not valid, so you were not redirected. Try again later.');
});

test('the return page: paid links the receipt and draws the success mark; failed offers a new top-up instead', () => {
  const base = { error: null, canRefresh: false, onRefresh() {}, onDismiss() {}, onRetry() {} };
  const order = { order_id: 'ord_fixture', status: 'paid', amount_minor: 5000, currency: 'CNY' };
  const paid = render(h(status.TopupStatusView, { ...base, view: 'paid', status: order }));
  assert.ok(paid.includes('href="/account/wallet/receipts/ord_fixture"'), paid);
  assert.ok(paid.includes('topup-mark paid') && paid.includes('<circle'));
  assert.ok(paid.includes('充值已到账') && paid.includes('50.00') && paid.includes('ord_fixture'));
  assert.ok(paid.includes('tabindex="-1"'), 'the outcome heading can take focus');
  const failed = render(h(status.TopupStatusView, { ...base, view: 'failed', status: { ...order, status: 'failed' } }));
  assert.ok(!failed.includes('/receipts/'), 'no receipt for an unpaid order');
  assert.ok(failed.includes('重新充值') && failed.includes('付款没有完成'));
  const waiting = render(h(status.TopupStatusView, { ...base, view: 'pending', status: null }));
  assert.ok(waiting.includes('aria-live="polite"') && waiting.includes('topup-spinner'));
  assert.ok(waiting.includes('稍后再看') && !waiting.includes('/receipts/'));
  const slow = render(h(status.TopupStatusView, { ...base, view: 'slow', status: { ...order, status: 'pending' }, canRefresh: true }));
  assert.ok(slow.includes('刷新状态') && slow.includes('不需要重复付款'));
});

test('the original order card describes unknown creation honestly without a time promise or another payment action', () => {
  const order = { order_id: 'top_unknown_fixture', status: 'pending', creation_status: 'verifying', amount_minor: 1000, currency: 'CNY' };
  const base = { error: null, onRefresh() {}, onDismiss() {}, onRetry() {} };
  for (const slow of [false, true]) {
    const view = model.topupView(order.status, slow, true, order.creation_status);
    const html = render(h(status.TopupStatusView, { ...base, view, status: order, canRefresh: slow }));
    assert.match(html, /充值请求结果待核实/);
    assert.match(html, /原充值请求与订单已保留/);
    assert.match(html, /可能需要管理员核实/);
    assert.match(html, /top_unknown_fixture/);
    assert.match(html, /aria-live="polite"/);
    assert.doesNotMatch(html, /通常只需几秒|正在确认付款|充值已到账|重新充值|查看收据|topup-spinner/);
    assert.equal(html.includes('刷新状态'), slow);
  }
  const { title, body } = status.TOPUP_TEXT.verifying;
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    assert.ok(ui.hasMessage(title) && ui.hasMessage(body));
    const translated = ui.translate(locale, title) + ' ' + ui.translate(locale, body);
    assert.doesNotMatch(translated, /几秒|幾秒|seconds|Confirming your payment/);
    if (locale === 'en') {
      assert.match(translated, /original top-up request and order/);
      assert.match(translated, /administrator/);
      assert.doesNotMatch(translated, /[\u3400-\u9fff]/);
    }
  }
});

test('balance figures: the ledger’s four numbers, the test-environment notice and a frozen wallet', () => {
  const overview = { environment: 'test', payer: { kind: 'user', id: 'u_fixture' }, payer_user_id: 'u_fixture', used_through_seq: '9',
    balances: [{ unit: 'CNY', scale: 4, booked: '120000', held: '2500', used: '3000', available: '117500', frozen: true }] };
  const html = render(h(wallet.BalanceFigures, { overview }));
  assert.ok(html.includes('测试环境'), html);
  assert.ok(html.includes('11.75') && html.includes('12.00') && html.includes('0.25') && html.includes('0.30'), html);
  assert.ok(html.includes('已被冻结'));
  const live = render(h(wallet.BalanceFigures, { overview: { ...overview, environment: 'live', balances: [{ ...overview.balances[0], frozen: undefined }] } }));
  assert.ok(!live.includes('测试环境') && !live.includes('已被冻结'));
});

test('the calls table shows band, price version, token breakdown and settled cost; an organization sees members, not conversations', () => {
  const row = { id: 'call_fixture', created_at: 1_790_000_000, model: 'deepseek-flash', band: 'off_peak', price_version: 'price_fixture_v2',
    state: 'settled', unit: 'CNY', scale: 6, charged_minor: '4321', held_minor: '0',
    tokens: { input_cached: 1200, input_uncached: 34, output: 560, reasoning: 120 }, finish_reason: 'length',
    dispatched_at: 1_790_000_000, completed_at: 1_790_000_007, duration_s: 7, conversation_id: 'conv_fixture', conversation_title: 'Fixture conversation' };
  const personal = render(h(usage.CallsTable, { items: [row, { ...row, id: 'c2', band: null, state: 'held', held_minor: '900000', tokens: null, finish_reason: null, duration_s: null, conversation_title: null }], organization: false, timezone: 'UTC' }));
  for (const text of ['deepseek-flash', '低谷时段', 'price_fixture_v2', '1,200', '560', '120', '0.004321', '达到长度上限', '7 秒', 'Fixture conversation', '<caption', 'scope="col"'])
    assert.ok(personal.includes(text), `${text} in ${personal}`);
  assert.ok(personal.includes('时段未记录') && personal.includes('已删除的对话') && personal.includes('预留 '), 'unknown facts are named, not guessed');
  // Seven columns scroll sideways on a 390px phone: the scroller is a named region a keyboard can focus and scroll.
  assert.match(personal, /^<div class="tbl-wrap usage-calls" tabindex="0" role="region" aria-label="每次调用">/);
  const org = render(h(usage.CallsTable, { items: [{ ...row, conversation_id: null, conversation_title: undefined, member_user_id: 'u_m', member_name: 'Member Fixture' }], organization: true, timezone: 'UTC' }));
  assert.ok(org.includes('Member Fixture') && org.includes('>成员<') && !org.includes('>对话<'), org);
});

test('the daily chart is an image with a summary, one column per day; other groupings are labelled rows', () => {
  const bars = model.chartBars({ group: 'day', period: { from: '2026-10-01', to: '2026-10-03' }, buckets: [
    { key: '2026-10-02', unit: 'CNY', scale: 2, calls: 1, charged_minor: '150', held_minor: '0', tokens: {} }] }, 'CNY', { other: 'o', untitled: 'u' });
  const html = render(h(chart.default, { bars, unit: 'CNY', scale: 2, locale: 'en', daily: true, summary: 'Fixture summary', empty: 'Nothing' }));
  assert.ok(html.includes('role="img"') && html.includes('aria-labelledby') && html.includes('Fixture summary'), html);
  assert.equal((html.match(/<rect /g) || []).length, 3);
  assert.ok(html.includes('2026-10-02 · CN¥1.50 · 1'));
  const empty = render(h(chart.default, { bars: bars.map(bar => ({ ...bar, value: '0', ratio: 0 })), unit: 'CNY', scale: 2, locale: 'en', daily: true, summary: 's', empty: 'Nothing settled' }));
  assert.ok(empty.includes('Nothing settled') && !empty.includes('<svg'), 'no empty chart pretending to show data');
  const rows = render(h(chart.default, { bars: [{ key: 'm', label: 'deepseek-flash', value: '150', ratio: 1, calls: 1 }], unit: 'CNY', scale: 2, locale: 'en', daily: false, summary: 'Rows summary', empty: 'x' }));
  assert.ok(rows.includes('deepseek-flash') && rows.includes('Rows summary') && rows.includes('aria-hidden="true"'));
});

test('a receipt lists only the server’s fields and states the closed-use, no-refund terms', () => {
  const data = { order_id: 'ord_fixture', status: 'paid', amount_minor: 5000, currency: 'CNY', method: 'wechat_pay', credited_at: 1_790_000_000,
    paid_at: null, provider_reference: null, operator_name: null, payer_kind: 'user' };
  const html = render(h(receipt.ReceiptView, { data, timezone: 'UTC', onDownload() {} }));
  assert.ok(html.includes('ord_fixture') && html.includes('50.00') && html.includes('微信支付') && html.includes('个人钱包'), html);
  assert.ok(!html.includes('付款时间') && !html.includes('支付服务商流水号') && !html.includes('收款方'), 'absent fields are left out, not shown empty');
  assert.ok(html.includes('充值后不可退款'));
  const t = s => s;
  assert.deepEqual(receipt.receiptLines(data, t, 'en', 'UTC').map(line => line.label), ['订单号', '金额', '付款方式', '入账时间', '付款方']);
  const text = model.receiptText('Title', [{ label: 'A', value: '1' }, { label: 'B', value: '' }]);
  assert.equal(text, 'Title\r\n\r\nA: 1\r\n');
});

test('the wallet’s own pages lead back to the wallet; the wallet leads back to the account', () => {
  assert.deepEqual(topbar.parentOf('/account/wallet/usage'), { href: '/account/wallet', label: '钱包' });
  assert.deepEqual(topbar.parentOf('/account/wallet/receipts/ord_1'), { href: '/account/wallet', label: '钱包' });
  assert.deepEqual(topbar.parentOf('/account/wallet'), { href: '/account', label: '账户与安全' });
});

// ------------------------------------------------------------------ a deployment without the wallet
test('without the wallet the bar draws no wallet entry, and the wallet pages say plainly it is not offered', () => {
  // The session's deployment state not known, or known without the wallet: no link to a page that cannot work.
  window.matchMedia ??= () => ({ matches: false, addEventListener() {}, removeEventListener() {} });   // fixture: a wide screen
  const bar = render(h(topbar.Topbar, { title: 'Fixture' }));
  assert.ok(bar.includes('href="/account') && !bar.includes('/account/wallet'), bar);
  assert.equal(gate.walletOffered(null), false);
  assert.equal(gate.walletOffered({}), false);
  assert.equal(gate.walletOffered({ wallet_offered: false }), false);
  assert.equal(gate.walletOffered({ wallet_offered: true }), true);
  const page = render(h(gate.WalletNotOffered));
  assert.ok(page.includes('这个部署没有提供付费 Agent 钱包') && page.includes('href="/account"'), page);
  assert.ok(!page.includes('重试') && !page.includes('稍后') && !page.includes('<button'), 'a deliberate off state is not an outage: no retry');
  for (const [locale, sentence] of [['en', 'This deployment does not offer the paid Agent wallet.'], ['zh-Hant', '這個部署沒有提供付費 Agent 錢包']])
    assert.equal(ui.translate(locale, '这个部署没有提供付费 Agent 钱包'), sentence);
});

// ------------------------------------------------------------------ three languages
const SOURCES = ['Wallet.tsx', 'WalletGate.tsx', 'Usage.tsx', 'UsageChart.tsx', 'TopupDialog.tsx', 'TopupStatus.tsx', 'Receipt.tsx', 'WalletSettings.tsx', 'walletModel.ts']
  .map(name => `src/pages/account/${name}`);
test('every sentence on the wallet pages exists in Traditional Chinese and English', () => {
  const missing = [];
  for (const file of SOURCES) {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '');
    for (const match of source.matchAll(/'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"/g)) {
      const text = match[1] ?? match[2];
      if (!/[一-鿿]/.test(text)) continue;
      // Registered (so zh-Hant has its own form, even when it is written the same) and English.
      if (!ui.hasMessage(text)) missing.push(`${file}: ${text}`);
      else if (/[一-鿿]/.test(ui.translate('en', text))) missing.push(`${file} en: ${text}`);
    }
  }
  assert.deepEqual(missing, []);
  // The bar's entry and the wallet pages' way up.
  for (const [locale, text] of [['en', 'Wallet'], ['zh-Hant', '錢包']]) assert.equal(ui.translate(locale, '钱包'), text);
});
