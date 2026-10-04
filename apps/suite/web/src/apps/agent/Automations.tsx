import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { ApiError, get, useSession } from '@synwevo/auth-client';
import { useDialog, useI18n, type Params } from '@synwevo/ui';
import { catalog, newKey, op, read, type CatalogApp } from '../../shared/ops';
import { Empty, Failure, Loading, RunBadge } from '../../shared/states';
import {
  automationInputSchema, availableCapabilities, canonical, emptyDefinition, emptyRead, exactScope, intentDigest, isObject, isReference,
  parameterSchema, parameterTypes, referenceRoots, resolveSchema, reuseTicket, runActions, seed, stepWorkspace,
  targets, uncertainFailure, validateDefinition, validateParameters, validateTrigger, validateValue,
  type Capability, type Definition, type Delivery, type Flow, type FlowTrigger, type FlowVersion, type IntentTicket,
  type Issue, type Json, type ParameterType, type Policy, type Predicate, type ReadSpec, type RefContext,
  type Run, type RunDetail, type Schema, type Step, type TriggerSpec, type Values,
} from './automationModel';
import { automationCopy, type AutomationText } from './automations.messages';
import { agentErrorText } from './errors';
import './automations.css';

function useText() {
  const { t, locale } = useI18n();
  const m = (key: AutomationText, params?: Params) => t(automationCopy[key][0], params);
  const date = (stamp: number | null | undefined) => stamp == null || !Number.isFinite(stamp) ? '—' : new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(stamp * 1000);
  return { m, t, locale, date };
}
const payload = (value: object): Values => JSON.parse(JSON.stringify(value)) as Values;
const safeCode = (value: unknown) => typeof value === 'string' && /^[A-Za-z0-9_.-]{1,120}$/.test(value) ? value : '';
function ErrorNotice({ error, retry }: { error: unknown; retry?: () => void }) {
  const { m, t } = useText();
  const code = safeCode(error instanceof ApiError ? error.code === 'idempotent_failure' ? error.detail?.code : error.code : error instanceof Error ? error.message : error);
  // A data-flow refusal or a personal step says exactly what the source's policy decided.
  const decided = code.startsWith('transfer_') || code === 'person_required' ? agentErrorText({ code }, t) : null;
  const key: AutomationText = code === 'pending_intent' ? 'conflictIntent' : code === 'flow_version_conflict' ? 'conflict'
    : /operation_failed|operation_cancelled/.test(code) ? 'operationEnded'
    : /reconciliation|outcome_unknown/.test(code) ? 'reconciliation' : /source_changed|source_unavailable|result_unverifiable/.test(code) ? 'changedSource'
    : /expired|exhausted|deadline|quota/.test(code) ? 'limitError' : /unknown_operation|app_unavailable/.test(code) ? 'unavailable'
    : error instanceof ApiError && error.status === 403 ? 'denied' : 'error';
  const sanitized = new Error(decided ?? m(key));
  return <div><Failure error={sanitized} onRetry={retry} />
    {code && <p className="muted small">{m('errorCode', { code })}</p>}
    {error instanceof ApiError && error.requestId && <p className="muted small">{m('requestId', { id: error.requestId })}</p>}
  </div>;
}
function Issues({ issues }: { issues: Issue[] }) {
  const { m } = useText();
  if (!issues.length) return null;
  return <div className="au-notice au-error" role="alert" tabIndex={-1}><strong>{m('validation')}</strong><ul>
    {issues.slice(0, 20).map((i, n) => <li key={n}><code>{i.path}</code>: {m(`invalid_${i.code}`)}</li>)}
  </ul></div>;
}
function Status({ value }: { value: string }) {
  const { m } = useText();
  // Reuse the common run badge for its states, and add engine-specific states here.
  if (['queued', 'running', 'succeeded', 'partial', 'failed', 'cancelled', 'unknown', 'waiting_approval'].includes(value)) return <RunBadge status={value} />;
  const key = Object.hasOwn(automationCopy, value) ? value as AutomationText : 'unknown';
  return <span className={`badge ${['blocked', 'paused', 'waiting_input'].includes(value) ? 'warn' : ''}`}>{m(key)}</span>;
}
function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return <label className="field"><span>{label}</span>{children}</label>;
}
function NumberField({ label, value, onChange, min = 1, max }: { label: string; value: number; onChange: (n: number) => void; min?: number; max?: number }) {
  return <Field label={label}><input className="input" type="number" min={min} max={max} step={1} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.value === '' ? NaN : Number(e.target.value))} /></Field>;
}

function useResource<T>(operation: string, input: Values, revision = 0) {
  const serialized = canonical(input);
  const [state, setState] = useState<{ data?: T; error?: unknown; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const previousQuery = useRef('');
  const reload = useCallback(() => setTick((n) => n + 1), []);
  useEffect(() => {
    const abort = new AbortController();
    const query = `${operation}:${serialized}`;
    const sameQuery = previousQuery.current === query;
    previousQuery.current = query;
    setState((old) => sameQuery ? { data: old.data, loading: true } : { loading: true });
    void read<T>(`agent.flow.${operation}`, JSON.parse(serialized), null, abort.signal)
      .then((data) => { if (!abort.signal.aborted) setState({ loading: false, data }); })
      .catch((error: unknown) => { if (!abort.signal.aborted) setState({ loading: false, error }); });
    return () => abort.abort();
  }, [operation, serialized, revision, tick]);
  return { ...state, reload };
}

interface PendingIntent { ticket: IntentTicket; input: Values }
// Memory is partitioned by signed-in session. Only the digest/key ticket is persisted to the tab.
const pendingIntents = new Map<string, PendingIntent>();
const flights = new Set<string>();
function useMutation(owner: string, onSaved: (operation: string, output: Values) => void) {
  const storageKey = `synwevo.automation.intent.${owner}`;
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(() => pendingIntents.has(owner));
  const [error, setError] = useState<unknown>();
  const [status, setStatus] = useState<string>();
  const [saved, setSaved] = useState(false);
  const live = useRef(true);
  const savedRef = useRef(onSaved); savedRef.current = onSaved;
  const ticketRef = useRef<IntentTicket | null>(null);
  if (!ticketRef.current) {
    try {
      const item = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
      if (item && typeof item.operation === 'string' && typeof item.digest === 'string' && typeof item.key === 'string') ticketRef.current = item;
    } catch { /* Private browsing still retains the in-memory intent. */ }
  }
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (pendingIntents.has(owner) || ticketRef.current) { e.preventDefault(); e.returnValue = ''; } };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [owner]);
  const clear = () => {
    pendingIntents.delete(owner); ticketRef.current = null;
    try { sessionStorage.removeItem(storageKey); } catch { /* storage unavailable */ }
  };
  async function execute(suffix?: string, input?: Values) {
    if (flights.has(owner)) return;
    flights.add(owner); setBusy(true); setError(undefined); setSaved(false); setStatus(undefined);
    let dispatched = false;
    const replaying = pendingIntents.has(owner) || ticketRef.current !== null;
    try {
      let intent = pendingIntents.get(owner);
      if (suffix && input) {
        const operation = `agent.flow.${suffix}`;
        const digest = await intentDigest(operation, input);
        const ticket = reuseTicket(intent?.ticket ?? ticketRef.current, operation, digest, newKey());
        intent = { ticket, input: structuredClone(input) };
        pendingIntents.set(owner, intent); ticketRef.current = ticket;
        try { sessionStorage.setItem(storageKey, JSON.stringify(ticket)); } catch { /* memory remains authoritative */ }
      }
      if (!intent) throw new Error('pending_intent');
      setPending(true); dispatched = true;
      const body = intent.ticket.operation === 'agent.flow.start' ? { ...intent.input, idempotency_key: intent.ticket.key } : intent.input;
      const result = await op<Values>(intent.ticket.operation, body, { workspace: null, key: intent.ticket.key });
      if (!live.current) return;
      const out = result.output;
      const hasIdentity = isObject(out) && (typeof out.id === 'string' || typeof out.flow_id === 'string' || typeof out.trigger_id === 'string');
      if (result.status === 'succeeded' && hasIdentity) {
        clear(); setPending(false); setSaved(true); savedRef.current(intent.ticket.operation, out);
      } else if (['failed', 'cancelled'].includes(result.status)) {
        clear(); setPending(false); setStatus(result.status);
      } else { setPending(true); setStatus(result.status === 'succeeded' ? 'unknown' : result.status); }
    } catch (e) {
      if (!live.current) return;
      const uncertain = !dispatched || !(e instanceof ApiError) || uncertainFailure(e, replaying);
      if (!uncertain) clear();
      setPending(pendingIntents.has(owner)); setError(e);
    } finally { flights.delete(owner); if (live.current) setBusy(false); }
  }
  return { busy, pending, error, status, saved, recovered: !!ticketRef.current && !pendingIntents.has(owner),
    locked: busy || pending, start: (suffix: string, input: Values) => void execute(suffix, input), retry: () => void execute() };
}
type Mutation = ReturnType<typeof useMutation>;
function MutationFeedback({ action }: { action: Mutation }) {
  const { m } = useText();
  return <div className="au-feedback" aria-live="polite">
    {action.busy && <p role="status">{m('saving')}</p>}
    {action.saved && <p role="status">{m('saved')}</p>}
    {action.error != null && <ErrorNotice error={action.error} />}
    {action.status && <Status value={action.status} />}
    {action.pending && !action.busy && <div className="au-notice"><p>{m('pending')}</p><button type="button" className="btn" disabled={action.busy} onClick={action.retry}>{m('retrySame')}</button></div>}
    {action.recovered && <p className="au-notice">{m('recovered')}</p>}
  </div>;
}

