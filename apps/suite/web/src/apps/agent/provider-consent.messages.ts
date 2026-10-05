import { registerMessages, type Messages } from '@synwevo/ui/i18n-core';

/** The separate consent to send content to the model provider (agent.provider_consent.*).
 *  [zh-Hans, en, zh-Hant]. The facts mirror synwevo_core.agent_safety.PROVIDER_TERMS and the
 *  privacy page (docs/platform/public-pages.md §2.9); a change of wording is a new version there. */
export const providerConsentCopy = {
  title: ['向模型提供方 DeepSeek 发送内容', 'Sending content to the model provider DeepSeek', '向模型提供方 DeepSeek 傳送內容'],
  intro: ['使用 Agent 需要你单独同意：你发送的消息，以及 Agent 为你读取的内容，会发送给模型提供方处理。在你同意之前不会发起任何模型调用。', 'Using Agent needs your separate consent: the messages you send, and the content Agent reads for you, are sent to the model provider for processing. No model call is made until you agree.', '使用 Agent 需要你單獨同意：你傳送的訊息，以及 Agent 為你讀取的內容，會傳送給模型提供方處理。在你同意之前不會發起任何模型呼叫。'],
  processor: ['模型提供方（受托处理方）', 'Model provider (processor)', '模型提供方（受託處理方）'],
  location: ['数据所在地', 'Where the data is kept', '資料所在地'],
  locationCN: ['中国境内（按 DeepSeek 的隐私政策存储）；Synwevo 的服务器在日本东京，内容会跨境传输。', 'Mainland China (stored under DeepSeek’s privacy policy); Synwevo’s server is in Tokyo, Japan, so content crosses borders.', '中國境內（按 DeepSeek 的私隱政策儲存）；Synwevo 的伺服器在日本東京，內容會跨境傳輸。'],
  training: ['是否用于改进模型', 'Use for improving models', '是否用於改進模型'],
  trainingPolicy: ['按 DeepSeek 的隐私政策，内容可能被用于改进其模型；我们正在取得 DeepSeek 的书面确认，取得后会更新说明并请你重新确认。', 'Under DeepSeek’s privacy policy, content may be used to improve its models; we are seeking DeepSeek’s written confirmation and will update this notice and ask you again.', '按 DeepSeek 的私隱政策，內容可能被用於改進其模型；我們正在取得 DeepSeek 的書面確認，取得後會更新說明並請你重新確認。'],
  scope: ['发送范围', 'What is sent', '傳送範圍'],
  scopeText: ['你在对话中写的内容、同一对话较早的消息、你让 Agent 记住的偏好，以及 AI 处理政策允许的工作空间内容；组织工作空间默认不允许。', 'What you write in the conversation, earlier messages of the same conversation, preferences you asked Agent to remember, and content of workspaces whose AI processing policy allows it; organisation workspaces do not by default.', '你在對話中寫的內容、同一對話較早的訊息、你讓 Agent 記住的偏好，以及 AI 處理政策允許的工作空間內容；組織工作空間預設不允許。'],
  withdrawNote: ['你可以随时在这里撤回；撤回后不再发起任何模型调用，正在进行的回答也会在下一次模型调用前停止。', 'You can withdraw here at any time; after that no model call is made, and a running answer stops before its next model call.', '你可以隨時在這裡撤回；撤回後不再發起任何模型呼叫，正在進行的回答也會在下一次模型呼叫前停止。'],
  version: ['说明版本 {version}，{date} 起生效', 'Notice version {version}, in effect from {date}', '說明版本 {version}，{date} 起生效'],
  accepted: ['你已同意此版本（{date}）。', 'You agreed to this version ({date}).', '你已同意此版本（{date}）。'],
  changed: ['说明已更新：你同意过的是版本 {version}。请查看后重新同意，在此之前不会发起模型调用。', 'The notice has changed: you agreed to version {version}. Review it and agree again; no model call is made until then.', '說明已更新：你同意過的是版本 {version}。請查看後重新同意，在此之前不會發起模型呼叫。'],
  notAccepted: ['你尚未同意，Agent 不会发起模型调用。', 'You have not agreed, so Agent makes no model call.', '你尚未同意，Agent 不會發起模型呼叫。'],
  notRequired: ['你当前使用的模型服务不经过 DeepSeek，这项同意暂不需要；切换到 DeepSeek 时会先请你同意。', 'The model service you use now does not go through DeepSeek, so this consent is not needed yet; you will be asked before DeepSeek is used.', '你目前使用的模型服務不經過 DeepSeek，這項同意暫不需要；切換到 DeepSeek 時會先請你同意。'],
  accept: ['同意并开始使用', 'Agree and start', '同意並開始使用'],
  withdraw: ['撤回同意', 'Withdraw consent', '撤回同意'],
  withdrawTitle: ['撤回向 DeepSeek 发送内容的同意', 'Withdraw consent to send content to DeepSeek', '撤回向 DeepSeek 傳送內容的同意'],
  withdrawBody: ['撤回后 Agent 不再发起任何模型调用；已有的对话保留在你的账户里。之后可以随时重新同意。', 'After withdrawing, Agent makes no further model call; your existing conversations stay in your account. You can agree again at any time.', '撤回後 Agent 不再發起任何模型呼叫；已有的對話保留在你的帳戶裡。之後可以隨時重新同意。'],
  saving: ['正在保存…', 'Saving…', '正在儲存…'],
  history: ['同意记录', 'Consent history', '同意紀錄'],
  historyAccepted: ['{date} 同意版本 {version}', '{date} agreed to version {version}', '{date} 同意版本 {version}'],
  historyWithdrawn: ['{date} 撤回版本 {version}', '{date} withdrew version {version}', '{date} 撤回版本 {version}'],
  privacy: ['完整的数据与隐私说明', 'Full data and privacy notice', '完整的資料與私隱說明'],
  rules: ['AI 使用规范', 'AI use rules', 'AI 使用規範'],
  failed: ['未能保存你的选择，请重试。', 'Your choice could not be saved. Please try again.', '未能儲存你的選擇，請重試。'],
  versionChanged: ['说明刚刚更新，请查看新版本后再同意。', 'The notice was just updated. Review the new version before agreeing.', '說明剛剛更新，請查看新版本後再同意。'],
} as const;
export type ProviderConsentText = keyof typeof providerConsentCopy;
export const providerConsentMessages: Messages = Object.fromEntries(
  Object.values(providerConsentCopy).map(([source, en, hant]) => [source, { en, 'zh-Hant': hant }]));
registerMessages(providerConsentMessages);
