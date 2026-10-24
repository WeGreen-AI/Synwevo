/** Send-and-go (owner decision 2026-10-23): a person types a request, presses send, and the
 *  answer starts at once. The page issues exactly one `agent.turn.start` per send (with the payer
 *  inline), never a quote or a consent, and no dialog stands between the click and the run.
 *  Every response here is an explicit FIXTURE in the shapes of the shared contract (design §3);
 *  no server, model, ledger or browser is involved. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as router from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, ui: any, core: any, client: any, chat: any, setupModule: any, parts: any;
before(async () => {
  server = await testServer();
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  for (const path of ['/src/shared/messages.ts', '/src/apps/agent/messages.ts']) await server.ssrLoadModule(path);
  [client, chat, setupModule, parts] = await Promise.all([server.ssrLoadModule('@synwevo/auth-client'), server.ssrLoadModule('/src/apps/agent/Chat.tsx'),
    server.ssrLoadModule('/src/apps/agent/AgentSetup.tsx'), server.ssrLoadModule('/src/apps/agent/ChatParts.tsx')]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));
const plain = (value: any) => JSON.parse(JSON.stringify(value));
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node)
  ? [node, ...all(node.props.children), ...all(node.props.footer), ...all(node.props.aside), ...all(node.props.status), ...all(node.props.maximum)] : [];
const spaces = [{ workspace_id: 'ws_a', name: 'Fixture a', role: 'member', processing: 'all' }];
const conversation = () => ({ id: 'conv_fixture', title: 'Fixture conversation', status: 'active', head_id: null,
  scope: null, sealed: null, messages: [], event_seq: 1, updated_at: 1 });

// ---------------------------------------------------------------- FIXTURE responses (design §3)
const UNIT = { code: 'CNY', scale: 2 };
const rate = (numerator: string, denominator = '1') => ({ numerator, denominator });
const fixtureOffer = () => ({ environment: 'test', model: 'fixture-model', confirmed_route: 'fixture-route', route_version: 'fixture-v2',
  price_id: 'price_fixture_2', unit: UNIT, per_tokens: 1, terms_ref: 'fixture:terms', per_call_hold: '39',
  rates: { input_uncached: rate('2'), input_cached: rate('1', '25'), output: rate('8') }, max_calls_limit: 12, run_seconds_limit: 3600, run_seconds_min: 150 });
const fixtureStatus = (overrides: Record<string, unknown> = {}) => ({ available: true, environment: 'test', model: 'fixture-model', unit: UNIT, per_call_hold: '39',
  balance: { booked: '1000', held: '0', available: '1000' },
  limit: { configured: true, enabled: true, usage_state: 'available', remaining: '900', period: 'daily' },
  // Addendum AD-1: the run names the price and route version in force, which every send repeats as seen_*.
  run: { maximum: '468', default: '468', cap: null, source: 'default', per_call_hold: '39', max_calls: 12, run_seconds: 3600, usable: true,
    price_id: 'price_fixture_2', route_version: 'fixture-v2' },
  price_notice: null, ...overrides });
const fixtureSetup = (overrides: Record<string, unknown> = {}) => ({ required: true, complete: true,
  provider_consent: { provider: 'deepseek', version: '2026-10-05', accepted: true, accepted_version: '2026-10-05' },
  terms: { complete: true, documents: [{ id: 'terms', version: '2026-10-24', effective: '2026-10-24', accepted: true },
    { id: 'ai-use', version: '2026-10-05', effective: '2026-10-05', accepted: true }, { id: 'pricing', version: '2026-10-24', effective: '2026-10-24', accepted: true }] },
  limits: { configured: true, enabled: true, revision: 1, unit: UNIT, suggested: null }, ...overrides });
const fixturePayers = () => ({ configured: true, environment: 'test', currency: 'CNY', unit: UNIT,
  payers: [{ kind: 'user', id: 'usr_fixture', label: null, available: '1000', frozen: false },
    { kind: 'organization', id: 'org_fixture', label: 'Fixture Org', available: '5000', frozen: false,
      monthly_limit: '2000', spent_this_month: '0', held: '0', remaining_this_month: '2000' }], welcome: null });

/** What the composer showed (status.run), repeated on every send (addendum AD-1). */
const SEEN = { seen_price_id: 'price_fixture_2', seen_route_version: 'fixture-v2' };

