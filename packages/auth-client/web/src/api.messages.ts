import { hasMessage, joinMessages, registerMessages, t, type Messages } from '@synwevo/ui/i18n-core';

/** Presentation only: wire codes, details and original diagnostics remain on ApiError. */
export const apiMessages: Messages = {
  '网络连接失败，请检查连接后重试。': { en: 'Network connection failed. Check your connection and try again.', 'zh-Hant': '網路連線失敗，請檢查連線後重試。' },
  '服务内部出错，请稍后重试。': { en: 'An internal server error occurred. Please try again later.', 'zh-Hant': '服務內部出錯，請稍後重試。' },
  '请求过于频繁，请稍后重试。': { en: 'Too many requests. Please try again later.', 'zh-Hant': '請求過於頻繁，請稍後重試。' },
  '请重新登录后再试。': { en: 'Please sign in again and retry.', 'zh-Hant': '請重新登入後再試。' },
  '你没有执行此操作的权限。': { en: 'You do not have permission to perform this action.', 'zh-Hant': '你沒有執行此操作的權限。' },
  '找不到请求的资源。': { en: 'The requested resource was not found.', 'zh-Hant': '找不到請求的資源。' },
  '请求参数无效，请检查设置。': { en: 'Invalid request parameters. Check your settings.', 'zh-Hant': '請求參數無效，請檢查設定。' },
  '内容已更改，请刷新后重试。': { en: 'The content has changed. Refresh and try again.', 'zh-Hant': '內容已變更，請重新整理後重試。' },
  '请先完成身份验证。': { en: 'Complete identity verification to continue.', 'zh-Hant': '請先完成身分驗證。' },
  '登录信息或验证码无效，请检查后重试。': { en: 'Invalid sign-in details or verification code. Check and try again.', 'zh-Hant': '登入資訊或驗證碼無效，請檢查後重試。' },
  '此链接已失效，请重新申请。': { en: 'This link is no longer valid. Request a new one.', 'zh-Hant': '此連結已失效，請重新申請。' },
  '文件或请求超过大小上限。': { en: 'The file or request exceeds the size limit.', 'zh-Hant': '檔案或請求超過大小上限。' },
  '该格式不支持此操作。': { en: 'This format does not support this operation.', 'zh-Hant': '該格式不支援此操作。' },
  '上传失败，请重试。': { en: 'Upload failed. Please try again.', 'zh-Hant': '上傳失敗，請重試。' },
  '创建过于频繁，请稍后重试。': { en: 'Too many creation requests. Please try again later.', 'zh-Hant': '建立請求過於頻繁，請稍後重試。' },
  '此邀请已被拒绝，请联系邀请人重新邀请。': { en: 'This invitation was declined. Ask the inviter to send a new invitation.', 'zh-Hant': '此邀請已被拒絕，請聯絡邀請人重新邀請。' },
  '头像文件无效，请选择受支持的图片。': { en: 'Invalid avatar file. Choose a supported image.', 'zh-Hant': '頭像檔案無效，請選擇受支援的圖片。' },
  '请求未完成（{code}）。': { en: 'The request could not be completed ({code}).', 'zh-Hant': '請求未完成（{code}）。' },
  '操作失败，请检查设置后重试。': { en: 'The operation failed. Check your settings and try again.', 'zh-Hant': '操作失敗，請檢查設定後重試。' },
  '请求编号：{id}': { en: 'Request ID: {id}', 'zh-Hant': '請求編號：{id}' },
  '前置网关': { en: 'Gateway', 'zh-Hant': '前置閘道' },
  '{gateway} 无法连接 Synwevo（HTTP {status}）。请稍后重试。': { en: '{gateway} could not reach Synwevo (HTTP {status}). Please try again later.', 'zh-Hant': '{gateway} 無法連線 Synwevo（HTTP {status}）。請稍後重試。' },
  '请求被 {gateway} 拦截（HTTP {status}），未到达 Synwevo。': { en: '{gateway} blocked the request (HTTP {status}); it did not reach Synwevo.', 'zh-Hant': '請求被 {gateway} 攔截（HTTP {status}），未到達 Synwevo。' },
};
registerMessages(apiMessages);
export const API_ERROR_SOURCE: Record<string, string> = {};
function codes(names: string, source: string) { for (const code of names.split(' ')) API_ERROR_SOURCE[code] = source; }
codes('network network_error', '网络连接失败，请检查连接后重试。');
codes('server_error internal_error', '服务内部出错，请稍后重试。');
codes('rate_limited login_rate_limited too_many_attempts', '请求过于频繁，请稍后重试。');
codes('unauthorized unauthenticated session_expired session_revoked login_required', '请重新登录后再试。');
codes('forbidden read_only_role export_not_allowed workspace_forbidden workspace_mismatch csrf_failed csrf_invalid platform_admin_required', '你没有执行此操作的权限。');
codes('not_found', '找不到请求的资源。');
codes('invalid bad_request bad_value bad_json invalid_locale', '请求参数无效，请检查设置。');
codes('stale stale_template conflict idempotency_conflict', '内容已更改，请刷新后重试。');
codes('reauth_required mfa_required verification_required', '请先完成身份验证。');
codes('bad_credentials invalid_credentials invalid_code bad_code', '登录信息或验证码无效，请检查后重试。');
codes('invalid_token expired_token invite_expired invite_invalid', '此链接已失效，请重新申请。');
codes('too_large too_many editor_body_too_large', '文件或请求超过大小上限。');
codes('unsupported unsupported_media_type bad_format', '该格式不支持此操作。');
codes('upload_failed', '上传失败，请重试。');
codes('creation_rate_limited', '创建过于频繁，请稍后重试。');
codes('invite_declined', '此邀请已被拒绝，请联系邀请人重新邀请。');
codes('bad_avatar', '头像文件无效，请选择受支持的图片。');

