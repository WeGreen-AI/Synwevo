import { registerMessages } from '@synwevo/ui';

// Refusals of the wallet top-up routes (synwevo_identity.payment_routes / payments) as the account
// pages meet them. Source text is Simplified Chinese; every entry carries English and Traditional Chinese.
registerMessages({
  '充值请求结果待核实': { en: 'Top-up request outcome unknown', 'zh-Hant': '儲值請求結果待核實' },
  '尚未确认支付服务商是否已创建付款页面。原充值请求与订单已保留，请勿重复充值。状态可能需要管理员核实；你可以稍后刷新，或向管理员提供订单号。': {
    en: 'We do not yet know whether the payment provider created a checkout. Your original top-up request and order are retained; do not start another top-up. An administrator may need to check the outcome. You can refresh later or give the order number to an administrator.',
    'zh-Hant': '尚未確認支付服務商是否已建立付款頁面。原儲值請求與訂單已保留，請勿重複儲值。狀態可能需要管理員核實；你可以稍後重新整理，或向管理員提供訂單號。' },
  '充值请求标识无效': { en: 'The top-up request identifier is invalid.', 'zh-Hant': '儲值請求識別碼無效' },
  '这次充值请求的内容已变化，请先核实原订单': { en: 'This top-up request has changed. Check the original order first.', 'zh-Hant': '這次儲值請求的內容已變更，請先核實原訂單' },
  '充值账户或环境已变化，请返回原账户核实订单': { en: 'The account or payment environment changed. Return to the original account to check the order.', 'zh-Hant': '儲值帳戶或環境已變更，請返回原帳戶核實訂單' },
  '充值配置已变化，请刷新后重试': { en: 'Top-up settings changed. Refresh before retrying.', 'zh-Hant': '儲值設定已變更，請重新整理後重試' },
  '无法读取或保存原充值请求。请保留此页面并核实原订单，暂不能发起新充值。': {
    en: 'The original top-up request could not be read or saved. Keep this page and check the original order before starting another top-up.',
    'zh-Hant': '無法讀取或儲存原儲值請求。請保留此頁面並核實原訂單，暫不能發起新儲值。' },
  '正在核实原充值请求。重试会使用原请求；核实完成前不能更改金额或创建另一笔充值。': {
    en: 'The original top-up request is being checked. Retrying reuses that request; wait for confirmation before changing the amount or starting another top-up.',
    'zh-Hant': '正在核實原儲值請求。重試會使用原請求；核實完成前不能變更金額或建立另一筆儲值。' },
  '支付配置已变化，原订单暂不能继续付款。请核实原订单，不要重复创建。': {
    en: 'Payment settings changed, so this order cannot continue to checkout. Check the original order before creating another.',
    'zh-Hant': '支付設定已變更，原訂單暫不能繼續付款。請核實原訂單，不要重複建立。' },
  '原充值订单已到账，可查看收据。': { en: 'The original top-up has been credited. You can view its receipt.', 'zh-Hant': '原儲值訂單已到帳，可檢視收據。' },
  '原充值订单已失败，可以重新选择金额。': { en: 'The original top-up failed. You can choose an amount again.', 'zh-Hant': '原儲值訂單已失敗，可以重新選擇金額。' },
  '原充值订单已过期，可以重新选择金额。': { en: 'The original top-up expired. You can choose an amount again.', 'zh-Hant': '原儲值訂單已過期，可以重新選擇金額。' },
  '原请求金额：{amount}': { en: 'Original requested amount: {amount}', 'zh-Hant': '原請求金額：{amount}' },
  '查看原订单': { en: 'View original order', 'zh-Hant': '檢視原訂單' },
  '重新选择金额': { en: 'Choose an amount again', 'zh-Hant': '重新選擇金額' },
  '正在核实充值…': { en: 'Checking top-up…', 'zh-Hant': '正在核實儲值…' },
  '核实并继续原订单': { en: 'Check and resume original order', 'zh-Hant': '核實並繼續原訂單' },
  '付款方无效': { en: 'The payer is not valid.', 'zh-Hant': '付款方無效' },
  '你不能为这个付款方充值或查看其订单': { en: 'You cannot top up for this payer or read its orders.', 'zh-Hant': '你不能為這個付款方儲值或檢視其訂單' },
  '你还有未完成的充值订单，请先完成或等待其过期': { en: 'You still have an unfinished top-up. Complete it or wait for it to expire first.', 'zh-Hant': '你還有未完成的儲值訂單，請先完成或等待其過期' },
  '充值暂不可用，请稍后再试': { en: 'Top-ups are unavailable right now. Try again later.', 'zh-Hant': '儲值暫不可用，請稍後再試' },
  '充值暂未开放': { en: 'Top-ups are not open yet.', 'zh-Hant': '儲值暫未開放' },
  '充值订单过于频繁，请稍后再试': { en: 'Too many top-up orders. Try again later.', 'zh-Hant': '儲值訂單過於頻繁，請稍後再試' },
  '已超过今日充值金额上限，请明天再试': { en: "You have reached today's top-up limit. Try again tomorrow.", 'zh-Hant': '已超過今日儲值金額上限，請明天再試' },
  '找不到这个充值订单': { en: 'That top-up order does not exist.', 'zh-Hant': '找不到這個儲值訂單' },
  '订单尚未入账，暂无收据': { en: 'The order has not been credited yet, so there is no receipt.', 'zh-Hant': '訂單尚未入帳，暫無收據' },
  '该付款方当前不能充值': { en: 'This payer cannot be topped up right now.', 'zh-Hant': '該付款方目前不能儲值' },
  '该钱包因支付争议已被冻结，暂不能充值': { en: 'This wallet is frozen because of a payment dispute and cannot be topped up.', 'zh-Hant': '該錢包因支付爭議已被凍結，暫不能儲值' },
  '请选择一个充值套餐或填写充值金额': { en: 'Choose a top-up package or enter an amount.', 'zh-Hant': '請選擇一個儲值套餐或填寫儲值金額' },
  '请先验证邮箱再继续': { en: 'Verify your email address before continuing.', 'zh-Hant': '請先驗證郵箱再繼續' },
  // why the provider refused to create the checkout (walletModel.TOPUP_FAILURE_REASONS); each says nothing was charged
  '支付服务拒绝创建付款页面：所选支付方式尚未在支付服务商处开通。没有扣款。请稍后再试或联系平台管理员。': {
    en: 'The payment service refused to create the payment page: a selected payment method has not been activated with the payment provider. Nothing was charged. Try again later or contact the platform administrator.',
    'zh-Hant': '支付服務拒絕建立付款頁面：所選支付方式尚未在支付服務商處開通。沒有扣款。請稍後再試或聯絡平台管理員。' },
  '支付服务拒绝创建付款页面：平台的支付服务配置有误。没有扣款。请稍后再试或联系平台管理员。': {
    en: "The payment service refused to create the payment page because of a problem with the platform's payment configuration. Nothing was charged. Try again later or contact the platform administrator.",
    'zh-Hant': '支付服務拒絕建立付款頁面：平台的支付服務設定有誤。沒有扣款。請稍後再試或聯絡平台管理員。' },
  '支付服务拒绝创建付款页面。没有扣款。请稍后再试或联系平台管理员。': {
    en: 'The payment service refused to create the payment page. Nothing was charged. Try again later or contact the platform administrator.',
    'zh-Hant': '支付服務拒絕建立付款頁面。沒有扣款。請稍後再試或聯絡平台管理員。' },
  // an unknown creation outcome, and a restored order read on opening the dialog (TopupDialog.tsx)
  '暂时无法确认付款页面是否已创建，因此没有跳转。原充值请求已保留：重试只会核实同一笔订单，不会重复创建；订单完成或过期之前，不能更改金额或创建另一笔充值。': {
    en: 'We cannot confirm yet whether the payment page was created, so you were not redirected. Your original request is kept: retrying only checks the same order and never creates another. Until that order completes or expires, you cannot change the amount or start another top-up.',
    'zh-Hant': '暫時無法確認付款頁面是否已建立，因此沒有跳轉。原儲值請求已保留：重試只會核實同一筆訂單，不會重複建立；訂單完成或過期之前，不能變更金額或建立另一筆儲值。' },
  '原充值订单仍在等待付款；继续时会核实付款页面是否仍可使用。在它完成或过期之前，不能更改金额或创建另一笔充值。': {
    en: 'The original order is still awaiting payment. Continuing first checks whether its payment page can still be used. Until it completes or expires, you cannot change the amount or start another top-up.',
    'zh-Hant': '原儲值訂單仍在等待付款；繼續時會核實付款頁面是否仍可使用。在它完成或過期之前，不能變更金額或建立另一筆儲值。' },
  '上一笔充值已到账。': { en: 'Your previous top-up has been credited.', 'zh-Hant': '上一筆儲值已到帳。' },
  '上一笔充值订单已过期。': { en: 'Your previous top-up expired.', 'zh-Hant': '上一筆儲值訂單已過期。' },
  '上一笔充值订单已失败，没有扣款。': { en: 'Your previous top-up failed and nothing was charged.', 'zh-Hant': '上一筆儲值訂單已失敗，沒有扣款。' },
  '可以选择新的金额。': { en: 'You can choose a new amount.', 'zh-Hant': '可以選擇新的金額。' },
  // statement entry kinds added with grants and chargebacks (Wallet.tsx)
  '赠送额度': { en: 'Granted credit', 'zh-Hant': '贈送額度' },
  '拒付追回': { en: 'Recovered after a chargeback', 'zh-Hant': '拒付追回' },
});
