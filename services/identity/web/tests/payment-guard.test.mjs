/** The administrator's side of a refused top-up: the publish check against the provider (names each
 *  refused method, deselects them, or accepts unverified methods explicitly) and the finance view's
 *  stored failure reasons. Isolated component fixtures: no browser, backend, credentials or payment;
 *  every server answer below is a fixture standing for the contract's shape. */
import { before, after, afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { testServer } from './_vite.mjs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const fixtureModule = 'virtual:payment-guard-fixture';
const fixtureRuntime = `
import * as React from 'react';
import { t, getLocale } from '@synwevo/ui';
export { fmtTime } from '@synwevo/ui';
export { ApiError } from '@synwevo/auth-client';
export const useI18n = () => ({ t, locale: getLocale() });
export const fixture = { writes: [], answer: null, load: null };
export function get() { throw new Error('Unexpected read in payment guard fixture'); }
export async function post(path, body) {
  fixture.writes.push({ path, body: JSON.parse(JSON.stringify(body)) });
  const answer = fixture.answer;
  if (answer instanceof Error) throw answer;
  return answer;
}
export const useAdminData = () => fixture.load;
let active;
export function withHooks(h, render) {
  const previous = active; active = h; h.index = 0;
  try { return render(); } finally { active = previous; }
}
export function useState(initial) {
  if (!active) return React.useState(initial);
  const h = active, i = h.index++;
  if (!(i in h.slots)) h.slots[i] = typeof initial === 'function' ? initial() : initial;
  return [h.slots[i], next => { h.slots[i] = typeof next === 'function' ? next(h.slots[i]) : next; }];
}
export function useRef(initial) { return active ? useState(() => ({ current: initial }))[0] : React.useRef(initial); }
export function useId() { return active ? useRef('fixture-' + active.index).current : React.useId(); }
export function useEffect(run, deps) { if (!active) return React.useEffect(run, deps); active.index++; }
`;

let server, support, fixture, payments, finance, ui, client;
before(async () => {
  server = await testServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'payment-guard-fixtures', enforce: 'pre',
      resolveId(id) { if (id === fixtureModule) return '\0' + fixtureModule; },
      load(id) { if (id === '\0' + fixtureModule) return fixtureRuntime; },
      transform(code, id) {
        if (!/\/governance\/(PaymentsTab|FinanceTab)\.tsx$/.test(id)) return;
        return code.replace(/from "(react|@synwevo\/ui|@synwevo\/auth-client|\.\/data)"/g, `from "${fixtureModule}"`);
      },
    }],
  });
  support = await server.ssrLoadModule(fixtureModule);
  fixture = support.fixture;
  payments = await server.ssrLoadModule('/src/governance/PaymentsTab.tsx');
  finance = await server.ssrLoadModule('/src/governance/FinanceTab.tsx');
  ui = await server.ssrLoadModule('@synwevo/ui');
  client = await server.ssrLoadModule('@synwevo/auth-client');
});
after(async () => { await server?.close(); });
afterEach(() => { ui.setLocale('zh-Hans'); fixture.writes = []; fixture.answer = null; });

function nodes(tree) {
  return React.Children.toArray(tree).flatMap(node => React.isValidElement(node) ? [node, ...nodes(node.props.children)] : []);
}
const han = /[㐀-鿿]/;
const spec = (overrides = {}) => ({ enabled: true, operator_name: 'Fixture merchant', payment_methods: ['card', 'wechat_pay', 'alipay'],
  packages: [{ id: 'fixture-10', amount_minor: 1000, label: 'Fixture' }], custom_amount: false, min_amount_minor: 1000,
  max_amount_minor: 1000, velocity: { max_orders_per_hour: 5, max_orders_per_day: 20, max_pending: 3, max_amount_per_day_minor: 100000 },
  ...overrides });
const saved = (overrides = {}) => ({ environment: 'test', revision: 3, spec: spec(), credential_configured: true, status: 'configured',
  wallet_unit: { code: 'CNY', scale: 2 }, webhook_url: 'https://fixture.invalid/webhook', webhook_events: [], created_at: 1_790_000_000,
  reason: null, open_checkouts: { count: 0, until: null }, ...overrides });