/** Hooks are fixtures; the component body and event handlers are compiled production code (as in agent-context). */
async function compiled(file: string, initialProps: any, overrides: Record<string, any> = {}, entry = 'default', globals: Record<string, unknown> = {}) {
  let index = 0, props = initialProps, tree: any, dirty = false;
  const values: any[] = [], effects: any[] = [], pending: (() => void)[] = [];
  const hooks = { useId: () => 'go-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (next: any) => { const value = typeof next === 'function' ? next(values[i]) : next;
        if (!Object.is(value, values[i])) { values[i] = value; dirty = true; } }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
    useMemo: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, value: fn() }; return values[i].value; },
    useEffect: (fn: any, deps: any[]) => { const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) pending.push(() => {
        effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() };
      }); },
  };
  const imports = { react: { ...React, ...hooks }, 'react/jsx-runtime': jsxRuntime, 'react-router-dom': router,
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (source: string, params: any) => core.translate('en', source, params), locale: 'en' }) }, ...overrides };
  const code = transformSync(file, readFileSync(new URL(`../src/apps/agent/${file}`, import.meta.url), 'utf8'),
    { target: 'es2022', jsx: { runtime: 'automatic' } });
  assert.equal(code.errors.length, 0);
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, JSON, Promise, ...globals });
  const module = new vm.SourceTextModule(code.code, { context });
  await module.link(async (name) => {
    const members = imports[name] ?? (name.endsWith('.css') ? {} : await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name));
    return new vm.SyntheticModule(Object.keys(members), function () {
      for (const [key, value] of Object.entries(members)) this.setExport(key, value);
    }, { context, identifier: name });
  });
  await module.evaluate();
  const fn = entry === 'app' ? (module.namespace as any).agentRoutes()[0].props.element.props.children.type : (module.namespace as any)[entry];
  const rerender = (next = {}) => {
    props = { ...props, ...next };
    let loops = 0;
    do { assert.ok(loops++ < 20, 'fixture render stabilizes'); dirty = false; index = 0; tree = fn(props); while (pending.length) pending.shift()!(); } while (dirty);
    return tree;
  };
  const find = (predicate: (el: any) => boolean) => { const matches = all(tree).filter(predicate); assert.equal(matches.length, 1, 'one matching element'); return matches[0]; };
  rerender();
  return { render: rerender, find, tree: () => tree, elements: () => all(tree),
    unmount: () => effects.forEach((effect) => effect?.cleanup?.()) };
}

/** The actual AgentApp (index.tsx) over FIXTURE reads and writes. */
/** `route: null` opens the page without a conversation (a new one is created by the send). */
async function appFixture({ billing = true, setup = fixtureSetup(), status: initialStatus = fixtureStatus() as any,
  route: initial = 'conv_fixture' as string | null, offer: initialOffer = fixtureOffer() as any, globals = {} as Record<string, unknown> } = {}) {
  const writes: any[] = [], reads: string[] = [], navigations: string[] = [];
  let failure: unknown, route: string | undefined = initial ?? undefined, status = initialStatus, offer = initialOffer;
  const h = await compiled('index.tsx', {}, {
    'react-router-dom': { ...router, useMatch: (path: string) => path === '/agent/c/:cid' && route ? { params: { cid: route } } : null,
      useLocation: () => ({ pathname: route ? `/agent/c/${route}` : '/agent' }), useNavigate: () => (path: string) => navigations.push(path) },
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (key: string) => key, locale: 'en' }), useDialog: () => ({}) },
    '@synwevo/auth-client': { ...client, useSession: () => ({ me: { id: 'usr_fixture', display_name: 'Fixture user' }, state: { site_url: 'https://site.fixture.test' } }),
      get: async () => fixturePayers() },
    '../../shared/catalog': { useApp: () => ({ app: { id: 'agent' } }) },
    './useConversation': { useConversation: () => ({ value: route ? { ...conversation(), id: route } : undefined, refresh: async () => {}, connection: 'connected' }) },
    './useHistory': { useHistory: () => ({ rows: [], cursor: null }) },
    '../../shared/ops': { newKey: () => `fixture_key_${writes.length}`, read: async (operation: string) => {
      reads.push(operation);
      if (operation === 'agent.settings.get') return { active: true, billing: { required: billing } };
      if (operation === 'agent.workspace_policy.list') return { workspaces: spaces };
      if (operation === 'agent.billing.status') return status;
      if (operation === 'agent.billing.offer') return offer;
      if (operation === 'agent.setup.get') return setup;
      return null;
    }, op: async (operation: string, input: any, options: any) => {
      writes.push({ operation, input, options }); if (failure) throw failure;
      return { status: 'succeeded', output: operation === 'agent.conversation.create' ? { id: 'conv_new_fixture' } : { turn_id: 'turn_fixture', user_message_id: 'msg_u', assistant_message_id: 'msg_a' } };
    } },
  }, 'app', globals);
  await settle(); h.render(); await settle(); h.render(); await settle(); h.render();
  const fixture = { ...h, writes, reads, navigations, fail: (value: any) => { failure = value; },
    /** The server's state moves on (an administrator adopted a new price): later reads return these. */
    serve: (next: { status?: any; offer?: any }) => { status = next.status ?? status; offer = next.offer ?? offer; },
    route: (id?: string) => { route = id; h.render(); },
    chat: () => h.find((el) => el.type === chat.default).props,
    money: () => fixture.chat().status.props.money,
    card: () => all(fixture.chat().aside).filter((el) => el.type === setupModule.default),
    dialogs: () => h.elements().filter((el) => typeof el.props.onQuote === 'function' || typeof el.props.onConfirm === 'function') };
  return fixture;
}

