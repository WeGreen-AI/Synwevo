import { useEffect, useId, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from 'react';
import { ApiError, useSession } from '@synwevo/auth-client';
import { useI18n, type Params } from '@synwevo/ui';
import { formatMoney, type MoneyUnit } from '@synwevo/ui/money';
import { newKey, op, read } from '../../shared/ops';
import { Empty, Failure, Loading } from '../../shared/states';
import { formatAmount } from './budget';
import {
  budgetDirty, budgetLocked, budgetPeriods, createBudgetEditor, parsePayerBudget, payerBudgetCode,
  payerBudgetErrorText, validatePayerBudgets, type BudgetEditorState, type BudgetField, type PayerBudgetEditor,
  type PayerBudgetItem, type PayerBudgets,
} from './payer-budget';
import { payerBudgetCopy, type PayerBudgetText } from './payer-budget.messages';

const stack: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 'var(--sp-3)', minWidth: 0, overflowWrap: 'anywhere' };
const control: CSSProperties = { height: 'auto', minHeight: 44, maxWidth: '100%', whiteSpace: 'normal' };
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 15rem), 1fr))', gap: 'var(--sp-3)', minWidth: 0 };
function useText() {
  const { t, locale } = useI18n();
  return { m: (key: PayerBudgetText, params?: Params) => t(payerBudgetCopy[key][0], params), locale };
}
/** Amounts as money in the reader's locale (shared rule, `@synwevo/ui/money`): usage at the currency's
 *  precision, rounded from the integer; a limit as the person typed it, to every digit the unit holds. */
