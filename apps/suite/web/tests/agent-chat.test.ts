/** The Agent chat's cost transparency, refusals, payer choice, composer facts and AI-content label.
 *  Explicit fixtures only (marked "fixture"): no live model, ledger, wallet, payment or browser
 *  claim is made here, and the amounts are arithmetic examples, not the governed prices. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { createServer } from 'vite';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import { Link, MemoryRouter } from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, cost: any, refusals: any, payers: any, composer: any, label: any, copy: any, parts: any, status: any,
  ui: any, core: any, errors: any;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    plugins: [{ name: 'chat-test-locale', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  // The shared i18n modules load first and once: loading them concurrently with the catalogues
  // could evaluate them twice, leaving a catalogue registered in an instance the tests do not read.
  ui = await server.ssrLoadModule('@synwevo/ui');
  core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  [cost, refusals, payers, composer, label, copy, parts, status] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/cost.ts'), server.ssrLoadModule('/src/apps/agent/refusals.ts'),
    server.ssrLoadModule('/src/apps/agent/payers.ts'), server.ssrLoadModule('/src/apps/agent/composer.ts'),
    server.ssrLoadModule('/src/apps/agent/aiLabel.ts'), server.ssrLoadModule('/src/apps/agent/chat.messages.ts'),
    server.ssrLoadModule('/src/apps/agent/ChatParts.tsx'), server.ssrLoadModule('/src/apps/agent/ComposerStatus.tsx'),
  ]);
  // The chat's error wording and its catalogue, as the Agent page loads them (index.tsx).
  [errors] = await Promise.all([server.ssrLoadModule('/src/apps/agent/errors.tsx'), server.ssrLoadModule('/src/apps/agent/messages.ts')]);
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

// ---------------------------------------------------------------- actual composer interactions

/** Execute Chat itself with the locked compiler, as in agent-conversation.test.ts. Hooks and
 * callbacks are explicit fixtures; inspect its React elements and invoke their real handlers.
 * Child components are not mounted here: DOM/browser integration remains a separate check. */
const compiledChat = transformSync('Chat.tsx', readFileSync(new URL('../src/apps/agent/Chat.tsx', import.meta.url), 'utf8'),
  { target: 'es2022', jsx: { runtime: 'automatic' } });
assert.equal(compiledChat.errors.length, 0);
const settleChat = () => new Promise<void>((resolve) => setImmediate(resolve));
const fixtureConversation = () => ({ id: 'conv_fixture', title: 'Fixture conversation', status: 'active', head_id: null,
  scope: null, sealed: null, messages: [], event_seq: 1, updated_at: 1 });

async function chatFixture(overrides: Record<string, unknown> = {}) {
  let index = 0, tree: any;
  const values: any[] = [], effects: any[] = [], pendingEffects: (() => void)[] = [];
  const sent: string[] = [];
  let props = { conversation: fixtureConversation(), loading: false, error: undefined, connection: 'connected', configured: true,
    spaces: [], refresh: async () => {}, send: async (text: string) => { sent.push(text); },
    act: async () => {}, ...overrides };
  const hooks = {
    useId: () => 'chat-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (value: any) => { values[i] = typeof value === 'function' ? value(values[i]) : value; }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pendingEffects.push(() => {
        effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() };
      }); },
  };
  const context = vm.createContext({ setTimeout, clearTimeout });
  const modules: Record<string, any> = {
    react: { ...React, ...hooks }, 'react/jsx-runtime': jsxRuntime, 'react-router-dom': { Link },
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (source: string) => source, locale: 'en' }) },
    './ChatParts': { ...parts, useChatText: () => ({ c: (key: string) => key }) },
    './ReportDialog': { ...await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx'), useReportText: () => (key: string) => key },
  };
  const module = new vm.SourceTextModule(compiledChat.code, { context });
  await module.link(async (name) => {
    const members = modules[name] ?? await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name);
    return new vm.SyntheticModule(Object.keys(members), function () {
      for (const [key, value] of Object.entries(members)) this.setExport(key, value);
    }, { context, identifier: name });
  });
  await module.evaluate();
  const renderChat = (next: Record<string, unknown> = {}) => {
    props = { ...props, ...next }; index = 0; tree = (module.namespace as any).default(props);
    while (pendingEffects.length) pendingEffects.shift()!();
  };
  const elements = (node: any): any[] => Array.isArray(node) ? node.flatMap(elements)
    : React.isValidElement<{ children?: React.ReactNode }>(node) ? [node, ...elements(node.props.children)] : [];
  const find = (type: string, attributes: Record<string, unknown> = {}) => {
    const matches = elements(tree).filter((el) => el.type === type && Object.entries(attributes).every(([key, value]) => el.props[key] === value));
    assert.equal(matches.length, 1, `one ${type} ${JSON.stringify(attributes)}`); return matches[0].props;
  };
  const edit = (value: string) => { find('textarea').onChange({ target: { value } }); renderChat(); };
  const submit = async (via: 'form' | 'enter') => {
    let prevented = false;
    const event = { preventDefault: () => { prevented = true; }, key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false } };
    if (via === 'form') find('form').onSubmit(event); else find('textarea').onKeyDown(event);
    assert.equal(prevented, true); await settleChat(); renderChat();
  };
  renderChat();
  return { render: renderChat, find, edit, submit, sent };
}

test('Chat blocks button, form and Enter during conversation reads and read failures without losing an editable draft', async () => {
  const h = await chatFixture({ conversation: undefined, loading: true });
  h.edit('Fixture draft');
  for (const state of [
    { loading: true, error: undefined },
    { loading: false, error: new Error('Fixture read failure') },
    { loading: true, error: undefined }, // retry still has no authorized snapshot
  ]) {
    h.render(state);
    assert.equal(h.find('button', { type: 'submit' }).disabled, true);
    assert.equal(h.find('textarea').disabled, false);
    h.edit(h.find('textarea').value + '!');
    await h.submit('form'); await h.submit('enter');
    assert.deepEqual(h.sent, []);
  }
  assert.equal(h.find('textarea').value, 'Fixture draft!!!');
  h.render({ conversation: fixtureConversation(), loading: false, error: undefined });
  assert.equal(h.find('button', { type: 'submit' }).disabled, false);
  await h.submit('enter');
  assert.deepEqual(h.sent, ['Fixture draft!!!']);
  assert.equal(h.find('textarea').value, '');
});

for (const existing of [false, true]) test(`Chat waits for confirmed model setup in ${existing ? 'an existing' : 'a new'} chat`, async () => {
  const h = await chatFixture({ conversation: existing ? fixtureConversation() : undefined, configured: undefined });
  h.edit('Fixture model draft');
  for (const configured of [undefined, false]) {
    h.render({ configured });
    assert.equal(h.find('button', { type: 'submit' }).disabled, true);
    assert.equal(h.find('textarea').disabled, false);
    await h.submit('form'); await h.submit('enter');
    assert.deepEqual(h.sent, []);
    assert.equal(h.find('textarea').value, 'Fixture model draft');
  }
  h.render({ configured: true });
  assert.equal(h.find('button', { type: 'submit' }).disabled, false);
  await h.submit('form');
  assert.deepEqual(h.sent, ['Fixture model draft']);
  assert.equal(h.find('textarea').value, '');
});