test('a billed send with setup complete issues exactly one agent.turn.start, with no quote, consent or dialog', async () => {
  const h = await appFixture();
  assert.equal(await h.chat().send('Fixture question'), true);
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.turn.start']);
  assert.deepEqual(plain(h.writes[0].input), { conversation_id: 'conv_fixture', text: 'Fixture question', ...SEEN });
  assert.equal(h.dialogs().length, 0); assert.equal(h.card().length, 0);
  // The composer is never disabled for money reasons and the maximum comes from status.run.
  assert.equal(h.chat().status.props.disabled, false);
  assert.equal(h.chat().maximum.type, parts.TurnMaximum);
  assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximum', amount: '468', unit: UNIT });
  h.unmount();
});

test('a new conversation is created first, then started in one request; context and file refs stay at top level', async () => {
  const h = await appFixture({ route: null });
  const refs = [{ kind: 'form', workspace_id: 'ws_a', resource_id: 'form_fixture', snapshot: 'a'.repeat(64) }];
  const files = [{ file_id: 'file_fixture', snapshot: 'b'.repeat(64) }];
  assert.equal(await h.chat().send('Fixture first', refs, files), true);
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.conversation.create', 'agent.turn.start']);
  assert.deepEqual(plain(h.writes[1].input), { conversation_id: 'conv_new_fixture', text: 'Fixture first', context_refs: refs, file_refs: files, ...SEEN });
  assert.deepEqual(h.navigations, ['/agent/c/conv_new_fixture']);
  h.unmount();
});

test('regenerate goes straight to agent.turn.start too, without attaching the selected sources', async () => {
  const h = await appFixture();
  await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' });
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.turn.start']);
  assert.deepEqual(plain(h.writes[0].input), { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture', ...SEEN });
  assert.equal(h.dialogs().length, 0);
  h.unmount();
});

test('an organization payer is named inline on the start; the personal default sends nothing extra', async () => {
  const h = await appFixture();
  const money = h.money();
  assert.equal(money.payer.kind, 'user');
  money.setPayer(money.payers[1]); h.render();
  assert.equal(h.money().payer.id, 'org_fixture');
  await h.chat().send('Fixture paid by the organization');
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture paid by the organization', payer_kind: 'organization', payer_id: 'org_fixture', ...SEEN });
  await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' });
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture', payer_kind: 'organization', payer_id: 'org_fixture', ...SEEN });
  for (const write of h.writes) assert.ok(!('quote_id' in write.input) && !('consent_id' in write.input), JSON.stringify(write.input));
  h.unmount();
});

test('no code path of the page issues a quote or a continuing consent', async () => {
  const h = await appFixture();
  await h.chat().send('Fixture'); await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'm' });
  assert.ok(h.writes.every((w) => !/^agent\.(turn\.quote|quote\.|consent\.)/.test(w.operation)), h.writes.map((w) => w.operation).join());
  h.unmount();
  const dir = new URL('../src/apps/agent/', import.meta.url);
  for (const file of readdirSync(dir)) {
    if (!/\.tsx?$/.test(file)) continue;
    const source = readFileSync(new URL(file, dir), 'utf8');
    assert.doesNotMatch(source, /agent\.turn\.quote|agent\.quote\.|agent\.consent\./, file);
  }
  for (const gone of ['BudgetDialog.tsx', 'budgetSources.ts', 'ConsentControls.tsx', 'consent.ts', 'consent.messages.ts', 'budget.messages.ts']) {
    assert.ok(!readdirSync(dir).includes(gone), `${gone} removed`);
  }
});