const refusal = (status, code, detail) => Object.assign(new client.ApiError(status, code, 'fixture server sentence', detail), { requestId: 'fixture-request' });

/** The payment settings tab with a saved, enabled revision, reviewed into its publish confirmation. */
function confirmation(data = saved()) {
  fixture.load = { data, loading: false, err: null, reload() {} };
  const h = { slots: [], index: 0 };
  const view = {
    tree: null,
    draw() { this.tree = support.withHooks(h, () => payments.PaymentsTab({ canManage: true, onDenied() {} })); return this.tree; },
    get dialog() { return nodes(this.tree).find(node => node.props?.title === '发布支付设置'); },
    get guard() { return nodes(this.dialog?.props.children).find(node => node.type === payments.PublishGuard); },
  };
  view.draw();
  nodes(view.tree).find(node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  view.draw();
  assert.ok(view.dialog, 'review opens the publish confirmation');
  return view;
}

test('a refusal is read only from the account service, and only methods this page knows are named', () => {
  assert.deepEqual(payments.publishRefusal(refusal(409, 'payment_methods_unavailable', { methods: ['alipay', 'future_channel', 'wechat_pay'] })),
    { kind: 'methods', methods: ['wechat_pay', 'alipay'] });
  assert.equal(payments.publishRefusal(refusal(409, 'payment_methods_unavailable', { methods: 'wechat_pay' })), null);
  assert.equal(payments.publishRefusal(refusal(409, 'payment_methods_unavailable')), null);
  assert.deepEqual(payments.publishRefusal(refusal(503, 'payment_readiness_unavailable', { acknowledgement: 'acknowledge_unverified_methods' })),
    { kind: 'unverified', cause: 'unavailable', methods: [], chargesDisabled: false });
  assert.deepEqual(payments.publishRefusal(refusal(409, 'payment_credentials_rejected')), { kind: 'rejected' });
  assert.deepEqual(payments.publishRefusal(refusal(409, 'payment_credentials_forbidden', { acknowledgement: 'acknowledge_unverified_methods' })),
    { kind: 'unverified', cause: 'forbidden', methods: [], chargesDisabled: false });
  assert.deepEqual(payments.publishRefusal(refusal(409, 'payment_methods_unverified',
    { acknowledgement: 'acknowledge_unverified_methods', methods: ['alipay', 'future_channel', 'card'], charges_enabled: false })),
    { kind: 'unverified', cause: 'inconclusive', methods: ['card', 'alipay'], chargesDisabled: true });
  const upstream = new client.ApiError(503, 'payment_readiness_unavailable', 'fixture', {}, true);
  for (const other of [upstream, refusal(409, 'payment_revision_conflict'), new TypeError('fixture'), null,
    // an acknowledgeable 409 must say so in its detail; a rejected key is never acknowledgeable
    refusal(409, 'payment_credentials_forbidden'), refusal(409, 'payment_methods_unverified', { methods: ['card'] }),
    refusal(503, 'payment_credentials_rejected')])
    assert.equal(payments.publishRefusal(other), null);
});

test('refused methods are named one by one, deselecting them confirms the remaining spec afresh, and no field is invented', async () => {
  const view = confirmation();
  const first = view.dialog;
  assert.match(renderToStaticMarkup(first.props.impact), /发布前，服务器会向支付服务商核实所选支付方式/);
  assert.equal(renderToStaticMarkup(view.guard), '', 'nothing to show before the server answers');
  fixture.answer = refusal(409, 'payment_methods_unavailable', { methods: ['wechat_pay', 'alipay'] });
  await assert.rejects(first.props.run('fixture publish reason'), error => error === fixture.answer);
  assert.equal(fixture.writes.length, 1);
  const sent = fixture.writes[0];
  assert.equal(sent.path, '/api/admin/billing/payments/publish');
  assert.deepEqual(sent.body.spec.payment_methods, ['card', 'wechat_pay', 'alipay']);
  assert.equal('acknowledge_unverified_methods' in sent.body, false);
  assert.deepEqual(sent.body.credential, { action: 'keep' }, 'kept keys are never resent');
  view.draw();
  assert.deepEqual(view.guard.props.blocked, ['wechat_pay', 'alipay']);
  for (const [locale, names] of [['zh-Hans', ['微信支付', '支付宝']], ['zh-Hant', ['微信支付', '支付寶']], ['en', ['WeChat Pay', 'Alipay']]]) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(view.guard);
    assert.equal((html.match(/<li>/g) || []).length, 2, html);
    for (const name of names) assert.ok(html.includes(name), `${locale}: ${name}`);
    assert.doesNotMatch(html, /wechat_pay|alipay|payment_methods_unavailable|\{method\}/);
    if (locale === 'en') { assert.doesNotMatch(html, han); assert.match(html, /Deselect these payment methods/); }
  }
  ui.setLocale('zh-Hans');
  const deselect = nodes(view.guard.type(view.guard.props)).find(node => node.type === 'button');
  assert.equal(deselect.props.children, '取消选择这些支付方式');
  deselect.props.onClick();
  view.draw();
  const second = view.dialog;
  assert.notEqual(second.key, first.key, 'a fresh confirmation for a different request');
  assert.deepEqual(second.props.impact && nodes(second.props.impact).find(node => node.type === payments.PaymentMethodSummary).props.spec.payment_methods, ['card']);
  assert.deepEqual(nodes(view.tree).find(node => node.type === payments.PaymentMethodChoices).props.selected, ['card'], 'the form shows the deselection');
  assert.deepEqual(view.guard.props.blocked, []);
  fixture.answer = { operation_id: 'ao_fixture', status: 'succeeded', methods_check: 'verified' };
  await second.props.run('fixture publish reason');
  const resent = fixture.writes[1];
  assert.deepEqual(resent.body.spec.payment_methods, ['card']);
  assert.notEqual(resent.body.idempotency_key, sent.body.idempotency_key);
  assert.equal(resent.body.expected_revision, 3);
});

