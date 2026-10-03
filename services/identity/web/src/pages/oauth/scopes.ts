import { registerMessages } from '@synwevo/ui';

/** What each scope lets an integration do, in the words a person decides on (API-4). The scope
 *  ids themselves stay literal on the page, beside the sentence. */
export const SCOPE_TEXT: Record<string, string> = {
  'docweave.read': '读取你在这个工作空间里能看到的 Docs 项目、模板、来源文件与生成结果',
  'docweave.write': '以你的名义创建和修改 Docs 项目、上传文件、提交生成任务',
  'docweave.manage': '管理这个工作空间的 Webhook 订阅，以及它自己的访问凭据',
  'forms.read': '读取这个工作空间里你能看到的表单、填写请求与提交内容',
  'forms.write': '以你的名义创建、修改和发布表单，设置公开链接，发出与关闭填写请求，审阅提交',
  'tables.read': '读取这个工作空间里你能看到的数据表、记录与变更历史',
  'tables.write': '以你的名义新增、修改和归档记录，导入与导出数据，保存视图，并按你的权限调整表结构',
  'dataprep.read': '读取这个工作空间里你能看到的数据集、处理方案与运行结果',
  'dataprep.write': '以你的名义导入数据、保存与发布处理方案、启动和取消运行',
  'extract.read': '读取这个工作空间里你能看到的提取模板、提取任务与结果',
  'extract.write': '以你的名义创建和发布提取模板，创建与取消提取任务',
  'extract.decide': '以你的名义修正并确认提取结果（人工复核）',
  'approvals.read': '读取这个工作空间里你能看到的审批模板、审批请求与时间线，以及可担任审批人的成员名单',
  'approvals.write': '以你的名义发起、修订和撤回审批请求并添加意见，按你的权限管理审批模板',
  'approvals.decide': '以你的名义作出审批决定（批准、驳回、退回），转交你的审批任务，并按你的权限修正停滞环节的审批人',
  offline_access: '在你离开后继续访问（长期有效），直到你在「已授权的应用」中撤回',
  openid: '确认你的 Synwevo 身份',
  profile: '读取你的姓名与语言设置',
  email: '读取你的邮箱地址',
};

registerMessages({
  '读取你在这个工作空间里能看到的 Docs 项目、模板、来源文件与生成结果': { en: 'Read the Docs projects, templates, source files and generated results you can see in this workspace', 'zh-Hant': '讀取你在這個工作空間裡能看到的 Docs 專案、範本、來源檔案與產生結果' },
  '以你的名义创建和修改 Docs 项目、上传文件、提交生成任务': { en: 'Create and change Docs projects, upload files and submit generation jobs as you', 'zh-Hant': '以你的名義建立和修改 Docs 專案、上傳檔案、提交產生任務' },
  '管理这个工作空间的 Webhook 订阅，以及它自己的访问凭据': { en: "Manage this workspace's webhook subscriptions and its own access credentials", 'zh-Hant': '管理這個工作空間的 Webhook 訂閱，以及它自己的存取憑證' },
  '读取这个工作空间里你能看到的表单、填写请求与提交内容': { en: 'Read the forms, fill-in requests and submissions you can see in this workspace', 'zh-Hant': '讀取這個工作空間裡你能看到的表單、填寫請求與提交內容' },
  '以你的名义创建、修改和发布表单，设置公开链接，发出与关闭填写请求，审阅提交': { en: 'Create, change and publish forms, set public links, send and close fill-in requests and review submissions as you', 'zh-Hant': '以你的名義建立、修改和發佈表單，設定公開連結，發出與關閉填寫請求，審閱提交' },
  '读取这个工作空间里你能看到的数据表、记录与变更历史': { en: 'Read the tables, records and change history you can see in this workspace', 'zh-Hant': '讀取這個工作空間裡你能看到的資料表、記錄與變更歷史' },
  '以你的名义新增、修改和归档记录，导入与导出数据，保存视图，并按你的权限调整表结构': { en: 'Add, change and archive records, import and export data and save views as you, and change table structure where your role allows', 'zh-Hant': '以你的名義新增、修改和封存記錄，匯入與匯出資料，儲存檢視，並依你的權限調整表結構' },
  '读取这个工作空间里你能看到的数据集、处理方案与运行结果': { en: 'Read the datasets, recipes and run results you can see in this workspace', 'zh-Hant': '讀取這個工作空間裡你能看到的資料集、處理方案與執行結果' },
  '以你的名义导入数据、保存与发布处理方案、启动和取消运行': { en: 'Import data, save and publish recipes, and start and cancel runs as you', 'zh-Hant': '以你的名義匯入資料、儲存與發佈處理方案、啟動和取消執行' },
  '读取这个工作空间里你能看到的提取模板、提取任务与结果': { en: 'Read the extraction templates, tasks and results you can see in this workspace', 'zh-Hant': '讀取這個工作空間裡你能看到的提取範本、提取任務與結果' },
  '以你的名义创建和发布提取模板，创建与取消提取任务': { en: 'Create and publish extraction templates, and create and cancel extraction tasks as you', 'zh-Hant': '以你的名義建立和發佈提取範本，建立與取消提取任務' },
  '以你的名义修正并确认提取结果（人工复核）': { en: 'Correct and confirm extraction results as you (human review)', 'zh-Hant': '以你的名義修正並確認提取結果（人工複核）' },
  '读取这个工作空间里你能看到的审批模板、审批请求与时间线，以及可担任审批人的成员名单': { en: 'Read the approval templates, requests and timelines you can see in this workspace, and the list of members who can be approvers', 'zh-Hant': '讀取這個工作空間裡你能看到的審批範本、審批請求與時間軸，以及可擔任審批人的成員名單' },
  '以你的名义发起、修订和撤回审批请求并添加意见，按你的权限管理审批模板': { en: 'Start, revise and withdraw approval requests and add comments as you, and manage approval templates where your role allows', 'zh-Hant': '以你的名義發起、修訂和撤回審批請求並新增意見，依你的權限管理審批範本' },
  '以你的名义作出审批决定（批准、驳回、退回），转交你的审批任务，并按你的权限修正停滞环节的审批人': { en: 'Decide approvals as you (approve, reject, return), hand over your approval tasks, and repair a stalled stage\'s approvers where your role allows', 'zh-Hant': '以你的名義作出審批決定（批准、駁回、退回），轉交你的審批任務，並依你的權限修正停滯環節的審批人' },
  '在你离开后继续访问（长期有效），直到你在「已授权的应用」中撤回': { en: 'Keep access after you leave (long-lived) until you withdraw it under “Connected apps”', 'zh-Hant': '在你離開後繼續存取（長期有效），直到你在「已授權的應用」中撤回' },
  '确认你的 Synwevo 身份': { en: 'Confirm your Synwevo identity', 'zh-Hant': '確認你的 Synwevo 身分' },
  '读取你的姓名与语言设置': { en: 'Read your name and language setting', 'zh-Hant': '讀取你的姓名與語言設定' },
  '读取你的邮箱地址': { en: 'Read your email address', 'zh-Hant': '讀取你的電子郵件地址' },
});
