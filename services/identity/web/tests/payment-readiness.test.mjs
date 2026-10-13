/** Isolated component fixtures: no browser, backend, credentials or payment requests.
 * The hook runner supplies React lifetimes and deferred API fixtures; the actual components,
 * translations, ApiError and error presenter are loaded through Vite. */
import { before, after, afterEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { testServer } from './_vite.mjs';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const fixtureModule = 'virtual:payment-readiness-fixture';
const fixtureRuntime = `
import * as React from 'react';
import { t, getLocale } from '@synwevo/ui';
export { fmtTime } from '@synwevo/ui';
export { ApiError } from '@synwevo/auth-client';
export const useI18n = () => ({ t, locale: getLocale() });
export const fixture = { reads: [], writes: [], load: null };
export function get(path, signal) {
  return new Promise((resolve, reject) => fixture.reads.push({ path, signal, resolve, reject }));
}
export function post(path, body) { fixture.writes.push({ path, body }); throw new Error('Unexpected write in readiness fixture'); }
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
  return [h.slots[i], next => {
    h.updates++;
    h.slots[i] = typeof next === 'function' ? next(h.slots[i]) : next;
  }];
}
export function useRef(initial) {
  if (!active) return React.useRef(initial);
  return useState(() => ({ current: initial }))[0];
}
export function useId() {
  if (!active) return React.useId();
  return useRef('fixture-' + active.index).current;
}
export function useEffect(run, deps) {
  if (!active) return React.useEffect(run, deps);
  const h = active, i = h.index++, prev = h.slots[i];
  if (!prev || !deps || deps.some((value, index) => !Object.is(value, prev.deps[index]))) {
    h.slots[i] = { deps, cleanup: prev?.cleanup };
    h.effects.push(() => { h.slots[i].cleanup?.(); h.slots[i].cleanup = run(); });
  }
}
`;

let server, support, fixture, readiness, payments, ui, client, labels, access;
before(async () => {
  server = await testServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'payment-readiness-fixtures', enforce: 'pre',
      resolveId(id) { if (id === fixtureModule) return '\0' + fixtureModule; },
      load(id) { if (id === '\0' + fixtureModule) return fixtureRuntime; },
      transform(code, id) {
        if (!/\/governance\/(PaymentReadiness|PaymentsTab)\.tsx$/.test(id)) return;
        return code.replace(/from "(react|@synwevo\/ui|@synwevo\/auth-client|\.\/data)"/g, `from "${fixtureModule}"`);
      },
    }],
  });
  support = await server.ssrLoadModule(fixtureModule);
  fixture = support.fixture;
  readiness = await server.ssrLoadModule('/src/governance/PaymentReadiness.tsx');
  payments = await server.ssrLoadModule('/src/governance/PaymentsTab.tsx');
  ui = await server.ssrLoadModule('@synwevo/ui');
  client = await server.ssrLoadModule('@synwevo/auth-client');
  labels = await server.ssrLoadModule('/src/governance/labels.ts');
  access = await server.ssrLoadModule('/src/governance/access.ts');
});
after(async () => { await server?.close(); });
const mounted = new Set();
afterEach(() => {
  for (const h of mounted) h.dispose();
  ui.setLocale('zh-Hans');
  assert.equal(fixture.writes.length, 0);
});

function hooks(render) {
  const h = { slots: [], effects: [], index: 0, updates: 0, tree: null,
    draw() { this.tree = support.withHooks(this, render); this.effects.splice(0).forEach(run => run()); return this.tree; },
    dispose() { this.slots.forEach(slot => slot?.cleanup?.()); mounted.delete(this); },
  };
  mounted.add(h); return h;
}
function nodes(tree) {
  return React.Children.toArray(tree).flatMap(node => React.isValidElement(node) ? [node, ...nodes(node.props.children)] : []);
}
const markup = tree => renderToStaticMarkup(tree);
const button = (tree, label) => nodes(tree).find(node => node.type === 'button' && node.props.children === label);
const methods = view => nodes(view.tree).filter(node => node.type === 'dd').map(node => node.props.children);
const statuses = ['默认配置可用', '默认配置不可用', '未能核实'];
const checkLabel = '检查已保存的支付渠道';
const response = (overrides = {}) => ({ revision: 7, credential_revision: 4, checked_at: 1790000000,
  provider: 'stripe', environment: 'test', charges_enabled: true,
  methods: { card: true, wechat_pay: false, alipay: null }, configuration_status: 'available', ...overrides });
