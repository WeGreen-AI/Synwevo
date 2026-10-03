// SuiteClient and UserToken against an in-process fetch double (not the real service): which
// variables are read, what is retried and with which key, and how a rotating refresh token is kept.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiKey, SuiteClient, UserToken, AuthenticationError, GoneError, PermissionDeniedError, ValidationError,
  WaitTimeoutError, SUITE_RESOURCE } from '../synwevo-docs.mjs';

const json = (status, body, headers = {}) => new Response(JSON.stringify(body),
  { status, headers: { 'Content-Type': 'application/json', ...headers } });
const quiet = { sleep: async () => {}, random: () => 0 };

test('suite environment is its own and only a delegation is accepted', async () => {
  const env = { SYNWEVO_BASE_URL: 'https://docs.invalid', SYNWEVO_API_KEY: 'docs-only-fixture',
    SYNWEVO_WORKSPACE_CLIENT_ID: 'directory-fixture', SYNWEVO_WORKSPACE_CLIENT_SECRET: 'synthetic' };
  await assert.rejects(SuiteClient.fromEnv(env), ValidationError);
  assert.throws(() => new SuiteClient(undefined, new ApiKey('dw_fixture_synthetic')), ValidationError);
  await assert.rejects(SuiteClient.fromEnv({ SYNWEVO_SUITE_REFRESH_TOKEN: 'synthetic-refresh' }), ValidationError);
  const seen = [];
  const fetch = async (url, init) => {
    seen.push([url, init.headers.Authorization ?? String(init.body)]);
    if (url.endsWith('/oidc/token')) return json(200, { access_token: 'access-1', refresh_token: 'refresh-2', expires_in: 900 });
    return json(200, { kind: 'delegated', scopes: ['forms.read'] });
  };
  const client = await SuiteClient.fromEnv({ ...env, SYNWEVO_SUITE_REFRESH_TOKEN: 'synthetic-refresh',
    SYNWEVO_SUITE_CLIENT_ID: 'crm-sync' }, { fetch });
  assert.equal(client.baseUrl, 'https://workplace.synwevo.com');
  assert.equal(typeof client.createProject, 'undefined');
  assert.deepEqual((await client.whoami()).scopes, ['forms.read']);
  assert.equal(seen[0][0], 'https://accounts.synwevo.com/oidc/token');
  assert.equal(new URLSearchParams(seen[0][1]).get('resource'), SUITE_RESOURCE);
  assert.deepEqual(seen[1], ['https://workplace.synwevo.com/api/suite/v1/whoami', 'Bearer access-1']);
  const token = new UserToken({ accessToken: 'synthetic-access', refreshToken: 'synthetic-refresh',
    tokenUrl: 'https://accounts.invalid/oidc/token', clientId: 'crm-sync', clientSecret: 'synthetic-secret' });
  const printed = JSON.stringify(token) + JSON.stringify({ ...token });
  for (const secret of ['synthetic-access', 'synthetic-refresh', 'synthetic-secret']) assert.ok(!printed.includes(secret));
});

test('every attempt of one invoke carries the same key and replays are reported', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    calls.push([init.method, new URL(url).pathname, init.headers['Idempotency-Key'], JSON.parse(init.body)]);
    if (calls.length % 2) return json(503, { error: { code: 'unavailable', message: 'synthetic fixture' } }, { 'Retry-After': '0' });
    return json(200, { run_id: 'run_1', operation: 'tables.record.create', status: 'succeeded', output: { id: 'rec_1' },
      replayed: calls.length > 2, workspace_id: 'w_1' });
  };
  const client = new SuiteClient('https://workplace.invalid/', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  const first = await client.invoke('tables.record.create', { table_id: 't 1', cells: {} });
  const mine = await client.invoke('tables.record.create', { table_id: 't 1', cells: {} },
    { idempotencyKey: 'order-42', correlationId: 'sync-7' });
  assert.deepEqual(calls.map((c) => c.slice(0, 2)), Array(4).fill(['POST', '/api/suite/v1/ops/tables.record.create']));
  assert.ok(calls[0][2]);
  assert.equal(calls[0][2], calls[1][2]);
  assert.deepEqual([calls[2][2], calls[3][2]], ['order-42', 'order-42']);
  assert.notEqual(calls[0][2], calls[2][2]);
  assert.deepEqual(calls[0][3], { input: { table_id: 't 1', cells: {} } });
  assert.equal(calls[2][3].correlation_id, 'sync-7');
  assert.deepEqual([first.output, first.$meta.replayed, mine.$meta.replayed], [{ id: 'rec_1' }, false, true]);
});

