/** A35: the platform-scope organization lifecycle — the organizations tab's controls, the
 *  preview dialog (governance/OrganizationLifecycleDialog.tsx: target, counts, warning,
 *  confirmation) and the refusals of `/api/admin/organizations/{id}/preview` and `/{action}`
 *  (governance/labels.ts). Every sentence is here in the three languages; the labels the tab
 *  already had (冻结组织, 恢复期限：{time}, 组织状态已更新, …) stay in messages.ts. */
import { registerMessages } from "@synwevo/ui";
export const organizationLifecycleMessages = {
  // ---- the tab
  "平台管理员可查看组织元数据与数量。持有组织管理权限的平台管理员可以在这里冻结、解冻、删除或恢复任何组织：先预览影响，再写明理由并重新验证身份；这不会授予成员身份或业务文档访问权。": {
    en: "Platform administrators can view organization metadata and counts. Those holding the organization management permission can freeze, unfreeze, delete or recover any organization from here: an impact preview first, then a written reason and re-authentication. This grants neither membership nor business document access.",
    "zh-Hant": "平台管理員可查看組織中繼資料與數量。持有組織管理權限的平台管理員可以在這裡凍結、解凍、刪除或恢復任何組織：先預覽影響，再寫明理由並重新驗證身份；這不會授予成員身份或業務文件存取權。" },
  "恢复期限已过，不能再恢复。": { en: "The recovery deadline has passed; it can no longer be recovered.", "zh-Hant": "恢復期限已過，不能再恢復。" },
  // ---- the dialog: states and controls
  "正在核对组织与操作影响…": { en: "Checking the organization and impact…", "zh-Hant": "正在核對組織與操作影響…" },
  "重新预览": { en: "Preview again", "zh-Hant": "重新預覽" },
  "预览有效至 {time}。组织变化后必须重新预览。": { en: "Preview valid until {time}. Preview again if the organization changes.", "zh-Hant": "預覽有效至 {time}。組織變更後必須重新預覽。" },
  "我已核对目标组织和上述影响。": { en: "I have checked the target organization and these effects.", "zh-Hant": "我已核對目標組織和上述影響。" },
  "这份预览已失效，请重新预览后再确认。": { en: "This preview is no longer valid. Preview again before confirming.", "zh-Hant": "這份預覽已失效，請重新預覽後再確認。" },
  "此操作由平台执行并写入审计记录；不会给任何平台管理员带来成员身份、资格或业务文档访问。": {
    en: "This is a platform action and is written to the audit record; it gives no platform administrator a membership, an entitlement or access to business documents.",
    "zh-Hant": "此操作由平台執行並寫入稽核記錄；不會給任何平台管理員帶來成員身份、資格或業務文件存取。" },
  // ---- the dialog: the target
  "目标组织": { en: "Target organization", "zh-Hant": "目標組織" },
  "删除时间：{time}": { en: "Deleted at {time}", "zh-Hant": "刪除時間：{time}" },
  "删除前状态：{status}": { en: "Status before deletion: {status}", "zh-Hant": "刪除前狀態：{status}" },
  // ---- the dialog: the counts and what each one means
  "操作影响": { en: "Impact", "zh-Hant": "操作影響" },
  "会话": { en: "Sessions", "zh-Hant": "工作階段" },
  "应用令牌": { en: "Application tokens", "zh-Hant": "應用程式權杖" },
  "待接受的邀请": { en: "Pending invitations", "zh-Hant": "待接受的邀請" },
  "API 凭据批准": { en: "API credential approvals", "zh-Hant": "API 憑證批准" },
  "运行中的任务": { en: "Running jobs", "zh-Hant": "執行中的任務" },
  // freeze and delete take authority away
  "成员在下一次请求时失去访问；成员关系保留。": { en: "Members lose access on their next request; memberships are kept.", "zh-Hant": "成員在下一次請求時失去存取；成員關係保留。" },
  "所属工作空间停止应用访问；内容不会删除，也不会提供给管理员。": { en: "Its workspaces lose application access; nothing is deleted and no content is shown to administrators.", "zh-Hant": "所屬工作空間停止應用存取；內容不會刪除，也不會提供給管理員。" },
  "这些会话将被撤销。": { en: "These sessions are revoked.", "zh-Hant": "這些工作階段將被撤銷。" },
  "这些应用令牌将被撤销。": { en: "These application tokens are revoked.", "zh-Hant": "這些應用程式權杖將被撤銷。" },
  "这些尚未接受的邀请将被撤回。": { en: "These pending invitations are withdrawn.", "zh-Hant": "這些尚未接受的邀請將被撤回。" },
  "这些 API 凭据批准将被撤销；凭据本身不会删除。": { en: "These API credential approvals are revoked; the credentials themselves are not deleted.", "zh-Hant": "這些 API 憑證批准將被撤銷；憑證本身不會刪除。" },
  "正在运行的任务会在下一次权限检查时停止；已生成的数据保留。": { en: "Running jobs stop at their next permission check; generated data is kept.", "zh-Hant": "正在執行的任務會在下一次權限檢查時停止；已產生的資料保留。" },
  // unfreeze and recover revoke nothing
  "成员按原有成员关系恢复访问。": { en: "Members regain access under their existing memberships.", "zh-Hant": "成員按原有成員關係恢復存取。" },
  "所属工作空间恢复可用；此操作不改变其内容。": { en: "Its workspaces become available again; their content is unchanged.", "zh-Hant": "所屬工作空間恢復可用；此操作不改變其內容。" },
  "现有会话不受影响；之前撤销的会话不会恢复。": { en: "Existing sessions are untouched; sessions revoked earlier do not come back.", "zh-Hant": "現有工作階段不受影響；之前撤銷的工作階段不會恢復。" },
  "现有应用令牌不受影响；之前撤销的令牌不会恢复。": { en: "Existing application tokens are untouched; tokens revoked earlier do not come back.", "zh-Hant": "現有應用程式權杖不受影響；之前撤銷的權杖不會恢復。" },
  "现有邀请不受影响；之前撤回的邀请不会恢复。": { en: "Existing invitations are untouched; invitations withdrawn earlier do not come back.", "zh-Hant": "現有邀請不受影響；之前撤回的邀請不會恢復。" },
  "现有 API 凭据批准不受影响；之前撤销的批准不会恢复。": { en: "Existing API credential approvals are untouched; approvals revoked earlier do not come back.", "zh-Hant": "現有 API 憑證批准不受影響；之前撤銷的批准不會恢復。" },
  "正在运行的任务不受影响。": { en: "Running jobs are not affected.", "zh-Hant": "執行中的任務不受影響。" },
  // ---- the dialog: what each action says before anything happens
  "冻结不会删除任何内容：组织及其工作空间停止应用访问，成员在下一次请求时失去访问；解冻后按原有成员关系恢复。": {
    en: "Freezing deletes nothing: the organization and its workspaces lose application access, members lose access on their next request, and unfreezing restores them under their existing memberships.",
    "zh-Hant": "凍結不會刪除任何內容：組織及其工作空間停止應用存取，成員在下一次請求時失去存取；解凍後按原有成員關係恢復。" },
  "删除不会立即清除任何内容：组织进入七天恢复期，期内可以恢复到删除前的状态；成员在下一次请求时失去访问。": {
    en: "Deletion removes nothing immediately: the organization enters a seven-day recovery window during which it can be restored to its previous status; members lose access on their next request.",
    "zh-Hant": "刪除不會立即清除任何內容：組織進入七天恢復期，期內可以恢復到刪除前的狀態；成員在下一次請求時失去存取。" },
  "解冻不撤销任何东西：组织按原有成员关系恢复可用；之前撤销的会话、令牌和邀请不会恢复。": {
    en: "Unfreezing revokes nothing: the organization becomes available again under its existing memberships; sessions, tokens and invitations revoked earlier do not come back.",
    "zh-Hant": "解凍不撤銷任何東西：組織按原有成員關係恢復可用；之前撤銷的工作階段、權杖和邀請不會恢復。" },
  "恢复不撤销任何东西：组织回到删除前的状态；之前撤销的会话、令牌和邀请不会恢复。": {
    en: "Recovery revokes nothing: the organization returns to its status before deletion; sessions, tokens and invitations revoked earlier do not come back.",
    "zh-Hant": "恢復不撤銷任何東西：組織回到刪除前的狀態；之前撤銷的工作階段、權杖和邀請不會恢復。" },
  // ---- refusals: the dialog's own wording when the server sends only a code (governance/labels.ts)
  "组织的状态、成员或工作空间已变化，请重新预览影响。": { en: "The organization's status, members or workspaces changed. Preview the impact again.", "zh-Hant": "組織的狀態、成員或工作空間已變更，請重新預覽影響。" },
  "不支持的组织操作，请刷新页面后重试。": { en: "Unsupported organization action. Refresh the page and try again.", "zh-Hant": "不支援的組織操作，請重新整理頁面後重試。" },
  // ---- refusals: the server's own sentences (admin_organizations.py, Organizations.platform_transition)
  "请选择有效的组织生命周期操作": { en: "Choose a valid organization lifecycle action.", "zh-Hant": "請選擇有效的組織生命週期操作" },
  "组织或其成员已变化，请重新预览": { en: "The organization or its members changed. Preview again.", "zh-Hant": "組織或其成員已變更，請重新預覽" },
  "操作目标已变化，请重新预览": { en: "The operation's target changed. Preview again.", "zh-Hant": "操作目標已變更，請重新預覽" },
  // a recovery that ends frozen (the organization was frozen when it was deleted)
  "组织恢复后仍为冻结状态：成员在解冻前仍无法访问；成员关系保留。": {
    en: "The organization comes back frozen: members stay without access until it is unfrozen. Memberships are kept.",
    "zh-Hant": "組織恢復後仍為凍結狀態：成員在解凍前仍無法存取；成員關係保留。" },
  "所属工作空间在解冻前仍停止应用访问；此操作不改变其内容。": {
    en: "Its workspaces stay closed to applications until it is unfrozen. Their content is not changed.",
    "zh-Hant": "所屬工作空間在解凍前仍停止應用存取；此操作不改變其內容。" },
  "恢复后组织仍为冻结状态，需要另行解冻才能恢复访问。": {
    en: "After recovery the organization is still frozen. Unfreeze it separately to restore access.",
    "zh-Hant": "恢復後組織仍為凍結狀態，需要另行解凍才能恢復存取。" },
};
registerMessages(organizationLifecycleMessages);
