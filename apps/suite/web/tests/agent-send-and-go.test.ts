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
async function appFixture({ billing = true, setup: initialSetup = fixtureSetup() as any, status: initialStatus = fixtureStatus() as any,
  route: initial = 'conv_fixture' as string | null, offer: initialOffer = fixtureOffer() as any, globals = {} as Record<string, unknown> } = {}) {
  const writes: any[] = [], reads: string[] = [], navigations: string[] = [];
  let failure: unknown, route: string | undefined = initial ?? undefined, status = initialStatus, offer = initialOffer, setup = initialSetup;
  /** Agent settings is a route inside the same AgentApp (no conversation is open there). */
  let onSettings = false;
  /** Errors the next requests meet, one each, before `failure` (e.g. a lost connection); a Promise
   *  instead holds that request until it settles (the server still answering it), then answers it. */
  const next: unknown[] = [];
  /** FIXTURE of the server's idempotency record (capabilities `_prior` / `_replay`): a key the
   *  server answered with a refusal is answered again with 409 idempotent_failure carrying that
   *  code; a request that never reached it (a network failure) leaves no record. */
  const answered = new Map<string, unknown>();
  const h = await compiled('index.tsx', {}, {
    'react-router-dom': { ...router, useMatch: (path: string) => path === '/agent/c/:cid' && route && !onSettings ? { params: { cid: route } } : null,
      useLocation: () => ({ pathname: onSettings ? '/agent/settings' : route ? `/agent/c/${route}` : '/agent' }), useNavigate: () => (path: string) => navigations.push(path) },
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
      writes.push({ operation, input, options });
      const replayed = options?.key && answered.get(`${operation}:${options.key}`);
      if (replayed) throw new client.ApiError(409, 'idempotent_failure', 'fixture', { run_id: 'run_fixture', code: (replayed as any).code });
      const error = next.length ? next.shift() : failure;
      if (error instanceof Promise) await error;
      else if (error) {
        if (options?.key && !((error as any).status === 0 || (error as any).upstream)) answered.set(`${operation}:${options.key}`, error);
        throw error;
      }
      return { status: 'succeeded', output: operation === 'agent.conversation.create' ? { id: 'conv_new_fixture' } : { turn_id: 'turn_fixture', user_message_id: 'msg_u', assistant_message_id: 'msg_a' } };
    } },
  }, 'app', globals);
  await settle(); h.render(); await settle(); h.render(); await settle(); h.render();
  const fixture = { ...h, writes, reads, navigations, fail: (value: any) => { failure = value; },
    failNext: (...errors: unknown[]) => { next.push(...errors); },
    /** The server's state moves on (an administrator adopted a new price): later reads return these. */
    serve: (later: { status?: any; offer?: any; setup?: any }) => {
      status = later.status !== undefined ? later.status : status; offer = later.offer ?? offer; setup = later.setup ?? setup; },
    route: (id?: string) => { route = id; h.render(); },
    /** Open Agent settings (true) or go back to the conversation (false), then let the reads settle. */
    settings: async (open: boolean) => { onSettings = open; h.render(); for (let i = 0; i < 3; i++) { await settle(); h.render(); } },
    chat: () => h.find((el) => el.type === chat.default).props,
    money: () => fixture.chat().status.props.money,
    card: () => all(fixture.chat().aside).filter((el) => el.type === setupModule.default),
    dialogs: () => h.elements().filter((el) => typeof el.props.onQuote === 'function' || typeof el.props.onConfirm === 'function') };
  return fixture;
}

