import { useCallback, useEffect, useState } from 'react';
import { get } from '@synwevo/auth-client';
import { useI18n } from '@synwevo/ui';
import { ErrorNotice, errorText, timeText } from './common';
import './second-factor-reset.messages';
interface Event { seq: number; at: number; action: string; result: string; ip: string | null }
interface Page { items: Event[]; next_before: number | null }
const labels: Record<string, string> = {
  'auth.login': '口令登录', 'auth.login.passkey': '安全密钥登录', 'auth.login.mfa': '两步验证登录',
  'auth.reauth': '身份确认', 'auth.profile.update': '更新个人资料', 'auth.avatar.update': '更新头像',
  'auth.avatar.remove': '移除头像', 'auth.email.change_requested': '申请更改邮箱', 'auth.email.changed': '更改邮箱',
  'auth.data.export': '导出个人数据', 'auth.deletion.requested': '申请注销账户', 'auth.deletion.cancelled': '取消账户注销',
  'auth.session.revoke': '结束登录会话', 'auth.session.revoke_others': '退出其他所有设备',
  'auth.passkey.register': '登记安全密钥', 'auth.passkey.rename': '重命名安全密钥', 'auth.passkey.remove': '删除安全密钥',
  'auth.reset.complete': '重置口令', 'auth.email.verified': '验证邮箱',
  'auth.second_factor_reset.cancelled': '取消第二因素重置',
};
export default function ActivitySection({ timezone }: { timezone: string }) {
  const { t, locale } = useI18n();
  const [page, setPage] = useState<Page | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async (before?: number) => {
    setBusy(true); setError(null);
    try {
      const next = await get<Page>('/api/auth/security-activity' + (before ? `?before=${before}` : ''));
      setPage(old => ({ ...next, items: before ? [...(old?.items || []), ...next.items] : next.items }));
    } catch (e) { setError(errorText(e, t)); } finally { setBusy(false); }
  }, [t]);
  useEffect(() => { void load(); }, [load]);
  return <section id="activity" className="card card-b col gap12 account-section">
    <div className="row gap12"><h2 className="secttl grow">{t('安全活动')}</h2><button className="btn ghost sm" disabled={busy} onClick={() => void load()}>{t('刷新')}</button></div>
    <p className="tiny muted">{t('仅显示与你的登录和账户安全有关的活动。时间采用 {timezone}。', { timezone })}</p>
    <ErrorNotice error={error} retry={() => void load()} />
    {!page && busy && <p role="status">{t('加载中…')}</p>}
    {page && <div className="account-table"><table className="tbl"><thead><tr><th>{t('时间')}</th><th>{t('活动')}</th><th>{t('结果')}</th><th>{t('来源')}</th></tr></thead>
      <tbody>{page.items.map(event => <tr key={event.seq}>
        <td className="small nowrap">{timeText(event.at, locale, timezone)}</td><td>{labels[event.action] ? t(labels[event.action]) : <>{t('账户安全事件')} <code className="tiny">{event.action}</code></>}</td>
        <td><span className={`badge ${event.result === 'ok' ? 'ok' : 'warn'}`}>{t(event.result === 'ok' ? '成功' : event.result === 'denied' ? '已拒绝' : '失败')}</span></td>
        <td className="mono small">{event.ip || '—'}</td></tr>)}</tbody></table></div>}
    {page?.items.length === 0 && <p className="muted">{t('暂无安全活动')}</p>}
    {page?.next_before && <div><button className="btn" disabled={busy} onClick={() => void load(page.next_before!)}>{t('加载更早的活动')}</button></div>}
  </section>;
}
