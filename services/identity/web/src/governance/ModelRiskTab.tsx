import { useId, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ActionDialog } from './ActionDialog';
import { permitted, type Held } from './access';
import { useAdminData } from './data';
import { walletAmount } from '../pages/account/money';
import { SCOPE_LABELS, STATUS_LABELS, codeLabel, lastEndedUtcDay, roundedUnits, topupUnits, utcDay, validDay,
  type AllowEntry, type Breaker, type Overview, type Page, type Reading, type Reconciliation, type Suspension, type Topup } from './model-risk';
import './model-risk.messages';

const API = '/api/admin/billing/risk';
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 };
const listStyle: CSSProperties = { listStyle: 'none', margin: 0, padding: 0 };
type Done = { operation_id: string; status: 'succeeded' };
/** One dialog at a time: what it does, to what, and the request it sends (same key on a re-authenticated retry). */
type Values = Record<string, string>;
type SetValue = (key: string, value: string) => void;
type Action = { title: string; confirm: string; impact: ReactNode; danger?: boolean; path: string; body: Record<string, unknown>;
  initial?: Values; ready?: (values: Values) => boolean; fields?: (values: Values, set: SetValue) => ReactNode;
  extra?: (values: Values) => Record<string, unknown> };

/** No read request is mounted without the view grant; the API stays authoritative for every change. */
export function ModelRiskTab({ held, onDenied }: { held: Held; onDenied: () => void }) {
  if (permitted(held, 'platform.config.view').disabled) return null;
  return <ModelRiskContent held={held} onDenied={onDenied} />;
}

