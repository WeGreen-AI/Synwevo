import { hasMessage, joinMessages, registerMessages, t, type Messages } from '@synwevo/ui/i18n-core';

/** Presentation only: wire codes, details and original diagnostics remain on ApiError. */
export const apiMessages: Messages = {
  '发布新内容需要源工作空间的有效应用委托，请在源工作空间重新授权': { en: 'Publishing new content requires a valid integration delegation for the source workspace. Authorize the integration there and try again.', 'zh-Hant': '發佈新內容需要來源工作空間的有效應用委託，請在來源工作空間重新授權。' },
  '组织包含无权访问来源内容的成员，当前数据流政策不允许发布。': { en: 'The organization includes members who cannot access the source content. The current data transfer policy does not allow publishing it.', 'zh-Hant': '組織包含無權存取來源內容的成員，目前的資料流政策不允許發佈。' },
  '来源组织的数据流政策无法识别，请联系组织管理员。': { en: 'The source organization’s data transfer policy is not recognized. Contact an organization administrator.', 'zh-Hant': '來源組織的資料流政策無法識別，請聯絡組織管理員。' },
  '来源空间已不可用，或你已失去其访问权限。': { en: 'The source workspace is unavailable or you no longer have access.', 'zh-Hant': '來源空間已不可用，或你已失去其存取權限。' },
  '你没有将来源内容导出到其他空间的权限。': { en: 'You do not have permission to export the source content to another workspace.', 'zh-Hant': '你沒有將來源內容匯出到其他空間的權限。' },
  '目标空间已不可用，或你已失去其访问权限。': { en: 'The target workspace is unavailable or you no longer have access.', 'zh-Hant': '目標空間已不可用，或你已失去其存取權限。' },
  '来源空间禁止将内容转出。请联系空间所有者。': { en: 'The source workspace does not allow content transfers. Contact its owner.', 'zh-Hant': '來源空間禁止將內容轉出。請聯絡空間擁有者。' },
  '目标包含无权访问来源内容的成员，当前数据流政策不允许转移。': { en: 'The target includes people without access to the source content. Its data transfer policy does not allow this transfer.', 'zh-Hant': '目標包含無權存取來源內容的成員，目前資料流政策不允許轉移。' },
  '来源的数据流政策不允许向这个目标发送内容。': { en: 'The source data transfer policy does not allow sending content to this target.', 'zh-Hant': '來源的資料流政策不允許向這個目標傳送內容。' },
  '来源内容只能在同一组织内转移。': { en: 'The source content can only be transferred within the same organization.', 'zh-Hant': '來源內容只能在同一組織內轉移。' },
  '数据流政策已更新，请刷新后重试。': { en: 'The data transfer policy has changed. Refresh and try again.', 'zh-Hant': '資料流政策已更新，請重新整理後重試。' },
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
codes('source_delegation_required', '发布新内容需要源工作空间的有效应用委托，请在源工作空间重新授权');
codes('transfer_organization_audience_denied', '组织包含无权访问来源内容的成员，当前数据流政策不允许发布。');
codes('transfer_publication_policy_denied', '来源组织的数据流政策无法识别，请联系组织管理员。');
codes('transfer_source_unavailable', '来源空间已不可用，或你已失去其访问权限。');
codes('transfer_export_denied', '你没有将来源内容导出到其他空间的权限。');
codes('transfer_target_unavailable', '目标空间已不可用，或你已失去其访问权限。');
codes('transfer_source_closed', '来源空间禁止将内容转出。请联系空间所有者。');
codes('transfer_audience_denied', '目标包含无权访问来源内容的成员，当前数据流政策不允许转移。');
codes('transfer_audience_external transfer_organization_external', '来源的数据流政策不允许向这个目标发送内容。');
codes('transfer_organization_denied transfer_organization_policy_denied', '来源内容只能在同一组织内转移。');
codes('transfer_policy_changed', '数据流政策已更新，请刷新后重试。');
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
/** The refusal as one sentence, without the request id. `codes` is a shell's own code → source
 *  map, for a code whose several server sentences share one meaning (or a sentence the catalogue
 *  cannot hold, such as one naming a provider): it is consulted after the server's catalogued
 *  sentence, which is always the more specific of the two, and before the shared codes. A shell
 *  that places the request id itself uses this; everything else uses `apiFailureText`. */
export function apiFailureSentence(error: ApiFailure, codes: Record<string, string> = {}): string {
  const template = templated(error);
  if (error.code === 'gateway_blocked' || error.code === 'upstream_unavailable') {
    return t(error.code === 'gateway_blocked' ? '请求被 {gateway} 拦截（HTTP {status}），未到达 Synwevo。' : '{gateway} 无法连接 Synwevo（HTTP {status}）。请稍后重试。', {
      gateway: error.detail?.gateway === '前置网关' ? t('前置网关') : error.detail?.gateway || t('前置网关'), status: String(error.status),
    });
  }
  if (template) return t(template[0], template[1]);
  // Domain catalogs may provide a more useful known sentence than the general code label.
  if (hasMessage(error.originalMessage)) return t(error.originalMessage);
  if (Object.hasOwn(codes, error.code)) return t(codes[error.code]);
  if (API_ERROR_SOURCE[error.code]) return t(API_ERROR_SOURCE[error.code]);
  if (error.status >= 500) return t('服务内部出错，请稍后重试。');
  return t('请求未完成（{code}）。', { code: error.code || String(error.status) });
}
export function apiFailureText(error: ApiFailure): string {
  const text = apiFailureSentence(error);
  return error.requestId ? joinMessages([text, t('请求编号：{id}', { id: error.requestId })]) : text;
}

function templated(error: ApiFailure): readonly [string, Record<string, string | number>?] | null {
  const tpl = Object.hasOwn(ERROR_TEMPLATES, error.code) ? ERROR_TEMPLATES[error.code] : undefined;
  if (!tpl) return null;
  try { return tpl(error.detail ?? {}); } catch { return null; }
}
