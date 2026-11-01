import { useEffect, useId, useState } from 'react';
import { get, put } from '@synwevo/auth-client';
import { useI18n, useToast } from '@synwevo/ui';
import { amountToUnits, unitsToAmount } from '../../governance/model-costs';
import { ErrorNotice, errorText } from './common';
import { WALLET_API, ledgerText, moneyText } from './walletModel';
import './wallet.messages';

interface Period { start: number; end: number; spent: string; held: string; remaining: string }
interface Budget {
  unit: { code: string; scale: number }; revision: number; configured: boolean; enabled: boolean;
  daily_budget: string; weekly_budget: string; monthly_budget: string;
  periods: Record<'daily' | 'weekly' | 'monthly', Period> | null; usage_state: string;
}
interface Reminder { unit: string; scale: number; configured: boolean; revision: number; enabled: boolean; threshold: string; available: string; below: boolean }
const PERIODS = [['daily', '每日上限', '今日'], ['weekly', '每周上限', '本周'], ['monthly', '每月上限', '本月']] as const;

/** A whole-unit amount the person types, as the ledger's integer units; '' when it is not exact. */
function toUnits(value: string, scale: number): string | null {
  return amountToUnits(value.trim(), scale);
}

/** One unit's limits and this period's spending against them. The spending sentence states the ledger
 *  exactly, like the limit in the box above it, so settled + held + remaining add up to that limit. */
export function LimitForm({ budget, onSaved }: { budget: Budget; onSaved: (next: Budget) => void }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const { code, scale } = budget.unit;
  const initial = () => ({ enabled: budget.enabled, daily: unitsToAmount(budget.daily_budget, scale),
    weekly: unitsToAmount(budget.weekly_budget, scale), monthly: unitsToAmount(budget.monthly_budget, scale) });
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => setForm(initial()), [budget.revision]);   // eslint-disable-line react-hooks/exhaustive-deps
  const units = { daily: toUnits(form.daily, scale), weekly: toUnits(form.weekly, scale), monthly: toUnits(form.monthly, scale) };
  const valid = Object.values(units).every(value => value !== null);
  async function save() {
    if (!valid) return;
    setBusy(true); setError(null);
    try {
      const next = await put<Budget>(`${WALLET_API}/limits`, { unit_code: code, expected_revision: budget.revision, enabled: form.enabled,
        daily_budget: units.daily, weekly_budget: units.weekly, monthly_budget: units.monthly });
      onSaved(next); toast(t('支出上限已保存'), 'ok');
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <form className="col gap12 wallet-setting" onSubmit={e => { e.preventDefault(); void save(); }}>
    <label className="row gap8 wallet-check"><input type="checkbox" checked={form.enabled} onChange={e => setForm({ ...form, enabled: e.target.checked })} />
      <span>{t('开启支出上限')}</span></label>
    <div className="wallet-limit-grid">
      {PERIODS.map(([key, label, now]) => {
        const period = budget.periods?.[key];
        const limit = BigInt(budget[`${key}_budget`]);
        const used = period ? BigInt(period.spent) + BigInt(period.held) : 0n;
        return <div key={key} className="col gap4">
          <label className="field"><span>{t(label)}</span>
            <input className="input" inputMode="decimal" autoComplete="off" value={form[key]} aria-invalid={units[key] === null}
              onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>
          {period && budget.configured && <>
            <meter min={0} max={Number(limit > 0n ? limit : 1n)} value={Number(used > limit ? limit : used)} aria-label={t('{period}已用', { period: t(now) })} />
            <span className="tiny muted">{t('{period}已结算 {spent}，预留 {held}，剩余 {remaining}', { period: t(now),
              spent: ledgerText(period.spent, scale, code, locale), held: ledgerText(period.held, scale, code, locale),
              remaining: ledgerText(period.remaining, scale, code, locale) })}</span>
          </>}
        </div>;
      })}
    </div>
    {!valid && <p className="tiny err-text" role="alert">{t('请输入不小于 0 的金额，小数位数不超过 {scale} 位。', { scale })}</p>}
    <ErrorNotice error={error ? errorText(error, t) : null} />
    <div><button className="btn primary" disabled={busy || !valid}>{t(busy ? '正在保存…' : '保存支出上限')}</button></div>
  </form>;
}

/** The person's own calendar spending limits (checked by the server before every paid call) and
 *  the low-balance reminder. Both are the signed-in person's; an organization's are not set here. */
export function LimitsSection() {
  const { t } = useI18n();
  const [data, setData] = useState<{ timezone: string; budgets: Budget[] } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const title = useId();
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    get<{ timezone: string; budgets: Budget[] }>(`${WALLET_API}/limits`, abort.signal).then(setData).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [tick]);
  return <section className="card card-b col gap12 account-section" aria-labelledby={title}>
    <h2 id={title} className="secttl">{t('支出上限')}</h2>
    <p className="small muted">{t('付费的 Agent 调用只在你开启支出上限后才会发起；每次发送前，服务端都会把本期已结算和仍在预留的金额加上这次的上限一起核对，超出就不发送、不扣费。周期按 UTC 计算。')}</p>
    <ErrorNotice error={error ? errorText(error, t) : null} retry={() => setTick(n => n + 1)} />
    {!data && !error && <p role="status">{t('正在读取支出上限…')}</p>}
    {data && !data.budgets.length && <p className="muted">{t('模型价格尚未发布，暂时不能设置支出上限。')}</p>}
    {data?.budgets.map(budget => <LimitForm key={budget.unit.code} budget={budget}
      onSaved={next => setData({ ...data, budgets: data.budgets.map(b => (b.unit.code === next.unit.code ? next : b)) })} />)}
  </section>;
}

