import { useState } from 'react';
import { post } from '@synwevo/auth-client';
import { useDialog, useI18n, useToast } from '@synwevo/ui';
import { ErrorNotice, errorText, PasswordProof, timeText, type Profile } from './common';

export default function PrivacySection({ profile, onDeleted }: { profile: Profile; onDeleted: (due: number) => void }) {
  const { t, locale } = useI18n();
  const { confirm } = useDialog();
  const toast = useToast();
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [due, setDue] = useState<number | null>(null);
  async function perform(action: 'export' | 'deletion') {
    if (action === 'deletion' && !await confirm(t('申请注销账户'), `${t('账户将立即停用。七天内可通过邮件取消注销；此后删除个人资料，组织中的贡献将保留，你是群主的群交给下一位管理员或成员。')} ${t('注销后，Agent 对话、记忆与个人模型密钥会被删除；Agent 钱包中未用完的余额作废，不退还（充值一经完成不可退款）。计费与付款记录不再关联你的姓名与邮箱，按法定期限保留。')}`, t('确认申请注销'), true)) return;
    setBusy(true); setError(null);
    try {
      const result = await post(`/api/auth/${action}`, { password });
      setPassword('');
      if (action === 'deletion') { setDue(result.deletion_due_at); onDeleted(result.deletion_due_at); return; }
      const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }));
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'synwevo-account.json'; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toast(t('个人数据已导出'), 'ok');
    } catch (e) { setError(errorText(e, t)); } finally { setBusy(false); }
  }
  return <section id="privacy" className="card card-b col gap12 account-section">
    <h2 className="secttl">{t('数据与账户')}</h2>
    <ErrorNotice error={error} />
    {due ? <div className="notice warn" role="status"><b>{t('注销申请已受理')}</b><p>{t('账户已停用。请在 {date} 前使用邮箱中的恢复链接取消注销。', { date: timeText(due, locale, profile.timezone) })}</p>
      <a href="/login" className="btn">{t('返回登录')}</a></div> : <>
      <p className="muted">{t('导出你的资料、成员关系、安全活动，以及你发出的消息、所在会话与消息设置。不含组织文档、他人的消息或登录密钥。')} {t('也包含 Agent 对话的可见文字、记忆、设置、数据同意、条款接受记录、运行授权与钱包流水；不含模型的思考过程、工具读取的业务数据或密钥。')}</p>
      <PasswordProof value={password} onChange={setPassword} />
      <div><button className="btn" disabled={busy || !password} onClick={() => void perform('export')}>{t('导出个人数据')}</button></div>
      <div className="account-danger col gap8"><h3>{t('申请注销账户')}</h3>
        <p className="muted">{t('账户将立即停用。七天内可通过邮件取消注销；此后删除个人资料，组织中的贡献将保留。')}</p>
        <p className="muted">{t('注销后，Agent 对话、记忆与个人模型密钥会被删除；Agent 钱包中未用完的余额作废，不退还（充值一经完成不可退款）。计费与付款记录不再关联你的姓名与邮箱，按法定期限保留。')}</p>
        <p className="tiny muted">{t('共享工作空间的最后一位所有者或管理员，以及最后一位平台管理员，需要先移交职责。')}</p>
        <div><button className="btn danger" disabled={busy || !password} onClick={() => void perform('deletion')}>{t('申请注销账户')}</button></div>
      </div>
    </>}
  </section>;
}
