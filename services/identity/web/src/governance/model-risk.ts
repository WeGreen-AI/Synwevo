/** Pure helpers for the paid-model risk tab (docs/platform/model-risk.md). Money stays in integer
 *  strings of the cost unit; nothing here converts an amount to a binary float. */
import type { CostUnit, RiskSpec } from './model-costs';

export type Environment = 'test' | 'live' | null;
export type Breaker = { id: string | null; state: 'open' | 'closed'; seq: number; source: string | null; reason_code: string | null;
  reason: string | null; actor_id: string | null; created_at: number | null };
export type Reading = { id: string; observed_at: number; status: 'ok' | 'not_configured' | 'error'; code: string;
  is_available: 0 | 1 | null; unit: string | null; scale: number | null; total_units: string | null };
export type Overview = { environment: Environment; configured: boolean; unit: CostUnit | null; risk: RiskSpec | null;
  max_call_cost: string | null; allowlist_required: boolean; allowlist_source: 'policy' | 'default'; breaker: Breaker | null;
  balance: Reading | null; active_suspensions: number; allowlisted: number };
export type Suspension = { id: string; scope_kind: 'user' | 'organization' | 'route'; scope_id: string; payer_label: string | null;
  source: 'brake' | 'journal'; reason_code: string; unit: string | null; window_s: number | null; threshold_units: string | null;
  observed_units: string | null; created_at: number; lifted_at: number | null; lifted_by: string | null; lift_reason: string | null };
export type AllowEntry = { id: string; payer_kind: 'user' | 'organization'; payer_id: string; payer_label: string | null;
  added_by: string; add_reason: string; added_at: number };
export type Topup = { id: string; unit: string; amount_units: string; topped_up_at: number; actor_id: string; reason: string };
export type Reconciliation = { id: string; kind: 'daily' | 'ledger_audit'; window_start: number; window_end: number;
  status: 'ok' | 'drift' | 'incomplete' | 'error'; code: string; unit: string | null; scale: number | null;
  upstream_units: string | null; topup_units: string | null; ledger_cost_n: string | null; ledger_cost_d: string | null;
  pending_units: string | null; drift_n: string | null; drift_d: string | null; tolerance_units: string | null;
  differences: number | null; trigger: 'schedule' | 'admin'; created_at: number };
export type Page<T> = { items: T[]; next_before: number | null };

const INTEGER = /^-?(0|[1-9][0-9]{0,30})$/;
/** A rational amount in smallest units, rounded half away from zero; `exact` says whether rounding happened. */
export function roundedUnits(numerator: string | null, denominator: string | null): { units: string; exact: boolean } | null {
  if (numerator === null || denominator === null || !INTEGER.test(numerator) || !/^[1-9][0-9]{0,30}$/.test(denominator)) return null;
  const n = BigInt(numerator), d = BigInt(denominator);
  const negative = n < 0n, absolute = negative ? -n : n;
  const whole = absolute / d, rest = absolute % d;
  const rounded = rest * 2n >= d ? whole + 1n : whole;
  return { units: String(negative && rounded ? -rounded : rounded), exact: rest === 0n };
}

/** The UTC calendar day of a time in seconds, as the server's reconciliation names it. */
export function utcDay(seconds: number): string {
  return new Date(seconds * 1000).toISOString().slice(0, 10);
}

/** The last UTC day that has ended at `nowMs`: the newest day a reconciliation can cover. */
export function lastEndedUtcDay(nowMs: number): string {
  return utcDay(Math.floor(nowMs / 1000) - 86400);
}

/** A valid finished UTC day in the form the server accepts. */
export function validDay(value: string, nowMs: number): boolean {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(value)) return false;
  const start = Date.parse(value + 'T00:00:00Z');
  return Number.isFinite(start) && utcDay(start / 1000) === value && start + 86400_000 <= nowMs;
}

/** A positive top-up amount in the cost unit's smallest units, or null. */
export function topupUnits(value: string, scale: number): string | null {
  if (!Number.isInteger(scale) || scale < 0 || scale > 12 || value.length > 33 || !/^(0|[1-9][0-9]*)(\.[0-9]+)?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  if (fraction.length > scale) return null;
  const units = BigInt(whole) * 10n ** BigInt(scale) + BigInt(fraction.padEnd(scale, '0') || '0');
  return units > 0n && units <= 9223372036854775807n ? String(units) : null;
}

/** Fixed wording for the server's stable codes (translated where shown). Unknown codes show as codes. */
export const CODE_LABELS: Record<string, string> = {
  balance: '上游余额', upstream_402: '模型提供方报告余额不足（402）', admin: '平台管理员', journal: '恢复备份后重放',
  upstream_balance_below_close: '余额低于停止阈值', upstream_unavailable: '上游账户不可用', upstream_payment_required: '上游返回余额不足',
  admin_opened: '管理员手动关闭', admin_closed: '管理员重新开启', journal_replay: '恢复备份后重放',
  payer_velocity: '付款方成本速率超过阈值', route_velocity: '模型路线成本速率超过阈值',
  ok: '正常', cost_policy_missing: '尚未发布成本策略', credential_unavailable: '平台凭据不可用', provider_disabled: '模型提供方未启用',
  upstream_unreachable: '无法连接上游', upstream_timeout: '上游响应超时', upstream_bad_response: '上游响应无法解析',
  currency_mismatch: '上游余额币种与成本单位不一致', within_tolerance: '差额在容差内', beyond_tolerance: '差额超出容差',
  balance_reading_missing: '缺少边界余额读数', risk_not_configured: '尚未设置风险控制', ledger_capacity: '账本记录过多，无法一次核对',
  cost_unit_mismatch: '成本单位不一致', consistent: '缓存与分录一致', differences: '缓存与分录不一致',
  journal_hold_invariant_broken: '账本冻结金额与分录不一致',
};
export const STATUS_LABELS: Record<Reconciliation['status'], string> = { ok: '一致', drift: '超出容差', incomplete: '数据不完整', error: '执行失败' };
export const SCOPE_LABELS: Record<Suspension['scope_kind'], string> = { user: '个人付款方', organization: '组织付款方', route: '模型路线' };

/** `upstream_http_401` and its kin carry the HTTP status the provider answered with. */
export function codeLabel(code: string | null): { text: string; params?: Record<string, string> } {
  if (!code) return { text: '—' };
  const http = /^upstream_http_([0-9]{3})$/.exec(code);
  if (http) return { text: '上游返回 HTTP {status}', params: { status: http[1] } };
  return CODE_LABELS[code] ? { text: CODE_LABELS[code] } : { text: '代码：{code}', params: { code } };
}
