import { registerMessages } from '@synwevo/ui';

/** What each scope lets an integration do, in the words a person decides on (API-4). The scope
 *  ids themselves stay literal on the page, beside the sentence. */
export const SCOPE_TEXT: Record<string, string> = {
  'docweave.read': '读取你在这个工作空间里能看到的 Docs 项目、模板、来源文件与生成结果',
  'docweave.write': '以你的名义创建和修改 Docs 项目、上传文件、提交生成任务',
  'docweave.manage': '管理这个工作空间的 Webhook 订阅，以及它自己的访问凭据',
  offline_access: '在你离开后继续访问（长期有效），直到你在「已授权的应用」中撤回',
  openid: '确认你的 Synwevo 身份',
  profile: '读取你的姓名与语言设置',
  email: '读取你的邮箱地址',
};

registerMessages({
  '读取你在这个工作空间里能看到的 Docs 项目、模板、来源文件与生成结果': { en: 'Read the Docs projects, templates, source files and generated results you can see in this workspace', 'zh-Hant': '讀取你在這個工作空間裡能看到的 Docs 專案、範本、來源檔案與產生結果' },
  '以你的名义创建和修改 Docs 项目、上传文件、提交生成任务': { en: 'Create and change Docs projects, upload files and submit generation jobs as you', 'zh-Hant': '以你的名義建立和修改 Docs 專案、上傳檔案、提交產生任務' },
  '管理这个工作空间的 Webhook 订阅，以及它自己的访问凭据': { en: "Manage this workspace's webhook subscriptions and its own access credentials", 'zh-Hant': '管理這個工作空間的 Webhook 訂閱，以及它自己的存取憑證' },
  '在你离开后继续访问（长期有效），直到你在「已授权的应用」中撤回': { en: 'Keep access after you leave (long-lived) until you withdraw it under “Connected apps”', 'zh-Hant': '在你離開後繼續存取（長期有效），直到你在「已授權的應用」中撤回' },
  '确认你的 Synwevo 身份': { en: 'Confirm your Synwevo identity', 'zh-Hant': '確認你的 Synwevo 身分' },
  '读取你的姓名与语言设置': { en: 'Read your name and language setting', 'zh-Hant': '讀取你的姓名與語言設定' },
  '读取你的邮箱地址': { en: 'Read your email address', 'zh-Hant': '讀取你的電子郵件地址' },
});