/** Structured, recursive fields for the registry's actual input schema. No code evaluation. */
function ValueEditor({ label, schema, root = schema, value, onChange, context, depth = 0 }: {
  label: string; schema: Schema; root?: Schema; value: Json; onChange: (v: Json) => void; context?: RefContext; depth?: number;
}) {
  const { m } = useText();
  const id = useId();
  const [newField, setNewField] = useState('');
  const s = resolveSchema(schema, root);
  const refs = context ? referenceRoots(context) : [];
  const reference = isReference(value);
  const union = s.anyOf ?? s.oneOf;
  const [variant, setVariant] = useState(() => Math.max(0, union?.findIndex((u) => !validateValue(value, u, root, '', !!context).length) ?? 0));
  const selectedVariant = union && union[variant] && !validateValue(value, union[variant], root, '', !!context).length ? variant
    : Math.max(0, union?.findIndex((u) => !validateValue(value, u, root, '', !!context).length) ?? 0);
  const genericType = value === null ? 'null' : Array.isArray(value) ? 'array' : typeof value === 'object' ? 'object' : typeof value;
  const kind = s.type ?? (s.properties ? 'object' : genericType);
  const child = (key: string, spec: Schema, v: Json, update: (v: Json) => void) => <ValueEditor key={key} label={key} schema={spec} root={root} value={v} onChange={update} context={context} depth={depth + 1} />;
  if (depth > 20 || s.type === 'unsupported' || s.allOf) return <p role="alert">{label}: {m('unsupported')}</p>;
  return <fieldset className="au-value"><legend>{label}</legend>
    {context && refs.length > 0 && <Field label={m('source')}><select className="input" value={reference ? 'ref' : 'value'} onChange={(e) => onChange(e.target.value === 'ref' ? { $ref: refs[0] } : seed(schema, root))}>
      <option value="value">{m('value')}</option><option value="ref">{m('mapping')}</option>
    </select></Field>}
    {reference ? <div className="col">
      <Field label={m('source')}><select className="input" value={refs.find((r) => value.$ref === r || value.$ref.startsWith(`${r}.`)) ?? ''} onChange={(e) => onChange({ $ref: e.target.value })}>
        <option value="">{m('choose')}</option>{refs.map((r) => <option key={r} value={r}>{r}</option>)}
      </select></Field>
      <Field label={m('path')}><input className="input mono" aria-describedby={`${id}-hint`} value={value.$ref} maxLength={2400} onChange={(e) => onChange({ $ref: e.target.value })} /></Field>
      <p id={`${id}-hint`} className="muted small">{m('mappingNote')}</p>
    </div> : union ? <div className="col">
      <Field label={m('type')}><select className="input" value={selectedVariant} onChange={(e) => { const n = Number(e.target.value); setVariant(n); onChange(seed(union[n], root)); }}>
        {union.map((u, i) => { const v = resolveSchema(u, root); return <option key={i} value={i}>{v.title ?? (v.type && Object.hasOwn(automationCopy, v.type) ? m(v.type as AutomationText) : String(i + 1))}</option>; })}
      </select></Field>{child(label, union[selectedVariant] ?? union[0], value, onChange)}
    </div> : s.enum || s.const !== undefined ? <Field label={m('value')}><select className="input" value={canonical(value)} onChange={(e) => onChange(JSON.parse(e.target.value))}>
      <option value="" disabled>{m('choose')}</option>{(s.enum ?? [s.const!]).map((v) => <option key={canonical(v)} value={canonical(v)}>{v === null ? m('null') : String(v)}</option>)}
    </select></Field> : <>
      {!s.type && !s.properties && <Field label={m('type')}><select className="input" value={genericType} onChange={(e) => onChange(seed({ type: e.target.value }))}>
        {(['string', 'number', 'boolean', 'object', 'array', 'null'] as const).map((v) => <option key={v} value={v}>{m(v)}</option>)}
      </select></Field>}
      {kind === 'object' ? <div className="col">
        {Object.entries(s.properties ?? {}).map(([k, spec]) => {
          const obj = isObject(value) ? value : {}; const included = Object.hasOwn(obj, k); const required = s.required?.includes(k);
          return <div key={k} className="au-property">{!required && <label className="au-check"><input type="checkbox" checked={included} onChange={(e) => { const next = { ...obj }; if (e.target.checked) next[k] = seed(spec, root); else delete next[k]; onChange(next); }} />{k} · {m('optional')}</label>}
            {(included || required) && child(k, spec, included ? obj[k] : seed(spec, root), (v) => onChange({ ...obj, [k]: v }))}
          </div>;
        })}
        {Object.entries(isObject(value) ? value : {}).filter(([k]) => !Object.hasOwn(s.properties ?? {}, k)).map(([k, v]) => <div className="au-property" key={k}>
          {child(k, typeof s.additionalProperties === 'object' ? s.additionalProperties : {}, v, (x) => onChange({ ...(value as Values), [k]: x }))}
          <button type="button" className="btn ghost sm" aria-label={`${m('remove')} ${k}`} onClick={() => { const next = { ...(value as Values) }; delete next[k]; onChange(next); }}>{m('remove')}</button>
        </div>)}
        {s.additionalProperties !== false && <div className="row wrap">
          <Field label={m('fieldName')}><input className="input" value={newField} maxLength={100} onChange={(e) => setNewField(e.target.value)} /></Field>
          <button type="button" className="btn sm" disabled={!newField || ['__proto__', 'constructor', 'prototype', '$ref'].includes(newField) || Object.hasOwn(isObject(value) ? value : {}, newField)} onClick={() => { onChange({ ...(isObject(value) ? value : {}), [newField]: seed(typeof s.additionalProperties === 'object' ? s.additionalProperties : {}, root) }); setNewField(''); }}>{m('addField')}</button>
        </div>}
      </div> : kind === 'array' ? <div className="col">
        {(Array.isArray(value) ? value : []).map((v, i) => <div className="au-property" key={i}>{child(String(i), s.items ?? {}, v, (x) => onChange((value as Json[]).map((old, n) => n === i ? x : old)))}
          <button type="button" className="btn ghost sm" aria-label={`${m('remove')} ${i + 1}`} onClick={() => onChange((value as Json[]).filter((_, n) => n !== i))}>{m('remove')}</button></div>)}
        <button type="button" className="btn sm" disabled={Array.isArray(value) && value.length >= (s.maxItems ?? 1000)} onClick={() => onChange([...(Array.isArray(value) ? value : []), seed(s.items ?? {}, root)])}>{m('addItem')}</button>
      </div> : kind === 'boolean' ? <Field label={m('value')}><select className="input" value={String(value)} onChange={(e) => onChange(e.target.value === 'true')}><option value="true">{m('yes')}</option><option value="false">{m('no')}</option></select></Field>
        : kind === 'number' || kind === 'integer' ? <Field label={m('value')}><input className="input" type="number" step={kind === 'integer' ? 1 : 'any'} min={s.minimum} max={s.maximum} value={typeof value === 'number' && Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))} /></Field>
        : kind === 'null' ? <p className="muted">{m('null')}</p>
        : <Field label={m('value')}><textarea className="input" rows={2} value={typeof value === 'string' ? value : ''} maxLength={s.maxLength ?? 262144} onChange={(e) => onChange(e.target.value)} /></Field>}
    </>}
  </fieldset>;
}