test('deselecting every selected method returns to the form, which says what is missing', async () => {
  const view = confirmation(saved({ spec: spec({ payment_methods: ['wechat_pay'] }) }));
  fixture.answer = refusal(409, 'payment_methods_unavailable', { methods: ['wechat_pay'] });
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  view.draw();
  nodes(view.guard.type(view.guard.props)).find(node => node.type === 'button').props.onClick();
  view.draw();
  assert.equal(view.dialog, undefined);
  assert.match(renderToStaticMarkup(view.tree), /请至少选择一种支付方式。/);
});

test('a check that could not run is published only with the explicit acknowledgement, which is sent as the named field', async () => {
  const view = confirmation();
  fixture.answer = refusal(503, 'payment_readiness_unavailable', { acknowledgement: 'acknowledge_unverified_methods' });
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  view.draw();
  assert.equal(view.guard.props.unverified.cause, 'unavailable');
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(view.guard);
    assert.match(html, /type="checkbox"/);
    assert.doesNotMatch(html, /checked=""/, 'never pre-acknowledged');
    if (locale === 'en') { assert.doesNotMatch(html, han); assert.match(html, /publish anyway/); }
  }
  ui.setLocale('zh-Hans');
  await assert.rejects(view.dialog.props.run('fixture publish reason'), 'a retry without the acknowledgement is checked again');
  assert.equal('acknowledge_unverified_methods' in fixture.writes[1].body, false);
  view.draw();
  view.guard.props.onAcknowledge(true);
  view.draw();
  fixture.answer = { operation_id: 'ao_fixture', status: 'succeeded', methods_check: 'unverified_acknowledged' };
  const result = await view.dialog.props.run('fixture publish reason');
  assert.equal(fixture.writes[2].body.acknowledge_unverified_methods, true);
  view.dialog.props.onDone(result);
  view.draw();
  assert.equal(view.dialog, undefined);
  ui.setLocale('en');
  const done = renderToStaticMarkup(view.draw());
  assert.match(done, /not verified with the payment provider/);
});

test('a closed revision needs no provider check and says nothing about one', async () => {
  const view = confirmation(saved({ spec: spec({ enabled: false }) }));
  assert.doesNotMatch(renderToStaticMarkup(view.dialog.props.impact), /核实所选支付方式/);
  fixture.answer = { operation_id: 'ao_fixture', status: 'succeeded', methods_check: 'not_required' };
  await view.dialog.props.run('fixture publish reason');
  assert.equal('acknowledge_unverified_methods' in fixture.writes[0].body, false);
});

