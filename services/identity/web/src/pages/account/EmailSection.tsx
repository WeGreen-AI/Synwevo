import { useEffect, useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ErrorNotice, errorText, PasswordProof, type Profile } from './common';
import { useAccountToken } from './linkToken';
export default function EmailSection({ profile }: { profile: Profile }) {
  const { t } = useI18n();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [queued, setQueued] = useState(false);
  const [done, setDone] = useState(false);
  const token = useAccountToken('email-change');
  useEffect(() => { if (token) { setQueued(false); setDone(false); setError(null); } }, [token]);
  return <section id="email" className="card card-b col gap12 account-section">
    <h2 className="secttl">{t('邮箱与口令')}</h2><p className="muted">{profile.email}</p>
    <ErrorNotice error={error} />
    {done ? <div className="notice ok" role="status">{t('邮箱已更改，请使用新邮箱重新登录。')} <a href="/login">{t('返回登录')}</a></div> : <form className="col gap12" onSubmit={e => {
      e.preventDefault(); setBusy(true); setError(null);
      post(token ? '/api/auth/email-change/confirm' : '/api/auth/email-change', token ? { token, password } : { email, password })
        .then(() => { setPassword(''); token ? setDone(true) : setQueued(true); })
        .catch(e => setError(errorText(e, t))).finally(() => setBusy(false));
    }}>
      <p className="tiny muted">{t('验证新邮箱后才会更改登录地址。原邮箱会收到安全通知，所有设备将退出登录。')}</p>
      {token ? <div className="notice info">{t('请确认身份以完成邮箱更改。')}</div> : <label className="field"><span>{t('新邮箱')}</span>
        <input type="email" className="input" autoComplete="email" required maxLength={254} value={email} onChange={e => setEmail(e.target.value)} /></label>}
      <PasswordProof value={password} onChange={setPassword} />
      {queued && <p className="notice info" role="status">{t('验证邮件已进入队列，请在一小时内打开新邮箱中的链接。')}</p>}
      <div><button className="btn" disabled={busy || !password}>{busy ? t('处理中…') : token ? t('确认更改邮箱') : t('验证新邮箱')}</button></div>
    </form>}
    <div className="account-divider"><h3>{t('更新口令')}</h3><p className="tiny muted">{t('通过已验证邮箱重置口令。完成后所有设备需重新登录，两步验证仍然有效。')}</p>
      <a href="/forgot-password" className="btn">{t('重置口令')}</a></div>
  </section>;
}
