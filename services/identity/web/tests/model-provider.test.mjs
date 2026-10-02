/** Isolated contract and SSR fixtures only. No server, real key, account, model call or payment. */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { transformWithOxc } from 'vite';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const governance = new URL('../src/governance/', import.meta.url);
const source = name => readFile(new URL(name, governance), 'utf8');
const moduleURL = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
async function compile(name) {
  const file = new URL(name, governance);
  return (await transformWithOxc(await readFile(file, 'utf8'), fileURLToPath(file), { jsx: { runtime: 'automatic' } })).code;
}
const helperURL = moduleURL(await compile('model-provider.ts'));
const m = await import(helperURL);
const route = { requested_model: 'fixture-requested', confirmed_route: 'fixture-confirmed', confirmation_ref: 'fixture-model-evidence',
  cost_basis_ref: 'fixture-cost-basis', account_confirmation_ref: 'fixture-account-evidence', sale_price_id: '9007199254740993123',
  cost_price_id: 'fixture-cost-price', input_token_ceiling: 2000000, output_token_ceiling: 32768, valid_until: 1798761607 };
const spec = { enabled: true, route };
const initial = { environment: 'test', provider: 'deepseek', account_ref: 'platform-deepseek', revision: 0, spec: null,
  credential_revision: 0, credential_configured: false, actor_id: null, created_at: null, reason: null, operation_id: null };
const requestFixture = () => ({ expected_revision: 0, spec: structuredClone(spec), credential: { action: 'replace', api_key: 'fixture-key-not-a-real-credential' } });
const NOW = 1780000000000;
const rawPreview = (request = requestFixture(), before = initial) => ({ before: structuredClone(before), after: structuredClone(request.spec),
  credential: { action: request.credential.action, digest: request.credential.action === 'replace' ? 'fixture-digest-never-render' : null },
  credential_revision: before.credential_revision + (request.credential.action === 'keep' ? 0 : 1),
  credential_configured: request.credential.action === 'keep' ? before.credential_configured : request.credential.action === 'replace',
  preview_token: 'fixture-signed-preview', expires_at: NOW / 1000 + 60.125, risk: 'high', environment: before.environment });
function attemptFixture() {
  const request = requestFixture();
  const plan = m.readProviderPreview(rawPreview(request), initial, request, NOW);
  return m.createProviderAttempt(plan, request, 'fixture publication reason', 'fixture-idempotency-key');
}
function publishedFixture(attempt = attemptFixture()) {
  return { operation_id: 'fixture-operation', status: 'succeeded', state: { ...initial, spec: structuredClone(attempt.body.spec),
    revision: attempt.body.expected_revision + 1, credential_revision: attempt.credential_revision,
    credential_configured: attempt.credential_configured, actor_id: 'fixture-admin', reason: attempt.body.reason,
    operation_id: 'fixture-operation', created_at: NOW / 1000 } };
}

test('unconfigured input invents no approval, model, account evidence, price or enabled route', () => {
  const empty = m.emptyProviderForm();
  assert.equal(empty.enabled, false);
  assert.equal(empty.route_mode, 'none');
  assert.equal(empty.credential_action, 'keep');
  for (const key of [...m.routeTextKeys, 'input_token_ceiling', 'output_token_ceiling', 'valid_until']) assert.equal(empty[key], '');
  assert.deepEqual(m.providerForm(null), empty);
  assert.deepEqual(m.validateProviderForm(empty, '').spec, { enabled: false, route: null });
  assert.ok(m.validProviderState(initial));
  assert.equal(m.validateProviderForm({ ...empty, enabled: true }, '').spec, null);
});

test('token limits are exact integers at inclusive boundaries, never boolean or decimal coercions', () => {
  for (const [min, max] of [[1, 2000000], [1, 32768]]) {
    assert.equal(m.exactInteger(String(min), min, max), min);
    assert.equal(m.exactInteger(String(max), min, max), max);
    for (const input of [true, false, 1, 1.5, null, undefined, {}, '', '0', '-1', '+1', '01', '1.0', '1e3', ' 1', '1 ', '１', '0x10', 'NaN', 'Infinity', String(max + 1), '9'.repeat(100)]) {
      assert.equal(m.exactInteger(input, min, max), null, String(input));
    }
  }
  for (const key of ['input_token_ceiling', 'output_token_ceiling', 'valid_until']) {
    for (const value of [true, '1', 1.5, NaN, Infinity, -1]) assert.equal(m.validProviderRoute({ ...route, [key]: value }), false);
  }
});