test('malformed operations, keys and inputs are refused before sending', async () => {
  let sent = 0;
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }),
    { fetch: async () => { sent += 1; return json(200, {}); } });
  for (const bad of ['forms', 'Forms.form.list', 'forms.form.list/../../x', 'forms..list', '', null]) {
    await assert.rejects(client.invoke(bad), ValidationError);
  }
  for (const idempotencyKey of ['with space', 'x'.repeat(129), '', '换行']) {
    await assert.rejects(client.invoke('forms.form.list', {}, { idempotencyKey }), ValidationError);
  }
  await assert.rejects(client.invoke('forms.form.list', ['not', 'an', 'object']), ValidationError);
  await assert.rejects(client.invoke('forms.form.list', null), ValidationError);
  assert.throws(() => client.run(''), ValidationError);
  assert.equal(sent, 0);
});

test('a refusal is a typed error with the request id and details', async () => {
  const fetch = async () => json(403, { error: { code: 'insufficient_scope', message: 'synthetic', request_id: 'req-9',
    details: { required_scope: 'forms.write' } } }, { 'X-Request-ID': 'req-9' });
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  await assert.rejects(client.invoke('forms.form.create', { title: 'x' }), (e) => e instanceof PermissionDeniedError
    && e.code === 'insufficient_scope' && e.requestId === 'req-9' && e.details.required_scope === 'forms.write');
});

test('a refused access token is replaced once and the successor is handed over before use', async () => {
  const seen = [];
  const stored = [];
  const fetch = async (url, init) => {
    if (url.endsWith('/oidc/token')) {
      const form = Object.fromEntries(new URLSearchParams(String(init.body)));
      seen.push(['token', form]);
      if (form.refresh_token !== 'refresh-1') return json(400, { error: 'invalid_grant', error_description: 'spent' });
      return json(200, { access_token: 'access-2', refresh_token: 'refresh-2', token_type: 'Bearer', expires_in: 900, scope: 'forms.read' });
    }
    seen.push(['api', init.headers.Authorization, stored.length]);
    if (init.headers.Authorization !== 'Bearer access-2') return json(401, { error: { code: 'unauthorized', message: 'synthetic' } });
    return json(200, { kind: 'delegated', scopes: ['forms.read'] });
  };
  const token = new UserToken({ accessToken: 'access-1', refreshToken: 'refresh-1', tokenUrl: 'https://accounts.invalid/oidc/token',
    clientId: 'crm-sync', clientSecret: 'synthetic-secret', resource: SUITE_RESOURCE, fetch,
    onRotate: async (tokens) => { stored.push(tokens); } });
  const client = new SuiteClient('https://workplace.invalid', token, { fetch, ...quiet });
  const [a, b] = await Promise.all([client.whoami(), client.whoami()]);     // concurrent callers share one exchange
  assert.deepEqual([a.scopes, b.scopes], [['forms.read'], ['forms.read']]);
  await client.whoami();
  assert.equal(seen.filter(([kind]) => kind === 'token').length, 1);
  assert.deepEqual(seen.find(([kind]) => kind === 'token')[1], { grant_type: 'refresh_token', refresh_token: 'refresh-1',
    client_id: 'crm-sync', client_secret: 'synthetic-secret', resource: SUITE_RESOURCE });
  assert.equal(stored.length, 1);
  assert.deepEqual([stored[0].accessToken, stored[0].refreshToken, stored[0].scope], ['access-2', 'refresh-2', 'forms.read']);
  // the new access token was not presented before the successor had been stored
  assert.ok(seen.filter((s) => s[0] === 'api' && s[1] === 'Bearer access-2').every((s) => s[2] === 1));
});

