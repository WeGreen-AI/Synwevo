/** Notification preferences: per category, in-app on/off and e-mail immediate / digest / off; quiet
 *  hours in the reader's time zone; how much of a notification the e-mail shows. Account security
 *  is shown but locked on — the mail that says an attacker changed a password must not be something
 *  the attacker can turn off from the same session. */
import { useEffect, useState } from 'react';
import { ApiError, get, put } from '@synwevo/auth-client';
import { useI18n, useToast } from '@synwevo/ui';
import { ErrorNotice, errorText } from './common';
import PushSection from './PushSection';
import './notifications.messages';

type EmailMode = 'immediate' | 'digest' | 'off';
interface Prefs {
  categories: Record<string, { in_app: boolean; email: EmailMode; push: boolean }>;
  categories_order: string[];
  quiet_start: string | null; quiet_end: string | null; timezone: string | null;
  preview: 'full' | 'minimal';
}
type PrefsPatch = { categories?: Record<string, Partial<{ in_app: boolean; email: EmailMode; push: boolean }>>; quiet_start?: string | null;
  quiet_end?: string | null; timezone?: string | null; preview?: 'full' | 'minimal' };
const CATEGORY_LABEL: Record<string, string> = {
  security: '账户安全', organization: '组织与工作空间', members: '成员导入', approval: '审批',
  conversation: '消息', docs: '文档处理', automation: '自动化与接口', platform: '平台运行',
};
const EMAIL_LABEL: Record<EmailMode, string> = { immediate: '立即', digest: '汇总（约 30 分钟一封）', off: '不发邮件' };
const ZONES = ['Asia/Shanghai', 'Asia/Taipei', 'Asia/Hong_Kong', 'Asia/Tokyo', 'Asia/Singapore', 'Europe/London', 'Europe/Berlin', 'America/New_York', 'America/Los_Angeles', 'UTC'];

export default function NotificationsSection({ timezone }: { timezone?: string }) {
  const { t } = useI18n();
  const toast = useToast();
  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [quiet, setQuiet] = useState(false);

  const load = () => get<Prefs>('/api/auth/notifications/preferences')
    .then(p => { setPrefs(p); setQuiet(!!p.quiet_start); setError(null); })
    .catch(e => setError(errorText(e, t)));
  useEffect(() => { void load(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  async function save(patch: PrefsPatch) {
    if (!prefs) return;
    setBusy(true);
    try {
      const out = await put<Prefs>('/api/auth/notifications/preferences', patch);
      setPrefs(out); setQuiet(!!out.quiet_start);
      toast(t('通知偏好已保存'), 'ok');
    } catch (e) {
      toast(e instanceof ApiError ? e.message : String(e), 'err');
    } finally { setBusy(false); }
  }

  const zoneOptions = Array.from(new Set([prefs?.timezone, timezone, ...ZONES].filter(Boolean) as string[]));
  return <section id="notifications" className="card card-b col gap12 account-section">
    <h2 className="secttl">{t('通知')}</h2>
    <p className="tiny muted">{t('站内通知始终记录在通知中心；这里决定每类通知是否出现在通知中心、是否发邮件、是否推送到已开启的设备。账户安全通知始终发送。')}</p>
    <ErrorNotice error={error} retry={() => void load()} />
    {prefs && <>
      <div className="account-table"><table className="tbl" aria-label={t('通知偏好')}>
        <thead><tr><th>{t('类别')}</th><th>{t('通知中心')}</th><th>{t('邮件')}</th><th>{t('浏览器推送')}</th></tr></thead>
        <tbody>{prefs.categories_order.map(cat => {
          const c = prefs.categories[cat]; const locked = cat === 'security';
          return <tr key={cat}>
            <td>{t(CATEGORY_LABEL[cat] || cat)}{locked && <span className="badge info" style={{ marginLeft: 6 }}>{t('始终开启')}</span>}</td>
            <td><label className="check"><input type="checkbox" checked={c.in_app} disabled={locked || busy} aria-label={t('{category}：在通知中心显示', { category: t(CATEGORY_LABEL[cat] || cat) })}
              onChange={e => void save({ categories: { [cat]: { in_app: e.target.checked } } })} /><span>{t(c.in_app ? '显示' : '不显示')}</span></label></td>
            <td><select className="input sm" value={c.email} disabled={locked || busy} aria-label={t('{category}：邮件方式', { category: t(CATEGORY_LABEL[cat] || cat) })}
              onChange={e => void save({ categories: { [cat]: { email: e.target.value as EmailMode } } })}>
              {(Object.keys(EMAIL_LABEL) as EmailMode[]).map(m => <option key={m} value={m}>{t(EMAIL_LABEL[m])}</option>)}
            </select></td>
            <td><label className="check"><input type="checkbox" checked={c.push} disabled={locked || busy} aria-label={t('{category}：浏览器推送', { category: t(CATEGORY_LABEL[cat] || cat) })}
              onChange={e => void save({ categories: { [cat]: { push: e.target.checked } } })} /><span>{t(c.push ? '推送' : '不推送')}</span></label></td>
          </tr>;
        })}</tbody>
      </table></div>
      <PushSection />
      <div className="col gap8">
        <label className="check"><input type="checkbox" checked={quiet} disabled={busy} onChange={e => {
          if (!e.target.checked) { setQuiet(false); void save({ quiet_start: null, quiet_end: null, timezone: prefs.timezone }); }
          else { setQuiet(true); }
        }} /><span>{t('免打扰时段：这段时间内不发普通通知的邮件，结束后再发；账户安全邮件不受影响')}</span></label>
        {quiet && <form className="row wrap gap8" onSubmit={e => {
          e.preventDefault(); const f = new FormData(e.currentTarget);
          void save({ quiet_start: String(f.get('start')), quiet_end: String(f.get('end')), timezone: String(f.get('tz')) });
        }}>
          <label className="field sm"><span>{t('开始')}</span><input className="input sm" type="time" name="start" defaultValue={prefs.quiet_start || '22:00'} required /></label>
          <label className="field sm"><span>{t('结束')}</span><input className="input sm" type="time" name="end" defaultValue={prefs.quiet_end || '08:00'} required /></label>
          <label className="field sm"><span>{t('时区')}</span><select className="input sm" name="tz" defaultValue={prefs.timezone || timezone || 'Asia/Shanghai'}>{zoneOptions.map(z => <option key={z} value={z}>{z}</option>)}</select></label>
          <button className="btn sm" disabled={busy}>{t('保存免打扰')}</button>
        </form>}
        <label className="field sm"><span>{t('邮件中显示的内容')}</span>
          <select className="input sm" value={prefs.preview} disabled={busy} onChange={e => void save({ preview: e.target.value as 'full' | 'minimal' })}>
            <option value="full">{t('标题与摘要')}</option><option value="minimal">{t('仅标题')}</option>
          </select></label>
      </div>
    </>}
  </section>;
}
