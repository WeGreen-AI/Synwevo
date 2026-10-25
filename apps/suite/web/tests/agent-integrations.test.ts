/** The Agent's MCP integration centre: its catalogue, its pure decisions (model.ts) and its pages
 *  rendered in all three locales from explicit fixtures. Every connection, tool, call and address
 *  below is a fixture (marked "fixture"): no server, model or browser claim is made here. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
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

// ---------------------------------------------------------------- writes: what a failure says (finding 25)

test('a write failure is read by what Synwevo said, not by its HTTP status', () => {
  const E = (status: number, code: string, detail?: unknown, upstream = false) => new auth.ApiError(status, code, 'server prose (fixture)', detail, upstream);
  // MCP refusals are recorded as failed runs: definitive whatever their status.
  for (const [status, code] of [[502, 'mcp_unreachable'], [503, 'mcp_circuit_open'], [504, 'mcp_timeout'], [502, 'mcp_server_error'], [502, 'mcp_oauth_discovery_failed']] as const) {
    assert.equal(shared.writeOutcome(E(status, code), false), 'refused', code);
    assert.equal(shared.writeOutcome(E(status, code), true), 'refused', `${code} on a retry`);
  }
  assert.equal(shared.writeOutcome(E(409, 'idempotent_failure', { code: 'mcp_unreachable' }), true), 'refused');
  assert.equal(shared.writeOutcome(E(422, 'invalid_input'), false), 'refused');
  assert.equal(shared.writeOutcome(E(429, 'rate_limited'), false), 'refused');
  // Only a lost connection, an intermediary and the server's crash codes leave the outcome open.
  assert.equal(shared.writeOutcome(E(0, 'network'), false), 'unknown');
  assert.equal(shared.writeOutcome(E(502, 'upstream_unavailable', {}, true), false), 'unknown');
  assert.equal(shared.writeOutcome(E(403, 'gateway_blocked', {}, true), false), 'unknown');
  for (const code of ['server_error', 'internal_error', 'operation_failed']) assert.equal(shared.writeOutcome(E(500, code), false), 'unknown', code);
  assert.equal(shared.writeOutcome(new Error('fixture parse failure'), false), 'unknown');
  // A receipt without a replayable result: executed, to be re-read rather than sent again.
  assert.equal(shared.writeOutcome(E(409, 'run_result_unavailable', { status: 'succeeded' }), true), 'executed');
  // On a retry, a refusal that precedes the receipt lookup says nothing about the first attempt.
  for (const [status, code] of [[429, 'rate_limited'], [403, 'app_access_denied'], [401, 'session_expired']] as const) {
    assert.equal(shared.writeOutcome(E(status, code), true), 'unknown', `${code} on a retry`);
  }
});

// ---------------------------------------------------------------- writes: what the verifier found open (finding 25)

// Corrected in this round: this test asserted that a first-attempt output_too_large leaves the
// outcome open behind a same-key retry, so the page showed "not confirmed" and re-read nothing
// although the change had been applied. Every write of the centre is two-phase (the next test),
// so the refusal itself says the work was done: the page re-reads at once and sends nothing again.
test('a result too large to record was carried out: the page re-reads at once and sends nothing again', async () => {
  const E = (status: number, code: string, detail?: unknown) => new auth.ApiError(status, code, 'server prose (fixture)', detail);
  // Raised by capabilities._finish after a two-phase handler committed, with the run left running.
  assert.equal(shared.writeOutcome(E(500, 'output_too_large'), false), 'executed');
  assert.equal(shared.writeOutcome(E(500, 'output_too_large'), true), 'executed', 'on a retry whose first attempt never arrived too');
  const ref = { connection_id: 'mcpc_0123456789ab' };
  for (const operation of ['agent.mcp.connection.test', 'agent.mcp.connection.refresh', 'agent.mcp.grant.set']) {
    const fixture = scripted(E(500, 'output_too_large'), { status: 'succeeded', output: {} });
    const writer = shared.createWriter(fixture.send, fixture.newKey);
    let reloaded = 0;
    writer.start(operation, ref, () => assert.fail('not saved'), () => { reloaded += 1; });
    await settle();
    const state = writer.getState();
    assert.equal(reloaded, 1, `${operation}: the detail is read again`);
    assert.equal(state.executed, true);
    assert.equal(state.pending, false, `${operation}: the tab is not locked behind a retry`);
    await writer.retry();
    assert.equal(fixture.sent.length, 1, `${operation}: nothing is sent again`);
  }
  // A lost first response, then the retry's receipt: the same ending.
  const lost = scripted(new auth.ApiError(0, 'network', 'fixture'), E(500, 'output_too_large'));
  const writer = shared.createWriter(lost.send, lost.newKey);
  let reloaded = 0;
  writer.start('agent.mcp.connection.refresh', ref, () => assert.fail('not saved'), () => { reloaded += 1; });
  await settle();
  assert.equal(writer.getState().pending, true);
  await writer.retry();
  assert.equal(lost.sent[1].key, lost.sent[0].key);
  assert.equal(reloaded, 1);
  assert.equal(writer.getState().executed, true);
});

test('every write the integration centre sends is two-phase, which is what makes output_too_large an executed write', () => {
  const source = readFileSync(new URL('../../../../apps/suite/synwevo_suite/agent/mcp/ops.py', import.meta.url), 'utf8');
  const writes = source.split(/\bop\(id="/).slice(1).map((chunk) => chunk.split(/\n\s*op\(id="|\n\s*def /)[0])
    .filter((chunk) => /side_effect="write"/.test(chunk));
  const ids = writes.map((chunk) => chunk.slice(0, chunk.indexOf('"')));
  for (const id of ['agent.mcp.connection.create', 'agent.mcp.connection.update', 'agent.mcp.connection.test', 'agent.mcp.connection.refresh',
    'agent.mcp.grant.set', 'agent.mcp.binding.set', 'agent.mcp.credential.revoke', 'agent.mcp.oauth.start']) assert.ok(ids.includes(id), id);
  for (const chunk of writes) assert.ok(/transactional=False/.test(chunk), `${chunk.slice(0, chunk.indexOf('"'))} is two-phase`);
});

test('a request refused for its own shape or size is definitive on a retry too', async () => {
  const E = (status: number, code: string) => new auth.ApiError(status, code, 'server prose (fixture)');
  // Validation reads only the payload, which a retry repeats byte for byte: no attempt under this key can have committed.
  for (const [status, code] of [[422, 'invalid_input'], [400, 'invalid_input'], [413, 'input_too_large']] as const) {
    assert.equal(shared.writeOutcome(E(status, code), false), 'refused', code);
    assert.equal(shared.writeOutcome(E(status, code), true), 'refused', `${code} on a retry`);
  }
  const input = { connection_id: 'mcpc_0123456789ab', tool: 'echo', mode: 'auto' };
  const fixture = scripted(new auth.ApiError(0, 'network', 'fixture'), E(422, 'invalid_input'), { status: 'succeeded', output: {} });
  const writer = shared.createWriter(fixture.send, fixture.newKey);
  writer.start('agent.mcp.grant.set', input, () => {});
  await settle();
  assert.equal(writer.getState().pending, true);
  await writer.retry();
  const state = writer.getState();
  assert.equal(state.pending, false, 'the tab is not locked behind a retry that can only be refused again');
  assert.equal(model.errorCodeOf(state.error), 'invalid_input');
  // Nothing waits: a further retry sends nothing, the refusal can be dismissed, a corrected intent starts afresh.
  await writer.retry();
  assert.equal(fixture.sent.length, 2);
  writer.dismiss();
  assert.equal(writer.getState().error, null);
  assert.equal(writer.start('agent.mcp.grant.set', { ...input, mode: 'confirm' }, () => {}), true);
  await settle();
  assert.notEqual(fixture.sent[2].key, fixture.sent[0].key);
});

/** A fixture sender: answers each request with the next scripted outcome and records what was sent. */
function scripted(...answers: unknown[]) {
  const sent: { operation: string; input: Record<string, unknown>; key: string }[] = [];
  const send = async (operation: string, input: Record<string, unknown>, key: string) => {
    sent.push({ operation, input: { ...input }, key });
    const answer = answers.shift();
    if (answer instanceof Error) throw answer;
    return answer as { status: string; output: unknown };
  };
  let n = 0;
  return { sent, send, newKey: () => `key-fixture-${++n}` };
}
/** Lets a started intent's request settle (the fixture sender answers at once). */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test('testing an unreachable server ends the attempt instead of locking the tab behind a retry', async () => {
  const fixture = scripted(new auth.ApiError(502, 'mcp_unreachable', 'server prose (fixture)'), { status: 'succeeded', output: {} });
  const writer = shared.createWriter(fixture.send, fixture.newKey);
  const ref = { connection_id: 'mcpc_0123456789ab' };
  assert.equal(writer.start('agent.mcp.connection.test', ref, () => assert.fail('not saved')), true);
  await settle();
  const state = writer.getState();
  assert.equal(state.pending, false, 'a definitive refusal is not an unknown outcome');
  assert.equal(state.busy, false);
  assert.equal(state.executed, false);
  assert.equal((state.error as any).code, 'mcp_unreachable');
  // Nothing waits for a same-key retry: a retry sends nothing, the next test starts with a new key.
  await writer.retry();
  assert.equal(fixture.sent.length, 1);
  assert.equal(writer.start('agent.mcp.connection.test', ref, () => {}), true);
  await settle();
  assert.deepEqual(fixture.sent.map((s) => s.key), ['key-fixture-1', 'key-fixture-2']);
});

