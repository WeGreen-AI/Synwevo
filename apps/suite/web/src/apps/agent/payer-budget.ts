import { ApiError } from '@synwevo/auth-client/api';
import { formatAmount, parseBudget, type Unit } from './budget';

export const budgetPeriods = ['daily', 'weekly', 'monthly'] as const;
export type BudgetPeriod = typeof budgetPeriods[number];
export type BudgetField = `${BudgetPeriod}_budget`;
export interface BudgetLimits {
  enabled: boolean;
  daily_budget: string;
  weekly_budget: string;
  monthly_budget: string;
}
export interface PayerBudgetVersion extends BudgetLimits { revision: number; created_at: number }
export interface PayerBudgetPeriod { start: number; end: number; spent: string; held: string; remaining: string }
export interface PayerBudgetItem extends BudgetLimits {
  unit: Unit;
  revision: number;
  configured: boolean;
  usage_state: 'available' | 'capacity' | 'inconsistent';
  periods: Record<BudgetPeriod, PayerBudgetPeriod> | null;
  history: PayerBudgetVersion[];
}
export interface PayerBudgets {
  environment: 'test' | 'live';
  timezone: 'UTC';
  budgets: PayerBudgetItem[];
}
export interface PayerBudgetInput extends BudgetLimits { unit_code: string; expected_revision: number }
// Draft amounts are major-unit decimal strings; all API amounts remain integer strings.
export type PayerBudgetDraft = BudgetLimits;
const maxAmount = 9223372036854775807n;

export function parsePayerBudget(value: string, scale: number): string {
  // Reuse the run-budget parser for positive amounts; allow strictly written zero as a cap.
  formatAmount('0', { code: '', scale });
  if (typeof value === 'string' && value.length <= 128 && /^0+(?:\.0+)?$/.test(value)) {
    if ((value.split('.')[1]?.length ?? 0) > scale) throw new Error('budget_precision');
    return '0';
  }
  return parseBudget(value, scale);
}

export function budgetDraft(item: BudgetLimits, unit: Unit): PayerBudgetDraft {
  const major = (amount: string) => formatAmount(amount, unit).slice(0, -(unit.code.length + 1));
  return { enabled: item.enabled, daily_budget: major(item.daily_budget),
    weekly_budget: major(item.weekly_budget), monthly_budget: major(item.monthly_budget) };
}

function integer(value: unknown, maximum = maxAmount): void {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]{0,22})$/.test(value) || BigInt(value) > maximum) {
    throw new Error('invalid_payer_budget_response');
  }
}
function timestamp(value: number): void {
  if (!Number.isSafeInteger(value) || value < 0 || !Number.isFinite(new Date(value * 1000).getTime())) {
    throw new Error('invalid_payer_budget_response');
  }
}
function limits(value: BudgetLimits): void {
  if (!value || typeof value.enabled !== 'boolean') throw new Error('invalid_payer_budget_response');
  for (const period of budgetPeriods) integer(value[`${period}_budget`]);
}

export function validatePayerBudget(value: PayerBudgetItem): void {
  limits(value);
  if (typeof value.unit?.code !== 'string' || !value.unit.code.trim()
    || !Number.isSafeInteger(value.revision) || value.revision < 0 || typeof value.configured !== 'boolean'
    || !['available', 'capacity', 'inconsistent'].includes(value.usage_state)
    || !Array.isArray(value.history) || value.history.length > 20) throw new Error('invalid_payer_budget_response');
  formatAmount('0', value.unit);
  if (!value.configured && (value.revision !== 0 || value.enabled || value.history.length
    || budgetPeriods.some((p) => value[`${p}_budget`] !== '0'))) throw new Error('invalid_payer_budget_response');
  if (value.configured && value.revision < 1) throw new Error('invalid_payer_budget_response');
  if (value.usage_state === 'available') {
    if (!value.periods) throw new Error('invalid_payer_budget_response');
    for (const period of budgetPeriods) {
      const item = value.periods[period];
      if (!item) throw new Error('invalid_payer_budget_response');
      timestamp(item.start); timestamp(item.end);
      if (item.end <= item.start) throw new Error('invalid_payer_budget_response');
      // Summed legacy spending can exceed a single int64 budget. The server reads at most 10,000 calls.
      integer(item.spent, maxAmount * 10_000n); integer(item.held, maxAmount * 10_000n); integer(item.remaining);
    }
  } else if (value.periods !== null) throw new Error('invalid_payer_budget_response');
  let previous = value.revision + 1;
  for (const version of value.history) {
    limits(version); timestamp(version.created_at);
    if (!Number.isSafeInteger(version.revision) || version.revision < 1 || version.revision >= previous) {
      throw new Error('invalid_payer_budget_response');
    }
    previous = version.revision;
  }
}

export function validatePayerBudgets(value: PayerBudgets): void {
  if (!value || !['test', 'live'].includes(value.environment) || value.timezone !== 'UTC'
    || !Array.isArray(value.budgets) || value.budgets.length > 100) throw new Error('invalid_payer_budget_response');
  const units = new Set<string>();
  for (const budget of value.budgets) {
    validatePayerBudget(budget);
    if (units.has(budget.unit.code)) throw new Error('invalid_payer_budget_response');
    units.add(budget.unit.code);
  }
}

export function payerBudgetCode(error: unknown): string {
  if (!(error instanceof ApiError) || error.upstream) return '';
  const code = error.code === 'idempotent_failure' ? error.detail?.code : error.code;
  return typeof code === 'string' ? code.replace(/^billing_/, '') : '';
}