function ModelRiskContent({ held, onDenied }: { held: Held; onDenied: () => void }) {
  const { t, locale } = useI18n();
  const id = useId();
  const [scope, setScope] = useState<'active' | 'all'>('active');
  const overview = useAdminData<Overview>(API, onDenied);
  const suspensions = useAdminData<Page<Suspension>>(`${API}/suspensions?state=${scope}`, onDenied);
  const allowlist = useAdminData<Page<AllowEntry>>(`${API}/allowlist`, onDenied);
  const breakers = useAdminData<{ items: Breaker[] }>(`${API}/breaker`, onDenied);
  const balance = useAdminData<{ readings: Reading[]; topups: Topup[] }>(`${API}/balance`, onDenied);
  const reconciliations = useAdminData<Page<Reconciliation>>(`${API}/reconciliations`, onDenied);
  const [action, setAction] = useState<Action | null>(null);
  const [values, setValues] = useState<Values>({});
  const [notice, setNotice] = useState<string | null>(null);
  const status = useRef<HTMLParagraphElement>(null);
  const manage = !permitted(held, 'platform.config.manage').disabled;
  const state = overview.data;
  const unit = state?.unit || null;
  const writable = manage && !!state && (state.environment === 'test' || state.environment === 'live');
  const time = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(seconds * 1000));
  const amount = (units: string | null, scale: number | null | undefined, code: string | null | undefined) =>
    units === null || scale == null ? '—' : `${walletAmount(units, scale, locale)} ${code || ''}`.trim();
  /** Rows that store only a unit code are scaled with the current policy unit when the codes match;
   *  a row from an earlier unit shows its exact smallest units instead of a guessed decimal. */
  const unitAmount = (units: string | null, code: string | null) => units === null ? '—'
    : unit && code === unit.code ? amount(units, unit.scale, code) : t('{units} 个最小单位（{code}）', { units, code: code || '—' });
  const label = (code: string | null) => { const value = codeLabel(code); return t(value.text, value.params); };
  function refresh() { overview.reload(); suspensions.reload(); allowlist.reload(); breakers.reload(); balance.reload(); reconciliations.reload(); }
  function open(next: Omit<Action, 'body'> & { body?: Record<string, unknown> }) {
    setNotice(null); setValues(next.initial || {});
    // One key per opened dialog: a retry after re-authentication repeats the same request.
    setAction({ ...next, body: { ...(next.body || {}), idempotency_key: crypto.randomUUID() } });
  }
  async function run(reason: string): Promise<Done> {
    if (!action) throw new Error('No action');
    return post<Done>(action.path, { ...action.body, ...(action.extra ? action.extra(values) : {}), reason: reason.trim() });
  }
  function done() {
    setAction(null); setNotice(t('已保存，并写入审计记录。')); refresh();
    requestAnimationFrame(() => status.current?.focus());
  }

  const breaker = state?.breaker;
  const reading = state?.balance;
  return <div className="col gap16" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <section className="card card-b col gap12" aria-labelledby={`${id}-title`} aria-busy={overview.loading}>
      <div className="row wrap gap12"><h2 id={`${id}-title`} className="grow">{t('付费调用风险控制')}</h2>
        <button type="button" className="btn" disabled={overview.loading} onClick={refresh}>{t('刷新')}</button></div>
      <p>{t('这里显示付费 Agent 调用的总闸、自动刹车暂停、放行名单、上游余额与对账结果。只显示金额、状态和代码，不显示任何对话内容。阈值在「模型成本」策略中设置。')}</p>
      {notice && <p className="notice ok" role="status" tabIndex={-1} ref={status}>{notice}</p>}
      {overview.err && <p className="notice err" role="alert">{t(overview.err)}</p>}
      {state && state.environment === null && <p className="notice warn">{t('计费环境尚未配置，风险控制操作已禁用。')}</p>}
      {state && state.environment !== null && !state.configured && <p className="notice">{t('尚未发布成本策略：付费调用不会开始，自动刹车、余额监控和对账也不会运行。')}</p>}
      {!manage && <p className="notice">{t('你可以查看风险控制状态，不能修改。')}</p>}
      {state && breaker && <dl className="small" style={grid}>
        <div><dt>{t('付费调用总闸')}</dt><dd style={{ margin: 0 }}>
          <span className={`badge ${breaker.state === 'open' ? 'err' : 'ok'}`}>{t(breaker.state === 'open' ? '已关闭全部付费调用' : '允许付费调用')}</span>
          {breaker.state === 'open' && breaker.created_at !== null && <span className="muted"> · {label(breaker.source)} · {label(breaker.reason_code)} · {time(breaker.created_at)}</span>}
        </dd></div>
        <div><dt>{t('放行名单模式')}</dt><dd style={{ margin: 0 }}>{t(state.allowlist_required ? '只允许名单中的付款方' : '所有付款方')}
          <span className="muted"> · {t(state.allowlist_source === 'policy' ? '来自成本策略' : '默认值（正式环境开启）')}</span></dd></div>
        <div><dt>{t('生效中的自动刹车暂停')}</dt><dd style={{ margin: 0 }}>{new Intl.NumberFormat(locale).format(state.active_suspensions)}</dd></div>
        <div><dt>{t('最近一次上游余额读数')}</dt><dd style={{ margin: 0 }}>{!reading ? t('尚无读数') : reading.status === 'ok'
          ? <>{amount(reading.total_units, reading.scale, reading.unit)}{reading.is_available === 0 && <> · {t('上游账户不可用')}</>}<span className="muted"> · {time(reading.observed_at)}</span></>
          : <>{label(reading.code)}<span className="muted"> · {time(reading.observed_at)}</span></>}</dd></div>
      </dl>}
      {writable && breaker && <div className="row wrap gap8">
        {breaker.state === 'closed'
          ? <button type="button" className="btn danger" onClick={() => open({ title: t('关闭全部付费调用？'), confirm: t('关闭付费调用'), danger: true,
              path: `${API}/breaker/open`, impact: <p>{t('所有新的付费 Agent 调用都会被拒绝，不会扣费；已经发送的调用照常结算。重新开启需要管理员核对并填写理由。')}</p> })}>{t('关闭全部付费调用')}</button>
          : <button type="button" className="btn primary" onClick={() => open({ title: t('重新开启付费调用？'), confirm: t('重新开启'),
              path: `${API}/breaker/close`, impact: <p>{t('请先确认上游余额和关闭原因已处理。若余额仍低于停止阈值，下一次读数会再次自动关闭。')}</p> })}>{t('重新开启付费调用')}</button>}
      </div>}
    </section>

    <section className="card card-b col gap12" aria-labelledby={`${id}-suspensions`} aria-busy={suspensions.loading}>
      <div className="row wrap gap12"><h3 id={`${id}-suspensions`} className="grow">{t('自动刹车暂停')}</h3>
        <label className="row gap8 small" htmlFor={`${id}-scope`}><span>{t('显示')}</span>
          <select id={`${id}-scope`} className="input" value={scope} onChange={event => setScope(event.target.value as 'active' | 'all')}>
            <option value="active">{t('生效中')}</option><option value="all">{t('全部（含已解除）')}</option></select></label></div>
      <p className="small muted">{t('滚动窗口内的提供方成本超过阈值时，该付款方或模型路线的付费调用被暂停，直到管理员解除。')}</p>
      {suspensions.err ? <p className="notice err" role="alert">{t(suspensions.err)}</p> : suspensions.loading ? <p role="status">{t('正在读取…')}</p>
        : !suspensions.data?.items.length ? <p className="muted">{t('没有暂停记录。')}</p>
        : <ul className="col gap8" style={listStyle}>{suspensions.data.items.map(item => <li key={item.id} className="notice col gap4">
          <div className="row wrap gap8"><b>{t(SCOPE_LABELS[item.scope_kind])}</b><span>{item.payer_label || item.scope_id}</span>
            <span className={`badge ${item.lifted_at === null ? 'err' : ''}`}>{t(item.lifted_at === null ? '暂停中' : '已解除')}</span></div>
          <span className="small">{label(item.reason_code)}{item.window_s !== null && <> · {t('窗口 {window} 秒', { window: item.window_s })}</>}
            {item.threshold_units !== null && <> · {t('阈值 {threshold}，本次将达到 {observed}', {
              threshold: unitAmount(item.threshold_units, item.unit), observed: unitAmount(item.observed_units, item.unit) })}</>}</span>
          <span className="small muted">{t('暂停于 {time}', { time: time(item.created_at) })}{item.lifted_at !== null && <> · {t('解除于 {time}：{reason}', { time: time(item.lifted_at), reason: item.lift_reason || '' })}</>}</span>
          {writable && item.lifted_at === null && <div><button type="button" className="btn" onClick={() => open({ title: t('解除这项暂停？'), confirm: t('解除暂停'),
            path: `${API}/suspensions/${encodeURIComponent(item.id)}/lift`,
            impact: <p>{t('解除后，{scope}「{name}」可以再次发起付费调用。若成本速率仍超过阈值，会再次被暂停。', { scope: t(SCOPE_LABELS[item.scope_kind]), name: item.payer_label || item.scope_id })}</p> })}>{t('解除暂停')}</button></div>}
        </li>)}</ul>}
    </section>

    <AllowlistSection id={id} data={allowlist} writable={writable} required={!!state?.allowlist_required} time={time} open={open} />

    <section className="card card-b col gap12" aria-labelledby={`${id}-balance`} aria-busy={balance.loading}>
      <h3 id={`${id}-balance`}>{t('上游余额')}</h3>
      <p className="small muted">{t('余额由后台定时读取（间隔在成本策略中设置）。低于预警阈值时提醒管理员，低于停止阈值或上游不可用时自动关闭全部付费调用。')}</p>
      {balance.err ? <p className="notice err" role="alert">{t(balance.err)}</p> : balance.loading ? <p role="status">{t('正在读取…')}</p> : <>
        {!balance.data?.readings.length ? <p className="muted">{t('尚无读数。')}</p> : <ul className="col gap4 small" style={listStyle}>
          {balance.data.readings.map(r => <li key={r.id}>{time(r.observed_at)} · {r.status === 'ok' ? amount(r.total_units, r.scale, r.unit) : label(r.code)}
            {r.is_available === 0 && <> · {t('上游账户不可用')}</>}</li>)}</ul>}
        <h4>{t('已登记的上游充值')}</h4>
        <p className="small muted">{t('向模型提供方账户充值后在这里登记，对账才不会把充值误认为负的支出。登记不改变任何用户余额。')}</p>
        {!balance.data?.topups.length ? <p className="muted small">{t('没有登记。')}</p> : <ul className="col gap4 small" style={listStyle}>
          {balance.data.topups.map(r => <li key={r.id}>{time(r.topped_up_at)} · {unitAmount(r.amount_units, r.unit)} · {r.reason}</li>)}</ul>}
        {writable && unit && state?.configured && <TopupButton unit={unit} open={open} />}
      </>}
    </section>

    <section className="card card-b col gap12" aria-labelledby={`${id}-reconcile`} aria-busy={reconciliations.loading}>
      <h3 id={`${id}-reconcile`}>{t('对账与账本审计')}</h3>
      <p className="small muted">{t('每日对账比较上游余额在一个 UTC 日内的变化（加上已登记的充值）与账本记录的成本；账本审计比较缓存余额与不可变分录，只报告差异，不自动修复。')}</p>
      {writable && <ReconcileButtons open={open} />}
      {reconciliations.err ? <p className="notice err" role="alert">{t(reconciliations.err)}</p> : reconciliations.loading ? <p role="status">{t('正在读取…')}</p>
        : !reconciliations.data?.items.length ? <p className="muted">{t('尚无对账结果。')}</p>
        : <ul className="col gap8" style={listStyle}>{reconciliations.data.items.map(r => {
          const cost = roundedUnits(r.ledger_cost_n, r.ledger_cost_d), drift = roundedUnits(r.drift_n, r.drift_d);
          return <li key={r.id} className="notice col gap4">
            <div className="row wrap gap8"><b>{t(r.kind === 'daily' ? '每日对账 {day}' : '账本余额审计', { day: utcDay(r.window_start) })}</b>
              <span className={`badge ${r.status === 'ok' ? 'ok' : 'err'}`}>{t(STATUS_LABELS[r.status])}</span>
              <span className="small muted">{label(r.code)} · {t(r.trigger === 'admin' ? '管理员发起' : '定时')} · {time(r.created_at)}</span></div>
            {r.kind === 'daily' && r.upstream_units !== null && <dl className="small" style={grid}>
              <div><dt>{t('上游支出（含已登记充值）')}</dt><dd style={{ margin: 0 }}>{amount(r.upstream_units, r.scale, r.unit)}</dd></div>
              <div><dt>{t('账本成本')}</dt><dd style={{ margin: 0 }}>{cost ? `${cost.exact ? '' : '≈ '}${amount(cost.units, r.scale, r.unit)}` : '—'}</dd></div>
              <div><dt>{t('差额')}</dt><dd style={{ margin: 0 }}>{drift ? `${drift.exact ? '' : '≈ '}${amount(drift.units, r.scale, r.unit)}` : '—'}</dd></div>
              <div><dt>{t('容差')}</dt><dd style={{ margin: 0 }}>{amount(r.tolerance_units, r.scale, r.unit)}</dd></div>
              <div><dt>{t('用量待确认的预留成本')}</dt><dd style={{ margin: 0 }}>{amount(r.pending_units, r.scale, r.unit)}</dd></div>
            </dl>}
            {r.kind === 'ledger_audit' && r.differences !== null && <p className="small">{t('差异 {count} 处', { count: r.differences })}</p>}
          </li>; })}</ul>}
    </section>

    <section className="card card-b col gap8" aria-labelledby={`${id}-history`} aria-busy={breakers.loading}>
      <h3 id={`${id}-history`}>{t('总闸记录')}</h3>
      {breakers.err ? <p className="notice err" role="alert">{t(breakers.err)}</p> : !breakers.data?.items.length ? <p className="muted">{t('总闸从未关闭过。')}</p>
        : <ul className="col gap4 small" style={listStyle}>{breakers.data.items.map(e => <li key={e.id || e.seq}>
          {e.created_at !== null && time(e.created_at)} · {t(e.state === 'open' ? '关闭付费调用' : '重新开启')} · {label(e.source)} · {label(e.reason_code)}{e.reason ? ` · ${e.reason}` : ''}</li>)}</ul>}
    </section>

    {action && <ActionDialog<Done> title={action.title} confirmLabel={action.confirm} danger={action.danger} reason="required"
      reauthScope="admin" impact={action.impact} ready={action.ready ? action.ready(values) : true} run={run} onDone={done}
      onClose={() => setAction(null)}>{action.fields?.(values, (key, value) => setValues(current => ({ ...current, [key]: value })))}</ActionDialog>}
  </div>;
}

