// Contract tests: pure parsing plus a real loopback signature receiver; no replacement Docs API.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { ApiKey, Client, errorFromResponse, ConflictError, RateLimitError, ValidationError,
  parseRetryAfter, parseRateLimitReset, safeFilename, signWebhook, verifyWebhook,
  verifyLegacyWebhook, createWebhookReceiver } from '../synwevo-docs.mjs';

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