test('a refused refresh token is not presented again', async () => {
  let exchanges = 0;
  const fetch = async (url) => {
    if (url.endsWith('/oidc/token')) { exchanges += 1; return json(400, { error: 'invalid_grant', error_description: 'synthetic' }); }
    return json(401, { error: { code: 'unauthorized', message: 'synthetic' } });
  };
  const token = new UserToken({ accessToken: 'access-1', refreshToken: 'spent', tokenUrl: 'https://accounts.invalid/oidc/token',
    clientId: 'crm-sync', fetch });
  const client = new SuiteClient('https://workplace.invalid', token, { fetch, ...quiet });
  await assert.rejects(client.whoami(), AuthenticationError);
  await assert.rejects(client.whoami(), AuthenticationError);
  assert.equal(exchanges, 1);
});

test('the event feed is followed page by page with the service\'s cursor', async () => {
  const asked = [];
  const naps = [];
  const pages = [
    { workspace_id: 'w_1', items: [{ id: 'evt_1' }, { id: 'evt_2' }], next_cursor: 's1.A', has_more: true },
    { workspace_id: 'w_1', items: [], next_cursor: 's1.B', has_more: true },          // stopped part-way
    { workspace_id: 'w_1', items: [{ id: 'evt_3' }], next_cursor: 's1.C', has_more: false },
    { workspace_id: 'w_1', items: [{ id: 'evt_4' }], next_cursor: 's1.D', has_more: false }];
  const fetch = async (url, init) => {
    const u = new URL(url);
    asked.push([init.method, u.pathname, Object.fromEntries(u.searchParams)]);
    if (asked.length > pages.length) {
      return json(410, { error: { code: 'cursor_expired', message: 'synthetic fixture', request_id: 'req_gone',
        field: 'cursor', details: { retention_seconds: 2592000 } } });
    }
    return json(200, pages[asked.length - 1]);
  };
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }),
    { fetch, sleep: async (ms) => { naps.push(ms); }, random: () => 0 });
  const seen = [];
  for await (const e of client.events({ types: ['tables.record.changed', 'forms.submission.created'], start: 'now', pageSize: 2 })) seen.push(e.id);
  const follow = client.events({ cursor: 's1.C', follow: true, intervalMs: 7500 });
  const later = (await follow.next()).value;
  await assert.rejects(follow.next(), (e) => e instanceof GoneError && e.code === 'cursor_expired' && e.requestId === 'req_gone');
  for (const wrong of [{ types: 'tables.record.changed' }, { types: [''] }, { start: 'yesterday' }, { start: 'now', cursor: 's1.A' }]) {
    assert.throws(() => client.eventsPage(wrong), ValidationError);
  }
  await assert.rejects(client.events({ follow: true, intervalMs: 0 }).next(), ValidationError);
  assert.deepEqual(seen, ['evt_1', 'evt_2', 'evt_3']);
  assert.equal(later.id, 'evt_4');
  assert.deepEqual(naps, [7500]);                                   // slept only when there was nothing more to read
  assert.deepEqual([...new Set(asked.map(([m, p]) => `${m} ${p}`))], ['GET /api/suite/v1/events']);
  const wanted = 'tables.record.changed,forms.submission.created';
  assert.deepEqual(asked.map(([, , q]) => q), [
    { types: wanted, limit: '2', start: 'now' },                    // start only without a cursor
    { cursor: 's1.A', types: wanted, limit: '2' },
    { cursor: 's1.B', types: wanted, limit: '2' },
    { cursor: 's1.C' }, { cursor: 's1.D' }]);
});

test('a stored cursor that is still null starts where the caller said, not from the oldest event', async () => {
  const asked = [];
  const fetch = async (url) => {
    asked.push(Object.fromEntries(new URL(url).searchParams));
    return json(200, { workspace_id: 'w_1', items: [], next_cursor: 's1.A', has_more: false });
  };
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  for await (const page of client.eventPages({ cursor: null, start: 'now' })) assert.equal(page.next_cursor, 's1.A');
  await client.eventsPage({ cursor: null, start: 'now' });
  assert.deepEqual(asked, [{ start: 'now' }, { start: 'now' }]);
});

