/** Real compiled components/helpers with explicit hook/network fixtures, following agent-chat.
 * No live service, model, billing or browser claim is made by these tests. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import * as router from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, ui: any, core: any, helpers: any, picker: any, copy: any, budget: any, errors: any, client: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'context-fixture-locale', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  for (const path of ['/src/shared/messages.ts', '/src/apps/agent/messages.ts']) await server.ssrLoadModule(path);
  [helpers, picker, copy, budget, errors, client] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/context.ts'), server.ssrLoadModule('/src/apps/agent/ContextPicker.tsx'),
    server.ssrLoadModule('/src/apps/agent/context.messages.ts'), server.ssrLoadModule('/src/apps/agent/budget.ts'),
    server.ssrLoadModule('/src/apps/agent/errors.tsx'), server.ssrLoadModule('@synwevo/auth-client'),
  ]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));
const plain = (value: any) => JSON.parse(JSON.stringify(value));
const spaces = ['a', 'b'].map((id) => ({ workspace_id: `ws_${id}`, name: `Fixture ${id}`, role: 'member', processing: 'all' }));
const item = (id = 'form_fixture', workspace_id = 'ws_a', kind = 'form') => ({ kind, workspace_id, resource_id: id, title: `Fixture ${id}` });
const resolved = (source = item(), snapshot = 'a'.repeat(64)) => ({ reference: { kind: source.kind, workspace_id: source.workspace_id,
  resource_id: source.resource_id, snapshot }, title: source.title, workspace_name: 'Fixture A', open_path: '/forms/w/ws_a/forms/form_fixture' });
const page = (items = [item()], next_offset: number | null = null, limited = false) => ({ items, next_offset, limited });
const conversation = (extra = {}) => ({ id: 'conv_fixture', title: 'Fixture conversation', status: 'active', head_id: null,
  scope: null, sealed: null, messages: [], event_seq: 1, updated_at: 1, ...extra });
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node)
  ? [node, ...all(node.props.children), ...all(node.props.footer), ...all(node.props.aside)] : [];
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';
function render(locale: string, element: React.ReactElement) {
  ui.setLocale(locale);
  return renderToStaticMarkup(React.createElement(router.MemoryRouter, null, element));
}

/** Hooks are fixtures; the component body and event handlers are compiled production code. */
async function compiled(file: string, initialProps: any, overrides: Record<string, any> = {}, entry = 'default') {
  let index = 0, props = initialProps, tree: any, dirty = false;
  const values: any[] = [], effects: any[] = [], pending: (() => void)[] = [];
  const hooks = { useId: () => 'context-fixture',
    useState: (initial: any) => { const i = index++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], (next: any) => { const value = typeof next === 'function' ? next(values[i]) : next;
        if (!Object.is(value, values[i])) { values[i] = value; dirty = true; } }]; },
    useRef: (initial: any) => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn: any, deps: any[]) => { const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn }; return values[i].fn; },
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
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout });
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
  return { render: rerender, find, tree: () => tree, elements: () => all(tree), text: () => strings(tree),
    unmount: () => effects.forEach((effect) => effect?.cleanup?.()),
    button: (label: string) => find((el) => el.type === 'button' && (el.props['aria-label'] === label || strings(el.props.children) === label)).props,
    control: (suffix: string) => find((el) => el.props.id === `context-fixture-${suffix}`).props };
}
async function dialogFixture() {
  const requests: any[] = [], chosen: any[] = [];
  let closed = false;
  const h = await compiled('ContextPicker.tsx', { spaces, selected: [], onChoose: (value: any) => chosen.push(value),
    onRemove() {}, onClose: () => { closed = true; } }, { '../../shared/ops': {
      read: (op: string, input: any, workspace: any, signal: AbortSignal) => new Promise((resolve, reject) => requests.push({ op, input, workspace, signal, resolve, reject })),
    } }, 'ContextDialog');
  return { ...h, requests, chosen, closed: () => closed,
    answer: async (n: number, value: any) => { requests[n].resolve(value); await settle(); h.render(); },
    fail: async (n: number, error: any) => { requests[n].reject(error); await settle(); h.render(); } };
}

