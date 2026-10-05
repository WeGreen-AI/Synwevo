/** The Agent chat's cost transparency, refusals, payer choice, composer facts and AI-content label.
 *  Explicit fixtures only (marked "fixture"): no live model, ledger, wallet, payment or browser
 *  claim is made here, and the amounts are arithmetic examples, not the governed prices. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, cost: any, refusals: any, payers: any, composer: any, label: any, copy: any, parts: any, status: any,
  ui: any, core: any;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'chat-test-locale', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  [cost, refusals, payers, composer, label, copy, parts, status, ui, core] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/cost.ts'), server.ssrLoadModule('/src/apps/agent/refusals.ts'),
    server.ssrLoadModule('/src/apps/agent/payers.ts'), server.ssrLoadModule('/src/apps/agent/composer.ts'),
    server.ssrLoadModule('/src/apps/agent/aiLabel.ts'), server.ssrLoadModule('/src/apps/agent/chat.messages.ts'),
    server.ssrLoadModule('/src/apps/agent/ChatParts.tsx'), server.ssrLoadModule('/src/apps/agent/ComposerStatus.tsx'),
    server.ssrLoadModule('@synwevo/ui'), server.ssrLoadModule('@synwevo/ui/i18n-core'),
  ]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });

const LOCALES = ['zh-Hans', 'en', 'zh-Hant'] as const;
function render(locale: string, element: React.ReactElement): string {
  ui.setLocale(locale);
  return renderToStaticMarkup(React.createElement(MemoryRouter, null,
    React.createElement(ui.I18nProvider ?? React.Fragment, null, element)));
}
const CNY = { code: 'CNY', scale: 2 };
function fixtureCost(overrides: Record<string, unknown> = {}) {
  return { charged_minor: '1234', currency: 'CNY', scale: 2, held_minor: '0', state: 'settled', model: 'fixture-model',
    band: 'off_peak', tokens: { input_cached: 1200, input_uncached: 34, output: 560, reasoning: 120 }, ...overrides };
}

// ---------------------------------------------------------------- formatting

test('ledger amounts format exactly in the reader\'s locale, never through a float', () => {
  assert.equal(cost.formatMoney('1234', CNY, 'zh-Hans'), '¥12.34');
  assert.equal(cost.formatMoney('1234', CNY, 'en'), 'CN¥12.34');
  assert.equal(cost.formatMoney('1234', CNY, 'zh-Hant'), 'CN¥12.34');
  // Above 2^53: a Number would round the last digit.
  assert.equal(cost.formatMoney('9007199254740993', CNY, 'en'), 'CN¥90,071,992,547,409.93');
  // A fine-grained ledger unit keeps every significant digit and the currency's usual minimum.
  assert.equal(cost.formatMoney('123', { code: 'CNY', scale: 6 }, 'zh-Hans'), '¥0.000123');
  assert.equal(cost.formatMoney('120000', { code: 'CNY', scale: 6 }, 'zh-Hans'), '¥0.12');
  assert.equal(cost.formatMoney('0', CNY, 'zh-Hans'), '¥0.00');
  assert.equal(cost.formatMoney('-50', CNY, 'zh-Hans'), '-¥0.50');
  // A non-ISO unit (a fixture, or admin-defined credits) shows its exact decimal and code.
  assert.equal(cost.formatMoney('1234567', { code: 'FIXTURE', scale: 3 }, 'en'), '1,234.567 FIXTURE');
  assert.equal(cost.majorDecimal('5', 6), '0.000005');
  assert.equal(cost.majorDecimal('1200', 2, 2), '12.00');
  for (const bad of ['1.5', '1e3', '', ' 1', '0x10', '01'] as const) assert.throws(() => cost.formatMoney(bad, CNY, 'en'), undefined, bad);
  assert.throws(() => cost.formatMoney('1', { code: 'CNY', scale: 19 }, 'en'));
  assert.equal(cost.formatTokens(1234567, 'en'), '1,234,567');
});

test('a per-batch rational rate shows its exact price per million tokens, or a fraction', () => {
  // Fixture: 2 units of 10^-6 per token is 2.00 per million tokens.
  const pricing = { unit: { code: 'CNY', scale: 6 }, per_tokens: 1, rates: {
    input_uncached: { numerator: '2', denominator: '1' }, input_cached: { numerator: '1', denominator: '25' },
    output: { numerator: '1', denominator: '3' } } };
  assert.equal(cost.formatPerMillion(pricing, 'input_uncached', 'zh-Hans', 'x'), '¥2.00');
  assert.equal(cost.formatPerMillion(pricing, 'input_cached', 'zh-Hans', 'x'), '¥0.04');
  assert.equal(cost.formatPerMillion(pricing, 'output', 'en', 'fallback'), 'fallback');
  assert.equal(composer.perMillion(pricing, 'output'), '1/3 CNY');
  assert.deepEqual(cost.rateAmount({ numerator: '0', denominator: '7' }, 1000, 2), { minor: '0', scale: 0 });
  assert.equal(cost.rateAmount({ numerator: '1', denominator: '0' }, 1000, 2), null);
});

test('only a well-formed ledger cost is shown, and its summary follows its state', () => {
  const value = cost.validTurnCost(fixtureCost());
  assert.ok(value);
  assert.deepEqual(cost.costSummary(value), { key: 'costSettled', amount: '1234' });
  assert.deepEqual(cost.costSummary(cost.validTurnCost(fixtureCost({ charged_minor: '0' }))), { key: 'costFree' });
  assert.deepEqual(cost.costSummary(cost.validTurnCost(fixtureCost({ state: 'held', held_minor: '90' }))), { key: 'costHeld', amount: '90' });
  // Unknown usage never shows a figure, not even the hold.
  assert.deepEqual(cost.costSummary(cost.validTurnCost(fixtureCost({ state: 'pending', held_minor: '90', charged_minor: '0' }))), { key: 'costPending' });
  for (const bad of [null, {}, fixtureCost({ charged_minor: 12 }), fixtureCost({ charged_minor: '-1' }), fixtureCost({ state: 'estimated' }),
    fixtureCost({ scale: 40 }), fixtureCost({ currency: '' }), fixtureCost({ tokens: { input_cached: 1, input_uncached: 1, output: 1, reasoning: 2 } }),
    fixtureCost({ tokens: { input_cached: 1.5, input_uncached: 1, output: 1, reasoning: 0 } }), fixtureCost({ model: '' })]) {
    assert.equal(cost.validTurnCost(bad), null, JSON.stringify(bad));
  }
  assert.equal(cost.bandText('peak'), 'bandPeak');
  assert.equal(cost.bandText('off_peak'), 'bandOffPeak');
  assert.equal(cost.bandText('mixed'), 'bandMixed');
  assert.equal(cost.bandText('flat'), 'bandFlat');
  assert.equal(cost.bandText(null), null);
  assert.equal(cost.bandText('night'), null);
});

test('an answer\'s cost line renders the ledger figure, its breakdown and state in three languages', () => {
  const expected = { 'zh-Hans': ['已结算 ¥12.34', '输入（缓存命中）', '1,200 个 token', '低谷时段', '其中思考'],
    en: ['Charged CN¥12.34', 'Input (cache hit)', '1,200 tokens', 'Off-peak', 'Of which reasoning'],
    'zh-Hant': ['已結算 CN¥12.34', '輸入（快取命中）', '1,200 個 token', '離峰時段', '其中思考'] } as const;
  for (const locale of LOCALES) {
    const html = render(locale, React.createElement(parts.TurnCostLine, { cost: fixtureCost() }));
    for (const text of expected[locale]) assert.ok(html.includes(text), `${locale}: ${text} in ${html}`);
    assert.match(html, /aria-expanded="false"/);
    assert.match(html, /aria-controls="[^"]+-cost"/);
    assert.match(html, / hidden=""/);
  }
  const pending = render('en', React.createElement(parts.TurnCostLine, { cost: fixtureCost({ state: 'pending', charged_minor: '0', held_minor: '90' }) }));
  assert.ok(pending.includes('Usage being verified') && pending.includes('nothing is charged from an estimate'));
  assert.ok(!pending.includes('Charged CN¥'), pending);
  assert.equal(render('en', React.createElement(parts.TurnCostLine, { cost: null })), '');
  assert.equal(render('en', React.createElement(parts.TurnCostLine, { cost: { charged_minor: '5' } })), '');
  const one = render('en', React.createElement(parts.TurnCostLine, { cost: fixtureCost({ tokens: { input_cached: 1, input_uncached: 0, output: 0, reasoning: 0 } }) }));
  assert.ok(one.includes('>1 token<'), one);
});

// ---------------------------------------------------------------- refusals

test('each refusal maps to a translated sentence and its one next step', () => {
  const cases: [string, string, string | null][] = [
    ['billing_insufficient_funds', 'funds', 'topup'],
    ['billing_payer_daily_budget_exceeded', 'limit', 'settings'],
    ['billing_payer_budget_disabled', 'limit', 'settings'],
    ['billing_payer_budget_not_configured', 'no_limit', 'settings'],
    ['billing_risk_payer_brake', 'brake', null],
    ['billing_risk_route_brake', 'brake', null],
    ['billing_risk_payer_suspended', 'suspended', null],
    ['billing_risk_route_suspended', 'suspended', null],
    ['billing_risk_breaker_open', 'breaker', null],
    ['billing_risk_not_allowlisted', 'allowlist', null],
    ['billing_cost_admission_disabled', 'breaker', null],
    ['billing_not_configured', 'not_configured', null],
    ['billing_model_route_unavailable', 'not_configured', null],
    ['provider_consent_required', 'consent', 'consent'],
    // A newer version of the data-sharing terms is its own sentence: "agree again", not "agree first".
    ['provider_consent_version_changed', 'consent_changed', 'consent'],
    ['billing_model_configuration_changed', 'price_changed', 'requote'],
    ['billing_consent_model_changed', 'price_changed', 'requote'],
    // The platform's per-call maximum cost is a policy limit, not an outage.
    ['billing_cost_call_limit_exceeded', 'call_cap', null],
    // Content safety: a blocked message was never sent; a filtered answer stopped.
    ['content_blocked_input', 'content_input', null],
    ['content_filtered_output', 'content_output', null],
    ['content_blocked_output', 'content_output', null],
  ];
  for (const [code, kind, next] of cases) {
    const refusal = refusals.refusalOf({ code });
    assert.equal(refusal?.kind, kind, code);
    assert.equal(refusal.next, next, code);
    for (const locale of ['en', 'zh-Hant'] as const) {
      const sentence = core.translate(locale, copy.chatCopy[refusal.text][0]);
      assert.notEqual(sentence, copy.chatCopy[refusal.text][0], `${code} translated to ${locale}`);
    }
  }
  // The operator's DeepSeek balance (upstream 402) is never the person's wallet.
  assert.equal(refusals.refusalOf({ code: 'provider_balance' }).kind, 'breaker');
  assert.equal(refusals.refusalOf({ code: 'billing_provider_balance' }).kind, 'breaker');
  // An idempotent replay of a refusal is the same refusal; a gateway failure has no code.
  assert.equal(refusals.refusalOf({ code: 'idempotent_failure', detail: { code: 'billing_insufficient_funds' } }).kind, 'funds');
  assert.equal(refusals.refusalOf({ code: 'billing_insufficient_funds', upstream: true }), null);
  // Not refusals of this kind: payment-consent states, the engine's own credential change, generic failures.
  for (const code of ['billing_consent_revoked', 'billing_consent_exhausted', 'model_configuration_changed', 'agent_not_configured',
    'provider_timeout', 'turn_running', '', 'consent_pending_operation']) {
    assert.equal(refusals.refusalOf({ code }), null, code);
  }
  assert.equal(refusals.refusalOf(null), null);
  assert.equal(refusals.refusalOf('billing_insufficient_funds'), null);
});

test('a refusal notice states what happened and offers the next step', () => {
  const funds = refusals.refusalOf({ code: 'billing_insufficient_funds' });
  const html = render('en', React.createElement(parts.RefusalNotice, { refusal: funds, walletUrl: '/account/wallet?from=suite', live: true }));
  assert.ok(html.includes('nothing was charged') && html.includes('role="alert"'), html);
  assert.ok(html.includes('href="/account/wallet?from=suite"') && html.includes('>Top up<'), html);
  const limit = render('zh-Hant', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_payer_weekly_budget_exceeded' }) }));
  assert.ok(limit.includes('href="/agent/settings"') && limit.includes('開啟 Agent 設定') && !limit.includes('role="alert"'), limit);
  const breaker = render('zh-Hans', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_risk_breaker_open' }) }));
  assert.ok(breaker.includes('没有为此扣费') && !breaker.includes('<a') && !breaker.includes('<button'), breaker);
  // Each refusal kind has its own sentence: no two kinds read alike.
  const sentences = new Set(Object.values(refusals.REFUSAL_TEXT));
  assert.equal(sentences.size, Object.keys(refusals.REFUSAL_TEXT).length);
  const cap = render('en', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_cost_call_limit_exceeded' }) }));
  assert.ok(cap.includes('per-call maximum') && cap.includes('nothing was charged') && !cap.includes('temporarily unavailable'), cap);
  const blocked = render('zh-Hant', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'content_blocked_input' }) }));
  assert.ok(blocked.includes('內容安全') && blocked.includes('沒有傳送給模型'), blocked);
  // A filtered answer does not promise "no charge": the admin's charging policy decides, and the
  // answer's own ledger line says what was charged.
  const filtered = render('en', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'content_filtered_output' }) }));
  assert.ok(filtered.includes('content safety') && !filtered.includes('nothing was charged'), filtered);
  const requote = render('en', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_consent_model_changed' }), onRequote() {} }));
  assert.ok(requote.includes('>Confirm at the new price</button>'), requote);
  // Without a handler the action is not offered (never an empty button).
  const none = render('en', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_consent_model_changed' }) }));
  assert.ok(!none.includes('<button'), none);
});

// ---------------------------------------------------------------- payer selection

const me = { id: 'usr_fixture', name: 'Fixture Person' };
test('the personal wallet is always offered first; only valid organization grants are added', () => {
  assert.deepEqual(payers.parsePayers(undefined, me).map(payers.payerKey), ['user:usr_fixture']);
  const list = payers.parsePayers({ payers: [
    { kind: 'organization', id: 'org_fixture', name: 'Fixture Org', balance: { available: '5000', currency: 'CNY', scale: 2 } },
    { kind: 'organization', id: 'org_fixture', name: 'Duplicate' },
    { kind: 'organization', id: 'org_paused', name: 'Paused Org', available: false },
    { kind: 'organization', id: 'bad id!', name: 'Bad' },
    { kind: 'organization', id: 'org_noname' },
    { kind: 'user', id: 'usr_someone_else', name: 'Someone' },
    { kind: 'user', id: 'usr_fixture', balance: { available: '100', currency: 'CNY', scale: 2 } },
    { kind: 'team', id: 'team_fixture', name: 'Team' },
  ] }, me);
  assert.deepEqual(list.map(payers.payerKey), ['user:usr_fixture', 'organization:org_fixture']);
  assert.deepEqual(list[0].balance, { available: '100', currency: 'CNY', scale: 2 });
  assert.equal(list[1].name, 'Fixture Org');
  assert.equal(payers.parsePayers({ payers: [{ kind: 'organization', id: 'org_x', name: 'X', balance: { available: '1.5', currency: 'CNY', scale: 2 } }] }, me)[1].balance, null);
});

test('the remembered payer is used only while still offered; the personal default sends nothing extra', () => {
  const list = payers.parsePayers({ payers: [{ kind: 'organization', id: 'org_fixture', name: 'Fixture Org' }] }, me);
  assert.equal(payers.payerKey(payers.choosePayer(list, { kind: 'organization', id: 'org_fixture' })), 'organization:org_fixture');
  assert.equal(payers.payerKey(payers.choosePayer(list, { kind: 'organization', id: 'org_revoked' })), 'user:usr_fixture');
  assert.equal(payers.payerKey(payers.choosePayer(list, null)), 'user:usr_fixture');
  assert.deepEqual(payers.payerInput(list[0], me.id), {});
  assert.deepEqual(payers.payerInput(list[1], me.id), { payer_kind: 'organization', payer_id: 'org_fixture' });
  const store = new Map<string, string>();
  const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } };
  payers.rememberPayer(me.id, list[1], storage);
  assert.deepEqual(payers.recallPayer(me.id, storage), { kind: 'organization', id: 'org_fixture' });
  assert.equal(payers.recallPayer('usr_other', storage), null);
  store.set('synwevo.agent.payer.usr_fixture', '{"kind":"admin","id":"x"}');
  assert.equal(payers.recallPayer(me.id, storage), null);
  const broken = { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } };
  assert.equal(payers.recallPayer(me.id, broken), null);
  assert.doesNotThrow(() => payers.rememberPayer(me.id, list[1], broken));
});

test('the composer shows the payer choice, balance, limit and a low-balance top-up', () => {
  const list = payers.parsePayers({ payers: [{ kind: 'organization', id: 'org_fixture', name: 'Fixture Org' }] }, me);
  const fixtureStatus = { available: true, environment: 'test', unit: CNY, per_call_hold: '100',
    balance: { booked: '350', held: '50', available: '300' },
    limit: { configured: true, enabled: true, usage_state: 'available', remaining: '2000', period: 'daily' } };
  const props = { status: fixtureStatus, offer: null, payers: list, payersError: false, payer: list[0], setPayer() {}, walletUrl: '/account/wallet' };
  const html = render('en', React.createElement(status.ComposerStatus, { money: props }));
  assert.ok(html.includes('<select') && html.includes('Personal wallet') && html.includes('Organization: Fixture Org'), html);
  assert.ok(html.includes('Balance CN¥3.00') && html.includes('CN¥0.50 on hold') && html.includes('CN¥20.00 left today'), html);
  assert.ok(html.includes('Test wallet'), html);
  assert.ok(html.includes('may not cover many more answers') && html.includes('href="/account/wallet"'), html);
  const single = render('zh-Hans', React.createElement(status.ComposerStatus, { money: { ...props, payers: [list[0]],
    status: { ...fixtureStatus, balance: { booked: '50', held: '0', available: '50' } } } }));
  assert.ok(!single.includes('<select') && single.includes('个人钱包') && single.includes('余额不足以完成一次调用'), single);
  // An organization payer shows only what the server listed for it, and no personal limit.
  const org = render('en', React.createElement(status.ComposerStatus, { money: { ...props, payer: list[1] } }));
  assert.ok(org.includes('Balance unavailable') && !org.includes('left today') && !org.includes('Top up'), org);
  const missing = render('en', React.createElement(status.ComposerStatus, { money: { ...props, payers: [list[0]],
    status: { ...fixtureStatus, limit: { configured: false, enabled: false, usage_state: null, remaining: null, period: null } } } }));
  assert.ok(missing.includes('No spending limit set') && missing.includes('href="/agent/settings"'), missing);
  // Without the paid service the composer says so instead of inventing a balance.
  const closed = render('zh-Hans', React.createElement(status.ComposerStatus, { money: { ...props, payers: [list[0]],
    status: { available: false, reason: 'not_configured' } } }));
  assert.ok(closed.includes('付费模型服务尚未开放') && !closed.includes('余额 ¥') && !closed.includes('去充值'), closed);
});

// ---------------------------------------------------------------- the composer's facts

test('balance state, the tightest limit and the turn maximum come from server values only', () => {
  const s = (available: string, hold = '100') => ({ available: true, balance: { booked: available, held: '0', available }, per_call_hold: hold });
  assert.equal(composer.fundsState(s('99')), 'empty');
  assert.equal(composer.fundsState(s('0')), 'empty');
  assert.equal(composer.fundsState(s('100')), 'low');
  assert.equal(composer.fundsState(s('499')), 'low');
  assert.equal(composer.fundsState(s('500')), 'ok');
  assert.equal(composer.fundsState(s('92233720368547758070', '1')), 'ok');
  assert.equal(composer.fundsState(null), 'unknown');
  assert.equal(composer.fundsState(s('1.5')), 'unknown');
  assert.deepEqual(composer.limitLine({ available: true, limit: { configured: true, enabled: true, usage_state: 'available', remaining: '7', period: 'weekly' } }),
    { key: 'limitRemaining', amount: '7', period: 'periodWeekly' });
  assert.deepEqual(composer.limitLine({ available: true, limit: { configured: true, enabled: false, usage_state: null, remaining: null, period: null } }), { key: 'limitPaused' });
  assert.deepEqual(composer.limitLine({ available: true, limit: { configured: false, enabled: false, usage_state: null, remaining: null, period: null } }), { key: 'limitMissing' });
  assert.equal(composer.limitLine({ available: false }), null);
  assert.deepEqual(composer.turnMaximum({ budget: '500', unit: CNY }, { per_call_hold: '100', unit: CNY }), { key: 'turnMaximum', amount: '500', unit: CNY });
  assert.deepEqual(composer.turnMaximum(null, { per_call_hold: '100', unit: CNY }), { key: 'turnMaximumCall', amount: '100', unit: CNY });
  assert.equal(composer.turnMaximum(null, null), null);
});

test('the top-up link points at the account service\'s wallet page and nowhere else', () => {
  const origin = 'https://docwave.fixture.test';
  assert.equal(composer.walletHref('https://accounts.fixture.test/account', 'suite', origin), 'https://accounts.fixture.test/account/wallet?from=suite');
  assert.equal(composer.walletHref('/account', null, origin), '/account/wallet');
  assert.equal(composer.walletHref(undefined, 'suite', origin), '/account/wallet?from=suite');
  assert.equal(composer.walletHref('javascript:alert(1)', null, origin), '/account/wallet');
  assert.equal(composer.walletHref('https://accounts.fixture.test/account#x?y', null, origin), 'https://accounts.fixture.test/account/wallet');
});

test('Enter sends, Shift+Enter breaks a line, and an input method composition is never cut short', () => {
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: false }, false), true);
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: true }, false), false);
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: false, altKey: true }, false), false);
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: false }, true), false);
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: false, isComposing: true }, false), false);
  // Safari: compositionend has already fired, the confirming Enter still reports keyCode 229.
  assert.equal(composer.shouldSubmit({ key: 'Enter', shiftKey: false, keyCode: 229 }, false), false);
  assert.equal(composer.shouldSubmit({ key: 'a', shiftKey: false }, false), false);
});

// ---------------------------------------------------------------- price change and re-confirmation

const rate = (numerator: string, denominator = '1') => ({ numerator, denominator });
const previousTerms = { environment: 'test', model: 'fixture-model', confirmed_route: 'fixture-route', route_version: 'fixture-v1',
  price_id: 'price_fixture_1', unit: { code: 'CNY', scale: 6 }, per_tokens: 1, terms_ref: 'fixture:terms', per_call_hold: '50000',
  rates: { input_uncached: rate('2'), input_cached: rate('1', '25'), output: rate('8') } };

test('what changed is listed per field, compared per million tokens, and nothing else counts as a change', () => {
  assert.deepEqual(composer.priceChanges(previousTerms, { ...previousTerms }), []);
  // The same price expressed per 1,000 tokens is not a change.
  assert.deepEqual(composer.priceChanges(previousTerms, { ...previousTerms, per_tokens: 1000,
    rates: { input_uncached: rate('2000'), input_cached: rate('40'), output: rate('8000') } }), []);
  const after = { ...previousTerms, model: 'fixture-model-2', price_id: 'price_fixture_2', per_call_hold: '60000',
    rates: { ...previousTerms.rates, output: rate('16') } };
  assert.deepEqual(composer.priceChanges(previousTerms, after), [
    { field: 'changedModel', before: 'fixture-model', after: 'fixture-model-2' },
    { field: 'changedPrice', before: 'price_fixture_1', after: 'price_fixture_2' },
    { field: 'changedOutput', before: '8 CNY', after: '16 CNY' },
    { field: 'changedHold', before: '0.050000 CNY', after: '0.060000 CNY' },
  ]);
  assert.equal(composer.samePricing(previousTerms, { ...previousTerms }), true);
  assert.equal(composer.samePricing(previousTerms, { ...previousTerms, terms_ref: 'fixture:terms-2' }), false);
  assert.equal(composer.samePricing(previousTerms, { ...previousTerms, environment: 'live' }), false);
  const html = render('en', React.createElement(parts.PriceChangeList, { before: previousTerms, after }));
  assert.ok(html.includes('What changed since you last confirmed') && html.includes('<del>') && html.includes('<ins>'), html);
  assert.ok(html.includes('Before </span>fixture-model<') && html.includes('Now </span>fixture-model-2<'), html);
  // Shown in the reader's locale; decided on exact values.
  assert.ok(html.includes('CN¥8.00') && html.includes('CN¥16.00') && html.includes('CN¥0.05') && html.includes('CN¥0.06'), html);
  const same = render('zh-Hant', React.createElement(parts.PriceChangeList, { before: previousTerms, after: { ...previousTerms } }));
  assert.ok(same.includes('價格條款未變'), same);
});

test('a one-step re-confirmation reuses the previous limits only when they fit the new offer', () => {
  const offer = { per_call_hold: '60000', max_calls_limit: 12, run_seconds_limit: 600, unit: { code: 'CNY', scale: 6 } };
  const previous = { budget: '500000', max_calls: 3, run_seconds: 300 };
  assert.deepEqual(composer.renewalParams(previous, offer, previousTerms.unit), previous);
  assert.equal(composer.renewalParams({ ...previous, budget: '59999' }, offer, previousTerms.unit), null);
  assert.equal(composer.renewalParams({ ...previous, max_calls: 13 }, offer, previousTerms.unit), null);
  assert.equal(composer.renewalParams({ ...previous, run_seconds: 601 }, offer, previousTerms.unit), null);
  assert.equal(composer.renewalParams(previous, offer, { code: 'USD', scale: 6 }), null);
  assert.equal(composer.renewalParams(previous, { ...offer, unit: { code: 'CNY', scale: 2 } }, previousTerms.unit), null);
});

// ---------------------------------------------------------------- the AI-content label

test('every answer carries the AI label in three languages, and copied text includes it once', () => {
  const wording = { 'zh-Hans': '由 AI 生成，可能有误，仅供参考', en: 'Generated by AI. May contain errors. For reference only.',
    'zh-Hant': '由 AI 生成，可能有誤，僅供參考' } as const;
  for (const locale of LOCALES) {
    assert.equal(core.translate(locale, label.AI_LABEL_SOURCE), wording[locale]);
    const html = render(locale, React.createElement(parts.AiLabel));
    assert.ok(html.includes(wording[locale]), html);
    assert.ok(html.includes('aria-hidden="true">AI</span>'), html);
  }
  const text = label.withAiLabel('Fixture answer.\n\n', wording.en);
  assert.equal(text, `Fixture answer.\n\n— ${wording.en}`);
  assert.equal(label.withAiLabel(text, wording.en), text);
  assert.equal(label.withAiLabel('', wording['zh-Hans']), `— ${wording['zh-Hans']}`);
});

test('the chat catalogue is complete in three languages and does not collide with other wording', () => {
  assert.deepEqual(core.validateMessages(copy.chatMessages), []);
  const seen = new Map<string, string>();
  for (const [key, [source, en, hant]] of Object.entries(copy.chatCopy) as [string, [string, any, string]][]) {
    const english = typeof en === 'string' ? en : en.other;
    if (seen.has(source)) assert.equal(seen.get(source), `${english}|${hant}`, `duplicate source ${key}`);
    seen.set(source, `${english}|${hant}`);
    // The first registered wording wins; ours must be the one the reader sees.
    assert.equal(core.translate('zh-Hant', source, { count: 2, amount: '1', period: 'p', name: 'n', model: 'm', n: 1, total: 2, lang: 'l', budget: 'b' }),
      hant.replace(/\{(\w+)\}/g, (_: string, p: string) => ({ count: '2', amount: '1', period: 'p', name: 'n', model: 'm', n: '1', total: '2', lang: 'l', budget: 'b' } as any)[p]), key);
  }
});
