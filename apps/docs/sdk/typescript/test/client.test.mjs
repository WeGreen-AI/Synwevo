// Contract tests: pure parsing plus a real loopback signature receiver; no replacement Docs API.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { ApiKey, Client, WorkspaceClient, errorFromResponse, ConflictError, RateLimitError, ValidationError,
  parseRetryAfter, parseRateLimitReset, safeFilename, signWebhook, verifyWebhook,
  verifyLegacyWebhook, createWebhookReceiver, WEBHOOK_MIN_KEY_BYTES } from '../synwevo-docs.mjs';

test('parses envelope, problem details, request IDs and rate-limit hints', () => {
  const conflict = errorFromResponse(409, new Headers({ 'x-request-id': 'synthetic-id' }),
    JSON.stringify({ error: { code: 'stale', message: 'Changed', field: 'base_rev', details: { rev: 2 } } }));
  assert(conflict instanceof ConflictError);
  assert.equal(conflict.requestId, 'synthetic-id');
  assert.equal(conflict.field, 'base_rev');
  assert.deepEqual(conflict.details, { rev: 2 });
  const limited = errorFromResponse(429, new Headers({ ratelimit: '"api";r=0;t=3' }),
    JSON.stringify({ type: 'urn:synwevo:problem:quota', detail: 'Wait', errors: [{ pointer: '#/formats/0' }] }));
  assert(limited instanceof RateLimitError);
  assert.equal(limited.retryAfter, 3);
  assert.equal(limited.code, 'quota');
  assert.equal(limited.field, 'formats.0');
});

test('Retry-After supports delta seconds and dates, and backoff never undercuts it', () => {
  assert.equal(parseRetryAfter('5'), 5);
  assert.equal(parseRetryAfter('Thu, 24 Sep 2026 00:00:05 GMT', Date.UTC(2026, 8, 24)), 5);
  assert.equal(parseRetryAfter('invalid'), null);
  assert.equal(parseRateLimitReset('"key";r=0;t=12'), 12);
  assert.equal(parseRateLimitReset('"key";r=298;t=1, "workspace";r=0;t=20'), 20);
  assert.equal(parseRateLimitReset('"key";r=0;t=3, "workspace";r=0;t=20'), 20);
  const client = new Client('http://127.0.0.1:1', new ApiKey('synthetic'), { random: () => 0.5 });
  assert.equal(client.backoffMs(0, 5), 5000);
  assert.equal(client.backoffMs(0, 121), null);
});

test('validation and redaction happen without sending requests', () => {
  const credential = new ApiKey('synthetic-credential-do-not-log');
  assert(!JSON.stringify(credential).includes('synthetic-credential'));
  const client = new Client('http://127.0.0.1:1', credential);
  assert.throws(() => client.createJob('synthetic', { errorPolicy: 'ignore' }), ValidationError);
  assert.throws(() => client.createJob('synthetic', { layout: 'stretch' }), ValidationError);
  assert.throws(() => client.createJob('synthetic', { formats: ['../../docx'] }), ValidationError);
  assert.equal(safeFilename('../folder\\sample.docx'), 'sample.docx');
  assert.equal(safeFilename('..'), 'download.bin');
});

test('Standard Webhooks verifies raw bytes, rotated signatures and rejects alteration/replay', () => {
  const secret = 'whsec_' + Buffer.from('synthetic-only-key-material').toString('base64');
  const body = Buffer.from('{ "synthetic": true }');
  const headers = { 'Webhook-ID': 'synthetic-event', 'Webhook-Timestamp': '1000',
    'Webhook-Signature': `v1,invalid ${signWebhook('synthetic-event', '1000', body, secret)}` };
  assert.equal(verifyWebhook(headers, body, ['old-synthetic-key', secret], { nowS: 1000 }).event.synthetic, true);
  assert.throws(() => verifyWebhook(headers, Buffer.from('{"synthetic":true}'), secret, { nowS: 1000 }),
    { code: 'invalid_signature' });
  assert.throws(() => verifyWebhook(headers, body, secret, { nowS: 1400 }), { code: 'timestamp_out_of_tolerance' });
  assert.throws(() => verifyWebhook(headers, body, secret, { nowS: 600 }), { code: 'timestamp_out_of_tolerance' });
});

test('legacy verifier preserves timestamp and raw-body signing contract', () => {
  const body = Buffer.from('{"synthetic":true}'); const secret = 'synthetic-legacy';
  const mac = createHmac('sha256', secret).update('1000.').update(body).digest('hex');
  assert.equal(verifyLegacyWebhook({ 'X-DocWeave-Timestamp': '1000', 'X-DocWeave-Signature': `v1=${mac}` },
    body, secret, { nowS: 1000 }).event.synthetic, true);
});

