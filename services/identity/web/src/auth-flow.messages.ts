import { registerMessages } from "@synwevo/ui";

export const messages = {
  "无法继续邮箱验证": { en: "Unable to continue email verification", "zh-Hant": "無法繼續電郵驗證" },
  "无法保存此验证链接。请先登录，再重新打开邮件链接。": { en: "This verification link could not be saved. Sign in first, then reopen the email link.", "zh-Hant": "無法儲存此驗證連結。請先登入，再重新開啟郵件連結。" },
  "请设置由你掌控的新口令以完成账户激活": { en: "Set a new password that you control to activate this account.", "zh-Hant": "請設定由你掌控的新密碼以完成帳戶啟用" },
  "已激活账户的邮箱验证不会修改口令": { en: "Verifying an active account’s email does not change its password.", "zh-Hant": "已啟用帳戶的電郵驗證不會修改密碼" },
  "设置新口令并验证邮箱": { en: "Set new password and verify email", "zh-Hant": "設定新密碼並驗證電郵" },
  "这个邀请已经被拒绝，请联系邀请人重新发送": { en: "This invitation was declined. Ask the sender for a new invitation.", "zh-Hant": "此邀請已被拒絕，請聯絡邀請人重新傳送" },
  "邀请已拒绝": {"en": "Invitation declined", "zh-Hant": "邀請已拒絕"},
  "你没有加入这个工作空间。若改变主意，请让邀请人重新发送邀请。": {"en": "You have not joined this workspace. If you change your mind, ask the sender for a new invitation.", "zh-Hant": "你沒有加入此工作空間。若改變主意，請讓邀請人重新傳送邀請。"},
  "拒绝邀请": {"en": "Decline invitation", "zh-Hant": "拒絕邀請"},
  "拒绝后，此邀请链接将失效，你不会加入「{workspace}」。": {"en": "Declining invalidates this invitation. You will not join “{workspace}”.", "zh-Hant": "拒絕後，此邀請連結將失效，你不會加入「{workspace}」。"},
  "保留邀请": {"en": "Keep invitation", "zh-Hant": "保留邀請"},
  "确认拒绝邀请": {"en": "Confirm decline", "zh-Hant": "確認拒絕邀請"},
  "返回普通登录": { en: "Back to ordinary sign-in", "zh-Hant": "返回一般登入" },
  "设置此部署的首位管理员": { en: "Set up this deployment’s first administrator", "zh-Hant": "設定此部署的首位管理員" },
  "验证码不正确，还可尝试 {count} 次。": { en: "Incorrect code. {count} attempts remaining.", "zh-Hant": "驗證碼不正確，還可嘗試 {count} 次。" },
  "验证码不正确，请重新登录。": { en: "Incorrect code. Please sign in again.", "zh-Hant": "驗證碼不正確，請重新登入。" },
  "此账户需要设备验证，请使用 PIN、指纹或面容，或改用口令和验证码登录。": { en: "This account requires device verification. Use a PIN, fingerprint or face, or sign in with a password and verification code.", "zh-Hant": "此帳戶需要裝置驗證，請使用 PIN、指紋或面容，或改用密碼和驗證碼登入。" },
  "无法连接服务器，请检查网络后重试。": {
    "en": "Unable to connect. Check your connection and try again.",
    "zh-Hant": "無法連接伺服器，請檢查網絡後重試。"
  },
  "邮箱或口令不正确。": {
    "en": "The email or password is incorrect.",
    "zh-Hant": "電郵或密碼不正確。"
  },
  "请先登录后继续。": {
    "en": "Sign in to continue.",
    "zh-Hant": "請先登入後繼續。"
  },
  "验证码不正确，请重试。": {
    "en": "The verification code is incorrect. Try again.",
    "zh-Hant": "驗證碼不正確，請重試。"
  },
  "这个口令不符合要求，请使用更长且不包含个人信息的口令。": {
    "en": "Choose a longer password that does not contain personal information.",
    "zh-Hant": "這個密碼不符合要求，請使用更長且不包含個人資料的密碼。"
  },
  "请输入有效的邮箱地址。": {
    "en": "Enter a valid email address.",
    "zh-Hant": "請輸入有效的電郵地址。"
  },
  "此服务暂未开放注册。": {
    "en": "Registration is not currently open on this service.",
    "zh-Hant": "此服務暫未開放註冊。"
  },
  "请先验证邮箱后继续。": {
    "en": "Verify your email before continuing.",
    "zh-Hant": "請先驗證電郵後繼續。"
  },
  "当前登录账户与邀请邮箱不一致，请退出后重试。": {
    "en": "This invitation is for a different email. Sign out and try again.",
    "zh-Hant": "目前登入的帳戶與邀請電郵不一致，請登出後重試。"
  },
  "邀请链接无效，请使用最新的邀请邮件。": {
    "en": "This invitation is invalid. Use the latest invitation email.",
    "zh-Hant": "邀請連結無效，請使用最新的邀請郵件。"
  },
  "邀请已过期，请让邀请人重新发送。": {
    "en": "This invitation has expired. Ask the sender for a new one.",
    "zh-Hant": "邀請已過期，請讓邀請人重新傳送。"
  },
  "邀请已被撤销，请使用最新的邀请邮件。": {
    "en": "This invitation was revoked. Use the latest invitation email.",
    "zh-Hant": "邀請已被撤銷，請使用最新的邀請郵件。"
  },
  "此邀请已被接受。": {
    "en": "This invitation has already been accepted.",
    "zh-Hant": "此邀請已被接受。"
  },
  "链接无效或已过期，请重新申请。": {
    "en": "This link is invalid or expired. Request a new one.",
    "zh-Hant": "連結無效或已過期，請重新申請。"
  },
  "你没有执行此操作的权限。": {
    "en": "You do not have permission to perform this action.",
    "zh-Hant": "你沒有執行此操作的權限。"
  },
  "找不到此内容，或你没有访问权限。": {
    "en": "This content was not found, or you do not have access.",
    "zh-Hant": "找不到此內容，或你沒有存取權限。"
  },
  "尝试过于频繁，请稍后再试。": {
    "en": "Too many attempts. Please try again later.",
    "zh-Hant": "嘗試過於頻繁，請稍後再試。"
  },
  "请在 {seconds} 秒后重试。": {
    "en": "Try again in {seconds} seconds.",
    "zh-Hant": "請在 {seconds} 秒後重試。"
  },
  "操作失败，请重试。": {
    "en": "The action failed. Please try again.",
    "zh-Hant": "操作失敗，請重試。"
  },
  "请求编号：{id}": {
    "en": "Request ID: {id}",
    "zh-Hant": "請求編號：{id}"
  },
  "口令至少需要 15 个字符。": {
    "en": "Use at least 15 characters for your password.",
    "zh-Hant": "密碼至少需要 15 個字元。"
  },
  "口令最多可以有 1024 个字符。": {
    "en": "Passwords can contain up to 1,024 characters.",
    "zh-Hant": "密碼最多可以有 1024 個字元。"
  },
  "这个口令在常见泄露口令名单里，请换一个。": {
    "en": "This password appears in a list of commonly leaked passwords. Choose another.",
    "zh-Hant": "這個密碼在常見外洩密碼名單中，請換一個。"
  },
  "这个口令重复度太高，请换一个。": {
    "en": "This password is too repetitive. Choose another.",
    "zh-Hant": "這個密碼重複度太高，請換一個。"
  },
  "口令不能包含本服务的名称，请换一个。": {
    "en": "Your password cannot include the service name. Choose another.",
    "zh-Hant": "密碼不能包含本服務的名稱，請換一個。"
  },
  "口令不能包含你的邮箱或姓名，请换一个。": {
    "en": "Your password cannot include your email or name. Choose another.",
    "zh-Hant": "密碼不能包含你的電郵或姓名，請換一個。"
  },
  "口令": {
    "en": "Password",
    "zh-Hant": "密碼"
  },
  "隐藏口令": {
    "en": "Hide password",
    "zh-Hant": "隱藏密碼"
  },
  "显示口令": {
    "en": "Show password",
    "zh-Hant": "顯示密碼"
  },
  "隐藏": {
    "en": "Hide",
    "zh-Hant": "隱藏"
  },
  "显示": {
    "en": "Show",
    "zh-Hant": "顯示"
  },
  "至少 15 个字符。使用一句容易记住的长句子。": {
    "en": "At least 15 characters. Try a long phrase you can remember.",
    "zh-Hant": "至少 15 個字元。使用一句容易記住的長句子。"
  },
  "加载中…": {
    "en": "Loading…",
    "zh-Hant": "載入中…"
  },
  "没有收到安全密钥的响应。": {
    "en": "No response was received from the security key.",
    "zh-Hant": "沒有收到安全金鑰的回應。"
  },
  "已取消，或没有可用于本站的安全密钥。": {
    "en": "Cancelled, or no security key is available for this site.",
    "zh-Hant": "已取消，或沒有可用於本站的安全金鑰。"
  },
  "两步验证": {
    "en": "Two-step verification",
    "zh-Hant": "兩步驗證"
  },
  "建立首位管理员": {
    "en": "Set up the first administrator",
    "zh-Hant": "建立首位管理員"
  },
  "输入一个恢复码。每个恢复码只能用一次。": {
    "en": "Enter a recovery code. Each code can be used once.",
    "zh-Hant": "輸入一個復原碼。每個復原碼只能使用一次。"
  },
  "打开验证器应用，输入其中显示的 6 位数字。": {
    "en": "Open your authenticator app and enter its 6-digit code.",
    "zh-Hant": "開啟驗證器應用程式，輸入其中顯示的 6 位數字。"
  },
  "重试": {
    "en": "Try again",
    "zh-Hant": "重試"
  },
  "正在前往…": {
    "en": "Redirecting…",
    "zh-Hant": "正在前往…"
  },
  "恢复码": {
    "en": "Recovery code",
    "zh-Hant": "復原碼"
  },
  "验证码": {
    "en": "Verification code",
    "zh-Hant": "驗證碼"
  },
  "验证中…": {
    "en": "Verifying…",
    "zh-Hant": "驗證中…"
  },
  "验证并登录": {
    "en": "Verify and sign in",
    "zh-Hant": "驗證並登入"
  },
  "改用验证器应用": {
    "en": "Use authenticator app",
    "zh-Hant": "改用驗證器應用程式"
  },
  "使用恢复码": {
    "en": "Use a recovery code",
    "zh-Hant": "使用復原碼"
  },
  "返回，换个账户登录": {
    "en": "Go back and use another account",
    "zh-Hant": "返回，換個帳戶登入"
  },
  "一次性引导令牌": {
    "en": "One-time bootstrap token",
    "zh-Hant": "一次性引導權杖"
  },
  "邮箱": {
    "en": "Email",
    "zh-Hant": "電郵"
  },
  "显示名称（可选）": {
    "en": "Display name (optional)",
    "zh-Hant": "顯示名稱（選填）"
  },
  "忘记口令？": {
    "en": "Forgot password?",
    "zh-Hant": "忘記密碼？"
  },
  "处理中…": {
    "en": "Working…",
    "zh-Hant": "處理中…"
  },
  "创建管理员并登录": {
    "en": "Create administrator and sign in",
    "zh-Hant": "建立管理員並登入"
  },
  "登录": {
    "en": "Sign in",
    "zh-Hant": "登入"
  },
  "使用安全密钥登录": {
    "en": "Sign in with a security key",
    "zh-Hant": "使用安全金鑰登入"
  },
  "还没有账户？": {
    "en": "New to Synwevo?",
    "zh-Hant": "還沒有帳戶？"
  },
  "创建账户": {
    "en": "Create an account",
    "zh-Hant": "建立帳戶"
  },
  "本服务目前为邀请制，请使用邀请邮件创建账户。": {
    "en": "This service is invitation-only. Use your invitation email to create an account.",
    "zh-Hant": "本服務目前採邀請制，請使用邀請郵件建立帳戶。"
  },
  "返回邀请": {
    "en": "Back to invitation",
    "zh-Hant": "返回邀請"
  },
  "返回登录": {
    "en": "Back to sign in",
    "zh-Hant": "返回登入"
  },
  "你正在接受「{workspace}」的邀请。": {
    "en": "You are joining “{workspace}” by invitation.",
    "zh-Hant": "你正在接受「{workspace}」的邀請。"
  },
  "工作空间": {
    "en": "Workspace",
    "zh-Hant": "工作空間"
  },
  "显示名称": {
    "en": "Display name",
    "zh-Hant": "顯示名稱"
  },
  "邮箱由邀请决定，无法在这里修改。": {
    "en": "This email is set by the invitation and cannot be changed here.",
    "zh-Hant": "電郵由邀請決定，無法在這裡修改。"
  },
  "你希望如何开始？": {
    "en": "How would you like to start?",
    "zh-Hant": "你希望如何開始？"
  },
  "个人使用": {
    "en": "For myself",
    "zh-Hant": "個人使用"
  },
  "团队协作": {
    "en": "With a team",
    "zh-Hant": "團隊協作"
  },
  "创建组织，再邀请团队成员。": {
    "en": "Create an organization and invite your team.",
    "zh-Hant": "建立組織，再邀請團隊成員。"
  },
  "提交中…": {
    "en": "Submitting…",
    "zh-Hant": "提交中…"
  },
  "创建账户并验证邮箱": {
    "en": "Create account and verify email",
    "zh-Hant": "建立帳戶並驗證電郵"
  },
  "已有账户？": {
    "en": "Already have an account?",
    "zh-Hant": "已有帳戶？"
  },
  "邮箱已验证": {
    "en": "Email verified",
    "zh-Hant": "電郵已驗證"
  },
  "验证你的邮箱": {
    "en": "Verify your email",
    "zh-Hant": "驗證你的電郵"
  },
  "邮件由 {sender} 发出，通常几分钟内送达；找不到时可按发件地址搜索。": { en: "The mail comes from {sender} and usually arrives within minutes; search for that sender if you cannot find it.", "zh-Hant": "郵件由 {sender} 發出，通常幾分鐘內送達；找不到時可按寄件地址搜尋。" },
  "每小时最多发送 3 封验证邮件，只有最新一封有效；链接 24 小时内有效。": { en: "At most 3 verification mails are sent per hour and only the latest link works; each link is valid for 24 hours.", "zh-Hant": "每小時最多寄送 3 封驗證郵件，只有最新一封有效；連結 24 小時內有效。" },
  "收不到邮件？换一个邮箱注册": { en: "Not receiving mail? Register with another address", "zh-Hant": "收不到郵件？換一個信箱註冊" },
  "继续": {
    "en": "Continue",
    "zh-Hant": "繼續"
  },
  "登录并继续": {
    "en": "Sign in and continue",
    "zh-Hant": "登入並繼續"
  },
  "验证此邮箱": {
    "en": "Verify this email",
    "zh-Hant": "驗證此電郵"
  },
  "邮件服务暂不可用。请稍后重新发送验证邮件，或联系服务管理员。": {
    "en": "Email delivery is temporarily unavailable. Try resending later or contact the service administrator.",
    "zh-Hant": "郵件服務暫不可用。請稍後重新傳送驗證郵件，或聯絡服務管理員。"
  },
  "重新发送验证邮件": {
    "en": "Resend verification email",
    "zh-Hant": "重新傳送驗證郵件"
  },
  "这个邀请已经用过了": {
    "en": "This invitation has already been used",
    "zh-Hant": "這個邀請已經使用過了"
  },
  "如果你已经加入，请直接登录查看工作空间。": {
    "en": "If you have already joined, sign in to view the workspace.",
    "zh-Hant": "如果你已經加入，請直接登入查看工作空間。"
  },
  "这个邀请已被撤销": {
    "en": "This invitation was revoked",
    "zh-Hant": "這個邀請已被撤銷"
  },
  "邀请人取消了这次邀请。请使用最新的邀请邮件。": {
    "en": "The sender cancelled this invitation. Use the latest invitation email.",
    "zh-Hant": "邀請人取消了這次邀請。請使用最新的邀請郵件。"
  },
  "这个邀请已过期": {
    "en": "This invitation has expired",
    "zh-Hant": "這個邀請已過期"
  },
  "邀请链接有有效期，请让邀请人重新发送。": {
    "en": "Invitation links expire. Ask the sender for a new one.",
    "zh-Hant": "邀請連結有有效期，請讓邀請人重新傳送。"
  },
  "这个链接缺少邀请令牌，请使用邮件里的完整链接。": {
    "en": "This link is missing its invitation token. Use the full link from the email.",
    "zh-Hant": "這個連結缺少邀請權杖，請使用郵件中的完整連結。"
  },
  "所有者": {
    "en": "Owner",
    "zh-Hant": "擁有者"
  },
  "管理员": {
    "en": "Administrator",
    "zh-Hant": "管理員"
  },
  "编辑者": {
    "en": "Editor",
    "zh-Hant": "編輯者"
  },
  "查看者": {
    "en": "Viewer",
    "zh-Hant": "檢視者"
  },
  "成员": {
    "en": "Member",
    "zh-Hant": "成員"
  },
  "邀请链接无法打开": {
    "en": "Unable to open invitation",
    "zh-Hant": "邀請連結無法開啟"
  },
  "前往登录": {
    "en": "Go to sign in",
    "zh-Hant": "前往登入"
  },
  "邀请邮箱": {
    "en": "Invited email",
    "zh-Hant": "受邀電郵"
  },
  "你的角色": {
    "en": "Your role",
    "zh-Hant": "你的角色"
  },
  "邀请人": {
    "en": "Invited by",
    "zh-Hant": "邀請人"
  },
  "有效期至": {
    "en": "Expires on",
    "zh-Hant": "有效期至"
  },
  "你当前登录的是 {current}，这个邀请是发给 {email} 的。": {
    "en": "You are signed in as {current}. This invitation is for {email}.",
    "zh-Hant": "你目前登入的是 {current}，這個邀請是寄給 {email} 的。"
  },
  "退出当前账户": {
    "en": "Sign out of this account",
    "zh-Hant": "登出目前帳戶"
  },
  "创建账户并继续": {
    "en": "Create account and continue",
    "zh-Hant": "建立帳戶並繼續"
  },
  "已有账户？登录并继续": {
    "en": "Already have an account? Sign in to continue",
    "zh-Hant": "已有帳戶？登入並繼續"
  },
  "请使用受邀邮箱登录，然后回来确认加入。": {
    "en": "Sign in with the invited email, then return to confirm you want to join.",
    "zh-Hant": "請使用受邀電郵登入，然後回來確認加入。"
  },
  "先验证邮箱": {
    "en": "Verify email first",
    "zh-Hant": "先驗證電郵"
  },
  "加入中…": {
    "en": "Joining…",
    "zh-Hant": "加入中…"
  },
  "接受邀请并加入": {
    "en": "Accept invitation and join",
    "zh-Hant": "接受邀請並加入"
  },
  "看看你的邮箱": {
    "en": "Check your inbox",
    "zh-Hant": "看看你的電郵"
  },
  "如果这个邮箱有可用账户且邮件服务正常，重置请求将进入发送队列。": {
    "en": "If this email has an available account and email delivery is working, a reset request will be queued.",
    "zh-Hant": "如果此電郵有可用帳戶且郵件服務正常，重設請求將進入傳送佇列。"
  },
  "链接 2 小时内有效，只能用一次。没收到邮件？请检查垃圾邮件或确认邮箱地址。": {
    "en": "The link is valid for 2 hours and can be used once. No email? Check your spam folder and the email address.",
    "zh-Hant": "連結 2 小時內有效，只能使用一次。沒收到郵件？請檢查垃圾郵件或確認電郵地址。"
  },
  "换个邮箱或重新申请": {
    "en": "Use another email or request again",
    "zh-Hant": "換個電郵或重新申請"
  },
  "发送重置链接": {
    "en": "Send reset link",
    "zh-Hant": "傳送重設連結"
  },
  "口令已更新": {
    "en": "Password updated",
    "zh-Hant": "密碼已更新"
  },
  "设置新口令": {
    "en": "Set a new password",
    "zh-Hant": "設定新密碼"
  },
  "所有设备上的登录都已结束，请使用新口令重新登录。": {
    "en": "All device sessions have ended. Sign in again with your new password.",
    "zh-Hant": "所有裝置上的登入都已結束，請使用新密碼重新登入。"
  },
  "两步验证保持启用，下次登录仍然需要它。": {
    "en": "Two-step verification is still enabled and will be required at your next sign-in.",
    "zh-Hant": "兩步驗證保持啟用，下次登入仍然需要它。"
  },
  "去登录": {
    "en": "Sign in",
    "zh-Hant": "去登入"
  },
  "重新申请一封": {
    "en": "Request a new email",
    "zh-Hant": "重新申請一封"
  },
  "新口令": {
    "en": "New password",
    "zh-Hant": "新密碼"
  },
  "设置新口令并结束所有登录": {
    "en": "Set password and end all sessions",
    "zh-Hant": "設定新密碼並結束所有登入"
  },
  "接受邀请，创建账户": {
    "en": "Accept invitation, create account",
    "zh-Hant": "接受邀請，建立帳戶"
  },
  "已加入工作空间。": {
    "en": "You have joined the workspace.",
    "zh-Hant": "已加入工作空間。"
  },
  "创建账户并接受邀请": {
    "en": "Create account and accept invitation",
    "zh-Hant": "建立帳戶並接受邀請"
  },
  "不接受此邀请，返回注册": {
    "en": "Continue registration without this invitation",
    "zh-Hant": "不接受此邀請，返回註冊"
  },
  "请填写引导令牌、邮箱与口令": {
    "en": "Enter the bootstrap token, email and password.",
    "zh-Hant": "請填寫引導權杖、電郵與密碼"
  },
  "本服务已经有平台管理员了": {
    "en": "This service already has a platform administrator.",
    "zh-Hant": "本服務已經有平台管理員了"
  },
  "引导令牌无效或已过期，请重新生成": {
    "en": "The bootstrap token is invalid or expired. Generate a new one.",
    "zh-Hant": "引導權杖無效或已過期，請重新產生"
  },
  "口令错误次数过多，这个登录已结束，请重新登录": {
    "en": "Too many incorrect passwords. This session has ended; sign in again.",
    "zh-Hant": "密碼錯誤次數過多，此登入已結束，請重新登入"
  },
  "注册请求包含不支持的字段": {
    "en": "The registration request contains unsupported fields.",
    "zh-Hant": "註冊請求包含不支援的欄位"
  },
  "语言或首次使用方式无效": {
    "en": "The language or onboarding choice is invalid.",
    "zh-Hant": "語言或首次使用方式無效"
  },
  "显示名称不得超过 100 个字符": {
    "en": "Display names must not exceed 100 characters.",
    "zh-Hant": "顯示名稱不得超過 100 個字元"
  },
  "邮箱格式不正确": {
    "en": "Enter a valid email address.",
    "zh-Hant": "電郵格式不正確"
  },
  "账户不可用，请联系管理员": {
    "en": "This account is unavailable. Contact the administrator.",
    "zh-Hant": "帳戶不可用，請聯絡管理員"
  },
  "会话已失效，请重新登录": {
    "en": "Your session has expired. Sign in again.",
    "zh-Hant": "工作階段已失效，請重新登入"
  },
  "已取消外部登录。": {
    "en": "External sign-in was cancelled.",
    "zh-Hant": "已取消外部登入。"
  },
  "外部身份提供商拒绝了这次登录，请重试。": {
    "en": "The external identity provider refused this sign-in. Please try again.",
    "zh-Hant": "外部身分提供者拒絕了這次登入，請重試。"
  },
  "登录请求已过期或已使用，请重新开始。": {
    "en": "This sign-in request has expired or was already used. Please start again.",
    "zh-Hant": "登入請求已過期或已使用，請重新開始。"
  },
  "外部身份提供商返回的身份信息无法验证，请重试。": {
    "en": "The identity returned by the external provider could not be verified. Please try again.",
    "zh-Hant": "外部身分提供者回傳的身分資訊無法驗證，請重試。"
  },
  "外部身份提供商未接受这次登录，请重试。": {
    "en": "The external identity provider did not accept this sign-in. Please try again.",
    "zh-Hant": "外部身分提供者未接受這次登入，請重試。"
  },
  "暂时无法连接外部身份提供商，请稍后再试。": {
    "en": "The external identity provider cannot be reached right now. Please try again later.",
    "zh-Hant": "暫時無法連線外部身分提供者，請稍後再試。"
  },
  "该提供商没有验证你的邮箱，无法用它创建或匹配账户。请用口令登录后，在「账户与安全」里关联。": {
    "en": "This provider has not verified your email address, so it cannot create or match an account. Sign in with your password and link it under Account & security.",
    "zh-Hant": "此提供者未驗證你的電子郵件地址，無法用它建立或比對帳戶。請以密碼登入後，在「帳戶與安全」中關聯。"
  },
  "这个邮箱已有账户，请先用口令登录，再在「账户与安全」里关联。": {
    "en": "An account already uses this email address. Sign in with your password first, then link the provider under Account & security.",
    "zh-Hant": "此電子郵件地址已有帳戶，請先以密碼登入，再在「帳戶與安全」中關聯。"
  },
  "账户不可用，请联系管理员。": {
    "en": "This account is unavailable. Contact an administrator.",
    "zh-Hant": "帳戶無法使用，請聯絡管理員。"
  },
  "平台管理员需先启用动态验证码，才能使用外部登录。": {
    "en": "Platform administrators must enable authenticator codes before signing in through an external provider.",
    "zh-Hant": "平台管理員需先啟用驗證器動態驗證碼，才能使用外部登入。"
  },
  "关联登录方式时登录状态发生了变化，请重新登录后再试。": {
    "en": "Your sign-in changed while the provider was being linked. Sign in again and retry.",
    "zh-Hant": "關聯登入方式時登入狀態發生變化，請重新登入後再試。"
  },
  "这个外部账户已关联到另一个 Synwevo 账户。": {
    "en": "This external account is already linked to another Synwevo account.",
    "zh-Hant": "此外部帳戶已關聯到另一個 Synwevo 帳戶。"
  },
  "未启用这个外部身份提供商。": {
    "en": "This external identity provider is not enabled.",
    "zh-Hant": "未啟用此外部身分提供者。"
  },
  "外部登录失败，请重试。": {
    "en": "External sign-in failed. Please try again.",
    "zh-Hant": "外部登入失敗，請重試。"
  },
  "邮件服务尚未配置，请改用口令或安全密钥登录。": {
    "en": "Email is not configured on this service. Sign in with your password or a passkey instead.",
    "zh-Hant": "此服務尚未設定電子郵件，請改用密碼或安全金鑰登入。"
  },
  "关联外部登录": {
    "en": "Link external sign-in",
    "zh-Hant": "關聯外部登入"
  },
  "用邮箱验证码登录": {
    "en": "Sign in with an email code",
    "zh-Hant": "以電子郵件驗證碼登入"
  },
  "外部": {
    "en": "external",
    "zh-Hant": "外部"
  },
  "验证码已发送到你的邮箱（如果该邮箱有账户），请在 10 分钟内输入。": {
    "en": "If this address has an account, a code has been sent to it. Enter it within 10 minutes.",
    "zh-Hant": "若此電子郵件地址有帳戶，驗證碼已寄出，請在 10 分鐘內輸入。"
  },
  "在此设备上保持登录": {
    "en": "Keep me signed in on this device",
    "zh-Hant": "在此裝置上保持登入"
  },
  "最长 14 天不活动仍保持登录；公共或共享设备请勿勾选。": {
    "en": "Stays signed in for up to 14 days without activity. Leave unchecked on public or shared devices.",
    "zh-Hant": "最長 14 天未使用仍保持登入；公用或共用裝置請勿勾選。"
  },
  "这个账户还没有口令。请先通过邮箱验证码或安全密钥登录，再在「账户与安全」里关联。": {
    "en": "This account has no password yet. Sign in with an email code or a passkey first, then link the provider under Account & security.",
    "zh-Hant": "此帳戶尚未設定密碼。請先以電子郵件驗證碼或安全金鑰登入，再在「帳戶與安全」中關聯。"
  },
  "关联并登录": {
    "en": "Link and sign in",
    "zh-Hant": "關聯並登入"
  },
  "不关联，返回登录": {
    "en": "Don't link, back to sign-in",
    "zh-Hant": "不關聯，返回登入"
  },
  "邮箱验证码": {
    "en": "Email code",
    "zh-Hant": "電子郵件驗證碼"
  },
  "重新发送或换个邮箱": {
    "en": "Resend or use another address",
    "zh-Hant": "重新傳送或更換地址"
  },
  "发送中…": {
    "en": "Sending…",
    "zh-Hant": "傳送中…"
  },
  "发送验证码": {
    "en": "Send code",
    "zh-Hant": "傳送驗證碼"
  },
  "改用口令登录": {
    "en": "Sign in with password instead",
    "zh-Hant": "改用密碼登入"
  },
  "其他登录方式": {
    "en": "Other ways to sign in",
    "zh-Hant": "其他登入方式"
  },
  "使用 {label} 登录": {
    "en": "Sign in with {label}",
    "zh-Hant": "以 {label} 登入"
  },
  "人机验证": { en: "Human verification", "zh-Hant": "人機驗證" },
  "正在加载人机验证…": { en: "Loading human verification…", "zh-Hant": "正在載入人機驗證…" },
  "人机验证已过期，正在刷新。": { en: "The verification expired and is being refreshed.", "zh-Hant": "人機驗證已過期，正在重新整理。" },
  "人机验证无法加载或未完成。请检查网络或浏览器的内容拦截设置后重试。": { en: "Human verification could not load or was not completed. Check your network or your browser’s content blocking, then try again.", "zh-Hant": "人機驗證無法載入或未完成。請檢查網路或瀏覽器的內容攔截設定後重試。" },
  "请先完成人机验证。": { en: "Complete the human verification first.", "zh-Hant": "請先完成人機驗證。" },
  "请先完成人机验证": { en: "Complete the human verification first", "zh-Hant": "請先完成人機驗證" },
  "人机验证未通过，请重试。": { en: "Human verification did not pass. Please try again.", "zh-Hant": "人機驗證未通過，請重試。" },
  "人机验证未通过，请重试": { en: "Human verification did not pass. Please try again", "zh-Hant": "人機驗證未通過，請重試" },
  "人机验证服务暂不可用，请稍后重试。": { en: "The human verification service is temporarily unavailable. Please try again later.", "zh-Hant": "人機驗證服務暫不可用，請稍後重試。" },
  "人机验证服务暂不可用，请稍后重试": { en: "The human verification service is temporarily unavailable. Please try again later", "zh-Hant": "人機驗證服務暫不可用，請稍後重試" },
  "登录一次，回到你的工作空间。": { en: "Sign in once and return to your workspace.", "zh-Hant": "登入一次，回到你的工作空間。" },
  "同一个账户进入你所属的组织与工作空间；登录方式、设备与通知都在这里管理。": { en: "One account for the organizations and workspaces you belong to; sign-in methods, devices and notifications are managed here.", "zh-Hant": "同一個帳戶進入你所屬的組織與工作空間；登入方式、裝置與通知都在這裡管理。" },
  "账户 · 工作空间 · 组织": { en: "Account · Workspace · Organization", "zh-Hant": "帳戶 · 工作空間 · 組織" },
  "源代码（AGPL-3.0）": { en: "Source code (AGPL-3.0)", "zh-Hant": "原始碼（AGPL-3.0）" },
  "下载正在运行版本的对应源代码": { en: "Download the Corresponding Source of the running version", "zh-Hant": "下載正在執行版本的對應原始碼" },
  "验证已超时，请重新登录。": { en: "Verification timed out. Sign in again.", "zh-Hant": "驗證已逾時，請重新登入。" },
  "这次登录无法验证，请重新开始。": { en: "This sign-in could not be verified. Please start again.", "zh-Hant": "這次登入無法驗證，請重新開始。" },
  "已取消，或此设备上没有可用于本站的安全密钥。平台域名已更换，在旧域名登记的安全密钥不能在这里使用：请先用口令或邮箱验证码登录，再在「账户与安全」重新登记。": { en: "Cancelled, or this device has no security key for this site. The platform moved to a new domain and keys registered on the old one cannot be used here: sign in with your password or an e-mail code, then register a new key under Account & security.", "zh-Hant": "已取消，或此裝置上沒有可用於本站的安全金鑰。平台網域已更換，在舊網域登記的安全金鑰不能在這裡使用：請先用密碼或電子郵件驗證碼登入，再到「帳戶與安全」重新登記。" },
  "这个 {label} 账户的邮箱已有 Synwevo 账户。输入该账户的口令即可关联并登录；之后可以直接用 {label} 登录。": { en: "The e-mail address of this {label} account already has a Synwevo account. Enter that account's password to link and sign in; afterwards you can sign in with {label} directly.", "zh-Hant": "此 {label} 帳戶的電子郵件地址已有 Synwevo 帳戶。輸入該帳戶的密碼即可關聯並登入；之後可直接以 {label} 登入。" },
  "需要服务器生成的一次性引导令牌。": { en: "Requires the one-time bootstrap token generated on the server.", "zh-Hant": "需要伺服器產生的一次性引導權杖。" },
  "验证码会发到你的账户邮箱，10 分钟内有效。": { en: "A code will be sent to your account's e-mail address; it is valid for 10 minutes.", "zh-Hant": "驗證碼會寄到你的帳戶電郵，10 分鐘內有效。" },
  "初始化这个部署需要服务器生成的一次性令牌；已有账户照常登录。": { en: "Setting up this deployment needs a one-time token generated on the server; existing accounts sign in as usual.", "zh-Hant": "初始化此部署需要伺服器產生的一次性權杖；已有帳戶照常登入。" },
  "受邀邮箱无需再验证；创建后直接加入工作空间。": { en: "The invited address needs no further verification; you join the workspace as soon as the account is created.", "zh-Hant": "受邀電郵無需再驗證；建立後直接加入工作空間。" },
  "创建后需要先验证邮箱。": { en: "You will need to verify your e-mail address after creating the account.", "zh-Hant": "建立後需要先驗證電郵。" },
  "建立自己的工作空间，整理文档与项目。": { en: "Set up your own workspace for documents and projects.", "zh-Hant": "建立自己的工作空間，整理文件與專案。" },
  "验证完成前，账户无法进入工作空间。": { en: "Until verified, the account cannot enter a workspace.", "zh-Hant": "驗證完成前，帳戶無法進入工作空間。" },
  "请打开邮箱，使用最新一封邮件里的验证链接。": { en: "Open your inbox and use the verification link in the latest e-mail.", "zh-Hant": "請開啟電郵，使用最新一封郵件裡的驗證連結。" },
  "验证完成；新口令已设置，请用它重新登录。": { en: "Verified. Your new password is set; sign in again with it.", "zh-Hant": "驗證完成；新密碼已設定，請用它重新登入。" },
  "验证完成，可以继续了。": { en: "Verified. You can continue.", "zh-Hant": "驗證完成，可以繼續了。" },
  "验证完成，请登录后继续。": { en: "Verified. Sign in to continue.", "zh-Hant": "驗證完成，請登入後繼續。" },
  "请设置一个由你掌控的新口令。此前的口令和所有登录将失效。": { en: "Set a new password that only you control. The previous password and every sign-in will stop working.", "zh-Hant": "請設定一個由你掌控的新密碼。先前的密碼和所有登入將失效。" },
  "如果这个邮箱需要验证，新的验证邮件已发出；请检查收件箱和垃圾邮件。": { en: "If this address still needs verification, a new e-mail has been sent; check your inbox and spam folder.", "zh-Hant": "如果此電郵需要驗證，新的驗證郵件已寄出；請檢查收件匣和垃圾郵件。" },
  "学校或公司邮箱通常经过安全网关，邮件可能延迟数分钟、进入垃圾邮件或隔离区；必要时把发件地址加入允许名单。": { en: "University and company mailboxes usually sit behind a security gateway: the mail may take minutes, land in junk or be quarantined; allow-list the sender if needed.", "zh-Hant": "學校或公司信箱通常經過安全閘道，郵件可能延遲數分鐘、進入垃圾郵件或隔離區；必要時把寄件地址加入允許名單。" },
  "找回账户": { en: "Recover account", "zh-Hant": "找回帳戶" },
  "如果这个邮箱有账户，口令重置链接已发出。": { en: "If this address has an account, a password reset link has been sent.", "zh-Hant": "如果此電郵有帳戶，密碼重設連結已寄出。" },
  "输入账户邮箱，申请口令重置链接。": { en: "Enter your account e-mail to request a password reset link.", "zh-Hant": "輸入帳戶電郵，申請密碼重設連結。" },
  "新口令生效后，所有设备都需要重新登录。": { en: "Once the new password is set, every device must sign in again.", "zh-Hant": "新密碼生效後，所有裝置都需要重新登入。" },
  "工作空间邀请": { en: "Workspace invitation", "zh-Hant": "工作空間邀請" },
  "核对邀请信息后再决定是否加入；打开链接不会自动接受。": { en: "Review the invitation before deciding to join; opening the link does not accept it.", "zh-Hant": "核對邀請資料後再決定是否加入；開啟連結不會自動接受。" },
  // ---- the account service's own sentences for refusals met while signing in (CI-85)
  "外部身份提供商没有返回身份信息，请重试。": { en: "The external provider returned no identity. Please try again.", "zh-Hant": "外部身分提供者沒有回傳身分資訊，請重試。" },
  "请先完成邮箱验证并激活账户，再关联外部登录": { en: "Verify your email and activate the account before linking an external sign-in.", "zh-Hant": "請先完成電郵驗證並啟用帳戶，再關聯外部登入" },
  "不能降级最后一名所有者": { en: "The last owner cannot be given a lower role.", "zh-Hant": "不能降級最後一名擁有者" },
  "邀请人已不再具备邀请权限，这个邀请失效了。请联系空间管理员重新邀请。": { en: "The inviter can no longer invite, so this invitation is no longer valid. Ask a workspace administrator to invite you again.", "zh-Hant": "邀請人已不再具備邀請權限，這個邀請失效了。請聯絡空間管理員重新邀請。" },
  "请设置口令": { en: "Set a password.", "zh-Hant": "請設定密碼" },
  "该工作空间已不可用": { en: "This workspace is no longer available.", "zh-Hant": "該工作空間已不可用" },
  "邀请已经处理或失效": { en: "This invitation has already been answered or is no longer valid.", "zh-Hant": "邀請已經處理或失效" },
  "该账户当前不可用": { en: "This account is currently unavailable.", "zh-Hant": "該帳戶目前不可用" },
  "这个安全密钥没有登记过": { en: "This security key is not registered.", "zh-Hant": "這個安全金鑰沒有登記過" },
  "该组织或工作空间当前不可用": { en: "This organization or workspace is currently unavailable.", "zh-Hant": "該組織或工作空間目前不可用" },
  "这个邀请已经被使用过了": { en: "This invitation has already been used.", "zh-Hant": "這個邀請已經被使用過了" },
  "这个账户启用了两步验证，请一并输入验证码": { en: "This account uses two-step verification; enter the verification code as well.", "zh-Hant": "這個帳戶啟用了兩步驗證，請一併輸入驗證碼" },
  "找不到与你的账户相符的邀请": { en: "No invitation matches your account.", "zh-Hant": "找不到與你的帳戶相符的邀請" },
  "这次关联已过期，请重新开始": { en: "This linking attempt has expired. Start again.", "zh-Hant": "這次關聯已過期，請重新開始" },
  "当前不开放公开注册，请使用有效邀请": { en: "Public registration is closed. Use a valid invitation.", "zh-Hant": "目前不開放公開註冊，請使用有效邀請" },
  "该邮箱已有账户，请先登录后再打开邀请链接": { en: "This email address already has an account. Sign in first, then open the invitation link.", "zh-Hant": "此電郵已有帳戶，請先登入後再開啟邀請連結" },
  "验证已超时或失败次数过多，请重新登录": { en: "Verification timed out or failed too many times. Sign in again.", "zh-Hant": "驗證已逾時或失敗次數過多，請重新登入" },
  "验证已超时，请重新登录": { en: "Verification timed out. Sign in again.", "zh-Hant": "驗證已逾時，請重新登入" },
  "请输入有效口令": { en: "Enter a valid password.", "zh-Hant": "請輸入有效的密碼" },
  "这个邀请已过期，请让邀请人重新发送": { en: "This invitation has expired. Ask the inviter to send it again.", "zh-Hant": "這個邀請已過期，請讓邀請人重新傳送" },
  "创建账户后直接加入；受邀邮箱无需再验证。": { en: "You join as soon as the account is created; the invited address needs no further verification.", "zh-Hant": "建立帳戶後直接加入；受邀電郵無需再驗證。" },

};

registerMessages(messages);