export interface ApiFailure { code: string; status: number; originalMessage: string; requestId?: string; detail?: any }

/** A refusal whose sentence carries a number from `detail` (a time window, a size or count limit)
 *  cannot be matched by the server's sentence once that number is configurable, so a shell may
 *  register a template per code: it returns the catalogue source and its parameters, or null to
 *  fall through to the server's sentence. Registered by the shell that owns those codes; the
 *  other shells never see them. */
export type ErrorTemplate = (detail: any) => readonly [source: string, params?: Record<string, string | number>] | null;
const ERROR_TEMPLATES: Record<string, ErrorTemplate> = {};
export function registerErrorTemplates(map: Record<string, ErrorTemplate>): void { Object.assign(ERROR_TEMPLATES, map); }
export function apiFailureText(error: ApiFailure): string {
  let text: string;
  const template = templated(error);
  if (error.code === 'gateway_blocked' || error.code === 'upstream_unavailable') {
    text = t(error.code === 'gateway_blocked' ? '请求被 {gateway} 拦截（HTTP {status}），未到达 Synwevo。' : '{gateway} 无法连接 Synwevo（HTTP {status}）。请稍后重试。', {
      gateway: error.detail?.gateway === '前置网关' ? t('前置网关') : error.detail?.gateway || t('前置网关'), status: String(error.status),
    });
  } else if (template) {
    text = t(template[0], template[1]);
  } else if (hasMessage(error.originalMessage)) {
    // Domain catalogs may provide a more useful known sentence than the general code label.
    text = t(error.originalMessage);
  } else if (API_ERROR_SOURCE[error.code]) text = t(API_ERROR_SOURCE[error.code]);
  else if (error.status >= 500) text = t('服务内部出错，请稍后重试。');
  else text = t('请求未完成（{code}）。', { code: error.code || String(error.status) });
  if (error.requestId) text = joinMessages([text, t('请求编号：{id}', { id: error.requestId })]);
  return text;
}

function templated(error: ApiFailure): readonly [string, Record<string, string | number>?] | null {
  const tpl = Object.hasOwn(ERROR_TEMPLATES, error.code) ? ERROR_TEMPLATES[error.code] : undefined;
  if (!tpl) return null;
  try { return tpl(error.detail ?? {}); } catch { return null; }
}