test('minimal receiver acknowledges only successful handling and retries a failed event', async () => {
  const secret = 'synthetic-receiver-secret'; let attempts = 0;
  const server = createWebhookReceiver({ secret, onEvent: async () => {
    attempts += 1;
    if (attempts === 1) throw new Error('Synthetic handler failure');
  } });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const body = Buffer.from('{"synthetic":true}'); const timestamp = String(Math.floor(Date.now() / 1000));
    const headers = { 'webhook-id': 'synthetic-event', 'webhook-timestamp': timestamp,
      'webhook-signature': signWebhook('synthetic-event', timestamp, body, secret) };
    const send = () => fetch(`http://127.0.0.1:${server.address().port}`, { method: 'POST', headers, body });
    assert.equal((await send()).status, 503);
    assert.equal((await send()).status, 204);
    assert.equal((await send()).status, 204);
    assert.equal(attempts, 2);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('the gate\'s own rate limit is retried after Retry-After even for a non-idempotent write', async () => {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: String(url), method: init.method });
    if (calls.length === 1) {
      return new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'fixture', details: { retry_after: 0.01 } } }),
        { status: 429, headers: { 'content-type': 'application/json', 'retry-after': '0' } });
    }
    return new Response(JSON.stringify({ rev: 2, mappings: [] }), { status: 200, headers: { 'content-type': 'application/json' } });
  };
  const client = new Client('http://fixture.invalid', new ApiKey('fixture'), { fetch: fetchImpl, sleep: async () => {} });
  const out = await client.putMappings('p', []);
  assert.equal(out.rev, 2);
  assert.equal(calls.length, 2);
  assert.ok(calls.every((c) => c.method === 'PUT'));
  // a bare 429 with another code on a non-idempotent write is still not retried
  const bare = async () => new Response(JSON.stringify({ error: { code: 'limited', message: 'fixture' } }), { status: 429, headers: { 'content-type': 'application/json', 'retry-after': '0' } });
  const strict = new Client('http://fixture.invalid', new ApiKey('fixture'), { fetch: bare, sleep: async () => {} });
  await assert.rejects(() => strict.putMappings('p', []), RateLimitError);
});

test('copy requests keep one key across retries and withdrawal is not retried', async () => {
  // Labelled transport-fault fixture; real authorization and copying use integration acceptance.
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url: new URL(url), method: init.method, key: init.headers['Idempotency-Key'], body: init.body });
    const fail = calls.length === 1 || new URL(url).pathname.endsWith('/withdraw');
    return new Response(JSON.stringify(fail
      ? { error: { code: 'unavailable', message: 'synthetic fault fixture' } }
      : { id: 'pc_fixture', status: 'pending' }),
    { status: fail ? 503 : 200, headers: { 'content-type': 'application/json', 'retry-after': '0' } });
  };
  const client = new Client('http://fixture.invalid', new ApiKey('fixture'), { fetch: fetchImpl, sleep: async () => {} });
  assert.throws(() => client.requestCopy('p', {}), ValidationError);
  assert.equal(calls.length, 0);
  const created = await client.requestCopy('p 1', { targetWorkspaceId: 'w_fixture', sources: true, name: 'copy' });
  assert.equal(created.id, 'pc_fixture');
  assert.equal(calls.length, 2);
  assert.ok(calls[0].key);
  assert.equal(calls[0].key, calls[1].key);
  assert.equal(calls[0].body, calls[1].body);
  assert.equal(calls[0].url.pathname, '/api/v1/projects/p%201/copy-requests');
  assert.deepEqual(JSON.parse(calls[0].body), { target_workspace_id: 'w_fixture', scope: { sources: true, corrections: false }, name: 'copy' });
  await assert.rejects(() => client.withdrawCopyRequest('pc fixture'), { status: 503 });
  assert.equal(calls.length, 3);
  assert.equal(calls[2].url.pathname, '/api/v1/copy-requests/pc%20fixture/withdraw');
  assert.equal(calls[2].key, undefined);
  await client.copyManifest('p', { corrections: true });
  assert.equal(calls[3].url.search, '?sources=false&corrections=true');
  await client.listCopyRequests('p', { status: 'withdrawn', limit: 10 });
  assert.equal(calls[4].url.search, '?status=withdrawn&limit=10');
});

test('directory credentials use account origin and workspace audience independently of Docs', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({ url: String(url), body: init.body });
    return new Response(JSON.stringify(calls.length === 1 ? { access_token: 'synthetic-token', expires_in: 300 } : { client_id: 'fixture' }), { status: 200 });
  };
  const client = await WorkspaceClient.fromEnv({ SYNWEVO_BASE_URL: 'https://docs.invalid', SYNWEVO_API_KEY: 'docs-only-fixture',
    SYNWEVO_WORKSPACE_CLIENT_ID: 'fixture', SYNWEVO_WORKSPACE_CLIENT_SECRET: 'synthetic-secret' }, { fetch });
  assert.equal(client.baseUrl, 'https://accounts.synwevo.com');
  assert.equal(typeof client.createProject, 'undefined');
  await client.whoami();
  assert.equal(calls[0].url, 'https://accounts.synwevo.com/oidc/token');
  assert.equal(calls[0].body.get('scope'), 'workspace.read');
  assert.equal(calls[0].body.get('resource'), 'urn:synwevo:platform:workspace');
  assert.equal(calls[1].url, 'https://accounts.synwevo.com/api/workspace/v1/whoami');
  await assert.rejects(() => WorkspaceClient.fromEnv({ SYNWEVO_CLIENT_ID: 'docs-only-fixture' }), ValidationError);
});

