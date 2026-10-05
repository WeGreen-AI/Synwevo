/** Model-provider contracts and exact form conversions. No I/O or persistent storage. */
export const MODEL_PROVIDER_API = '/api/admin/billing/model-provider';
export type ProviderEnvironment = 'test' | 'live' | null;
export const routeTextKeys = ['requested_model', 'confirmed_route', 'confirmation_ref', 'cost_basis_ref',
  'account_confirmation_ref', 'sale_price_id', 'cost_price_id'] as const;
export type RouteTextKey = typeof routeTextKeys[number];
/** DeepSeek reasoning_effort values in canonical order; 'none' turns thinking off. */
export const efforts = ['none', 'low', 'high', 'max'] as const;
export type Effort = typeof efforts[number];
/** Incomplete endings an administrator may charge the payer for (shared contract). */
export const chargingKinds = ['length', 'content_filter', 'provider_interrupted', 'client_cancelled'] as const;
export type ChargingKind = typeof chargingKinds[number];
export type ChargingPolicy = Record<ChargingKind, boolean>;
export type ProviderRoute = Record<RouteTextKey, string> & {
  input_token_ceiling: number; output_token_ceiling: number; valid_until: number; reasoning_efforts?: Effort[];
};
/** Versions published before a field existed are read with the server's defaults. */
export type ProviderSpec = { enabled: boolean; route: ProviderRoute | null; incomplete_charging?: ChargingPolicy };
export type ProviderState = {
  environment: ProviderEnvironment; provider: 'deepseek'; account_ref: 'platform-deepseek'; revision: number;
  spec: ProviderSpec | null; credential_revision: number; credential_configured: boolean;
  actor_id: string | null; created_at: number | null; reason: string | null; operation_id: string | null;
};
/** Automatic closure of paid dispatch on the current revision (upstream 402, missing model id). */
export type ProviderHealth = {
  closure: { state: 'model_missing' | 'upstream_balance'; reason_code: string; requested_model: string; since: number } | null;
  last_models_check: { state: 'model_listed' | 'model_missing' | 'models_check_failed'; at: number } | null;
};
export type ProviderHistory = { items: ProviderState[]; next_before_revision: number | null };
export type CredentialAction = 'keep' | 'replace' | 'revoke';
export type ProviderCredential = { action: 'keep' | 'revoke' } | { action: 'replace'; api_key: string };
export type ProviderRequest = { expected_revision: number; spec: ProviderSpec; credential: ProviderCredential };
/** The server digest is intentionally excluded from the retained/displayed preview. */
export type ProviderPreview = {
  before: ProviderState; after: ProviderSpec; credential: { action: CredentialAction };
  credential_revision: number; credential_configured: boolean; preview_token: string;
  expires_at: number; risk: 'high'; environment: ProviderEnvironment;
};
export type ProviderPublication = { operation_id: string; status: 'succeeded'; state: ProviderState };
export type ProviderAttempt = {
  environment: ProviderEnvironment; credential_revision: number; credential_configured: boolean;
  body: ProviderRequest & { preview_token: string; idempotency_key: string; reason: string };
};
export type ProviderForm = Record<RouteTextKey | 'input_token_ceiling' | 'output_token_ceiling' | 'valid_until', string> & {
  enabled: boolean; route_mode: 'none' | 'route'; credential_action: CredentialAction;
} & Record<`effort_${Effort}` | `charge_${ChargingKind}`, boolean>;
export type ProviderErrors = Partial<Record<keyof ProviderForm | 'api_key' | 'reasoning_efforts', string>>;
export const effortLabels: Record<Effort, string> = { none: '不思考（关闭思考模式）', low: '低', high: '高（默认）', max: '最高' };
export const chargingLabels: Record<ChargingKind, string> = {
  length: '输出达到长度上限而截断', content_filter: '被提供方内容过滤终止',
  provider_interrupted: '提供方中断（资源不足或中止）', client_cancelled: '用户在最终用量返回后停止',
};
export const noCharging = (): ChargingPolicy => ({ length: false, content_filter: false, provider_interrupted: false, client_cancelled: false });
export const routeEfforts = (route: ProviderRoute): Effort[] => route.reasoning_efforts ?? [...efforts];
export const specCharging = (spec: ProviderSpec): ChargingPolicy => spec.incomplete_charging ?? noCharging();
export const routeLabels: Record<keyof ProviderRoute, string> = {
  requested_model: '请求模型标识', confirmed_route: '已核实的实际路线', confirmation_ref: '模型与路线核实证据',
  cost_basis_ref: '成本依据引用', account_confirmation_ref: '账户核实证据', sale_price_id: '销售价格版本 ID',
  cost_price_id: '成本价格版本 ID', input_token_ceiling: '输入 token 上限', output_token_ceiling: '输出 token 上限',
  valid_until: '路线有效期（UTC）', reasoning_efforts: '允许的推理投入',
};
const MAX_SECONDS = 253402300799;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number =>
  typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max;