type Open = (next: Omit<Action, 'body'> & { body?: Record<string, unknown> }) => void;

function AllowlistSection({ id, data, writable, required, time, open }: { id: string; writable: boolean; required: boolean;
  data: { data: Page<AllowEntry> | null; err: string | null; loading: boolean }; time: (s: number) => string; open: Open }) {
  const { t } = useI18n();
  const [kind, setKind] = useState<'user' | 'organization'>('user');
  const [value, setValue] = useState('');
  const field = useId();
  const valid = kind === 'user' ? /^[^\s@]+@[^\s@]+$/.test(value.trim()) && value.trim().length <= 320 : /^[A-Za-z0-9_-]{1,64}$/.test(value.trim());
  return <section className="card card-b col gap12" aria-labelledby={`${id}-allowlist`} aria-busy={data.loading}>
    <h3 id={`${id}-allowlist`}>{t('放行名单')}</h3>
    <p className="small muted">{t(required ? '放行名单模式已开启：只有名单中的付款方可以发起付费 Agent 调用。' : '放行名单模式未开启：名单保留，但不限制付款方。')}</p>
    {writable && <form className="row wrap gap8" style={{ alignItems: 'flex-end' }} onSubmit={event => { event.preventDefault(); if (!valid) return;
      const target = value.trim();
      open({ title: t('加入放行名单？'), confirm: t('加入'), path: '/api/admin/billing/risk/allowlist',
        body: kind === 'user' ? { payer_kind: 'user', email: target } : { payer_kind: 'organization', organization_id: target },
        impact: <p>{t('「{name}」将可以在分阶段开放期间发起付费 Agent 调用；其他额度与预算仍然适用。', { name: target })}</p> }); }}>
      <label className="field" htmlFor={`${field}-kind`}><span>{t('付款方类型')}</span>
        <select id={`${field}-kind`} className="input" value={kind} onChange={event => { setKind(event.target.value as 'user' | 'organization'); setValue(''); }}>
          <option value="user">{t('个人（按登录邮箱）')}</option><option value="organization">{t('组织（按组织编号）')}</option></select></label>
      <label className="field grow" htmlFor={`${field}-value`} style={{ minWidth: 0 }}><span>{t(kind === 'user' ? '登录邮箱' : '组织编号')}</span>
        <input id={`${field}-value`} className="input" type={kind === 'user' ? 'email' : 'text'} autoComplete="off" value={value}
          maxLength={kind === 'user' ? 320 : 64} onChange={event => setValue(event.target.value)} style={{ width: '100%', minWidth: 0 }} /></label>
      <button type="submit" className="btn" disabled={!valid}>{t('加入放行名单')}</button>
    </form>}
    {data.err ? <p className="notice err" role="alert">{t(data.err)}</p> : data.loading ? <p role="status">{t('正在读取…')}</p>
      : !data.data?.items.length ? <p className="muted">{t('名单为空。')}</p>
      : <ul className="col gap8" style={listStyle}>{data.data.items.map(item => <li key={item.id} className="row wrap gap8 notice">
        <span className="grow" style={{ minWidth: 0 }}><b>{t(item.payer_kind === 'user' ? '个人付款方' : '组织付款方')}</b> {item.payer_label || item.payer_id}
          <span className="small muted"> · {t('加入于 {time}：{reason}', { time: time(item.added_at), reason: item.add_reason })}</span></span>
        {writable && <button type="button" className="btn" onClick={() => open({ title: t('移出放行名单？'), confirm: t('移出'), danger: true,
          path: `/api/admin/billing/risk/allowlist/${encodeURIComponent(item.id)}/remove`,
          impact: <p>{t('放行名单模式开启时，「{name}」的新付费调用会被拒绝；已发送的调用照常结算。', { name: item.payer_label || item.payer_id })}</p> })}>{t('移出')}</button>}
      </li>)}</ul>}
  </section>;
}