// ------------------------------------------------------------------ finance: stored reasons
const payment = (overrides = {}) => ({ order_id: 'top_fixture', payer_kind: 'user', payer_id: 'u_fixture', payer_email: 'fixture@example.test',
  organization_name: null, initiator_email: 'fixture@example.test', amount_minor: 1000, currency: 'CNY', status: 'failed', method: null,
  created_at: 1_790_000_000, expires_at: 1_790_003_600, credited_at: null, failure_code: null, failure_detail: null, failure_reason: null,
  ...overrides });
const financeData = payments => ({ environment: 'live', configured: true, unit: { code: 'CNY', scale: 2 },
  totals: { paid_minor: 0, paid_count: 0, welcome_units: 0, admin_grant_units: 0, recovered_units: 0, outstanding_units: 0,
    chargeback_count: 0, webhook_failures: 0, frozen_wallets: 0 },
  payments, grants: [], chargebacks: [], base_dates: [] });

test('the finance view shows why each top-up failed or is unresolved, with the sanitized diagnosis and never anything else', () => {
  const rows = [
    payment({ order_id: 'top_refused', failure_code: 'provider_refused', failure_reason: 'payment_method_not_enabled',
      failure_detail: 'http_400;type=invalid_request_error;code=parameter_invalid;param=payment_method_types' }),
    payment({ order_id: 'top_unknown', status: 'expired', failure_code: 'provider_unknown', failure_detail: 'http_503;type=api_error' }),
    payment({ order_id: 'top_local', failure_code: 'provider_refused', failure_reason: 'provider_configuration', failure_detail: 'local=payment_methods' }),
    payment({ order_id: 'top_future', failure_code: 'future_code', failure_detail: 'No such key: Bearer fixture' }),
    payment({ order_id: 'top_paid', status: 'paid', credited_at: 1_790_000_100 }),
  ];
  fixture.load = { data: financeData(rows), loading: false, err: null, reload() {} };
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(React.createElement(finance.FinanceTab, { canManage: false, onDenied() {} }));
    assert.ok(html.includes('http_400;type=invalid_request_error;code=parameter_invalid;param=payment_method_types'), html);
    assert.ok(html.includes('http_503;type=api_error') && html.includes('local=payment_methods'));
    assert.ok(html.includes('<code>future_code</code>'), 'an unknown code is shown as itself');
    assert.doesNotMatch(html, /Bearer|No such key/, 'only the documented diagnosis alphabet is shown');
    if (locale === 'zh-Hans') {
      assert.match(html, /支付服务拒绝创建付款页面 · 所选支付方式未在支付服务商处开通/);
      assert.match(html, /未能确认付款页面是否已创建/);
      assert.match(html, /支付密钥、权限或账户配置有误/);
    }
    if (locale === 'en') {
      assert.match(html, /The payment service refused to create the payment page · a selected payment method is not activated/);
      assert.match(html, /Whether the payment page was created is unknown/);
      const reasons = [...html.matchAll(/<div class="cell-note">(.*?)<\/div><\/td>/g)].map(match => match[1]).join(' ');
      assert.doesNotMatch(reasons, han, reasons);
    }
  }
  const paid = renderToStaticMarkup(React.createElement(finance.PaymentFailure, { payment: { failure_code: null, failure_detail: null } }));
  assert.equal(paid, '');
});

