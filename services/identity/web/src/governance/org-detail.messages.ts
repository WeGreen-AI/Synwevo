/** A34: the platform's read-only organization detail (governance/OrganizationDetail.tsx) — the
 *  organizations tab's 详情 action, the panel's sections, the member search and the per-member
 *  permission explainer, and the refusals of `/api/admin/organizations/{oid}` and its workspaces,
 *  members and permissions routes worded by code (governance/labels.ts). Every sentence is here in
 *  the three languages. The sentences the tenant organization pages already word (the two policies,
 *  the import and copy states, the effective-permission explainer, the server's own refusals) are
 *  repeated with the tenant wording, because that catalogue is not part of the administration bundle;
 *  labels other admin lists already have (状态, 加入时间, 详情, 不统计, …) stay in their catalogues. */
import { registerMessages } from "@synwevo/ui";
export const orgDetailMessages = {
  // ---- the list action and the panel
  "查看组织详情：{name}": { en: "View organization details: {name}", "zh-Hant": "檢視組織詳情：{name}" },
  "组织详情": { en: "Organization details", "zh-Hant": "組織詳情" },
  "组织详情：{name}": { en: "Organization details: {name}", "zh-Hant": "組織詳情：{name}" },
  "正在读取组织详情…": { en: "Loading organization details…", "zh-Hant": "正在讀取組織詳情…" },
  "平台管理只读取组织的治理元数据：不授予成员身份，不显示项目、模板、导入文件或文档内容。": {
    en: "Platform administration reads only the organization's governance metadata: it grants no membership and shows no projects, templates, imported files or document content.",
    "zh-Hant": "平台管理只讀取組織的治理中繼資料：不授予成員身份，不顯示專案、範本、匯入檔案或文件內容。" },
  "你的平台角色不能查看组织详情。": { en: "Your platform role cannot view organization details.", "zh-Hant": "你的平台角色不能檢視組織詳情。" },
  "你在这个组织的角色：{role}。组织设置页按这个成员角色开放；这里是平台治理视图。": {
    en: "Your role in this organization: {role}. Its settings page follows that member role; this is the platform governance view.",
    "zh-Hant": "你在這個組織的角色：{role}。組織設定頁按這個成員角色開放；這裡是平台治理檢視。" },
  "你不是这个组织的成员：这里只显示治理元数据，不授予成员身份或内容访问；组织设置页对你不可用。": {
    en: "You are not a member of this organization: only governance metadata is shown here, granting neither membership nor content access, and its settings page is not available to you.",
    "zh-Hant": "你不是這個組織的成員：這裡只顯示治理中繼資料，不授予成員身份或內容存取；組織設定頁對你不可用。" },
  // ---- overview
  "概况": { en: "Overview", "zh-Hant": "概況" },
  "恢复期限已过（{time}），不能再恢复。": { en: "The recovery deadline passed ({time}); it can no longer be recovered.", "zh-Hant": "恢復期限已過（{time}），不能再恢復。" },
  "组织头像": { en: "Organization avatar", "zh-Hant": "組織頭像" },
  "邀请策略": { en: "Invitation policy", "zh-Hant": "邀請策略" },
  "所有者和管理员": { en: "Owners and administrators", "zh-Hant": "擁有者和管理員" },
  "仅所有者": { en: "Owners only", "zh-Hant": "僅擁有者" },
  "组织所有者和管理员可发送工作空间邀请。": { en: "Organization owners and administrators can send workspace invitations.", "zh-Hant": "組織擁有者和管理員可傳送工作空間邀請。" },
  "仅组织所有者可发送工作空间邀请。": { en: "Only organization owners can send workspace invitations.", "zh-Hant": "僅組織擁有者可傳送工作空間邀請。" },
  "内容外发上限": { en: "Content export cap", "zh-Hant": "內容外發上限" },
  "仅限组织内的工作空间": { en: "Only workspaces of this organization", "zh-Hant": "僅限組織內的工作空間" },
  "按各工作空间的数据流政策": { en: "As each workspace's data-flow policy allows", "zh-Hant": "按各工作空間的資料流政策" },
  "组织内容只能在组织的工作空间之间转移。": { en: "Organization content can only move between the organization's workspaces.", "zh-Hant": "組織內容只能在組織的工作空間之間轉移。" },
  "组织内容可按各工作空间的数据流政策带到组织以外。": { en: "Organization content may leave the organization as each workspace's data-flow policy allows.", "zh-Hant": "組織內容可按各工作空間的資料流政策帶到組織以外。" },
  "所有者人数": { en: "Owners", "zh-Hant": "擁有者人數" },
  "管理员人数": { en: "Administrators", "zh-Hant": "管理員人數" },
  "工作空间数": { en: "Workspaces", "zh-Hant": "工作空間數" },
  "自定义角色": { en: "Custom roles", "zh-Hant": "自訂角色" },
  "其中可用 {count}": { en: "{count} usable", "zh-Hant": "其中可用 {count}" },
  "其中已冻结 {count}": { en: "{count} frozen", "zh-Hant": "其中已凍結 {count}" },
  "未完成的成员导入": { en: "Unfinished member imports", "zh-Hant": "未完成的成員匯入" },
  "未完成的复制申请": { en: "Unfinished copy requests", "zh-Hant": "未完成的複製申請" },
  // ---- owners and administrators
  "所有者与管理员": { en: "Owners and administrators", "zh-Hant": "擁有者與管理員" },
  "这个组织没有可用的所有者：所有者的账户已停用或已删除。": {
    en: "This organization has no usable owner: its owners' accounts are suspended or deleted.",
    "zh-Hant": "這個組織沒有可用的擁有者：擁有者的帳戶已停用或已刪除。" },
  "可以在本页「没有有效拥有者的组织」中把一位现有成员指定为拥有者。": {
    en: "You can name an existing member as owner under “Organizations without a usable owner” on this page.",
    "zh-Hant": "可以在本頁「沒有有效擁有者的組織」中把一位現有成員指定為擁有者。" },
  "所有者（{count} 位）": { en: "Owners ({count})", "zh-Hant": "擁有者（{count} 位）" },
  "管理员（{count} 位）": { en: "Administrators ({count})", "zh-Hant": "管理員（{count} 位）" },
  "没有所有者。": { en: "No owners.", "zh-Hant": "沒有擁有者。" },
  "没有管理员。": { en: "No administrators.", "zh-Hant": "沒有管理員。" },
  // "账户不可用" is worded by the account catalogues already ("Account unavailable")
  "账户可用": { en: "Account available", "zh-Hant": "帳戶可用" },
  "共 {total} 位，这里按加入时间显示前 {shown} 位；在下方成员列表中按角色筛选可以看到全部。": {
    en: "{total} in all; the first {shown} by joining date are shown here. Filter the member list below by role to see everyone.",
    "zh-Hant": "共 {total} 位，這裡按加入時間顯示前 {shown} 位；在下方成員列表中按角色篩選可以看到全部。" },
  // ---- workspaces
  "不含已删除的工作空间；只显示名称与成员人数，不显示项目或内容。": {
    en: "Deleted workspaces are not listed; only names and member counts are shown, never projects or content.",
    "zh-Hant": "不含已刪除的工作空間；只顯示名稱與成員人數，不顯示專案或內容。" },
  "这个组织还没有工作空间。": { en: "This organization has no workspaces yet.", "zh-Hant": "這個組織還沒有工作空間。" },
  "组织的工作空间": { en: "The organization's workspaces", "zh-Hant": "組織的工作空間" },
  // ---- members
  "组织成员": { en: "Organization members", "zh-Hant": "組織成員" },
  "搜索姓名或邮箱": { en: "Search name or email", "zh-Hant": "搜尋姓名或電郵" },
  "搜索姓名": { en: "Search name", "zh-Hant": "搜尋姓名" },
  "组织角色": { en: "Organization role", "zh-Hant": "組織角色" },
  "你的平台角色不能查看账户邮箱：这里不显示邮箱，搜索只按姓名匹配。": {
    en: "Your platform role cannot read account email addresses: none are shown here, and the search matches names only.",
    "zh-Hant": "你的平台角色不能檢視帳戶電郵：這裡不顯示電郵，搜尋只按姓名比對。" },
  "正在加载成员…": { en: "Loading members…", "zh-Hant": "正在載入成員…" },
  "查看有效权限：{name}": { en: "View effective permissions: {name}", "zh-Hant": "檢視有效權限：{name}" },
  "没有符合条件的成员。": { en: "No members match your search.", "zh-Hant": "沒有符合條件的成員。" },
  "这个组织没有成员。": { en: "This organization has no members.", "zh-Hant": "這個組織沒有成員。" },
  // ---- the effective-permission explainer (the tenant members page's wording)
  "有效权限：{name}": { en: "Effective permissions: {name}", "zh-Hant": "有效權限：{name}" },
  "正在计算有效权限…": { en: "Working out the effective permissions…", "zh-Hant": "正在計算有效權限…" },
  "基础角色：{role}": { en: "Base role: {role}", "zh-Hant": "基礎角色：{role}" },
  "自定义角色：{names}": { en: "Custom roles: {names}", "zh-Hant": "自訂角色：{names}" },
  "基础角色 {role}": { en: "base role {role}", "zh-Hant": "基礎角色 {role}" },
  "角色「{name}」": { en: "role “{name}”", "zh-Hant": "角色「{name}」" },
  "该成员没有组织级权限；工作空间内的权限另行管理。": {
    en: "This member holds no organization-level permissions; workspace permissions are managed separately.",
    "zh-Hant": "此成員沒有組織層級權限；工作空間內的權限另行管理。" },
  "按成员资格与角色计算得出，不是该成员登录后看到的内容。": {
    en: "Computed from membership and roles, not what that member sees after signing in.",
    "zh-Hant": "依成員資格與角色計算得出，不是該成員登入後看到的內容。" },
  "这个账户当前不可用（{status}），组织权限对它不生效。": {
    en: "This account is not active ({status}), so no organization permissions apply to it.",
    "zh-Hant": "這個帳戶目前不可用（{status}），組織權限對它不生效。" },
  "这个账户当前没有生效的组织角色，组织权限对它不生效。": {
    en: "This account has no organization role in effect, so no organization permissions apply to it.",
    "zh-Hant": "這個帳戶目前沒有生效的組織角色，組織權限對它不生效。" },
  "权限": { en: "Permission", "zh-Hant": "權限" },
  "来源": { en: "Source", "zh-Hant": "來源" },
  // ---- custom roles
  "自定义角色（{count} 个）": { en: "Custom roles ({count})", "zh-Hant": "自訂角色（{count} 個）" },
  "自定义角色把一组组织级权限分配给成员，不改变工作空间内的权限。": {
    en: "A custom role gives members a set of organization-level permissions; it never changes permissions inside workspaces.",
    "zh-Hant": "自訂角色把一組組織層級權限指派給成員，不改變工作空間內的權限。" },
  "还没有自定义角色。": { en: "No custom roles yet.", "zh-Hant": "還沒有自訂角色。" },
  "已分配给 {count} 位成员": { en: "Assigned to {count} member(s)", "zh-Hant": "已指派給 {count} 位成員" },
  "这个角色没有权限。": { en: "This role holds no permissions.", "zh-Hant": "這個角色沒有權限。" },
  // ---- pending imports and copies
  "未完成的成员导入与复制申请": { en: "Unfinished member imports and copy requests", "zh-Hant": "未完成的成員匯入與複製申請" },
  "只显示状态、行数和时间：导入的文件、行内容和项目名称不显示。": {
    en: "Only states, row counts and times are shown: imported files, row contents and project names are not.",
    "zh-Hant": "只顯示狀態、行數和時間：匯入的檔案、行內容和專案名稱不顯示。" },
  "这个部署没有成员导入记录，不统计。": { en: "This deployment keeps no member-import records, so none are counted.", "zh-Hant": "這個部署沒有成員匯入紀錄，不統計。" },
  "没有未完成的成员导入。": { en: "No unfinished member imports.", "zh-Hant": "沒有未完成的成員匯入。" },
  "导入编号": { en: "Import ID", "zh-Hant": "匯入編號" },
  "行数": { en: "Rows", "zh-Hant": "行數" },
  "发起人": { en: "Started by", "zh-Hant": "發起人" },
  "共 {total} 项，这里显示最新的 {shown} 项。": { en: "{total} in all; the newest {shown} are shown here.", "zh-Hant": "共 {total} 項，這裡顯示最新的 {shown} 項。" },
  "项目复制申请": { en: "Project copy requests", "zh-Hant": "專案複製申請" },
  "这个部署没有项目复制记录，不统计。": { en: "This deployment keeps no project-copy records, so none are counted.", "zh-Hant": "這個部署沒有專案複製紀錄，不統計。" },
  "没有未完成的复制申请。": { en: "No unfinished copy requests.", "zh-Hant": "沒有未完成的複製申請。" },
  "申请编号": { en: "Request ID", "zh-Hant": "申請編號" },
  "生成预览后计数": { en: "Counted once previewed", "zh-Hant": "產生預覽後計數" },
  "当前步骤截止": { en: "Current step due", "zh-Hant": "目前步驟截止" },
  "执行中，无截止": { en: "Running, no deadline", "zh-Hant": "執行中，無截止" },
  "来源空间": { en: "Source workspace", "zh-Hant": "來源空間" },
  "目标空间": { en: "Target workspace", "zh-Hant": "目標空間" },
  // member_imports.status and project_copies.status (the tenant import and approvals pages' wording)
  "已读取": { en: "Read", "zh-Hant": "已讀取" },
  "已生成预览": { en: "Preview generated", "zh-Hant": "已產生預覽" },
  "部分完成": { en: "Partly finished", "zh-Hant": "部分完成" },
  "待审批": { en: "Pending", "zh-Hant": "待審批" },
  "已批准，等待复制": { en: "Approved, waiting to copy", "zh-Hant": "已批准，等待複製" },
  "复制中": { en: "Copying", "zh-Hant": "複製中" },
  "已撤回": { en: "Withdrawn", "zh-Hant": "已撤回" },
  "需重新申请": { en: "Needs a new request", "zh-Hant": "需重新申請" },
  // ---- refusals: this panel's wording by code (OrganizationDetail.tsx ORG_DETAIL_REFUSALS)
  "找不到这个组织：它可能已被永久清除。请刷新组织列表。": {
    en: "This organization was not found: it may have been purged. Refresh the organization list.",
    "zh-Hant": "找不到這個組織：它可能已被永久清除。請重新整理組織列表。" },
  "这个账户已不是该组织的成员；请刷新成员列表。": {
    en: "This account is no longer a member of the organization. Refresh the member list.",
    "zh-Hant": "這個帳戶已不是該組織的成員；請重新整理成員列表。" },
  "请选择有效的组织角色筛选。": { en: "Choose a valid organization role filter.", "zh-Hant": "請選擇有效的組織角色篩選。" },
  "搜索或分页参数无效，请清除搜索后重试。": { en: "The search or paging is invalid. Clear the search and try again.", "zh-Hant": "搜尋或分頁參數無效，請清除搜尋後重試。" },
  // every admin page's wording for the codes (labels.ts), when the server's sentence is not catalogued
  "请选择有效的角色。": { en: "Choose a valid role.", "zh-Hant": "請選擇有效的角色。" },
  "有字段无效，请检查输入后重试。": { en: "A field is invalid. Check your input and try again.", "zh-Hant": "有欄位無效，請檢查輸入後重試。" },
  // the core's own sentences for the organization member page (organizations.py `member_page`), which
  // the platform route reuses, worded as the tenant pages word them
  "找不到该组织": { en: "This organization was not found.", "zh-Hant": "找不到此組織" },
  "请选择有效的组织角色": { en: "Choose a valid organization role.", "zh-Hant": "請選擇有效的組織角色" },
  "搜索或分页参数无效": { en: "The search or pagination parameters are invalid.", "zh-Hant": "搜尋或分頁參數無效" },
  // the platform route's own sentence for a member that is not one (admin_org_detail.py)
  "该账户不是这个组织的成员": { en: "This account is not a member of this organization.", "zh-Hant": "該帳戶不是這個組織的成員" },
};
registerMessages(orgDetailMessages);
