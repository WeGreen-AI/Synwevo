/** A37: the platform-controlled ownership recovery panel (governance/OwnershipRecovery.tsx) and the
 *  refusals of its recover-owner routes (governance/labels.ts). Every sentence of the panel, the
 *  confirmation and the refusal map is here, in the three languages. */
import { registerMessages } from "@synwevo/ui";
registerMessages({
  // ---- the panel
  "没有有效拥有者的工作空间": { en: "Workspaces without a usable owner", "zh-Hant": "沒有有效擁有者的工作空間" },
  "没有有效拥有者的组织": { en: "Organizations without a usable owner", "zh-Hant": "沒有有效擁有者的組織" },
  "这些工作空间没有任何一位可用的拥有者：原拥有者的账户已停用、已删除，或已不在所属组织。": {
    en: "None of these workspaces has a usable owner: the former owners' accounts are suspended or deleted, or they have left the organization the workspace belongs to.",
    "zh-Hant": "這些工作空間沒有任何一位可用的擁有者：原擁有者的帳戶已停用、已刪除，或已不在所屬組織。" },
  "这些组织没有任何一位可用的拥有者：原拥有者的账户已停用或已删除。": {
    en: "None of these organizations has a usable owner: the former owners' accounts are suspended or deleted.",
    "zh-Hant": "這些組織沒有任何一位可用的擁有者：原擁有者的帳戶已停用或已刪除。" },
  "平台管理可以把其中一位现有成员指定为拥有者：不新增成员，不授予任何平台管理员内容访问；接任的成员必须已启用第二因素。": {
    en: "Platform administration can name one of the existing members as owner: no member is added, no platform administrator gains content access, and the successor must have a second factor enabled.",
    "zh-Hant": "平台管理可以把其中一位現有成員指定為擁有者：不新增成員，不授予任何平台管理員內容存取；接任的成員必須已啟用第二因素。" },
  "你的平台角色可以查看这份清单，但不能指定拥有者。": { en: "Your platform role can view this list but cannot name an owner.", "zh-Hant": "你的平台角色可以查看這份清單，但不能指定擁有者。" },
  "正在读取拥有者情况…": { en: "Reading ownership…", "zh-Hant": "正在讀取擁有者情況…" },
  "读取于 {time}": { en: "Read at {time}", "zh-Hant": "讀取於 {time}" },
  "原因": { en: "Why", "zh-Hant": "原因" },
  "{count} 个工作空间": { en: { one: "{count} workspace", other: "{count} workspaces" }, "zh-Hant": "{count} 個工作空間" },
  "账户已删除": { en: "Account deleted", "zh-Hant": "帳戶已刪除" },
  "账户已停用": { en: "Account suspended", "zh-Hant": "帳戶已停用" },
  "账户待删除": { en: "Account pending deletion", "zh-Hant": "帳戶待刪除" },
  "账户待激活": { en: "Account pending activation", "zh-Hant": "帳戶待啟用" },
  "已不在组织": { en: "No longer in the organization", "zh-Hant": "已不在組織" },
  "没有拥有者成员": { en: "No owner membership at all", "zh-Hant": "沒有擁有者成員" },
  "指定拥有者": { en: "Name an owner", "zh-Hant": "指定擁有者" },
  "当前没有缺少有效拥有者的工作空间。": { en: "No workspace is without a usable owner.", "zh-Hant": "目前沒有缺少有效擁有者的工作空間。" },
  "当前没有缺少有效拥有者的组织。": { en: "No organization is without a usable owner.", "zh-Hant": "目前沒有缺少有效擁有者的組織。" },
  "共 {total} 个，这里只显示前 {shown} 个。": { en: "Showing the first {shown} of {total}.", "zh-Hant": "共 {total} 個，這裡只顯示前 {shown} 個。" },
  "已指定 {email} 为拥有者。": { en: "{email} is now the owner.", "zh-Hant": "已指定 {email} 為擁有者。" },
  // ---- the confirmation
  "工作空间「{name}」目前没有有效拥有者。": { en: "Workspace “{name}” has no usable owner.", "zh-Hant": "工作空間「{name}」目前沒有有效擁有者。" },
  "组织「{name}」目前没有有效拥有者。": { en: "Organization “{name}” has no usable owner.", "zh-Hant": "組織「{name}」目前沒有有效擁有者。" },
  "{email} 将成为拥有者（当前角色：{role}）；其他成员的角色和权限不变。": {
    en: "{email} becomes the owner (current role: {role}); every other member keeps their role and permissions.",
    "zh-Hant": "{email} 將成為擁有者（目前角色：{role}）；其他成員的角色和權限不變。" },
  "请先选择一位现有成员；其他成员的角色和权限不变。": { en: "Choose an existing member first; every other member keeps their role and permissions.", "zh-Hant": "請先選擇一位現有成員；其他成員的角色和權限不變。" },
  "这不会给任何平台管理员带来成员身份或内容访问；理由会原样写入审计记录。": {
    en: "No platform administrator gains membership or content access; the reason is written to the audit record as typed.",
    "zh-Hant": "這不會給任何平台管理員帶來成員身分或內容存取；理由會原樣寫入審計記錄。" },
  "新拥有者": { en: "New owner", "zh-Hant": "新擁有者" },
  "请选择成员": { en: "Choose a member", "zh-Hant": "請選擇成員" },
  "只能选择账户正常、已启用第二因素且没有平台权限的现有成员；不符合条件的成员会标明原因。": {
    en: "Only an existing member whose account is active, who has a second factor enabled and who holds no platform permission can be chosen; members who do not qualify are listed with the reason.",
    "zh-Hant": "只能選擇帳戶正常、已啟用第二因素且沒有平台權限的現有成員；不符合條件的成員會標明原因。" },
  "这个工作空间没有可指定的成员。": { en: "This workspace has no member to name.", "zh-Hant": "這個工作空間沒有可指定的成員。" },
  "这个组织没有可指定的成员。": { en: "This organization has no member to name.", "zh-Hant": "這個組織沒有可指定的成員。" },
  "当前没有符合条件的成员：请先让其中一位启用第二因素，或恢复其账户。": {
    en: "No member qualifies right now: ask one of them to enable a second factor, or restore their account first.",
    "zh-Hant": "目前沒有符合條件的成員：請先讓其中一位啟用第二因素，或恢復其帳戶。" },
  "已启用第二因素": { en: "Second factor enabled", "zh-Hant": "已啟用第二因素" },
  "未启用第二因素": { en: "Second factor not enabled", "zh-Hant": "未啟用第二因素" },
  "持有平台权限": { en: "Holds platform permissions", "zh-Hant": "持有平台權限" },
  "账户不可用": { en: "Account unavailable", "zh-Hant": "帳戶不可用" },
  // ---- the recover-owner routes' refusals, by code (governance/labels.ts)
  "这里已经有有效的拥有者，不需要指定；请刷新清单。": { en: "There is already a usable owner here; nothing to name. Refresh the list.", "zh-Hant": "這裡已經有有效的擁有者，不需要指定；請重新整理清單。" },
  "这里当前不能由平台指定拥有者：待认领、已删除，或是只剩本人的个人空间。": {
    en: "An owner cannot be named here by the platform: it is awaiting a claim, deleted, or a personal workspace with nobody else in it.",
    "zh-Hant": "這裡目前不能由平台指定擁有者：待認領、已刪除，或是只剩本人的個人空間。" },
  "所选账户不是这里的成员，或账户已停用、已删除；请刷新后重新选择。": {
    en: "The chosen account is not a member here, or it is suspended or deleted. Refresh and choose again.",
    "zh-Hant": "所選帳戶不是這裡的成員，或帳戶已停用、已刪除；請重新整理後重新選擇。" },
  "所选账户已不在这个工作空间所属的组织，不能成为拥有者。": {
    en: "The chosen account is no longer in the organization this workspace belongs to and cannot become its owner.",
    "zh-Hant": "所選帳戶已不在這個工作空間所屬的組織，不能成為擁有者。" },
  "所选账户尚未启用第二因素，不能成为拥有者。": { en: "The chosen account has no second factor enabled and cannot become the owner.", "zh-Hant": "所選帳戶尚未啟用第二因素，不能成為擁有者。" },
  "不能指定自己，请由另一位管理员操作。": { en: "You cannot name yourself; ask another administrator.", "zh-Hant": "不能指定自己，請由另一位管理員操作。" },
  "所选账户持有平台权限：平台管理与业务数据须由不同账户持有。": {
    en: "The chosen account holds platform permissions: platform administration and business data must be held by different accounts.",
    "zh-Hant": "所選帳戶持有平台權限：平台管理與業務資料須由不同帳戶持有。" },
  "请选择一位成员。": { en: "Choose a member.", "zh-Hant": "請選擇一位成員。" },
  "共 {total} 位成员，这里只列出前 {shown} 位（符合条件者优先）。": { en: "{total} members in all; only the first {shown} are listed here (eligible members first).", "zh-Hant": "共 {total} 位成員，這裡只列出前 {shown} 位（符合條件者優先）。" },
  // the audit page's scope shortcut for the organization administration records (access.ts)
  "组织管理": { en: "Organization administration", "zh-Hant": "組織管理" },
});
