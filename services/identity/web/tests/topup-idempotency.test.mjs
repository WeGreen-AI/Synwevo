/** Isolated submission/storage test doubles: no browser, account, socket or provider. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';

let server, topup, ApiError, ui;
before(async () => {
  server = await createServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
    logLevel: 'error', server: { middlewareMode: true, hmr: false, ws: false, watch: null } });
  topup = await server.ssrLoadModule('/src/pages/account/TopupDialog.tsx');
  ({ ApiError } = await server.ssrLoadModule('@synwevo/auth-client'));
  ui = await server.ssrLoadModule('@synwevo/ui');
});
after(async () => { await server?.close(); });
const context = { environment: 'test', initiator_id: 'u_fixture', payer_kind: 'user', payer_id: 'u_fixture' };
const selection = { package_id: 'cny-10', quoted_amount_minor: 1000 };
const ready = { order_id: 'top_fixture', status: 'pending', creation_status: 'ready',
  payer_kind: 'user', payer_id: 'u_fixture', amount_minor: 1000,
  checkout_url: 'https://checkout.stripe.com/c/pay/cs_fixture' };
const verifying = { ...ready, creation_status: 'verifying', checkout_url: null };
function storageFixture() {
  const entries = new Map();
  return { entries, getItem: key => entries.get(key) ?? null, setItem: (key, value) => entries.set(key, value),
    removeItem: key => entries.delete(key) };
}

test('lost response, reload and changed options reuse the original key and exact pinned request', async () => {
  const storage = storageFixture(), requests = [];
  let attempt = new topup.TopupAttempt(context, storage);
  const lost = async (path, body) => {
    assert.equal(path, '/api/auth/wallet/topups');
    assert.ok(storage.getItem(attempt.storageKey), 'intent must be durable before dispatch');
    requests.push(body);
    throw new TypeError('fixture: response lost after provider accepted it');
  };
  await assert.rejects(attempt.submit(selection, { challenge_token: 'fixture-once-only-token' }, lost));
  const firstKey = attempt.intent.idempotency_key;
  assert.throws(() => attempt.startNew(), /topup_unresolved/);
  attempt = new topup.TopupAttempt(context, storage); // page refresh, with no checkout address
  await assert.rejects(attempt.submit({ package_id: 'cny-50', quoted_amount_minor: 5000 }, {}, lost), /topup_unresolved/);
  const reply = await attempt.submit(undefined, {}, async (_path, body) => { requests.push(body); return ready; });
  assert.equal(attempt.intent.idempotency_key, firstKey);
  const { challenge_token, ...first } = requests[0];
  assert.deepEqual(requests[1], first);
  assert.equal(reply.order_id, 'top_fixture');
  assert.equal(requests.length, 2);
  const saved = storage.getItem(attempt.storageKey);
  assert.doesNotMatch(saved, /checkout_url|checkout\.stripe|challenge|fixture-once-only-token/);
  assert.equal(JSON.parse(saved).order_id, 'top_fixture');
  assert.deepEqual(topup.topupRequest(attempt.intent), { ...context, package_id: 'cny-10', idempotency_key: firstKey });
});

test('double click shares one in-flight request and persistence failure prevents all dispatch', async () => {
  const storage = storageFixture();
  const attempt = new topup.TopupAttempt(context, storage);
  let finish, calls = 0;
  const sender = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  const first = attempt.submit(selection, {}, sender);
  const second = attempt.submit(selection, {}, sender);
  assert.equal(first, second);
  assert.equal(calls, 1);
  finish(verifying);
  await first;
  assert.throws(() => attempt.startNew(), /topup_unresolved/);
  const broken = new topup.TopupAttempt(context, { ...storageFixture(), setItem() { throw new Error('fixture quota'); } });
  await assert.rejects(broken.submit(selection, {}, sender), /topup_storage/);
  assert.equal(calls, 1);
});

test('actor, environment and payer have separate storage and fixed outbound context', async () => {
  const storage = storageFixture();
  const scopes = [context, { ...context, initiator_id: 'u_other', payer_id: 'u_other' },
    { ...context, environment: 'live' }, { ...context, payer_kind: 'organization', payer_id: 'org_fixture' }];
  const keys = [];
  for (const scope of scopes) {
    const attempt = new topup.TopupAttempt(scope, storage);
    assert.equal(attempt.intent, null);
    await attempt.submit(selection, {}, async (_path, body) => {
      for (const [key, value] of Object.entries(scope)) assert.equal(body[key], value);
      return { ...verifying, payer_kind: scope.payer_kind, payer_id: scope.payer_id };
    });
    keys.push(attempt.storageKey);
  }
  assert.equal(new Set(keys).size, scopes.length);
  assert.equal(storage.entries.size, scopes.length);
});

test('unknown body, gateway refusal and server failure keep original intent; no secret becomes UI text', async () => {
  const errors = [new TypeError('fixture-sensitive-upstream'), new ApiError(503, 'server_error', 'fixture-sensitive-upstream'),
    new ApiError(403, 'payer_forbidden', 'fixture-sensitive-upstream', {}, true),
    new ApiError(403, 'payer_forbidden', 'fixture-sensitive-upstream'),
    new ApiError(401, 'unauthorized', 'fixture-sensitive-upstream'),
    new ApiError(403, 'email_verification_required', 'fixture-sensitive-upstream')];
  for (const error of errors) {
    const storage = storageFixture(), attempt = new topup.TopupAttempt(context, storage);
    await assert.rejects(attempt.submit(selection, {}, async () => { throw error; }));
    const restored = new topup.TopupAttempt(context, storage);
    assert.equal(restored.intent.idempotency_key, attempt.intent.idempotency_key);
    assert.throws(() => restored.startNew(), /topup_unresolved/);
    assert.doesNotMatch(topup.topupErrorText(error, key => ui.translate('en', key)), /fixture-sensitive-upstream/);
  }
  for (const raw of [null, '<html>fixture-sensitive-upstream</html>', { secret: 'fixture-sensitive-upstream' },
    { ...ready, payer_id: 'u_other' }, { ...ready, order_id: '' }, { ...ready, status: 'paid' }]) {
    const attempt = new topup.TopupAttempt(context, storageFixture());
    await assert.rejects(attempt.submit(selection, {}, async () => raw), /topup_response_unknown/);
    assert.ok(attempt.intent);
  }
});

test('a known first pre-admission refusal allows edits; the same refusal after an unknown response does not', async () => {
  const storage = storageFixture(), attempt = new topup.TopupAttempt(context, storage);
  const refusal = new ApiError(400, 'challenge_required', 'fixture');
  await assert.rejects(attempt.submit(selection, {}, async () => { throw refusal; }));
  assert.equal(attempt.intent, null);
  assert.equal(storage.entries.size, 0);
  await assert.rejects(attempt.submit(selection, {}, async () => { throw new TypeError('fixture lost response'); }));
  const key = attempt.intent.idempotency_key;
  const restored = new topup.TopupAttempt(context, storage);
  await assert.rejects(restored.submit(undefined, {}, async () => { throw refusal; }));
  assert.equal(restored.intent.idempotency_key, key);
  assert.throws(() => restored.startNew(), /topup_unresolved/);
});

test('only an acknowledged terminal order unlocks a new intent, and a changed amount gets a fresh key', async () => {
  for (const status of ['paid', 'failed', 'expired']) {
    const storage = storageFixture(), attempt = new topup.TopupAttempt(context, storage);
    await attempt.submit(selection, {}, async () => ({ ...ready, status, creation_status: 'resolved', checkout_url: null }));
    const old = attempt.intent.idempotency_key;
    assert.equal(topup.topupSettled(attempt.reply), true);
    attempt.startNew();
    await attempt.submit({ amount_minor: 1500, quoted_amount_minor: 1500 }, {}, async (_path, body) => {
      assert.equal(body.amount_minor, 1500); assert.equal(body.package_id, undefined);
      assert.notEqual(body.idempotency_key, old);
      return { ...verifying, order_id: 'top_fixture_next', amount_minor: 1500 };
    });
  }
  for (const creation_status of ['verifying', 'blocked', 'ready']) {
    const attempt = new topup.TopupAttempt(context, storageFixture());
    await attempt.submit(selection, {}, async () => ({ ...ready, creation_status }));
    assert.equal(topup.topupSettled(attempt.reply), false);
    assert.throws(() => attempt.startNew(), /topup_unresolved/);
  }
});

test('corrupt or inaccessible storage is never treated as permission to replace the previous intent', () => {
  const storage = storageFixture(), key = topup.topupStorageKey(context);
  for (const raw of ['{truncated', 'null', '{"version":1}', JSON.stringify({ ...context, ...selection,
    version: 1, idempotency_key: 'fixture-key', checkout_url: 'https://checkout.stripe.com/secret' })]) {
    storage.setItem(key, raw);
    assert.throws(() => new topup.TopupAttempt(context, storage), /topup_storage/);
    assert.equal(storage.getItem(key), raw, 'do not discard recovery evidence');
  }
  assert.throws(() => new topup.TopupAttempt(context, { getItem() { throw new Error('fixture denied'); } }), /topup_storage/);
});

test('recovery, blocked and terminal notices are available in all supported languages', () => {
  for (const reply of [null, verifying, { ...ready, creation_status: 'blocked' },
    ...['paid', 'expired', 'failed'].map(status => ({ ...ready, status, creation_status: 'resolved' }))]) {
    const message = topup.topupNotice(reply);
    assert.ok(ui.hasMessage(message));
    assert.doesNotMatch(ui.translate('en', message), /[\u3400-\u9fff]/);
    assert.ok(ui.translate('zh-Hant', message));
  }
});