const topupReady = (values: Values, scale: number) => topupUnits(values.amount || '', scale) !== null &&
  Number.isFinite(Date.parse(values.when || '')) && Date.parse(values.when || '') <= Date.now();

function TopupButton({ unit, open }: { unit: { code: string; scale: number }; open: Open }) {
  const { t } = useI18n();
  const id = useId();
  return <div><button type="button" className="btn" onClick={() => open({ title: t('登记上游充值'), confirm: t('登记'), path: '/api/admin/billing/risk/topups',
    impact: <p>{t('登记一笔已经完成的模型提供方账户充值。它只用于对账，不改变任何用户余额。')}</p>,
    initial: { amount: '', when: '' }, ready: values => topupReady(values, unit.scale),
    extra: values => ({ amount_units: topupUnits(values.amount, unit.scale), topped_up_at: Math.floor(Date.parse(values.when) / 1000) }),
    fields: (values, set) => <div className="col gap8">
      <label className="field" htmlFor={`${id}-amount`}><span>{t('充值金额（{code}）', { code: unit.code })}</span>
        <input id={`${id}-amount`} className="input" inputMode="decimal" maxLength={33} value={values.amount || ''} aria-describedby={`${id}-amount-help`}
          onChange={event => set('amount', event.target.value)} />
        <span id={`${id}-amount-help`} className="small muted">{t('使用小数点，不含分隔符；最多 {scale} 位小数。', { scale: unit.scale })}</span></label>
      <label className="field" htmlFor={`${id}-when`}><span>{t('充值时间（本地时间）')}</span>
        <input id={`${id}-when`} className="input" type="datetime-local" value={values.when || ''} onChange={event => set('when', event.target.value)} /></label>
    </div> })}>{t('登记上游充值')}</button></div>;
}