const text = (v: unknown): v is string => typeof v === 'string' && !!v.trim() && v === v.trim() && !/[\u0000-\u001f\u007f]/.test(v);
export function exactInteger(value: unknown, min: number, max: number): number | null {
  if (typeof value !== 'string' || !/^(0|[1-9][0-9]*)$/.test(value) || value.length > 16) return null;
  const n = Number(value);
  return integer(n, min, max) ? n : null;
}
/** Explicit UTC avoids DST gaps/folds and preserves the server's instant when copying history. */
export function providerDateInput(seconds: number): string {
  return integer(seconds, 0, MAX_SECONDS) ? new Date(seconds * 1000).toISOString().slice(0, 19) : '';
}
export function providerTimestamp(value: unknown): number | null {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) return null;
  const normalized = value.length === 16 ? `${value}:00` : value;
  const seconds = Date.parse(`${normalized}Z`) / 1000;
  return integer(seconds, 0, MAX_SECONDS) && providerDateInput(seconds) === normalized ? seconds : null;
}
export const emptyProviderForm = (): ProviderForm => ({ enabled: false, route_mode: 'none', credential_action: 'keep',
  requested_model: '', confirmed_route: '', confirmation_ref: '', cost_basis_ref: '', account_confirmation_ref: '',
  sale_price_id: '', cost_price_id: '', input_token_ceiling: '', output_token_ceiling: '', valid_until: '',
  effort_none: true, effort_low: true, effort_high: true, effort_max: true,
  charge_length: false, charge_content_filter: false, charge_provider_interrupted: false, charge_client_cancelled: false });
export function providerForm(spec: ProviderSpec | null): ProviderForm {
  const form = emptyProviderForm();
  if (!spec) return form;
  form.enabled = spec.enabled;
  const charging = specCharging(spec);
  for (const kind of chargingKinds) form[`charge_${kind}`] = charging[kind];
  if (spec.route) {
    const allowed = routeEfforts(spec.route);
    for (const effort of efforts) form[`effort_${effort}`] = allowed.includes(effort);
    form.route_mode = 'route';
    for (const key of routeTextKeys) form[key] = spec.route[key];
    form.input_token_ceiling = String(spec.route.input_token_ceiling);
    form.output_token_ceiling = String(spec.route.output_token_ceiling);
    form.valid_until = providerDateInput(spec.route.valid_until);
  }
  return form;
}
/** Copy route settings only; neither historical credential metadata nor a draft key is restored. */
export function copyProviderRoute(form: ProviderForm, spec: ProviderSpec): ProviderForm {
  return { ...providerForm(spec), credential_action: form.credential_action };
}
export function validateProviderForm(form: ProviderForm, apiKey: string): {
  spec: ProviderSpec | null; credential: ProviderCredential | null; errors: ProviderErrors;
} {
  const errors: ProviderErrors = {};
  if (typeof form.enabled !== 'boolean') errors.enabled = '请选择路线启用状态。';
  if (!['none', 'route'].includes(form.route_mode) || form.enabled && form.route_mode !== 'route') {
    errors.route_mode = '启用前必须填写完整路线；没有路线时只能停用。';
  }
  let route: ProviderRoute | null = null;
  if (form.route_mode === 'route') {
    for (const key of routeTextKeys) if (!text(form[key])) errors[key] = '请填写真实标识或证据引用，不含首尾空白或控制字符。';
    const input = exactInteger(form.input_token_ceiling, 1, 2000000);
    const output = exactInteger(form.output_token_ceiling, 1, 32768);
    const until = providerTimestamp(form.valid_until);
    if (input === null) errors.input_token_ceiling = '输入上限须为 1–2000000 的整数，不接受小数或指数。';
    if (output === null) errors.output_token_ceiling = '输出上限须为 1–32768 的整数，不接受小数或指数。';
    if (until === null) errors.valid_until = '请输入有效的 UTC 日期时间，精确到秒，范围为 1970–9999 年。';
    const allowed = efforts.filter(effort => form[`effort_${effort}`] === true);
    if (!allowed.length) errors.reasoning_efforts = '请至少允许一种推理投入。';
    if (!Object.keys(errors).length) route = { requested_model: form.requested_model, confirmed_route: form.confirmed_route,
      confirmation_ref: form.confirmation_ref, cost_basis_ref: form.cost_basis_ref, account_confirmation_ref: form.account_confirmation_ref,
      sale_price_id: form.sale_price_id, cost_price_id: form.cost_price_id, input_token_ceiling: input!, output_token_ceiling: output!,
      valid_until: until!, reasoning_efforts: allowed };
  }
  const incomplete_charging = noCharging();
  for (const kind of chargingKinds) {
    if (typeof form[`charge_${kind}`] !== 'boolean') errors[`charge_${kind}`] = '请选择是否收费。';
    else incomplete_charging[kind] = form[`charge_${kind}`];
  }
  let credential: ProviderCredential | null = null;
  if (form.credential_action === 'replace') {
    if (!text(apiKey)) errors.api_key = '请输入新密钥，不含首尾空白或控制字符。';
    else credential = { action: 'replace', api_key: apiKey };
  } else if (form.credential_action === 'keep' || form.credential_action === 'revoke') credential = { action: form.credential_action };
  else errors.credential_action = '请选择凭据操作。';
  return { errors, spec: Object.keys(errors).length ? null : { enabled: form.enabled, route, incomplete_charging }, credential };
}
const validEfforts = (value: unknown) => value === undefined || (Array.isArray(value) && value.length > 0 &&
  value.every(v => (efforts as readonly unknown[]).includes(v)) && new Set(value).size === value.length);