/** The actual Chat (Chat.tsx) with FIXTURE send/act functions in the page's place. */
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
/** A transcript with one answer (it can be regenerated). */
const answered = () => ({ ...conversation(), head_id: 'msg_a', messages: [
  { id: 'msg_u', parent_id: null, role: 'user', status: 'done', body: 'Fixture question', hidden: false, error: null, turn: null, tool_calls: [], created_at: 1 },
  { id: 'msg_a', parent_id: 'msg_u', role: 'assistant', status: 'done', body: 'Fixture answer', hidden: false, error: null,
    turn: { id: 'turn_a', status: 'succeeded', model: 'fixture-model', credential: 'platform' }, tool_calls: [], created_at: 2 }] });

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
  const r = await chatFixture(async () => true, { conversation: answered(),
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

test('W3: without a displayed price a paid send does not go out: the draft stays, the price is read again, and the next send carries it', async () => {
  const refusals = await server.ssrLoadModule('/src/apps/agent/refusals.ts');
  // FIXTURE: the run bound could not be read (null), the route has no run (run: null), or a server before AD-1 (no price fields).
  for (const unshown of [null, fixtureStatus({ run: null }), fixtureStatus({ run: { ...fixtureStatus().run, price_id: undefined, route_version: undefined } })]) {
    const h = await appFixture({ status: unshown, route: null });
    const statusReads = () => h.reads.filter((r) => r === 'agent.billing.status').length;
    const before = statusReads();
    // The composer says honestly that it cannot show the maximum, rather than nothing.
    assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximumUnavailable' });
    let refused: any;
    await assert.rejects(h.chat().send('Fixture question'), (e: any) => { refused = e; return true; });
    assert.deepEqual(h.writes, [], 'nothing is sent: not the turn, and no conversation for it either');
    const refusal = refusals.refusalOf(refused);
    assert.deepEqual([refusal.kind, refusal.next, refusal.text], ['price_unshown', 'retry', 'refusedPriceUnshown']);
    await assert.rejects(h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_a' }));
    assert.deepEqual(h.writes, [], 'a regenerate is held the same way');
    await settle(); h.render(); await settle(); h.render();
    assert.ok(statusReads() > before, 'the status is read again');
    assert.equal(h.chat().sentDraft, 0, 'the message stays in the composer');
    // The price arrives; the composer shows the maximum and the next send carries it.
    h.serve({ status: fixtureStatus() });
    await assert.rejects(h.chat().send('Fixture question'));
    await settle(); h.render(); await settle(); h.render();
    assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximum', amount: '468', unit: UNIT });
    assert.equal(await h.chat().send('Fixture question'), true);
    assert.deepEqual(h.writes.map((w) => w.operation), ['agent.conversation.create', 'agent.turn.start']);
    assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_new_fixture', text: 'Fixture question', ...SEEN });
    h.unmount();
  }
  // While the status is still loading the composer says so, and a send is held the same way.
  const pending = await appFixture({ status: new Promise(() => {}) });
  assert.deepEqual(pending.chat().maximum.props.maximum, { key: 'turnMaximumLoading' });
  await assert.rejects(pending.chat().send('Fixture early'), (e: any) => refusals.refusalOf(e)?.kind === 'price_unshown');
  assert.deepEqual(pending.writes, []);
  pending.unmount();
  // Without billing nothing about prices is asked or sent.
  const free = await appFixture({ billing: false, status: null });
  await free.chat().send('Fixture free');
  assert.deepEqual(plain(free.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture free' });
  free.unmount();
});

test('W3: the held send is worded in three languages: nothing was sent or charged, the price is being read, send again', async () => {
  const copy = await server.ssrLoadModule('/src/apps/agent/chat.messages.ts');
  const [zh, en, hant] = copy.chatCopy.refusedPriceUnshown;
  assert.match(zh, /没有发送，也没有扣费/); assert.match(en, /not sent and nothing was charged/); assert.match(hant, /沒有傳送，也沒有扣費/);
  assert.equal(core.translate('en', zh), en); assert.equal(core.translate('zh-Hant', zh), hant);
  for (const key of ['turnMaximumUnavailable', 'turnMaximumLoading']) {
    const [source, english, traditional] = copy.chatCopy[key];
    assert.equal(core.translate('en', source), english, key); assert.equal(core.translate('zh-Hant', source), traditional, key);
  }
});

test('S2: the second seen field carries the server\'s price key when the run reports one, else its route version', async () => {
  const composer = await server.ssrLoadModule('/src/apps/agent/composer.ts');
  const run = fixtureStatus().run;
  assert.deepEqual(plain(composer.seenPrice({ run })), SEEN);
  assert.deepEqual(plain(composer.seenPrice({ run: { ...run, price_key: 'pk_fixture_2' } })), { seen_price_id: 'price_fixture_2', seen_route_version: 'pk_fixture_2' });
  // A malformed key is no price at all (the send is held), never the route version in its place.
  assert.deepEqual(plain(composer.seenPrice({ run: { ...run, price_key: 'bad key' } })), {});
  assert.deepEqual(plain(composer.seenPrice({ run: { ...run, price_key: null } })), SEEN);
  const h = await appFixture({ status: fixtureStatus({ run: { ...run, price_key: 'pk_fixture_2' } }) });
  await h.chat().send('Fixture keyed');
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture keyed', seen_price_id: 'price_fixture_2', seen_route_version: 'pk_fixture_2' });
  h.unmount();
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

test('"send again at the new price" goes through the page as it is now: the message as it now stands, the price the page now shows', async () => {
  // FIXTURE: the page's send before the status was read again (it would carry the old price) and after.
  const calls: string[] = [];
  const refused = new client.ApiError(409, 'billing_price_changed', 'fixture', { current: { price_id: 'price_fixture_3' } });
  const before = async (text: string) => { calls.push(`before:${text}`); throw refused; };
  const after = async (text: string) => { calls.push(`after:${text}`); return true; };
  const h = await chatFixture(before);
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture draft' } }); h.render();
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); h.render();
  assert.deepEqual(calls, ['before:Fixture draft']);
  // The page read the new price (a new send function), and the person touched up the kept message.
  h.render({ send: after });
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture draft, edited' } }); h.render();
  const notice = h.find((el) => el.type === parts.RefusalNotice).props;
  assert.equal(notice.refusal.next, 'retry');
  notice.onRetry(); await settle(); h.render();
  assert.deepEqual(calls, ['before:Fixture draft', 'after:Fixture draft, edited']);
  assert.equal(h.find((el) => el.type === 'textarea').props.value, '');
  h.unmount();
  // The same for a refused regenerate: the same regenerate, through the page's current act.
  const acts: string[] = [];
  const r = await chatFixture(async () => true, { conversation: answered(),
    act: async (operation: string, input: any) => { acts.push(`before:${operation}:${input.regenerate_of}`); throw refused; } });
  r.elements().find((el) => el.type === 'button' && strings(el.props.children) === core.translate('en', '重新生成回答')).props.onClick();
  await settle(); r.render();
  r.render({ act: async (operation: string, input: any) => { acts.push(`after:${operation}:${input.regenerate_of}`); } });
  r.find((el) => el.type === parts.RefusalNotice).props.onRetry(); await settle(); r.render();
  assert.deepEqual(acts, ['before:agent.turn.start:msg_a', 'after:agent.turn.start:msg_a']);
  r.unmount();
});

test('a send started from a function the page handed out earlier still carries the price and payer the composer shows now', async () => {
  const h = await appFixture();
  // Held from before (as a click handler or the setup step holds it).
  const earlierSend = h.chat().send, earlierAct = h.chat().act;
  h.fail(new client.ApiError(409, 'billing_price_changed', 'fixture', { current: { price_id: 'price_fixture_3' } }));
  h.serve({ status: fixtureStatus({ run: { ...fixtureStatus().run, maximum: '600', price_id: 'price_fixture_3', route_version: 'fixture-v3' } }),
    offer: { ...fixtureOffer(), route_version: 'fixture-v3', price_id: 'price_fixture_3' } });
  await assert.rejects(h.chat().send('Fixture'), (e: any) => e.code === 'billing_price_changed');
  await settle(); h.render(); await settle(); h.render();
  assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximum', amount: '600', unit: UNIT });
  const money = h.money(); money.setPayer(money.payers[1]); h.render();
  h.fail(undefined);
  const NOW = { payer_kind: 'organization', payer_id: 'org_fixture', seen_price_id: 'price_fixture_3', seen_route_version: 'fixture-v3' };
  assert.equal(await earlierSend('Fixture again'), true);
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture again', ...NOW });
  await earlierAct('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' });
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture', ...NOW });
  h.unmount();
});

test('when the setup step\'s send is refused, "send again" sends the message as the composer now shows it', async () => {
  const h = await appFixture({ setup: fixtureSetup({ complete: false, terms: { complete: false, documents: fixtureSetup().terms.documents.map((d: any) => ({ ...d, accepted: false })) } }) });
  assert.equal(await h.chat().send('Fixture draft'), false); h.render();
  h.fail(new client.ApiError(409, 'billing_price_changed', 'fixture', { current: { price_id: 'price_fixture_3' } }));
  await h.card()[0].props.onComplete(); await settle(); h.render();
  assert.equal(h.card().length, 0);
  const refused = () => h.elements().filter((el) => el.type === parts.RefusalNotice);
  assert.equal(refused().length, 1);
  // The person touches up the kept message in the composer (the chat reports it to the page).
  assert.equal(typeof h.chat().onDraft, 'function', 'the page follows the composer while the refused message waits');
  h.chat().onDraft({ text: 'Fixture draft, edited' }); h.render();
  h.fail(undefined);
  refused()[0].props.onRetry(); await settle(); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture draft, edited', ...SEEN });
  assert.equal(h.chat().sentDraft, 1);
  assert.equal(h.chat().onDraft, undefined, 'sent: the page stops following the composer');
  h.unmount();
});

// ---------------------------------------------------------------- addendum-2 (integrated review of f834ae50)

test('W1: every explicit send is a new attempt with a new key, so a cause fixed in between is never answered with the replayed refusal', async () => {
  const h = await appFixture();
  const starts = () => h.writes.filter((w) => w.operation === 'agent.turn.start');
  // FIXTURE: the terms are not accepted yet; the server refuses the send (and records the refusal under its key).
  h.fail(new client.ApiError(409, 'terms_acceptance_required', 'fixture'));
  await assert.rejects(h.chat().send('Fixture question'), (e: any) => e.code === 'terms_acceptance_required');
  await settle(); h.render();
  // The person finishes the setup step (the terms are now accepted) and its button sends the kept message.
  h.fail(undefined);
  h.chat().onSetup({ text: 'Fixture question' }); h.render(); await settle(); h.render(); await settle(); h.render();
  await h.card()[0].props.onComplete(); await settle(); h.render();
  assert.equal(starts().length, 2);
  assert.deepEqual(plain(starts()[1].input), plain(starts()[0].input), 'the same message, unchanged');
  assert.notEqual(starts()[1].options.key, starts()[0].options.key, 'a new attempt, not the refused one');
  assert.equal(h.chat().sentDraft, 1, 'it starts');
  // A refused regenerate, repeated after the cause is fixed (the maximum raised in Agent settings).
  const regenerate = { conversation_id: 'conv_fixture', regenerate_of: 'msg_a' };
  h.fail(new client.ApiError(409, 'billing_run_cap_below_call_hold', 'fixture', { per_call_hold: '39', cap: '30', unit: UNIT }));
  await assert.rejects(h.chat().act('agent.turn.start', regenerate), (e: any) => e.code === 'billing_run_cap_below_call_hold');
  h.fail(undefined);
  await h.chat().act('agent.turn.start', regenerate);
  assert.equal(starts().length, 4);
  assert.notEqual(starts()[3].options.key, starts()[2].options.key);
  // And the plain composer: the same text sent again after a refusal is a new attempt too.
  h.fail(new client.ApiError(429, 'agent_busy', 'fixture'));
  await assert.rejects(h.chat().send('Fixture again'));
  h.fail(undefined);
  assert.equal(await h.chat().send('Fixture again'), true);
  assert.equal(new Set(starts().map((w) => w.options.key)).size, starts().length, 'no key is ever sent twice by a click');
  h.unmount();
});

test('W1: an attempt that got no answer is retried automatically with its own key; any answer from the server ends it', async () => {
  const waits: number[] = [];
  const instant = (fn: () => void, ms: number) => { waits.push(ms); return setTimeout(fn, 0); };
  const h = await appFixture({ globals: { setTimeout: instant } });
  const starts = () => h.writes.filter((w) => w.operation === 'agent.turn.start');
  // FIXTURE: the connection drops, then a gateway in front of Synwevo cannot reach it; the third try is answered.
  h.failNext(new client.ApiError(0, 'network', 'fixture'), new client.ApiError(504, 'upstream_unavailable', 'fixture', { status: 504 }, true));
  assert.equal(await h.chat().send('Fixture over a bad connection'), true);
  assert.equal(starts().length, 3);
  assert.equal(new Set(starts().map((w) => w.options.key)).size, 1, 'the same attempt, so a lost response is never run twice');
  assert.equal(waits.length, 2);
  // Retries are bounded: a connection that stays down ends the attempt with the network error.
  const down = new client.ApiError(0, 'network', 'fixture');
  h.failNext(down, down, down);
  await assert.rejects(h.chat().send('Fixture offline'), (e: any) => e.code === 'network');
  assert.equal(starts().length, 6);
  assert.equal(new Set(starts().slice(3).map((w) => w.options.key)).size, 1);
  // An answer from Synwevo (a refusal, or its own server error) is final: never retried automatically.
  h.failNext(new client.ApiError(409, 'billing_price_changed', 'fixture', { current: {} }));
  await assert.rejects(h.chat().send('Fixture refused'));
  h.failNext(new client.ApiError(500, 'internal_error', 'fixture'));
  await assert.rejects(h.chat().send('Fixture failed'));
  assert.equal(starts().length, 8);
  h.unmount();
});

test('fv-agent-1: an attempt that never got an answer keeps its key for the same request, so Send again replays a lost admission; any server answer drops it', async () => {
  const instant = (fn: () => void) => setTimeout(fn, 0);
  const h = await appFixture({ globals: { setTimeout: instant } });
  const starts = () => h.writes.filter((w) => w.operation === 'agent.turn.start');
  const keys = () => starts().map((w) => w.options.key);
  const down = new client.ApiError(0, 'network', 'fixture');
  const gateway = (status: number) => new client.ApiError(status, 'upstream_unavailable', 'fixture', { status }, true);
  // FIXTURE: the request may have been admitted (in reality the turn and its quote may be stored), but no
  // answer came back, and the connection stays down through both automatic retries.
  h.failNext(down, down, down);
  await assert.rejects(h.chat().send('Fixture over a lost connection'), (e: any) => e.code === 'network');
  assert.equal(starts().length, 3);
  assert.equal(new Set(keys()).size, 1);
  // The person presses Send again with the very same message: the same request, so the same key, and the
  // server answers from its record of the first attempt instead of starting a second paid answer.
  assert.equal(await h.chat().send('Fixture over a lost connection'), true);
  assert.equal(starts().length, 4);
  assert.deepEqual(plain(starts()[3].input), plain(starts()[0].input), 'the same body');
  assert.equal(keys()[3], keys()[0], 'the first attempt\'s key, so a lost admission is replayed, not run twice');
  // That answer ended the attempt: the next send of the same text is a new one.
  assert.equal(await h.chat().send('Fixture over a lost connection'), true);
  assert.notEqual(keys()[4], keys()[0]);
  // A server refusal is an answer: Send again carries a new key (a fixed cause is never met with the replayed refusal).
  h.fail(new client.ApiError(409, 'terms_acceptance_required', 'fixture'));
  await assert.rejects(h.chat().send('Fixture refused'), (e: any) => e.code === 'terms_acceptance_required');
  h.fail(undefined);
  assert.equal(await h.chat().send('Fixture refused'), true, 'it starts, not the replayed refusal');
  assert.notEqual(keys()[6], keys()[5]);
  // No answer, then the replay of that key is answered with a refusal: the kept key goes with that answer.
  const at = starts().length;
  h.failNext(gateway(502), gateway(503), gateway(504), new client.ApiError(409, 'agent_busy', 'fixture'));
  await assert.rejects(h.chat().send('Fixture behind a gateway'), (e: any) => e.status === 504);
  await assert.rejects(h.chat().send('Fixture behind a gateway'), (e: any) => e.code === 'agent_busy');
  assert.equal(keys()[at + 3], keys()[at], 'replayed with the kept key');
  assert.equal(await h.chat().send('Fixture behind a gateway'), true);
  assert.notEqual(keys()[at + 4], keys()[at], 'dropped on the server\'s answer');
  // The key is kept for that request only: a changed message (or price, or payer) is another request.
  const other = starts().length;
  h.failNext(down, down, down);
  await assert.rejects(h.chat().send('Fixture before an edit'));
  assert.equal(await h.chat().send('Fixture after an edit'), true);
  assert.notEqual(keys()[other + 3], keys()[other]);
  // A regenerate the same way.
  const regenerate = { conversation_id: 'conv_fixture', regenerate_of: 'msg_a' };
  const again = starts().length;
  h.failNext(down, down, down);
  await assert.rejects(h.chat().act('agent.turn.start', regenerate));
  await h.chat().act('agent.turn.start', regenerate);
  assert.equal(keys()[again + 3], keys()[again]);
  h.unmount();
  // A new conversation: the resend goes into the conversation the first attempt was made for, with its key.
  const fresh = await appFixture({ route: null, globals: { setTimeout: instant } });
  fresh.failNext(undefined, down, down, down);
  await assert.rejects(fresh.chat().send('Fixture first message'));
  assert.equal(await fresh.chat().send('Fixture first message'), true);
  assert.deepEqual(fresh.writes.map((w) => w.operation), ['agent.conversation.create', 'agent.turn.start', 'agent.turn.start', 'agent.turn.start', 'agent.turn.start']);
  assert.equal(fresh.writes[4].options.key, fresh.writes[1].options.key);
  assert.deepEqual(plain(fresh.writes[4].input), plain(fresh.writes[1].input));
  assert.deepEqual(fresh.navigations, ['/agent/c/conv_new_fixture']);
  fresh.unmount();
});

test('W8: the composer honours run.usable: an unusable cap says so with the way to Agent settings, never "at most <default>"', async () => {
  const { renderToStaticMarkup } = await import('react-dom/server');
  const run = fixtureStatus().run;
  const unusable = (over: Record<string, unknown>) => fixtureStatus({ run: { ...run, usable: false, ...over } });
  const pending = fixtureSetup({ complete: false, terms: { complete: false, documents: fixtureSetup().terms.documents.map((d: any) => ({ ...d, accepted: false })) } });
  // FIXTURE (quotes.run_bound): a cap below one call's hold reports the default as maximum with usable=false.
  const low = await appFixture({ status: unusable({ cap: '30', source: 'personal_cap' }), setup: pending });
  assert.deepEqual(low.chat().maximum.props.maximum, { key: 'turnMaximumCapLow', amount: '39', unit: UNIT });
  // The setup step's terms sentence then names no amount it could not honour.
  assert.equal(await low.chat().send('Fixture'), false); low.render(); await settle(); low.render(); await settle(); low.render();
  assert.equal(low.card()[0].props.maximum, null);
  low.unmount();
  // A cap that cannot be read (cap null) or is in another unit: unusable, said without a figure.
  for (const status of [unusable({ cap: null, source: 'personal_cap' }), unusable({ cap: '100', source: 'personal_cap' })]) {
    const h = await appFixture({ status });
    assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximumCapUnusable' }, JSON.stringify(status.run));
    h.unmount();
  }
  // Not the person's cap (the route's validity is nearly over): the service, not a settings matter.
  for (const status of [unusable({}), unusable({ maximum: '300', cap: '300', source: 'personal_cap' })]) {
    const h = await appFixture({ status });
    assert.deepEqual(h.chat().maximum.props.maximum, { key: 'turnMaximumPaused' }, JSON.stringify(status.run));
    h.unmount();
  }
  // Rendered: the cap problems link to Agent settings; nothing says "at most".
  const html = (maximum: unknown) => renderToStaticMarkup(React.createElement(router.MemoryRouter, null, React.createElement(parts.TurnMaximum, { maximum })));
  const capLow = html({ key: 'turnMaximumCapLow', amount: '39', unit: UNIT });
  assert.match(capLow, /低于一次调用的预留金额 ¥0\.39/); assert.match(capLow, /href="\/agent\/settings"/);
  assert.doesNotMatch(capLow, /最多/);
  assert.match(html({ key: 'turnMaximumCapUnusable' }), /href="\/agent\/settings"/);
  assert.doesNotMatch(html({ key: 'turnMaximumPaused' }), /href=/);
  const copy = await server.ssrLoadModule('/src/apps/agent/chat.messages.ts');
  for (const key of ['turnMaximumCapLow', 'turnMaximumCapUnusable', 'turnMaximumPaused']) {
    const [source, en, hant] = copy.chatCopy[key];
    assert.equal(core.translate('en', source), en, key); assert.equal(core.translate('zh-Hant', source), hant, key);
  }
});

test('W2: leaving Agent settings, and saving the maximum there, reads agent.billing.status again, so the composer never shows a stale maximum', async () => {
  const capped = (amount: string) => fixtureStatus({ run: { ...fixtureStatus().run, maximum: amount, cap: amount, source: 'personal_cap' } });
  const h = await appFixture({ status: capped('100') });
  const statusReads = () => h.reads.filter((r) => r === 'agent.billing.status').length;
  assert.equal(h.chat().maximum.props.maximum.amount, '100');
  await h.settings(true);
  // The settings page hands every saved or cleared maximum back to the page.
  const page = h.find((el) => typeof el.props.onBillingChanged === 'function');
  const before = statusReads();
  h.serve({ status: capped('300') });   // FIXTURE: the person raised their maximum to 3.00
  page.props.onBillingChanged(); await settle(); h.render(); await settle(); h.render();
  assert.equal(statusReads(), before + 1, 'a save reads the status again');
  h.serve({ status: capped('250') });   // FIXTURE: and lowered it again before going back
  await h.settings(false);
  assert.equal(statusReads(), before + 2, 'leaving the settings reads it again');
  assert.equal(h.chat().maximum.props.maximum.amount, '250');
  await h.chat().send('Fixture question');
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture question', ...SEEN });
  h.unmount();
});

test('W4: opening the setup step reads agent.setup.get again and shows it loading until the fresh state arrives; leaving Agent settings reads it too', async () => {
  const h = await appFixture();                 // setup complete when the page loaded
  const setupReads = () => h.reads.filter((r) => r === 'agent.setup.get').length;
  const withdrawn = fixtureSetup({ complete: false, provider_consent: { ...fixtureSetup().provider_consent, accepted: false, accepted_version: null } });
  // FIXTURE: the consent was withdrawn in Agent settings meanwhile; the server refuses the send.
  h.fail(new client.ApiError(409, 'provider_consent_required', 'fixture', { provider: 'deepseek', version: '2026-10-05' }));
  await assert.rejects(h.chat().send('Fixture question'));
  h.fail(undefined); h.serve({ setup: withdrawn });
  const before = setupReads();
  h.chat().onSetup({ text: 'Fixture question' }); h.render();
  assert.equal(h.card().length, 1);
  assert.equal(h.card()[0].props.setup, undefined, 'loading, not the state read at page load');
  await settle(); h.render(); await settle(); h.render();
  assert.equal(setupReads(), before + 1, 'read again when the step opens');
  assert.equal(h.card()[0].props.setup.provider_consent.accepted, false, 'the step asks for what is missing now');
  h.card()[0].props.onDismiss(); h.render();
  // Opened by Send (the page knows something is missing): read again as well.
  const bySend = setupReads();
  assert.equal(await h.chat().send('Fixture question'), false); h.render();
  assert.equal(h.card()[0].props.setup, undefined);
  await settle(); h.render(); await settle(); h.render();
  assert.equal(setupReads(), bySend + 1);
  h.card()[0].props.onDismiss(); h.render();
  // The facts may change in Agent settings: leaving it reads the state again.
  await h.settings(true);
  const away = setupReads();
  h.serve({ setup: fixtureSetup() });
  await h.settings(false);
  assert.equal(setupReads(), away + 1, 'read again on leaving Agent settings');
  assert.equal(await h.chat().send('Fixture question'), true, 'consent given there: the send goes straight out');
  h.unmount();
});

test('W9: the page keys the price notice on the price key of the run it shows', async () => {
  const prices = await server.ssrLoadModule('/src/apps/agent/PriceNotice.tsx');
  const previous = { ...fixtureOffer(), route_version: 'fixture-v1', price_id: 'price_fixture_1', per_call_hold: '30', price_key: 'pk1:previous' };
  const h = await appFixture({ status: fixtureStatus({ run: { ...fixtureStatus().run, price_key: 'pk1:current' },
    price_notice: { previous, previous_run_at: 1_700_000_000 } }) });
  const notices = all(h.chat().aside).filter((el) => el.type === prices.default);
  assert.equal(notices.length, 1);
  assert.equal(notices[0].props.currentKey, 'pk1:current');
  h.unmount();
});

test('W10: a refused message is kept when the person goes to Agent settings and is back in the composer when they return', async () => {
  const h = await appFixture();
  // FIXTURE: the send is refused for the per-answer maximum; its next step is Agent settings.
  h.fail(new client.ApiError(409, 'billing_run_cap_below_call_hold', 'fixture', { per_call_hold: '39', cap: '30', unit: UNIT }));
  await assert.rejects(h.chat().send('Fixture question'));
  h.fail(undefined);
  const leave = h.chat().onLeave;
  assert.equal(typeof leave, 'function', 'the composer hands its message to the page when it goes away');
  // The composer's state as Chat holds it (text, source chips, file chips), handed over as it unmounts.
  const context = { reference: { workspace_id: 'ws_a', kind: 'form', id: 'frm_fixture' }, title: 'Fixture form', workspace: 'Fixture a', kind: 'form' };
  const kept = { text: 'Fixture question', contexts: [context], files: [] };
  await h.settings(true);
  leave(kept);
  await h.settings(false);
  assert.deepEqual(plain(h.chat().restore), plain(kept), 'the message is back in the composer');
  // Once the composer took it, it is not handed out again.
  h.chat().onRestored(); h.render();
  assert.equal(h.chat().restore, undefined);
  // Leaving for anything but Agent settings (another conversation) keeps nothing; an empty composer neither.
  h.chat().onLeave({ text: 'Fixture elsewhere', contexts: [], files: [] }); h.render();
  assert.equal(h.chat().restore, undefined);
  const again = h.chat().onLeave;
  await h.settings(true); again({ text: '  ', contexts: [], files: [] }); await h.settings(false);
  assert.equal(h.chat().restore, undefined);
  // Kept for the conversation it was written in: another one does not receive it.
  const third = h.chat().onLeave;
  await h.settings(true); third(kept); h.route('conv_other'); await h.settings(false);
  assert.equal(h.chat().restore, undefined);
  h.route('conv_fixture');
  assert.deepEqual(plain(h.chat().restore), plain(kept));
  h.unmount();
});

test('W10: the composer starts from a kept message and hands its message over when it goes away', async () => {
  const context = { reference: { workspace_id: 'ws_a', kind: 'form', id: 'frm_fixture' }, title: 'Fixture form', workspace: 'Fixture a', kind: 'form' };
  const left: any[] = []; let restored = 0;
  const h = await chatFixture(async () => true, { restore: { text: 'Fixture kept', contexts: [context], files: [] },
    onRestored: () => { restored += 1; }, onLeave: (draft: unknown) => left.push(draft) });
  assert.equal(h.find((el) => el.type === 'textarea').props.value, 'Fixture kept');
  assert.equal(restored, 1, 'the page is told the kept message was taken');
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture kept, edited' } }); h.render();
  h.unmount();
  assert.deepEqual(plain(left), [{ text: 'Fixture kept, edited', contexts: [context], files: [] }]);
});

test('W10: when the setup step\'s send is refused, focus moves to the refusal notice', async () => {
  const h = await appFixture({ setup: fixtureSetup({ complete: false, terms: { complete: false, documents: fixtureSetup().terms.documents.map((d: any) => ({ ...d, accepted: false })) } }) });
  assert.equal(await h.chat().send('Fixture draft'), false); h.render(); await settle(); h.render(); await settle(); h.render();
  h.fail(new client.ApiError(409, 'billing_run_cap_below_call_hold', 'fixture', { per_call_hold: '39', cap: '30', unit: UNIT }));
  await h.card()[0].props.onComplete(); await settle(); h.render();
  const notice = h.find((el) => el.type === parts.RefusalNotice);
  assert.ok(notice.props.focusTick > 0, 'the notice is asked to take focus');
  // The notice itself (ChatParts, compiled): focus goes to its next step, or to the notice when it has none.
  const focused: string[] = [];
  const r = await compiled('ChatParts.tsx', { refusal: { kind: 'run_cap', next: 'settings', text: 'refusedRunCap' }, live: true }, {}, 'RefusalNotice');
  const box = r.tree();
  assert.equal(box.props.tabIndex, undefined, 'not focusable while nothing asks for it');
  box.props.ref.current = { querySelector: () => ({ focus: () => focused.push('next step') }), focus: () => focused.push('notice') };
  r.render({ focusTick: 1 });
  assert.deepEqual(focused, ['next step']);
  assert.equal(r.tree().props.tabIndex, -1);
  r.tree().props.ref.current = { querySelector: () => null, focus: () => focused.push('notice') };
  r.render({ focusTick: 2 });
  assert.deepEqual(focused, ['next step', 'notice']);
  r.render({ focusTick: 2 });
  assert.equal(focused.length, 2, 'only when asked again');
  h.unmount();
});

// ---------------------------------------------------------------- the composer across a trip to Agent settings (browser proof, defect 1)

const pendingSetup = () => fixtureSetup({ complete: false, terms: { complete: false, documents: fixtureSetup().terms.documents.map((d: any) => ({ ...d, accepted: false })) } });
const CONTEXT = { reference: { kind: 'form', workspace_id: 'ws_a', resource_id: 'frm_fixture', snapshot: 'a'.repeat(64) },
  title: 'Fixture form', workspace_name: 'Fixture a', open_path: null };
const FILE = { title: 'fixture.txt', reference: { file_id: 'file_fixture', snapshot: 'b'.repeat(64) } };

/** The page's Chat as React holds it: the compiled Chat.tsx mounted with the props the page renders
 *  now, kept while the page renders the same one (same key), mounted afresh when it renders a new one
 *  (back from Agent settings, another conversation), and unmounted (running its cleanups: it hands its
 *  message to the page) when the page renders none. */
async function liveChat(h: any) {
  const reports = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  const picker = await server.ssrLoadModule('/src/apps/agent/ContextPicker.tsx');
  const overrides = { './ContextPicker': { ...picker, useContextText: () => (key: string) => key },
    './ChatParts': { ...parts, useChatText: () => ({ c: (key: string) => key }) },
    './ReportDialog': { ...reports, useReportText: () => (key: string) => key } };
  let mounted: any = null, key: unknown, mounts = 0;
  const live = {
    mounts: () => mounts,
    chat: () => mounted,
    async sync() {
      for (let pass = 0; pass < 4; pass++) {
        await settle(); h.render();
        const element = h.elements().find((el: any) => el.type === chat.default);
        if (!element) { mounted?.unmount(); mounted = null; continue; }
        if (mounted && element.key === key) { mounted.render(element.props); continue; }
        mounted?.unmount();
        key = element.key; mounts += 1;
        mounted = await compiled('Chat.tsx', element.props, overrides);
      }
      h.render();
    },
    async type(text: string) { mounted.find((el: any) => el.type === 'textarea').props.onChange({ target: { value: text } }); mounted.render(); await live.sync(); },
    async attach() {
      mounted.find((el: any) => el.props?.spaces && el.props.onChoose).props.onChoose(CONTEXT); mounted.render();
      mounted.find((el: any) => el.props?.client && el.props.onChoose).props.onChoose(FILE); mounted.render();
      await live.sync();
    },
    async submit() { mounted.find((el: any) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await live.sync(); await live.sync(); },
    /** What the composer holds: the text, the source chips and the file chips. */
    composer() {
      const chips = (name: string) => mounted.elements().find((el: any) => el.type?.name === name)?.props.selected ?? [];
      return plain({ text: mounted.find((el: any) => el.type === 'textarea').props.value, contexts: chips('ContextChips'), files: chips('FileChips') });
    },
    unmount() { mounted?.unmount(); mounted = null; },
  };
  await live.sync();
  return live;
}
/** A new person: the setup step opens on Send and its button sends the message (the page's sentDraft). */
async function afterSetupSend() {
  const h = await appFixture({ setup: pendingSetup() });
  const c = await liveChat(h);
  await c.type('Fixture first question'); await c.submit();
  assert.equal(h.card().length, 1, 'the setup step is open with the message kept');
  h.serve({ setup: fixtureSetup() });   // FIXTURE: what the step records (its own writes are tested in agent-setup)
  await h.card()[0].props.onComplete(); await c.sync();
  assert.deepEqual(h.writes.map((w) => w.operation), ['agent.turn.start']);
  assert.equal(h.chat().sentDraft, 1);
  assert.deepEqual(c.composer(), { text: '', contexts: [], files: [] }, 'the sent message left the composer');
  return { h, c };
}
const TYPED = { text: 'Fixture typed after the answer', contexts: plain([CONTEXT]), files: plain([FILE]) };

test('proof defect 1: after the setup step\'s send, a message typed next survives trips to Agent settings with its sources and files', async () => {
  const { h, c } = await afterSetupSend();
  await c.type(TYPED.text); await c.attach();
  assert.deepEqual(c.composer(), TYPED);
  for (const trip of [1, 2]) {
    await h.settings(true); await c.sync();
    assert.equal(c.chat(), null, 'Agent settings replaces the conversation');
    await h.settings(false); await c.sync();
    assert.deepEqual(c.composer(), TYPED, `back from Agent settings (trip ${trip})`);
  }
  assert.equal(c.mounts(), 3, 'a new composer each time back (the case the old clearing on mount broke)');
  // A send that completes still clears the composer.
  await c.submit();
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', text: TYPED.text, context_refs: [CONTEXT.reference], file_refs: [FILE.reference], ...SEEN });
  assert.deepEqual(c.composer(), { text: '', contexts: [], files: [] });
  c.unmount(); h.unmount();
});

test('proof defect 1: after "send again", a refused message taken to Agent settings by its own link is back on return', async () => {
  const { renderToStaticMarkup } = await import('react-dom/server');
  const h = await appFixture({ setup: pendingSetup() });
  const c = await liveChat(h);
  await c.type('Fixture first question'); await c.submit();
  h.serve({ setup: fixtureSetup() });
  // The setup step's send is refused; "send again" (the page's notice) sends it.
  h.fail(new client.ApiError(409, 'billing_price_changed', 'fixture', { current: { price_id: 'price_fixture_2' } }));
  await h.card()[0].props.onComplete(); await c.sync();
  h.fail(undefined);
  h.find((el) => el.type === parts.RefusalNotice).props.onRetry(); await c.sync();
  assert.equal(h.chat().sentDraft, 1);
  assert.deepEqual(c.composer(), { text: '', contexts: [], files: [] });
  // The next message is refused for the per-answer maximum: its next step is a link to Agent settings.
  h.fail(new client.ApiError(409, 'billing_run_cap_below_call_hold', 'fixture', { per_call_hold: '39', cap: '30', unit: UNIT }));
  await c.type(TYPED.text); await c.attach(); await c.submit();
  h.fail(undefined);
  const notice = c.chat().find((el: any) => el.type === parts.RefusalNotice);
  assert.equal(notice.props.refusal.next, 'settings');
  assert.match(renderToStaticMarkup(React.createElement(router.MemoryRouter, null, notice)), /href="\/agent\/settings"/);
  assert.deepEqual(c.composer(), TYPED, 'refused: the message stays');
  await h.settings(true); await c.sync(); await h.settings(false); await c.sync();
  assert.deepEqual(c.composer(), TYPED, 'and comes back from Agent settings');
  c.unmount(); h.unmount();
});

test('proof defect 1: a message kept in Agent settings comes back to its own conversation after a switch, never to another', async () => {
  const { h, c } = await afterSetupSend();
  await c.type(TYPED.text); await c.attach();
  await h.settings(true); await c.sync();
  // From Agent settings the person opens another conversation, then the one they wrote in.
  h.route('conv_other'); await h.settings(false); await c.sync();
  assert.deepEqual(c.composer(), { text: '', contexts: [], files: [] }, 'another conversation does not receive it');
  h.route('conv_fixture'); await c.sync();
  assert.deepEqual(c.composer(), TYPED, 'its own conversation does');
  assert.equal(c.mounts(), 3);
  c.unmount(); h.unmount();
});

test('proof defect 1: a message whose send completes while the person is in Agent settings does not come back', async () => {
  const h = await appFixture({ setup: pendingSetup() });
  const c = await liveChat(h);
  await c.type('Fixture in flight'); await c.submit();
  h.serve({ setup: fixtureSetup() });
  let answer!: () => void;
  h.failNext(new Promise<void>((resolve) => { answer = resolve; }));   // FIXTURE: the server is still answering
  const sending = h.card()[0].props.onComplete(); await c.sync();
  await h.settings(true); await c.sync();
  answer(); await sending; await c.sync();
  await h.settings(false); await c.sync();
  assert.equal(h.chat().sentDraft, 1);
  assert.deepEqual(c.composer(), { text: '', contexts: [], files: [] }, 'it was sent: it is not put back');
  c.unmount(); h.unmount();
});

test('C1: the model panel beside the composer shows the unit prices, the price version and the per-answer maximum', async () => {
  const { renderToStaticMarkup } = await import('react-dom/server');
  const copy = await server.ssrLoadModule('/src/apps/agent/chat.messages.ts');
  const payerModule = await server.ssrLoadModule('/src/apps/agent/payers.ts');
  // The chat's words in English (fixture translator), so the rendered panel can be read.
  const text = { './ChatParts': { ...parts, useChatText: () => ({ c: (key: string, params?: any) => core.translate('en', copy.chatCopy[key][0], params), locale: 'en' }) } };
  const me = payerModule.personal('usr_fixture', 'Fixture user');
  const money = { status: fixtureStatus(), offer: fixtureOffer(), payers: [me], payersError: false, payer: me, setPayer() {}, walletUrl: '/account/wallet' };
  const bar = await compiled('ComposerStatus.tsx', { money }, text, 'ComposerStatus');
  const picker = bar.find((el) => Array.isArray(el.props.offers));
  assert.deepEqual(plain(picker.props.maximum), { key: 'turnMaximum', amount: '468', unit: UNIT }, 'the maximum the composer shows');
  const panel = await compiled('ComposerStatus.tsx', picker.props, text, 'ModelPicker');
  panel.find((el) => el.type === 'button' && el.props['aria-haspopup'] === 'dialog').props.onClick(); panel.render();
  const modal = panel.find((el) => typeof el.props.onClose === 'function' && el.props.title !== undefined);
  const html = renderToStaticMarkup(React.createElement(router.MemoryRouter, null, modal));
  for (const words of ['Price per million tokens', 'Price version', 'price_fixture_2', 'Maximum per answer', 'CN¥4.68']) assert.ok(html.includes(words), `${words} in ${html}`);
  // While the run bound is read, the panel says so rather than show a figure.
  const loading = await compiled('ComposerStatus.tsx', { money: { ...money, status: undefined } }, text, 'ComposerStatus');
  assert.deepEqual(plain(loading.find((el) => Array.isArray(el.props.offers)).props.maximum), { key: 'turnMaximumLoading' });
});