const saved = (overrides = {}) => ({ revision: 7, environment: 'test', credential_configured: true, status: 'configured',
  wallet_unit: { code: 'CNY', scale: 100 }, webhook_url: 'https://fixture.invalid/webhook', webhook_events: [], created_at: 1790000000,
  spec: { enabled: false, operator_name: 'Fixture merchant', payment_methods: ['card', 'wechat_pay'],
    packages: [{ id: 'fixture', amount_minor: 1000, label: 'Fixture' }], custom_amount: false, min_amount_minor: 1000,
    max_amount_minor: 1000, velocity: { max_orders_per_hour: 5, max_orders_per_day: 20, max_pending: 3, max_amount_per_day_minor: 100000 } },
  ...overrides });

function screen(options = {}) {
  fixture.reads = []; fixture.writes = []; ui.setLocale('zh-Hans');
  const props = { revision: 7, savedMethods: ['card', 'wechat_pay'], hasDraft: false, ...options.props };
  fixture.load = { data: options.saved ?? saved(), loading: false, err: null, reload() {} };
  const parent = options.parent ? hooks(() => payments.PaymentsTab({ canManage: !!options.canManage, onDenied() {} })) : null;
  let child, element;
  const view = { props, parent,
    get tree() { return child?.tree; },
    get instance() { return child; },
    draw() {
      parent?.draw();
      const nextProps = parent ? nodes(parent.tree).find(node => node.type === readiness.PaymentReadiness)?.props : props;
      const next = nextProps ? readiness.PaymentReadiness(nextProps) : null;
      if (!next || next.key !== element?.key || next.type !== element?.type) { child?.dispose(); child = null; }
      element = next;
      if (element) {
        child ??= hooks(() => element.type(element.props));
        child.draw();
      }
      return this.tree;
    },
    async flush() { await new Promise(resolve => setImmediate(resolve)); this.draw(); },
    html() { return markup(this.tree); },
    async click(label = checkLabel) {
      const b = button(this.tree, label); assert.ok(b, label); assert.ok(!b.props.disabled, label);
      b.props.onClick(); await this.flush();
    },
  };
  view.draw(); return view;
}

test('billing-view readers can manually check saved credentials while top-ups are closed', async () => {
  assert.equal(access.TAB_PERMISSION.payments, 'platform.billing.view');
  assert.ok(access.visibleTabs(new Set(['platform.billing.view'])).includes('payments'));
  assert.ok(!access.visibleTabs(new Set(['platform.billing.manage'])).includes('payments'));
  const view = screen({ parent: true, canManage: false });
  assert.equal(nodes(view.parent.tree).filter(node => node.type === 'form').length, 0);
  assert.equal(fixture.reads.length, 0, 'mount must not query readiness');
  await view.click();
  assert.equal(fixture.reads.length, 1);
  assert.equal(fixture.reads[0].path, '/api/admin/billing/payments/readiness');
  assert.ok(fixture.reads[0].signal instanceof AbortSignal);
  assert.equal(button(view.tree, checkLabel).props['aria-disabled'], true);
  assert.ok(nodes(view.tree).some(node => node.props.role === 'status'));
  fixture.reads[0].resolve(response()); await view.flush();
  assert.deepEqual(methods(view), statuses);
  const warnings = nodes(view.tree).filter(node => node.props.role === 'note').map(node => markup(node)).join('');
  assert.match(warnings, /微信支付/);
  assert.doesNotMatch(warnings, /支付宝/, 'unknown is not unavailable');
  await view.flush(); ui.setLocale('en'); await view.flush();
  assert.equal(fixture.reads.length, 1, 'rendering and language changes do not poll');
});