// ------------------------------------------------------------------ Stripe's answer about the key and an inconclusive answer
test('a key Stripe rejects is named as such, with no acknowledgement to tick and no claim that it is temporary (UX-2)', async () => {
  const view = confirmation();
  fixture.answer = refusal(409, 'payment_credentials_rejected');
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  view.draw();
  assert.equal(view.guard.props.rejected, true);
  assert.equal(view.guard.props.unverified, null);
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(view.guard);
    assert.doesNotMatch(html, /type="checkbox"/, 'nothing can override a rejected key');
    assert.doesNotMatch(html, /暂时|稍后|暫時|稍後|right now|try again later/i, html);
    if (locale === 'zh-Hans') assert.match(html, /Stripe 拒绝了这个密钥.*设置或轮换密钥/);
    if (locale === 'en') { assert.doesNotMatch(html, han); assert.match(html, /Stripe rejected this key.*Set or rotate keys/); }
  }
  ui.setLocale('zh-Hans');
  // Even a ticked box from an earlier refusal is not sent with a rejected key.
  view.guard.props.onAcknowledge(true); view.draw();
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  assert.equal('acknowledge_unverified_methods' in fixture.writes[1].body, false);
  // The server's own sentence is worded in every language (errText of the refusal).
  const sentence = 'Stripe 拒绝了这个密钥（密钥无效、已撤销或已过期），设置未发布。请在 Stripe 核对后重新设置密钥；这项拒绝不能通过确认跳过';
  assert.ok(ui.hasMessage(sentence));
  assert.doesNotMatch(ui.translate('en', sentence), han);
  assert.doesNotMatch(ui.translate('en', sentence), /temporar|later/i);
});

test('a key that may not read the account is acknowledged with wording about the key, not about unverified methods only', async () => {
  const view = confirmation();
  fixture.answer = refusal(409, 'payment_credentials_forbidden', { acknowledgement: 'acknowledge_unverified_methods' });
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  view.draw();
  assert.equal(view.guard.props.unverified.cause, 'forbidden');
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(view.guard);
    assert.match(html, /type="checkbox"/);
    if (locale === 'zh-Hans') assert.match(html, /权限受限的密钥[\s\S]*我知道这个密钥不能读取账户信息/);
    if (locale === 'en') { assert.doesNotMatch(html, han); assert.match(html, /restricted key[\s\S]*this key cannot read the account/); }
  }
  ui.setLocale('zh-Hans');
  view.guard.props.onAcknowledge(true); view.draw();
  fixture.answer = { operation_id: 'ao_fixture', status: 'succeeded', methods_check: 'unverified_acknowledged' };
  await view.dialog.props.run('fixture publish reason');
  assert.equal(fixture.writes[1].body.acknowledge_unverified_methods, true);
  assert.equal(fixture.writes[1].body.idempotency_key, fixture.writes[0].body.idempotency_key, 'the same publication');
});

test('an inconclusive answer names the unverified methods and a disabled account, and is acknowledged explicitly (SEC-1)', async () => {
  const view = confirmation();
  fixture.answer = refusal(409, 'payment_methods_unverified',
    { acknowledgement: 'acknowledge_unverified_methods', methods: ['wechat_pay', 'alipay'], charges_enabled: false });
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  view.draw();
  assert.deepEqual(view.guard.props.unverified, { kind: 'unverified', cause: 'inconclusive', methods: ['wechat_pay', 'alipay'], chargesDisabled: true });
  for (const [locale, names] of [['zh-Hans', ['微信支付', '支付宝']], ['zh-Hant', ['微信支付', '支付寶']], ['en', ['WeChat Pay', 'Alipay']]]) {
    ui.setLocale(locale);
    const html = renderToStaticMarkup(view.guard);
    assert.equal((html.match(/<li>/g) || []).length, 2, html);
    for (const name of names) assert.ok(html.includes(name), `${locale}: ${name}`);
    assert.match(html, /type="checkbox"/);
    assert.doesNotMatch(html, /checked=""/, 'never pre-acknowledged');
    assert.doesNotMatch(html, /wechat_pay|alipay|payment_methods_unverified|\{method\}/);
    if (locale === 'en') { assert.doesNotMatch(html, han); assert.match(html, /not enabled to accept payments/); }
  }
  ui.setLocale('zh-Hans');
  // A different reason later resets an earlier tick: an acknowledgement covers the reason it was given for.
  view.guard.props.onAcknowledge(true); view.draw();
  fixture.answer = refusal(409, 'payment_credentials_forbidden', { acknowledgement: 'acknowledge_unverified_methods' });
  await assert.rejects(view.dialog.props.run('fixture publish reason'));
  assert.equal(fixture.writes[1].body.acknowledge_unverified_methods, true);
  view.draw();
  assert.equal(view.guard.props.unverified.cause, 'forbidden');
  assert.equal(view.guard.props.acknowledged, false);
});