test('webhook calls that carry a secret or start a send are sent once, without a key', async () => {
  const calls = [];
  const fetch = async (url, init) => {
    const u = new URL(url);
    calls.push([init.method, u.pathname, init.headers['Idempotency-Key'], init.headers['If-Match'], init.body ? JSON.parse(init.body) : null,
      Object.fromEntries(u.searchParams)]);
    // every first attempt of a write fails: what is tried again shows which calls the client considers repeatable
    const attempts = calls.filter((c) => c[0] === init.method && c[1] === u.pathname).length;
    if (init.method !== 'GET' && attempts === 1) {
      return json(503, { error: { code: 'unavailable', message: 'synthetic fixture' } }, { 'Retry-After': '0' });
    }
    return json(200, { id: 'whsub_1', version: 2, secret: 'whsec_synthetic', items: [], next_cursor: null, has_more: false });
  };
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  const base = '/api/suite/v1/webhooks/subscriptions';
  assert.throws(() => client.createWebhook({ url: 'https://hooks.invalid/x', events: 'tables.*' }), ValidationError);
  assert.throws(() => client.getWebhook(''), ValidationError);
  for (const once of [() => client.createWebhook({ url: 'https://hooks.invalid/x', events: ['tables.*'], description: 'sync' }),
    () => client.rotateWebhookSecret('whsub 1', { overlapS: 600 }), () => client.pingWebhook('whsub 1'),
    () => client.redeliverWebhook('whsub 1', 'whdel/1'), () => client.updateWebhook('whsub 1', { events: ['*'] }, { version: 3 }),
    () => client.deleteWebhook('whsub 1')]) {
    await assert.rejects(once(), (e) => e.status === 503);        // not tried again
  }
  await client.pauseWebhook('whsub 1');                             // these two say the same thing twice safely
  await client.resumeWebhook('whsub 1');
  const page = await client.webhookDeliveries('whsub 1', { status: 'failed', cursor: null, limit: 10 });
  await client.webhookDelivery('whsub 1', 'whdel/1');
  await client.listWebhooks();
  assert.deepEqual(page.items, []);
  assert.deepEqual(calls.map((c) => `${c[0]} ${c[1]}`), [
    `POST ${base}`, `POST ${base}/whsub%201/rotate-secret`, `POST ${base}/whsub%201/ping`,
    `POST ${base}/whsub%201/deliveries/whdel%2F1/redeliver`, `PATCH ${base}/whsub%201`, `DELETE ${base}/whsub%201`,
    `POST ${base}/whsub%201/pause`, `POST ${base}/whsub%201/pause`, `POST ${base}/whsub%201/resume`, `POST ${base}/whsub%201/resume`,
    `GET ${base}/whsub%201/deliveries`, `GET ${base}/whsub%201/deliveries/whdel%2F1`, `GET ${base}`]);
  assert.ok(calls.every((c) => c[2] === undefined), 'no idempotency key on any webhook call');
  assert.deepEqual(calls[0][4], { url: 'https://hooks.invalid/x', events: ['tables.*'], description: 'sync' });
  assert.deepEqual(calls[1][4], { overlap_s: 600 });
  assert.deepEqual([calls[4][3], calls[4][4]], ['"3"', { events: ['*'] }]);
  assert.deepEqual(calls[10][5], { status: 'failed', limit: '10' });
});

test('waiting for a run stops at a terminal state or at the deadline', async () => {
  const states = ['queued', 'running', 'succeeded'];
  const fetch = async () => json(200, { id: 'run_1', status: states.shift() ?? 'running' });
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  assert.equal((await client.wait('run_1', { timeoutMs: 30_000, pollMs: 1 })).status, 'succeeded');
  await assert.rejects(client.wait('run_1', { timeoutMs: 0, pollMs: 1 }),
    (e) => e instanceof WaitTimeoutError && e.details.status === 'running');
});