test('cancelled reads and their failures cannot overwrite a newer result', async () => {
  for (const lateFailure of [false, true]) {
    const view = screen(); await view.click();
    const older = fixture.reads[0];
    await view.click('取消检查'); assert.equal(older.signal.aborted, true);
    assert.deepEqual(methods(view), []);
    await view.click(); const newer = fixture.reads[1];
    newer.resolve(response({ methods: { card: false, wechat_pay: true, alipay: true } })); await view.flush();
    if (lateFailure) older.reject(new client.ApiError(0, 'network', 'fixture'));
    else older.resolve(response());
    await view.flush();
    assert.deepEqual(methods(view), [statuses[1], statuses[0], statuses[0]]);
    assert.equal(nodes(view.tree).filter(node => node.props.role === 'alert').length, 0);
  }
});

test('repeated activation while checking stays single-flight and keeps the check button focusable', async () => {
  const view = screen(); const click = button(view.tree, checkLabel).props.onClick;
  click(); click(); await view.flush();
  assert.equal(fixture.reads.length, 1);
  assert.equal(fixture.reads[0].signal.aborted, false);
  assert.equal(button(view.tree, checkLabel).props['aria-disabled'], true);
  assert.equal(button(view.tree, checkLabel).props.disabled, undefined, 'native disabled drops keyboard focus');
  let focused = false;
  button(view.tree, checkLabel).props.ref.current = { focus() { focused = true; } };
  await view.click('取消检查');
  assert.equal(focused, true, 'cancel restores focus before its own button disappears');
  assert.equal(fixture.reads[0].signal.aborted, true);
  fixture.reads[0].resolve(response()); await view.flush();
  assert.deepEqual(methods(view), []);
});

test('a new published revision immediately removes results, cancels pending reads and cannot resurrect them', async () => {
  const view = screen({ parent: true });
  await view.click(); fixture.reads[0].resolve(response()); await view.flush();
  assert.deepEqual(methods(view), statuses);
  fixture.load.data = saved({ revision: 8 }); view.draw();
  assert.deepEqual(methods(view), [], 'no old result is rendered on the first draw of the new version');
  assert.equal(fixture.reads.length, 1, 'version changes never trigger automatic checks');
  await view.click(); const older = fixture.reads[1], retired = view.instance;
  fixture.load.data = saved({ revision: 9 }); view.draw();
  const updates = retired.updates;
  assert.equal(older.signal.aborted, true);
  await view.click(); fixture.reads[2].resolve(response({ revision: 9 })); await view.flush();
  older.resolve(response({ revision: 8, methods: { card: false, wechat_pay: false, alipay: false } })); await view.flush();
  assert.equal(retired.updates, updates, 'a retired component must not accept the late response');
  assert.deepEqual(methods(view), statuses);
  fixture.load.data = saved(); view.draw();
  assert.deepEqual(methods(view), [], 'returning to an earlier version does not restore its old result');
});

test('page unmount aborts an in-flight check and ignores success or failure after cleanup', async () => {
  for (const fail of [false, true]) {
    const view = screen({ parent: true }); await view.click();
    const pending = fixture.reads[0], retired = view.instance;
    fixture.load.loading = true; view.draw();
    assert.equal(view.tree, undefined); assert.equal(pending.signal.aborted, true);
    const updates = retired.updates;
    if (fail) pending.reject(new client.ApiError(0, 'network', 'fixture'));
    else pending.resolve(response());
    await view.flush(); assert.equal(retired.updates, updates);
  }
});

