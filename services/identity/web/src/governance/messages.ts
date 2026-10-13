import { registerMessages } from "@synwevo/ui";

// Account governance owns these messages; source text is Simplified Chinese.
export const governanceMessages = {
  "已清除选择": { en: "Selection cleared", "zh-Hant": "已清除選取" },
  "邀请已接受、已拒绝、已撤销或已过期": { en: "Invitation accepted, declined, revoked or expired", "zh-Hant": "邀請已接受、已拒絕、已撤銷或已過期" },
  "邀请已过期，请让邀请人重新发送。": { en: "Invitation expired. Ask the inviter to send a new one.", "zh-Hant": "邀請已過期，請讓邀請人重新傳送。" },
  "邀请已被拒绝，请让邀请人重新发送。": { en: "Invitation declined. Ask the inviter to send a new one.", "zh-Hant": "邀請已被拒絕，請讓邀請人重新傳送。" },
  "已拒绝": { en: "Declined", "zh-Hant": "已拒絕" },
  "平台管理 · Synwevo Account": {
    "en": "Platform administration · Synwevo Account",
    "zh-Hant": "平台管理 · Synwevo Account"
  },
  "页面不存在": {
    "en": "Page not found",
    "zh-Hant": "頁面不存在"
  },
  "前往账户设置": {
    "en": "Go to account settings",
    "zh-Hant": "前往帳戶設置"
  },
  "平台管理": {
    "en": "Platform management",
    "zh-Hant": "平台管理"
  },
  "管理后台": {
    "en": "Administration",
    "zh-Hant": "管理後台"
  },
  "概览": {
    "en": "Overview",
    "zh-Hant": "概覽"
  },
  "账户": {
    "en": "Accounts",
    "zh-Hant": "帳戶"
  },
  "工作空间": {
    "en": "Workspaces",
    "zh-Hant": "工作空間"
  },
  "组织": {
    "en": "Organizations",
    "zh-Hant": "組織"
  },
  "注册策略": {
    "en": "Registration policy",
    "zh-Hant": "註冊策略"
  },
  "邮件投递": {
    "en": "Mail delivery",
    "zh-Hant": "郵件投遞"
  },
  "审计": {
    "en": "Audit",
    "zh-Hant": "審計"
  },
  "加载中…": {
    "en": "Loading…",
    "zh-Hant": "載入中…"
  },
  "重试": {
    "en": "Try again",
    "zh-Hant": "重試"
  },
  "平台概览": {
    "en": "Platform overview",
    "zh-Hant": "平台概覽"
  },
  "其中已停用": {
    "en": "Suspended accounts",
    "zh-Hant": "其中已停用"
  },
  "平台管理员": {
    "en": "Platform administrators",
    "zh-Hant": "平台管理員"
  },
  "待认领的历史数据": {
    "en": "Unclaimed historical data",
    "zh-Hant": "待認領的歷史資料"
  },
  "搜索接收账户": {
    "en": "Search recipient accounts",
    "zh-Hant": "搜索接收帳戶"
  },
  "正在查找账户…": {
    "en": "Finding accounts…",
    "zh-Hant": "正在查找帳戶…"
  },
  "接收账户": {
    "en": "Recipient account",
    "zh-Hant": "接收帳戶"
  },
  "没有可选的正常账户。": {
    "en": "No active accounts available.",
    "zh-Hant": "沒有可選的正常帳戶。"
  },
  "指派历史数据": {
    "en": "Assign historical data",
    "zh-Hant": "指派歷史資料"
  },
  "将把「{name}」及其中 {projects}的访问权交给{email}，角色为所有者。不会授予平台管理权限。": {
    "en": "Give {email} ownership of “{name}” and access to its {projects}. This does not grant platform administration rights.",
    "zh-Hant": "將把「{name}」及其中 {projects}的訪問權交給{email}，角色為所有者。不會授予平台管理權限。"
  },
  "发送找回邮件": {
    "en": "Send recovery email",
    "zh-Hant": "發送找回郵件"
  },
  "撤销全部会话": {
    "en": "Revoke all sessions",
    "zh-Hant": "撤銷全部工作階段"
  },
  "修改平台角色": {
    "en": "Change platform role",
    "zh-Hant": "修改平台角色"
  },
  "停用账户": {
    "en": "Suspend account",
    "zh-Hant": "停用帳戶"
  },
  "恢复账户": {
    "en": "Restore account",
    "zh-Hant": "恢復帳戶"
  },
  "搜索邮箱或姓名": {
    "en": "Search email or name",
    "zh-Hant": "搜索電子郵件地址或姓名"
  },
  "搜索账户": {
    "en": "Search accounts",
    "zh-Hant": "搜索帳戶"
  },
  "刷新账户": {
    "en": "Refresh accounts",
    "zh-Hant": "重新整理帳戶"
  },
  "账户状态": {
    "en": "Account status",
    "zh-Hant": "帳戶狀態"
  },
  "筛选账户状态": {
    "en": "Filter account status",
    "zh-Hant": "篩選帳戶狀態"
  },
  "全部状态": {
    "en": "All statuses",
    "zh-Hant": "全部狀態"
  },
  "平台角色": {
    "en": "Platform role",
    "zh-Hant": "平台角色"
  },
  "筛选平台角色": {
    "en": "Filter platform roles",
    "zh-Hant": "篩選平台角色"
  },
  "全部角色": {
    "en": "All roles",
    "zh-Hant": "全部角色"
  },
  "正在加载账户…": {
    "en": "Loading accounts…",
    "zh-Hant": "正在加載帳戶…"
  },
  "平台账户": {
    "en": "Platform accounts",
    "zh-Hant": "平台帳戶"
  },
  "状态": {
    "en": "Status",
    "zh-Hant": "狀態"
  },
  "在线会话": {
    "en": "Active sessions",
    "zh-Hant": "在線工作階段"
  },
  "操作": {
    "en": "Actions",
    "zh-Hant": "操作"
  },
  "{p0} 的账户操作": {
    "en": "Account actions for {p0}",
    "zh-Hant": "{p0} 的帳戶操作"
  },
  "不能停用自己的账户": {
    "en": "You cannot suspend your own account",
    "zh-Hant": "不能停用自己的帳戶"
  },
  "请先将平台角色改为普通用户": {
    "en": "First change the platform role to a regular user",
    "zh-Hant": "請先將平台角色改為普通用戶"
  },
  "没有匹配的账户。": {
    "en": "No matching accounts.",
    "zh-Hant": "沒有匹配的帳戶。"
  },
  "找回链接将发送到该账户当前邮箱。旧找回链接失效；管理员不会看到链接或设置新口令。": {
    "en": "A recovery link will be sent to this account's current email address. Previous recovery links will expire. Administrators cannot view the link or choose a new password.",
    "zh-Hant": "找回連結將發送到該帳戶當前電子郵件地址。舊找回連結失效；管理員不會看到連結或設置新密碼。"
  },
  "全部设备将退出，已签发的应用登录凭据会失效。": {
    "en": "All devices will be signed out and issued application sign-in credentials will expire.",
    "zh-Hant": "全部設備將退出，已簽發的應用登入憑據會失效。"
  },
  "平台角色将从「{p0}」改为「{p1}」。这不授予文档读取权限。": {
    "en": "The platform role will change from “{p0}” to “{p1}”. This does not grant document access.",
    "zh-Hant": "平台角色將從「{p0}」改為「{p1}」。這不授予文檔讀取權限。"
  },
  "账户将被停用，全部设备退出，应用访问立即停止。": {
    "en": "The account will be suspended, all devices signed out, and application access stopped immediately.",
    "zh-Hant": "帳戶將被停用，全部設備退出，應用訪問立即停止。"
  },
  "账户将恢复正常；已经结束的登录不会恢复。": {
    "en": "The account will become active. Revoked sessions will not be restored.",
    "zh-Hant": "帳戶將恢復正常；已經結束的登入不會恢復。"
  },
  "这是你自己的账户，完成后可能需要重新登录或离开平台管理页。": {
    "en": "This is your own account. You may need to sign in again or leave platform administration afterward.",
    "zh-Hant": "這是你自己的帳戶，完成後可能需要重新登入或離開平台管理頁。"
  },
  "找回邮件已排队，请在邮件投递中查看状态。": {
    "en": "Recovery email queued. Check Mail delivery for its status.",
    "zh-Hant": "找回郵件已排隊，請在郵件投遞中查看狀態。"
  },
  "{action}完成，撤销 {count} 个会话。": {
    "en": "{action} completed; {count} sessions revoked.",
    "zh-Hant": "{action}完成，撤銷 {count} 個工作階段。"
  },
  "新平台角色": {
    "en": "New platform role",
    "zh-Hant": "新平台角色"
  },
  "普通用户": {
    "en": "Regular user",
    "zh-Hant": "普通用戶"
  },
  "平台运维": {
    "en": "Platform operator",
    "zh-Hant": "平台運維"
  },
  "搜索工作空间": {
    "en": "Search workspaces",
    "zh-Hant": "搜索工作空間"
  },
  "正在加载工作空间…": {
    "en": "Loading workspaces…",
    "zh-Hant": "正在加載工作空間…"
  },
  "平台工作空间": {
    "en": "Platform workspaces",
    "zh-Hant": "平台工作空間"
  },
  "名称": {
    "en": "Name",
    "zh-Hant": "名稱"
  },
  "类型": {
    "en": "Type",
    "zh-Hant": "類型"
  },
  "成员": {
    "en": "Member",
    "zh-Hant": "成員"
  },
  "项目": {
    "en": "Projects",
    "zh-Hant": "專案"
  },
  "成员与邀请": {
    "en": "Members & invitations",
    "zh-Hant": "成員與邀請"
  },
  "没有匹配的工作空间。": {
    "en": "No matching workspaces.",
    "zh-Hant": "沒有匹配的工作空間。"
  },
  "审计记录": {
    "en": "Audit records",
    "zh-Hant": "審計記錄"
  },
  "操作者邮箱或对象": {
    "en": "Actor email or object",
    "zh-Hant": "操作者電子郵件地址或對象"
  },
  "搜索审计记录": {
    "en": "Search audit records",
    "zh-Hant": "搜索審計記錄"
  },
  "刷新审计": {
    "en": "Refresh audit records",
    "zh-Hant": "重新整理審計"
  },
  "操作范围": {
    "en": "Action category",
    "zh-Hant": "操作範圍"
  },
  "筛选审计操作": {
    "en": "Filter audit actions",
    "zh-Hant": "篩選審計操作"
  },
  "全部操作": {
    "en": "All actions",
    "zh-Hant": "全部操作"
  },
  "Synwevo Docs 操作": {
    "en": "Synwevo Docs actions",
    "zh-Hant": "Synwevo Docs 操作"
  },
  "工作空间应用操作": {
    "en": "Workplace application actions",
    "zh-Hant": "工作空間應用操作"
  },
  "账户管理": {
    "en": "Account administration",
    "zh-Hant": "帳戶管理"
  },
  "登录与账户安全": {
    "en": "Sign-in and account security",
    "zh-Hant": "登入與帳戶安全"
  },
  "成员管理": {
    "en": "Member management",
    "zh-Hant": "成員管理"
  },
  "邀请": {
    "en": "Invitations",
    "zh-Hant": "邀請"
  },
  "两步验证": {
    "en": "Two-step verification",
    "zh-Hant": "兩步驗證"
  },
  "正在加载审计记录…": {
    "en": "Loading audit records…",
    "zh-Hant": "正在加載審計記錄…"
  },
  "时间": {
    "en": "Time",
    "zh-Hant": "時間"
  },
  "操作者": {
    "en": "Actor",
    "zh-Hant": "操作者"
  },
  "对象": {
    "en": "Object",
    "zh-Hant": "對象"
  },
  "结果": {
    "en": "Result",
    "zh-Hant": "結果"
  },
  "理由": {
    "en": "Reason",
    "zh-Hant": "理由"
  },
  "成功": {
    "en": "Success",
    "zh-Hant": "成功"
  },
  "被拒绝": {
    "en": "Denied",
    "zh-Hant": "被拒絕"
  },
  "没有匹配的记录。": {
    "en": "No matching records.",
    "zh-Hant": "沒有匹配的記錄。"
  },
  "成员与邀请 · {p0}": {
    "en": "Members and invitations · {p0}",
    "zh-Hant": "成員與邀請 · {p0}"
  },
  "切换要管理的工作空间": {
    "en": "Switch the workspace to manage",
    "zh-Hant": "切換要管理的工作空間"
  },
  "选择工作空间…": {
    "en": "Choose a workspace…",
    "zh-Hant": "選擇工作空間…"
  },
  "找不到这个工作空间": {
    "en": "Workspace not found",
    "zh-Hant": "找不到這個工作空間"
  },
  "你不能管理这个工作空间的成员": {
    "en": "You cannot manage members in this workspace",
    "zh-Hant": "你不能管理這個工作空間的成員"
  },
  "它不存在，或者你不是它的成员。": {
    "en": "It does not exist, or you are not a member.",
    "zh-Hant": "它不存在，或者你不是它的成員。"
  },
  "你在这里的角色是「{role}」。": {
    "en": "Your role here is “{role}”.",
    "zh-Hant": "你在這裡的角色是「{role}」。"
  },
  "返回账户与安全": {
    "en": "Back to account and security",
    "zh-Hant": "返回帳戶與安全"
  },
  "成员列表加载失败": {
    "en": "Could not load members",
    "zh-Hant": "成員列表加載失敗"
  },
  "你的角色：{p0}": {
    "en": "Your role: {p0}",
    "zh-Hant": "你的角色：{p0}"
  },
  "使用资格随成员关系，可逐人撤销": {
    "en": "Access follows membership and can be revoked individually",
    "zh-Hant": "使用資格隨成員關係，可逐人撤銷"
  },
  "需要逐人授予使用资格": {
    "en": "Access must be granted individually",
    "zh-Hant": "需要逐人授予使用資格"
  },
  "{p0} 待接受": {
    "en": "{p0} pending",
    "zh-Hant": "{p0} 待接受"
  },
  "所有者": {
    "en": "Owner",
    "zh-Hant": "擁有者"
  },
  "管理员": {
    "en": "Admin",
    "zh-Hant": "管理員"
  },
  "管理所有成员（包括其他所有者）、邀请任意角色、移交所有权": {
    "en": "Manage all members, including other owners; invite any role; transfer ownership",
    "zh-Hant": "管理所有成員（包括其他所有者）、邀請任意角色、移交所有權"
  },
  "只能查看，不能管理成员；导出需要单独开启": {
    "en": "View only; no member management; export must be enabled separately",
    "zh-Hant": "只能查看，不能管理成員；匯出需要單獨開啓"
  },
  "正常": {
    "en": "Active",
    "zh-Hant": "正常"
  },
  "已停用": {
    "en": "Suspended",
    "zh-Hant": "已停用"
  },
  "待激活": {
    "en": "Pending activation",
    "zh-Hant": "待激活"
  },
  "待删除": {
    "en": "Pending deletion",
    "zh-Hant": "待刪除"
  },
  "已删除": {
    "en": "Deleted",
    "zh-Hant": "已刪除"
  },
  "已冻结": {
    "en": "Frozen",
    "zh-Hant": "已凍結"
  },
  "待认领": {
    "en": "Unclaimed",
    "zh-Hant": "待認領"
  },
  "个人": {
    "en": "Personal",
    "zh-Hant": "個人"
  },
  "团队": {
    "en": "Team",
    "zh-Hant": "團隊"
  },
  "历史数据": {
    "en": "Historical data",
    "zh-Hant": "歷史資料"
  },
  "口令": {
    "en": "Password",
    "zh-Hant": "密碼"
  },
  "安全密钥": {
    "en": "Passkey",
    "zh-Hant": "安全金鑰"
  },
  "待接受": {
    "en": "Pending acceptance",
    "zh-Hant": "待接受"
  },
  "已过期": {
    "en": "Expired",
    "zh-Hant": "已過期"
  },
  "已撤销": {
    "en": "Revoked",
    "zh-Hant": "已撤銷"
  },
  "已接受": {
    "en": "Accepted",
    "zh-Hant": "已接受"
  },
  "排队发送中": {
    "en": "Queued",
    "zh-Hant": "排隊發送中"
  },
  "已寄出": {
    "en": "Sent",
    "zh-Hant": "已寄出"
  },
  "发送失败": {
    "en": "Delivery failed",
    "zh-Hant": "發送失敗"
  },
  "无发送记录": {
    "en": "No delivery record",
    "zh-Hant": "無發送記錄"
  },
  "已授予资格": {
    "en": "Access granted",
    "zh-Hant": "已授予資格"
  },
  "随成员资格": {
    "en": "Included with membership",
    "zh-Hant": "隨成員資格"
  },
  "未授予": {
    "en": "Not granted",
    "zh-Hant": "未授予"
  },
  "请写明理由（至少 {p0} 个字）。它会原样写入审计记录。": {
    "en": "Enter a reason of at least {p0} characters. It will be recorded verbatim in the audit log.",
    "zh-Hant": "請寫明理由（至少 {p0} 個字）。它會原樣寫入審計記錄。"
  },
  "请输入你的口令以确认身份。": {
    "en": "Enter your password to confirm your identity.",
    "zh-Hant": "請輸入你的密碼以確認身份。"
  },
  "重新登录": {
    "en": "Sign in again",
    "zh-Hant": "重新登入"
  },
  "取消": {
    "en": "Cancel",
    "zh-Hant": "取消"
  },
  "处理中…": {
    "en": "Processing…",
    "zh-Hant": "處理中…"
  },
  "{p0}后可重试": {
    "en": "Retry in {p0}",
    "zh-Hant": "{p0}後可重試"
  },
  "验证并{p0}": {
    "en": "Verify and {p0}",
    "zh-Hant": "驗證並{p0}"
  },
  "（必填，至少{p0}个字）": {
    "en": "(required, at least {p0} characters)",
    "zh-Hant": "（必填，至少{p0}個字）"
  },
  "（可选）": {
    "en": "(optional)",
    "zh-Hant": "（可選）"
  },
  "为什么要这样做？": {
    "en": "Why is this change needed?",
    "zh-Hant": "為甚麼要這樣做？"
  },
  "会原样写入审计记录{p0}。": {
    "en": "Recorded verbatim in the audit log{p0}.",
    "zh-Hant": "會原樣寫入審計記錄{p0}。"
  },
  "，还差 {p0} 个字": {
    "en": "; {p0} more characters needed",
    "zh-Hant": "，還差 {p0} 個字"
  },
  "这一步需要重新验证身份。": {
    "en": "Confirm your identity to continue.",
    "zh-Hant": "這一步需要重新驗證身份。"
  },
  "验证只对这类操作有效，并在几分钟后失效。连续输错会结束当前登录。": {
    "en": "Verification applies only to this type of action and expires in a few minutes. Repeated incorrect passwords will end this session.",
    "zh-Hant": "驗證只對這類操作有效，並在幾分鐘後失效。連續輸錯會結束當前登入。"
  },
  "你的口令": {
    "en": "Your password",
    "zh-Hant": "你的密碼"
  },
  "{p0}：{p1} 项全部成功": {
    "en": "{p0}: all {p1} items succeeded",
    "zh-Hant": "{p0}：{p1} 項全部成功"
  },
  "{p0}：{p1} 项全部失败，详情见结果列表": {
    "en": "{p0}: all {p1} items failed; see the results for details",
    "zh-Hant": "{p0}：{p1} 項全部失敗，詳情見結果列表"
  },
  "{p0}：{p1} 项成功，{p2} 项失败，详情见结果列表": {
    "en": "{p0}: {p1} succeeded, {p2} failed; see the results for details",
    "zh-Hant": "{p0}：{p1} 項成功，{p2} 項失敗，詳情見結果列表"
  },
  "只选中失败的{p0}项": {
    "en": "Select only the {p0} failed items",
    "zh-Hant": "只選中失敗的{p0}項"
  },
  "关闭": {
    "en": "Close",
    "zh-Hant": "關閉"
  },
  "全部{p0}项成功。": {
    "en": "All {p0} items succeeded.",
    "zh-Hant": "全部{p0}項成功。"
  },
  "成功{p0}项，失败{p1}项。请查看失败原因，并核对刷新后的列表。": {
    "en": "{p0} succeeded and {p1} failed. Review the failure reasons and check the refreshed list.",
    "zh-Hant": "成功{p0}項，失敗{p1}項。請查看失敗原因，並核對重新整理後的列表。"
  },
  "逐项结果": {
    "en": "Individual results",
    "zh-Hant": "逐項結果"
  },
  "失败": {
    "en": "Failed",
    "zh-Hant": "失敗"
  },
  "完成": {
    "en": "Done",
    "zh-Hant": "完成"
  },
  "开放注册": {
    "en": "Open registration",
    "zh-Hant": "開放註冊"
  },
  "仅限邀请": {
    "en": "Invitation only",
    "zh-Hant": "僅限邀請"
  },
  "关闭注册": {
    "en": "Registration closed",
    "zh-Hant": "關閉註冊"
  },
  "策略保存后对新的注册请求生效，不改变已有账户、成员关系或文档权限。": {
    "en": "The saved policy applies to new registration requests. Existing accounts, memberships and document permissions remain unchanged.",
    "zh-Hant": "策略保存後對新的註冊請求生效，不改變已有帳戶、成員關係或文檔權限。"
  },
  "刷新": {
    "en": "Refresh",
    "zh-Hant": "重新整理"
  },
  "当前策略：{mode}；来源：{source}": {
    "en": "Current policy: {mode}; source: {source}",
    "zh-Hant": "當前策略：{mode}；來源：{source}"
  },
  "平台设置": {
    "en": "Platform setting",
    "zh-Hant": "平台設置"
  },
  "环境配置或默认值": {
    "en": "Environment configuration or default",
    "zh-Hant": "環境設定或預設值"
  },
  "新的注册策略": {
    "en": "New registration policy",
    "zh-Hant": "新的註冊策略"
  },
  "保存注册策略": {
    "en": "Save registration policy",
    "zh-Hant": "保存註冊策略"
  },
  "保存策略": {
    "en": "Save policy",
    "zh-Hant": "保存策略"
  },
  "注册策略将从「{before}」改为「{after}」。此设置会覆盖环境配置。": {
    "en": "Change registration from “{before}” to “{after}”. This setting overrides the environment configuration.",
    "zh-Hant": "註冊策略將從「{before}」改為「{after}」。此設置會覆蓋環境設定。"
  },
  "注册策略已保存": {
    "en": "Registration policy saved",
    "zh-Hant": "註冊策略已保存"
  },
  "账户找回": {
    "en": "Account recovery",
    "zh-Hant": "帳戶找回"
  },
  "邮箱验证": {
    "en": "Email verification",
    "zh-Hant": "電子郵件地址驗證"
  },
  "安全通知": {
    "en": "Security notification",
    "zh-Hant": "安全通知"
  },
  "邮箱变更": {
    "en": "Email change",
    "zh-Hant": "電子郵件地址變更"
  },
  "投递结果不确定，禁止自动重发": {
    "en": "Delivery outcome uncertain; automatic resend blocked",
    "zh-Hant": "投遞結果不確定，禁止自動重發"
  },
  "已生成替代邮件": {
    "en": "Replacement email generated",
    "zh-Hant": "已生成替代郵件"
  },
  "投递失败；请检查邮件服务配置。": {
    "en": "Delivery failed. Check the mail service configuration.",
    "zh-Hant": "投遞失敗；請檢查郵件服務設定。"
  },
  "当前为本地文件模式：邮件仅写入本地文件，没有真正寄出。": {
    "en": "Local file mode: messages are written to local files and are not actually sent.",
    "zh-Hant": "當前為本地文件模式：郵件僅寫入本地文件，沒有真正寄出。"
  },
  "邮件服务尚未配置。": {
    "en": "Mail service is not configured.",
    "zh-Hant": "郵件服務尚未設定。"
  },
  "当前为 SMTP 模式；已寄出表示邮件服务器已接受，不代表已到达收件箱。": {
    "en": "SMTP mode: Sent means the mail server accepted the message, not that it reached the inbox.",
    "zh-Hant": "當前為 SMTP 模式；已寄出表示郵件服務器已接受，不代表已到達收件箱。"
  },
  "搜索收件人或邮件编号": {
    "en": "Search recipient or message ID",
    "zh-Hant": "搜索收件人或郵件編號"
  },
  "邮件状态": {
    "en": "Mail status",
    "zh-Hant": "郵件狀態"
  },
  "收件人": {
    "en": "Recipient",
    "zh-Hant": "收件人"
  },
  "尝试次数": {
    "en": "Attempts",
    "zh-Hant": "嘗試次數"
  },
  "其他邮件": {
    "en": "Other mail",
    "zh-Hant": "其他郵件"
  },
  "寄出时间：{time}": {
    "en": "Sent: {time}",
    "zh-Hant": "寄出時間：{time}"
  },
  "下一次尝试：{time}": {
    "en": "Next attempt: {time}",
    "zh-Hant": "下一次嘗試：{time}"
  },
  "生成新链接并重试": {
    "en": "Generate a new link and retry",
    "zh-Hant": "生成新連結並重試"
  },
  "没有匹配的邮件。": {
    "en": "No matching messages.",
    "zh-Hant": "沒有匹配的郵件。"
  },
  "重新排队": {
    "en": "Queue again",
    "zh-Hant": "重新排隊"
  },
  "为原收件人 {email} 对应账户的当前邮箱生成新链接。旧链接失效，结果会写入审计记录。": {
    "en": "Generate a new link for the current email address of the account originally addressed as {email}. The old link will expire and the action will be audited.",
    "zh-Hant": "為原收件人 {email} 對應帳戶的當前電子郵件地址生成新連結。舊連結失效，結果會寫入審計記錄。"
  },
  "新邮件已排队：{email}。请刷新查看投递状态。": {
    "en": "New email queued for {email}. Refresh to check delivery status.",
    "zh-Hant": "新郵件已排隊：{email}。請重新整理查看投遞狀態。"
  },
  "冻结组织": {
    "en": "Freeze organization",
    "zh-Hant": "凍結組織"
  },
  "解冻组织": {
    "en": "Unfreeze organization",
    "zh-Hant": "解凍組織"
  },
  "删除组织": {
    "en": "Delete organization",
    "zh-Hant": "刪除組織"
  },
  "恢复组织": {
    "en": "Recover organization",
    "zh-Hant": "恢復組織"
  },
  "组织治理": {
    "en": "Organization governance",
    "zh-Hant": "組織治理"
  },
  "平台管理员可查看组织元数据与数量。只有组织所有者可执行生命周期操作；平台管理不会授予成员身份或业务文档访问权。": {
    "en": "Platform administrators can view organization metadata and counts. Lifecycle actions require organization ownership. Platform administration grants neither membership nor business document access.",
    "zh-Hant": "平台管理員可查看組織中繼資料與數量。只有組織所有者可執行生命週期操作；平台管理不會授予成員身份或業務文檔訪問權。"
  },
  "搜索组织名称或编号": {
    "en": "Search organization name or ID",
    "zh-Hant": "搜索組織名稱或編號"
  },
  "恢复期限：{time}": {
    "en": "Recovery deadline: {time}",
    "zh-Hant": "恢復期限：{time}"
  },
  "组织设置": {
    "en": "Organization settings",
    "zh-Hant": "組織設定"
  },
  "生命周期操作需要组织所有者权限": {
    "en": "Lifecycle actions require organization ownership",
    "zh-Hant": "生命週期操作需要組織所有者權限"
  },
  "没有匹配的组织。": {
    "en": "No matching organizations.",
    "zh-Hant": "沒有匹配的組織。"
  },
  "组织将按原有成员关系恢复可用。已撤销的会话和令牌不会恢复；删除前已冻结的组织恢复后仍为冻结状态。": {
    "en": "Availability will be restored using existing memberships. Revoked sessions and tokens will not return. Organizations frozen before deletion remain frozen after recovery.",
    "zh-Hant": "組織將按原有成員關係恢復可用。已撤銷的工作階段和權杖不會恢復；刪除前已凍結的組織恢復後仍為凍結狀態。"
  },
  "组织状态已更新": {
    "en": "Organization status updated",
    "zh-Hant": "組織狀態已更新"
  },
  "邀请操作": {
    "en": "Invitation actions",
    "zh-Hant": "邀請操作"
  },
  "搜索邀请邮箱": {
    "en": "Search invitation email",
    "zh-Hant": "搜索邀請電子郵件地址"
  },
  "邀请状态": {
    "en": "Invitation status",
    "zh-Hant": "邀請狀態"
  },
  "待接受且发送失败": {
    "en": "Pending with failed delivery",
    "zh-Hant": "待接受且發送失敗"
  },
  "刷新邀请": {
    "en": "Refresh invitations",
    "zh-Hant": "重新整理邀請"
  },
  "邀请成员": {
    "en": "Invite members",
    "zh-Hant": "邀請成員"
  },
  "重试邀请列表": {
    "en": "Retry invitation list",
    "zh-Hant": "重試邀請列表"
  },
  "正在加载邀请…": {
    "en": "Loading invitations…",
    "zh-Hant": "正在加載邀請…"
  },
  "工作空间邀请": {
    "en": "Workspace invitations",
    "zh-Hant": "工作空間邀請"
  },
  "邮箱": {
    "en": "Email",
    "zh-Hant": "電郵"
  },
  "角色 / 导出": {
    "en": "Role / export",
    "zh-Hant": "角色 / 匯出"
  },
  "有效期至": {
    "en": "Expires on",
    "zh-Hant": "有效期至"
  },
  "不能管理比自己角色更高的邀请": {
    "en": "Cannot manage an invitation with a higher role than yours",
    "zh-Hant": "不能管理比自己角色更高的邀請"
  },
  "选择邀请 {p0}": {
    "en": "Select invitation for {p0}",
    "zh-Hant": "選擇邀請 {p0}"
  },
  "邀请人：{p0}": {
    "en": "Invited by: {p0}",
    "zh-Hant": "邀請人：{p0}"
  },
  "无记录": {
    "en": "Not recorded",
    "zh-Hant": "無記錄"
  },
  "允许导出": {
    "en": "Allow export",
    "zh-Hant": "允許匯出"
  },
  "不允许导出": {
    "en": "Export not allowed",
    "zh-Hant": "不允許匯出"
  },
  "发送失败；可重发或联系管理员。": {
    "en": "Delivery failed. Resend or contact an administrator.",
    "zh-Hant": "發送失敗；可重發或聯繫管理員。"
  },
  "{p0} 的邀请操作": {
    "en": "Invitation actions for {p0}",
    "zh-Hant": "{p0} 的邀請操作"
  },
  "修改邀请": {
    "en": "Edit invitation",
    "zh-Hant": "修改邀請"
  },
  "重发邀请": {
    "en": "Resend invitation",
    "zh-Hant": "重發邀請"
  },
  "撤销邀请": {
    "en": "Revoke invitation",
    "zh-Hant": "撤銷邀請"
  },
  "没有匹配的邀请。": {
    "en": "No matching invitations.",
    "zh-Hant": "沒有匹配的邀請。"
  },
  "批量邀请操作": {
    "en": "Bulk invitation actions",
    "zh-Hant": "批量邀請操作"
  },
  "已选{p0}项（仅本页）": {
    "en": "{p0} selected on this page",
    "zh-Hant": "已選{p0}項（僅本頁）"
  },
  "清除选择": {
    "en": "Clear selection",
    "zh-Hant": "清除選擇"
  },
  "邀请已更新，链接不变": {
    "en": "Invitation updated; the link is unchanged",
    "zh-Hant": "邀請已更新，連結不變"
  },
  "邀请已更新": {
    "en": "Invitation updated",
    "zh-Hant": "邀請已更新"
  },
  "邀请操作结果": {
    "en": "Invitation results",
    "zh-Hant": "邀請操作結果"
  },
  "新邀请已排队发送，尚未接受": {
    "en": "New invitation queued; not yet accepted",
    "zh-Hant": "新邀請已排隊發送，尚未接受"
  },
  "发送邀请": {
    "en": "Send invitations",
    "zh-Hant": "發送邀請"
  },
  "向以下邮箱发出加入「{p0}」的邀请，角色为{p1}。已是成员的邮箱会单独报告失败；每个邮箱只发送一次。": {
    "en": "Invite the following addresses to “{p0}” as {p1}. Existing members will be reported individually as failures. Each address is sent only once.",
    "zh-Hant": "向以下電子郵件地址發出加入「{p0}」的邀請，角色為{p1}。已是成員的電子郵件地址會單獨報告失敗；每個電子郵件地址只發送一次。"
  },
  "邮箱（每行一个，也可用逗号分隔）": {
    "en": "Email addresses (one per line or separated by commas)",
    "zh-Hant": "電子郵件地址（每行一個，也可用逗號分隔）"
  },
  "邀请角色": {
    "en": "Invited role",
    "zh-Hant": "邀請角色"
  },
  "有效天数": {
    "en": "Valid for days",
    "zh-Hant": "有效天數"
  },
  "保存邀请": {
    "en": "Save invitation",
    "zh-Hant": "保存邀請"
  },
  "修改发给{p0}的待接受邀请。原链接保持有效；已经接受的邀请无法修改。": {
    "en": "Edit the pending invitation to {p0}. The current link remains valid. Accepted invitations cannot be edited.",
    "zh-Hant": "修改發給{p0}的待接受邀請。原連結保持有效；已經接受的邀請無法修改。"
  },
  "将发送新的邀请，旧链接会失效。": {
    "en": "A new invitation will be sent and the old link will expire.",
    "zh-Hant": "將發送新的邀請，舊連結會失效。"
  },
  "每项单独执行并报告结果。": {
    "en": "Each item is processed and reported separately.",
    "zh-Hant": "每項單獨執行並報告結果。"
  },
  "新邀请有效天数": {
    "en": "New invitation validity in days",
    "zh-Hant": "新邀請有效天數"
  },
  "列表分页": {
    "en": "List pagination",
    "zh-Hant": "列表分頁"
  },
  "{p0}–{p1} / {p2} 项": {
    "en": "{p0}–{p1} of {p2} items",
    "zh-Hant": "{p0}–{p1} / {p2} 項"
  },
  "{p0}–{p1} / {p2}+ 项": {
    "en": "{p0}–{p1} of {p2}+ items",
    "zh-Hant": "{p0}–{p1} / {p2}+ 項"
  },
  "0 项": {
    "en": "0 items",
    "zh-Hant": "0 項"
  },
  "上一页": {
    "en": "Previous",
    "zh-Hant": "上一頁"
  },
  "下一页": {
    "en": "Next",
    "zh-Hant": "下一頁"
  },
  "选择本页可操作的项": {
    "en": "Select actionable items on this page",
    "zh-Hant": "選擇本頁可操作的項"
  },
  "不能管理同级或更高角色的成员": {
    "en": "You cannot manage members with the same or a higher role",
    "zh-Hant": "不能管理同級或更高角色的成員"
  },
  "修改角色": {
    "en": "Change role",
    "zh-Hant": "修改角色"
  },
  "导出随角色授予": {
    "en": "Export included with role",
    "zh-Hant": "匯出隨角色授予"
  },
  "关闭导出": {
    "en": "Disable export",
    "zh-Hant": "關閉匯出"
  },
  "授予应用使用资格": {
    "en": "Grant application access",
    "zh-Hant": "授予應用使用資格"
  },
  "已经授予": {
    "en": "Already granted",
    "zh-Hant": "已經授予"
  },
  "撤销应用使用资格": {
    "en": "Revoke application access",
    "zh-Hant": "撤銷應用使用資格"
  },
  "已经撤销": {
    "en": "Already revoked",
    "zh-Hant": "已經撤銷"
  },
  "移除成员": {
    "en": "Remove member",
    "zh-Hant": "移除成員"
  },
  "移交所有权": {
    "en": "Transfer ownership",
    "zh-Hant": "移交所有權"
  },
  "接收方账户必须正常": {
    "en": "The recipient account must be active",
    "zh-Hant": "接收方帳戶必須正常"
  },
  "搜索成员": {
    "en": "Search members",
    "zh-Hant": "搜索成員"
  },
  "邮箱或姓名": {
    "en": "Email or name",
    "zh-Hant": "電子郵件地址或姓名"
  },
  "角色": {
    "en": "Role",
    "zh-Hant": "角色"
  },
  "应用使用资格": {
    "en": "Application access",
    "zh-Hant": "應用使用資格"
  },
  "全部资格": {
    "en": "All access states",
    "zh-Hant": "全部資格"
  },
  "已授予": {
    "en": "Granted",
    "zh-Hant": "已授予"
  },
  "未单独设置": {
    "en": "Not explicitly set",
    "zh-Hant": "未單獨設置"
  },
  "正在更新成员列表…": {
    "en": "Updating members…",
    "zh-Hant": "正在更新成員列表…"
  },
  "工作空间成员": {
    "en": "Workspace members",
    "zh-Hant": "工作空間成員"
  },
  "导出": {
    "en": "Export",
    "zh-Hant": "匯出"
  },
  "选择 {p0}": {
    "en": "Select {p0}",
    "zh-Hant": "選擇 {p0}"
  },
  "（你）": {
    "en": "(you)",
    "zh-Hant": "（你）"
  },
  "允许": {
    "en": "Allowed",
    "zh-Hant": "允許"
  },
  "随角色授予": {
    "en": "Included with role",
    "zh-Hant": "隨角色授予"
  },
  "未配置应用": {
    "en": "No application configured",
    "zh-Hant": "未設定應用"
  },
  "{p0} 的操作": {
    "en": "Actions for {p0}",
    "zh-Hant": "{p0} 的操作"
  },
  "没有匹配的成员。试试其他搜索条件。": {
    "en": "No matching members. Try different search criteria.",
    "zh-Hant": "沒有匹配的成員。試試其他搜索條件。"
  },
  "批量成员操作": {
    "en": "Bulk member actions",
    "zh-Hant": "批量成員操作"
  },
  "已选{p0}位（仅本页）": {
    "en": "{p0} members selected on this page",
    "zh-Hant": "已選{p0}位（僅本頁）"
  },
  "成员角色已更新": {
    "en": "Member role updated",
    "zh-Hant": "成員角色已更新"
  },
  "撤销使用资格": {
    "en": "Revoke access",
    "zh-Hant": "撤銷使用資格"
  },
  "已撤销应用使用资格": {
    "en": "Application access revoked",
    "zh-Hant": "已撤銷應用使用資格"
  },
  "授予使用资格": {
    "en": "Grant access",
    "zh-Hant": "授予使用資格"
  },
  "更新导出权限": {
    "en": "Update export permission",
    "zh-Hant": "更新匯出權限"
  },
  "{p0}在「{p1}」的导出权限将{p2}，角色不变。": {
    "en": "Export for {p0} in “{p1}” will be set to {p2}. Their role will not change.",
    "zh-Hant": "{p0}在「{p1}」的匯出權限將{p2}，角色不變。"
  },
  "开启": {
    "en": "Enabled",
    "zh-Hant": "開啓"
  },
  "导出权限已更新": {
    "en": "Export permission updated",
    "zh-Hant": "匯出權限已更新"
  },
  "所有权已移交": {
    "en": "Ownership transferred",
    "zh-Hant": "所有權已移交"
  },
  "账户导航": {
    "en": "Account navigation",
    "zh-Hant": "帳戶導航"
  },
  "账户与安全": {
    "en": "Account & security",
    "zh-Hant": "帳戶與安全"
  },
  "API 管理": {
    "en": "API management",
    "zh-Hant": "API 管理"
  },
  "退出登录": {
    "en": "Sign out",
    "zh-Hant": "退出登入"
  },
  "将不再是所有者：失去管理其他所有者与管理员、移交所有权的权限。": {
    "en": "Will no longer be an owner, losing the ability to manage owners and administrators or transfer ownership.",
    "zh-Hant": "將不再是所有者：失去管理其他所有者與管理員、移交所有權的權限。"
  },
  "将失去邀请成员和管理成员的权限。": {
    "en": "Will lose the ability to invite and manage members.",
    "zh-Hant": "將失去邀請成員和管理成員的權限。"
  },
  "将不能再编辑；导出权限会随之关闭，需要时可以再单独开启。": {
    "en": "Will no longer be able to edit. Export will be disabled and can be enabled separately if needed.",
    "zh-Hant": "將不能再編輯；匯出權限會隨之關閉，需要時可以再單獨開啓。"
  },
  "将获得「{p0}」的权限：{p1}。": {
    "en": "Will receive “{p0}” permissions: {p1}.",
    "zh-Hant": "將獲得「{p0}」的權限：{p1}。"
  },
  "已移除": {
    "en": "Removed",
    "zh-Hant": "已移除"
  },
  "已撤销 {p0} 项应用使用资格、{p1} 个令牌；": {
    "en": "Revoked {p0} application grants and {p1} tokens; ",
    "zh-Hant": "已撤銷 {p0} 項應用使用資格、{p1} 個權杖；"
  },
  "修改 {p0} 的角色": {
    "en": "Change role for {p0}",
    "zh-Hant": "修改 {p0} 的角色"
  },
  "{name}（{email}）在「{space}」的角色将从「{before}」改为「{after}」。": {
    "en": "Change {name} ({email}) in “{space}” from “{before}” to “{after}”.",
    "zh-Hant": "{name}（{email}）在「{space}」的角色將從「{before}」改為「{after}」。"
  },
  "这是需要写明理由并重新验证身份的操作。": {
    "en": "This action requires a written reason and identity verification.",
    "zh-Hant": "這是需要寫明理由並重新驗證身份的操作。"
  },
  "新角色": {
    "en": "New role",
    "zh-Hant": "新角色"
  },
  "（当前）": {
    "en": "(current)",
    "zh-Hant": "（當前）"
  },
  "管理员最多可以授予「管理员」；授予后你将不能再管理这位成员。": {
    "en": "Administrators can grant up to the administrator role. You will no longer be able to manage that member afterward.",
    "zh-Hant": "管理員最多可以授予「管理員」；授予後你將不能再管理這位成員。"
  },
  "修改 {p0} 位成员的角色": {
    "en": "Change roles for {p0} members",
    "zh-Hant": "修改 {p0} 位成員的角色"
  },
  "改为「{p0}」": {
    "en": "Change to “{p0}”",
    "zh-Hant": "改為「{p0}」"
  },
  "将把以下 {count} 位成员在「{space}」的角色改为「{role}」。": {
    "en": "Change the following {count} members in “{space}” to “{role}”.",
    "zh-Hant": "將把以下 {count} 位成員在「{space}」的角色改為「{role}」。"
  },
  "（另有{p0}位已经是该角色，不会变化）": {
    "en": "({p0} others already have this role and will not change)",
    "zh-Hant": "（另有{p0}位已經是該角色，不會變化）"
  },
  "其中包含管理员或所有者的降级、或新的所有者：需要写明理由并重新验证身份。": {
    "en": "This includes demoting an administrator or owner, or appointing a new owner. A written reason and identity verification are required.",
    "zh-Hant": "其中包含管理員或所有者的降級、或新的所有者：需要寫明理由並重新驗證身份。"
  },
  "每一项单独执行、单独报告结果；被拒绝的项不会改变。": {
    "en": "Each item is processed and reported separately. Refused items remain unchanged.",
    "zh-Hant": "每一項單獨執行、單獨報告結果；被拒絕的項不會改變。"
  },
  "应用": {
    "en": "Application",
    "zh-Hant": "應用"
  },
  "移除 {p0} 位成员": {
    "en": "Remove {p0} members",
    "zh-Hant": "移除 {p0} 位成員"
  },
  "例如：已离职 / 项目结束 / 账户交接": {
    "en": "For example: departure / project completion / account handover",
    "zh-Hant": "例如：已離職 / 專案結束 / 帳戶交接"
  },
  "将把{p0}（{p1}，{p2}）从「{p3}」移除。": {
    "en": "Remove {p0} ({p1}, {p2}) from “{p3}”.",
    "zh-Hant": "將把{p0}（{p1}，{p2}）從「{p3}」移除。"
  },
  "将把以下 {count} 位成员从「{space}」移除：": {
    "en": "Remove the following {count} members from “{space}”:",
    "zh-Hant": "將把以下 {count} 位成員從「{space}」移除："
  },
  "立即生效：": {
    "en": "Effective immediately:",
    "zh-Hant": "立即生效："
  },
  "这些成员发出但尚未被接受的邀请会被取消。": {
    "en": "Pending invitations issued by these members are cancelled.",
    "zh-Hant": "這些成員發出但尚未被接受的邀請會被取消。"
  },
  "撤销 {p0} 使用资格": {
    "en": "Revoke {p0} access",
    "zh-Hant": "撤銷 {p0} 使用資格"
  },
  "将撤销{p0}（{p1}）在「{p2}」使用{p3}的资格。": {
    "en": "Revoke {p0} ({p1})'s access to {p3} in “{p2}”.",
    "zh-Hant": "將撤銷{p0}（{p1}）在「{p2}」使用{p3}的資格。"
  },
  "将撤销以下 {count} 位成员在「{space}」使用 {app} 的资格：": {
    "en": "Revoke access to {app} in “{space}” for the following {count} members:",
    "zh-Hant": "將撤銷以下 {count} 位成員在「{space}」使用 {app} 的資格："
  },
  "立即不能再打开{p0}；": {
    "en": "They can no longer open {p0};",
    "zh-Hant": "立即不能再打開{p0}；"
  },
  "已签发给{p0}的令牌全部失效；": {
    "en": "All tokens issued to {p0} expire;",
    "zh-Hant": "已簽發給{p0}的權杖全部失效；"
  },
  "成员身份和角色不变，之后可以再授予。": {
    "en": "Membership and role remain unchanged. Access can be granted again later.",
    "zh-Hant": "成員身份和角色不變，之後可以再授予。"
  },
  "{name}（{email}，当前为{role}）将成为「{space}」的所有者，拥有与你相同的全部管理权限，包括管理你自己。": {
    "en": "{name} ({email}, currently {role}) will become an owner of “{space}”, with the same management permissions as you, including the ability to manage you.",
    "zh-Hant": "{name}（{email}，當前為{role}）將成為「{space}」的所有者，擁有與你相同的全部管理權限，包括管理你自己。"
  },
  "你仍保留所有者身份：接收方也将拥有所有者身份。": {
    "en": "You remain an owner, sharing ownership with the recipient.",
    "zh-Hant": "你仍保留所有者身份：接收方也將擁有所有者身份。"
  },
  "你的角色将变为「{role}」。": {
    "en": "Your role will change to “{role}”.",
    "zh-Hant": "你的角色將變為「{role}」。"
  },
  "移交之后，我的角色": {
    "en": "My role after the transfer",
    "zh-Hant": "移交之後，我的角色"
  },
  "仍是所有者（共同拥有）": {
    "en": "Remain an owner (shared ownership)",
    "zh-Hant": "仍是所有者（共同擁有）"
  },
  "你将离开「{space}」：": {
    "en": "You will leave “{space}”:",
    "zh-Hant": "你將離開「{space}」："
  },
  "你发出但尚未被接受的邀请会被取消。": {
    "en": "Pending invitations you issued are cancelled.",
    "zh-Hant": "你發出但尚未被接受的邀請會被取消。"
  },
  "{n} 秒": {
    "en": "{n} seconds",
    "zh-Hant": "{n} 秒"
  },
  "{n} 分钟": {
    "en": "{n} minutes",
    "zh-Hant": "{n} 分鐘"
  },
  "{n} 小时": {
    "en": "{n} hours",
    "zh-Hant": "{n} 小時"
  },
  "请写明理由（8 至 500 个字）": {
    "en": "Enter a reason (8 to 500 characters)",
    "zh-Hant": "請寫明理由（8 至 500 個字）"
  },
  "口令不正确，请重试。": {
    "en": "Incorrect password. Try again.",
    "zh-Hant": "密碼不正確，請重試。"
  },
  "登录已失效，请重新登录。": {
    "en": "Your session has expired. Sign in again.",
    "zh-Hant": "登入已失效，請重新登入。"
  },
  "当前角色无权执行此操作。": {
    "en": "Your current role cannot perform this action.",
    "zh-Hant": "當前角色無權執行此操作。"
  },
  "找不到该组织或你不是其成员。": {
    "en": "Organization not found, or you are not a member.",
    "zh-Hant": "找不到該組織或你不是其成員。"
  },
  "目标账户不存在或不可用。": {
    "en": "The target account does not exist or is unavailable.",
    "zh-Hant": "目標帳戶不存在或不可用。"
  },
  "找不到该账户。": {
    "en": "Account not found.",
    "zh-Hant": "找不到該帳戶。"
  },
  "必须保留至少一位平台管理员。": {
    "en": "At least one platform administrator must remain.",
    "zh-Hant": "必須保留至少一位平台管理員。"
  },
  "必须保留至少一位正常的所有者。": {
    "en": "At least one active owner must remain.",
    "zh-Hant": "必須保留至少一位正常的所有者。"
  },
  "注册策略已更改，请刷新后重试。": {
    "en": "Registration policy changed. Refresh and try again.",
    "zh-Hant": "註冊策略已更改，請重新整理後重試。"
  },
  "发送过于频繁，请稍后重试。": {
    "en": "Too many delivery requests. Try again later.",
    "zh-Hant": "發送過於頻繁，請稍後重試。"
  },
  "邮件链接地址尚未配置，请联系部署管理员。": {
    "en": "Email link URL is not configured. Contact the deployment administrator.",
    "zh-Hant": "郵件連結地址尚未設定，請聯繫部署管理員。"
  },
  "邮件服务配置无效，请联系部署管理员。": {
    "en": "Mail configuration is invalid. Contact the deployment administrator.",
    "zh-Hant": "郵件服務設定無效，請聯繫部署管理員。"
  },
  "该邮件不能安全重试。": {
    "en": "This message cannot be retried safely.",
    "zh-Hant": "該郵件不能安全重試。"
  },
  "找不到该邮件。": {
    "en": "Message not found.",
    "zh-Hant": "找不到該郵件。"
  },
  "该邮箱已经验证。": {
    "en": "This email address is already verified.",
    "zh-Hant": "該電子郵件地址已經驗證。"
  },
  "组织当前状态不允许此操作，请刷新。": {
    "en": "The organization's current state does not allow this action. Refresh the page.",
    "zh-Hant": "組織當前狀態不允許此操作，請重新整理。"
  },
  "组织的七天恢复期限已过。": {
    "en": "The organization's seven-day recovery period has expired.",
    "zh-Hant": "組織的七天恢復期限已過。"
  },
  "邀请过于频繁，请稍后重试。": {
    "en": "Too many invitations. Try again later.",
    "zh-Hant": "邀請過於頻繁，請稍後重試。"
  },
  "已有待接受的邀请。": {
    "en": "A pending invitation already exists.",
    "zh-Hant": "已有待接受的邀請。"
  },
  "该邀请已不在待接受状态（已接受、已拒绝或已撤销），请刷新。": {
    "en": "This invitation is no longer pending (it was accepted, declined or revoked). Refresh the page.",
    "zh-Hant": "該邀請已不在待接受狀態（已接受、已拒絕或已撤銷），請重新整理。"
  },
  "操作未完成，请刷新后重试。": {
    "en": "Action not completed. Refresh and try again.",
    "zh-Hant": "操作未完成，請重新整理後重試。"
  },
  "无法连接服务，请检查连接后重试。": {
    "en": "Could not connect to the service. Check your connection and try again.",
    "zh-Hant": "無法連接服務，請檢查連接後重試。"
  },
  "网关未能完成请求，请稍后重试。": {
    "en": "The gateway could not complete the request. Try again later.",
    "zh-Hant": "閘道未能完成請求，請稍後重試。"
  },
  "请在 {duration} 后重试。": {
    "en": "Try again in {duration}.",
    "zh-Hant": "請在 {duration} 後重試。"
  },
  "请求编号：{id}": {
    "en": "Request ID: {id}",
    "zh-Hant": "請求編號：{id}"
  },
  "账户体系建立之前的数据，属于一个没有成员的工作空间，目前没有人能读取（包括平台管理员）。指派给一个已有账户后才能打开。": { en: "Data from before the account system, held in a workspace with no members: nobody can read it right now, platform administrators included. Assign it to an existing account to make it accessible.", "zh-Hant": "帳戶體系建立之前的資料，屬於一個沒有成員的工作空間，目前沒有人能讀取（包括平台管理員）。指派給一個已有帳戶後才能開啟。" },
  "刷新工作空间": { en: "Refresh workspaces", "zh-Hant": "重新整理工作空間" },
  "只显示数量，不显示内容；平台管理权限不包含读取文档或管理工作空间成员。": { en: "Counts only, never content; platform administration does not include reading documents or managing workspace members.", "zh-Hant": "只顯示數量，不顯示內容；平台管理權限不包含讀取文件或管理工作空間成員。" },
  "冻结工作空间": { en: "Freeze workspace", "zh-Hant": "凍結工作空間" },
  "解冻工作空间": { en: "Unfreeze workspace", "zh-Hant": "解凍工作空間" },
  "成员将立即失去应用访问，当前会话将移出这个工作空间。成员关系和内容保留。": { en: "Members immediately lose application access and current sessions leave this workspace. Memberships and content are kept.", "zh-Hant": "成員將立即失去應用存取，目前工作階段將移出這個工作空間。成員關係和內容保留。" },
  "工作空间将恢复可用，成员仍按原有资格访问。此前结束的登录不会恢复。": { en: "The workspace becomes available again with its existing access; sessions ended earlier are not restored.", "zh-Hant": "工作空間將恢復可用，成員仍按原有資格存取。此前結束的登入不會恢復。" },
  "工作空间已冻结。": { en: "Workspace frozen.", "zh-Hant": "工作空間已凍結。" },
  "工作空间已解冻。若之后恢复更早的备份，冻结可能重新生效，需要再次解冻。": { en: "Workspace unfrozen. Restoring an earlier backup may freeze it again, in which case unfreeze it once more.", "zh-Hant": "工作空間已解凍。若之後恢復更早的備份，凍結可能重新生效，需要再次解凍。" },
  "工作空间管理": { en: "Workspace administration", "zh-Hant": "工作空間管理" },
  "翻页时保留本次查询的范围；刷新后包含新记录。": { en: "Paging keeps this query's range; refresh to include new records.", "zh-Hant": "翻頁時保留本次查詢的範圍；重新整理後包含新記錄。" },
  "开放注册允许任何人创建账户；其他两种策略只允许受邀者。": { en: "Open registration lets anyone create an account; the other two policies admit invited people only.", "zh-Hant": "開放註冊允許任何人建立帳戶；其他兩種策略只允許受邀者。" },
  "只显示投递记录，不显示邮件内容。重试找回和验证邮件会生成新链接并使旧链接失效；邀请由工作空间管理员在「成员与邀请」中重发。安全通知和投递结果不确定的邮件不能重发。": { en: "Delivery records only, never message contents. Retrying a recovery or verification mail creates a new link and invalidates the old one; invitations are resent by workspace administrators under “Members and invitations”. Security notices and deliveries with an uncertain outcome cannot be resent.", "zh-Hant": "只顯示投遞記錄，不顯示郵件內容。重試找回和驗證郵件會產生新連結並使舊連結失效；邀請由工作空間管理員在「成員與邀請」中重發。安全通知和投遞結果不確定的郵件不能重發。" },
  "组织将停止使用，并保留七天恢复期限。所属工作空间的访问会被收回，内容不会因此提供给管理员。": { en: "The organization stops working, with a seven-day recovery period. Access to its workspaces is withdrawn; their content is not exposed to administrators.", "zh-Hant": "組織將停止使用，並保留七天恢復期限。所屬工作空間的存取會被收回，內容不會因此提供給管理員。" },
  "组织及其工作空间将停止应用访问，已有会话和令牌会被撤销。": { en: "The organization and its workspaces lose application access; existing sessions and tokens are revoked.", "zh-Hant": "組織及其工作空間將停止應用存取，已有工作階段和權杖會被撤銷。" },
  "工作空间冻结期间不能邀请": { en: "Cannot invite while the workspace is frozen", "zh-Hant": "工作空間凍結期間不能邀請" },
  "对方接受邀请后才成为成员；重发会让旧链接失效。": { en: "Someone becomes a member only after accepting; resending invalidates the old link.", "zh-Hant": "對方接受邀請後才成為成員；重發會讓舊連結失效。" },
  "工作空间冻结期间不能重发": { en: "Cannot resend while the workspace is frozen", "zh-Hant": "工作空間凍結期間不能重發" },
  "{p0} 个邮箱；一次最多 100 个。每个工作空间 24 小时最多创建 50 个邀请，超出部分会报告失败。": { en: "{p0} addresses; up to 100 per submission. Each workspace can create 50 invitations per 24 hours; the rest are reported as failures.", "zh-Hant": "{p0} 個電子郵件地址；一次最多 100 個。每個工作空間 24 小時最多建立 50 個邀請，超出部分會報告失敗。" },
  "允许导出（编辑者及以上角色自带）": { en: "Allow export (included with editor and higher roles)", "zh-Hant": "允許匯出（編輯者及以上角色自帶）" },
  "这些链接将失效，收件人将不能通过它们加入工作空间。已加入的成员不受影响。": { en: "These links will expire and can no longer be used to join the workspace. Existing members are unaffected.", "zh-Hant": "這些連結將失效，收件人將不能透過它們加入工作空間。已加入的成員不受影響。" },
  "不能修改自己的权限，请使用退出工作空间或移交所有权": { en: "You cannot change your own permissions. Leave the workspace or transfer ownership instead.", "zh-Hant": "不能修改自己的權限，請使用退出工作空間或移交所有權" },
  "编辑者及以上角色自带导出权限；需先降低角色": { en: "Editor and higher roles include export; lower the role first", "zh-Hant": "編輯者及以上角色自帶匯出權限；需先降低角色" },
  "工作空间冻结期间不能增加权限": { en: "Cannot add permissions while the workspace is frozen", "zh-Hant": "工作空間凍結期間不能增加權限" },
  "工作空间冻结期间不能授予": { en: "Cannot grant access while the workspace is frozen", "zh-Hant": "工作空間凍結期間不能授予" },
  "工作空间冻结期间不能移交": { en: "Cannot transfer ownership while the workspace is frozen", "zh-Hant": "工作空間凍結期間不能移交" },
  "退出工作空间": { en: "Leave workspace", "zh-Hant": "退出工作空間" },
  "账户、工作空间或应用当前不可用": { en: "Account, workspace or application currently unavailable", "zh-Hant": "帳戶、工作空間或應用目前不可用" },
  "{members} 将获得在「{space}」使用 {app} 的资格。账户、工作空间和应用仍须正常可用。": { en: "{members} will receive access to {app} in “{space}”. The account, workspace and application must still be available.", "zh-Hant": "{members} 將獲得在「{space}」使用 {app} 的資格。帳戶、工作空間和應用仍須正常可用。" },
  "将成为所有者：可以管理这个工作空间里的所有成员（包括你）、邀请任意角色、移交所有权。所有者只能由其他所有者降级。": { en: "Will become an owner: able to manage every member of this workspace including you, invite any role and transfer ownership. Only other owners can demote an owner.", "zh-Hant": "將成為所有者：可以管理這個工作空間裡的所有成員（包括你）、邀請任意角色、移交所有權。所有者只能由其他所有者降級。" },
  "{p0} 个登录已移出该工作空间；取消了 {p1} 个由其发出的邀请": { en: "moved {p0} sign-ins out of the workspace and cancelled {p1} invitations they had issued", "zh-Hant": "{p0} 個登入已移出該工作空間；取消了 {p1} 個由其發出的邀請" },
  "工作空间已冻结：只能降低角色，不能提升。": { en: "Workspace frozen: roles can only be lowered, not raised.", "zh-Hant": "工作空間已凍結：只能降低角色，不能提升。" },
  "降为查看者的成员将不能再编辑，导出权限随之关闭。": { en: "Members changed to viewer can no longer edit, and export is switched off.", "zh-Hant": "降為檢視者的成員將不能再編輯，匯出權限隨之關閉。" },
  "工作空间已冻结，其中 {p0} 项提升会被拒绝。": { en: "Workspace frozen: {p0} of these promotions will be refused.", "zh-Hant": "工作空間已凍結，其中 {p0} 項提升會被拒絕。" },
  "失去这个工作空间里全部项目、模板与文件的访问；": { en: "Access to every project, template and file in this workspace is removed;", "zh-Hant": "失去這個工作空間裡全部專案、範本與檔案的存取；" },
  "在这个工作空间使用{p0}的资格被撤销，已签发给应用的令牌全部失效；": { en: "Access to {p0} in this workspace is revoked and every token issued to the application expires;", "zh-Hant": "在這個工作空間使用{p0}的資格被撤銷，已簽發給應用的權杖全部失效；" },
  "正在这个工作空间里的登录会被移出（账户不会退出登录）；": { en: "Sessions currently in this workspace are moved out (the account is not signed out);", "zh-Hant": "正在這個工作空間裡的登入會被移出（帳戶不會登出）；" },
  "账户本身不受影响。以后可以重新邀请，但不会恢复原来的角色和资格。": { en: "The account itself is unaffected. They can be invited again later, without their previous role or access.", "zh-Hant": "帳戶本身不受影響。以後可以重新邀請，但不會恢復原來的角色和資格。" },
  "工作空间始终有所有者。": { en: "The workspace always has an owner.", "zh-Hant": "工作空間始終有所有者。" },
  "失去这个工作空间里全部内容的访问；": { en: "Access to everything in this workspace is removed;", "zh-Hant": "失去這個工作空間裡全部內容的存取；" },
  "你在这个工作空间的应用使用资格与令牌会被撤销；": { en: "Your application access and tokens in this workspace are revoked;", "zh-Hant": "你在這個工作空間的應用使用資格與權杖會被撤銷；" },
  "要回来，需要工作空间管理员重新邀请你。": { en: "A workspace administrator must invite you again to return.", "zh-Hant": "要回來，需要工作空間管理員重新邀請你。" },
  "你是这个工作空间唯一的所有者，不能直接退出。请先把所有权移交给另一位成员。": { en: "You are the only owner of this workspace and cannot leave directly. Transfer ownership to another member first.", "zh-Hant": "你是這個工作空間唯一的所有者，不能直接退出。請先把所有權移交給另一位成員。" },
  "请先移交所有权，再退出工作空间。": { en: "Transfer ownership before leaving the workspace.", "zh-Hant": "請先移交所有權，再退出工作空間。" },
  "工作空间已冻结，不能增加权限。": { en: "Workspace frozen. Permissions cannot be increased.", "zh-Hant": "工作空間已凍結，不能增加權限。" },
  "该账户已经是工作空间成员。": { en: "This account is already a member of the workspace.", "zh-Hant": "該帳戶已經是工作空間成員。" },
  "该工作空间已被认领或不能认领，请刷新。": { en: "This workspace has already been claimed or cannot be claimed. Refresh the page.", "zh-Hant": "該工作空間已被認領或不能認領，請重新整理。" },
  "邀请成员，管理编辑者与查看者，最高可授予管理员": { en: "Invite members, manage editors and viewers, assign roles up to administrator", "zh-Hant": "邀請成員，管理編輯者與檢視者，最高可授予管理員" },
  "在工作空间内编辑，不能管理成员": { en: "Edit within the workspace; no member management", "zh-Hant": "在工作空間內編輯，不能管理成員" },
  "编辑者": { en: "Editor", "zh-Hant": "編輯者" },
  "查看者": { en: "Viewer", "zh-Hant": "檢視者" },
  "只有工作空间所有者和管理员可以查看与管理成员。{p0}": { en: "Only workspace owners and administrators can view and manage members. {p0}", "zh-Hant": "只有工作空間所有者和管理員可以查看與管理成員。{p0}" },
  "{kind}工作空间": { en: "{kind} workspace", "zh-Hant": "{kind}工作空間" },
  "实际访问还取决于账户、工作空间和应用是否可用；授予使用资格不会启用已停用的应用。": { en: "Access also depends on the account, workspace and application being available; granting access does not enable a disabled application.", "zh-Hant": "實際存取還取決於帳戶、工作空間和應用是否可用；授予使用資格不會啟用已停用的應用。" },
  "这个工作空间已被平台管理员冻结：成员暂时无法使用其中的应用。解冻前只能移除成员和撤销权限，不能邀请、提升角色或授予权限。": { en: "A platform administrator froze this workspace: members cannot use its applications for now. Until it is unfrozen you can only remove members and revoke access, not invite, promote or grant.", "zh-Hant": "這個工作空間已被平台管理員凍結：成員暫時無法使用其中的應用。解凍前只能移除成員和撤銷權限，不能邀請、提升角色或授予權限。" },
  // The account service's own sentences for refusals met on the administration and members pages (CI-85).
  "当前登录只验证了一个因素：请在账户页输入口令确认身份后再使用平台管理": { en: "This sign-in proved one factor only: confirm your identity with your password on the account page before using platform management.", "zh-Hant": "目前登入只驗證了一個因素：請在帳戶頁輸入密碼確認身分後再使用平台管理" },
  "该工作空间没有被冻结": { en: "This workspace is not frozen.", "zh-Hant": "該工作空間沒有被凍結" },
  "该用户不是这个空间的成员": { en: "This person is not a member of this workspace.", "zh-Hant": "該使用者不是這個空間的成員" },
  "这个空间还没有启用该应用，授予使用资格没有意义": { en: "This workspace has not enabled the application yet, so granting access to it would have no effect.", "zh-Hant": "這個空間還沒有啟用該應用，授予使用資格沒有意義" },
  "不能把所有权移交给自己": { en: "You cannot transfer ownership to yourself.", "zh-Hant": "不能把所有權移交給自己" },
  "接收人必须是这个空间里账户状态正常的成员": { en: "The new owner must be a member of this workspace with an active account.", "zh-Hant": "接收人必須是這個空間裡帳戶狀態正常的成員" },
  "请稍后再发送邀请": { en: "Wait a moment before sending this invitation again.", "zh-Hant": "請稍後再傳送邀請" },
  "已接受、已拒绝或已撤销的邀请不能重发": { en: "An invitation that was accepted, declined or revoked cannot be resent.", "zh-Hant": "已接受、已拒絕或已撤銷的邀請不能重新傳送" },
  "找不到该邀请": { en: "That invitation was not found.", "zh-Hant": "找不到該邀請" },
  "要退出这个工作空间，请使用「退出工作空间」": { en: "To leave this workspace, use “Leave workspace”.", "zh-Hant": "要退出這個工作空間，請使用「退出工作空間」" },
  "这个邀请已经被接受了": { en: "This invitation has already been accepted.", "zh-Hant": "這個邀請已經被接受了" },
  "请选择接收所有权的成员": { en: "Choose the member who will receive ownership.", "zh-Hant": "請選擇接收所有權的成員" },
  "当前组织邀请策略不允许此操作": { en: "The organization's invitation policy does not allow this.", "zh-Hant": "目前組織的邀請政策不允許此操作" },
  "该组织当前不可用": { en: "This organization is currently unavailable.", "zh-Hant": "此組織目前不可用" },
  "该邮箱已经是这个空间的成员": { en: "This email address is already a member of this workspace.", "zh-Hant": "此電郵已經是這個空間的成員" },
  "只有空间管理员或所有者可以查看邀请": { en: "Only a workspace administrator or owner can view invitations.", "zh-Hant": "只有空間管理員或所有者可以查看邀請" },
  "只有空间管理员或所有者可以邀请成员": { en: "Only a workspace administrator or owner can invite members.", "zh-Hant": "只有空間管理員或所有者可以邀請成員" },
  "只有空间管理员或所有者可以管理成员": { en: "Only a workspace administrator or owner can manage members.", "zh-Hant": "只有空間管理員或所有者可以管理成員" },
  "只有空间所有者可以移交所有权": { en: "Only a workspace owner can transfer ownership.", "zh-Hant": "只有空間所有者可以移交所有權" },
  "只能修改尚未接受、未撤销、未过期的邀请": { en: "Only an invitation that is not accepted, revoked or expired can be changed.", "zh-Hant": "只能修改尚未接受、未撤銷、未過期的邀請" },
  "不能邀请比自己权限更高的角色": { en: "You cannot invite a role above your own", "zh-Hant": "不能邀請比自己權限更高的角色" },
  "不能授予比自己更高的角色": { en: "You cannot grant a role above your own.", "zh-Hant": "不能授予比自己更高的角色" },
  "不能管理与自己同级或更高角色的成员": { en: "You cannot manage members with the same or a higher role.", "zh-Hant": "不能管理與自己同級或更高角色的成員" },
  "这个工作空间只有 {p0} 位正常的所有者。建议再指定一位，以免无人能管理。": { en: "This workspace has only {p0} active owner. Appoint another so it can still be managed if that owner becomes unavailable.", "zh-Hant": "這個工作空間只有 {p0} 位正常的所有者。建議再指定一位，以免無人能管理。" },

  // ---- undoing the assignment of the historical data (overview card and its confirmation)
  "历史数据已指派": { en: "Historical data assigned", "zh-Hant": "歷史資料已指派" },
  "账户体系建立之前的数据已经指派给下列账户。如果指派错了，可以撤销：数据回到任何账户都无法读取的待认领状态，不删除任何内容，之后可以重新指派。": { en: "The data from before the account system has been assigned to the accounts below. If that was a mistake, undo it: the data goes back to waiting for assignment, where no account can read it. Nothing is deleted, and it can be assigned again.", "zh-Hant": "帳戶體系建立之前的資料已經指派給下列帳戶。如果指派錯了，可以撤銷：資料回到任何帳戶都無法讀取的待認領狀態，不刪除任何內容，之後可以重新指派。" },
  "这个空间目前被冻结。撤销指派会同时结束冻结：数据直接回到待认领状态，中间不会重新开放给这些账户。": { en: "This workspace is frozen. Undoing the assignment ends the freeze with it: the data goes straight back to waiting for assignment and is not reopened to these accounts in between.", "zh-Hant": "這個空間目前被凍結。撤銷指派會同時結束凍結：資料直接回到待認領狀態，中間不會重新開放給這些帳戶。" },
  "它目前被冻结；冻结随撤销一并结束，不需要先解冻。": { en: "It is frozen at the moment; the freeze ends with the undo, so there is no need to unfreeze it first.", "zh-Hant": "它目前被凍結；凍結隨撤銷一併結束，不需要先解凍。" },
  "再次指派之后：经批准的凭据只有在数据回到当初批准它们的账户时才重新生效；运维直接登记的服务账户随空间一起恢复。": { en: "After a new assignment, an approved credential works again only if the data goes back to the account that approved it; a service account an operator registered directly works again with the workspace.", "zh-Hant": "再次指派之後：經批准的憑據只有在資料回到當初批准它們的帳戶時才重新生效；運維直接登記的服務帳戶隨空間一起恢復。" },
  "{count} 个排队或运行中的生成任务会停止：剩余样本被取消，已生成的结果保留；再次指派之后需要重新运行。": { en: { one: "{count} queued or running generation job stops: its remaining samples are cancelled and the results already generated are kept. It has to be run again after a new assignment.", other: "{count} queued or running generation jobs stop: their remaining samples are cancelled and the results already generated are kept. They have to be run again after a new assignment." }, "zh-Hant": "{count} 個排隊或執行中的生成任務會停止：剩餘樣本被取消，已生成的結果保留；再次指派之後需要重新執行。" },
  "不会删除任何数据：{projects}和 {templates}原样保留。": { en: "No data is deleted: {projects} and {templates} stay as they are.", "zh-Hant": "不會刪除任何資料：{projects}和 {templates}原樣保留。" },
  "{count} 个模板": { en: { one: "{count} template", other: "{count} templates" }, "zh-Hant": "{count} 個範本" },
  "{count} 位成员": { en: { one: "{count} member", other: "{count} members" }, "zh-Hant": "{count} 位成員" },
  "在再次指派之前，任何账户都不能打开它，包括平台管理员。": { en: "Until it is assigned again, no account can open it, platform administrators included.", "zh-Hant": "在再次指派之前，任何帳戶都不能開啟它，包括平台管理員。" },
  "例外：{count} 个由运维签发的 dw_ 访问密钥不受撤销影响，和指派之前一样可以读写这些数据，直到运维撤销它们。": { en: { one: "Exception: {count} dw_ access key issued by an operator is not affected by the undo. As before the assignment, it can read and write this data until an operator revokes it.", other: "Exception: {count} dw_ access keys issued by an operator are not affected by the undo. As before the assignment, they can read and write this data until an operator revokes them." }, "zh-Hant": "例外：{count} 個由運維簽發的 dw_ 存取金鑰不受撤銷影響，和指派之前一樣可以讀寫這些資料，直到運維撤銷它們。" },
  "{projects}，{members}。请选择接收这个工作空间的已有账户。": { en: "{projects}, {members}. Choose an existing account to receive this workspace.", "zh-Hant": "{projects}，{members}。請選擇接收這個工作空間的已有帳戶。" },
  "已把包含 {projects}的工作空间指派给 {email}。": { en: "Assigned the workspace with {projects} to {email}.", "zh-Hant": "已把包含 {projects}的工作空間指派給 {email}。" },
  "正在读取指派情况…": { en: "Reading the assignment…", "zh-Hant": "正在讀取指派情況…" },
  "刷新概览": { en: "Refresh overview", "zh-Hant": "重新整理概覽" },
  "所有者账户": { en: "Owner accounts", "zh-Hant": "擁有者帳戶" },
  "没有所有者": { en: "No owner", "zh-Hant": "沒有擁有者" },
  "成员人数": { en: "Members", "zh-Hant": "成員人數" },
  "共 {count} 位": { en: "{count} in total", "zh-Hant": "共 {count} 位" },
  "指派时间": { en: "Assigned on", "zh-Hant": "指派時間" },
  "没有找到指派记录": { en: "No record of the assignment was found", "zh-Hant": "沒有找到指派記錄" },
  "指派人": { en: "Assigned by", "zh-Hant": "指派人" },
  "{count} 个项目": { en: { one: "{count} project", other: "{count} projects" }, "zh-Hant": "{count} 個專案" },
  "撤销指派": { en: "Undo assignment", "zh-Hant": "撤銷指派" },
  "例如：指派给了错误的账户": { en: "For example: assigned to the wrong account", "zh-Hant": "例如：指派給了錯誤的帳戶" },
  "已撤销指派：{count} 位成员失去访问，数据全部保留。": { en: { one: "Assignment undone: {count} member lost access. All data is kept.", other: "Assignment undone: {count} members lost access. All data is kept." }, "zh-Hant": "已撤銷指派：{count} 位成員失去存取權，資料全部保留。" },
  "「{name}」将回到待认领状态。": { en: "“{name}” goes back to waiting for assignment.", "zh-Hant": "「{name}」將回到待認領狀態。" },
  "以下 {count} 位成员将失去访问：": { en: { one: "{count} member will lose access:", other: "These {count} members will lose access:" }, "zh-Hant": "以下 {count} 位成員將失去存取權：" },
  "将失去访问的成员": { en: "Members who will lose access", "zh-Hant": "將失去存取權的成員" },
  "这个空间目前没有成员。": { en: "This workspace has no members at the moment.", "zh-Hant": "這個空間目前沒有成員。" },
  "{count} 个尚未接受的邀请将被撤回。": { en: { one: "{count} invitation not yet accepted will be withdrawn.", other: "{count} invitations not yet accepted will be withdrawn." }, "zh-Hant": "{count} 個尚未接受的邀請將被撤回。" },
  "{count} 个仍可使用的应用登录凭据将被撤销。": { en: { one: "{count} application sign-in token that still works will be revoked.", other: "{count} application sign-in tokens that still work will be revoked." }, "zh-Hant": "{count} 個仍可使用的應用登入憑據將被撤銷。" },
  "这个空间的服务账户（{p0} 个）和经批准的 API 凭据（{p1} 项）将停止工作，但不会被删除。": { en: "This workspace's service accounts ({p0}) and approved API credentials ({p1}) stop working, but are not deleted.", "zh-Hant": "這個空間的服務帳戶（{p0} 個）和經批准的 API 憑據（{p1} 項）將停止運作，但不會被刪除。" },
  "名称改回「{name}」。": { en: "The name goes back to “{name}”.", "zh-Hant": "名稱改回「{name}」。" },
  "名称「{name}」保持不变。": { en: "The name “{name}” stays as it is.", "zh-Hant": "名稱「{name}」保持不變。" },
  // the account service's refusals of the undo (admin_routes.unclaim / unclaim_preview)
  "只有历史数据空间可以撤销指派，这个工作空间不是": { en: "Only the historical data workspace can have its assignment undone, and this workspace is not it.", "zh-Hant": "只有歷史資料空間可以撤銷指派，這個工作空間不是" },
  "这个工作空间已被删除，不能撤销指派": { en: "This workspace has been deleted, so its assignment cannot be undone.", "zh-Hant": "這個工作空間已被刪除，不能撤銷指派" },
  "这个工作空间属于一个组织，不能在平台管理中撤销指派": { en: "This workspace belongs to an organization; its assignment cannot be undone from platform administration.", "zh-Hant": "這個工作空間屬於一個組織，不能在平台管理中撤銷指派" },
  "这份历史数据目前没有指派给任何人，无需撤销": { en: "This historical data is not assigned to anyone right now, so there is nothing to undo.", "zh-Hant": "這份歷史資料目前沒有指派給任何人，無需撤銷" },
  "安全日志暂时无法写入，撤销指派没有执行，什么都没有改变。请检查安全日志；需要立即停止访问时，可以先冻结这个空间": { en: "The security journal cannot be written right now, so the assignment was not undone and nothing changed. Check the security journal; to stop access immediately, freeze this workspace first.", "zh-Hant": "安全日誌暫時無法寫入，撤銷指派沒有執行，什麼都沒有改變。請檢查安全日誌；需要立即停止存取時，可以先凍結這個空間" },
  "你确认之后，这个空间的成员发生了变化。请重新查看将失去访问的人，再确认撤销": { en: "The members of this workspace changed after you looked. Review who will lose access again, then confirm the undo.", "zh-Hant": "你確認之後，這個空間的成員發生了變化。請重新查看將失去存取權的人，再確認撤銷" },
  // Account detail dialog (admin coverage A13)
  "查看详情": { en: "View details", "zh-Hant": "查看詳情" },
  "{role}（到期：{time}）": { en: "{role} (expires {time})", "zh-Hant": "{role}（到期：{time}）" },
  "不是任何工作空间的成员。": { en: "Not a member of any workspace.", "zh-Hant": "不是任何工作空間的成員。" },
  "不是任何组织的成员。": { en: "Not a member of any organization.", "zh-Hant": "不是任何組織的成員。" },
  "加入时间": { en: "Joined", "zh-Hant": "加入時間" },
  "名称不可见：你的角色不能查看工作空间列表": { en: "Name hidden: your role cannot view the workspace list", "zh-Hant": "名稱不可見：你的角色不能檢視工作空間列表" },
  "名称不可见：你的角色不能查看组织列表": { en: "Name hidden: your role cannot view the organization list", "zh-Hant": "名稱不可見：你的角色不能檢視組織列表" },
  "在线会话（{count} 个）": { en: "Active sessions ({count})", "zh-Hant": "線上工作階段（{count} 個）" },
  "地址": { en: "Address", "zh-Hant": "位址" },
  "地址是登录时记录的来源地址，不推断位置；撤销会话在账户行的菜单里。": { en: "The address is the source recorded at sign-in; no location is inferred. Revoking sessions is in the account row's menu.", "zh-Hant": "位址是登入時記錄的來源位址，不推斷位置；撤銷工作階段在帳戶列的選單裡。" },
  "外部身份": { en: "External identities", "zh-Hant": "外部身分" },
  "外部身份（{provider}）": { en: "External identity ({provider})", "zh-Hant": "外部身分（{provider}）" },
  "安全时间线": { en: "Security timeline", "zh-Hant": "安全時間線" },
  "工作空间（{count} 个）": { en: "Workspaces ({count})", "zh-Hant": "工作空間（{count} 個）" },
  "已同步到多台设备": { en: "Synced across devices", "zh-Hant": "已同步到多台裝置" },
  "已开启，剩余恢复码 {count} 个": { en: "On; {count} recovery codes left", "zh-Hant": "已開啟，剩餘復原碼 {count} 個" },
  "已设置": { en: "Set", "zh-Hant": "已設定" },
  "已验证（{time}）": { en: "Verified ({time})", "zh-Hant": "已驗證（{time}）" },
  "应用身份：由它所属的应用管理，不是一个人。": { en: "Application identity: managed by its application, not a person.", "zh-Hant": "應用身分：由它所屬的應用程式管理，不是一個人。" },
  "当前没有在线会话。": { en: "No active sessions right now.", "zh-Hant": "目前沒有線上工作階段。" },
  "提供商已停用": { en: "Provider disabled", "zh-Hant": "提供者已停用" },
  "无": { en: "None", "zh-Hant": "無" },
  "最近 {count} 条与该账户有关的登录、安全与管理事件；完整记录在审计页。": { en: "The latest {count} sign-in, security and administration events about this account; the full log is on the audit page.", "zh-Hant": "最近 {count} 條與該帳戶有關的登入、安全與管理事件；完整記錄在稽核頁。" },
  "最近使用：{time}": { en: "Last used: {time}", "zh-Hant": "最近使用：{time}" },
  "最近更新": { en: "Last updated", "zh-Hant": "最近更新" },
  "未使用过": { en: "Never used", "zh-Hant": "未使用過" },
  "未命名": { en: "Unnamed", "zh-Hant": "未命名" },
  "未开启": { en: "Off", "zh-Hant": "未開啟" },
  "正在加载账户详情…": { en: "Loading account details…", "zh-Hant": "正在載入帳戶詳情…" },
  "没有失败的登录记录。": { en: "No failed sign-ins on record.", "zh-Hant": "沒有失敗的登入記錄。" },
  "没有相关事件。": { en: "No related events.", "zh-Hant": "沒有相關事件。" },
  "登录退避": { en: "Sign-in backoff", "zh-Hant": "登入退避" },
  "登记于 {time}": { en: "Registered {time}", "zh-Hant": "登記於 {time}" },
  "组织（{count} 个）": { en: "Organizations ({count})", "zh-Hant": "組織（{count} 個）" },
  "该账户的平台权限高于你的：可以查看，不能对它执行管理操作。": { en: "This account's platform permissions outrank yours: you can view it, not act on it.", "zh-Hant": "該帳戶的平台權限高於你的：可以檢視，不能對它執行管理操作。" },
  "账户详情：{email}": { en: "Account details: {email}", "zh-Hant": "帳戶詳情：{email}" },
  "资料": { en: "Profile", "zh-Hant": "資料" },
  "连续失败 {count} 次，最近一次 {time}；下次尝试需等待 {wait}。": { en: "{count} consecutive failures, the last at {time}; the next attempt must wait {wait}.", "zh-Hant": "連續失敗 {count} 次，最近一次 {time}；下次嘗試需等待 {wait}。" },
  "连续失败 {count} 次，最近一次 {time}；现在可以重试，成功登录后计数清零。": { en: "{count} consecutive failures, the last at {time}; a new attempt is allowed now, and a successful sign-in resets the count.", "zh-Hant": "連續失敗 {count} 次，最近一次 {time}；現在可以重試，成功登入後計數清零。" },
  "需要审计查看权限才能显示时间线；完整记录在审计页。": { en: "Showing the timeline needs the audit view permission; the full log is on the audit page.", "zh-Hant": "需要稽核檢視權限才能顯示時間線；完整記錄在稽核頁。" },
  // Shared with the account pages, registered here too: the administration page loads only these catalogues.
  "姓名": { en: "Name", "zh-Hant": "姓名" },
  "未验证": { en: "Not verified", "zh-Hant": "未驗證" },
  "未设置": { en: "Not set", "zh-Hant": "未設定" },
  "可导出": { en: "May export", "zh-Hant": "可匯出" },
  "设备": { en: "Device", "zh-Hant": "裝置" },
  "最近活动": { en: "Last active", "zh-Hant": "最近活動" },
  "保持登录": { en: "Stays signed in", "zh-Hant": "保持登入" },
  // Sign-in methods and devices, worded as the account's own page words them.
  "{platform} 上的 {browser}": { en: "{browser} on {platform}", "zh-Hant": "{platform} 上的 {browser}" },
  "未知设备": { en: "Unknown device", "zh-Hant": "未知裝置" },
  "口令与动态验证码": { en: "Password and authenticator code", "zh-Hant": "密碼與動態驗證碼" },
  "口令与恢复码": { en: "Password and recovery code", "zh-Hant": "密碼與復原碼" },
  "安全密钥（已验证身份）": { en: "Passkey (identity verified)", "zh-Hant": "安全金鑰（已驗證身分）" },
  "邮箱验证码与动态验证码": { en: "Email code and authenticator code", "zh-Hant": "電子郵件驗證碼與動態驗證碼" },
  "邮箱验证码与恢复码": { en: "Email code and recovery code", "zh-Hant": "電子郵件驗證碼與復原碼" },
  "{method}与动态验证码": { en: "{method} and authenticator code", "zh-Hant": "{method}與動態驗證碼" },
  "{method}与恢复码": { en: "{method} and recovery code", "zh-Hant": "{method}與復原碼" },
  // Account detail per-object actions (admin coverage A17).
  "结束此设备的登录": { en: "Sign this device out", "zh-Hant": "結束此裝置的登入" },
  "移除此安全密钥": { en: "Remove this passkey", "zh-Hant": "移除此安全金鑰" },
  "解除此外部身份": { en: "Unlink this external identity", "zh-Hant": "解除此外部身分" },
  "你的平台角色可以查看这些信息，但不能对其执行操作。": { en: "Your platform role can view this, not act on it.", "zh-Hant": "你的平台角色可以檢視這些資訊，但不能對其執行操作。" },
  "{email}：该设备的登录与它签发的应用访问将立即结束；其他设备不受影响。本人会收到安全通知。": { en: "{email}: this device's sign-in and the application access it minted end immediately; other devices are unaffected. The person gets a security notice.", "zh-Hant": "{email}：該裝置的登入與它簽發的應用程式存取將立即結束；其他裝置不受影響。本人會收到安全通知。" },
  "{email}：该安全密钥将不能再用于登录；若它是账户唯一的登录方式，服务器会拒绝。本人会收到安全通知。": { en: "{email}: this passkey can no longer sign in; if it is the account's only way in, the server refuses. The person gets a security notice.", "zh-Hant": "{email}：該安全金鑰將不能再用於登入；若它是帳戶唯一的登入方式，伺服器會拒絕。本人會收到安全通知。" },
  "{email}：该外部账户的下一次登录将不再进入此账户；若它是唯一的登录方式，服务器会拒绝。本人会收到安全通知。": { en: "{email}: the next sign-in with this external account no longer reaches this account; if it is the only way in, the server refuses. The person gets a security notice.", "zh-Hant": "{email}：該外部帳戶的下一次登入將不再進入此帳戶；若它是唯一的登入方式，伺服器會拒絕。本人會收到安全通知。" },
  "已结束该设备的登录。": { en: "The device has been signed out.", "zh-Hant": "已結束該裝置的登入。" },
  "已移除该安全密钥。": { en: "The passkey has been removed.", "zh-Hant": "已移除該安全金鑰。" },
  "已解除该外部身份。": { en: "The external identity has been unlinked.", "zh-Hant": "已解除該外部身分。" },
  // Refusals of the account detail's per-object actions, worded for the administrator.
  "找不到该账户的这个在线会话，可能已经结束。": { en: "This session of the account was not found; it may have ended already.", "zh-Hant": "找不到該帳戶的這個線上工作階段，可能已經結束。" },
  "找不到该账户的这个外部身份，可能已经解除。": { en: "This external identity of the account was not found; it may have been unlinked already.", "zh-Hant": "找不到該帳戶的這個外部身分，可能已經解除。" },
  "找不到该账户的这个安全密钥，可能已经移除。": { en: "This passkey of the account was not found; it may have been removed already.", "zh-Hant": "找不到該帳戶的這個安全金鑰，可能已經移除。" },
  "这是该账户唯一的登录方式，不能解除关联；需要停止访问请停用账户。": { en: "This is the account's only way to sign in, so it cannot be unlinked; to stop access, suspend the account.", "zh-Hant": "這是該帳戶唯一的登入方式，不能解除關聯；需要停止存取請停用帳戶。" },
  "这是该账户唯一的登录方式，不能移除；需要停止访问请停用账户。": { en: "This is the account's only way to sign in, so it cannot be removed; to stop access, suspend the account.", "zh-Hant": "這是該帳戶唯一的登入方式，不能移除；需要停止存取請停用帳戶。" },
  // Administrator second-factor policy (admin coverage A11).
  "策略已更改，请刷新后重试。": { en: "The policy changed in the meantime; reload and try again.", "zh-Hant": "策略已變更，請重新整理後重試。" },
  "至少需要一名可用的最高管理员已登记安全密钥或动态验证码，才能要求管理员使用第二因素。": { en: "At least one usable super administrator must have enrolled a passkey or an authenticator before administrators can be required to use a second factor.", "zh-Hant": "至少需要一名可用的最高管理員已登記安全金鑰或動態驗證碼，才能要求管理員使用第二因素。" },
  "请选择有效的第二因素策略。": { en: "Choose a valid second-factor policy.", "zh-Hant": "請選擇有效的第二因素策略。" },
  "不要求（沿用现有规则）": { en: "Not required (existing rule)", "zh-Hant": "不要求（沿用現有規則）" },
  "要求管理员使用第二因素": { en: "Required for administrators", "zh-Hant": "要求管理員使用第二因素" },
  "管理员第二因素": { en: "Administrator second factor", "zh-Hant": "管理員第二因素" },
  "要求后，平台管理员的登录必须包含第二个因素（动态验证码或经用户验证的安全密钥）才能使用平台管理与 API 管理；只验证口令的登录会被锁在管理之外，账户本身不受影响。": { en: "Once required, a platform administrator's sign-in must include a second factor (an authenticator code or a user-verified passkey) before platform or API management can be used; a password-only sign-in is locked out of management, the account itself is unaffected.", "zh-Hant": "要求後，平台管理員的登入必須包含第二個因素（動態驗證碼或經使用者驗證的安全金鑰）才能使用平台管理與 API 管理；只驗證口令的登入會被鎖在管理之外，帳戶本身不受影響。" },
  "可用最高管理员 {total} 人，其中 {enrolled} 人已登记安全密钥或动态验证码。": { en: "{total} usable super administrators, {enrolled} of them with a passkey or an authenticator enrolled.", "zh-Hant": "可用最高管理員 {total} 人，其中 {enrolled} 人已登記安全金鑰或動態驗證碼。" },
  "你自己尚未登记安全密钥或动态验证码：要求第二因素后，你的下一次管理操作会被锁定，直到登记并重新登录。": { en: "You have not enrolled a passkey or an authenticator yourself: once a second factor is required, your next management action is locked until you enrol and sign in again.", "zh-Hant": "你自己尚未登記安全金鑰或動態驗證碼：要求第二因素後，你的下一次管理操作會被鎖定，直到登記並重新登入。" },
  "你本次登录只验证了口令：要求第二因素后，需要用安全密钥确认或退出后用口令和动态验证码重新登录，才能继续管理。": { en: "This sign-in proved the password only: once a second factor is required, confirm with a passkey or sign out and sign in again with the password and the authenticator code to continue managing.", "zh-Hant": "你本次登入只驗證了口令：要求第二因素後，需要用安全金鑰確認或登出後用口令和動態驗證碼重新登入，才能繼續管理。" },
  "新的第二因素策略": { en: "New second-factor policy", "zh-Hant": "新的第二因素策略" },
  "只有至少一名可用的最高管理员已登记第二因素时才能开启；策略只约束平台管理，不改变账户登录本身。": { en: "It can be required only while at least one usable super administrator has a second factor enrolled; the policy binds platform management only, not account sign-in itself.", "zh-Hant": "只有至少一名可用的最高管理員已登記第二因素時才能開啟；策略只約束平台管理，不改變帳戶登入本身。" },
  "你的平台角色可以查看第二因素策略，但不能修改。": { en: "Your platform role can view the second-factor policy, not change it.", "zh-Hant": "你的平台角色可以檢視第二因素策略，但不能修改。" },
  "保存第二因素策略": { en: "Save second-factor policy", "zh-Hant": "儲存第二因素策略" },
  "第二因素策略将从「{before}」改为「{after}」。": { en: "The second-factor policy changes from “{before}” to “{after}”.", "zh-Hant": "第二因素策略將從「{before}」改為「{after}」。" },
  "第二因素策略已保存": { en: "Second-factor policy saved", "zh-Hant": "第二因素策略已儲存" },
  "平台要求管理员使用第二因素：请用安全密钥确认身份，或先登记安全密钥/动态验证码后重新登录": { en: "The platform requires administrators to use a second factor: confirm with your passkey, or enrol a passkey or an authenticator first and sign in again.", "zh-Hant": "平台要求管理員使用第二因素：請用安全金鑰確認身分，或先登記安全金鑰/動態驗證碼後重新登入" },
  // A20: deletion requests as administration sees them
  "注销申请": { en: "Deletion requests", "zh-Hant": "註銷申請" },
  "待删除账户": { en: "Accounts pending deletion", "zh-Hant": "待刪除帳戶" },
  "已到期未处理": { en: "Past due, not yet finalized", "zh-Hant": "已到期未處理" },
  "待写安全记录": { en: "Withdrawal record owed", "zh-Hant": "待寫安全記錄" },
  "本人申请注销后账户立即停用，7 天宽限期内可用邮件里的链接取消；到期后由每小时一次的后台任务终结并写入安全记录。已到期未处理或待写安全记录不为零时，可以立即运行一次到期处理；它不会提前任何申请的到期时间。": { en: "When a person requests deletion the account is disabled at once; during the 7-day grace period they can cancel through the link in their mail. After that an hourly background sweep finalizes the deletion and writes the withdrawal record. When past-due requests or owed records are not zero, the sweep can be run once now; it never brings any request's due date forward.", "zh-Hant": "本人申請註銷後帳戶立即停用，7 天寬限期內可用郵件裡的連結取消；到期後由每小時一次的背景任務終結並寫入安全記錄。已到期未處理或待寫安全記錄不為零時，可以立即執行一次到期處理；它不會提前任何申請的到期時間。" },
  "立即处理到期删除": { en: "Finalize due deletions now", "zh-Hant": "立即處理到期刪除" },
  "已过宽限期的 {count} 个注销申请将被终结：账户变为已删除，登录方式与会话全部移除，组织贡献保留；待写的安全记录会补写。宽限期内的申请不受影响。": { en: "{count} deletion request(s) past their grace period will be finalized: the accounts become deleted, every sign-in method and session is removed, organization contributions stay; owed withdrawal records are written. Requests still in their grace period are untouched.", "zh-Hant": "已過寬限期的 {count} 個註銷申請將被終結：帳戶變為已刪除，登入方式與工作階段全部移除，組織貢獻保留；待寫的安全記錄會補寫。寬限期內的申請不受影響。" },
  "已终结 {count} 个到期的注销申请。": { en: "Finalized {count} due deletion request(s).", "zh-Hant": "已終結 {count} 個到期的註銷申請。" },
  "到期：{time}": { en: "Due: {time}", "zh-Hant": "到期：{time}" },
  "重发取消注销链接": { en: "Re-send cancellation link", "zh-Hant": "重發取消註銷連結" },
  "该账户没有待处理的注销申请": { en: "This account has no pending deletion request", "zh-Hant": "該帳戶沒有待處理的註銷申請" },
  "该账户没有待处理的注销申请。": { en: "This account has no pending deletion request.", "zh-Hant": "該帳戶沒有待處理的註銷申請。" },
  "到期删除未能完成，已记录失败；请查看服务日志后重试。": { en: "Finalizing the due deletions failed; the failure is recorded. Check the service log and try again.", "zh-Hant": "到期刪除未能完成，已記錄失敗；請查看服務日誌後重試。" },
  "新的取消注销链接将发送到该账户当前邮箱，到期时间不变；之前的链接失效。管理员不会看到链接，也不能代为取消注销。": { en: "A new cancellation link goes to the account's current e-mail address; the due date is unchanged and the earlier link stops working. Administrators never see the link and cannot cancel the deletion on the person's behalf.", "zh-Hant": "新的取消註銷連結將傳送到該帳戶目前的電子郵件地址，到期時間不變；先前的連結失效。管理員不會看到連結，也不能代為取消註銷。" },
  "取消注销链接已排队，请在邮件投递中查看状态。": { en: "The cancellation link is queued; see its status under mail delivery.", "zh-Hant": "取消註銷連結已排入佇列，請在郵件投遞中查看狀態。" },
  "本人于 {requested} 申请注销，{due} 到期删除。": { en: "Deletion requested by the person on {requested}; due on {due}.", "zh-Hant": "本人於 {requested} 申請註銷，{due} 到期刪除。" },
  "已过到期时间，等待后台任务终结；可在平台概览页立即处理。": { en: "Past due and waiting for the background sweep; it can be finalized now from the platform overview.", "zh-Hant": "已過到期時間，等待背景任務終結；可在平台概覽頁立即處理。" },
  "取消注销的链接仍然有效，到期前本人可随时取消。": { en: "The cancellation link is still valid; the person can cancel any time before the due date.", "zh-Hant": "取消註銷的連結仍然有效，到期前本人可隨時取消。" },
  "取消注销的链接已失效；可在账户行的菜单里重发。": { en: "The cancellation link is no longer valid; it can be re-sent from the account row's menu.", "zh-Hant": "取消註銷的連結已失效；可在帳戶列的選單裡重發。" },
  "没有注销申请。若本人现在申请注销，将被拒绝：{rules}。": { en: "No deletion request. A request made now would be refused: {rules}.", "zh-Hant": "沒有註銷申請。若本人現在申請註銷，將被拒絕：{rules}。" },
  "没有注销申请；本人现在可以申请注销。": { en: "No deletion request; the person could request one now.", "zh-Hant": "沒有註銷申請；本人現在可以申請註銷。" },
  "最后一名可用的最高管理员": { en: "the last usable super administrator", "zh-Hant": "最後一名可用的最高管理員" },
  "共享工作空间的所有权或管理权未移交": { en: "ownership or administration of a shared workspace not yet handed over", "zh-Hant": "共享工作空間的所有權或管理權未移交" },
  "组织的所有权或管理权未移交": { en: "ownership or administration of an organization not yet handed over", "zh-Hant": "組織的所有權或管理權未移交" },
  "仍在恢复期内的已删除工作空间没有其他可用的拥有者或管理者": { en: "a deleted workspace still within its recovery period has no other usable owner or manager", "zh-Hant": "仍在恢復期內的已刪除工作空間沒有其他可用的擁有者或管理者" },
  // A17: browser push devices in the account detail
  "推送设备（{count} 个）": { en: "Push devices ({count})", "zh-Hant": "推播裝置（{count} 個）" },
  "推送设备": { en: "Push devices", "zh-Hant": "推播裝置" },
  "开启时间": { en: "Enabled on", "zh-Hant": "開啟時間" },
  "最近推送": { en: "Last push", "zh-Hant": "最近推播" },
  "未推送过": { en: "Never pushed", "zh-Hant": "未推播過" },
  "最近 {count} 次推送失败": { en: "Last {count} pushes failed", "zh-Hant": "最近 {count} 次推播失敗" },
  "可接收": { en: "Receiving", "zh-Hant": "可接收" },
  "登录已结束，不再接收": { en: "Session ended; no longer receiving", "zh-Hant": "登入已結束，不再接收" },
  "撤销全部推送订阅": { en: "Revoke all push subscriptions", "zh-Hant": "撤銷全部推播訂閱" },
  "没有开启推送的设备。": { en: "No device has push enabled.", "zh-Hant": "沒有開啟推播的裝置。" },
  "浏览器": { en: "Browser", "zh-Hant": "瀏覽器" },
  "{email}：该账户所有浏览器的推送订阅将被撤销，这些设备不再收到系统通知；登录与登录方式不变。本人会收到安全通知，可在自己的设备上重新开启。": { en: "{email}: every browser push subscription of this account is revoked and those devices stop receiving system notifications; sessions and sign-in methods are unchanged. The person gets a security notice and can enable push again on a device they hold.", "zh-Hant": "{email}：該帳戶所有瀏覽器的推播訂閱將被撤銷，這些裝置不再收到系統通知；登入與登入方式不變。本人會收到安全通知，可在自己的裝置上重新開啟。" },
  "已撤销全部推送订阅。": { en: "All push subscriptions revoked.", "zh-Hant": "已撤銷全部推播訂閱。" },
  // A13: the Agent content-safety suspension in the account detail
  "Agent 使用权限": { en: "Agent access", "zh-Hant": "Agent 使用權限" },
  "自 {time} 起暂停；理由：{reason}": { en: "Suspended since {time}; reason: {reason}", "zh-Hant": "自 {time} 起暫停；理由：{reason}" },
  "操作者：{email}": { en: "By: {email}", "zh-Hant": "操作者：{email}" },
  "关联举报 {id}": { en: "Linked report {id}", "zh-Hant": "關聯舉報 {id}" },
  "正常：没有生效中的暂停。": { en: "Normal: no suspension in force.", "zh-Hant": "正常：沒有生效中的暫停。" },
  "{email}：该账户将可以再次使用个人 Agent；理由会记录在审计中。": { en: "{email}: this account can use the personal Agent again; the reason is recorded in the audit log.", "zh-Hant": "{email}：該帳戶將可以再次使用個人 Agent；理由會記錄在稽核中。" },
  "已恢复该账户的 Agent 使用权限。": { en: "Agent access restored for this account.", "zh-Hant": "已恢復該帳戶的 Agent 使用權限。" },
  // A27: registration diagnosis
  "注册诊断": { en: "Registration diagnosis", "zh-Hant": "註冊診斷" },
  "输入邮箱地址，查看该地址注册的每个阶段：地址校验、人机验证、频率限制、账户、验证链接、邮件与激活。不显示验证令牌或邮件正文；查看不写审计。": { en: "Enter an e-mail address to follow its registration stage by stage: address validation, human verification, rate limit, account, verification link, mail and activation. Tokens and mail bodies are never shown; reading is not audited.", "zh-Hant": "輸入電子郵件地址，查看該地址註冊的每個階段：地址校驗、人機驗證、頻率限制、帳戶、驗證連結、郵件與啟用。不顯示驗證權杖或郵件正文；檢視不寫稽核。" },
  "邮箱地址": { en: "E-mail address", "zh-Hant": "電子郵件地址" },
  "要诊断的邮箱地址": { en: "E-mail address to diagnose", "zh-Hant": "要診斷的電子郵件地址" },
  "正在诊断…": { en: "Diagnosing…", "zh-Hant": "正在診斷…" },
  "诊断": { en: "Diagnose", "zh-Hant": "診斷" },
  "地址 {email} · 注册策略：{mode}": { en: "Address {email} · registration policy: {mode}", "zh-Hant": "地址 {email} · 註冊策略：{mode}" },
  "注册阶段": { en: "Registration stages", "zh-Hant": "註冊階段" },
  "阶段": { en: "Stage", "zh-Hant": "階段" },
  "地址校验": { en: "Address validation", "zh-Hant": "地址校驗" },
  "人机验证": { en: "Human verification", "zh-Hant": "人機驗證" },
  "频率限制": { en: "Rate limit", "zh-Hant": "頻率限制" },
  "验证链接": { en: "Verification link", "zh-Hant": "驗證連結" },
  "邮件": { en: "Mail", "zh-Hant": "郵件" },
  "激活": { en: "Activation", "zh-Hant": "啟用" },
  "地址格式可接受": { en: "Address format accepted", "zh-Hant": "地址格式可接受" },
  "通过": { en: "Passed", "zh-Hant": "通過" },
  "未通过": { en: "Failed", "zh-Hant": "未通過" },
  "验证服务出错": { en: "Verifier error", "zh-Hant": "驗證服務出錯" },
  "没有记录": { en: "No record", "zh-Hant": "沒有記錄" },
  "需要审计查看权限": { en: "Needs the audit view permission", "zh-Hant": "需要稽核檢視權限" },
  "未被限流": { en: "Not throttled", "zh-Hant": "未被限流" },
  "曾被限流（已记录）": { en: "Was throttled (on record)", "zh-Hant": "曾被限流（已記錄）" },
  "当前已限流": { en: "Throttled now", "zh-Hant": "目前已限流" },
  "没有账户": { en: "No account", "zh-Hant": "沒有帳戶" },
  "没有签发过": { en: "Never issued", "zh-Hant": "沒有簽發過" },
  "已签发，未使用": { en: "Issued, unused", "zh-Hant": "已簽發，未使用" },
  "已使用": { en: "Used", "zh-Hant": "已使用" },
  "已过期未使用": { en: "Expired unused", "zh-Hant": "已過期未使用" },
  "没有邮件": { en: "No mail", "zh-Hant": "沒有郵件" },
  "已排队": { en: "Queued", "zh-Hant": "已排入佇列" },
  "已发送": { en: "Sent", "zh-Hant": "已傳送" },
  "已丢弃": { en: "Dropped", "zh-Hant": "已捨棄" },
  "需要邮件查看权限": { en: "Needs the mail view permission", "zh-Hant": "需要郵件檢視權限" },
  "尚未激活": { en: "Not activated yet", "zh-Hant": "尚未啟用" },
  "已激活": { en: "Activated", "zh-Hant": "已啟用" },
  "错误": { en: "Error", "zh-Hant": "錯誤" },
  "该地址在最近一小时内请求了 {count} 次（上限 {limit}）；当前已限流，{until}。": { en: "This address made {count} requests in the last hour (limit {limit}); it is throttled now, {until}.", "zh-Hant": "該地址在最近一小時內請求了 {count} 次（上限 {limit}）；目前已限流，{until}。" },
  "至 {time}": { en: "until {time}", "zh-Hant": "至 {time}" },
  "窗口结束后恢复": { en: "until the window ends", "zh-Hant": "視窗結束後恢復" },
  "该地址在最近一小时内请求了 {count} 次（上限 {limit}）；未被限流。": { en: "This address made {count} requests in the last hour (limit {limit}); not throttled.", "zh-Hant": "該地址在最近一小時內請求了 {count} 次（上限 {limit}）；未被限流。" },
  "账户 {id}：{status}，创建于 {time}": { en: "Account {id}: {status}, created {time}", "zh-Hant": "帳戶 {id}：{status}，建立於 {time}" },
  "邮箱验证于 {time}": { en: "e-mail verified {time}", "zh-Hant": "電子郵件驗證於 {time}" },
  "该地址没有账户。": { en: "No account for this address.", "zh-Hant": "該地址沒有帳戶。" },
  "验证链接（{count} 个）": { en: "Verification links ({count})", "zh-Hant": "驗證連結（{count} 個）" },
  "签发于 {issued}，{until} 到期": { en: "issued {issued}, expires {until}", "zh-Hant": "簽發於 {issued}，{until} 到期" },
  "已于 {time} 使用": { en: "used {time}", "zh-Hant": "已於 {time} 使用" },
  "未使用": { en: "unused", "zh-Hant": "未使用" },
  "没有签发过验证链接。": { en: "No verification link has been issued.", "zh-Hant": "沒有簽發過驗證連結。" },
  "邮件（{count} 封）": { en: "Mail ({count})", "zh-Hant": "郵件（{count} 封）" },
  "需要邮件查看权限才能显示邮件投递。": { en: "Showing mail delivery needs the mail view permission.", "zh-Hant": "需要郵件檢視權限才能顯示郵件投遞。" },
  "排队时间": { en: "Queued at", "zh-Hant": "排入佇列時間" },
  "发送时间": { en: "Sent at", "zh-Hant": "傳送時間" },
  "没有邮件。": { en: "No mail.", "zh-Hant": "沒有郵件。" },
  "事件（{count} 条）": { en: "Events ({count})", "zh-Hant": "事件（{count} 筆）" },
  "需要审计查看权限才能显示注册事件与人机验证结果。": { en: "Showing registration events and human-verification results needs the audit view permission.", "zh-Hant": "需要稽核檢視權限才能顯示註冊事件與人機驗證結果。" },
  "注册事件": { en: "Registration events", "zh-Hant": "註冊事件" },
  "来源地址": { en: "Source address", "zh-Hant": "來源位址" },
  "说明": { en: "Detail", "zh-Hant": "說明" },
  "人机验证（{count} 条，同一来源地址、前后 15 分钟内）": { en: "Human verification ({count}, same source address within 15 minutes)", "zh-Hant": "人機驗證（{count} 筆，同一來源位址、前後 15 分鐘內）" },
  // G19: throttles
  "账户登录退避": { en: "Account sign-in backoff", "zh-Hant": "帳戶登入退避" },
  "来源地址登录窗口": { en: "Source-address sign-in window", "zh-Hant": "來源位址登入視窗" },
  "会话重认证失败": { en: "Session re-authentication failures", "zh-Hant": "工作階段重新驗證失敗" },
  "注册（按地址）": { en: "Registration (by address)", "zh-Hant": "註冊（按地址）" },
  "注册（按来源地址）": { en: "Registration (by source address)", "zh-Hant": "註冊（按來源位址）" },
  "邮箱验证码登录（按地址）": { en: "E-mail code sign-in (by address)", "zh-Hant": "電子郵件驗證碼登入（按地址）" },
  "邮箱验证码登录（按来源地址）": { en: "E-mail code sign-in (by source address)", "zh-Hant": "電子郵件驗證碼登入（按來源位址）" },
  "邮箱验证码登录（按账户）": { en: "E-mail code sign-in (by account)", "zh-Hant": "電子郵件驗證碼登入（按帳戶）" },
  "第二因素验证退避": { en: "Second-factor verification backoff", "zh-Hant": "第二因素驗證退避" },
  "最高管理员须由本人通过安全密钥接受提议": { en: "A super administrator must accept the offer in person with a passkey", "zh-Hant": "最高管理員須由本人透過安全金鑰接受提議" },
  "该账户的邮箱尚未验证，不能获得平台权限": { en: "The account's e-mail address is not verified yet, so it cannot receive platform permissions", "zh-Hant": "該帳戶的電子郵件尚未驗證，不能獲得平台權限" },
  "该账户还没有完成邮箱验证，不能获得平台权限": { en: "The account has not completed e-mail verification, so it cannot receive platform permissions", "zh-Hant": "該帳戶還沒有完成電子郵件驗證，不能獲得平台權限" },
  "该账户已停用或正在删除，不能获得平台权限": { en: "The account is suspended or being deleted, so it cannot receive platform permissions", "zh-Hant": "該帳戶已停用或正在刪除，不能獲得平台權限" },
  "该账户没有口令、可验证身份的安全密钥或动态验证码，无法完成管理员身份确认": { en: "The account has no password, user-verifying passkey or authenticator code, so it cannot complete administrator identity confirmation", "zh-Hant": "該帳戶沒有密碼、可驗證身分的安全金鑰或動態驗證碼，無法完成管理員身分確認" },
  "该账户没有口令或可验证身份的安全密钥，无法完成危险操作要求的重新验证": { en: "The account has no password or user-verifying passkey, so it cannot complete the re-authentication that dangerous operations require", "zh-Hant": "該帳戶沒有密碼或可驗證身分的安全金鑰，無法完成危險操作要求的重新驗證" },
  "找回口令（按来源地址）": { en: "Password recovery (by source address)", "zh-Hant": "找回密碼（按來源位址）" },
  "初始化（按来源地址）": { en: "Bootstrap (by source address)", "zh-Hant": "初始化（按來源位址）" },
  "安全密钥登录（按来源地址）": { en: "Passkey sign-in (by source address)", "zh-Hant": "安全金鑰登入（按來源位址）" },
  "安全密钥重认证": { en: "Passkey re-authentication", "zh-Hant": "安全金鑰重新驗證" },
  "账户敏感操作": { en: "Sensitive account actions", "zh-Hant": "帳戶敏感操作" },
  "推送订阅": { en: "Push subscriptions", "zh-Hant": "推播訂閱" },
  "其他": { en: "Other", "zh-Hant": "其他" },
  "没有生效中的限流记录。": { en: "No throttle is in force.", "zh-Hant": "沒有生效中的限流記錄。" },
  "限流记录": { en: "Throttles", "zh-Hant": "限流記錄" },
  "次数": { en: "Count", "zh-Hant": "次數" },
  "首次": { en: "First", "zh-Hant": "首次" },
  "最近": { en: "Latest", "zh-Hant": "最近" },
  "不可见": { en: "Not visible", "zh-Hant": "不可見" },
  "已限流，至 {time}": { en: "Throttled until {time}", "zh-Hant": "已限流，至 {time}" },
  "等待 {wait}": { en: "Waiting {wait}", "zh-Hant": "等待 {wait}" },
  "未生效（下次失败将延长等待）": { en: "Not in force (the next failure lengthens the wait)", "zh-Hant": "未生效（下次失敗將延長等待）" },
  "解除": { en: "Release", "zh-Hant": "解除" },
  "限流状态": { en: "Throttles", "zh-Hant": "限流狀態" },
  "登录退避、来源地址窗口、注册与找回窗口等由服务器持有的限流记录。来源地址只以哈希显示；账户只对能查看账户的角色显示。解除需要理由与重新验证身份，只让下一次尝试从零开始。": { en: "The throttles the server holds: sign-in backoff, source-address windows, registration and recovery windows. Source addresses appear hashed only; accounts only to roles that can view accounts. Releasing one needs a reason and re-authentication, and only lets the next attempt start from zero.", "zh-Hant": "登入退避、來源位址視窗、註冊與找回視窗等由伺服器持有的限流記錄。來源位址只以雜湊顯示；帳戶只對能檢視帳戶的角色顯示。解除需要理由與重新驗證身分，只讓下一次嘗試從零開始。" },
  "仅显示生效中": { en: "Only those in force", "zh-Hant": "僅顯示生效中" },
  "你的平台角色可以查看限流记录，但不能解除。": { en: "Your platform role can view throttles, not release them.", "zh-Hant": "你的平台角色可以檢視限流記錄，但不能解除。" },
  "解除限流": { en: "Release throttle", "zh-Hant": "解除限流" },
  "将解除「{kind}」的这条限流记录（对象：{subject}）。下一次尝试从零开始；不改变口令、会话或任何权限。": { en: "This “{kind}” throttle (subject: {subject}) will be released. The next attempt starts from zero; passwords, sessions and permissions are unchanged.", "zh-Hant": "將解除「{kind}」的這條限流記錄（對象：{subject}）。下一次嘗試從零開始；不改變密碼、工作階段或任何權限。" },
  "限流已解除": { en: "Throttle released", "zh-Hant": "限流已解除" },
  "解除登录退避": { en: "Release sign-in backoff", "zh-Hant": "解除登入退避" },
  "{email}：该账户的登录退避将被解除，下一次登录尝试从零开始；口令、会话与权限不变。": { en: "{email}: this account's sign-in backoff is released and the next sign-in attempt starts from zero; password, sessions and permissions are unchanged.", "zh-Hant": "{email}：該帳戶的登入退避將被解除，下一次登入嘗試從零開始；密碼、工作階段與權限不變。" },
  "已解除该账户的登录退避。": { en: "Sign-in backoff released for this account.", "zh-Hant": "已解除該帳戶的登入退避。" },
  "请选择要解除的限流记录。": { en: "Choose the throttle to release.", "zh-Hant": "請選擇要解除的限流記錄。" },
  "找不到该限流记录，可能已经过期或被解除。": { en: "That throttle was not found; it may have expired or been released.", "zh-Hant": "找不到該限流記錄，可能已經過期或被解除。" },
  // A49: effective access explanation
  "访问": { en: "Access", "zh-Hant": "存取" },
  "解释访问": { en: "Explain access", "zh-Hant": "解釋存取" },
  "应用状态": { en: "Application status", "zh-Hant": "應用程式狀態" },
  "空间成员资格": { en: "Workspace membership", "zh-Hant": "工作空間成員資格" },
  "空间状态": { en: "Workspace status", "zh-Hant": "工作空間狀態" },
  "组织状态与组织成员": { en: "Organization status and membership", "zh-Hant": "組織狀態與組織成員" },
  "空间已启用该应用": { en: "Application enabled in the workspace", "zh-Hant": "工作空間已啟用該應用程式" },
  "使用资格": { en: "Entitlement", "zh-Hant": "使用資格" },
  "不适用": { en: "Not applicable", "zh-Hant": "不適用" },
  "成员即可使用": { en: "Members may use it", "zh-Hant": "成員即可使用" },
  "需逐人授予": { en: "Granted per person", "zh-Hant": "需逐人授予" },
  "无单独记录": { en: "No separate record", "zh-Hant": "無單獨記錄" },
  "组织不可用": { en: "Organization unavailable", "zh-Hant": "組織不可用" },
  "不是该组织的成员": { en: "Not a member of the organization", "zh-Hant": "不是該組織的成員" },
  "按工作空间": { en: "Per workspace", "zh-Hant": "按工作空間" },
  "按组织": { en: "Per organization", "zh-Hant": "按組織" },
  "个人应用": { en: "Personal application", "zh-Hant": "個人應用程式" },
  "平台门户": { en: "Platform portal", "zh-Hant": "平台入口" },
  "不是成员": { en: "Not a member", "zh-Hant": "不是成員" },
  "组织正常且本人是其成员": { en: "Organization active and the person is a member", "zh-Hant": "組織正常且本人是其成員" },
  "不属于组织": { en: "Not in an organization", "zh-Hant": "不屬於組織" },
  "已启用（{time}）": { en: "Enabled ({time})", "zh-Hant": "已啟用（{time}）" },
  "未启用": { en: "Not enabled", "zh-Hant": "未啟用" },
  "该应用不按工作空间授权": { en: "This application is not authorized per workspace", "zh-Hant": "該應用程式不按工作空間授權" },
  "可以打开：{app}（{workspace}）": { en: "Can open: {app} ({workspace})", "zh-Hant": "可以開啟：{app}（{workspace}）" },
  "不能打开：{reason}": { en: "Cannot open: {reason}", "zh-Hant": "不能開啟：{reason}" },
  "访问闸门": { en: "Access gates", "zh-Hant": "存取閘門" },
  "闸门": { en: "Gate", "zh-Hant": "閘門" },
  "逐道闸门的解释与实际检查结果不一致：以实际结果为准，请把这个页面报告给开发者。": { en: "The gate-by-gate explanation disagrees with the real check: the real result stands; please report this page to the developers.", "zh-Hant": "逐道閘門的解釋與實際檢查結果不一致：以實際結果為準，請把這個頁面回報給開發者。" },
  "解释与本人实际打开应用走同一条检查路径，只读取，不以该用户身份行事，也不授予任何访问。": { en: "The explanation runs the same check the person's own request runs; it only reads, never acts as the person and grants no access.", "zh-Hant": "解釋與本人實際開啟應用程式走同一條檢查路徑，只讀取，不以該使用者身分行事，也不授予任何存取。" },
  "正在解释访问…": { en: "Explaining access…", "zh-Hant": "正在解釋存取…" },
  "有效访问：{email} · {workspace}": { en: "Effective access: {email} · {workspace}", "zh-Hant": "有效存取：{email} · {workspace}" },
  "要解释的应用": { en: "Application to explain", "zh-Hant": "要解釋的應用程式" },
  "该工作空间没有启用任何应用。": { en: "This workspace has no application enabled.", "zh-Hant": "該工作空間沒有啟用任何應用程式。" },
  "请指定要解释的工作空间与应用。": { en: "Choose the workspace and application to explain.", "zh-Hant": "請指定要解釋的工作空間與應用程式。" },
  "找不到该应用。": { en: "That application was not found.", "zh-Hant": "找不到該應用程式。" },
  // The verdict's reason: what Apps.check_access says to the refused person (AccountDetail.ACCESS_REASON_SENTENCES),
  // worded as the application shells word the same refusal.
  "该应用不存在": { en: "This application does not exist.", "zh-Hant": "該應用程式不存在" },
  "该应用已停用": { en: "This application has been disabled.", "zh-Hant": "該應用程式已停用" },
  "账户不可用": { en: "Account unavailable", "zh-Hant": "帳戶不可用" },
  "你不是该工作空间的成员": { en: "You are not a member of this workspace.", "zh-Hant": "你不是該工作空間的成員" },
  "你还不属于任何工作空间，请联系管理员邀请你加入": { en: "You do not belong to any workspace yet. Ask an administrator to invite you.", "zh-Hant": "你還不屬於任何工作空間，請聯絡管理員邀請你加入" },
  "该工作空间不可用": { en: "This workspace is unavailable.", "zh-Hant": "該工作空間不可用" },
  "你在该工作空间使用「{app}」的资格已被撤销，请联系工作空间管理员": { en: "Your access to “{app}” in this workspace has been revoked. Contact the workspace administrator.", "zh-Hant": "你在該工作空間使用「{app}」的資格已被撤銷，請聯絡工作空間管理員" },
  "你所在的工作空间尚未启用「{app}」，或你没有使用它的资格": { en: "Your workspace has not enabled “{app}”, or you are not entitled to use it.", "zh-Hant": "你所在的工作空間尚未啟用「{app}」，或你沒有使用它的資格" },
  "该工作空间尚未启用「{app}」": { en: "This workspace has not enabled “{app}”.", "zh-Hant": "該工作空間尚未啟用「{app}」" },
  // The enumeration separator of a list inside a sentence (the account pages' own entry).
  "、": { en: ", ", "zh-Hant": "、" },
  // A32: second-factor adoption
  "第二因素普及": { en: "Second-factor adoption", "zh-Hant": "第二因素普及" },
  "正常账户": { en: "Active accounts", "zh-Hant": "正常帳戶" },
  "已登记任一第二因素": { en: "With any second factor", "zh-Hant": "已登記任一第二因素" },
  "动态验证码": { en: "Authenticator codes", "zh-Hant": "動態驗證碼" },
  "其中可验证身份": { en: "Of which user-verifying", "zh-Hant": "其中可驗證身分" },
  "恢复码已用尽": { en: "Recovery codes exhausted", "zh-Hant": "復原碼已用盡" },
  "可用最高管理员 {total} 人，其中 {enrolled} 人已登记第二因素。只统计正常的个人账户，不含应用身份；这里只有数量，没有任何密钥或验证码。": { en: "{total} usable super administrators, {enrolled} of them with a second factor. Only active person accounts are counted, not application identities; these are counts only, never a key or a code.", "zh-Hant": "可用最高管理員 {total} 人，其中 {enrolled} 人已登記第二因素。只統計正常的個人帳戶，不含應用身分；這裡只有數量，沒有任何金鑰或驗證碼。" },
  // A11: grace period
  "宽限期至 {time}：期内只提醒，不锁定": { en: "Grace period until {time}: a warning only until then, no lock", "zh-Hant": "寬限期至 {time}：期內只提醒，不鎖定" },
  "宽限期（天，0 表示立即生效，最多 {max} 天）": { en: "Grace period (days; 0 applies at once; at most {max} days)", "zh-Hant": "寬限期（天，0 表示立即生效，最多 {max} 天）" },
  "宽限期内，未登记第二因素的管理员只会在账户页看到提醒；到期后才被锁在管理之外。再次保存可重设宽限期。": { en: "During the grace period administrators without a second factor only see a reminder on their account page; the lock applies when it ends. Saving again resets the period.", "zh-Hant": "寬限期內，未登記第二因素的管理員只會在帳戶頁看到提醒；到期後才被鎖在管理之外。再次儲存可重設寬限期。" },
  "宽限期 {days} 天：期内只提醒，不锁定。": { en: "Grace period {days} days: a warning only until then, no lock.", "zh-Hant": "寬限期 {days} 天：期內只提醒，不鎖定。" },
  "没有宽限期：保存后立即生效。": { en: "No grace period: applies as soon as it is saved.", "zh-Hant": "沒有寬限期：儲存後立即生效。" },
  "现有宽限期将结束：要求立即生效。": { en: "The current grace period ends: the requirement applies at once.", "zh-Hant": "現有寬限期將結束：要求立即生效。" },
  "宽限期须为 0 到 30 的整数天。": { en: "The grace period must be a whole number of days from 0 to 30.", "zh-Hant": "寬限期須為 0 到 30 的整數天。" },
  // A5: permission simulation
  "若授予「{role}」": { en: "If “{role}” were granted", "zh-Hant": "若授予「{role}」" },
  "若撤销「{role}」": { en: "If “{role}” were revoked", "zh-Hant": "若撤銷「{role}」" },
  "你现在可以执行这一变化。": { en: "You could make this change now.", "zh-Hant": "你現在可以執行這一變化。" },
  "你现在不能执行这一变化：{reason}": { en: "You could not make this change now: {reason}", "zh-Hant": "你現在不能執行這一變化：{reason}" },
  "该账户已持有这个角色": { en: "The account already holds this role", "zh-Hant": "該帳戶已持有這個角色" },
  "该账户并未持有这个角色": { en: "The account does not hold this role", "zh-Hant": "該帳戶並未持有這個角色" },
  "不能授予自己没有的权限": { en: "Cannot grant permissions you do not hold", "zh-Hant": "不能授予自己沒有的權限" },
  "不能更改自己的授予权限": { en: "Cannot change your own assigning permissions", "zh-Hant": "不能更改自己的授予權限" },
  "该账户的授予目前不计入（邮箱未验证或账户不可用），所以有效权限不会变化。": { en: "This account's grants do not count at present (address unverified or account unavailable), so effective permissions would not change.", "zh-Hant": "該帳戶的授予目前不計入（電子郵件未驗證或帳戶不可用），所以有效權限不會變化。" },
  "将新增权限：{list}": { en: "Would gain: {list}", "zh-Hant": "將新增權限：{list}" },
  "不会新增权限。": { en: "Would gain no permission.", "zh-Hant": "不會新增權限。" },
  "将失去权限：{list}": { en: "Would lose: {list}", "zh-Hant": "將失去權限：{list}" },
  "不会失去权限。": { en: "Would lose no permission.", "zh-Hant": "不會失去權限。" },
  "模拟只按角色目录与计入规则计算，不执行任何变更，也不改变任何授予。": { en: "The simulation is computed from the role catalogue and the counting rules only; nothing is executed and no grant changes.", "zh-Hant": "模擬只按角色目錄與計入規則計算，不執行任何變更，也不改變任何授予。" },
  "模拟一次变化": { en: "Simulate a change", "zh-Hant": "模擬一次變化" },
  "变化": { en: "Change", "zh-Hant": "變化" },
  "授予": { en: "Grant", "zh-Hant": "授予" },
  "正在模拟…": { en: "Simulating…", "zh-Hant": "正在模擬…" },
  "模拟": { en: "Simulate", "zh-Hant": "模擬" },
  "请选择要模拟的变化（授予或撤销）和一个内置角色。": { en: "Choose the change to simulate (grant or revoke) and a built-in role.", "zh-Hant": "請選擇要模擬的變化（授予或撤銷）和一個內建角色。" },
};
registerMessages(governanceMessages);
