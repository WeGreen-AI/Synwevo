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
};

registerMessages(messages);
