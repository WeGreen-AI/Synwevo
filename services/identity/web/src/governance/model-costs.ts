/** Exact, bounded conversions for platform cost policy forms. No monetary Number conversion. */
export type CostUnit = { code: string; scale: number };
export const budgetKeys = ['daily_budget', 'weekly_budget', 'monthly_budget', 'unknown_budget'] as const;
export const limitKeys = ['max_inflight', 'max_unknown'] as const;
/** Optional model-call rates and waiting (docs/platform/model-traffic.md); server bounds repeated here. */
export const trafficKeys = ['platform_rpm', 'platform_tpm', 'payer_rpm', 'payer_tpm', 'payer_inflight', 'wait_seconds',
  'max_waiting', 'max_waiting_per_payer'] as const;
export const trafficBounds: Record<typeof trafficKeys[number], [number, number]> = { platform_rpm: [1, 1_000_000],
  platform_tpm: [1, 1_000_000_000_000], payer_rpm: [1, 1_000_000], payer_tpm: [1, 1_000_000_000_000], payer_inflight: [1, 10000],
  wait_seconds: [0, 900], max_waiting: [1, 10000], max_waiting_per_payer: [1, 10000] };
export type BudgetKey = typeof budgetKeys[number];
export type LimitKey = typeof limitKeys[number];
export type TrafficKey = typeof trafficKeys[number];
export type TrafficSpec = Record<TrafficKey, number>;
export type CostPolicySpec = { enabled: boolean; unit: CostUnit; traffic?: TrafficSpec } & Record<BudgetKey, string> & Record<LimitKey, number>;
export type CostForm = { enabled: '' | 'yes' | 'no'; unit: string; traffic: '' | 'yes' | 'no' } & Record<BudgetKey | LimitKey | TrafficKey, string>;
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
export function trafficNumber(key: TrafficKey, value: string): number | null {
  const [low, high] = trafficBounds[key];
  if (!/^(0|[1-9][0-9]{0,12})$/.test(value)) return null;
  const n = Number(value);
  return Number.isSafeInteger(n) && n >= low && n <= high ? n : null;
}
const emptyTraffic = () => Object.fromEntries(trafficKeys.map(key => [key, ''])) as Record<TrafficKey, string>;
export const emptyCostForm = (): CostForm => ({ enabled: '', unit: '', daily_budget: '', weekly_budget: '', monthly_budget: '',
  unknown_budget: '', max_inflight: '', max_unknown: '', traffic: '', ...emptyTraffic() });
export function costForm(spec: CostPolicySpec | null): CostForm {
  if (!spec) return emptyCostForm();
  return { enabled: spec.enabled ? 'yes' : 'no', unit: unitKey(spec.unit),
    daily_budget: unitsToAmount(spec.daily_budget, spec.unit.scale), weekly_budget: unitsToAmount(spec.weekly_budget, spec.unit.scale),
    monthly_budget: unitsToAmount(spec.monthly_budget, spec.unit.scale), unknown_budget: unitsToAmount(spec.unknown_budget, spec.unit.scale),
    max_inflight: String(spec.max_inflight), max_unknown: String(spec.max_unknown), traffic: spec.traffic ? 'yes' : 'no',
    ...(spec.traffic ? Object.fromEntries(trafficKeys.map(key => [key, String(spec.traffic![key])])) as Record<TrafficKey, string> : emptyTraffic()) };
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
  let traffic: TrafficSpec | undefined;
  if (form.traffic !== 'yes' && form.traffic !== 'no') errors.traffic = '请选择是否启用模型调用速率与排队限制。';
  else if (form.traffic === 'yes') {
    const values = {} as TrafficSpec;
    for (const key of trafficKeys) {
      const value = trafficNumber(key, form[key]);
      if (value === null) errors[key] = '请输入范围内的整数。';
      else values[key] = value;
    }
    if (!trafficKeys.some(key => errors[key])) {
      if (values.payer_rpm > values.platform_rpm) errors.payer_rpm = '付款人上限不能高于平台上限。';
      if (values.payer_tpm > values.platform_tpm) errors.payer_tpm = '付款人上限不能高于平台上限。';
      if (values.max_waiting_per_payer > values.max_waiting) errors.max_waiting_per_payer = '每个付款人的排队数不能高于总排队数。';
      if (limits.max_inflight !== undefined && values.payer_inflight > limits.max_inflight) errors.payer_inflight = '付款人同时调用数不能高于平台同时调用上限。';
    }
    traffic = values;
  }
  return { errors, spec: Object.keys(errors).length ? null : { enabled: form.enabled === 'yes', unit: { ...unit! }, ...amounts, ...limits,
    ...(traffic ? { traffic } : {}) } };
}
const sameTraffic = (a?: TrafficSpec, b?: TrafficSpec) => !a || !b ? !a && !b : trafficKeys.every(key => a[key] === b[key]);
export function sameCostSpec(a: CostPolicySpec | null, b: CostPolicySpec | null): boolean {
  if (a === null || b === null) return a === b;
  return a.enabled === b.enabled && unitKey(a.unit) === unitKey(b.unit) && budgetKeys.every(key => a[key] === b[key]) &&
    limitKeys.every(key => a[key] === b[key]) && sameTraffic(a.traffic, b.traffic);
}
export function validCostSpec(value: unknown): value is CostPolicySpec {
  if (!value || typeof value !== 'object') return false;
  const spec = value as CostPolicySpec;
  if (typeof spec.enabled !== 'boolean' || !validUnit(spec.unit)) return false;
  return budgetKeys.every(key => typeof spec[key] === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(spec[key]) && BigInt(spec[key]) <= MAX) &&
    limitKeys.every(key => Number.isInteger(spec[key]) && spec[key] >= 1 && spec[key] <= 10000) &&
    (spec.traffic === undefined || (!!spec.traffic && typeof spec.traffic === 'object' &&
      trafficKeys.every(key => trafficNumber(key, String(spec.traffic![key])) === spec.traffic![key])));
}