test('Chat honors loading and read errors even with a snapshot, but an SSE outage alone does not block sending', async () => {
  const h = await chatFixture(); h.edit('Fixture offline draft');
  for (const state of [{ loading: true, error: undefined }, { loading: false, error: new Error('Fixture reauthorization failure') }]) {
    h.render(state);
    assert.equal(h.find('button', { type: 'submit' }).disabled, true);
    await h.submit('form'); await h.submit('enter');
    assert.deepEqual(h.sent, []);
  }
  for (const connection of ['offline', 'connecting']) {
    h.render({ loading: false, error: null, connection }); h.edit(`Fixture ${connection} draft`);
    assert.equal(h.find('button', { type: 'submit' }).disabled, false);
    await h.submit('form');
  }
  assert.deepEqual(h.sent, ['Fixture offline draft', 'Fixture connecting draft']);
});

test('Chat keeps cancellation available while sending is blocked, and preserves the draft through cancellation', async () => {
  const running = { id: 'msg_fixture', parent_id: null, role: 'assistant', status: 'running', body: '', hidden: false,
    error: null, tool_calls: [], created_at: 1, turn: { id: 'turn_fixture', status: 'running', model: 'fixture-model', credential: 'platform' } };
  let finish!: () => void;
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  const actions: { operation: string; input: Record<string, unknown> }[] = [];
  const h = await chatFixture({ conversation: { ...fixtureConversation(), head_id: running.id, messages: [running] },
    loading: true, error: new Error('Fixture read failure'), configured: undefined,
    act: (operation: string, input: Record<string, unknown>) => { actions.push({ operation, input }); return pending; } });
  h.edit('Fixture next question');
  assert.equal(h.find('textarea').disabled, false);
  await h.submit('form'); await h.submit('enter');
  assert.deepEqual(h.sent, []);
  const cancel = () => h.find('button', { type: 'button', className: 'btn' });
  assert.equal(cancel().disabled, false);
  cancel().onClick(); h.render();
  assert.equal(actions.length, 1);
  assert.equal(actions[0].operation, 'agent.turn.cancel');
  assert.equal(actions[0].input.turn_id, 'turn_fixture');
  assert.equal(cancel().disabled, true);
  finish(); await settleChat(); h.render();
  assert.equal(cancel().disabled, false);
  assert.equal(h.find('textarea').value, 'Fixture next question');
});

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
    fixtureCost({ tokens: { input_cached: 1.5, input_uncached: 1, output: 1, reasoning: 0 } }), fixtureCost({ model: '' }),
    fixtureCost({ payer: { kind: 'team', id: 'x' } }), fixtureCost({ payer: { kind: 'organization', id: 'bad id!' } }), fixtureCost({ payer: 'org' })]) {
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
  // Who the ledger charged: the personal wallet, or the organization by the name the payer list gives.
  const mine = render('en', React.createElement(parts.TurnCostLine, { cost: fixtureCost({ payer: { kind: 'user', id: 'usr_fixture' } }) }));
  assert.ok(mine.includes('<dt>Paid by</dt><dd>Personal wallet</dd>'), mine);
  const org = { kind: 'organization', id: 'org_fixture' };
  const named = render('zh-Hant', React.createElement(parts.TurnCostLine, { cost: fixtureCost({ payer: org }),
    payerName: (p: any) => (p.id === 'org_fixture' ? 'Fixture Org' : null) }));
  assert.ok(named.includes('<dt>付款方</dt><dd>組織：Fixture Org</dd>'), named);
  const unnamed = render('en', React.createElement(parts.TurnCostLine, { cost: fixtureCost({ payer: org }) }));
  assert.ok(unnamed.includes('<dd>An organization</dd>'), unnamed);
  // Servers before organization payment send no payer: no row rather than a guess.
  assert.ok(!render('en', React.createElement(parts.TurnCostLine, { cost: fixtureCost() })).includes('Paid by'));
});

// ---------------------------------------------------------------- refusals

