import { registerMessages } from "@synwevo/ui";

export const messages = {
  "该组织尚未设置头像": { en: "This organization has no avatar yet.", "zh-Hant": "此組織尚未設定頭像" },
  "头像请求包含不支持的字段": { en: "The avatar request contains unsupported fields.", "zh-Hant": "頭像請求包含不支援的欄位" },
  "请上传不超过 256 KB、512 × 512 像素的普通 PNG 图片": { en: "Upload a standard PNG no larger than 256 KB and 512 × 512 pixels.", "zh-Hant": "請上傳不超過 256 KB、512 × 512 像素的普通 PNG 圖片" },
  "组织头像": {"en": "Organization avatar", "zh-Hant": "組織頭像"},
  "请选择不超过 256 KB、512 × 512 像素的 PNG 图片。": {"en": "Choose a PNG no larger than 256 KB and 512 × 512 pixels.", "zh-Hant": "請選擇不超過 256 KB、512 × 512 像素的 PNG 圖片。"},
  "「{name}」的组织头像": {"en": "Avatar for “{name}”", "zh-Hant": "「{name}」的組織頭像"},
  "组织名称首字": {"en": "Organization initial", "zh-Hant": "組織名稱首字"},
  "更换组织头像": {"en": "Change organization avatar", "zh-Hant": "更換組織頭像"},
  "上传组织头像": {"en": "Upload organization avatar", "zh-Hant": "上傳組織頭像"},
  "移除组织头像": {"en": "Remove organization avatar", "zh-Hant": "移除組織頭像"},
  "选择组织头像": {"en": "Choose organization avatar", "zh-Hant": "選擇組織頭像"},
  "PNG 图片，不超过 256 KB 和 512 × 512 像素。更改需要确认身份和填写原因。": {"en": "PNG, up to 256 KB and 512 × 512 pixels. Changes require identity verification and a reason.", "zh-Hant": "PNG 圖片，不超過 256 KB 和 512 × 512 像素。更改需要確認身分和填寫原因。"},
  "组织头像暂时无法加载。": {"en": "The organization avatar could not be loaded.", "zh-Hant": "組織頭像暫時無法載入。"},
  "更新组织头像": {"en": "Update organization avatar", "zh-Hant": "更新組織頭像"},
  "移除后，组织将显示名称首字。组织成员与工作空间保持不变。": {"en": "The organization will display its initial after removal. Members and workspaces are unchanged.", "zh-Hant": "移除後，組織將顯示名稱首字。組織成員與工作空間保持不變。"},
  "确认将所选图片设置为此组织的头像，所有当前组织成员都可以看到。": {"en": "Confirm the selected image as the organization avatar. All current organization members can see it.", "zh-Hant": "確認將所選圖片設為此組織的頭像，所有目前的組織成員都可以看到。"},
  "所选组织头像预览": {"en": "Selected organization avatar preview", "zh-Hant": "所選組織頭像預覽"},
  "请填写组织名称和工作空间名称。": {
    "en": "Enter an organization name and a workspace name.",
    "zh-Hant": "請填寫組織名稱和工作空間名稱。"
  },
  "组织名称": {
    "en": "Organization name",
    "zh-Hant": "組織名稱"
  },
  "首个工作空间名称": {
    "en": "First workspace name",
    "zh-Hant": "首個工作空間名稱"
  },
  "工作空间用于组织项目。创建后，你将成为组织和这个空间的所有者。": {
    "en": "Workspaces organize your projects. You will own the organization and its first workspace.",
    "zh-Hant": "工作空間用於組織專案。建立後，你將成為組織和此空間的擁有者。"
  },
  "创建中…": {
    "en": "Creating…",
    "zh-Hant": "建立中…"
  },
  "创建组织和工作空间": {
    "en": "Create organization and workspace",
    "zh-Hant": "建立組織和工作空間"
  },
  "如果请求中断，保持名称不变并重试，可以取回同一次创建的结果。": {
    "en": "If the request was interrupted, keep the same names and retry to recover the same creation result.",
    "zh-Hant": "如果請求中斷，保持名稱不變並重試，可以取回同一次建立的結果。"
  },
  "请填写至少 8 个字符的操作原因。": {
    "en": "Explain the reason using at least 8 characters.",
    "zh-Hant": "請填寫至少 8 個字元的操作原因。"
  },
  "操作原因": {
    "en": "Reason for this action",
    "zh-Hant": "操作原因"
  },
  "8–500 个字符，会写入审计记录。": {
    "en": "8–500 characters. This will be recorded in the audit log.",
    "zh-Hant": "8–500 個字元，會寫入稽核記錄。"
  },
  "这一步需要重新验证身份，请输入当前口令。": {
    "en": "This step requires verification. Enter your current password.",
    "zh-Hant": "這一步需要重新驗證身分，請輸入目前密碼。"
  },
  "当前口令": {
    "en": "Current password",
    "zh-Hant": "目前密碼"
  },
  "重新登录": {
    "en": "Sign in again",
    "zh-Hant": "重新登入"
  },
  "取消": {
    "en": "Cancel",
    "zh-Hant": "取消"
  },
  "验证并确认": {
    "en": "Verify and confirm",
    "zh-Hant": "驗證並確認"
  },
  "确认操作": {
    "en": "Confirm action",
    "zh-Hant": "確認操作"
  },
  "你的工作空间已准备好": {
    "en": "Your workspace is ready",
    "zh-Hant": "你的工作空間已準備好"
  },
  "从这里开始": {
    "en": "Start here",
    "zh-Hant": "從這裡開始"
  },
  "创建个人空间、组建团队，或接受已有团队的邀请。": {
    "en": "Create your own workspace, build a team, or accept a team's invitation.",
    "zh-Hant": "建立個人空間、組建團隊，或接受已有團隊的邀請。"
  },
  "账户设置": {
    "en": "Account settings",
    "zh-Hant": "帳戶設定"
  },
  "我的组织": {
    "en": "My organizations",
    "zh-Hant": "我的組織"
  },
  "可用工作空间": {
    "en": "Available workspaces",
    "zh-Hant": "可用工作空間"
  },
  "个人空间": {
    "en": "Personal workspace",
    "zh-Hant": "個人空間"
  },
  "团队空间": {
    "en": "Team workspace",
    "zh-Hant": "團隊空間"
  },
  "进入工作空间": {
    "en": "Open workspace",
    "zh-Hant": "進入工作空間"
  },
  "等待你接受的邀请": {
    "en": "Invitations waiting for you",
    "zh-Hant": "等待你接受的邀請"
  },
  "请打开对应邀请邮件中的链接，核对信息并确认加入。": {
    "en": "Open the link in the invitation email to review the details and confirm you want to join.",
    "zh-Hant": "請開啟對應邀請郵件中的連結，核對資料並確認加入。"
  },
  "查看当前邀请": {
    "en": "View current invitation",
    "zh-Hant": "查看目前邀請"
  },
  "准备一个新空间": {
    "en": "Set up a new workspace",
    "zh-Hant": "準備一個新空間"
  },
  "拥有自己的工作空间，整理文档与项目。已有个人空间时会继续使用它。": {
    "en": "Organize documents and projects in your own workspace. If you already have one, you will keep using it.",
    "zh-Hant": "擁有自己的工作空間，整理文件與專案。已有個人空間時會繼續使用它。"
  },
  "使用个人空间": {
    "en": "Use personal workspace",
    "zh-Hant": "使用個人空間"
  },
  "组建团队": {
    "en": "Build a team",
    "zh-Hant": "組建團隊"
  },
  "创建组织与首个工作空间，按角色邀请成员一起工作。": {
    "en": "Create an organization and its first workspace, then invite members with the roles they need.",
    "zh-Hant": "建立組織與首個工作空間，按角色邀請成員一起工作。"
  },
  "收起创建表单": {
    "en": "Close creation form",
    "zh-Hant": "收起建立表單"
  },
  "创建组织": {
    "en": "Create organization",
    "zh-Hant": "建立組織"
  },
  "正常": {
    "en": "Active",
    "zh-Hant": "正常"
  },
  "已冻结": {
    "en": "Frozen",
    "zh-Hant": "已凍結"
  },
  "已删除": {
    "en": "Deleted",
    "zh-Hant": "已刪除"
  },
  "不可用": {
    "en": "Unavailable",
    "zh-Hant": "不可用"
  },
  "组织成员": {
    "en": "Organization members",
    "zh-Hant": "組織成員"
  },
  "搜索姓名或邮箱": {
    "en": "Search name or email",
    "zh-Hant": "搜尋姓名或電郵"
  },
  "搜索": {
    "en": "Search",
    "zh-Hant": "搜尋"
  },
  "共 {count} 位成员": {
    "en": "{count} members",
    "zh-Hant": "共 {count} 位成員"
  },
  "当前只有一位可用所有者。退出前，请先交接组织和工作空间所有权。": {
    "en": "There is only one active owner. Transfer organization and workspace ownership before leaving.",
    "zh-Hant": "目前只有一位可用擁有者。退出前，請先交接組織和工作空間擁有權。"
  },
  "没有符合条件的成员。": {
    "en": "No members match your search.",
    "zh-Hant": "沒有符合條件的成員。"
  },
  "你": {
    "en": "You",
    "zh-Hant": "你"
  },
  "账户不可用": {
    "en": "Account unavailable",
    "zh-Hant": "帳戶不可用"
  },
  "修改角色": {
    "en": "Change role",
    "zh-Hant": "修改角色"
  },
  "交接所有权": {
    "en": "Transfer ownership",
    "zh-Hant": "交接擁有權"
  },
  "移除": {
    "en": "Remove",
    "zh-Hant": "移除"
  },
  "上一页": {
    "en": "Previous",
    "zh-Hant": "上一頁"
  },
  "下一页": {
    "en": "Next",
    "zh-Hant": "下一頁"
  },
  "修改成员角色": {
    "en": "Change member role",
    "zh-Hant": "修改成員角色"
  },
  "交接组织所有权": {
    "en": "Transfer organization ownership",
    "zh-Hant": "交接組織擁有權"
  },
  "移除组织成员": {
    "en": "Remove organization member",
    "zh-Hant": "移除組織成員"
  },
  "修改 {email} 的组织角色。工作空间内的权限仍由各空间独立管理。": {
    "en": "Change the organization role for {email}. Workspace permissions remain managed within each workspace.",
    "zh-Hant": "修改 {email} 的組織角色。工作空間內的權限仍由各空間獨立管理。"
  },
  "将组织所有权交给 {email}，你的组织角色将变为管理员。工作空间所有权不会自动转移。": {
    "en": "Transfer organization ownership to {email}. You will become an administrator. Workspace ownership will not transfer automatically.",
    "zh-Hant": "將組織擁有權交給 {email}，你的組織角色將變為管理員。工作空間擁有權不會自動轉移。"
  },
  "移除 {email} 后，对方会失去此组织及所属工作空间的成员资格，相关会话、令牌和待处理邀请会被撤销。": {
    "en": "Removing {email} removes their membership from this organization and its workspaces, and revokes related sessions, tokens and pending invitations.",
    "zh-Hant": "移除 {email} 後，對方會失去此組織及所屬工作空間的成員資格，相關工作階段、權杖和待處理邀請會被撤銷。"
  },
  "新角色": {
    "en": "New role",
    "zh-Hant": "新角色"
  },
  "请填写工作空间名称。": {
    "en": "Enter a workspace name.",
    "zh-Hant": "請填寫工作空間名稱。"
  },
  "组织工作空间": {
    "en": "Organization workspaces",
    "zh-Hant": "組織工作空間"
  },
  "尚未加入此空间": {
    "en": "Not a member of this workspace",
    "zh-Hant": "尚未加入此空間"
  },
  "成员与邀请": {
    "en": "Members and invitations",
    "zh-Hant": "成員與邀請"
  },
  "这个组织还没有工作空间。": {
    "en": "This organization has no workspaces yet.",
    "zh-Hant": "此組織還沒有工作空間。"
  },
  "新工作空间名称": {
    "en": "New workspace name",
    "zh-Hant": "新工作空間名稱"
  },
  "创建工作空间": {
    "en": "Create workspace",
    "zh-Hant": "建立工作空間"
  },
  "纳入已有工作空间": {
    "en": "Add an existing workspace",
    "zh-Hant": "納入已有工作空間"
  },
  "选择你拥有的团队空间": {
    "en": "Choose a team workspace you own",
    "zh-Hant": "選擇你擁有的團隊空間"
  },
  "仅可纳入尚未关联组织的团队空间。服务器会再次核对归属。": {
    "en": "Only team workspaces without an organization can be added. The server will verify ownership again.",
    "zh-Hant": "僅可納入尚未關聯組織的團隊空間。伺服器會再次核對歸屬。"
  },
  "查看并确认纳入": {
    "en": "Review and confirm addition",
    "zh-Hant": "查看並確認納入"
  },
  "将「{name}」纳入此组织。现有空间成员会加入组织，但不会因此获得其他空间的权限。": {
    "en": "Add “{name}” to this organization. Existing workspace members will join the organization without gaining access to other workspaces.",
    "zh-Hant": "將「{name}」納入此組織。現有空間成員會加入組織，但不會因此獲得其他空間的權限。"
  },
  "管理团队归属与协作空间。组织成员身份与工作空间权限分别管理。": {
    "en": "Manage your teams and their workspaces. Organization membership and workspace permissions are managed separately.",
    "zh-Hant": "管理團隊歸屬與協作空間。組織成員身分與工作空間權限分別管理。"
  },
  "查看组织": {
    "en": "View organization",
    "zh-Hant": "查看組織"
  },
  "你还没有加入组织。可以创建一个，或使用团队发来的邀请邮件加入。": {
    "en": "You have not joined an organization yet. Create one or join using an invitation from your team.",
    "zh-Hant": "你還沒有加入組織。可以建立一個，或使用團隊寄來的邀請郵件加入。"
  },
  "冻结组织": {
    "en": "Freeze organization",
    "zh-Hant": "凍結組織"
  },
  "冻结后，所属工作空间将停止访问，相关会话、令牌与待处理邀请会被撤销。之后可以解冻，已撤销的凭据不会恢复。": {
    "en": "Freezing blocks access to the organization's workspaces and revokes related sessions, tokens and pending invitations. You can unfreeze later; revoked credentials will not be restored.",
    "zh-Hant": "凍結後，所屬工作空間將停止存取，相關工作階段、權杖與待處理邀請會被撤銷。之後可以解凍，已撤銷的憑證不會恢復。"
  },
  "解冻组织": {
    "en": "Unfreeze organization",
    "zh-Hant": "解凍組織"
  },
  "恢复组织访问。已撤销的会话、令牌和邀请不会恢复，成员需要重新登录。": {
    "en": "Restore organization access. Revoked sessions, tokens and invitations will not return. Members must sign in again.",
    "zh-Hant": "恢復組織存取。已撤銷的工作階段、權杖和邀請不會恢復，成員需要重新登入。"
  },
  "删除组织": {
    "en": "Delete organization",
    "zh-Hant": "刪除組織"
  },
  "组织和所属工作空间会停止访问，相关会话、令牌与待处理邀请会被撤销。七天内可恢复组织；此操作不会直接删除文档数据。": {
    "en": "Access to the organization and its workspaces will stop. Related sessions, tokens and pending invitations will be revoked. The organization can be recovered within seven days; this action does not directly delete document data.",
    "zh-Hant": "組織和所屬工作空間會停止存取，相關工作階段、權杖與待處理邀請會被撤銷。七天內可恢復組織；此操作不會直接刪除文件資料。"
  },
  "恢复组织": {
    "en": "Recover organization",
    "zh-Hant": "恢復組織"
  },
  "恢复至删除前的正常或冻结状态。已撤销的会话、令牌和邀请不会恢复。": {
    "en": "Restore the active or frozen state from before deletion. Revoked sessions, tokens and invitations will not return.",
    "zh-Hant": "恢復至刪除前的正常或凍結狀態。已撤銷的工作階段、權杖和邀請不會恢復。"
  },
  "退出组织": {
    "en": "Leave organization",
    "zh-Hant": "退出組織"
  },
  "你将失去此组织及所属工作空间的成员资格，相关登录与令牌会失效。若你是唯一所有者，需要先交接组织和工作空间。": {
    "en": "You will lose membership in this organization and its workspaces. Related sessions and tokens will be revoked. Sole owners must first transfer the organization and their workspaces.",
    "zh-Hant": "你將失去此組織及所屬工作空間的成員資格，相關登入與權杖會失效。若你是唯一擁有者，需要先交接組織和工作空間。"
  },
  "编辑组织设置": {
    "en": "Edit organization settings",
    "zh-Hant": "編輯組織設定"
  },
  "更新组织名称或邀请策略。更严格的策略可能撤销不再符合条件的待处理邀请。": {
    "en": "Update the organization name or invitation policy. A stricter policy may revoke pending invitations that no longer qualify.",
    "zh-Hant": "更新組織名稱或邀請策略。更嚴格的策略可能撤銷不再符合條件的待處理邀請。"
  },
  "组织详情": {
    "en": "Organization details",
    "zh-Hant": "組織詳情"
  },
  "成员、空间与组织设置，在这里保持清晰。": {
    "en": "Keep members, workspaces and organization settings in view.",
    "zh-Hant": "成員、空間與組織設定，在這裡保持清晰。"
  },
  "返回我的组织": {
    "en": "Back to my organizations",
    "zh-Hant": "返回我的組織"
  },
  "你的角色：{role}": {
    "en": "Your role: {role}",
    "zh-Hant": "你的角色：{role}"
  },
  "{workspaces} 个空间 · {members} 位成员": {
    "en": "{workspaces} workspaces · {members} members",
    "zh-Hant": "{workspaces} 個空間 · {members} 位成員"
  },
  "组织已冻结，工作空间暂时无法访问。": {
    "en": "This organization is frozen. Its workspaces are temporarily inaccessible.",
    "zh-Hant": "組織已凍結，工作空間暫時無法存取。"
  },
  "组织已删除，工作空间暂时无法访问。": {
    "en": "This organization is deleted. Its workspaces are currently inaccessible.",
    "zh-Hant": "組織已刪除，工作空間暫時無法存取。"
  },
  "可恢复至 {date}": {
    "en": "Recoverable until {date}",
    "zh-Hant": "可恢復至 {date}"
  },
  "组织设置": {
    "en": "Organization settings",
    "zh-Hant": "組織設定"
  },
  "仅组织所有者可发送工作空间邀请。": {
    "en": "Only organization owners can send workspace invitations.",
    "zh-Hant": "僅組織擁有者可傳送工作空間邀請。"
  },
  "组织所有者和管理员可发送工作空间邀请。": {
    "en": "Organization owners and administrators can send workspace invitations.",
    "zh-Hant": "組織擁有者和管理員可傳送工作空間邀請。"
  },
  "组织访问": {
    "en": "Organization access",
    "zh-Hant": "組織存取"
  },
  "恢复期限已过。": {
    "en": "The recovery period has ended.",
    "zh-Hant": "恢復期限已過。"
  },
  "邀请策略": {
    "en": "Invitation policy",
    "zh-Hant": "邀請策略"
  },
  "所有者和管理员": {
    "en": "Owners and administrators",
    "zh-Hant": "擁有者和管理員"
  },
  "仅所有者": {
    "en": "Owners only",
    "zh-Hant": "僅擁有者"
  },
  "找不到该组织": {
    "en": "This organization was not found.",
    "zh-Hant": "找不到此組織"
  },
  "该组织当前不可用": {
    "en": "This organization is currently unavailable.",
    "zh-Hant": "此組織目前不可用"
  },
  "当前组织邀请策略不允许此操作": {
    "en": "The organization's invitation policy does not allow this action.",
    "zh-Hant": "目前的組織邀請策略不允許此操作"
  },
  "请提供有效的幂等请求标识": {
    "en": "The creation request is missing a valid retry identifier.",
    "zh-Hant": "請提供有效的冪等請求識別碼"
  },
  "该请求标识已用于不同的创建内容": {
    "en": "This retry identifier was used for different creation details.",
    "zh-Hant": "此請求識別碼已用於不同的建立內容"
  },
  "组织设置已更新，请刷新后重试": {
    "en": "Organization settings changed. Refresh and try again.",
    "zh-Hant": "組織設定已更新，請重新整理後重試"
  },
  "只有当前工作空间所有者可以将其纳入组织": {
    "en": "Only the workspace's current owner can add it to an organization.",
    "zh-Hant": "只有目前的工作空間擁有者可以將其納入組織"
  },
  "个人空间与历史默认空间不能纳入组织": {
    "en": "Personal and legacy default workspaces cannot be added to an organization.",
    "zh-Hant": "個人空間與歷史預設空間不能納入組織"
  },
  "该工作空间已属于一个组织": {
    "en": "This workspace already belongs to an organization.",
    "zh-Hant": "此工作空間已屬於一個組織"
  },
  "请先交接仍由此账户独自拥有的工作空间": {
    "en": "First transfer any workspaces for which this account is the only owner.",
    "zh-Hant": "請先交接仍由此帳戶獨自擁有的工作空間"
  },
  "该组织的七天恢复期限已过": {
    "en": "The organization's seven-day recovery period has ended.",
    "zh-Hant": "此組織的七天恢復期限已過"
  },
  "组织当前状态不允许此操作": {
    "en": "The organization's current state does not allow this action.",
    "zh-Hant": "組織目前狀態不允許此操作"
  },
  "账户不可用，请重新登录": {
    "en": "Your account is unavailable. Sign in again.",
    "zh-Hant": "帳戶不可用，請重新登入"
  },
  "当前组织角色无权执行此操作": {
    "en": "Your organization role cannot perform this action.",
    "zh-Hant": "目前的組織角色無權執行此操作"
  },
  "请填写有效名称": {
    "en": "Enter a valid name.",
    "zh-Hant": "請填寫有效名稱"
  },
  "名称须为 1 至 100 个字符且不能包含控制字符": {
    "en": "Names must contain 1–100 characters and no control characters.",
    "zh-Hant": "名稱須為 1 至 100 個字元且不能包含控制字元"
  },
  "请写明理由（8 至 500 个字），它会写入审计记录": {
    "en": "Provide a reason of 8–500 characters for the audit log.",
    "zh-Hant": "請寫明理由（8 至 500 個字），它會寫入稽核記錄"
  },
  "请提供要修改的组织设置": {
    "en": "Specify the organization settings to update.",
    "zh-Hant": "請提供要修改的組織設定"
  },
  "请选择有效的邀请策略": {
    "en": "Choose a valid invitation policy.",
    "zh-Hant": "請選擇有效的邀請策略"
  },
  "找不到该工作空间": {
    "en": "This workspace was not found.",
    "zh-Hant": "找不到此工作空間"
  },
  "该工作空间当前不可用": {
    "en": "This workspace is currently unavailable.",
    "zh-Hant": "此工作空間目前不可用"
  },
  "请选择有效的组织角色": {
    "en": "Choose a valid organization role.",
    "zh-Hant": "請選擇有效的組織角色"
  },
  "搜索或分页参数无效": {
    "en": "The search or pagination parameters are invalid.",
    "zh-Hant": "搜尋或分頁參數無效"
  },
  "该账户不是组织成员": {
    "en": "This account is not an organization member.",
    "zh-Hant": "此帳戶不是組織成員"
  },
  "不能移除或降级最后一名可用的组织所有者": {
    "en": "The last active organization owner cannot be removed or demoted.",
    "zh-Hant": "不能移除或降級最後一名可用的組織擁有者"
  },
  "只有组织所有者可以管理管理员和所有者": {
    "en": "Only organization owners can manage administrators and owners.",
    "zh-Hant": "只有組織擁有者可以管理管理員和擁有者"
  },
  "该组织当前不可增加权限": {
    "en": "Access cannot be increased in this organization's current state.",
    "zh-Hant": "此組織目前不可增加權限"
  },
  "只能退出自己的组织成员身份": {
    "en": "You can only leave an organization on your own behalf.",
    "zh-Hant": "只能退出自己的組織成員身分"
  },
  "请选择另一名组织成员": {
    "en": "Select another organization member.",
    "zh-Hant": "請選擇另一名組織成員"
  },
  "这一步需要重新验证身份": {
    "en": "This step requires identity verification.",
    "zh-Hant": "這一步需要重新驗證身分"
  },
  "该组织或工作空间当前不可用": {
    "en": "This organization or workspace is currently unavailable.",
    "zh-Hant": "此組織或工作空間目前不可用"
  },
  "自定义角色": { en: "Custom roles", "zh-Hant": "自訂角色" },
  "角色是一组组织级权限，可分配给成员；不改变工作空间内的权限。所有者拥有全部权限；管理员持有默认的管理权限；普通成员默认没有组织级权限。": { en: "A role is a named set of organization-level permissions that can be assigned to members; it never changes permissions inside workspaces. Owners hold everything, admins hold the default administrative set, ordinary members hold none by default.", "zh-Hant": "角色是一組組織層級權限，可指派給成員；不改變工作空間內的權限。擁有者擁有全部權限；管理員持有預設的管理權限；一般成員預設沒有組織層級權限。" },
  "创建角色": { en: "Create role", "zh-Hant": "建立角色" },
  "编辑角色": { en: "Edit role", "zh-Hant": "編輯角色" },
  "删除角色": { en: "Delete role", "zh-Hant": "刪除角色" },
  "还没有自定义角色。": { en: "No custom roles yet.", "zh-Hant": "還沒有自訂角色。" },
  "已分配给 {count} 位成员": { en: "Assigned to {count} member(s)", "zh-Hant": "已指派給 {count} 位成員" },
  "编辑": { en: "Edit", "zh-Hant": "編輯" },
  "删除": { en: "Delete", "zh-Hant": "刪除" },
  "删除后，{count} 位成员将失去这个角色带来的权限。": { en: "After deletion, {count} member(s) lose the permissions this role granted.", "zh-Hant": "刪除後，{count} 位成員將失去此角色帶來的權限。" },
  "这个角色没有分配给任何成员。": { en: "This role is not assigned to anyone.", "zh-Hant": "此角色未指派給任何成員。" },
  "角色只能包含你自己已持有的权限。分配给成员后，成员立即获得这些权限。": { en: "A role may only contain permissions you hold yourself. Once assigned, the member holds them immediately.", "zh-Hant": "角色只能包含你自己已持有的權限。指派給成員後，成員立即取得這些權限。" },
  "角色名称": { en: "Role name", "zh-Hant": "角色名稱" },
  "说明（可选）": { en: "Description (optional)", "zh-Hant": "說明（選填）" },
  "仅所有者持有，不能放进自定义角色": { en: "Owners only; cannot be placed in a custom role", "zh-Hant": "僅擁有者持有，不能放進自訂角色" },
  "你没有这项权限，不能授予他人": { en: "You do not hold this permission, so you cannot grant it", "zh-Hant": "你沒有這項權限，不能授予他人" },
  "组织": { en: "Organization", "zh-Hant": "組織" },
  "成员": { en: "Members", "zh-Hant": "成員" },
  "角色": { en: "Roles", "zh-Hant": "角色" },
  "工作空间": { en: "Workspaces", "zh-Hant": "工作空間" },
  "治理": { en: "Governance", "zh-Hant": "治理" },
  "查看权限": { en: "View permissions", "zh-Hant": "檢視權限" },
  "分配角色": { en: "Assign roles", "zh-Hant": "指派角色" },
  "分配自定义角色": { en: "Assign custom roles", "zh-Hant": "指派自訂角色" },
  "为 {email} 设置自定义角色。成员立即获得所选角色的组织级权限；工作空间内的权限不变。": { en: "Set the custom roles of {email}. The member immediately holds the selected roles' organization-level permissions; workspace permissions are unchanged.", "zh-Hant": "為 {email} 設定自訂角色。成員立即取得所選角色的組織層級權限；工作空間內的權限不變。" },
  "有效权限：{name}": { en: "Effective permissions: {name}", "zh-Hant": "有效權限：{name}" },
  "基础角色：{role}": { en: "Base role: {role}", "zh-Hant": "基礎角色：{role}" },
  "自定义角色：{names}": { en: "Custom roles: {names}", "zh-Hant": "自訂角色：{names}" },
  "该成员没有组织级权限；工作空间内的权限由各空间独立管理。": { en: "This member holds no organization-level permissions; workspace permissions are managed per workspace.", "zh-Hant": "此成員沒有組織層級權限；工作空間內的權限由各空間獨立管理。" },
  "来自：{sources}": { en: "From: {sources}", "zh-Hant": "來自：{sources}" },
  "基础角色 {role}": { en: "base role {role}", "zh-Hant": "基礎角色 {role}" },
  "角色「{name}」": { en: "role “{name}”", "zh-Hant": "角色「{name}」" },
  "这里显示的是根据成员资格与角色计算出的结果，不是以该成员身份登录后的预览。": { en: "This is computed from the membership and roles, not a preview as that member.", "zh-Hant": "這裡顯示的是依成員資格與角色計算出的結果，不是以該成員身分登入後的預覽。" },
  // ---- bulk member import (CI-25)
  "{n} 行": { en: "{n} rows", "zh-Hant": "{n} 列" },
  "上传中 {p}%": { en: "Uploading {p}%", "zh-Hant": "上傳中 {p}%" },
  "上传并读取": { en: "Upload and read", "zh-Hant": "上傳並讀取" },
  "下载报告（CSV）": { en: "Download report (CSV)", "zh-Hant": "下載報告（CSV）" },
  "不使用": { en: "Not used", "zh-Hant": "不使用" },
  "不变": { en: "Unchanged", "zh-Hant": "不變" },
  "不能邀请比自己权限更高的角色": { en: "You cannot invite a role above your own", "zh-Hant": "不能邀請比自己權限更高的角色" },
  "你不能邀请到这个空间": { en: "You cannot invite into this workspace", "zh-Hant": "你不能邀請到這個空間" },
  "你没有分配这个角色所需的权限": { en: "You lack the permissions needed to assign this role", "zh-Hant": "你沒有指派這個角色所需的權限" },
  "全部": { en: "All", "zh-Hant": "全部" },
  "单次最多 {n} 行。": { en: "At most {n} rows per import.", "zh-Hant": "單次最多 {n} 列。" },
  "去创建角色": { en: "Create a role", "zh-Hant": "去建立角色" },
  "取消只作用于尚未处理的行；已发出的邀请要在成员列表里撤回。执行前会再次核对你的权限；新人接受邀请时才会加入组织并获得指定角色。": { en: "Cancelling only affects rows not yet processed; invitations already sent are withdrawn from the members list. Your permissions are checked again before each row runs; newcomers join the organization and receive the specified roles only when they accept.", "zh-Hant": "取消只作用於尚未處理的列；已發出的邀請要在成員清單裡撤回。執行前會再次核對你的權限；新人接受邀請時才會加入組織並獲得指定角色。" },
  "只显示前 300 行；完整结果见报告。": { en: "Only the first 300 rows are shown; the report has everything.", "zh-Hant": "只顯示前 300 列；完整結果見報告。" },
  "只读成员": { en: "Viewer", "zh-Hant": "檢視成員" },
  "可读取：{formats}。": { en: "Readable: {formats}.", "zh-Hant": "可讀取：{formats}。" },
  "字段映射": { en: "Field mapping", "zh-Hant": "欄位對應" },
  "将发出 {i} 封邀请邮件并更新 {u} 位成员的角色。邀请只有接受后才生效；已有成员不会被降级或移除。": { en: "{i} invitation mails will be sent and {u} members' roles updated. Invitations take effect only when accepted; existing members are never demoted or removed.", "zh-Hant": "將發出 {i} 封邀請郵件並更新 {u} 位成員的角色。邀請只有接受後才生效；既有成員不會被降級或移除。" },
  "工作空间已冻结": { en: "Workspace is frozen", "zh-Hant": "工作空間已凍結" },
  "已发出邀请 {invited}，更新成员 {updated}，跳过 {skipped}，失败 {failed}，待处理 {pending}。": { en: "{invited} invited, {updated} updated, {skipped} skipped, {failed} failed, {pending} pending.", "zh-Hant": "已發出邀請 {invited}，更新成員 {updated}，略過 {skipped}，失敗 {failed}，待處理 {pending}。" },
  "已取消": { en: "Cancelled", "zh-Hant": "已取消" },
  "已是此空间成员，无变化": { en: "Already a member of this workspace, no change", "zh-Hant": "已是此空間成員，無變化" },
  "已有待接受的邀请": { en: "A pending invitation already exists", "zh-Hant": "已有待接受的邀請" },
  "已有账户：发送邀请": { en: "Has an account: send invitation", "zh-Hant": "已有帳戶：發送邀請" },
  "成员文件": { en: "Member file", "zh-Hant": "成員檔案" },
  "所有者/管理员基础角色不能通过导入授予": { en: "Owner/admin base roles cannot be granted by import", "zh-Hant": "擁有者／管理員基礎角色不能透過匯入授予" },
  "打开": { en: "Open", "zh-Hant": "開啟" },
  "扫描件与图片可用文字识别。": { en: "Scans and images can be read with text recognition.", "zh-Hant": "掃描件與圖片可用文字辨識。" },
  "批量导入成员": { en: "Bulk member import", "zh-Hant": "批次匯入成員" },
  "按行识别邮箱（{n} 行）": { en: "Find addresses line by line ({n} lines)", "zh-Hant": "按行辨識電郵（{n} 行）" },
  "文件内重复": { en: "Duplicate within the file", "zh-Hant": "檔案內重複" },
  "文件超过单次上限，后 {n} 行未处理；请分批导入。": { en: "The file exceeds the per-import limit; the last {n} rows were not processed. Import in batches.", "zh-Hant": "檔案超過單次上限，後 {n} 列未處理；請分批匯入。" },
  "文件里的空间角色写法 → 实际角色": { en: "Workspace role as written in the file → actual role", "zh-Hant": "檔案裡的空間角色寫法 → 實際角色" },
  "文件里的组织角色写法 → 实际角色": { en: "Organization role as written in the file → actual role", "zh-Hant": "檔案裡的組織角色寫法 → 實際角色" },
  "文本行：{n}": { en: "Text lines: {n}", "zh-Hant": "文字行：{n}" },
  "文档引擎未接入：只能读取 CSV、TSV、JSON、TXT 与 Markdown。": { en: "Document engine not connected: only CSV, TSV, JSON, TXT and Markdown can be read.", "zh-Hant": "文件引擎未接入：只能讀取 CSV、TSV、JSON、TXT 與 Markdown。" },
  "新增组织角色：{roles}": { en: "Organization roles added: {roles}", "zh-Hant": "新增組織角色：{roles}" },
  "新成员：发送邀请": { en: "New person: send invitation", "zh-Hant": "新成員：發送邀請" },
  "暂不支持这种格式": { en: "This format is not supported yet", "zh-Hant": "暫不支援這種格式" },
  "更新": { en: "Update", "zh-Hant": "更新" },
  "最近的导入（{n}）": { en: "Recent imports ({n})", "zh-Hant": "最近的匯入（{n}）" },
  "此实例没有文字识别。": { en: "This instance has no text recognition.", "zh-Hant": "此執行個體沒有文字辨識。" },
  "此实例没有文字识别，扫描件与图片无法读取。": { en: "This instance has no text recognition; scans and images cannot be read.", "zh-Hant": "此執行個體沒有文字辨識，掃描件與圖片無法讀取。" },
  "没有这类行。": { en: "No rows of this kind.", "zh-Hant": "沒有這類列。" },
  "生成预览": { en: "Generate preview", "zh-Hant": "產生預覽" },
  "用手头的成员名单文件批量邀请：文件里的一行不会直接成为成员，新人仍然通过邀请邮件自己接受；已有成员只会提升空间角色或增加组织角色，不会被降级或移除。": { en: "Invite in bulk from the member list you already have: a row in a file never becomes a member by itself — newcomers still accept an invitation mail themselves; existing members only gain a higher workspace role or additional organization roles, never a demotion or removal.", "zh-Hant": "用手邊的成員名單檔案批次邀請：檔案裡的一列不會直接成為成員，新人仍然透過邀請郵件自己接受；既有成員只會提升空間角色或增加組織角色，不會被降級或移除。" },
  "目标工作空间": { en: "Target workspace", "zh-Hant": "目標工作空間" },
  "确认导入": { en: "Confirm import", "zh-Hant": "確認匯入" },
  "确认导入（邀请 {i} 人，更新 {u} 人）": { en: "Confirm import (invite {i}, update {u})", "zh-Hant": "確認匯入（邀請 {i} 人，更新 {u} 人）" },
  "空间管理员": { en: "Workspace administrator", "zh-Hant": "空間管理員" },
  "空间角色 {from} → {to}": { en: "Workspace role {from} → {to}", "zh-Hant": "空間角色 {from} → {to}" },
  "空间角色未映射": { en: "Workspace role not mapped", "zh-Hant": "空間角色未對應" },
  "第 {n} 列": { en: "Column {n}", "zh-Hant": "第 {n} 欄" },
  "第一行是标题": { en: "The first row is a header", "zh-Hant": "第一列是標題" },
  "组织成员：邀请加入此空间": { en: "Organization member: invite into this workspace", "zh-Hant": "組織成員：邀請加入此空間" },
  "组织角色未映射": { en: "Organization role not mapped", "zh-Hant": "組織角色未對應" },
  "编辑成员": { en: "Editor", "zh-Hant": "編輯成員" },
  "表格：{name}": { en: "Table: {name}", "zh-Hant": "表格：{name}" },
  "角色的权限不会从名字推断。没有合适的角色时，先在「自定义角色」里创建，再回到这里映射。": { en: "A role's permissions are never inferred from its name. If no role fits, create one under “Custom roles” first, then map it here.", "zh-Hant": "角色的權限不會從名字推斷。沒有合適的角色時，先在「自訂角色」裡建立，再回到這裡對應。" },
  "识别文字（OCR）": { en: "Recognize text (OCR)", "zh-Hant": "辨識文字（OCR）" },
  "请选择…": { en: "Choose…", "zh-Hant": "請選擇…" },
  "读取方式": { en: "Read as", "zh-Hant": "讀取方式" },
  "超出单次导入上限": { en: "Beyond the per-import limit", "zh-Hant": "超出單次匯入上限" },
  "跳过": { en: "Skip", "zh-Hant": "略過" },
  "跳过 {n} 行阻断项，只导入有效行": { en: "Skip the {n} blocked rows and import only valid ones", "zh-Hant": "略過 {n} 列阻斷項，只匯入有效列" },
  "这个文件没有可读文字（扫描件或图片）。": { en: "This file has no readable text (a scan or an image).", "zh-Hant": "這個檔案沒有可讀文字（掃描件或圖片）。" },
  "这是你自己": { en: "This is you", "zh-Hant": "這是你自己" },
  "邀请": { en: "Invite", "zh-Hant": "邀請" },
  "重试失败行": { en: "Retry failed rows", "zh-Hant": "重試失敗列" },
  "阻断": { en: "Blocked", "zh-Hant": "阻斷" },
  "默认空间角色": { en: "Default workspace role", "zh-Hant": "預設空間角色" },
  "默认组织角色（每个人都会获得）": { en: "Default organization roles (everyone receives them)", "zh-Hant": "預設組織角色（每個人都會獲得）" },
  "（已截断）": { en: "(truncated)", "zh-Hant": "（已截斷）" },
  "取消剩余行": { en: "Cancel remaining rows", "zh-Hant": "取消剩餘列" },
  "处理中": { en: "Processing", "zh-Hant": "處理中" },
  "失败": { en: "Failed", "zh-Hant": "失敗" },
  "姓名列": { en: "Name column", "zh-Hant": "姓名欄" },
  "安全检查：{status}": { en: "Security check: {status}", "zh-Hant": "安全檢查：{status}" },
  "安全检查：未检出已知威胁": { en: "Security check: no known threat detected", "zh-Hant": "安全檢查：未檢出已知威脅" },
  "安全检查：此实例未配置扫描引擎": { en: "Security check: no scanner is configured on this instance", "zh-Hant": "安全檢查：此執行個體未設定掃描引擎" },
  "完成": { en: "Done", "zh-Hant": "完成" },
  "已完成": { en: "Finished", "zh-Hant": "已完成" },
  "已生成预览": { en: "Preview generated", "zh-Hant": "已產生預覽" },
  "已读取": { en: "Read", "zh-Hant": "已讀取" },
  "已跳过": { en: "Skipped", "zh-Hant": "已略過" },
  "待处理": { en: "Pending", "zh-Hant": "待處理" },
  "执行中": { en: "Running", "zh-Hant": "執行中" },
  "正在取消…": { en: "Cancelling…", "zh-Hant": "正在取消…" },
  "空间角色列": { en: "Workspace role column", "zh-Hant": "空間角色欄" },
  "组织角色列": { en: "Organization role column", "zh-Hant": "組織角色欄" },
  "结果": { en: "Results", "zh-Hant": "結果" },
  "邮箱列（必填）": { en: "E-mail column (required)", "zh-Hant": "電郵欄（必填）" },
  "部分完成": { en: "Partly finished", "zh-Hant": "部分完成" },
  "预览": { en: "Preview", "zh-Hant": "預覽" },
  // ---- copy approvals (CI-27)
  "{n} 个字段": { en: "{n} fields", "zh-Hant": "{n} 個欄位" },
  "不包含": { en: "Not included", "zh-Hant": "不包含" },
  "不包含（只复制配置与模板；源项目有 {n} 个文件）": { en: "Not included (configuration and template only; the source has {n} files)", "zh-Hant": "不包含（只複製設定與範本；來源專案有 {n} 個檔案）" },
  "不复制": { en: "Not copied", "zh-Hant": "不複製" },
  "个人空间的项目要复制到本组织的工作空间，需要一位持有审批权限并管理目标空间的成员同意。你看到的是申请人确认过的清单，不是对方的个人空间。": { en: "Copying a personal-workspace project into one of this organization's workspaces needs consent from a member who holds the approval permission and administers the target workspace. What you see is the manifest the requester confirmed, not their personal workspace.", "zh-Hant": "個人空間的專案要複製到本組織的工作空間，需要一位持有審批權限並管理目標空間的成員同意。你看到的是申請人確認過的清單，不是對方的個人空間。" },
  "任务、产物、上传、授权、Webhook、API 凭据与原项目来源记录": { en: "Jobs, artifacts, uploads, grants, webhooks, API credentials and the original's provenance", "zh-Hant": "任務、產物、上傳、授權、Webhook、API 憑證與原專案來源記錄" },
  "修正与识别文字": { en: "Corrections and recognized text", "zh-Hant": "修正與辨識文字" },
  "修订 {rev}，{n} 条（启用 {e}）": { en: "Revision {rev}, {n} mappings ({e} enabled)", "zh-Hant": "修訂 {rev}，{n} 條（啟用 {e}）" },
  "副本": { en: "Copy", "zh-Hant": "副本" },
  "包含": { en: "Included", "zh-Hant": "包含" },
  "包含 {n} 个文件，共 {size}": { en: "{n} files included, {size} in total", "zh-Hant": "包含 {n} 個檔案，共 {size}" },
  "原因": { en: "Reason", "zh-Hant": "原因" },
  "复制中": { en: "Copying", "zh-Hant": "複製中" },
  "审批": { en: "Approvals", "zh-Hant": "審批" },
  "已批准，等待复制": { en: "Approved, waiting to copy", "zh-Hant": "已批准，等待複製" },
  "已拒绝": { en: "Rejected", "zh-Hant": "已拒絕" },
  "已撤回": { en: "Withdrawn", "zh-Hant": "已撤回" },
  "已过期": { en: "Expired", "zh-Hant": "已過期" },
  "待审批": { en: "Pending", "zh-Hant": "待審批" },
  "批准": { en: "Approve", "zh-Hant": "批准" },
  "批准后副本将按清单在「{workspace}」创建，受该空间的成员与保留策略管理；源项目之后的改动不会带入。批准 7 天内有效。": { en: "Once approved, the copy is created in “{workspace}” exactly as listed and is governed by that workspace's members and retention; later changes to the source are not carried over. The approval is valid for 7 days.", "zh-Hant": "批准後副本將按清單在「{workspace}」建立，受該空間的成員與保留策略管理；來源專案之後的改動不會帶入。批准 7 天內有效。" },
  "批准复制": { en: "Approve copy", "zh-Hant": "批准複製" },
  "拒绝": { en: "Reject", "zh-Hant": "拒絕" },
  "拒绝后申请人会收到你的理由，可以修改后重新申请。": { en: "The requester receives your reason and may adjust and ask again.", "zh-Hant": "拒絕後申請人會收到你的理由，可以修改後重新申請。" },
  "拒绝复制": { en: "Reject copy", "zh-Hant": "拒絕複製" },
  "收起清单": { en: "Hide manifest", "zh-Hant": "收起清單" },
  "映射": { en: "Mappings", "zh-Hant": "對應" },
  "未设置": { en: "Not set", "zh-Hant": "未設定" },
  "来源数据": { en: "Source data", "zh-Hant": "來源資料" },
  "查看清单": { en: "View manifest", "zh-Hant": "檢視清單" },
  "模板": { en: "Template", "zh-Hant": "範本" },
  "没有待审批的申请。": { en: "No pending requests.", "zh-Hant": "沒有待審批的申請。" },
  "由 {who} 决定": { en: "Decided by {who}", "zh-Hant": "由 {who} 決定" },
  "申请人自行批准": { en: "Self-approved by the requester", "zh-Hant": "申請人自行批准" },
  "申请人：{who} · {when}": { en: "Requester: {who} · {when}", "zh-Hant": "申請人：{who} · {when}" },
  "源项目「{name}」": { en: "Source project “{name}”", "zh-Hant": "源專案「{name}」" },
  "等待检查": { en: "Awaiting check", "zh-Hant": "等待檢查" },
  "未配置扫描": { en: "Scanning not configured", "zh-Hant": "未設定掃描" },
  "检查失败": { en: "Check failed", "zh-Hant": "檢查失敗" },
  "超出扫描大小": { en: "Over the scan size limit", "zh-Hant": "超出掃描大小" },
  "检出威胁": { en: "Threat detected", "zh-Hant": "檢出威脅" },
  "还没有申请。": { en: "No requests yet.", "zh-Hant": "還沒有申請。" },
  "需重新申请": { en: "Needs a new request", "zh-Hant": "需重新申請" },
  "项目 {id}，{s} 个来源，{m} 条映射": { en: "Project {id}, {s} sources, {m} mappings", "zh-Hant": "專案 {id}，{s} 個來源，{m} 條對應" },
  "（已固定）": { en: "(pinned)", "zh-Hant": "（已固定）" },
  "举报处理": { en: "Reports", "zh-Hant": "檢舉處理" },
  "成员举报的消息。你看到的是举报时的消息内容、发送者、举报人和原因，不是整段会话。删除会让这条消息对所有人显示为「消息已删除」；无论删除还是驳回，举报人都会收到结果。": { en: "Messages members reported. You see the message as it was when reported, its sender, the reporter and the reason — not the conversation. Removing shows “message deleted” to everyone; the reporter is told the outcome either way.", "zh-Hant": "成員檢舉的訊息。你看到的是檢舉時的訊息內容、傳送者、檢舉人和原因，不是整段對話。刪除會讓這則訊息對所有人顯示為「訊息已刪除」；無論刪除還是駁回，檢舉人都會收到結果。" },
  "没有待处理的举报。": { en: "No open reports.", "zh-Hant": "沒有待處理的檢舉。" },
  "还没有举报。": { en: "No reports yet.", "zh-Hant": "還沒有檢舉。" },
  "群聊「{name}」": { en: "Group “{name}”", "zh-Hant": "群組「{name}」" },
  "私聊": { en: "Direct message", "zh-Hant": "私訊" },
  "举报人：{who} · {when}": { en: "Reported by {who} · {when}", "zh-Hant": "檢舉人：{who} · {when}" },
  "原因：{reason}": { en: "Reason: {reason}", "zh-Hant": "原因：{reason}" },
  "由 {who} 处理 · {when}": { en: "Reviewed by {who} · {when}", "zh-Hant": "由 {who} 處理 · {when}" },
  "删除消息": { en: "Remove message", "zh-Hant": "刪除訊息" },
  "驳回举报": { en: "Dismiss report", "zh-Hant": "駁回檢舉" },
  "删除被举报的消息": { en: "Remove the reported message", "zh-Hant": "刪除被檢舉的訊息" },
  "已删除消息": { en: "Message removed", "zh-Hant": "已刪除訊息" },
  "已驳回": { en: "Dismissed", "zh-Hant": "已駁回" },
  "这条消息将对所有人显示为「消息已删除」，同一条消息的其它举报一并处理；举报人会收到结果和你的说明。": { en: "The message will show as “message deleted” to everyone and every other report of it is settled too; the reporter receives the outcome and your note.", "zh-Hant": "這則訊息將對所有人顯示為「訊息已刪除」，同一則訊息的其它檢舉一併處理；檢舉人會收到結果和你的說明。" },
  "消息保留；同一条消息的其它举报一并驳回；举报人会收到结果和你的说明。": { en: "The message stays; every other report of it is dismissed too; the reporter receives the outcome and your note.", "zh-Hant": "訊息保留；同一則訊息的其它檢舉一併駁回；檢舉人會收到結果和你的說明。" },
  "骚扰或辱骂": { en: "Harassment or abuse", "zh-Hant": "騷擾或辱罵" },
  "垃圾信息": { en: "Spam", "zh-Hant": "垃圾訊息" },
  "泄露隐私或索要凭据": { en: "Privacy leak or asking for credentials", "zh-Hant": "洩露隱私或索取憑據" },
  "其他": { en: "Other", "zh-Hant": "其他" },
};

registerMessages(messages);