const validCharging = (value: unknown) => value === undefined || (record(value) &&
  Object.keys(value).every(key => (chargingKinds as readonly string[]).includes(key)) &&
  chargingKinds.every(kind => typeof value[kind] === 'boolean'));
export function validProviderRoute(value: unknown): value is ProviderRoute {
  return record(value) && routeTextKeys.every(key => text(value[key])) && integer(value.input_token_ceiling, 1, 2000000) &&
    integer(value.output_token_ceiling, 1, 32768) && integer(value.valid_until, 0, MAX_SECONDS) && validEfforts(value.reasoning_efforts);
}
export function validProviderSpec(value: unknown): value is ProviderSpec {
  return record(value) && typeof value.enabled === 'boolean' && (value.route === null ? !value.enabled : validProviderRoute(value.route)) &&
    validCharging(value.incomplete_charging);
}
/** The health of the current revision, or null when the payload carries none (or an invalid one). */
export function providerHealth(value: unknown): ProviderHealth | null {
  if (!record(value) || !record(value.health)) return null;
  const { closure, last_models_check: check } = value.health;
  const closureOk = closure === null || (record(closure) && ['model_missing', 'upstream_balance'].includes(closure.state as string) &&
    text(closure.reason_code) && text(closure.requested_model) && integer(closure.since, 0, MAX_SECONDS));
  const checkOk = check === null || (record(check) && ['model_listed', 'model_missing', 'models_check_failed'].includes(check.state as string) &&
    integer(check.at, 0, MAX_SECONDS));
  return closureOk && checkOk ? value.health as ProviderHealth : null;
}
export function validProviderState(value: unknown): value is ProviderState {
  return record(value) && [null, 'test', 'live'].includes(value.environment as ProviderEnvironment) &&
    value.provider === 'deepseek' && value.account_ref === 'platform-deepseek' && integer(value.revision) &&
    (value.spec === null || validProviderSpec(value.spec)) && integer(value.credential_revision) && typeof value.credential_configured === 'boolean' &&
    [value.actor_id, value.reason, value.operation_id].every(v => v === null || typeof v === 'string') &&
    (value.created_at === null || integer(value.created_at, 0, MAX_SECONDS));
}
export function sameProviderSpec(a: ProviderSpec | null, b: ProviderSpec | null): boolean {
  if (a === null || b === null) return a === b;
  if (a.enabled !== b.enabled) return false;
  const ac = specCharging(a), bc = specCharging(b);
  if (chargingKinds.some(kind => ac[kind] !== bc[kind])) return false;
  if (a.route === null || b.route === null) return a.route === b.route;
  return [...routeTextKeys, 'input_token_ceiling', 'output_token_ceiling', 'valid_until']
    .every(key => a.route![key as keyof ProviderRoute] === b.route![key as keyof ProviderRoute]) &&
    routeEfforts(a.route).join() === routeEfforts(b.route).join();
}
export function sameProviderVersion(a: ProviderState, b: ProviderState): boolean {
  return a.environment === b.environment && a.revision === b.revision && a.credential_revision === b.credential_revision &&
    a.credential_configured === b.credential_configured && sameProviderSpec(a.spec, b.spec);
}
export function validProviderHistory(value: unknown, cursor: number | null, environment: ProviderEnvironment | undefined): value is ProviderHistory {
  if (!record(value) || !Array.isArray(value.items) || value.items.length > 20 || !value.items.every(validProviderState)) return false;
  const items = value.items as ProviderState[];
  if (items.some((item, i) => item.environment !== environment || item.revision <= 0 ||
    (i === 0 ? cursor !== null && item.revision >= cursor : item.revision >= items[i - 1].revision))) return false;
  return value.next_before_revision === null || (items.length > 0 && integer(value.next_before_revision, 1) &&
    value.next_before_revision === items[items.length - 1].revision && (cursor === null || value.next_before_revision < cursor));
}
const credentialAfter = (before: ProviderState, action: CredentialAction) => ({
  credential_revision: before.credential_revision + (action === 'keep' ? 0 : 1),
  credential_configured: action === 'keep' ? before.credential_configured : action === 'replace',
});
export function readProviderPreview(value: unknown, before: ProviderState, request: ProviderRequest, now = Date.now()): ProviderPreview | null {
  if (!record(value) || !validProviderState(value.before) || !sameProviderVersion(value.before, before) ||
    request.expected_revision !== before.revision || !validProviderSpec(value.after) || !sameProviderSpec(value.after, request.spec) ||
    !record(value.credential) || value.credential.action !== request.credential.action ||
    !(value.credential.digest === null || typeof value.credential.digest === 'string') ||
    value.environment !== before.environment || value.risk !== 'high' || !text(value.preview_token) ||
    typeof value.expires_at !== 'number' || !Number.isFinite(value.expires_at) || value.expires_at * 1000 <= now || value.expires_at > MAX_SECONDS) return null;
  const expected = credentialAfter(before, request.credential.action);
  if (value.credential_revision !== expected.credential_revision || value.credential_configured !== expected.credential_configured) return null;
  return { before: value.before, after: value.after, credential: { action: request.credential.action }, ...expected,
    preview_token: value.preview_token, expires_at: value.expires_at, risk: 'high', environment: before.environment };
}
export function createProviderAttempt(plan: ProviderPreview, request: ProviderRequest, reason: string, id: string): ProviderAttempt {
  if (!text(id) || Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500 ||
    request.expected_revision !== plan.before.revision || !sameProviderSpec(request.spec, plan.after) || request.credential.action !== plan.credential.action) {
    throw new RangeError('Invalid provider publication');
  }
  // Own the exact payload so later edits cannot alter an uncertain retry.
  return { environment: plan.environment, credential_revision: plan.credential_revision, credential_configured: plan.credential_configured,
    body: { expected_revision: request.expected_revision, spec: { enabled: request.spec.enabled,
      route: request.spec.route ? { ...request.spec.route, ...(request.spec.route.reasoning_efforts ? { reasoning_efforts: [...request.spec.route.reasoning_efforts] } : {}) } : null,
      ...(request.spec.incomplete_charging ? { incomplete_charging: { ...request.spec.incomplete_charging } } : {}) },
      credential: { ...request.credential }, preview_token: plan.preview_token, idempotency_key: id, reason: reason.trim() } };
}
export function validProviderPublication(value: unknown, attempt: ProviderAttempt): value is ProviderPublication {
  return record(value) && value.status === 'succeeded' && text(value.operation_id) && validProviderState(value.state) &&
    value.state.environment === attempt.environment && value.state.revision === attempt.body.expected_revision + 1 &&
    value.state.operation_id === value.operation_id && sameProviderSpec(value.state.spec, attempt.body.spec) &&
    value.state.credential_revision === attempt.credential_revision && value.state.credential_configured === attempt.credential_configured;
}
/** Erase our mutable references after success/unmount, never during an unresolved replay. */
export function clearProviderCredential(request: ProviderRequest | null): void {
  if (request?.credential.action === 'replace') request.credential.api_key = '';
}
export function providerFailureMode(wasUncertain: boolean, error: { application: boolean; status?: number; code?: string }): 'uncertain' | 'reauth' | 'refused' {
  if (wasUncertain || !error.application || error.status === 0 || error.status === 408 || (error.status ?? 500) >= 500) return 'uncertain';
  return error.code === 'reauth_required' ? 'reauth' : 'refused';
}
