import { registerMessages } from "@synwevo/ui";

// Refusals of an organization's payment settings (synwevo_identity.org_billing_routes) as the
// organization pages meet them. Source text is Simplified Chinese; every entry carries English and
// Traditional Chinese.
registerMessages({
  "你没有管理该组织付费的权限": { en: "You do not have permission to manage this organization's payments.", "zh-Hant": "你沒有管理該組織付費的權限" },
  "组织付费尚未开放": { en: "Organization payments are not available yet.", "zh-Hant": "組織付費尚未開放" },
  "额度须为 0 至 1000000 元之间的金额": { en: "A limit must be an amount between CNY 0 and 1,000,000.", "zh-Hant": "額度須為 0 至 1000000 元之間的金額" },
  "付费设置已被他人修改，请刷新后重试": { en: "Someone else changed the payment settings. Refresh and try again.", "zh-Hant": "付費設定已被他人修改，請重新整理後重試" },
  "只能授权本组织的有效成员用组织付费": { en: "Only active members of this organization can be allowed to charge it.", "zh-Hant": "只能授權本組織的有效成員以組織付費" },
  "该工作空间不属于这个组织": { en: "That workspace does not belong to this organization.", "zh-Hant": "該工作空間不屬於這個組織" },
  "该组织当前不可用": { en: "This organization is currently unavailable.", "zh-Hant": "此組織目前不可用" },
});
