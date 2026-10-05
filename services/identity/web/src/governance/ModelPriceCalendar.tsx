import { useEffect, useId, useRef, useState, type CSSProperties, type FormEvent } from 'react';
import { ApiError, post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ActionDialog } from './ActionDialog';
import { useAdminData } from './data';
import { errText } from './labels';
import { useUnsavedChanges } from '../lib/hooks';
import { MAX_WINDOWS, WEEKDAYS, bandAt, canonicalSchedule, expandDates, mergeHolidays, minutes, scheduleProblems, shiftedTime, utcOffset,
  validHolidayName, validTime, type PeakWindow, type ScheduleProblem, type ScheduleSpec } from './model-price-bands';
import './model-price-bands.messages';

/** Peak windows (UTC) and the public-holiday calendar that decide the band of a time-banded
 *  price at dispatch. Published versions are immutable; a change re-bands future calls only. */
type Environment = 'test' | 'live' | null;
export type ScheduleVersion = { environment: Environment; schedule_id: 'deepseek'; revision: number; spec: ScheduleSpec | null;
  actor_id: string | null; created_at: number | null; reason: string | null; operation_id: string | null };
type State = ScheduleVersion & { defaults: ScheduleSpec };
type History = { items: ScheduleVersion[]; next_before_revision: number | null };
type Changes = { holidays_added: string[]; holidays_removed: string[]; holidays_renamed: string[]; windows_changed: boolean };
type Plan = { before: ScheduleVersion; after: ScheduleSpec; changes: Changes; preview_token: string; expires_at: number;
  risk: 'high'; environment: Environment };
type Publication = { operation_id: string; status: 'succeeded'; state: ScheduleVersion; changes: Changes };
type Attempt = { environment: Environment; body: { expected_revision: number; spec: ScheduleSpec; preview_token: string;
  idempotency_key: string; reason: string } };
const API = '/api/admin/billing/prices/schedule';
export const WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const PROBLEMS: Record<ScheduleProblem, string> = {
  windows: '须有 1–14 个高峰时段。',
  window_days: '每个高峰时段至少选择一天。',
  window_time: '时间须为 24 小时制 HH:MM（UTC），结束可为 24:00。',
  window_order: '高峰时段的结束时间须晚于开始时间，且在同一 UTC 日内。',
  window_duplicate: '有重复的高峰时段。',
  holiday_date: '节假日日期无效。',
  holiday_name: '节假日名称须为 1–40 个字符，首尾不能有空格。',
  holiday_order: '节假日须按日期排列且不能重复。',
  holidays_many: '节假日最多 1000 天。',
  source: '依据引用须以英文字母或数字开头，共 1–200 位，仅限英文字母、数字及 . _ : / -。',
};
const grid: CSSProperties = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 180px), 1fr))', gap: 12 };
const fieldset: CSSProperties = { margin: 0, padding: 0, border: 0, minWidth: 0 };
const uncertainResponse = (err: unknown) => !(err instanceof ApiError) || err.upstream || err.status >= 500 || err.status === 408 || err.status === 0;
const localError = (message: string) => new ApiError(400, 'model_price_calendar_form', message);
const same = (a: ScheduleSpec | null, b: ScheduleSpec | null) => (a === null || b === null) ? a === b : canonicalSchedule(a) === canonicalSchedule(b);
const copy = (spec: ScheduleSpec): ScheduleSpec => ({ ...spec, peak_windows: spec.peak_windows.map(w => ({ ...w, weekdays: [...w.weekdays] })),
  holidays: spec.holidays.map(h => ({ ...h })) });

