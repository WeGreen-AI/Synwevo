/** Explicit budget/accounting fixtures. No live model, billing service, funds or browser claims. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.ts';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { PayerBudgetInput, PayerBudgetItem, PayerBudgets } from '../src/apps/agent/payer-budget.ts';

const storage = new Map<string, string>();
globalThis.window ??= { location: new URL('http://127.0.0.1/agent/settings'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {} } as any;
globalThis.sessionStorage ??= { getItem: (k: string) => storage.get(k) ?? null,
  setItem: (k: string, v: string) => storage.set(k, v), removeItem: (k: string) => storage.delete(k) } as any;
globalThis.localStorage ??= globalThis.sessionStorage;
let server: any, model: any, components: any, copy: any, ui: any, client: any, core: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'payer-budget-test-exports', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/src/apps/agent/PayerBudget.tsx')) return code + '\nexport { BudgetPanel, PayerBudgetSection, ErrorNotice };';
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  // The shared i18n modules load first and once: loading them concurrently with the catalogues
  // could evaluate them twice, leaving a catalogue registered in an instance the tests do not read.
  ui = await server.ssrLoadModule('@synwevo/ui');
  core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  [model, components, copy, client] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/payer-budget.ts'), server.ssrLoadModule('/src/apps/agent/PayerBudget.tsx'),
    server.ssrLoadModule('/src/apps/agent/payer-budget.messages.ts'), server.ssrLoadModule('@synwevo/auth-client'),
  ]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });

const unit = { code: 'FIXTURE', scale: 2 };
function fixture(overrides: Partial<PayerBudgetItem> = {}): PayerBudgetItem {
  return { unit: { ...unit }, revision: 0, configured: false, enabled: false,
    daily_budget: '0', weekly_budget: '0', monthly_budget: '0', usage_state: 'available',
    periods: {
      daily: { start: 1790899200, end: 1790985600, spent: '0', held: '0', remaining: '0' },
      weekly: { start: 1790553600, end: 1791158400, spent: '0', held: '0', remaining: '0' },
      monthly: { start: 1790812800, end: 1793491200, spent: '0', held: '0', remaining: '0' },
    }, history: [], ...overrides };
}
function envelope(budget = fixture()): PayerBudgets { return { environment: 'test', timezone: 'UTC', budgets: [budget] }; }
function committed(input: PayerBudgetInput, overrides: Partial<PayerBudgetItem> = {}) {
  const amounts = { enabled: input.enabled, daily_budget: input.daily_budget,
    weekly_budget: input.weekly_budget, monthly_budget: input.monthly_budget };
  const revision = input.expected_revision + 1;
  return fixture({ ...amounts, configured: true, revision,
    history: [{ ...amounts, revision, created_at: 1790899230 }], ...overrides });
}
function makeEditor(send = async (input: PayerBudgetInput) => ({ status: 'succeeded', output: committed(input) }), initial = fixture()) {
  let keys = 0;
  return { editor: model.createBudgetEditor(initial, send, () => `fixture-key-${++keys}`), keyCount: () => keys };
}
function proposal(editor: any) {
  editor.edit({ enabled: true, daily_budget: '12.34', weekly_budget: '100.00', monthly_budget: '90071992547409.93' });
  editor.review();
}
function render(name: string, props: object, locale = 'en') {
  ui.setLocale(locale);
  return renderToStaticMarkup(React.createElement(components[name], props));
}
const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

test('zero and positive major decimals stay exact through int64 bounds and every supported scale', () => {
  for (let scale = 0; scale <= 18; scale++) {
    assert.equal(model.parsePayerBudget('0', scale), '0');
    const tiny = scale ? `0.${'0'.repeat(scale - 1)}1` : '1';
    assert.equal(model.parsePayerBudget(tiny, scale), '1');
  }
  assert.equal(model.parsePayerBudget('000.00', 2), '0');
  assert.equal(model.parsePayerBudget('90071992547409.93', 2), '9007199254740993');
  assert.equal(model.parsePayerBudget('92233720368547758.07', 2), '9223372036854775807');
  assert.equal(model.parsePayerBudget('9.223372036854775807', 18), '9223372036854775807');
  assert.equal(model.budgetDraft(fixture({ daily_budget: '9007199254740993' }), unit).daily_budget, '90071992547409.93');
  for (const bad of ['', ' ', ' 0', '0 ', '-0', '+0', '0e0', '.0', '0.', '0.000', '1.001',
    'NaN', 'Infinity', '1e3', '1,000', '1_000', '１２', '1\n', '92233720368547758.08', '0'.repeat(129)]) {
    assert.throws(() => model.parsePayerBudget(bad, 2), undefined, bad);
  }
  for (const scale of [-1, 19, 1.5, NaN]) assert.throws(() => model.parsePayerBudget('0', scale));
});

test('remote snapshots are bounded, exact and do not fabricate unconfigured budgets or missing usage', () => {
  model.validatePayerBudgets(envelope());
  for (const usage_state of ['capacity', 'inconsistent'] as const) {
    model.validatePayerBudgets(envelope(fixture({ usage_state, periods: null })));
    assert.throws(() => model.validatePayerBudgets(envelope(fixture({ usage_state }))));
  }
  for (const invalid of [fixture({ periods: null }), fixture({ daily_budget: '1' }), fixture({ enabled: true }),
    fixture({ revision: 1 }), fixture({ configured: true }), fixture({ daily_budget: 100 as any }),
    fixture({ unit: { code: 'FIXTURE', scale: 19 } }), fixture({ daily_budget: '9223372036854775808' })]) {
    assert.throws(() => model.validatePayerBudgets(envelope(invalid)));
  }
  assert.throws(() => model.validatePayerBudgets({ ...envelope(), timezone: 'Asia/Hong_Kong' }));
  assert.throws(() => model.validatePayerBudgets({ ...envelope(), environment: 'unknown' }));
  assert.throws(() => model.validatePayerBudgets({ ...envelope(), budgets: [fixture(), fixture()] }));
  const budgets = Array.from({ length: 100 }, (_, n) => fixture({ unit: { code: `FIXTURE${n}`, scale: 2 } }));
  model.validatePayerBudgets({ ...envelope(), budgets });
  assert.throws(() => model.validatePayerBudgets({ ...envelope(), budgets: [...budgets, fixture()] }));
});

test('legacy period totals may exceed int64 without hiding limits or rounding usage', () => {
  const budget = fixture();
  for (const period of Object.values(budget.periods!)) period.spent = '18446744073709551614';
  model.validatePayerBudgets(envelope(budget));
  const html = render('BudgetUsage', { budget });
  assert.match(text(html), /184,467,440,737,095,516\.14 FIXTURE/);
  const editor = makeEditor(undefined, budget).editor;
  proposal(editor);
  assert.equal(editor.getSnapshot().phase, 'review');
  budget.periods!.daily.spent = '92233720368547758070001';
  assert.throws(() => model.validatePayerBudgets(envelope(budget)));
});

test('read, edit, review, and back never save or choose nonzero default amounts', async () => {
  let calls = 0;
  const { editor, keyCount } = makeEditor(async (input) => { calls++; return { status: 'succeeded', output: committed(input) }; });
  assert.deepEqual(editor.getSnapshot().draft, { enabled: false, daily_budget: '0.00', weekly_budget: '0.00', monthly_budget: '0.00' });
  await editor.save(); await editor.retry();
  editor.refresh(fixture()); proposal(editor); editor.back();
  assert.equal(editor.getSnapshot().draft.daily_budget, '12.34');
  assert.equal(calls, 0); assert.equal(keyCount(), 0);
  editor.review(); await editor.save();
  assert.equal(calls, 1); assert.equal(keyCount(), 1);
  assert.equal(editor.getSnapshot().phase, 'saved');
  assert.equal(editor.getSnapshot().current.monthly_budget, '9007199254740993');
});

test('uncertain saves retain the exact reviewed payload and key, lock all replacements and suppress double submission', async () => {
  let finish: ((result: any) => void) | undefined;
  const requests: { input: PayerBudgetInput; key: string }[] = [];
  const { editor, keyCount } = makeEditor((input, key?: string) => {
    requests.push({ input, key: key! });
    if (requests.length === 1) return Promise.reject(new TypeError('fixture dropped response'));
    return new Promise((resolve) => { finish = resolve; });
  });
  proposal(editor);
  const reviewed = editor.getSnapshot().review;
  assert.ok(Object.isFrozen(reviewed));
  await editor.save();
  assert.equal(editor.getSnapshot().phase, 'uncertain');
  const before = editor.getSnapshot();
  editor.edit({ enabled: false, daily_budget: '0', weekly_budget: '0', monthly_budget: '0' });
  editor.discard(); editor.back(); editor.review(); editor.refresh(fixture()); await editor.save();
  assert.equal(editor.getSnapshot(), before);
  const retry = editor.retry();
  await editor.retry(); await editor.save();
  assert.equal(editor.getSnapshot().phase, 'saving');
  assert.equal(requests.length, 2); assert.equal(keyCount(), 1);
  assert.equal(requests[0].input, reviewed); assert.equal(requests[1].input, reviewed);
  assert.equal(requests[0].key, requests[1].key);
  finish!({ status: 'succeeded', output: committed(reviewed) });
  await retry;
  assert.equal(editor.getSnapshot().phase, 'saved');
});

test('revision conflicts preserve all input and require refreshing and reviewing the new before/after amounts', async () => {
  let calls = 0;
  const requests: { input: PayerBudgetInput; key: string }[] = [];
  const { editor } = makeEditor(async (input, key?: string) => {
    calls++; requests.push({ input, key: key! });
    if (calls === 1) throw new client.ApiError(409, 'billing_payer_budget_conflict', 'untrusted fixture prose');
    return { status: 'succeeded', output: committed(input) };
  });
  proposal(editor);
  const draft = editor.getSnapshot().draft;
  await editor.save();
  assert.equal(editor.getSnapshot().phase, 'conflict'); assert.deepEqual(editor.getSnapshot().draft, draft);
  editor.review(); await editor.save(); assert.equal(calls, 1);
  const newer = committed({ unit_code: unit.code, expected_revision: 1, enabled: false,
    daily_budget: '300', weekly_budget: '400', monthly_budget: '500' });
  editor.refresh(newer);
  assert.deepEqual(editor.getSnapshot().draft, draft);
  await editor.save(); assert.equal(calls, 1);
  editor.review();
  assert.equal(editor.getSnapshot().review.expected_revision, 2);
  const html = text(render('BudgetReview', { state: editor.getSnapshot() }));
  assert.match(html, /Current: 3.00 FIXTURE/); assert.match(html, /After change: 12.34 FIXTURE/);
  await editor.save();
  assert.notEqual(requests[1].key, requests[0].key);
  assert.equal(editor.getSnapshot().current.revision, 3);
});

test('unresolved results, malformed success, mismatched unit or amounts never unlock a new save', async () => {
  for (const response of [
    (input: PayerBudgetInput) => ({ status: 'running', output: committed(input) }),
    (input: PayerBudgetInput) => ({ status: 'partial', output: committed(input) }),
    (input: PayerBudgetInput) => ({ status: 'succeeded', output: envelope(committed(input)) }),
    (input: PayerBudgetInput) => ({ status: 'succeeded', output: committed(input, { unit: { code: 'OTHER', scale: 2 } }) }),
    (input: PayerBudgetInput) => ({ status: 'succeeded', output: committed(input, { revision: 99 }) }),
    (input: PayerBudgetInput) => ({ status: 'succeeded', output: committed(input, { daily_budget: '1' }) }),
  ]) {
    const { editor } = makeEditor(async (input) => response(input) as any);
    proposal(editor); await editor.save();
    assert.equal(editor.getSnapshot().phase, 'uncertain');
    assert.equal(model.budgetLocked(editor.getSnapshot()), true);
    assert.equal(editor.getSnapshot().review.daily_budget, '1234');
  }
});

test('a stale read cannot roll back a recovered saved revision', async () => {
  const { editor } = makeEditor();
  proposal(editor); await editor.save();
  const saved = editor.getSnapshot();
  editor.refresh(fixture());
  assert.equal(editor.getSnapshot(), saved);
  assert.equal(editor.getSnapshot().current.revision, 1);
});

test('transport, gateway, timeout, in-flight, and permission failures during replay preserve uncertainty', () => {
  for (const failure of [new TypeError('fixture'), new client.ApiError(503, 'server_error', ''),
    new client.ApiError(403, 'gateway_blocked', '', undefined, true), new client.ApiError(408, 'timeout', ''),
    new client.ApiError(429, 'rate_limited', ''), new client.ApiError(409, 'idempotency_conflict', ''),
    new client.ApiError(409, 'run_in_progress', ''), new client.ApiError(409, 'run_result_unavailable', '')]) {
    assert.equal(model.uncertainBudgetFailure(failure, false), true);
  }
  assert.equal(model.uncertainBudgetFailure(new client.ApiError(403, 'app_access_denied', ''), false), false);
  assert.equal(model.uncertainBudgetFailure(new client.ApiError(403, 'app_access_denied', ''), true), true);
  assert.equal(model.uncertainBudgetFailure(new client.ApiError(409, 'idempotent_failure', '', { code: 'billing_payer_budget_conflict' }), true), false);
});

test('capacity and inconsistent usage permit explicit disable/limit changes while displaying no invented usage', async () => {
  for (const usage_state of ['capacity', 'inconsistent'] as const) {
    const budget = fixture({ configured: true, revision: 2, enabled: true, daily_budget: '100', weekly_budget: '200', monthly_budget: '300',
      usage_state, periods: null });
    const { editor } = makeEditor(async (input) => ({ status: 'succeeded', output: committed(input, { usage_state, periods: null }) }), budget);
    const html = text(render('BudgetUsage', { budget }));
    assert.match(html, /New paid calls are blocked/);
    assert.doesNotMatch(html, /0.00 FIXTURE|Period start|Period end/);
    assert.match(html, /1.00 FIXTURE/);
    editor.edit({ enabled: false, daily_budget: '0', weekly_budget: '0', monthly_budget: '0' });
    editor.review(); await editor.save();
    assert.equal(editor.getSnapshot().phase, 'saved');
    assert.equal(editor.getSnapshot().current.enabled, false);
    assert.equal(editor.getSnapshot().current.periods, null);
  }
});

test('current usage displays the server’s still-held and settled amounts exactly, without computing a wallet balance', () => {
  const budget = fixture({ configured: true, revision: 1, enabled: true, daily_budget: '10000',
    periods: { ...fixture().periods!, daily: { start: 1790899200, end: 1790985600,
      spent: '9007199254740993', held: '101', remaining: '0' } } });
  model.validatePayerBudgets(envelope(budget));
  const html = text(render('BudgetUsage', { budget }));
  assert.match(html, /90,071,992,547,409.93 FIXTURE/); assert.match(html, /1.01 FIXTURE/);
  assert.match(html, /Remaining budget 0.00 FIXTURE/); assert.match(html, /UTC/);
  assert.doesNotMatch(html, /Wallet balance/);
});

test('the budget panel states the ledger exactly in the reader\'s locale: the limit as typed and the period\'s settled, held and remaining amounts, which add up to it', () => {
  // FIXTURE: the production wallet unit's shape, CNY at scale 6 (micro-yuan); the amounts are arithmetic examples.
  const micro = { code: 'CNY', scale: 6 };
  const budget = fixture({ unit: micro, configured: true, revision: 1, enabled: true, daily_budget: '10000000', weekly_budget: '30000000', monthly_budget: '100000000',
    periods: { ...fixture().periods!, daily: { start: 1790899200, end: 1790985600, spent: '5111820', held: '524289', remaining: '4363891' } } });
  model.validatePayerBudgets(envelope(budget));
  // 5.11182 + 0.524289 + 4.363891 = 10.00: rounded to the fen they would not (5.11 + 0.52 + 4.36 = 9.99).
  const words = { en: ['Limit CN¥10.00', 'Settled costs CN¥5.11182', 'All still-held costs CN¥0.524289', 'Remaining budget CN¥4.363891'],
    'zh-Hans': ['上限 ¥10.00', '已结算费用 ¥5.11182', '全部仍预留费用 ¥0.524289', '剩余预算 ¥4.363891'],
    'zh-Hant': ['上限 CN¥10.00', '已結算費用 CN¥5.11182', '全部仍預留費用 CN¥0.524289', '剩餘預算 CN¥4.363891'] } as const;
  for (const locale of ['en', 'zh-Hans', 'zh-Hant'] as const) {
    const html = text(render('BudgetUsage', { budget }, locale));
    for (const word of words[locale]) assert.ok(html.includes(word), `${locale}: ${word} in ${html}`);
    assert.ok(!html.includes('10.000000') && !html.includes('¥5.11 ') && !html.includes('¥4.36 ') && !html.includes(' CNY'), `${locale}: ${html}`);
  }
  // A limit equal to one call's hold: the limit and the remaining budget are the same server value and read the same;
  // a settled cost below one fen is the figure itself, not "under ¥0.01" and not nothing.
  const fine = fixture({ unit: micro, configured: true, revision: 1, enabled: true, daily_budget: '524289',
    periods: { ...fixture().periods!, daily: { start: 1790899200, end: 1790985600, spent: '200', held: '0', remaining: '524089' } } });
  model.validatePayerBudgets(envelope(fine));
  const html = text(render('BudgetUsage', { budget: fine }, 'en'));
  assert.ok(html.includes('Limit CN¥0.524289') && html.includes('Settled costs CN¥0.0002') && html.includes('All still-held costs CN¥0.00')
    && html.includes('Remaining budget CN¥0.524089'), html);
  const untouched = text(render('BudgetUsage', { budget: fixture({ unit: micro, configured: true, revision: 1, enabled: true, daily_budget: '524289',
    periods: { ...fixture().periods!, daily: { start: 1790899200, end: 1790985600, spent: '0', held: '0', remaining: '524289' } } }) }, 'en'));
  assert.ok(untouched.includes('Limit CN¥0.524289') && untouched.includes('Remaining budget CN¥0.524289') && !untouched.includes('CN¥0.52 '), untouched);
  // The review and history show the limits the same way, in the reader's locale.
  const { editor } = makeEditor(undefined, budget);
  editor.edit({ enabled: true, daily_budget: '12.34', weekly_budget: '30.00', monthly_budget: '100.00' }); editor.review();
  const review = text(render('BudgetReview', { state: editor.getSnapshot() }, 'zh-Hans'));
  assert.ok(review.includes('当前：¥10.00') && review.includes('更改后：¥12.34'), review);
});

test('history is bounded to the latest 20 versions, with exact amounts and UTC timestamps', () => {
  const history = Array.from({ length: 20 }, (_, n) => ({ revision: 30 - n, enabled: n % 2 === 0,
    daily_budget: '9007199254740993', weekly_budget: '0', monthly_budget: '0', created_at: 1790899230 - n }));
  const budget = fixture({ configured: true, revision: 30, history });
  model.validatePayerBudgets(envelope(budget));
  const html = render('BudgetHistory', { budget });
  assert.match(text(html), /Latest 20 budget revisions/);
  assert.equal((html.match(/<li>/g) ?? []).length, 20);
  assert.match(text(html), /90,071,992,547,409.93 FIXTURE/); assert.match(text(html), /UTC/);
  assert.doesNotMatch(text(html), /All history/);
  assert.throws(() => model.validatePayerBudgets(envelope({ ...budget, history: [...history, { ...history[19], revision: 10 }] })));
});

test('all service refusals use localized safe copy, including wrapped replay errors', () => {
  const codes = ['payer_budget_conflict', 'payer_budget_not_configured', 'payer_budget_disabled',
    'payer_daily_budget_exceeded', 'payer_weekly_budget_exceeded', 'payer_monthly_budget_exceeded',
    'payer_budget_capacity', 'payer_budget_inconsistent', 'invalid_integer', 'unknown_sale_unit'];
  for (const code of codes) {
    const error = new client.ApiError(409, 'idempotent_failure', 'fixture private server prose', { code: `billing_${code}` });
    assert.notEqual(components.payerBudgetErrorText(error, 'refused'), 'refused', code);
    for (const locale of ['zh-Hans', 'en', 'zh-Hant']) {
      const html = render('ErrorNotice', { error, fallback: 'refused' }, locale);
      assert.doesNotMatch(html, /fixture private server prose/);
      if (locale === 'en') assert.doesNotMatch(text(html), /[㐀-鿿]/);
    }
  }
});

test('forms expose exact decimal text inputs, labels, native keyboard controls and concrete review', () => {
  const { editor } = makeEditor();
  let html = render('BudgetEditorForm', { editor, environment: 'test' });
  const inputs = [...html.matchAll(/<input\b[^>]*type="text"[^>]*>/g)].map((match) => match[0]);
  assert.equal(inputs.length, 3);
  for (const input of inputs) {
    assert.match(input, /inputMode="decimal"/); assert.match(input, /aria-describedby=/); assert.match(input, /value="0.00"/);
    const id = input.match(/id="([^"]+)"/)![1]; assert.ok(html.includes(`for="${id}"`));
  }
  assert.doesNotMatch(html, /type="number"/);
  proposal(editor);
  html = render('BudgetEditorForm', { editor, environment: 'test' });
  assert.match(text(html), /Current: 0.00 FIXTURE/); assert.match(text(html), /After change: 12.34 FIXTURE/);
  assert.match(text(html), /90,071,992,547,409.93 FIXTURE/); assert.match(text(html), /Confirm and save test budgets/);
  assert.ok([...html.matchAll(/<input\b[^>]*>/g)].every((match) => match[0].includes('disabled')));
});

test('uncertain and conflict form controls only expose the appropriate manual next step', async () => {
  const { editor } = makeEditor(async () => { throw new TypeError('fixture lost response'); });
  proposal(editor); await editor.save();
  const html = render('BudgetEditorForm', { editor, environment: 'live' });
  assert.match(text(html), /Check the original save request/);
  assert.doesNotMatch(text(html), /Confirm and save budgets|Back to editing|Discard unsaved edits/);
  assert.ok([...html.matchAll(/<input\b[^>]*>/g)].every((match) => match[0].includes('disabled')));
  const conflict = makeEditor(async () => { throw new client.ApiError(409, 'billing_payer_budget_conflict', ''); }).editor;
  proposal(conflict); await conflict.save();
  const conflictHTML = render('BudgetEditorForm', { editor: conflict, environment: 'live' });
  assert.match(conflictHTML, /<button[^>]*type="submit"[^>]*disabled/);
  assert.match(conflictHTML, /value="12.34"/);
});

test('test/live notice, personal scope and three-language dictionaries remain truthful', () => {
  assert.deepEqual(core.validateMessages(copy.payerBudgetMessages), []);
  for (const [source, en, hant] of Object.values(copy.payerBudgetCopy) as string[][]) {
    assert.ok(source && en && hant);
    assert.equal(core.translate('en', source), en); assert.equal(core.translate('zh-Hant', source), hant);
  }
  for (const locale of ['zh-Hans', 'en', 'zh-Hant']) {
    const section = render('PayerBudgetSection', { owner: `fixture:${locale}` }, locale);
    const panel = render('BudgetPanel', { initial: envelope(), owner: `fixture:${locale}` }, locale);
    if (locale === 'en') {
      const prose = text(section + panel);
      assert.doesNotMatch(prose, /[㐀-鿿]/);
      assert.match(prose, /Each run still needs a quote and your confirmation/);
      assert.match(prose, /no tool, payment or recurring payment authority/);
      assert.match(prose, /weeks on Monday/); assert.match(prose, /original reservation period/);
      assert.match(prose, /does not cancel settled bills/); assert.match(prose, /Test environment/);
    }
  }
  const live = render('BudgetPanel', { initial: { ...envelope(), environment: 'live' }, owner: 'fixture:live' });
  assert.match(text(live), /Live environment/); assert.doesNotMatch(text(live), /Test environment/);
});