export function uncertainBudgetFailure(error: unknown, retrying: boolean): boolean {
  if (!(error instanceof ApiError) || error.upstream) return true;
  // A replay can be refused before reaching the original receipt (e.g. permissions changed).
  if (retrying && error.code !== 'idempotent_failure') return true;
  if (['idempotency_conflict', 'idempotency_in_progress', 'run_in_progress', 'outcome_unknown',
    'unknown', 'run_result_unavailable', 'result_access_unverifiable'].includes(payerBudgetCode(error))) return true;
  return error.status < 400 || error.status >= 500 || error.status === 408 || error.status === 429;
}

export interface BudgetEditorState {
  current: PayerBudgetItem;
  draft: PayerBudgetDraft;
  phase: 'editing' | 'review' | 'saving' | 'uncertain' | 'saved' | 'conflict' | 'refused';
  review: Readonly<PayerBudgetInput> | null;
  error: unknown;
}
type Sender = (input: Readonly<PayerBudgetInput>, key: string) => Promise<{ status: string; output: PayerBudgetItem }>;
export function budgetLocked(state: BudgetEditorState): boolean {
  return ['review', 'saving', 'uncertain'].includes(state.phase);
}
export function budgetDirty(state: BudgetEditorState): boolean {
  const original = budgetDraft(state.current, state.current.unit);
  return state.draft.enabled !== original.enabled
    || budgetPeriods.some((p) => state.draft[`${p}_budget`] !== original[`${p}_budget`]);
}

/** A person explicitly reviews, saves, or retries. Reads never submit a write. */
export function createBudgetEditor(initial: PayerBudgetItem, send: Sender, key: () => string) {
  validatePayerBudget(initial);
  let state: BudgetEditorState = { current: initial, draft: budgetDraft(initial, initial.unit),
    phase: 'editing', review: null, error: null };
  let ticket: { input: Readonly<PayerBudgetInput>; key: string } | null = null;
  const listeners = new Set<() => void>();
  const publish = (next: BudgetEditorState) => { state = next; listeners.forEach((f) => f()); };
  async function execute(retrying: boolean) {
    if (!ticket || state.phase === 'saving') return;
    const request = ticket;
    publish({ ...state, phase: 'saving', error: null });
    try {
      const result = await send(request.input, request.key);
      if (result.status === 'succeeded') {
        const item = result.output;
        validatePayerBudget(item);
        if (!item.configured || item.unit.code !== request.input.unit_code || item.unit.scale !== state.current.unit.scale
          || item.revision !== request.input.expected_revision + 1 || item.enabled !== request.input.enabled
          || budgetPeriods.some((p) => item[`${p}_budget`] !== request.input[`${p}_budget`])) {
          throw new Error('invalid_payer_budget_response');
        }
        ticket = null;
        publish({ current: item, draft: budgetDraft(item, item.unit), phase: 'saved', review: null, error: null });
      } else if (['failed', 'cancelled'].includes(result.status)) {
        ticket = null;
        publish({ ...state, phase: 'refused', review: null, error: new Error('budget_save_refused') });
      } else publish({ ...state, phase: 'uncertain' });
    } catch (error) {
      if (uncertainBudgetFailure(error, retrying)) publish({ ...state, phase: 'uncertain', error });
      else {
        ticket = null;
        publish({ ...state, phase: payerBudgetCode(error) === 'payer_budget_conflict' ? 'conflict' : 'refused', review: null, error });
      }
    }
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    edit(draft: PayerBudgetDraft) {
      if (!budgetLocked(state)) publish({ ...state, draft: { ...draft }, phase: state.phase === 'conflict' ? 'conflict' : 'editing', error: null });
    },
    discard() {
      if (!budgetLocked(state)) publish({ ...state, draft: budgetDraft(state.current, state.current.unit), error: null });
    },
    refresh(item: PayerBudgetItem) {
      if (budgetLocked(state)) return;
      validatePayerBudget(item);
      if (item.unit.code !== state.current.unit.code || item.unit.scale !== state.current.unit.scale) throw new Error('invalid_payer_budget_response');
      // A read started before a recovered save may arrive afterward. Never roll it back.
      if (item.revision < state.current.revision) return;
      const draft = budgetDirty(state) || state.phase === 'conflict' ? state.draft : budgetDraft(item, item.unit);
      publish({ current: item, draft, phase: 'editing', review: null, error: null });
    },
    review() {
      if (budgetLocked(state) || state.phase === 'conflict' || (!budgetDirty(state) && state.current.configured)) return;
      const input = Object.freeze({ unit_code: state.current.unit.code, expected_revision: state.current.revision,
        enabled: state.draft.enabled, daily_budget: parsePayerBudget(state.draft.daily_budget, state.current.unit.scale),
        weekly_budget: parsePayerBudget(state.draft.weekly_budget, state.current.unit.scale),
        monthly_budget: parsePayerBudget(state.draft.monthly_budget, state.current.unit.scale) });
      publish({ ...state, phase: 'review', review: input, error: null });
    },
    back() { if (state.phase === 'review') publish({ ...state, phase: 'editing', review: null }); },
    save() {
      if (state.phase !== 'review' || !state.review || ticket) return Promise.resolve();
      ticket = { input: state.review, key: key() };
      return execute(false);
    },
    retry() { return state.phase === 'uncertain' ? execute(true) : Promise.resolve(); },
  };
}
export type PayerBudgetEditor = ReturnType<typeof createBudgetEditor>;