test('a create whose response was lost and whose result cannot be replayed is never offered as a second create', async () => {
  const fixture = scripted(new auth.ApiError(0, 'network', 'fixture'),
    new auth.ApiError(409, 'run_result_unavailable', 'server prose (fixture)', { run_id: 'run_fixture', status: 'succeeded' }));
  const writer = shared.createWriter(fixture.send, fixture.newKey);
  const input = { owner_kind: 'user', name: 'Fixture notes server', url: 'https://mcp.fixture.test/mcp', auth_kind: 'none' };
  let executed = 0;
  writer.start('agent.mcp.connection.create', input, () => assert.fail('not saved'), () => { executed += 1; });
  await settle();
  assert.equal(writer.getState().pending, true, 'a lost response leaves the outcome open');
  assert.equal(writer.start('agent.mcp.connection.create', input, () => {}), false, 'no second intent while the first is open');
  await writer.retry();
  assert.equal(fixture.sent.length, 2);
  assert.equal(fixture.sent[1].key, fixture.sent[0].key, 'the retry repeats the same key');
  assert.deepEqual(fixture.sent[1].input, fixture.sent[0].input, 'and the same payload');
  const state = writer.getState();
  assert.equal(state.executed, true, 'the server holds a receipt: executed');
  assert.equal(state.pending, false);
  assert.equal(executed, 1, 'the page is told to re-read instead of offering the create again');
  // A further retry sends nothing: the intent is settled.
  await writer.retry();
  assert.equal(fixture.sent.length, 2);
  writer.dismiss();
  assert.equal(writer.getState().executed, false);
});

test('an open outcome settles only on what the retry establishes', async () => {
  // A crash code, then a retry the rate limiter refused before the receipt lookup, then the replay.
  const fixture = scripted(new auth.ApiError(500, 'operation_failed', 'fixture'), new auth.ApiError(429, 'rate_limited', 'fixture'),
    { status: 'succeeded', output: { id: 'mcpc_0123456789ab' } });
  const writer = shared.createWriter(fixture.send, fixture.newKey);
  const saved: unknown[] = [];
  writer.start('agent.mcp.grant.set', { connection_id: 'mcpc_0123456789ab', tool: 'echo', mode: 'auto' }, (out: unknown) => saved.push(out));
  await settle();
  assert.equal(writer.getState().pending, true);
  await writer.retry();
  assert.equal(writer.getState().pending, true, 'a 429 on a retry does not say whether the first attempt committed');
  await writer.retry();
  assert.deepEqual(saved, [{ id: 'mcpc_0123456789ab' }]);
  assert.equal(writer.getState().saved, true);
  assert.equal(new Set(fixture.sent.map((s) => s.key)).size, 1, 'one key for the whole intent');
  // A replayed failure is definitive; the next intent gets a new key.
  const failing = scripted(new auth.ApiError(0, 'network', 'fixture'), new auth.ApiError(409, 'idempotent_failure', 'fixture', { code: 'mcp_unreachable' }),
    { status: 'succeeded', output: {} });
  const second = shared.createWriter(failing.send, failing.newKey);
  second.start('agent.mcp.connection.test', { connection_id: 'mcpc_0123456789ab' }, () => {});
  await settle();
  await second.retry();
  assert.equal(second.getState().pending, false);
  assert.equal(model.errorCodeOf(second.getState().error), 'mcp_unreachable');
  assert.equal(second.start('agent.mcp.connection.test', { connection_id: 'mcpc_0123456789ab' }, () => {}), true);
  await settle();
  assert.notEqual(failing.sent[2].key, failing.sent[0].key);
  // A run still in progress keeps the intent; a closed page forgets it.
  const running = scripted({ status: 'running', output: null });
  const third = shared.createWriter(running.send, running.newKey);
  third.start('agent.mcp.connection.refresh', { connection_id: 'mcpc_0123456789ab' }, () => {});
  await settle();
  assert.equal(third.getState().pending, true);
  third.close(); third.open();
  assert.equal(third.start('agent.mcp.connection.refresh', { connection_id: 'mcpc_0123456789ab' }, () => {}), true);
});

