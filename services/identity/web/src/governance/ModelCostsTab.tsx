import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ApiError, post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ActionDialog } from './ActionDialog';
import { permitted, type Held } from './access';
import { useAdminData } from './data';
import { errText } from './labels';
import { useUnsavedChanges } from '../lib/hooks';
import { walletAmount } from '../pages/account/money';
import { budgetKeys, limitKeys, trafficBounds, trafficKeys, costForm, emptyCostForm, sameCostSpec, unitKey, validCostSpec, validUnit,
  validateCostForm, unitsToAmount, type CostForm, type CostPolicySpec, type CostUnit, type TrafficKey } from './model-costs';
import './model-costs.messages';

type Environment = 'test' | 'live' | null;
type PolicyState = { environment: Environment; provider: 'deepseek'; account_ref: 'platform-deepseek'; revision: number;
  spec: CostPolicySpec | null; actor_id: string | null; created_at: number | null; reason: string | null; operation_id: string | null };
type Current = PolicyState & { units: CostUnit[] };
type History = { items: PolicyState[]; next_before_revision: number | null };
type Plan = { before: PolicyState; after: CostPolicySpec; preview_token: string; expires_at: number; risk: 'high'; environment: Environment };
type Publication = { operation_id: string; status: 'succeeded'; state: PolicyState };
type TrafficUsage = { environment: Environment; configured: boolean; as_of: number | null; window_s: number; requests: number;
  tokens: number; inflight: number; waiting: number; oldest_wait_s: number };
const count = (value: unknown) => Number.isSafeInteger(value) && (value as number) >= 0;
const validUsage = (value: TrafficUsage | null): value is TrafficUsage => !!value && typeof value.configured === 'boolean' &&
  (value.as_of === null || count(value.as_of)) && [value.window_s, value.requests, value.tokens, value.inflight, value.waiting, value.oldest_wait_s].every(count);
type Attempt = { environment: Environment; body: { expected_revision: number; spec: CostPolicySpec; preview_token: string;
  idempotency_key: string; reason: string } };
const API = '/api/admin/billing/cost-policy';
const labels = { daily_budget: '每日成本上限', weekly_budget: '每周成本上限', monthly_budget: '每月成本上限',
  unknown_budget: '用量待确认的成本上限', max_inflight: '同时进行的调用上限', max_unknown: '用量待确认的调用上限',
  platform_rpm: '平台每分钟调用数', platform_tpm: '平台每分钟 token 数', payer_rpm: '每个付款人每分钟调用数',
  payer_tpm: '每个付款人每分钟 token 数', payer_inflight: '每个付款人同时进行的调用数', wait_seconds: '最长排队等待（秒）',
  max_waiting: '排队中的调用总数上限', max_waiting_per_payer: '每个付款人排队中的调用上限' };
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 16 };
const fieldset: CSSProperties = { margin: 0, padding: 0, border: 0, minWidth: 0 };
const sameForm = (a: CostForm, b: CostForm) => Object.keys(a).every(key => a[key as keyof CostForm] === b[key as keyof CostForm]);
const uncertainResponse = (err: unknown) => !(err instanceof ApiError) || err.upstream || err.status >= 500 || err.status === 408 || err.status === 0;
const localError = (message: string) => new ApiError(400, 'model_cost_form', message);
function validState(value: unknown): value is PolicyState {
  if (!value || typeof value !== 'object') return false;
  const state = value as PolicyState;
  return [null, 'test', 'live'].includes(state.environment) && state.provider === 'deepseek' && state.account_ref === 'platform-deepseek' &&
    Number.isSafeInteger(state.revision) && state.revision >= 0 && (state.spec === null ? state.revision === 0 : state.revision > 0 && validCostSpec(state.spec)) &&
    [state.actor_id, state.reason, state.operation_id].every(value => value === null || typeof value === 'string') &&
    (state.created_at === null || Number.isSafeInteger(state.created_at) && state.created_at >= 0 && state.created_at <= 253402300799);
}
const validCurrent = (value: Current | null): value is Current => validState(value) && Array.isArray(value.units) && value.units.length <= 100 && value.units.every(validUnit);