test('directory invitation create/resend keep retry keys and member edits are not retried', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    const path = new URL(url).pathname;
    calls.push({ path, method: init.method, key: init.headers['Idempotency-Key'], body: init.body });
    const count = calls.filter(c => c.path === path && c.method === init.method).length;
    const retryable = path === '/api/workspace/v1/invitations' || path.endsWith('/resend');
    const ok = retryable && count === 2;
    return new Response(JSON.stringify(ok ? { id: 'inv_fixture' } : { error: { code: 'unavailable', message: 'synthetic fault' } }),
      { status: ok ? 200 : 503, headers: { 'retry-after': '0' } });
  };
  const client = new WorkspaceClient('http://fixture.invalid', new ApiKey('synthetic-token'), { fetch, sleep: async () => {} });
  await client.createInvitation({ email: 'new@example.invalid', role: 'viewer' });
  await client.resendInvitation('inv 1');
  await assert.rejects(() => client.revokeInvitation('inv 1'), { status: 503 });
  await assert.rejects(() => client.changeMember('user 1', { role: 'editor', expectedRole: 'viewer' }), { status: 503 });
  await assert.rejects(() => client.removeMember('user 1', { expectedRole: 'viewer', reason: 'Synthetic removal' }), { status: 503 });
  assert.throws(() => client.changeMember('user 1', { role: 'viewer' }), ValidationError);
  assert.equal(calls.length, 7);
  for (const [a, b] of [[calls[0], calls[1]], [calls[2], calls[3]]]) { assert.ok(a.key); assert.deepEqual(a, b); }
  assert.equal(calls[2].path, '/api/workspace/v1/invitations/inv%201/resend');
  assert.deepEqual(JSON.parse(calls[5].body), { role: 'editor', expected_role: 'viewer' });
  assert.ok(calls.slice(4).every(c => c.key === undefined));
});

test('a verifier refuses empty or short key material (CR-049 WH-4)', () => {
  const body = Buffer.from('{"type":"fixture"}'); const ts = String(Math.floor(Date.now() / 1000));
  const forged = 'v1,' + createHmac('sha256', Buffer.alloc(0)).update(`msg_1.${ts}.`).update(body).digest('base64');
  const headers = { 'webhook-id': 'msg_1', 'webhook-timestamp': ts, 'webhook-signature': forged };
  for (const weak of ['whsec_', ['whsec_'], 'short', 'whsec_' + Buffer.alloc(15, 120).toString('base64')]) {
    assert.throws(() => verifyWebhook(headers, body, weak), (e) => e.code === 'weak_secret', String(weak));
  }
  assert.throws(() => verifyWebhook(headers, body, ''), (e) => e.code === 'no_secret');
});

test('the legacy verifier refuses a missing or empty secret but not a short one (R3-WH-3, R4)', () => {
  // process.env.X of an unset variable is undefined; String(undefined) was the key 'undefined'.
  const body = Buffer.from('{"id":"ev_forged","type":"job.succeeded"}'); const ts = String(Math.floor(Date.now() / 1000));
  const forge = (key) => ({ 'X-DocWeave-Timestamp': ts,
    'X-DocWeave-Signature': `v1=${createHmac('sha256', key).update(`${ts}.`).update(body).digest('hex')}` });
  for (const [weak, key] of [[undefined, 'undefined'], [null, 'null'], ['', ''], [16, '16']]) {
    assert.throws(() => verifyLegacyWebhook(forge(key), body, weak), (e) => e.code === 'no_secret', String(weak));
  }
  assert.throws(() => verifyLegacyWebhook(forge(''), body, Buffer.alloc(0)), (e) => e.code === 'no_secret');
  // a short secret is the deployment's own choice (the server signs with any non-empty one):
  // it verifies what it signed and nothing else
  for (const key of ['k'.repeat(WEBHOOK_MIN_KEY_BYTES), 'short', 'x'.repeat(WEBHOOK_MIN_KEY_BYTES - 1)]) {
    assert.equal(verifyLegacyWebhook(forge(key), body, key).event.id, 'ev_forged');
    assert.throws(() => verifyLegacyWebhook(forge(key), body, `${key}-other`), (e) => e.code === 'invalid_signature');
  }
});
