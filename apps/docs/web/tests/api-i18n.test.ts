import { test } from 'node:test';
import assert from 'node:assert/strict';
import { api, ApiError, authHeaders, setWorkspaceContext, upload, workspaceContext } from '@synwevo/auth-client/api';
import { apiMessages, API_ERROR_SOURCE } from '@synwevo/auth-client/api-messages';
import { setLocale, validateMessages } from '@synwevo/ui/i18n-core';
import { downloadName, readResource } from '../src/lib/resources.ts';

// Explicit isolated browser doubles: no network or persisted user data.
test('API error catalogs have locale/parameter parity and leave wire details intact', () => {
  assert.deepEqual(validateMessages(apiMessages), []);
  for (const source of Object.values(API_ERROR_SOURCE)) assert.ok(source in apiMessages);
  const details = { field: '客户名称', retry_after: 4 };
  const error = new ApiError(400, 'future_code', 'Raw server prose must not leak into UI', details);
  error.requestId = 'fixture-request';
  setLocale('en');
  assert.equal(error.message, 'The request could not be completed (future_code). · Request ID: fixture-request');
  setLocale('zh-Hant');
  assert.equal(error.message, '請求未完成（future_code）。 · 請求編號：fixture-request');
  assert.equal(error.originalMessage, 'Raw server prose must not leak into UI');
  assert.equal(error.detail, details);
  assert.equal(error.code, 'future_code');
  const network = new ApiError(0, 'network', 'original diagnostic');
  assert.match(network.message, /網路/);
  setLocale('en');
  assert.match(network.message, /Network/);
});

test('JSON, explicit SSO context, multipart and binary resources keep tab workspace/CSRF binding', async () => {
  const store = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptors(globalThis);
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => store.set(key, value), removeItem: (key: string) => store.delete(key),
  } });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { cookie: 'dw_csrf=fixture%20csrf' } });
  const calls: { url: string; opts: RequestInit }[] = [];
  globalThis.fetch = (async (url: string, opts: RequestInit) => {
    calls.push({ url, opts });
    return new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'fixture-request' } });
  }) as typeof fetch;
  try {
    setWorkspaceContext('tab-A');
    assert.deepEqual(authHeaders('POST', true), { 'X-Synwevo-Workspace': 'tab-A', 'Content-Type': 'application/json', 'X-Synwevo-CSRF': 'fixture csrf' });
    await api('/api/fixture', { body: { value: '客户原文' }, headers: { 'Idempotency-Key': 'fixture-key', 'X-Synwevo-Workspace': 'cannot-override-tab' } });
    assert.deepEqual(calls[0].opts.headers, { 'Idempotency-Key': 'fixture-key', 'X-Synwevo-Workspace': 'tab-A', 'Content-Type': 'application/json', 'X-Synwevo-CSRF': 'fixture csrf' });
    await api('/api/auth/me', { workspaceId: 'validated-by-server' });
    assert.equal((calls[1].opts.headers as any)['X-Synwevo-Workspace'], 'validated-by-server');
    assert.equal(workspaceContext(), 'tab-A', 'a launch probe must not overwrite tab context');
    const response = await readResource('/api/fixture/download');
    assert.equal((calls[2].opts.headers as any)['X-Synwevo-Workspace'], 'tab-A');
    assert.equal(await response.text(), '{"ok":true}', 'JSON files must be downloaded as bytes, not parsed');
    await assert.rejects(readResource('https://example.invalid/file'), /Only local/);
    let xhr: any;
    class FixtureXHR {
      headers: Record<string, string> = {}; upload: any = {}; status = 200; responseText = '{"id":"fixture"}'; onload?: () => void;
      constructor() { xhr = this; }
      open() {} setRequestHeader(k: string, v: string) { this.headers[k] = v; }
      send() { this.onload?.(); }
    }
    Object.defineProperty(globalThis, 'XMLHttpRequest', { configurable: true, value: FixtureXHR });
    await upload('/api/fixture/upload', new FormData());
    assert.deepEqual(xhr.headers, { 'X-Synwevo-Workspace': 'tab-A', 'X-Synwevo-CSRF': 'fixture csrf' });
    setWorkspaceContext(null);
    assert.equal(workspaceContext(), null);
  } finally {
    for (const key of ['fetch', 'document', 'sessionStorage', 'XMLHttpRequest']) {
      if (original[key]) Object.defineProperty(globalThis, key, original[key]);
      else delete (globalThis as any)[key];
    }
    setLocale('zh-Hans');
  }
});

