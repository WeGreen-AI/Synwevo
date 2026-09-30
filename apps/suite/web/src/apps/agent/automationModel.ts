/** Personal deterministic-flow contracts. No browser workspace, expressions, or model credentials. */
export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
export type Values = Record<string, Json>;
export type ParameterType = 'string' | 'integer' | 'number' | 'boolean' | 'object' | 'array';
export interface Schema {
  $ref?: string; $defs?: Record<string, Schema>; type?: string; title?: string;
  properties?: Record<string, Schema>; required?: string[]; additionalProperties?: boolean | Schema;
  items?: Schema; anyOf?: Schema[]; oneOf?: Schema[]; allOf?: Schema[];
  enum?: Json[]; const?: Json; default?: Json; minLength?: number; maxLength?: number;
  minimum?: number; maximum?: number; exclusiveMinimum?: number; exclusiveMaximum?: number;
  minItems?: number; maxItems?: number; minProperties?: number; maxProperties?: number;
  uniqueItems?: boolean; pattern?: string; format?: string;
}
export interface Capability {
  id: string; app: string; version: number; title: Record<string, string>; need: string;
  side_effect: string; unattended: boolean; mode: string; events: string[];
  input_schema: Schema; output_schema?: Schema | null; writes_elsewhere?: boolean;
}
export interface AppAccess { id: string; scope: string; workspaces: { id: string; name: string; role: string }[] }
export interface Parameter { type: ParameterType; required: boolean }
export interface ReadSpec { operation: string; operation_version: number; workspace_id: string | null; input: Values }
export interface Predicate { left: Json; op: 'eq' | 'ne' | 'in' | 'exists'; right: Json }
interface BaseStep { id: string; next?: string | null; on_error?: string | null }
export interface CallStep extends BaseStep, ReadSpec { kind: 'call'; result_read?: ReadSpec | null; max_attempts?: number; retry_codes?: string[]; backoff_s?: number }
export interface WaitStep extends BaseStep { kind: 'wait'; read: ReadSpec; until: Predicate; reject?: Predicate | null; state: 'waiting_input' | 'waiting_approval'; event_types?: string[]; poll_s: number; timeout_s: number }
export interface BranchStep extends BaseStep { kind: 'branch'; condition: Predicate; then: string; otherwise: string }
export interface FailStep extends BaseStep { kind: 'fail'; code: string }
export type Step = CallStep | WaitStep | BranchStep | FailStep;
export interface Definition { parameters: Record<string, Parameter>; steps: Step[]; max_calls: number; timeout_s: number }
export interface Flow { id: string; name: string; latest_version: number; created_at: number }
export interface FlowVersion { flow_id: string; name: string; version: number; definition: Definition }
export interface Run {
  id: string; flow_id: string; version: number; status: string; step_id: string | null; error_code: string | null;
  calls_used: number; deadline: number; created_at: number; updated_at: number; finished_at: number | null;
}
export interface History { seq: number; type: string; status: string; step_id: string | null; error_code: string | null; created_at: number }
export interface RunDetail extends Run {
  steps: { step_id: string; status: string; attempt: number; checks: number; op_run_id: string | null; operation_status: string | null; error_code: string | null; output: Json; snapshot_hidden: boolean }[];
  history: History[]; next: number | null;
}
export interface TriggerSpec {
  kind: 'schedule' | 'event'; input: Values; timezone?: string | null; at?: string | null;
  weekdays: number[]; misfire: 'skip' | 'once'; grace_s: number;
  event_type?: string | null; workspace_id?: string | null; object_kind?: string | null; object_id?: string | null;
}
export interface Policy { tools: string[]; workspaces: string[]; max_runs: number; expires_at: number | null; until_revoked: boolean }
export interface FlowTrigger { id: string; flow_id: string; version: number; enabled: boolean | number; trigger: TriggerSpec; policy: Policy; runs_used: number; next_at: number | null; error_code: string | null }
export interface Delivery { occurrence: string; run_id: string | null; status: string; error_code: string | null; created_at: number }
export interface Issue { path: string; code: 'required' | 'type' | 'range' | 'format' | 'unknownField' | 'reference' | 'transfer' | 'operation' | 'graph' | 'budget' | 'lifecycle' }
const identifier = /^[A-Za-z][A-Za-z0-9_-]{0,79}$/;
const forbidden = new Set(['__proto__', 'constructor', 'prototype']);
export const parameterTypes: ParameterType[] = ['string', 'integer', 'number', 'boolean', 'object', 'array'];
export const isObject = (v: unknown): v is Values => !!v && typeof v === 'object' && !Array.isArray(v);
export const isReference = (v: unknown): v is { $ref: string } => isObject(v) && Object.keys(v).length === 1 && typeof v.$ref === 'string';
export const emptyDefinition = (): Definition => ({ parameters: {}, steps: [], max_calls: 100, timeout_s: 604800 });
export const emptyRead = (): ReadSpec => ({ operation: '', operation_version: 1, workspace_id: null, input: {} });
export const stepWorkspace = (step: Step): string | null => step.kind === 'call' ? step.workspace_id : step.kind === 'wait' ? step.read.workspace_id : null;
export const readsOf = (definition: Definition): ReadSpec[] => definition.steps.flatMap((s) => s.kind === 'call' ? [s, ...(s.result_read ? [s.result_read] : [])] : s.kind === 'wait' ? [s.read] : []);

