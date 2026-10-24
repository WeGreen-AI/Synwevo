/** B1/B2: the application catalogue on the administration page — the tab and its per-workspace
 *  list (governance/AppsTab.tsx), the change dialog with its impact preview and notes
 *  (governance/AppChangeDialog.tsx), and the refusals of `/api/admin/apps/{id}/preview` and
 *  `/{change}` worded by code (governance/labels.ts). Every sentence is here in the three
 *  languages; labels other admin lists already have (状态, 操作, 按工作空间, 重新预览, 操作影响, …)
 *  stay in their own catalogues. */
import { registerMessages } from "@synwevo/ui";
export const appsMessages = {
  // ---- navigation and the audit filter
  "应用目录": { en: "Application catalogue", "zh-Hant": "應用目錄" },
  "应用管理": { en: "Application management", "zh-Hant": "應用管理" },
  // ---- the tab
  "刷新应用目录": { en: "Refresh application catalogue", "zh-Hant": "重新整理應用目錄" },
  "这里列出平台上登记的应用：是否在全平台启用、谁可以使用（访问策略）、在哪些工作空间开通，以及当前可以使用的人数。只显示数量和元数据，不显示任何业务内容。": {
    en: "This lists the applications registered on the platform: whether each is on platform-wide, who may use it (its access policy), in which workspaces it is enabled, and how many people can use it now. Only counts and metadata are shown, never business content.",
    "zh-Hant": "這裡列出平台上登記的應用：是否在全平台啟用、誰可以使用（存取策略）、在哪些工作空間開通，以及目前可以使用的人數。只顯示數量和中繼資料，不顯示任何業務內容。" },
  "停用应用或收紧访问策略不会删除任何数据。在这里看到应用不等于有权使用它：每次访问仍按成员关系、应用开通状态和授权判断。": {
    en: "Turning an application off or tightening its access policy deletes no data. Seeing an application here is not being allowed to use it: every access is still decided by membership, whether the application is enabled, and grants.",
    "zh-Hant": "停用應用或收緊存取策略不會刪除任何資料。在這裡看到應用不等於有權使用它：每次存取仍按成員關係、應用開通狀態和授權判斷。" },
  "你的平台角色可以查看应用目录，但不能启用、停用应用或修改访问策略。": {
    en: "Your platform role can view the application catalogue but cannot turn applications on or off or change their access policy.",
    "zh-Hant": "你的平台角色可以檢視應用目錄，但不能啟用、停用應用或修改存取策略。" },
  "平台范围的变更（全平台启用、停用和访问策略）需要最高管理员；你可以在「按工作空间」里为单个工作空间启用或停用应用。": {
    en: "Platform-scope changes (turning applications on or off platform-wide, and their access policy) need a super administrator; you can turn an application on or off for a single workspace under “Per workspace”.",
    "zh-Hant": "平台範圍的變更（全平台啟用、停用和存取策略）需要最高管理員；你可以在「按工作空間」裡為單個工作空間啟用或停用應用。" },
  "正在加载应用目录…": { en: "Loading the application catalogue…", "zh-Hant": "正在載入應用目錄…" },
  "访问策略": { en: "Access policy", "zh-Hant": "存取策略" },
  "已开通的工作空间": { en: "Workspaces enabled", "zh-Hant": "已開通的工作空間" },
  "当前可使用的人": { en: "People with access now", "zh-Hant": "目前可使用的人" },
  "明确授权": { en: "Explicit grants", "zh-Hant": "明確授權" },
  "单独撤销访问": { en: "Individually revoked", "zh-Hant": "單獨撤銷存取" },
  "排队或运行中的任务": { en: "Queued or running jobs", "zh-Hant": "排隊或執行中的任務" },
  "入口地址：{url}": { en: "Entry address: {url}", "zh-Hant": "入口位址：{url}" },
  "不统计": { en: "Not counted", "zh-Hant": "不統計" },
  "按工作空间查看：{p0}": { en: "View per workspace: {p0}", "zh-Hant": "按工作空間檢視：{p0}" },
  "{action}：{p0}": { en: "{action}: {p0}", "zh-Hant": "{action}：{p0}" },
  "还没有登记任何应用。": { en: "No applications are registered yet.", "zh-Hant": "還沒有登記任何應用。" },
  "操作编号：{id}": { en: "Operation ID: {id}", "zh-Hant": "操作編號：{id}" },
  "每个有效账户": { en: "Every live account", "zh-Hant": "每個有效帳戶" },
  "这个应用不按工作空间开通或授权：每个有效账户都可以使用，只能在全平台启用或停用。": {
    en: "This application is not enabled or granted per workspace: every live account can use it, and it can only be turned on or off platform-wide.",
    "zh-Hant": "這個應用不按工作空間開通或授權：每個有效帳戶都可以使用，只能在全平台啟用或停用。" },
  "当前部署没有发布这个应用，不能在这里启用。": { en: "This deployment does not release this application, so it cannot be turned on here.", "zh-Hant": "目前部署沒有發布這個應用，不能在這裡啟用。" },
  // ---- the per-workspace list
  "输入工作空间名称的一部分，或完整的工作空间编号。": { en: "Enter part of a workspace name, or a whole workspace ID.", "zh-Hant": "輸入工作空間名稱的一部分，或完整的工作空間編號。" },
  "「{app}」的工作空间": { en: "Workspaces for “{app}”", "zh-Hant": "「{app}」的工作空間" },
  "每个工作空间是否开通了这个应用，以及其中可以使用它的人数。只显示数量，不显示成员或内容。为一个空间停用不影响其他空间，也不删除任何数据。": {
    en: "Whether each workspace has this application enabled, and how many of its people can use it. Only counts are shown, never members or content. Turning it off for one workspace does not affect the others and deletes no data.",
    "zh-Hant": "每個工作空間是否開通了這個應用，以及其中可以使用它的人數。只顯示數量，不顯示成員或內容。為一個空間停用不影響其他空間，也不刪除任何資料。" },
  "这个应用": { en: "This application", "zh-Hant": "這個應用" },
  "成员人数": { en: "Members", "zh-Hant": "成員人數" },
  "已开通": { en: "Enabled", "zh-Hant": "已開通" },
  "未开通": { en: "Not enabled", "zh-Hant": "未開通" },
  "开通于 {time}": { en: "Since {time}", "zh-Hant": "開通於 {time}" },
  "其中可使用 {count}": { en: "{count} with access", "zh-Hant": "其中可使用 {count}" },
  "待认领的历史数据由认领流程管理，不能在这里启用或停用应用。": {
    en: "The unclaimed historical data belongs to its claim flow; applications cannot be turned on or off for it here.",
    "zh-Hant": "待認領的歷史資料由認領流程管理，不能在這裡啟用或停用應用。" },
  "工作空间不可用时不能为它启用应用。": { en: "An application cannot be turned on for a workspace that is not available.", "zh-Hant": "工作空間不可用時不能為它啟用應用。" },
  // ---- status and policy
  "全平台已停用": { en: "Off platform-wide", "zh-Hant": "全平台已停用" },
  "成员均可使用": { en: "All members", "zh-Hant": "成員均可使用" },
  "仅限明确授权": { en: "Explicit grants only", "zh-Hant": "僅限明確授權" },
  "已开通这个应用的工作空间中，每位成员都可以使用，被单独撤销访问的成员除外。": {
    en: "Every member of a workspace with this application enabled can use it, except members whose access was revoked individually.",
    "zh-Hant": "已開通這個應用的工作空間中，每位成員都可以使用，被單獨撤銷存取的成員除外。" },
  "只有被明确授予资格的成员可以使用；其他成员保留成员身份，但打不开这个应用。": {
    en: "Only members explicitly granted access can use it; other members keep their membership but cannot open this application.",
    "zh-Hant": "只有被明確授予資格的成員可以使用；其他成員保留成員身份，但打不開這個應用。" },
  // ---- the changes
  "停用应用": { en: "Turn off application", "zh-Hant": "停用應用" },
  "启用应用": { en: "Turn on application", "zh-Hant": "啟用應用" },
  "改为仅限明确授权": { en: "Limit to explicit grants", "zh-Hant": "改為僅限明確授權" },
  "改为成员均可使用": { en: "Open to all members", "zh-Hant": "改為成員均可使用" },
  "为这个空间停用": { en: "Turn off for this workspace", "zh-Hant": "為這個空間停用" },
  "为这个空间启用": { en: "Turn on for this workspace", "zh-Hant": "為這個空間啟用" },
  "停用后，所有工作空间的成员都不能再打开这个应用，已发出的入口和链接在下一次使用时被拒绝。不会删除任何数据。重新启用后，按当时的成员关系和授权恢复使用；期间被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。": {
    en: "Once turned off, members of every workspace can no longer open this application, and entry points and links already handed out are refused the next time they are used. No data is deleted. Once it is turned on again, use resumes according to the memberships and grants at that time; webhook subscriptions switched off and integrations that lost their authorization in the meantime do not come back by themselves and have to be re-enabled or re-authorized.",
    "zh-Hant": "停用後，所有工作空間的成員都不能再開啟這個應用，已發出的入口和連結在下一次使用時被拒絕。不會刪除任何資料。重新啟用後，按當時的成員關係和授權恢復使用；期間被停用的 Webhook 訂閱和失去授權的整合不會自動恢復，需要重新啟用或重新授權。" },
  "启用后，已开通这个应用的工作空间按当时的成员关系和授权恢复使用。之前停止的任务、被停用的 Webhook 订阅和失去授权的集成不会自动恢复：任务需要重新运行，订阅需要重新启用，集成需要重新授权。": {
    en: "Once turned on, the workspaces with this application enabled can use it again according to the memberships and grants at that time. Jobs that were stopped, webhook subscriptions that were switched off and integrations that lost their authorization do not come back by themselves: jobs have to be run again, subscriptions re-enabled and integrations re-authorized.",
    "zh-Hant": "啟用後，已開通這個應用的工作空間按當時的成員關係和授權恢復使用。之前停止的任務、被停用的 Webhook 訂閱和失去授權的整合不會自動恢復：任務需要重新執行，訂閱需要重新啟用，整合需要重新授權。" },
  "改为仅限明确授权后，只有被明确授予资格的成员可以使用；其他成员在下一次请求时失去访问，成员身份保留。改回成员均可使用后他们重新可以使用，但期间被停用的 Webhook 订阅和失去授权的集成需要重新启用或重新授权。": {
    en: "Once limited to explicit grants, only members explicitly granted access can use it; other members lose access on their next request and keep their membership. Opening it to all members again gives them access back, but webhook subscriptions switched off and integrations that lost their authorization in the meantime have to be re-enabled or re-authorized.",
    "zh-Hant": "改為僅限明確授權後，只有被明確授予資格的成員可以使用；其他成員在下一次請求時失去存取，成員身份保留。改回成員均可使用後他們重新可以使用，但期間被停用的 Webhook 訂閱和失去授權的整合需要重新啟用或重新授權。" },
  "改为成员均可使用后，已开通这个应用的工作空间中的成员都可以使用；被单独撤销访问的成员仍然不能使用。之前被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。": {
    en: "Once open to all members, every member of a workspace with this application enabled can use it; members whose access was revoked individually still cannot. Webhook subscriptions switched off and integrations that lost their authorization earlier do not come back by themselves and have to be re-enabled or re-authorized.",
    "zh-Hant": "改為成員均可使用後，已開通這個應用的工作空間中的成員都可以使用；被單獨撤銷存取的成員仍然不能使用。之前被停用的 Webhook 訂閱和失去授權的整合不會自動恢復，需要重新啟用或重新授權。" },
  "停用后，这个工作空间的成员不能再打开这个应用；其他工作空间不受影响。不会删除任何数据。重新启用后，按当时的成员关系和授权恢复使用；期间被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。": {
    en: "Once turned off, members of this workspace can no longer open this application; other workspaces are not affected. No data is deleted. Once it is turned on again, use resumes according to the memberships and grants at that time; webhook subscriptions switched off and integrations that lost their authorization in the meantime do not come back by themselves and have to be re-enabled or re-authorized.",
    "zh-Hant": "停用後，這個工作空間的成員不能再開啟這個應用；其他工作空間不受影響。不會刪除任何資料。重新啟用後，按當時的成員關係和授權恢復使用；期間被停用的 Webhook 訂閱和失去授權的整合不會自動恢復，需要重新啟用或重新授權。" },
  "启用后，这个工作空间的成员按应用的访问策略和已有授权使用这个应用。之前被停用的 Webhook 订阅和失去授权的集成不会自动恢复，需要重新启用或重新授权。": {
    en: "Once turned on, members of this workspace use this application according to its access policy and existing grants. Webhook subscriptions switched off and integrations that lost their authorization earlier do not come back by themselves and have to be re-enabled or re-authorized.",
    "zh-Hant": "啟用後，這個工作空間的成員按應用的存取策略和已有授權使用這個應用。之前被停用的 Webhook 訂閱和失去授權的整合不會自動恢復，需要重新啟用或重新授權。" },
  "已在全平台停用「{app}」。": { en: "“{app}” is turned off platform-wide.", "zh-Hant": "已在全平台停用「{app}」。" },
  "已在全平台启用「{app}」。": { en: "“{app}” is turned on platform-wide.", "zh-Hant": "已在全平台啟用「{app}」。" },
  "「{app}」已改为仅限明确授权。": { en: "“{app}” is now limited to explicit grants.", "zh-Hant": "「{app}」已改為僅限明確授權。" },
  "「{app}」已改为成员均可使用。": { en: "“{app}” is now open to all members.", "zh-Hant": "「{app}」已改為成員均可使用。" },
  "已为「{workspace}」停用「{app}」。": { en: "“{app}” is turned off for “{workspace}”.", "zh-Hant": "已為「{workspace}」停用「{app}」。" },
  "已为「{workspace}」启用「{app}」。": { en: "“{app}” is turned on for “{workspace}”.", "zh-Hant": "已為「{workspace}」啟用「{app}」。" },
  "此操作由平台执行并写入审计记录；不会给任何平台管理员带来成员身份、资格或业务文档访问。": {
    en: "This is a platform action and is written to the audit record; it gives no platform administrator a membership, an entitlement or access to business documents.",
    "zh-Hant": "此操作由平台執行並寫入稽核記錄；不會給任何平台管理員帶來成員身份、資格或業務文件存取。" },
  // ---- the dialog: the target, the transition and its states
  "目标应用": { en: "Target application", "zh-Hant": "目標應用" },
  "目标工作空间": { en: "Target workspace", "zh-Hant": "目標工作空間" },
  "在这个空间：{before} → {after}": { en: "In this workspace: {before} → {after}", "zh-Hant": "在這個空間：{before} → {after}" },
  "访问策略：{before} → {after}": { en: "Access policy: {before} → {after}", "zh-Hant": "存取策略：{before} → {after}" },
  "正在核对应用与操作影响…": { en: "Checking the application and impact…", "zh-Hant": "正在核對應用與操作影響…" },
  "预览有效至 {time}。应用或工作空间变化后必须重新预览。": {
    en: "Preview valid until {time}. Preview again if the application or the workspace changes.",
    "zh-Hant": "預覽有效至 {time}。應用或工作空間變更後必須重新預覽。" },
  "我已核对目标应用和上述影响。": { en: "I have checked the target application and these effects.", "zh-Hant": "我已核對目標應用和上述影響。" },
  "这项更改的说明": { en: "About this change", "zh-Hant": "這項變更的說明" },
  // ---- the dialog: the counts and what each one means
  "将失去访问的人": { en: "People losing access", "zh-Hant": "將失去存取的人" },
  "将获得访问的人": { en: "People gaining access", "zh-Hant": "將獲得存取的人" },
  "受影响的工作空间": { en: "Workspaces affected", "zh-Hant": "受影響的工作空間" },
  "相关的在线会话": { en: "Signed-in sessions involved", "zh-Hant": "相關的線上工作階段" },
  "{people} 人 · {memberships} 个成员关系": { en: "People: {people} · memberships: {memberships}", "zh-Hant": "{people} 人 · {memberships} 個成員關係" },
  "从下一次请求起，在这些成员关系中不能再使用这个应用；成员身份和内容保留。在其他工作空间仍可使用的，不受影响。": {
    en: "From their next request, this application can no longer be used in these memberships; memberships and content are kept. Access the same people still have in other workspaces is not affected.",
    "zh-Hant": "從下一次請求起，在這些成員關係中不能再使用這個應用；成員身份和內容保留。在其他工作空間仍可使用的，不受影響。" },
  "从下一次请求起，在这些成员关系中可以使用这个应用，仍受各自的成员角色限制。": {
    en: "From their next request, this application can be used in these memberships, still within each member's role.",
    "zh-Hant": "從下一次請求起，在這些成員關係中可以使用這個應用，仍受各自的成員角色限制。" },
  "没有人因此失去访问。": { en: "Nobody loses access through this.", "zh-Hant": "沒有人因此失去存取。" },
  "没有人因此获得访问。": { en: "Nobody gains access through this.", "zh-Hant": "沒有人因此獲得存取。" },
  "这些工作空间里的这个应用随之停止可用；内容不会删除，也不会提供给管理员。": {
    en: "This application stops being available in these workspaces; nothing is deleted and no content is shown to administrators.",
    "zh-Hant": "這些工作空間裡的這個應用隨之停止可用；內容不會刪除，也不會提供給管理員。" },
  "这些工作空间里的这个应用随之可用；此操作不改变其内容。": {
    en: "This application becomes available in these workspaces; their content is unchanged.",
    "zh-Hant": "這些工作空間裡的這個應用隨之可用；此操作不改變其內容。" },
  "这些任务会在下一次权限检查时停止，其余部分不再生成；已生成的结果保留。": {
    en: "These jobs stop at their next permission check and generate nothing more; results already generated are kept.",
    "zh-Hant": "這些任務會在下一次權限檢查時停止，其餘部分不再產生；已產生的結果保留。" },
  "正在运行的任务不受影响。": { en: "Running jobs are not affected.", "zh-Hant": "執行中的任務不受影響。" },
  "这些会话不会被结束，但之后对这个应用的请求会被拒绝。": {
    en: "These sessions are not ended, but their later requests to this application are refused.",
    "zh-Hant": "這些工作階段不會被結束，但之後對這個應用的請求會被拒絕。" },
  "现有会话不受影响。": { en: "Existing sessions are not affected.", "zh-Hant": "現有工作階段不受影響。" },
  "这个应用的任务不在这里统计。": { en: "This application's jobs are not counted here.", "zh-Hant": "這個應用的任務不在這裡統計。" },
  "这项数量暂时无法统计。": { en: "This number cannot be counted at the moment.", "zh-Hant": "這項數量暫時無法統計。" },
  "这个应用不按工作空间开通。": { en: "This application is not enabled per workspace.", "zh-Hant": "這個應用不按工作空間開通。" },
  "这个应用不按工作空间开通，在线会话不在这里统计。": {
    en: "This application is not enabled per workspace; signed-in sessions are not counted here.",
    "zh-Hant": "這個應用不按工作空間開通，線上工作階段不在這裡統計。" },
  // ---- the dialog: the server's notes, by code and by the direction of the change
  "不会删除任何业务数据：应用里的项目、模板和结果原样保留，只是暂时打不开。": {
    en: "No business data is deleted: the projects, templates and results in the application stay as they are; they just cannot be opened for now.",
    "zh-Hant": "不會刪除任何業務資料：應用裡的專案、範本和結果原樣保留，只是暫時打不開。" },
  "此操作不改变任何业务数据。": { en: "This changes no business data.", "zh-Hant": "此操作不改變任何業務資料。" },
  "应用入口和已发出的链接在下一次使用时被拒绝；重新启用后，未过期的链接重新可用，每次访问仍按当时的成员关系和授权判断。": {
    en: "The application's entry points and links already handed out are refused the next time they are used; once it is turned on again, unexpired links work again, and every access is decided by the memberships and grants at that time.",
    "zh-Hant": "應用入口和已發出的連結在下一次使用時被拒絕；重新啟用後，未過期的連結重新可用，每次存取仍按當時的成員關係和授權判斷。" },
  "没有明确授权的人，其应用入口和由他们发出的链接在下一次使用时被拒绝；获得授权或改回成员均可使用后，未过期的链接重新可用。通过 API 密钥或服务账户发出的链接按批准该凭据的空间拥有者判断：批准者仍可使用则不受影响。": {
    en: "For people without an explicit grant, the application's entry points and the links they handed out are refused the next time they are used; once they are granted access or the application is open to all members again, unexpired links work again. Links issued through API keys or service accounts follow the workspace owner who approved the credential: they are unaffected while that approver can still use the application.",
    "zh-Hant": "沒有明確授權的人，其應用入口和由他們發出的連結在下一次使用時被拒絕；獲得授權或改回成員均可使用後，未過期的連結重新可用。透過 API 金鑰或服務帳戶發出的連結按核准該憑證的空間擁有者判斷：核准者仍可使用則不受影響。" },
  "重新获得访问的人，其应用入口和未过期的链接重新可用；批准者重新获得访问后，由其批准的 API 密钥和服务账户发出的链接也重新可用。": {
    en: "For people who regain access, the application's entry points and unexpired links work again; once an approver regains access, links issued through the API keys and service accounts they approved work again too.",
    "zh-Hant": "重新獲得存取的人，其應用入口和未過期的連結重新可用；核准者重新獲得存取後，由其核准的 API 金鑰和服務帳戶發出的連結也重新可用。" },
  "应用入口和未过期的链接重新可用，每次访问仍按当时的成员关系和授权判断。": {
    en: "The application's entry points and unexpired links work again; every access is still decided by the memberships and grants at that time.",
    "zh-Hant": "應用入口和未過期的連結重新可用，每次存取仍按當時的成員關係和授權判斷。" },
  "创建者失去访问的 Webhook 订阅会在下一次投递时被自动停用；之后即使恢复访问也保持停用，需要有人重新启用。": {
    en: "Webhook subscriptions whose creator loses access are switched off at their next delivery; they stay off even after access returns, and someone has to re-enable them.",
    "zh-Hant": "建立者失去存取的 Webhook 訂閱會在下一次投遞時被自動停用；之後即使恢復存取也保持停用，需要有人重新啟用。" },
  "之前因创建者失去访问而停用的 Webhook 订阅不会自动恢复，需要有人重新启用。": {
    en: "Webhook subscriptions switched off earlier because their creator lost access do not come back by themselves; someone has to re-enable them.",
    "zh-Hant": "之前因建立者失去存取而停用的 Webhook 訂閱不會自動恢復，需要有人重新啟用。" },
  "有 API 密钥或服务账户的批准者会因此失去访问：这些凭据的调用、排队任务和发出的链接在下一次权限检查时被拒绝。批准者重新获得访问后，凭据恢复可用，无需重新签发。": {
    en: "Some approvers of API keys or service accounts lose access with this change: calls, queued jobs and links of those credentials are refused at their next permission check. Once the approver regains access, the credentials work again without being reissued.",
    "zh-Hant": "有 API 金鑰或服務帳戶的核准者會因此失去存取：這些憑證的呼叫、排隊任務和發出的連結在下一次權限檢查時被拒絕。核准者重新獲得存取後，憑證恢復可用，無需重新簽發。" },
  "之前因批准者失去访问而被拒绝的 API 密钥和服务账户，在批准者重新获得访问后恢复可用，无需重新签发。": {
    en: "API keys and service accounts refused earlier because their approver lost access work again once the approver regains access, without being reissued.",
    "zh-Hant": "之前因核准者失去存取而被拒絕的 API 金鑰和服務帳戶，在核准者重新獲得存取後恢復可用，無需重新簽發。" },
  "将停止的 API 凭据": { en: "API credentials that stop", "zh-Hant": "將停止的 API 憑證" },
  "由失去访问的空间拥有者批准的 API 密钥和服务账户（包括已批准、尚未签发的）在下一次使用时被拒绝；批准者重新获得访问后恢复可用，无需重新签发。": {
    en: "API keys and service accounts approved by a workspace owner who loses access (including approved ones not yet issued) are refused the next time they are used; once the approver regains access they work again without being reissued.",
    "zh-Hant": "由失去存取的空間擁有者核准的 API 金鑰和服務帳戶（包括已核准、尚未簽發的）在下一次使用時被拒絕；核准者重新獲得存取後恢復可用，無需重新簽發。" },
  "此操作不会停止任何 API 凭据。": { en: "This change stops no API credentials.", "zh-Hant": "此操作不會停止任何 API 憑證。" },
  "这个应用不签发 API 凭据。": { en: "This application issues no API credentials.", "zh-Hant": "這個應用不簽發 API 憑證。" },
  "失去访问的人连接的集成会在下一次刷新授权时失去授权；之后即使恢复访问，也需要重新授权。": {
    en: "Integrations connected by people who lose access lose their authorization at their next token refresh; even after access returns, they have to be re-authorized.",
    "zh-Hant": "失去存取的人連接的整合會在下一次重新整理授權時失去授權；之後即使恢復存取，也需要重新授權。" },
  "之前已失去授权的集成不会自动恢复，需要重新授权。": {
    en: "Integrations that lost their authorization earlier do not come back by themselves; they have to be re-authorized.",
    "zh-Hant": "之前已失去授權的整合不會自動恢復，需要重新授權。" },
  "在此期间被停用的集成和 Webhook 订阅不会自动恢复：集成需要重新授权，Webhook 订阅需要重新启用。": {
    en: "Integrations and webhook subscriptions switched off in the meantime do not come back by themselves: integrations have to be re-authorized and webhook subscriptions re-enabled.",
    "zh-Hant": "在此期間被停用的整合和 Webhook 訂閱不會自動恢復：整合需要重新授權，Webhook 訂閱需要重新啟用。" },
  "排队或运行中的任务会在下一次权限检查时停止（开始运行前、样本之间、发布之前），其余样本不再生成；已生成的结果保留，重新启用后需要有人重新运行。账户体系建立之前由运维提交的任务不受影响。": {
    en: "Queued and running jobs stop at their next permission check (before a run starts, between samples, before publishing) and generate no further samples; results already generated are kept, and someone has to run them again once the application is back. Jobs an operator submitted before accounts existed are not affected.",
    "zh-Hant": "排隊或執行中的任務會在下一次權限檢查時停止（開始執行前、樣本之間、發布之前），其餘樣本不再產生；已產生的結果保留，重新啟用後需要有人重新執行。帳戶體系建立之前由維運提交的任務不受影響。" },
  "没有明确授权的人提交的排队或运行中任务会在下一次权限检查时停止，已生成的结果保留；通过 API 密钥或服务账户提交的任务按批准该凭据的空间拥有者判断，批准者没有明确授权时同样停止。": {
    en: "Queued and running jobs submitted by people without an explicit grant stop at their next permission check, and results already generated are kept; jobs submitted through API keys or service accounts follow the workspace owner who approved the credential and stop as well when that approver has no explicit grant.",
    "zh-Hant": "沒有明確授權的人提交的排隊或執行中任務會在下一次權限檢查時停止，已產生的結果保留；透過 API 金鑰或服務帳戶提交的任務按核准該憑證的空間擁有者判斷，核准者沒有明確授權時同樣停止。" },
  "正在运行的任务继续运行；之前因停用而停止的任务不会自动继续，需要有人重新运行。": {
    en: "Running jobs carry on; jobs stopped by an earlier withdrawal do not resume on their own, and someone has to run them again.",
    "zh-Hant": "執行中的任務繼續執行；之前因停用而停止的任務不會自動繼續，需要有人重新執行。" },
  "已有的明确授权全部保留：持有授权的成员继续可以使用。": {
    en: "Every existing explicit grant is kept: members holding one can still use the application.",
    "zh-Hant": "已有的明確授權全部保留：持有授權的成員繼續可以使用。" },
  "已有的明确授权全部保留；以后再改为仅限明确授权时仍然有效。": {
    en: "Every existing explicit grant is kept, and still counts if the application is limited to explicit grants again later.",
    "zh-Hant": "已有的明確授權全部保留；以後再改為僅限明確授權時仍然有效。" },
  "被单独撤销访问的成员仍然不能使用。": { en: "Members whose access was revoked individually still cannot use it.", "zh-Hant": "被單獨撤銷存取的成員仍然不能使用。" },
  "被单独撤销访问的成员仍然不能使用：改为成员均可使用不会恢复已撤销的访问。": {
    en: "Members whose access was revoked individually still cannot use it: opening it to all members does not restore a revoked access.",
    "zh-Hant": "被單獨撤銷存取的成員仍然不能使用：改為成員均可使用不會恢復已撤銷的存取。" },
  "另有一项说明（{code}），本页面无法解释，请刷新页面后重新预览。": {
    en: "There is one more note ({code}) this page cannot explain. Refresh the page and preview again.",
    "zh-Hant": "另有一項說明（{code}），本頁面無法解釋，請重新整理頁面後重新預覽。" },
  // ---- refusals: the dialog's own wording when the server sends only a code
  "找不到这个应用，可能已被移除；请刷新应用目录。": { en: "This application cannot be found; it may have been removed. Refresh the application catalogue.", "zh-Hant": "找不到這個應用，可能已被移除；請重新整理應用目錄。" },
  "找不到这个工作空间，可能已被删除；请刷新列表。": { en: "This workspace cannot be found; it may have been deleted. Refresh the list.", "zh-Hant": "找不到這個工作空間，可能已被刪除；請重新整理清單。" },
  "这是待认领的历史数据空间，由认领流程管理，不能在这里启用或停用应用。": {
    en: "This is the unclaimed historical data's workspace. Its claim flow manages it; applications cannot be turned on or off for it here.",
    "zh-Hant": "這是待認領的歷史資料空間，由認領流程管理，不能在這裡啟用或停用應用。" },
  "这个工作空间当前不可用（已冻结或已删除），不能为它启用应用。": {
    en: "This workspace is not available right now (frozen or deleted), so an application cannot be turned on for it.",
    "zh-Hant": "這個工作空間目前不可用（已凍結或已刪除），不能為它啟用應用。" },
  "应用已经处于这个状态，不需要更改；请刷新查看最新状态。": {
    en: "The application is already in this state; nothing needs to change. Refresh to see the current state.",
    "zh-Hant": "應用已經處於這個狀態，不需要變更；請重新整理檢視最新狀態。" },
  "不支持的应用操作，请刷新页面后重试。": { en: "Unsupported application change. Refresh the page and try again.", "zh-Hant": "不支援的應用操作，請重新整理頁面後重試。" },
  "平台本身依赖这个应用，不能停用它或改为仅限明确授权。": {
    en: "The platform itself depends on this application, so it cannot be turned off or limited to explicit grants.",
    "zh-Hant": "平台本身依賴這個應用，不能停用它或改為僅限明確授權。" },
  "当前部署没有发布这个应用，不能在这里启用；启用后下次启动也会被重新停用。": {
    en: "This deployment does not release this application, so it cannot be turned on here; it would be switched off again at the next start.",
    "zh-Hant": "目前部署沒有發布這個應用，不能在這裡啟用；啟用後下次啟動也會被重新停用。" },
  "应用、工作空间或可使用的人已变化，请重新预览影响。": {
    en: "The application, the workspace or the people with access changed. Preview the impact again.",
    "zh-Hant": "應用、工作空間或可使用的人已變更，請重新預覽影響。" },
  // ---- refusals: the server's own sentences (synwevo_identity.admin_apps); the two naming the
  // application (f-strings) are worded by their code. `找不到这个工作空间`, the preview refusals and
  // the reason are catalogued already.
  "请选择有效的应用操作（disable / enable / policy_explicit / policy_members）": {
    en: "Choose a valid application change (disable / enable / policy_explicit / policy_members).",
    "zh-Hant": "請選擇有效的應用操作（disable / enable / policy_explicit / policy_members）" },
  "门户是平台本身，不是可以启停的应用：任何有效账户都可以打开它": {
    en: "The portal is the platform itself, not an application that can be turned on or off: every live account can open it.",
    "zh-Hant": "入口網站是平台本身，不是可以啟停的應用：任何有效帳戶都可以開啟它" },
  "找不到该应用": { en: "The application could not be found.", "zh-Hant": "找不到該應用" },
  "工作空间编号无效": { en: "That workspace ID is not valid.", "zh-Hant": "工作空間編號無效" },
  "按工作空间只能启用或停用应用；访问策略对全平台生效": {
    en: "Per workspace an application can only be turned on or off; the access policy applies platform-wide.",
    "zh-Hant": "按工作空間只能啟用或停用應用；存取策略對全平台生效" },
  "这是待认领的历史数据空间，由认领流程管理，不能在这里启用或停用应用": {
    en: "This is the unclaimed historical data's workspace. Its claim flow manages it; applications cannot be turned on or off for it here.",
    "zh-Hant": "這是待認領的歷史資料空間，由認領流程管理，不能在這裡啟用或停用應用" },
  "该工作空间当前不可用，不能为它启用应用": {
    en: "This workspace is not available right now, so an application cannot be turned on for it.",
    "zh-Hant": "該工作空間目前不可用，不能為它啟用應用" },
  "该工作空间的这个应用已经处于这个状态，不需要更改": {
    en: "This application is already in this state for the workspace; nothing needs to change.",
    "zh-Hant": "該工作空間的這個應用已經處於這個狀態，不需要變更" },
  "应用已经处于这个状态，不需要更改": { en: "The application is already in this state; nothing needs to change.", "zh-Hant": "應用已經處於這個狀態，不需要變更" },
  "当前部署没有发布这个应用：它的功能在服务里不存在，启用后下次启动也会被重新停用": {
    en: "This deployment does not release this application: its functions do not exist in the service, and it would be switched off again at the next start.",
    "zh-Hant": "目前部署沒有發布這個應用：它的功能在服務裡不存在，啟用後下次啟動也會被重新停用" },
  "应用、工作空间或可使用的人已变化，请重新预览": {
    en: "The application, the workspace or the people with access changed. Preview again.",
    "zh-Hant": "應用、工作空間或可使用的人已變更，請重新預覽" },
  // B3: the organization target of Synwevo Message's switch (admin_apps `_target`)
  "不能同时指定工作空间和组织": { en: "Name either a workspace or an organization, not both.", "zh-Hant": "不能同時指定工作空間和組織" },
  "组织编号无效": { en: "That organization ID is not valid.", "zh-Hant": "組織編號無效" },
  "按组织只能启用或停用应用；访问策略对全平台生效": {
    en: "Per organization an application can only be turned on or off; the access policy applies platform-wide.",
    "zh-Hant": "按組織只能啟用或停用應用；存取策略對全平台生效" },
  "该组织已删除且不能再恢复，不能为它启用应用": {
    en: "This organization was deleted and can no longer be recovered, so an application cannot be turned on for it.",
    "zh-Hant": "該組織已刪除且不能再恢復，不能為它啟用應用" },
  "该组织的这个应用已经处于这个状态，不需要更改": {
    en: "This application is already in this state for the organization; nothing needs to change.",
    "zh-Hant": "該組織的這個應用已經處於這個狀態，不需要變更" },
  "安全记录暂不可用，操作未提交，请稍后重试": {
    en: "The security record is unavailable, so nothing was committed. Try again later.",
    "zh-Hant": "安全記錄暫不可用，操作未提交，請稍後重試" },
  // ---- refusals: every admin page's wording for the codes (labels.ts), when the server's sentence is not catalogued
  "这是待认领的历史数据空间，由认领流程管理，不能在这里更改。": {
    en: "This is the unclaimed historical data's workspace. Its claim flow manages it; it cannot be changed here.",
    "zh-Hant": "這是待認領的歷史資料空間，由認領流程管理，不能在這裡變更。" },
  "这个工作空间当前不可用，请刷新后重试。": { en: "This workspace is not available right now. Refresh and try again.", "zh-Hant": "這個工作空間目前不可用，請重新整理後重試。" },
  "已经是这个状态，不需要更改；请刷新查看最新状态。": {
    en: "It is already in this state; nothing needs to change. Refresh to see the current state.",
    "zh-Hant": "已經是這個狀態，不需要變更；請重新整理檢視最新狀態。" },
  "不支持的更改，请刷新页面后重试。": { en: "Unsupported change. Refresh the page and try again.", "zh-Hant": "不支援的變更，請重新整理頁面後重試。" },
};
registerMessages(appsMessages);