function validSpec(spec: unknown): spec is ScheduleSpec {
  const s = spec as ScheduleSpec;
  return !!s && Array.isArray(s.peak_windows) && Array.isArray(s.holidays) && Number.isSafeInteger(s.holiday_utc_offset_minutes) &&
    typeof s.source_ref === 'string' && s.peak_windows.every(w => Array.isArray(w?.weekdays) && typeof w.start === 'string' && typeof w.end === 'string') &&
    s.holidays.every(h => typeof h?.date === 'string' && typeof h.name === 'string') && !scheduleProblems(s).length;
}
export function validVersion(value: unknown): value is ScheduleVersion {
  const v = value as ScheduleVersion;
  return !!v && typeof v === 'object' && [null, 'test', 'live'].includes(v.environment) && v.schedule_id === 'deepseek' &&
    Number.isSafeInteger(v.revision) && v.revision >= 0 && (v.spec === null ? v.revision === 0 : v.revision > 0 && validSpec(v.spec)) &&
    [v.actor_id, v.reason, v.operation_id].every(x => x === null || typeof x === 'string') &&
    (v.created_at === null || Number.isSafeInteger(v.created_at));
}
const validState = (value: unknown): value is State => validVersion(value) && validSpec((value as State).defaults);

/** Weekdays as words: a run of three or more as "first to last", otherwise listed. */
function dayText(days: number[], t: (key: string, args?: Record<string, string | number>) => string) {
  const run = days.length > 2 && days.every((d, i) => i === 0 || d === days[i - 1] + 1);
  return run ? t('{from}至{to}', { from: t(WEEKDAY_NAMES[days[0] - 1]), to: t(WEEKDAY_NAMES[days[days.length - 1] - 1]) })
    : days.map(d => t(WEEKDAY_NAMES[d - 1])).join(t('、'));
}

export function ScheduleDetails({ spec }: { spec: ScheduleSpec | null }) {
  const { t } = useI18n();
  if (!spec) return <p className="muted">{t('尚未发布高峰时段与节假日日历。分时价格不能发布，分时调用不会派发。')}</p>;
  const offset = utcOffset(spec.holiday_utc_offset_minutes);
  return <div className="col gap8 small">
    <p><b>{t('高峰时段（UTC）')}</b></p>
    <ul>{spec.peak_windows.map((w, i) => <li key={i}>{t('{days} UTC {start}–{end}（{offset} {localStart}–{localEnd}）', { days: dayText(w.weekdays, t),
      start: w.start, end: w.end, offset, localStart: shiftedTime(w.start, spec.holiday_utc_offset_minutes), localEnd: shiftedTime(w.end, spec.holiday_utc_offset_minutes) })}</li>)}</ul>
    <p>{t('其余时间、周末与以下节假日均为低谷时段。节假日按 {offset} 的日期判断。', { offset })}</p>
    {spec.holidays.length ? <details><summary>{t('节假日 {count} 天', { count: spec.holidays.length })}</summary>
      <ul>{spec.holidays.map(h => <li key={h.date}>{t('{date}：{name}', h)}</li>)}</ul></details>
      : <p>{t('没有登记节假日。')}</p>}
    <p className="muted">{t('依据：{ref}', { ref: spec.source_ref })}</p>
  </div>;
}

