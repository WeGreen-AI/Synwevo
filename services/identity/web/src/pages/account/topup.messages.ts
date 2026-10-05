import { registerMessages } from '@synwevo/ui';

// Refusals of the wallet top-up routes (synwevo_identity.payment_routes / payments) as the account
// pages meet them. Source text is Simplified Chinese; every entry carries English and Traditional Chinese.
registerMessages({
  '付款方无效': { en: 'The payer is not valid.', 'zh-Hant': '付款方無效' },
  '你不能为这个付款方充值或查看其订单': { en: 'You cannot top up for this payer or read its orders.', 'zh-Hant': '你不能為這個付款方儲值或檢視其訂單' },
  '你还有未完成的充值订单，请先完成或等待其过期': { en: 'You still have an unfinished top-up. Complete it or wait for it to expire first.', 'zh-Hant': '你還有未完成的儲值訂單，請先完成或等待其過期' },
  '充值暂不可用，请稍后再试': { en: 'Top-ups are unavailable right now. Try again later.', 'zh-Hant': '儲值暫不可用，請稍後再試' },
  '充值暂未开放': { en: 'Top-ups are not open yet.', 'zh-Hant': '儲值暫未開放' },
  '充值订单过于频繁，请稍后再试': { en: 'Too many top-up orders. Try again later.', 'zh-Hant': '儲值訂單過於頻繁，請稍後再試' },
  '已超过今日充值金额上限，请明天再试': { en: "You have reached today's top-up limit. Try again tomorrow.", 'zh-Hant': '已超過今日儲值金額上限，請明天再試' },
  '找不到这个充值订单': { en: 'That top-up order does not exist.', 'zh-Hant': '找不到這個儲值訂單' },
  '支付服务暂时无法创建收银台，未扣款，请稍后再试': { en: 'The payment service could not open a checkout. Nothing was charged; try again later.', 'zh-Hant': '支付服務暫時無法建立收銀台，未扣款，請稍後再試' },
  '订单尚未入账，暂无收据': { en: 'The order has not been credited yet, so there is no receipt.', 'zh-Hant': '訂單尚未入帳，暫無收據' },
  '该付款方当前不能充值': { en: 'This payer cannot be topped up right now.', 'zh-Hant': '該付款方目前不能儲值' },
  '该钱包因支付争议已被冻结，暂不能充值': { en: 'This wallet is frozen because of a payment dispute and cannot be topped up.', 'zh-Hant': '該錢包因支付爭議已被凍結，暫不能儲值' },
  '请选择一个充值套餐或填写充值金额': { en: 'Choose a top-up package or enter an amount.', 'zh-Hant': '請選擇一個儲值套餐或填寫儲值金額' },
  '请先验证邮箱再继续': { en: 'Verify your email address before continuing.', 'zh-Hant': '請先驗證郵箱再繼續' },
});
