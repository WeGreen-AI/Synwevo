/** Run from the repository root:
 * node --experimental-vm-modules --test packages/auth-client/web/tests/session-generation.test.mjs
 *
 * Actual api.ts/session.tsx, compiled by the repository's locked Vite/Rolldown toolchain.
 * Fetch completion, XHR, DOM and React hooks are explicit scheduling doubles. These tests
 * exercise async ordering and the reload boundary; they do not claim browser/React E2E.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';

const compiled = {};
for (const file of ['api.ts', 'session.tsx']) {
  const source = readFileSync(new URL('../src/' + file, import.meta.url), 'utf8');
  const result = transformSync(file, source, { jsx: { runtime: 'automatic' }, target: 'es2022' });
  assert.equal(result.errors.length, 0, file);
  compiled[file] = result.code;
}
const turn = () => new Promise(resolve => setImmediate(resolve));
const authState = { mode: 'session', password_login: true };
const me = (workspace = 'A', user = 'fixture-user', session = 'fixture-session') => ({
  id: user, workspace_id: workspace, status: 'active', locale: 'en', workspaces: [],
  session: { id: session, amr: 'password' },
});
const outcome = promise => promise.then(value => ({ value }), error => ({ error }));
const expired = { error: 'unauthorized', message: 'Fixture expired session' };
const invalidWorkspace = { error: 'workspace_context_invalid', message: 'Fixture removed membership' };

async function setup({ search = '', blockedStorage = false } = {}) {
  const values = [], effects = [], pendingEffects = [], requests = [], navigation = [], xhrs = [], events = [];
  const storage = new Map();
  let index = 0, provider;
  const react = {
    createContext: initial => ({ Provider: 'provider', initial }),
    useState: initial => {
      const i = index++;
      if (!(i in values)) values[i] = initial;
      return [values[i], value => { values[i] = typeof value === 'function' ? value(values[i]) : value; }];
    },
    useRef: initial => { const i = index++; return values[i] ||= { current: initial }; },
    useCallback: (fn, deps) => {
      const i = index++;
      if (!values[i] || deps.some((d, j) => d !== values[i].deps[j])) values[i] = { deps, fn };
      return values[i].fn;
    },
    useEffect: (fn, deps) => {
      const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) {
        pendingEffects.push(() => { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; });
      }
    },
    useContext: () => provider.props.value,
  };
  const window = new EventTarget();
  window.location = { search, href: 'http://fixture.test/' + search, origin: 'http://fixture.test', pathname: '/',
    assign: path => navigation.push(['assign', path]), replace: path => navigation.push(['replace', path]),
    reload: () => navigation.push(['reload']) };
  window.history = { state: null, replaceState: (...args) => navigation.push(['history', args[2]]) };
  window.addEventListener('synwevo:session-expired', event => events.push(event.detail));
  const document = new EventTarget();
  document.visibilityState = 'visible'; document.cookie = 'dw_csrf=fixture-csrf';
  const sessionStorage = {
    getItem: key => { if (blockedStorage) throw Error('Fixture denied storage'); return storage.get(key) || null; },
    setItem: (key, value) => { if (blockedStorage) throw Error('Fixture denied storage'); storage.set(key, value); },
    removeItem: key => { if (blockedStorage) throw Error('Fixture denied storage'); storage.delete(key); },
  };
  class XHR {
    constructor() { this.upload = {}; this.headers = {}; xhrs.push(this); }
    open(method, path) { this.method = method; this.path = path; }
    setRequestHeader(key, value) { this.headers[key] = value; }
    send(body) { this.body = body; }
    getResponseHeader(key) { return key.toLowerCase() === 'x-request-id' ? 'fixture-request' : null; }
    finish(status, body) { this.status = status; this.responseText = JSON.stringify(body); this.onload(); }
  }
  const ui = { getLocale: () => 'en', normalizeLocale: value => value, setLocale: () => {},
    subscribeLocale: () => () => {}, useI18n: () => ({ t: value => value }),
    PageState: ({ children, role }) => ({ type: 'PageState', props: { children, role } }) };
  const context = vm.createContext({ window, document, sessionStorage, localStorage: { setItem: () => {} },
    URL, URLSearchParams, DOMException, CustomEvent, AbortController, FormData, Error, XMLHttpRequest: XHR,
    setTimeout, clearTimeout, fetch: (path, opts) => new Promise((resolve, reject) => requests.push({ path, opts, resolve, reject })) });
  const synthetic = (name, members) => new vm.SyntheticModule(Object.keys(members), function () {
    for (const [key, value] of Object.entries(members)) this.setExport(key, value);
  }, { context, identifier: name });
  const modules = {
    react: synthetic('react', react),
    'react/jsx-runtime': synthetic('jsx', { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: 'fragment' }),
    '@synwevo/ui': synthetic('ui', ui),
    '@synwevo/auth-client/api-messages': synthetic('messages', { apiFailureText: error => error.originalMessage }),
    './session.messages': synthetic('session.messages', {}),
  };
  const apiModule = new vm.SourceTextModule(compiled['api.ts'], { context, identifier: 'api.ts' });
  modules['./api'] = apiModule;
  const sessionModule = new vm.SourceTextModule(compiled['session.tsx'], { context, identifier: 'session.tsx' });
  const link = name => { assert(modules[name], name); return modules[name]; };
  await apiModule.link(link); await apiModule.evaluate();
  await sessionModule.link(link); await sessionModule.evaluate();
  const api = apiModule.namespace, session = sessionModule.namespace;
  const children = { fixture: 'business-subtree-with-cached-data' };
  const render = () => {
    index = 0; provider = session.SessionProvider({ children });
    while (pendingEffects.length) pendingEffects.shift()();
    return provider.props.value;
  };
  const next = path => {
    const i = requests.findIndex(request => request.path === path);
    assert(i >= 0, 'pending request: ' + path);
    return requests.splice(i, 1)[0];
  };
  const answer = async (request, body, status = 200) => {
    request.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'fixture-request' } }));
    await turn();
  };
  const bootstrap = async (user = me()) => {
    render(); await answer(next('/api/auth/state'), authState); await answer(next('/api/auth/me'), user); return render();
  };
  const beginRefresh = async () => { const promise = render().refresh(); await answer(next('/api/auth/state'), authState); return { promise, request: next('/api/auth/me') }; };
  return { api, session, render, next, answer, bootstrap, beginRefresh, requests, navigation, xhrs, events, storage, window,
    children, currentChildren: () => provider.props.children };
}

test('multipart preserves bytes, explicit target, CSRF and retry identity without a JSON content type', async () => {
  const h = await setup(); await h.bootstrap();
  const form = new FormData(); form.append('file', new Blob(['code,amount\n00127,123.40\n']), 'fixture.csv');
  const pending = h.api.api('/api/suite/extract/files?workspace_id=B', {
    method: 'POST', body: form, workspaceId: 'B',
    headers: { 'Idempotency-Key': 'fixture-upload-key', 'Content-Type': 'application/json' },
  });
  const request = h.next('/api/suite/extract/files?workspace_id=B');
  assert.equal(request.opts.body, form);
  assert.equal(await request.opts.body.get('file').text(), 'code,amount\n00127,123.40\n');
  assert.equal(request.opts.headers['Content-Type'], undefined);
  assert.equal(request.opts.headers['X-Synwevo-Workspace'], 'B');
  assert.equal(request.opts.headers['X-Synwevo-CSRF'], 'fixture-csrf');
  assert.equal(request.opts.headers['Idempotency-Key'], 'fixture-upload-key');
  await h.answer(request, { id: 'fixture-file' });
  assert.deepEqual(await pending, { id: 'fixture-file' });
  assert.equal(h.api.workspaceContext(), 'A');
});

test('G-S01: an obsolete workspace refusal cannot clear the newer tab context or probe a fallback', async () => {
  const h = await setup(); await h.bootstrap();
  const older = await h.beginRefresh();
  const newer = await h.beginRefresh();
  await h.answer(newer.request, me()); await newer.promise;
  await h.answer(older.request, invalidWorkspace, 403); await older.promise;
  assert.equal(h.api.workspaceContext(), 'A');
  assert.equal(h.render().me.workspace_id, 'A');
  assert.equal(h.requests.length, 0);
});

test('fallback omits only the probe header and leaves other pending requests scoped', async () => {
  const h = await setup(); await h.bootstrap();
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, invalidWorkspace, 403);
  const fallback = h.next('/api/auth/me');
  assert.equal(fallback.opts.headers['X-Synwevo-Workspace'], undefined);
  assert.equal(h.api.workspaceContext(), 'A');
  assert.equal(h.api.authHeaders('POST', true)['X-Synwevo-Workspace'], 'A');
  await h.answer(fallback, me('B')); await refresh.promise;
  h.render();
  assert.equal(h.api.workspaceContext(), 'B');
  assert.deepEqual(h.navigation, [['reload']]);
  assert.notEqual(h.currentChildren(), h.children);
});

test('a failed fallback leaves the tab selection intact', async () => {
  const h = await setup(); await h.bootstrap();
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, invalidWorkspace, 403);
  await h.answer(h.next('/api/auth/me'), { error: 'server_error' }, 503); await refresh.promise;
  assert.equal(h.api.workspaceContext(), 'A');
  assert.equal(h.render().error.status, 503);
  assert.deepEqual(h.navigation, []);
});

test('an explicit launch workspace refusal is not replaced with a different default', async () => {
  const h = await setup({ search: '?synwevo_workspace=requested' });
  h.render(); await h.answer(h.next('/api/auth/state'), authState);
  const request = h.next('/api/auth/me');
  assert.equal(request.opts.headers['X-Synwevo-Workspace'], 'requested');
  await h.answer(request, invalidWorkspace, 403);
  assert.equal(h.render().error.code, 'workspace_context_invalid');
  assert.equal(h.requests.length, 0);
});

for (const [label, current] of [['workspace', me('B')], ['account', me('A', 'another-user')], ['session', me('A', 'fixture-user', 'another-session')]]) {
  test(`G-S02: changed ${label} hides old business children and crosses a full reload boundary`, async () => {
    const h = await setup(); await h.bootstrap();
    const previous = outcome(h.api.get('/api/projects'));
    const request = h.next('/api/projects');
    const refresh = await h.beginRefresh();
    await h.answer(refresh.request, current); await refresh.promise;
    assert.equal(h.render().me, undefined);
    assert.notEqual(h.currentChildren(), h.children);
    assert.deepEqual(h.navigation, [['reload']]);
    await h.answer(request, { fixtureOldRows: ['old-row'] });
    assert.equal((await previous).error.name, 'AbortError');
    await h.render().refresh(); assert.equal(h.requests.length, 0);
  });
}

test('same account/session/workspace refresh keeps children and in-flight data usable', async () => {
  const h = await setup(); await h.bootstrap();
  const generation = h.api.getSessionGeneration();
  const pending = h.api.get('/api/projects'); const request = h.next('/api/projects');
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, me()); await refresh.promise;
  assert.equal(h.api.getSessionGeneration(), generation);
  h.render(); assert.equal(h.currentChildren(), h.children); assert.deepEqual(h.navigation, []);
  await h.answer(request, { fixtureRows: ['current-row'] });
  assert.deepEqual(await pending, { fixtureRows: ['current-row'] });
});

test('email verification and permission updates on the same binding preserve the current flow', async () => {
  const h = await setup(); await h.bootstrap({ ...me(), email_verified: false, permissions: [] });
  const generation = h.api.getSessionGeneration();
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, { ...me(), email_verified: true, permissions: ['api_platform.manage'] });
  await refresh.promise;
  const session = h.render();
  assert.equal(session.me.email_verified, true);
  assert.deepEqual(session.me.permissions, ['api_platform.manage']);
  assert.equal(h.currentChildren(), h.children);
  assert.equal(h.api.getSessionGeneration(), generation);
  assert.deepEqual(h.navigation, []);
});

test('first sign-in refresh can finish without reloading the login/bootstrap document', async () => {
  const h = await setup(); h.render();
  await h.answer(h.next('/api/auth/state'), authState);
  await h.answer(h.next('/api/auth/me'), expired, 401);
  assert.equal(h.render().me, null);
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, me()); await refresh.promise;
  assert.equal(h.render().me.id, 'fixture-user');
  assert.deepEqual(h.navigation, []);
});

test('activation revoking a pending session preserves the public verification success view', async () => {
  const h = await setup(); await h.bootstrap({ ...me(), status: 'pending', email_verified: false });
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, expired, 401); await refresh.promise;
  assert.equal(h.render().me, null);
  assert.equal(h.currentChildren(), h.children);
  assert.deepEqual(h.navigation, []);
});

for (const status of [200, 401]) {
  test(`explicit workspace handoff is not interrupted by an earlier refresh (${status})`, async () => {
    const h = await setup(); await h.bootstrap();
    const refresh = await h.beginRefresh();
    const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
    const destination = 'http://portal.fixture.test/auth/start?workspace_id=B&lang=en&next=%2Fworkspace';
    const entering = (async () => {
      await h.api.post('/api/auth/workspace', { workspace_id: 'B' });
      h.api.setWorkspaceContext('B');
      // Full-navigation actions do not await refresh(): their destination revalidates /me.
      h.window.location.assign(destination);
    })();
    await h.answer(h.next('/api/auth/workspace'), { ok: true }); await entering;
    await h.answer(refresh.request, status === 200 ? me('B') : expired, status); await refresh.promise;
    await h.answer(request, ['old-workspace-rows']);
    assert.equal((await pending).error.name, 'AbortError');
    assert.equal(h.api.workspaceContext(), 'B');
    assert.equal(h.events.length, 0);
    assert.deepEqual(h.navigation, [['assign', destination]]);
  });
}

test('G-S03: an old 401 after new context validation cannot erase that context', async () => {
  const h = await setup(); await h.bootstrap();
  const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, me('B', 'another-user')); await refresh.promise;
  await h.answer(request, expired, 401);
  assert.equal((await pending).error.name, 'AbortError');
  assert.equal(h.api.workspaceContext(), 'B');
  assert.equal(h.events.length, 0);
  assert.notEqual(h.render().me, null);
});

test('obsolete session probes handle their own 401 only after their ordering check', async () => {
  const h = await setup(); await h.bootstrap();
  const older = await h.beginRefresh(); const newer = await h.beginRefresh();
  await h.answer(older.request, expired, 401); await older.promise;
  assert.equal(h.events.length, 0); assert.equal(h.render().me.id, 'fixture-user');
  await h.answer(newer.request, me()); await newer.promise;
  assert.equal(h.render().me.id, 'fixture-user');
});

test('current confirmed revocation clears the session and invalidates other outstanding data', async () => {
  const h = await setup(); await h.bootstrap();
  const data = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  const denied = outcome(h.api.get('/api/templates')); const refusal = h.next('/api/templates');
  await h.answer(refusal, expired, 401);
  assert.equal((await denied).error.status, 401);
  assert.equal(h.render().me, null); assert.equal(h.api.workspaceContext(), null);
  await h.answer(request, ['old-rows']); assert.equal((await data).error.name, 'AbortError');
});

test('a rapid re-login still reloads even if no intermediate signed-out render occurred', async () => {
  const h = await setup(); await h.bootstrap();
  const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  await h.answer(request, expired, 401); await pending;
  // Deliberately do not render the null state before validating the new identity.
  const refresh = await h.beginRefresh();
  await h.answer(refresh.request, me('B', 'another-user')); await refresh.promise;
  h.render(); assert.notEqual(h.currentChildren(), h.children);
  assert.deepEqual(h.navigation, [['reload']]);
});

for (const [status, body] of [[401, { error: 'bad_credentials' }], [403, { error: 'not_allowed' }]]) {
  test(`ordinary ${body.error} does not expire an existing session`, async () => {
    const h = await setup(); await h.bootstrap();
    const pending = outcome(h.api.post('/api/auth/reauth', {}));
    await h.answer(h.next('/api/auth/reauth'), body, status);
    assert.equal((await pending).error.status, status);
    assert.equal(h.render().me.id, 'fixture-user'); assert.equal(h.events.length, 0);
  });
}

for (const status of [200, 401]) {
  test(`generation is checked after delayed JSON decoding (${status})`, async () => {
    const h = await setup(); h.api.setWorkspaceContext('A');
    const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
    let decode;
    request.resolve({ ok: status === 200, status, headers: new Headers({ 'Content-Type': 'application/json', 'X-Request-ID': 'fixture' }),
      json: () => new Promise(resolve => { decode = resolve; }) });
    await turn(); h.api.setWorkspaceContext('B'); decode(status === 200 ? ['old-data'] : expired);
    assert.equal((await pending).error.name, 'AbortError'); assert.equal(h.events.length, 0);
  });
}

test('obsolete network failures become cancellation, not a current network error', async () => {
  const h = await setup();
  const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  h.api.advanceSessionGeneration(); request.reject(new TypeError('Fixture network failure'));
  assert.equal((await pending).error.name, 'AbortError');
});

test('a decoding failure from an obsolete success is also cancellation', async () => {
  const h = await setup();
  const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  let fail;
  request.resolve({ ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }),
    json: () => new Promise((resolve, reject) => { fail = reject; }) });
  await turn(); h.api.advanceSessionGeneration(); fail(new SyntaxError('Fixture truncated JSON'));
  assert.equal((await pending).error.name, 'AbortError');
});

for (const authenticated of [false, true]) {
  test(`initial session discovery preserves public reset/invitation previews (${authenticated})`, async () => {
    const h = await setup(); h.render();
    // Public recovery pages mount before SessionProvider finishes its initial /me probe.
    const preview = h.api.post('/api/auth/reset/preview', { token: 'fixture-only-token' });
    const request = h.next('/api/auth/reset/preview');
    await h.answer(h.next('/api/auth/state'), authState);
    await h.answer(h.next('/api/auth/me'), authenticated ? me() : expired, authenticated ? 200 : 401);
    h.render();
    await h.answer(request, { email: 'fixture@example.test' });
    assert.deepEqual(await preview, { email: 'fixture@example.test' });
    assert.equal(h.events.length, 0);
  });
}

for (const status of [200, 401]) {
  test(`XHR discards obsolete progress and response (${status})`, async () => {
    const h = await setup(); const progress = [];
    const pending = outcome(h.api.upload('/api/upload', new FormData(), value => progress.push(value)));
    h.api.advanceSessionGeneration();
    h.xhrs[0].upload.onprogress({ lengthComputable: true, loaded: 1, total: 2 });
    h.xhrs[0].finish(status, status === 200 ? { fixture: 'upload' } : expired);
    assert.equal((await pending).error.name, 'AbortError');
    assert.deepEqual(progress, []); assert.equal(h.events.length, 0);
  });
}

test('binary expiry notifications accept a captured generation and preserve the legacy call signature', async () => {
  const h = await setup(); const headers = new Headers({ 'X-Request-ID': 'fixture' });
  const previous = h.api.getSessionGeneration(); h.api.advanceSessionGeneration();
  h.api.notifySessionExpired(401, 'unauthorized', headers, 'fixture', previous);
  assert.equal(h.events.length, 0);
  assert.throws(() => h.api.assertSessionGeneration(previous), { name: 'AbortError' });
  h.api.notifySessionExpired(401, 'unauthorized', headers, 'fixture', h.api.getSessionGeneration());
  assert.equal(h.events.length, 1);
  h.api.notifySessionExpired(401, 'unauthorized', headers, 'fixture');
  assert.equal(h.events.length, 2);
  h.api.notifySessionExpired(401, 'unauthorized', new Headers());
  assert.equal(h.events.length, 2);
});

test('tab selection remains scoped in memory when sessionStorage is unavailable', async () => {
  const h = await setup({ blockedStorage: true }); await h.bootstrap();
  assert.equal(h.api.authHeaders('GET', false)['X-Synwevo-Workspace'], 'A');
  h.api.setWorkspaceContext('B');
  assert.equal(h.api.authHeaders('GET', false)['X-Synwevo-Workspace'], 'B');
});

test('sign-out clears context, rejects old deliveries, and navigates away', async () => {
  const h = await setup(); await h.bootstrap();
  const pending = outcome(h.api.get('/api/projects')); const request = h.next('/api/projects');
  const leaving = h.render().signOut(); await h.answer(h.next('/api/auth/logout'), { ok: true }); await leaving;
  assert.equal(h.api.workspaceContext(), null); assert.equal(h.render().me, null);
  assert.deepEqual(h.navigation, [['replace', '/logged-out']]);
  await h.answer(request, ['old-data']); assert.equal((await pending).error.name, 'AbortError');
});

test('a sign-out in progress does not turn the emptied session into a sign-in redirect', async () => {
  // The guarded page under the sign-out button re-renders with me === null as soon as
  // invalidate() lands, while the /logged-out navigation is still pending. RequireSession used to
  // answer that with assign('/login?next=…'), which Chromium let win over the earlier replace.
  const h = await setup(); await h.bootstrap();
  const leaving = h.render().signOut(); await h.answer(h.next('/api/auth/logout'), { ok: true }); await leaving;
  assert.equal(h.render().me, null);
  const guarded = h.session.RequireSession({ children: h.children });
  assert.equal(guarded.type.name, 'PageState'); assert.equal(guarded.props.children, '正在退出…');
  assert.deepEqual(h.navigation, [['replace', '/logged-out']]);
});

test('on a relying party, sign-out ends the platform session and follows the account service\'s logout URL', async () => {
  const h = await setup(); h.render();
  await h.answer(h.next('/api/auth/state'), { ...authState, role: 'app', portal_url: 'http://portal.test' });
  await h.answer(h.next('/api/auth/me'), me()); h.render();
  const leaving = h.render().signOut();
  const request = h.next('/api/auth/sign-out?everywhere=true');
  assert.equal(request.opts.method, 'POST');
  const logout = 'http://accounts.test/oidc/logout?client_id=docweave-web&post_logout_redirect_uri=http%3A%2F%2Fdocs.test%2Fsigned-out';
  await h.answer(request, { ok: true, scope: 'platform', redirect: logout }); await leaving;
  assert.equal(h.render().me, null);
  assert.deepEqual(h.navigation, [['replace', logout]]);
  assert.equal(h.requests.filter(r => r.path === '/api/auth/logout').length, 0);
});

test('an expired session (not a sign-out) is still sent to sign in with its return address', async () => {
  const h = await setup({ search: '?tab=security' }); await h.bootstrap();
  h.window.dispatchEvent(new CustomEvent('synwevo:session-expired', { detail: {} })); h.render();
  assert.equal(h.render().me, null);
  const guarded = h.session.RequireSession({ children: h.children });
  assert.equal(guarded.props.children, '正在前往登录…');
  assert.deepEqual(h.navigation, [['assign', '/login?next=%2F%3Ftab%3Dsecurity&lang=en']]);
});

test('repeated auth-disabled refreshes do not cancel ordinary business requests', async () => {
  const h = await setup(); h.render();
  await h.answer(h.next('/api/auth/state'), { mode: 'none' }); h.render();
  const pending = h.api.get('/api/projects'); const request = h.next('/api/projects');
  const refresh = h.render().refresh(); await h.answer(h.next('/api/auth/state'), { mode: 'none' }); await refresh;
  assert.equal(h.render().disabled, true);
  await h.answer(request, ['fixture-data']); assert.deepEqual(await pending, ['fixture-data']);
});
