// The core entry, not the component entry: the catalogue is also loaded by `node --test`.
import { registerMessages } from "@synwevo/ui/i18n-core";

/** Strings every application of this shell shares, in three languages. Source keys are zh-Hans.
 *  Vocabulary: 工作空间 (a workspace — the tenant a record belongs to), 应用, 运行记录 (a run: one
 *  recorded call of an operation), 草稿 / 已发布 / 已停用 / 已归档 (lifecycle of a definition). */
export const shared = {
  "加载中…": { en: "Loading…", "zh-Hant": "載入中…" },
  "页面不存在": { en: "Page not found", "zh-Hant": "找不到頁面" },
  "这个地址不存在。": { en: "This address does not exist.", "zh-Hant": "這個地址不存在。" },
  "返回工作空间": { en: "Back to the workspace", "zh-Hant": "返回工作空間" },
  "重试": { en: "Retry", "zh-Hant": "重試" },
  "请求编号：{id}": { en: "Request ID: {id}", "zh-Hant": "請求編號：{id}" },
  "工作空间": { en: "Workspace", "zh-Hant": "工作空間" },
  "选择工作空间": { en: "Choose a workspace", "zh-Hant": "選擇工作空間" },
  "在哪个工作空间使用": { en: "Workspace to work in", "zh-Hant": "在哪個工作空間使用" },
  "你在这些工作空间可以使用{app}：": { en: "You can use {app} in these workspaces:", "zh-Hant": "你在這些工作空間可以使用{app}：" },
  "你目前没有可以使用{app}的工作空间。加入一个工作空间，或请工作空间管理员启用它。": {
    en: "You have no workspace where {app} is available. Join a workspace, or ask a workspace administrator to turn it on.",
    "zh-Hant": "你目前沒有可以使用{app}的工作空間。加入一個工作空間，或請工作空間管理員啟用它。" },
  "你不能在这个工作空间使用{app}，或它已不可用。": {
    en: "You cannot use {app} in this workspace, or it is no longer available.",
    "zh-Hant": "你不能在這個工作空間使用{app}，或它已不可用。" },
  "所有者": { en: "Owner", "zh-Hant": "擁有者" },
  "管理员": { en: "Admin", "zh-Hant": "管理員" },
  "编辑者": { en: "Editor", "zh-Hant": "編輯者" },
  "查看者": { en: "Viewer", "zh-Hant": "檢視者" },
  "只读：你在这个工作空间是查看者。": { en: "Read-only: you are a viewer in this workspace.", "zh-Hant": "唯讀：你在這個工作空間是檢視者。" },
  "{app} 首页": { en: "{app} home", "zh-Hant": "{app} 首頁" },
  "应用导航": { en: "Application sections", "zh-Hant": "應用導覽" },
  "取消": { en: "Cancel", "zh-Hant": "取消" },
  "保存": { en: "Save", "zh-Hant": "儲存" },
  "关闭": { en: "Close", "zh-Hant": "關閉" },
  "删除": { en: "Delete", "zh-Hant": "刪除" },
  "确定": { en: "OK", "zh-Hant": "確定" },
  "排队中": { en: "Queued", "zh-Hant": "排隊中" },
  "进行中": { en: "Running", "zh-Hant": "進行中" },
  "等待填写": { en: "Waiting for input", "zh-Hant": "等待填寫" },
  "等待审批": { en: "Waiting for approval", "zh-Hant": "等待審批" },
  "已完成": { en: "Succeeded", "zh-Hant": "已完成" },
  "部分完成": { en: "Partly succeeded", "zh-Hant": "部分完成" },
  "失败": { en: "Failed", "zh-Hant": "失敗" },
  "已取消": { en: "Cancelled", "zh-Hant": "已取消" },
  "结果待核实": { en: "Outcome unknown — needs checking", "zh-Hant": "結果待核實" },
  "操作失败，请稍后重试": { en: "The operation failed. Try again later.", "zh-Hant": "操作失敗，請稍後重試" },
} as const;

registerMessages(shared);