test('a response from another saved version is discarded, with a refresh instruction and no channel claims', async () => {
  for (const revision of [6, 8]) {
    const view = screen(); await view.click();
    fixture.reads[0].resolve(response({ revision, charges_enabled: false })); await view.flush();
    assert.deepEqual(methods(view), []);
    assert.match(view.html(), /请刷新支付设置后重新检查/);
    assert.equal(nodes(view.tree).filter(node => node.props.role === 'alert').length, 0);
  }
});

test('draft edits and key rotation keep the check scoped to saved choices without blocking review or changing checkboxes', async () => {
  const view = screen({ parent: true, canManage: true }); await view.click();
  fixture.reads[0].resolve(response({ charges_enabled: false })); await view.flush();
  let choice = nodes(view.parent.tree).find(node => node.type === payments.PaymentMethodChoices);
  assert.deepEqual(choice.props.selected, ['card', 'wechat_pay']);
  choice.props.onChange(['alipay']); view.draw();
  const rotate = nodes(view.parent.tree).filter(node => node.props.type === 'radio')[1];
  rotate.props.onChange(); view.draw();
  for (const input of nodes(view.parent.tree).filter(node => node.props.type === 'password')) input.props.ref.current = { value: 'fixture-new-secret' };
  assert.equal(elementOf(view).hasDraft, true);
  assert.deepEqual(elementOf(view).savedMethods, ['card', 'wechat_pay']);
  assert.match(view.html(), /不代表草稿或新密钥已经验证/);
  assert.doesNotMatch(view.html(), /fixture-new-secret/);
  assert.deepEqual(methods(view), statuses, 'a draft edit must not relabel the saved result');
  choice = nodes(view.parent.tree).find(node => node.type === payments.PaymentMethodChoices);
  assert.deepEqual(choice.props.selected, ['alipay']); assert.equal(choice.props.invalid, false);
  const form = nodes(view.parent.tree).find(node => node.type === 'form');
  form.props.onSubmit({ preventDefault() {} }); view.draw();
  const dialog = nodes(view.parent.tree).find(node => node.props.title === '发布支付设置');
  assert.ok(dialog, 'unavailable channels and disabled charges do not add a publication gate');
  assert.equal(fixture.reads.length, 1);
  assert.deepEqual(fixture.load.data.spec.payment_methods, ['card', 'wechat_pay']);
});
function elementOf(view) { return nodes(view.parent.tree).find(node => node.type === readiness.PaymentReadiness).props; }

test('unconfigured credentials and network errors use the existing ApiError presenter and clear old success', async () => {
  const failures = [
    new client.ApiError(409, 'payment_readiness_no_keys', '请先通过发布流程保存支付密钥，再检查已保存的支付渠道'),
    new client.ApiError(503, 'payment_readiness_unavailable', '暂时无法检查 Stripe 支付渠道，请稍后重试；这不会更改支付设置'),
    new client.ApiError(503, 'payment_readiness_busy', '支付渠道检查正在进行，请稍后重试'),
    new client.ApiError(409, 'payment_revision_conflict', '支付配置已变化，请保留输入并重新读取'),
    // UX-2: definite answers about the saved key are worded as such, never as a temporary outage
    new client.ApiError(409, 'payment_credentials_rejected', 'Stripe 拒绝了已保存的密钥（密钥无效、已撤销或已过期）。请在 Stripe 核对后，通过“检查并发布”设置新的密钥'),
    new client.ApiError(409, 'payment_credentials_forbidden', 'Stripe 不允许已保存的密钥读取账户信息（可能是权限受限的密钥），因此无法检查支付渠道'),
    new client.ApiError(0, 'network', 'fixture diagnostic must not be shown'),
    new client.ApiError(403, 'platform_permission_denied', 'fixture diagnostic must not be shown'),
  ];
  for (const failure of failures) {
    failure.requestId = 'fixture-request';
    const view = screen({ parent: true, saved: saved({ spec: null, credential_configured: false, status: 'unconfigured' }) });
    await view.click(); fixture.reads[0].resolve(response()); await view.flush();
    await view.click(); assert.deepEqual(methods(view), [], 'a new check clears the old success while pending');
    fixture.reads[1].reject(failure); await view.flush();
    assert.deepEqual(methods(view), []);
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      ui.setLocale(locale); view.draw();
      const alert = nodes(view.tree).find(node => node.props.role === 'alert');
      assert.equal(alert.props.children, labels.errText(failure));
      assert.match(alert.props.children, /fixture-request/);
      if (locale === 'en') assert.doesNotMatch(alert.props.children, /[\u3400-\u9fff]|payment_readiness_|payment_revision_conflict|fixture diagnostic/);
    }
  }
});

