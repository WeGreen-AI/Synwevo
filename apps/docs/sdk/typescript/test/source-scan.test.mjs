// Synthetic loopback HTTP scan-contract fixtures; no scanner or user data.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { APIError, ApiKey, Client, TransportError, ValidationError, WaitTimeoutError } from '../synwevo-docs.mjs';

function source(status = 'scan_failed', scan = { status: 'error', attempts: 12, cycle_attempts: 6, retry_exhausted: true }, extra = {}) {
  return { id: 'source_fixture', project_id: 'project_fixture', filename: 'fixture.csv', ord: 0,
    size: 12, format: 'csv', family: 'sheet', status, error: 'Synthetic scan failure', notes: ['fixture note'],
    structure: {}, version: 7, representative: false, created_at: 1, updated_at: 2, options: {},
    ocr_pages: [], corrections: {}, sha256: 'a'.repeat(64), scan,
    future_detail: { preserve: ['fixture', 1] }, ...extra };
}

async function fixture(t, respond, options = {}, credentials = new ApiKey('synthetic-fixture')) {
  const server = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    respond(req, res, Buffer.concat(chunks).toString());
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  return new Client(`http://127.0.0.1:${server.address().port}`, credentials,
    { maxRetries: 3, sleep: async () => {}, ...options });
}

function answer(res, status, body, headers = {}) {
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(JSON.stringify(body));
}

test('scan retry encodes version and returns the full 202 source', async t => {
  const calls = [];
  const payload = source('scanning', { status: 'pending', attempts: 12, cycle_attempts: 0, retry_exhausted: false },
    { version: 8, error: null });
  const client = await fixture(t, (req, res, body) => {
    calls.push({ method: req.method, path: req.url, body: JSON.parse(body), key: req.headers['idempotency-key'] });
    answer(res, 202, payload, { 'x-request-id': 'scan-fixture-request' });
  });
  const result = await client.retrySourceScan('source /fixture', { version: 7 });
  assert.deepEqual(calls, [{ method: 'POST', path: '/api/v1/files/source%20%2Ffixture/scan/retry', body: { version: 7 }, key: undefined }]);
  assert.deepEqual(result, payload);
  assert.equal(result.$meta.status, 202);
  assert.equal(result.$meta.requestId, 'scan-fixture-request');
});

test('scan retry validates a positive safe integer version before sending', async t => {
  const calls = [];
  const client = await fixture(t, (req, res) => { calls.push(req.url); answer(res, 202, source()); });
  for (const version of [undefined, null, true, false, 0, -1, 1.5, '7', NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, [], {}]) {
    assert.throws(() => client.retrySourceScan('fixture', { version }), error => error instanceof ValidationError && error.field === 'version');
  }
  assert.throws(() => client.retrySourceScan('fixture'), ValidationError);
  assert.deepEqual(calls, []);
});

for (const [status, code] of [[429, 'rate_limited'], [401, 'unauthorized'], [503, 'unavailable'],
  [409, 'source_changed'], [409, 'scan_not_retryable']]) {
  test(`scan retry sends once for ${status} ${code}`, async t => {
    const calls = []; const sleeps = []; let invalidations = 0;
    const credentials = { refreshable: true, authorization: async () => 'Bearer synthetic-fixture',
      invalidate() { invalidations++; } };
    const details = { source: source() };
    const client = await fixture(t, (req, res, body) => {
      calls.push({ method: req.method, path: req.url, body: JSON.parse(body) });
      answer(res, status, { error: { code, message: 'Synthetic fault', details } },
        { 'retry-after': '0', 'x-request-id': 'scan-fixture-fault' });
    }, { sleep: async delay => { sleeps.push(delay); } }, credentials);
    await assert.rejects(() => client.retrySourceScan('fixture', { version: 7 }), error => {
      assert(error instanceof APIError);
      assert.equal(error.status, status);
      assert.equal(error.code, code);
      assert.equal(error.requestId, 'scan-fixture-fault');
      assert.deepEqual(error.details, details);
      return true;
    });
    assert.deepEqual(calls, [{ method: 'POST', path: '/api/v1/files/fixture/scan/retry', body: { version: 7 } }]);
    assert.deepEqual(sleeps, []);
    assert.equal(invalidations, 0);
  });
}