function PolicyDetails({ spec }: { spec: CostPolicySpec | null }) {
  const { t, locale } = useI18n();
  if (!spec) return <p className="muted">{t('尚未发布成本策略。')}</p>;
  return <dl className="col gap8 small" style={{ margin: 0 }}>
    <div><dt>{t('新的模型调用')}</dt><dd style={{ margin: 0 }}>{t(spec.enabled ? '允许（仍须满足其他条件）' : '停止')}</dd></div>
    <div><dt>{t('成本单位')}</dt><dd style={{ margin: 0 }}>{t('{code}（小数位数：{scale}）', spec.unit)}</dd></div>
    {budgetKeys.map(key => <div key={key}><dt>{t(labels[key])}</dt><dd style={{ margin: 0 }}>{walletAmount(spec[key], spec.unit.scale, locale)} {spec.unit.code}</dd></div>)}
    {limitKeys.map(key => <div key={key}><dt>{t(labels[key])}</dt><dd style={{ margin: 0 }}>{spec[key]}</dd></div>)}
    <div><dt>{t('模型调用速率与排队')}</dt><dd style={{ margin: 0 }}>{t(spec.traffic ? '已启用' : '未设置（只执行同时进行的调用上限）')}</dd></div>
    {spec.traffic && trafficKeys.map(key => <div key={key}><dt>{t(labels[key])}</dt>
      <dd style={{ margin: 0 }}>{new Intl.NumberFormat(locale).format(spec.traffic![key])}</dd></div>)}
  </dl>;
}

/** No read request or editor is mounted without the view grant. The API remains authoritative. */
export function ModelCostsTab({ held, onDenied }: { held: Held; onDenied: () => void }) {
  if (permitted(held, 'platform.config.view').disabled) return null;
  return <ModelCostsContent held={held} onDenied={onDenied} />;
}

