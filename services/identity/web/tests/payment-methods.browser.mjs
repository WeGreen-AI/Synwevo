/** Local-only browser fixtures exercising the real React components and auth-client request path.
 * Run: node --test tests/payment-methods.browser.mjs
 * Requires Playwright + Chromium; PAYMENT_TEST_PLAYWRIGHT_MODULE can name an installed module URL.
 * No proxy or backend is started. Every API response is a fixture; non-local network is blocked. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const { chromium } = await import(process.env.PAYMENT_TEST_PLAYWRIGHT_MODULE || 'playwright');
let server, browser, origin;
const legacy = { enabled: true, operator_name: 'Fixture merchant', packages: [{ id: 'fixture', amount_minor: 1000, label: 'Fixture package' }],
  custom_amount: false, min_amount_minor: 1000, max_amount_minor: 1000,
  velocity: { max_orders_per_hour: 5, max_orders_per_day: 20, max_pending: 3, max_amount_per_day_minor: 100000 } };
const state = spec => ({ environment: 'test', revision: spec ? 7 : 0, spec, credential_configured: !!spec,
  status: spec ? 'configured' : 'unconfigured', wallet_unit: { code: 'CNY', scale: 2 },
  webhook_url: 'http://fixture.invalid/webhook', webhook_events: [], created_at: spec ? 1790000000 : null, reason: null });
const options = methods => ({ available: true, payment_methods: methods, currency: 'CNY', packages: legacy.packages,
  custom_amount: false, min_amount_minor: 1000, max_amount_minor: 1000 });

before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false, logLevel: 'error',
    server: { host: '127.0.0.1', port: 0, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'isolated-payment-fixture', configureServer(vite) {
      vite.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/__payment_fixture')) return next();
        res.setHeader('Content-Type', 'text/html');
        res.end('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/tests/fixtures/payment-methods.tsx"></script></body></html>');
      });
    } }] });
  await server.listen();
  origin = `http://127.0.0.1:${server.httpServer.address().port}`;
  browser = await chromium.launch({ headless: true });
});
after(async () => { await browser?.close(); await server?.close(); });

async function fixture({ current = state(legacy), topupOptions = options(['card']), width = 390, publish } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const calls = [], errors = [], external = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { external.push(url.origin); return route.abort(); }
    if (!url.pathname.startsWith('/api/')) return route.continue();
    const request = route.request();
    const body = request.method() === 'GET' ? null : request.postDataJSON();
    calls.push({ path: url.pathname, method: request.method(), body });
    // Real identity errors carry a middleware request id; preserve that response contract here.
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json',
      body: JSON.stringify(status >= 400 ? { ...body, request_id: 'payment-ui-fixture-request' } : body) });
    if (url.pathname === '/api/admin/billing/payments') return json(current);
    if (url.pathname === '/api/admin/billing/payments/publish' && publish) return publish(body, json);
    if (url.pathname === '/api/auth/reauth/options') return json({ scope: 'admin', methods: ['password'] });
    if (url.pathname === '/api/auth/reauth') return json({ ok: true });
    if (url.pathname === '/api/auth/wallet/topup-options') return json(topupOptions);
    errors.push(`Unexpected API request: ${url.pathname}`);
    return json({ error: 'fixture_unexpected_request', message: 'No such fixture endpoint' }, 500);
  });
  const open = async query => {
    await page.goto(`${origin}/__payment_fixture?${new URLSearchParams(query)}`);
    await page.getByText('Isolated payment-method UI fixture', { exact: false }).waitFor();
  };
  const close = async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(external, []); };
  return { page, calls, open, close };
}

test('new form starts empty, requires a method before any publish, and keeps keyboard selection across all locales at 320px', async () => {
  const f = await fixture({ current: state(null), width: 320 });
  try {
    await f.open({ locale: 'en' });
    const field = f.page.getByRole('group', { name: 'Payment methods (select at least one)' });
    await field.waitFor();
    assert.equal(await field.locator('input:checked').count(), 0);
    await f.page.getByRole('button', { name: 'Review and publish' }).click();
    await f.page.getByRole('alert').filter({ hasText: 'Select at least one payment method.' }).waitFor();
    assert.equal(f.calls.filter(call => call.path.endsWith('/publish')).length, 0);
    const card = field.getByRole('checkbox', { name: 'Card', exact: true });
    await card.focus(); await f.page.keyboard.press('Space');
    assert.equal(await card.isChecked(), true);
    await f.page.keyboard.press('Tab');
    assert.equal(await field.getByRole('checkbox', { name: 'WeChat Pay' }).evaluate(node => node === document.activeElement), true);
    await f.page.keyboard.press('Space');
    assert.equal(await field.getByRole('checkbox', { name: 'WeChat Pay' }).isChecked(), true);
    for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
      await f.page.locator('select.language-menu, .language-menu select').selectOption(locale);
      assert.equal(await f.page.locator('input[name="payment_methods"]:checked').count(), 2);
      assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow: ${locale}`);
    }
  } finally { await f.close(); }
});

test('confirmation includes only chosen channels; reason, real reauth request, stable idempotency and revision survive a conflict', async () => {
  let published = 0;
  const f = await fixture({ publish: (_body, json) => ++published === 1
    ? json({ error: 'reauth_required', message: 'Fixture: reauthentication required', detail: { scope: 'admin' } }, 403)
    : json({ error: 'payment_revision_conflict', message: '支付配置已变化，请保留输入并重新读取' }, 409) });
  try {
    await f.open({ locale: 'en' });
    const channels = f.page.getByRole('group', { name: 'Payment methods (select at least one)' });
    await channels.waitFor();
    assert.equal(await channels.locator('input:checked').count(), 3, 'legacy settings');
    await channels.getByRole('checkbox', { name: 'WeChat Pay' }).uncheck();
    await channels.getByRole('checkbox', { name: 'Alipay' }).uncheck();
    await f.page.getByRole('button', { name: 'Review and publish' }).click();
    const dialog = f.page.getByRole('alertdialog');
    await dialog.waitFor();
    assert.match(await dialog.innerText(), /Card/);
    assert.doesNotMatch(await dialog.innerText(), /WeChat Pay|Alipay/);
    await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
    assert.equal(published, 0, 'a reason is required before sending');
    await dialog.locator('textarea').fill('Fixture channel change reason');
    await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
    await dialog.locator('input[autocomplete="current-password"]').fill('fixture-password-not-a-real-credential');
    await dialog.getByRole('button', { name: /Verify.*Publish/ }).click();
    await dialog.getByRole('alert').filter({ hasText: 'The payment settings changed meanwhile.' }).waitFor();
    const requests = f.calls.filter(call => call.path.endsWith('/publish'));
    assert.equal(requests.length, 2);
    assert.deepEqual(requests[0].body, requests[1].body);
    assert.deepEqual(requests[1].body.spec.payment_methods, ['card']);
    assert.equal(requests[1].body.expected_revision, 7);
    assert.equal(requests[1].body.reason, 'Fixture channel change reason');
    assert.equal(requests[1].body.credential.action, 'keep');
    assert.match(requests[1].body.idempotency_key, /^pay_[a-f0-9]{32}$/);
    assert.equal(f.calls.filter(call => call.path === '/api/auth/reauth').length, 1);
    assert.equal(f.calls.find(call => call.path === '/api/auth/reauth').body.scope, 'admin');
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal(await channels.getByRole('checkbox', { name: 'Card', exact: true }).isChecked(), true);
    assert.equal(await channels.getByRole('checkbox', { name: 'WeChat Pay' }).isChecked(), false);
  } finally { await f.close(); }
});

test('read-only published overview displays only the saved subset', async () => {
  const f = await fixture({ current: state({ ...legacy, payment_methods: ['alipay'] }) });
  try {
    await f.open({ readonly: 'true', locale: 'en' });
    await f.page.getByText('Selected payment methods', { exact: true }).waitFor();
    const text = await f.page.locator('main').innerText();
    assert.match(text, /Alipay/); assert.doesNotMatch(text, /WeChat Pay|Card/);
    assert.equal(await f.page.locator('input[name="payment_methods"]').count(), 0);
  } finally { await f.close(); }
});

test('publishing a subset sends canonical methods and refreshes both the saved overview and editor', async () => {
  const current = state(legacy);
  const f = await fixture({ current, publish: (body, json) => {
    Object.assign(current, { spec: body.spec, revision: 8 });
    return json({ status: 'succeeded', operation_id: 'fixture-publication', state: current });
  } });
  try {
    await f.open({ locale: 'en' });
    const channels = f.page.getByRole('group', { name: 'Payment methods (select at least one)' });
    await channels.getByRole('checkbox', { name: 'WeChat Pay' }).uncheck();
    await channels.getByRole('checkbox', { name: 'Card', exact: true }).uncheck();
    await channels.getByRole('checkbox', { name: 'Card', exact: true }).check();
    await f.page.getByRole('button', { name: 'Review and publish' }).click();
    const dialog = f.page.getByRole('alertdialog');
    assert.match(await dialog.innerText(), /Card and Alipay/);
    assert.doesNotMatch(await dialog.innerText(), /WeChat Pay/);
    await dialog.locator('textarea').fill('Fixture publish selected channels');
    await dialog.getByRole('button', { name: 'Publish', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    await f.page.getByText('Version 8', { exact: true }).waitFor();
    const request = f.calls.find(call => call.path.endsWith('/publish'));
    assert.deepEqual(request.body.spec.payment_methods, ['card', 'alipay']);
    assert.equal(request.body.expected_revision, 7);
    assert.match(await f.page.locator('dl').first().innerText(), /Card and Alipay/);
    assert.doesNotMatch(await f.page.locator('dl').first().innerText(), /WeChat Pay/);
    assert.equal(await channels.locator('input:checked').count(), 2);
    assert.equal(await channels.getByRole('checkbox', { name: 'WeChat Pay' }).isChecked(), false);
  } finally { await f.close(); }
});

test('personal and organization top-ups show only returned channels in three locales, with no narrow-screen overflow', async () => {
  const labels = { en: ['Card', 'WeChat Pay', 'Alipay'], 'zh-Hans': ['银行卡', '微信支付', '支付宝'], 'zh-Hant': ['銀行卡', '微信支付', '支付寶'] };
  for (const surface of ['wallet', 'organization']) for (const locale of Object.keys(labels)) {
    const f = await fixture({ topupOptions: options(['card', 'alipay']), width: 320 });
    try {
      await f.open({ surface, locale });
      await f.page.getByText(/Available payment methods|可用支付方式/).waitFor();
      const text = await f.page.locator('body').innerText();
      assert.ok(text.includes(labels[locale][0])); assert.ok(text.includes(labels[locale][2]));
      assert.ok(!text.includes(labels[locale][1]));
      assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${surface}: ${locale}`);
    } finally { await f.close(); }
  }
});

test('no-channel, missing-channel and unavailable options never offer payment or advertise any channel', async () => {
  for (const surface of ['wallet', 'organization']) for (const topupOptions of [
    { ...options([]), available: false, reason: 'not_configured' }, options([]), options(undefined),
    { ...options(['card']), available: false, reason: 'email_unverified' },
    { ...options(['card']), available: false, reason: 'wallet_frozen' },
  ]) {
    const f = await fixture({ topupOptions });
    try {
      await f.open({ surface, locale: 'en' });
      await f.page.getByText(/not open|not yet|not available|Verify your email|verify your email|frozen/i).waitFor();
      const text = await f.page.locator('body').innerText();
      assert.doesNotMatch(text, /Card|WeChat Pay|Alipay/);
      assert.equal(await f.page.getByRole('button', { name: 'Continue to payment', exact: true }).count(), 0);
      assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
    } finally { await f.close(); }
  }
});