test('each refusal maps to a translated sentence and its one next step', () => {
  const cases: [string, string, string | null][] = [
    ['billing_insufficient_funds', 'funds', 'topup'],
    ['billing_wallet_frozen', 'frozen', 'wallet'],
    ['billing_org_member_monthly_limit_exceeded', 'org_limit', 'personal'],
    ['billing_org_workspace_monthly_limit_exceeded', 'org_limit', 'personal'],
    ['billing_org_payer_not_granted', 'org_payer', 'personal'],
    ['billing_org_payer_not_member', 'org_payer', 'personal'],
    ['billing_organization_unavailable', 'org_payer', 'personal'],
    ['billing_organization_billing_not_configured', 'org_payer', 'personal'],
    ['billing_payer_forbidden', 'org_payer', 'personal'],
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
  // An organization payer cannot be topped up by the member: its own sentence and next step.
  assert.deepEqual([refusals.refusalOf({ code: 'billing_insufficient_funds' }, { organization: true }).kind,
    refusals.refusalOf({ code: 'billing_insufficient_funds' }, { organization: true }).next], ['org_funds', 'personal']);
  assert.deepEqual([refusals.refusalOf({ code: 'billing_wallet_frozen' }, { organization: true }).kind,
    refusals.refusalOf({ code: 'billing_wallet_frozen' }, { organization: true }).next], ['org_frozen', 'personal']);
  assert.equal(refusals.refusalOf({ code: 'billing_risk_breaker_open' }, { organization: true }).kind, 'breaker');
});

test('a suspended Agent access says so, without a payment next step, and tells the person whom to ask', async () => {
  const refusal = refusals.refusalOf({ code: 'agent_access_suspended' });
  assert.equal(refusal?.kind, 'access_suspended');
  assert.equal(refusal.next, null);
  // The same refusal replayed, and the same code ending a running answer, read the same.
  assert.equal(refusals.refusalOf({ code: 'idempotent_failure', detail: { code: 'agent_access_suspended' } }).kind, 'access_suspended');
  const words = { 'zh-Hans': ['管理员已暂停你的 Agent 使用权限', '联系平台管理员'],
    en: ['An administrator has suspended your Agent access', 'contact the platform administrator'],
    'zh-Hant': ['管理員已暫停你的 Agent 使用權限', '聯絡平台管理員'] } as const;
  for (const locale of LOCALES) {
    const html = render(locale, React.createElement(parts.RefusalNotice, { refusal, walletUrl: '/account/wallet', live: true, onRequote() {}, onPersonal() {} }));
    for (const text of words[locale]) assert.ok(html.includes(text), `${locale}: ${text} in ${html}`);
    // Not a wallet, limit or price problem: no top-up, settings or re-confirmation is offered.
    assert.ok(!html.includes('<a') && !html.includes('<button') && html.includes('role="alert"'), html);
    assert.ok(!/余额|餘額|balance/i.test(html), html);
  }
  // A send under a continuing consent that the server refuses for this reason says it too, not
  // the consent panel's generic "refused".
  const controls = await server.ssrLoadModule('/src/apps/agent/ConsentControls.tsx');
  const failure = render('en', React.createElement(controls.ConsentFailure, { error: { code: 'agent_access_suspended' } }));
  assert.ok(failure.includes('An administrator has suspended your Agent access'), failure);
  const own = render('en', React.createElement(controls.ConsentFailure, { error: { code: 'consent_revoked' } }));
  assert.ok(!own.includes('suspended'), own);
});

/** Every error code the merged backend can hand the chat (a refused send, a refused quote or
 *  confirmation, or the error a running answer ends with), read from the Python sources, must be
 *  either a refusal with its own sentence and next step (refusals.ts) or a specific sentence of
 *  the chat (errors.tsx) — never the generic "could not finish" — unless it is listed below with
 *  the reason no chat ever shows it. Billing errors reach the chat as "billing_" + code. */
const ROOT = fileURLToPath(new URL('../../../../', import.meta.url));
const AGENT_DIR = 'apps/suite/synwevo_suite/agent/';
const SOURCES = [...readdirSync(ROOT + AGENT_DIR).filter((f) => f.endsWith('.py')).map((f) => AGENT_DIR + f),
  'packages/core/synwevo_core/agent_safety.py',
  ...['ledger', 'admission', 'risk', 'traffic', 'payer_budgets', 'org_payers', 'models'].map((n) => `packages/core/synwevo_core/billing/${n}.py`)];
/** Codes built at run time (an HTTP status table, a period name), spelled out. */
const COMPUTED = ['provider_balance', 'provider_error', 'provider_overloaded', 'provider_rate_limited',
  ...['daily', 'weekly', 'monthly'].flatMap((p) => [`billing_payer_${p}_budget_exceeded`, `billing_cost_${p}_budget_exceeded`])];
function serverCodes(): Set<string> {
  const codes = new Set<string>(COMPUTED);
  for (const file of SOURCES) {
    const source = readFileSync(ROOT + file, 'utf8');
    for (const [, kind, code] of source.matchAll(/\b(ServiceError|ProviderError|BillingError|DeepSeekError)\(\s*(?:\d+\s*,\s*)?"([a-z_]+)"/g)) {
      if (code.endsWith('_')) continue;  // a prefix joined at run time ("billing_" + exc.code)
      codes.add(kind === 'BillingError' ? `billing_${code}` : code);
    }
  }
  return codes;
}
const NOT_IN_CHAT: [string, string][] = [
  ['agent_suspension_not_found missing_reason report_closed report_decision_invalid report_not_found safety_rule_invalid safety_rules_changed safety_rules_invalid '
    + 'billing_invalid_risk_policy billing_invalid_risk_scope billing_invalid_breaker_source',
    // The Admin risk tab's own codes (risk_allowlist_*, risk_suspension_*) need no entry: the
    // chat words any "risk_" code as a stop before dispatch.
    'administrators govern these in the Admin shell, which words them'],
  ['report_exists report_category_invalid report_excerpt_hidden', 'met only in the report dialog, which words them (ReportDialog)'],
  ['provider_consent_not_needed', 'answers the consent page itself; the consent dialog words it'],
  ['bad_key model_personal_key_unsupported memory_not_found too_many_memories transfer_policy_changed',
    'met on the Agent settings page (personal key, memories, transfer policy), which words them'],
  ['wallet_unavailable', 'the payer list (GET /api/suite/agent/payers); the composer says the organization payers cannot be loaded'],
  ['billing_quote_limit_exceeded billing_quote_requires_turn_transaction',
    'met only while asking for a quote: the budget dialog words a refused quote'],
  ['empty_message', 'the composer never sends an empty message'],
  ['unauthorized', 'an expired session: the shared client catalogue asks the person to sign in again on every page'],
  ['billing_grant_reason_required billing_invalid_grant billing_welcome_already_granted billing_freeze_not_found billing_order_already_funded '
    + 'billing_payment_authority_denied billing_payment_delegation_required billing_payment_environment_mismatch billing_payment_event_conflict '
    + 'billing_payment_evidence_untrusted billing_payment_not_succeeded billing_payment_order_mismatch billing_payment_transaction_conflict '
    + 'billing_wallet_forbidden billing_invalid_org_payer billing_org_payer_conflict billing_org_workspace_unknown billing_price_version_conflict '
    + 'billing_dispute_payment_mismatch billing_invalid_dispute billing_payment_not_credited',
    'wallet, payment, grant and organization-payer management on the account service, never on the chat path'],
  ['billing_model_authority_required billing_model_configuration_required billing_model_cost_admission_required billing_model_traffic_required '
    + 'billing_invalid_price_bands billing_invalid_price_basis billing_invalid_price_purpose '
    + 'billing_invalid_route billing_invalid_traffic_policy billing_invalid_cost_policy billing_invalid_payer_budget billing_price_band_required',
    'composition and configuration checks: the service refuses to start or an administrator\'s change is refused'],
  ['billing_attempt_not_claimable billing_attempt_not_found billing_attempt_not_pending billing_balance_cache_inconsistent billing_balance_cache_missing '
    + 'billing_billing_requires_own_transaction billing_call_not_dispatchable billing_call_not_found billing_dispatch_fenced billing_idempotency_conflict '
    + 'billing_inconsistent_usage billing_invalid_attempt_ticket billing_invalid_context billing_invalid_cost_admission billing_invalid_cost_expiry '
    + 'billing_invalid_cost_fact billing_invalid_cost_time billing_invalid_dispatch_admission billing_invalid_environment billing_invalid_evidence_digest '
    + 'billing_invalid_hold_deadline billing_invalid_identifier billing_invalid_integer billing_invalid_payer billing_invalid_price billing_invalid_rebuild '
    + 'billing_invalid_unit billing_invalid_usage billing_invalid_usage_evidence billing_journal_hold_invariant_broken billing_missing_cache_classification '
    + 'billing_missing_usage billing_model_call_already_recorded billing_payer_budget_conflict billing_payer_budget_requires_transaction '
    + 'billing_provider_request_conflict billing_retry_requires_known_platform_fault billing_run_context_conflict billing_run_not_found '
    + 'billing_run_route_conflict billing_unconfirmed_price_route billing_unexpected_retry_reason billing_unit_mismatch billing_unit_scale_conflict '
    + 'billing_unknown_sale_unit billing_usage_conflict billing_usage_evidence_mismatch billing_usage_evidence_missing billing_usage_evidence_untrusted '
    + 'billing_usage_owner_mismatch billing_usage_without_dispatch',
    'ledger invariants and recovery internals: a broken invariant is an internal fault, worded by the generic billing sentence and logged'],
];
const GENERIC = ['费用或授权状态暂不允许继续，请核对后重试。', '未能完成此操作，请核对结果后重试。'];

test('every refusal the backend can send to the chat has its own honest sentence', () => {
  const codes = serverCodes();
  // Families the scan must find, or it is reading the wrong files.
  for (const code of ['agent_access_suspended', 'billing_insufficient_funds', 'billing_risk_breaker_open', 'content_blocked_input',
    'provider_consent_required', 'model_effort_unavailable', 'billing_org_payer_not_granted']) assert.ok(codes.has(code), code);
  const elsewhere = new Map<string, string>();
  for (const [names, why] of NOT_IN_CHAT) for (const code of names.split(' ')) {
    assert.ok(codes.has(code), `${code} is listed as not shown in the chat but no longer raised (${why})`);
    elsewhere.set(code, why);
  }
  const t = (source: string) => source;
  const unworded: string[] = [], misfiled: string[] = [];
  for (const code of [...codes].sort()) {
    const refusal = refusals.refusalOf({ code });
    const source = refusal ? copy.chatCopy[refusal.text][0] : errors.agentErrorText({ code }, t);
    if (!refusal && GENERIC.includes(source)) {
      if (!elsewhere.has(code)) unworded.push(code);
      continue;
    }
    if (elsewhere.has(code)) misfiled.push(code);
    for (const locale of ['en', 'zh-Hant'] as const) assert.notEqual(core.translate(locale, source), source, `${code} in ${locale}`);
    // An empty wallet is the person's only when the ledger says so; everything else the platform
    // or provider causes (an upstream 402 or 429, a breaker) never reads as "your balance" or
    // "your quota ran out".
    if (code !== 'billing_insufficient_funds') {
      assert.ok(!/余额|餘額|额度不足|額度不足/.test(source)
        && !/\b(balance|insufficient|top up|quota)\b/i.test(core.translate('en', source)), `${code}: ${source}`);
    }
  }
  assert.deepEqual(unworded, [], 'raised to the chat with only a generic sentence');
  assert.deepEqual(misfiled, [], 'worded in the chat but listed as never shown there');
  // A data-sharing consent page's own answer is never a send refusal.
  assert.equal(refusals.refusalOf({ code: 'provider_consent_not_needed' }), null);
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
  const orgFunds = render('en', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_insufficient_funds' }, { organization: true }),
    walletUrl: '/account/wallet', onPersonal() {} }));
  assert.ok(orgFunds.includes('organization’s wallet') && orgFunds.includes('>Pay with my personal wallet</button>') && !orgFunds.includes('Top up'), orgFunds);
  const orgLimit = render('zh-Hans', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_org_member_monthly_limit_exceeded' }) }));
  assert.ok(orgLimit.includes('本月额度已用完') && !orgLimit.includes('<button'), orgLimit);
  const frozen = render('zh-Hant', React.createElement(parts.RefusalNotice, { refusal: refusals.refusalOf({ code: 'billing_wallet_frozen' }), walletUrl: '/account/wallet' }));
  assert.ok(frozen.includes('暫時凍結') && frozen.includes('href="/account/wallet"') && frozen.includes('開啟錢包'), frozen);
});

// ---------------------------------------------------------------- reporting an answer

test('a report sends the reason and note, and the answer\'s text only when the person ticks the box', async () => {
  const report = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  assert.equal(report.reportInput('conv_fixture', 'msg_fixture', report.initialReport), null);
  assert.equal(report.reportInput('conv_fixture', 'msg_fixture', { ...report.initialReport, category: 'spam' }), null);
  // Off by default: a reason alone shares nothing of the conversation.
  assert.deepEqual(report.reportInput('conv_fixture', 'msg_fixture', { ...report.initialReport, category: 'wrong' }),
    { conversation_id: 'conv_fixture', message_id: 'msg_fixture', category: 'wrong', include_excerpt: false });
  assert.deepEqual(report.reportInput('conv_fixture', 'msg_fixture', { category: 'privacy', note: '  fixture note  ', attach: true }),
    { conversation_id: 'conv_fixture', message_id: 'msg_fixture', category: 'privacy', note: 'fixture note', include_excerpt: true });
  assert.equal(report.reportInput('c', 'm', { category: 'other', note: 'x'.repeat(1200), attach: false }).note.length, 1000);
  assert.deepEqual([...report.REPORT_CATEGORIES], ['harmful', 'illegal', 'privacy', 'wrong', 'other']);
  // The server's own categories (synwevo_core.agent_safety.REPORT_CATEGORIES) and limits.
  const safety = readFileSync(ROOT + 'packages/core/synwevo_core/agent_safety.py', 'utf8');
  assert.match(safety, /REPORT_CATEGORIES = \("harmful", "illegal", "privacy", "wrong", "other"\)/);
  assert.match(safety, /MAX_NOTE = 1000\b/);
  assert.match(safety, /MAX_EXCERPT = 2000\b/);
});

test('a report ends sent, already reported, or not sent with the reason, and a retry is offered only then', async () => {
  const report = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  const sent: unknown[] = [];
  const ok = await report.sendReport(async (input: unknown) => { sent.push(input); return { id: 'arp_fixture', status: 'open', included_excerpt: false }; },
    { category: 'wrong' });
  assert.deepEqual(ok, { state: 'sent', withText: false });
  assert.deepEqual(sent, [{ category: 'wrong' }]);
  assert.deepEqual(await report.sendReport(async () => ({ included_excerpt: true }), {}), { state: 'sent', withText: true });
  const refuse = (code: unknown) => async () => { throw code; };
  assert.deepEqual(await report.sendReport(refuse({ code: 'report_exists' }), {}), { state: 'exists' });
  assert.deepEqual(await report.sendReport(refuse({ code: 'idempotent_failure', detail: { code: 'report_exists' } }), {}), { state: 'exists' });
  assert.deepEqual(await report.sendReport(refuse({ code: 'report_category_invalid' }), {}), { state: 'failed', text: 'failedCategory' });
  assert.deepEqual(await report.sendReport(refuse({ code: 'message_not_found' }), {}), { state: 'failed', text: 'failedGone' });
  assert.deepEqual(await report.sendReport(refuse({ code: 'report_excerpt_hidden' }), {}), { state: 'failed', text: 'failedHidden' });
  assert.deepEqual(await report.sendReport(refuse({ code: 'conversation_not_found' }), {}), { state: 'failed', text: 'failedGone' });
  assert.deepEqual(await report.sendReport(refuse(new TypeError('Failed to fetch')), {}), { state: 'failed', text: 'failed' });
  // A gateway's answer is not the application's: never "already reported".
  assert.deepEqual(await report.sendReport(refuse({ code: 'report_exists', upstream: true }), {}), { state: 'failed', text: 'failed' });
  const words = { 'zh-Hans': ['举报已提交', '你已经举报过这条回答', '举报没有提交成功'],
    en: ['Report sent', 'You have already reported this answer', 'The report was not sent'],
    'zh-Hant': ['檢舉已送出', '你已經檢舉過這則回答', '檢舉沒有送出成功'] } as const;
  for (const locale of LOCALES) {
    const done = render(locale, React.createElement(report.ReportStatus, { phase: { state: 'sent', withText: false } }));
    assert.ok(done.includes(words[locale][0]) && done.includes('role="status"'), done);
    const exists = render(locale, React.createElement(report.ReportStatus, { phase: { state: 'exists' } }));
    assert.ok(exists.includes(words[locale][1]) && exists.includes('role="status"'), exists);
    const failed = render(locale, React.createElement(report.ReportStatus, { phase: { state: 'failed', text: 'failed' } }));
    assert.ok(failed.includes(words[locale][2]) && failed.includes('role="alert"'), failed);
  }
  assert.ok(render('en', React.createElement(report.ReportStatus, { phase: { state: 'sent', withText: true } })).includes('with the answer’s text'));
  assert.equal(render('en', React.createElement(report.ReportStatus, { phase: { state: 'editing' } })), '');
});

test('the report dialog is a labelled form: a reason group, an optional note and an unticked attach box', async () => {
  const report = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  const words = { 'zh-Hans': ['举报这条回答', '原因', '有害或冒犯', '内容错误或误导', '备注（可选）', '附上这条回答的文字', '提交举报', '取消'],
    en: ['Report this answer', 'Reason', 'Harmful or offensive', 'Wrong or misleading', 'Note (optional)', 'Attach this answer’s text', 'Send report', 'Cancel'],
    'zh-Hant': ['檢舉這則回答', '原因', '有害或冒犯', '內容錯誤或誤導', '備註（選填）', '附上這則回答的文字', '送出檢舉', '取消'] } as const;
  for (const locale of LOCALES) {
    const html = render(locale, React.createElement(report.default, { conversationId: 'conv_fixture', messageId: 'msg_fixture',
      send: async () => ({}), onClose() {}, onReported() {} }));
    for (const text of words[locale]) assert.ok(html.includes(text), `${locale}: ${text}`);
    assert.match(html, /role="dialog" aria-modal="true" aria-labelledby="[^"]+" aria-describedby="[^"]+-intro"/);
    assert.match(html, /<fieldset[^>]*><legend>/);
    assert.equal(html.match(/type="radio"/g)?.length, 5);
    assert.equal(html.match(/type="checkbox"/g)?.length, 1);
    assert.ok(!/type="checkbox"[^>]*checked/.test(html), 'attach is off by default');
    assert.match(html, /<textarea[^>]*maxLength="1000"/);
    // The first reason takes focus when the dialog opens (keyboard users start at the choice).
    assert.match(html, /type="radio"[^>]*data-autofocus="true"/);
    assert.match(html, /<button type="submit" form="[^"]+-form" class="btn primary"/);
  }
  // Complete in three languages and not colliding with another wording of the same source.
  const copy = await server.ssrLoadModule('/src/apps/agent/report.messages.ts');
  assert.deepEqual(core.validateMessages(copy.reportMessages), []);
  for (const [key, [source, en, hant]] of Object.entries(copy.reportCopy) as [string, [string, string, string]][]) {
    const params = { count: 7 };
    const fill = (text: string) => text.replace('{count}', '7');
    assert.equal(core.translate('en', source, params), fill(en), key);
    assert.equal(core.translate('zh-Hant', source, params), fill(hant), key);
  }
});

test('every answer with text has a report control; questions and answers being written do not', async () => {
  const chat = await server.ssrLoadModule('/src/apps/agent/Chat.tsx');
  const message = (id: string, role: string, body: string, extra: Record<string, unknown> = {}) => ({ id, parent_id: null, role, status: 'done',
    body, hidden: false, error: null, turn: null, tool_calls: [], created_at: 1, ...extra });
  const conversation = { id: 'conv_fixture', title: 'Fixture', status: 'active', scope: [], sealed: false, event_seq: 1, head_id: 'msg_a2',
    messages: [message('msg_u1', 'user', 'Fixture question?'), { ...message('msg_a1', 'assistant', 'Fixture answer.'), parent_id: 'msg_u1' },
      { ...message('msg_u2', 'user', 'Again?'), parent_id: 'msg_a1' },
      { ...message('msg_a2', 'assistant', 'Being written', { turn: { id: 'turn_fixture', status: 'running' } }), parent_id: 'msg_u2' }] };
  const props = { conversation, loading: false, error: null, connection: 'connected', configured: true, spaces: [],
    act: async () => ({}), send: async () => true, refresh: async () => {} };
  for (const [locale, label] of [['en', 'Report this answer'], ['zh-Hant', '檢舉這則回答'], ['zh-Hans', '举报这条回答']] as const) {
    const html = render(locale, React.createElement(chat.default, props));
    // One control: the finished answer; not the questions, not the answer still being written.
    assert.equal(html.split(`aria-label="${label}"`).length - 1, 1, html);
    assert.match(html, new RegExp(`aria-haspopup="dialog" aria-label="${label}"`));
  }
});

// ---------------------------------------------------------------- payer selection

const me = { id: 'usr_fixture', name: 'Fixture Person' };
/** Fixture: the response shape of GET /api/suite/agent/payers (package org-payments,
 *  synwevo_suite.agent.payers.payers), with made-up wallet figures in units of scale 4. */
const UNIT4 = { code: 'CNY', scale: 4 };
function fixturePayers(overrides: Record<string, unknown> = {}, org: Record<string, unknown> = {}) {
  return { configured: true, environment: 'test', currency: 'CNY', unit: UNIT4,
    payers: [
      { kind: 'user', id: 'usr_fixture', label: null, available: '35000', frozen: false },
      { kind: 'organization', id: 'org_fixture', label: 'Fixture Org', available: '900000', frozen: false,
        monthly_limit: '200000', spent_this_month: '150000', held: '20000', remaining_this_month: '30000', ...org },
    ],
    welcome: { state: 'granted' }, ...overrides };
}

test('the payer list is read exactly as org-payments sends it; the personal wallet always comes first', () => {
  const list = payers.parsePayers(fixturePayers(), me);
  assert.equal(list.configured, true);
  assert.equal(list.environment, 'test');
  assert.deepEqual(list.unit, UNIT4);
  assert.equal(list.welcome, 'granted');
  assert.deepEqual(list.payers.map(payers.payerKey), ['user:usr_fixture', 'organization:org_fixture']);
  assert.deepEqual(list.payers[0].facts, { unit: UNIT4, available: '35000', frozen: false });
  assert.equal(list.payers[1].name, 'Fixture Org');
  assert.deepEqual(list.payers[1].facts, { unit: UNIT4, available: '900000', frozen: false,
    monthly: { limit: '200000', spent: '150000', held: '20000', remaining: '30000' } });
  // What an organization payer can still spend is the smaller of its wallet and the allowance.
  assert.equal(payers.spendable(list.payers[1]), 30000n);
  assert.equal(payers.spendable(list.payers[0]), 35000n);
  assert.equal(payers.spendable(payers.parsePayers(fixturePayers({}, { available: '-5', remaining_this_month: '30000' }), me).payers[1]), -5n);
  assert.equal(payers.spendable(payers.parsePayers(fixturePayers({}, { frozen: true }), me).payers[1]), 0n);
});

test('a missing, unconfigured or malformed payer list offers only what it can prove', () => {
  for (const value of [undefined, null, {}, 'x', { configured: false, environment: 'test', currency: 'CNY', unit: null, payers: [], welcome: null }]) {
    const list = payers.parsePayers(value, me);
    assert.deepEqual(list.payers.map(payers.payerKey), ['user:usr_fixture']);
    assert.equal(list.payers[0].facts, null);
    assert.equal(list.welcome, null);
  }
  // An unconfigured wallet lists no organization, even if entries were sent.
  assert.equal(payers.parsePayers(fixturePayers({ configured: false }), me).payers.length, 1);
  const messy = payers.parsePayers(fixturePayers({ payers: [
    { kind: 'organization', id: 'org_fixture', label: 'Fixture Org', available: '1', frozen: false,
      monthly_limit: '1', spent_this_month: '0', held: '0', remaining_this_month: '1' },
    { kind: 'organization', id: 'org_fixture', label: 'Duplicate', available: '1', frozen: false },
    { kind: 'organization', id: 'bad id!', label: 'Bad' },
    { kind: 'organization', id: 'org_nolabel', name: 'Only a name field', available: '1', frozen: false },
    { kind: 'organization', id: 'org_badfigures', label: 'Bad figures', available: '1.5', frozen: false },
    { kind: 'organization', id: 'org_nolimit', label: 'No limit', available: '10', frozen: false },
    { kind: 'user', id: 'usr_someone_else', label: null, available: '999', frozen: false },
    { kind: 'user', id: 'usr_fixture', label: null, available: '100', frozen: true },
    { kind: 'team', id: 'team_fixture', label: 'Team' },
  ], welcome: { state: 'surprise' }, unit: { code: 'CNY', scale: 2.5 } }), me);
  assert.deepEqual(messy.payers.map(payers.payerKey),
    ['user:usr_fixture', 'organization:org_fixture', 'organization:org_badfigures', 'organization:org_nolimit']);
  // Without a valid unit no figure is trusted; an unknown welcome state is ignored.
  assert.ok(messy.payers.every((p: any) => p.facts === null));
  assert.equal(messy.welcome, null);
  const figures = payers.parsePayers(fixturePayers({ payers: [
    { kind: 'organization', id: 'org_badfigures', label: 'Bad figures', available: '1.5', frozen: false,
      monthly_limit: '1', spent_this_month: '0', held: '0', remaining_this_month: '1' },
    { kind: 'organization', id: 'org_nolimit', label: 'No limit', available: '10', frozen: false },
    { kind: 'organization', id: 'org_frozenflag', label: 'Frozen flag', available: '10', frozen: 'no',
      monthly_limit: '1', spent_this_month: '0', held: '0', remaining_this_month: '1' },
  ] }), me);
  assert.ok(figures.payers.slice(1).every((p: any) => p.facts === null), JSON.stringify(figures.payers));
  assert.equal(payers.spendable(figures.payers[1]), null);
});

test('the remembered payer is used only while still offered; the personal default sends nothing extra', () => {
  const list = payers.parsePayers(fixturePayers(), me).payers;
  assert.equal(payers.payerKey(payers.choosePayer(list, { kind: 'organization', id: 'org_fixture' })), 'organization:org_fixture');
  assert.equal(payers.payerKey(payers.choosePayer(list, { kind: 'organization', id: 'org_revoked' })), 'user:usr_fixture');
  assert.equal(payers.payerKey(payers.choosePayer(list, null)), 'user:usr_fixture');
  assert.deepEqual(payers.payerInput(list[0], me.id), {});
  // agent.turn.quote takes the payer flat (org-payments QuoteTurn: payer_kind, payer_id).
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

test('a quote or consent pays only for the payer it names', () => {
  const org = { kind: 'organization', id: 'org_fixture' }, mine = { kind: 'user', id: me.id };
  // Terms issued before organization payment existed carry no payer: they are personal.
  assert.deepEqual(payers.payerOf({}, me.id), mine);
  assert.deepEqual(payers.payerOf({ payer: { kind: 'user', id: me.id } }, me.id), mine);
  assert.deepEqual(payers.payerOf({ payer: { kind: 'user' } }, me.id), mine);
  assert.deepEqual(payers.payerOf({ payer: org }, me.id), org);
  assert.equal(payers.payerOf({ payer: { kind: 'user', id: 'usr_someone_else' } }, me.id), null);
  assert.equal(payers.payerOf({ payer: { kind: 'organization' } }, me.id), null);
  assert.equal(payers.payerOf({ payer: { kind: 'team', id: 'x' } }, me.id), null);
  assert.equal(payers.samePayer(payers.payerOf({ payer: org }, me.id), mine), false);
  assert.equal(payers.samePayer(payers.payerOf({}, me.id), mine), true);
  assert.equal(payers.samePayer(null, mine), false);
});

const fixtureStatus = { available: true, environment: 'test', unit: CNY, per_call_hold: '100',
  balance: { booked: '350', held: '50', available: '300' },
  limit: { configured: true, enabled: true, usage_state: 'available', remaining: '2000', period: 'daily' } };
function moneyProps(overrides: Record<string, unknown> = {}) {
  const list = payers.parsePayers(fixturePayers(), me);
  return { status: fixtureStatus, offer: null, payers: list.payers, payersError: false, payer: list.payers[0], setPayer() {},
    walletUrl: '/account/wallet', accountUrl: '/account', environment: list.environment, welcome: null, ...overrides };
}

test('the composer shows the payer choice, balance, limit and a low-balance top-up', () => {
  const props = moneyProps();
  const html = render('en', React.createElement(status.ComposerStatus, { money: props }));
  assert.ok(html.includes('<select') && html.includes('Personal wallet') && html.includes('Organization: Fixture Org'), html);
  assert.ok(html.includes('Balance CN¥3.00') && html.includes('CN¥0.50 on hold') && html.includes('CN¥20.00 left today'), html);
  assert.ok(html.includes('Test wallet'), html);
  assert.ok(html.includes('may not cover many more answers') && html.includes('href="/account/wallet"'), html);
  const single = render('zh-Hans', React.createElement(status.ComposerStatus, { money: { ...props, payers: [props.payers[0]],
    status: { ...fixtureStatus, balance: { booked: '50', held: '0', available: '50' } } } }));
  assert.ok(!single.includes('<select') && single.includes('个人钱包') && single.includes('余额不足以完成一次调用'), single);
  const missing = render('en', React.createElement(status.ComposerStatus, { money: { ...props, payers: [props.payers[0]],
    status: { ...fixtureStatus, limit: { configured: false, enabled: false, usage_state: null, remaining: null, period: null } } } }));
  assert.ok(missing.includes('No spending limit set') && missing.includes('href="/agent/settings"'), missing);
  // Without the paid service the composer says so instead of inventing a balance.
  const closed = render('zh-Hans', React.createElement(status.ComposerStatus, { money: { ...props, payers: [props.payers[0]],
    status: { available: false, reason: 'not_configured' } } }));
  assert.ok(closed.includes('付费模型服务尚未开放') && !closed.includes('余额 ¥') && !closed.includes('去充值'), closed);
  // The personal balance falls back to the payer list when the billing status has none.
  const listed = render('en', React.createElement(status.ComposerStatus, { money: { ...props, status: null } }));
  assert.ok(listed.includes('Balance CN¥3.50') && !listed.includes('Balance unavailable'), listed);
});

test('an organization payer shows its wallet and this member\'s monthly allowance, and never a personal top-up', () => {
  // The organization's figures are in wallet units of scale 4; the offer's hold in the same unit.
  const status4 = { ...fixtureStatus, unit: UNIT4, per_call_hold: '10000', balance: { booked: '35000', held: '0', available: '35000' } };
  const list = payers.parsePayers(fixturePayers(), me).payers;
  const props = moneyProps({ status: status4, payer: list[1] });
  const html = render('en', React.createElement(status.ComposerStatus, { money: props }));
  assert.ok(html.includes('Organization balance CN¥90.00') && html.includes('CN¥3.00 of your CN¥20.00 monthly allowance left'), html);
  assert.ok(!html.includes('left today') && !html.includes('Top up') && !html.includes('href="/account/wallet"'), html);
  // 30000 left of the allowance is three holds: low, and the way on is the personal wallet.
  assert.ok(html.includes('monthly allowance from it is low') && html.includes('>Pay with my personal wallet</button>'), html);
  const empty = render('zh-Hant', React.createElement(status.ComposerStatus, { money: moneyProps({ status: status4,
    payer: payers.parsePayers(fixturePayers({}, { remaining_this_month: '9999' }), me).payers[1] }) }));
  assert.ok(empty.includes('不足以完成一次呼叫') && empty.includes('改用個人錢包'), empty);
  const plenty = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ status: status4,
    payer: payers.parsePayers(fixturePayers({}, { remaining_this_month: '200000' }), me).payers[1] }) }));
  assert.ok(!plenty.includes('is low') && !plenty.includes('cannot cover'), plenty);
  // Figures in another unit than the offer's are shown but never compared with its hold.
  const otherUnit = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ payer: list[1] }) }));
  assert.ok(otherUnit.includes('Organization balance') && !otherUnit.includes('is low') && !otherUnit.includes('cannot cover'), otherUnit);
  // An organization listed without figures shows none.
  const bare = payers.parsePayers(fixturePayers({}, { available: 'x' }), me).payers[1];
  const unknown = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ payer: bare }) }));
  assert.ok(unknown.includes('Balance unavailable') && !unknown.includes('allowance left'), unknown);
});

