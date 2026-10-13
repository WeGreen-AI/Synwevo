/** A26: the signed-out page the second-factor reset mail links to (pages/account/SecondFactorReset.tsx)
 *  and the person's own security activity row for a cancelled reset (ActivitySection.tsx). Every
 *  sentence is here in the three languages; 加载中…, 处理中… and 返回登录 are the account pages'. */
import { registerMessages } from '@synwevo/ui';
export const secondFactorResetMessages = {
  '取消第二因素重置': { en: 'Cancel second-factor reset', 'zh-Hant': '取消第二因素重設' },
  'Synwevo 平台管理员核验你的身份后，为这个账户申请了重置第二因素。': {
    en: 'After confirming your identity, a Synwevo platform administrator requested a reset of this account’s second factor.',
    'zh-Hant': 'Synwevo 平台管理員核驗你的身分後，為這個帳戶申請了重設第二因素。' },
  '重置将于 {date} 生效。': { en: 'The reset takes effect on {date}.', 'zh-Hant': '重設將於 {date} 生效。' },
  '生效时会发生：': { en: 'When it takes effect:', 'zh-Hant': '生效時會發生：' },
  '删除账户的动态验证码和恢复码；如果申请包含安全密钥，也会一并移除。': {
    en: 'The account’s authenticator and recovery codes are deleted; if the request includes passkeys, they are removed too.',
    'zh-Hant': '刪除帳戶的動態驗證碼和復原碼；如果申請包含安全金鑰，也會一併移除。' },
  '结束所有设备上的登录。': { en: 'You are signed out on every device.', 'zh-Hant': '結束所有裝置上的登入。' },
  '口令、资料和数据不变；之后请用口令登录，再重新设置第二因素。': {
    en: 'Your password, profile and data stay as they are; then sign in with your password and set up a second factor again.',
    'zh-Hant': '密碼、個人資料和資料不變；之後請用密碼登入，再重新設定第二因素。' },
  '如果你没有请求平台帮你找回第二因素，请立即取消，并联系平台管理员。如果是你请求的，无需任何操作。': {
    en: 'If you did not ask the platform to help you recover your second factor, cancel now and contact the platform administrators. If you did ask, you do not need to do anything.',
    'zh-Hant': '如果你沒有請求平台幫你找回第二因素，請立即取消，並聯絡平台管理員。如果是你請求的，無需任何操作。' },
  '取消后什么都不会改变。此操作不会自动登录。': {
    en: 'If you cancel, nothing changes. This does not sign you in.',
    'zh-Hant': '取消後什麼都不會改變。此操作不會自動登入。' },
  '取消重置': { en: 'Cancel reset', 'zh-Hant': '取消重設' },
  '重置已取消：你的第二因素保持不变。': {
    en: 'The reset is cancelled: your second factor stays as it is.',
    'zh-Hant': '重設已取消：你的第二因素保持不變。' },
  '这个链接无效或已失效：重置可能已经取消或完成，或链接不完整。请打开邮件里的完整链接；如有疑问，请联系平台管理员。': {
    en: 'This link is invalid or no longer works: the reset may have been cancelled or completed, or the link is incomplete. Open the full link from the email; if in doubt, contact the platform administrators.',
    'zh-Hant': '這個連結無效或已失效：重設可能已經取消或完成，或連結不完整。請開啟郵件裡的完整連結；如有疑問，請聯絡平台管理員。' },
  '取消期限已过（{date}）：重置已经或即将生效，这个链接不能再取消它。之后请用口令登录，再重新设置第二因素。': {
    en: 'The time to cancel has passed ({date}): the reset has taken effect or is about to, and this link can no longer cancel it. Sign in with your password, then set up a second factor again.',
    'zh-Hant': '取消期限已過（{date}）：重設已經或即將生效，這個連結不能再取消它。之後請用密碼登入，再重新設定第二因素。' },
  // the server's own sentence for every unusable link (account_routes.py); the page shows its own
  // invalid-link state for `bad_token`, this words it wherever the refusal is shown as such
  '链接无效或已失效：这项重置可能已经生效、已取消或已作废': {
    en: 'This link is invalid or no longer works: the reset may have taken effect, been cancelled or become void.',
    'zh-Hant': '連結無效或已失效：這項重設可能已經生效、已取消或已作廢' },
};
registerMessages(secondFactorResetMessages);
