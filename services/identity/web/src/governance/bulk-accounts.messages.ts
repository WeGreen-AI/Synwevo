/** A18: bulk suspension and bulk session revocation from the platform account list — the
 *  selection bar (Admin.tsx UsersTab), the preview and confirmation (governance/BulkAccountDialog.tsx),
 *  the item-by-item result, and the refusals of `/api/admin/users/bulk-preview` and `/bulk` worded
 *  by code (governance/labels.ts). Every sentence is here in the three languages; the sentences the
 *  single-account dialogs already have (重新预览, 预览有效至 {time}…, 清除选择, 选择 {p0}, …) stay in
 *  their own catalogues. */
import { registerMessages } from "@synwevo/ui";
export const bulkAccountMessages = {
  // ---- the selection
  "选择本页的账户": { en: "Select the accounts on this page", "zh-Hant": "選擇本頁的帳戶" },
  "批量账户操作": { en: "Bulk account actions", "zh-Hant": "批量帳戶操作" },
  "已选 {count} 个账户": {
    en: { one: "{count} account selected", other: "{count} accounts selected" },
    "zh-Hant": "已選 {count} 個帳戶" },
  "翻页或更换筛选时，已选账户保留。": { en: "The selection is kept when you change pages or filters.", "zh-Hant": "翻頁或更換篩選時，已選帳戶保留。" },
  "一次最多选择 {limit} 个账户；已达到上限。": { en: "You can select up to {limit} accounts at a time; the limit is reached.", "zh-Hant": "一次最多選擇 {limit} 個帳戶；已達到上限。" },
  "停用所选账户": { en: "Suspend selected accounts", "zh-Hant": "停用所選帳戶" },
  "撤销所选账户的全部会话": { en: "Revoke all sessions of selected accounts", "zh-Hant": "撤銷所選帳戶的全部工作階段" },
  "你的平台角色不能停用账户。": { en: "Your platform role cannot suspend accounts.", "zh-Hant": "你的平台角色不能停用帳戶。" },
  "你的平台角色不能撤销会话。": { en: "Your platform role cannot revoke sessions.", "zh-Hant": "你的平台角色不能撤銷工作階段。" },
  // ---- the preview and the confirmation
  "所选账户将逐个停用：每个账户按单个停用的同一规则重新核对，并撤销其会话和应用令牌；不符合条件的账户不会被处理。": {
    en: "The selected accounts are suspended one at a time: each is checked again under the same rules as a single suspension, and its sessions and application tokens are revoked. Accounts that do not qualify are left untouched.",
    "zh-Hant": "所選帳戶將逐一停用：每個帳戶依單一停用的同一規則重新核對，並撤銷其工作階段和應用程式權杖；不符合條件的帳戶不會被處理。" },
  "所选账户的全部会话和应用令牌将逐个撤销：账户本身保持可用，本人可以重新登录；不符合条件的账户不会被处理。": {
    en: "All sessions and application tokens of the selected accounts are revoked one account at a time. The accounts stay usable and their owners can sign in again. Accounts that do not qualify are left untouched.",
    "zh-Hant": "所選帳戶的全部工作階段和應用程式權杖將逐一撤銷：帳戶本身保持可用，本人可以重新登入；不符合條件的帳戶不會被處理。" },
  "每个账户单独执行、单独写入审计记录；不会授予管理员任何文档或私信内容访问权。": {
    en: "Each account is acted on and audited on its own. This grants no access to documents or private messages.",
    "zh-Hant": "每個帳戶單獨執行、單獨寫入審計記錄；不會授予管理員任何文件或私訊內容存取權。" },
  "正在核对所选账户与操作影响…": { en: "Checking the selected accounts and impact…", "zh-Hant": "正在核對所選帳戶與操作影響…" },
  "{eligible} / {total} 个账户可以执行": { en: "Accounts that can be processed: {eligible} of {total}", "zh-Hant": "{eligible} / {total} 個帳戶可以執行" },
  "符合条件的账户合计：{sessions} 个会话、{tokens} 个应用令牌将被撤销。": {
    en: "To be revoked across the eligible accounts — sessions: {sessions}; application tokens: {tokens}.",
    "zh-Hant": "符合條件的帳戶合計：{sessions} 個工作階段、{tokens} 個應用程式權杖將被撤銷。" },
  "{count} 个账户不符合条件，不会被处理；原因见下方列表。": {
    en: { one: "{count} account does not qualify and will not be processed; the list below says why.",
      other: "{count} accounts do not qualify and will not be processed; the list below says why." },
    "zh-Hant": "{count} 個帳戶不符合條件，不會被處理；原因見下方清單。" },
  "所选账户都不符合条件，没有可以执行的账户。": { en: "None of the selected accounts qualifies; there is nothing to process.", "zh-Hant": "所選帳戶都不符合條件，沒有可以執行的帳戶。" },
  "所选账户包含你自己的账户，完成后可能需要重新登录或离开平台管理页。": {
    en: "Your own account is among the selected accounts; afterwards you may need to sign in again or leave platform administration.",
    "zh-Hant": "所選帳戶包含你自己的帳戶，完成後可能需要重新登入或離開平台管理頁。" },
  "所选账户及其核对结果": { en: "Selected accounts and their checks", "zh-Hant": "所選帳戶及其核對結果" },
  "可以执行：{sessions} 个会话，{tokens} 个应用令牌": { en: "Can be processed — sessions: {sessions}; application tokens: {tokens}", "zh-Hant": "可以執行：{sessions} 個工作階段，{tokens} 個應用程式權杖" },
  "不处理：{reason}": { en: "Not processed: {reason}", "zh-Hant": "不處理：{reason}" },
  // ---- the result
  "已停用，撤销 {sessions} 个会话和 {tokens} 个应用令牌": { en: "Suspended — sessions revoked: {sessions}; application tokens revoked: {tokens}", "zh-Hant": "已停用，撤銷 {sessions} 個工作階段和 {tokens} 個應用程式權杖" },
  "已撤销 {sessions} 个会话和 {tokens} 个应用令牌": { en: "Revoked — sessions: {sessions}; application tokens: {tokens}", "zh-Hant": "已撤銷 {sessions} 個工作階段和 {tokens} 個應用程式權杖" },
  "此前已完成，本次未重复执行。": { en: "Already done earlier; not applied again.", "zh-Hant": "此前已完成，本次未重複執行。" },
  "重新预览失败的账户": { en: "Preview the failed accounts again", "zh-Hant": "重新預覽失敗的帳戶" },
  // ---- refusals by code (governance/labels.ts)
  "不支持的批量操作，请刷新页面后重试。": { en: "This bulk action is not supported. Refresh the page and try again.", "zh-Hant": "不支援的批量操作，請重新整理頁面後重試。" },
  "请至少选择一个账户。": { en: "Select at least one account.", "zh-Hant": "請至少選擇一個帳戶。" },
  "一次选择的项目过多，请减少后分批处理。": { en: "Too many items at once. Select fewer and process them in batches.", "zh-Hant": "一次選擇的項目過多，請減少後分批處理。" },
  "该账户是共享工作空间唯一可用的拥有者或管理者，需先为该空间另设拥有者或管理者。": {
    en: "This account is the only usable owner or manager of a shared workspace; that workspace needs another owner or manager first.",
    "zh-Hant": "該帳戶是共享工作空間唯一可用的擁有者或管理者，需先為該空間另設擁有者或管理者。" },
  "该账户是组织唯一可用的拥有者或管理者，需先为该组织另设拥有者或管理者。": {
    en: "This account is the only usable owner or manager of an organization; that organization needs another owner or manager first.",
    "zh-Hant": "該帳戶是組織唯一可用的擁有者或管理者，需先為該組織另設擁有者或管理者。" },
  "操作未能完成，已记录失败；请稍后重新预览。": { en: "The action could not be completed and the failure was recorded. Preview again later.", "zh-Hant": "操作未能完成，已記錄失敗；請稍後重新預覽。" },
};
registerMessages(bulkAccountMessages);
