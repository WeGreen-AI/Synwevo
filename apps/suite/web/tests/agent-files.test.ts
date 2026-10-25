/** Compiled production UI/helpers with explicit hook/network fixtures. No live browser,
 * scanner, model, billing or upload service claim is made by these source tests. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';
import { testServer } from './_vite.ts';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import * as router from 'react-router-dom';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/c/conv_fixture'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;
let server: any, ui: any, core: any, helpers: any, picker: any, copy: any, budget: any, errors: any, client: any, contextHelpers: any, businessPicker: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'file-fixture-locale', enforce: 'pre', transform(code, id) {
      if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
    } }],
  });
  ui = await server.ssrLoadModule('@synwevo/ui'); core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  for (const path of ['/src/shared/messages.ts', '/src/apps/agent/messages.ts']) await server.ssrLoadModule(path);
  [helpers, picker, copy, budget, errors, client, contextHelpers, businessPicker] = await Promise.all([
    server.ssrLoadModule('/src/apps/agent/files.ts'), server.ssrLoadModule('/src/apps/agent/FilePicker.tsx'),
    server.ssrLoadModule('/src/apps/agent/files.messages.ts'), server.ssrLoadModule('/src/apps/agent/budget.ts'),
    server.ssrLoadModule('/src/apps/agent/errors.tsx'), server.ssrLoadModule('@synwevo/auth-client'),
    server.ssrLoadModule('/src/apps/agent/context.ts'), server.ssrLoadModule('/src/apps/agent/ContextPicker.tsx'),
  ]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));
const plain = (value: any) => JSON.parse(JSON.stringify(value));
const spaces = ['a', 'b'].map((id) => ({ workspace_id: `ws_${id}`, name: `Fixture ${id}`, role: 'member', processing: 'all' }));
const conversation = (extra = {}) => ({ id: 'conv_fixture', title: 'Fixture conversation', status: 'active', head_id: null,
  scope: null, sealed: null, messages: [], event_seq: 1, updated_at: 1, ...extra });
const all = (node: any): any[] => Array.isArray(node) ? node.flatMap(all) : React.isValidElement<any>(node)
  ? [node, ...all(node.props.children), ...all(node.props.footer), ...all(node.props.aside)] : [];
const strings = (node: any): string => Array.isArray(node) ? node.map(strings).join(' ') : React.isValidElement<any>(node)
  ? strings(node.props.children) : typeof node === 'string' ? node : '';
const caps = { configured: true, formats: ['txt', 'md', 'csv', 'tsv', 'pdf'], max_bytes: 8388608, max_files: 50,
  max_storage_bytes: 52428800, max_context_chars: 60000, unattached_retention_seconds: 86400, scanner: { status: 'off', configured: false }, ocr: false };
const record = (extra: any = {}) => ({ id: 'af_123456789abc', filename: 'Fixture.txt', size: 12, created_at: 1791634501.123456, updated_at: 1791634502.654321,
  attempts: 1, status: 'ready', error_code: null, scan_status: 'not_configured',
  reference: { file_id: 'af_123456789abc', snapshot: 'a'.repeat(64) }, download_path: '/api/suite/agent/files/af_123456789abc/download', ...extra });
const queued = (extra: any = {}) => record({ status: 'queued', reference: null, download_path: null, ...extra });
const filePage = (files: any[] = [record()], next_offset: number | null = null) => ({ files, next_offset });
test('file records accept real fractional-second timestamps but reject invalid time or fractional counts', () => {
  assert.equal(helpers.fileRecord(record()).created_at, 1791634501.123456);
  for (const key of ['created_at', 'updated_at']) {
    for (const value of [-1, NaN, Infinity, '100']) assert.throws(() => helpers.fileRecord(record({ [key]: value })), /invalid_file_response/);
  }
  for (const key of ['size', 'attempts']) assert.throws(() => helpers.fileRecord(record({ [key]: 1.5 })), /invalid_file_response/);
});
function render(locale: string, element: React.ReactElement) {
  ui.setLocale(locale); return renderToStaticMarkup(React.createElement(router.MemoryRouter, null, element));
}
/** Hooks are fixtures; the component body and event handlers are compiled production code. */
async function compiled(file: string, initialProps: any, overrides: Record<string, any> = {}, entry = 'default', globals: Record<string, any> = {}) {
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
  const context = vm.createContext({ AbortController, URL, setTimeout, clearTimeout, crypto: webcrypto, File, FormData, ...globals });
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
/** FIXTURE `agent.setup.get` (send-and-go contract §3): everything done, or the terms still to accept. */
function setupFixture(complete: boolean) {
  return { required: true, complete, provider_consent: null,
    terms: { complete, documents: [{ id: 'terms', version: 'fixture-1', effective: '2026-10-24', accepted: complete }] },
    limits: { configured: true, enabled: true, revision: 1, unit: { code: 'CNY', scale: 2 }, suggested: null } };
}
/** FIXTURE `agent.billing.status`: the run bound with the price every paid send repeats (AD-1); a paid
 *  send without a shown price is held in the page (addendum-2 W3). */
const SEEN = { seen_price_id: 'price_fixture', seen_route_version: 'fixture-v1' };
const statusFixture = () => ({ available: true, environment: 'test', unit: { code: 'CNY', scale: 2 }, per_call_hold: '39',
  run: { maximum: '468', default: '468', cap: null, source: 'default', per_call_hold: '39', max_calls: 12, run_seconds: 3600, usable: true,
    price_id: SEEN.seen_price_id, route_version: SEEN.seen_route_version }, price_notice: null });
async function appFixture(billing = true, setupComplete = true) {
  const chat = await server.ssrLoadModule('/src/apps/agent/Chat.tsx');
  const setupCard = await server.ssrLoadModule('/src/apps/agent/AgentSetup.tsx');
  const current = conversation({ scope: ['ws_a'] });
  const writes: any[] = [], navigations: string[] = [];
  let failure: unknown, failing: string | undefined, route: string | undefined = current.id;
  const h = await compiled('index.tsx', {}, {
    'react-router-dom': { ...router, useMatch: (path: string) => path === '/agent/c/:cid' && route ? { params: { cid: route } } : null,
      useLocation: () => ({ pathname: route ? `/agent/c/${route}` : '/agent' }), useNavigate: () => (path: string) => navigations.push(path) },
    '@synwevo/ui': { ...ui, useI18n: () => ({ t: (key: string) => key }), useDialog: () => ({}) },
    '@synwevo/auth-client': { ...client, useSession: () => ({ me: { id: 'usr_fixture', display_name: 'Fixture user' }, state: {} }),
      get: async () => ({ configured: false, payers: [] }) },
    '../../shared/catalog': { useApp: () => ({ app: { id: 'agent' } }) },
    './useConversation': { useConversation: () => ({ value: route ? { ...current, id: route } : undefined, refresh: async () => {}, connection: 'connected' }) },
    './useHistory': { useHistory: () => ({ rows: [], cursor: null }) },
    '../../shared/ops': { newKey: () => `fixture_key_${writes.length}`, read: async (operation: string) => {
      if (operation === 'agent.settings.get') return { active: true, billing: { required: billing } };
      if (operation === 'agent.workspace_policy.list') return { workspaces: spaces };
      if (operation === 'agent.setup.get') return setupFixture(setupComplete);
      if (operation === 'agent.billing.status') return statusFixture();
      return null;
    }, op: async (operation: string, input: any, options: any) => {
      writes.push({ operation, input, options }); if (failure && (!failing || failing === operation)) throw failure;
      return { status: 'succeeded', output: operation === 'agent.conversation.create' ? { id: 'conv_new_fixture' } : { turn_id: 'turn_fixture' } };
    } },
  }, 'app');
  await settle(); h.render(); await settle(); h.render();
  const fixture = { ...h, writes, navigations, fail: (value: any, operation?: string) => { failure = value; failing = operation; },
    route: (id?: string) => { route = id; h.render(); },
    chat: () => h.find((el) => el.type === chat.default).props,
    card: () => all(fixture.chat().aside).filter((el) => el.type === setupCard.default) };
  return fixture;
}


test('capabilities govern formats and size; incomplete records, unsafe downloads and malformed extraction fail closed', () => {
  assert.deepEqual(helpers.fileCapabilities(caps), caps);
  const recognizing = { ...caps, ocr: true, ocr_languages: ['chi_sim', 'eng'], ocr_pages: 5, formats: [...caps.formats, 'png', 'jpg', 'jpeg'] };
  assert.deepEqual(helpers.fileCapabilities(recognizing), recognizing);
  for (const value of [{ ...caps, ocr: 'yes' }, { ...recognizing, ocr_pages: 0 }, { ...recognizing, ocr_languages: 'eng' }, { ...caps, max_bytes: 0 }, { ...caps, formats: ['../txt'] }]) assert.throws(() => helpers.fileCapabilities(value));
  for (const name of ['Fixture.docx', 'Fixture.xlsx', 'Fixture.png']) assert.throws(() => helpers.checkUpload(new File(['fixture'], name), caps), /context_format_unsupported/);
  assert.throws(() => helpers.checkUpload(new File([], 'empty.txt'), caps), /agent_file_limit/);
  assert.throws(() => helpers.checkUpload(new File(['ab'], 'large.txt'), { ...caps, max_bytes: 1 }), /agent_file_limit/);
  assert.throws(() => helpers.checkUpload(new File(['fixture'], 'safe.txt'), { ...caps, configured: false }), /agent_file_not_configured/);
  assert.throws(() => helpers.fileRecord(record({ reference: { file_id: 'wrong', snapshot: 'a'.repeat(64) } })));
  assert.throws(() => helpers.fileRecord(record({ extraction: { lines: [{ text: {} }], tables: [] } })));
  assert.throws(() => helpers.filePage(filePage(Array(21).fill(record())), 0));
  assert.throws(() => helpers.filePage(filePage([], 20), 0));
  for (const path of ['//evil.test', '/%2f%2fevil.test', '/api/suite/agent/files/wrong/download', '/agent/settings', '/api/suite/agent/files/af_123456789abc/download?redirect=x']) {
    assert.equal(helpers.fileRecord(record({ download_path: path })).download_path, null);
  }
  assert.throws(() => helpers.selectFile(queued()), /agent_file_not_ready/);
  assert.equal(helpers.canRetryFile(queued({ status: 'failed', attempts: 3 })), false);
});

test('combined five-source limit and exact file refs by id and snapshot', () => {
  const selected = helpers.selectFile(record());
  assert.deepEqual(helpers.fileReferences([selected], 4), [record().reference]);
  assert.throws(() => helpers.fileReferences([selected], 5), /invalid_context/);
  assert.deepEqual(helpers.addFile([], selected, 5), []);
  assert.equal(helpers.addFile([selected], selected, 0).length, 1);
  const business = { reference: { kind: 'form', workspace_id: 'fixture', resource_id: 'fixture', snapshot: 'b'.repeat(64) }, title: 'Fixture' };
  assert.deepEqual(contextHelpers.addContext([], business, 5), []);
});

test('real File multipart uses the authenticated API, CSRF and a stable retry key; replaced bytes get a new key', async () => {
  const originalFetch = globalThis.fetch, originalDocument = globalThis.document;
  const requests: any[] = [];
  globalThis.document = { cookie: 'dw_csrf=fixture_csrf' } as any;
  client.setWorkspaceContext('ws_fixture', { initial: true });
  globalThis.fetch = (async (path, options) => {
    requests.push({ path, ...options });
    if (requests.length === 1) throw new Error('fixture dropped connection');
    return new Response(JSON.stringify(queued()), { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as any;
  try {
    const files = helpers.createFileClient();
    const first = await files.prepare(new File(['same'], 'Fixture.txt'), caps);
    await assert.rejects(files.upload(first, new AbortController().signal));
    assert.equal(first.state, 'unknown');
    const retry = await files.prepare(new File(['same'], 'Fixture.txt'), caps);
    assert.equal(first, retry);
    await files.upload(retry, new AbortController().signal);
    assert.equal(requests.length, 2); assert.equal(requests[0].headers['Idempotency-Key'], requests[1].headers['Idempotency-Key']);
    assert.equal(requests[0].headers['X-Synwevo-CSRF'], 'fixture_csrf');
    assert.equal(requests[0].headers['X-Synwevo-Workspace'], undefined);
    assert.equal(Object.keys(requests[0].headers).some((key) => key.toLowerCase() === 'content-type'), false);
    assert.ok(requests[0].body instanceof FormData); assert.ok(requests[0].body.get('file') instanceof File);
    assert.equal(await requests[0].body.get('file').text(), 'same'); assert.deepEqual([...requests[0].body.keys()], ['file']);
    const replaced = await files.prepare(new File(['diff'], 'Fixture.txt'), caps);
    assert.notEqual(replaced.key, first.key);
  } finally { globalThis.fetch = originalFetch; globalThis.document = originalDocument; client.setWorkspaceContext(null, { initial: true }); }
});

async function moduleFixture(overrides: any) {
  const context = vm.createContext({ crypto: webcrypto, File, FormData });
  const source = transformSync('files.ts', readFileSync(new URL('../src/apps/agent/files.ts', import.meta.url), 'utf8'), { target: 'es2022' });
  const mod = new vm.SourceTextModule(source.code, { context });
  await mod.link(async (name) => {
    const members = overrides[name] ?? await server.ssrLoadModule(name.startsWith('.') ? `/src/apps/agent/${name}` : name);
    return new vm.SyntheticModule(Object.keys(members), function () { for (const [key, value] of Object.entries(members)) this.setExport(key, value); }, { context });
  });
  await mod.evaluate(); return mod.namespace as any;
}
test('cancelled uploads retain keys across close/reopen and stale completion cannot replace a newer retry', async () => {
  const calls: any[] = [];
  const module = await moduleFixture({ '@synwevo/auth-client': { api: (_: string, options: any) => new Promise((resolve, reject) => calls.push({ options, resolve, reject })) } });
  const files = module.createFileClient();
  const attempt = await files.prepare(new File(['Fixture'], 'Fixture.txt'), caps);
  const old = new AbortController(); const first = files.upload(attempt, old.signal); old.abort(); attempt.state = 'unknown';
  assert.equal(files.pending(), attempt);
  const retry = files.upload(attempt, new AbortController().signal);
  calls[0].reject(new Error('fixture aborted')); await assert.rejects(first);
  assert.equal(attempt.state, 'uploading');
  assert.equal(calls[0].options.headers['Idempotency-Key'], calls[1].options.headers['Idempotency-Key']);
  calls[1].resolve(queued()); await retry; assert.equal(attempt.state, 'received');
});

test('file mutations use the operation idempotency helper and accept the minimal delete response', async () => {
  const writes: any[] = [], reads: any[] = []; let fail = true, key = 0;
  const module = await moduleFixture({ '../../shared/ops': { newKey: () => `fixture_${++key}`,
    read: async (...args: any[]) => { reads.push(args); return args[0] === 'agent.file.list' ? filePage() : record(); },
    op: async (operation: string, input: any, options: any) => { writes.push({ operation, input, options });
      if (fail) throw new Error('fixture uncertain');
      return { output: operation === 'agent.file.delete' ? { id: input.file_id, status: 'deleted' } : queued() }; },
  } });
  const files = module.createFileClient(), signal = new AbortController().signal;
  await files.list(20, signal); await files.get(record().id, signal);
  assert.deepEqual(plain(reads.map((r) => r.slice(0, 3))), [['agent.file.list', { offset: 20 }, null], ['agent.file.get', { file_id: record().id }, null]]);
  await assert.rejects(files.mutate('retry', record().id, signal)); fail = false;
  await files.mutate('retry', record().id, signal);
  assert.equal(writes[0].options.key, writes[1].options.key); assert.equal(writes[0].options.workspace, null);
  assert.deepEqual(plain(await files.mutate('delete', record().id, signal)), { id: record().id, status: 'deleted' });
  assert.notEqual(writes[2].options.key, writes[1].options.key);
});

async function dialogFixture(initial = filePage(), overrides: any = {}) {
  const requests: any[] = [], chosen: any[] = [], records: any[] = [], mutations: any[] = [];
  const timers = new Map<number, () => Promise<void>>(); let timer = 0, closed = false, refreshed = 0;
  const fake = { pending: () => undefined, capabilities: async () => caps, list: async () => initial,
    get: (id: string, signal: AbortSignal) => new Promise((resolve, reject) => requests.push({ id, signal, resolve, reject })),
    mutate: async (action: string, id: string, signal: AbortSignal) => { mutations.push({ action, id, signal }); return { id, status: 'deleted' }; }, ...overrides };
  const h = await compiled('FilePicker.tsx', { client: fake, selected: [], businessCount: 0, onChoose: (file: any) => chosen.push(file),
    onRemove() {}, onRecord: (file: any) => records.push(file), onDeleted: async () => { refreshed++; }, onClose: () => { closed = true; } }, {}, 'FileDialog', {
      setTimeout: (fn: () => Promise<void>) => { timers.set(++timer, fn); return timer; }, clearTimeout: (id: number) => timers.delete(id),
    });
  await settle(); h.render();
  return { ...h, requests, chosen, records, mutations, timers, closed: () => closed, refreshed: () => refreshed,
    poll: async () => { const [id, fn] = timers.entries().next().value!; timers.delete(id); void fn(); await settle(); h.render(); },
    answer: async (n: number, value: any) => { requests[n].resolve(value); await settle(); h.render(); },
    fail: async (n: number, error: any) => { requests[n].reject(error); await settle(); h.render(); } };
}

test('file selection resolves current ready reference; processing and combined limits cannot be chosen', async () => {
  const g = await dialogFixture(filePage([record(), queued({ id: 'af_queued', filename: 'Queued.txt' })]));
  assert.equal(g.button('Use “Queued.txt”').disabled, true);
  g.button('Use “Fixture.txt”').onClick(); g.render();
  assert.equal(g.requests[0].id, record().id);
  await g.answer(0, record({ reference: { ...record().reference, snapshot: 'b'.repeat(64) } }));
  assert.deepEqual(plain(g.chosen), [{ title: 'Fixture.txt', reference: { ...record().reference, snapshot: 'b'.repeat(64) } }]);
  g.render({ businessCount: 5 }); assert.equal(g.button('Use “Fixture.txt”').disabled, true);
  g.render({ businessCount: 0 }); g.button('Use “Fixture.txt”').onClick(); g.render();
  await g.answer(1, queued()); assert.equal(g.chosen.length, 1);
  assert.equal(g.find((el) => el.type.name === 'FileFailure').props.error.code, 'agent_file_not_ready');
  g.unmount();
});

test('only processing files poll; terminal failures and polling errors stop until explicit refresh', async () => {
  const h = await dialogFixture(filePage([queued(), record({ id: 'ready_fixture' }), queued({ id: 'failed_fixture', status: 'failed' })]));
  assert.equal(h.timers.size, 1); await h.poll();
  assert.deepEqual(h.requests.map((r) => r.id), [record().id]);
  await h.answer(0, queued({ status: 'parsing' })); assert.equal(h.timers.size, 1);
  await h.poll(); await h.answer(1, queued({ status: 'failed', error_code: 'context_needs_ocr' }));
  assert.equal(h.timers.size, 0);
  h.button('Refresh file list').onClick(); await settle(); h.render();
  await h.poll(); await h.fail(2, new Error('fixture offline'));
  assert.equal(h.timers.size, 0); h.render(); assert.equal(h.requests.length, 3);
  h.unmount();
});
for (const action of ['close', 'route']) test(`${action} aborts file reads and prevents late selection or poll updates`, async () => {
  const h = await dialogFixture(filePage([record(), queued({ id: 'af_other', filename: 'Queued.txt' })]));
  h.button('Use “Fixture.txt”').onClick(); h.render();
  if (action === 'close') h.tree().props.onClose(); else h.unmount();
  assert.equal(h.requests[0].signal.aborted, true);
  await h.answer(0, record()); assert.equal(h.chosen.length, 0);
  h.unmount(); assert.equal(h.timers.size, 0);
  const poll = await dialogFixture(filePage([queued()])); await poll.poll();
  poll.unmount(); assert.equal(poll.requests[0].signal.aborted, true);
});

test('deletion requires visible impact confirmation, accepts a tombstone, removes selection and refreshes history', async () => {
  let removed = '';
  const h = await dialogFixture(); h.render({ selected: [helpers.selectFile(record())], onRemove: (id: string) => { removed = id; } });
  h.button('Delete source file').onClick(); h.render();
  assert.equal(h.mutations.length, 0); assert.match(h.text(), /Dependent conversation history will be hidden/);
  h.button('Keep file').onClick(); h.render(); assert.equal(h.mutations.length, 0);
  h.button('Delete source file').onClick(); h.render();
  const group = h.find((el) => el.props.role === 'group');
  all(group).find((el) => el.type === 'button' && strings(el.props.children) === 'Delete source file').props.onClick();
  await settle(); h.render();
  assert.equal(h.mutations.length, 1); assert.equal(h.mutations[0].action, 'delete'); assert.equal(removed, record().id);
  assert.equal(h.refreshed(), 1); assert.ok(h.records.some((r) => r.status === 'deleted'));
  h.unmount();
});

test('file list pagination appends records and failed reads have an explicit refresh path', async () => {
  const offsets: number[] = []; let fail = false;
  const h = await dialogFixture(undefined, { list: async (offset: number) => { offsets.push(offset);
    if (fail) throw new Error('fixture offline');
    return offset ? filePage([record({ id: 'second', filename: 'Second.txt' })]) : filePage([record()], 20); } });
  h.button('Load more files').onClick(); await settle(); h.render();
  assert.deepEqual(offsets, [0, 20]); assert.match(h.text(), /Second.txt/); assert.match(h.text(), /Fixture.txt/);
  fail = true; h.button('Refresh file list').onClick(); await settle(); h.render();
  assert.ok(h.elements().some((el) => el.type.name === 'FileFailure'));
  fail = false; h.button('Refresh file list').onClick(); await settle(); h.render();
  assert.equal(h.elements().some((el) => el.type.name === 'FileFailure'), false); h.unmount();
});

test('all file refusals translate in three languages; scanner absence never claims clearance and sources stay personal', () => {
  assert.deepEqual(core.validateMessages(copy.fileMessages), []);
  const codes = ['agent_file_expired', 'agent_file_not_found', 'agent_file_not_ready', 'agent_file_changed', 'agent_file_limit', 'agent_file_quota',
    'agent_file_busy', 'agent_file_not_configured', 'agent_file_retry_unavailable', 'context_format_unsupported', 'context_incomplete',
    'parse_timeout', 'parse_failed', 'parse_interrupted', 'parse_cancelled', 'upload_interrupted', 'context_limit_exceeded', 'context_parse_error',
    'context_needs_ocr', 'context_ocr_unsupported', 'context_ocr_failed', 'context_encrypted', 'scan_unavailable', 'scan_limit', 'unchecked_file', 'malware_detected'];
  for (const locale of ['zh-Hans', 'en', 'zh-Hant']) {
    for (const code of codes) {
      const key = helpers.fileErrorKey({ code }); assert.ok(key, code);
      const expected = core.translate(locale, copy.fileCopy[key][0]);
      assert.equal(errors.agentErrorText({ code }, (key: string) => core.translate(locale, key)), expected);
      assert.equal(helpers.fileErrorKey({ code: 'idempotent_failure', detail: { code } }), key);
    }
    const html = render(locale, React.createElement(picker.FileDialog, { client: helpers.createFileClient(), selected: [], businessCount: 0,
      onChoose() {}, onRemove() {}, onRecord() {}, onDeleted: async () => {}, onClose() {} }));
    assert.match(html, /role="dialog"[^>]*aria-modal="true"/); assert.match(html, /<label for=/); assert.match(html, /type="file"/);
    assert.ok(html.includes(core.translate(locale, copy.fileCopy.intro[0])));
    const sources = [{ title: 'Fixture source', workspace_name: null, kind: 'file', open_path: record().download_path }];
    const card = render(locale, React.createElement(businessPicker.ContextSources, { contexts: sources }));
    assert.ok(card.includes(core.translate(locale, copy.fileCopy.open[0]))); assert.match(card, /download=""/);
    assert.equal(render(locale, React.createElement(businessPicker.ContextSources, { contexts: sources, hidden: true })), '');
  }
  assert.equal(helpers.scannerLabel(null, 'clean'), 'scanOff');
  assert.equal(helpers.scannerLabel({ status: 'off', configured: false }, 'clean'), 'scanOff');
  assert.equal(helpers.scannerLabel({ status: 'down' }, 'clean'), 'scanDown');
  assert.equal(helpers.scannerLabel({ status: 'ok' }, 'clean'), 'scanClean');
  assert.equal(helpers.scannerLabel({ status: 'ok' }, 'pending'), 'scanPending');
});

test('preview preserves text and sources without running user markup; controls wrap on narrow screens', () => {
  const html = render('en', React.createElement(picker.FilePreview, { file: record({ extraction: {
    lines: [{ text: '<script>fixture</script>', source: { page: 1, line: 2 } }],
    tables: [{ name: 'Fixture table', source: { sheet: 'Fixture' }, row_numbers: [7], rows: [['<img src=x>', 'Fixture cell']] }],
  } }) }));
  assert.ok(!html.includes('<script>')); assert.ok(html.includes('&lt;script&gt;'));
  assert.match(html, /Page 1, line 2/); assert.match(html, /scope="row">Row 7/); assert.match(html, /tabindex="0" role="region"/);
  const css = readFileSync(new URL('../src/apps/agent/agent.css', import.meta.url), 'utf8');
  assert.match(css, /\.ag-file-actions \{[^}]*flex-wrap: wrap/);
  assert.match(css, /@media \(max-width: 480px\) \{\s*\.ag-file-actions \.btn/);
});

async function chatFixture(send: any) {
  const parts = await server.ssrLoadModule('/src/apps/agent/ChatParts.tsx');
  const reports = await server.ssrLoadModule('/src/apps/agent/ReportDialog.tsx');
  return compiled('Chat.tsx', { conversation: conversation(), loading: false, error: undefined, connection: 'connected', configured: true,
    spaces, send, act: async () => {}, refresh: async () => {} }, {
      './ContextPicker': { ...businessPicker, useContextText: () => (key: string) => key },
      './ChatParts': { ...parts, useChatText: () => ({ c: (key: string) => key }) },
      './ReportDialog': { ...reports, useReportText: () => (key: string) => key },
    });
}
test('Chat preserves file drafts through a kept send or a failure, clears only accepted sends and never replays history refs', async () => {
  const sends: any[] = []; let outcome: any = false;
  const h = await chatFixture(async (...args: any[]) => { sends.push(args); if (outcome instanceof Error) throw outcome; return outcome; });
  const choose = () => h.find((el) => el.type === picker.default).props;
  const form = h.find((el) => el.type === 'form'); assert.ok(!all(form).some((el) => el.type === picker.default));
  choose().onChoose(helpers.selectFile(record())); h.render();
  h.find((el) => el.type === 'textarea').props.onChange({ target: { value: 'Fixture question' } }); h.render();
  const submit = async () => { h.find((el) => el.type === 'form').props.onSubmit({ preventDefault() {} }); await settle(); h.render(); };
  await submit(); assert.equal(choose().selected.length, 1);
  assert.deepEqual(plain(sends[0]), ['Fixture question', null, [record().reference]]);
  outcome = new Error('fixture failed'); await submit(); assert.equal(choose().selected.length, 1);
  h.render({ conversation: conversation({ scope: [] }) }); assert.equal(choose().selected.length, 1);
  outcome = true; await submit(); assert.equal(choose().selected.length, 0);
  choose().onChoose(helpers.selectFile(record())); h.render(); h.render({ sentDraft: 1 }); assert.equal(choose().selected.length, 0);
  choose().onChoose(helpers.selectFile(record())); h.render(); choose().onRecord({ id: record().id, status: 'deleted' }); h.render();
  assert.equal(choose().selected.length, 0);
  h.unmount();
});

test('billed or not, a start carries file_refs at top level and keeps context_refs separate, also through the setup step', async () => {
  const files = [record().reference], contexts = [{ kind: 'form', workspace_id: 'ws_a', resource_id: 'fixture', snapshot: 'b'.repeat(64) }];
  const direct = await appFixture(false); await direct.chat().send('Fixture', contexts, files);
  assert.deepEqual(plain(direct.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture', context_refs: contexts, file_refs: files }); direct.unmount();
  const billed = await appFixture(); assert.equal(await billed.chat().send('Fixture', contexts, files), true);
  assert.deepEqual(billed.writes.map((w) => w.operation), ['agent.turn.start']);
  assert.deepEqual(plain(billed.writes[0].input), { conversation_id: 'conv_fixture', text: 'Fixture', context_refs: contexts, file_refs: files, ...SEEN }); billed.unmount();
  const first = await appFixture(true, false); assert.equal(await first.chat().send('Fixture', contexts, files), false); first.render();
  assert.deepEqual(first.writes, []); assert.equal(first.chat().sentDraft, 0);
  await first.card()[0].props.onComplete(); first.render();
  assert.deepEqual(plain(first.writes.at(-1).input), { conversation_id: 'conv_fixture', text: 'Fixture', context_refs: contexts, file_refs: files, ...SEEN });
  assert.equal(first.chat().sentDraft, 1); first.unmount();
});

test('file refusals keep the draft; new conversations retain refs and regeneration does not attach them', async () => {
  const h = await appFixture(); h.route(undefined);
  const refusal = new client.ApiError(409, 'agent_file_changed', 'fixture'); h.fail(refusal, 'agent.turn.start');
  await assert.rejects(h.chat().send('Fixture', undefined, [record().reference])); h.render();
  assert.equal(h.writes[0].operation, 'agent.conversation.create');
  assert.equal(h.chat().sentDraft, 0);
  h.fail(undefined);
  // The created conversation is reused for the retry, with the same file refs.
  await h.chat().send('Fixture', undefined, [record().reference]); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_new_fixture', text: 'Fixture', file_refs: [record().reference], ...SEEN });
  assert.equal(h.writes.filter((w) => w.operation === 'agent.conversation.create').length, 1);
  h.route('conv_fixture');
  await h.chat().act('agent.turn.start', { conversation_id: 'conv_fixture', regenerate_of: 'fixture_message' }); h.render();
  assert.deepEqual(plain(h.writes.at(-1).input), { conversation_id: 'conv_fixture', regenerate_of: 'fixture_message', ...SEEN }); h.unmount();
});

test('upload UI accepts a native File, cancels explicitly and offers the same pending attempt after reopening without auto-upload', async () => {
  let pending: any; const uploads: any[] = [];
  const api = {
    pending: () => pending,
    prepare: async (file: File) => pending = { file, key: 'fixture_upload_key', state: 'prepared' },
    upload: (attempt: any, signal: AbortSignal) => { attempt.state = 'uploading'; return new Promise((resolve, reject) => {
      uploads.push({ attempt, signal, resolve }); signal.addEventListener('abort', () => { attempt.state = 'unknown'; reject(new Error('fixture aborted')); });
    }); },
  };
  const h = await dialogFixture(filePage([]), api);
  const file = new File(['Fixture text'], 'Fixture.txt');
  h.control('file').onChange({ target: { files: [file], value: 'fixture' } }); h.render();
  h.button('Upload selected file').onClick(); await settle(); h.render();
  assert.equal(uploads[0].attempt.file, file); assert.equal(uploads[0].attempt.key, 'fixture_upload_key');
  assert.match(h.text(), /Uploading file/); h.button('Stop upload request').onClick(); await settle(); h.render();
  assert.equal(uploads[0].signal.aborted, true); assert.match(h.text(), /upload outcome is unknown/);
  assert.equal(uploads.length, 1); h.tree().props.onClose(); h.unmount();
  const reopened = await dialogFixture(filePage([]), api);
  assert.match(reopened.text(), /upload outcome is unknown/); assert.equal(uploads.length, 1);
  reopened.button('Refresh file list').onClick(); await settle(); reopened.render(); assert.equal(uploads.length, 1);
  reopened.button('Retry this upload').onClick(); await settle(); reopened.render();
  assert.equal(uploads[1].attempt, uploads[0].attempt);
  uploads[1].resolve(queued()); await settle(); reopened.render();
  assert.match(reopened.text(), /File received/); assert.equal(reopened.button('Use “Fixture.txt”').disabled, true);
  reopened.unmount();
});

test('expired files cannot be selected, downloaded or retried; a deliberate new upload receives a new key', async () => {
  const expired = queued({ status: 'failed', error_code: 'agent_file_expired' });
  assert.equal(helpers.canRetryFile(expired), false); assert.throws(() => helpers.selectFile(expired), /agent_file_expired/);
  const h = await dialogFixture(filePage([expired]));
  assert.equal(h.button('Use “Fixture.txt”').disabled, true);
  assert.ok(!h.elements().some((el) => el.type === 'a' || (el.type === 'button' && strings(el.props.children) === 'Retry parsing')));
  h.unmount();
  const module = await moduleFixture({ '@synwevo/auth-client': { api: async () => queued() } });
  const files = module.createFileClient(), native = new File(['Fixture'], 'Fixture.txt');
  const first = await files.prepare(native, caps); await files.upload(first, new AbortController().signal);
  const next = await files.prepare(native, caps); assert.notEqual(next.key, first.key);
});

test('parse retry/cancel use explicit operations, with no retry offered after three attempts', async () => {
  const calls: string[] = [];
  const h = await dialogFixture(filePage([queued({ status: 'failed', error_code: 'parse_interrupted', attempts: 2 })]), {
    mutate: async (action: string) => { calls.push(action); return action === 'retry' ? queued({ attempts: 2 }) : queued({ status: 'cancelled', attempts: 3 }); },
  });
  h.button('Retry parsing').onClick(); await settle(); h.render(); assert.deepEqual(calls, ['retry']);
  h.button('Cancel parsing').onClick(); await settle(); h.render(); assert.deepEqual(calls, ['retry', 'cancel']);
  assert.ok(!h.elements().some((el) => el.type === 'button' && strings(el.props.children) === 'Retry parsing'));
  assert.equal(h.timers.size, 0); h.unmount();
});

test('closing an active poll aborts immediately, even before the parent unmounts the dialog', async () => {
  const h = await dialogFixture(filePage([queued()])); await h.poll();
  h.tree().props.onClose(); assert.equal(h.requests[0].signal.aborted, true);
  h.unmount();
});

test('clearing an account session also invalidates an in-flight file fingerprint', async () => {
  const files = helpers.createFileClient(); let release!: (value: ArrayBuffer) => void;
  const native = new File(['Fixture'], 'Fixture.txt');
  Object.defineProperty(native, 'arrayBuffer', { value: () => new Promise<ArrayBuffer>((resolve) => { release = resolve; }) });
  const pending = files.prepare(native, caps); files.reset(); release(new TextEncoder().encode('Fixture').buffer);
  await assert.rejects(pending, /upload_interrupted/); assert.equal(files.pending(), undefined);
});

test('preview sources use real page/line/table/row labels in three languages and never invent missing locations', () => {
  for (const locale of ['zh-Hans', 'en', 'zh-Hant']) {
    const f = (key: string, params?: any) => core.translate(locale, copy.fileCopy[key][0], params);
    assert.equal(helpers.fileSourceLabel({ page: 2, line: 9 }, f), f('sourcePageLine', { page: 2, line: 9 }));
    assert.equal(helpers.fileSourceLabel({ line: 4 }, f), f('sourceLine', { line: 4 }));
    assert.equal(helpers.fileSourceLabel({ page: 3 }, f), f('sourcePage', { page: 3 }));
    assert.equal(helpers.fileSourceLabel({ page: 2, line: 3, recognized: true, confidence: 87 }, f), f('sourceRecognized', { page: 2, line: 3, confidence: 87 }));
    for (const bad of [{ page: 2, recognized: true, confidence: 87 }, { page: 2, line: 3, recognized: true, confidence: 101 }, { page: 2, line: 3, recognized: true }]) assert.equal(helpers.fileSourceLabel(bad, f), f('sourceUnavailable'));
    assert.equal(helpers.fileSourceLabel({ sheet: 'Fixture table' }, f), f('sourceTable', { name: 'Fixture table' }));
    assert.equal(helpers.fileSourceLabel({ sheet: 'Hidden', hidden: true }, f), f('sourceTableHidden', { name: 'Hidden' }));
    assert.equal(helpers.fileSourceLabel({ part: 'body', paragraph: 3 }, f), f('sourcePartParagraph', { part: f('partBody', { n: '' }), paragraph: 3 }));
    assert.equal(helpers.fileSourceLabel({ part: 'header:2', paragraph: 1 }, f), f('sourcePartParagraph', { part: f('partHeader', { n: '2' }), paragraph: 1 }));
    assert.equal(helpers.fileSourceLabel({ part: 'footer:1', table: 1, row: 4 }, f), f('sourcePartTableRow', { part: f('partFooter', { n: '1' }), table: 1, row: 4 }));
    assert.equal(helpers.fileSourceLabel({ part: 'comments', paragraph: 2 }, f), f('sourcePartParagraph', { part: f('partComments', { n: '' }), paragraph: 2 }));
    assert.equal(helpers.fileSourceLabel({ part: 'body', paragraph: 1, textbox: 2 }, f),
      f('sourceTextbox', { label: f('sourcePartParagraph', { part: f('partBody', { n: '' }), paragraph: 1 }), n: 2 }));
    for (const bad of [{ part: 'body' }, { part: 'header', paragraph: 1 }, { part: 'body:1', paragraph: 1 }, { part: 'macros', paragraph: 1 },
      { part: 'body', paragraph: 1, table: 1, row: 1 }, { part: 'body', table: 1 }, { part: 'body', paragraph: 0 }, { part: 'body', paragraph: 1, textbox: 0 }]) {
      assert.equal(helpers.fileSourceLabel(bad, f), f('sourceUnavailable'), JSON.stringify(bad));
    }
    assert.equal(helpers.fileRowLabel(7, f), f('sourceRow', { row: 7 }));
    for (const source of [null, {}, { page: 0, line: 3 }, { line: -1 }, { line: '3' }, { unknown_schema_field: 1 }]) {
      assert.equal(helpers.fileSourceLabel(source, f), f('sourceUnavailable'));
    }
    for (const row of [undefined, null, 0, -1, 1.5, '2']) assert.equal(helpers.fileRowLabel(row, f), f('sourceUnavailable'));
    const html = render(locale, React.createElement(picker.FilePreview, { file: record({ extraction: {
      lines: [{ text: 'Fixture body', source: { page: 2, line: 9 } }, { text: 'Fixture unknown', source: { unknown_schema_field: 'private_fixture' } }],
      tables: [{ name: 'Fixture table', source: { sheet: 'Fixture table' }, rows: [['Fixture cell']] }],
    } }) }));
    assert.ok(html.includes(f('sourcePageLine', { page: 2, line: 9 })));
    assert.ok(html.includes(f('sourceUnavailable'))); assert.ok(!html.includes('unknown_schema_field')); assert.ok(!html.includes('private_fixture'));
    assert.ok(!html.includes(f('sourceRow', { row: 1 })));
  }
});
