// Synthetic acceptance only: never accepts a remote or unmarked service.
import assert from 'node:assert/strict';
import { access, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { Client, credentialsFromEnv, ConflictError, IntegrityError, BadRequestError } from '../typescript/synwevo-docs.mjs';

const [kit, out] = process.argv.slice(2);
const base = process.env.SYNWEVO_BASE_URL;
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.equal((await (await fetch(`${base}/healthz`)).json()).instance, 'test');
const client = new Client(base, await credentialsFromEnv(), { chunkBytes: 1024, multipartMaxBytes: 1 });
// Fault injection discards an actual successful API response; no API is mocked.
let discardCreate = true; let discardChunk = true; const retryKeys = [];
const actualFetch = client.fetch;
client.fetch = async (url, options) => {
  const res = await actualFetch(url, options);
  if (options.method === 'POST' && url.endsWith('/api/v1/projects')) {
    retryKeys.push(options.headers['Idempotency-Key']);
    if (discardCreate && res.ok) {
      discardCreate = false;
      res.text = async () => { throw new Error('Synthetic lost response body after real creation'); };
    }
  }
  if (discardChunk && options.method === 'PUT' && url.includes('/uploads/') && res.ok) {
    discardChunk = false;
    await res.body.cancel();
    throw new Error('Synthetic lost chunk acknowledgement after real upload');
  }
  return res;
};
const who = await client.whoami();
assert.deepEqual([...who.scopes].sort(), ['docweave.read', 'docweave.write']);
await writeFile(join(kit, 'whoami.json'), JSON.stringify(who));
const args = { sources: [join(kit, 'customers.xlsx')], template: join(kit, 'template.docx'),
  fields: join(kit, 'fields.json'), definition: join(kit, 'project.json'),
  formats: ['docx'], outDir: out, keyPrefix: 'synthetic-ts-acceptance', projectName: 'Synthetic TS acceptance' };
const first = await client.generate(args);
assert.equal(discardCreate, false);
assert.equal(discardChunk, false);
assert.equal(retryKeys[0], retryKeys[1]);
assert.equal(first.preflight.records, 12);
assert.equal(first.preview.simulation.checked, 12);
assert.equal(first.preview.simulation.failed, 0);
assert.equal(first.preview.download.verified, true);
assert(first.preview.download.project_signature);
assert.equal(first.job.status, 'completed');
assert.equal(first.artifacts.length, 12);
assert(first.artifacts.every(a => a.sha256 === a.manifest_sha256 && a.sha256 === a.header_sha256));
// Replaying a consumed resumable upload must still replay the original creating POST.
const replay = await client.generate(args);
assert.equal(replay.project_id, first.project_id);
assert.equal(replay.template_id, first.template_id);
assert.equal(replay.job.id, first.job.id);
assert(replay.artifacts.every(a => a.reused));
assert.equal((await client.collect(client.artifacts(first.job.id, { limit: 3 }))).length, 12);
assert.equal((await client.collect(client.listJobs(first.project_id))).length, 1);
assert.equal((await client.collect(client.runs(first.job.id))).length, 1);
assert.equal((await client.collect(client.preflightIssues(first.project_id, { level: 'error' }))).length, 0);
await assert.rejects(client.createProject({ name: 'Changed payload' }, { idempotencyKey: 'synthetic-ts-acceptance:project' }), ConflictError);
await assert.rejects(client.downloadArtifact({ ...first.artifacts[0], sha256: '0'.repeat(64) },
  { destPath: join(out, 'must-not-exist.docx') }), IntegrityError);
await assert.rejects(access(join(out, 'must-not-exist.docx')));
await assert.rejects(client.downloadPreview(first.project_id, join(out, 'stale-preview.docx'), { signature: 'stale' }), ConflictError);
assert.equal((await client.collect(client.listJobs(first.project_id))).length, 1);
await assert.rejects(client.retryJob(first.job.id, { failedOnly: true }), { code: 'nothing_to_retry' });
await assert.rejects(client.retryJob(first.job.id, { failedOnly: true, includeSkipped: true }), BadRequestError);
await client.downloadArchive(first.job.id, join(out, 'archive.zip'));
await client.downloadReport(first.job.id, join(out, 'report.csv'));
await writeFile(join(out, 'result.json'), JSON.stringify(first, null, 2));
console.log(JSON.stringify({ status: 'passed', job: first.job, artifacts: first.artifacts.length,
  replay: true, pagination: true, conflict: true, checksumRejection: true, upload: 'resumable',
  lostCreateResponse: 'same key retried', lostChunkAcknowledgement: 'resumed from server offset' }));