test('files: the workspace is read once, keyed uploads retry with one key, an unkeyed upload is not retried', async () => {
  const calls = [];
  let failNext = { '/api/suite/v1/dataprep/files': 1, '/api/suite/v1/tables/t%201/imports': 1 };
  const fetch = async (url, init) => {
    const u = new URL(url);
    const body = init.body instanceof FormData ? init.body.get('file') : null;
    calls.push({ method: init.method, path: u.pathname, query: Object.fromEntries(u.searchParams),
      key: init.headers['Idempotency-Key'], file: body ? [body.name, body.type, await body.text()] : null });
    if (u.pathname.endsWith('/whoami')) return json(200, { workspace_id: 'w_1', scopes: [] });
    if (failNext[u.pathname]) { failNext[u.pathname] -= 1; return json(503, { error: { code: 'unavailable', message: 'synthetic' } }, { 'Retry-After': '0' }); }
    if (init.method === 'POST') return json(200, { id: 'f_1', row_count: 2 });
    if (u.pathname.endsWith('/issues') || u.pathname.includes('/tasks/')) return json(200, u.pathname.endsWith('/issues') ? [{ code: 'x' }] : { values: {} });
    return new Response(new Uint8Array([105, 100, 10, 48, 48, 49, 10]), { status: 200, headers: { 'Content-Type': 'text/csv' } });
  };
  const client = new SuiteClient('https://workplace.invalid', new UserToken({ accessToken: 'synthetic-access' }), { fetch, ...quiet });
  const bytes = new TextEncoder().encode('id\n001\n');

  const uploaded = await client.dataprepUpload(bytes, { filename: 'amounts.csv', idempotencyKey: 'amounts-1' });
  assert.equal(uploaded.id, 'f_1');
  const dp = calls.filter((c) => c.path === '/api/suite/v1/dataprep/files');
  assert.equal(dp.length, 2);                                               // one retry after the 503
  assert.deepEqual(dp.map((c) => c.key), ['amounts-1', 'amounts-1']);
  assert.deepEqual(dp[1].file, ['amounts.csv', 'text/csv', 'id\n001\n']);
  assert.deepEqual(dp[1].query, { workspace_id: 'w_1' });

  await client.extractUpload(new Blob([bytes]), { filename: 'invoice.csv' });
  const ex = calls.find((c) => c.path === '/api/suite/v1/extract/files');
  assert.match(ex.key, /^[0-9a-f-]{36}$/);                                  // a key of its own when none is given

  await assert.rejects(client.tablesImportUpload('t 1', bytes, { filename: 'codes.csv' }), (e) => e.status === 503);
  const tb = calls.filter((c) => c.path === '/api/suite/v1/tables/t%201/imports');
  assert.equal(tb.length, 1);                                               // not retried: no key, a repeat would stage twice
  assert.equal(tb[0].key, undefined);

  assert.deepEqual(Array.from(await client.dataprepExport('dset 1', { format: 'xlsx' })), [105, 100, 10, 48, 48, 49, 10]);
  assert.deepEqual(await client.dataprepRunIssues('run_1'), [{ code: 'x' }]);
  assert.deepEqual(await client.extractExport('extask_1', 2), { values: {} });
  await client.extractDownload('exfile_1');
  await client.tablesExportDownload('exp_1');
  await client.formsFile('ffile_1');
  const gets = calls.filter((c) => c.method === 'GET' && !c.path.endsWith('/whoami')).map((c) => [c.path, c.query]);
  assert.deepEqual(gets, [
    ['/api/suite/v1/dataprep/datasets/dset%201/export', { workspace_id: 'w_1', format: 'xlsx' }],
    ['/api/suite/v1/dataprep/runs/run_1/issues', { workspace_id: 'w_1' }],
    ['/api/suite/v1/extract/tasks/extask_1/export', { workspace_id: 'w_1', revision: '2' }],
    ['/api/suite/v1/extract/files/exfile_1', { workspace_id: 'w_1' }],
    ['/api/suite/v1/tables/exports/exp_1', { workspace_id: 'w_1' }],
    ['/api/suite/v1/forms/files/ffile_1', { workspace_id: 'w_1' }],
  ]);
  assert.equal(calls.filter((c) => c.path.endsWith('/whoami')).length, 1);
  await assert.rejects(client.extractUpload(bytes), ValidationError);       // bytes need a name
  await assert.rejects(client.dataprepUpload(bytes, { filename: 'a.csv', idempotencyKey: 'with space' }), ValidationError);
});
