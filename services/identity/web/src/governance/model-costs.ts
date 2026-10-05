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
/** Optional risk controls (docs/platform/model-risk.md): brake windows, balance thresholds, allowlist mode.
 *  Nullable amounts switch a check off; seconds are bounded like the server's RiskSpec. */
export const riskSecondKeys = ['payer_window_s', 'route_window_s', 'balance_poll_s'] as const;
export const riskOptionalAmountKeys = ['payer_window_cost', 'route_window_cost', 'balance_warn', 'balance_close', 'pending_alert'] as const;
export const riskSecondBounds: [number, number] = [60, 86400];
export type BudgetKey = typeof budgetKeys[number];
export type LimitKey = typeof limitKeys[number];
export type TrafficKey = typeof trafficKeys[number];
export type RiskSecondKey = typeof riskSecondKeys[number];
export type RiskAmountKey = typeof riskOptionalAmountKeys[number];
export type TrafficSpec = Record<TrafficKey, number>;
export type RiskSpec = { allowlist_only: boolean; reconcile_tolerance: string } & Record<RiskSecondKey, number> & Record<RiskAmountKey, string | null>;
export type CostPolicySpec = { enabled: boolean; unit: CostUnit; traffic?: TrafficSpec; max_call_cost?: string; risk?: RiskSpec } &
  Record<BudgetKey, string> & Record<LimitKey, number>;
export type CostForm = { enabled: '' | 'yes' | 'no'; unit: string; traffic: '' | 'yes' | 'no'; max_call_cost: string;
  risk: '' | 'yes' | 'no'; allowlist_only: '' | 'yes' | 'no'; reconcile_tolerance: string } &
  Record<BudgetKey | LimitKey | TrafficKey | RiskSecondKey | RiskAmountKey, string>;
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
export function riskSeconds(value: string): number | null {
  if (!/^[1-9][0-9]{1,4}$/.test(value)) return null;
  const n = Number(value);
  return n >= riskSecondBounds[0] && n <= riskSecondBounds[1] ? n : null;
}
const emptyTraffic = () => Object.fromEntries(trafficKeys.map(key => [key, ''])) as Record<TrafficKey, string>;
const emptyRisk = () => ({ allowlist_only: '' as const, reconcile_tolerance: '',
  ...Object.fromEntries([...riskSecondKeys, ...riskOptionalAmountKeys].map(key => [key, ''])) }) as
  Pick<CostForm, 'allowlist_only' | 'reconcile_tolerance' | RiskSecondKey | RiskAmountKey>;
export const emptyCostForm = (): CostForm => ({ enabled: '', unit: '', daily_budget: '', weekly_budget: '', monthly_budget: '',
  unknown_budget: '', max_inflight: '', max_unknown: '', traffic: '', ...emptyTraffic(), max_call_cost: '', risk: '', ...emptyRisk() });