test('list/search/pagination use personal reads, show limits, retry the failed page and resolve exact references', async () => {
  const h = await dialogFixture();
  assert.equal(h.requests[0].op, 'agent.context.list'); assert.equal(h.requests[0].workspace, null);
  assert.deepEqual(plain(h.requests[0].input), { workspace_id: 'ws_a', kind: 'form', offset: 0 });
  assert.match(h.text(), /Loading available context/);
  await h.answer(0, page([item()], 50, true));
  assert.match(h.text(), /first 500 forms/);
  h.button('Load more context').onClick(); h.render();
  assert.equal(h.button('Load more context').disabled, true);
  await h.fail(1, new Error('Fixture network failure'));
  const failure = h.find((el) => typeof el.props.retry === 'function'); failure.props.retry(); h.render();
  assert.equal(h.requests[2].input.offset, 50);
  await h.answer(2, page([item('form_second')]));
  assert.match(h.text(), /Fixture form_fixture/); assert.match(h.text(), /Fixture form_second/);
  h.button('Choose “Fixture form_second”').onClick(); h.render();
  assert.equal(h.requests[3].op, 'agent.context.resolve'); assert.equal(h.requests[3].workspace, null);
  assert.deepEqual(plain(h.requests[3].input), { workspace_id: 'ws_a', kind: 'form', resource_id: 'form_second' });
  await h.answer(3, resolved(item('form_second'), 'b'.repeat(64)));
  assert.deepEqual(plain(h.chosen), [resolved(item('form_second'), 'b'.repeat(64))]);
  h.control('query').onChange({ target: { value: '  invoice  ' } }); h.render();
  h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); h.render();
  assert.deepEqual(plain(h.requests[4].input), { workspace_id: 'ws_a', kind: 'form', q: 'invoice', offset: 0 });
  h.unmount();
});

for (const transition of ['workspace', 'kind', 'scope', 'close', 'route'] as const) test(`a stale resolve cannot choose anything after ${transition} changes`, async () => {
  const h = await dialogFixture(); await h.answer(0, page());
  h.button('Choose “Fixture form_fixture”').onClick(); h.render();
  const old = h.requests[1];
  if (transition === 'workspace') { h.control('workspace').onChange({ target: { value: 'ws_b' } }); h.render(); }
  if (transition === 'kind') { h.control('kind').onChange({ target: { value: 'table' } }); h.render(); }
  if (transition === 'scope') h.render({ spaces: [spaces[1]] });
  if (transition === 'close') h.tree().props.onClose();
  if (transition === 'route') h.unmount();
  assert.equal(old.signal.aborted, true);
  old.resolve(resolved()); await settle(); assert.equal(h.chosen.length, 0);
  if (transition === 'close') assert.equal(h.closed(), true);
  h.unmount();
});

test('late lists are ignored; resolution refusals can be retried and malformed references are never chosen', async () => {
  const h = await dialogFixture();
  h.control('workspace').onChange({ target: { value: 'ws_b' } }); h.render();
  assert.equal(h.requests[0].signal.aborted, true);
  await h.answer(1, page([item('current_fixture', 'ws_b')]));
  await h.answer(0, page([item('old_fixture')]));
  assert.ok(!h.text().includes('old_fixture'));
  h.button('Choose “Fixture current_fixture”').onClick(); h.render();
  await h.fail(2, { code: 'context_changed' });
  h.find((el) => typeof el.props.retry === 'function').props.retry(); h.render();
  await h.answer(3, resolved(item('current_fixture', 'ws_b'), 'invalid'));
  assert.equal(h.chosen.length, 0);
  assert.equal(h.find((el) => typeof el.props.retry === 'function').props.error.code, 'invalid_context_response');
  h.unmount();
});

test('five is a hard selection limit, duplicates stay unchanged, and only exact reference fields are sent', () => {
  let selected: any[] = [];
  for (let i = 0; i < 6; i++) selected = helpers.addContext(selected, resolved(item(`form_${i}`)));
  assert.equal(selected.length, 5);
  assert.equal(helpers.addContext(selected, resolved(item('other'))), selected);
  const one = [resolved()]; assert.equal(helpers.addContext(one, resolved()), one);
  assert.deepEqual(helpers.contextReferences(one), [resolved().reference]);
  assert.equal(helpers.contextReferences([]), undefined);
  assert.deepEqual(helpers.contextSpaces([...spaces, { ...spaces[0], workspace_id: 'ws_denied', processing: 'none' }], null), spaces);
  assert.deepEqual(helpers.contextSpaces(spaces, ['ws_b']), [spaces[1]]); assert.deepEqual(helpers.contextSpaces(spaces, []), []);
  const a = { conversation_id: 'conv_fixture', text: 'Fixture', context_refs: [resolved().reference] };
  assert.equal(budget.sameIntent(a, { ...a, context_refs: [{ ...a.context_refs[0] }] }), true);
  assert.equal(budget.sameIntent(a, { ...a, context_refs: [{ ...a.context_refs[0], snapshot: 'b'.repeat(64) }] }), false);
  assert.equal(budget.sameIntent(a, { ...a, context_refs: undefined }), false);
});