function ReconcileButtons({ open }: { open: Open }) {
  const { t } = useI18n();
  const id = useId();
  return <div className="row wrap gap8">
    <button type="button" className="btn" onClick={() => open({ title: t('重新对账某一天'), confirm: t('开始对账'), path: '/api/admin/billing/risk/reconciliations/run',
      body: { kind: 'daily' }, impact: <p>{t('用已记录的余额读数和已登记的充值重新计算一个已结束的 UTC 日；结果追加保存，不覆盖原结果。')}</p>,
      initial: { day: lastEndedUtcDay(Date.now()) }, ready: values => validDay(values.day || '', Date.now()), extra: values => ({ day: values.day }),
      fields: (values, set) => <label className="field" htmlFor={`${id}-day`}><span>{t('UTC 日期')}</span>
        <input id={`${id}-day`} className="input" type="date" value={values.day || ''} max={lastEndedUtcDay(Date.now())}
          onChange={event => set('day', event.target.value)} /></label> })}>{t('重新对账某一天')}</button>
    <button type="button" className="btn" onClick={() => open({ title: t('立即审计账本余额'), confirm: t('开始审计'), path: '/api/admin/billing/risk/reconciliations/run',
      body: { kind: 'ledger_audit' }, impact: <p>{t('比较缓存余额与不可变分录，只报告差异，不修改任何余额。')}</p> })}>{t('立即审计账本余额')}</button>
  </div>;
}

