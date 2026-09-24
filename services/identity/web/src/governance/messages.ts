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
  "这些数据是账户体系建立之前产生的。它现在属于一个没有任何成员的工作空间，因此没有人能读取它——包括平台管理员。指派给一个已有账户之后，才会有人能打开。": {
    "en": "These records predate the account system. They belong to a workspace with no members, so nobody—including platform administrators—can read them. Assign the workspace to an existing account to grant access.",
    "zh-Hant": "這些資料是帳戶體系建立之前產生的。它現在屬於一個沒有任何成員的工作空間，因此沒有人能讀取它——包括平台管理員。指派給一個已有帳戶之後，才會有人能打開。"
  },
  "{p0}个项目，{p1}位成员。请选择接收此空间的已有账户。": {
    "en": "{p0} projects, {p1} members. Choose an existing account to receive this workspace.",
    "zh-Hant": "{p0}個專案，{p1}位成員。請選擇接收此空間的已有帳戶。"
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
  "将把「{p0}」及其中{p1}个项目的访问权交给{p2}，角色为所有者。不会授予平台管理权限。": {
    "en": "Give {p2} ownership of “{p0}” and access to its {p1} projects. This does not grant platform administration rights.",
    "zh-Hant": "將把「{p0}」及其中{p1}個專案的訪問權交給{p2}，角色為所有者。不會授予平台管理權限。"
  },
  "已把包含 {p0} 个项目的空间指派给 {p1}。": {
    "en": "Assigned the workspace with {p0} projects to {p1}.",
    "zh-Hant": "已把包含 {p0} 個專案的空間指派給 {p1}。"
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
  "空间": {
    "en": "Workspaces",
    "zh-Hant": "空間"
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
  "刷新空间": {
    "en": "Refresh workspaces",
    "zh-Hant": "重新整理空間"
  },
  "显示数量，不显示内容。平台管理权限不等于读取文档或管理空间成员的权限。": {
    "en": "Only counts are shown, never content. Platform administration does not grant document access or workspace member management rights.",
    "zh-Hant": "顯示數量，不顯示內容。平台管理權限不等於讀取文檔或管理空間成員的權限。"
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
  "冻结空间": {
    "en": "Freeze workspace",
    "zh-Hant": "凍結空間"
  },
  "解冻空间": {
    "en": "Unfreeze workspace",
    "zh-Hant": "解凍空間"
  },
  "成员与邀请": {
    "en": "Members & invitations",
    "zh-Hant": "成員與邀請"
  },
  "没有匹配的工作空间。": {
    "en": "No matching workspaces.",
    "zh-Hant": "沒有匹配的工作空間。"
  },
  "成员将立即失去应用访问，当前会话将移出空间。已有成员关系和内容保留。": {
    "en": "Members will immediately lose application access and current sessions will leave this workspace. Memberships and content are retained.",
    "zh-Hant": "成員將立即失去應用訪問，當前工作階段將移出空間。已有成員關係和內容保留。"
  },
  "空间将恢复可用，成员仍按原有资格访问。此前结束的登录不会恢复。": {
    "en": "The workspace will be available with its existing access rules. Previously ended sessions will not be restored.",
    "zh-Hant": "空間將恢復可用，成員仍按原有資格訪問。此前結束的登入不會恢復。"
  },
  "空间已冻结。": {
    "en": "Workspace frozen.",
    "zh-Hant": "空間已凍結。"
  },
  "空间已解冻。若之后恢复更早的备份，冻结可能重新生效，需要再次解冻。": {
    "en": "Workspace unfrozen. Restoring an earlier backup may freeze it again; it would then need to be unfrozen again.",
    "zh-Hant": "空間已解凍。若之後恢復更早的備份，凍結可能重新生效，需要再次解凍。"
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
  "账户管理": {
    "en": "Account administration",
    "zh-Hant": "帳戶管理"
  },
  "空间管理": {
    "en": "Workspace administration",
    "zh-Hant": "空間管理"
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
  "只读。翻页时保留本次查询的记录范围；点击刷新查看新记录。": {
    "en": "Read only. Pages retain the current query's insertion boundary. Refresh to include new records.",
    "zh-Hant": "唯讀。翻頁時保留本次查詢的記錄範圍；點擊重新整理查看新記錄。"
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
  "只有空间所有者和管理员可以查看与管理成员。{p0}": {
    "en": "Only workspace owners and administrators can view and manage members. {p0}",
    "zh-Hant": "只有空間所有者和管理員可以查看與管理成員。{p0}"
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
  "{kind}空间": {
    "en": "{kind} workspace",
    "zh-Hant": "{kind}空間"
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
  "实际访问仍取决于账户、空间和应用是否可用；授予使用资格不会启用已停用的应用。": {
    "en": "Access also depends on the account, workspace and application being available. Granting access does not enable a disabled application.",
    "zh-Hant": "實際訪問仍取決於帳戶、空間和應用是否可用；授予使用資格不會啓用已停用的應用。"
  },
  "这个空间已被平台管理员冻结：成员暂时无法使用其中的应用。你仍然可以移除成员、撤销权限，但不能邀请新成员、提升角色或授予权限，直到空间解冻。": {
    "en": "A platform administrator froze this workspace. Members cannot use its applications. You can still remove members and revoke access, but cannot invite members, promote roles or grant access until it is unfrozen.",
    "zh-Hant": "這個空間已被平台管理員凍結：成員暫時無法使用其中的應用。你仍然可以移除成員、撤銷權限，但不能邀請新成員、提升角色或授予權限，直到空間解凍。"
  },
  "这个空间只有{p0}位状态正常的所有者。建议再指定一位所有者，以免唯一的所有者无法联系时没有人能管理这个空间。": {
    "en": "This workspace has only {p0} active owner. Consider appointing another owner so the workspace can still be managed if its sole owner becomes unavailable.",
    "zh-Hant": "這個空間只有{p0}位狀態正常的所有者。建議再指定一位所有者，以免唯一的所有者無法聯繫時沒有人能管理這個空間。"
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
  "编辑": {
    "en": "Editor",
    "zh-Hant": "編輯者"
  },
  "只读": {
    "en": "Viewer",
    "zh-Hant": "檢視者"
  },
  "管理所有成员（包括其他所有者）、邀请任意角色、移交所有权": {
    "en": "Manage all members, including other owners; invite any role; transfer ownership",
    "zh-Hant": "管理所有成員（包括其他所有者）、邀請任意角色、移交所有權"
  },
  "邀请成员，管理编辑与只读成员，可授予最高到管理员的角色": {
    "en": "Invite members, manage editors and viewers, and assign roles up to administrator",
    "zh-Hant": "邀請成員，管理編輯與唯讀成員，可授予最高到管理員的角色"
  },
  "在空间内编辑，不能管理成员": {
    "en": "Edit within the workspace; no member management",
    "zh-Hant": "在空間內編輯，不能管理成員"
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
  "口令 + 动态验证码": {
    "en": "Password + authenticator code",
    "zh-Hant": "密碼 + 動態驗證碼"
  },
  "口令 + 恢复码": {
    "en": "Password + recovery code",
    "zh-Hant": "密碼 + 恢復碼"
  },
  "安全密钥（含设备验证）": {
    "en": "Security key with device verification",
    "zh-Hant": "安全密鑰（含設備驗證）"
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
  "开放注册允许访客建立账户并验证邮箱；其他策略关闭公开注册。已有邀请仍须通过邀请流程的独立校验。": {
    "en": "Open registration lets visitors create accounts and verify their email. Other policies disable public registration. Existing invitations are still subject to the invitation flow’s separate checks.",
    "zh-Hant": "開放註冊允許訪客建立帳戶並驗證電子郵件地址；其他策略關閉公開註冊。已有邀請仍須通過邀請流程的獨立驗證。"
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
  "这里只显示投递元数据。找回和验证邮件的安全重试会生成新链接，并使旧链接失效；邀请请由空间管理员在成员与邀请中重发。安全通知、投递结果不确定或无法识别的历史失败不能重放。": {
    "en": "Only delivery metadata is shown. Retrying recovery or verification mail creates a new link and invalidates the old link. Workspace administrators can resend invitations from Members and invitations. Security notifications, uncertain deliveries and unrecognized historical failures cannot be replayed.",
    "zh-Hant": "這裡只顯示投遞中繼資料。找回和驗證郵件的安全重試會生成新連結，並使舊連結失效；邀請請由空間管理員在成員與邀請中重發。安全通知、投遞結果不確定或無法識別的歷史失敗不能重放。"
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
  "组织将停止使用，并保留七天恢复期限。关联空间的访问会被收回，内容不会通过此操作提供给管理员。": {
    "en": "The organization will become unavailable with a seven-day recovery period. Access to associated workspaces will be revoked. This action does not expose their content to administrators.",
    "zh-Hant": "組織將停止使用，並保留七天恢復期限。關聯空間的訪問會被收回，內容不會通過此操作提供給管理員。"
  },
  "组织及其关联空间将停止应用访问，已有会话和令牌会被撤销。": {
    "en": "The organization and its workspaces will lose application access. Existing sessions and tokens will be revoked.",
    "zh-Hant": "組織及其關聯空間將停止應用訪問，已有工作階段和權杖會被撤銷。"
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
  "空间冻结期间不能邀请": {
    "en": "Cannot invite while the workspace is frozen",
    "zh-Hant": "空間凍結期間不能邀請"
  },
  "邀请成员": {
    "en": "Invite members",
    "zh-Hant": "邀請成員"
  },
  "邮件排队或寄出不代表对方已加入。只有接受邀请后才成为成员。重发会让旧链接失效。": {
    "en": "Queued or sent mail does not mean someone has joined. They become a member only after accepting. Resending invalidates the old link.",
    "zh-Hant": "郵件排隊或寄出不代表對方已加入。只有接受邀請後才成為成員。重發會讓舊連結失效。"
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
  "邀请已接受、已撤销或已过期": {
    "en": "Invitation accepted, revoked or expired",
    "zh-Hant": "邀請已接受、已撤銷或已過期"
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
  "空间冻结期间不能重发": {
    "en": "Cannot resend while the workspace is frozen",
    "zh-Hant": "空間凍結期間不能重發"
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
  "{p0}个邮箱；一次最多 100 个。每个空间 24 小时最多创建 50 个邀请，超出部分会报告失败。": {
    "en": "{p0} addresses; up to 100 per submission. Each workspace can create 50 invitations per 24 hours; additional invitations will be reported as failures.",
    "zh-Hant": "{p0}個電子郵件地址；一次最多 100 個。每個空間 24 小時最多建立 50 個邀請，超出部分會報告失敗。"
  },
  "邀请角色": {
    "en": "Invited role",
    "zh-Hant": "邀請角色"
  },
  "允许导出（编辑及以上角色自带）": {
    "en": "Allow export (included with editor and higher roles)",
    "zh-Hant": "允許匯出（編輯及以上角色自帶）"
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
  "这些链接将失效，收件人将不能通过它们加入空间。已加入的成员不受影响。": {
    "en": "These links will expire and can no longer be used to join the workspace. Existing members are unaffected.",
    "zh-Hant": "這些連結將失效，收件人將不能通過它們加入空間。已加入的成員不受影響。"
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
  "不能修改自己的权限，请使用退出空间或移交所有权": {
    "en": "You cannot change your own permissions. Leave the workspace or transfer ownership instead.",
    "zh-Hant": "不能修改自己的權限，請使用退出空間或移交所有權"
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
  "编辑及以上角色自带导出权限；需先降低角色": {
    "en": "Editor and higher roles include export; lower the role first",
    "zh-Hant": "編輯及以上角色自帶匯出權限；需先降低角色"
  },
  "空间冻结期间不能增加权限": {
    "en": "Cannot add permissions while the workspace is frozen",
    "zh-Hant": "空間凍結期間不能增加權限"
  },
  "授予应用使用资格": {
    "en": "Grant application access",
    "zh-Hant": "授予應用使用資格"
  },
  "空间冻结期间不能授予": {
    "en": "Cannot grant access while the workspace is frozen",
    "zh-Hant": "空間凍結期間不能授予"
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
  "空间冻结期间不能移交": {
    "en": "Cannot transfer ownership while the workspace is frozen",
    "zh-Hant": "空間凍結期間不能移交"
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
  "退出空间": {
    "en": "Leave workspace",
    "zh-Hant": "退出空間"
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
  "账户、空间或应用当前不可用": {
    "en": "Account, workspace or application currently unavailable",
    "zh-Hant": "帳戶、空間或應用當前不可用"
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
  "{members} 将获得在「{space}」使用 {app} 的资格。账户、空间和应用仍须正常可用。": {
    "en": "{members} will receive access to {app} in “{space}”. The account, workspace and application must still be available.",
    "zh-Hant": "{members} 將獲得在「{space}」使用 {app} 的資格。帳戶、空間和應用仍須正常可用。"
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
  "将成为所有者：可以管理这个空间里的所有成员（包括你）、邀请任意角色、移交所有权。所有者只能由其他所有者降级。": {
    "en": "Will become an owner, able to manage all members including you, invite any role and transfer ownership. Only other owners can demote an owner.",
    "zh-Hant": "將成為所有者：可以管理這個空間里的所有成員（包括你）、邀請任意角色、移交所有權。所有者只能由其他所有者降級。"
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
  "{p0} 个登录会话已移出该空间；取消了 {p1} 个 TA 发出的邀请": {
    "en": "moved {p0} sessions out of the workspace and cancelled {p1} invitations they issued",
    "zh-Hant": "{p0} 個登入工作階段已移出該空間；取消了 {p1} 個 TA 發出的邀請"
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
  "空间已冻结：只能降低角色，不能提升。": {
    "en": "Workspace frozen: roles can only be lowered, not raised.",
    "zh-Hant": "空間已凍結：只能降低角色，不能提升。"
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
  "降为只读的成员将不能再编辑，导出权限随之关闭。": {
    "en": "Members changed to viewer will lose editing and export access.",
    "zh-Hant": "降為唯讀的成員將不能再編輯，匯出權限隨之關閉。"
  },
  "其中包含管理员或所有者的降级、或新的所有者：需要写明理由并重新验证身份。": {
    "en": "This includes demoting an administrator or owner, or appointing a new owner. A written reason and identity verification are required.",
    "zh-Hant": "其中包含管理員或所有者的降級、或新的所有者：需要寫明理由並重新驗證身份。"
  },
  "空间已冻结，其中{p0}项提升会被拒绝。": {
    "en": "Workspace frozen: {p0} promotions will be refused.",
    "zh-Hant": "空間已凍結，其中{p0}項提升會被拒絕。"
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
  "失去这个空间里全部项目、模板与文件的访问；": {
    "en": "Access to all projects, templates and files in this workspace is removed;",
    "zh-Hant": "失去這個空間里全部專案、範本與文件的訪問；"
  },
  "在这个空间使用{p0}的资格被撤销，已签发给应用的令牌全部失效；": {
    "en": "Access to {p0} in this workspace is revoked, along with all tokens issued to the application;",
    "zh-Hant": "在這個空間使用{p0}的資格被撤銷，已簽發給應用的權杖全部失效；"
  },
  "正停留在这个空间的登录会被移出该空间（账户本身不会被退出登录）；": {
    "en": "Sessions currently in this workspace are moved out, without signing out of the account;",
    "zh-Hant": "正停留在這個空間的登入會被移出該空間（帳戶本身不會被退出登入）；"
  },
  "这些成员发出但尚未被接受的邀请会被取消。": {
    "en": "Pending invitations issued by these members are cancelled.",
    "zh-Hant": "這些成員發出但尚未被接受的邀請會被取消。"
  },
  "账户本身不受影响。以后需要时可以重新邀请，重新加入后从头开始，不会恢复原来的角色和资格。": {
    "en": "The account itself is unaffected. You can invite them again later, but rejoining does not restore their previous role or access grants.",
    "zh-Hant": "帳戶本身不受影響。以後需要時可以重新邀請，重新加入後從頭開始，不會恢復原來的角色和資格。"
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
  "两项变化在同一个事务里完成，空间在任何时刻都有所有者。": {
    "en": "Both changes happen in one transaction, so the workspace always has an owner.",
    "zh-Hant": "兩項變化在同一個事務里完成，空間在任何時刻都有所有者。"
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
  "失去这个空间里全部内容的访问；": {
    "en": "Access to all content in this workspace is removed;",
    "zh-Hant": "失去這個空間里全部內容的訪問；"
  },
  "你在这个空间的应用使用资格与令牌会被撤销；": {
    "en": "Your application access and tokens in this workspace are revoked;",
    "zh-Hant": "你在這個空間的應用使用資格與權杖會被撤銷；"
  },
  "你发出但尚未被接受的邀请会被取消。": {
    "en": "Pending invitations you issued are cancelled.",
    "zh-Hant": "你發出但尚未被接受的邀請會被取消。"
  },
  "要回来，需要空间管理员重新邀请你。": {
    "en": "A workspace administrator must invite you again to return.",
    "zh-Hant": "要回來，需要空間管理員重新邀請你。"
  },
  "你是这个空间唯一的所有者，不能直接退出。请先把所有权移交给另一位成员。": {
    "en": "You are the only owner and cannot leave directly. Transfer ownership to another member first.",
    "zh-Hant": "你是這個空間唯一的所有者，不能直接退出。請先把所有權移交給另一位成員。"
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
  "请先移交所有权，再退出空间。": {
    "en": "Transfer ownership before leaving the workspace.",
    "zh-Hant": "請先移交所有權，再退出空間。"
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
  "空间已冻结，不能增加权限。": {
    "en": "Workspace frozen. Permissions cannot be increased.",
    "zh-Hant": "空間已凍結，不能增加權限。"
  },
  "该账户已经是空间成员。": {
    "en": "This account is already a workspace member.",
    "zh-Hant": "該帳戶已經是空間成員。"
  },
  "邀请过于频繁，请稍后重试。": {
    "en": "Too many invitations. Try again later.",
    "zh-Hant": "邀請過於頻繁，請稍後重試。"
  },
  "已有待接受的邀请。": {
    "en": "A pending invitation already exists.",
    "zh-Hant": "已有待接受的邀請。"
  },
  "邀请已接受或已撤销，请刷新。": {
    "en": "Invitation accepted or revoked. Refresh the page.",
    "zh-Hant": "邀請已接受或已撤銷，請重新整理。"
  },
  "该空间已被认领或不能认领，请刷新。": {
    "en": "Workspace already claimed or not claimable. Refresh the page.",
    "zh-Hant": "該空間已被認領或不能認領，請重新整理。"
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
  }
};
registerMessages(governanceMessages);
