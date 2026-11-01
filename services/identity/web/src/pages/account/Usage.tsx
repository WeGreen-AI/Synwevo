import { useEffect, useId, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { get, useSession } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { Topbar } from '../../components/Topbar';
import { useLoad } from '../../lib/hooks';
import { ErrorNotice, errorText, timeText } from './common';
import UsageChart from './UsageChart';
import { PayerPicker, payerName, usePayers } from './Wallet';
import { WalletGate } from './WalletGate';
import { PRESETS, callsPath, chartBars, csvPath, ledgerText, localToday, parsePayer, payerKey, periodDays, presetPeriod,
  usagePath, withQuery, type CallRow, type CallState, type CallsPage, type Payer, type Period, type Preset,
  type UsageGroup, type UsageSummary } from './walletModel';
import './wallet.messages';
import './account.css';
import './wallet.css';

export const PRESET_LABELS: Record<Preset | 'custom', string> = {
  '7d': '最近 7 天', '30d': '最近 30 天', '90d': '最近 90 天', month: '本月', last_month: '上个月', custom: '自定义',
};
export const GROUP_LABELS: Record<UsageGroup, string> = { day: '按日', model: '按模型', conversation: '按对话', member: '按成员' };
export const STATE_LABELS: Record<CallState, string> = { settled: '已结算', pending: '用量确认中', held: '预留中', not_charged: '未扣费' };
export const BAND_LABELS: Record<string, string> = { peak: '高峰时段', off_peak: '低谷时段', flat: '统一价' };
export const FINISH_LABELS: Record<string, string> = {
  stop: '正常结束', length: '达到长度上限', content_filter: '内容被过滤', tool_calls: '调用工具',
  insufficient_system_resource: '服务资源不足', aborted: '已中断',
};

function usePeriod(params: URLSearchParams, timezone: string): { preset: Preset | 'custom'; period: Period; valid: boolean } {
  const today = localToday(timezone);
  const raw = params.get('period');
  if (raw === 'custom') {
    const period = { from: params.get('from') || '', to: params.get('to') || '' };
    return { preset: 'custom', period, valid: periodDays(period) !== null };
  }
  const preset = (PRESETS as string[]).includes(raw || '') ? raw as Preset : '30d';
  return { preset, period: presetPeriod(preset, today), valid: true };
}

function tokensText(row: { tokens: CallRow['tokens'] }, locale: string, t: (s: string, p?: Record<string, string | number>) => string) {
  if (!row.tokens) return '—';
  const n = (value: number) => new Intl.NumberFormat(locale).format(value);
  return t('缓存命中 {cached} · 未命中 {uncached} · 输出 {output}（含思考 {reasoning}）', { cached: n(row.tokens.input_cached),
    uncached: n(row.tokens.input_uncached), output: n(row.tokens.output), reasoning: n(row.tokens.reasoning) });
}

/** One row per call, newest first: what was asked of which model, in which time band and price
 *  version, the tokens the provider reported, and what the ledger settled — exactly, to the digit the
 *  unit holds, like every figure on this page, so rows add up to the totals and to the CSV. */
export function CallsTable({ items, organization, timezone }: { items: CallRow[]; organization: boolean; timezone: string }) {
  const { t, locale } = useI18n();
  // The table scrolls sideways on a phone: a focusable, named region so a keyboard can scroll it too.
  return <div className="tbl-wrap usage-calls" tabIndex={0} role="region" aria-label={t('每次调用')}><table className="tbl">
      <caption className="sr-only">{t('每次调用的模型、时段、价格版本、token 构成、费用与结束原因')}</caption>
      <thead><tr><th scope="col">{t('时间')}</th><th scope="col">{t(organization ? '成员' : '对话')}</th><th scope="col">{t('模型与时段')}</th>
        <th scope="col">{t('Token')}</th><th scope="col">{t('费用')}</th><th scope="col">{t('状态')}</th><th scope="col">{t('结束原因与耗时')}</th></tr></thead>
      <tbody>{items.map(row => <tr key={row.id}>
        <td className="nowrap small"><time dateTime={new Date(row.created_at * 1000).toISOString()}>{timeText(row.created_at, locale, timezone)}</time></td>
        <td className="small usage-who">{organization ? (row.member_name || t('已删除的成员')) : (row.conversation_title || t('已删除的对话'))}</td>
        <td className="small"><div>{row.model || '—'}</div><div className="muted">{row.band ? t(BAND_LABELS[row.band] || row.band) : t('时段未记录')}</div>
          <div className="muted tiny" title={t('价格版本')}>{row.price_version}</div></td>
        <td className="small">{tokensText(row, locale, t)}</td>
        <td className="nowrap usage-money">{row.state === 'settled' ? ledgerText(row.charged_minor, row.scale, row.unit, locale)
          : row.state === 'held' || row.state === 'pending' ? t('预留 {amount}', { amount: ledgerText(row.held_minor, row.scale, row.unit, locale) })
          : ledgerText('0', row.scale, row.unit, locale)}</td>
        <td><span className={`badge ${row.state === 'settled' ? 'ok' : row.state === 'not_charged' ? '' : 'info'}`}>{t(STATE_LABELS[row.state] || row.state)}</span></td>
        <td className="small">{row.finish_reason ? t(FINISH_LABELS[row.finish_reason] || row.finish_reason) : '—'}
          {row.duration_s !== null && <div className="muted">{t('{seconds} 秒', { seconds: row.duration_s })}</div>}</td>
      </tr>)}</tbody></table></div>;
}

function Calls({ payer, period, timezone }: { payer: Payer; period: Period; timezone: string }) {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const model = params.get('model') || '';
  const state = (params.get('state') || '') as CallState | '';
  const [items, setItems] = useState<CallRow[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const title = useId();
  const filters = { model, state };
  // One request at a time: a newer page, filter or period cancels the older request, so a slow
  // "load more" can never append rows of a previous filter to the table.
  const inflight = useRef<AbortController | null>(null);
  const load = (cursor: string | null) => {
    inflight.current?.abort();
    const abort = new AbortController();
    inflight.current = abort;
    setLoading(true); setError(null);
    return get<CallsPage>(callsPath(payer, period, filters, timezone, cursor), abort.signal).then(page => {
      setItems(old => (cursor ? [...old, ...page.items] : page.items)); setModels(page.models); setNext(page.next_cursor);
    }).catch(e => { if (!abort.signal.aborted) setError(e); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
  };
  useEffect(() => {
    void load(null);
    return () => inflight.current?.abort();
  }, [payerKey(payer), period.from, period.to, model, state, timezone]);   // eslint-disable-line react-hooks/exhaustive-deps
  const filter = (key: 'model' | 'state', value: string) => setParams(old => {
    const query = new URLSearchParams(old);
    if (value) query.set(key, value); else query.delete(key);
    return query;
  }, { replace: true });
  const organization = payer.kind === 'organization';
  return <section className="card card-b col gap12" aria-labelledby={title} aria-busy={loading}>
    <div className="row wrap gap8"><h2 id={title} className="secttl grow">{t('每次调用')}</h2>
      <a className="btn sm" href={csvPath(payer, period, filters, timezone)} download>{t('导出 CSV')}</a></div>
    <div className="row wrap gap12 usage-filters">
      <label className="field"><span>{t('模型')}</span><select className="input" value={model} onChange={e => filter('model', e.target.value)}>
        <option value="">{t('全部模型')}</option>{[...new Set(model ? [...models, model] : models)].map(name => <option key={name} value={name}>{name}</option>)}</select></label>
      <label className="field"><span>{t('状态')}</span><select className="input" value={state} onChange={e => filter('state', e.target.value)}>
        <option value="">{t('全部状态')}</option>{(Object.keys(STATE_LABELS) as CallState[]).map(key => <option key={key} value={key}>{t(STATE_LABELS[key])}</option>)}</select></label>
    </div>
    <ErrorNotice error={error ? errorText(error, t) : null} retry={() => void load(null)} />
    {!loading && !error && !items.length && <p className="muted">{t('这段时间没有符合条件的调用。')}</p>}
    {!!items.length && <CallsTable items={items} organization={organization} timezone={timezone} />}
    {loading && <p role="status" className="small muted">{t('正在读取调用明细…')}</p>}
    {next && !loading && <div><button type="button" className="btn" onClick={() => void load(next)}>{t('加载更多')}</button></div>}
    <p className="tiny muted">{t('费用来自账本的结算记录，不是界面的估算。预留中的调用结算后只按实际用量扣费；未扣费的调用没有收取任何费用。')}</p>
  </section>;
}

export default function Usage() {
  return <WalletGate><UsagePage /></WalletGate>;
}

function UsagePage() {
  const { me } = useSession();
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const payer = parsePayer(params.get('payer'));
  const payers = usePayers(me?.id);
  const timezone = me?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
  const { preset, period, valid } = usePeriod(params, timezone);
  const organization = payer.kind === 'organization';
  const groups: UsageGroup[] = organization ? ['day', 'model', 'member'] : ['day', 'model', 'conversation'];
  const group = (groups as string[]).includes(params.get('group') || '') ? params.get('group') as UsageGroup : 'day';
  const summary = useLoad<UsageSummary>(me && valid ? `${me.id}:${usagePath(payer, period, group, timezone)}` : null,
    signal => get(usagePath(payer, period, group, timezone), signal));
  const set = (changes: Record<string, string | null>) => setParams(old => {
    const query = new URLSearchParams(old);
    for (const [key, value] of Object.entries(changes)) if (value) query.set(key, value); else query.delete(key);
    return query;
  }, { replace: true });
  const data = summary.error ? null : summary.data;  // kept while a new period loads, so the page does not flash empty
  const name = payerName(payer, payers.list, t);
  const legend = useId();

  return <div className="app"><Topbar title={t('用量')} /><div className="wallet-scroll">
    <main className="account-center col gap24 wallet-page">
      <header className="row wrap gap12 wallet-head"><div className="grow"><h1>{t('用量与明细')}</h1>
        <p className="muted">{t('{name}：每天、每个模型、每段对话花了多少，以及每次调用的明细。只统计账本里的真实记录。', { name })}</p></div>
        <PayerPicker payer={payer} list={payers.list} onChange={next => set({ payer: next.kind === 'organization' ? payerKey(next) : null, group: null, model: null })} />
      </header>
      <section className="card card-b col gap12" aria-labelledby={legend}>
        <h2 id={legend} className="sr-only">{t('时间段与分组')}</h2>
        <div className="row wrap gap12 usage-controls">
          <label className="field"><span>{t('时间段')}</span>
            <select className="input" value={preset} onChange={e => e.target.value === 'custom'
              ? set({ period: 'custom', from: period.from, to: period.to }) : set({ period: e.target.value === '30d' ? null : e.target.value, from: null, to: null })}>
              {[...PRESETS, 'custom' as const].map(key => <option key={key} value={key}>{t(PRESET_LABELS[key])}</option>)}</select></label>
          {preset === 'custom' && <>
            <label className="field"><span>{t('开始日期')}</span><input className="input" type="date" value={period.from} max={period.to || undefined}
              onChange={e => set({ from: e.target.value })} aria-invalid={!valid} /></label>
            <label className="field"><span>{t('结束日期')}</span><input className="input" type="date" value={period.to} min={period.from || undefined}
              onChange={e => set({ to: e.target.value })} aria-invalid={!valid} /></label>
          </>}
          <div className="field"><span id={`${legend}-group`}>{t('分组')}</span>
            <div className="seg" role="group" aria-labelledby={`${legend}-group`}>{groups.map(key => <button key={key} type="button"
              className={group === key ? 'on' : ''} aria-pressed={group === key} onClick={() => set({ group: key === 'day' ? null : key })}>{t(GROUP_LABELS[key])}</button>)}</div></div>
        </div>
        {!valid && <p className="notice warn" role="alert">{t('请选择有效的起止日期：开始不晚于结束，最多相隔 366 天。')}</p>}
        <p className="tiny muted">{t('{from} 至 {to}，按 {timezone} 的日历日统计。', { from: period.from, to: period.to, timezone })}</p>
      </section>
      <section className="card card-b col gap16" aria-labelledby="usage-summary-title" aria-busy={summary.loading}>
        <h2 id="usage-summary-title" className="secttl">{t('汇总')}</h2>
        <ErrorNotice error={summary.error ? errorText(summary.error, t) : null} retry={summary.reload} />
        {summary.loading && !data && <p role="status">{t('正在读取用量…')}</p>}
        {data && data.environment === 'test' && <p className="notice info">{t('测试环境：以下记录不代表真实充值或正式账单。')}</p>}
        {data && !data.totals.length && <p className="muted">{t('这段时间没有付费调用。')}</p>}
        {data?.totals.map(total => {
          const bars = chartBars(data, total.unit, { other: t('其他'), untitled: t(group === 'member' ? '已删除的成员' : group === 'conversation' ? '已删除的对话' : '未记录') });
          const buckets = data.buckets.filter(bucket => bucket.unit === total.unit);
          return <div key={total.unit} className="col gap16">
            <dl className="usage-totals">
              <div><dt>{t('已结算费用')}</dt><dd>{ledgerText(total.charged_minor, total.scale, total.unit, locale)}</dd></div>
              <div><dt>{t('调用次数')}</dt><dd>{new Intl.NumberFormat(locale).format(total.calls)}</dd></div>
              <div><dt>{t('预留中')}</dt><dd>{ledgerText(total.held_minor, total.scale, total.unit, locale)}</dd></div>
              <div className="usage-token-total"><dt>{t('Token 构成')}</dt><dd className="small">{tokensText(total, locale, t)}</dd></div>
            </dl>
            <UsageChart bars={bars} unit={total.unit} scale={total.scale} locale={locale} daily={data.group === 'day'}
              empty={t('这段时间没有已结算的费用。')}
              summary={t('{group}的已结算费用：共 {count} 项，合计 {total}。具体数值见下表。', { group: t(GROUP_LABELS[data.group]),
                count: buckets.length, total: ledgerText(total.charged_minor, total.scale, total.unit, locale) })} />
            <div className="tbl-wrap"><table className="tbl usage-buckets">
              <caption className="sr-only">{t('{group}的用量明细', { group: t(GROUP_LABELS[data.group]) })}</caption>
              <thead><tr><th scope="col">{t(data.group === 'day' ? '日期' : data.group === 'model' ? '模型' : data.group === 'member' ? '成员' : '对话')}</th>
                <th scope="col">{t('调用次数')}</th><th scope="col">{t('已结算费用')}</th><th scope="col">{t('预留中')}</th><th scope="col">{t('Token')}</th></tr></thead>
              <tbody>{buckets.map(bucket => <tr key={bucket.key}>
                <th scope="row" className="usage-who">{bucket.title || bucket.key || t('未记录')}</th>
                <td>{new Intl.NumberFormat(locale).format(bucket.calls)}</td>
                <td className="usage-money">{ledgerText(bucket.charged_minor, bucket.scale, bucket.unit, locale)}</td>
                <td className="usage-money">{ledgerText(bucket.held_minor, bucket.scale, bucket.unit, locale)}</td>
                <td className="small">{tokensText(bucket, locale, t)}</td></tr>)}</tbody></table></div>
          </div>;
        })}
      </section>
      {valid && <Calls payer={payer} period={period} timezone={timezone} />}
      <div className="row wrap gap8"><Link className="btn" to={withQuery('/account/wallet', organization ? { payer: payerKey(payer) } : {})}>{t('返回钱包')}</Link></div>
    </main>
  </div></div>;
}