for (const locale of LOCALES) test(`an executed write and a definitive MCP refusal are worded for what they are in ${locale}`, () => {
  const write = (state: Record<string, unknown>) => ({ busy: false, pending: false, error: null, status: null, saved: false, executed: false, operation: null, input: null,
    locked: false, start: () => false, retry() {}, dismiss() {}, ...state });
  const executed = render(locale, React.createElement(shared.WriteFeedback, { write: write({ executed: true,
    error: new auth.ApiError(409, 'run_result_unavailable', 'server prose (fixture)', { status: 'succeeded' }) }) }));
  assert.ok(executed.includes(tr(locale, 'executed')));
  assert.ok(!executed.includes(tr(locale, 'retrySame')) && !executed.includes(tr(locale, 'pending')));
  assert.ok(!executed.includes('server prose'));
  const refused = render(locale, React.createElement(shared.WriteFeedback, { write: write({ error: new auth.ApiError(502, 'mcp_unreachable', 'server prose (fixture)') }) }));
  assert.ok(refused.includes(tr(locale, 'mcp_unreachable')) && !refused.includes(tr(locale, 'retrySame')));
  // Elsewhere the same code reads as the same sentence.
  assert.equal(shared.errorKeyOf(new auth.ApiError(409, 'run_result_unavailable', 'x')), 'executed');
  // The list re-read after such a create (or delete) says so.
  const listed = (unverified: unknown) => render(locale, React.createElement(list.ListView, { items: [summary()], policy: policy(), q: '', setQ() {},
    loading: false, error: null, reload() {}, unverified }));
  assert.ok(listed({ kind: 'create', name: 'Fixture notes server' }).includes(tr(locale, 'createUnverified', { name: 'Fixture notes server' })));
  assert.ok(listed({ kind: 'delete', name: 'Fixture notes server' }).includes(tr(locale, 'deleteUnverified', { name: 'Fixture notes server' })));
  assert.ok(!listed(null).includes(tr(locale, 'createUnverified', { name: 'Fixture notes server' })));
  assert.deepEqual(list.unverifiedOf({ unverified: { kind: 'create', name: 'x' } }), { kind: 'create', name: 'x' });
  for (const bad of [null, {}, { unverified: { kind: 'other', name: 'x' } }, { unverified: { kind: 'create', name: 1 } }]) assert.equal(list.unverifiedOf(bad), null);
});

// ---------------------------------------------------------------- a stale grant (finding 26)

test('a stale grant is shown as suspended and can be granted again in its previous mode', () => {
  const grant = (mode: string, stale: boolean) => ({ mode, stale, granted_by: 'user_fixture', granted_at: 1_700_000_000 });
  assert.deepEqual(model.grantView({ grant: grant('auto', true), removed: false, annotations: { readOnlyHint: true } }), { value: '', suspended: 'auto', reconfirm: true });
  assert.deepEqual(model.grantView({ grant: grant('confirm', true), removed: false, annotations: {} }), { value: '', suspended: 'confirm', reconfirm: true });
  // The server no longer declares it read-only: auto cannot be granted again, another mode can be chosen.
  assert.deepEqual(model.grantView({ grant: grant('auto', true), removed: false, annotations: {} }), { value: '', suspended: 'auto', reconfirm: false });
  // A stale off suspends nothing; a removed tool has no controls; a current grant shows its mode.
  assert.deepEqual(model.grantView({ grant: grant('off', true), removed: false, annotations: {} }), { value: 'off', suspended: null, reconfirm: false });
  assert.deepEqual(model.grantView({ grant: grant('confirm', true), removed: true, annotations: {} }), { value: 'confirm', suspended: null, reconfirm: false });
  assert.deepEqual(model.grantView({ grant: grant('auto', false), removed: false, annotations: { readOnlyHint: true } }), { value: 'auto', suspended: null, reconfirm: false });
});

