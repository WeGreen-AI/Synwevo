import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { del, get, post, useSession } from '@synwevo/auth-client';
import { useDialog, useI18n, useToast } from '@synwevo/ui';
import { Topbar } from '../components/Topbar';
import { LeaveDialog } from './members/dialogs';
import { canExport } from '../lib/labels';
import MfaSection from '../components/MfaSection';
import PasskeySection from '../components/PasskeySection';
import ProfileSection from './account/ProfileSection';
import EmailSection from './account/EmailSection';
import PrivacySection from './account/PrivacySection';
import ActivitySection from './account/ActivitySection';
import { ErrorNotice, errorText, timeText, type Profile } from './account/common';
import './account/account.css';
export { default as AccountRecovery } from './account/Recovery';
interface SessionRow { id: string; current: boolean; created_at: number; last_seen_at: number; expires_at: number; amr: string; client: string; ip: string | null }
const roles: Record<string, string> = { owner: '所有者', admin: '管理员', editor: '编辑', viewer: '只读' };
const methods: Record<string, string> = { password: '口令', 'password+totp': '口令与动态验证码', 'password+recovery': '口令与恢复码', passkey_uv: '安全密钥（已验证身份）', passkey: '安全密钥' };
export default function Account() {
  const { me, signOut } = useSession();
  const { t, locale, setLocale } = useI18n();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<{ id: string; name: string } | null>(null);
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const { confirm } = useDialog();
  const loadProfile = useCallback(() => {
    get<Profile>('/api/auth/profile').then(p => { setProfile(p); setProfileError(null); setLocale(p.locale); })
      .catch(e => setProfileError(errorText(e, t)));
  // Loading stored preferences must not refetch whenever the language menu changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const loadSessions = useCallback(() => {
    get<{ items: SessionRow[] }>('/api/auth/sessions').then(r => { setRows(r.items); setError(null); })
      .catch(e => setError(errorText(e, t)));
  }, [t]);
  useEffect(loadProfile, [loadProfile]);
  useEffect(() => { if (profile?.status === 'active') loadSessions(); }, [profile?.status, loadSessions]);
  async function revoke(row?: SessionRow) {
    const count = rows?.filter(r => !r.current).length || 0;
    const title = row?.current ? '退出当前设备' : row ? '结束登录会话' : '退出其他所有设备';
    if (!await confirm(t(title), t(row ? '该设备需要重新登录才能继续使用。' : '将结束其他 {count} 个登录。', { count }), t('确认'), true)) return;
    setBusy(true); setError(null);
    try {
      if (row?.current) await signOut();
      else {
        if (row) await del(`/api/auth/sessions/${row.id}`);
        else await post('/api/auth/sessions/revoke-others');
        toast(t('设备已退出登录'), 'ok'); loadSessions();
      }
    } catch (e) { setError(errorText(e, t)); } finally { setBusy(false); }
  }
  return <div className="app"><Topbar title={t('账户与安全')} /><div style={{ overflow: 'auto', flex: 1 }}>
    <main className="account-center"><header className="account-hero"><div><p className="account-eyebrow">Synwevo Account</p>
      <h1>{t('你的账户，由你掌控')}</h1><p className="muted">{t('管理个人资料、登录方式与隐私。')}</p></div></header>
      <ErrorNotice error={profileError} retry={loadProfile} />
      {!profile && !profileError && <p role="status">{t('加载中…')}</p>}
      {profile?.status === 'pending' && <section className="card card-b col gap12"><h2>{t('请先验证邮箱')}</h2>
        <p>{t('验证邮箱后即可管理个人资料和安全设置。')}</p><p className="muted">{profile.email}</p>
        <ErrorNotice error={error} /><div><button className="btn primary" disabled={busy} onClick={() => {
          setBusy(true); setError(null); post('/api/auth/verify-email/send').then(() => toast(t('验证邮件已进入队列'), 'ok'))
            .catch(e => setError(errorText(e, t))).finally(() => setBusy(false));
        }}>{t('重新发送验证邮件')}</button></div></section>}
      {profile?.status === 'pending_delete' && <section className="card card-b col gap12" role="status"><h2>{t('注销申请已受理')}</h2>
        <p>{t('账户已停用。请在 {date} 前使用邮箱中的恢复链接取消注销。', { date: timeText(profile.deletion_due_at!, locale, profile.timezone) })}</p>
        <div><a href="/login" className="btn">{t('返回登录')}</a></div></section>}
      {profile?.status === 'active' && me && <div className="account-layout"><nav className="account-nav" aria-label={t('账户设置导航')}>
        {[['profile', '个人资料'], ['email', '邮箱与口令'], ['security', '登录与安全'], ['devices', '登录的设备'], ['workspaces', '工作空间'], ['activity', '安全活动'], ['privacy', '数据与账户']].map(([id, label]) => <a key={id} href={`#${id}`}>{t(label)}</a>)}
      </nav><div className="account-content">
        <ProfileSection profile={profile} onChange={setProfile} /><EmailSection profile={profile} />
        <div id="security" className="col gap12 account-section"><PasskeySection timezone={profile.timezone} /><MfaSection /></div>
        <section id="devices" className="card card-b col gap12 account-section"><div className="row gap12 wrap"><h2 className="secttl grow">{t('登录的设备')}</h2>
          {!!rows?.some(r => !r.current) && <button className="btn danger sm" disabled={busy} onClick={() => void revoke()}>{t('退出其他所有设备')}</button>}</div>
          <ErrorNotice error={error} retry={loadSessions} />{rows === null && !error && <p role="status">{t('加载中…')}</p>}
          <div className="account-table"><table className="tbl" aria-label={t('登录会话')}><thead><tr><th>{t('设备')}</th><th>{t('登录方式')}</th><th>{t('最近活动')}</th><th>{t('到期')}</th><th>{t('来源')}</th><th>{t('操作')}</th></tr></thead>
            <tbody>{rows?.map(row => <tr key={row.id}><td>{row.client} {row.current && <span className="badge ok">{t('当前设备')}</span>}</td>
              <td className="small">{methods[row.amr] ? t(methods[row.amr]) : row.amr}</td><td className="small nowrap">{timeText(row.last_seen_at, locale, profile.timezone)}</td>
              <td className="small nowrap">{timeText(row.expires_at, locale, profile.timezone)}</td><td className="mono small">{row.ip || '—'}</td>
              <td><button className="btn xs" disabled={busy} onClick={() => void revoke(row)}>{t(row.current ? '退出' : '结束')}</button></td></tr>)}</tbody></table></div>
          {rows?.length === 0 && <p className="muted">{t('没有活动的登录')}</p>}
        </section>
        <section id="workspaces" className="card card-b col gap12 account-section"><h2 className="secttl">{t('工作空间')}</h2>
          <p className="tiny muted">{t('平台管理权限不会授予工作空间文档的读取权限。')}</p>
          <div className="account-table"><table className="tbl"><thead><tr><th>{t('名称')}</th><th>{t('我的角色')}</th><th>{t('可导出')}</th><th>{t('操作')}</th></tr></thead>
            <tbody>{me.workspaces.map(w => <tr key={w.id}><td>{w.name}</td><td>{t(roles[w.role] || w.role)}</td><td>{t(canExport(w.role, w.can_export) ? '是' : '否')}</td><td><div className="row gap4 wrap">
              {(w.role === 'owner' || w.role === 'admin') && <Link className="btn sm" to={`/account/workspaces/${encodeURIComponent(w.id)}/members`}>{t('成员与邀请')}</Link>}
              <button className="btn ghost sm" onClick={() => setLeaving(w)}>{t('退出空间')}</button></div></td></tr>)}</tbody></table></div>
          {!me.workspaces.length && <p className="muted">{t('你当前不属于任何工作空间。')}</p>}
        </section>
        <ActivitySection timezone={profile.timezone} /><PrivacySection profile={profile} onDeleted={due => setProfile({ ...profile, status: 'pending_delete', deletion_due_at: due })} />
      </div></div>}
    </main></div>
    {leaving && <LeaveDialog wid={leaving.id} wsName={leaving.name} soleOwner={false} onClose={() => setLeaving(null)} onDone={() => window.location.reload()} />}
  </div>;
}