test('a frozen wallet is said plainly, with the wallet page or the personal wallet as the way on', () => {
  const list = payers.parsePayers(fixturePayers({ payers: [
    { kind: 'user', id: 'usr_fixture', label: null, available: '35000', frozen: true },
    { kind: 'organization', id: 'org_fixture', label: 'Fixture Org', available: '900000', frozen: true,
      monthly_limit: '200000', spent_this_month: '0', held: '0', remaining_this_month: '200000' },
  ] }), me).payers;
  const mine = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ payers: list, payer: list[0], status: null }) }));
  assert.ok(mine.includes('Wallet frozen') && mine.includes('payment issue') && mine.includes('href="/account/wallet"') && !mine.includes('Top up'), mine);
  const org = render('zh-Hans', React.createElement(status.ComposerStatus, { money: moneyProps({ payers: list, payer: list[1] }) }));
  assert.ok(org.includes('钱包已冻结') && org.includes('组织钱包已冻结') && org.includes('改用个人钱包</button>'), org);
});

test('the welcome allowance is mentioned once granted, or how to receive it', () => {
  const granted = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ welcome: 'granted' }) }));
  assert.ok(granted.includes('one-time welcome allowance was added'), granted);
  const verify = render('zh-Hant', React.createElement(status.ComposerStatus, { money: moneyProps({ welcome: 'email_unverified' }) }));
  assert.ok(verify.includes('驗證你的電子郵件地址') && verify.includes('href="/account"'), verify);
  for (const welcome of ['already', 'ineligible', 'capacity', 'deferred', null]) {
    const html = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ welcome }) }));
    assert.ok(!html.includes('welcome allowance'), String(welcome));
  }
  // It is about the personal wallet: not shown while an organization pays.
  const list = payers.parsePayers(fixturePayers(), me).payers;
  const org = render('en', React.createElement(status.ComposerStatus, { money: moneyProps({ welcome: 'granted', payer: list[1] }) }));
  assert.ok(!org.includes('welcome allowance'), org);
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

