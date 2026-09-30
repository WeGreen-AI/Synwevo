import { registerMessages } from "@synwevo/ui";

/** Refusals of the account service that can reach a person on more than one page of this shell,
 *  and the sentences `lib/refusals.ts` words from a code or from `detail` (CI-85). Loaded with
 *  the entry chunk, so these read the same wherever they are met; a refusal met on one page only
 *  belongs to that page's catalogue. */
export const messages = {
  // Directory application authority is checked both by the API and when a person accepts an invitation.
  "邀请必须绑定当前批准人与原审批": { en: "The invitation must remain bound to its original approval and current approver.", "zh-Hant": "邀請必須繫結目前核准人與原核准紀錄" },
  "邀请的原审批已失效": { en: "The original approval for this invitation is no longer valid.", "zh-Hant": "邀請的原核准紀錄已失效" },
  "发出邀请的集成已不再具备权限，请联系空间管理员重新邀请": { en: "The integration that sent this invitation no longer has permission. Ask a workspace administrator for a new invitation.", "zh-Hant": "發出邀請的整合已不再具備權限，請聯絡空間管理員重新邀請" },
  "集成发出的邀请只能赋予普通成员角色；请撤回这份邀请，再以本人身份重新发送": { en: "An integration's invitation can only grant a regular member role. Withdraw it, then send a new invitation as yourself.", "zh-Hant": "整合發出的邀請只能授予一般成員角色；請撤回這份邀請，再以本人身分重新傳送" },
  "管理员与所有者的成员操作需要本人在账户页面完成": { en: "Administrator and owner membership changes must be made by a person on the account page.", "zh-Hant": "管理員與擁有者的成員操作需要本人在帳戶頁面完成" },
  "角色必须为 viewer 或 editor": { en: "The role must be viewer or editor.", "zh-Hant": "角色必須為 viewer 或 editor" },
  "服务账户的目录写入授权已失效": { en: "This service account's permission to change the workspace directory is no longer valid.", "zh-Hant": "服務帳戶的目錄寫入授權已失效" },
  "服务账户没有获批的成员写入权限": { en: "This service account has no approved permission to change members.", "zh-Hant": "服務帳戶沒有獲准的成員寫入權限" },
  "该幂等键的先前请求结果尚未确认，不能自动重做；请先核对原请求结果": { en: "The outcome of the earlier request using this idempotency key is unknown. Check that request's result before trying again.", "zh-Hant": "此冪等鍵的先前請求結果尚未確認，不能自動重做；請先核對原請求結果" },
  "使用同一个幂等键的请求尚无已确认的响应；请稍后用同一个键重试": { en: "The request using this idempotency key has no confirmed response yet. Try again later with the same key.", "zh-Hant": "使用同一冪等鍵的請求尚無已確認的回應；請稍後以同一個鍵重試" },
  "该幂等键已用于另一个不同内容的请求；请换一个新的幂等键，或发送与原请求完全相同的内容": { en: "This idempotency key was used for a different request. Use a new key or send exactly the original request.", "zh-Hant": "此冪等鍵已用於另一個不同內容的請求；請換用新的冪等鍵，或傳送與原請求完全相同的內容" },
  "Idempotency-Key 必须是 1–255 个可打印 ASCII 字符，建议使用 UUID": { en: "Idempotency-Key must contain 1–255 printable ASCII characters. A UUID is recommended.", "zh-Hant": "Idempotency-Key 必須為 1–255 個可列印的 ASCII 字元，建議使用 UUID" },
  "配置记录暂不可用，请联系管理员检查": { en: "Settings records are temporarily unavailable. Contact an administrator.", "zh-Hant": "設定記錄暫時無法使用，請聯絡管理員檢查" },
  // ---- server sentences met on more than one page (the same wording as the page catalogues that also hold them)
  "这一步需要重新验证身份": { en: "This step requires identity verification.", "zh-Hant": "這一步需要重新驗證身分" },
  // a platform permission this person's roles do not carry (platform administration and the API console)
  "你的平台角色不包含这项操作所需的权限": { en: "Your platform roles do not include the permission this action needs.", "zh-Hant": "你的平台角色不包含這項操作所需的權限" },
  "请写明理由（8 至 500 个字）": { en: "Enter a reason (8 to 500 characters)", "zh-Hant": "請寫明理由（8 至 500 個字）" },
  "账户不可用": { en: "Account unavailable", "zh-Hant": "帳戶不可用" },
  "找不到该工作空间": { en: "This workspace was not found.", "zh-Hant": "找不到此工作空間" },
  "该工作空间当前不可用": { en: "This workspace is currently unavailable.", "zh-Hant": "此工作空間目前不可用" },
  "邮件地址必须是单个有效邮箱地址。": { en: "The email address must be a single valid address.", "zh-Hant": "郵件地址必須是單一有效的電郵地址。" },
  "该邮箱已被注册": { en: "This email address is already registered.", "zh-Hant": "此電郵已被註冊" },
  "请先验证邮箱再创建个人空间": { en: "Verify your email before creating a personal workspace.", "zh-Hant": "請先驗證電郵再建立個人空間" },
  "账户不存在": { en: "The account does not exist.", "zh-Hant": "帳戶不存在" },
  // password checks shed load with 503 before comparing anything: not a server fault
  "当前登录请求过多，请稍后再试": { en: "Too many sign-in requests right now. Try again shortly.", "zh-Hant": "目前登入請求過多，請稍後再試" },

  // Scoped password/Passkey confirmation is used by account and governance pages.
  "请选择有效的身份确认用途": { en: "Choose a valid purpose for identity verification.", "zh-Hant": "請選擇有效的身分確認用途" },
  "当前页面不能完成身份确认，请从账户服务重新开始": { en: "Identity verification is not available on this page. Start again from the account service.", "zh-Hant": "目前頁面無法完成身分確認，請從帳戶服務重新開始" },
  "安全密钥验证失败，请重新开始身份确认": { en: "The security key could not be verified. Start identity verification again.", "zh-Hant": "安全金鑰驗證失敗，請重新開始身分確認" },
  "当前账户没有可用于身份确认的安全密钥": { en: "This account has no security key available for identity verification.", "zh-Hant": "目前帳戶沒有可用於身分確認的安全金鑰" },
  "身份确认已过期或已使用，请重新开始": { en: "This identity verification has expired or was already used. Start again.", "zh-Hant": "身分確認已過期或已使用，請重新開始" },
  "账户不可用，请重新登录": { en: "The account is unavailable. Sign in again.", "zh-Hant": "帳戶無法使用，請重新登入" },
  "不能移除最后一名可用最高管理员的身份确认方式": { en: "You cannot remove the identity verification method of the last available super administrator.", "zh-Hant": "不能移除最後一名可用最高管理員的身分確認方式" },

  // ---- worded from detail (lib/refusals.ts REFUSAL_TEMPLATES)
  "验证码不正确，请重新获取。": { en: "Incorrect code. Request a new one.", "zh-Hant": "驗證碼不正確，請重新取得。" },
  "口令至少需要 {min} 个字符（当前 {length} 个）。长句子比复杂符号更好记也更安全。": { en: "Your password needs at least {min} characters (it has {length}). A long sentence is easier to remember and safer than complex symbols.", "zh-Hant": "密碼至少需要 {min} 個字元（目前 {length} 個）。長句子比複雜符號更好記也更安全。" },
  "口令过长（上限 {max} 个字符）。": { en: "The password is too long (at most {max} characters).", "zh-Hant": "密碼過長（上限 {max} 個字元）。" },
  "{problem}（这个链接已失效，请重新申请一封邮件）": { en: "{problem} (This link can no longer be used; request a new email.)", "zh-Hant": "{problem}（這個連結已失效，請重新申請一封郵件）" },
  "无法连接 {provider}，请稍后再试": { en: "Could not reach {provider}. Try again later.", "zh-Hant": "無法連線 {provider}，請稍後再試" },
  "{provider} 的发现文档不完整": { en: "The discovery document from {provider} is incomplete.", "zh-Hant": "{provider} 的探索文件不完整" },
  "{provider} 的发现文档 issuer 与配置不一致": { en: "The issuer in the discovery document from {provider} does not match the configuration.", "zh-Hant": "{provider} 探索文件中的 issuer 與設定不一致" },
  "{provider} 的端点不是 https": { en: "An endpoint of {provider} does not use https.", "zh-Hant": "{provider} 的端點不是 https" },
  "无法取得 {provider} 的签名密钥": { en: "Could not fetch the signing keys of {provider}.", "zh-Hant": "無法取得 {provider} 的簽章金鑰" },
  "这个 {provider} 账户已关联到另一个 Synwevo 账户": { en: "This {provider} account is already linked to another Synwevo account.", "zh-Hant": "這個 {provider} 帳戶已關聯到另一個 Synwevo 帳戶" },
  "这个空间 24 小时内最多发出 {n} 个邀请，请稍后再试": { en: "A workspace can send at most {n} invitations in 24 hours. Try again later.", "zh-Hant": "這個空間 24 小時內最多發出 {n} 個邀請，請稍後再試" },
  "成员文件超过 {n} MB 上限": { en: "The member file is over the {n} MB limit.", "zh-Hant": "成員檔案超過 {n} MB 上限" },
  "文件超过安全检查的大小上限（{n} MB），无法接纳": { en: "The file is over the security check's size limit ({n} MB) and cannot be accepted.", "zh-Hant": "檔案超過安全檢查的大小上限（{n} MB），無法接收" },
  "文件「{filename}」被判定为恶意内容（{signature}），已拒绝": { en: "“{filename}” was identified as malicious ({signature}) and was refused.", "zh-Hant": "檔案「{filename}」被判定為惡意內容（{signature}），已拒絕" },
  "文件设有密码或含有加密内容，安全检查无法查看其中的内容，因此未被接纳；请移除密码（或另存一份不加密的副本）后重新上传": { en: "The file is password-protected or contains encrypted content, so the security check cannot look inside it and it was not accepted. Remove the password (or save an unencrypted copy) and upload it again.", "zh-Hant": "檔案設有密碼或含有加密內容，安全檢查無法查看其中的內容，因此未被接收；請移除密碼（或另存一份不加密的副本）後重新上傳" },
  "文件的结构无法安全地完成检查（可能已损坏、被截断或结构异常），因此未被接纳；如果它没有设密码，请用生成它的程序重新保存（或拆分成较小的文件）后重新上传": { en: "The file's structure could not be checked safely (it may be damaged, cut short or built in an unusual way), so it was not accepted. If it is not password-protected, save it again from the program that made it (or split it into smaller files) and upload it again.", "zh-Hant": "檔案的結構無法安全地完成檢查（可能已損壞、被截斷或結構異常），因此未被接收；如果它沒有設密碼，請用產生它的程式重新儲存（或拆分成較小的檔案）後重新上傳" },
  "文件「{filename}」被判定为恶意内容，已拒绝": { en: "“{filename}” was identified as malicious and was refused.", "zh-Hant": "檔案「{filename}」被判定為惡意內容，已拒絕" },
  "最多只能登记 {n} 个安全密钥": { en: "You can register at most {n} security keys.", "zh-Hant": "最多只能登記 {n} 個安全金鑰" },
  "一次最多处理 {n} 项": { en: "At most {n} items can be processed at once.", "zh-Hant": "一次最多處理 {n} 項" },
  "最多 {n} 个设备，请先移除不用的设备": { en: "At most {n} devices. Remove a device you no longer use first.", "zh-Hant": "最多 {n} 個裝置，請先移除不用的裝置" },
  "一个组织最多 {n} 个自定义角色": { en: "An organization can have at most {n} custom roles.", "zh-Hant": "一個組織最多 {n} 個自訂角色" },
  "请写明理由（至少 {n} 个字），它会写入审计记录": { en: "Give a reason (at least {n} characters); it is written to the audit log.", "zh-Hant": "請寫明理由（至少 {n} 個字），它會寫入稽核記錄" },

  // ---- worded by code (lib/refusals.ts REFUSAL_SOURCES)
  "邮件链接地址尚未配置，请联系部署管理员。": { en: "The address used in email links is not configured yet. Contact the deployment administrator.", "zh-Hant": "郵件連結位址尚未設定，請聯絡部署管理員。" },
  "邮件服务尚未配置，请联系管理员。": { en: "Email is not configured yet. Contact your administrator.", "zh-Hant": "郵件服務尚未設定，請聯絡管理員。" },
  "邮件服务配置无效，请联系部署管理员。": { en: "The email configuration is invalid. Contact the deployment administrator.", "zh-Hant": "郵件服務設定無效，請聯絡部署管理員。" },
  "安全密钥登记失败，请重试。": { en: "The security key could not be registered. Try again.", "zh-Hant": "安全金鑰登記失敗，請重試。" },
  "安全密钥验证失败，请重试。": { en: "The security key could not be verified. Try again.", "zh-Hant": "安全金鑰驗證失敗，請重試。" },
  "安全密钥验证已过期，请重新开始。": { en: "The security key check has expired. Start again.", "zh-Hant": "安全金鑰驗證已過期，請重新開始。" },
  "所选的组织角色已不存在，请刷新后重新选择。": { en: "A chosen organization role no longer exists. Refresh and choose again.", "zh-Hant": "所選的組織角色已不存在，請重新整理後重新選擇。" },
  "限制值必须是 {min} 至 {max} 之间的整数，留空表示继承": { en: "A limit must be a whole number from {min} to {max}; leave it empty to inherit.", "zh-Hant": "限制值必須是 {min} 至 {max} 之間的整數，留空表示繼承" },
  "只能冻结状态正常的工作空间，请刷新后再试。": { en: "Only an active workspace can be frozen. Refresh and try again.", "zh-Hant": "只能凍結狀態正常的工作空間，請重新整理後再試。" },
};

registerMessages(messages);

registerMessages({
  "引导令牌有效期须为 60 至 3600 秒": { en: "The bootstrap token must be valid for 60 to 3600 seconds.", "zh-Hant": "引導權杖有效期須為 60 至 3600 秒" },
});