test('a server revision conflict preserves unpublished channel and credential edits', async () => {
  const view = screen({ parent: true, canManage: true });
  nodes(view.parent.tree).find(node => node.type === payments.PaymentMethodChoices).props.onChange(['alipay']); view.draw();
  nodes(view.parent.tree).filter(node => node.props.type === 'radio')[1].props.onChange(); view.draw();
  const inputs = nodes(view.parent.tree).filter(node => node.props.type === 'password');
  inputs.forEach(input => { input.props.ref.current = { value: 'fixture-unpublished-secret' }; });
  await view.click();
  fixture.reads[0].reject(new client.ApiError(409, 'payment_revision_conflict', '支付配置已变化，请保留输入并重新读取'));
  await view.flush();
  assert.deepEqual(nodes(view.parent.tree).find(node => node.type === payments.PaymentMethodChoices).props.selected, ['alipay']);
  assert.equal(nodes(view.parent.tree).filter(node => node.props.type === 'radio')[1].props.checked, true);
  for (const input of inputs) assert.equal(input.props.ref.current.value, 'fixture-unpublished-secret');
  assert.match(view.html(), /请保留输入并重新读取/);
});

test('an AbortError is silent and permits a new manual check', async () => {
  const view = screen(); await view.click();
  fixture.reads[0].reject(new DOMException('fixture cancellation', 'AbortError')); await view.flush();
  assert.equal(nodes(view.tree).filter(node => node.props.role === 'alert').length, 0);
  assert.equal(button(view.tree, checkLabel).props['aria-disabled'], false);
  await view.click(); fixture.reads[1].resolve(response()); await view.flush();
  assert.deepEqual(methods(view), statuses);
});

test('three-state channel results, configuration notices and charges warnings render in all locales without wire fields', async () => {
  for (const configuration_status of ['available', 'missing', 'ambiguous', 'incomplete', 'inactive']) {
    const view = screen(); await view.click();
    const allUnknown = ['missing', 'ambiguous', 'inactive'].includes(configuration_status);
    fixture.reads[0].resolve(response({ configuration_status, charges_enabled: false,
      methods: allUnknown ? { card: null, wechat_pay: null, alipay: null } : response().methods }));
    await view.flush();
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      ui.setLocale(locale); view.draw();
      const expected = (allUnknown ? [statuses[2], statuses[2], statuses[2]] : statuses).map(key => ui.t(key));
      assert.deepEqual(methods(view), expected);
      const alert = nodes(view.tree).find(node => node.props.role === 'alert');
      assert.ok(alert.props.className.includes('err'));
      assert.ok(nodes(alert).some(node => node.type === 'strong'));
      const html = view.html();
      assert.doesNotMatch(html, /credential_revision|checked_at|charges_enabled|configuration_status|payment_method_types|wechat_pay|fixture diagnostic/);
      if (locale === 'en') assert.doesNotMatch(html, /[\u3400-\u9fff]|\{(?:n|env|time|methods)\}/);
      assert.equal(nodes(view.tree).filter(node => node.type === 'table').length, 0);
    }
    const notes = nodes(view.tree).filter(node => node.props.role === 'note');
    assert.equal(notes.length, (configuration_status === 'available' ? 0 : 1) + (allUnknown ? 0 : 1));
  }
});

