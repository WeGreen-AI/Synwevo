/** The Agent's MCP integration centre: its catalogue, its pure decisions (model.ts) and its pages
 *  rendered in all three locales from explicit fixtures. Every connection, tool, call and address
 *  below is a fixture (marked "fixture"): no server, model or browser claim is made here. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { testServer } from './_vite.ts';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { integrationCopy, integrationMessages } from '../src/apps/agent/integrations/integrations.messages.ts';

globalThis.window ??= { location: new URL('http://127.0.0.1/agent/integrations'), history: { state: null, replaceState() {} },
  addEventListener() {}, removeEventListener() {}, setInterval: () => 0, clearInterval() {} } as any;

let server: any, ui: any, core: any, auth: any, model: any, shared: any, list: any, fresh: any, detail: any, clients: any, bindings: any,
  router: any, parts: any, errors: any;
before(async () => {
  server = await testServer({ plugins: [{ name: 'integrations-test-locale', enforce: 'pre', transform(code, id) {
    if (id.endsWith('/ui/src/i18n.tsx')) return code.replace("() => 'zh-Hans' as const", 'getLocale');
  } }] });
  // The shared i18n modules load first and once, so every catalogue registers into the instance read here.
  ui = await server.ssrLoadModule('@synwevo/ui');
  core = await server.ssrLoadModule('@synwevo/ui/i18n-core');
  auth = await server.ssrLoadModule('@synwevo/auth-client');
  [model, shared, list, fresh, detail, clients, bindings, router, parts] = await Promise.all([
    'model.ts', 'shared.tsx', 'List.tsx', 'New.tsx', 'Detail.tsx', 'Clients.tsx', 'Bindings.tsx', 'Integrations.tsx',
  ].map((f) => server.ssrLoadModule(`/src/apps/agent/integrations/${f}`)).concat([server.ssrLoadModule('/src/apps/agent/ChatParts.tsx')]));
  [errors] = await Promise.all([server.ssrLoadModule('/src/apps/agent/errors.tsx'), server.ssrLoadModule('/src/apps/agent/messages.ts')]);
});
after(async () => { ui?.setLocale('zh-Hans'); await server?.close(); });

const LOCALES = ['zh-Hans', 'en', 'zh-Hant'] as const;
const tr = (locale: string, key: keyof typeof integrationCopy, params?: Record<string, string | number>) =>
  core.translate(locale, integrationCopy[key][0], params);
function render(locale: string, element: React.ReactElement, path = '/agent/integrations'): string {
  ui.setLocale(locale);
  // Entities decoded as the identity tests do, so a sentence with an apostrophe is found as written.
  return renderToStaticMarkup(React.createElement(MemoryRouter, { initialEntries: [path] },
    React.createElement(ui.I18nProvider ?? React.Fragment, null, element)))
    .replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&');
}
const text = (html: string) => html.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

// ---------------------------------------------------------------- fixtures

function summary(overrides: Record<string, unknown> = {}) {
  return { id: 'mcpc_0123456789ab', owner_kind: 'user', owner_id: 'user_fixture', owner_name: null, slug: 'fixture-notes', name: 'Fixture notes server',
    description: 'A fixture', url: 'https://mcp.fixture.test/mcp', host: 'mcp.fixture.test', auth_kind: 'bearer', credential_hint: '…cdef',
    has_credential: true, oauth_client: null, status: 'active', health: 'ok', health_checked_at: 1_700_000_000, last_ok_at: 1_700_000_000,
    last_error: null, circuit_open_until: null, protocol_version: '2025-06-18', server_info: { name: 'fixture-server', version: '1.2.3' },
    capabilities: { tools: {} }, discovered_at: 1_700_000_000, counts: { tools: 3, resources: 1, prompts: 1, granted: 2 }, last_call_at: 1_700_000_100,
    manage: true, binding: { connection_id: 'mcpc_0123456789ab', bound: true, alias: 'fixture-notes', enabled: true, resources_enabled: false, tool_modes: { add_note: 'confirm' } },
    created_at: 1_699_999_000, updated_at: 1_700_000_000, ...overrides };
}
function tool(name: string, overrides: Record<string, unknown> = {}) {
  return { name, title: null, description: `Fixture tool ${name}`, input_schema: { type: 'object', properties: { text: { type: 'string' } } }, output_schema: null,
    annotations: {}, schema_hash: `hash-${name}`, removed: false, grant: { mode: 'confirm', stale: false, granted_by: 'user_fixture', granted_at: 1_700_000_000 },
    personal_mode: null, first_seen_at: 1_700_000_000, last_seen_at: 1_700_000_000, ...overrides };
}
function call(overrides: Record<string, unknown> = {}) {
  return { id: `mcpcall_${Math.random().toString(36).slice(2, 8)}`, origin: 'agent', kind: 'tools/call', name: 'echo', status: 'succeeded', user_id: 'user_fixture',
    conversation_id: 'conv_fixture', turn_id: 'turn_fixture', tool_call_id: 'tc_fixture', request_id: 'req_fixture', input_preview: '{"text": "hi"}',
    result_preview: '{"ok": true}', error: null, started_at: 1_700_000_100, finished_at: 1_700_000_101, duration_ms: 850, ...overrides };
}
function detailFixture(overrides: Record<string, unknown> = {}) {
  return { ...summary(), tools: [
    tool('echo', { title: 'Echo', annotations: { readOnlyHint: true }, grant: { mode: 'auto', stale: false, granted_by: 'user_fixture', granted_at: 1_700_000_000 } }),
    tool('add_note', { annotations: { destructiveHint: true }, grant: { mode: 'confirm', stale: true, granted_by: 'user_fixture', granted_at: 1_700_000_000 }, personal_mode: 'confirm' }),
    tool('gone', { removed: true, grant: { mode: 'off', stale: false, granted_by: null, granted_at: null } }),
  ], resources: [{ uri: 'notes://fixture/today', name: 'today', title: 'Today', description: 'Fixture resource', mime_type: 'text/plain', size: 120 }],
  prompts: [{ name: 'summarize', title: 'Summarize', description: 'Fixture prompt', arguments: [{ name: 'topic', required: true }, { name: 'tone', description: 'optional' }] }],
  instructions: 'Fixture instructions from the server', calls: [call(), call({ user_id: null, input_preview: null, result_preview: null, status: 'failed',
    error: { code: 'mcp_timeout', message: 'fixture' }, conversation_id: null, turn_id: null, tool_call_id: null })], ...overrides };
}
const policy = (outbound = true) => ({ revision: 1, outbound_enabled: outbound, inbound_enabled: true, allow_http: false, allowed_hosts: null, blocked_hosts: [],
  limits: {}, client_registration: 'preregistered_only' });
const inbound = (enabled = true) => ({ enabled, endpoint: 'https://workplace.fixture.test/api/suite/v1/mcp',
  metadata_url: 'https://workplace.fixture.test/.well-known/oauth-protected-resource/api/suite/v1/mcp', issuer: 'https://accounts.fixture.test',
  authorization_server_metadata: 'https://accounts.fixture.test/.well-known/oauth-authorization-server', scopes: ['mcp', 'offline_access'],
  explicit_scopes: ['approvals.decide', 'extract.decide'],
  protocol_versions: ['2026-07-28', '2025-11-25'], client_registration: 'cimd_and_preregistered' as const, transport: 'streamable-http' });

// ---------------------------------------------------------------- catalogue

test('the integration catalogue validates in all three locales and every tuple translates', () => {
  assert.deepEqual(core.validateMessages(integrationMessages), []);
  for (const [, [hans, en, hant]] of Object.entries(integrationCopy)) {
    assert.ok(hans && en && hant);
    const expectEn = typeof en === 'string' ? en : en.other;
    assert.equal(core.translate('en', hans, typeof en === 'string' ? {} : { n: 2 }), typeof en === 'string' ? expectEn : expectEn.replace('{n}', '2'));
    assert.equal(core.translate('zh-Hant', hans), hant);
  }
});

test('every mcp_* refusal code of the backend contract has its own sentence, in the chat too', () => {
  const codes = ['mcp_manage_forbidden', 'mcp_connection_limit', 'mcp_url_invalid', 'mcp_host_blocked', 'mcp_host_not_allowed', 'mcp_outbound_disabled',
    'mcp_auth_required', 'mcp_circuit_open', 'mcp_connection_disabled', 'mcp_tool_not_found', 'mcp_auto_requires_read_only', 'mcp_alias_taken',
    'mcp_timeout', 'mcp_unreachable', 'mcp_server_error', 'mcp_protocol_error', 'mcp_transfer_refused', 'mcp_resource_too_large', 'mcp_prompt_not_found',
    'mcp_oauth_denied', 'mcp_oauth_flow_expired', 'mcp_oauth_token_failed', 'mcp_oauth_registration_unavailable'];
  const t = (s: string) => s;
  for (const code of codes) {
    assert.equal(model.mcpErrorKey(code), code, code);
    const sentence = errors.agentErrorText({ code }, t);
    assert.equal(sentence, integrationCopy[code as keyof typeof integrationCopy][0]);
    for (const locale of ['en', 'zh-Hant']) assert.notEqual(core.translate(locale, sentence), sentence, `${code} in ${locale}`);
    assert.ok(!/余额|餘額|quota|balance/i.test(sentence + core.translate('en', sentence)), code);
  }
  // An OAuth code the page does not know falls to the family's sentence; any other mcp_ code to the generic MCP one; others are not MCP.
  assert.equal(model.mcpErrorKey('mcp_oauth_something_new'), 'mcp_oauth_failed');
  assert.equal(model.mcpErrorKey('mcp_brand_new'), 'mcp_failed');
  assert.equal(model.mcpErrorKey('billing_insufficient_funds'), null);
  assert.equal(errors.agentErrorText({ code: 'mcp_brand_new' }, t), integrationCopy.mcp_failed[0]);
  // The chat's own refusals are untouched.
  assert.equal(errors.agentErrorText({ code: 'conversation_not_found' }, t), '会话不存在或已删除。');
});

// ---------------------------------------------------------------- model decisions

test('health, hints, workspaces, aliases and personal modes follow the backend rules', () => {
  assert.deepEqual(model.healthView('ok'), { key: 'healthOk', tone: 'ok' });
  assert.deepEqual(model.healthView('auth_required'), { key: 'healthAuthRequired', tone: 'warn' });
  assert.deepEqual(model.healthView('whatever'), { key: 'healthUnknown', tone: '' });
  assert.deepEqual(model.toolHints({ readOnlyHint: true, destructiveHint: true }), ['hintReadOnly']);
  assert.deepEqual(model.toolHints({ destructiveHint: true, openWorldHint: true }), ['hintWrites', 'hintDestructive', 'hintOpenWorld']);
  assert.deepEqual(model.toolHints(null), ['hintWrites']);
  assert.deepEqual(model.manageableWorkspaces([{ id: 'A', name: 'A', role: 'owner' }, { id: 'B', name: 'B', role: 'admin', status: 'active' },
    { id: 'C', name: 'C', role: 'editor' }, { id: 'D', name: 'D', role: 'owner', status: 'suspended' }]).map((ws: any) => ws.id), ['A', 'B']);
  assert.deepEqual(model.manageableWorkspaces(undefined), []);
  assert.equal(model.slugifyAlias('My Notes!! Server'), 'my-notes-server');
  assert.equal(model.slugifyAlias('a'.repeat(40)), 'a'.repeat(24));
  assert.equal(model.slugifyAlias('   '), '');
  const tools = [{ name: 'a', personal_mode: 'confirm' }, { name: 'b', personal_mode: null }];
  assert.deepEqual(model.personalModes(tools), { a: 'confirm' });
  assert.deepEqual(model.personalModes(tools, { tool: 'b', mode: 'off' }), { a: 'confirm', b: 'off' });
  assert.deepEqual(model.personalModes(tools, { tool: 'a', mode: null }), {});
  assert.deepEqual(model.formatDuration(850), { key: 'durationMs', n: 850 });
  assert.deepEqual(model.formatDuration(12_400), { key: 'durationS', n: 12 });
  assert.equal(model.formatDuration(null), null);
});

test('the OAuth callback outcome and the authorization address are accepted only in safe shapes', () => {
  assert.deepEqual(model.oauthOutcome(new URLSearchParams('auth=ok')), { ok: true });
  assert.deepEqual(model.oauthOutcome(new URLSearchParams('auth=error&code=mcp_oauth_denied')), { ok: false, code: 'mcp_oauth_denied' });
  assert.deepEqual(model.oauthOutcome(new URLSearchParams('auth=error&code=<img>')), { ok: false, code: 'mcp_oauth_failed' });
  assert.equal(model.oauthOutcome(new URLSearchParams('tab=tools')), null);
  assert.equal(model.safeAuthorizationUrl('https://as.fixture.test/authorize?x=1'), 'https://as.fixture.test/authorize?x=1');
  assert.equal(model.safeAuthorizationUrl('javascript:alert(1)'), null);
  assert.equal(model.safeAuthorizationUrl('not a url'), null);
  assert.equal(model.safeAuthorizationUrl(42), null);
  assert.equal(model.errorCodeOf(new auth.ApiError(409, 'idempotent_failure', 'x', { code: 'mcp_alias_taken' })), 'mcp_alias_taken');
  assert.equal(model.errorCodeOf({ code: 'bad code!' }), '');
  assert.equal(model.errorCodeOf(new Error('x')), '');
});

test('client examples are generated from the reported addresses and never from a hard-coded host', () => {
  const info = inbound();
  const examples = model.clientExamples(info);
  assert.deepEqual(JSON.parse(examples.generic), { mcpServers: { synwevo: { type: 'http', url: info.endpoint } } });
  assert.equal(examples.claude, `claude mcp add --transport http synwevo ${info.endpoint}`);
  for (const line of ['from mcp import Client', 'from mcp.client.streamable_http import streamable_http_client', 'httpx2.AsyncClient(auth=auth, timeout=',
    'OAuthClientProvider(', `ENDPOINT = "${info.endpoint}"`, 'SCOPE = "mcp offline_access"', 'AuthorizationCodeResult(code=', 'iss=query.get("iss"',
    'REDIRECT_URI = os.environ["SYNWEVO_MCP_REDIRECT_URI"]']) assert.ok(examples.python.includes(line), line);
  // The registration the platform allows decides how the client identifies itself.
  const pre = model.clientExamples({ ...info, client_registration: 'preregistered_only' }).python;
  assert.ok(pre.includes('CLIENT_ID = os.environ["SYNWEVO_MCP_CLIENT_ID"]') && pre.includes('client_id=CLIENT_ID') && !pre.includes('client_metadata_url='));
  const doc = model.clientExamples({ ...info, client_registration: 'cimd_and_preregistered' }).python;
  assert.ok(doc.includes('client_metadata_url=CLIENT_METADATA_URL') && !doc.includes('CLIENT_ID ='));
  assert.ok(!/https?:\/\//.test(pre.replace(info.endpoint, '')), 'the only address in the example is the reported endpoint');
  const source = readFileSync(new URL('../src/apps/agent/integrations/model.ts', import.meta.url), 'utf8');
  assert.ok(!/https?:\/\/[a-z0-9.-]+\.(com|cn|io|net|org)\b/i.test(source), 'no host is written in the page source');
  // A shell-unsafe endpoint is quoted for the command line.
  assert.equal(model.clientExamples({ endpoint: "https://w.fixture.test/mcp?a=1&b='x'", scopes: [] }).claude,
    `claude mcp add --transport http synwevo 'https://w.fixture.test/mcp?a=1&b='\\''x'\\'''`);
});

test('the centre routes its own paths and refuses an id that is not a connection id', () => {
  assert.deepEqual(router.routeOf('/agent/integrations'), { page: 'list' });
  assert.deepEqual(router.routeOf('/agent/integrations/'), { page: 'list' });
  assert.deepEqual(router.routeOf('/agent/integrations/new'), { page: 'new' });
  assert.deepEqual(router.routeOf('/agent/integrations/clients'), { page: 'clients' });
  assert.deepEqual(router.routeOf('/agent/integrations/mcpc_0123456789ab'), { page: 'detail', cid: 'mcpc_0123456789ab' });
  assert.deepEqual(router.routeOf('/agent/integrations/../settings'), { page: 'unknown' });
  assert.deepEqual(router.routeOf('/agent/integrations/mcpc_0123456789ab/extra'), { page: 'unknown' });
  assert.equal(detail.tabFromSearch(new URLSearchParams('tab=tools')), 'tools');
  assert.equal(detail.tabFromSearch(new URLSearchParams('tab=nope')), 'overview');
  assert.deepEqual(detail.withBinding(detailFixture(), { connection_id: 'mcpc_0123456789ab', bound: true, alias: 'x', enabled: true, resources_enabled: true, tool_modes: { echo: 'off' } })
    .tools.map((t: any) => t.personal_mode), ['off', null, null]);
});

// ---------------------------------------------------------------- pages

for (const locale of LOCALES) test(`the connection list renders its states in ${locale}`, () => {
  const items = [summary(), summary({ id: 'mcpc_ba9876543210', name: 'Fixture workspace server', owner_kind: 'workspace', owner_id: 'ws_fixture', owner_name: 'Fixture workspace',
    health: 'auth_required', status: 'disabled', manage: false, binding: { connection_id: 'mcpc_ba9876543210', bound: false }, last_call_at: null })];
  const view = (props: Record<string, unknown>) => render(locale, React.createElement(list.ListView, { items, policy: policy(), q: '', setQ() {}, loading: false, error: null, reload() {}, ...props }));
  const html = view({});
  for (const needle of ['Fixture notes server', 'Fixture workspace server', 'mcp.fixture.test', tr(locale, 'healthOk'), tr(locale, 'healthAuthRequired'),
    tr(locale, 'statusDisabled'), tr(locale, 'bound'), tr(locale, 'add'), tr(locale, 'clientsLink'), tr(locale, 'neverCalled'),
    tr(locale, 'workspaceOwner', { name: 'Fixture workspace' }), tr(locale, 'grantedCount', { n: 2 })]) assert.ok(html.includes(needle), needle);
  assert.ok(html.includes('href="/agent/integrations/mcpc_0123456789ab"'));
  // Outbound closed by the platform: the notice, and no way to add.
  const closed = view({ policy: policy(false) });
  assert.ok(closed.includes(tr(locale, 'outboundClosed')));
  assert.ok(!closed.includes('href="/agent/integrations/new"'));
  assert.ok(text(view({ items: [] })).includes(tr(locale, 'empty')));
  assert.ok(text(view({ q: 'nothing matches this' })).includes(tr(locale, 'noMatch')));
  assert.ok(text(view({ deleted: 'Old fixture' })).includes(tr(locale, 'deleted')));
  const failed = view({ items: null, error: new auth.ApiError(403, 'mcp_outbound_disabled', 'server prose must not show') });
  assert.ok(failed.includes(tr(locale, 'mcp_outbound_disabled')));
  assert.ok(!failed.includes('server prose'));
  assert.ok(failed.includes(tr(locale, 'retry')));
  assert.ok(text(view({ items: null, loading: true })).includes(core.translate(locale, '加载中…')));
  assert.equal(list.matches(items[1], 'WORKSPACE'), true);
  assert.equal(list.matches(items[0], 'workspace'), false);
});

for (const locale of LOCALES) test(`the add flow starts at ownership and address in ${locale}`, () => {
  const html = render(locale, React.createElement(fresh.NewConnectionForm, { workspaces: [] }));
  for (const needle of [tr(locale, 'step1'), tr(locale, 'step2'), tr(locale, 'step3'), tr(locale, 'ownerPersonal'), tr(locale, 'noManagedWorkspaces'),
    tr(locale, 'name'), tr(locale, 'url'), tr(locale, 'urlHint'), tr(locale, 'next'), tr(locale, 'cancel')]) assert.ok(html.includes(needle), needle);
  assert.ok(html.includes('aria-current="step"'));
  assert.ok(/value="workspace"[^>]*disabled=""/.test(html) || /disabled=""[^>]*value="workspace"/.test(html), 'workspace ownership disabled without a managed workspace');
  const withWorkspace = render(locale, React.createElement(fresh.NewConnectionForm, { workspaces: [{ id: 'ws_fixture', name: 'Fixture workspace', role: 'owner' }] }));
  assert.ok(!withWorkspace.includes(tr(locale, 'noManagedWorkspaces')));
  assert.equal(fresh.validUrl('https://mcp.fixture.test/mcp'), true);
  for (const bad of ['ftp://x.test/mcp', 'https://user:pw@x.test/mcp', 'https://x.test/mcp#frag', 'https://x .test', '']) assert.equal(fresh.validUrl(bad), false, bad);
});

for (const locale of LOCALES) test(`a connection's detail shows its facts, grants, calls and binding in ${locale}`, () => {
  const data = detailFixture();
  const view = (tab: string, props: Record<string, unknown> = {}) => render(locale, React.createElement(detail.DetailView,
    { detail: data, tab, setTab() {}, update() {}, onDeleted() {}, ...props }), '/agent/integrations/mcpc_0123456789ab');
  const overview = view('overview');
  for (const needle of [tr(locale, 'tabOverview'), tr(locale, 'tabTools'), tr(locale, 'tabCalls'), tr(locale, 'tabMine'), tr(locale, 'healthOk'),
    'fixture-server 1.2.3', '2025-06-18', tr(locale, 'circuitClosed'), tr(locale, 'credentialEnding', { hint: 'cdef' }), tr(locale, 'testConnection'),
    tr(locale, 'refreshCaps'), tr(locale, 'disable'), tr(locale, 'revoke'), tr(locale, 'remove'), tr(locale, 'instructions')]) assert.ok(overview.includes(needle), needle);
  assert.ok(overview.includes('role="tablist"') && overview.includes('aria-selected="true"'));
  assert.ok(!overview.includes(tr(locale, 'manageOnly')));
  assert.ok(!overview.includes('disabled=""'), 'a manager has every action');
  // Not a manager: every management action disabled, with the reason.
  const readOnly = view('overview', { detail: { ...data, manage: false } });
  assert.ok(readOnly.includes(tr(locale, 'manageOnly')));
  assert.ok((readOnly.match(/<button[^>]*disabled=""/g) ?? []).length >= 5);
  // A failing OAuth connection names its state and offers authentication.
  const failing = view('overview', { detail: { ...data, auth_kind: 'oauth', has_credential: false, health: 'auth_required', last_error: { code: 'mcp_auth_required', message: 'server prose' },
    circuit_open_until: 1_700_000_900, oauth_client: { kind: 'cimd', client_id: 'https://workplace.fixture.test/.well-known/mcp-client' } } });
  for (const needle of [tr(locale, 'authenticate'), tr(locale, 'authFirst'), tr(locale, 'mcp_auth_required'), tr(locale, 'oauthCimd'), tr(locale, 'credentialNone')]) assert.ok(failing.includes(needle), needle);
  assert.ok(failing.includes(core.translate(locale, '连续失败，暂停调用至 {time}').split('{time}')[0].trim().slice(0, 4)));
  assert.ok(!failing.includes('server prose'));
  // The OAuth callback's outcome.
  assert.ok(view('overview', { authNotice: { ok: true } }).includes(tr(locale, 'authOk')));
  const denied = view('overview', { authNotice: { ok: false, code: 'mcp_oauth_denied' }, dismissAuth() {} });
  assert.ok(denied.includes(tr(locale, 'mcp_oauth_denied')) && denied.includes('mcp_oauth_denied') && denied.includes(tr(locale, 'dismiss')));
  // Tools: grant modes, the stale badge, the removed tool, auto only for read-only, personal narrowing when bound.
  const tools = view('tools');
  for (const needle of ['Echo', 'add_note', 'gone', tr(locale, 'stale'), tr(locale, 'removedTool'), tr(locale, 'hintReadOnly'), tr(locale, 'hintDestructive'),
    tr(locale, 'hintWrites'), tr(locale, 'modeAuto'), tr(locale, 'personalLabel'), tr(locale, 'personalConfirm'), tr(locale, 'schemaToggle'), tr(locale, 'toolsIntro')]) assert.ok(tools.includes(needle), needle);
  assert.ok(tools.includes('class="mi-entry removed"'));
  assert.equal((tools.match(/<option[^>]*disabled=""[^>]*value="auto"|<option[^>]*value="auto"[^>]*disabled=""/g) ?? []).length, 2, 'auto is offered only to the read-only tool');
  const unbound = view('tools', { detail: { ...data, binding: { connection_id: data.id, bound: false } } });
  assert.ok(unbound.includes(tr(locale, 'noBinding')) && !unbound.includes(tr(locale, 'personalLabel')));
  assert.ok(view('tools', { detail: { ...data, manage: false } }).includes(tr(locale, 'toolsManagerNote')));
  assert.ok(text(view('tools', { detail: { ...data, tools: [] } })).includes(tr(locale, 'toolsEmpty')));
  // Resources and prompts.
  const resources = view('resources');
  for (const needle of ['Today', 'notes://fixture/today', 'text/plain', tr(locale, 'bytes', { n: 120 }), tr(locale, 'read'), tr(locale, 'resourcesHint')]) assert.ok(resources.includes(needle), needle);
  const prompts = view('prompts');
  for (const needle of ['Summarize', 'topic', tr(locale, 'requiredArg'), 'tone', tr(locale, 'render'), tr(locale, 'promptsHint')]) assert.ok(prompts.includes(needle), needle);
  assert.ok(/<button[^>]*type="submit"[^>]*disabled=""/.test(prompts), 'a required argument blocks rendering until filled');
  // Calls: own calls show previews, another person's does not; the error is worded with its code.
  const calls = view('calls');
  for (const needle of [tr(locale, 'kindTool'), tr(locale, 'originAgent'), tr(locale, 'callSucceeded'), tr(locale, 'callFailed'), tr(locale, 'mcp_timeout'), 'mcp_timeout',
    'req_fixture', tr(locale, 'durationMs', { n: 850 }), '{"text": "hi"}', tr(locale, 'callsHint')]) assert.ok(calls.includes(needle), needle);
  assert.equal((calls.match(/<details>/g) ?? []).length, 1, 'only the own call expands');
  assert.ok(text(view('calls', { detail: { ...data, calls: [] } })).includes(tr(locale, 'callsEmpty')));
  // My Agent.
  const mine = view('mine');
  for (const needle of [tr(locale, 'mineIntro'), tr(locale, 'enabled'), tr(locale, 'alias'), 'value="fixture-notes"', tr(locale, 'resourcesEnabled'), tr(locale, 'bound'), tr(locale, 'save')]) assert.ok(mine.includes(needle), needle);
  assert.ok(view('mine', { detail: { ...data, binding: { connection_id: data.id, bound: false } } }).includes(tr(locale, 'notBound')));
});

for (const locale of LOCALES) test(`the client page shows the reported addresses and generated examples in ${locale}`, () => {
  const info = inbound();
  const html = render(locale, React.createElement(clients.ClientsView, { info }));
  for (const needle of [info.endpoint, info.metadata_url, info.authorization_server_metadata, info.issuer, 'offline_access', '2026-07-28, 2025-11-25',
    tr(locale, 'inboundOpen'), tr(locale, 'registrationCimd'), tr(locale, 'pkceNote'), tr(locale, 'explicitScopes'), 'approvals.decide', tr(locale, 'exampleGeneric'), tr(locale, 'exampleClaude'), tr(locale, 'examplePython'),
    'claude mcp add --transport http synwevo', 'from mcp import Client', tr(locale, 'copy')]) assert.ok(html.includes(needle), needle);
  assert.ok(!html.includes(tr(locale, 'inboundClosed')));
  // Every host on the page is the server's.
  for (const host of text(html).match(/https?:\/\/[a-z0-9.-]+/gi) ?? []) assert.ok(host.endsWith('.fixture.test'), host);
  const closed = render(locale, React.createElement(clients.ClientsView, { info: inbound(false) }));
  assert.ok(closed.includes(tr(locale, 'inboundClosed')) && closed.includes(tr(locale, 'inboundNotOpen')) && closed.includes(info.endpoint));
  assert.ok(render(locale, React.createElement(clients.ClientsView, { info: { ...info, client_registration: 'preregistered_only' } })).includes(tr(locale, 'registrationPre')));
});

for (const locale of LOCALES) test(`the settings section lists bound connections with their switches in ${locale}`, () => {
  const item = summary();
  const html = render(locale, React.createElement(bindings.BindingRow, { item, toolCount: 2, onChanged() {} }));
  for (const needle of ['Fixture notes server', tr(locale, 'bound'), tr(locale, 'enabled'), tr(locale, 'resourcesEnabled'), tr(locale, 'alias'), 'value="fixture-notes"',
    tr(locale, 'effectiveTools', { n: 2 }), 'href="/agent/integrations/mcpc_0123456789ab"']) assert.ok(html.includes(needle), needle);
  const off = render(locale, React.createElement(bindings.BindingRow, { item: summary({ binding: { ...item.binding, enabled: false } }), toolCount: 0, onChanged() {} }));
  assert.ok(off.includes(tr(locale, 'boundDisabled')));
});

for (const locale of LOCALES) test(`an external tool call's card names the connection, the tool and its outcome in ${locale}`, () => {
  const mcp = { connection: 'fixture-notes', connection_name: 'Fixture notes server', tool: 'add_note', is_error: true, duration_ms: 1_250, truncated: true, call_id: 'mcpcall_fixture', external: true };
  const html = render(locale, React.createElement(parts.McpCallLine, { mcp, pending: true }));
  for (const needle of [tr(locale, 'external'), tr(locale, 'cardConnection', { name: 'Fixture notes server' }), tr(locale, 'cardTool', { tool: 'add_note' }),
    tr(locale, 'cardDuration', { time: tr(locale, 'durationMs', { n: 1250 }) }), tr(locale, 'cardIsError'), tr(locale, 'cardTruncated'), tr(locale, 'cardExternalWarning')]) assert.ok(html.includes(needle), needle);
  const done = render(locale, React.createElement(parts.McpCallLine, { mcp: { ...mcp, is_error: false, truncated: false, duration_ms: null }, pending: false }));
  for (const absent of [tr(locale, 'cardIsError'), tr(locale, 'cardTruncated'), tr(locale, 'cardExternalWarning'), '耗时', 'Took']) assert.ok(!done.includes(absent), absent);
});

test('the Agent shell routes the centre and lists it beside automations and settings', () => {
  const index = readFileSync(new URL('../src/apps/agent/index.tsx', import.meta.url), 'utf8');
  assert.ok(index.includes('useMatch("/agent/integrations/*")'));
  assert.ok(index.includes('to="/agent/integrations"'));
  assert.ok(index.includes('import("./integrations/Integrations")'));
  const settings = readFileSync(new URL('../src/apps/agent/Settings.tsx', import.meta.url), 'utf8');
  assert.ok(settings.includes('<McpBindings />'));
  const chat = readFileSync(new URL('../src/apps/agent/Chat.tsx', import.meta.url), 'utf8');
  assert.ok(chat.includes('call.mcp ? <McpCallLine mcp={call.mcp} pending={pending} />'));
});