/** The catalogue narrows choices; the backend remains authoritative for resource ACLs. */
export function targets(cap: Capability, apps: AppAccess[]): (string | null)[] {
  const app = apps.find((a) => a.id === cap.app);
  if (!app || !cap.unattended || cap.id.startsWith('agent.flow.') || cap.writes_elsewhere || (cap.need === 'read' && cap.side_effect !== 'none')) return [];
  if (cap.need === 'personal') return app.scope === 'personal' ? [null] : [];
  const roles = cap.need === 'manage' ? ['owner', 'admin'] : cap.need === 'write' ? ['owner', 'admin', 'editor'] : cap.need === 'read' ? ['owner', 'admin', 'editor', 'viewer'] : [];
  return app.workspaces.filter((w) => roles.includes(w.role)).map((w) => w.id);
}
export function availableCapabilities(caps: Capability[], apps: AppAccess[], readOnly = false): Capability[] {
  return caps.filter((c) => targets(c, apps).length && (!readOnly || (c.side_effect === 'none' && c.mode === 'sync')));
}
/** Pin application configuration as well as the operation contract. Several real adapters
 * accept null/omitted version to mean latest; recurring work must make that choice explicit. */
export function automationInputSchema(cap: Capability): Schema {
  const root = cap.input_schema;
  const properties = { ...root.properties };
  const pinned = ['version', 'template_version', 'config_version', 'plan_version'].filter((key) => Object.hasOwn(properties, key));
  for (const key of pinned) {
    const s = resolveSchema(properties[key], root);
    properties[key] = { ...s, default: s.default === null ? undefined : s.default,
      ...(s.anyOf ? { anyOf: s.anyOf.filter((v) => v.type !== 'null') } : {}),
      ...(s.oneOf ? { oneOf: s.oneOf.filter((v) => v.type !== 'null') } : {}) };
  }
  return { ...root, properties, required: [...new Set([...(root.required ?? []), ...pinned])] };
}
export function resolveSchema(schema: Schema, root: Schema, seen = new Set<string>()): Schema {
  if (!schema.$ref) return schema;
  const name = schema.$ref.startsWith('#/$defs/') ? schema.$ref.slice(8) : '';
  if (!name || seen.has(name) || !root.$defs?.[name]) return { ...schema, type: 'unsupported' };
  return resolveSchema({ ...root.$defs[name], ...schema, $ref: undefined }, root, new Set([...seen, name]));
}
export function seed(schema: Schema, root = schema, depth = 0): Json {
  if (depth > 16) return null;
  const s = resolveSchema(schema, root);
  if (s.default !== undefined) return structuredClone(s.default);
  if (s.const !== undefined) return s.const;
  if (s.enum?.length) return s.enum[0];
  if (s.anyOf || s.oneOf) return seed((s.anyOf ?? s.oneOf)![0], root, depth + 1);
  if (s.type === 'object' || s.properties) return Object.fromEntries((s.required ?? []).map((k) => [k, seed(s.properties?.[k] ?? {}, root, depth + 1)]));
  if (s.type === 'array') return [];
  if (s.type === 'boolean') return false;
  if (s.type === 'integer' || s.type === 'number') return s.minimum ?? 0;
  return s.type === 'null' ? null : '';
}
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  return JSON.stringify(value);
}
export function referencePaths(value: Json): string[] {
  if (isReference(value)) return [value.$ref];
  if (Array.isArray(value)) return value.flatMap(referencePaths);
  return isObject(value) ? Object.values(value).flatMap(referencePaths) : [];
}
export function validateValue(value: Json | undefined, schema: Schema, root = schema, path = '', allowRefs = false, depth = 0): Issue[] {
  const issue = (code: Issue['code']): Issue[] => [{ path, code }];
  if (depth > 24) return issue('budget');
  if (value === undefined) return issue('required');
  if (allowRefs && isReference(value)) return [];
  if (isObject(value) && Object.hasOwn(value, '$ref')) return issue('reference');
  const s = resolveSchema(schema, root);
  const union = s.anyOf ?? s.oneOf;
  if (union && !union.some((x) => !validateValue(value, x, root, path, allowRefs, depth + 1).length)) return issue('type');
  if (s.allOf) {
    const errors = s.allOf.flatMap((x) => validateValue(value, x, root, path, allowRefs, depth + 1));
    if (errors.length) return errors;
  }
  if (s.const !== undefined && canonical(value) !== canonical(s.const)) return issue('type');
  if (s.enum && !s.enum.some((x) => canonical(x) === canonical(value))) return issue('type');
  if (s.type && !(s.type === 'null' ? value === null : s.type === 'object' ? isObject(value) : s.type === 'array' ? Array.isArray(value) : s.type === 'integer' ? typeof value === 'number' && Number.isSafeInteger(value) : typeof value === s.type)) return issue('type');
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return issue('type');
    if ((s.minimum !== undefined && value < s.minimum) || (s.maximum !== undefined && value > s.maximum) || (s.exclusiveMinimum !== undefined && value <= s.exclusiveMinimum) || (s.exclusiveMaximum !== undefined && value >= s.exclusiveMaximum)) return issue('range');
  }
  if (typeof value === 'string') {
    const length = [...value].length;
    if ((s.minLength !== undefined && length < s.minLength) || (s.maxLength !== undefined && length > s.maxLength)) return issue('range');
    // Avoid interpreting server patterns as code. Unsupported JS patterns remain server-validated.
    if (s.pattern) { try { if (!new RegExp(s.pattern, 'u').test(value)) return issue('format'); } catch { /* Python-only pattern */ } }
    if (s.format === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(value)) return issue('format');
    if (s.format === 'date-time' && !Number.isFinite(Date.parse(value))) return issue('format');
  }
  if (Array.isArray(value)) {
    if ((s.minItems !== undefined && value.length < s.minItems) || value.length > (s.maxItems ?? 1000)) return issue('range');
    if (s.uniqueItems && new Set(value.map(canonical)).size !== value.length) return issue('format');
    return value.flatMap((v, i) => validateValue(v, s.items ?? {}, root, `${path}.${i}`, allowRefs, depth + 1));
  }
  if (isObject(value)) {
    const keys = Object.keys(value);
    const errors: Issue[] = [];
    if ((s.minProperties !== undefined && keys.length < s.minProperties) || keys.length > (s.maxProperties ?? 1000)) errors.push(...issue('range'));
    for (const k of s.required ?? []) if (!Object.hasOwn(value, k)) errors.push({ path: `${path}.${k}`, code: 'required' });
    for (const [k, v] of Object.entries(value)) {
      if (forbidden.has(k) || (!Object.hasOwn(s.properties ?? {}, k) && s.additionalProperties === false)) errors.push({ path: `${path}.${k}`, code: 'unknownField' });
      else errors.push(...validateValue(v, s.properties?.[k] ?? (typeof s.additionalProperties === 'object' ? s.additionalProperties : {}), root, `${path}.${k}`, allowRefs, depth + 1));
    }
    return errors;
  }
  return [];
}
export interface RefContext { definition: Definition; index: number; workspace: string | null; receipt?: boolean; record?: boolean; condition?: boolean; event?: boolean; capabilities: Capability[] }
export function referenceRoots(ctx: RefContext): string[] {
  if (ctx.event) return ['event.id', 'event.object_id', 'event.workspace_id', 'event.type'];
  const roots = Object.keys(ctx.definition.parameters).map((k) => `params.${k}`);
  ctx.definition.steps.slice(0, ctx.index).forEach((step) => {
    const op = step.kind === 'call' ? ctx.capabilities.find((c) => c.id === step.operation) : null;
    if ((step.kind === 'wait' || (step.kind === 'call' && (step.result_read || op?.side_effect === 'none'))) &&
        (ctx.condition || stepWorkspace(step) === ctx.workspace)) roots.push(`steps.${step.id}`);
  });
  if (ctx.receipt) roots.push('receipt');
  if (ctx.record) roots.push('record');
  return roots;
}
export function validateReference(path: string, ctx: RefContext, expected: Schema = {}): Issue['code'] | null {
  const bits = path.split('.');
  if (bits.length < 2 || bits.length > 24 || bits.some((b) => !/^[\p{L}\p{N}_ -]{1,100}$/u.test(b) || forbidden.has(b))) return 'reference';
  if (ctx.event) return referenceRoots(ctx).includes(path) && (!expected.type || expected.type === 'string') ? null : 'reference';
  if (bits[0] === 'steps') {
    const source = ctx.definition.steps.slice(0, ctx.index).find((s) => s.id === bits[1]);
    if (!source || bits.length < 3) return 'reference';
    if (!ctx.condition && stepWorkspace(source) !== ctx.workspace) return 'transfer';
  }
  const root = referenceRoots(ctx).find((r) => path === r || path.startsWith(`${r}.`));
  if (!root || ((root === 'receipt' || root === 'record' || root.startsWith('steps.')) && path === root)) return 'reference';
  if (bits[0] === 'params' && bits.length === 2 && expected.type) {
    const type = ctx.definition.parameters[bits[1]]?.type;
    if (type !== expected.type && !(type === 'integer' && expected.type === 'number')) return 'type';
  }
  return null;
}
function mappingIssues(value: Json, schema: Schema, root: Schema, ctx: RefContext, path: string): Issue[] {
  if (isReference(value)) { const code = validateReference(value.$ref, ctx, resolveSchema(schema, root)); return code ? [{ path, code }] : []; }
  const s = resolveSchema(schema, root);
  if (Array.isArray(value)) return value.flatMap((v, i) => mappingIssues(v, s.items ?? {}, root, ctx, `${path}.${i}`));
  if (isObject(value)) return Object.entries(value).flatMap(([k, v]) => mappingIssues(v, s.properties?.[k] ?? {}, root, ctx, `${path}.${k}`));
  return [];
}
export function validateDefinition(def: Definition, caps: Capability[], apps: AppAccess[]): Issue[] {
  const errors: Issue[] = [];
  const add = (path: string, code: Issue['code']) => errors.push({ path, code });
  if (!def.steps.length || def.steps.length > 64 || Object.keys(def.parameters).length > 50) add('steps', 'budget');
  if (!Number.isInteger(def.max_calls) || def.max_calls < 1 || def.max_calls > 10000 || !Number.isInteger(def.timeout_s) || def.timeout_s < 1 || def.timeout_s > 2592000) add('limits', 'budget');
  for (const [name, p] of Object.entries(def.parameters)) if (!identifier.test(name) || forbidden.has(name) || !parameterTypes.includes(p.type)) add(name, 'format');
  const ids = def.steps.map((s) => s.id);
  def.steps.forEach((step, index) => {
    if (!identifier.test(step.id) || step.id === 'end' || ids.indexOf(step.id) !== index) add(`steps.${index}`, 'graph');
    for (const next of [step.next, step.on_error, ...(step.kind === 'branch' ? [step.then, step.otherwise] : [])]) if (next != null && next !== 'end' && ids.indexOf(next) <= index) add(step.id, 'graph');
    const ctx: RefContext = { definition: def, index, workspace: stepWorkspace(step), capabilities: caps };
    const checkRead = (ref: ReadSpec, readOnly: boolean, receipt = false) => {
      const cap = availableCapabilities(caps, apps, readOnly).find((c) => c.id === ref.operation && c.version === ref.operation_version);
      if (!cap || !targets(cap, apps).includes(ref.workspace_id)) { add(step.id, 'operation'); return; }
      if (receipt && ref.workspace_id !== stepWorkspace(step)) add(step.id, 'transfer');
      const inputSchema = automationInputSchema(cap);
      errors.push(...validateValue(ref.input, inputSchema, inputSchema, `${step.id}.input`, true));
      errors.push(...mappingIssues(ref.input, inputSchema, inputSchema, { ...ctx, workspace: ref.workspace_id, receipt }, step.id));
    };
    const checkPredicate = (p: Predicate, record: boolean) => {
      for (const v of [p.left, p.right]) {
        errors.push(...validateValue(v, {}, {}, step.id, true));
        errors.push(...mappingIssues(v, {}, {}, { ...ctx, record, condition: true }, step.id));
      }
      if (p.op === 'in' && !isReference(p.right) && !Array.isArray(p.right)) add(step.id, 'type');
    };
    if (step.kind === 'call') { checkRead(step, false); if (step.result_read) checkRead(step.result_read, true, true); }
    if (step.kind === 'wait') {
      checkRead(step.read, true); checkPredicate(step.until, true); if (step.reject) checkPredicate(step.reject, true);
      if (!Number.isInteger(step.poll_s) || step.poll_s < 1 || step.poll_s > 3600 || !Number.isInteger(step.timeout_s) || step.timeout_s < 1 || step.timeout_s > 2592000) add(step.id, 'range');
    }
    if (step.kind === 'branch') checkPredicate(step.condition, false);
    if (step.kind === 'fail' && !identifier.test(step.code)) add(step.id, 'format');
  });
  if (new TextEncoder().encode(JSON.stringify(def)).length > 262144) add('definition', 'budget');
  return errors;
}
export function parameterSchema(def: Definition): Schema {
  return { type: 'object', properties: Object.fromEntries(Object.entries(def.parameters).map(([k, p]) => [k, { type: p.type }])), required: Object.entries(def.parameters).filter(([, p]) => p.required).map(([k]) => k), additionalProperties: false };
}
export function validateParameters(def: Definition, values: Values, event = false): Issue[] {
  const schema = parameterSchema(def);
  const errors = validateValue(values, schema, schema, 'input', event);
  if (new TextEncoder().encode(JSON.stringify(values)).length > 262144) errors.push({ path: 'input', code: 'budget' });
  return errors;
}
export function exactScope(def: Definition, eventWorkspace?: string | null): Pick<Policy, 'tools' | 'workspaces'> {
  const refs = readsOf(def);
  return { tools: [...new Set(refs.map((r) => r.operation))].sort(), workspaces: [...new Set([...refs.map((r) => r.workspace_id), eventWorkspace].filter((w): w is string => !!w))].sort() };
}
export function validateTrigger(def: Definition, trigger: TriggerSpec, policy: Policy, now = Date.now() / 1000): Issue[] {
  const schema = parameterSchema(def);
  const errors = validateParameters(def, trigger.input, trigger.kind === 'event');
  if (trigger.kind === 'event') {
    if (!trigger.event_type || !trigger.workspace_id) errors.push({ path: 'event', code: 'required' });
    errors.push(...mappingIssues(trigger.input, schema, schema, { definition: def, capabilities: [], index: 0, workspace: null, event: true }, 'input'));
  } else {
    if (!/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(trigger.at ?? '')) errors.push({ path: 'at', code: 'format' });
    try { if (!trigger.timezone) throw new Error(); new Intl.DateTimeFormat('en', { timeZone: trigger.timezone }).format(); } catch { errors.push({ path: 'timezone', code: 'format' }); }
    if (!trigger.weekdays.length || new Set(trigger.weekdays).size !== trigger.weekdays.length || trigger.weekdays.some((d) => !Number.isInteger(d) || d < 0 || d > 6)) errors.push({ path: 'weekdays', code: 'range' });
  }
  if (!Number.isInteger(trigger.grace_s) || trigger.grace_s < 1 || trigger.grace_s > 86400) errors.push({ path: 'grace_s', code: 'range' });
  if (!Number.isInteger(policy.max_runs) || policy.max_runs < 1 || policy.max_runs > 10000) errors.push({ path: 'max_runs', code: 'range' });
  if (policy.until_revoked ? policy.expires_at !== null : policy.expires_at === null || !Number.isFinite(policy.expires_at) || policy.expires_at <= now) errors.push({ path: 'expires_at', code: 'lifecycle' });
  const scope = exactScope(def, trigger.kind === 'event' ? trigger.workspace_id : null);
  if (scope.workspaces.length > 50 || scope.tools.length > 128) errors.push({ path: 'policy', code: 'budget' });
  if (!scope.tools.length || canonical(policy.tools) !== canonical(scope.tools) || canonical(policy.workspaces) !== canonical(scope.workspaces)) errors.push({ path: 'policy', code: 'operation' });
  return errors;
}
export function runActions(status: string): ('pause' | 'resume' | 'cancel')[] {
  if (['paused', 'blocked', 'unknown'].includes(status)) return ['resume', 'cancel'];
  if (status === 'failed') return ['resume'];
  if (['queued', 'running', 'retrying', 'waiting_input', 'waiting_approval'].includes(status)) return ['pause', 'cancel'];
  return [];
}
export function uncertainFailure(error: { status?: number; upstream?: boolean; code?: string; detail?: Record<string, unknown> }, retrying = false): boolean {
  // Authorization precedes the registry's replay lookup. A refusal after a lost response
  // cannot establish whether the ORIGINAL request committed before permissions changed.
  if (retrying && error.code !== 'idempotent_failure') return true;
  // A 409 may describe an in-flight receipt, rather than a refused request.
  if (['run_in_progress', 'idempotency_in_progress', 'outcome_unknown', 'unknown', 'run_result_unavailable', 'result_access_unverifiable', 'idempotency_conflict'].includes(String(error.detail?.code ?? error.code))) return true;
  return !!error.upstream || !error.status || error.status >= 500 || error.status === 408 || error.status === 429;
}

/** Store only a digest and key in sessionStorage, never parameters, definitions, or business data.
 * Matching retries after reload recover the same key. Pending intents survive SPA remounts in memory.
 * The caller removes a ticket only after a definitive operation response. */
export interface IntentTicket { operation: string; digest: string; key: string }
export async function intentDigest(operation: string, input: Values): Promise<string> {
  const bytes = new TextEncoder().encode(canonical([operation, input]));
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('');
}
export function reuseTicket(previous: IntentTicket | null, operation: string, digest: string, key: string): IntentTicket {
  if (previous && (previous.operation !== operation || previous.digest !== digest)) throw new Error('pending_intent');
  return previous ?? { operation, digest, key };
}
