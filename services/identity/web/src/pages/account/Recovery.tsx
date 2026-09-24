import { useEffect, useState } from 'react';
import { post } from '@synwevo/auth-client';
import { LanguageMenu, useI18n } from '@synwevo/ui';
import { ErrorNotice, errorText, timeText } from './common';
import { useAccountToken } from './linkToken';
export default function AccountRecovery() {
  const { t, locale } = useI18n();
  const token = useAccountToken();
  const [due, setDue] = useState<number | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (done) return;
    post<{ deletion_due_at: number }>('/api/auth/deletion/recovery/preview', { token })
      .then(r => setDue(r.deletion_due_at)).catch(e => setError(errorText(e, t)));
  }, [token, t, done]);
  return <main className="authpage"><section className="authcard col gap12"><LanguageMenu /><h1>{t('取消账户注销')}</h1>
    <ErrorNotice error={error} />
    {done ? <p role="status">{t('注销已取消。请正常登录，之前的会话仍然失效。')}</p> : due ? <>
      <p>{t('你可以在 {date} 前取消注销。此操作不会自动登录。', { date: timeText(due, locale, 'UTC') + ' UTC' })}</p>
      <button className="btn primary" disabled={busy} onClick={() => {
        setBusy(true); setError(null); post('/api/auth/deletion/recovery', { token }).then(() => setDone(true))
          .catch(e => setError(errorText(e, t))).finally(() => setBusy(false));
      }}>{t('取消账户注销')}</button></> : !error && <p role="status">{t('加载中…')}</p>}
    <a href="/login">{t('返回登录')}</a>
  </section></main>;
}