for (const locale of LOCALES) test(`the tools tab offers a stale grant again in the same mode in ${locale}`, () => {
  const stale = tool('echo', { title: 'Echo', annotations: { readOnlyHint: true }, grant: { mode: 'auto', stale: true, granted_by: 'user_fixture', granted_at: 1_700_000_000 } });
  const data = detailFixture({ tools: [stale] });
  const view = (d: any) => render(locale, React.createElement(detail.DetailView, { detail: d, tab: 'tools', setTab() {}, update() {}, onDeleted() {}, reload() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  const html = view(data);
  const auto = tr(locale, 'modeAuto');
  // The select does not claim the suspended mode: a placeholder is selected, so choosing any mode is a change.
  assert.ok(/<option value="" disabled="" selected="">/.test(html), 'the placeholder is the selected option');
  assert.ok(html.includes(tr(locale, 'staleSelect')));
  assert.ok(!/<option value="auto" selected=""/.test(html), 'auto is not shown as the current grant');
  assert.ok(html.includes(tr(locale, 'staleNotice', { mode: auto })));
  // One explicit action grants it again in the same mode, available to a manager.
  const button = html.match(/<button[^>]*>([^<]*)<\/button>/g)?.find((b) => b.includes(tr(locale, 'reconfirm', { mode: auto })));
  assert.ok(button, 'the re-grant button');
  assert.ok(!button!.includes('disabled=""'));
  const readOnly = view({ ...data, manage: false }).match(/<button[^>]*>([^<]*)<\/button>/g)?.find((b) => b.includes(tr(locale, 'reconfirm', { mode: auto })));
  assert.ok(readOnly && readOnly.includes('disabled=""'), 'not a manager: disabled');
  // A tool the server no longer declares read-only cannot be granted auto again.
  const writes = view({ ...data, tools: [{ ...stale, annotations: {} }] }).match(/<button[^>]*>([^<]*)<\/button>/g)?.find((b) => b.includes(tr(locale, 'reconfirm', { mode: auto })));
  assert.ok(writes && writes.includes('disabled=""'));
  // A current grant has no placeholder and no notice.
  const current = view({ ...data, tools: [{ ...stale, grant: { ...stale.grant, stale: false } }] });
  assert.ok(!current.includes(tr(locale, 'staleSelect')) && /<option value="auto" selected=""/.test(current));
});

// ---------------------------------------------------------------- the codes and states the backend added (this round)

/** Every mcp_* code the suite's MCP backend can raise, read from the Python sources: a refusal, a
 *  call record's reason, a tool's unusable reason. Quoted names of schema objects are not codes. */
const ROOT = new URL('../../../../', import.meta.url);
function backendMcpCodes(): Set<string> {
  const dir = 'apps/suite/synwevo_suite/agent/mcp/';
  const files = [...readdirSync(new URL(dir, ROOT)).filter((f) => f.endsWith('.py')).map((f) => dir + f),
    'apps/suite/synwevo_suite/agent/engine.py', 'apps/suite/synwevo_suite/agent/tools.py',
    'packages/core/synwevo_core/mcp_policy.py', 'packages/core/synwevo_core/suite_schema/mcp.py'];
  const schema = readFileSync(new URL('packages/core/synwevo_core/suite_schema/mcp.py', ROOT), 'utf8');
  const objects = new Set([...schema.matchAll(/CREATE (?:UNIQUE )?(?:TABLE|INDEX) IF NOT EXISTS (mcp_[a-z_]+)/g)].map((m) => m[1]));
  const codes = new Set<string>();
  for (const file of files) for (const [, code] of readFileSync(new URL(file, ROOT), 'utf8').matchAll(/"(mcp_[a-z_]+)"/g)) if (!objects.has(code)) codes.add(code);
  return codes;
}
const NOT_SHOWN: [string, string][] = [
  ['mcp_connection', 'an audit object kind and a message source key, never a refusal'],
  ['mcp_policy_invalid mcp_policy_bad_revision mcp_policy_revision_conflict',
    'raised only when a platform administrator publishes a policy (synwevo_identity.admin_mcp); the Admin shell words them'],
];

test('every mcp_* code the backend raises has its own sentence, on the pages and in the chat', () => {
  const codes = backendMcpCodes();
  // The scan reads the right files: codes of each source it covers are found.
  for (const code of ['mcp_platform_disabled', 'mcp_destination_is_self', 'mcp_response_too_large', 'mcp_oauth_denied', 'mcp_agent_only',
    'mcp_call_interrupted', 'mcp_host_blocked', 'mcp_transfer_refused']) assert.ok(codes.has(code), code);
  const skip = new Set<string>();
  for (const [names, why] of NOT_SHOWN) for (const code of names.split(' ')) {
    assert.ok(codes.has(code), `${code} is listed as not shown but no longer found (${why})`);
    skip.add(code);
  }
  const t = (s: string) => s;
  const unworded: string[] = [];
  for (const code of [...codes].sort()) {
    if (skip.has(code)) continue;
    if (model.mcpErrorKey(code) !== code) { unworded.push(code); continue; }
    const sentence = integrationCopy[code as keyof typeof integrationCopy][0];
    assert.equal(errors.agentErrorText({ code }, t), sentence, `${code} in the chat`);
    assert.equal(shared.errorKeyOf(new auth.ApiError(409, code, 'server prose (fixture)')), code, `${code} on the pages`);
    for (const locale of ['en', 'zh-Hant']) assert.notEqual(core.translate(locale, sentence), sentence, `${code} in ${locale}`);
  }
  assert.deepEqual(unworded, [], 'raised with only the generic MCP sentence');
});

for (const locale of LOCALES) test(`each new refusal reads as what happened in ${locale}`, () => {
  const say = (code: string) => text(render(locale, React.createElement(shared.ErrorNotice, { error: new auth.ApiError(409, code, 'server prose (fixture)') })));
  for (const code of ['mcp_platform_disabled', 'mcp_connection_changed', 'mcp_destination_is_self', 'mcp_confirmation_required', 'mcp_agent_only',
    'mcp_record_failed', 'mcp_call_interrupted', 'mcp_response_too_large', 'mcp_policy_unavailable', 'mcp_tool_unusable', 'mcp_schema_too_large',
    'mcp_offer_too_large', 'mcp_address_changed'] as const) {
    const shown = say(code);
    assert.ok(shown.includes(tr(locale, code)), `${code}: ${shown}`);
    assert.ok(!shown.includes('server prose') && !shown.includes(tr(locale, 'mcp_failed')), code);
  }
  // The OAuth callback that lost its connection to a change says so on the detail page.
  const changed = render(locale, React.createElement(detail.DetailView, { detail: detailFixture({ auth_kind: 'oauth' }), tab: 'overview', setTab() {}, update() {},
    onDeleted() {}, authNotice: { ok: false, code: 'mcp_connection_changed' } }), '/agent/integrations/mcpc_0123456789ab');
  assert.ok(changed.includes(tr(locale, 'authError', { reason: tr(locale, 'mcp_connection_changed') })));
  // A read the server could not return for its size is named as such, not as a generic failure.
  assert.ok(text(render(locale, React.createElement(shared.ErrorNotice, { error: new auth.ApiError(500, 'output_too_large', 'x') }))).includes(tr(locale, 'tooLarge')));
  // An executed write whose result was too large says that it was carried out.
  const write = { busy: false, pending: false, error: new auth.ApiError(500, 'output_too_large', 'server prose (fixture)'), status: null, saved: false,
    executed: true, operation: 'agent.mcp.connection.test', input: null, locked: false, start: () => false, retry() {}, dismiss() {} };
  const fed = render(locale, React.createElement(shared.WriteFeedback, { write }));
  assert.ok(fed.includes(tr(locale, 'executedTooLarge')) && !fed.includes(tr(locale, 'retrySame')) && !fed.includes('server prose'));
});

for (const locale of LOCALES) test(`a call a stopped process left running reads as an unknown outcome in ${locale}`, () => {
  assert.deepEqual(model.callStatusView('unknown'), { key: 'callUnknown', tone: 'warn' });
  const html = render(locale, React.createElement(detail.CallEntry, { call: call({ status: 'unknown', finished_at: 1_700_001_000, duration_ms: null,
    error: { code: 'mcp_call_interrupted', message: 'fixture' } }) }));
  assert.ok(html.includes(tr(locale, 'callUnknown')) && html.includes(tr(locale, 'mcp_call_interrupted')) && html.includes('mcp_call_interrupted'));
  assert.ok(!html.includes(`>${tr(locale, 'callFailed')}<`), 'not worded as a failure');
  // A discovery whose findings could not be saved is this side's failure, worded as such.
  const unsaved = render(locale, React.createElement(detail.CallEntry, { call: call({ kind: 'discover', origin: 'test', status: 'failed',
    error: { code: 'mcp_record_failed', message: 'fixture' } }) }));
  assert.ok(unsaved.includes(tr(locale, 'mcp_record_failed')));
  // The chat's action card an engine restart left unknown says to check, not to retry.
  const card = errors.agentErrorText({ code: 'outcome_unknown', message: 'server prose (fixture)' }, (s: string) => core.translate(locale, s));
  assert.equal(card, tr(locale, 'outcomeUnknown'));
});

/** A connection a platform administrator stopped (fixture): disabled by the stop, credential forgotten. */
const stoppedFixture = (overrides: Record<string, unknown> = {}) => detailFixture({ status: 'disabled', platform_disabled: true, platform_disabled_at: 1_700_000_500,
  has_credential: false, credential_hint: null, health: 'auth_required', ...overrides });
const button = (html: string, label: string) => (html.match(/<button[^>]*>[^<]*<\/button>/g) ?? []).find((b) => b.replace(/<[^>]+>/g, '') === label);

for (const locale of LOCALES) test(`a platform stop is named on the list, the detail and the settings, and blocks what it refuses in ${locale}`, () => {
  // The list: the stop's own badge; the binding is not shown as used, since no Agent uses it.
  const listed = render(locale, React.createElement(list.ListView, { items: [summary({ status: 'disabled', platform_disabled: true, platform_disabled_at: 1_700_000_500 })],
    policy: policy(), q: '', setQ() {}, loading: false, error: null, reload() {} }));
  assert.ok(listed.includes(tr(locale, 'statusPlatformStopped')));
  assert.ok(listed.includes(tr(locale, 'platformStoppedRow')), 'the row says who lifts the stop');
  assert.ok(!listed.includes(`>${tr(locale, 'bound')}<`), 'not "used by my Agent" while stopped');
  assert.ok(!render(locale, React.createElement(list.ListView, { items: [summary()], policy: policy(), q: '', setQ() {}, loading: false, error: null, reload() {} }))
    .includes(tr(locale, 'statusPlatformStopped')));
  assert.ok(!render(locale, React.createElement(list.ListView, { items: [summary({ status: 'disabled' })], policy: policy(), q: '', setQ() {}, loading: false, error: null, reload() {} }))
    .includes(tr(locale, 'platformStoppedRow')), 'the owner\'s own switch is not a platform stop');
  // The detail, for the connection's manager.
  const view = (tab: string, data: any) => render(locale, React.createElement(detail.DetailView, { detail: data, tab, setTab() {}, update() {}, onDeleted() {}, reload() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  for (const kind of ['bearer', 'oauth', 'none']) {
    const overview = view('overview', stoppedFixture({ auth_kind: kind }));
    for (const needle of [tr(locale, 'platformStopped'), tr(locale, 'statusPlatformStopped'), tr(locale, 'platformStoppedAt')]) assert.ok(overview.includes(needle), `${kind}: ${needle}`);
    assert.ok(!overview.includes(tr(locale, 'disabledHint')), 'the stop, not the owner\'s own switch, is the reason');
    for (const label of ['testConnection', 'refreshCaps', 'enable'] as const) assert.ok(button(overview, tr(locale, label))?.includes('disabled=""'), `${kind}: ${label} disabled`);
    if (kind === 'oauth') assert.ok(button(overview, tr(locale, 'authenticate'))?.includes('disabled=""'), 'authenticate disabled');
    assert.ok(!overview.includes(tr(locale, 'authFirst')) && !overview.includes(tr(locale, 'tokenMissing')), 'no step the stop refuses is offered');
    assert.ok(!/type="password"/.test(overview), 'no credential can be given while stopped');
    // Deleting the stopped connection is refused (it is the record of the stop), and the manager is told why.
    assert.ok(button(overview, tr(locale, 'remove'))?.includes('disabled=""'), `${kind}: delete refused while stopped`);
    assert.ok(overview.includes(tr(locale, 'deleteStopped')), `${kind}: why delete is refused`);
  }
  // Disabling stays open if the connection is somehow still on.
  assert.ok(!button(view('overview', stoppedFixture({ status: 'active' })), tr(locale, 'disable'))?.includes('disabled=""'));
  // Resources and prompts cannot be used; the binding can be kept but the Agent does not use it.
  const resources = view('resources', stoppedFixture());
  assert.ok(resources.includes(tr(locale, 'platformStoppedUse')) && button(resources, tr(locale, 'read'))?.includes('disabled=""'));
  const prompts = view('prompts', stoppedFixture({ prompts: [{ name: 'today', title: null, description: null, arguments: [] }] }));
  assert.ok(prompts.includes(tr(locale, 'platformStoppedUse')) && /<button[^>]*type="submit"[^>]*disabled=""/.test(prompts));
  const mine = view('mine', stoppedFixture());
  assert.ok(mine.includes(tr(locale, 'platformStoppedAgent')) && mine.includes(tr(locale, 'statusPlatformStopped')));
  // A connection that is not stopped shows none of this.
  const free = view('overview', detailFixture());
  assert.ok(!free.includes(tr(locale, 'platformStopped')) && !free.includes(tr(locale, 'statusPlatformStopped')));
  assert.ok(!view('resources', detailFixture()).includes(tr(locale, 'platformStoppedUse')));
  // The Agent settings row.
  const row = render(locale, React.createElement(bindings.BindingRow, { item: summary({ status: 'disabled', platform_disabled: true }), toolCount: 0, onChanged() {} }));
  assert.ok(row.includes(tr(locale, 'statusPlatformStopped')) && row.includes(tr(locale, 'platformStoppedAgent')));
  assert.ok(!render(locale, React.createElement(bindings.BindingRow, { item: summary(), toolCount: 2, onChanged() {} })).includes(tr(locale, 'platformStoppedAgent')));
});

/** A connection at a server address another connection's platform stop closes (fixture): itself not stopped. */
const closedAddressFixture = (overrides: Record<string, unknown> = {}) => detailFixture({ destination_stopped: true, platform_disabled: false,
  platform_disabled_at: null, ...overrides });

for (const locale of LOCALES) test(`a connection whose server address a stop closes is refused the same uses, and may still be deleted, in ${locale}`, () => {
  const view = (tab: string, data: any) => render(locale, React.createElement(detail.DetailView, { detail: data, tab, setTab() {}, update() {}, onDeleted() {}, reload() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  const overview = view('overview', closedAddressFixture({ has_credential: false, credential_hint: null }));
  assert.ok(overview.includes(tr(locale, 'destinationStopped')) && !overview.includes(tr(locale, 'platformStopped')), 'the address, not this connection, was stopped');
  assert.ok(overview.includes(tr(locale, 'statusPlatformStopped')) && !overview.includes(tr(locale, 'platformStoppedAt')));
  for (const label of ['testConnection', 'refreshCaps'] as const) assert.ok(button(overview, tr(locale, label))?.includes('disabled=""'), `${label} disabled`);
  assert.ok(!/type="password"/.test(overview), 'no credential can be given for a closed address');
  assert.ok(!button(overview, tr(locale, 'remove'))?.includes('disabled=""') && !overview.includes(tr(locale, 'deleteStopped')), 'it is not the record of the stop');
  assert.ok(view('resources', closedAddressFixture()).includes(tr(locale, 'platformStoppedUse')));
  assert.ok(view('mine', closedAddressFixture()).includes(tr(locale, 'platformStoppedAgent')));
  const listed = render(locale, React.createElement(list.ListView, { items: [summary({ destination_stopped: true })], policy: policy(), q: '', setQ() {},
    loading: false, error: null, reload() {} }));
  assert.ok(listed.includes(tr(locale, 'platformStoppedRow')) && !listed.includes(`>${tr(locale, 'bound')}<`));
  assert.equal(model.platformStopped({ destination_stopped: true }), true);
  assert.equal(model.ownStop({ destination_stopped: true } as any), false);
});

test('a token is sent with the address the page shows, so the server can refuse it for another one', () => {
  const source = readFileSync(new URL('../src/apps/agent/integrations/Detail.tsx', import.meta.url), 'utf8');
  const write = source.slice(source.indexOf("tokenWrite.start<ConnectionSummary>('agent.mcp.connection.update'"));
  assert.ok(/^tokenWrite\.start<ConnectionSummary>\('agent\.mcp\.connection\.update', \{ \.\.\.ref, credential, expected_url: detail\.url \}/.test(write), write.slice(0, 160));
  const ops = readFileSync(new URL('../../../../apps/suite/synwevo_suite/agent/mcp/ops.py', import.meta.url), 'utf8');
  assert.ok(/expected_url: str \| None = F\(/.test(ops) && /expected_url=i\.expected_url/.test(ops), 'the operation takes it and hands it to the service');
});

test('the refusals added for the address a change was made against, a stopped address and a busy resolver read as what happened', () => {
  const t = (s: string) => s;
  for (const code of ['mcp_address_changed', 'mcp_destination_stopped', 'mcp_resolver_busy', 'mcp_connection_changed'] as const) {
    assert.equal(model.mcpErrorKey(code), code, code);
    assert.equal(errors.agentErrorText({ code }, t), integrationCopy[code][0], `${code} in the chat`);
    for (const locale of ['en', 'zh-Hant']) assert.notEqual(core.translate(locale, integrationCopy[code][0]), integrationCopy[code][0], `${code} in ${locale}`);
  }
  // The address refusal now covers a credential refused for another address as well as a test's discarded answer.
  assert.ok(/credential given for the old address/.test(tr('en', 'mcp_address_changed')) && /test found/.test(tr('en', 'mcp_address_changed')));
  // A changed connection says that no call was sent, not only that an authentication was not saved.
  assert.ok(/no call was sent/.test(tr('en', 'mcp_connection_changed')) && /authentication was not saved/.test(tr('en', 'mcp_connection_changed')));
  assert.ok(/Nothing was sent/.test(tr('en', 'mcp_resolver_busy')) && /try again/.test(tr('en', 'mcp_resolver_busy')));
  // A stop forbids deleting the stopped connection, and says so.
  assert.ok(/deleted/.test(tr('en', 'mcp_platform_disabled')) && /deleted/.test(tr('en', 'platformStopped')));
});

test('a connection pointed at another address is recognised from what the server reports, and its next steps follow who is reading', () => {
  const removed = [tool('echo', { removed: true, grant: { mode: 'off', stale: false, granted_by: null, granted_at: null } })];
  assert.equal(model.pointedElsewhere({ discovered_at: null, tools: removed }), true);
  assert.equal(model.pointedElsewhere({ discovered_at: 1_700_000_000, tools: removed }), false, 'discovered there already');
  assert.equal(model.pointedElsewhere({ discovered_at: null, tools: [] }), false, 'never discovered: a new connection');
  const off = { connection_id: 'mcpc_0123456789ab', bound: true, alias: 'fixture-notes', enabled: false, resources_enabled: false };
  assert.deepEqual(model.repointSteps({ manage: true, auth_kind: 'oauth', has_credential: false, binding: off }), ['nextAuthenticate', 'nextTest', 'nextGrant', 'nextBind']);
  assert.deepEqual(model.repointSteps({ manage: true, auth_kind: 'bearer', has_credential: false, binding: off }), ['nextToken', 'nextTest', 'nextGrant', 'nextBind']);
  assert.deepEqual(model.repointSteps({ manage: true, auth_kind: 'bearer', has_credential: true, binding: { connection_id: 'x', bound: false } }), ['nextTest', 'nextGrant']);
  assert.deepEqual(model.repointSteps({ manage: false, auth_kind: 'oauth', has_credential: false, binding: off }), ['nextManagers', 'nextBind']);
  // A personal connection's first discovery at the new address sets every usable tool to confirm
  // (connections._record_discovery); a workspace's tools wait for a manager's grant.
  assert.deepEqual(model.repointSteps({ owner_kind: 'user', manage: true, auth_kind: 'none', has_credential: false, binding: off }), ['nextTest', 'nextGrantPersonal', 'nextBind']);
  assert.deepEqual(model.repointSteps({ owner_kind: 'workspace', manage: true, auth_kind: 'none', has_credential: false, binding: off }), ['nextTest', 'nextGrant', 'nextBind']);
  assert.equal(model.needsToken({ auth_kind: 'bearer', has_credential: false }), true);
  assert.equal(model.needsToken({ auth_kind: 'oauth', has_credential: false }), false);
  assert.equal(model.platformStopped({ platform_disabled: true }), true);
  assert.equal(model.platformStopped({}), false);
});

for (const locale of LOCALES) test(`a connection pointed at another address says what has to happen next in ${locale}`, () => {
  // Fixture: what connections.update leaves after a new address — no discovery, the old tools removed
  // and ungranted, the credential forgotten, this person's binding switched off.
  const moved = (overrides: Record<string, unknown> = {}) => detailFixture({ url: 'https://other.fixture.test/mcp', host: 'other.fixture.test', discovered_at: null,
    server_info: null, protocol_version: null, health: 'auth_required', has_credential: false, credential_hint: null, counts: { tools: 0, resources: 0, prompts: 0, granted: 0 },
    tools: [tool('echo', { removed: true, grant: { mode: 'off', stale: false, granted_by: null, granted_at: null } })], resources: [], prompts: [],
    binding: { connection_id: 'mcpc_0123456789ab', bound: true, alias: 'fixture-notes', enabled: false, resources_enabled: false, tool_modes: {} }, ...overrides });
  const view = (data: any) => render(locale, React.createElement(detail.DetailView, { detail: data, tab: 'overview', setTab() {}, update() {}, onDeleted() {}, reload() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  // The manager of a bearer connection: the token first, then test, grant, and the binding.
  const bearer = view(moved());
  for (const key of ['repointed', 'repointedNext', 'nextToken', 'nextTest', 'nextGrantPersonal', 'nextBind', 'tokenLabel', 'saveToken'] as const) assert.ok(bearer.includes(tr(locale, key)), key);
  assert.ok(!bearer.includes(tr(locale, 'nextGrant')), 'a personal connection is granted confirm by its test, not left off');
  const team = view(moved({ owner_kind: 'workspace', owner_id: 'ws_fixture', owner_name: 'Fixture workspace' }));
  assert.ok(team.includes(tr(locale, 'nextGrant')) && !team.includes(tr(locale, 'nextGrantPersonal')), 'a workspace connection waits for a grant');
  assert.ok(/<input[^>]*type="password"[^>]*autoComplete="new-password"|<input[^>]*autoComplete="new-password"[^>]*type="password"/i.test(bearer), 'the token field');
  assert.ok(!button(bearer, tr(locale, 'saveToken'))?.includes('disabled=""'));
  assert.ok(!bearer.includes(tr(locale, 'tokenMissing')), 'the steps already say it');
  assert.ok(bearer.indexOf(tr(locale, 'nextToken')) < bearer.indexOf(tr(locale, 'nextTest')), 'in the order they have to happen');
  // OAuth: authenticate (the page's own button), no token field.
  const oauth = view(moved({ auth_kind: 'oauth' }));
  assert.ok(oauth.includes(tr(locale, 'nextAuthenticate')) && !oauth.includes(tr(locale, 'nextToken')) && !/type="password"/.test(oauth));
  assert.ok(!oauth.includes(tr(locale, 'authFirst')), 'one explanation, not two');
  // Someone who uses but does not manage it: what the managers must do, and their own binding.
  const member = view(moved({ manage: false }));
  assert.ok(member.includes(tr(locale, 'nextManagers')) && member.includes(tr(locale, 'nextBind')));
  assert.ok(!member.includes(tr(locale, 'nextTest')) && !/type="password"/.test(member));
  // Discovered at the new address: no longer described as moved.
  assert.ok(!view(moved({ discovered_at: 1_700_000_900 })).includes(tr(locale, 'repointed')));
  // A stopped connection cannot take these steps: only the stop is explained.
  const stopped = view(moved({ platform_disabled: true, platform_disabled_at: 1_700_000_500, status: 'disabled' }));
  assert.ok(stopped.includes(tr(locale, 'platformStopped')) && !stopped.includes(tr(locale, 'repointed')) && !/type="password"/.test(stopped));
  // A bearer connection whose token was revoked can be given one, by its manager only.
  const revoked = view(detailFixture({ has_credential: false, credential_hint: null }));
  assert.ok(revoked.includes(tr(locale, 'tokenMissing')) && /type="password"/.test(revoked) && !revoked.includes(tr(locale, 'repointed')));
  assert.ok(!/type="password"/.test(view(detailFixture({ has_credential: false, credential_hint: null, manage: false }))));
  assert.ok(!/type="password"/.test(view(detailFixture())), 'a stored token is not asked for again');
});

for (const locale of LOCALES) test(`a tool whose definition could not be kept can only be off, and says why in ${locale}`, () => {
  const unusable = tool('huge', { annotations: { readOnlyHint: true }, input_schema: null, unusable: { code: 'mcp_schema_too_large', max_chars: 64_000 },
    grant: { mode: 'off', stale: false, granted_by: null, granted_at: null } });
  const html = render(locale, React.createElement(detail.DetailView, { detail: detailFixture({ tools: [unusable] }), tab: 'tools', setTab() {}, update() {}, onDeleted() {}, reload() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  assert.ok(html.includes(tr(locale, 'toolUnusable')) && html.includes(tr(locale, 'mcp_schema_too_large')));
  for (const mode of ['confirm', 'auto']) assert.ok(new RegExp(`<option value="${mode}" disabled=""`).test(html), `${mode} cannot be chosen`);
  assert.ok(/<option value="off" selected=""/.test(html));
  assert.ok(!html.includes(tr(locale, 'schemaToggle')), 'no definition to show');
  // A stale grant on such a tool is not offered again.
  assert.equal(model.grantView({ ...unusable, grant: { mode: 'confirm', stale: true, granted_by: null, granted_at: null } }).reconfirm, false);
  const stale = render(locale, React.createElement(detail.DetailView, { detail: detailFixture({ tools: [{ ...unusable, grant: { mode: 'confirm', stale: true, granted_by: null, granted_at: null } }] }),
    tab: 'tools', setTab() {}, update() {}, onDeleted() {}, reload() {} }), '/agent/integrations/mcpc_0123456789ab');
  assert.ok(!stale.includes(tr(locale, 'reconfirm', { mode: tr(locale, 'modeConfirm') })));
  // An invalid definition has its own reason, and so does an offer too large to keep whole.
  assert.ok(render(locale, React.createElement(detail.ToolEntry, { tool: { ...unusable, unusable: { code: 'mcp_schema_invalid' } }, detail: detailFixture(),
    write: { locked: false, start: () => false }, update() {} })).includes(tr(locale, 'mcp_schema_invalid')));
  const crowded = render(locale, React.createElement(detail.ToolEntry, { tool: { ...unusable, unusable: { code: 'mcp_offer_too_large', max_bytes: 524_288 } },
    detail: detailFixture(), write: { locked: false, start: () => false }, update() {} }));
  assert.ok(crowded.includes(tr(locale, 'mcp_offer_too_large')) && !crowded.includes(tr(locale, 'mcp_tool_unusable')));
});

// ---------------------------------------------------------------- a page read too large to return (G7's regression a)

test('a page read too large to return is refused for its size, not reported as an executed change', async () => {
  const E = (status: number, code: string) => new auth.ApiError(status, code, 'server prose (fixture)');
  for (const operation of ['agent.mcp.resource.read_page', 'agent.mcp.prompt.render']) {
    assert.equal(shared.writeOutcome(E(500, 'output_too_large'), false, operation), 'refused', operation);
    assert.equal(shared.writeOutcome(E(500, 'output_too_large'), true, operation), 'refused', `${operation} on a retry`);
  }
  // A change is still executed, with or without the operation named.
  for (const operation of ['agent.mcp.connection.test', 'agent.mcp.grant.set', undefined]) {
    assert.equal(shared.writeOutcome(E(500, 'output_too_large'), false, operation), 'executed', String(operation));
  }
  const fixture = scripted(E(500, 'output_too_large'), { status: 'succeeded', output: {} });
  const writer = shared.createWriter(fixture.send, fixture.newKey);
  let reloaded = 0;
  writer.start('agent.mcp.resource.read_page', { connection_id: 'mcpc_0123456789ab', uri: 'notes://fixture/big' }, () => assert.fail('not shown'),
    () => { reloaded += 1; });
  await settle();
  const state = writer.getState();
  assert.equal(reloaded, 0, 'nothing changed that a re-read could show');
  assert.equal(state.executed, false);
  assert.equal(state.pending, false, 'the tab is not locked behind a retry');
  assert.equal(shared.errorKeyOf(state.error), 'tooLarge');
  // The next read starts afresh.
  assert.equal(writer.start('agent.mcp.resource.read_page', { connection_id: 'mcpc_0123456789ab', uri: 'notes://fixture/small' }, () => {}), true);
  await settle();
  assert.notEqual(fixture.sent[1].key, fixture.sent[0].key);
  for (const locale of LOCALES) {
    const shown = render(locale, React.createElement(shared.WriteFeedback, { write: { ...state, locked: false, start: () => false, retry() {}, dismiss() {} } }));
    assert.ok(text(shown).includes(tr(locale, 'tooLarge')), locale);
    assert.ok(!shown.includes(tr(locale, 'executedTooLarge')) && !shown.includes(tr(locale, 'retrySame')) && !shown.includes('server prose'), locale);
  }
});

test('the refusals a stop or a busy connection explains name every cause the backend has for them', () => {
  // mcp_tool_unavailable and mcp_resources_unavailable: effective_tools and resource_connections leave a
  // stopped connection out, so a stop reaches the chat as these codes, not as mcp_platform_disabled.
  for (const code of ['mcp_tool_unavailable', 'mcp_resources_unavailable'] as const) {
    assert.ok(/stopped by the platform/.test(tr('en', code)), code);
    assert.ok(tr('zh-Hans', code).includes('被平台停用') && tr('zh-Hant', code).includes('被平台停用'), code);
  }
  // mcp_connection_busy is raised for a token refresh under way (connections._refreshed) and for the
  // connection's concurrency cap (runtime): one sentence for both.
  assert.ok(/token is being refreshed/.test(tr('en', 'mcp_connection_busy')) && /concurrent calls/.test(tr('en', 'mcp_connection_busy')));
  // A lifted stop leaves the connection disabled and without its credential (admin_mcp.lift).
  assert.ok(/stays disabled/.test(tr('en', 'platformStopped')) && /token or OAuth/.test(tr('en', 'platformStopped')));
});

test('a refusal is worded by what its detail adds: a refresh the authorization server failed, a step-up authenticating cannot meet, why an authorization could not begin', () => {
  const t = (s: string) => s;
  for (const code of ['mcp_unreachable', 'mcp_unresolvable', 'mcp_timeout', 'mcp_server_error', 'mcp_protocol_error', 'mcp_rate_limited']) {
    assert.equal(model.mcpRefusalKey(code, { cause: 'token_refresh', retryable: true }), 'refreshFailed', code);
    assert.equal(model.mcpRefusalKey(code, { retryable: true }), code, `${code} of the MCP server itself`);
    assert.equal(shared.errorKeyOf(new auth.ApiError(502, code, 'server prose (fixture)', { cause: 'token_refresh' })), 'refreshFailed', code);
    assert.equal(errors.agentErrorText(new auth.ApiError(502, code, 'server prose (fixture)', { cause: 'token_refresh' }), t), integrationCopy.refreshFailed[0]);
  }
  // A grant the token endpoint refused still asks for a new authentication; a local refusal keeps its own words.
  assert.equal(model.mcpRefusalKey('mcp_auth_required', { cause: 'token_refresh' }), 'mcp_auth_required');
  assert.equal(model.mcpRefusalKey('mcp_resolver_busy', { cause: 'token_refresh' }), 'mcp_resolver_busy');
  assert.equal(model.mcpRefusalKey('mcp_auth_required', { step_up: 'already_requested' }), 'stepUpAlready');
  assert.equal(model.mcpRefusalKey('mcp_auth_required', { step_up: 'capped' }), 'stepUpCapped');
  assert.equal(model.mcpRefusalKey('mcp_auth_required', { step_up: 'requested' }), 'mcp_auth_required');
  assert.equal(model.mcpRefusalKey('mcp_oauth_discovery_failed', { reason: 'endpoint_origin_mismatch', endpoints: ['token_endpoint'] }), 'oauthEndpointOrigin');
  assert.equal(model.mcpRefusalKey('mcp_oauth_discovery_failed', { reason: 'issuer_mismatch' }), 'mcp_oauth_discovery_failed');
  assert.equal(model.mcpRefusalKey('mcp_oauth_registration_unavailable', { reason: 'iss_unsupported' }), 'oauthIssUnsupported');
  assert.equal(model.mcpRefusalKey('mcp_oauth_registration_unavailable', { reason: 'endpoint_origin_mismatch' }), 'oauthRegistrationOrigin');
  assert.equal(model.mcpRefusalKey('mcp_oauth_registration_unavailable', { issuer: 'https://as.fixture.test' }), 'mcp_oauth_registration_unavailable');
  assert.equal(shared.errorKeyOf(new auth.ApiError(409, 'mcp_oauth_registration_unavailable', 'x', { reason: 'iss_unsupported' })), 'oauthIssUnsupported');
  // A replay of a refused request carries the code only.
  assert.equal(shared.errorKeyOf(new auth.ApiError(409, 'idempotent_failure', 'x', { code: 'mcp_unreachable', cause: 'token_refresh' })), 'mcp_unreachable');
  // Each sentence says what it means for the person.
  assert.ok(/need not authenticate again/.test(tr('en', 'refreshFailed')) && /was not called/.test(tr('en', 'refreshFailed')));
  assert.ok(/unlikely/.test(tr('en', 'stepUpAlready')) && /limit/.test(tr('en', 'stepUpCapped')));
  assert.ok(/does not say it returns iss/.test(tr('en', 'oauthIssUnsupported')) && /client_id/.test(tr('en', 'oauthEndpointOrigin')));
});

for (const locale of LOCALES) test(`a workspace connection says its results are the workspace's content, and where its last failure happened, in ${locale}`, () => {
  const team = detailFixture({ owner_kind: 'workspace', owner_id: 'ws_fixture', owner_name: 'Fixture team' });
  const at = (d: unknown, tab: string) => render(locale, React.createElement(detail.DetailView, { detail: d, tab, setTab() {}, update() {}, onDeleted() {} }),
    '/agent/integrations/mcpc_0123456789ab');
  for (const tab of ['overview', 'mine']) assert.ok(at(team, tab).includes(tr(locale, 'workspaceContent')), tab);
  assert.ok(!at(detailFixture(), 'mine').includes(tr(locale, 'workspaceContent')), 'a personal connection is the person\'s');
  const refreshing = at(detailFixture({ health: 'failed', last_error: { code: 'mcp_unreachable', message: 'server prose (fixture)', cause: 'token_refresh' } }), 'overview');
  assert.ok(refreshing.includes(tr(locale, 'refreshFailed')) && !refreshing.includes(tr(locale, 'mcp_unreachable')) && !refreshing.includes('server prose'));
  const own = at(detailFixture({ health: 'failed', last_error: { code: 'mcp_unreachable', message: 'server prose (fixture)', cause: null } }), 'overview');
  assert.ok(own.includes(tr(locale, 'mcp_unreachable')) && !own.includes(tr(locale, 'refreshFailed')));
});

test('the person\'s own client is kept to its authorization server, and an answer too large to refresh with is the authorization server\'s', () => {
  const t = (s: string) => s;
  // A server that now names another authorization server: its own sentence, on the pages and in the chat.
  assert.equal(model.mcpErrorKey('mcp_oauth_issuer_changed'), 'mcp_oauth_issuer_changed');
  assert.equal(shared.errorKeyOf(new auth.ApiError(409, 'mcp_oauth_issuer_changed', 'server prose (fixture)',
    { issuer: 'https://other-as.fixture.test', registered_issuer: 'https://as.fixture.test' })), 'mcp_oauth_issuer_changed');
  assert.equal(errors.agentErrorText({ code: 'mcp_oauth_issuer_changed' }, t), integrationCopy.mcp_oauth_issuer_changed[0]);
  assert.ok(/not handed to the new authorization server/.test(tr('en', 'mcp_oauth_issuer_changed'))
    && /register a client at the new authorization server/.test(tr('en', 'mcp_oauth_issuer_changed')));
  // The document's refusal holds however its id came to be named, and says so.
  assert.ok(/registration endpoint handed that id back/.test(tr('en', 'oauthIssUnsupported')) && /entered for the connection/.test(tr('en', 'oauthIssUnsupported')));
  // Where the endpoints are matters for that shared document only (a client the person or the server registered is that server's own).
  assert.ok(/Client ID Metadata Document is shared by every authorization server/.test(tr('en', 'oauthEndpointOrigin')));
  // Adding a connection says when the document is used and that a client given is kept to its server.
  assert.ok(/says it returns iss/.test(tr('en', 'oauthHint')) && /does not hand the client to another one/.test(tr('en', 'oauthHint')));
  // The authorization server's answer too large to read while refreshing is not the MCP server's.
  assert.equal(model.mcpRefusalKey('mcp_response_too_large', { cause: 'token_refresh', max_bytes: 1048576 }), 'refreshFailed');
  assert.equal(model.mcpRefusalKey('mcp_response_too_large', { max_bytes: 1048576 }), 'mcp_response_too_large');
  // A recorded error (a tool call's) that carries its detail beside the code is worded by it as well; one without stays by code.
  assert.equal(errors.agentErrorText({ code: 'mcp_unreachable', message: 'fixture', detail: { cause: 'token_refresh' } }, t), integrationCopy.refreshFailed[0]);
  assert.equal(errors.agentErrorText({ code: 'mcp_auth_required', message: 'fixture', detail: { step_up: 'capped' } }, t), integrationCopy.stepUpCapped[0]);
  assert.equal(errors.agentErrorText({ code: 'mcp_unreachable', message: 'fixture' }, t), integrationCopy.mcp_unreachable[0]);
  // A workspace connection's result the Agent did not read (tools.WITHHELD) says whose policy kept it, and that the person still sees it.
  assert.equal(model.mcpErrorKey('mcp_result_withheld'), 'mcp_result_withheld');
  assert.equal(errors.agentErrorText({ code: 'mcp_result_withheld' }, t), integrationCopy.mcp_result_withheld[0]);
  assert.ok(/model processing policy/.test(tr('en', 'mcp_result_withheld')) && /still see it/.test(tr('en', 'mcp_result_withheld')));
});

for (const locale of LOCALES) test(`a connection's OAuth client names the authorization server it is kept to, in ${locale}`, () => {
  const at = (client: unknown) => render(locale, React.createElement(detail.DetailView, { detail: detailFixture({ auth_kind: 'oauth', credential_hint: 'oauth', oauth_client: client }),
    tab: 'overview', setTab() {}, update() {}, onDeleted() {} }), '/agent/integrations/mcpc_0123456789ab');
  const kept = at({ kind: 'preregistered', client_id: 'fixture-preregistered', issuer: 'https://as.fixture.test' });
  assert.ok(kept.includes(tr(locale, 'oauthClientAt', { issuer: 'https://as.fixture.test' })) && kept.includes('fixture-preregistered'));
  const notYet = at({ kind: 'preregistered', client_id: 'fixture-preregistered' });
  assert.ok(!notYet.includes('https://as.fixture.test') && notYet.includes(tr(locale, 'oauthPreregistered')));
  assert.ok(at({ kind: 'dynamic', client_id: 'dyn-fixture', issuer: 'https://as.fixture.test' }).includes(tr(locale, 'oauthClientAt', { issuer: 'https://as.fixture.test' })));
  // The document is every server's client (a step-up record of it names the last server, which it does not belong to).
  assert.ok(!at({ kind: 'cimd', client_id: 'https://workplace.fixture.test/.well-known/mcp-client/synwevo-agent.json', issuer: 'https://as.fixture.test' })
    .includes(tr(locale, 'oauthClientAt', { issuer: 'https://as.fixture.test' })));
});
