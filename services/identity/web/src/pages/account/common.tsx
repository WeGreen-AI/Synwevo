import { ApiError } from '@synwevo/auth-client';
import { useI18n, type Locale, type Translator } from '@synwevo/ui';
import { refusalText } from '../../lib/refusals';
import '../account.messages';

export interface Profile {
  id: string; email: string; display_name: string | null; status: string;
  locale: Locale; timezone: string; has_avatar: boolean; updated_at: number;
  deletion_due_at: number | null; email_verified_at: number | null;
}
const errors: Record<string, string> = {
  bad_avatar: '请上传不超过 256 KB、512 × 512 像素的普通 PNG 图片',
  bad_display_name: '名称须为 1 至 80 个字符', bad_locale: '请选择支持的语言',
  bad_timezone: '请选择有效的 IANA 时区', email_unavailable: '此邮箱无法使用',
  bad_email: '请输入不同的有效邮箱', bad_token: '链接无效或已过期，请重新申请',
  last_admin: '你是最后一个平台管理员，请先移交管理权限',
  ownership_required: '请先移交共享工作空间的所有权和管理权限',
  organization_ownership_required: '请先移交组织的所有权和管理权限',
  mail_not_configured: '邮件服务尚未配置，请联系管理员', public_url_missing: '邮件服务尚未配置，请联系管理员',
  bad_credentials: '邮箱或口令不正确', bad_password: '口令不正确',
  reauth_failed: '口令不正确', too_many_attempts: '操作过于频繁，请稍后再试',
  unauthorized: '登录已失效，请重新登录', csrf_failed: '请求校验失败，请刷新页面后重试',
  reauth_required: '这是敏感操作，请重新输入口令确认。', bad_code: '验证码不正确，请检查手机上的时间是否准确',
  last_factor: '请先添加另一种登录方式，再删除最后一个安全密钥',
  session_ended: '登录已失效，请重新登录', no_challenge: '验证已过期，请重新开始',
  code_expired: '验证码已失效，请重新获取', last_sign_in_method: '这是账户唯一的登录方式。请先设置口令或添加安全密钥，再解除关联',
  identity_linked_elsewhere: '这个外部账户已关联到另一个 Synwevo 账户', unknown_provider: '未启用这个外部身份提供商',
  no_password: '这个账户还没有口令。请通过邮箱中的重置链接设置口令',
  weak_password: '这个口令不符合要求，请使用更长且不包含个人信息的口令',
};
export function errorText(error: unknown, t: Translator) {
  // The server's catalogued sentence first, then this page's wording for its code (lib/refusals.ts).
  if (error instanceof ApiError) return refusalText(error, errors) + (error.requestId ? ` (${error.requestId})` : '');
  return t('操作失败，请重试。');
}
export function ErrorNotice({ error, retry }: { error: string | null; retry?: () => void }) {
  const { t } = useI18n();
  return error ? <div className="notice err" role="alert">{error} {retry && <button className="btn sm" onClick={retry}>{t('重试')}</button>}</div> : null;
}
export function PasswordProof({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useI18n();
  return <label className="field"><span>{t('当前口令')}</span><input className="input" type="password" autoComplete="current-password"
    value={value} onChange={e => onChange(e.target.value)} required maxLength={1024} />
    <span className="tiny muted">{t('敏感操作需要重新确认身份。')}</span></label>;
}
export function timeText(at: number, locale: string, timezone: string) {
  return new Intl.DateTimeFormat(locale, { dateStyle: 'medium', timeStyle: 'short', timeZone: timezone }).format(at * 1000);
}
