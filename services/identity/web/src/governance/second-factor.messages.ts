/** A26: the platform-controlled reset of a lost second factor on the administration page — the
 *  account detail's second-factor section, the reset and cancel confirmations
 *  (governance/SecondFactorResetDialog.tsx), the permission's label (governance/PlatformAccess.tsx)
 *  and the reset's refusals worded by code (governance/labels.ts). Every sentence is here in the
 *  three languages; the sentences the other confirmed-operation dialogs already have (重新预览,
 *  预览有效至 {time}…, 这份预览已失效…) are repeated with the same wording so this page does not
 *  depend on which other dialog was loaded first. */
import { registerMessages } from "@synwevo/ui";
export const secondFactorMessages = {
  // ---- the permission (platform_access.PERMISSIONS)
  "核验身份后重置丢失的第二因素（延迟生效并通知本人）": {
    en: "Reset a lost second factor after confirming identity (delayed and announced to the person)",
    "zh-Hant": "核驗身分後重設遺失的第二因素（延遲生效並通知本人）" },

  // ---- the account detail's section
  "第二因素": { en: "Second factor", "zh-Hant": "第二因素" },
  "动态验证码": { en: "Authenticator codes", "zh-Hant": "動態驗證碼" },
  "已登记": { en: "Enrolled", "zh-Hant": "已登記" },
  "未登记": { en: "Not enrolled", "zh-Hant": "未登記" },
  "剩余恢复码": { en: "Recovery codes left", "zh-Hant": "剩餘復原碼" },
  "已登记的安全密钥": { en: "Registered passkeys", "zh-Hant": "已登記的安全金鑰" },
  "有一次第二因素重置待生效": { en: "A second-factor reset is pending", "zh-Hant": "有一次第二因素重設待生效" },
  "生效时间": { en: "Takes effect", "zh-Hant": "生效時間" },
  "移除范围": { en: "Removes", "zh-Hant": "移除範圍" },
  "动态验证码、恢复码和安全密钥": { en: "Authenticator, recovery codes and passkeys", "zh-Hant": "動態驗證碼、復原碼和安全金鑰" },
  "动态验证码和恢复码（保留安全密钥）": { en: "Authenticator and recovery codes (passkeys kept)", "zh-Hant": "動態驗證碼和復原碼（保留安全金鑰）" },
  "核验方式": { en: "Identity confirmed by", "zh-Hant": "核驗方式" },
  "申请人": { en: "Requested by", "zh-Hant": "申請人" },
  "申请时间": { en: "Requested", "zh-Hant": "申請時間" },
  "取消重置": { en: "Cancel reset", "zh-Hant": "取消重設" },
  "重置第二因素": { en: "Reset second factor", "zh-Hant": "重設第二因素" },
  "用于本人同时丢失了动态验证码和全部恢复码：先核验本人身份再申请，{hours} 小时后生效，期间本人可以取消。": {
    en: "For a person who lost both the authenticator and every recovery code: confirm their identity first, then request it. It takes effect after {hours} hours, and the person can cancel it until then.",
    "zh-Hant": "用於本人同時遺失了動態驗證碼和全部復原碼：先核驗本人身分再申請，{hours} 小時後生效，期間本人可以取消。" },
  "你的平台角色不能重置第二因素。": { en: "Your platform role cannot reset a second factor.", "zh-Hant": "你的平台角色不能重設第二因素。" },
  "请通过应用身份管理操作该账户。": { en: "Manage this account through application identities.", "zh-Hant": "請透過應用程式身分管理操作該帳戶。" },
  "不能为自己的账户申请重置第二因素。": { en: "You cannot request a second-factor reset for your own account.", "zh-Hant": "不能為自己的帳戶申請重設第二因素。" },
  "该账户持有平台权限：管理员找回访问走最高管理员交接流程，不能在这里重置第二因素": {
    en: "This account holds platform access: an administrator regains access through the super administrator handover, so its second factor cannot be reset here.",
    "zh-Hant": "該帳戶持有平台權限：管理員找回存取走最高管理員交接流程，不能在這裡重設第二因素" },
  "只能为状态正常的账户重置第二因素。": { en: "A second factor can be reset only for an active account.", "zh-Hant": "只能為狀態正常的帳戶重設第二因素。" },
  "该账户没有动态验证码、恢复码或安全密钥，无需重置。": {
    en: "This account has no authenticator, recovery codes or passkeys; there is nothing to reset.",
    "zh-Hant": "該帳戶沒有動態驗證碼、復原碼或安全金鑰，無需重設。" },
  "第二因素重置记录": { en: "Second-factor resets", "zh-Hant": "第二因素重設記錄" },
  "生效或结束": { en: "Effective or ended", "zh-Hant": "生效或結束" },
  "待生效": { en: "Pending", "zh-Hant": "待生效" },
  "已取消": { en: "Cancelled", "zh-Hant": "已取消" },
  "已完成": { en: "Completed", "zh-Hant": "已完成" },
  "已作废": { en: "Void", "zh-Hant": "已作廢" },
  "{time} 生效": { en: "Takes effect {time}", "zh-Hant": "{time} 生效" },
  "{time} 取消": { en: "Cancelled {time}", "zh-Hant": "{time} 取消" },
  "{time} 完成": { en: "Completed {time}", "zh-Hant": "{time} 完成" },
  "到期时未执行（{time}）": { en: "Not carried out when due ({time})", "zh-Hant": "到期時未執行（{time}）" },
  "到期时未执行（{time}）：{reason}": { en: "Not carried out when due ({time}): {reason}", "zh-Hant": "到期時未執行（{time}）：{reason}" },
  "账户已删除": { en: "the account was deleted", "zh-Hant": "帳戶已刪除" },
  "从备份恢复后作废": { en: "voided by a restore from backup", "zh-Hant": "從備份還原後作廢" },
  "期间账户被停用": { en: "the account was suspended during the waiting period", "zh-Hant": "期間帳戶被停用" },
  "账户邮箱未验证": { en: "the account's email is not verified", "zh-Hant": "帳戶郵箱未驗證" },
  "期间账户邮箱已更改": { en: "the account's email changed during the waiting period", "zh-Hant": "期間帳戶郵箱已變更" },
  "申请人已不再持有重置权限": { en: "the requester no longer holds the reset permission", "zh-Hant": "申請人已不再持有重設權限" },
  "通知邮件未能送达本人": { en: "the notice could not be delivered to the person", "zh-Hant": "通知郵件未能送達本人" },
  "到期时已没有可重置的第二因素": { en: "there was no second factor left to reset when it fell due", "zh-Hant": "到期時已沒有可重設的第二因素" },
  "期间第二因素有变化或被使用过（说明本人仍持有它）": { en: "the second factor changed or was used during the waiting period (so the person still has it)", "zh-Hant": "期間第二因素有變化或被使用過（說明本人仍持有它）" },
  "等待发送": { en: "waiting to be sent", "zh-Hant": "等待傳送" },
  "正在发送": { en: "sending", "zh-Hant": "正在傳送" },
  "已送达发信服务": { en: "handed to the mail service", "zh-Hant": "已送達發信服務" },
  "发送失败": { en: "failed", "zh-Hant": "傳送失敗" },
  "未发送": { en: "not sent", "zh-Hant": "未傳送" },
  "通知邮件": { en: "Notice email", "zh-Hant": "通知郵件" },
  "通知邮件送达 {hours} 小时后（最早 {time}）": { en: "{hours} hours after the notice is delivered (at the earliest {time})", "zh-Hant": "通知郵件送達 {hours} 小時後（最早 {time}）" },
  "只有通知邮件送达本人且满 {hours} 小时后才会执行；本人可在此之前用邮件里的链接取消。期间邮箱变更、账户停用、第二因素被使用或申请人失去权限，都会使它作废。": {
    en: "It runs only once the notice has reached the person and {hours} hours have passed since; until then they can cancel it with the link in the email. An email change, a suspension, any use of the second factor or the requester losing the permission voids it.",
    "zh-Hant": "只有通知郵件送達本人且滿 {hours} 小時後才會執行；本人可在此之前用郵件裡的連結取消。期間郵箱變更、帳戶停用、第二因素被使用或申請人失去權限，都會使它作廢。" },
  "该账户的邮箱尚未验证：通知与取消链接无法可靠送达本人，不能申请重置。": {
    en: "This account's email is not verified, so the notice and cancel link cannot reliably reach the person. A reset cannot be requested.",
    "zh-Hant": "該帳戶的郵箱尚未驗證：通知與取消連結無法可靠送達本人，不能申請重設。" },
  "该账户的邮箱最近更改过：为防止通知被转到他人邮箱，更改后 7 天内不能申请重置。": {
    en: "This account's email changed recently. So that the notice cannot be redirected to someone else's mailbox, a reset cannot be requested within 7 days of the change.",
    "zh-Hant": "該帳戶的郵箱最近變更過：為防止通知被轉到他人郵箱，變更後 7 天內不能申請重設。" },
  "账户已不是正常状态": { en: "the account was no longer active", "zh-Hant": "帳戶已不是正常狀態" },
  "账户已持有平台权限": { en: "the account held platform access", "zh-Hant": "帳戶已持有平台權限" },
  "账户是应用身份": { en: "the account is an application identity", "zh-Hant": "帳戶是應用程式身分" },
  "没有第二因素重置记录。": { en: "No second-factor resets on record.", "zh-Hant": "沒有第二因素重設記錄。" },
  "正在读取第二因素状态…": { en: "Reading the second-factor state…", "zh-Hant": "正在讀取第二因素狀態…" },

  // ---- the reset confirmation
  "重置不会立即执行：确认后 {hours} 小时才生效。期间本人会收到附取消链接的邮件，可以随时取消。": {
    en: "Nothing is removed now: the reset takes effect {hours} hours after you confirm. Meanwhile the person receives an email with a cancel link and can cancel at any time.",
    "zh-Hant": "重設不會立即執行：確認後 {hours} 小時才生效。期間本人會收到附取消連結的郵件，可以隨時取消。" },
  "这是平台的恢复操作，会写入审计记录；全程不读取、不显示动态验证码种子或恢复码，也不以本人身份更改任何凭据。": {
    en: "This is a platform recovery act and is written to the audit record. No authenticator seed or recovery code is read or shown at any point, and no credential is changed as the person.",
    "zh-Hant": "這是平台的復原操作，會寫入審計記錄；全程不讀取、不顯示動態驗證碼種子或復原碼，也不以本人身分變更任何憑證。" },
  "同时移除该账户的安全密钥（{count} 个）": {
    en: { one: "Also remove the account's passkey ({count})", other: "Also remove the account's passkeys ({count})" },
    "zh-Hant": "同時移除該帳戶的安全金鑰（{count} 個）" },
  "正在核对账户与重置影响…": { en: "Checking the account and what the reset will do…", "zh-Hant": "正在核對帳戶與重設影響…" },
  "目标账户": { en: "Target account", "zh-Hant": "目標帳戶" },
  "生效时间：{time}": { en: "Takes effect: {time}", "zh-Hant": "生效時間：{time}" },
  "生效时将发生": { en: "When it takes effect", "zh-Hant": "生效時將發生" },
  "删除动态验证码：身份验证器应用里的条目不再有效。": {
    en: "The authenticator is removed: the entry in the authenticator app stops working.",
    "zh-Hant": "刪除動態驗證碼：驗證器應用程式裡的項目不再有效。" },
  "删除剩余的 {count} 个恢复码。": {
    en: { one: "The remaining {count} recovery code is deleted.", other: "The remaining {count} recovery codes are deleted." },
    "zh-Hant": "刪除剩餘的 {count} 個復原碼。" },
  "移除 {count} 个安全密钥。": {
    en: { one: "{count} passkey is removed.", other: "{count} passkeys are removed." },
    "zh-Hant": "移除 {count} 個安全金鑰。" },
  "保留 {count} 个安全密钥：本人仍可用它们登录。": {
    en: { one: "{count} passkey is kept: the person can still sign in with it.", other: "{count} passkeys are kept: the person can still sign in with them." },
    "zh-Hant": "保留 {count} 個安全金鑰：本人仍可用它們登入。" },
  "结束该账户的全部会话并撤销应用令牌（当前 {sessions} 个会话、{tokens} 个应用令牌）。": {
    en: "Every session of the account ends and its application tokens are revoked (currently sessions: {sessions}, application tokens: {tokens}).",
    "zh-Hant": "結束該帳戶的全部工作階段並撤銷應用程式權杖（目前 {sessions} 個工作階段、{tokens} 個應用程式權杖）。" },
  "口令、资料、成员关系和数据不变；之后本人用口令登录，再重新设置第二因素。": {
    en: "The password, profile, memberships and data stay as they are; the person then signs in with the password and sets up a second factor again.",
    "zh-Hant": "密碼、個人資料、成員關係和資料不變；之後本人用密碼登入，再重新設定第二因素。" },
  "确认后会立即给本人的邮箱发送通知：由平台管理员申请、何时生效，并附取消链接。本人在生效前取消，则什么都不会改变。": {
    en: "When you confirm, the person's own address is emailed at once: that a platform administrator requested it, when it takes effect, and a cancel link. If the person cancels before then, nothing changes.",
    "zh-Hant": "確認後會立即寄送通知到本人的電子郵件：由平台管理員申請、何時生效，並附取消連結。本人在生效前取消，則什麼都不會改變。" },
  "这份预览已失效，请重新预览后再确认。": { en: "This preview is no longer valid. Preview again before confirming.", "zh-Hant": "這份預覽已失效，請重新預覽後再確認。" },
  "预览有效至 {time}。账户变化后必须重新预览。": { en: "Preview valid until {time}. Review again if the account changes.", "zh-Hant": "預覽有效至 {time}。帳戶變更後必須重新預覽。" },
  "如何核验了本人身份": { en: "How you confirmed the person's identity", "zh-Hant": "如何核驗了本人身分" },
  "（必选）": { en: " (required)", "zh-Hant": "（必選）" },
  "请选择核验方式…": { en: "Choose how…", "zh-Hant": "請選擇核驗方式…" },
  "当面核验": { en: "In person", "zh-Hant": "當面核驗" },
  "视频通话核验": { en: "On a video call", "zh-Hant": "視訊通話核驗" },
  "核验了证件": { en: "Checked an identity document", "zh-Hant": "核驗了證件" },
  "其他（在理由中说明）": { en: "Other (explain in the reason)", "zh-Hant": "其他（在理由中說明）" },
  "我已核验本人身份，并核对了目标账户和上述影响。": {
    en: "I have confirmed the person's identity and checked the target account and these effects.",
    "zh-Hant": "我已核驗本人身分，並核對了目標帳戶和上述影響。" },
  "写明如何核验了本人身份，以及为什么需要重置": {
    en: "How was the identity confirmed, and why is the reset needed?",
    "zh-Hant": "寫明如何核驗了本人身分，以及為什麼需要重設" },
  "重新预览": { en: "Preview again", "zh-Hant": "重新預覽" },
  "已申请重置第二因素，将于 {time} 生效；已给本人发送附取消链接的邮件。": {
    en: "Second-factor reset requested; it takes effect {time}. The person has been sent an email with a cancel link.",
    "zh-Hant": "已申請重設第二因素，將於 {time} 生效；已寄送附取消連結的郵件給本人。" },
  "账户的第二因素或状态已变化，请重新预览影响。": {
    en: "The account's second factor or status has changed. Review the impact again.",
    "zh-Hant": "帳戶的第二因素或狀態已變更，請重新預覽影響。" },

  // ---- the cancellation
  "{email}：将于 {time} 生效的第二因素重置会被取消；动态验证码、恢复码和安全密钥保持不变。本人会收到邮件和安全通知。": {
    en: "{email}: the second-factor reset due {time} is cancelled; the authenticator, recovery codes and passkeys stay as they are. The person receives an email and a security notice.",
    "zh-Hant": "{email}：將於 {time} 生效的第二因素重設會被取消；動態驗證碼、復原碼和安全金鑰保持不變。本人會收到郵件和安全通知。" },
  "已取消第二因素重置。": { en: "Second-factor reset cancelled.", "zh-Hant": "已取消第二因素重設。" },

  // ---- refusals by code (governance/labels.ts)
  "该账户没有可重置的第二因素：没有动态验证码和恢复码，也没有选择移除安全密钥。": {
    en: "This account has nothing to reset: no authenticator or recovery codes, and removing passkeys was not chosen.",
    "zh-Hant": "該帳戶沒有可重設的第二因素：沒有動態驗證碼和復原碼，也沒有選擇移除安全金鑰。" },
  "该账户已有待生效的第二因素重置；请先取消它，或等待它生效。": {
    en: "This account already has a pending second-factor reset. Cancel it first, or wait for it to take effect.",
    "zh-Hant": "該帳戶已有待生效的第二因素重設；請先取消它，或等待它生效。" },
  "请选择核验本人身份的方式。": { en: "Choose how the person's identity was confirmed.", "zh-Hant": "請選擇核驗本人身分的方式。" },
  "找不到这项第二因素重置，可能已被删除；请刷新。": { en: "This second-factor reset was not found; refresh the page.", "zh-Hant": "找不到這項第二因素重設，可能已被刪除；請重新整理。" },
  "这项第二因素重置已经结束（已生效、已取消或已作废），请刷新。": {
    en: "This second-factor reset has already ended (taken effect, cancelled or void). Refresh the page.",
    "zh-Hant": "這項第二因素重設已經結束（已生效、已取消或已作廢），請重新整理。" },

  // ---- the server's own sentences (admin_second_factor.py), preferred to the code map above
  "不能重置自己的第二因素；请在「账户与安全」里管理自己的登录方式": {
    en: "You cannot reset your own second factor; manage your own sign-in methods under Account and security.",
    "zh-Hant": "不能重設自己的第二因素；請在「帳戶與安全」裡管理自己的登入方式" },
  "只有正常使用中的账户可以重置第二因素，请刷新后重试": {
    en: "Only an active account can have its second factor reset. Refresh and try again.",
    "zh-Hant": "只有正常使用中的帳戶可以重設第二因素，請重新整理後重試" },
  "该账户没有可以重置的第二因素": { en: "This account has no second factor to reset.", "zh-Hant": "該帳戶沒有可以重設的第二因素" },
  "该账户已有一项等待生效的第二因素重置": { en: "This account already has a second-factor reset waiting to take effect.", "zh-Hant": "該帳戶已有一項等待生效的第二因素重設" },
  "请选择你核实对方身份的方式": { en: "Choose how you confirmed the person's identity.", "zh-Hant": "請選擇你核實對方身分的方式" },
  "该账户的第二因素或状态已变化，请重新预览": { en: "The account's second factor or status has changed. Preview again.", "zh-Hant": "該帳戶的第二因素或狀態已變更，請重新預覽" },
  "找不到该账户的这项第二因素重置": { en: "This second-factor reset of the account was not found.", "zh-Hant": "找不到該帳戶的這項第二因素重設" },
  "这项第二因素重置已经结束（已生效、已取消或已作废）": {
    en: "This second-factor reset has already ended (taken effect, cancelled or void).",
    "zh-Hant": "這項第二因素重設已經結束（已生效、已取消或已作廢）" },
};
registerMessages(secondFactorMessages);
