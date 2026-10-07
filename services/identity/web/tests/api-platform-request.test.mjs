/** D20 transport fixtures: exercise the real console request serializer, with no server or user data.
 * Action lifetime, reauthentication and committed-response loss are tested by browser_webhook_redelivery.py.
 */
import { after, before, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

const storage = new Map();
globalThis.window = { location: new URL('http://127.0.0.1/'), history: { state: null, replaceState() {} }, addEventListener() {}, removeEventListener() {} };
globalThis.sessionStorage = { getItem: k => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k) };
const originalFetch = globalThis.fetch;
let server, requestMachine, ApiError;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', appType: 'custom', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  ({ requestMachine } = await server.ssrLoadModule('/src/pages/apiplatform/common.tsx'));
  ({ ApiError } = await server.ssrLoadModule('@synwevo/auth-client'));
  globalThis.document = { cookie: '' };
});
after(async () => { globalThis.fetch = originalFetch; await server?.close(); });

const target = '/api/v1/webhooks/subscriptions/whs_fixture/deliveries/wh_fixture/redeliver';
const key = 'b6d72763-e9be-4864-9d44-bdf2d904a1a6';
const delivery = { id: 'wh_new_fixture', status: 'pending', redelivery: true };
const reason = 'Isolated console transport fixture';

test('redelivery key is a console body field, never an arbitrary request header', async () => {
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/admin/api-platform/request');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), {
      credential: 'fixture-only', reason, method: 'POST', path: target, query: {}, idempotency_key: key,
    });
    assert.equal(new Headers(options.headers).has('Idempotency-Key'), false);
    return Response.json({ status: 202, data: delivery });
  };
  assert.deepEqual(await requestMachine('fixture-only', reason, 'POST', target, {}, undefined, key), delivery);
});

test('existing callers omit idempotency and preserve read queries and explicit zero overlap', async () => {
  const sent = [];
  globalThis.fetch = async (_, options) => {
    sent.push(JSON.parse(options.body));
    return Response.json({ status: 200, data: {} });
  };
  await requestMachine('fixture-only', reason, 'GET', '/api/v1/events', { limit: 25, cursor: 'fixture-cursor' });
  await requestMachine('fixture-only', reason, 'POST', '/api/v1/webhooks/subscriptions/whs_fixture/rotate-secret', {}, { overlap_s: 0 });
  assert.equal(sent.some(body => 'idempotency_key' in body || 'headers' in body), false);
  assert.deepEqual(sent[0].query, { limit: 25, cursor: 'fixture-cursor' });
  assert.deepEqual(sent[1].body, { overlap_s: 0 });
});

test('network failure remains a refusal; a caller retry retains the provided key', async () => {
  const sent = [];
  globalThis.fetch = async (_, options) => {
    sent.push(JSON.parse(options.body));
    if (sent.length === 1) throw new TypeError('fixture response lost');
    return Response.json({ status: 202, data: delivery });
  };
  const run = () => requestMachine('fixture-only', reason, 'POST', target, {}, undefined, key);
  await assert.rejects(run(), e => e instanceof ApiError && e.code === 'network');
  assert.deepEqual(await run(), delivery);
  assert.deepEqual(sent.map(body => body.idempotency_key), [key, key]);
});

test('reauthentication and data-plane refusals keep their existing error semantics', async () => {
  globalThis.fetch = async () => Response.json({ error: 'reauth_required', message: 'Fixture reauthentication required' }, { status: 403 });
  await assert.rejects(requestMachine('fixture-only', reason, 'POST', target, {}, undefined, key),
    e => e instanceof ApiError && e.code === 'reauth_required' && e.status === 403);
  globalThis.fetch = async () => Response.json({ status: 409, data: { error: { code: 'subscription_not_active', message: 'Fixture inactive subscription' } }, request_id: 'fixture-request' });
  await assert.rejects(requestMachine('fixture-only', reason, 'POST', target, {}, undefined, key),
    e => e instanceof ApiError && e.code === 'subscription_not_active' && e.detail.console_request === true && e.requestId === 'fixture-request');
});