export function ModelPriceCalendar({ manage, onDenied }: { manage: boolean; onDenied: () => void }) {
  const { t, locale } = useI18n();
  const id = useId();
  const current = useAdminData<State>(API, onDenied);
  const [cursors, setCursors] = useState<(number | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];
  const history = useAdminData<History>(`${API}/history?limit=20${cursor === null ? '' : `&before_revision=${cursor}`}`, onDenied);
  const state = validState(current.data) ? current.data : null;
  const environment = state?.environment;
  const [base, setBase] = useState<ScheduleVersion | null>(null);
  const [draft, setDraft] = useState<ScheduleSpec | null>(null);
  const [holiday, setHoliday] = useState({ from: '', to: '', name: '' });
  const [holidayProblem, setHolidayProblem] = useState<string | null>(null);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);
  const [busy, setBusy] = useState<'preview' | 'publish' | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [result, setResult] = useState<Publication | null>(null);
  const [now, setNow] = useState(Date.now());
  const flight = useRef(false);
  const alive = useRef(true);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!base && state && !current.loading && !current.err) { setBase(state); setDraft(copy(state.spec || state.defaults)); }
  }, [base, state, current.loading, current.err]);
  useEffect(() => {
    if (!plan) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [plan]);
  const canWrite = manage && !!state && (environment === 'test' || environment === 'live') && !current.loading && !current.err;
  const changedBase = !!base && !!state && (base.environment !== state.environment || base.revision !== state.revision);
  const problems = draft ? scheduleProblems(draft) : [];
  // The editor starts from the published version, or from the provider's published windows.
  const reference = base?.spec ?? state?.defaults ?? null;
  const dirty = !!draft && !!reference && !same(draft, reference);
  const frozen = !!busy || dialogOpen || uncertain;
  const expired = !!plan && now >= plan.expires_at * 1000;
  const historyValid = !!history.data && Array.isArray(history.data.items) && history.data.items.every(validVersion);
  useUnsavedChanges(dirty || uncertain || !!busy);

  const environmentName = (env: Environment | undefined) => t(env === 'live' ? '正式环境' : env === 'test' ? '测试环境' : '未配置');
  const time = (seconds: number) => new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'long' }).format(new Date(seconds * 1000));
  const bandName = (band: 'peak' | 'off_peak') => t(band === 'peak' ? '高峰' : '低谷');
  function fail(err: unknown) {
    setError(err);
    if (err instanceof ApiError && !err.upstream) {
      if (err.code === 'band_schedule_revision_conflict') setConflict(true);
      if (['platform_permission_denied', 'platform_admin_required'].includes(err.code)) onDenied();
    }
  }
  function refresh() { current.reload(); history.reload(); }
  function change(next: ScheduleSpec) {
    if (frozen || !canWrite) return;
    setDraft(next); setPlan(null); setAttempt(null); setResult(null);
  }
  function editWindow(index: number, patch: Partial<PeakWindow>) {
    if (!draft) return;
    change({ ...draft, peak_windows: draft.peak_windows.map((w, i) => i === index ? { ...w, ...patch } : w) });
  }
  function toggleDay(index: number, day: number) {
    if (!draft) return;
    const days = draft.peak_windows[index].weekdays;
    editWindow(index, { weekdays: (days.includes(day) ? days.filter(d => d !== day) : [...days, day]).sort((a, b) => a - b) });
  }
  function addHolidays() {
    if (!draft || frozen || !canWrite) return;
    const dates = expandDates(holiday.from, holiday.to);
    if (!dates) { setHolidayProblem('请选择有效日期；一次最多添加 31 天，结束日期不能早于开始日期。'); return; }
    if (!validHolidayName(holiday.name)) { setHolidayProblem('节假日名称须为 1–40 个字符，首尾不能有空格。'); return; }
    setHolidayProblem(null);
    change({ ...draft, holidays: mergeHolidays(draft.holidays, dates, holiday.name) });
    setHoliday({ from: '', to: '', name: holiday.name });
  }
  function adoptLatest() {
    if (!state || frozen) return;
    setBase(state); setConflict(false); setPlan(null); setAttempt(null); setError(null);
    requestAnimationFrame(() => heading.current?.focus());
  }
  async function preview(event: FormEvent) {
    event.preventDefault(); setTried(true);
    if (!canWrite || !base || !draft || frozen || flight.current || conflict || changedBase || problems.length) return;
    flight.current = true; setBusy('preview'); setError(null); setConfirmed(false); setPlan(null); setAttempt(null);
    const spec = copy(draft);
    try {
      const out = await post<Plan>(`${API}/preview`, { expected_revision: base.revision, spec });
      if (!alive.current) return;
      if (!validVersion(out?.before) || out.before.revision !== base.revision || !validSpec(out.after) || !same(out.after, spec) ||
        out.environment !== environment || out.risk !== 'high' || typeof out.preview_token !== 'string' || !out.preview_token ||
        typeof out.expires_at !== 'number' || out.expires_at * 1000 <= Date.now() || !out.changes) {
        throw localError('日历预览与当前输入不一致或已过期，请刷新后重新预览。');
      }
      setPlan(out); setNow(Date.now()); setDialogOpen(true);
    } catch (err) { if (alive.current) fail(err); } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  async function publish(reason: string): Promise<Publication> {
    if (flight.current || !plan || !canWrite || !confirmed) throw localError('请先核对有效的日历预览和发布影响。');
    let original = attempt;
    if (!uncertain && (changedBase || conflict || Date.now() >= plan.expires_at * 1000)) throw localError('日历预览已失效，请关闭并重新预览。');
    if (!original) {
      if (Array.from(reason.trim()).length < 8 || Array.from(reason.trim()).length > 500) throw localError('发布理由须为 8–500 个字符。');
      original = { environment, body: { expected_revision: plan.before.revision, spec: plan.after, preview_token: plan.preview_token,
        idempotency_key: crypto.randomUUID(), reason: reason.trim() } };
      setAttempt(original);
    }
    if (original.environment !== environment) throw localError('环境已变化，不能在另一环境重试原请求。');
    flight.current = true; setBusy('publish'); setError(null);
    try {
      const out = await post<Publication>(`${API}/publish`, original.body);
      if (out?.status !== 'succeeded' || !out.operation_id || !validVersion(out.state) || out.state.revision !== original.body.expected_revision + 1 ||
        !same(out.state.spec, original.body.spec)) throw new Error('Invalid calendar publication response');
      return out;
    } catch (err) {
      if (alive.current) {
        // Once an outcome is unknown, only replaying the identical request can settle it.
        if (uncertain || uncertainResponse(err)) setUncertain(true);
        else if (!(err instanceof ApiError && err.code === 'reauth_required')) { setAttempt(null); setPlan(null); setDialogOpen(false); }
        fail(err);
      }
      throw err;
    } finally { flight.current = false; if (alive.current) setBusy(null); }
  }
  function completed(out: Publication) {
    if (!alive.current) return;
    setUncertain(false); setAttempt(null); setResult(out); setBase(out.state); setDraft(copy(out.state.spec!)); setConflict(false);
    setPlan(null); setDialogOpen(false); setConfirmed(false); setTried(false); setCursors([null]); refresh();
    requestAnimationFrame(() => heading.current?.focus());
  }
  function versionDetails(item: ScheduleVersion) {
    return <div className="col gap8">
      <ScheduleDetails spec={item.spec} />
      {item.created_at !== null && <p className="small muted">{t('发布于 {time} · 操作者 {actor}', { time: time(item.created_at), actor: item.actor_id || '—' })}</p>}
      {item.reason !== null && <p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('发布理由：{reason}', { reason: item.reason })}</p>}
      {item.operation_id && <p className="small muted">{t('操作编号：{id}', { id: item.operation_id })}</p>}
    </div>;
  }
  const nowBand = state?.spec ? bandAt(state.spec, Math.floor(Date.now() / 1000)) : null;

  return <section className="card card-b col gap12" aria-labelledby={`${id}-title`} aria-busy={current.loading} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
    <h3 id={`${id}-title`}>{t('高峰时段与节假日日历')}</h3>
    <p className="small">{t('分时价格按调用派发时刻（UTC）落入的时段计价，并把时段固定在该次调用上；之后修改日历不会改变已派发调用的费用。节假日全天为低谷时段。')}</p>
    <p className="notice info">{t('日历环境：{environment}', { environment: environmentName(environment) })}</p>
    {current.err && <p className="notice err" role="alert">{t(current.err)}</p>}
    {current.loading ? <p role="status">{t('正在读取时段日历…')}</p> : !current.err && !state ? <p className="notice err" role="alert">{t('无法确认服务器的时段日历，请刷新后重试。')}</p> : state && <>
      <div className="col gap8">
        <p><b>{state.revision ? t('当前版本 {revision}', { revision: state.revision }) : t('尚未发布')}</b></p>
        {nowBand && <p className="small" role="note">{t('按当前日历，现在是{band}时段。', { band: bandName(nowBand) })}</p>}
        {versionDetails(state)}
      </div>
    </>}
    {state?.environment === null && <p className="notice warn">{t('计费环境尚未配置，日历编辑与发布已禁用。')}</p>}
    {!manage && <p className="notice">{t('你可以查看日历和历史，不能修改或发布。')}</p>}
    {error != null && <div className="notice err" role="alert"><p>{t('日历请求未完成。你的输入已保留，请核对下方状态。')}</p><p>{errText(error)}</p></div>}
    {result && <p className="notice ok" role="status">{t('日历版本 {revision} 已发布，操作编号 {id}。仅影响之后派发的调用；绑定旧日历的报价与持续同意需要重新确认。', { revision: result.state.revision, id: result.operation_id })}</p>}
    {uncertain && attempt && <div className="notice warn col gap8" role="alert">
      <p>{t('发布结果尚不确定，输入已锁定。请保留此页面，仅重试原请求；刷新记录不能确认原请求失败。')}</p>
      <p className="small">{t('原请求编号：{id}', { id: attempt.body.idempotency_key })}</p>
      <div><button type="button" className="btn" disabled={!canWrite || !!busy || dialogOpen} onClick={() => { setConfirmed(false); setDialogOpen(true); }}>{t('核对并重试原请求')}</button></div>
    </div>}

    {manage && base && draft && <form className="col gap16" onSubmit={preview} noValidate aria-labelledby={`${id}-editor`}>
      <h4 id={`${id}-editor`} ref={heading} tabIndex={-1}>{t('编辑下一版本')}</h4>
      {!base.spec && <p className="small muted">{t('编辑器从 DeepSeek 公布的高峰时段开始；节假日须由管理员逐一登记，发布后才生效。')}</p>}
      {(changedBase || conflict) && <div className="notice warn col gap8" role="alert">
        <p>{t('服务器上的日历已变化。输入已保留；请比较最新版本后再继续。')}</p>
        <div className="row wrap gap8"><button type="button" className="btn" disabled={current.loading || !!busy} onClick={refresh}>{t('读取最新日历')}</button>
          <button type="button" className="btn" disabled={frozen || !state} onClick={adoptLatest}>{t('保留输入，以最新版本为基础')}</button></div>
      </div>}
      <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
        <legend><b>{t('高峰时段（UTC）')}</b></legend>
        <p className="small muted" id={`${id}-window-help`}>{t('时间按 UTC 输入，24 小时制；开始时刻计入高峰，结束时刻不计入。括号内为 {offset} 的对应时间。', { offset: utcOffset(draft.holiday_utc_offset_minutes) })}</p>
        {draft.peak_windows.map((w, index) => <fieldset key={index} className="notice col gap8" style={{ minWidth: 0 }}>
          <legend className="small">{t('高峰时段 {n}', { n: index + 1 })}</legend>
          <div className="row wrap gap8" role="group" aria-label={t('高峰时段 {n} 的星期', { n: index + 1 })}>
            {WEEKDAYS.map(day => <label key={day} className="check"><input type="checkbox" checked={w.weekdays.includes(day)} onChange={() => toggleDay(index, day)} />{t(WEEKDAY_NAMES[day - 1])}</label>)}
          </div>
          <div style={grid}>
            <label className="field" style={{ minWidth: 0 }}><span>{t('开始（UTC）')}</span>
              <input className="input" type="text" inputMode="numeric" maxLength={5} placeholder="01:00" value={w.start} aria-describedby={`${id}-window-help`}
                onChange={event => editWindow(index, { start: event.target.value })} style={{ width: '100%', minWidth: 0 }} /></label>
            <label className="field" style={{ minWidth: 0 }}><span>{t('结束（UTC）')}</span>
              <input className="input" type="text" inputMode="numeric" maxLength={5} placeholder="04:00" value={w.end} aria-describedby={`${id}-window-help`}
                onChange={event => editWindow(index, { end: event.target.value })} style={{ width: '100%', minWidth: 0 }} /></label>
          </div>
          {validTime(w.start) && validTime(w.end, true) && minutes(w.start) < minutes(w.end) &&
            <p className="small muted">{t('{offset} {start}–{end}', { offset: utcOffset(draft.holiday_utc_offset_minutes),
              start: shiftedTime(w.start, draft.holiday_utc_offset_minutes), end: shiftedTime(w.end, draft.holiday_utc_offset_minutes) })}</p>}
          <div><button type="button" className="btn" disabled={draft.peak_windows.length <= 1}
            aria-label={t('删除高峰时段 {n}', { n: index + 1 })} onClick={() => change({ ...draft, peak_windows: draft.peak_windows.filter((_, i) => i !== index) })}>{t('删除此时段')}</button></div>
        </fieldset>)}
        <div><button type="button" className="btn" disabled={draft.peak_windows.length >= MAX_WINDOWS}
          onClick={() => change({ ...draft, peak_windows: [...draft.peak_windows, { weekdays: [1, 2, 3, 4, 5], start: '', end: '' }] })}>{t('添加高峰时段')}</button></div>
      </fieldset>

      <fieldset className="col gap12" style={fieldset} disabled={!canWrite || frozen}>
        <legend><b>{t('节假日（全天低谷）')}</b></legend>
        <p className="small muted">{t('按 {offset} 的日期登记中国法定节假日；调休上班日不在此登记，按星期判断。', { offset: utcOffset(draft.holiday_utc_offset_minutes) })}</p>
        <div style={grid}>
          <label className="field" style={{ minWidth: 0 }}><span>{t('开始日期')}</span>
            <input className="input" type="date" value={holiday.from} onChange={event => setHoliday({ ...holiday, from: event.target.value })} style={{ width: '100%', minWidth: 0 }} /></label>
          <label className="field" style={{ minWidth: 0 }}><span>{t('结束日期（可选，最多 31 天）')}</span>
            <input className="input" type="date" value={holiday.to} onChange={event => setHoliday({ ...holiday, to: event.target.value })} style={{ width: '100%', minWidth: 0 }} /></label>
          <label className="field" style={{ minWidth: 0 }}><span>{t('节假日名称')}</span>
            <input className="input" type="text" maxLength={40} value={holiday.name} onChange={event => setHoliday({ ...holiday, name: event.target.value })} style={{ width: '100%', minWidth: 0 }} /></label>
        </div>
        <div><button type="button" className="btn" onClick={addHolidays}>{t('添加节假日')}</button></div>
        {holidayProblem && <p className="notice err small" role="alert">{t(holidayProblem)}</p>}
        {draft.holidays.length ? <div className="tbl-wrap"><table className="tbl">
          <caption className="small" style={{ textAlign: 'left' }}>{t('已登记 {count} 天', { count: draft.holidays.length })}</caption>
          <thead><tr><th scope="col">{t('日期')}</th><th scope="col">{t('名称')}</th><th scope="col"><span className="sr-only">{t('操作')}</span></th></tr></thead>
          <tbody>{draft.holidays.map(h => <tr key={h.date}><td>{h.date}</td><td>{h.name}</td>
            <td><button type="button" className="btn" aria-label={t('移除节假日 {date}', { date: h.date })}
              onClick={() => change({ ...draft, holidays: draft.holidays.filter(x => x.date !== h.date) })}>{t('移除')}</button></td></tr>)}</tbody>
        </table></div> : <p className="small muted">{t('没有登记节假日。')}</p>}
      </fieldset>

      <fieldset className="col gap8" style={fieldset} disabled={!canWrite || frozen}>
        <label className="field" style={{ minWidth: 0, maxWidth: 480 }}><span>{t('依据引用（例如官方定价页与核对日期）')}</span>
          <input className="input" type="text" maxLength={200} value={draft.source_ref} onChange={event => change({ ...draft, source_ref: event.target.value })}
            style={{ width: '100%', minWidth: 0 }} /></label>
      </fieldset>
      {(tried || problems.length > 0) && problems.length > 0 && <ul className="notice err small" role="alert">{problems.map(p => <li key={p}>{t(PROBLEMS[p])}</li>)}</ul>}
      <div className="row wrap gap8"><button type="submit" className="btn primary"
        disabled={!canWrite || frozen || changedBase || conflict || problems.length > 0 || (!!base.spec && !dirty)}>{t('预览日历变更')}</button>
        {dirty && reference && <button type="button" className="btn" disabled={frozen} onClick={() => { setDraft(copy(reference)); setPlan(null); }}>{t('放弃未发布的修改')}</button>}</div>
      {dirty && <p className="small muted">{t('输入尚未发布，当前生效日历保持不变。')}</p>}
    </form>}

    <details className="col gap8" aria-busy={history.loading}>
      <summary>{t('日历发布历史')}</summary>
      <div className="col gap8" style={{ paddingTop: 8 }}>
        {history.err && <p className="notice err" role="alert">{t(history.err)}</p>}
        {history.loading ? <p role="status">{t('正在读取发布历史…')}</p> : !history.err && (historyValid ? <>
          {!history.data!.items.length && <p className="muted">{t('此页没有已发布的日历版本。')}</p>}
          {history.data!.items.map(item => <details key={item.revision} className="notice">
            <summary>{t('日历版本 {revision} · {time}', { revision: item.revision, time: item.created_at === null ? '—' : time(item.created_at) })}</summary>
            <div style={{ paddingTop: 8 }}>{versionDetails(item)}</div>
          </details>)}
        </> : <p className="notice err" role="alert">{t('无法确认服务器的时段日历，请刷新后重试。')}</p>)}
        <nav className="row wrap gap8" aria-label={t('日历历史分页')}>
          <button type="button" className="btn" disabled={history.loading || cursors.length === 1} onClick={() => setCursors(v => v.slice(0, -1))}>{t('较新一页')}</button>
          <button type="button" className="btn" disabled={history.loading || !historyValid || history.data?.next_before_revision == null}
            onClick={() => { const next = history.data?.next_before_revision; if (next != null) setCursors(v => [...v, next]); }}>{t('较旧一页')}</button>
        </nav>
      </div>
    </details>

    {dialogOpen && plan && <ActionDialog<Publication> title={t('确认发布时段日历')} confirmLabel={t(uncertain ? '重试原请求' : '发布日历')}
      reason={attempt ? 'none' : 'required'} reauthScope="admin" size="wide"
      ready={canWrite && confirmed && !busy && (uncertain || (!expired && !changedBase && !conflict))}
      impact={<div className="col gap12">
        <p><b>{t('高风险操作 · {environment}', { environment: environmentName(plan.environment) })}</b></p>
        <ul className="small">
          <li>{t(plan.changes.windows_changed ? '高峰时段或节假日时区有变化。' : '高峰时段不变。')}</li>
          <li>{t('新增节假日：{dates}', { dates: plan.changes.holidays_added.join(t('、')) || '—' })}</li>
          <li>{t('移除节假日：{dates}', { dates: plan.changes.holidays_removed.join(t('、')) || '—' })}</li>
          <li>{t('改名的节假日：{dates}', { dates: plan.changes.holidays_renamed.join(t('、')) || '—' })}</li>
        </ul>
        <ScheduleDetails spec={plan.after} />
        <p>{t('发布后，新派发的调用按新日历定档；已派发的调用保持原时段。使用分时价格的路线版本随之变化，用户尚未使用的报价与持续同意须重新确认。')}</p>
        <p className="small">{t('服务器确认预览有效至 {time}。', { time: time(plan.expires_at) })}</p>
        {attempt && <p className="small" style={{ whiteSpace: 'pre-wrap' }}>{t('原发布理由：{reason}', { reason: attempt.body.reason })}</p>}
        {uncertain && <p className="notice warn">{t('重试将发送完全相同的原请求、理由和请求编号，即使原预览已经到期。不会创建另一笔发布。')}</p>}
        {!uncertain && (expired || changedBase || conflict) && <p className="notice warn" role="alert">{t('日历预览已失效，请关闭并重新预览。')}</p>}
      </div>}
      run={publish} onDone={completed} onClose={() => { if (flight.current) return; setDialogOpen(false); setConfirmed(false); if (!uncertain) { setPlan(null); setAttempt(null); } }}>
      <label className="row gap8" style={{ alignItems: 'flex-start' }}><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />
        <span>{t('我已核对高峰时段、节假日日期与发布影响。')}</span></label>
    </ActionDialog>}
  </section>;
}