test('the terms, AI use rules and pricing are the portal\'s pages on the configured public site', async () => {
  const site = 'https://site.fixture.test';
  assert.equal(composer.publicHref(site, 'terms', 'zh-Hans', 'https://docwave.fixture.test'), 'https://site.fixture.test/terms');
  assert.equal(composer.publicHref(`${site}/ignored/path?x=1`, 'ai-use', 'en', 'https://docwave.fixture.test'), 'https://site.fixture.test/en/ai-use');
  assert.equal(composer.publicHref(site, 'pricing', 'zh-Hant', 'https://docwave.fixture.test'), 'https://site.fixture.test/zh-Hant/pricing');
  // A single-host install (no separate site, or the site is this host) stays on this host.
  assert.equal(composer.publicHref(undefined, 'terms', 'en', 'https://docwave.fixture.test'), '/en/terms');
  assert.equal(composer.publicHref(site, 'terms', 'en', site), '/en/terms');
  for (const bad of ['javascript:alert(1)', 'not a url', 'ftp://site.fixture.test']) {
    assert.equal(composer.publicHref(bad, 'terms', 'zh-Hans', 'https://docwave.fixture.test'), '/terms', bad);
  }
  // The same addresses the portal itself links (its publicPath) and the server renders (PUBLIC_PATHS).
  const portal = await server.ssrLoadModule(ROOT + 'apps/portal/web/src/publicRoutes.ts');
  for (const page of ['terms', 'ai-use', 'pricing', 'privacy'] as const) for (const locale of LOCALES) {
    assert.equal(composer.publicHref(site, page, locale, 'https://docwave.fixture.test'), site + portal.publicPath(page, locale), `${page} ${locale}`);
    assert.deepEqual(portal.publicRoute(portal.publicPath(page, locale)), { page, locale });
  }
  const served = readFileSync(ROOT + 'apps/portal/synwevo_portal/public.py', 'utf8');
  assert.match(served, /PAGES = \("home", "privacy", "service", "terms", "ai-use", "pricing"\)/);
});