test('three-language dialog/chips/source cards disclose business boundaries and never render unsafe links or hidden sources', () => {
  assert.deepEqual(core.validateMessages(copy.contextMessages), []);
  const fixtures = [{ title: 'Fixture safe', workspace_name: 'Fixture A', open_path: '/forms/w/ws_a/forms/a?view=current#top' },
    ...['https://evil.test', '//evil.test', '/\\evil.test', '/%2f%2fevil.test', '/%5cevil.test', '/\nevil.test', 'javascript:alert(1)']
      .map((path) => ({ title: 'Fixture unsafe', workspace_name: 'Fixture B', open_path: path }))];
  for (const locale of ['zh-Hans', 'en', 'zh-Hant']) {
    for (const [source, en, hant] of Object.values(copy.contextCopy) as string[][]) {
      assert.equal(core.translate(locale, source, { count: 1, title: 'Fixture' }), (locale === 'en' ? en : locale === 'zh-Hant' ? hant : source)
        .replaceAll('{count}', '1').replaceAll('{title}', 'Fixture'));
    }
    const html = render(locale, React.createElement(picker.ContextDialog, { spaces, selected: [resolved()], onChoose() {}, onRemove() {}, onClose() {} }));
    for (const key of ['formHint', 'tableHint', 'intro']) assert.ok(html.includes(core.translate(locale, copy.contextCopy[key][0])), key);
    assert.match(html, /role="dialog"[^>]*aria-modal="true"/); assert.match(html, /<label for="/); assert.match(html, /type="search"/);
    const cards = render(locale, React.createElement(picker.ContextSources, { contexts: fixtures }));
    assert.equal((cards.match(/href=/g) ?? []).length, 1); assert.ok(cards.includes('href="/forms/w/ws_a/forms/a?view=current#top"'));
    assert.equal(render(locale, React.createElement(picker.ContextSources, { contexts: fixtures, hidden: true })), '');
    for (const code of ['context_changed', 'context_unavailable', 'context_too_large', 'invalid_context', 'outside_conversation_scope', 'source_policy_changed']) {
      const key = helpers.contextErrorKey({ code });
      assert.equal(errors.agentErrorText({ code }, (source: string) => core.translate(locale, source)), core.translate(locale, copy.contextCopy[key][0]));
      assert.equal(helpers.contextErrorKey({ code: 'idempotent_failure', detail: { code } }), key);
    }
  }
});

async function chatFixture(send: any) {
  const parts = await server.ssrLoadModule('/src/apps/agent/ChatParts.tsx');
  const reports = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  return compiled('Chat.tsx', { conversation: conversation(), loading: false, error: undefined, connection: 'connected', configured: true,
    spaces, send, act: async () => {}, refresh: async () => {} }, {
      './ContextPicker': { ...picker, useContextText: () => (key: string) => key },
      './ChatParts': { ...parts, useChatText: () => ({ c: (key: string) => key }) },
      './ReportDialog': { ...reports, useReportText: () => (key: string) => key },
    });
}
test('Chat preserves references on errors/cancel, clears on success/sentDraft, and never replays message sources', async () => {
  const sends: any[] = []; let outcome: any = false;
  const h = await chatFixture(async (...args: any[]) => { sends.push(args); if (outcome instanceof Error) throw outcome; return outcome; });
  const choose = () => h.find((el) => el.type === picker.default).props;
  choose().onChoose(resolved()); h.render();
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture question' } }); h.render();
  const submit = async () => { h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); h.render(); };
  await submit(); assert.equal(choose().selected.length, 1);
  assert.deepEqual(plain(sends[0]), ['Fixture question', [resolved().reference]]);
  outcome = new Error('Fixture refused'); await submit(); assert.equal(choose().selected.length, 1);
  h.render({ conversation: conversation({ scope: ['ws_b'] }) });
  assert.equal(choose().selected.length, 1); assert.deepEqual(choose().spaces, [spaces[1]]); assert.ok(h.text().includes('outsideScope'));
  const sources = [{ title: 'History fixture', workspace_name: 'Fixture B', open_path: '/tables/w/ws_b/tables/t' }];
  const msg = { id: 'm_fixture', parent_id: null, role: 'user', body: 'Fixture history', hidden: false, tool_calls: [], contexts: sources };
  h.render({ conversation: conversation({ head_id: msg.id, messages: [msg] }) });
  assert.equal(h.find((el) => el.type === picker.ContextSources).props.contexts, sources);
  outcome = true; await submit(); assert.equal(choose().selected.length, 0);
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture follow-up' } }); h.render();
  await submit(); assert.equal(sends.at(-1)[1], undefined);
  choose().onChoose(resolved()); h.render(); h.render({ sentDraft: 1 }); assert.equal(choose().selected.length, 0);
  h.unmount();
});
test('Chat disables the picker in every blocked state and changes its key when scope changes', async () => {
  const h = await chatFixture(async () => false);
  const find = () => h.find((el) => el.type === picker.default);
  const key = find().key;
  for (const overrides of [{ loading: true }, { error: new Error('fixture') }, { configured: false },
    { conversation: conversation({ sealed: 'access_changed' }) }, { conversation: conversation({ status: 'archived' }) },
    { conversation: conversation({ messages: [{ id: 'fixture', turn: { status: 'running' } }] }) }]) {
    h.render({ loading: false, error: undefined, configured: true, conversation: conversation(), ...overrides });
    assert.equal(find().props.disabled, true);
  }
  h.render({ loading: false, error: undefined, configured: true, conversation: conversation({ scope: ['ws_b'] }) });
  assert.notEqual(find().key, key); assert.equal(find().props.disabled, false);
  h.unmount();
});