test('a send while setup is incomplete keeps the draft, issues nothing and opens the setup card with focus', async () => {
  const h = await appFixture({ setup: fixtureSetup({ complete: false, terms: { complete: false, documents: fixtureSetup().terms.documents.map((d: any) => ({ ...d, accepted: false })) } }) });
  assert.equal(h.card().length, 0);
  assert.equal(await h.chat().send('Fixture draft'), false); h.render();
  assert.deepEqual(h.writes, []);
  assert.equal(h.card().length, 1);
  const card = h.card()[0].props;
  assert.equal(card.focus, true); assert.equal(card.hasDraft, true); assert.equal(card.organizationPayer, false);
  assert.equal(h.chat().sentDraft, 0);
  // Finishing the card sends the kept draft in one request and clears the composer.
  await card.onComplete(); h.render();
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.turn.start']);
  assert.deepEqual(plain(h.writes[0].input), { conversation_id: 'conv_fixture', text: 'Fixture draft', ...SEEN });
  assert.equal(h.chat().sentDraft, 1);
  assert.equal(h.card().length, 0);
  h.unmount();
});

test('a price notice is shown without blocking the send', async () => {
  const notice = { previous: { ...fixtureOffer(), route_version: 'fixture-v1', price_id: 'price_fixture_1', per_call_hold: '30' }, previous_run_at: 1_700_000_000 };
  const h = await appFixture({ status: fixtureStatus({ price_notice: notice }) });
  const prices = await server.ssrLoadModule('/src/apps/agent/PriceNotice.tsx');
  assert.equal(all(h.chat().aside).filter((el) => el.type === prices.default).length, 1);
  assert.equal(await h.chat().send('Fixture at the new price'), true);
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.turn.start']);
  h.unmount();
});