interface EditingContext { caps: Capability[]; apps: CatalogApp[] }
function OperationEditor({ value, onChange, context, access, readOnly = false, workspaceOnly }: {
  value: ReadSpec; onChange: (v: ReadSpec) => void; context: RefContext; access: EditingContext; readOnly?: boolean; workspaceOnly?: string | null;
}) {
  const { m, locale } = useText();
  const choices = availableCapabilities(access.caps, access.apps, readOnly).filter((c) => workspaceOnly === undefined || targets(c, access.apps).includes(workspaceOnly));
  const cap = choices.find((c) => c.id === value.operation && c.version === value.operation_version);
  const spaces = cap ? targets(cap, access.apps).filter((w) => workspaceOnly === undefined || w === workspaceOnly) : [];
  return <div className="col au-operation">
    <Field label={m('operation')}><select className="input" value={cap?.id ?? ''} onChange={(e) => {
      const c = choices.find((x) => x.id === e.target.value); if (!c) return;
      const possible = targets(c, access.apps); const workspace = workspaceOnly !== undefined ? workspaceOnly : possible.length === 1 && possible[0] === null ? null : possible.includes(value.workspace_id) ? value.workspace_id : null;
      onChange({ operation: c.id, operation_version: c.version, workspace_id: workspace, input: seed(automationInputSchema(c)) as Values });
    }}><option value="">{m('choose')}</option>{choices.map((c) => <option key={c.id} value={c.id}>{c.title[locale] ?? c.title.en ?? c.id} · {c.id}</option>)}</select></Field>
    {!choices.length && <p className="muted">{m('noOperations')}</p>}
    {value.operation && !cap && <p className="au-notice">{m('invalid_operation')} <code>{value.operation}</code></p>}
    {cap && <>
      <div className="row wrap"><span className="badge">{m('version', { n: cap.version })}</span><span className="badge">{m(cap.side_effect === 'external' ? 'external' : cap.side_effect === 'write' ? 'write' : 'none')}</span></div>
      {spaces.length === 1 && spaces[0] === null ? <p>{m('personalTarget')}</p> : <Field label={m('workspace')}><select className="input" value={value.workspace_id ?? ''} onChange={(e) => onChange({ ...value, workspace_id: e.target.value || null })}>
        <option value="">{m('choose')}</option>{spaces.map((w) => <option key={w} value={w!}>{access.apps.find((a) => a.id === cap.app)?.workspaces.find((x) => x.id === w)?.name ?? w} · {w}</option>)}
      </select></Field>}
      <ValueEditor label={m('params')} schema={automationInputSchema(cap)} value={value.input} onChange={(v) => onChange({ ...value, input: v as Values })} context={{ ...context, workspace: value.workspace_id }} />
    </>}
  </div>;
}

function PredicateEditor({ value, onChange, context }: { value: Predicate; onChange: (v: Predicate) => void; context: RefContext }) {
  const { m } = useText();
  return <div className="au-grid">
    <ValueEditor label={m('left')} schema={{}} value={value.left} onChange={(v) => onChange({ ...value, left: v })} context={context} />
    <Field label={m('compare')}><select className="input" value={value.op} onChange={(e) => onChange({ ...value, op: e.target.value as Predicate['op'], right: e.target.value === 'in' ? [] : value.right })}>
      {(['eq', 'ne', 'in', 'exists'] as const).map((v) => <option key={v} value={v}>{m(v)}</option>)}
    </select></Field>
    {value.op !== 'exists' && <ValueEditor label={m('right')} schema={value.op === 'in' ? { type: 'array' } : {}} value={value.right} onChange={(v) => onChange({ ...value, right: v })} context={context} />}
  </div>;
}
function Scope({ definition, policy, access }: { definition?: Definition; policy?: Policy; access: EditingContext }) {
  const { m, date, locale } = useText(); const scope = policy ?? exactScope(definition!);
  return <div className="au-scope">
    <h3>{m('scope')}</h3>
    <dl><dt>{m('tools')}</dt><dd><ul>{scope.tools.map((x) => <li key={x}><code>{x}</code></li>)}</ul></dd>
      <dt>{m('spaces')}</dt><dd>{scope.workspaces.length ? <ul>{scope.workspaces.map((w) => <li key={w}>{access.apps.flatMap((a) => a.workspaces).find((s) => s.id === w)?.name ?? w} <code>{w}</code></li>)}</ul> : m('personalTarget')}</dd>
      {policy && <><dt>{m('maxRuns')}</dt><dd>{policy.max_runs.toLocaleString(locale)}</dd><dt>{m('expiry')}</dt><dd>{policy.until_revoked ? m('untilRevoked') : date(policy.expires_at)}</dd></>}
    </dl><p className="muted small">{m('exactScope')}</p>
  </div>;
}