test('gateway identity, application request id and retry information survive localization', async () => {
  const original = globalThis.fetch;
  setLocale('en');
  try {
    globalThis.fetch = async () => new Response('<html>blocked</html>', { status: 503, headers: { Server: 'cloudflare' } });
    await assert.rejects(api('/api/fixture'), (e: any) => e instanceof ApiError && e.upstream && e.code === 'upstream_unavailable' && e.message.includes('Gateway could not reach Synwevo (HTTP 503)') && !/cloudflare/i.test(e.message) && e.detail?.server === 'cloudflare');
    globalThis.fetch = async () => new Response(JSON.stringify({ error: 'rate_limited', message: 'backend diagnostic', request_id: 'request-429' }), { status: 429, headers: { 'Content-Type': 'application/json', 'Retry-After': '8' } });
    await assert.rejects(api('/api/fixture'), (e: any) => e.code === 'rate_limited' && e.retryAfter === 8 && e.requestId === 'request-429' && e.message.includes('Too many requests.') && !e.message.includes('backend diagnostic'));
  } finally { globalThis.fetch = original; setLocale('zh-Hans'); }
});

test('download filenames preserve Unicode and cannot contain path separators', () => {
  assert.equal(downloadName("attachment; filename*=UTF-8''%E5%AE%A2%E6%88%B7.csv"), '客户.csv');
  assert.equal(downloadName('attachment; filename="../a.csv"'), '.._a.csv');
  assert.equal(downloadName(null), 'download');
});

test('only confirmed Synwevo session 401s broadcast expiration, including XHR and binary requests', async () => {
  const original = Object.getOwnPropertyDescriptors(globalThis);
  const windowFixture = new EventTarget();
  Object.defineProperty(globalThis, 'window', { configurable: true, value: windowFixture });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { cookie: '' } });
  let events = 0;
  windowFixture.addEventListener('synwevo:session-expired', () => events++);
  try {
    for (const [code, status, confirmed] of [
      ['bad_credentials', 401, true], ['unauthorized', 401, false],
      ['session_expired', 403, true], ['unauthorized', 401, true],
      ['session_expired', 401, true], ['session_revoked', 401, true],
    ] as const) {
      globalThis.fetch = async () => new Response(JSON.stringify({ error: code, message: 'fixture diagnostic' }), { status, headers: { 'Content-Type': 'application/json', ...(confirmed ? { 'X-Request-ID': 'fixture-401' } : {}) } });
      await assert.rejects(api('/api/fixture'));
    }
    assert.equal(events, 3);
    globalThis.fetch = async () => new Response('<html>Gateway sign in</html>', { status: 401, headers: { server: 'cloudflare' } });
    await assert.rejects(api('/api/fixture'));
    assert.equal(events, 3);
    class FixtureXHR {
      upload: any = {}; status = 401; responseText = '{"error":"session_revoked"}'; onload?: () => void;
      open() {} setRequestHeader() {} send() { this.onload?.(); }
      getResponseHeader(name: string) { return name === 'X-Request-ID' ? 'fixture-xhr' : null; }
    }
    Object.defineProperty(globalThis, 'XMLHttpRequest', { configurable: true, value: FixtureXHR });
    await assert.rejects(upload('/api/fixture/upload', new FormData()));
    assert.equal(events, 4);
    globalThis.fetch = async () => new Response('{"error":"session_expired"}', { status: 401, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'fixture-binary' } });
    await assert.rejects(readResource('/api/fixture/download'));
    assert.equal(events, 5);
  } finally {
    for (const key of ['fetch', 'document', 'window', 'XMLHttpRequest']) {
      if (original[key]) Object.defineProperty(globalThis, key, original[key]); else delete (globalThis as any)[key];
    }
  }
});
