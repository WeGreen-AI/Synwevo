import { registerMessages, type Messages } from '@synwevo/ui/i18n-core';

/** Reporting an answer to the platform's reviewers (agent.message.report). Each entry:
 *  [zh-Hans source, en, zh-Hant]. */
export const reportCopy = {
  open: ['举报', 'Report', '檢舉'],
  openLabel: ['举报这条回答', 'Report this answer', '檢舉這則回答'],
  reported: ['已举报', 'Reported', '已檢舉'],
  reportedLabel: ['这条回答已举报', 'This answer has been reported', '這則回答已檢舉'],
  title: ['举报这条回答', 'Report this answer', '檢舉這則回答'],
  intro: ['告诉平台的审核人员这条回答有什么问题。他们会看到你选择的原因和备注；只有在你勾选时，才会附上回答的文字。', 'Tell the platform’s reviewers what is wrong with this answer. They see the reason you choose and your note; the answer’s text is included only if you tick the box below.', '告訴平台的審核人員這則回答有什麼問題。他們會看到你選擇的原因和備註；只有在你勾選時，才會附上回答的文字。'],
  reason: ['原因', 'Reason', '原因'],
  harmful: ['有害或冒犯', 'Harmful or offensive', '有害或冒犯'],
  illegal: ['可能违法', 'Possibly illegal', '可能違法'],
  privacy: ['涉及个人信息', 'Exposes personal information', '涉及個人資訊'],
  wrong: ['内容错误或误导', 'Wrong or misleading', '內容錯誤或誤導'],
  other: ['其他问题', 'Something else', '其他問題'],
  note: ['备注（可选）', 'Note (optional)', '備註（選填）'],
  noteHint: ['最多 {count} 个字。不要写入密码或他人的个人信息。', 'Up to {count} characters. Do not include passwords or other people’s personal information.', '最多 {count} 個字。不要寫入密碼或他人的個人資訊。'],
  attach: ['附上这条回答的文字', 'Attach this answer’s text', '附上這則回答的文字'],
  attachHint: ['默认不附上。勾选后，审核人员会看到回答的文字（最多 {count} 个字）。', 'Off by default. If ticked, the reviewers see the answer’s text (up to {count} characters).', '預設不附上。勾選後，審核人員會看到回答的文字（最多 {count} 個字）。'],
  chooseReason: ['请选择一个原因。', 'Choose a reason.', '請選擇一個原因。'],
  send: ['提交举报', 'Send report', '送出檢舉'],
  sending: ['正在提交举报…', 'Sending the report…', '正在送出檢舉…'],
  retry: ['重新提交', 'Send again', '重新送出'],
  cancel: ['取消', 'Cancel', '取消'],
  close: ['关闭', 'Close', '關閉'],
  sent: ['举报已提交，谢谢。平台的审核人员会处理。', 'Report sent. Thank you; the platform’s reviewers will look at it.', '檢舉已送出，謝謝。平台的審核人員會處理。'],
  sentWithText: ['举报已提交，并附上了回答的文字。谢谢。', 'Report sent with the answer’s text. Thank you.', '檢舉已送出，並附上了回答的文字。謝謝。'],
  exists: ['你已经举报过这条回答，审核人员会处理，不需要再次提交。', 'You have already reported this answer. The reviewers will look at it; there is no need to send it again.', '你已經檢舉過這則回答，審核人員會處理，不需要再次送出。'],
  failedCategory: ['这个原因无法使用，请重新选择后再提交。', 'This reason cannot be used. Choose a reason and send again.', '這個原因無法使用，請重新選擇後再送出。'],
  failedGone: ['这条回答已不存在或你已无法访问，举报没有提交。请刷新会话。', 'This answer no longer exists or is no longer yours to see, so the report was not sent. Refresh the conversation.', '這則回答已不存在或你已無法存取，檢舉沒有送出。請重新整理會話。'],
  failed: ['举报没有提交成功。请检查网络后重新提交。', 'The report was not sent. Check your connection and send it again.', '檢舉沒有送出成功。請檢查網路後重新送出。'],
} as const;

export type ReportText = keyof typeof reportCopy;
export const reportMessages: Messages = Object.fromEntries(Object.values(reportCopy).map(([source, en, hant]) => [source, { en, 'zh-Hant': hant }]));
registerMessages(reportMessages);
