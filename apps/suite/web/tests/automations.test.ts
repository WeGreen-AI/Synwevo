import { test } from 'node:test';
import assert from 'node:assert/strict';
import { validateMessages, translate } from '@synwevo/ui/i18n-core';
import {
  automationInputSchema, availableCapabilities, emptyDefinition, exactScope, intentDigest, parameterSchema, referenceRoots,
  resolveSchema, reuseTicket, runActions, seed, targets, uncertainFailure, validateDefinition,
  validateParameters, validateReference, validateTrigger, validateValue,
  type AppAccess, type Capability, type Definition, type Policy, type RefContext, type TriggerSpec,
} from '../src/apps/agent/automationModel.ts';
import { automationCopy, automationMessages } from '../src/apps/agent/automations.messages.ts';

// Explicit fixtures model registry contracts; they do not stand in for browser/backend acceptance.
const apps: AppAccess[] = [
  { id: 'fixture', scope: 'workspace', workspaces: [{ id: 'A', name: 'First', role: 'editor' }, { id: 'B', name: 'Second', role: 'viewer' }] },
  { id: 'agent', scope: 'personal', workspaces: [] },
];
const cap: Capability = { id: 'fixture.read', app: 'fixture', version: 1, title: { en: 'Fixture read' }, need: 'read', side_effect: 'none', unattended: true, mode: 'sync', events: ['fixture.updated'], input_schema: { type: 'object', properties: { id: { type: 'string', minLength: 1 } }, required: ['id'], additionalProperties: false } };
const write: Capability = { ...cap, id: 'fixture.write', need: 'write', side_effect: 'write' };
const caps = [cap, write];
function definition(): Definition {
  return { ...emptyDefinition(), parameters: { record: { type: 'string', required: true } }, steps: [
    { id: 'source', kind: 'call', operation: cap.id, operation_version: 1, workspace_id: 'A', input: { id: { $ref: 'params.record' } } },
    { id: 'save', kind: 'call', operation: write.id, operation_version: 1, workspace_id: 'A', input: { id: { $ref: 'steps.source.id' } } },
  ] };
}
const context = (): RefContext => ({ definition: definition(), index: 1, workspace: 'A', capabilities: caps });

