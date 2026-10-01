/** Exact, bounded conversions for platform cost policy forms. No monetary Number conversion. */
export type CostUnit = { code: string; scale: number };
export const budgetKeys = ['daily_budget', 'weekly_budget', 'monthly_budget', 'unknown_budget'] as const;
export const limitKeys = ['max_inflight', 'max_unknown'] as const;
export type BudgetKey = typeof budgetKeys[number];
export type LimitKey = typeof limitKeys[number];
export type CostPolicySpec = { enabled: boolean; unit: CostUnit } & Record<BudgetKey, string> & Record<LimitKey, number>;
export type CostForm = { enabled: '' | 'yes' | 'no'; unit: string } & Record<BudgetKey | LimitKey, string>;
export type CostErrors = Partial<Record<keyof CostForm, string>>;
const MAX = 9223372036854775807n;
export const validUnit = (unit: CostUnit): boolean => !!unit && /^[A-Z][A-Z0-9_]{1,15}$/.test(unit.code) &&
  Number.isInteger(unit.scale) && unit.scale >= 0 && unit.scale <= 12;
export const unitKey = (unit: CostUnit): string => `${unit.code}:${unit.scale}`;
const validScale = (scale: number) => Number.isInteger(scale) && scale >= 0 && scale <= 12;
export function amountToUnits(value: string, scale: number): string | null {
  if (!validScale(scale) || value.length > 33 || !/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  // Even extra zeroes are rejected: the stated unit precision is an input boundary.
  if (fraction.length > scale) return null;
  const units = BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, '0') || '0');
  return units <= MAX ? String(units) : null;
}
export function unitsToAmount(value: string, scale: number): string {
  if (!validScale(scale) || !/^(0|[1-9][0-9]{0,18})$/.test(value) || BigInt(value) > MAX) throw new RangeError('Invalid cost amount');
  if (!scale) return value;
  const digits = value.padStart(scale + 1, '0');
  const fraction = digits.slice(-scale).replace(/0+$/, '');
  return digits.slice(0, -scale) + (fraction ? `.${fraction}` : '');
}
export function callLimit(value: string): number | null {
  return /^(?:[1-9][0-9]{0,3}|10000)$/.test(value) ? Number(value) : null;
}
export const emptyCostForm = (): CostForm => ({ enabled: '', unit: '', daily_budget: '', weekly_budget: '', monthly_budget: '',
  unknown_budget: '', max_inflight: '', max_unknown: '' });
export function costForm(spec: CostPolicySpec | null): CostForm {
  if (!spec) return emptyCostForm();
  return { enabled: spec.enabled ? 'yes' : 'no', unit: unitKey(spec.unit),
    daily_budget: unitsToAmount(spec.daily_budget, spec.unit.scale), weekly_budget: unitsToAmount(spec.weekly_budget, spec.unit.scale),
    monthly_budget: unitsToAmount(spec.monthly_budget, spec.unit.scale), unknown_budget: unitsToAmount(spec.unknown_budget, spec.unit.scale),
    max_inflight: String(spec.max_inflight), max_unknown: String(spec.max_unknown) };
}
export function validateCostForm(form: CostForm, units: readonly CostUnit[]): { spec: CostPolicySpec | null; errors: CostErrors } {
  const errors: CostErrors = {};
  const unit = units.find(item => validUnit(item) && unitKey(item) === form.unit);
  if (!unit) errors.unit = '请选择服务器提供的成本单位。';
  if (form.enabled !== 'yes' && form.enabled !== 'no') errors.enabled = '请选择是否允许新的模型调用。';
  const amounts = {} as Record<BudgetKey, string>;
  for (const key of budgetKeys) {
    const value = unit ? amountToUnits(form[key], unit.scale) : null;
    if (value === null) errors[key] = '请输入非负金额，使用小数点且不含分隔符；不得超出单位精度或金额上限。';
    else amounts[key] = value;
  }
  const limits = {} as Record<LimitKey, number>;
  for (const key of limitKeys) {
    const value = callLimit(form[key]);
    if (value === null) errors[key] = '调用数量须为 1–10000 的整数。';
    else limits[key] = value;
  }
  return { errors, spec: Object.keys(errors).length ? null : { enabled: form.enabled === 'yes', unit: { ...unit! }, ...amounts, ...limits } };
}
export function sameCostSpec(a: CostPolicySpec | null, b: CostPolicySpec | null): boolean {
  if (a === null || b === null) return a === b;
  return a.enabled === b.enabled && unitKey(a.unit) === unitKey(b.unit) && budgetKeys.every(key => a[key] === b[key]) && limitKeys.every(key => a[key] === b[key]);
}
export function validCostSpec(value: unknown): value is CostPolicySpec {
  if (!value || typeof value !== 'object') return false;
  const spec = value as CostPolicySpec;
  if (typeof spec.enabled !== 'boolean' || !validUnit(spec.unit)) return false;
  return budgetKeys.every(key => typeof spec[key] === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(spec[key]) && BigInt(spec[key]) <= MAX) &&
    limitKeys.every(key => Number.isInteger(spec[key]) && spec[key] >= 1 && spec[key] <= 10000);
}