function DefinitionEditor({ initial, flow, access, action, close }: { initial: Definition; flow?: FlowVersion; access: EditingContext; action: Mutation; close: () => void }) {
  const { m } = useText(); const dialog = useDialog();
  const [definition, setDefinition] = useState(() => structuredClone(initial));
  const [name, setName] = useState(flow?.name ?? '');
  const [parameterName, setParameterName] = useState('');
  const [issues, setIssues] = useState<Issue[]>([]);
  const [confirming, setConfirming] = useState(false);
  const lock = action.locked || confirming;
  const editStep = (index: number, next: Step) => setDefinition((d) => ({ ...d, steps: d.steps.map((s, i) => i === index ? next : s) }));
  const add = (kind: Step['kind']) => {
    let n = definition.steps.length + 1; while (definition.steps.some((s) => s.id === `step${n}`)) n++;
    const base = { id: `step${n}` };
    const step: Step = kind === 'call' ? { ...base, ...emptyRead(), kind } : kind === 'wait' ? { ...base, kind, read: emptyRead(), state: 'waiting_input', until: { left: { $ref: 'record.status' }, op: 'eq', right: '' }, poll_s: 60, timeout_s: 604800 } : kind === 'branch' ? { ...base, kind, condition: { left: '', op: 'eq', right: '' }, then: 'end', otherwise: 'end' } : { ...base, kind, code: 'business_rule_failed' };
    setDefinition({ ...definition, steps: [...definition.steps, step] });
  };
  async function publish() {
    if (lock) return;
    const errors = validateDefinition(definition, access.caps, access.apps);
    if (!name.trim() || name.trim().length > 200) errors.unshift({ path: 'name', code: 'required' });
    setIssues(errors); if (errors.length) return;
    setConfirming(true);
    try {
      if (!await dialog.confirm(flow ? m('publish') : m('createPublish'), <div className="col"><strong>{name}</strong><p>{m('publishNote')}</p><Scope definition={definition} access={access} /></div>, m('confirm'))) return;
      action.start(flow ? 'publish' : 'create', flow ? payload({ flow_id: flow.flow_id, expected_version: flow.version, definition }) : payload({ name: name.trim(), definition }));
    } finally { setConfirming(false); }
  }
  return <form className="col gap16" onSubmit={(e) => { e.preventDefault(); void publish(); }}>
    <div className="suite-head"><h2>{flow ? m('publish') : m('create')}</h2></div><p className="muted">{m('publishNote')}</p>
    <fieldset disabled={lock} className="au-editor col gap16">
      {!flow && <Field label={m('name')}><input className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} required /></Field>}
      <section className="card"><div className="card-h"><h3>{m('params')}</h3></div><div className="card-b col">
        {Object.entries(definition.parameters).map(([k, p]) => <div className="row wrap" key={k}><code>{k}</code>
          <Field label={m('type')}><select className="input" value={p.type} onChange={(e) => setDefinition({ ...definition, parameters: { ...definition.parameters, [k]: { ...p, type: e.target.value as ParameterType } } })}>{parameterTypes.map((t) => <option key={t} value={t}>{m(t)}</option>)}</select></Field>
          <label className="au-check"><input type="checkbox" checked={p.required} onChange={(e) => setDefinition({ ...definition, parameters: { ...definition.parameters, [k]: { ...p, required: e.target.checked } } })} />{m('required')}</label>
          <button type="button" className="btn ghost sm" aria-label={`${m('remove')} ${k}`} onClick={() => { const next = { ...definition.parameters }; delete next[k]; setDefinition({ ...definition, parameters: next }); }}>{m('remove')}</button>
        </div>)}
        <div className="row wrap"><Field label={m('paramName')}><input className="input" value={parameterName} maxLength={80} onChange={(e) => setParameterName(e.target.value)} /></Field>
          <button type="button" className="btn sm" disabled={!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(parameterName) || ['constructor', 'prototype', '__proto__'].includes(parameterName) || Object.hasOwn(definition.parameters, parameterName) || Object.keys(definition.parameters).length >= 50} onClick={() => { setDefinition({ ...definition, parameters: { ...definition.parameters, [parameterName]: { type: 'string', required: true } } }); setParameterName(''); }}>{m('addParam')}</button></div>
      </div></section>
      <div><h3>{m('steps')}</h3><p className="muted">{m('accessNote')}</p><p className="muted small">{m('pinNote')}</p><p className="muted small">{m('transferNote')}</p></div>
      {definition.steps.map((step, index) => {
        const ctx: RefContext = { definition, index, workspace: stepWorkspace(step), capabilities: access.caps };
        const forward = definition.steps.slice(index + 1);
        const destination = (label: string, value: string | null | undefined, onChange: (v: string | null) => void, defaultLabel?: string) => <Field label={label}><select className="input" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
          {defaultLabel && <option value="">{defaultLabel}</option>}<option value="end">{m('end')}</option>{forward.map((s) => <option key={s.id} value={s.id}>{s.id}</option>)}
        </select></Field>;
        return <section className="card au-step" key={index}>
          <div className="card-h row wrap"><h3>{m('step', { n: index + 1 })} · {m(step.kind)}</h3><span className="spacer" />
            {([-1, 1] as const).map((delta) => <button className="btn ghost sm" key={delta} type="button" disabled={index + delta < 0 || index + delta >= definition.steps.length} aria-label={`${m(delta < 0 ? 'up' : 'down')} ${step.id}`} onClick={() => { const next = [...definition.steps]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; setDefinition({ ...definition, steps: next }); }}>{m(delta < 0 ? 'up' : 'down')}</button>)}
            <button type="button" className="btn ghost sm" aria-label={`${m('remove')} ${step.id}`} onClick={() => setDefinition({ ...definition, steps: definition.steps.filter((_, i) => i !== index) })}>{m('remove')}</button>
          </div><div className="card-b col gap16">
            <Field label={m('stepId')}><input className="input" maxLength={80} value={step.id} onChange={(e) => editStep(index, { ...step, id: e.target.value })} /></Field>
            {step.kind === 'call' && <><OperationEditor value={step} onChange={(v) => editStep(index, { ...step, ...v, result_read: v.workspace_id !== step.workspace_id || v.operation !== step.operation ? null : step.result_read })} context={ctx} access={access} />
              <label className="au-check"><input type="checkbox" checked={!!step.result_read} onChange={(e) => editStep(index, { ...step, result_read: e.target.checked ? { ...emptyRead(), workspace_id: step.workspace_id } : null })} />{m('readResult')}</label>
              {step.result_read && <div className="au-inset"><p className="muted small">{m('readResultNote')}</p><OperationEditor value={step.result_read} onChange={(v) => editStep(index, { ...step, result_read: v })} context={{ ...ctx, receipt: true }} access={access} readOnly workspaceOnly={step.workspace_id} /></div>}
            </>}
            {step.kind === 'wait' && <><OperationEditor value={step.read} onChange={(read) => editStep(index, { ...step, read })} context={ctx} access={access} readOnly />
              <Field label={m('waitState')}><select className="input" value={step.state} onChange={(e) => editStep(index, { ...step, state: e.target.value as 'waiting_input' | 'waiting_approval' })}><option value="waiting_input">{m('waiting_input')}</option><option value="waiting_approval">{m('waiting_approval')}</option></select></Field>
              <h4>{m('condition')}</h4><PredicateEditor value={step.until} onChange={(until) => editStep(index, { ...step, until })} context={{ ...ctx, record: true, condition: true }} />
              <label className="au-check"><input type="checkbox" checked={!!step.reject} onChange={(e) => editStep(index, { ...step, reject: e.target.checked ? { left: { $ref: 'record.status' }, op: 'eq', right: '' } : null })} />{m('addReject')}</label>
              {step.reject && <PredicateEditor value={step.reject} onChange={(reject) => editStep(index, { ...step, reject })} context={{ ...ctx, record: true, condition: true }} />}
              <div className="au-grid"><NumberField label={m('poll')} value={step.poll_s} max={3600} onChange={(poll_s) => editStep(index, { ...step, poll_s })} /><NumberField label={m('timeout')} value={step.timeout_s} max={2592000} onChange={(timeout_s) => editStep(index, { ...step, timeout_s })} /></div>
            </>}
            {step.kind === 'branch' && <><PredicateEditor value={step.condition} onChange={(condition) => editStep(index, { ...step, condition })} context={{ ...ctx, condition: true }} />
              {destination(m('then'), step.then, (v) => editStep(index, { ...step, then: v ?? 'end' }))}{destination(m('otherwise'), step.otherwise, (v) => editStep(index, { ...step, otherwise: v ?? 'end' }))}</>}
            {step.kind === 'fail' && <Field label={m('failureCode')}><input className="input" value={step.code} maxLength={80} onChange={(e) => editStep(index, { ...step, code: e.target.value })} /></Field>}
            {(step.kind === 'call' || step.kind === 'wait') && <div className="au-grid">{destination(m('next'), step.next, (next) => editStep(index, { ...step, next }), m('sequential'))}{destination(m('onError'), step.on_error, (on_error) => editStep(index, { ...step, on_error }), m('stop'))}</div>}
          </div>
        </section>;
      })}
      <div className="row wrap">{(['call', 'wait', 'branch', 'fail'] as const).map((kind) => <button type="button" className="btn" key={kind} disabled={definition.steps.length >= 64} onClick={() => add(kind)}>{m('addStep')} · {m(kind)}</button>)}</div>
      <section className="card"><div className="card-h"><h3>{m('limits')}</h3></div><div className="card-b au-grid"><NumberField label={m('maxCalls')} value={definition.max_calls} max={10000} onChange={(max_calls) => setDefinition({ ...definition, max_calls })} /><NumberField label={m('deadline')} value={definition.timeout_s} max={2592000} onChange={(timeout_s) => setDefinition({ ...definition, timeout_s })} /></div></section>
      <Issues issues={issues} />
      <div className="au-actions"><button type="submit" className="btn primary">{flow ? m('publish') : m('createPublish')}</button>
        <button type="button" className="btn" onClick={async () => { if (await dialog.confirm(m('cancelEdit'), m('discard'), m('confirm'))) close(); }}>{m('cancelEdit')}</button></div>
    </fieldset>
  </form>;
}