function ModelCostsContent({ held, onDenied }: { held: Held; onDenied: () => void }) {
  const { t, locale } = useI18n();
  const id = useId();
  const current = useAdminData<Current>(API, onDenied);
  const [cursors, setCursors] = useState<(number | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const history = useAdminData<History>(`${API}/history?limit=20${cursor === null ? '' : `&before_revision=${cursor}`}`, onDenied);
  const traffic = useAdminData<TrafficUsage>(`${API}/traffic`, onDenied);
  const usage = validUsage(traffic.data) ? traffic.data : null;
  const [base, setBase] = useState<PolicyState | null>(null);
  const [form, setForm] = useState<CostForm>(emptyCostForm);
  const [saved, setSaved] = useState<CostForm>(emptyCostForm);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<'preview' | 'publish' | null>(null);
  const [tried, setTried] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const attemptRef = useRef<Attempt | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const uncertainRef = useRef(false);
  const [result, setResult] = useState<Publication | null>(null);
  const [copy, setCopy] = useState<PolicyState | null>(null);
  const [now, setNow] = useState(Date.now());
  const heading = useRef<HTMLHeadingElement>(null);
  const flight = useRef(false);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const state = validCurrent(current.data) ? current.data : null;
  const units = state?.units || [];
  const selectedUnit = units.find(unit => unitKey(unit) === form.unit);
  const environment = state?.environment;
  const manage = !permitted(held, 'platform.config.manage').disabled;
  const canWrite = manage && !!state && (environment === 'test' || environment === 'live') && !current.loading && !current.err;
  const changedBase = !!base && !!state && (base.environment !== state.environment || base.revision !== state.revision);
  const changedEnvironment = !!plan && plan.environment !== environment;
  const frozen = !!busy || dialogOpen || uncertain || !!copy;
  const dirty = !sameForm(form, saved);
  const validation = validateCostForm(form, units);
  const expired = !!plan && now >= plan.expires_at * 1000;
  const historyValid = !!history.data && Array.isArray(history.data.items) && history.data.items.length <= 20 && history.data.items.every(validState) &&
    (history.data.next_before_revision === null || Number.isSafeInteger(history.data.next_before_revision) && history.data.next_before_revision > 0 &&
      (cursor === null || history.data.next_before_revision < cursor));
  useUnsavedChanges(dirty || uncertain || !!busy);

  useEffect(() => {
    if (!base && state && !current.loading && !current.err) {
      setBase(state); const next = costForm(state.spec); setForm(next); setSaved(next);
    }
  }, [base, state, current.loading, current.err]);
  useEffect(() => {
    if (!plan) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan]);

  const environmentName = (env: Environment | undefined) => t(env === 'live' ? '正式环境' : env === 'test' ? '测试环境' : '未配置');
  const time = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long' }).format(new Date(seconds * 1000));
  function fail(err: unknown) {
    setError(err);
    if (err instanceof ApiError && !err.upstream) {
      if (err.status === 409) setConflict(true);
      if (['platform_permission_denied', 'platform_admin_required'].includes(err.code) || err.status === 404) onDenied();
    }
  }
  function setOriginal(value: Attempt | null) { attemptRef.current = value; setAttempt(value); }
  function refresh() { current.reload(); history.reload(); traffic.reload(); }
  function edit(key: keyof CostForm, value: string) {
    if (frozen || !canWrite) return;
    setForm({ ...form, [key]: value }); setPlan(null); setOriginal(null); setResult(null);
  }
  function adoptLatest() {
    if (!state || !canWrite || frozen) return;
    setBase(state); setSaved(costForm(state.spec)); setConflict(false); setPlan(null); setOriginal(null); setError(null);
    // Deliberately keep every input, even when the newest version has a different unit.
    heading.current?.focus();
  }
  function applyCopy(item: PolicyState) {
    if (!canWrite || flight.current || uncertainRef.current || !item.spec || item.environment !== environment) return;
    setForm(costForm(item.spec)); setPlan(null); setOriginal(null); setTried(false); setError(null); setResult(null); setCopy(null);
    requestAnimationFrame(() => heading.current?.focus());
  }
  async function preview(event: FormEvent) {
    event.preventDefault(); setTried(true);
    if (!canWrite || !base || frozen || flight.current || conflict || changedBase) return;
    if (!validation.spec) { document.getElementById(`${id}-${Object.keys(validation.errors)[0]}`)?.focus(); return; }
    flight.current = true; setBusy('preview'); setError(null); setConfirmed(false); setPlan(null); setOriginal(null);
    const spec = validation.spec;
    try {
      const out = await post<Plan>(`${API}/preview`, { expected_revision: base.revision, spec });
      if (!alive.current) return;
      if (!validState(out?.before) || !validCostSpec(out.after) || out.before.revision !== base.revision ||
        !sameCostSpec(out.before.spec, base.spec) || out.environment !== environment || out.before.environment !== environment ||
        !sameCostSpec(out.after, spec) || out.risk !== 'high' || typeof out.preview_token !== 'string' || !out.preview_token ||
        typeof out.expires_at !== 'number' || !Number.isFinite(out.expires_at) ||
        out.expires_at * 1000 <= Date.now() || out.expires_at > 253402300799) {
        throw localError('成本预览与当前输入不一致或已过期，请刷新后重新预览。');
      }
      setPlan(out); setDialogOpen(true); setNow(Date.now());
    } catch (err) { if (alive.current) fail(err); }
    finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  async function publish(reason: string): Promise<Publication> {
    if (flight.current || !plan || !canWrite || !confirmed || changedEnvironment) throw localError('请先核对有效的成本预览和发布影响。');
    let original = attemptRef.current;
    if (!uncertainRef.current && (changedBase || conflict || Date.now() >= plan.expires_at * 1000)) throw localError('成本预览已失效，请关闭并重新预览。');
    if (!original) {
      if (Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500) throw localError('发布理由须为 8–500 个字符。');
      original = { environment, body: { expected_revision: plan.before.revision, spec: plan.after, preview_token: plan.preview_token,
        idempotency_key: crypto.randomUUID(), reason: reason.trim() } };
      setOriginal(original);
    }
    if (original.environment !== environment) throw localError('环境已变化，不能在另一环境重试原请求。');
    flight.current = true; setBusy('publish'); setError(null);
    try {
      const out = await post<Publication>(`${API}/publish`, original.body);
      if (out?.status !== 'succeeded' || typeof out.operation_id !== 'string' || !out.operation_id || !validState(out.state) ||
        out.state.revision !== original.body.expected_revision + 1 || out.state.environment !== original.environment ||
        out.state.operation_id !== out.operation_id || !sameCostSpec(out.state.spec, original.body.spec)) throw new Error('Invalid cost publication response');
      return out;
    } catch (err) {
      if (alive.current) {
        // Once an outcome is unknown, a later refusal is not evidence that the earlier
        // request failed. Keep the original signed payload and key until replay succeeds.
        if (uncertainRef.current || uncertainResponse(err)) { uncertainRef.current = true; setUncertain(true); }
        else if (!(err instanceof ApiError && err.code === 'reauth_required')) { setOriginal(null); setPlan(null); setDialogOpen(false); }
        fail(err);
      }
      throw err;
    } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  function completed(out: Publication) {
    if (!alive.current) return;
    uncertainRef.current = false; setUncertain(false); setOriginal(null); setResult(out); setBase(out.state);
    const next = costForm(out.state.spec); setForm(next); setSaved(next); setConflict(false); setPlan(null); setDialogOpen(false); setConfirmed(false);
    setCursors([null]); refresh(); requestAnimationFrame(() => heading.current?.focus());
  }
  function details(item: PolicyState) {
    return <div className="col gap8">
      <p>{t('版本 {revision} · {environment}', { revision: item.revision, environment: environmentName(item.environment) })}</p>
      <p className="small">{t('提供方：{provider} · 成本账户：{account}', { provider: item.provider, account: item.account_ref })}</p>
      <PolicyDetails spec={item.spec} />
      {item.created_at !== null && <p className="small muted">{t('发布于 {time} · 操作者 {actor}', { time: time(item.created_at), actor: item.actor_id || '—' })}</p>}
      {item.reason !== null && <p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('发布理由：{reason}', { reason: item.reason })}</p>}
      {item.operation_id && <p className="small muted">{t('操作编号：{id}', { id: item.operation_id })}</p>}
    </div>;
  }
  function field(key: typeof budgetKeys[number] | typeof limitKeys[number] | TrafficKey, monetary: boolean) {
    const problem = tried ? validation.errors[key] : undefined;
    const traffic = (trafficKeys as readonly string[]).includes(key);
    return <label className="field" htmlFor={`${id}-${key}`} key={key} style={{ minWidth: 0 }}>
      <span>{t(labels[key])}{monetary && selectedUnit ? ` (${selectedUnit.code})` : ''}</span>
      <input id={`${id}-${key}`} className="input" type="text" inputMode={monetary ? 'decimal' : 'numeric'} required
        maxLength={monetary ? 33 : traffic ? 13 : 5} value={form[key]} onChange={event => edit(key, event.target.value)}
        aria-invalid={!!problem} aria-describedby={`${id}-${traffic ? `${key}-range` : 'amount-help'}${problem ? ` ${id}-${key}-error` : ''}`}
        style={{ width: '100%', minWidth: 0 }} />
      {traffic && <span id={`${id}-${key}-range`} className="small muted">{t('范围 {low}–{high}', {
        low: new Intl.NumberFormat(locale).format(trafficBounds[key as TrafficKey][0]),
        high: new Intl.NumberFormat(locale).format(trafficBounds[key as TrafficKey][1]) })}</span>}
      {problem && <span className="small" id={`${id}-${key}-error`}>{t(problem)}</span>}
    </label>;
  }

  return <div className="col gap16" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <section className="card card-b col gap12" aria-labelledby={`${id}-title`}>
      <div className="row wrap gap12"><h2 id={`${id}-title`} className="grow">{t('模型成本策略')}</h2>
        <button type="button" className="btn" disabled={current.loading || history.loading || !!busy || dialogOpen} onClick={refresh}>{t('刷新当前策略与历史')}</button></div>
      <p className="notice info">{t('成本策略环境：{environment}', { environment: environmentName(environment) })}</p>
      <p>{t('这些上限约束平台向模型提供方支付的成本，不是用户售价。发布策略不会启用付费模型，也不会创建价格。')}</p>
      <ul className="small muted">
        <li>{t('按 UTC 自然日、周一开始的自然周和自然月统计成本。')}</li>
        <li>{t('释放用户的预留余额不会清除用量待确认的成本；它持续占用待确认上限，直到完成核实。')}</li>
        <li>{t('停止新的调用不会停止已发生用量的结算，也不会撤销已发送的调用。')}</li>
      </ul>
      {!manage && <p className="notice">{t('你可以查看成本策略和历史，不能修改或发布。')}</p>}
      {state?.environment === null && <p className="notice warn">{t('计费环境尚未配置，成本策略编辑与发布已禁用。')}</p>}
      {current.err && <p className="notice err" role="alert">{t(current.err)}</p>}
      {!current.loading && !current.err && !state && <p className="notice err" role="alert">{t('无法确认服务器的成本策略，请刷新后重试。')}</p>}
      {error != null && <div className="notice err" role="alert"><p>{t('成本策略请求未完成。你的输入已保留，请核对下方状态。')}</p><p>{errText(error)}</p></div>}
      {result && <p className="notice ok" role="status">{t('成本策略版本 {revision} 已发布，操作编号 {id}。付费模型启用状态未改变。', { revision: result.state.revision, id: result.operation_id })}</p>}
      {uncertain && attempt && <div className="notice warn col gap8" role="alert">
        <p>{t('发布结果尚不确定，输入已锁定。请保留此页面，仅重试原请求；刷新记录不能确认原请求失败。')}</p>
        <p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p>
        {changedEnvironment && <p>{t('环境已变化，不能在另一环境重试原请求。')}</p>}
        <div><button type="button" className="btn" disabled={!canWrite || !!busy || dialogOpen || changedEnvironment} onClick={() => { setConfirmed(false); setDialogOpen(true); }}>{t('核对并重试原请求')}</button></div>
      </div>}
    </section>

    <section className="card card-b col gap12" aria-labelledby={`${id}-traffic-usage`} aria-busy={traffic.loading}>
      <h3 id={`${id}-traffic-usage`}>{t('当前模型调用流量')}</h3>
      <p className="small muted">{t('仅显示平台合计，不显示付款人或会话。数值在读取时计算，不会自动刷新。')}</p>
      {traffic.err ? <p className="notice err" role="alert">{t(traffic.err)}</p> : traffic.loading ? <p role="status">{t('正在读取模型调用流量…')}</p>
        : !usage ? <p className="notice err" role="alert">{t('无法确认模型调用流量，请刷新后重试。')}</p> : <>
        {!usage.configured && <p className="notice">{t('当前成本策略未设置速率与排队限制；以下数值仅供参考。')}</p>}
        <dl className="small" style={grid}>
          {([['最近 {window} 秒内放行的调用', usage.requests], ['最近 {window} 秒内计入的 token', usage.tokens],
            ['进行中的调用', usage.inflight], ['正在排队的调用', usage.waiting], ['最久排队（秒）', usage.oldest_wait_s]] as const).map(([label, value]) =>
            <div key={label}><dt>{t(label, { window: usage.window_s })}</dt><dd style={{ margin: 0 }}>{new Intl.NumberFormat(locale).format(value)}</dd></div>)}
        </dl>
        {usage.as_of !== null && <p className="small muted">{t('读取于 {time}', { time: time(usage.as_of) })}</p>}
      </>}
    </section>

    <section className="card card-b col gap12" aria-labelledby={`${id}-current`} aria-busy={current.loading}>
      <h3 id={`${id}-current`}>{t('当前服务器版本')}</h3>
      {current.loading ? <p role="status">{t('正在读取成本策略…')}</p> : !current.err && state && details(state)}
    </section>

    {manage && base && <section className="card card-b col gap12" aria-labelledby={`${id}-editor`}>
      <h3 id={`${id}-editor`} ref={heading} tabIndex={-1}>{t('编辑下一版本')}</h3>
      <p className="small">{t('以版本 {revision} 为基础；发布前需要核对变更并填写理由。', { revision: base.revision })}</p>
      {(changedBase || conflict) && <div className="notice warn col gap8" role="alert">
        <p>{t('服务器版本或发布条件已变化。输入已保留；请刷新并比较当前版本，再继续。')}</p>
        <div className="row wrap gap8"><button type="button" className="btn" disabled={current.loading || !!busy || dialogOpen} onClick={current.reload}>{t('读取最新成本策略')}</button>
          <button type="button" className="btn" disabled={!canWrite || frozen || (state?.environment === base.environment && state.revision < base.revision)} onClick={adoptLatest}>{t('保留输入，采用当前版本继续')}</button></div>
      </div>}
      {!units.length && <p className="notice">{t('没有可用的成本单位。请先在同一环境发布模型成本价格，然后刷新；读取本页不会创建价格。')}</p>}
      <form className="col gap16" onSubmit={preview} noValidate>
        <p id={`${id}-amount-help`} className="small muted">{t('金额以所选单位输入，使用小数点，不使用千位分隔符；不进行舍入。调用数量为 1–10000，金额为零表示没有可用预算。')}</p>
        <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
          <legend className="small">{t('所有字段均须明确选择或填写')}</legend>
          <div style={grid}>
            <label className="field" htmlFor={`${id}-enabled`}><span>{t('新的模型调用')}</span>
              <select id={`${id}-enabled`} className="input" required value={form.enabled} onChange={event => edit('enabled', event.target.value)}
                aria-invalid={tried && !!validation.errors.enabled} aria-describedby={tried && validation.errors.enabled ? `${id}-enabled-error` : undefined}>
                <option value="">{t('请选择调用策略')}</option><option value="yes">{t('允许（仍须满足其他条件）')}</option><option value="no">{t('停止')}</option>
              </select>{tried && validation.errors.enabled && <span id={`${id}-enabled-error`} className="small">{t(validation.errors.enabled)}</span>}
            </label>
            <label className="field" htmlFor={`${id}-unit`}><span>{t('成本单位')}</span>
              <select id={`${id}-unit`} className="input" required value={form.unit} onChange={event => edit('unit', event.target.value)}
                aria-invalid={tried && !!validation.errors.unit} aria-describedby={`${id}-unit-help${tried && validation.errors.unit ? ` ${id}-unit-error` : ''}`}>
                <option value="">{t('请选择已有成本单位')}</option>
                {form.unit && !units.some(unit => unitKey(unit) === form.unit) && <option value={form.unit} disabled>{t('原单位当前不可用')}</option>}
                {units.map(unit => <option key={unitKey(unit)} value={unitKey(unit)}>{t('{code}（小数位数：{scale}）', unit)}</option>)}
              </select><span id={`${id}-unit-help`} className="small muted">{t('更改单位不会换算已输入的金额，请逐项核对。')}</span>
              {tried && validation.errors.unit && <span id={`${id}-unit-error`} className="small">{t(validation.errors.unit)}</span>}
            </label>
            {budgetKeys.map(key => field(key, true))}{limitKeys.map(key => field(key, false))}
          </div>
          {selectedUnit && <p className="small muted">{t('此单位的单项金额上限为 {amount} {code}。', {
            amount: unitsToAmount('9223372036854775807', selectedUnit.scale), code: selectedUnit.code })}</p>}
        </fieldset>
        <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
          <legend>{t('模型调用速率与排队')}</legend>
          <p className="small muted" id={`${id}-traffic-help`}>{t('调用在预留资金前排队；按付款人轮流放行，单个付款人的上限不影响其他人。平台上限须按已核实的提供方账户额度填写，多个密钥不代表多个账户额度。超过最长等待仍未放行的调用不会发送。')}</p>
          <label className="field" htmlFor={`${id}-traffic`} style={{ maxWidth: 480 }}><span>{t('速率与排队限制')}</span>
            <select id={`${id}-traffic`} className="input" required value={form.traffic} onChange={event => edit('traffic', event.target.value)}
              aria-invalid={tried && !!validation.errors.traffic} aria-describedby={`${id}-traffic-help${tried && validation.errors.traffic ? ` ${id}-traffic-error` : ''}`}>
              <option value="">{t('请选择是否启用')}</option><option value="yes">{t('启用')}</option><option value="no">{t('不设置（只执行同时进行的调用上限）')}</option>
            </select>{tried && validation.errors.traffic && <span id={`${id}-traffic-error`} className="small">{t(validation.errors.traffic)}</span>}
          </label>
          {form.traffic === 'yes' && <div style={grid}>{trafficKeys.map(key => field(key, false))}</div>}
        </fieldset>
        {tried && !validation.spec && <p className="notice err" role="alert">{t('请修正已标出的成本字段，再预览变更。')}</p>}
        <div><button type="submit" className="btn primary" disabled={!canWrite || frozen || changedBase || conflict || (validation.spec !== null && sameCostSpec(validation.spec, base.spec))}>{t('预览成本变更')}</button></div>
        {dirty && <p className="small muted">{t('输入尚未发布，当前生效版本保持不变。')}</p>}
      </form>
    </section>}

    <section className="card card-b col gap12" aria-labelledby={`${id}-history`} aria-busy={history.loading}>
      <h3 id={`${id}-history`}>{t('成本策略发布历史')}</h3>
      <p className="small muted">{t('历史版本不可修改。复制历史设置仅填入编辑器，必须作为新版本预览和发布，不会回滚历史费用。')}</p>
      {history.err && <p className="notice err" role="alert">{t(history.err)}</p>}
      {history.loading ? <p role="status">{t('正在读取发布历史…')}</p> : !history.err && (historyValid ? <>
        {!history.data!.items.length && <p className="muted">{t('此页没有已发布的成本策略。')}</p>}
        {history.data!.items.map(item => <details key={`${item.environment}:${item.revision}`} className="notice">
          <summary>{t('查看成本策略版本 {revision} · {time}', { revision: item.revision, time: item.created_at === null ? '—' : time(item.created_at) })}</summary>
          <div className="col gap12" style={{ paddingTop: 12 }}>{details(item)}
            {manage && <div><button type="button" className="btn" disabled={!canWrite || frozen || !item.spec || item.environment !== environment} onClick={() => { if (dirty) setCopy(item); else applyCopy(item); }}>{t('复制设置到编辑器')}</button></div>}
          </div>
        </details>)}
      </> : <p className="notice err" role="alert">{t('无法确认服务器的成本策略，请刷新后重试。')}</p>)}
      <nav className="row wrap gap8" aria-label={t('成本策略历史分页')}>
        <button type="button" className="btn" disabled={history.loading || cursors.length === 1 || !!busy || dialogOpen} onClick={() => setCursors(values => values.slice(0, -1))}>{t('较新一页')}</button>
        <span className="small">{t('第 {page} 页，每页最多 20 条', { page: cursors.length })}</span>
        <button type="button" className="btn" disabled={history.loading || !!history.err || !historyValid || history.data?.next_before_revision == null || !!busy || dialogOpen}
          onClick={() => { const next = history.data?.next_before_revision; if (next != null) setCursors(values => [...values, next]); }}>{t('较旧一页')}</button>
        {cursors.length > 1 && <button type="button" className="btn" disabled={history.loading || !!busy || dialogOpen} onClick={() => setCursors([null])}>{t('返回最新版本')}</button>}
      </nav>
    </section>
    {busy && <p role="status" className="small muted">{t('正在等待服务器响应…')}</p>}
    {copy && <ActionDialog title={t('替换尚未发布的输入？')} confirmLabel={t('替换输入')} reason="none"
      impact={t('历史设置会替换当前编辑器中的输入。它们仍须作为新版本发布，才会生效。')}
      run={async () => copy} onDone={applyCopy} onClose={() => setCopy(null)} />}
    {dialogOpen && plan && <ActionDialog<Publication> title={t('确认发布成本策略')} confirmLabel={t(uncertain ? '重试原请求' : '发布成本策略')}
      reason={attempt ? 'none' : 'required'} reauthScope="admin" size="wide"
      ready={canWrite && confirmed && !busy && !changedEnvironment && (uncertain || (!expired && !changedBase && !conflict))}
      impact={<div className="col gap12">
        <p><b>{t('高风险操作 · {environment}', { environment: environmentName(plan.environment) })}</b></p>
        <p>{t('提供方：{provider} · 成本账户：{account}', { provider: plan.before.provider, account: plan.before.account_ref })}</p>
        <div style={grid}><div className="col gap8"><h4>{t('变更前：版本 {revision}', { revision: plan.before.revision })}</h4><PolicyDetails spec={plan.before.spec} /></div>
          <div className="col gap8"><h4>{t('准备发布的新设置')}</h4><PolicyDetails spec={plan.after} /></div></div>
        <p>{t('本次发布只修改成本限制；不会启用付费模型、改变用户售价或重算历史费用。停止新调用后，既有用量仍继续结算。')}</p>
        <p className="small">{t('服务器确认预览有效至 {time}。', { time: time(plan.expires_at) })}</p>
        {attempt && <><p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('原发布理由：{reason}', { reason: attempt.body.reason })}</p><p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p></>}
        {uncertain && <p className="notice warn">{t('重试将发送完全相同的原请求、理由和请求编号，即使原预览已经到期。不会创建另一笔发布。')}</p>}
        {(!uncertain && expired || changedBase && !uncertain || conflict && !uncertain) && <p className="notice warn" role="alert">{t('成本预览已失效，请关闭并重新预览。')}</p>}
        {changedEnvironment && <p className="notice warn" role="alert">{t('环境已变化，不能在另一环境重试原请求。')}</p>}
      </div>}
      run={publish} onDone={completed} onClose={() => { if (flight.current) return; setDialogOpen(false); setConfirmed(false); if (!uncertainRef.current) { setPlan(null); setOriginal(null); } }}>
      <label className="row gap8" style={{ alignItems: 'flex-start' }}><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
        <span>{t('我已核对环境、变更前后金额及调用上限，并理解发布影响。')}</span></label>
    </ActionDialog>}
  </div>;
}
