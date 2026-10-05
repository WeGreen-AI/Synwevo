import { registerMessages } from "@synwevo/ui";

// Organization payments as the organization pages meet them: the refusals of
// synwevo_identity.org_billing_routes and the wording of pages/org/OrganizationBilling.tsx
// (checked by pages/org/flow.validation.mjs). Source text is Simplified Chinese; every entry
// carries English and Traditional Chinese.
export const messages = {
  // refusals (org_billing_routes.py)
  "你没有管理该组织付费的权限": { en: "You do not have permission to manage this organization's payments.", "zh-Hant": "你沒有管理該組織付費的權限" },
  "组织付费尚未开放": { en: "Organization payments are not available yet.", "zh-Hant": "組織付費尚未開放" },
  "额度须为 0 至 1000000 元之间的金额": { en: "A limit must be an amount between CNY 0 and 1,000,000.", "zh-Hant": "額度須為 0 至 1000000 元之間的金額" },
  "付费设置已被他人修改，请刷新后重试": { en: "Someone else changed the payment settings. Refresh and try again.", "zh-Hant": "付費設定已被他人修改，請重新整理後重試" },
  "只能授权本组织的有效成员用组织付费": { en: "Only active members of this organization can be allowed to charge it.", "zh-Hant": "只能授權本組織的有效成員以組織付費" },
  "该工作空间不属于这个组织": { en: "That workspace does not belong to this organization.", "zh-Hant": "該工作空間不屬於這個組織" },
  "该组织当前不可用": { en: "This organization is currently unavailable.", "zh-Hant": "此組織目前不可用" },
  // the organization payments section
  "组织付费": { en: "Organization payments", "zh-Hant": "組織付費" },
  "组织可以为成员的 Agent 使用付费。每位成员需要单独授权并设置每月额度；成员在对话中自己选择由个人还是组织付款。额度在每次模型调用前重新核对。": {
    en: "The organization can pay for members' Agent use. Each member is allowed separately with a monthly limit; members choose in the conversation whether they or the organization pays. Limits are checked again before every model call.",
    "zh-Hant": "組織可以為成員的 Agent 使用付費。每位成員需要單獨授權並設定每月額度；成員在對話中自行選擇由個人還是組織付款。額度在每次模型呼叫前重新核對。" },
  "组织钱包可用：{amount}": { en: "Organization wallet available: {amount}", "zh-Hant": "組織錢包可用：{amount}" },
  "调用中冻结：{amount}": { en: "Held for calls in progress: {amount}", "zh-Hant": "呼叫中凍結：{amount}" },
  "因支付争议已冻结": { en: "Frozen by a payment dispute", "zh-Hant": "因支付爭議已凍結" },
  "为组织钱包充值": { en: "Top up the organization wallet", "zh-Hant": "為組織錢包儲值" },
  "选择充值金额（人民币）。充值后不可退款。": { en: "Choose an amount (CNY). Top-ups are final and cannot be refunded.", "zh-Hant": "選擇儲值金額（人民幣）。儲值後不可退款。" },
  "其他金额": { en: "Another amount", "zh-Hant": "其他金額" },
  "金额（元），可充值 {min} 至 {max}": { en: "Amount (CNY), from {min} to {max}", "zh-Hant": "金額（元），可儲值 {min} 至 {max}" },
  "付款在支付服务的收银台完成（银行卡、微信支付或支付宝）。只有支付服务的签名通知确认到账后，金额才会计入组织钱包并出现在账单中。": {
    en: "You pay on the payment service's checkout page (card, WeChat Pay or Alipay). The amount reaches the organization wallet and the statement only after the payment service's signed notice confirms it.",
    "zh-Hant": "付款在支付服務的收銀台完成（銀行卡、微信支付或支付寶）。只有支付服務的簽名通知確認到帳後，金額才會計入組織錢包並出現在帳單中。" },
  "前往支付": { en: "Continue to payment", "zh-Hant": "前往支付" },
  "正在打开收银台…": { en: "Opening the checkout…", "zh-Hant": "正在開啟收銀台…" },
  "充值暂未开放": { en: "Top-ups are not open yet.", "zh-Hant": "儲值暫未開放" },
  "请先验证邮箱再继续": { en: "Verify your email address before continuing.", "zh-Hant": "請先驗證郵箱再繼續" },
  "该钱包因支付争议已被冻结，暂不能充值": { en: "This wallet is frozen because of a payment dispute and cannot be topped up.", "zh-Hant": "該錢包因支付爭議已被凍結，暫不能儲值" },
  "支付服务暂时无法创建收银台，未扣款，请稍后再试": { en: "The payment service could not open a checkout. Nothing was charged; try again later.", "zh-Hant": "支付服務暫時無法建立收銀台，未扣款，請稍後再試" },
  "成员额度": { en: "Member limits", "zh-Hant": "成員額度" },
  "本月（自 {date} 起，UTC）已结算与调用中冻结的金额合计不能超过成员的每月额度。": {
    en: "This month (since {date}, UTC), settled charges plus amounts held for calls in progress cannot exceed a member's monthly limit.",
    "zh-Hant": "本月（自 {date} 起，UTC）已結算與呼叫中凍結的金額合計不能超過成員的每月額度。" },
  "已保存「{name}」的付费设置。": { en: "Saved the payment settings for “{name}”.", "zh-Hant": "已儲存「{name}」的付費設定。" },
  "这个组织还没有成员。": { en: "This organization has no members yet.", "zh-Hant": "這個組織還沒有成員。" },
  "本月已用 {spent} · 冻结 {held}": { en: "Used this month {spent} · held {held}", "zh-Hant": "本月已用 {spent} · 凍結 {held}" },
  "可用组织付费": { en: "May charge the organization", "zh-Hant": "可用組織付費" },
  "每月额度（元）": { en: "Monthly limit (CNY)", "zh-Hant": "每月額度（元）" },
  "保存": { en: "Save", "zh-Hant": "儲存" },
  "保存「{name}」的付费设置": { en: "Save the payment settings for “{name}”", "zh-Hant": "儲存「{name}」的付費設定" },
  "工作空间额度": { en: "Workspace limits", "zh-Hant": "工作空間額度" },
  "可选：限制归属到某个工作空间的组织付费调用每月合计。留空表示不单独限制。": {
    en: "Optional: cap the monthly total of organization-paid calls attributed to a workspace. Leave empty for no separate limit.",
    "zh-Hant": "可選：限制歸屬到某個工作空間的組織付費呼叫每月合計。留空表示不單獨限制。" },
  "已保存「{name}」的每月额度。": { en: "Saved the monthly limit for “{name}”.", "zh-Hant": "已儲存「{name}」的每月額度。" },
  "每月额度（元，留空不限）": { en: "Monthly limit (CNY, empty for none)", "zh-Hant": "每月額度（元，留空不限）" },
  "保存「{name}」的每月额度": { en: "Save the monthly limit for “{name}”", "zh-Hant": "儲存「{name}」的每月額度" },
  "组织账单": { en: "Organization statement", "zh-Hant": "組織帳單" },
  "组织钱包的每一笔入账与扣费；消费注明成员与工作空间，不包含任何对话内容。": {
    en: "Every credit and charge of the organization wallet; charges name the member and workspace, never any conversation content.",
    "zh-Hant": "組織錢包的每一筆入帳與扣費；消費註明成員與工作空間，不包含任何對話內容。" },
  "还没有账单记录。": { en: "No statement entries yet.", "zh-Hant": "還沒有帳單記錄。" },
  "充值到账": { en: "Top-up received", "zh-Hant": "充值到帳" },
  "测试到账": { en: "Test credit", "zh-Hant": "測試到帳" },
  "已结算消费": { en: "Settled charge", "zh-Hant": "已結算消費" },
  "赠送额度": { en: "Granted credit", "zh-Hant": "贈送額度" },
  "拒付追回": { en: "Recovered after a chargeback", "zh-Hant": "拒付追回" },
  // the return from the hosted checkout
  "充值订单 {order}": { en: "Top-up order {order}", "zh-Hant": "儲值訂單 {order}" },
  "你已取消支付，没有扣款。订单会在收银台过期后关闭。": { en: "You cancelled the payment and nothing was charged. The order closes when the checkout expires.", "zh-Hant": "你已取消支付，沒有扣款。訂單會在收銀台過期後關閉。" },
  "充值已到账。": { en: "The top-up has been credited.", "zh-Hant": "儲值已到帳。" },
  "支付没有完成，没有扣款。可以重新发起充值。": { en: "The payment did not complete and nothing was charged. You can start a new top-up.", "zh-Hant": "支付沒有完成，沒有扣款。可以重新發起儲值。" },
  "收银台已过期，没有扣款。可以重新发起充值。": { en: "The checkout expired and nothing was charged. You can start a new top-up.", "zh-Hant": "收銀台已過期，沒有扣款。可以重新發起儲值。" },
  "正在等待支付服务确认到账…": { en: "Waiting for the payment service to confirm the payment…", "zh-Hant": "正在等待支付服務確認到帳…" },
  "支付服务尚未确认到账。如果你已经付款，稍后再查看；确认前不会入账。": { en: "The payment service has not confirmed the payment yet. If you paid, check again later; nothing is credited before it confirms.", "zh-Hant": "支付服務尚未確認到帳。如果你已經付款，稍後再查看；確認前不會入帳。" },
  "金额": { en: "Amount", "zh-Hant": "金額" },
  "付款方式": { en: "Payment method", "zh-Hant": "付款方式" },
  "银行卡": { en: "Card", "zh-Hant": "銀行卡" },
  "微信支付": { en: "WeChat Pay", "zh-Hant": "微信支付" },
  "支付宝": { en: "Alipay", "zh-Hant": "支付寶" },
  "支付服务未报告": { en: "Not reported by the payment service", "zh-Hant": "支付服務未報告" },
  "到账时间": { en: "Credited at", "zh-Hant": "到帳時間" },
  "收款方": { en: "Payee", "zh-Hant": "收款方" },
  "支付单号": { en: "Payment reference", "zh-Hant": "支付單號" },
  "再次查看": { en: "Check again", "zh-Hant": "再次查看" },
  "打印收据": { en: "Print receipt", "zh-Hant": "列印收據" },
  "关闭": { en: "Close", "zh-Hant": "關閉" },
};

registerMessages(messages);
