// Real loopback HTTP fault fixtures for Docs redelivery. Scripted receipts are not business
// acceptance, and no live webhook is delivered. The Suite endpoint has a different contract.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { ApiKey, Client, TransportError, ValidationError } from '../synwevo-docs.mjs';

async function fixture(t, respond, options = {}) {
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    respond(req, res, Buffer.concat(chunks).toString());
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  return new Client(`http://127.0.0.1:${server.address().port}`, new ApiKey('synthetic-fixture'),
    { maxRetries: 2, sleep: async () => {}, random: () => 0, ...options });
}

function answer(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(JSON.stringify(body));
}

function loseResponse(res, partial = false) {
  if (partial) {
    res.writeHead(200, { 'content-length': '1000' });
    res.flushHeaders();
    res.write('{"id":');
    setImmediate(() => res.destroy());
  } else res.destroy();
}

for (const partial of [false, true]) {
  test(`lost ${partial ? 'partial' : 'whole'} response keeps the key; a new method call gets a new key`, async t => {
    const calls = []; const receipts = new Map();
    const client = await fixture(t, (req, res, body) => {
      const key = req.headers['idempotency-key'];
      calls.push({ method: req.method, path: req.url, key, body });
      if (!receipts.has(key)) {
        receipts.set(key, { id: `fixture_delivery_${receipts.size}`, redelivery: true });
        loseResponse(res, partial);
      } else answer(res, 200, receipts.get(key), { 'idempotent-replayed': 'true' });
    });
    const first = await client.redeliver('sub 1', 'delivery/1');
    const second = await client.redeliver('sub 1', 'delivery/1');
    assert.equal(calls.length, 4);
    assert.deepEqual(calls[0], calls[1]);
    assert.deepEqual(calls[2], calls[3]);
    assert.equal(calls[0].method, 'POST');
    assert.equal(calls[0].path, '/api/v1/webhooks/subscriptions/sub%201/deliveries/delivery%2F1/redeliver');
    assert.equal(calls[0].body, '');
    assert.notEqual(calls[0].key, calls[2].key);
    for (const key of receipts.keys()) assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.notEqual(first.id, second.id);
    assert.equal(first.$meta.replayed, true);
    assert.equal(second.$meta.replayed, true);
  });
}

test('explicit key survives a transport retry and repeated method calls', async t => {
  const keys = [];
  const client = await fixture(t, (req, res) => {
    keys.push(req.headers['idempotency-key']);
    if (keys.length === 1) loseResponse(res);
    else answer(res, 200, { id: 'fixture_original_receipt' }, { 'idempotent-replayed': 'true' });
  });
  const first = await client.redeliver('s', 'd', { idempotencyKey: 'fixture:operator-intent' });
  const second = await client.redeliver('s', 'd', { idempotencyKey: 'fixture:operator-intent' });
  assert.deepEqual(first, second);
  assert.deepEqual(keys, Array(3).fill('fixture:operator-intent'));
  assert.equal(first.$meta.replayed, true);
  assert.equal(second.$meta.replayed, true);
});

test('Docs key boundaries are validated before transport, independently of Suite limits', async t => {
  const keys = [];
  const client = await fixture(t, (req, res) => {
    keys.push(req.headers['idempotency-key']);
    answer(res, 200, { id: 'fixture_receipt' });
  });
  const valid = ['x', 'x'.repeat(129), 'x'.repeat(255), 'fixture with space'];
  for (const idempotencyKey of valid) await client.redeliver('s', 'd', { idempotencyKey });
  for (const idempotencyKey of ['', 'x'.repeat(256), 'line\n', 'line\r', '\t', '\x7f', '非ASCII', 1, false, null, []]) {
    assert.throws(() => client.redeliver('s', 'd', { idempotencyKey }),
      error => error instanceof ValidationError && error.field === 'Idempotency-Key');
  }
  assert.deepEqual(keys, valid);
});

test('conflict, permission and unsupported-key errors surface without unkeyed fallback', async t => {
  const cases = [[400, 'idempotency_not_supported'], [400, 'invalid_idempotency_key'],
    [401, 'unauthorized'], [403, 'permission_denied'], [404, 'not_found'], [409, 'idempotency_conflict']];
  const calls = [];
  const client = await fixture(t, (req, res) => {
    calls.push({ path: req.url, key: req.headers['idempotency-key'] });
    const [status, code] = cases[Number(req.url.split('/').at(-2))];
    answer(res, status, { error: { code, message: 'synthetic refusal' } }, { 'retry-after': '0' });
  });
  for (const [index, [status, code]] of cases.entries()) {
    await assert.rejects(() => client.redeliver('s', String(index), { idempotencyKey: 'fixture:stable' }), { status, code });
  }
  assert.equal(calls.length, cases.length);
  assert(calls.every(call => call.key === 'fixture:stable'));
});

test('revocation on retry surfaces instead of returning or repeating a receipt', async t => {
  const keys = [];
  const client = await fixture(t, (req, res) => {
    keys.push(req.headers['idempotency-key']);
    if (keys.length === 1) loseResponse(res);
    else answer(res, 403, { error: { code: 'permission_denied', message: 'fixture access revoked' } });
  });
  await assert.rejects(() => client.redeliver('s', 'd', { idempotencyKey: 'fixture:revoked' }),
    { status: 403, code: 'permission_denied' });
  assert.deepEqual(keys, ['fixture:revoked', 'fixture:revoked']);
});

test('retry count and Retry-After wait limits preserve the same key', async t => {
  const calls = []; const delays = [];
  const responses = { busy: [409, 'idempotency_in_progress', '0'], unavailable: [503, 'unavailable', '0'],
    limited: [429, 'rate_limited', '0'], 'long-wait': [503, 'unavailable', '121'] };
  const client = await fixture(t, (req, res) => {
    const name = req.url.split('/').at(-2);
    calls.push([name, req.headers['idempotency-key']]);
    const [status, code, wait] = responses[name];
    answer(res, status, { error: { code, message: 'synthetic refusal' } }, { 'retry-after': wait });
  }, { sleep: async ms => { delays.push(ms); }, maxRetryAfterMs: 120_000 });
  for (const [name, [status, code]] of Object.entries(responses)) {
    await assert.rejects(() => client.redeliver('s', name, { idempotencyKey: `fixture:${name}` }), { status, code });
  }
  assert.deepEqual(calls, Object.keys(responses).flatMap(name => Array.from(
    { length: name === 'long-wait' ? 1 : 3 }, () => [name, `fixture:${name}`])));
  assert.deepEqual(delays, Array(6).fill(0));
});

for (const maxRetries of [0, 2]) {
  test(`network failure is bounded with maxRetries=${maxRetries}`, async t => {
    const keys = [];
    const client = await fixture(t, (req, res) => {
      keys.push(req.headers['idempotency-key']);
      loseResponse(res);
    }, { maxRetries });
    await assert.rejects(() => client.redeliver('s', 'd', { idempotencyKey: 'fixture:network' }), TransportError);
    assert.deepEqual(keys, Array(maxRetries + 1).fill('fixture:network'));
  });
}