async function appFixture(billing = true, withConsent = false) {
  const chat = await server.ssrLoadModule('/src/apps/agent/Chat.tsx');
  const controls = await server.ssrLoadModule('/src/apps/agent/ConsentControls.tsx');
  const consents = await server.ssrLoadModule('/src/apps/agent/consent.ts');
  const current = conversation({ scope: ['ws_a'] });
  const writes: any[] = [], navigations: string[] = [];
  let failure: unknown, route: string | undefined = current.id;
  const consent = { id: 'consent_fixture', conversation_id: current.id, scope: ['ws_a'], status: 'active', expires_at: null, remaining_runs: 2 };
  const h = await compiled('index.tsx', {}, {
    'react-router-dom': { ...router, useMatch: (path: string) => path === '/agent/c/:cid' && route ? { params: { cid: route } } : null,
      useLocation: () => ({ pathname: route ? `/agent/c/${route}` : '/agent' }), useNavigate: () => (path: string) => navigations.push(path) },
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (key: string) => key }), useDialog: () => ({}) },
    '@synwevo/auth-client': { ...client, useSession: () => ({ me: { id: 'usr_fixture', display_name: 'Fixture user' }, state: {} }),
      get: async () => ({ configured: false, payers: [] }) },
    '../../shared/catalog': { useApp: () => ({ app: { id: 'agent' } }) },
    './useConversation': { useConversation: () => ({ value: route ? { ...current, id: route } : undefined, refresh: async () => {}, connection: 'connected' }) },
    './useHistory': { useHistory: () => ({ rows: [], cursor: null }) },
    './ConsentControls': { ...controls, useConversationConsents: () => ({ data: withConsent ? [consent] : [], refresh() {} }) },
    // Actual consent writer; only its surrounding read state is a fixture.
    './consent': { ...consents },
    '../../shared/ops': { newKey: () => `fixture_key_${writes.length}`, read: async (operation: string) => {
      if (operation === 'agent.settings.get') return { active: true, billing: { required: billing } };
      if (operation === 'agent.workspace_policy.list') return { workspaces: spaces };
      return null;
    }, op: async (operation: string, input: any, options: any) => {
      writes.push({ operation, input, options }); if (failure) throw failure;
      return { status: 'succeeded', output: operation === 'agent.turn.quote'
        ? { id: 'quote_fixture', payer_user_id: 'usr_fixture' } : operation === 'agent.conversation.create' ? { id: 'conv_new_fixture' } : { turn_id: 'turn_fixture' } };
    } },
  }, 'app');
  await settle(); h.render(); await settle(); h.render();
  return { ...h, writes, navigations, consent, fail: (value: any) => { failure = value; },
    route: (id?: string) => { route = id; h.render(); },
    chat: () => h.find((el) => el.type === chat.default).props,
    dialog: () => h.find((el) => typeof el.props.onQuote === 'function').props };
}

