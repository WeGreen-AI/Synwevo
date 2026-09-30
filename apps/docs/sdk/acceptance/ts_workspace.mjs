// Real directory acceptance. The runner installs the local tarball and copies this external
// client there, so the package import is resolved independently of repository modules.
import assert from 'node:assert/strict';
import { WorkspaceClient, ConflictError } from '@synwevo/docs-sdk';

const client = await WorkspaceClient.fromEnv();
const who = await client.whoami();
assert.equal(who.workspace.id, process.env.ACCEPTANCE_DIRECTORY_WORKSPACE);
assert.deepEqual(new Set(who.scopes), new Set(['workspace.read', 'workspace.members.write']));
const memberId = process.env.ACCEPTANCE_DIRECTORY_MEMBER;
const members = [];
for await (const member of client.iterMembers({ limit: 1 })) members.push(member.user_id);
assert.ok(members.includes(memberId));
const input = { email: 'typescript-invite@example.invalid', role: 'viewer' };
const first = await client.createInvitation(input, { idempotencyKey: 'typescript:invite' });
assert.equal(first.mail, 'queued');
assert.equal(first.state, 'pending');
assert.equal(first.invited_via, who.client_id);
const replay = await client.createInvitation(input, { idempotencyKey: 'typescript:invite' });
assert.equal(replay.id, first.id);
assert.equal(replay.$meta.replayed, true);
const resent = await client.resendInvitation(first.id, { idempotencyKey: 'typescript:resend' });
assert.notEqual(resent.id, first.id);
assert.equal((await client.resendInvitation(first.id, { idempotencyKey: 'typescript:resend' })).id, resent.id);
assert.equal((await client.revokeInvitation(resent.id)).state, 'revoked');
const revoked = [];
for await (const invite of client.iterInvitations({ state: 'revoked', limit: 1 })) revoked.push(invite.id);
assert.ok(revoked.includes(first.id) && revoked.includes(resent.id));
const changed = await client.changeMember(memberId, { role: 'editor', expectedRole: 'viewer' });
assert.equal(changed.role, 'editor');
assert.equal(changed.previous_role, 'viewer');
await assert.rejects(() => client.changeMember(memberId, { role: 'viewer', expectedRole: 'viewer' }),
  error => error instanceof ConflictError && error.code === 'role_changed');
assert.equal((await client.removeMember(memberId, { expectedRole: 'editor', reason: 'Synthetic SDK acceptance cleanup' })).removed, true);
for await (const member of client.iterMembers({ limit: 1 })) assert.notEqual(member.user_id, memberId);
console.log(JSON.stringify({ language: 'typescript', status: 'passed', client_id: who.client_id,
  invitation_ids: [first.id, resent.id], member_id: memberId, stale_role: 'role_changed', removed: true }));