test('the cost dialog links the terms in force and says the first paid run accepts them', async () => {
  const dialog = await server.ssrLoadModule('/src/apps/agent/BudgetDialog.tsx');
  const words = { 'zh-Hans': ['即表示你接受当前生效版本的服务条款、AI 使用规范与定价', '首次付费运行会记录这些版本', '>服务条款<', '>AI 使用规范<', '>定价<', '在新标签页打开'],
    en: ['By confirming a paid run you accept the versions in force', 'Your first paid run records those versions', '>Terms of Service<', '>AI use rules<', '>Pricing<', 'opens in a new tab'],
    'zh-Hant': ['即表示你接受目前生效版本的服務條款、AI 使用規範與定價', '首次付費執行會記錄這些版本', '>服務條款<', '>AI 使用規範<', '>定價<', '在新分頁開啟'] } as const;
  const prefix = { 'zh-Hans': '', en: '/en', 'zh-Hant': '/zh-Hant' } as const;
  for (const locale of LOCALES) {
    const html = render(locale, React.createElement(dialog.default, { intent: { conversation_id: 'conv_fixture', text: 'Fixture question' },
      onQuote: async () => { throw new Error('fixture'); }, onConfirm: async () => {}, onClose() {}, siteUrl: 'https://site.fixture.test' }));
    for (const text of words[locale]) assert.ok(html.includes(text), `${locale}: ${text}`);
    for (const page of ['terms', 'ai-use', 'pricing']) {
      assert.ok(html.includes(`href="https://site.fixture.test${prefix[locale]}/${page}" target="_blank" rel="noopener noreferrer"`), `${locale} ${page}`);
    }
    assert.ok(!html.includes('localhost'), html);
    assert.match(html, /<section class="notice"[^>]*aria-labelledby="([^"]+-terms)"[\s\S]*<p id="\1"/);
  }
  // The data-sharing consent's privacy and AI-use links go to the public site too, never this host.
  const consent = readFileSync(new URL('../src/apps/agent/ProviderConsent.tsx', import.meta.url), 'utf8');
  assert.ok(!/href="\/(privacy|ai-use)"/.test(consent) && consent.includes('publicHref(auth?.site_url, "privacy", locale)'), 'ProviderConsent links');
  const index = readFileSync(new URL('../src/apps/agent/index.tsx', import.meta.url), 'utf8');
  assert.ok(index.includes('siteUrl={auth?.site_url}'), 'the dialog gets the server-controlled site origin');
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
  // A previous run time below the server's current minimum (one whole call) is not reused.
  assert.equal(composer.renewalParams(previous, { ...offer, run_seconds_min: 301 }, previousTerms.unit), null);
  assert.deepEqual(composer.renewalParams(previous, { ...offer, run_seconds_min: 300 }, previousTerms.unit), previous);
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

test('an exported conversation is the visible text only, with the AI label after every answer', async () => {
  const ex = await server.ssrLoadModule('/src/apps/agent/exportChat.ts');
  const words = { you: 'You', agent: 'Synwevo Agent', hidden: 'Hidden after an access change', label: 'Generated by AI. May contain errors. For reference only.' };
  const message = (id: string, role: string, body: string, extra: Record<string, unknown> = {}) => ({ id, parent_id: null, role, status: 'done', body,
    hidden: false, error: null, turn: null, tool_calls: [], created_at: 1, ...extra });
  const text = ex.conversationMarkdown('Fixture\nconversation', [
    message('msg_u1', 'user', 'Fixture question?'),
    message('msg_a1', 'assistant', 'Fixture answer.\n\n| a | b |\n|---|---|\n| 1 | 2 |', { turn: { id: 'turn_1', status: 'succeeded', cost: { charged_minor: '99' } },
      tool_calls: [{ id: 'call_1', input: { secret_fixture_input: true } }] }),
    message('msg_u2', 'user', 'Second fixture question'),
    message('msg_a2', 'assistant', '', { status: 'failed', error: { code: 'provider_timeout', message: 'x' } }),
    message('msg_a3', 'assistant', 'withheld fixture text', { hidden: true }),
    message('msg_s', 'system', 'internal fixture text'),
  ], words);
  assert.equal(text, '# Fixture conversation\n\n## You\n\nFixture question?\n\n## Synwevo Agent\n\nFixture answer.\n\n| a | b |\n|---|---|\n| 1 | 2 |'
    + `\n\n— ${words.label}\n\n## You\n\nSecond fixture question\n\n## Synwevo Agent\n\n${words.hidden}\n`);
  // Every answer that has text carries the label exactly once; questions never do.
  assert.equal(text.split(words.label).length - 1, 1);
  for (const absent of ['secret_fixture_input', 'withheld fixture text', 'internal fixture text', '99']) assert.ok(!text.includes(absent), absent);
  assert.equal(ex.exportFileName('Fixture: a/b*c?', new Date(Date.UTC(2026, 9, 5, 12))), 'Fixture a b c-2026-10-05.md');
  assert.equal(ex.exportFileName('  \n ', new Date(Date.UTC(2026, 0, 2, 12))), 'conversation-2026-01-02.md');
  assert.equal(ex.exportFileName('比较两个模型的价格', new Date(Date.UTC(2026, 9, 5, 12))), '比较两个模型的价格-2026-10-05.md');
  assert.ok(ex.exportFileName('x'.repeat(300), new Date(Date.UTC(2026, 9, 5, 12))).length <= 80 + '-2026-10-05.md'.length);
});

test('the chat catalogue is complete in three languages and does not collide with other wording', () => {
  assert.deepEqual(core.validateMessages(copy.chatMessages), []);
  const seen = new Map<string, string>();
  for (const [key, [source, en, hant]] of Object.entries(copy.chatCopy) as [string, [string, any, string]][]) {
    const english = typeof en === 'string' ? en : en.other;
    if (seen.has(source)) assert.equal(seen.get(source), `${english}|${hant}`, `duplicate source ${key}`);
    seen.set(source, `${english}|${hant}`);
    // The first registered wording wins; ours must be the one the reader sees.
    assert.equal(core.translate('zh-Hant', source, { count: 2, amount: '1', period: 'p', name: 'n', model: 'm', n: 1, total: 2, lang: 'l', budget: 'b', limit: 'x' }),
      hant.replace(/\{(\w+)\}/g, (_: string, p: string) => ({ count: '2', amount: '1', period: 'p', name: 'n', model: 'm', n: '1', total: '2', lang: 'l', budget: 'b', limit: 'x' } as any)[p]), key);
  }
});