test('UTC form dates preserve seconds, leap days and instants across local DST transitions', () => {
  const previous = process.env.TZ;
  try {
    for (const zone of ['UTC', 'Asia/Hong_Kong', 'America/New_York']) {
      process.env.TZ = zone;
      for (const seconds of [0, 1, 1709164807, 1730615407, route.valid_until, 253402300799]) {
        assert.equal(m.providerTimestamp(m.providerDateInput(seconds)), seconds, `${zone}: ${seconds}`);
      }
      assert.equal(m.providerTimestamp('2024-03-10T02:30:07'), Date.parse('2024-03-10T02:30:07Z') / 1000);
      assert.equal(m.providerTimestamp('2024-02-29T00:00'), 1709164800);
    }
  } finally { if (previous === undefined) delete process.env.TZ; else process.env.TZ = previous; }
  for (const value of [true, 1, '2023-02-29T00:00:00', '2024-02-30T00:00:00', '2024-13-01T00:00:00', '2024-01-01T24:00:00',
    '2024-01-01T00:00:00.5', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00+08:00', '1969-12-31T23:59:59', '10000-01-01T00:00:00', '']) {
    assert.equal(m.providerTimestamp(value), null, String(value));
  }
});

test('route form round-trips string price IDs and preserves all independent evidence fields', () => {
  const form = m.providerForm(spec);
  assert.deepEqual(m.validateProviderForm(form, '').spec, spec);
  for (const key of m.routeTextKeys) {
    for (const value of ['', ' leading', 'trailing ', 'control\n', false, 123]) {
      const validated = m.validateProviderForm({ ...form, [key]: value }, '');
      assert.equal(validated.spec, null);
      assert.ok(validated.errors[key]);
    }
  }
  for (const [key, value] of [['input_token_ceiling', '2000001'], ['output_token_ceiling', '32769'], ['valid_until', '2023-02-29T00:00:00']]) {
    assert.ok(m.validateProviderForm({ ...form, [key]: value }, '').errors[key]);
  }
  assert.equal(m.validateProviderForm({ ...form, enabled: false }, '').spec.enabled, false);
  // Expired evidence may be retained when stopping a route; the server governs activation.
  assert.ok(m.validateProviderForm({ ...form, enabled: false, valid_until: '2000-01-01T00:00:01' }, '').spec);
});

test('credential actions never include an old key, and replacing requires explicit secret input', () => {
  for (const action of ['keep', 'revoke']) {
    assert.deepEqual(m.validateProviderForm({ ...m.emptyProviderForm(), credential_action: action }, 'fixture-discarded-key').credential, { action });
  }
  const form = { ...m.emptyProviderForm(), credential_action: 'replace' };
  for (const value of ['', ' key', 'key ', 'key\nsecret', true, null]) {
    assert.ok(m.validateProviderForm(form, value).errors.api_key);
  }
  const checked = m.validateProviderForm(form, 'fixture-key-not-a-real-credential');
  assert.equal(checked.credential.api_key, 'fixture-key-not-a-real-credential');
  assert.equal(m.validateProviderForm({ ...form, credential_action: 'unknown' }, '').spec, null);
});

test('copying history copies only the route/enablement, preserves draft credential choice and never restores historical keys', () => {
  const historical = { ...spec, api_key: 'fixture-never-copy', credential_revision: 91 };
  for (const action of ['keep', 'replace', 'revoke']) {
    const copied = m.copyProviderRoute({ ...m.emptyProviderForm(), credential_action: action }, historical);
    assert.equal(copied.credential_action, action);
    assert.equal(copied.requested_model, route.requested_model);
    assert.equal(JSON.stringify(copied).includes('fixture-never-copy'), false);
    assert.equal('credential_revision' in copied, false);
  }
  assert.equal(m.providerForm(spec).credential_action, 'keep');
});

test('state guards reject unknown environments, accounts, coerced revisions, dates and malformed specs', () => {
  for (const change of [{ environment: 'unknown' }, { provider: 'other' }, { account_ref: 'another-cost-account' }, { revision: true },
    { revision: 0.1 }, { revision: -1 }, { revision: '0' }, { credential_revision: true }, { credential_configured: 1 },
    { created_at: 1.5 }, { created_at: true }, { created_at: 253402300800 }, { actor_id: 7 }, { reason: {} }, { operation_id: undefined },
    { spec: { enabled: 'false', route: null } }, { spec: { enabled: true, route: null } }]) assert.equal(m.validProviderState({ ...initial, ...change }), false);
  for (const value of [null, true, [], {}, { ...initial, environment: undefined }]) assert.equal(m.validProviderState(value), false);
  assert.equal(m.sameProviderVersion(initial, { ...initial, credential_revision: 1 }), false);
  assert.equal(m.sameProviderVersion(initial, { ...initial, environment: 'live' }), false);
});

test('preview binds every route field, environment, configuration and credential version; fractional expiry is accepted', () => {
  const request = requestFixture();
  const raw = rawPreview(request);
  const plan = m.readProviderPreview(raw, initial, request, NOW);
  assert.ok(plan);
  assert.equal(plan.expires_at, NOW / 1000 + 60.125);
  assert.deepEqual(plan.credential, { action: 'replace' });
  assert.equal(JSON.stringify(plan).includes('fixture-digest'), false);
  assert.equal(JSON.stringify(plan).includes(request.credential.api_key), false);
  for (const change of [{ environment: 'live' }, { risk: 'low' }, { expires_at: true }, { expires_at: String(raw.expires_at) },
    { expires_at: Infinity }, { expires_at: NOW / 1000 }, { preview_token: '' }, { credential_revision: true },
    { credential_revision: 2 }, { credential_configured: false }, { credential: { action: 'keep', digest: null } },
    { before: { ...initial, revision: 1 } }, { before: { ...initial, credential_revision: 1 } },
    { before: { ...initial, credential_configured: true } }, { before: { ...initial, spec: { enabled: false, route: null } } }]) {
    assert.equal(m.readProviderPreview({ ...raw, ...change }, initial, request, NOW), null);
  }
  for (const key of Object.keys(route)) {
    const changed = structuredClone(raw);
    changed.after.route[key] = typeof route[key] === 'number' ? route[key] - 1 : `${route[key]}-other`;
    assert.equal(m.readProviderPreview(changed, initial, request, NOW), null, key);
  }
  assert.equal(m.readProviderPreview(raw, initial, { ...request, expected_revision: 1 }, NOW), null);
});

test('keep/revoke previews retain or advance the credential revision without resetting the fixed account', () => {
  const before = { ...initial, revision: 7, credential_revision: 3, credential_configured: true, spec };
  for (const action of ['keep', 'revoke']) {
    const request = { expected_revision: 7, spec: { ...spec, enabled: false }, credential: { action } };
    const plan = m.readProviderPreview(rawPreview(request, before), before, request, NOW);
    assert.equal(plan.credential_revision, action === 'keep' ? 3 : 4);
    assert.equal(plan.credential_configured, action === 'keep');
    assert.equal(plan.before.account_ref, 'platform-deepseek');
  }
});

test('history pagination rejects duplicate/reversed/cross-environment revisions and nonadvancing cursors', () => {
  const item = revision => ({ ...initial, revision, spec });
  const page = { items: [item(7), item(6)], next_before_revision: 6 };
  assert.ok(m.validProviderHistory(page, null, 'test'));
  assert.ok(m.validProviderHistory(page, 8, 'test'));
  assert.ok(m.validProviderHistory({ items: [], next_before_revision: null }, null, null));
  for (const invalid of [{ ...page, next_before_revision: true }, { ...page, next_before_revision: 8 },
    { ...page, next_before_revision: 5 }, { items: [item(6), item(7)], next_before_revision: null },
    { items: [item(6), item(6)], next_before_revision: null }, { items: [initial], next_before_revision: null },
    { items: [], next_before_revision: 1 }, { items: Array.from({ length: 21 }, (_, i) => item(30 - i)), next_before_revision: null }]) {
    assert.equal(m.validProviderHistory(invalid, null, 'test'), false);
  }
  assert.equal(m.validProviderHistory(page, 7, 'test'), false);
  assert.equal(m.validProviderHistory(page, null, 'live'), false);
});

test('an original publication owns an exact body, reason, key and ID independently of subsequent editor mutations', () => {
  const request = requestFixture();
  const plan = m.readProviderPreview(rawPreview(request), initial, request, NOW);
  const attempt = m.createProviderAttempt(plan, request, '  fixture publication reason  ', 'fixture-original-id');
  const wireBody = JSON.stringify(attempt.body);
  request.spec.route.sale_price_id = 'fixture-new-price';
  request.credential.api_key = 'fixture-other-key';
  request.expected_revision = 99;
  plan.after.route.requested_model = 'fixture-changed-plan';
  assert.equal(JSON.stringify(attempt.body), wireBody);
  assert.equal(attempt.body.reason, 'fixture publication reason');
  assert.equal(attempt.body.idempotency_key, 'fixture-original-id');
  assert.equal(attempt.body.credential.api_key, 'fixture-key-not-a-real-credential');
  assert.equal('digest' in attempt.body.credential, false);
});

test('uncertain results retain their replay even after expiry, conflicts, denial or another reauth requirement', () => {
  for (const failure of [{ application: false }, { application: false, status: 403 }, { application: true, status: 0 },
    { application: true, status: 408 }, { application: true, status: 500 }, { application: true, status: 503 }]) {
    assert.equal(m.providerFailureMode(false, failure), 'uncertain');
  }
  assert.equal(m.providerFailureMode(false, { application: true, status: 403, code: 'reauth_required' }), 'reauth');
  const attempt = attemptFixture();
  const wireBody = JSON.stringify(attempt.body);
  for (const code of ['reauth_required', 'preview_expired', 'model_revision_conflict', 'platform_permission_denied']) {
    assert.equal(m.providerFailureMode(true, { application: true, status: 409, code }), 'uncertain');
    assert.equal(JSON.stringify(attempt.body), wireBody);
  }
  assert.equal(m.providerFailureMode(false, { application: true, status: 409 }), 'refused');
  assert.equal(m.providerFailureMode(false, { application: true, status: 400 }), 'refused');
});

test('publication response must confirm the exact new version, credential result and operation ID', () => {
  const attempt = attemptFixture();
  const result = publishedFixture(attempt);
  assert.ok(m.validProviderPublication(result, attempt));
  for (const change of [{ environment: 'live' }, { revision: 3 }, { credential_revision: 0 }, { credential_configured: false },
    { operation_id: 'fixture-other-operation' }, { spec: { ...spec, enabled: false } }, { account_ref: 'fixture-other-account' }]) {
    assert.equal(m.validProviderPublication({ ...result, state: { ...result.state, ...change } }, attempt), false);
  }
  assert.equal(m.validProviderPublication({ ...result, status: 'pending' }, attempt), false);
  assert.equal(m.validProviderPublication({ ...result, operation_id: '' }, attempt), false);
});

test('credential cleanup clears owned request references after completion/unmount', () => {
  const request = requestFixture();
  const attempt = attemptFixture();
  const reference = attempt.body.credential;
  m.clearProviderCredential(request);
  m.clearProviderCredential(attempt.body);
  assert.equal(request.credential.api_key, '');
  assert.equal(reference.api_key, '');
  m.clearProviderCredential(null);
  const keep = { expected_revision: 1, spec, credential: { action: 'keep' } };
  m.clearProviderCredential(keep);
  assert.deepEqual(keep.credential, { action: 'keep' });
});

// SSR adapter fixtures replace HTTP and shared-context hooks only. React rendering remains real.
// transformWithOxc compiles without opening a Vite server or shared optimizer cache.
const supportURL = moduleURL(`
export const fixture = { messages: {}, reads: [], locale: 'zh-Hans', current: null, history: null, loading: false };
export const registerMessages = messages => Object.assign(fixture.messages, messages);
export function useI18n() { return { locale: fixture.locale, t: (key, args = {}) =>
  (fixture.locale === 'zh-Hans' ? key : fixture.messages[key]?.[fixture.locale] || key).replace(/\\{([^}]+)\\}/g, (_, key) => String(args[key] ?? key)) }; }
export function useAdminData(path) { fixture.reads.push(path); return { data: path.includes('/history?') ? fixture.history : fixture.current, err: null, loading: fixture.loading, reload() {} }; }
export const useUnsavedChanges = () => {};
export class ApiError extends Error {}
export const post = () => { throw new Error('HTTP prohibited in SSR fixture'); };
export const errText = () => 'fixture error';
export const ActionDialog = () => { throw new Error('Unexpected dialog in SSR fixture'); };
`);
const support = await import(supportURL);
const messagesURL = moduleURL((await compile('model-provider.messages.ts')).replaceAll("'@synwevo/ui'", JSON.stringify(supportURL)).replaceAll('"@synwevo/ui"', JSON.stringify(supportURL)));
const imports = { react: import.meta.resolve('react'), 'react/jsx-runtime': import.meta.resolve('react/jsx-runtime'),
  '@synwevo/auth-client': supportURL, '@synwevo/ui': supportURL, './ActionDialog': supportURL, './data': supportURL,
  './labels': supportURL, '../lib/hooks': supportURL, './model-provider': helperURL, './model-provider.messages': messagesURL };
const compiled = (await compile('ModelProviderTab.tsx')).replace(/(from\s*|import\s*)(['"])([^'"]+)\2/g,
  (match, prefix, _quote, name) => imports[name] ? `${prefix}${JSON.stringify(imports[name])}` : match);
const { ModelProviderTab } = await import(moduleURL(compiled));

function render(props = {}) {
  return renderToStaticMarkup(React.createElement(ModelProviderTab, { canRead: true, canManage: false, onDenied() {}, ...props }));
}
test('denied readers mount no loader even with manage=true, and read-only rendering shows no write controls', () => {
  support.fixture.reads = [];
  const denied = render({ canRead: false, canManage: true });
  assert.deepEqual(support.fixture.reads, []);
  assert.match(denied, /没有查看/);
  support.fixture.current = initial;
  support.fixture.history = { items: [], next_before_revision: null };
  const html = render();
  assert.deepEqual(support.fixture.reads, ['/api/admin/billing/model-provider', '/api/admin/billing/model-provider/history?limit=20']);
  assert.match(html, /尚未配置模型提供方/);
  assert.match(html, /不能修改或发布/);
  assert.doesNotMatch(html, /<input|<form|复制路线到编辑器/);
});

test('current and immutable historical routes show concrete fields and credential status without server key/digest extras', () => {
  support.fixture.current = { ...publishedFixture().state, api_key: 'fixture-server-secret', digest: 'fixture-server-digest' };
  support.fixture.history = { items: [support.fixture.current], next_before_revision: null };
  const html = render();
  for (const value of m.routeTextKeys.map(key => route[key])) assert.ok(html.includes(value), value);
  assert.match(html, /凭据版本 1/);
  assert.match(html, /<details/);
  assert.doesNotMatch(html, /fixture-server-secret|fixture-server-digest|fixture-key-not-a-real-credential/);
  assert.match(html, /1798761607/);
});

test('loading, null environment and invalid server state are explicit in the rendered page', () => {
  support.fixture.loading = true;
  assert.match(render(), /正在读取提供方配置/);
  assert.match(render(), /aria-busy="true"/);
  support.fixture.loading = false;
  support.fixture.current = { ...initial, environment: null };
  support.fixture.history = { items: [], next_before_revision: null };
  assert.match(render(), /提供方编辑与发布已禁用/);
  support.fixture.current = { ...initial, revision: true };
  assert.match(render(), /无法确认服务器的提供方配置/);
});

test('all page and validation copy has synchronized English and Traditional Chinese with matching placeholders', async () => {
  const strings = [await source('ModelProviderTab.tsx'), await source('model-provider.ts')]
    .flatMap(code => [...code.matchAll(/'([^'\n]*[\u3400-\u9fff][^'\n]*)'/g)].map(match => match[1]));
  const tokens = value => [...value.matchAll(/\{([^}]+)\}/g)].map(match => match[1]).sort();
  for (const key of new Set(strings)) {
    const translations = support.fixture.messages[key];
    assert.ok(translations, key);
    for (const locale of ['en', 'zh-Hant']) {
      assert.ok(translations[locale]?.trim(), `${locale}: ${key}`);
      assert.deepEqual(tokens(translations[locale]), tokens(key), `${locale}: ${key}`);
    }
    assert.doesNotMatch(translations.en, /[\u3400-\u9fff]/, key);
  }
  support.fixture.current = initial;
  support.fixture.history = { items: [], next_before_revision: null };
  for (const [locale, title] of [['zh-Hans', '模型提供方'], ['zh-Hant', '模型提供方'], ['en', 'Model provider']]) {
    support.fixture.locale = locale;
    const html = render();
    assert.ok(html.includes(title));
    if (locale === 'en') assert.doesNotMatch(html, /[\u3400-\u9fff]/);
  }
});

test('editor uses protected input, shared admin reauth, synchronous flight guards and no browser persistence', async () => {
  const component = await source('ModelProviderTab.tsx');
  const helper = await source('model-provider.ts');
  assert.match(component, /type="password" autoComplete="new-password"/);
  assert.match(component, /reauthScope="admin"/);
  assert.match(component, /if \(!alive\.current \|\| flight\.current/);
  assert.match(component, /return \(\) => \{ alive\.current = false; clearSecrets\(\); \}/);
  assert.match(component, /post<unknown>\(`\$\{API\}\/publish`, original\.body\)/);
  assert.match(component, /aria-invalid=/);
  assert.match(component, /aria-describedby=/);
  assert.doesNotMatch(component + helper, /localStorage|sessionStorage|console\.|fetch\(|window\.location|URLSearchParams/);
  assert.doesNotMatch(component, /#[0-9a-f]{3,8}\b|rgba?\(|hsla?\(/i);
});
