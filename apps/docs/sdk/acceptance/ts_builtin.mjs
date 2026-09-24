// Synthetic builtin-template acceptance: real Account/Docs HTTP and unchanged shared source kit.
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { appendFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { Client, ClientCredentials, credentialsFromEnv, ConflictError, PermissionDeniedError,
  ValidationError } from '../typescript/synwevo-docs.mjs';

const [kit, out] = process.argv.slice(2);
const base = process.env.SYNWEVO_BASE_URL;
assert.equal(new URL(base).hostname, '127.0.0.1');
assert.equal((await (await fetch(`${base}/healthz`)).json()).instance, 'test');
const credentials = await credentialsFromEnv();
assert(credentials instanceof ClientCredentials);
const client = new Client(base, credentials);
const reader = new Client(base, new ClientCredentials({ tokenUrl: credentials.tokenUrl,
  clientId: credentials.clientId, clientSecret: credentials.clientSecret,
  resource: credentials.resource, scope: 'docweave.read' }));
const who = await client.whoami();
assert.deepEqual([...who.scopes].sort(), ['docweave.read', 'docweave.write']);
assert.deepEqual((await reader.whoami()).scopes, ['docweave.read']);
await mkdir(out, { recursive: true });
const fields = JSON.parse(await readFile(join(kit, 'fields.json'), 'utf8')).fields;
const definition = JSON.parse(await readFile(join(kit, 'project.json'), 'utf8'));
const expected = JSON.parse(await readFile(join(kit, 'expected.json'), 'utf8'));
const cli = fileURLToPath(new URL('../typescript/synwevo-docs.mjs', import.meta.url));
async function command(args) {
  const argv = [cli, ...args];
  let result;
  try {
    result = await promisify(execFile)(process.execPath, argv, { encoding: 'utf8', timeout: 60_000 });
  } catch (error) {
    await appendFile(join(out, 'commands.jsonl'), JSON.stringify({ argv: [process.execPath, ...argv], exit_code: error.code }) + '\n');
    throw error;
  }
  await appendFile(join(out, 'commands.jsonl'), JSON.stringify({ argv: [process.execPath, ...argv], exit_code: 0 }) + '\n');
  return JSON.parse(result.stdout);
}

const results = [];
for (const kind of ['builtin_doc', 'builtin_sheet']) {
  const format = kind === 'builtin_doc' ? 'docx' : 'xlsx';
  const directory = join(out, kind);
  await mkdir(directory, { recursive: true });
  const marker = 'Synthetic builtin revision 2';
  const initialMarker = 'Synthetic builtin revision 1';
  const editor = kind === 'builtin_doc'
    ? { body: { type: 'doc', content: [
      { type: 'paragraph', content: [{ type: 'text', text: initialMarker }] },
      ...fields.map((field, i) => ({ type: 'paragraph', content: [
        { type: 'text', text: field.name + '：' },
        { type: 'field', attrs: { id: `f${i}`, name: field.name, required: true } },
      ] })),
    ] } }
    : { sheets: [{ name: '结果', cells: { A1: { v: initialMarker },
      ...Object.fromEntries(fields.flatMap((field, i) => [
        [`A${i + 2}`, { v: field.name }], [`B${i + 2}`, { v: field.find }],
      ])) }, fields: fields.map((field, i) => ({ id: `f${i}`, name: field.name,
        kind: 'cell', ref: `B${i + 2}`, required: true })) }] };
  const create = { kind, name: `Synthetic ${kind}`, editor };
  const createKey = `synthetic-${kind}:create`;
  const template = await client.createBuiltinTemplate(create, { idempotencyKey: createKey });
  assert.equal(template.head, 1);
  const replay = await client.createBuiltinTemplate(create, { idempotencyKey: createKey });
  assert.deepEqual(replay, template);
  assert.equal(replay.$meta.replayed, true);
  assert.deepEqual((await reader.getBuiltinTemplate(template.id)).editor, editor);
  await assert.rejects(reader.createBuiltinTemplate(create), PermissionDeniedError);
  await assert.rejects(client.createBuiltinTemplate({ ...create, name: 'Changed input' },
    { idempotencyKey: createKey }), ConflictError);

  const updated = structuredClone(editor);
  if (kind === 'builtin_doc') updated.body.content[0].content[0].text = marker;
  else updated.sheets[0].cells.A1.v = marker;
  const save = { editor: updated, baseVersion: 1, name: `Updated synthetic ${kind}` };
  const saveKey = `synthetic-${kind}:save`;
  for (const baseVersion of [undefined, 0, -1, '1', true, 1.5]) {
    assert.throws(() => client.saveBuiltinTemplate(template.id, { ...save, baseVersion }), ValidationError);
  }
  const saved = await client.saveBuiltinTemplate(template.id, save, { idempotencyKey: saveKey });
  assert.equal(saved.head, 2);
  assert.equal(saved.name, save.name);
  const savedReplay = await client.saveBuiltinTemplate(template.id, save, { idempotencyKey: saveKey });
  assert.deepEqual(savedReplay, saved);
  assert.equal(savedReplay.$meta.replayed, true);
  await assert.rejects(client.saveBuiltinTemplate(template.id, save), { code: 'stale_template', status: 409 });
  await assert.rejects(reader.saveBuiltinTemplate(template.id, { ...save, baseVersion: 2 }), PermissionDeniedError);
  const latest = await client.getBuiltinTemplate(template.id);
  assert.equal(latest.version, 2);
  assert.deepEqual(latest.editor, updated);
  assert.equal(latest.fields.length, fields.length);
  const historical = await client.getBuiltinTemplate(template.id, { version: 1 });
  assert.equal(historical.version, 1);
  assert.deepEqual(historical.editor, editor);
  await writeFile(join(directory, 'editor-v1.json'), JSON.stringify(editor));
  await writeFile(join(directory, 'editor-v2.json'), JSON.stringify(updated));
  assert.deepEqual(await command(['templates', 'create-builtin', '--kind', kind, '--name', create.name,
    '--file', join(directory, 'editor-v1.json'), '--idempotency-key', createKey]), template);
  assert.deepEqual(await command(['templates', 'save-builtin', template.id, '--name', save.name,
    '--file', join(directory, 'editor-v2.json'), '--base-version', '1', '--idempotency-key', saveKey]), saved);
  assert.deepEqual(await command(['templates', 'get-builtin', template.id, '--version', '1']), historical);

  const project = await client.createProject({ name: `Synthetic ${kind} generation` });
  const source = await client.uploadSource(project.id, join(kit, 'customers.xlsx'));
  assert.equal((await client.waitForFile(source.id)).status, 'ready');
  await client.bindTemplate(project.id, template.id, { version: 2 });
  await client.putDefinition(project.id, definition, { baseRev: 0 });
  const preflight = await client.preflight(project.id, { formats: [format], simulate: true });
  assert.equal(preflight.valid, expected.samples);
  assert.equal(preflight.invalid, 0);
  assert.equal(preflight.simulation.failed, 0);
  const preview = await client.downloadPreview(project.id, join(directory, `preview.${format}`),
    { index: 0, signature: preflight.signature });
  assert.equal(preview.verified, true);
  assert.equal(preview.template_version, '2');
  assert.equal((await client.collect(client.listJobs(project.id))).length, 0);
  const created = await client.createJob(project.id, { formats: [format] });
  const job = await client.waitForJob(created.id);
  assert.equal(job.status, 'completed');
  const artifacts = [];
  for await (const artifact of client.artifacts(job.id, { limit: 3 })) {
    const download = await client.downloadArtifact(artifact, { outDir: directory });
    assert.equal(download.sha256, artifact.sha256);
    assert.equal(download.header_sha256, artifact.sha256);
    artifacts.push({ ...download, filename: artifact.filename, format: artifact.format, manifest_sha256: artifact.sha256 });
  }
  assert.equal(artifacts.length, expected.samples);
  results.push({ kind, tenant: who.tenant, template_id: template.id, version: latest.version,
    marker, initial_marker: initialMarker, field_names: fields.map(field => field.name),
    preflight, preview: { download: preview }, job: { id: job.id, status: job.status }, artifacts,
    checks: { create_replay: true, save_replay: true, stale_save_409: true, version_export: true,
      read_scope_can_export: true, read_scope_write_denied_403: true, cli_operations: 3 } });
}
await writeFile(join(out, 'result.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify({ status: 'passed', operations: ['create_builtin_template',
  'save_builtin_template', 'get_builtin_template'], kinds: results.map(result => result.kind),
  artifacts: results.reduce((sum, result) => sum + result.artifacts.length, 0), previews: results.length }));
