/** Isolated submission/storage test doubles: no browser, account, socket or provider. */
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { testServer } from './_vite.mjs';

let server, topup, ApiError, ui;
before(async () => {
  server = await testServer({ root: fileURLToPath(new URL('../', import.meta.url)), configFile: false,
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

// ------------------------------------------------------------------ definite refusals, unknown outcomes, restored orders
const refused = reason => ({ ...ready, status: 'failed', creation_status: 'resolved', checkout_url: null, failure_reason: reason });
const han = /[㐀-鿿]/;

test('a definite provider refusal frees the choice and says why in all three languages, always that nothing was charged', async () => {
  for (const reason of ['payment_method_not_enabled', 'provider_configuration', 'provider_rejected']) {
    const attempt = new topup.TopupAttempt(context, storageFixture());
    const reply = await attempt.submit(selection, {}, async () => refused(reason));
    assert.equal(topup.checkoutTarget(reply), null, 'a refused order is never followed anywhere');
    assert.equal(topup.topupSettled(reply), true);
    const message = topup.topupNotice(reply);
    assert.ok(ui.hasMessage(message), message);
    assert.match(message, /支付服务拒绝创建付款页面.*没有扣款/);
    assert.match(ui.translate('en', message), /refused to create the payment page.*Nothing was charged/);
    assert.doesNotMatch(ui.translate('en', message), han);
    assert.match(ui.translate('zh-Hant', message), /沒有扣款/);
    attempt.startNew();
    assert.equal(attempt.intent, null, 'the person can choose again at once');
  }
  assert.match(topup.topupNotice(refused('payment_method_not_enabled')), /所选支付方式尚未在支付服务商处开通/);
  assert.match(ui.translate('en', topup.topupNotice(refused('provider_configuration'))), /payment configuration/);
  for (const reason of [undefined, null, 'future_reason', 'toString'])
    assert.equal(topup.topupNotice(refused(reason)), '原充值订单已失败，可以重新选择金额。', String(reason));
});

test('an unknown creation outcome is worded honestly: not redirected, no claim about money, the same order is checked again', () => {
  const unknown = topup.topupNotice(verifying);
  assert.notEqual(unknown, topup.topupNotice(null), 'a verifying answer differs from no answer at all');
  assert.match(unknown, /^暂时无法确认付款页面是否已创建，因此没有跳转/);
  // UX-5: like the return page and the organization panel, the verifying wording never says no money was taken.
  assert.doesNotMatch(unknown, /扣款/);
  for (const locale of ['en', 'zh-Hant']) assert.ok(ui.hasMessage(unknown) && ui.translate(locale, unknown) !== unknown);
  const en = ui.translate('en', unknown);
  assert.match(en, /cannot confirm yet whether the payment page was created, so you were not redirected/);
  assert.doesNotMatch(en, /charged/);
  assert.doesNotMatch(ui.translate('zh-Hant', unknown), /扣款/);
  assert.doesNotMatch(en, han);
  assert.doesNotMatch(en, /seconds|minutes/, 'no time promise for an unknown outcome');
});

test('only a ready reply with a Stripe checkout address is followed; a verifying reply never dereferences its empty address', () => {
  const organization = { ...ready, payer_kind: 'organization', payer_id: 'org_fixture' };
  for (const reply of [ready, organization]) assert.equal(topup.checkoutTarget(reply), 'https://checkout.stripe.com/c/pay/cs_fixture');
  for (const reply of [verifying, { ...organization, creation_status: 'verifying', checkout_url: null },
    { ...ready, creation_status: 'blocked', checkout_url: null }, refused('provider_rejected')])
    assert.equal(topup.checkoutTarget(reply), null);
  for (const checkout_url of [null, 'https://evil.example/c/pay', 'http://checkout.stripe.com/c/pay', 'javascript:alert(1)'])
    assert.throws(() => topup.checkoutTarget({ ...ready, checkout_url }), /checkout_address/, String(checkout_url));
});

const orderStatus = (overrides = {}) => ({ order_id: 'top_fixture', status: 'pending', creation_status: 'verifying', amount_minor: 1000,
  currency: 'CNY', payer_kind: 'user', payer_id: 'u_fixture', created_at: 1_790_000_000, expires_at: 1_790_003_600, ...overrides });
async function restoredAttempt(storage = storageFixture(), scope = context) {
  const first = new topup.TopupAttempt(scope, storage);
  await first.submit(selection, {}, async () => ({ ...verifying, payer_kind: scope.payer_kind, payer_id: scope.payer_id }));
  return new topup.TopupAttempt(scope, storage);   // the dialog reopened in the same tab
}

test('a restored intent whose order is final is released on opening, and a new choice gets a fresh key', async () => {
  for (const [status, extra] of [['paid', { credited_at: 1_790_000_100 }], ['failed', { failure_reason: 'payment_method_not_enabled' }],
    ['expired', {}]]) {
    const storage = storageFixture(), attempt = await restoredAttempt(storage);
    const oldKey = attempt.intent.idempotency_key, reads = [];
    const signal = new AbortController().signal;
    const out = await topup.readRestoredOrder(attempt, signal, async (path, given) => {
      reads.push(path); assert.equal(given, signal);
      return orderStatus({ status, creation_status: 'resolved', ...extra });
    });
    assert.deepEqual(reads, ['/api/auth/wallet/topups/top_fixture'], 'a read of the existing order, never a create');
    assert.equal(out.released, true, status);
    assert.equal(out.status.status, status);
    assert.equal(attempt.intent, null);
    assert.equal(storage.entries.size, 0, 'the tab no longer pins the old request');
    await attempt.submit({ package_id: 'cny-50', quoted_amount_minor: 5000 }, {}, async (_path, body) => {
      assert.notEqual(body.idempotency_key, oldKey); assert.equal(body.package_id, 'cny-50');
      return { ...verifying, order_id: 'top_fixture_next', amount_minor: 5000 };
    });
  }
  // The organization's dialog recovers the same way, under its own storage key.
  const storage = storageFixture(), scope = { ...context, payer_kind: 'organization', payer_id: 'org_fixture' };
  const attempt = await restoredAttempt(storage, scope);
  const out = await topup.readRestoredOrder(attempt, undefined, async () =>
    orderStatus({ status: 'expired', creation_status: 'resolved', payer_kind: 'organization', payer_id: 'org_fixture' }));
  assert.equal(out.released, true);
  assert.equal(storage.entries.size, 0);
});

test('an order that may still complete, or an answer not about it, keeps the intent: no second order can race the first', async () => {
  const keeps = [
    orderStatus(), orderStatus({ creation_status: 'resolved' }),   // verifying, or a checkout still open
    orderStatus({ status: 'paid', creation_status: 'verifying' }),
    orderStatus({ status: 'failed', creation_status: 'resolved', order_id: 'top_other' }),
    orderStatus({ status: 'failed', creation_status: 'resolved', payer_id: 'u_other' }),
    orderStatus({ status: 'failed', creation_status: 'resolved', payer_kind: 'organization' }),
    orderStatus({ status: 'refunded', creation_status: 'resolved' }), orderStatus({ status: 'expired' , creation_status: undefined }),
    null, '<html>fixture</html>', { secret: 'fixture' },
  ];
  for (const answer of keeps) {
    const storage = storageFixture(), attempt = await restoredAttempt(storage);
    const key = attempt.intent.idempotency_key;
    const out = await topup.readRestoredOrder(attempt, undefined, async () => answer);
    assert.equal(out.released, false, JSON.stringify(answer));
    assert.equal(attempt.intent.idempotency_key, key);
    assert.equal(storage.entries.size, 1);
    assert.throws(() => attempt.startNew(), /topup_unresolved/);
  }
  const lost = await restoredAttempt();
  await assert.rejects(topup.readRestoredOrder(lost, undefined, async () => { throw new TypeError('fixture network'); }));
  assert.ok(lost.intent, 'an unreadable status keeps the original request');
  // No known order id (the first response was lost): nothing to read; only a replay can tell.
  const storage = storageFixture(), unsent = new topup.TopupAttempt(context, storage);
  await assert.rejects(unsent.submit(selection, {}, async () => { throw new TypeError('fixture lost response'); }));
  const reopened = new topup.TopupAttempt(context, storage);
  let reads = 0;
  assert.deepEqual(await topup.readRestoredOrder(reopened, undefined, async () => { reads++; }), { status: null, released: false });
  assert.equal(reads, 0);
  // A replay in flight wins: a status read cannot release the intent underneath it.
  const busy = await restoredAttempt();
  let finish; const flight = busy.submit(undefined, {}, () => new Promise(resolve => { finish = resolve; }));
  assert.equal(busy.releaseFinal(orderStatus({ status: 'failed', creation_status: 'resolved' })), false);
  finish(verifying); await flight;
  assert.ok(busy.intent);
  // A record that cannot be removed is reported, never silently replaced.
  const stuck = await restoredAttempt({ ...storageFixture(), removeItem() { throw new Error('fixture denied'); } });
  await assert.rejects(topup.readRestoredOrder(stuck, undefined, async () => orderStatus({ status: 'expired', creation_status: 'resolved' })), /topup_storage/);
});

test('restored and released order notices exist in all three languages', () => {
  const lines = [topup.restoredNotice(orderStatus()), topup.restoredNotice(orderStatus({ creation_status: 'resolved' })),
    ...['paid', 'expired', 'failed'].flatMap(status => topup.releasedNotice(orderStatus({ status, creation_status: 'resolved' }))),
    ...['paid', 'expired', 'failed'].flatMap(status => topup.releasedNotice(orderStatus({ status, creation_status: 'resolved' }), false)),
    ...topup.releasedNotice(orderStatus({ status: 'failed', creation_status: 'resolved', failure_reason: 'provider_configuration' }))];
  assert.equal(topup.restoredNotice(null), null);
  assert.equal(topup.restoredNotice(orderStatus({ creation_status: 'blocked' })), null, 'only a replay can say what continuing does');
  assert.equal(topup.restoredNotice(orderStatus()), topup.topupNotice(verifying));
  assert.deepEqual(topup.releasedNotice(orderStatus({ status: 'failed', creation_status: 'resolved', failure_reason: 'provider_configuration' }))[1],
    '可以选择新的金额。');
  for (const line of lines) {
    assert.ok(ui.hasMessage(line), line);
    assert.doesNotMatch(ui.translate('en', line), han, line);
    assert.notEqual(ui.translate('zh-Hant', line), undefined);
  }
});

test('a restored pending order is never promised a usable payment page: continuing checks it first (UX-3)', () => {
  // The status read cannot see that top-ups closed or the keys changed (the replay then answers
  // blocked), nor that the person already paid; the notice only says what continuing does.
  const pending = topup.restoredNotice(orderStatus({ creation_status: 'resolved' }));
  assert.equal(pending, '原充值订单仍在等待付款；继续时会核实付款页面是否仍可使用。在它完成或过期之前，不能更改金额或创建另一笔充值。');
  for (const [locale, promise] of [['zh-Hans', /仍可使用。继续会回到|付款页面仍可使用/], ['zh-Hant', /付款頁面仍可使用。繼續會回到/],
    ['en', /still open|takes you back/]]) {
    const text = locale === 'zh-Hans' ? pending : ui.translate(locale, pending);
    assert.doesNotMatch(text, promise, `${locale}: ${text}`);
  }
  assert.match(ui.translate('en', pending), /still awaiting payment\. Continuing first checks whether its payment page can still be used/);
  assert.match(ui.translate('zh-Hant', pending), /仍在等待付款；繼續時會核實/);
  // Consistent with the closed notice beside it: nothing here creates an order.
  assert.doesNotMatch(pending, /新的|重新选择/);
});

test('a released order invites a new amount only where top-ups are open (UX-4)', () => {
  const final = (status, extra = {}) => orderStatus({ status, creation_status: 'resolved', ...extra });
  const cases = [
    [final('paid'), '上一笔充值已到账。'],
    [final('expired'), '上一笔充值订单已过期。'],
    [final('failed'), '上一笔充值订单已失败，没有扣款。'],
    [final('failed', { failure_reason: 'payment_method_not_enabled' }), topup.topupNotice(refused('payment_method_not_enabled'))],
  ];
  for (const [status, outcome] of cases) {
    assert.deepEqual(topup.releasedNotice(status), [outcome, '可以选择新的金额。'], status.status);
    assert.deepEqual(topup.releasedNotice(status, true), [outcome, '可以选择新的金额。']);
    assert.deepEqual(topup.releasedNotice(status, false), [outcome], 'closed, frozen or not permitted: only the outcome');
    for (const line of topup.releasedNotice(status, false)) {
      assert.doesNotMatch(ui.translate('en', line), /choose a new amount/i);
      assert.doesNotMatch(ui.translate('zh-Hant', line), /選擇新的金額/);
    }
  }
  // The expired outcome makes no claim that nothing was charged: a late signed payment still credits it.
  for (const locale of ['zh-Hans', 'zh-Hant', 'en']) {
    const line = locale === 'zh-Hans' ? '上一笔充值订单已过期。' : ui.translate(locale, '上一笔充值订单已过期。');
    assert.doesNotMatch(line, /扣款|charged/);
  }
});

// The mounted dialog (closed, frozen, verifying, released) is covered by topup-dialog-restored.test.mjs.