test('direct, quoted, renewed and consent-paid starts preserve exact context refs through the actual AgentApp', async () => {
  const refs = [resolved().reference, resolved(item('table_fixture', 'ws_a', 'table'), 'b'.repeat(64)).reference];
  const direct = await appFixture(false);
  await direct.chat().send('Fixture question', refs);
  assert.deepEqual(plain(direct.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture question', context_refs: refs });
  direct.unmount();
  const quoted = await appFixture();
  assert.equal(await quoted.chat().send('Fixture question', refs), false); quoted.render();
  assert.equal(quoted.dialog().intent.context_refs, refs);
  const params = { budget: '500', max_calls: 3, run_seconds: 300 };
  const quote = await quoted.dialog().onQuote(params);
  await quoted.dialog().onQuote({ ...params, budget: '600' });
  for (const write of quoted.writes) assert.deepEqual(plain(write.input.context_refs), refs);
  await quoted.dialog().onConfirm(quote); quoted.render();
  assert.deepEqual(plain(quoted.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture question', context_refs: refs, quote_id: 'quote_fixture' });
  assert.equal(quoted.chat().sentDraft, 1); quoted.unmount();
  const active = await appFixture(true, true);
  assert.equal(await active.chat().send('Fixture consent', refs), true);
  assert.deepEqual(plain(active.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture consent', context_refs: refs, consent_id: 'consent_fixture' });
  active.unmount();
  const created = await appFixture();
  await created.chat().send('Fixture new consent', refs); created.render();
  await created.dialog().consent.send(created.consent, false); created.render();
  assert.deepEqual(plain(created.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture new consent', context_refs: refs, consent_id: 'consent_fixture' });
  assert.equal(created.chat().sentDraft, 1); created.unmount();
});

test('quote cancellation retains draft intent; source refusals expose the actionable error and never clear sentDraft', async () => {
  const h = await appFixture(); const refs = [resolved().reference];
  await h.chat().send('Fixture question', refs); h.render();
  h.dialog().onClose(); h.render(); assert.equal(h.chat().sentDraft, 0);
  await h.chat().send('Fixture question', refs); h.render();
  const refusal = new client.ApiError(409, 'context_changed', 'Fixture server refusal'); h.fail(refusal);
  await assert.rejects(h.dialog().onQuote({ budget: '500', max_calls: 3, run_seconds: 300 })); h.render();
  assert.equal(h.elements().filter((el) => typeof el.props.onQuote === 'function').length, 0);
  assert.ok(h.elements().some((el) => el.props.error === refusal)); assert.equal(h.chat().sentDraft, 0);
  assert.deepEqual(plain(h.writes.at(-1).input.context_refs), refs); h.unmount();
});

test('new-conversation refs survive creation; regeneration never auto-attaches the selected/history sources', async () => {
  const h = await appFixture(); const refs = [resolved().reference]; h.route(undefined);
  await h.chat().send('Fixture first question', refs); h.render();
  assert.equal(h.writes[0].operation, 'agent.conversation.create');
  assert.equal(h.dialog().intent.conversation_id, 'conv_new_fixture'); assert.equal(h.dialog().intent.context_refs, refs);
  h.dialog().onClose(); h.render(); h.route('conv_fixture');
  await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'message_fixture' }); h.render();
  assert.equal(h.dialog().intent.context_refs, undefined);
  h.dialog().onClose(); h.render();
  await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', text: 'Fixture explicit', parent_message_id: 'parent_fixture', context_refs: refs }); h.render();
  assert.equal(h.dialog().intent.context_refs, refs); assert.equal(h.dialog().intent.parent_message_id, 'parent_fixture'); h.unmount();
});

test('the picker and its dialog are outside the composer form; narrow layout and shared focus handling are retained', async () => {
  const h = await chatFixture(async () => false);
  const form = h.find((el) => el.type === 'form');
  assert.ok(!all(form).some((el) => el.type === picker.default));
  const dialog = await dialogFixture();
  assert.equal(dialog.control('query').maxLength, 100); assert.equal(dialog.tree().type, ui.Modal);
  const css = readFileSync(new URL('../src/apps/agent/agent.css', import.meta.url), 'utf8');
  assert.match(css, /@media \(max-width: 480px\)\s*\{\s*\.ag-context-filters \{ grid-template-columns: minmax\(0, 1fr\);/);
  const full = await compiled('ContextPicker.tsx', { spaces, selected: [], disabled: false, onChoose() {}, onRemove() {} });
  full.button('Choose business context').onClick(); full.render();
  assert.ok(full.elements().some((el) => el.type.name === 'ContextDialog'));
  full.render({ disabled: true }); assert.ok(!full.elements().some((el) => el.type.name === 'ContextDialog'));
  full.unmount(); dialog.unmount(); h.unmount();
});
