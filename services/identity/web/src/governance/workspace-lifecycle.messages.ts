/** A44: the platform workspace lifecycle — the workspace list's controls and status filter
 *  (Admin.tsx WorkspacesTab), the preview dialog and its result (governance/WorkspaceLifecycleDialog.tsx:
 *  target, counts, notes, the new name, what was withdrawn, the restore note) and the refusals of
 *  `/api/admin/workspaces/{id}/lifecycle/preview` and `/{action}` (governance/labels.ts). Every
 *  sentence is here in the three languages; the labels the page already had (成员, 会话, 恢复期限：{time},
 *  重新预览, …) stay in their own catalogues. Registered through labels.ts. */
import { registerMessages } from "@synwevo/ui";
export const workspaceLifecycleMessages = {
  // ---- the workspace list
  "持有工作空间管理权限的平台管理员可以重命名、冻结、删除或恢复工作空间：重命名、删除和恢复都要先预览影响，再写明理由并重新验证身份。删除后的工作空间对所有人不可访问，七天内可以恢复；期满后数据仍然保存，只是不能再从控制台恢复。": {
    en: "Platform administrators holding the workspace management permission can rename, freeze, delete or recover workspaces. Renaming, deleting and recovering each start with an impact preview, then a written reason and re-authentication. A deleted workspace is closed to everyone and can be recovered within seven days; after that its data stays stored, but it can no longer be recovered from the console.",
    "zh-Hant": "持有工作空間管理權限的平台管理員可以重新命名、凍結、刪除或恢復工作空間：重新命名、刪除和恢復都要先預覽影響，再寫明理由並重新驗證身份。刪除後的工作空間對所有人不可存取，七天內可以恢復；期滿後資料仍然保存，只是不能再從控制台恢復。" },
  "你的平台角色可以查看工作空间，但不能冻结、解冻、重命名、删除或恢复。": {
    en: "Your platform role can view workspaces but cannot freeze, unfreeze, rename, delete or recover them.",
    "zh-Hant": "你的平台角色可以檢視工作空間，但不能凍結、解凍、重新命名、刪除或恢復。" },
  "工作空间状态": { en: "Workspace status", "zh-Hant": "工作空間狀態" },
  "全部（不含已删除）": { en: "All (except deleted)", "zh-Hant": "全部（不含已刪除）" },
  "已删除的工作空间不出现在工作空间切换器和其他列表中，任何人都不能打开；在恢复期限之前可以恢复，期限过后数据仍然保存，但不能再从控制台恢复。": {
    en: "Deleted workspaces appear in no workspace switcher or other list, and nobody can open them. Each can be recovered until its recovery deadline; after the deadline its data stays stored, but it can no longer be recovered from the console.",
    "zh-Hant": "已刪除的工作空間不出現在工作空間切換器和其他清單中，任何人都不能開啟；在恢復期限之前可以恢復，期限過後資料仍然保存，但不能再從控制台恢復。" },
  "所属组织：{name}": { en: "Organization: {name}", "zh-Hant": "所屬組織：{name}" },
  "{name} 的工作空间操作": { en: "Workspace actions for {name}", "zh-Hant": "{name} 的工作空間操作" },
  "没有已删除的工作空间。": { en: "No deleted workspaces.", "zh-Hant": "沒有已刪除的工作空間。" },
  "恢复期限已过": { en: "Recovery deadline passed", "zh-Hant": "恢復期限已過" },
  "恢复期限已过：数据仍然保存，但不能再从控制台恢复。": {
    en: "Recovery deadline passed: the data stays stored, but the workspace can no longer be recovered from the console.",
    "zh-Hant": "恢復期限已過：資料仍然保存，但不能再從控制台恢復。" },
  // the permission, as the platform-access dialog lists it (governance/PlatformAccess.tsx)
  "冻结、解冻、重命名、删除与恢复工作空间": { en: "Freeze, unfreeze, rename, delete and recover workspaces", "zh-Hant": "凍結、解凍、重新命名、刪除與恢復工作空間" },
  // ---- the actions and what each says before anything is counted
  "重命名工作空间": { en: "Rename workspace", "zh-Hant": "重新命名工作空間" },
  "删除工作空间": { en: "Delete workspace", "zh-Hant": "刪除工作空間" },
  "恢复工作空间": { en: "Recover workspace", "zh-Hant": "恢復工作空間" },
  "重命名只改变名称：成员、内容和访问都不变。": {
    en: "Renaming changes only the name: members, content and access stay as they are.",
    "zh-Hant": "重新命名只改變名稱：成員、內容和存取都不變。" },
  "删除不会清除任何数据，之后也不会自动清除：工作空间进入七天恢复期，期内可以恢复到删除前的状态；期满后它保持已删除状态，数据仍然保存，但不能再从控制台恢复。删除后任何人都不能打开它（包括平台管理员），它也不再出现在工作空间切换器和列表中。有些撤销是永久的，恢复也不会带回来，请看下方的影响。": {
    en: "Deleting clears no data, now or later: the workspace enters a seven-day recovery window during which it can be restored to its status before deletion. After the window it stays deleted with its data stored, but it can no longer be recovered from the console. Once deleted, nobody can open it (platform administrators included), and it no longer appears in workspace switchers or lists. Some revocations are permanent and recovery does not bring them back; see the impact below.",
    "zh-Hant": "刪除不會清除任何資料，之後也不會自動清除：工作空間進入七天恢復期，期內可以恢復到刪除前的狀態；期滿後它保持已刪除狀態，資料仍然保存，但不能再從控制台恢復。刪除後任何人都不能開啟它（包括平台管理員），它也不再出現在工作空間切換器和清單中。有些撤銷是永久的，恢復也不會帶回來，請看下方的影響。" },
  "恢复让工作空间回到删除前的状态，成员关系保留；成员能否重新访问取决于恢复后的状态、所属组织和各自的账户。删除时撤销或停止的东西并不都会回来，请看下方的影响。": {
    en: "Recovery returns the workspace to its status before deletion and keeps its memberships; whether members regain access depends on that status, its organization and each member's own account. Not everything revoked or stopped by the deletion comes back; see the impact below.",
    "zh-Hant": "恢復讓工作空間回到刪除前的狀態，成員關係保留；成員能否重新存取取決於恢復後的狀態、所屬組織和各自的帳戶。刪除時撤銷或停止的東西並不都會回來，請看下方的影響。" },
  // ---- the dialog: the new name
  "新名称": { en: "New name", "zh-Hant": "新名稱" },
  "预览影响": { en: "Preview impact", "zh-Hant": "預覽影響" },
  "1 至 100 个字符，不能包含控制字符；首尾空格会被去掉。": {
    en: "1 to 100 characters, no control characters; leading and trailing spaces are removed.",
    "zh-Hant": "1 至 100 個字元，不能包含控制字元；首尾空格會被去掉。" },
  "填写新名称后预览影响；名称改动后需要重新预览。": {
    en: "Enter the new name, then preview the impact. Changing the name afterwards needs a new preview.",
    "zh-Hant": "填寫新名稱後預覽影響；名稱改動後需要重新預覽。" },
  "请填写新名称。": { en: "Enter a new name.", "zh-Hant": "請填寫新名稱。" },
  "名称最多 {max} 个字符（当前 {count} 个）。": {
    en: "The name can have at most {max} characters (now {count}).",
    "zh-Hant": "名稱最多 {max} 個字元（目前 {count} 個）。" },
  "名称不能包含换行、制表符等控制字符。": { en: "The name cannot contain control characters such as line breaks or tabs.", "zh-Hant": "名稱不能包含換行、定位字元等控制字元。" },
  "新名称与当前名称相同，无需重命名。": { en: "The new name is the same as the current one; there is nothing to rename.", "zh-Hant": "新名稱與目前名稱相同，無需重新命名。" },
  // ---- the dialog: states and controls
  "正在核对工作空间与操作影响…": { en: "Checking the workspace and impact…", "zh-Hant": "正在核對工作空間與操作影響…" },
  "预览有效至 {time}。工作空间变化后必须重新预览。": { en: "Preview valid until {time}. Preview again if the workspace changes.", "zh-Hant": "預覽有效至 {time}。工作空間變更後必須重新預覽。" },
  "我已核对目标工作空间和上述影响。": { en: "I have checked the target workspace and these effects.", "zh-Hant": "我已核對目標工作空間和上述影響。" },
  // ---- the dialog: the target
  "高风险": { en: "High risk", "zh-Hant": "高風險" },
  "中等风险": { en: "Medium risk", "zh-Hant": "中等風險" },
  "类型：{kind}": { en: "Type: {kind}", "zh-Hant": "類型：{kind}" },
  "名称：{before} → {after}": { en: "Name: {before} → {after}", "zh-Hant": "名稱：{before} → {after}" },
  "恢复后工作空间仍为冻结状态，需要另行解冻才能恢复访问。": {
    en: "After recovery the workspace is still frozen. Unfreeze it separately to restore access.",
    "zh-Hant": "恢復後工作空間仍為凍結狀態，需要另行解凍才能恢復存取。" },
  // ---- the dialog: the counts and what each one means
  "可用的拥有者": { en: "Usable owners", "zh-Hant": "可用的擁有者" },
  "不经批准签发的 API 访问密钥": { en: "API access keys not issued through an approval", "zh-Hant": "不經批准簽發的 API 存取金鑰" },
  "不经批准签发的服务账户": { en: "Service accounts not issued through an approval", "zh-Hant": "不經批准簽發的服務帳戶" },
  "Webhook 订阅": { en: "Webhook subscriptions", "zh-Hant": "Webhook 訂閱" },
  "表单公开链接": { en: "Public form links", "zh-Hant": "表單公開連結" },
  "发布到组织的模板": { en: "Templates published to the organization", "zh-Hant": "發佈到組織的範本" },
  "此部署没有这些数据，不统计：{names}": { en: "This deployment has no such data, so these are not counted: {names}", "zh-Hant": "此部署沒有這些資料，不統計：{names}" },
  "这些成员会在工作空间切换器、成员列表和邀请邮件中看到新名称。": {
    en: "These members see the new name in the workspace switcher, the member list and invitation mail.",
    "zh-Hant": "這些成員會在工作空間切換器、成員清單和邀請郵件中看到新名稱。" },
  // a deletion
  "成员关系保留；成员从下一次请求起不能再打开这个工作空间。": {
    en: "Memberships are kept; from their next request members can no longer open this workspace.",
    "zh-Hant": "成員關係保留；成員從下一次請求起不能再開啟這個工作空間。" },
  "拥有者的角色保留；恢复后由他们继续管理。": { en: "Owners keep their role and manage the workspace again after a recovery.", "zh-Hant": "擁有者的角色保留；恢復後由他們繼續管理。" },
  "当前没有可用的拥有者；恢复后仍需要为它指定拥有者。": {
    en: "There is no usable owner now; after a recovery an owner still has to be assigned.",
    "zh-Hant": "目前沒有可用的擁有者；恢復後仍需要為它指定擁有者。" },
  "这些会话会移出这个工作空间，不会退出登录；恢复后成员需要重新切换进来。": {
    en: "These sessions are moved out of this workspace without signing anyone out; after a recovery members have to switch back in.",
    "zh-Hant": "這些工作階段會移出這個工作空間，不會登出；恢復後成員需要重新切換進來。" },
  "这些应用令牌立即撤销；恢复后不会恢复，应用需要重新登录。": {
    en: "These application tokens are revoked at once and do not come back with a recovery; the applications have to sign in again.",
    "zh-Hant": "這些應用程式權杖立即撤銷；恢復後不會恢復，應用程式需要重新登入。" },
  "这些邀请立即撤回；恢复后不会恢复，需要重新邀请。": {
    en: "These invitations are withdrawn at once and do not come back with a recovery; people have to be invited again.",
    "zh-Hant": "這些邀請立即撤回；恢復後不會恢復，需要重新邀請。" },
  "这些批准被永久撤销：通过它们签发的 API 访问密钥和服务账户从此停止工作，恢复后也不会重新生效。": {
    en: "These approvals are revoked for good: the API access keys and service accounts issued through them stop working, and a recovery does not bring them back.",
    "zh-Hant": "這些批准被永久撤銷：透過它們簽發的 API 存取金鑰和服務帳戶從此停止運作，恢復後也不會重新生效。" },
  "这些密钥暂停使用，不会删除；恢复后重新可用。经 API 凭据批准签发的密钥不在此列，它们随批准永久停止。": {
    en: "These keys are paused, not deleted; they work again after a recovery. Keys issued through an API credential approval are not among them: they stop for good with the approval.",
    "zh-Hant": "這些金鑰暫停使用，不會刪除；恢復後重新可用。經 API 憑證批准簽發的金鑰不在此列，它們隨批准永久停止。" },
  "这些服务账户暂停使用，不会删除；恢复后重新可用。经 API 凭据批准签发的服务账户不在此列，它们随批准永久停止。": {
    en: "These service accounts are paused, not deleted; they work again after a recovery. Service accounts issued through an API credential approval are not among them: they stop for good with the approval.",
    "zh-Hant": "這些服務帳戶暫停使用，不會刪除；恢復後重新可用。經 API 憑證批准簽發的服務帳戶不在此列，它們隨批准永久停止。" },
  "这些任务会在下一次检查时永久停止；已生成的结果保留，恢复后需要有人重新运行。": {
    en: "These jobs stop for good at their next check; results already generated are kept, and after a recovery someone has to run them again.",
    "zh-Hant": "這些任務會在下一次檢查時永久停止；已產生的結果保留，恢復後需要有人重新執行。" },
  "Docs 的订阅在删除期间遇到下一次投递时被停用，业务应用的订阅随应用授权撤销而停用；已停用的订阅恢复后仍保持停用，需要有人重新启用。": {
    en: "Docs subscriptions are switched off at their next delivery during the deletion, and business application subscriptions are switched off as their application's authorization is revoked. Switched-off subscriptions stay off after a recovery until someone switches them on again.",
    "zh-Hant": "Docs 的訂閱在刪除期間遇到下一次投遞時被停用，業務應用的訂閱隨應用授權撤銷而停用；已停用的訂閱恢復後仍保持停用，需要有人重新啟用。" },
  "这些链接暂时打不开；恢复后未过期的链接重新可用。": { en: "These links stop opening for now; unexpired links work again after a recovery.", "zh-Hant": "這些連結暫時打不開；恢復後未過期的連結重新可用。" },
  "项目原样保留，不会删除，也不会提供给管理员。": { en: "Projects stay as they are: nothing is deleted and nothing is shown to administrators.", "zh-Hant": "專案原樣保留，不會刪除，也不會提供給管理員。" },
  "这些模板属于组织，保留不变。": { en: "These templates belong to the organization and stay unchanged.", "zh-Hant": "這些範本屬於組織，保留不變。" },
  // a recovery
  "成员关系保留；账户和组织成员身份仍然有效的成员恢复后可以重新访问。": {
    en: "Memberships are kept; members whose account and organization membership are still valid regain access after the recovery.",
    "zh-Hant": "成員關係保留；帳戶和組織成員身分仍然有效的成員恢復後可以重新存取。" },
  "恢复后由这些拥有者继续管理。": { en: "These owners manage the workspace again after the recovery.", "zh-Hant": "恢復後由這些擁有者繼續管理。" },
  "恢复后没有可用的拥有者：请在本页「没有有效拥有者的工作空间」中为它指定拥有者。": {
    en: "After the recovery there is no usable owner: assign one in “Workspaces without a valid owner” on this page.",
    "zh-Hant": "恢復後沒有可用的擁有者：請在本頁「沒有有效擁有者的工作空間」中為它指定擁有者。" },
  "恢复后仍为冻结状态：成员在解冻前仍无法访问；成员关系保留。": {
    en: "It comes back frozen: members stay without access until it is unfrozen. Memberships are kept.",
    "zh-Hant": "恢復後仍為凍結狀態：成員在解凍前仍無法存取；成員關係保留。" },
  "所属组织当前不可用：恢复后成员仍无法访问，直到组织恢复正常；成员关系保留。": {
    en: "Its organization is not available: after the recovery members still have no access until the organization is back to normal. Memberships are kept.",
    "zh-Hant": "所屬組織目前不可用：恢復後成員仍無法存取，直到組織恢復正常；成員關係保留。" },
  "成员关系保留；恢复后没有可用的拥有者：成员能否访问取决于各自的账户和组织成员身份，也没有人能管理成员和设置。": {
    en: "Memberships are kept; after the recovery there is no usable owner: whether members have access depends on their own account and organization membership, and nobody can manage members or settings.",
    "zh-Hant": "成員關係保留；恢復後沒有可用的擁有者：成員能否存取取決於各自的帳戶和組織成員身分，也沒有人能管理成員和設定。" },
  "现有会话不受影响；删除时移出的会话不会自动回来，成员需要重新切换进来。": {
    en: "Existing sessions are untouched; sessions moved out by the deletion do not come back by themselves, and members have to switch back in.",
    "zh-Hant": "現有工作階段不受影響；刪除時移出的工作階段不會自動回來，成員需要重新切換進來。" },
  "现有批准不受影响；删除时撤销的批准不会恢复，通过它们签发的 API 访问密钥和服务账户仍然无效，需要重新申请批准并签发。": {
    en: "Existing approvals are untouched; approvals revoked by the deletion do not come back, and the API access keys and service accounts issued through them stay invalid until approval is requested again and new ones are issued.",
    "zh-Hant": "現有批准不受影響；刪除時撤銷的批准不會恢復，透過它們簽發的 API 存取金鑰和服務帳戶仍然無效，需要重新申請批准並簽發。" },
  "这些密钥随恢复重新可用；经 API 凭据批准签发的密钥不在此列，仍然无效。": {
    en: "These keys work again with the recovery; keys issued through an API credential approval are not among them and stay invalid.",
    "zh-Hant": "這些金鑰隨恢復重新可用；經 API 憑證批准簽發的金鑰不在此列，仍然無效。" },
  "这些服务账户随恢复重新可用；经 API 凭据批准签发的服务账户不在此列，仍然无效。": {
    en: "These service accounts work again with the recovery; service accounts issued through an API credential approval are not among them and stay invalid.",
    "zh-Hant": "這些服務帳戶隨恢復重新可用；經 API 憑證批准簽發的服務帳戶不在此列，仍然無效。" },
  "这些任务尚未停止：恢复后仍按提交者当前的授权核对，授权已在删除时撤销的任务会停止。删除期间已停止的任务保持停止，需要有人重新运行。": {
    en: "These jobs have not stopped yet: after the recovery each is still checked against its submitter's current authorization, and one whose authorization the deletion revoked stops. Jobs stopped during the deletion stay stopped until someone runs them again.",
    "zh-Hant": "這些任務尚未停止：恢復後仍按提交者目前的授權核對，授權已在刪除時撤銷的任務會停止。刪除期間已停止的任務保持停止，需要有人重新執行。" },
  "这些订阅尚未被停用：恢复后每次投递仍按创建者当前的授权核对，授权已在删除时撤销的会被停用。删除期间已被停用的订阅保持停用，需要有人重新启用。": {
    en: "These subscriptions have not been switched off yet: after the recovery each delivery is still checked against its creator's current authorization, and one whose authorization the deletion revoked is switched off. Subscriptions switched off during the deletion stay off until someone switches them on again.",
    "zh-Hant": "這些訂閱尚未被停用：恢復後每次投遞仍按建立者目前的授權核對，授權已在刪除時撤銷的會被停用。刪除期間已被停用的訂閱保持停用，需要有人重新啟用。" },
  "这些链接随恢复重新可用（已过期的除外）。": { en: "These links work again with the recovery (except expired ones).", "zh-Hant": "這些連結隨恢復重新可用（已過期的除外）。" },
  "项目原样保留。": { en: "Projects stay as they are.", "zh-Hant": "專案原樣保留。" },
  "这些模板属于组织，不受影响。": { en: "These templates belong to the organization and are not affected.", "zh-Hant": "這些範本屬於組織，不受影響。" },
  // ---- the dialog: the server's notes, by code and action
  "这项操作的说明": { en: "About this action", "zh-Hant": "這項操作的說明" },
  "不会删除任何数据，七天后也不会自动清除：项目、模板和结果原样保留，只是在删除期间打不开；平台管理员也看不到其内容。": {
    en: "No data is deleted, and nothing is cleared automatically after seven days either: projects, templates and results stay as they are and only cannot be opened while the workspace is deleted. Platform administrators cannot see their content either.",
    "zh-Hant": "不會刪除任何資料，七天後也不會自動清除：專案、範本和結果原樣保留，只是在刪除期間打不開；平台管理員也看不到其內容。" },
  "删除期间数据原样保留；成员恢复访问后即可照常使用。": {
    en: "The data stayed as it was during the deletion; members can use it as usual once they have access again.",
    "zh-Hant": "刪除期間資料原樣保留；成員恢復存取後即可照常使用。" },
  "重命名不改变任何数据、成员或访问。": { en: "Renaming changes no data, members or access.", "zh-Hant": "重新命名不改變任何資料、成員或存取。" },
  "成员当前在这个工作空间的登录会话会移出它，不会退出登录；恢复后成员需要重新切换进来。": {
    en: "Members' sessions currently in this workspace are moved out of it without signing anyone out; after a recovery members have to switch back in.",
    "zh-Hant": "成員目前在這個工作空間的登入工作階段會移出它，不會登出；恢復後成員需要重新切換進來。" },
  "发给这个工作空间的应用令牌和授权码立即作废；恢复后不会恢复，连接的应用需要重新登录或重新授权。": {
    en: "Application tokens and authorization codes issued for this workspace are voided at once and do not come back with a recovery; connected applications have to sign in or be authorized again.",
    "zh-Hant": "發給這個工作空間的應用程式權杖和授權碼立即作廢；恢復後不會恢復，連接的應用程式需要重新登入或重新授權。" },
  "尚未接受的邀请立即撤回；恢复后不会恢复，需要重新邀请。": {
    en: "Pending invitations are withdrawn at once and do not come back with a recovery; people have to be invited again.",
    "zh-Hant": "尚未接受的邀請立即撤回；恢復後不會恢復，需要重新邀請。" },
  "API 凭据批准（待处理的和已批准的）被永久撤销：通过它们签发的 API 访问密钥和服务账户从此停止工作，恢复后也不会重新生效，需要重新申请批准并签发新的凭据。": {
    en: "API credential approvals, pending and approved, are revoked for good: the API access keys and service accounts issued through them stop working and a recovery does not bring them back. Approval has to be requested again and new credentials issued.",
    "zh-Hant": "API 憑證批准（待處理的和已批准的）被永久撤銷：透過它們簽發的 API 存取金鑰和服務帳戶從此停止運作，恢復後也不會重新生效，需要重新申請批准並簽發新的憑證。" },
  "删除时撤销的 API 凭据批准不会恢复：通过它们签发的 API 访问密钥和服务账户仍然无效，需要重新申请批准并签发新的凭据。": {
    en: "API credential approvals revoked by the deletion do not come back: the API access keys and service accounts issued through them stay invalid, so approval has to be requested again and new credentials issued.",
    "zh-Hant": "刪除時撤銷的 API 憑證批准不會恢復：透過它們簽發的 API 存取金鑰和服務帳戶仍然無效，需要重新申請批准並簽發新的憑證。" },
  "排队或运行中的 Docs 任务会在下一次检查时永久停止；已生成的结果保留，恢复后不会自动继续，需要有人重新运行。": {
    en: "Queued or running Docs jobs stop for good at their next check; results already generated are kept, they do not resume after a recovery, and someone has to run them again.",
    "zh-Hant": "排隊或執行中的 Docs 任務會在下一次檢查時永久停止；已產生的結果保留，恢復後不會自動繼續，需要有人重新執行。" },
  "删除期间停止的 Docs 任务保持停止，提交者授权已随删除结束的排队或运行中任务也会在下一次检查时停止；它们不会随恢复继续，已生成的结果保留，需要有人重新运行。": {
    en: "Docs jobs stopped during the deletion stay stopped, and queued or running ones whose submitter's authorization ended with the deletion stop at their next check; none of them resumes with the recovery. Results already generated are kept, and someone has to run them again.",
    "zh-Hant": "刪除期間停止的 Docs 任務保持停止，提交者授權已隨刪除結束的排隊或執行中任務也會在下一次檢查時停止；它們不會隨恢復繼續，已產生的結果保留，需要有人重新執行。" },
  "Docs 的 Webhook 订阅会在下一次投递时被停用，业务应用的 Webhook 订阅随应用授权撤销而停用；恢复不会重新启用它们，需要有人逐个重新启用。": {
    en: "Docs webhook subscriptions are switched off at their next delivery, and business application webhook subscriptions as their application's authorization is revoked; a recovery does not switch them back on, and someone has to switch each one on again.",
    "zh-Hant": "Docs 的 Webhook 訂閱會在下一次投遞時被停用，業務應用的 Webhook 訂閱隨應用授權撤銷而停用；恢復不會重新啟用它們，需要有人逐一重新啟用。" },
  "删除期间被停用的 Webhook 订阅（Docs 和业务应用的）保持停用，创建者授权已随删除结束的订阅也会在下一次投递时被停用；恢复不会重新启用它们，需要有人逐个重新启用。": {
    en: "Webhook subscriptions switched off during the deletion, Docs and business application ones alike, stay off, and those whose creator's authorization ended with the deletion are switched off at their next delivery; the recovery does not switch them back on, and someone has to switch each one on again.",
    "zh-Hant": "刪除期間被停用的 Webhook 訂閱（Docs 和業務應用的）保持停用，建立者授權已隨刪除結束的訂閱也會在下一次投遞時被停用；恢復不會重新啟用它們，需要有人逐一重新啟用。" },
  "不经 API 凭据批准签发的 API 访问密钥和服务账户暂停使用，不会删除；恢复后它们重新可用，无需重新签发。经批准签发的密钥和服务账户不在此列，随批准永久停止。": {
    en: "API access keys and service accounts not issued through an API credential approval are paused, not deleted; they work again after a recovery without being reissued. Keys and service accounts issued through an approval are not among them: they stop for good with the approval.",
    "zh-Hant": "不經 API 憑證批准簽發的 API 存取金鑰和服務帳戶暫停使用，不會刪除；恢復後它們重新可用，無需重新簽發。經批准簽發的金鑰和服務帳戶不在此列，隨批准永久停止。" },
  "暂停的、不经 API 凭据批准签发的 API 访问密钥和服务账户随恢复重新可用，无需重新签发；经批准签发的仍然无效。": {
    en: "The paused API access keys and service accounts not issued through an API credential approval work again with the recovery, without being reissued; those issued through an approval stay invalid.",
    "zh-Hant": "暫停的、不經 API 憑證批准簽發的 API 存取金鑰和服務帳戶隨恢復重新可用，無需重新簽發；經批准簽發的仍然無效。" },
  "表单的公开填写链接暂时失效，填写者会看到链接已失效；恢复后未过期的链接重新可用。": {
    en: "Public form links stop working for now and respondents see that the link is no longer valid; unexpired links work again after a recovery.",
    "zh-Hant": "表單的公開填寫連結暫時失效，填寫者會看到連結已失效；恢復後未過期的連結重新可用。" },
  "表单的公开填写链接随恢复重新可用（已过期的除外）。": { en: "Public form links work again with the recovery (except expired ones).", "zh-Hant": "表單的公開填寫連結隨恢復重新可用（已過期的除外）。" },
  "这个工作空间发布到组织的模板属于组织，保留不变，组织成员仍可使用。": {
    en: "Templates this workspace published to the organization belong to the organization: they stay unchanged and its members can still use them.",
    "zh-Hant": "這個工作空間發佈到組織的範本屬於組織，保留不變，組織成員仍可使用。" },
  "这个工作空间发布到组织的模板一直保留在组织中，不受恢复影响。": {
    en: "Templates this workspace published to the organization stayed in the organization throughout and are not affected by the recovery.",
    "zh-Hant": "這個工作空間發佈到組織的範本一直保留在組織中，不受恢復影響。" },
  "删除后有七天恢复期：期内平台管理员可以把它恢复到删除前的状态；期满后它保持已删除状态，数据仍然保存，但不能再从控制台恢复。": {
    en: "Deletion opens a seven-day recovery window: within it a platform administrator can restore the workspace to its status before deletion. After it the workspace stays deleted with its data stored, but it can no longer be recovered from the console.",
    "zh-Hant": "刪除後有七天恢復期：期內平台管理員可以把它恢復到刪除前的狀態；期滿後它保持已刪除狀態，資料仍然保存，但不能再從控制台恢復。" },
  "恢复只能在删除后七天内进行；期满后数据仍然保存，但不能再从控制台恢复。": {
    en: "Recovery is possible only within seven days of the deletion; after that the data stays stored, but the workspace can no longer be recovered from the console.",
    "zh-Hant": "恢復只能在刪除後七天內進行；期滿後資料仍然保存，但不能再從控制台恢復。" },
  "删除会写入安全日志，之后的恢复不会：恢复之后如果从更早的备份还原，它会再次处于已删除状态。": {
    en: "The deletion is written to the security journal but a later recovery is not: if an older backup is restored after a recovery, the workspace is deleted again.",
    "zh-Hant": "刪除會寫入安全日誌，之後的恢復不會：恢復之後如果從更早的備份還原，它會再次處於已刪除狀態。" },
  "恢复不会写入安全日志：如果之后从早于此刻的备份还原，这个工作空间会按原来的删除时间再次处于已删除状态，届时可能已超出恢复期限。": {
    en: "The recovery is not written to the security journal: if a backup older than now is restored later, this workspace is deleted again with its original deletion time, possibly past its recovery deadline by then.",
    "zh-Hant": "恢復不會寫入安全日誌：如果之後從早於此刻的備份還原，這個工作空間會按原來的刪除時間再次處於已刪除狀態，屆時可能已超出恢復期限。" },
  "所属组织当前已冻结或已删除：恢复后这个工作空间仍然无法访问，直到组织恢复正常。": {
    en: "Its organization is frozen or deleted: after the recovery this workspace still cannot be accessed until the organization is back to normal.",
    "zh-Hant": "所屬組織目前已凍結或已刪除：恢復後這個工作空間仍然無法存取，直到組織恢復正常。" },
  "恢复后没有可用的拥有者：没有人能管理成员和设置。请在本页「没有有效拥有者的工作空间」中为它指定拥有者。": {
    en: "After the recovery there is no usable owner: nobody can manage members or settings. Assign an owner in “Workspaces without a valid owner” on this page.",
    "zh-Hant": "恢復後沒有可用的擁有者：沒有人能管理成員和設定。請在本頁「沒有有效擁有者的工作空間」中為它指定擁有者。" },
  "新名称立即对所有成员可见：工作空间切换器、成员列表、邀请邮件和各个应用都会显示新名称。": {
    en: "The new name is visible to every member at once: the workspace switcher, the member list, invitation mail and every application show it.",
    "zh-Hant": "新名稱立即對所有成員可見：工作空間切換器、成員清單、邀請郵件和各個應用程式都會顯示新名稱。" },
  "重命名不会写入安全日志：如果之后从早于此刻的备份还原，会显示原来的名称，需要再次重命名。": {
    en: "The rename is not written to the security journal: if a backup older than now is restored later, the old name is shown again and the workspace has to be renamed again.",
    "zh-Hant": "重新命名不會寫入安全日誌：如果之後從早於此刻的備份還原，會顯示原來的名稱，需要再次重新命名。" },
  // ---- the result
  "工作空间已重命名": { en: "Workspace renamed", "zh-Hant": "工作空間已重新命名" },
  "工作空间已删除": { en: "Workspace deleted", "zh-Hant": "工作空間已刪除" },
  "工作空间已恢复": { en: "Workspace recovered", "zh-Hant": "工作空間已恢復" },
  "工作空间已重命名为「{name}」。": { en: "The workspace is now named “{name}”.", "zh-Hant": "工作空間已重新命名為「{name}」。" },
  "「{name}」已删除，可在 {time} 前恢复。": { en: "“{name}” was deleted. It can be recovered until {time}.", "zh-Hant": "「{name}」已刪除，可在 {time} 前恢復。" },
  "「{name}」已删除。": { en: "“{name}” was deleted.", "zh-Hant": "「{name}」已刪除。" },
  "「{name}」已恢复，当前状态：{status}。": { en: "“{name}” was recovered. Status now: {status}.", "zh-Hant": "「{name}」已恢復，目前狀態：{status}。" },
  "「{name}」已恢复，当前状态：{status}。所属组织当前已冻结或已删除：在组织恢复正常之前，成员仍无法访问这个工作空间。": {
    en: "“{name}” was recovered. Status now: {status}. Its organization is frozen or deleted: members still cannot access this workspace until the organization is back to normal.",
    "zh-Hant": "「{name}」已恢復，目前狀態：{status}。所屬組織目前已凍結或已刪除：在組織恢復正常之前，成員仍無法存取這個工作空間。" },
  "已撤回的访问": { en: "Access withdrawn", "zh-Hant": "已撤回的存取" },
  "移出这个工作空间的会话": { en: "Sessions moved out of this workspace", "zh-Hant": "移出這個工作空間的工作階段" },
  "撤销的应用令牌": { en: "Application tokens revoked", "zh-Hant": "撤銷的應用程式權杖" },
  "作废的授权码": { en: "Authorization codes voided", "zh-Hant": "作廢的授權碼" },
  "撤回的邀请": { en: "Invitations withdrawn", "zh-Hant": "撤回的邀請" },
  "永久撤销的 API 凭据批准": { en: "API credential approvals revoked for good", "zh-Hant": "永久撤銷的 API 憑證批准" },
  "这些在删除时撤回的访问不会随恢复回来。": { en: "Access withdrawn by the deletion does not come back with a recovery.", "zh-Hant": "這些在刪除時撤回的存取不會隨恢復回來。" },
  // ---- refusals: the dialog's own wording when the server sends only a code (WorkspaceLifecycleDialog.tsx)
  "不支持的工作空间操作，请刷新页面后重试。": { en: "Unsupported workspace action. Refresh the page and try again.", "zh-Hant": "不支援的工作空間操作，請重新整理頁面後重試。" },
  "新名称无效：须为 1 至 100 个字符，且不能包含换行、制表符等控制字符。": {
    en: "The new name is not valid: it must have 1 to 100 characters and no control characters such as line breaks or tabs.",
    "zh-Hant": "新名稱無效：須為 1 至 100 個字元，且不能包含換行、定位字元等控制字元。" },
  "找不到这个工作空间，请刷新列表。": { en: "This workspace cannot be found. Refresh the list.", "zh-Hant": "找不到這個工作空間，請重新整理清單。" },
  "安全记录暂不可用，删除未完成；如果之后从备份还原，这次删除仍可能生效。请重新预览并核实工作空间状态后再试。": {
    en: "Security recording is unavailable and the deletion did not complete; if a backup is restored later, this deletion may still take effect. Preview again and check the workspace's status before retrying.",
    "zh-Hant": "安全記錄暫不可用，刪除未完成；如果之後從備份還原，這次刪除仍可能生效。請重新預覽並核實工作空間狀態後再試。" },
  "工作空间的状态、名称或成员已变化，请重新预览影响。": {
    en: "The workspace's status, name or members changed. Preview the impact again.",
    "zh-Hant": "工作空間的狀態、名稱或成員已變更，請重新預覽影響。" },
  // ---- refusals: the page-wide wording by code (governance/labels.ts)
  "工作空间当前状态不允许此操作，请刷新列表后重试。": {
    en: "The workspace's current status does not allow this action. Refresh the list and try again.",
    "zh-Hant": "工作空間目前狀態不允許此操作，請重新整理清單後重試。" },
  "这个工作空间的七天恢复期限已过，不能再恢复。": {
    en: "This workspace's seven-day recovery deadline has passed; it can no longer be recovered.",
    "zh-Hant": "這個工作空間的七天恢復期限已過，不能再恢復。" },
  "个人空间只能重命名，不能删除或恢复。": { en: "A personal workspace can only be renamed, not deleted or recovered.", "zh-Hant": "個人空間只能重新命名，不能刪除或恢復。" },
  "历史数据空间由认领流程管理，不能在这里重命名、删除或恢复。": {
    en: "The historical data workspace is managed by the claim flow and cannot be renamed, deleted or recovered here.",
    "zh-Hant": "歷史資料空間由認領流程管理，不能在這裡重新命名、刪除或恢復。" },
  // ---- refusals: the server's own sentences (admin_workspaces.py, workspace_lifecycle.py, admin_routes.py)
  "请选择有效的工作空间操作": { en: "Choose a valid workspace action.", "zh-Hant": "請選擇有效的工作空間操作" },
  "历史数据空间由认领流程管理，不能在这里重命名、删除或恢复": {
    en: "The historical data workspace is managed by the claim flow and cannot be renamed, deleted or recovered here.",
    "zh-Hant": "歷史資料空間由認領流程管理，不能在這裡重新命名、刪除或恢復" },
  "个人空间不能删除或恢复，只能重命名": { en: "A personal workspace cannot be deleted or recovered, only renamed.", "zh-Hant": "個人空間不能刪除或恢復，只能重新命名" },
  "工作空间当前状态不允许此操作": { en: "The workspace's current status does not allow this action.", "zh-Hant": "工作空間目前狀態不允許此操作" },
  "新名称与当前名称相同": { en: "The new name is the same as the current one.", "zh-Hant": "新名稱與目前名稱相同" },
  "该工作空间的七天恢复期限已过": { en: "This workspace's seven-day recovery deadline has passed.", "zh-Hant": "該工作空間的七天恢復期限已過" },
  "安全记录暂不可用，删除未完成；这次尝试通常会在本答复前作废，若未能作废，之后从备份还原时这次删除仍可能生效。请重新预览并核实工作空间状态后再试": {
    en: "Security recording is unavailable and the deletion did not complete. The attempt is normally voided before this answer; if it could not be, this deletion may still take effect when a backup is restored later. Preview again and check the workspace's status before retrying.",
    "zh-Hant": "安全記錄暫不可用，刪除未完成；這次嘗試通常會在本答覆前作廢，若未能作廢，之後從備份還原時這次刪除仍可能生效。請重新預覽並核實工作空間狀態後再試" },
  "安全记录暂不可用，冻结未完成；这次尝试通常会在本答复前作废，若未能作废，之后从备份还原时这次冻结仍可能生效。请核实工作空间状态后再试": {
    en: "Security recording is unavailable and the freeze did not complete. The attempt is normally voided before this answer; if it could not be, this freeze may still take effect when a backup is restored later. Check the workspace's status before retrying.",
    "zh-Hant": "安全記錄暫不可用，凍結未完成；這次嘗試通常會在本答覆前作廢，若未能作廢，之後從備份還原時這次凍結仍可能生效。請核實工作空間狀態後再試" },
  "工作空间或其成员已变化，请重新预览": { en: "The workspace or its members changed. Preview again.", "zh-Hant": "工作空間或其成員已變更，請重新預覽" },
  "请选择有效的工作空间状态": { en: "Choose a valid workspace status.", "zh-Hant": "請選擇有效的工作空間狀態" },
};
registerMessages(workspaceLifecycleMessages);