function riskForm(risk: RiskSpec, scale: number) {
  return { allowlist_only: risk.allowlist_only ? 'yes' as const : 'no' as const, reconcile_tolerance: unitsToAmount(risk.reconcile_tolerance, scale),
    ...Object.fromEntries(riskSecondKeys.map(key => [key, String(risk[key])])),
    ...Object.fromEntries(riskOptionalAmountKeys.map(key => [key, risk[key] === null ? '' : unitsToAmount(risk[key]!, scale)])) } as
    Pick<CostForm, 'allowlist_only' | 'reconcile_tolerance' | RiskSecondKey | RiskAmountKey>;
}
export function costForm(spec: CostPolicySpec | null): CostForm {
  if (!spec) return emptyCostForm();
  return { enabled: spec.enabled ? 'yes' : 'no', unit: unitKey(spec.unit),
    daily_budget: unitsToAmount(spec.daily_budget, spec.unit.scale), weekly_budget: unitsToAmount(spec.weekly_budget, spec.unit.scale),
    monthly_budget: unitsToAmount(spec.monthly_budget, spec.unit.scale), unknown_budget: unitsToAmount(spec.unknown_budget, spec.unit.scale),
    max_inflight: String(spec.max_inflight), max_unknown: String(spec.max_unknown), traffic: spec.traffic ? 'yes' : 'no',
    ...(spec.traffic ? Object.fromEntries(trafficKeys.map(key => [key, String(spec.traffic![key])])) as Record<TrafficKey, string> : emptyTraffic()),
    max_call_cost: spec.max_call_cost === undefined ? '' : unitsToAmount(spec.max_call_cost, spec.unit.scale),
    risk: spec.risk ? 'yes' : 'no', ...(spec.risk ? riskForm(spec.risk, spec.unit.scale) : emptyRisk()) };
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
  let maxCallCost: string | undefined;
  if (form.max_call_cost !== '') {
    const value = unit ? amountToUnits(form.max_call_cost, unit.scale) : null;
    if (value === null) errors.max_call_cost = '请输入非负金额，使用小数点且不含分隔符；不得超出单位精度或金额上限。';
    else maxCallCost = value;
  }
  let risk: RiskSpec | undefined;
  if (form.risk !== 'yes' && form.risk !== 'no') errors.risk = '请选择是否启用自动刹车与上游余额阈值。';
  else if (form.risk === 'yes') {
    const values = {} as RiskSpec;
    if (form.allowlist_only !== 'yes' && form.allowlist_only !== 'no') errors.allowlist_only = '请选择是否只允许放行名单中的付款方。';
    else values.allowlist_only = form.allowlist_only === 'yes';
    for (const key of riskSecondKeys) {
      const value = riskSeconds(form[key]);
      if (value === null) errors[key] = '请输入 60–86400 的整数秒数。';
      else values[key] = value;
    }
    for (const key of riskOptionalAmountKeys) {
      if (form[key] === '') { values[key] = null; continue; }
      const value = unit ? amountToUnits(form[key], unit.scale) : null;
      if (value === null) errors[key] = '请输入非负金额，使用小数点且不含分隔符；不得超出单位精度或金额上限。';
      else values[key] = value;
    }
    const tolerance = unit ? amountToUnits(form.reconcile_tolerance, unit.scale) : null;
    if (tolerance === null) errors.reconcile_tolerance = '请输入非负金额，使用小数点且不含分隔符；不得超出单位精度或金额上限。';
    else values.reconcile_tolerance = tolerance;
    if (!errors.balance_warn && !errors.balance_close && values.balance_warn != null && values.balance_close != null &&
      BigInt(values.balance_close) > BigInt(values.balance_warn)) errors.balance_close = '停止阈值不能高于预警阈值。';
    risk = values;
  }
  return { errors, spec: Object.keys(errors).length ? null : { enabled: form.enabled === 'yes', unit: { ...unit! }, ...amounts, ...limits,
    ...(traffic ? { traffic } : {}), ...(maxCallCost !== undefined ? { max_call_cost: maxCallCost } : {}), ...(risk ? { risk } : {}) } };
}
const sameTraffic = (a?: TrafficSpec, b?: TrafficSpec) => !a || !b ? !a && !b : trafficKeys.every(key => a[key] === b[key]);
const sameRisk = (a?: RiskSpec, b?: RiskSpec) => !a || !b ? !a && !b : a.allowlist_only === b.allowlist_only &&
  a.reconcile_tolerance === b.reconcile_tolerance && [...riskSecondKeys, ...riskOptionalAmountKeys].every(key => a[key] === b[key]);
export function sameCostSpec(a: CostPolicySpec | null, b: CostPolicySpec | null): boolean {
  if (a === null || b === null) return a === b;
  return a.enabled === b.enabled && unitKey(a.unit) === unitKey(b.unit) && budgetKeys.every(key => a[key] === b[key]) &&
    limitKeys.every(key => a[key] === b[key]) && sameTraffic(a.traffic, b.traffic) && a.max_call_cost === b.max_call_cost &&
    sameRisk(a.risk, b.risk);
}
const amountString = (value: unknown) => typeof value === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= MAX;
export function validRiskSpec(value: unknown): value is RiskSpec {
  if (!value || typeof value !== 'object') return false;
  const risk = value as RiskSpec;
  return typeof risk.allowlist_only === 'boolean' && amountString(risk.reconcile_tolerance) &&
    riskSecondKeys.every(key => riskSeconds(String(risk[key])) === risk[key]) &&
    riskOptionalAmountKeys.every(key => risk[key] === null || amountString(risk[key])) &&
    (risk.balance_warn === null || risk.balance_close === null || BigInt(risk.balance_close) <= BigInt(risk.balance_warn));
}
export function validCostSpec(value: unknown): value is CostPolicySpec {
  if (!value || typeof value !== 'object') return false;
  const spec = value as CostPolicySpec;
  if (typeof spec.enabled !== 'boolean' || !validUnit(spec.unit)) return false;
  return budgetKeys.every(key => typeof spec[key] === 'string' && /^(0|[1-9][0-9]{0,18})$/.test(spec[key]) && BigInt(spec[key]) <= MAX) &&
    limitKeys.every(key => Number.isInteger(spec[key]) && spec[key] >= 1 && spec[key] <= 10000) &&
    (spec.traffic === undefined || (!!spec.traffic && typeof spec.traffic === 'object' &&
      trafficKeys.every(key => trafficNumber(key, String(spec.traffic![key])) === spec.traffic![key]))) &&
    (spec.max_call_cost === undefined || amountString(spec.max_call_cost)) && (spec.risk === undefined || validRiskSpec(spec.risk));
}