for (const partial of [false, true]) {
  test(`scan retry never repeats a lost ${partial ? 'partial' : 'whole'} response`, async t => {
    const calls = []; const sleeps = [];
    const client = await fixture(t, (req, res) => {
      calls.push(req.method);
      if (partial) {
        res.writeHead(202, { 'content-length': '1000' });
        res.flushHeaders();
        res.write('{"id":');
        setImmediate(() => res.destroy());
      } else res.destroy();
    }, { sleep: async delay => { sleeps.push(delay); } });
    await assert.rejects(() => client.retrySourceScan('fixture', { version: 7 }), TransportError);
    assert.deepEqual(calls, ['POST']);
    assert.deepEqual(sleeps, []);
  });
}

for (const [scan, code] of [[source().scan, 'scan_retry_exhausted'],
  [{ status: 'too_large' }, 'scan_too_large'], [{ status: 'too_large', retry_exhausted: true }, 'scan_too_large']]) {
  test(`terminal scan wait rejects on its first read with ${code} and complete details (${JSON.stringify(scan)})`, async t => {
    const calls = []; const sleeps = [];
    const payload = source('scan_failed', scan);
    const client = await fixture(t, (req, res) => {
      calls.push({ method: req.method, path: req.url });
      answer(res, 200, calls.length === 1 ? payload : source('ready'));
    }, { sleep: async delay => { sleeps.push(delay); } });
    await assert.rejects(() => client.waitForFile('fixture', { timeoutMs: 60_000 }), error => {
      assert(error instanceof APIError);
      assert.equal(error.code, code);
      assert.deepEqual(error.details, payload);
      assert.deepEqual(error.toJSON().details, payload);
      assert(error.message.includes(payload.error));
      return true;
    });
    assert.deepEqual(calls, [{ method: 'GET', path: '/api/v1/files/fixture' }]);
    assert.deepEqual(sleeps, []);
  });
}

test('other pending scan states keep polling without starting a cycle', async t => {
  const states = [source('scanning', { status: 'pending' }),
    source('scan_failed', { status: 'error', retry_exhausted: false }),
    source('scan_failed', { status: 'error', attempts: 99 }),
    source('scan_failed', { status: 'not_configured' }),
    source('scan_failed', { status: 'error', retry_exhausted: 1 }),
    source('scan_failed', { status: 'error', retry_exhausted: 'true' }),
    source('scan_failed', undefined, { scan: undefined }),
    source('parsing', { status: 'clean' }), source('ready', { status: 'clean' }, { error: null })];
  const calls = []; const sleeps = [];
  const client = await fixture(t, (req, res) => {
    calls.push({ method: req.method, path: req.url });
    answer(res, 200, states[Math.min(calls.length - 1, states.length - 1)]);
  }, { sleep: async delay => { sleeps.push(delay); } });
  assert.deepEqual(await client.waitForFile('fixture', { timeoutMs: 60_000 }), states.at(-1));
  assert.deepEqual(calls, states.map(() => ({ method: 'GET', path: '/api/v1/files/fixture' })));
  assert.equal(sleeps.length, states.length - 1);
});

test('pending scan still times out with the full last source', async t => {
  const payload = source('scan_failed', { status: 'error', retry_exhausted: false });
  const client = await fixture(t, (req, res) => answer(res, 200, payload));
  await assert.rejects(() => client.waitForFile('fixture', { timeoutMs: 10_000, pollMs: 20_000 }), error => {
    assert(error instanceof WaitTimeoutError);
    assert.deepEqual(error.details, payload);
    return true;
  });
});