/** The low-balance line and, when the server says the balance is below it, that balance rounded down —
 *  so it always reads below the line in the box, which is shown as typed. */
export function ReminderForm({ reminder, onSaved }: { reminder: Reminder; onSaved: (next: Reminder) => void }) {
  const { t, locale } = useI18n();
  const toast = useToast();
  const [enabled, setEnabled] = useState(reminder.enabled);
  const [threshold, setThreshold] = useState(unitsToAmount(reminder.threshold, reminder.scale));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { setEnabled(reminder.enabled); setThreshold(unitsToAmount(reminder.threshold, reminder.scale)); }, [reminder.revision]);   // eslint-disable-line react-hooks/exhaustive-deps
  const units = toUnits(threshold, reminder.scale);
  async function save() {
    if (units === null) return;
    setBusy(true); setError(null);
    try {
      const next = await put<Reminder>(`${WALLET_API}/reminder`, { unit: reminder.unit, expected_revision: reminder.revision, enabled, threshold: units });
      onSaved(next); toast(t('余额提醒已保存'), 'ok');
    } catch (e) { setError(e); } finally { setBusy(false); }
  }
  return <form className="col gap12 wallet-setting" onSubmit={e => { e.preventDefault(); void save(); }}>
    <label className="row gap8 wallet-check"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
      <span>{t('可用余额低于提醒线时通知我')}</span></label>
    <label className="field"><span>{t('提醒线')}</span>
      <input className="input" inputMode="decimal" autoComplete="off" value={threshold} aria-invalid={units === null} onChange={e => setThreshold(e.target.value)} /></label>
    {reminder.below && <p className="notice warn" role="status">{t('当前可用余额 {available}，已低于提醒线。', { available: moneyText(reminder.available, reminder.scale, reminder.unit, locale, { rounding: 'down' }) })}</p>}
    {units === null && <p className="tiny err-text" role="alert">{t('请输入不小于 0 的金额，小数位数不超过 {scale} 位。', { scale: reminder.scale })}</p>}
    <ErrorNotice error={error ? errorText(error, t) : null} />
    <div><button className="btn primary" disabled={busy || units === null}>{t(busy ? '正在保存…' : '保存余额提醒')}</button></div>
  </form>;
}

export function ReminderSection() {
  const { t } = useI18n();
  const [rows, setRows] = useState<Reminder[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [tick, setTick] = useState(0);
  const title = useId();
  useEffect(() => {
    const abort = new AbortController();
    setError(null);
    get<{ reminders: Reminder[] }>(`${WALLET_API}/reminder`, abort.signal).then(r => setRows(r.reminders)).catch(e => { if (!abort.signal.aborted) setError(e); });
    return () => abort.abort();
  }, [tick]);
  return <section className="card card-b col gap12 account-section" aria-labelledby={title}>
    <h2 id={title} className="secttl">{t('低余额提醒')}</h2>
    <p className="small muted">{t('余额每次跌破提醒线时，站内通知（以及按你的通知设置发送的邮件）提醒一次；回到提醒线以上后再次跌破会再提醒。提醒不会阻止调用。')}</p>
    <ErrorNotice error={error ? errorText(error, t) : null} retry={() => setTick(n => n + 1)} />
    {!rows && !error && <p role="status">{t('正在读取余额提醒…')}</p>}
    {rows?.map(row => <ReminderForm key={row.unit} reminder={row} onSaved={next => setRows(rows.map(r => (r.unit === next.unit ? next : r)))} />)}
  </section>;
}
