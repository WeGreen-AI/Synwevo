import { useState } from 'react';
import { del, patch, put } from '@synwevo/auth-client';
import { useI18n, useToast, type Locale } from '@synwevo/ui';
import { ErrorNotice, errorText, type Profile } from './common';

const zones = ['UTC', 'Asia/Hong_Kong', 'Asia/Shanghai', 'Asia/Taipei', 'Asia/Tokyo', 'Asia/Singapore', 'Europe/London', 'Europe/Paris', 'America/New_York', 'America/Los_Angeles', 'Australia/Sydney'];
export default function ProfileSection({ profile, onChange }: { profile: Profile; onChange: (p: Profile) => void }) {
  const { t, setLocale } = useI18n();
  const toast = useToast();
  const [name, setName] = useState(profile.display_name || '');
  const [language, setLanguage] = useState<Locale>(profile.locale);
  const [timezone, setTimezone] = useState(profile.timezone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(fn: () => Promise<void>) {
    setBusy(true); setError(null);
    try { await fn(); } catch (e) { setError(errorText(e, t)); } finally { setBusy(false); }
  }
  return <section id="profile" className="card card-b col gap12 account-section">
    <div><p className="account-eyebrow">{t('个人资料')}</p><h2 className="secttl">{t('让账户更像你')}</h2></div>
    <ErrorNotice error={error} />
    <div className="row gap12 wrap">
      <div className="account-avatar" aria-hidden="true">{profile.has_avatar
        ? <img src={`/api/auth/profile/avatar?v=${profile.updated_at}`} alt="" />
        : (profile.display_name || profile.email).slice(0, 1).toUpperCase()}</div>
      <div className="col gap8"><label className="field"><span>{t('上传头像')}</span>
        <input type="file" accept="image/png" disabled={busy} onChange={e => {
          const file = e.target.files?.[0]; e.target.value = '';
          if (!file) return;
          void run(async () => {
            if (file.size > 256 * 1024) { setError(t('请上传不超过 256 KB、512 × 512 像素的普通 PNG 图片')); return; }
            const bytes = new Uint8Array(await file.arrayBuffer());
            let text = ''; bytes.forEach(b => { text += String.fromCharCode(b); });
            await put('/api/auth/profile/avatar', { data: btoa(text) });
            onChange({ ...profile, has_avatar: true, updated_at: Date.now() / 1000 });
            toast(t('头像已保存'), 'ok');
          });
        }} /></label><span className="tiny muted">{t('PNG · 最大 256 KB · 最大 512 × 512 像素')}</span>
        {profile.has_avatar && <button className="btn ghost sm" disabled={busy} onClick={() => void run(async () => {
          await del('/api/auth/profile/avatar'); onChange({ ...profile, has_avatar: false });
        })}>{t('移除头像')}</button>}</div>
    </div>
    <form className="col gap12" onSubmit={e => { e.preventDefault(); void run(async () => {
      const next = await patch<Profile>('/api/auth/profile', { display_name: name, locale: language, timezone });
      onChange(next); setLocale(next.locale); toast(t('个人资料已保存'), 'ok');
    }); }}>
      <label className="field"><span>{t('显示名称')}</span><input className="input" autoComplete="name" value={name} maxLength={80}
        required onChange={e => setName(e.target.value)} /></label>
      <div className="account-fields"><label className="field"><span>{t('语言')}</span>
        <select className="input" aria-label={t('语言')} value={language} onChange={e => setLanguage(e.target.value as Locale)}>
          <option value="zh-Hans">{t('简体中文')}</option><option value="zh-Hant">{t('繁体中文')}</option><option value="en">{t('英语')}</option>
        </select></label>
        <label className="field"><span>{t('时区')}</span><input className="input" list="account-timezones" value={timezone} required
          onChange={e => setTimezone(e.target.value)} /><datalist id="account-timezones">{zones.map(z => <option key={z} value={z} />)}</datalist></label></div>
      <p className="tiny muted">{t('语言用于账户界面，时区用于下方设备和安全活动时间。')}</p>
      <div><button className="btn primary" disabled={busy}>{busy ? t('保存中…') : t('保存更改')}</button></div>
    </form>
  </section>;
}
