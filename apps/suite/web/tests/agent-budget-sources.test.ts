/** Read-only protocol fixtures, not live provider/payment tests. */
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { testServer } from './_vite.ts';

let server: any, resolve: any, ReviewError: any;
before(async () => {
  server = await testServer();
  const mod = await server.ssrLoadModule('/src/apps/agent/budgetSources.ts');
  resolve = mod.resolveBudgetSources; ReviewError = mod.SourceReviewError;
});
after(async () => { await server?.close(); });
const signal = new AbortController().signal;
const business = { kind: 'form', workspace_id: 'ws_a', resource_id: 'frm_123456789abc', snapshot: 'a'.repeat(64) };
const file = { file_id: 'af_123456789abc', snapshot: 'b'.repeat(64) };
const intent = { conversation_id: 'conv_fixture', text: 'Synthetic question', context_refs: [business], file_refs: [file] };
const contextResult = () => ({ reference: { ...business }, title: 'Same name', workspace_name: 'Source workspace', open_path: null });
const fileResult = () => ({ id: file.file_id, filename: 'Same name', size: 4, created_at: 1791634501.123456,
  updated_at: 1791634502.654321, attempts: 1, status: 'ready', error_code: null, scan_status: 'not_configured',
  reference: { ...file }, download_path: `/api/suite/agent/files/${file.file_id}/download`,
  extraction: { lines: [{ text: 'synthetic private content', source: { line: 1 } }], tables: [] } });
const clean = (x: any) => JSON.parse(JSON.stringify(x));

test('review reads only chosen references with explicit personal context and retains titles, not content', async () => {
  const calls: any[] = [];
  const result = await resolve(intent, async (...args: any[]) => {
    calls.push(args); return args[0] === 'agent.context.resolve' ? contextResult() : fileResult();
  }, signal);
  assert.deepEqual(clean(result), [{ title: 'Same name', workspace: 'Source workspace', kind: 'form' },
    { title: 'Same name', workspace: null, kind: 'file' }]);
  assert.deepEqual(clean(calls.map((c) => c.slice(0, 3))), [
    ['agent.context.resolve', { workspace_id: 'ws_a', kind: 'form', resource_id: 'frm_123456789abc' }, null],
    ['agent.file.get', { file_id: file.file_id }, null],
  ]);
  assert.ok(calls.every((c) => c[3] === signal));
  assert.ok(!JSON.stringify(result).includes('synthetic private content'));
});

test('a changed business snapshot or changed/deleted file refuses the entire review', async () => {
  for (const change of ['business', 'file', 'deleted']) {
    const context = contextResult(), attached = fileResult();
    if (change === 'business') context.reference.snapshot = 'c'.repeat(64);
    if (change === 'file') attached.reference.snapshot = 'c'.repeat(64);
    if (change === 'deleted') Object.assign(attached, { status: 'deleted', reference: null, extraction: null, download_path: null });
    await assert.rejects(resolve(intent, async (operation: string) => operation === 'agent.context.resolve' ? context : attached, signal), ReviewError);
  }
});

test('revoked or malformed source responses cannot silently disappear from a payment review', async () => {
  await assert.rejects(resolve(intent, async () => { throw new Error('sensitive server diagnostic'); }, signal),
    (error: any) => error instanceof ReviewError && !error.message.includes('sensitive'));
  const wrong = fileResult(); wrong.id = 'af_abcdefabcdef';
  await assert.rejects(resolve({ ...intent, context_refs: [] }, async () => wrong, signal), ReviewError);
});

test('ordinary and regeneration intents do not fetch any unrelated source', async () => {
  for (const request of [{ conversation_id: 'conv_fixture', text: 'plain' }, { conversation_id: 'conv_fixture', regenerate_of: 'msg_fixture' }]) {
    const result = await resolve(request, () => { throw new Error('must not read'); }, signal);
    assert.deepEqual(clean(result), []);
  }
  await assert.rejects(resolve({ ...intent, file_refs: [file, file, file, file, file] },
    () => { throw new Error('over-limit must not read'); }, signal), ReviewError);
});