function DataView({ value, depth = 0 }: { value: Json; depth?: number }) {
  const { m } = useText();
  const [limit, setLimit] = useState(50);
  if (depth > 24) return <span>…</span>;
  if (isReference(value)) return <code>{value.$ref}</code>;
  if (Array.isArray(value)) return <div><ol className="au-data">{value.slice(0, limit).map((v, i) => <li key={i}><DataView value={v} depth={depth + 1} /></li>)}</ol>{value.length > limit && <button type="button" className="btn ghost sm" onClick={() => setLimit(limit + 50)}>{m('more')}</button>}</div>;
  if (isObject(value)) { const entries = Object.entries(value); return <div><dl className="au-data">{entries.slice(0, limit).map(([k, v]) => <div key={k}><dt>{k}</dt><dd><DataView value={v} depth={depth + 1} /></dd></div>)}</dl>{entries.length > limit && <button type="button" className="btn ghost sm" onClick={() => setLimit(limit + 50)}>{m('more')}</button>}</div>; }
  return <span className="au-literal">{value === null ? 'null' : String(value)}</span>;
}
function DefinitionSummary({ flow, access }: { flow: FlowVersion; access: EditingContext }) {
  const { m, locale } = useText();
  const readSummary = (r: ReadSpec) => {
    const cap = access.caps.find((c) => c.id === r.operation);
    return <div className="col"><strong>{cap?.title[locale] ?? r.operation}</strong><code>{r.operation} · v{r.operation_version}</code>
      <p>{m('workspace')}: {r.workspace_id ? `${access.apps.flatMap((a) => a.workspaces).find((w) => w.id === r.workspace_id)?.name ?? ''} · ${r.workspace_id}` : m('personalTarget')}</p>
      <DataView value={r.input} />
    </div>;
  };
  return <div className="col gap16"><div className="row wrap"><strong>{flow.name}</strong><span className="badge">{m('version', { n: flow.version })}</span><span className="badge">{m('fixed')}</span></div>
    <ol className="au-outline">{flow.definition.steps.map((s) => <li key={s.id}><details><summary><code>{s.id}</code> · {m(s.kind)}{s.kind === 'call' && ` · ${s.operation}`}</summary>
      <div className="au-inset col">
        {s.kind === 'call' && <>{readSummary(s)}{s.result_read && <><h4>{m('readResult')}</h4>{readSummary(s.result_read)}</>}</>}
        {s.kind === 'wait' && <>{readSummary(s.read)}<h4>{m('condition')}</h4><DataView value={payload(s.until)} />{s.reject && <><h4>{m('reject')}</h4><DataView value={payload(s.reject)} /></>}<p>{m('timeout')}: {s.timeout_s} · {m('poll')}: {s.poll_s}</p></>}
        {s.kind === 'branch' && <><DataView value={payload(s.condition)} /><p>{m('then')}: {s.then} · {m('otherwise')}: {s.otherwise}</p></>}
        {s.kind === 'fail' && <code>{s.code}</code>}
        {(s.kind === 'call' || s.kind === 'wait') && <p>{m('next')}: {s.next ?? m('sequential')} · {m('onError')}: {s.on_error ?? m('stop')}</p>}
      </div></details></li>)}</ol>
    <p className="muted small">{m('maxCalls')}: {flow.definition.max_calls} · {m('deadline')}: {flow.definition.timeout_s}</p>
  </div>;
}
function ManualRun({ flow, access, action }: { flow: FlowVersion; access: EditingContext; action: Mutation }) {
  const { m } = useText(); const dialog = useDialog(); const schema = parameterSchema(flow.definition);
  const [input, setInput] = useState<Values>(() => seed(schema) as Values);
  const [issues, setIssues] = useState<Issue[]>([]); const [confirming, setConfirming] = useState(false);
  async function start() {
    if (action.locked || confirming) return;
    const errors = validateParameters(flow.definition, input); setIssues(errors); if (errors.length) return;
    setConfirming(true);
    try {
      if (!await dialog.confirm(m('runConfirm'), <div className="col gap16"><DefinitionSummary flow={flow} access={access} /><h3>{m('params')}</h3><DataView value={input} /><Scope definition={flow.definition} access={access} /></div>, m('run'))) return;
      action.start('start', { flow_id: flow.flow_id, version: flow.version, parameters: input });
    } finally { setConfirming(false); }
  }
  return <section className="card"><div className="card-h"><h2>{m('manual')}</h2></div><form className="card-b col" onSubmit={(e) => { e.preventDefault(); void start(); }}>
    <fieldset className="au-editor col" disabled={action.locked || confirming}><ValueEditor label={m('params')} schema={schema} value={input} onChange={(v) => setInput(v as Values)} /><Issues issues={issues} /><div><button type="submit" className="btn primary">{m('run')}</button></div></fieldset>
  </form></section>;
}