test('an admission refusal keeps the draft in the composer and renders the refusal with its next step', async () => {
  const compiledChat = transformSync('Chat.tsx', readFileSync(new URL('../src/apps/agent/Chat.tsx', import.meta.url), 'utf8'), { target: 'es2022', jsx: { runtime: 'automatic' } });
  assert.equal(compiledChat.errors.length, 0);
  async function chatFixture(send: any, extra: Record<string, unknown> = {}) {
    const reports = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
    const picker = await server.ssrLoadModule('/src/apps/agent/ContextPicker.tsx');
    return compiled('Chat.tsx', { conversation: conversation(), loading: false, error: undefined, connection: 'connected', configured: true,
      spaces, send, act: async () => {}, refresh: async () => {}, ...extra }, {
        './ContextPicker': { ...picker, useContextText: () => (key: string) => key },
        './ChatParts': { ...parts, useChatText: () => ({ c: (key: string) => key }) },
        './ReportDialog': { ...reports, useReportText: () => (key: string) => key },
      });
  }
  const sends: string[] = []; const opened: unknown[] = [];
  let outcome: any = new client.ApiError(409, 'terms_acceptance_required', 'fixture');
  const h = await chatFixture(async (text: string) => { sends.push(text); if (outcome instanceof Error) throw outcome; return outcome; },
    { onSetup: (draft: unknown) => opened.push(draft) });
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture draft' } }); h.render();
  const submit = async () => { h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); h.render(); };
  await submit();
  assert.deepEqual(sends, ['Fixture draft']);
  assert.equal(h.find((el) => el.type === 'textarea').props.value, 'Fixture draft');
  let notice = h.find((el) => el.type === parts.RefusalNotice).props;
  assert.equal(notice.refusal.kind, 'terms'); assert.equal(notice.refusal.next, 'setup'); assert.equal(notice.live, true);
  assert.equal(typeof notice.onSetup, 'function'); assert.equal(notice.onRetry, undefined);
  notice.onSetup(); h.render();
  assert.deepEqual(plain(opened), [{ text: 'Fixture draft' }]);
  // The step now says what is needed; the stale refusal is not left beside it, the message stays.
  assert.equal(h.elements().filter((el) => el.type === parts.RefusalNotice).length, 0);
  assert.equal(h.find((el) => el.type === 'textarea').props.value, 'Fixture draft');
  // A changed price: "send again" re-sends the kept draft, with no dialog.
  outcome = new client.ApiError(409, 'billing_model_configuration_changed', 'fixture');
  await submit();
  notice = h.find((el) => el.type === parts.RefusalNotice).props;
  assert.equal(notice.refusal.kind, 'price_changed'); assert.equal(notice.refusal.next, 'retry');
  outcome = true; notice.onRetry(); await settle(); h.render();
  assert.deepEqual(sends, ['Fixture draft', 'Fixture draft', 'Fixture draft']);
  assert.equal(h.find((el) => el.type === 'textarea').props.value, '');
  assert.equal(h.elements().filter((el) => el.type === parts.RefusalNotice).length, 0);
  h.unmount();
  // A refused regenerate: "send again at the new price" repeats that regenerate, not the composer text.
  const acts: any[] = [];
  let actOutcome: any = new client.ApiError(409, 'billing_price_changed', 'fixture', { current: {} });
  const answered = { ...conversation(), head_id: 'msg_a', messages: [
    { id: 'msg_u', parent_id: null, role: 'user', status: 'done', body: 'Fixture question', hidden: false, error: null, turn: null, tool_calls: [], created_at: 1 },
    { id: 'msg_a', parent_id: 'msg_u', role: 'assistant', status: 'done', body: 'Fixture answer', hidden: false, error: null,
      turn: { id: 'turn_a', status: 'succeeded', model: 'fixture-model', credential: 'platform' }, tool_calls: [], created_at: 2 }] };
  const r = await chatFixture(async () => true, { conversation: answered,
    act: async (operation: string, input: any) => { acts.push([operation, plain(input)]); if (actOutcome) throw actOutcome; } });
  const regenerate = r.elements().filter((el) => el.type === 'button' && typeof el.props.onClick === 'function'
    && strings(el.props.children) === core.translate('en', '重新生成回答'));
  assert.equal(regenerate.length, 1);
  regenerate[0].props.onClick(); await settle(); r.render();
  const stale = r.find((el) => el.type === parts.RefusalNotice).props;
  assert.deepEqual([stale.refusal.kind, stale.refusal.next, stale.refusal.text], ['price_changed', 'retry', 'refusedPriceUpdated']);
  actOutcome = undefined; stale.onRetry(); await settle(); r.render();
  assert.deepEqual(acts, [['agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_a' }],
    ['agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_a' }]]);
  assert.equal(r.elements().filter((el) => el.type === parts.RefusalNotice).length, 0);
  r.unmount();
});

// ---------------------------------------------------------------- addendum AD-1: the price shown is the price sent

test('without a run bound to show (older server, or none read yet) the send names no price, and the server does not check one', async () => {
  const h = await appFixture({ status: fixtureStatus({ run: null }) });
  await h.chat().send('Fixture legacy');
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture legacy' });
  h.unmount();
  // A run without the price fields (a server before AD-1) is the same: both are omitted, never one.
  const old = await appFixture({ status: fixtureStatus({ run: { ...fixtureStatus().run, price_id: undefined, route_version: undefined } }) });
  await old.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' });
  assert.deepEqual(plain(old.writes.at(-1).input), { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' });
  old.unmount();
});

test('a price adopted while the page was open: the send is refused, the draft stays, the new price is read and shown, and one click sends at it', async () => {
  const h = await appFixture();
  // FIXTURE: an administrator adopted a new sale price; the server refuses the stale seen_* (AD-1 shape).
  const current = { price_id: 'price_fixture_3', route_version: 'fixture-v3', maximum: '600', per_call_hold: '50', unit: UNIT };
  h.fail(new client.ApiError(409, 'billing_price_changed', '价格或模型已更新，本次没有发送，也没有扣费。请查看新价格后重新发送。', { current }));
  h.serve({ status: fixtureStatus({ per_call_hold: '50', run: { ...fixtureStatus().run, maximum: '600', default: '600', per_call_hold: '50',
    price_id: 'price_fixture_3', route_version: 'fixture-v3' } }), offer: { ...fixtureOffer(), route_version: 'fixture-v3', price_id: 'price_fixture_3', per_call_hold: '50' } });
  const before = h.reads.filter((r) => r === 'agent.billing.status').length;
  await assert.rejects(h.chat().send('Fixture at the shown price'), (e: any) => e.code === 'billing_price_changed');
  await settle(); h.render(); await settle(); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture at the shown price', ...SEEN });
  assert.equal(h.chat().sentDraft, 0, 'the message stays in the composer');
  assert.ok(h.reads.filter((r) => r === 'agent.billing.status').length > before, 'the status is read again');
  // The composer now shows the new maximum, and a price notice (information, not a dialog) says what changed.
  assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximum', amount: '600', unit: UNIT });
  const prices = await server.ssrLoadModule('/src/apps/agent/PriceNotice.tsx');
  const notices = all(h.chat().aside).filter((el) => el.type === prices.default);
  assert.equal(notices.length, 1);
  assert.equal(notices[0].props.notice.previous.price_id, 'price_fixture_2');
  assert.equal(notices[0].props.offer.price_id, 'price_fixture_3');
  assert.equal(h.dialogs().length, 0); assert.equal(h.card().length, 0);
  // The refusal is the existing price_changed kind with the one-click "send again at the new price".
  const refusals = await server.ssrLoadModule('/src/apps/agent/refusals.ts');
  const refusal = refusals.refusalOf({ code: 'billing_price_changed', detail: { current } });
  assert.deepEqual([refusal.kind, refusal.next, refusal.text], ['price_changed', 'retry', 'refusedPriceUpdated']);
  // Sending again carries what is now shown and starts.
  h.fail(undefined);
  assert.equal(await h.chat().send('Fixture at the shown price'), true);
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture at the shown price',
    seen_price_id: 'price_fixture_3', seen_route_version: 'fixture-v3' });
  h.unmount();
});