const usageMoney = (units: string, unit: MoneyUnit, locale: string) => formatMoney(units, unit, locale);
const limitMoney = (units: string, unit: MoneyUnit, locale: string) => formatMoney(units, unit, locale, { precision: 'exact' });
/** Kept here as well for existing importers; the words live beside the budget model. */
export { payerBudgetErrorText };
function ErrorNotice({ error, fallback, retry }: { error: unknown; fallback: PayerBudgetText; retry?: () => void }) {
  const { m } = useText();
  return <div>
    <Failure error={new Error(m(payerBudgetErrorText(error, fallback)))} onRetry={retry} />
    {error instanceof ApiError && error.requestId && <p className="muted small">{m('requestId', { id: error.requestId })}</p>}
  </div>;
}
function date(stamp: number, locale: string) {
  return new Intl.DateTimeFormat(locale, { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short' }).format(stamp * 1000);
}

// Retain unresolved requests across Settings navigation, partitioned by the signed-in session.
// Nothing is submitted on mount, on refresh, or when returning to this section.
let editorOwner = '';
const editors = new Map<string, PayerBudgetEditor>();
function editorFor(owner: string, environment: string, item: PayerBudgetItem) {
  if (editorOwner !== owner) { editors.clear(); editorOwner = owner; }
  const id = `${environment}:${item.unit.code}`;
  let editor = editors.get(id);
  if (!editor) {
    editor = createBudgetEditor(item, (input, key) => op<PayerBudgetItem>('agent.billing.budget.set', { ...input }, { workspace: null, key }), newKey);
    editors.set(id, editor);
  }
  return editor;
}

export function BudgetUsage({ budget }: { budget: PayerBudgetItem }) {
  const { m, locale } = useText();
  return <div style={stack}>
    <h3>{m('current')}</h3>
    <p>{budget.configured ? <>{m('revision', { revision: budget.revision })} · {m(budget.enabled ? 'enabled' : 'disabled')}</> : m('unconfigured')}</p>
    {budget.usage_state !== 'available' && <p className="notice warn" role="status">{m(budget.usage_state)}</p>}
    <div style={grid}>{budgetPeriods.map((period) => {
      const usage = budget.usage_state === 'available' ? budget.periods?.[period] : null;
      return <section key={period} className="notice" style={stack} aria-label={m(period)}>
        <h4>{m(period)}</h4>
        <dl style={{ ...stack, margin: 0 }}>
          <div><dt className="muted small">{m('limit')}</dt><dd style={{ margin: 0 }}>{limitMoney(budget[`${period}_budget`], budget.unit, locale)}</dd></div>
          {usage && <>
            {(['spent', 'held', 'remaining'] as const).map((key) => <div key={key}>
              <dt className="muted small">{m(key)}</dt><dd style={{ margin: 0 }}>{usageMoney(usage[key], budget.unit, locale)}</dd>
            </div>)}

          </>}
        </dl>
        {usage && <details>
          <summary style={{ ...control, cursor: 'pointer' }}>{m('dates')}</summary>
          <dl style={{ ...stack, margin: 0 }}>
            <div><dt className="muted small">{m('start')}</dt><dd style={{ margin: 0 }}>{date(usage.start, locale)}</dd></div>
            <div><dt className="muted small">{m('end')}</dt><dd style={{ margin: 0 }}>{date(usage.end, locale)}</dd></div>
          </dl>
        </details>}
      </section>;
    })}</div>
  </div>;
}

export function BudgetReview({ state }: { state: BudgetEditorState }) {
  const { m, locale } = useText();
  const next = state.review;
  if (!next) return null;
  const current = state.current;
  return <div style={stack}>
    <p>{m('revision', { revision: next.expected_revision })} · {current.unit.code}</p>
    <p>{m('reviewNote')}</p>
    <dl style={{ ...stack, margin: 0 }}>
      <div><dt>{m('status')}</dt><dd style={{ margin: 0 }}>
        <p>{m('before', { value: m(current.configured ? current.enabled ? 'enabled' : 'disabled' : 'unconfigured') })}</p>
        <p>{m('after', { value: m(next.enabled ? 'enabled' : 'disabled') })}</p>
      </dd></div>
      {budgetPeriods.map((period) => <div key={period}>
        <dt>{m(period)}</dt><dd style={{ margin: 0 }}>
          <p>{m('before', { value: limitMoney(current[`${period}_budget`], current.unit, locale) })}</p>
          <p>{m('after', { value: limitMoney(next[`${period}_budget`], current.unit, locale) })}</p>
        </dd>
      </div>)}
    </dl>
    <p className="muted small">{m('effect')}</p>
  </div>;
}

export function BudgetHistory({ budget }: { budget: PayerBudgetItem }) {
  const { m, locale } = useText();
  return <details style={{ minWidth: 0 }}>
    <summary style={{ ...control, cursor: 'pointer' }}>{m('history')}</summary>
    {budget.history.length === 0 ? <p className="muted">{m('noHistory')}</p> : <ol style={{ ...stack, paddingInlineStart: 'var(--sp-5)' }}>
      {budget.history.map((version) => <li key={version.revision}>
        <p><strong>{m('revision', { revision: version.revision })}</strong> · {m(version.enabled ? 'enabled' : 'disabled')}</p>
        <p className="muted small">{m('created', { time: date(version.created_at, locale) })}</p>
        <dl style={{ ...stack, margin: 0 }}>{budgetPeriods.map((period) => <div key={period}>
          <dt className="muted small">{m(period)}</dt><dd style={{ margin: 0 }}>{limitMoney(version[`${period}_budget`], budget.unit, locale)}</dd>
        </div>)}</dl>
      </li>)}
    </ol>}
  </details>;
}

export function BudgetEditorForm({ editor, environment, refreshing = false }: {
  editor: PayerBudgetEditor; environment: PayerBudgets['environment']; refreshing?: boolean;
}) {
  const { m } = useText();
  const id = useId();
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot);
  const reviewFocus = useRef<HTMLHeadingElement>(null);
  const editFocus = useRef<HTMLInputElement>(null);
  const previousPhase = useRef(state.phase);
  const [issue, setIssue] = useState<{ field: BudgetField; text: PayerBudgetText } | null>(null);
  const locked = budgetLocked(state) || refreshing;
  useEffect(() => {
    if (state.phase === 'review') reviewFocus.current?.focus();
    if (state.phase === 'editing' && previousPhase.current === 'review') editFocus.current?.focus();
    previousPhase.current = state.phase;
  }, [state.phase]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (['saving', 'uncertain'].includes(editor.getSnapshot().phase)) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [editor]);

  function review() {
    if (locked || state.phase === 'conflict') return;
    for (const period of budgetPeriods) {
      const field = `${period}_budget` as const;
      try { parsePayerBudget(state.draft[field], state.current.unit.scale); }
      catch (error) {
        setIssue({ field, text: error instanceof Error && error.message === 'budget_precision' ? 'tooPrecise' : 'invalidAmount' });
        document.getElementById(`${id}-${field}`)?.focus();
        return;
      }
    }
    setIssue(null); editor.review();
  }
  return <div style={stack}>
    <form style={stack} noValidate aria-labelledby={`${id}-edit`} onSubmit={(event) => { event.preventDefault(); review(); }}>
      <h3 id={`${id}-edit`}>{m('edit')}</h3>
      <label className="check" style={{ minHeight: 44 }}>
        <input ref={editFocus} type="checkbox" checked={state.draft.enabled} disabled={locked}
          onChange={(event) => editor.edit({ ...state.draft, enabled: event.target.checked })} />
        <span>{m('enable')}</span>
      </label>
      <p id={`${id}-precision`} className="muted small">{m('precision', { scale: state.current.unit.scale, amount: formatAmount('1', state.current.unit) })}</p>
      <div style={grid}>{budgetPeriods.map((period) => {
        const field = `${period}_budget` as const;
        return <label key={period} className="field" htmlFor={`${id}-${field}`} style={{ minWidth: 0 }}>
          <span>{m(`${period}Input`, { unit: state.current.unit.code })}</span>
          <input className="input" id={`${id}-${field}`} type="text" inputMode="decimal" autoComplete="off" spellCheck={false}
            style={{ ...control, width: '100%', minWidth: 0 }} maxLength={128} required disabled={locked}
            value={state.draft[field]} aria-invalid={issue?.field === field}
            aria-describedby={`${id}-precision${issue?.field === field ? ` ${id}-issue` : ''}`}
            onChange={(event) => { setIssue(null); editor.edit({ ...state.draft, [field]: event.target.value }); }} />
        </label>;
      })}</div>
      {issue && <p className="notice err" id={`${id}-issue`} role="alert">{m(issue.text)}</p>}
      {!state.review && <div className="row wrap">
        <button className="btn primary" style={control} type="submit"
          disabled={locked || state.phase === 'conflict' || (!budgetDirty(state) && state.current.configured)}>{m('review')}</button>
        {budgetDirty(state) && <button className="btn" style={control} type="button" disabled={locked}
          onClick={() => { setIssue(null); editor.discard(); }}>{m('discard')}</button>}
      </div>}
    </form>
    {state.review && <section className="notice info" style={stack} aria-labelledby={`${id}-review`}>
      <h3 id={`${id}-review`} ref={reviewFocus} tabIndex={-1}>{m('review')}</h3>
      <BudgetReview state={state} />
      {state.phase === 'review' && <div className="row wrap">
        <button className="btn" style={control} type="button" onClick={() => editor.back()}>{m('back')}</button>
        <button className="btn primary" style={control} type="button" onClick={() => void editor.save()}>{m(environment === 'test' ? 'saveTest' : 'save')}</button>
      </div>}
    </section>}
    <div aria-live="polite">
      {state.phase === 'saving' && <p role="status">{m('saving')}</p>}
      {state.phase === 'saved' && <p className="notice ok" role="status">{m('saved')}</p>}
      {state.phase === 'conflict' && <p className="notice warn" role="alert">{m('conflict')}</p>}
      {state.phase === 'refused' && <ErrorNotice error={state.error} fallback="refused" />}
      {state.phase === 'uncertain' && <div className="notice warn" style={stack}>
        <p role="alert">{m('uncertain')}</p>
        {state.error instanceof ApiError && state.error.requestId && <p className="muted small">{m('requestId', { id: state.error.requestId })}</p>}
        <div><button className="btn" style={control} type="button" onClick={() => void editor.retry()}>{m('retry')}</button></div>
      </div>}
    </div>
  </div>;
}

function BudgetPanel({ initial, owner }: { initial: PayerBudgets; owner: string }) {
  const { m } = useText();
  const [data, setData] = useState(initial);
  const remembered = editorOwner === owner ? [...editors.entries()].find(([key, editor]) =>
    key.startsWith(`${initial.environment}:`) && ['saving', 'uncertain'].includes(editor.getSnapshot().phase))?.[1] : undefined;
  const [code, setCode] = useState(() => remembered?.getSnapshot().current.unit.code ?? initial.budgets[0].unit.code);
  const item = data.budgets.find((budget) => budget.unit.code === code)
    ?? editors.get(`${data.environment}:${code}`)!.getSnapshot().current;
  const editor = useMemo(() => editorFor(owner, data.environment, item), [owner, data.environment, item.unit.code]);
  const state = useSyncExternalStore(editor.subscribe, editor.getSnapshot, editor.getSnapshot);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const request = useRef<AbortController | null>(null);
  const id = useId();
  useEffect(() => { editor.refresh(item); }, [editor, item]);
  useEffect(() => () => request.current?.abort(), []);

  async function refresh() {
    if (budgetLocked(editor.getSnapshot()) || request.current) return;
    const abort = new AbortController(); request.current = abort;
    setRefreshing(true); setError(null);
    try {
      const value = await read<PayerBudgets>('agent.billing.budget.get', {}, null, abort.signal);
      validatePayerBudgets(value);
      if (abort.signal.aborted) return;
      if (value.environment !== data.environment) throw new Error('invalid_payer_budget_response');
      const updated = value.budgets.find((budget) => budget.unit.code === code);
      if (!updated) throw new ApiError(409, 'billing_unknown_sale_unit', '');
      editor.refresh(updated); setData(value);
    } catch (failure) { if (!abort.signal.aborted) setError(failure); }
    finally { if (!abort.signal.aborted) setRefreshing(false); request.current = null; }
  }
  const choices = data.budgets.some((budget) => budget.unit.code === code) ? data.budgets : [item, ...data.budgets];
  return <div style={stack}>
    <p className={`notice ${data.environment === 'test' ? 'warn' : 'info'}`} role="note">{m(data.environment)}</p>
    <p className="muted small">{m('unitsNote')}</p>
    <label className="field" htmlFor={`${id}-unit`}><span>{m('unit')}</span>
      <select id={`${id}-unit`} className="input" style={{ ...control, width: '100%' }} value={code}
        disabled={refreshing || budgetLocked(state) || budgetDirty(state)} onChange={(event) => { setCode(event.target.value); setError(null); }}>
        {choices.map((budget) => <option key={budget.unit.code} value={budget.unit.code}>{budget.unit.code}</option>)}
      </select>
    </label>
    <div><button className="btn" style={control} type="button" disabled={refreshing || budgetLocked(state)} onClick={() => void refresh()}>{m('reload')}</button></div>
    {refreshing && <Loading label={m('loading')} />}
    {error != null && <ErrorNotice error={error} fallback="readError" />}
    <BudgetUsage budget={state.current} />
    <BudgetEditorForm key={code} editor={editor} environment={data.environment} refreshing={refreshing} />
    <BudgetHistory budget={state.current} />
  </div>;
}

function PayerBudgetSection({ owner }: { owner: string }) {
  const { m } = useText();
  const id = useId();
  const [data, setData] = useState<PayerBudgets | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    void read<PayerBudgets>('agent.billing.budget.get', {}, null, abort.signal).then((value) => {
      validatePayerBudgets(value);
      if (!abort.signal.aborted) setData(value);
    }).catch((failure: unknown) => { if (!abort.signal.aborted) setError(failure); });
    return () => abort.abort();
  }, [attempt]);
  return <section className="card" style={{ minWidth: 0 }} aria-labelledby={`${id}-title`}>
    <div className="card-h"><h2 id={`${id}-title`}>{m('title')}</h2></div>
    <div className="card-b" style={stack}>
      <p className="muted">{m('scope')}</p>
      <p className="muted small">{m('effect')}</p>
      <details>
        <summary style={{ ...control, cursor: 'pointer' }}>{m('counting')}</summary>
        <p className="muted small">{m('calendar')}</p>
        <p className="muted small">{m('accounting')}</p>
      </details>
      {error != null ? <ErrorNotice error={error} fallback="readError" retry={() => setAttempt((n) => n + 1)} />
        : !data ? <Loading label={m('loading')} /> : data.budgets.length === 0 ? <>
          <p className={`notice ${data.environment === 'test' ? 'warn' : 'info'}`}>{m(data.environment)}</p>
          <Empty title={m('empty')}><p>{m('unitsNote')}</p></Empty>
          <div><button className="btn" style={control} type="button" onClick={() => setAttempt((n) => n + 1)}>{m('reload')}</button></div>
        </> : <BudgetPanel initial={data} owner={owner} />}
    </div>
  </section>;
}

export default function PayerBudget() {
  const { me } = useSession();
  if (!me) return null;
  const owner = `${me.id}:${me.session?.id ?? ''}`;
  return <PayerBudgetSection key={owner} owner={owner} />;
}