test('the check names the Stripe API version applied, warns when it is not the pinned one, and says publishing checks again', async () => {
  const pinned = '2026-04-22.dahlia';
  for (const [api_version, shown, warned] of [[pinned, true, false], ['2026-09-30.endive', true, true],
    ['<b>fixture</b>', false, false], [null, false, false], [undefined, false, false]]) {
    const view = screen(); await view.click();
    fixture.reads[0].resolve(response({ api_version, api_version_requested: pinned })); await view.flush();
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      ui.setLocale(locale); view.draw();
      const html = view.html();
      assert.equal(html.includes('Stripe API'), shown, `${api_version}: ${html}`);
      if (shown) assert.ok(html.includes(api_version));
      assert.doesNotMatch(html, /<b>fixture/);
      const notes = nodes(view.tree).filter(node => node.props.role === 'note').map(node => markup(node)).join('');
      assert.equal(notes.includes(pinned) && notes.includes('2026-09-30.endive'), warned);
      if (locale === 'en') {
        assert.doesNotMatch(html, /[㐀-鿿]|\{(?:version|applied|pinned)\}/);
        assert.match(html, /refuses channels that are not activated, and asks for an explicit confirmation when it cannot verify them/);
        assert.match(html, /refuses to create a payment page/, 'a saved channel that is not activated is named as a cause of refused top-ups');
      }
    }
  }
});

test('activation evidence is shown per method and decides the not-activated warning (PS-1)', async () => {
  const view = screen(); await view.click();
  // WeChat Pay: shown off in the default configuration, but the account's capability is active.
  fixture.reads[0].resolve(response({ methods: { card: true, wechat_pay: false, alipay: false },
    activated: { card: true, wechat_pay: true, alipay: false } }));
  await view.flush();
  const notes = () => nodes(view.tree).filter(node => node.props.role === 'note').map(node => markup(node)).join('');
  assert.doesNotMatch(notes(), /微信支付/, 'activated but shown off is not a cause of refused top-ups');
  for (const [locale, active, inactive] of [['zh-Hans', 'Stripe 账户已开通此方式', 'Stripe 账户未开通此方式'],
    ['zh-Hant', 'Stripe 帳戶已開通此方式', 'Stripe 帳戶未開通此方式'], ['en', 'Activated for the Stripe account', 'Not activated for the Stripe account']]) {
    ui.setLocale(locale); view.draw();
    const shown = methods(view);
    assert.deepEqual(shown.filter(text => text === active).length, 2, `${locale}: ${shown}`);
    assert.deepEqual(shown.filter(text => text === inactive).length, 1, `${locale}: ${shown}`);
    if (locale === 'en') assert.doesNotMatch(view.html(), /[\u3400-\u9fff]/);
  }
  ui.setLocale('zh-Hans');
  // The saved selection is card + WeChat Pay: neither is "not activated", so no warning names them.
  assert.equal(nodes(view.tree).filter(node => node.props.role === 'note').length, 0);
  // A saved method Stripe has not activated is named.
  const second = screen({ props: { savedMethods: ['card', 'alipay'] } }); await second.click();
  fixture.reads[0].resolve(response({ methods: { card: true, wechat_pay: false, alipay: false },
    activated: { card: true, wechat_pay: true, alipay: false } }));
  await second.flush();
  const warning = nodes(second.tree).filter(node => node.props.role === 'note').map(node => markup(node)).join('');
  assert.match(warning, /Stripe 报告尚未开通的渠道：支付宝/);
  // Unknown evidence (null) adds no line and no warning.
  const third = screen(); await third.click();
  fixture.reads[0].resolve(response({ activated: { card: null, wechat_pay: null, alipay: null } }));
  await third.flush();
  assert.deepEqual(methods(third), statuses);
  assert.equal(nodes(third.tree).filter(node => node.props.role === 'note').length, 0);
});