test('the status is read again when the window regains focus or becomes visible, and every 5 minutes while visible', async () => {
  const listeners: Record<string, (() => void)[]> = {}, timers: { fn: () => void; ms: number }[] = [];
  let now = 1_000_000;
  const fakeWindow = { addEventListener: (type: string, fn: () => void) => { (listeners[`window:${type}`] ||= []).push(fn); },
    removeEventListener: (type: string, fn: () => void) => { listeners[`window:${type}`] = (listeners[`window:${type}`] || []).filter((f) => f !== fn); },
    setInterval: (fn: () => void, ms: number) => { timers.push({ fn, ms }); return timers.length; }, clearInterval: (n: number) => { timers[n - 1] = { fn() {}, ms: 0 }; } };
  const fakeDocument = { visibilityState: 'visible', getElementById: () => null,
    addEventListener: (type: string, fn: () => void) => { (listeners[`document:${type}`] ||= []).push(fn); },
    removeEventListener: (type: string, fn: () => void) => { listeners[`document:${type}`] = (listeners[`document:${type}`] || []).filter((f) => f !== fn); } };
  const h = await appFixture({ globals: { window: fakeWindow, document: fakeDocument, Date: { now: () => now } } });
  const count = () => h.reads.filter((r) => r === 'agent.billing.status').length;
  const fire = async (key: string) => { for (const fn of listeners[key] ?? []) fn(); await settle(); h.render(); await settle(); };
  const first = count();
  now += 60_000; await fire('window:focus');
  assert.equal(count(), first + 1, 'focus reads the status again');
  // focus and visibilitychange often arrive together: one read, not two.
  await fire('document:visibilitychange');
  assert.equal(count(), first + 1);
  now += 60_000; await fire('document:visibilitychange');
  assert.equal(count(), first + 2, 'becoming visible reads it again');
  fakeDocument.visibilityState = 'hidden'; now += 60_000; await fire('document:visibilitychange');
  assert.equal(count(), first + 2, 'a hidden page does not read');
  // The periodic read runs at most every 5 minutes, and only while the page is visible.
  const tick = timers.find((t) => t.ms > 0)!;
  assert.ok(tick.ms <= 5 * 60_000);
  now += 5 * 60_000; tick.fn(); await settle(); h.render();
  assert.equal(count(), first + 2, 'not while hidden');
  fakeDocument.visibilityState = 'visible'; tick.fn(); await settle(); h.render();
  assert.equal(count(), first + 3, 'visible again and 5 minutes on');
  now += 60_000; tick.fn(); await settle(); h.render();
  assert.equal(count(), first + 3, 'never sooner than 5 minutes');
  h.unmount();
  assert.ok(Object.values(listeners).every((list) => list.length === 0), 'listeners are removed');
});
