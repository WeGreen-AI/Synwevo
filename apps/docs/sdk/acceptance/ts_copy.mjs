// Synthetic real-service SDK acceptance, launched only by python_full_flow.py's isolated fixture.
// Approval/execution belong to the separate scenario C integration; this checks the six methods.
import assert from 'node:assert/strict';
import { ApiKey, Client } from '../typescript/synwevo-docs.mjs';

const client = new Client(process.env.SYNWEVO_BASE_URL, new ApiKey(process.env.ACCEPTANCE_COPY_TOKEN));
const targetWorkspaceId = process.env.ACCEPTANCE_WORKSPACE_A;
const targets = await client.copyTargets();
assert.equal(targets.source_is_personal, true);
assert.ok(targets.items.some(w => w.id === targetWorkspaceId));
const project = await client.createProject({ name: 'SYNTHETIC TypeScript copy request' });
const manifest = await client.copyManifest(project.id);
const request = await client.requestCopy(project.id, { targetWorkspaceId }, { idempotencyKey: 'typescript-copy' });
assert.equal(request.status, 'pending');
assert.equal(request.digest, manifest.digest);
assert.equal((await client.requestCopy(project.id, { targetWorkspaceId }, { idempotencyKey: 'typescript-copy' })).id, request.id);
assert.equal((await client.getCopyRequest(request.id)).status, 'pending');
assert.deepEqual((await client.listCopyRequests(project.id, { status: 'pending', limit: 1 })).items.map(r => r.id), [request.id]);
assert.equal((await client.withdrawCopyRequest(request.id)).status, 'withdrawn');
assert.deepEqual((await client.listCopyRequests(project.id, { status: 'pending' })).items, []);
const events = (await client.collect(client.events({ types: ['copy.requested', 'copy.withdrawn'] }))).filter(e => e.copy_id === request.id);
assert.deepEqual(new Set(events.map(e => e.type)), new Set(['copy.requested', 'copy.withdrawn']));
console.log(JSON.stringify({ project_id: project.id, copy_id: request.id, status: 'withdrawn', events: events.length }));