test('operation picker respects app availability, role, personal scope, and unattended/read contracts', () => {
  assert.deepEqual(targets(cap, apps), ['A', 'B']);
  assert.deepEqual(targets(write, apps), ['A']);
  assert.deepEqual(targets({ ...cap, need: 'manage' }, apps), []);
  assert.deepEqual(targets({ ...cap, app: 'missing' }, apps), []);
  assert.deepEqual(targets({ ...cap, side_effect: 'write' }, apps), []);
  assert.deepEqual(targets({ ...cap, app: 'agent', id: 'agent.personal', need: 'personal' }, apps), [null]);
  assert.deepEqual(availableCapabilities([cap, write, { ...cap, unattended: false }, { ...cap, writes_elsewhere: true }, { ...cap, id: 'agent.flow.create' }], apps, true), [cap]);
});
test('valid definitions keep explicit target and operation version; revoked permissions fail validation', () => {
  assert.deepEqual(validateDefinition(definition(), caps, apps), []);
  assert.ok(validateDefinition(definition(), caps, []).some((e) => e.code === 'operation'));
  const def = definition(); def.steps[0] = { ...def.steps[0], operation_version: 2 } as Definition['steps'][number];
  assert.ok(validateDefinition(def, caps, apps).some((e) => e.code === 'operation'));
});
test('nullable latest-template defaults become explicit version choices for fixed automations', () => {
  const schema = automationInputSchema({ ...cap, input_schema: { ...cap.input_schema, properties: { ...cap.input_schema.properties, version: { anyOf: [{ type: 'integer', minimum: 1 }, { type: 'null' }], default: null } } } });
  assert.ok(validateValue({ id: 'x' }, schema).some((e) => e.code === 'required'));
  assert.ok(validateValue({ id: 'x', version: null }, schema).some((e) => e.code === 'type'));
  assert.deepEqual(validateValue({ id: 'x', version: 2 }, schema), []);
  assert.equal((seed(schema) as Record<string, unknown>).version, 1);
});
test('manual parameters enforce required types and refuse unknown fields, references, and nonfinite values', () => {
  const def = definition(); def.parameters.count = { type: 'integer', required: false };
  const schema = parameterSchema(def);
  assert.deepEqual(validateValue({ record: 'real-record', count: 3 }, schema), []);
  for (const bad of [{}, { record: 'x', count: true }, { record: 'x', count: 0.5 }, { record: 'x', other: 1 }, { record: { $ref: 'event.id' } }, { record: 'x', count: Infinity }]) assert.ok(validateValue(bad, schema).length);
});
test('registry schemas validate local refs, unions, nested objects, arrays, bounds and formats', () => {
  const schema = { type: 'object', $defs: { Limit: { type: 'integer', minimum: 1, maximum: 3 } }, properties: {
    count: { $ref: '#/$defs/Limit' }, status: { enum: ['ready', 'done'] }, tags: { type: 'array', maxItems: 2, items: { type: 'string', pattern: '^[a-z]+$' } }, optional: { anyOf: [{ type: 'string' }, { type: 'null' }] },
  }, required: ['count'], additionalProperties: false };
  assert.deepEqual(resolveSchema({ $ref: '#/$defs/Limit' }, schema).type, 'integer');
  assert.deepEqual(validateValue({ count: 2, status: 'ready', tags: ['alpha'], optional: null }, schema), []);
  assert.ok(validateValue({ count: 4, status: 'other', tags: ['A', 'b', 'c'] }, schema).length >= 3);
  assert.deepEqual(seed(schema), { count: 1 });
});
test('recursive schema seeds are bounded; unresolved schema refs cannot validate as a free-form value', () => {
  const recursive = { $ref: '#/$defs/Node', $defs: { Node: { anyOf: [{ $ref: '#/$defs/Node' }, { type: 'null' }] } } };
  assert.equal(seed(recursive), null);
  assert.ok(validateValue({}, { $ref: '#/$defs/Absent' }).length);
});
test('reference namespaces, malformed objects, forward references and prototype paths are refused', () => {
  const ctx = context();
  assert.equal(validateReference('params.record', ctx, { type: 'string' }), null);
  assert.equal(validateReference('steps.source.items.0.id', ctx), null);
  assert.equal(validateReference('steps.source.金额', ctx), null);
  for (const path of ['params.nope', 'steps.save.id', 'steps.source', 'steps.source.__proto__', 'steps.source..id', 'receipt.id', 'record.status', 'event.id', 'steps.source.items[-1]']) assert.equal(validateReference(path, ctx), 'reference', path);
  assert.equal(validateReference('params.record', ctx, { type: 'integer' }), 'type');
  assert.ok(validateValue({ $ref: 'params.record', extra: 'x' }, {}, {}, '', true).length);
  assert.ok(validateValue(JSON.parse('{"__proto__":{"polluted":true}}'), {}).length);
});
test('a branch may test an earlier call\'s outcome; nowhere else and only as a whole value', () => {
  const condition = { ...context(), condition: true };
  assert.equal(validateReference('outcome.source', condition), null);
  for (const path of ['outcome.source.status', 'outcome.save', 'outcome.missing']) assert.equal(validateReference(path, condition), 'reference', path);
  assert.equal(validateReference('outcome.source', context()), 'reference');
});
test('parameter size limits count UTF-8 bytes, including Chinese business content', () => {
  assert.ok(validateParameters(definition(), { record: '资料'.repeat(50000) }).some((i) => i.code === 'budget'));
});
test('receipt and record references are limited to result reads and wait predicates', () => {
  assert.equal(validateReference('receipt.id', { ...context(), receipt: true }), null);
  assert.equal(validateReference('record.status', { ...context(), record: true }), null);
  const def = definition();
  if (def.steps[1].kind !== 'call') throw new Error('fixture');
  def.steps[1].result_read = { operation: cap.id, operation_version: 1, workspace_id: 'A', input: { id: { $ref: 'receipt.id' } } };
  assert.deepEqual(validateDefinition(def, caps, apps), []);
  def.steps[1].result_read.input.id = { $ref: 'record.id' };
  assert.ok(validateDefinition(def, caps, apps).some((e) => e.code === 'reference'));
});
test('result mappings cannot transfer business content across spaces or consume unguarded write outputs', () => {
  assert.equal(validateReference('steps.source.id', { ...context(), workspace: 'B' }), 'transfer');
  assert.equal(validateReference('steps.source.id', { ...context(), workspace: null }), 'transfer');
  const def = definition(); if (def.steps[0].kind !== 'call') throw new Error('fixture');
  def.steps[0].operation = write.id;
  assert.ok(!referenceRoots({ ...context(), definition: def }).includes('steps.source'));
  def.steps[0].result_read = { operation: cap.id, operation_version: 1, workspace_id: 'B', input: { id: 'x' } };
  assert.ok(validateDefinition(def, caps, apps).some((e) => e.code === 'transfer'));
});
test('independent operations in multiple explicit spaces remain allowed', () => {
  const def = definition(); if (def.steps[1].kind !== 'call') throw new Error('fixture');
  def.steps[1] = { ...def.steps[1], operation: cap.id, workspace_id: 'B', input: { id: 'independent-record' } };
  assert.deepEqual(validateDefinition(def, caps, apps), []);
  assert.deepEqual(exactScope(def), { tools: ['fixture.read'], workspaces: ['A', 'B'] });
});
test('forward-only graphs reject duplicate IDs and backwards paths, but permit failure exits', () => {
  const def = definition(); def.steps[0].on_error = 'end';
  assert.deepEqual(validateDefinition(def, caps, apps), []);
  def.steps[1].next = 'source'; assert.ok(validateDefinition(def, caps, apps).some((e) => e.code === 'graph'));
  def.steps[1].next = null; def.steps[1].id = 'source'; assert.ok(validateDefinition(def, caps, apps).some((e) => e.code === 'graph'));
});
function triggerFixture(): { def: Definition; trigger: TriggerSpec; policy: Policy } {
  const def = definition();
  return { def, trigger: { kind: 'schedule', timezone: 'Asia/Hong_Kong', at: '09:30', weekdays: [0, 2, 4], misfire: 'skip', grace_s: 300, input: { record: 'x' } }, policy: { ...exactScope(def), max_runs: 4, expires_at: 2000, until_revoked: false } };
}
test('recurring authority has exact tools, spaces and bounded runs, plus one explicit lifecycle', () => {
  const { def, trigger, policy } = triggerFixture();
  assert.deepEqual(validateTrigger(def, trigger, policy, 1000), []);
  assert.deepEqual(validateTrigger(def, trigger, { ...policy, expires_at: null, until_revoked: true }, 1000), []);
  for (const bad of [{ ...policy, tools: [...policy.tools, 'fixture.other'] }, { ...policy, workspaces: ['A', 'B'] }, { ...policy, max_runs: 0 }, { ...policy, expires_at: 999 }, { ...policy, until_revoked: true }, { ...policy, expires_at: null }]) assert.ok(validateTrigger(def, trigger, bad, 1000).length);
});
test('schedules validate IANA time zones, local time, days, grace period and fixed inputs', () => {
  const { def, trigger, policy } = triggerFixture();
  for (const bad of [{ ...trigger, timezone: 'made/up' }, { ...trigger, at: '24:00' }, { ...trigger, weekdays: [] }, { ...trigger, weekdays: [7] }, { ...trigger, grace_s: 0 }, { ...trigger, input: { record: { $ref: 'event.id' } } }]) assert.ok(validateTrigger(def, bad, policy, 1000).length);
});
test('events allow only routing identities and require their explicit workspace in the grant', () => {
  const { def, policy } = triggerFixture();
  const trigger: TriggerSpec = { kind: 'event', workspace_id: 'B', event_type: 'fixture.updated', weekdays: [0], misfire: 'skip', grace_s: 300, input: { record: { $ref: 'event.object_id' } } };
  const grant = { ...policy, ...exactScope(def, 'B') };
  assert.deepEqual(validateTrigger(def, trigger, grant, 1000), []);
  assert.ok(validateTrigger(def, trigger, policy, 1000).length);
  for (const ref of ['event.body', 'event.object_id.name', 'params.record', 'steps.source.id']) assert.ok(validateTrigger(def, { ...trigger, input: { record: { $ref: ref } } }, grant, 1000).length);
});
test('safe recovery controls keep terminal and uncertain outcomes distinct', () => {
  assert.deepEqual(runActions('unknown'), ['resume', 'cancel']);
  assert.deepEqual(runActions('failed'), ['resume']);
  assert.deepEqual(runActions('waiting_approval'), ['pause', 'cancel']);
  for (const status of ['partial', 'succeeded', 'cancelled']) assert.deepEqual(runActions(status), []);
});
test('uncertain transport, hidden receipts and idempotency conflicts retain the original intent', () => {
  for (const error of [{ status: 0 }, { status: 503 }, { status: 403, upstream: true }, { status: 409, code: 'run_result_unavailable' }, { status: 409, code: 'idempotency_conflict' }]) assert.equal(uncertainFailure(error), true);
  for (const error of [{ status: 403 }, { status: 422 }, { status: 409, code: 'flow_version_conflict' }, { status: 409, code: 'idempotent_failure', detail: { code: 'flow_policy_expired' } }]) assert.equal(uncertainFailure(error), false);
});
test('permission changes during replay cannot turn a possibly committed request into a new action', () => {
  assert.equal(uncertainFailure({ status: 403, code: 'app_access_denied' }, true), true);
  assert.equal(uncertainFailure({ status: 404, code: 'unknown_operation' }, true), true);
  assert.equal(uncertainFailure({ status: 409, code: 'idempotent_failure', detail: { code: 'invalid_input' } }, true), false);
});
test('lost-response retry binds to the same operation, canonical payload, key and run identity', async () => {
  const digest = await intentDigest('agent.flow.start', { flow_id: 'fixture', version: 2, parameters: { id: 'x' } });
  const reordered = await intentDigest('agent.flow.start', { parameters: { id: 'x' }, version: 2, flow_id: 'fixture' });
  assert.equal(digest, reordered);
  const ticket = reuseTicket(null, 'agent.flow.start', digest, 'original-key');
  assert.equal(reuseTicket(ticket, 'agent.flow.start', reordered, 'unused-new-key').key, 'original-key');
  const changed = await intentDigest('agent.flow.start', { flow_id: 'fixture', version: 3, parameters: { id: 'x' } });
  assert.throws(() => reuseTicket(ticket, 'agent.flow.start', changed, 'new-key'), /pending_intent/);
  assert.throws(() => reuseTicket(ticket, 'agent.flow.create', digest, 'new-key'), /pending_intent/);
  assert.deepEqual(Object.keys(ticket).sort(), ['digest', 'key', 'operation']);
});
test('automation catalogue independently validates all three locales and interpolation', () => {
  assert.deepEqual(validateMessages(automationMessages), []);
  for (const [, [hans, en, hant]] of Object.entries(automationCopy)) {
    assert.ok(hans && en && hant);
    assert.equal(translate('en', hans), en);
    assert.equal(translate('zh-Hant', hans), hant);
  }
});
