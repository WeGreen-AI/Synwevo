/** Deterministic scheduling regression, not browser E2E. Executes the actual API,
 * resources and React component sources with explicit fetch/DOM/hook test doubles.
 * node --experimental-vm-modules --test apps/docs/web/tests/resources-generation.test.mjs
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { transformSync } from 'rolldown/utils';

const compiled = {};
for (const [name, path] of Object.entries({
  api: '../../../../packages/auth-client/web/src/api.ts',
  resources: '../src/lib/resources.ts', component: '../src/components/ApiResource.tsx',
})) {
  const result = transformSync(path, readFileSync(new URL(path, import.meta.url), 'utf8'),
    { jsx: { runtime: 'automatic' }, target: 'es2022' });
  assert.equal(result.errors.length, 0); compiled[name] = result.code;
}
const turn = () => new Promise(resolve => setImmediate(resolve));
const outcome = promise => promise.then(value => ({ value }), error => ({ error }));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const response = (status = 200, body = new Blob(['fixture-file'])) => ({
  ok: status === 200, status,
  headers: new Headers({ 'X-Request-ID': 'fixture-request', 'Content-Type': status === 200 ? 'image/png' : 'application/json',
    'Content-Disposition': "attachment; filename*=UTF-8''%E5%AE%A2%E6%88%B7.png" }),
  blob: () => Promise.resolve(body), json: () => Promise.resolve({ error: 'session_revoked' }),
});

async function setup() {
  const requests = [], events = [], urls = [], revoked = [], downloads = [], toasts = [], timers = [];
  const values = [], effects = [], pending = []; let index = 0, tree;
  const react = {
    useState: initial => {
      const i = index++;
      if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial;
      return [values[i], value => { values[i] = typeof value === 'function' ? value(values[i]) : value; }];
    },
    useRef: initial => { const i = index++; return values[i] ||= { current: initial }; },
    useEffect: (fn, deps) => {
      const i = index++;
      if (!effects[i] || deps.some((d, j) => d !== effects[i].deps[j])) {
        pending.push(() => { effects[i]?.cleanup?.(); effects[i] = { deps, cleanup: fn() }; });
      }
    },
  };
  const window = new EventTarget();
  window.addEventListener('synwevo:session-expired', event => events.push(event.detail));
  const document = { cookie: '', body: { append() {} }, createElement: () => {
    const anchor = { remove() {}, click() { downloads.push({ url: anchor.href, name: anchor.download }); } }; return anchor;
  } };
  const context = vm.createContext({ window, document, Error, DOMException, CustomEvent, AbortController,
    setTimeout: fn => { timers.push(fn); },
    URL: { createObjectURL: blob => { const url = 'blob:fixture-' + urls.length; urls.push({ url, blob }); return url; },
      revokeObjectURL: url => revoked.push(url) },
    fetch: (path, opts) => new Promise((resolve, reject) => requests.push({ path, opts, resolve, reject })),
  });
  const synthetic = (name, members) => new vm.SyntheticModule(Object.keys(members), function () {
    for (const [key, value] of Object.entries(members)) this.setExport(key, value);
  }, { context, identifier: name });
  const modules = {
    react: synthetic('react', react),
    'react/jsx-runtime': synthetic('jsx', { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: 'fragment' }),
    '@synwevo/ui': synthetic('ui', { useI18n: () => ({ t: value => value }), useToast: () => (...args) => toasts.push(args) }),
    '@synwevo/auth-client/api-messages': synthetic('messages', { apiFailureText: error => error.originalMessage }),
    '../lib/perm': synthetic('perm', { errorText: error => error.message }),
  };
  const link = name => { assert(modules[name], name); return modules[name]; };
  const apiModule = new vm.SourceTextModule(compiled.api, { context, identifier: 'api' });
  modules['@synwevo/auth-client/api'] = apiModule;
  await apiModule.link(link); await apiModule.evaluate();
  const resourceModule = new vm.SourceTextModule(compiled.resources, { context, identifier: 'resources' });
  modules['../lib/resources'] = resourceModule;
  await resourceModule.link(link); await resourceModule.evaluate();
  const componentModule = new vm.SourceTextModule(compiled.component, { context, identifier: 'component' });
  await componentModule.link(link); await componentModule.evaluate();
  const api = apiModule.namespace, resource = resourceModule.namespace, components = componentModule.namespace;
  const render = (name, props) => { index = 0; tree = components[name](props); while (pending.length) pending.shift()(); return tree; };
  const unmount = () => { for (const effect of effects) effect?.cleanup?.(); };
  const next = () => { assert(requests.length, 'fixture request is pending'); return requests.shift(); };
  api.setWorkspaceContext('A');
  return { api, resource, render, unmount, next, requests, events, urls, revoked, downloads, toasts, timers,
    imageUrl: () => tree.props.children[0].props.src, imageError: () => tree.props.children[1] };
}

for (const status of [200, 401]) {
  test(`late binary headers cannot deliver a previous workspace response (${status})`, async () => {
    const h = await setup(); const pending = outcome(h.resource.readResource('/api/fixture/file')); const request = h.next();
    assert.equal(request.opts.headers['X-Synwevo-Workspace'], 'A');
    h.api.setWorkspaceContext('B'); request.resolve(response(status));
    assert.equal((await pending).error.name, 'AbortError'); assert.equal(h.events.length, 0);
  });
}

test('late decoded binary 401 cannot expire the newer session', async () => {
  const h = await setup(); const pending = outcome(h.resource.readResource('/api/fixture/file')); const request = h.next();
  const json = deferred(); request.resolve({ ...response(401), json: () => json.promise }); await turn();
  h.api.advanceSessionGeneration(); json.resolve({ error: 'unauthorized' });
  assert.equal((await pending).error.name, 'AbortError'); assert.equal(h.events.length, 0);
});

test('current binary 401 still notifies session expiry', async () => {
  const h = await setup(); const pending = outcome(h.resource.readResource('/api/fixture/file'));
  h.next().resolve(response(401));
  assert.equal((await pending).error.code, 'session_revoked'); assert.equal(h.events.length, 1);
});

test('obsolete binary network failure is cancellation', async () => {
  const h = await setup(); const pending = outcome(h.resource.readResource('/api/fixture/file')); const request = h.next();
  h.api.advanceSessionGeneration(); request.reject(new TypeError('Fixture network failure'));
  assert.equal((await pending).error.name, 'AbortError');
});

for (const fails of [false, true]) {
  test(`a delayed download Blob cannot create a URL after rollover (decoding fails=${fails})`, async () => {
    const h = await setup(); const pending = outcome(h.resource.downloadResource('/api/fixture/file'));
    const blob = deferred(); h.next().resolve({ ...response(), blob: () => blob.promise }); await turn();
    h.api.setWorkspaceContext('B');
    if (fails) blob.reject(new TypeError('Fixture truncated body')); else blob.resolve(new Blob(['old-data']));
    assert.equal((await pending).error.name, 'AbortError');
    assert.equal(h.urls.length, 0); assert.equal(h.downloads.length, 0);
  });
}

test('current download preserves bytes and Unicode name and schedules URL release', async () => {
  const h = await setup(); const blob = new Blob(['fixture-data']); const pending = h.resource.downloadResource('/api/fixture/file');
  h.next().resolve(response(200, blob)); await pending;
  assert.equal(h.urls[0].blob, blob);
  assert.deepEqual(h.downloads, [{ url: 'blob:fixture-0', name: '客户.png' }]);
  h.timers[0](); assert.deepEqual(h.revoked, ['blob:fixture-0']);
});

test('preview from an old generation cannot overwrite the new preview', async () => {
  const h = await setup(); h.render('ApiImage', { src: '/api/fixture/image' });
  const blob = deferred(); h.next().resolve({ ...response(), blob: () => blob.promise }); await turn();
  h.api.setWorkspaceContext('B'); h.render('ApiImage', { src: '/api/fixture/image' });
  assert.equal(h.imageUrl(), undefined);
  const current = new Blob(['new-workspace-image']); h.next().resolve(response(200, current)); await turn();
  h.render('ApiImage', { src: '/api/fixture/image' }); const url = h.imageUrl(); assert.match(url, /^blob:/);
  blob.resolve(new Blob(['old-workspace-image'])); await turn(); h.render('ApiImage', { src: '/api/fixture/image' });
  assert.equal(h.imageUrl(), url); assert.equal(h.urls.length, 1); assert.equal(h.urls[0].blob, current);
  assert.equal(h.imageError(), false); h.unmount(); assert.deepEqual(h.revoked, [url]);
});

test('an already loaded preview is hidden and released on a new generation', async () => {
  const h = await setup(); h.render('ApiImage', { src: '/api/fixture/image' });
  h.next().resolve(response()); await turn(); h.render('ApiImage', { src: '/api/fixture/image' }); const oldUrl = h.imageUrl();
  h.api.advanceSessionGeneration(); h.render('ApiImage', { src: '/api/fixture/image' });
  assert.equal(h.imageUrl(), undefined); assert.deepEqual(h.revoked, [oldUrl]);
  h.unmount();
});

test('preview unmount during body decoding does not create a URL or failure message', async () => {
  const h = await setup(); h.render('ApiImage', { src: '/api/fixture/image' });
  const blob = deferred(); h.next().resolve({ ...response(), blob: () => blob.promise }); await turn();
  h.unmount(); blob.resolve(new Blob(['obsolete'])); await turn();
  assert.equal(h.urls.length, 0); assert.equal(h.events.length, 0);
});

test('download cancellation on rollover is silent, whereas a current denial shows an error', async () => {
  const h = await setup(); let button = h.render('DownloadLink', { href: '/api/fixture/file' });
  const old = button.props.onClick(); const request = h.next(); h.api.setWorkspaceContext('B');
  request.resolve(response(401)); await old;
  assert.equal(h.toasts.length, 0); assert.equal(h.events.length, 0);
  button = h.render('DownloadLink', { href: '/api/fixture/file' });
  const current = button.props.onClick(); h.next().resolve(response(401)); await current;
  assert.equal(h.toasts.length, 1); assert.equal(h.events.length, 1);
});

test('changing a download target cancels its old Blob and leaves the new button usable', async () => {
  const h = await setup(); let button = h.render('DownloadLink', { href: '/api/fixture/old' });
  const old = button.props.onClick(); const blob = deferred(); h.next().resolve({ ...response(), blob: () => blob.promise }); await turn();
  h.render('DownloadLink', { href: '/api/fixture/new' }); button = h.render('DownloadLink', { href: '/api/fixture/new' });
  assert.equal(button.props.disabled, false);
  const current = button.props.onClick(); h.next().resolve(response()); await current;
  blob.resolve(new Blob(['obsolete'])); await old;
  assert.equal(h.downloads.length, 1); assert.equal(h.urls.length, 1); assert.equal(h.toasts.length, 0);
});