/** Each list follows its server cursor; no guessed timestamps or local sorting of cursor pages. */
function usePages<T>(suffix: string, input: Values, field: string, revision: number) {
  const key = canonical(input);
  const [rows, setRows] = useState<T[]>([]); const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(); const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0); const abortRef = useRef<AbortController | null>(null); const flight = useRef(false);
  const load = useCallback(async (after = '') => {
    if (flight.current) return;
    flight.current = true;
    const abort = new AbortController(); abortRef.current = abort; setLoading(true); setError(undefined);
    try {
      const out = await read<Record<string, unknown>>(`agent.flow.${suffix}`, { ...JSON.parse(key), after, limit: 50 }, null, abort.signal);
      if (!abort.signal.aborted) { setRows((old) => after ? [...old, ...(out[field] as T[])] : out[field] as T[]); setNext(out.next as string | null); }
    } catch (e) { if (!abort.signal.aborted) setError(e); }
    finally { if (!abort.signal.aborted) { setLoading(false); flight.current = false; } }
  }, [suffix, key, field]);
  useEffect(() => { setRows([]); setNext(null); flight.current = false; void load(); return () => { abortRef.current?.abort(); flight.current = false; }; }, [load, revision, tick]);
  return { rows, next, error, loading, more: () => void load(next ?? ''), reload: () => setTick((n) => n + 1), retry: () => void load(rows.length && next ? next : '') };
}
function RunHistory({ flowId, revision, select }: { flowId: string; revision: number; select: (run: Run) => void }) {
  const { m, date } = useText(); const page = usePages<Run>('runs', { flow_id: flowId }, 'runs', revision);
  return <section className="card"><div className="card-h row wrap"><h2>{m('history')}</h2><span className="spacer" /><button type="button" className="btn ghost sm" disabled={page.loading} onClick={page.reload}>{m('refresh')}</button></div><div className="card-b col">
    {page.error != null && <ErrorNotice error={page.error} retry={page.retry} />}
    {!page.loading && page.error == null && !page.rows.length && <Empty title={m('noRuns')} />}
    {page.rows.map((r) => <button type="button" className="au-run-row" key={r.id} onClick={() => select(r)}><span className="col"><code>{r.id}</code><span className="muted small">{date(r.created_at)} · {m('version', { n: r.version })}</span></span><Status value={r.status} /></button>)}
    {page.loading && <Loading />}{page.next && <button type="button" className="btn" disabled={page.loading} onClick={page.more}>{m('more')}</button>}
  </div></section>;
}
function RunView({ runId, flowId, revision, action }: { runId: string; flowId: string; revision: number; action: Mutation }) {
  const { m, date } = useText(); const dialog = useDialog();
  const [cursor, setCursor] = useState(0); const [pages, setPages] = useState<RunDetail['history']>([]);
  const resource = useResource<RunDetail>('run', { run_id: runId, after: cursor, limit: 100 }, revision);
  const [confirming, setConfirming] = useState(false);
  const run = resource.data?.flow_id === flowId ? resource.data : undefined;
  // Each refresh reauthorizes protected outputs; a failed refresh removes the old snapshot.
  const refresh = () => { setCursor(0); setPages([]); resource.reload(); };
  useEffect(() => { setCursor(0); setPages([]); }, [revision]);
  useEffect(() => {
    if (!run || !['queued', 'running', 'retrying', 'waiting_input', 'waiting_approval'].includes(run.status) || cursor) return;
    const timer = window.setTimeout(() => { if (document.visibilityState === 'visible') resource.reload(); }, 10000);
    const visible = () => { if (document.visibilityState === 'visible') resource.reload(); };
    document.addEventListener('visibilitychange', visible);
    return () => { window.clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [run, cursor, resource.reload]);
  async function control(kind: 'pause' | 'resume' | 'cancel') {
    if (!run || action.locked || confirming) return;
    setConfirming(true);
    try {
      const title = m(kind === 'cancel' ? 'cancelRun' : kind);
      if (!await dialog.confirm(title, <div className="col"><code>{run.id}</code><p>{m('version', { n: run.version })}</p><Status value={run.status} /><p>{m(kind === 'pause' ? 'pauseNote' : kind === 'resume' ? 'resumeNote' : 'cancelNote')}</p></div>, title, kind === 'cancel')) return;
      action.start(kind, { run_id: run.id });
    } finally { setConfirming(false); }
  }
  return <section className="card au-run-detail" aria-labelledby="au-run-title"><div className="card-h row wrap"><h2 id="au-run-title">{m('runDetail')}</h2><span className="spacer" /><button type="button" className="btn ghost sm" disabled={resource.loading} onClick={refresh}>{m('refresh')}</button></div><div className="card-b col gap16">
    {resource.loading && !run ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={refresh} /> : resource.data && !run ? <Issues issues={[{ path: 'run', code: 'format' }]} /> : run && <>
      <div className="row wrap"><code>{run.id}</code><Status value={run.status} /><span>{m('version', { n: run.version })}</span></div>
      <dl className="au-meta"><dt>{m('started')}</dt><dd>{date(run.created_at)}</dd><dt>{m('updated')}</dt><dd>{date(run.updated_at)}</dd><dt>{m('callsUsed')}</dt><dd>{run.calls_used}</dd><dt>{m('deadlineAt')}</dt><dd>{date(run.deadline)}</dd></dl>
      {run.error_code && <ErrorNotice error={run.error_code} />}
      {['unknown', 'partial', 'waiting_input', 'waiting_approval'].includes(run.status) && <p className="au-notice">{m(run.status === 'unknown' ? 'unknownNote' : run.status === 'partial' ? 'partialNote' : 'waitingNote')}</p>}
      <div className="row wrap">{runActions(run.status).map((kind) => <button type="button" className="btn" disabled={action.locked || confirming} key={kind} onClick={() => void control(kind)}>{m(kind === 'cancel' ? 'cancelRun' : kind)}</button>)}</div>
      <h3>{m('steps')}</h3><ol className="au-outline">{run.steps.map((s) => <li key={s.step_id}><div className="row wrap"><code>{s.step_id}</code><Status value={s.status} /></div>
        <p className="muted small">{m('attempts', { n: s.attempt })} · {m('checks', { n: s.checks })}</p>
        {s.op_run_id && <div className="row wrap"><code>{s.op_run_id}</code>{s.operation_status && <Status value={s.operation_status} />}</div>}
        {s.error_code && <ErrorNotice error={s.error_code} />}
        {s.snapshot_hidden ? <p className="au-notice">{m('hidden')}</p> : s.output != null && <details><summary>{m('result')}</summary><DataView value={s.output} /></details>}
      </li>)}</ol>
      <h3>{m('timeline')}</h3><ol className="au-timeline">{[...pages, ...run.history].map((h) => <li key={h.seq}><time dateTime={new Date(h.created_at * 1000).toISOString()}>{date(h.created_at)}</time><code>{h.step_id}</code><Status value={h.status} />{h.error_code && <code>{safeCode(h.error_code)}</code>}</li>)}</ol>
      {run.next != null && <button type="button" className="btn" onClick={() => { setPages([...pages, ...run.history]); setCursor(run.next!); }}>{m('more')}</button>}
    </>}
  </div></section>;
}

const dayKeys = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const;
function TriggerSummary({ spec, policy, version, access }: { spec: TriggerSpec; policy: Policy; version: number; access: EditingContext }) {
  const { m } = useText();
  return <div className="col gap16"><span>{m('version', { n: version })}</span>
    {spec.kind === 'schedule' ? <div><strong>{spec.at} · {spec.timezone}</strong><p>{spec.weekdays.map((d) => m(dayKeys[d])).join(' · ')}</p><p>{m('misfire')}: {m(spec.misfire)} · {m('grace')}: {spec.grace_s}</p><p className="muted small">{m('dst')}</p></div>
      : <div><strong>{m('eventType')}: <code>{spec.event_type}</code></strong><p>{m('eventWorkspace')}: <code>{spec.workspace_id}</code></p>{spec.object_kind && <p>{m('objectKind')}: {spec.object_kind}</p>}{spec.object_id && <p>{m('objectId')}: {spec.object_id}</p>}<p className="muted small">{m('eventNote')}</p></div>}
    <h4>{m('params')}</h4><DataView value={spec.input} /><Scope policy={policy} access={access} />
  </div>;
}
function TriggerEditor({ flow, access, action, close }: { flow: FlowVersion; access: EditingContext; action: Mutation; close: () => void }) {
  const { m } = useText(); const dialog = useDialog(); const { me } = useSession();
  const [spec, setSpec] = useState<TriggerSpec>(() => ({ kind: 'schedule', timezone: me?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? 'UTC', at: '09:00', weekdays: [0, 1, 2, 3, 4], misfire: 'skip', grace_s: 300, input: seed(parameterSchema(flow.definition)) as Values }));
  const [maxRuns, setMaxRuns] = useState(10); const [untilRevoked, setUntilRevoked] = useState(false); const [expiry, setExpiry] = useState('');
  const [issues, setIssues] = useState<Issue[]>([]); const [confirming, setConfirming] = useState(false);
  const policy: Policy = { ...exactScope(flow.definition, spec.kind === 'event' ? spec.workspace_id : null), max_runs: maxRuns, expires_at: untilRevoked ? null : expiry ? new Date(expiry).getTime() / 1000 : null, until_revoked: untilRevoked };
  const events = [...new Set(access.caps.filter((c) => c.app !== 'agent').flatMap((c) => c.events))].sort();
  const eventApp = access.caps.find((c) => c.events.includes(spec.event_type ?? ''))?.app;
  const spaces = access.apps.find((a) => a.id === eventApp)?.workspaces ?? [];
  async function save() {
    if (action.locked || confirming) return;
    const errors = [...validateDefinition(flow.definition, access.caps, access.apps), ...validateTrigger(flow.definition, spec, policy)];
    if (spec.kind === 'event' && (!events.includes(spec.event_type ?? '') || !spaces.some((w) => w.id === spec.workspace_id))) errors.push({ path: 'event', code: 'operation' });
    setIssues(errors); if (errors.length) return;
    setConfirming(true);
    try {
      if (!await dialog.confirm(m('approveTrigger'), <div className="col gap16"><p>{m('recurringNote')}</p><DefinitionSummary flow={flow} access={access} /><TriggerSummary spec={spec} policy={policy} version={flow.version} access={access} /></div>, m('approveTrigger'))) return;
      action.start('trigger.create', payload({ flow_id: flow.flow_id, version: flow.version, trigger: spec, policy }));
    } finally { setConfirming(false); }
  }
  const update = (fields: Partial<TriggerSpec>) => setSpec({ ...spec, ...fields });
  return <form className="col gap16 au-inset" onSubmit={(e) => { e.preventDefault(); void save(); }}><h3>{m('newTrigger')}</h3><p>{m('triggerNote')}</p>
    <fieldset className="au-editor col gap16" disabled={action.locked || confirming}>
      <Field label={m('triggerKind')}><select className="input" value={spec.kind} onChange={(e) => setSpec(e.target.value === 'schedule' ? { kind: 'schedule', input: seed(parameterSchema(flow.definition)) as Values, timezone: me?.timezone ?? 'UTC', at: '09:00', weekdays: [0, 1, 2, 3, 4], misfire: 'skip', grace_s: 300 } : { kind: 'event', input: seed(parameterSchema(flow.definition)) as Values, event_type: '', workspace_id: '', weekdays: [0, 1, 2, 3, 4, 5, 6], misfire: 'skip', grace_s: 300 })}><option value="schedule">{m('schedule')}</option><option value="event">{m('event')}</option></select></Field>
      {spec.kind === 'schedule' ? <>
        <div className="au-grid"><Field label={m('timezone')}><input className="input" value={spec.timezone ?? ''} maxLength={100} onChange={(e) => update({ timezone: e.target.value })} required /></Field><Field label={m('at')}><input className="input" type="time" value={spec.at ?? ''} onChange={(e) => update({ at: e.target.value })} required /></Field></div>
        <fieldset className="au-days"><legend>{m('weekdays')}</legend>{dayKeys.map((d, i) => <label className="au-check" key={d}><input type="checkbox" checked={spec.weekdays.includes(i)} onChange={(e) => update({ weekdays: e.target.checked ? [...spec.weekdays, i].sort() : spec.weekdays.filter((n) => n !== i) })} />{m(d)}</label>)}</fieldset>
        <div className="au-grid"><Field label={m('misfire')}><select className="input" value={spec.misfire} onChange={(e) => update({ misfire: e.target.value as 'skip' | 'once' })}><option value="skip">{m('skip')}</option><option value="once">{m('once')}</option></select></Field><NumberField label={m('grace')} value={spec.grace_s} max={86400} onChange={(grace_s) => update({ grace_s })} /></div><p className="muted small">{m('dst')}</p>
      </> : <>
        <Field label={m('eventType')}><select className="input" value={spec.event_type ?? ''} onChange={(e) => update({ event_type: e.target.value, workspace_id: '' })}><option value="">{m('choose')}</option>{events.map((e) => <option value={e} key={e}>{e}</option>)}</select></Field>
        <Field label={m('eventWorkspace')}><select className="input" value={spec.workspace_id ?? ''} onChange={(e) => update({ workspace_id: e.target.value })}><option value="">{m('choose')}</option>{spaces.map((s) => <option value={s.id} key={s.id}>{s.name} · {s.id}</option>)}</select></Field>
        <div className="au-grid"><Field label={m('objectKind')}><input className="input" maxLength={100} value={spec.object_kind ?? ''} onChange={(e) => update({ object_kind: e.target.value || null })} /></Field><Field label={m('objectId')}><input className="input" maxLength={100} value={spec.object_id ?? ''} onChange={(e) => update({ object_id: e.target.value || null })} /></Field></div><p className="muted small">{m('eventNote')}</p>
      </>}
      <ValueEditor label={m('params')} schema={parameterSchema(flow.definition)} value={spec.input} onChange={(v) => update({ input: v as Values })} context={spec.kind === 'event' ? { definition: flow.definition, index: 0, workspace: null, event: true, capabilities: access.caps } : undefined} />
      <NumberField label={m('maxRuns')} value={maxRuns} max={10000} onChange={setMaxRuns} />
      <Field label={m('expiry')}><select className="input" value={untilRevoked ? 'revoked' : 'date'} onChange={(e) => setUntilRevoked(e.target.value === 'revoked')}><option value="date">{m('expires')}</option><option value="revoked">{m('untilRevoked')}</option></select></Field>
      {!untilRevoked && <Field label={m('expiresAt')}><input className="input" type="datetime-local" value={expiry} onChange={(e) => setExpiry(e.target.value)} required /></Field>}
      <Scope policy={policy} access={access} /><Issues issues={issues} />
      <div className="au-actions"><button type="submit" className="btn primary">{m('approveTrigger')}</button><button type="button" className="btn" onClick={close}>{m('cancel')}</button></div>
    </fieldset>
  </form>;
}
function Deliveries({ triggerId, revision, openRun }: { triggerId: string; revision: number; openRun: (id: string) => void }) {
  const { m, date } = useText(); const page = usePages<Delivery>('trigger.deliveries', { trigger_id: triggerId }, 'deliveries', revision);
  return <div className="au-inset col"><div className="row wrap"><h4>{m('deliveries')}</h4><button type="button" className="btn ghost sm" onClick={page.reload} disabled={page.loading}>{m('refresh')}</button></div>
    {page.error != null && <ErrorNotice error={page.error} retry={page.retry} />}
    {!page.loading && !page.error && !page.rows.length && <p className="muted">{m('noDeliveries')}</p>}
    {page.rows.map((d) => <div className="au-delivery" key={d.occurrence}><time>{date(d.created_at)}</time><Status value={d.status} />{d.run_id && <button type="button" className="btn ghost sm" onClick={() => openRun(d.run_id!)}>{m('runDetail')}</button>}{d.error_code && <code>{safeCode(d.error_code)}</code>}</div>)}
    {page.loading && <Loading />}{page.next && <button type="button" className="btn" disabled={page.loading} onClick={page.more}>{m('more')}</button>}
  </div>;
}
function TriggerList({ flow, access, revision, action, openRun }: { flow: FlowVersion; access: EditingContext; revision: number; action: Mutation; openRun: (id: string) => void }) {
  const { m, date } = useText(); const dialog = useDialog();
  const resource = useResource<{ triggers: FlowTrigger[] }>('trigger.list', { flow_id: flow.flow_id }, revision);
  const [adding, setAdding] = useState(false); const [receipts, setReceipts] = useState<string>(); const [confirming, setConfirming] = useState(false);
  useEffect(() => { setAdding(false); }, [revision]);
  async function toggle(trigger: FlowTrigger) {
    if (action.locked || confirming) return;
    setConfirming(true);
    try {
      // Confirm freshly read authority, rather than a possibly stale row from the list.
      const current = await read<FlowTrigger>('agent.flow.trigger.get', { trigger_id: trigger.id }, null);
      const pinned = await read<FlowVersion>('agent.flow.get', { flow_id: current.flow_id, version: current.version }, null);
      const enabled = !current.enabled;
      const title = m(enabled ? 'enable' : 'disable');
      if (!await dialog.confirm(title, <div className="col gap16"><code>{current.id}</code><p>{m(enabled ? 'enableNote' : 'disableNote')}</p><p>{m('usage', { used: current.runs_used, max: current.policy.max_runs })}</p><DefinitionSummary flow={pinned} access={access} /><TriggerSummary spec={current.trigger} policy={current.policy} version={current.version} access={access} /></div>, title, !enabled)) return;
      action.start('trigger.enable', { trigger_id: current.id, enabled });
    } catch (e) { setToggleError(e); } finally { setConfirming(false); }
  }
  const [toggleError, setToggleError] = useState<unknown>();
  return <section className="card"><div className="card-h row wrap"><h2>{m('triggers')}</h2><span className="spacer" /><button type="button" className="btn ghost sm" disabled={resource.loading || action.locked} onClick={resource.reload}>{m('refresh')}</button><button type="button" className="btn sm" disabled={action.locked || adding} onClick={() => setAdding(true)}>{m('newTrigger')}</button></div><div className="card-b col gap16">
    <p className="muted">{m('triggerNote')}</p>{toggleError != null && <ErrorNotice error={toggleError} />}
    {resource.loading ? <Loading /> : resource.error != null ? <ErrorNotice error={resource.error} retry={resource.reload} /> : resource.data && <>
      {!resource.data.triggers.length && !adding && <Empty title={m('noTriggers')} />}
      {resource.data.triggers.map((trigger) => {
        const expired = trigger.policy.expires_at != null && trigger.policy.expires_at <= Date.now() / 1000;
        const exhausted = trigger.runs_used >= trigger.policy.max_runs;
        return <article className="au-trigger col" key={trigger.id}><div className="row wrap"><code>{trigger.id}</code><span className="badge">{m(expired ? 'expired' : exhausted ? 'exhausted' : trigger.enabled ? 'enabled' : 'disabled')}</span></div>
          <p>{m('usage', { used: trigger.runs_used, max: trigger.policy.max_runs })}</p>
          {!!trigger.enabled && !expired && !exhausted && trigger.trigger.kind === 'schedule' && <p>{m('nextAt')}: {date(trigger.next_at)}</p>}
          {trigger.error_code && <ErrorNotice error={trigger.error_code} />}
          <details><summary>{m('scope')} · {m('version', { n: trigger.version })}</summary><TriggerSummary spec={trigger.trigger} policy={trigger.policy} version={trigger.version} access={access} /></details>
          <div className="row wrap"><button type="button" className="btn" disabled={action.locked || confirming || (!trigger.enabled && (expired || exhausted))} onClick={() => void toggle(trigger)}>{m(trigger.enabled ? 'disable' : 'enable')}</button><button type="button" className="btn ghost" aria-expanded={receipts === trigger.id} onClick={() => setReceipts(receipts === trigger.id ? undefined : trigger.id)}>{m('deliveries')}</button></div>
          {receipts === trigger.id && <Deliveries triggerId={trigger.id} revision={revision} openRun={openRun} />}
        </article>;
      })}
    </>}
    {adding && <TriggerEditor flow={flow} access={access} action={action} close={() => setAdding(false)} />}
  </div></section>;
}

function FlowDetail({ flowId, version, runId, editing, access, action, revision, navigate }: {
  flowId: string; version: number; runId: string | null; editing: boolean; access: EditingContext; action: Mutation; revision: number; navigate: (params: Record<string, string>) => void;
}) {
  const { m } = useText(); const resource = useResource<FlowVersion>('get', { flow_id: flowId, version }, revision);
  const [selectedVersion, setSelectedVersion] = useState(version);
  useEffect(() => setSelectedVersion(version), [version]);
  const openRun = (id: string) => navigate({ flow: flowId, version: String(version), run: id });
  if (resource.loading) return <Loading />;
  if (resource.error != null) return <ErrorNotice error={resource.error} retry={resource.reload} />;
  const flow = resource.data; if (!flow) return null;
  if (editing) return <DefinitionEditor key={`${flowId}/${version}`} initial={flow.definition} flow={flow} access={access} action={action} close={() => navigate({ flow: flowId, version: String(version) })} />;
  return <div className="col gap16"><div className="suite-head"><div><h2>{flow.name}</h2><span className="muted">{m('version', { n: flow.version })}</span></div><div className="actions">
    <button type="button" className="btn" disabled={action.locked} onClick={() => navigate({ flow: flowId, version: String(version), edit: '1' })}>{m('edit')}</button>
    <button type="button" className="btn ghost" disabled={action.locked} onClick={resource.reload}>{m('refresh')}</button>
  </div></div>
    <form className="row wrap" onSubmit={(e) => { e.preventDefault(); if (Number.isSafeInteger(selectedVersion) && selectedVersion > 0) navigate({ flow: flowId, version: String(selectedVersion) }); }}>
      <NumberField label={m('viewVersion')} value={selectedVersion} onChange={setSelectedVersion} /><button type="submit" className="btn sm" disabled={action.locked}>{m('viewVersion')}</button>
    </form>
    <section className="card"><div className="card-b"><DefinitionSummary flow={flow} access={access} /></div></section>
    {runId && <RunView key={runId} runId={runId} flowId={flowId} revision={revision} action={action} />}
    <ManualRun key={`${flowId}/${version}`} flow={flow} access={access} action={action} />
    <TriggerList flow={flow} access={access} revision={revision} action={action} openRun={openRun} />
    <RunHistory flowId={flowId} revision={revision} select={(run) => navigate({ flow: flowId, version: String(run.version), run: run.id })} />
  </div>;
}
function FlowList({ revision, action, select }: { revision: number; action: Mutation; select: (flow: Flow) => void }) {
  const { m, date } = useText(); const page = usePages<Flow>('list', {}, 'flows', revision);
  return <div className="col gap16"><div className="row"><span className="spacer" /><button type="button" className="btn ghost" disabled={page.loading} onClick={page.reload}>{m('refresh')}</button></div>
    {page.error != null && <ErrorNotice error={page.error} retry={page.retry} />}
    {!page.loading && !page.error && !page.rows.length && <Empty title={m('empty')}>{m('emptyHint')}</Empty>}
    <div className="au-flow-list">{page.rows.map((flow) => <button type="button" className="au-flow-card" key={flow.id} disabled={action.locked} onClick={() => select(flow)}><strong>{flow.name}</strong><span className="row wrap"><span className="badge">{m('fixed')}</span><span>{m('version', { n: flow.latest_version })}</span></span><span className="muted small">{date(flow.created_at)}</span></button>)}</div>
    {page.loading && <Loading />}{page.next && <button type="button" className="btn" disabled={page.loading} onClick={page.more}>{m('more')}</button>}
  </div>;
}

function PersonalAutomations({ owner }: { owner: string }) {
  const { m } = useText(); const [search, setSearch] = useSearchParams(); const [revision, setRevision] = useState(0);
  const [access, setAccess] = useState<EditingContext>(); const [accessError, setAccessError] = useState<unknown>(); const [catalogRevision, setCatalogRevision] = useState(0);
  const flowId = search.get('flow'); const version = Number(search.get('version') ?? 1); const creating = search.get('new') === '1';
  const heading = useRef<HTMLHeadingElement>(null);
  const locationKey = search.toString();
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [locationKey]);
  const action = useMutation(owner, (operation, output) => {
    setRevision((n) => n + 1);
    if (operation === 'agent.flow.create' || operation === 'agent.flow.publish') setSearch({ flow: String(output.flow_id), version: String(output.version) });
    if (operation === 'agent.flow.start') setSearch({ flow: String(output.flow_id), version: String(output.version), run: String(output.id) });
  });
  useEffect(() => {
    const abort = new AbortController(); setAccess(undefined); setAccessError(undefined);
    // These are read-only catalog endpoints. All business reads/writes use shared read()/op().
    void Promise.all([catalog(abort.signal), get<{ operations: Capability[] }>('/api/suite/operations', abort.signal)])
      .then(([apps, ops]) => { if (!abort.signal.aborted) setAccess({ apps: apps.apps, caps: ops.operations }); })
      .catch((e: unknown) => { if (!abort.signal.aborted) setAccessError(e); });
    return () => abort.abort();
  }, [catalogRevision]);
  const available = access?.caps.some((c) => c.id === 'agent.flow.list');
  return <div className="au-page col gap16">
    <header className="suite-head"><div><div className="row wrap"><h1 ref={heading} tabIndex={-1}>{m('title')}</h1><span className="badge">{m('personal')}</span></div><p className="lede">{m('intro')}</p></div><div className="actions">
      {(flowId || creating) && <button type="button" className="btn ghost" disabled={action.locked || creating || search.get('edit') === '1'} onClick={() => setSearch({})}>{m('back')}</button>}
      {!flowId && !creating && available && <button type="button" className="btn primary" disabled={action.locked} onClick={() => setSearch({ new: '1' })}>{m('create')}</button>}
    </div></header><p className="muted small">{m('ownerNote')}</p>
    <MutationFeedback action={action} />
    {accessError != null ? <ErrorNotice error={accessError} retry={() => setCatalogRevision((n) => n + 1)} /> : !access ? <Loading /> : !available ? <Empty title={m('unavailable')} action={<button type="button" className="btn" onClick={() => setCatalogRevision((n) => n + 1)}>{m('refresh')}</button>} /> : <>
      {!creating && search.get('edit') !== '1' && <div><button type="button" className="btn ghost sm" disabled={action.locked} onClick={() => setCatalogRevision((n) => n + 1)}>{m('refresh')} · {m('tools')}</button></div>}
      {creating ? <DefinitionEditor initial={emptyDefinition()} access={access} action={action} close={() => setSearch({})} />
        : flowId && /^flow_[a-f0-9]{12}$/.test(flowId) && Number.isSafeInteger(version) && version > 0 ? <FlowDetail key={`${flowId}/${version}`} flowId={flowId} version={version} runId={search.get('run')} editing={search.get('edit') === '1'} access={access} action={action} revision={revision} navigate={(p) => { if (!action.locked) setSearch(p); }} />
          : flowId ? <Issues issues={[{ path: 'flow', code: 'format' }]} /> : <FlowList revision={revision} action={action} select={(flow) => setSearch({ flow: flow.id, version: String(flow.latest_version) })} />}
    </>}
  </div>;
}

/** Coordinator: render inside the authenticated Agent shell at /agent/automations.
 * Query deep links: ?flow=flow_…&version=1&run=flowrun_… ; ?new=1 ; &edit=1.
 * No model setup or workspace selection is required for this personal page. */
export default function Automations() {
  const { me } = useSession();
  if (!me) return <Loading />;
  const owner = `${me.id}:${me.session?.id ?? ''}`;
  return <PersonalAutomations key={owner} owner={owner} />;
}
