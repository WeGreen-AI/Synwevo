/** A47: platform oversight of invitations across workspaces (governance/InvitationsTab.tsx) — the
 *  explanation, the state chips, the table, the selection bar, the single and bulk revocation and
 *  their results, and the mail failures worded by their classified code. Every sentence is here in
 *  the three languages; the sentences other admin lists already have (刷新邀请, 撤销邀请, 清除选择,
 *  选择邀请 {p0}, 无发送记录, …) stay in their own catalogues. */
import { registerMessages } from "@synwevo/ui";
export const invitationMessages = {
  // ---- the explanation
  "全部空间的邀请": { en: "Invitations across workspaces", "zh-Hant": "全部空間的邀請" },
  "这里列出所有工作空间发出的邀请，用来发现长期没有处理、已过期或投递失败的邀请，以及滥用的邀请。受邀地址只显示首字符和域名；这里看不到完整地址、邀请链接或令牌。": {
    en: "This lists the invitations every workspace has sent, to find invitations left pending for a long time, expired or undelivered, and invitations sent in abuse. An invitee's address shows only its first character and its domain; the full address, the invitation link and its token are never shown here.",
    "zh-Hant": "這裡列出所有工作空間發出的邀請，用來發現長期沒有處理、已過期或投遞失敗的邀請，以及濫用的邀請。受邀地址只顯示首字元和網域；這裡看不到完整地址、邀請連結或權杖。" },
  "撤销后，邀请链接在预览和接受时都会失效；邀请人之后可以重新邀请。": {
    en: "Once revoked, the invitation link stops working both when it is previewed and when it is accepted; the inviter can invite again later.",
    "zh-Hant": "撤銷後，邀請連結在預覽和接受時都會失效；邀請人之後可以重新邀請。" },
  "撤销会写入邀请所在空间自己的审计记录，标明由平台管理执行，你填写的理由对能查看该组织审计的人可见；不会授予你该空间的任何访问权。": {
    en: "A revocation is written to the audit log of the invitation's own workspace as done by platform administration, and the reason you give is visible to those who can read that organization's audit log. It gives you no access to that workspace.",
    "zh-Hant": "撤銷會寫入邀請所在空間自己的審計記錄，標明由平台管理執行，你填寫的理由對能查看該組織審計的人可見；不會授予你該空間的任何存取權。" },
  "你的平台角色可以查看邀请，但不能撤销。": { en: "Your platform role can view invitations but cannot revoke them.", "zh-Hant": "你的平台角色可以查看邀請，但不能撤銷。" },
  // ---- the filters
  "按状态筛选邀请": { en: "Filter invitations by state", "zh-Hant": "依狀態篩選邀請" },
  "{label}（{count}）": { en: "{label} ({count})", "zh-Hant": "{label}（{count}）" },
  "待接受但投递失败": { en: "Pending, mail failed", "zh-Hant": "待接受但投遞失敗" },
  "搜索邀请": { en: "Search invitations", "zh-Hant": "搜尋邀請" },
  "输入完整邮箱、@域名，或工作空间名称的一部分；邮箱的一部分不会匹配。": {
    en: "Enter a full email address, an @domain, or part of a workspace name; part of an email address matches nothing.",
    "zh-Hant": "輸入完整電子郵件、@網域，或工作空間名稱的一部分；電子郵件的一部分不會比對。" },
  "只显示「{name}」的邀请。": { en: "Showing only the invitations of “{name}”.", "zh-Hant": "只顯示「{name}」的邀請。" },
  "显示全部空间": { en: "Show all workspaces", "zh-Hant": "顯示全部空間" },
  // ---- the table
  "所在空间": { en: "Workspace", "zh-Hant": "所在空間" },
  "受邀人": { en: "Invitee", "zh-Hant": "受邀人" },
  "邀请人": { en: "Invited by", "zh-Hant": "邀請人" },
  "发出与到期": { en: "Sent and expiry", "zh-Hant": "發出與到期" },
  "邀请邮件": { en: "Invitation mail", "zh-Hant": "邀請郵件" },
  "所属组织 {id}": { en: "Organization {id}", "zh-Hant": "所屬組織 {id}" },
  "已有账户": { en: "Has an account", "zh-Hant": "已有帳戶" },
  "有效期至 {time}": { en: "Valid until {time}", "zh-Hant": "有效期至 {time}" },
  "撤销于 {time}": { en: "Revoked {time}", "zh-Hant": "撤銷於 {time}" },
  "接受于 {time}": { en: "Accepted {time}", "zh-Hant": "接受於 {time}" },
  "拒绝于 {time}": { en: "Declined {time}", "zh-Hant": "拒絕於 {time}" },
  "只看这个空间": { en: "Only this workspace", "zh-Hant": "只看這個空間" },
  "只看这个空间：{p0}": { en: "Only this workspace: {p0}", "zh-Hant": "只看這個空間：{p0}" },
  "撤销邀请：{p0}": { en: "Revoke invitation: {p0}", "zh-Hant": "撤銷邀請：{p0}" },
  // ---- the mail: its state and its failure by classified code (the mail tab's sentences for the rest)
  "投递失败": { en: "Delivery failed", "zh-Hant": "投遞失敗" },
  "已放弃投递": { en: "Dropped", "zh-Hant": "已放棄投遞" },
  "已尝试 {count} 次": { en: { one: "{count} attempt", other: "{count} attempts" }, "zh-Hant": "已嘗試 {count} 次" },
  "收件服务器拒收了这个地址": { en: "The recipient's mail server refused this address", "zh-Hant": "收件伺服器拒收了這個地址" },
  "邮件服务器拒收了这封邮件": { en: "A mail server refused this message", "zh-Hant": "郵件伺服器拒收了這封郵件" },
  // ---- the selection
  "选择本页可撤销的邀请": { en: "Select the revocable invitations on this page", "zh-Hant": "選擇本頁可撤銷的邀請" },
  "批量撤销邀请": { en: "Bulk invitation revocation", "zh-Hant": "批量撤銷邀請" },
  "已选 {count} 个邀请": {
    en: { one: "{count} invitation selected", other: "{count} invitations selected" },
    "zh-Hant": "已選 {count} 個邀請" },
  "撤销所选邀请": { en: "Revoke selected invitations", "zh-Hant": "撤銷所選邀請" },
  "翻页或更换筛选时，已选邀请保留。": { en: "The selection is kept when you change pages or filters.", "zh-Hant": "翻頁或更換篩選時，已選邀請保留。" },
  "一次最多选择 {limit} 个邀请；已达到上限。": { en: "You can select up to {limit} invitations at a time; the limit is reached.", "zh-Hant": "一次最多選擇 {limit} 個邀請；已達到上限。" },
  // ---- the confirmations and their outcomes
  "例如：同一来源短时间内大量邀请陌生地址": { en: "For example: one source invited many unknown addresses within a short time", "zh-Hant": "例如：同一來源短時間內大量邀請陌生地址" },
  "发给 {who} 的邀请将被撤销，对方不能再用它加入「{workspace}」（{role}）。": {
    en: "The invitation to {who} will be revoked; it can no longer be used to join “{workspace}” ({role}).",
    "zh-Hant": "發給 {who} 的邀請將被撤銷，對方不能再用它加入「{workspace}」（{role}）。" },
  "所选邀请将逐个撤销，每个邀请单独写入审计记录；已接受或已拒绝的邀请不会被处理。": {
    en: "The selected invitations are revoked one at a time, and each is audited on its own. Accepted or declined invitations are left untouched.",
    "zh-Hant": "所選邀請將逐一撤銷，每個邀請單獨寫入審計記錄；已接受或已拒絕的邀請不會被處理。" },
  "所选邀请": { en: "Selected invitations", "zh-Hant": "所選邀請" },
  "已撤销发给 {who} 的邀请。": { en: "The invitation to {who} was revoked.", "zh-Hant": "已撤銷發給 {who} 的邀請。" },
  "这个邀请此前已被撤销，没有重复操作。": { en: "This invitation had already been revoked; nothing was done again.", "zh-Hant": "這個邀請此前已被撤銷，沒有重複操作。" },
  "此前已撤销，本次未重复执行。": { en: "Already revoked earlier; not revoked again.", "zh-Hant": "此前已撤銷，本次未重複執行。" },
  // ---- the server's refusals (admin_invitations.py, Invites.platform_revoke), and by code (governance/labels.ts)
  "已接受或已拒绝的邀请不能撤销": { en: "An accepted or declined invitation cannot be revoked.", "zh-Hant": "已接受或已拒絕的邀請不能撤銷" },
  "邀请编号格式无效": { en: "The invitation identifiers are not valid. Refresh the list and select again.", "zh-Hant": "邀請編號格式無效" },
  "找不到这个邀请，可能已被删除；请刷新。": { en: "That invitation was not found; it may have been removed. Refresh the list.", "zh-Hant": "找不到這個邀請，可能已被刪除；請重新整理。" },
  "请至少选择一项。": { en: "Select at least one item.", "zh-Hant": "請至少選擇一項。" },
  // the workspace invitation list (InvitesPanel): revoked by platform administration
  "由平台管理撤销": { en: "revoked by platform administration", "zh-Hant": "由平台管理撤銷" },
};
registerMessages(invitationMessages);
